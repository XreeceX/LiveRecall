// Theme.swift
//
// Color tokens lifted from phone/glasses.html so the iOS app reads as part
// of the same product family (purple/blue gradient on a near-black canvas,
// muted text in #94a3b8). Centralized here so views don't sprinkle
// hex literals everywhere.

import SwiftUI

enum Theme {
    static let background    = Color(hex: 0x0A0D18)
    static let panel         = Color(hex: 0x131A2A)
    static let panelBorder   = Color(hex: 0x1F2937)
    static let text          = Color(hex: 0xE5E7EB)
    static let muted         = Color(hex: 0x94A3B8)
    static let accent        = Color(hex: 0xA78BFA) // purple — glasses POV
    static let accent2       = Color(hex: 0x60A5FA) // blue
    static let warn          = Color(hex: 0xFBBF24)
    static let err           = Color(hex: 0xF87171)

    static let povGradient = LinearGradient(
        colors: [accent, accent2],
        startPoint: .leading,
        endPoint: .trailing
    )
}

extension Color {
    init(hex: UInt32, opacity: Double = 1.0) {
        self.init(
            .sRGB,
            red:   Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >>  8) & 0xFF) / 255,
            blue:  Double( hex        & 0xFF) / 255,
            opacity: opacity
        )
    }
}
