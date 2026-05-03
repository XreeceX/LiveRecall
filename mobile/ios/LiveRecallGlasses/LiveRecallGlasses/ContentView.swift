// ContentView.swift
//
// Top-level screen. Layout deliberately mirrors phone/glasses.html so a
// teammate flipping between the web stand-in and the iOS app sees the same
// shape: header pill, settings panel, POV preview, connect/leave row, log.

import SwiftUI

struct ContentView: View {
    @EnvironmentObject var session: SessionController
    @EnvironmentObject var config: AppConfig

    @State private var isWorking: Bool = false

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                header
                SettingsPanel(disabled: session.isLive || isWorking)
                PovPreview(track: session.livekit.localVideoTrack)
                actionRow
                LogPanel()
                footer
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .background(
            ZStack {
                Theme.background
                RadialGradient(
                    colors: [Theme.accent.opacity(0.18), .clear],
                    center: .topTrailing, startRadius: 20, endRadius: 600
                )
                RadialGradient(
                    colors: [Theme.accent2.opacity(0.12), .clear],
                    center: .bottomLeading, startRadius: 10, endRadius: 500
                )
            }
            .ignoresSafeArea()
        )
        .foregroundColor(Theme.text)
    }

    // MARK: - Header

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("LiveRecall")
                    .font(.system(size: 20, weight: .semibold))
                Text("· glasses (POV)")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
            }
            Spacer()
            HStack(spacing: 6) {
                glassesPill
                ConnectionPill(status: session.status)
            }
        }
        .padding(.top, 12)
    }

    private var glassesPill: some View {
        Text("Glasses · POV")
            .font(.system(size: 11, weight: .semibold))
            .tracking(0.6)
            .textCase(.uppercase)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .foregroundColor(Color(hex: 0xDDD6FE))
            .background(
                LinearGradient(
                    colors: [Theme.accent.opacity(0.25), Theme.accent2.opacity(0.18)],
                    startPoint: .leading, endPoint: .trailing
                )
            )
            .overlay(
                Capsule().stroke(Theme.accent.opacity(0.45), lineWidth: 1)
            )
            .clipShape(Capsule())
    }

    // MARK: - Connect / Leave

    private var actionRow: some View {
        HStack(spacing: 10) {
            Button(action: { Task { await connect() } }) {
                Text(session.isLive ? "Live" : "Connect (Ray-Ban POV)")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(Theme.background)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(Theme.povGradient)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(session.isLive || isWorking)

            Button(action: { Task { await leave() } }) {
                Text("Leave")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(Theme.err)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(Theme.err.opacity(0.18))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(!session.isLive || isWorking)
        }
    }

    // MARK: - Footer

    private var footer: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Capture path")
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(Theme.text)
            Text("""
                 Glasses → Bluetooth → this app (Wearables Device Access Toolkit) \
                 → Wi-Fi/LTE → LiveKit room liverecall-{session} → backend worker \
                 → Vision/Router/etc. Backend already accepts capture_mode=glasses; \
                 no server changes required.
                 """)
                .font(.system(size: 11))
                .foregroundColor(Theme.muted)
                .lineSpacing(2)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.panel)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Theme.panelBorder, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    // MARK: - Actions

    private func connect() async {
        isWorking = true
        await session.connect(config: config)
        isWorking = false
    }

    private func leave() async {
        isWorking = true
        await session.disconnect()
        isWorking = false
    }
}

#Preview {
    ContentView()
        .environmentObject(SessionController())
        .environmentObject(AppConfig.shared)
        .environmentObject(SessionLogger.shared)
}
