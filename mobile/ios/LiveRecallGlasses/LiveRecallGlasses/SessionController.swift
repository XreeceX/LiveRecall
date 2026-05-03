// SessionController.swift
//
// One object the UI talks to. It composes:
//   - WearablesController (Meta toolkit)
//   - LiveKitController (LiveKit Room + publishers)
//   - BackendClient (LiveRecall /token)
//
// The user-visible flow:
//   1. Tap "Connect" in ContentView.
//   2. Ask WearablesController to register + start its stream.
//   3. Ask BackendClient for a JWT scoped to liverecall-{session_id}, with
//      capture_mode=glasses so the dashboard pill flips and Vision uses
//      the first-person POV hint.
//   4. Hand (url, token) to LiveKitController, which publishes audio + video.

import Foundation
import SwiftUI

@MainActor
final class SessionController: ObservableObject {

    enum Status: Equatable {
        case idle
        case connecting
        case live
        case error(String)
    }

    @Published private(set) var status: Status = .idle
    @Published private(set) var lastTokenURL: String? = nil

    let wearables = WearablesController()
    let livekit = LiveKitController()

    private let logger = SessionLogger.shared

    var isLive: Bool {
        if case .live = status { return true }
        return false
    }

    // MARK: - Lifecycle

    func connect(config: AppConfig) async {
        status = .connecting

        if config.preferGlasses {
            await wearables.register()
            await wearables.requestCameraPermission()
            await wearables.startStream()
        } else {
            logger.log("session: phone-camera mode (glasses skipped)", level: .info)
        }

        do {
            let token = try await BackendClient.shared.requestToken(
                backendURL: config.backendURL,
                identity: config.identity,
                room: config.room,
                captureMode: "glasses" // see backend/main.py /token contract
            )
            lastTokenURL = token.url
            logger.log("backend: token ok (room=\(token.room), mode=\(token.capture_mode))", level: .ok)

            // If WearablesController couldn't bring up a real stream, fall
            // back to the phone camera so we still produce a video track.
            let useGlassesSource: Bool
            switch wearables.state {
            case .streaming: useGlassesSource = true
            default: useGlassesSource = false
            }

            await livekit.connect(
                url: token.url,
                token: token.token,
                useGlassesSource: useGlassesSource,
                wearables: useGlassesSource ? wearables : nil
            )

            switch livekit.state {
            case .connected: status = .live
            case .failed(let m): status = .error(m)
            default: status = .error("LiveKit did not reach connected state")
            }
        } catch {
            status = .error(error.localizedDescription)
            logger.log("session: connect failed — \(error.localizedDescription)", level: .err)
        }
    }

    func disconnect() async {
        await livekit.disconnect()
        await wearables.stopStream()
        status = .idle
    }

    func handleOpenURL(_ url: URL) async {
        await wearables.handleDeepLink(url)
    }
}
