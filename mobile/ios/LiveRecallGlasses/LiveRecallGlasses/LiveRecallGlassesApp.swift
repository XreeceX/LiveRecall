// LiveRecallGlassesApp.swift
//
// Entry point for the iOS publisher app.
//
// Mirrors phone/glasses.html in the parent repo: connects to the LiveRecall
// backend, asks for a token with capture_mode="glasses", and publishes a
// LiveKit room participant whose video track carries Ray-Ban Meta POV frames
// (or the phone camera as a fallback when the glasses aren't paired).

import SwiftUI

@main
struct LiveRecallGlassesApp: App {
    @StateObject private var session = SessionController()
    @StateObject private var config = AppConfig.shared
    @StateObject private var logger = SessionLogger.shared

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
