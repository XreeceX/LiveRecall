// LiveKitController.swift
//
// Owns the LiveKit Room for this app:
//   - Connects to LiveKit Cloud using the JWT from POST /token.
//   - Publishes a microphone track (phone mic) so the existing Scribe v2 STT
//     path on the backend keeps firing without changes.
//   - Publishes a *custom* video track whose source is a BufferCapturer that
//     receives Ray-Ban POV frames from WearablesController. When the toolkit
//     isn't linked yet we fall back to the phone camera so the app stays
//     useful for end-to-end testing.
//   - Subscribes to the worker's TTS audio track and routes it to the
//     phone speaker (or paired Bluetooth: glasses, AirPods, headset).
//
// The custom-video-source pattern is documented in the LiveKit Swift SDK:
//   https://docs.livekit.io/client-sdk-swift/  (look for `BufferCapturer`).

import AVFoundation
import Combine
import Foundation
import LiveKit

@MainActor
final class LiveKitController: ObservableObject {

    enum State: Equatable {
        case disconnected
        case connecting
        case connected
        case failed(String)
    }

    @Published private(set) var state: State = .disconnected
    @Published private(set) var participantCount: Int = 0

    /// Exposed so the SwiftUI preview can attach a VideoView when we are
    /// publishing the phone camera (the glasses path renders into the
    /// preview through a BufferCapturer; same VideoView works for both).
    @Published private(set) var localVideoTrack: LocalVideoTrack?

    private let logger = SessionLogger.shared
    private var room: Room?
    private var bufferCapturer: BufferCapturer?
    private var cameraCapturer: CameraCapturer?
    private var audioTrack: LocalAudioTrack?
    private var cancellables = Set<AnyCancellable>()

    // MARK: - Connect / publish

    func connect(
        url: String,
        token: String,
        useGlassesSource: Bool,
        wearables: WearablesController?
    ) async {
        state = .connecting
        let room = Room()
        self.room = room

        room.add(delegate: self)

        do {
            try await room.connect(url: url, token: token)
            state = .connected
            logger.log("livekit: connected to \(url)", level: .ok)

            // ------- audio (phone mic) ----------------------------------
            let mic = LocalAudioTrack.createTrack(name: "phone-mic")
            self.audioTrack = mic
            try await room.localParticipant.publish(audioTrack: mic)
            logger.log("livekit: published phone mic", level: .ok)

            // ------- video ---------------------------------------------
            if useGlassesSource {
                try await publishGlassesTrack(wearables: wearables)
            } else {
                try await publishPhoneCameraTrack()
            }
        } catch {
            state = .failed(error.localizedDescription)
            logger.log("livekit: connect failed — \(error.localizedDescription)", level: .err)
        }
    }

    func disconnect() async {
        await room?.disconnect()
        room = nil
        bufferCapturer = nil
        cameraCapturer = nil
        audioTrack = nil
        localVideoTrack = nil
        state = .disconnected
        logger.log("livekit: disconnected", level: .info)
    }

    // MARK: - Track creation

    private func publishGlassesTrack(wearables: WearablesController?) async throws {
        guard let room else { return }

        // BufferCapturer: a LiveKit-supplied capturer that lets us push raw
        // CVPixelBuffer / CMSampleBuffer frames from any source — perfect
        // for the Wearables toolkit's videoFramePublisher.
        let capturer = BufferCapturer()
        let track = LocalVideoTrack.createBufferTrack(
            name: "glasses-pov",
            source: .camera,
            capturer: capturer
        )
        self.bufferCapturer = capturer
        self.localVideoTrack = track
        try await room.localParticipant.publish(videoTrack: track)
        logger.log("livekit: published glasses-pov video track", level: .ok)

        // Wire the WearablesController's frame callback through to LiveKit.
        wearables?.onVideoFrame = { [weak self] frame in
            // BufferCapturer.capture(_: CVPixelBuffer, timeStampNs:, rotation:)
            // is the canonical entry point per the LiveKit Swift SDK.
            self?.bufferCapturer?.capture(
                pixelBuffer: frame.pixelBuffer,
                timeStampNs: Int64(frame.timestampNs),
                rotation: VideoRotation(rawValue: frame.rotation) ?? ._0
            )
        }
    }

    private func publishPhoneCameraTrack() async throws {
        guard let room else { return }
        // Default LiveKit camera capturer — back camera, 720p, ~24 fps.
        // Mirrors what phone/glasses.html requests in createLocalTracks().
        let opts = CameraCaptureOptions(
            position: .back,
            dimensions: VideoParameters.presetH720_169.dimensions,
            fps: 24
        )
        let track = LocalVideoTrack.createCameraTrack(name: "glasses-pov-fallback",
                                                       options: opts)
        self.localVideoTrack = track
        if let cap = track.capturer as? CameraCapturer { self.cameraCapturer = cap }
        try await room.localParticipant.publish(videoTrack: track)
        logger.log("livekit: published phone camera (glasses SDK unavailable)", level: .warn)
    }
}

// MARK: - RoomDelegate

extension LiveKitController: RoomDelegate {

    nonisolated func room(_ room: Room,
                          participant: RemoteParticipant?,
                          didSubscribeTrack publication: RemoteTrackPublication) {
        Task { @MainActor in
            self.logger.log(
                "livekit: subscribed \(publication.kind) from \(participant?.identity ?? "?")",
                level: .ok
            )
            // Remote audio = the worker's TTS. iOS routes it through the
            // current AVAudioSession by default; nothing more to do here.
        }
    }

    nonisolated func room(_ room: Room, didConnect: Bool) {
        Task { @MainActor in self.participantCount = room.allParticipants.count }
    }

    nonisolated func room(_ room: Room, didDisconnect: Error?) {
        Task { @MainActor in
            self.state = .disconnected
            if let didDisconnect {
                self.logger.log("livekit: disconnect — \(didDisconnect.localizedDescription)", level: .err)
            }
        }
    }
}
