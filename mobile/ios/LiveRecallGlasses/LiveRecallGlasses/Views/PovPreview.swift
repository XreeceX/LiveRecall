// PovPreview.swift
//
// 16:9 viewport that hosts the LiveKit VideoView for whichever local video
// track is currently published — glasses POV via BufferCapturer or the
// phone camera fallback. Visual treatment matches the .pov-frame block in
// phone/glasses.html (purple border, dashed reticle, "POV · first-person"
// chip in the top-left).

import SwiftUI
import LiveKit

struct PovPreview: View {
    let track: LocalVideoTrack?

    var body: some View {
        ZStack(alignment: .topLeading) {
            // Black backdrop so a missing track still draws the right shape.
            Theme.background

            if let track {
                // SwiftUIVideoView ships with the LiveKit Swift SDK and
                // auto-attaches/detaches the underlying renderer.
                SwiftUIVideoView(track)
                    .clipShape(RoundedRectangle(cornerRadius: 16))
            } else {
                VStack(spacing: 6) {
                    Text("waiting for video")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(Theme.muted)
                    Text("connect to start streaming")
                        .font(.system(size: 11))
                        .foregroundColor(Theme.muted.opacity(0.7))
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            // Reticle: dashed inset hint that this is "first-person".
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                .foregroundColor(Theme.accent.opacity(0.25))
                .padding(8)

            Text("POV · FIRST-PERSON")
                .font(.system(size: 10, weight: .semibold))
                .tracking(0.8)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Theme.background.opacity(0.6))
                .overlay(
                    RoundedRectangle(cornerRadius: 6)
                        .stroke(Theme.accent.opacity(0.35), lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .foregroundColor(Color(hex: 0xDDD6FE))
                .padding(10)
        }
        .aspectRatio(16/9, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .stroke(Theme.accent.opacity(0.3), lineWidth: 1)
        )
    }
}
