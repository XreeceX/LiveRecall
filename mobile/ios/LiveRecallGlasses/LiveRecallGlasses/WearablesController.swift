// WearablesController.swift
//
// Wraps the Meta Wearables Device Access Toolkit (`wearables-dat-ios`) so the
// rest of the app sees a tiny surface area:
//
//   1. Register with the Meta AI app (deep-link round trip via the URL
//      scheme declared in Info.plist as `AppLinkURLScheme`).
//   2. Ask the user for camera permission on the glasses.
//   3. Open a DeviceSession + StreamSession at 24 fps medium resolution
//      (those are the values we documented in the build plan).
//   4. Hand each video frame to a callback the LiveKit publisher
//      installs at startup.
//
// Building without toolkit access:
//   - Until you've imported the WearablesDAT Swift package and uncommented it
//     in `project.yml`, this file compiles in a "stub" mode that just logs
//     "wearables-sdk-unavailable". The phone-camera fallback in
//     LiveKitController keeps the app usable.
//   - Once the package is wired up, set USE_WEARABLES_SDK = true at the top
//     and replace the stub bodies with the real toolkit calls below.
//
// Reference (developer preview):
//   https://wearables.developer.meta.com/docs/build-integration-ios/

import Foundation
import UIKit

/// Toggle once the WearablesDAT Swift package has been added to the target.
/// Until then we fall back to the phone camera path so the app still demos.
private let USE_WEARABLES_SDK = false

/// One frame from the glasses, normalized for the LiveKit publisher.
/// We carry a `CVPixelBuffer` because that's the cheapest input for
/// LiveKit's BufferCapturer (no extra copy on the way to the encoder).
struct GlassesVideoFrame {
    let pixelBuffer: CVPixelBuffer
    let timestampNs: UInt64
    let rotation: Int        // 0 / 90 / 180 / 270
}

@MainActor
final class WearablesController: ObservableObject {

    enum State: Equatable {
        case idle
        case registering
        case awaitingPermission
        case ready                // device paired, waiting to start streaming
        case streaming
        case unavailable(String)  // toolkit not available or permission denied
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var deviceName: String? = nil

    /// Installed by LiveKitController.start() — every glasses frame is
    /// forwarded into the LiveKit BufferCapturer through this closure.
    var onVideoFrame: ((GlassesVideoFrame) -> Void)?

    private let logger = SessionLogger.shared

    // MARK: - Public API consumed by SessionController

    func register() async {
        guard USE_WEARABLES_SDK else {
            logger.log("wearables: SDK not linked — falling back to phone camera", level: .warn)
            state = .unavailable("WearablesDAT package not added to this build.")
            return
        }
        state = .registering
        // TODO(WearablesDAT): replace with the real toolkit call:
        //
        //   for await regState in wearables.registrationStateStream() {
        //       switch regState { ... }
        //   }
        //
        // The Meta AI app handles the registration UI; iOS deep-links back
        // through our `AppLinkURLScheme` and we forward that URL via
        // SessionController.handleOpenURL → handleDeepLink(_:).
        logger.log("wearables: register() (TODO — wire WearablesDAT.register)", level: .warn)
    }

    func handleDeepLink(_ url: URL) async {
        guard USE_WEARABLES_SDK else { return }
        logger.log("wearables: deep-link \(url.absoluteString)", level: .info)
        // TODO(WearablesDAT): forward to wearables.handleAuthCallback(url:)
    }

    func requestCameraPermission() async {
        guard USE_WEARABLES_SDK else { return }
        state = .awaitingPermission
        // TODO(WearablesDAT): wearables.requestPermission(.camera)
        logger.log("wearables: requestCameraPermission() (TODO)", level: .warn)
    }

    func startStream() async {
        guard USE_WEARABLES_SDK else {
            logger.log("wearables: startStream skipped (SDK unavailable)", level: .warn)
            return
        }
        // TODO(WearablesDAT): build StreamSessionConfig + addStream + listen.
        //
        //   let config = StreamSessionConfig(
        //       videoCodec: .raw,
        //       resolution: .medium,
        //       frameRate: 24
        //   )
        //   guard let stream = try? session.addStream(config: config) else { ... }
        //
        //   _ = stream.videoFramePublisher.listen { [weak self] frame in
        //       guard let pixelBuffer = frame.pixelBuffer else { return }
        //       let ts = UInt64(frame.timestamp * 1_000_000_000)
        //       self?.onVideoFrame?(.init(
        //           pixelBuffer: pixelBuffer,
        //           timestampNs: ts,
        //           rotation: 0
        //       ))
        //   }
        //   await stream.start()
        state = .streaming
        logger.log("wearables: startStream() (TODO)", level: .warn)
    }

    func stopStream() async {
        guard USE_WEARABLES_SDK else { return }
        // TODO(WearablesDAT): stream.stop()
        state = .ready
        logger.log("wearables: stopStream()", level: .info)
    }
}
