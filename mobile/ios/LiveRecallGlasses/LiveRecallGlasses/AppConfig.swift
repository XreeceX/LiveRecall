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

    // Camera source. Glasses requires the Wearables toolkit + a paired Meta
    // device; phone uses the on-device camera so the app is still usable as
    // a stand-in when the toolkit isn't wired up yet (matches the
    // phone/glasses.html webcam fallback story).
    @AppStorage("preferGlasses") var preferGlasses: Bool = true

    var sessionId: String {
        // Same convention as phone/glasses.html: the LiveKit room name is
        // "liverecall-{session_id}", so strip the prefix to recover it.
        room.hasPrefix("liverecall-") ? String(room.dropFirst("liverecall-".count)) : room
    }

    private init() {}
}
