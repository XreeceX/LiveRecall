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
    @State private var snapQuestion: String = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                header
                SettingsPanel(disabled: session.isLive || isWorking)
                PovPreview(track: session.livekit.localVideoTrack)
                actionRow
                snapPanel
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
                Text(config.preferGlasses ? "· glasses + phone fallback" : "· phone camera")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.muted)
            }
            Spacer()
            HStack(spacing: 6) {
                modePill
                ConnectionPill(status: session.status)
            }
        }
        .padding(.top, 12)
    }

    private var modePill: some View {
        let isGlasses = config.preferGlasses
        let label = isGlasses ? "Glasses · POV" : "Phone · POV"
        let textColor = isGlasses ? Color(hex: 0xDDD6FE) : Color(hex: 0xCBD5E1)
        let strokeOpacity = isGlasses ? 0.45 : 0.30
        let bgGradient: LinearGradient = isGlasses
            ? LinearGradient(
                colors: [Theme.accent.opacity(0.25), Theme.accent2.opacity(0.18)],
                startPoint: .leading, endPoint: .trailing
              )
            : LinearGradient(
                colors: [Color.white.opacity(0.10), Color.white.opacity(0.04)],
                startPoint: .leading, endPoint: .trailing
              )
        return Text(label)
            .font(.system(size: 11, weight: .semibold))
            .tracking(0.6)
            .textCase(.uppercase)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .foregroundColor(textColor)
            .background(bgGradient)
            .overlay(
                Capsule().stroke(Theme.accent.opacity(strokeOpacity), lineWidth: 1)
            )
            .clipShape(Capsule())
    }

    // MARK: - Connect / Leave

    private var connectButtonLabel: String {
        if session.isLive { return "Live" }
        if isWorking { return "Connecting…" }
        return config.preferGlasses ? "Connect (Ray-Ban POV)" : "Connect (phone POV)"
    }

    private var actionRow: some View {
        HStack(spacing: 10) {
            Button(action: { Task { await connect() } }) {
                Text(connectButtonLabel)
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

    // MARK: - Snap & ask
    //
    // Single-image retrieval. Captures one JPEG off the active glasses stream
    // (DAT photoDataPublisher) and POSTs it to /snap. If the question field
    // is non-empty the backend also kicks the question through Router →
    // Retrievers → Reranker → Answerer; the spoken answer comes back through
    // the LiveKit room audio track LiveKitController is already subscribed
    // to, so no extra wiring needed here. The button is disabled until the
    // glasses stream is actually delivering frames so we don't try to capture
    // off the phone-camera fallback (that path doesn't go through DAT).

    private var snapPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Snap & ask")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(Theme.text)
                Spacer()
                if !session.isLive {
                    Text("connect first")
                        .font(.system(size: 10))
                        .foregroundColor(Theme.muted)
                } else if session.snapInFlight {
                    Text("snapping…")
                        .font(.system(size: 10))
                        .foregroundColor(Theme.muted)
                }
            }

            // Mirrors the explanatory paragraph in phone/glasses.html so a
            // first-time user understands that:
            //  - Snap always runs the full pipeline (blank box → default
            //    "What am I looking at?", same as the phone client).
            //  - The continuous mic path is also live; ElevenLabs Scribe v2
            //    Realtime commits about a second after silence.
            //  - There is no wake word — just speak in plain English once the
            //    session is connected.
            Text("""
                 Grab one JPEG + kick off retrieval + answers on the dashboard. \
                 If you leave the box blank we still send "What am I looking at?" \
                 so Router → Answerer runs (same as typing it yourself). \
                 Speaking: after Connect, talk into the mic; ElevenLabs STT \
                 commits after ~1s of silence. This demo does not wake on \
                 "Hey Meta" — just ask in plain English.
                 """)
                .font(.system(size: 11))
                .foregroundColor(Theme.muted)
                .lineSpacing(2)
                .fixedSize(horizontal: false, vertical: true)

            TextField(
                "What am I looking at? (optional — blank uses this)",
                text: $snapQuestion
            )
            .textFieldStyle(.plain)
            .font(.system(size: 13))
            .foregroundColor(Theme.text)
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(Theme.background.opacity(0.6))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Theme.panelBorder, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .disabled(!session.isLive)

            Button(action: { Task { await snap() } }) {
                HStack(spacing: 8) {
                    if session.snapInFlight {
                        ProgressView()
                            .progressViewStyle(.circular)
                            .controlSize(.small)
                            .tint(Theme.background)
                    }
                    Text(session.snapInFlight ? "Snapping…" : "Snap & ask")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(Theme.background)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(Theme.povGradient.opacity(session.canSnap ? 1.0 : 0.45))
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .disabled(!session.canSnap)

            if let snap = session.lastSnap {
                snapResultLine(snap)
            }
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

    private func snapResultLine(_ snap: SnapResponse) -> some View {
        // Concise one-liner: what Vision saw + question_id if a question
        // also fired (so the user knows to look at the dashboard for the
        // reasoning trace).
        let visible = snap.text_visible.prefix(3).joined(separator: ", ")
        let summary = visible.isEmpty
            ? snap.objects.prefix(3).joined(separator: ", ")
            : visible
        return HStack(alignment: .top, spacing: 6) {
            Text("✓")
                .font(.system(size: 11, weight: .bold))
                .foregroundColor(Theme.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(summary.isEmpty ? "scene captured" : "saw \(summary)")
                    .font(.system(size: 11))
                    .foregroundColor(Theme.muted)
                if let qid = snap.question_id {
                    Text("question \(qid) — answer streaming back through LiveKit")
                        .font(.system(size: 10))
                        .foregroundColor(Theme.accent2)
                }
            }
            Spacer()
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

    private func snap() async {
        await session.snapAndAsk(question: snapQuestion, config: config)
        // Clear the question field after a successful Snap so the next tap
        // doesn't accidentally re-ask the same thing. Errors keep the text
        // around so the user can retry without retyping.
        if session.lastSnap?.question_id != nil {
            snapQuestion = ""
        }
    }
}

#Preview {
    ContentView()
        .environmentObject(SessionController())
        .environmentObject(AppConfig.shared)
        .environmentObject(SessionLogger.shared)
}
