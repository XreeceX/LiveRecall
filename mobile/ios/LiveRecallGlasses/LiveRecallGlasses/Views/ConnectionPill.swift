// ConnectionPill.swift
//
// Status pill in the top-right of the screen. Mirrors the .pill / .pill.live
// / .pill.warn / .pill.err treatment from phone/glasses.html.

import SwiftUI

struct ConnectionPill: View {
    let status: SessionController.Status

    var body: some View {
        Text(label)
            .font(.system(size: 11, weight: .semibold))
            .tracking(0.6)
            .textCase(.uppercase)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(background)
            .foregroundColor(foreground)
            .clipShape(Capsule())
    }

    private var label: String {
        switch status {
        case .idle: return "disconnected"
        case .connecting: return "connecting…"
        case .live: return "live · POV"
        case .error: return "error"
        }
    }

    private var background: some View {
        Group {
            switch status {
            case .live:
                Theme.accent.opacity(0.18)
            case .connecting:
                Theme.warn.opacity(0.15)
            case .error:
                Theme.err.opacity(0.18)
            case .idle:
                Color(hex: 0x1F2937)
            }
        }
    }

    private var foreground: Color {
        switch status {
        case .live: return Theme.accent
        case .connecting: return Theme.warn
        case .error: return Theme.err
        case .idle: return Theme.muted
        }
    }
}
