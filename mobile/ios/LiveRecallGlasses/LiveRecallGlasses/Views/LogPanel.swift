// LogPanel.swift
//
// Same role as the <div id="log"> block in phone/glasses.html — a rolling
// monospace feed of session events, color-coded by severity, newest at the
// top. Backed by SessionLogger.shared so any module in the app can write
// into it from the main actor.

import SwiftUI

struct LogPanel: View {
    @EnvironmentObject var logger: SessionLogger

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Log")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
                Spacer()
                Button(action: { logger.clear() }) {
                    Text("clear")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(Theme.muted)
                }
                .buttonStyle(.plain)
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

    private func color(for level: SessionLogger.Level) -> Color {
        switch level {
        case .info: return Theme.muted
        case .ok:   return Theme.accent
        case .warn: return Theme.warn
        case .err:  return Theme.err
        }
    }
}
