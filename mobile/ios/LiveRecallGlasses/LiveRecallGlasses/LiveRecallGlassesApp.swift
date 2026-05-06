// LiveRecallGlassesApp.swift
//
// Entry point for the iOS publisher app.
//
// Mirrors phone/glasses.html in the parent repo: connects to the LiveRecall
// backend, asks for a token with capture_mode="glasses", and publishes a
// LiveKit room participant whose video track carries Ray-Ban Meta POV frames
// (or the phone camera as a fallback when the glasses aren't paired).
//
// At launch we also call Wearables.configure() exactly once. The DAT docs
// require this before any other Wearables.shared call, including the ones
// in SessionController.connect() that start the registration handshake.

import SwiftUI
import MWDATCore

@main
struct LiveRecallGlassesApp: App {
    @StateObject private var session = SessionController()
    @StateObject private var config = AppConfig.shared
    @StateObject private var logger = SessionLogger.shared

    init() {
        // configure() is a one-shot static; calling it twice throws
        // WearablesError.alreadyConfigured. Hot reloads in SwiftUI Previews
        // can re-run init(), so we swallow that one specific case rather
        // than crashing during a preview refresh.
        //
        // App.init() isn't main-actor-isolated, but SessionLogger.shared is,
        // so the log calls hop onto a MainActor task. configure() itself is
        // safe to call from any actor.
        let outcome: ConfigureOutcome
        do {
            try Wearables.configure()
            outcome = .ok
        } catch {
            let msg = String(describing: error).lowercased()
            outcome = msg.contains("alreadyconfigured")
                ? .alreadyConfigured
                : .failed(error.localizedDescription)
        }
        Task { @MainActor in
            switch outcome {
            case .ok:
                SessionLogger.shared.log("wearables: Wearables.configure() ok", level: .ok)
            case .alreadyConfigured:
                SessionLogger.shared.log("wearables: already configured (preview reload?)", level: .info)
            case .failed(let detail):
                SessionLogger.shared.log("wearables: configure failed — \(detail)", level: .err)
            }
        }
    }

    private enum ConfigureOutcome {
        case ok
        case alreadyConfigured
        case failed(String)
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(session)
                .environmentObject(config)
                .environmentObject(logger)
                .preferredColorScheme(.dark)
                // The Meta AI app uses our registered URL scheme to call back
                // during Wearables registration. SessionController.handleOpenURL
                // forwards the deep link into the toolkit.
                .onOpenURL { url in
                    Task { await session.handleOpenURL(url) }
                }
        }
    }
}
