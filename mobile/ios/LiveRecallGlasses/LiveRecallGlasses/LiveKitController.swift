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
import CoreImage
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

    // Sink that the LiveKit SDK calls every time the published local video
    // track produces a frame. We hold the most recent CVPixelBuffer in a
    // lock-protected slot so Snap & ask can grab a still without spinning
    // up a parallel AVCaptureSession (which would conflict with LiveKit's
    // hold on the camera). Works for both publish paths because both go
    // through the same VideoCapturer renderer pipeline.
    private let frameRenderer = LatestFrameRenderer()

    // MARK: - Connect / publish

    func connect(
        url: String,
        token: String,
        useGlassesSource: Bool,
        wearables: WearablesController?
    ) async {
        state = .connecting
        // Match phone/glasses.html (commit 0a085b2): adaptiveStream on,
        // dynacast off. Dynacast on cellular caused stalled video for the
        // worker subscriber; the iOS SDK defaults to (false, false) so we
        // need to set this explicitly even though `false` for dynacast is
        // already the default — keeping `adaptiveStream: true` parity is
        // what matters here.
        let room = Room(
            roomOptions: RoomOptions(adaptiveStream: true, dynacast: false)
        )
        self.room = room

        room.add(delegate: self)

        do {
            try await room.connect(url: url, token: token)
            state = .connected
            logger.log("livekit: connected to \(url)", level: .ok)

            // ------- video FIRST ----------------------------------------
            // Order matters: video is the headline product moment for
            // the glasses demo. If the AVAudioSession is wedged (Meta
            // AI / a previous run holding it), the mic publish fails —
            // we don't want that to also kill the glasses video. So
            // publish video first; mic is best-effort below.
            do {
                if useGlassesSource {
                    try await publishGlassesTrack(wearables: wearables)
                } else {
                    try await publishPhoneCameraTrack()
                }
            } catch {
                logger.log("livekit: video publish failed — \(error.localizedDescription)", level: .err)
                state = .failed(error.localizedDescription)
                return
            }

            // ------- audio (phone mic) — best-effort --------------------
            // Most common failure here on iOS is "Audio Session Error
            // (Failed to configure audio session)" which means another
            // process (Meta AI, Voice Memos, an active call) owns the
            // session, or our last disconnect didn't release it cleanly.
            // STT degrades gracefully on the backend (text-only path
            // still works via /snap), so we log + continue rather than
            // tearing down the working video track.
            do {
                let mic = LocalAudioTrack.createTrack(name: "phone-mic")
                self.audioTrack = mic
                try await room.localParticipant.publish(audioTrack: mic)
                logger.log("livekit: published phone mic", level: .ok)
            } catch {
                logger.log(
                    "livekit: mic publish failed (\(error.localizedDescription)) — continuing without mic; STT disabled, /snap still works. Common fix: force-quit Meta AI + this app, then retry.",
                    level: .warn
                )
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

        // BufferCapturer's init is internal in LiveKit Swift SDK 2.x —
        // createBufferTrack constructs one for us and stores it on the
        // returned track. We grab it back via track.capturer to push frames.
        let track = LocalVideoTrack.createBufferTrack(
            name: "glasses-pov",
            source: .camera
        )
        self.bufferCapturer = track.capturer as? BufferCapturer
        self.localVideoTrack = track

        // CRITICAL: wire the WearablesController's frame callback BEFORE
        // calling publish(). The LiveKit SDK comment on BufferCapturer
        // says publish() will hang for up to 10s if no frame is captured
        // before publish, because dimensions must be resolved at publish
        // time. By wiring the callback first, the wearables stream
        // (which is already in `.streaming` by this point) starts
        // pushing into bufferCapturer immediately, dimensions resolve,
        // and publish completes in a few hundred ms.
        wearables?.onVideoFrame = { [weak self] frame in
            self?.bufferCapturer?.capture(
                frame.pixelBuffer,
                timeStampNs: Int64(frame.timestampNs),
                rotation: VideoRotation(rawValue: frame.rotation) ?? ._0
            )
        }

        // Give the very first frame a brief window to land in the
        // capturer (real-world timing: ~30-100ms after the callback is
        // wired). Belt-and-braces — most cases the first frame is
        // already there, but on a cold start the BT pipe can briefly
        // pause and we'd hit the publish timeout for nothing.
        try? await Task.sleep(nanoseconds: 250_000_000)

        try await room.localParticipant.publish(videoTrack: track)
        track.add(videoRenderer: frameRenderer)
        logger.log("livekit: published glasses-pov video track", level: .ok)
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
        let track = LocalVideoTrack.createCameraTrack(name: "phone-camera",
                                                       options: opts)
        self.localVideoTrack = track
        if let cap = track.capturer as? CameraCapturer { self.cameraCapturer = cap }
        try await room.localParticipant.publish(videoTrack: track)
        track.add(videoRenderer: frameRenderer)
        logger.log("livekit: published phone camera", level: .ok)
    }

    // MARK: - Snap from local video track
    //
    // Used by SessionController.snapAndAsk in phone-camera mode (or any
    // time WearablesController.capturePhoto isn't available). Returns a
    // JPEG of the most recent frame the publishing track produced — that
    // frame is exactly what the worker is also seeing on the LiveKit
    // subscriber side, so Vision results match what's on the dashboard.
    func snapLocalFrameJPEG(quality: CGFloat = 0.85) throws -> Data {
        try frameRenderer.snapJPEG(quality: quality)
    }
}

// MARK: - LatestFrameRenderer
//
// Drop-in `VideoRenderer` that pockets the most recent frame's
// CVPixelBuffer behind a lock. JPEG encoding happens on demand (Snap &
// ask, ~once every few seconds at most) using CoreImage so we don't pay
// the cost on every frame.

final class LatestFrameRenderer: NSObject, VideoRenderer, @unchecked Sendable {
    enum SnapError: LocalizedError {
        case noFrameYet
        case unsupportedBuffer
        case encodeFailed
        var errorDescription: String? {
            switch self {
            case .noFrameYet: return "no frame from the local video track yet — wait a moment after Connect"
            case .unsupportedBuffer: return "local video frame buffer is not a CVPixelBuffer (unexpected codec path)"
            case .encodeFailed: return "JPEG encoding failed"
            }
        }
    }

    private let lock = NSLock()
    private var latestPixelBuffer: CVPixelBuffer?
    private var latestRotation: VideoRotation = ._0
    private let ciContext = CIContext(options: nil)

    // VideoRenderer protocol: we don't drive AdaptiveStream, just observe.
    @MainActor var isAdaptiveStreamEnabled: Bool { false }
    @MainActor var adaptiveStreamSize: CGSize { .zero }

    nonisolated func render(frame: VideoFrame) {
        guard let pb = frame.toCVPixelBuffer() else { return }
        lock.lock()
        latestPixelBuffer = pb
        latestRotation = frame.rotation
        lock.unlock()
    }

    func snapJPEG(quality: CGFloat) throws -> Data {
        lock.lock()
        let pb = latestPixelBuffer
        let rot = latestRotation
        lock.unlock()
        guard let pb else { throw SnapError.noFrameYet }

        var image = CIImage(cvPixelBuffer: pb)
        // Bake LiveKit's logical rotation into the pixels. Phone cameras in
        // landscape orientation come through as ._90/._270 most of the time.
        switch rot {
        case ._90:  image = image.oriented(.right)
        case ._180: image = image.oriented(.down)
        case ._270: image = image.oriented(.left)
        default: break
        }

        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        guard let data = ciContext.jpegRepresentation(
            of: image,
            colorSpace: colorSpace,
            options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: quality]
        ) else { throw SnapError.encodeFailed }
        return data
    }
}

// MARK: - RoomDelegate

extension LiveKitController: RoomDelegate {

    nonisolated func room(_ room: Room,
                          participant: RemoteParticipant?,
                          didSubscribeTrack publication: RemoteTrackPublication) {
        Task { @MainActor in
            // Participant.identity is a `Identity?` (the SDK's wrapper around
            // a string); pull .stringValue for human-readable logging.
            let who = participant?.identity?.stringValue ?? "?"
            self.logger.log(
                "livekit: subscribed \(publication.kind) from \(who)",
                level: .ok
            )
            // Remote audio = the worker's TTS. iOS routes it through the
            // current AVAudioSession by default; nothing more to do here.
        }
    }

    // LiveKit Swift SDK 2.x renamed these two delegate methods:
    //   room(_:didConnect:)        -> roomDidConnect(_:)
    //   room(_:didDisconnect:)     -> room(_:didDisconnectWithError:)
    // The SDK marks the old names @available(*, unavailable, renamed:),
    // so the build won't compile if we keep them.

    nonisolated func roomDidConnect(_ room: Room) {
        Task { @MainActor in self.participantCount = room.allParticipants.count }
    }

    nonisolated func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
        Task { @MainActor in
            self.state = .disconnected
            if let error {
                self.logger.log("livekit: disconnect — \(error.localizedDescription)", level: .err)
            }
        }
    }
}
