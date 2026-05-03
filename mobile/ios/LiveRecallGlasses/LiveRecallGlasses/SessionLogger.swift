// SessionLogger.swift
//
// Tiny in-process log feed for the on-screen "Log" panel. Every component
// writes through SessionLogger.shared.log("…"), which appends to a SwiftUI
// observable so the LogView re-renders automatically.

import Foundation
import os.log

@MainActor
final class SessionLogger: ObservableObject {
    static let shared = SessionLogger()

    enum Level: String {
        case info, ok, warn, err
    }

    struct Entry: Identifiable {
        let id = UUID()
        let timestamp: Date
        let level: Level
        let message: String
    }

    @Published private(set) var entries: [Entry] = []
    private let osLog = Logger(subsystem: "com.liverecall.glasses", category: "session")
    private let maxEntries = 500

    private init() {}

    func log(_ message: String, level: Level = .info) {
        let entry = Entry(timestamp: Date(), level: level, message: message)
        entries.insert(entry, at: 0)
        if entries.count > maxEntries {
            entries.removeLast(entries.count - maxEntries)
        }
        switch level {
        case .info, .ok: osLog.info("\(message, privacy: .public)")
        case .warn: osLog.warning("\(message, privacy: .public)")
        case .err: osLog.error("\(message, privacy: .public)")
        }
    }

    func clear() { entries.removeAll() }
}
