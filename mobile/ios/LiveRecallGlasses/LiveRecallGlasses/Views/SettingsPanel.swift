// SettingsPanel.swift
//
// "Backend URL / Identity / Room" form. Same three inputs as
// phone/glasses.html, plus a toggle for the glasses-vs-phone source so the
// app is usable end-to-end before the Meta Wearables Swift package has
// been added to the build.

import SwiftUI

struct SettingsPanel: View {
    @EnvironmentObject var config: AppConfig
    var disabled: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            field(label: "Backend URL", placeholder: "http://192.168.1.100:8000",
                  text: $config.backendURL, keyboard: .URL)

            HStack(spacing: 10) {
                field(label: "Identity", placeholder: "kalle-glasses",
                      text: $config.identity, keyboard: .default)
                field(label: "Room", placeholder: "liverecall-demo",
                      text: $config.room, keyboard: .default)
            }

            Toggle(isOn: $config.preferGlasses) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Try Ray-Ban Meta glasses (experimental)")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(Theme.text)
                    Text(config.preferGlasses
                         ? "Will try DAT first, fall back to phone camera if devices don't enumerate within 15s"
                         : "Phone camera only — recommended for the demo (no DAT round-trip)")
                        .font(.system(size: 11))
                        .foregroundColor(Theme.muted)
                }
            }
            .tint(Theme.accent)
        }
        .padding(14)
        .background(Theme.panel)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(Theme.panelBorder, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .disabled(disabled)
        .opacity(disabled ? 0.6 : 1.0)
    }

    @ViewBuilder
    private func field(label: String, placeholder: String,
                       text: Binding<String>, keyboard: UIKeyboardType) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.system(size: 12))
                .foregroundColor(Theme.muted)
            TextField(placeholder, text: text)
                .keyboardType(keyboard)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .foregroundColor(Theme.text)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Theme.background)
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(Theme.panelBorder, lineWidth: 1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 10))
        }
    }
}
