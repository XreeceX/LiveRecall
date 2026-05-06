// AppConfig.swift
//
// User-facing settings persisted to UserDefaults. The form fields in
// SettingsView read and write these values so the next launch picks up
// where we left off (same UX as phone/glasses.html, which uses
// localStorage for the same purpose).

import Foundation
import SwiftUI

@MainActor
final class AppConfig: ObservableObject {
    static let shared = AppConfig()

    // MARK: - Persisted user inputs

    @AppStorage("backendURL") var backendURL: String = "http://localhost:8000"
    @AppStorage("identity") var identity: String = "kalle-glasses"
    @AppStorage("room") var room: String = "liverecall-demo"

    // Camera source. Default OFF: Meta DAT device enumeration is unreliable
    // in our test environment (devicesStream stays empty even after a
    // successful registration + camera permission grant — Meta's known
    // issue, not a bug in our integration), so the demo path uses the
    // phone camera and Snap & ask is wired through LiveKit's local video
    // track. Flip ON only if you have a paired Ray-Ban that's actively
    // streaming to Meta AI right now and want to try the DAT path.
    @AppStorage("preferGlasses") var preferGlasses: Bool = false

    var sessionId: String {
        // Same convention as phone/glasses.html: the LiveKit room name is
        // "liverecall-{session_id}", so strip the prefix to recover it.
        room.hasPrefix("liverecall-") ? String(room.dropFirst("liverecall-".count)) : room
    }

    private init() {}
}
