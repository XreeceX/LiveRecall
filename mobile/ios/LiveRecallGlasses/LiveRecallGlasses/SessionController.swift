// SessionController.swift
//
// One object the UI talks to. It composes:
//   - WearablesController (Meta toolkit)
//   - LiveKitController (LiveKit Room + publishers)
//   - BackendClient (LiveRecall /token)
//
// The user-visible flow:
//   1. Tap "Connect" in ContentView.
//   2. Ask WearablesController to register + start its stream.
//   3. Ask BackendClient for a JWT scoped to liverecall-{session_id}, with
//      capture_mode=glasses so the dashboard pill flips and Vision uses
//      the first-person POV hint.
//   4. Hand (url, token) to LiveKitController, which publishes audio + video.

import Foundation
import SwiftUI

@MainActor
final class SessionController: ObservableObject {

    enum Status: Equatable {
        case idle
        case connecting
        case live
        case error(String)
    }

    @Published private(set) var status: Status = .idle
    @Published private(set) var lastTokenURL: String? = nil

    /// True while a Snap & ask round-trip is in flight — used by the UI to
    /// disable the Snap button and show a spinner.
    @Published private(set) var snapInFlight: Bool = false

    /// Most recent SnapResponse from POST /snap, surfaced for the UI to
    /// render a one-line "saw X" confirmation.
    @Published private(set) var lastSnap: SnapResponse? = nil

    let wearables = WearablesController()
    let livekit = LiveKitController()

    private let logger = SessionLogger.shared

    var isLive: Bool {
        if case .live = status { return true }
        return false
    }

    /// True when the LiveKit session is up. Snap & ask works against either
    /// the glasses stream (uses DAT photoDataPublisher for a high-res still)
    /// or the phone camera (uses LiveKit's local video track via
    /// LatestFrameRenderer for a 720p still). The backend /snap endpoint
    /// doesn't care which produced the JPEG.
    var canSnap: Bool {
        isLive && !snapInFlight
    }

    // MARK: - Lifecycle

    func connect(config: AppConfig) async {
        status = .connecting

        var glassesReady = false
        if config.preferGlasses {
            await wearables.register()
            // Wait for the device to appear in DAT's catalog BEFORE asking
            // for permission. requestPermission(.camera) throws
            // PermissionError 0 (.noDevice) if it runs before the device
            // is enumerated, which then falls through to a deep-link path
            // that races with permission grant fanout and times out at
            // PermissionError 5 (.requestTimeout) — the symptom we saw
            // in 19:03 logs. With the device confirmed first, Meta AI
            // can surface the camera permission prompt against the right
            // device context.
            logger.log("session: waiting up to 8s for device to appear in DAT catalog before asking permission", level: .info)
            let deviceFirst = await wearables.awaitDevice(timeoutSeconds: 8.0)
            if !deviceFirst {
                logger.log(
                    "session: no device after 8s — common causes: glasses in case/asleep, BT dropped from iPhone. Take glasses out of case + put them on, wait for the Meta AI 'connected' tone, then try again.",
                    level: .warn
                )
            }
            // Now request permission. Retry up to 3 times — each retry
            // re-opens Meta AI which gives the user another chance to
            // accept the prompt if they missed it the first time, or
            // recovers from a transient .requestTimeout while Meta AI
            // was foregrounded for something else.
            await wearables.requestCameraPermissionWithRetry(maxAttempts: 3)
            // Even if we didn't see the device the first time, give it
            // another shot now that permission is granted — the device
            // list often updates *because* of the permission grant per
            // Meta's docs.
            _ = await wearables.awaitDevice(timeoutSeconds: 8.0)
            // We always try startStream regardless. createSession reads
            // Wearables.shared.devices synchronously via AutoDeviceSelector,
            // and `noEligibleDevice` here will be a clean signal that
            // either (a) the device is gone again, or (b) permission
            // wasn't actually persistently granted ("Allow Once" expires
            // before the next call).
            await wearables.startStream()
            // 30s window for the stream. Why this long: DAT's own internal
            // timeout for a wedged camera is ~30s before it fires
            // .timeout on errorPublisher. If we bail at 12s we drop a
            // connection that might still recover AND we miss the
            // SDK's own diagnostic. The "wake" portion is normally
            // 1-7s on cold start (audible tone in glasses speakers);
            // anything past that means the camera service is in a slow
            // recovery path. Worst case we wait an extra 18s on a
            // failed attempt — still cheaper than a Disconnect+Retry
            // cycle which costs ~25s of round-tripping.
            glassesReady = await wearables.awaitStreaming(timeoutSeconds: 30.0)
            if !glassesReady {
                logger.log(
                    "session: glasses didn't reach .streaming within 30s — falling back to phone camera. If you saw 'stream error — timeout' above, the glasses' camera service is wedged: PHYSICAL FIX → hold the temple-side power button for 10-15s to power the glasses off, wait 30s, put back on, retry.",
                    level: .warn
                )
            }
        } else {
            logger.log("session: phone-camera mode (glasses skipped)", level: .info)
        }

        do {
            // If WearablesController couldn't bring up a real stream we fall
            // back to the phone camera. Pick the capture_mode we report to
            // the backend up-front so the dashboard pill matches reality:
            // running the agent prompts as "glasses POV" when the source is
            // actually the phone would mislead Vision's first-person hint.
            let useGlassesSource = glassesReady && wearables.state == .streaming
            let captureMode = useGlassesSource ? "glasses" : "phone"

            // Per-session identity suffix mirrors phone/glasses.html (commit
            // 061f1e5): every connect uses a fresh `<base>-<5 random>` so
            // reconnects don't kick the previous LiveKit identity. The
            // user-set value in Settings is kept as the prefix so dashboard
            // logs still read e.g. `kalle-glasses-x7q9p`.
            let identity = "\(config.identity)-\(Self.randomIdentitySuffix())"
            let token = try await BackendClient.shared.requestToken(
                backendURL: config.backendURL,
                identity: identity,
                room: config.room,
                captureMode: captureMode // see backend/main.py /token contract
            )
            lastTokenURL = token.url
            logger.log("backend: token ok (room=\(token.room), mode=\(token.capture_mode), identity=\(identity))", level: .ok)

            await livekit.connect(
                url: token.url,
                token: token.token,
                useGlassesSource: useGlassesSource,
                wearables: useGlassesSource ? wearables : nil
            )

            switch livekit.state {
            case .connected: status = .live
            case .failed(let m): status = .error(m)
            default: status = .error("LiveKit did not reach connected state")
            }
        } catch {
            status = .error(error.localizedDescription)
            logger.log("session: connect failed — \(error.localizedDescription)", level: .err)
        }
    }

    func disconnect() async {
        await livekit.disconnect()
        await wearables.stopStream()
        status = .idle
    }

    func handleOpenURL(_ url: URL) async {
        await wearables.handleDeepLink(url)
    }

    /// 5-char lowercase alphanumeric — same shape as
    /// `Math.random().toString(36).slice(2, 7)` in phone/glasses.html.
    private static func randomIdentitySuffix() -> String {
        let chars = Array("abcdefghijklmnopqrstuvwxyz0123456789")
        return String((0..<5).map { _ in chars.randomElement()! })
    }

    // MARK: - Snap & ask

    /// Capture a single still off the glasses stream and POST it to /snap on
    /// the backend. If the snap field is blank we still send a default
    /// question ("What am I looking at?") so the full pipeline (Router →
    /// Retrievers → Reranker → Answerer → TTS) runs every time. This
    /// matches phone/glasses.html (commit 86c4c8a) — the alternative
    /// "scene only" behavior was confusing because Snap silently produced
    /// nothing audible. The TTS arrives via the LiveKit audio track
    /// LiveKitController is already subscribed to, so no extra wiring here.
    static let defaultSnapQuestion = "What am I looking at?"

    func snapAndAsk(question: String?, config: AppConfig) async {
        guard isLive else {
            logger.log("snap: cannot snap — session is not live", level: .warn)
            return
        }
        snapInFlight = true
        defer { snapInFlight = false }
        do {
            let started = Date()
            // Pick the source that's actually streaming. Glasses path uses
            // DAT photoDataPublisher (high-res still, ~1-2 MP). Phone path
            // pulls the most recent frame off LiveKit's local video track
            // via LatestFrameRenderer (720p, JPEG'd via CoreImage). Both
            // paths produce JPEG bytes with the same downstream contract.
            let jpeg: Data
            let captureMode: String
            if wearables.state == .streaming {
                jpeg = try await wearables.capturePhoto()
                captureMode = "glasses"
            } else {
                jpeg = try livekit.snapLocalFrameJPEG()
                captureMode = "phone"
            }
            // base64 without line breaks. The backend strips a `data:` prefix
            // if present, but we never add one to keep the payload small.
            let imageB64 = jpeg.base64EncodedString()
            logger.log(
                "snap: captured \(jpeg.count) bytes from \(captureMode) in \(Int(Date().timeIntervalSince(started) * 1000))ms",
                level: .info
            )
            let typed = (question ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            let effectiveQuestion = typed.isEmpty ? Self.defaultSnapQuestion : typed
            let resp = try await BackendClient.shared.snap(
                backendURL: config.backendURL,
                sessionId: config.sessionId,
                imageB64: imageB64,
                question: effectiveQuestion,
                captureMode: captureMode
            )
            lastSnap = resp
            let qSuffix = resp.question_id.map { " (q=\($0))" } ?? ""
            let qLabel = typed.isEmpty ? "default question" : "question fired"
            logger.log(
                "snap: ok — \(qLabel) · saw \(resp.objects.prefix(3).joined(separator: ", "))\(qSuffix)",
                level: .ok
            )
        } catch {
            logger.log("snap: failed — \(error.localizedDescription)", level: .err)
        }
    }
}
