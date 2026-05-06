// LogPanel.swift
//
// Same role as the <div id="log"> block in phone/glasses.html — a rolling
// monospace feed of session events, color-coded by severity, newest at the
// top. Backed by SessionLogger.shared so any module in the app can write
// into it from the main actor.

import SwiftUI
import UIKit

struct LogPanel: View {
    @EnvironmentObject var logger: SessionLogger

    /// Brief "Copied" toast on the Copy button — much less invasive than a
    /// system overlay and works without iOS 16's UIPasteControl.
    @State private var copiedAt: Date? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Log")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
                Spacer()
                Button(action: copyToClipboard) {
                    Text(copyLabel)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(copyColor)
                }
                .buttonStyle(.plain)
                Button(action: { logger.clear() }) {
                    Text("clear")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(Theme.muted)
                }
                .buttonStyle(.plain)
                .padding(.leading, 12)
            }

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    ForEach(logger.entries) { entry in
                        Text(line(for: entry))
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundColor(color(for: entry.level))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .frame(height: 160)
            .padding(10)
            .background(Color(hex: 0x060914))
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .padding(14)
        .background(Theme.panel)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Theme.panelBorder, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func line(for e: SessionLogger.Entry) -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return "[\(f.string(from: e.timestamp))] \(e.message)"
    }

    /// Plain-text rendering used by the Copy button. Order matches what's
    /// shown on screen (newest first, oldest last) so a paste into chat
    /// reads the same as the panel.
    private func plainTextSnapshot() -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        let header = "LiveRecall iOS log — captured \(ISO8601DateFormatter().string(from: Date()))"
        let body = logger.entries
            .map { e in
                let lvl = String(describing: e.level).uppercased()
                return "[\(f.string(from: e.timestamp))] [\(lvl)] \(e.message)"
            }
            .joined(separator: "\n")
        return header + "\n" + body
    }

    private func copyToClipboard() {
        UIPasteboard.general.string = plainTextSnapshot()
        copiedAt = Date()
        // Auto-revert the "copied" label after 2 s so the button is ready
        // for the next paste without the user having to tap anywhere else.
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if let t = copiedAt, Date().timeIntervalSince(t) >= 2 {
                copiedAt = nil
            }
        }
    }

    private var copyLabel: String {
        guard let copiedAt else { return "copy" }
        return Date().timeIntervalSince(copiedAt) < 2 ? "copied ✓" : "copy"
    }

    private var copyColor: Color {
        copiedAt == nil ? Theme.muted : Theme.accent
    }

    private func color(for level: SessionLogger.Level) -> Color {
        switch level {
        case .info: return Theme.muted
        case .ok:   return Theme.accent
        case .warn: return Theme.warn
        case .err:  return Theme.err
        }
    }
}
