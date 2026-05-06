// WearablesController.swift
//
// Wraps the Meta Wearables Device Access Toolkit (`meta-wearables-dat-ios`)
// so the rest of the app sees a tiny surface area:
//
//   1. Register with the Meta AI app (deep-link round trip via the URL
//      scheme declared in Info.plist as MWDAT.AppLinkURLScheme).
//   2. Ask the user for camera permission on the glasses.
//   3. Open a DeviceSession + StreamSession at 24 fps medium resolution
//      (the values documented in CLAUDE.md's latency budget).
//   4. Hand each video frame to a callback the LiveKit publisher installs at
//      startup, and expose capturePhoto() for the Snap & ask path.
//
// Building without toolkit access:
//   - The DAT Swift package is public (https://github.com/facebook/meta-wearables-dat-ios)
//     so SPM resolves cleanly. What still needs per-developer credentials are
//     MetaAppID + ClientToken in Info.plist; without them Wearables.configure()
//     succeeds but registration with real glasses will fail. The Mock Device
//     Kit lets you exercise the full surface without hardware.
//   - If you want to run the app entirely without DAT (purely as the LiveKit
//     phone-camera fallback) flip USE_WEARABLES_SDK = false and the entire
//     toolkit is short-circuited.
//
// Reference (developer preview):
//   https://wearables.developer.meta.com/docs/build-integration-ios/

import Foundation
import UIKit
import CoreMedia
import CoreVideo
import MWDATCore
import MWDATCamera

/// Master switch. The DAT package itself is small enough to keep linked even
/// when we want to demo with the phone camera, so we keep this `true` by
/// default; flip to `false` only if Xcode can't resolve the SPM package and
/// you need the build to keep working.
private let USE_WEARABLES_SDK = true

/// One frame from the glasses, normalized for the LiveKit publisher.
/// We carry a `CVPixelBuffer` because that's the cheapest input for
/// LiveKit's BufferCapturer (no extra copy on the way to the encoder).
struct GlassesVideoFrame: @unchecked Sendable {
    let pixelBuffer: CVPixelBuffer
    let timestampNs: UInt64
    let rotation: Int        // 0 / 90 / 180 / 270
}

/// One-shot resolver guard used by awaitLinkConnected so a listener
/// callback racing the timeout doesn't double-resume a continuation.
private final class _ResolvedBox: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false
    func tryResolve() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if done { return false }
        done = true
        return true
    }
}

/// Lock-protected storage for the per-frame callback. NSLock-based so it
/// works back to iOS 15.2 (vs OSAllocatedUnfairLock which is iOS 16+).
/// `@unchecked Sendable` because the lock provides the synchronization
/// the compiler can't see through.
private final class _FrameCallbackBox: @unchecked Sendable {
    private let lock = NSLock()
    private var value: ((GlassesVideoFrame) -> Void)?

    func set(_ v: ((GlassesVideoFrame) -> Void)?) {
        lock.lock(); defer { lock.unlock() }
        value = v
    }

    func get() -> ((GlassesVideoFrame) -> Void)? {
        lock.lock(); defer { lock.unlock() }
        return value
    }
}

@MainActor
final class WearablesController: ObservableObject {

    enum State: Equatable {
        case idle
        case registering
        case awaitingPermission
        case ready                // device paired, waiting to start streaming
        case streaming
        case unavailable(String)  // toolkit not available or permission denied
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var deviceName: String? = nil
    /// Mirrors the latest devicesStream emission count. Per Meta's docs, the
    /// device list is empty until the user has granted at least one
    /// permission — we use this to wait between requestPermission(.camera)
    /// returning and calling createSession(), otherwise AutoDeviceSelector
    /// races the async backend update and throws .noEligibleDevice.
    @Published private(set) var deviceCount: Int = 0

    /// Installed by LiveKitController.start() — every glasses frame is
    /// forwarded into the LiveKit BufferCapturer through this closure.
    ///
    /// Backed by a lock-protected box (`_frameCallbackBox`) so the setter
    /// (called on MainActor by LiveKitController) and the getter (called
    /// at DAT camera fps from a background queue) are safe without
    /// hopping to the main actor for every frame at 24 fps.
    nonisolated var onVideoFrame: ((GlassesVideoFrame) -> Void)? {
        get { _frameCallbackBox.get() }
        set { _frameCallbackBox.set(newValue) }
    }

    private nonisolated let _frameCallbackBox = _FrameCallbackBox()

    private let logger = SessionLogger.shared

    // MARK: - DAT handles
    //
    // We hold strong references to every DAT object that's still active so
    // their listeners keep firing. AnyListenerToken cancels on deinit, so
    // dropping a token mid-stream is the same as calling stop().

    private var deviceSession: DeviceSession?
    private var streamSession: StreamSession?
    private var registrationStateTask: Task<Void, Never>?
    private var devicesTask: Task<Void, Never>?
    private var sessionErrorTask: Task<Void, Never>?
    private var sessionStateTask: Task<Void, Never>?
    private var devicesListenerToken: AnyListenerToken?
    private var stateListenerToken: AnyListenerToken?
    private var frameListenerToken: AnyListenerToken?
    private var errorListenerToken: AnyListenerToken?
    private var photoListenerToken: AnyListenerToken?

    /// Resolved when the next photoDataPublisher event fires after a
    /// capturePhoto() request. capturePhoto() is fire-and-forget at the DAT
    /// level (returns Bool); we bridge it to async/throws here.
    private var pendingPhotoContinuation: CheckedContinuation<Data, Error>?

    /// Resolved (with `true`) the first time the stream reports `.streaming`
    /// after a startStream() call, or `false` after the awaiter times out.
    /// SessionController uses this so it can decide whether to publish the
    /// glasses video track or fall back to the phone camera, without racing
    /// the publisher callback that flips `state`.
    private var firstStreamingContinuation: CheckedContinuation<Bool, Never>?

    /// Resolved (with `true`) the first time devicesStream emits a non-empty
    /// list — i.e. the moment Meta AI confirms our app may see a device for
    /// this account/permission tuple. Companion to firstStreamingContinuation
    /// but one step earlier in the chain (devices appear before streams do).
    private var firstDeviceContinuation: CheckedContinuation<Bool, Never>?

    enum WearablesControllerError: Error {
        case sdkUnavailable
        case noActiveStream
        case captureRejected
    }

    // MARK: - Public API consumed by SessionController

    func register() async {
        guard USE_WEARABLES_SDK else {
            logger.log("wearables: SDK switch off — falling back to phone camera", level: .warn)
            state = .unavailable("USE_WEARABLES_SDK = false in WearablesController.swift")
            return
        }
        state = .registering
        // Diag #1: synchronous device list at start of register(). On a warm
        // launch (Meta AI already paired) this should already be non-empty
        // even before our async loops fire.
        let preDevices = Wearables.shared.devices
        logger.log(
            "wearables: pre-register devices.count=\(preDevices.count) names=\(preDevices.map { String(describing: $0) })",
            level: preDevices.isEmpty ? .info : .ok
        )
        do {
            try await Wearables.shared.startRegistration()
            logger.log("wearables: startRegistration() — handing off to Meta AI app", level: .info)
        } catch let err as RegistrationError where err == .alreadyRegistered {
            // Per the SDK's swiftinterface, RegistrationError raw value 0 is
            // `.alreadyRegistered`. This is the Meta-blessed way of saying
            // "your app already completed registration on a prior launch,
            // nothing to do" — it is NOT a fatal error and we should proceed
            // to permission + stream calls. Without this catch the user sees
            // a scary red "RegistrationError error 0" line in the log even
            // though everything is fine.
            logger.log("wearables: already registered with Meta AI (re-using prior registration)", level: .ok)
        } catch {
            state = .failed("startRegistration: \(error.localizedDescription)")
            logger.log("wearables: startRegistration failed — \(error.localizedDescription)", level: .err)
            return
        }

        // Drive `state` from the registration AsyncStream. Once we see a
        // registered state we'll also kick off devices observation so the
        // user knows when their glasses become discoverable.
        registrationStateTask?.cancel()
        registrationStateTask = Task { @MainActor [weak self] in
            for await regState in Wearables.shared.registrationStateStream() {
                guard let self else { return }
                self.logger.log("wearables: registrationState=\(regState)", level: .info)
                switch String(describing: regState).lowercased() {
                case let s where s.contains("registered"):
                    self.state = .ready
                    self.startObservingDevices()
                case let s where s.contains("registering"),
                     let s where s.contains("inprogress"):
                    self.state = .registering
                case let s where s.contains("unregistered"),
                     let s where s.contains("none"):
                    self.state = .idle
                default:
                    break
                }
            }
        }
    }

    func handleDeepLink(_ url: URL) async {
        guard USE_WEARABLES_SDK else { return }
        do {
            let handled = try await Wearables.shared.handleUrl(url)
            logger.log(
                "wearables: handleUrl(\(url.absoluteString)) handled=\(handled)",
                level: handled ? .ok : .info
            )
        } catch {
            logger.log("wearables: handleUrl failed — \(error.localizedDescription)", level: .err)
        }
    }

    /// Retry-friendly wrapper around requestCameraPermission. Up to
    /// `maxAttempts` calls, returning early on success. Between attempts
    /// it sleeps 750ms — long enough that Meta AI has time to surface
    /// the prompt UI without making the whole flow drag. Use this from
    /// SessionController.connect; the single-shot version is left for
    /// callers that need finer control.
    func requestCameraPermissionWithRetry(maxAttempts: Int) async {
        guard USE_WEARABLES_SDK else { return }
        for attempt in 1...maxAttempts {
            await requestCameraPermission()
            if state == .ready {
                return
            }
            if attempt < maxAttempts {
                logger.log(
                    "wearables: requestPermission attempt \(attempt) didn't yield .ready (state=\(state)); retrying in 750ms — switch to Meta AI on the next prompt to accept",
                    level: .warn
                )
                try? await Task.sleep(nanoseconds: 750_000_000)
            }
        }
    }

    func requestCameraPermission() async {
        guard USE_WEARABLES_SDK else { return }
        state = .awaitingPermission
        // Diag #2: probe status first. If it's already .granted we can skip
        // the deep-link round trip entirely (which is the source of the
        // PermissionError 5 / .requestTimeout we keep hitting when Meta AI
        // is backgrounded). The probe is cheap and doesn't open Meta AI.
        do {
            let preStatus = try await Wearables.shared.checkPermissionStatus(.camera)
            let preStr = String(describing: preStatus).lowercased()
            logger.log("wearables: camera permission preStatus=\(preStatus)", level: preStr.contains("granted") ? .ok : .info)
            if preStr.contains("granted") {
                state = .ready
                // Devices should already be listable now if Meta AI is paired.
                let devs = Wearables.shared.devices
                logger.log(
                    "wearables: post-checkPermission devices.count=\(devs.count)",
                    level: devs.isEmpty ? .info : .ok
                )
                return
            }
        } catch {
            // Non-fatal: fall through to requestPermission and let that
            // surface the real error.
            logger.log("wearables: checkPermissionStatus(.camera) errored — \(error.localizedDescription); will try requestPermission", level: .info)
        }
        do {
            // requestPermission opens the Meta AI app and only resolves once
            // the user has responded; the response comes back as a fresh
            // PermissionStatus.
            let status = try await Wearables.shared.requestPermission(.camera)
            let str = String(describing: status).lowercased()
            logger.log("wearables: camera permission=\(status)", level: str.contains("granted") ? .ok : .warn)
            if str.contains("granted") {
                state = .ready
            } else {
                state = .unavailable("camera permission \(status)")
            }
        } catch {
            state = .failed("requestPermission: \(error.localizedDescription)")
            logger.log("wearables: requestPermission failed — \(error.localizedDescription)", level: .err)
        }
    }

    func startStream() async {
        guard USE_WEARABLES_SDK else {
            logger.log("wearables: startStream skipped (SDK switch off)", level: .warn)
            return
        }
        // Self-cleanup: if a previous startStream left a session in
        // place (user tapped Connect twice without Disconnect in
        // between, or LiveKit publish failed mid-flight) DAT throws
        // .sessionAlreadyExists. Tear down any leftover session first.
        if streamSession != nil || deviceSession != nil {
            logger.log("wearables: cleaning up leftover session before re-start", level: .info)
            await stopStream()
            try? await Task.sleep(nanoseconds: 200_000_000)
        }
        // Diag #5: log every paired device's linkState before we even try
        // createSession. `noEligibleDevice` from createSession means
        // "device exists but isn't BT-connected right now" — paired is not
        // the same as connected. If linkState is .disconnected, the glasses
        // need to be physically taken out of the case + put on (don'd) so
        // Meta AI re-establishes the BT pipe before we call startStream.
        let snap = Wearables.shared.devices
        for id in snap {
            if let dev = Wearables.shared.deviceForIdentifier(id) {
                let compat = dev.compatibility()
                let compatOK = String(describing: compat).lowercased().contains("compatible")
                    && !String(describing: compat).lowercased().contains("required")
                logger.log(
                    "wearables: device \(id) linkState=\(dev.linkState) compatibility=\(compat) name=\(dev.name)",
                    level: (dev.linkState == .connected && compatOK) ? .ok : .warn
                )
                if !compatOK {
                    logger.log(
                        "wearables: device compatibility is NOT .compatible — AutoDeviceSelector will refuse this device with .noEligibleDevice. Fix: Meta AI → Devices → Wayfarer → Software Update (sometimes requires the glasses to be in their case + connected to power).",
                        level: .err
                    )
                }
            } else {
                logger.log("wearables: deviceForIdentifier(\(id)) returned nil", level: .warn)
            }
        }
        // Diag #6: if the only device we have is .disconnected or .connecting,
        // wait up to 8s for it to flip to .connected. The audible "tone"
        // the user hears in the glasses speakers IS this state transition;
        // we just need to give it the time. AddLinkStateListener fires on
        // every transition.
        if let firstId = snap.first,
           let dev = Wearables.shared.deviceForIdentifier(firstId),
           dev.linkState != .connected {
            logger.log(
                "wearables: waiting up to 8s for device \(firstId) to reach .connected (currently \(dev.linkState))",
                level: .info
            )
            let connected = await awaitLinkConnected(device: dev, timeoutSeconds: 8.0)
            if !connected {
                logger.log(
                    "wearables: device \(firstId) never reached .connected — startStream will likely throw .noEligibleDevice; physical fix: take glasses out of case, unfold + put on, wait for Meta AI 'connected' tone",
                    level: .warn
                )
            } else {
                logger.log("wearables: device reached .connected — proceeding to startStream", level: .ok)
            }
        }
        do {
            // Try AutoDeviceSelector first — fine for the single-device
            // demo flow. If it returns .noEligibleDevice we'll retry with
            // SpecificDeviceSelector(device:) using the explicit ID we
            // already pulled from Wearables.shared.devices. Some SDK
            // versions enforce stricter compatibility on Auto than on
            // Specific, so this is a meaningful second chance.
            let selector: DeviceSelector
            if let firstId = Wearables.shared.devices.first {
                selector = SpecificDeviceSelector(device: firstId)
                logger.log("wearables: using SpecificDeviceSelector(device: \(firstId))", level: .info)
            } else {
                selector = AutoDeviceSelector(wearables: Wearables.shared)
                logger.log("wearables: no device IDs available, using AutoDeviceSelector", level: .info)
            }
            let session = try Wearables.shared.createSession(deviceSelector: selector)
            self.deviceSession = session
            // Diag #7: subscribe to session-level error + state streams
            // BEFORE start/addStream. addStream returning nil instead of
            // throwing is a known SDK quirk; the actual reason often
            // surfaces on errorStream while we're staring at the nil
            // return. Same for state transitions — we want to see the
            // session state when addStream is called.
            sessionErrorTask?.cancel()
            sessionErrorTask = Task { @MainActor [weak self] in
                guard let session = self?.deviceSession else { return }
                for await err in session.errorStream() {
                    self?.logger.log("wearables: deviceSession.error → \(err)", level: .err)
                }
            }
            sessionStateTask?.cancel()
            sessionStateTask = Task { @MainActor [weak self] in
                guard let session = self?.deviceSession else { return }
                for await s in session.stateStream() {
                    self?.logger.log("wearables: deviceSession.state → \(s)", level: .info)
                }
            }
            try session.start()
            logger.log("wearables: deviceSession.start() called, current state=\(session.state)", level: .ok)
            // Some SDK versions need a brief moment after start() before
            // capabilities are addable — give the device 300ms to settle
            // its state, then proceed.
            try? await Task.sleep(nanoseconds: 300_000_000)
            logger.log("wearables: post-start sleep done, state=\(session.state)", level: .info)

            // addStream is finicky about codec/resolution/fps combos and
            // also about `skipAppLaunch`. We try lower-bandwidth configs
            // first because the demo doesn't need 1080p — and once the
            // glasses' BT pipe has been hammered by prior start/stop
            // cycles in the same session, lower bandwidth is the
            // difference between "stream stays up" and "internalError 6
            // seconds in". The first successful run today (21:31) used
            // defaults; later defaults attempts died with internalError,
            // which is a known symptom of pipe saturation.
            // Order:
            //   1. hvc1 + low + 15 + skipAppLaunch — minimum bandwidth
            //   2. hvc1 + medium + 24 + skipAppLaunch — CLAUDE.md target
            //   3. SDK defaults — fallback if explicit configs reject
            let configs: [(StreamSessionConfig, String)] = [
                (StreamSessionConfig(videoCodec: .hvc1, resolution: .low, frameRate: 15, skipAppLaunch: true), "hvc1+low+15+skipAppLaunch"),
                (StreamSessionConfig(videoCodec: .hvc1, resolution: .medium, frameRate: 24, skipAppLaunch: true), "hvc1+medium+24+skipAppLaunch"),
                (StreamSessionConfig(), "defaults"),
            ]
            var stream: StreamSession? = nil
            for (cfg, label) in configs {
                do {
                    if let s = try session.addStream(config: cfg) {
                        stream = s
                        logger.log("wearables: addStream(\(label)) succeeded", level: .ok)
                        break
                    } else {
                        logger.log("wearables: addStream(\(label)) returned nil — trying next config", level: .warn)
                    }
                } catch {
                    logger.log("wearables: addStream(\(label)) threw — \(error.localizedDescription)", level: .warn)
                }
            }
            guard let stream else {
                let hint = """
                addStream returned nil for all configs. Per the Meta DAT SDK, \
                StreamSessionError has a `hingesClosed` case — DAT silently \
                returns nil from addStream when the glasses' temples are \
                FOLDED. PHYSICAL FIX: take glasses out of case, unfold both \
                temples, put them on your face (or hold them open), wait for \
                the Meta AI 'connected' chime, then try Connect again — and \
                KEEP THEM OPEN the whole time. Other (less likely) causes: \
                Meta AI is currently using the camera (close that flow); \
                thermal throttling (let glasses cool); firmware too old.
                """
                state = .failed(hint)
                logger.log("wearables: addStream returned nil — likely hingesClosed (glasses folded). UNFOLD the temples + put glasses on, then retry.", level: .err)
                return
            }
            self.streamSession = stream

            // Wire the three publishers we care about: state, frames, and
            // photo data. Each .listen returns an AnyListenerToken whose
            // deinit cancels the subscription, so we hold them on self.

            stateListenerToken = stream.statePublisher.listen { [weak self] streamState in
                Task { @MainActor in
                    guard let self else { return }
                    self.logger.log("wearables: streamState=\(streamState)", level: .info)
                    switch String(describing: streamState).lowercased() {
                    case let s where s.contains("streaming"):
                        self.state = .streaming
                        if let cont = self.firstStreamingContinuation {
                            self.firstStreamingContinuation = nil
                            cont.resume(returning: true)
                        }
                    case let s where s.contains("stopped"):
                        self.state = .ready
                    default:
                        break
                    }
                }
            }

            errorListenerToken = stream.errorPublisher.listen { [weak self] err in
                Task { @MainActor in
                    guard let self else { return }
                    let label = String(describing: err).lowercased()
                    self.logger.log("wearables: stream error — \(err)", level: .err)
                    // `internalError` mid-flight = camera capability died on
                    // the glasses (thermal, BT pipe saturation, or Meta AI
                    // grabbed the camera in the background). Tell the user
                    // exactly what to do — there is no software-side fix.
                    if label.contains("internal") {
                        self.logger.log(
                            "wearables: glasses camera died mid-stream. PHYSICAL RECOVERY: 1) Disconnect → 2) Open Meta AI app, force-quit it (swipe up). 3) Put glasses in their case for ~30s (this hard-resets the camera service). 4) Take out + put on. 5) Tap Connect again. The first attempt today worked at 21:31 — same code, fresher hardware state.",
                            level: .err
                        )
                    }
                    self.state = .failed(String(describing: err))
                }
            }

            frameListenerToken = stream.videoFramePublisher.listen { [weak self] frame in
                guard let self else { return }
                guard let normalized = Self.normalize(frame: frame) else { return }
                // No MainActor.run hop here: BufferCapturer.capture is
                // documented as thread-safe and we want the encoder pipeline
                // to stay off the main queue.
                self.onVideoFrame?(normalized)
            }

            photoListenerToken = stream.photoDataPublisher.listen { [weak self] photo in
                Task { @MainActor in
                    guard let self else { return }
                    if let cont = self.pendingPhotoContinuation {
                        self.pendingPhotoContinuation = nil
                        cont.resume(returning: photo.data)
                    } else {
                        // Photo arrived but no one is awaiting it (e.g. user
                        // tapped Snap then disconnected). Just log so we
                        // notice if it ever happens.
                        self.logger.log(
                            "wearables: photoData (\(photo.data.count) bytes) with no pending continuation",
                            level: .warn
                        )
                    }
                }
            }

            await stream.start()
            logger.log("wearables: streamSession.start()", level: .ok)
            // Note: state will flip to .streaming via the statePublisher
            // callback above, not here — DAT may park us in .waitingForDevice
            // first if the glasses haven't woken up yet.
        } catch let err as DeviceSessionError {
            // DeviceSessionError cases per the SDK swiftinterface (line 283):
            //   .noEligibleDevice         ← actual cause of "No eligible device available"
            //   .sessionAlreadyStopped
            //   .sessionAlreadyExists
            //   .sessionIdle
            //   .capabilityAlreadyActive
            //   .capabilityNotFound
            //   .unexpectedError(description:)
            //
            // `.noEligibleDevice` specifically means DAT enumerated paired
            // devices and found zero. Almost always = the Ray-Bans aren't
            // actually registered with Meta AI on this iPhone (Meta AI's
            // App Info screen will also show `Error: noDeviceConfig`).
            // The fix is upstream of us: re-pair the glasses through Meta
            // AI → Devices → Add Device, or use the Mock Device Kit.
            let hint: String = {
                switch err {
                case .noEligibleDevice:
                    return " (Meta AI has no paired Ray-Bans for this account; check Meta AI → Devices, and App Info for `noDeviceConfig`)"
                case .sessionAlreadyExists:
                    return " (a stream is already running — call stopStream first)"
                default:
                    return ""
                }
            }()
            state = .failed("startStream: \(err)\(hint)")
            logger.log("wearables: startStream failed — \(err)\(hint)", level: .err)
        } catch {
            state = .failed("startStream: \(error.localizedDescription)")
            logger.log("wearables: startStream failed — \(error.localizedDescription)", level: .err)
        }
    }

    func stopStream() async {
        guard USE_WEARABLES_SDK else { return }
        // stop() is async in 0.6.x — await so the BT pipe is flushed before
        // we drop the listener tokens (otherwise the last few frames may
        // arrive after we've already torn the closures down).
        await streamSession?.stop()
        streamSession = nil
        // Listener tokens auto-cancel on nil-out; explicit for readability.
        // We deliberately keep devicesListenerToken alive across stop/start
        // cycles so subsequent reconnects don't re-pay the device-discovery
        // race we're working around above.
        stateListenerToken = nil
        frameListenerToken = nil
        errorListenerToken = nil
        photoListenerToken = nil
        sessionErrorTask?.cancel()
        sessionErrorTask = nil
        sessionStateTask?.cancel()
        sessionStateTask = nil
        deviceSession?.stop()
        deviceSession = nil
        // If anyone is still awaiting first-streaming, unblock them so
        // SessionController doesn't hang on disconnect-during-startup.
        if let cont = firstStreamingContinuation {
            firstStreamingContinuation = nil
            cont.resume(returning: false)
        }
        state = .ready
        logger.log("wearables: stopStream()", level: .info)
    }

    /// Wait up to `timeoutSeconds` for the stream to enter `.streaming`.
    /// Returns `true` if it did, `false` on timeout (or if the SDK isn't
    /// linked / startStream was never called). Must be called from the main
    /// actor (the whole controller is @MainActor anyway).
    ///
    /// Concurrency note: Swift 5.9 will warn that the continuation closure
    /// captures self across an actor boundary. In practice the closure runs
    /// synchronously inside a MainActor-isolated method, so the assignment
    /// happens on the same thread that reads it later (statePublisher
    /// callback hops back to MainActor too). MainActor.assumeIsolated would
    /// silence the warning but is iOS 17+ only and the deployment target
    /// here is iOS 15.2; we accept the warning.
    /// Block until DAT's devicesStream emits at least one device, or until
    /// `timeoutSeconds` elapses. Required between requestPermission(.camera)
    /// returning .granted and createSession() being called — without this
    /// gap the AutoDeviceSelector races the permission-grant fanout and
    /// throws DeviceSessionError.noEligibleDevice even though everything
    /// else is fine. Returns true if a device showed up, false on timeout.
    func awaitDevice(timeoutSeconds: Double = 5.0) async -> Bool {
        guard USE_WEARABLES_SDK else { return false }
        // Diag #4a: if we already saw a device via stream/listener, return now.
        if deviceCount > 0 { return true }
        // Diag #4b: fast-path the synchronous getter. `Wearables.shared.devices`
        // is the same source AutoDeviceSelector.activeDevice reads from, so
        // if this is non-empty createSession will succeed even if the
        // AsyncStream never fired.
        let immediate = Wearables.shared.devices
        if !immediate.isEmpty {
            handleDevicesUpdate(immediate, source: "awaitDevice-sync")
            return true
        }
        return await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
            self.firstDeviceContinuation = cont
            // Diag #4c: also poll the synchronous getter every 500ms in case
            // the streams genuinely never fire. If devices show up here we
            // resolve the same continuation.
            let pollTask = Task { @MainActor [weak self] in
                let intervalMs: UInt64 = 500
                let deadlineNs = UInt64(timeoutSeconds * 1_000_000_000)
                var elapsedNs: UInt64 = 0
                while elapsedNs < deadlineNs {
                    try? await Task.sleep(nanoseconds: intervalMs * 1_000_000)
                    elapsedNs += intervalMs * 1_000_000
                    guard let self else { return }
                    let cur = Wearables.shared.devices
                    if !cur.isEmpty {
                        self.handleDevicesUpdate(cur, source: "awaitDevice-poll")
                        return
                    }
                }
            }
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
                guard let self else { return }
                pollTask.cancel()
                if let pending = self.firstDeviceContinuation {
                    self.firstDeviceContinuation = nil
                    let final = Wearables.shared.devices
                    self.logger.log(
                        "wearables: awaitDevice timed out after \(timeoutSeconds)s (sync devices.count=\(final.count)) — if non-zero, the AsyncStream is bugged but createSession will still work; we'll try it",
                        level: .warn
                    )
                    pending.resume(returning: !final.isEmpty)
                }
            }
        }
    }

    /// Wait up to `timeoutSeconds` for the device's BT link to flip to
    /// `.connected`. Returns true if it did. Used by startStream to avoid
    /// a guaranteed `.noEligibleDevice` throw when the device is paired
    /// but not currently BT-connected (the user just woke the glasses).
    private func awaitLinkConnected(device: Device, timeoutSeconds: Double) async -> Bool {
        if device.linkState == .connected { return true }
        return await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
            // Capture-once box so timeout and listener can race safely.
            // NSLock-protected because the listener fires on a background
            // queue per the SDK's @Sendable closure annotation.
            let resolved = _ResolvedBox()
            let token = device.addLinkStateListener { state in
                if state == .connected, resolved.tryResolve() {
                    cont.resume(returning: true)
                }
            }
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
                if resolved.tryResolve() {
                    cont.resume(returning: false)
                }
                Task { await token.cancel() }
            }
        }
    }

    func awaitStreaming(timeoutSeconds: Double = 4.0) async -> Bool {
        guard USE_WEARABLES_SDK else { return false }
        if state == .streaming { return true }
        if streamSession == nil { return false }
        return await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
            self.firstStreamingContinuation = cont
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeoutSeconds * 1_000_000_000))
                guard let self else { return }
                if let pending = self.firstStreamingContinuation {
                    self.firstStreamingContinuation = nil
                    self.logger.log(
                        "wearables: awaitStreaming timed out after \(timeoutSeconds)s (state=\(self.state))",
                        level: .warn
                    )
                    pending.resume(returning: false)
                }
            }
        }
    }

    // MARK: - Snap & ask

    /// Fires a still-photo capture on the active stream and awaits the JPEG.
    /// Throws if no stream is active or the request was rejected (e.g. a
    /// previous capture is still in flight). Photos are delivered through
    /// `photoDataPublisher`, which we bridge to async via a continuation.
    func capturePhoto() async throws -> Data {
        guard USE_WEARABLES_SDK else { throw WearablesControllerError.sdkUnavailable }
        guard let stream = streamSession else { throw WearablesControllerError.noActiveStream }
        if pendingPhotoContinuation != nil {
            throw WearablesControllerError.captureRejected
        }
        return try await withCheckedThrowingContinuation { cont in
            // Same isolation note as awaitStreaming: closure runs sync on
            // the main actor since capturePhoto() itself is MainActor.
            self.pendingPhotoContinuation = cont
            let accepted = stream.capturePhoto(format: .jpeg)
            if !accepted {
                self.pendingPhotoContinuation = nil
                cont.resume(throwing: WearablesControllerError.captureRejected)
            }
        }
    }

    // MARK: - Helpers

    private func startObservingDevices() {
        // Diag #3a: AsyncStream consumer (existing path)
        devicesTask?.cancel()
        devicesTask = Task { @MainActor [weak self] in
            for await devices in Wearables.shared.devicesStream() {
                guard let self else { return }
                self.handleDevicesUpdate(devices, source: "stream")
            }
        }
        // Diag #3b: callback-based listener — Meta SDK exposes BOTH
        // devicesStream() AND addDevicesListener for a reason; if the
        // AsyncStream hangs (we suspect a bug because we get
        // registrationState=registered events fine but no device events),
        // the callback path is independent and might fire first.
        // AnyListenerToken.cancel() is async per the SDK's swiftinterface;
        // we fire-and-forget the cancel of any prior token so this method
        // can stay synchronous.
        if let prior = devicesListenerToken {
            Task { await prior.cancel() }
        }
        devicesListenerToken = Wearables.shared.addDevicesListener { [weak self] devices in
            Task { @MainActor in
                self?.handleDevicesUpdate(devices, source: "listener")
            }
        }
        // Diag #3c: also flush whatever's already in the synchronous
        // `devices` getter at this very moment. If Meta AI has the device
        // paired but neither stream/listener has fired yet, this surfaces it.
        let snap = Wearables.shared.devices
        if !snap.isEmpty {
            handleDevicesUpdate(snap, source: "sync")
        }
    }

    /// Called from devicesStream(), addDevicesListener, and the synchronous
    /// `Wearables.shared.devices` poll. Idempotent — if the same set arrives
    /// from multiple sources we just log and update state, the continuation
    /// resolves at most once.
    private func handleDevicesUpdate(_ devices: [DeviceIdentifier], source: String) {
        let names = devices.map { String(describing: $0) }
        self.deviceName = names.first
        self.deviceCount = devices.count
        self.logger.log(
            "wearables: devices=[\(names.joined(separator: ", "))] (count=\(devices.count), src=\(source))",
            level: devices.isEmpty ? .info : .ok
        )
        if !devices.isEmpty, let pending = self.firstDeviceContinuation {
            self.firstDeviceContinuation = nil
            pending.resume(returning: true)
        }
    }

    /// Convert a DAT VideoFrame into LiveKit's BufferCapturer input shape.
    /// VideoFrame only exposes a CMSampleBuffer; we pull the image buffer
    /// and the presentation timestamp from CoreMedia ourselves.
    /// `nonisolated` so the videoFramePublisher callback (background queue,
    /// ~24 Hz) can call it without hopping to MainActor.
    private nonisolated static func normalize(frame: VideoFrame) -> GlassesVideoFrame? {
        let sampleBuffer = frame.sampleBuffer
        guard let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            return nil
        }
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let seconds = CMTimeGetSeconds(pts)
        let nanos: UInt64
        if seconds.isFinite, seconds > 0 {
            nanos = UInt64(seconds * 1_000_000_000)
        } else {
            // Some early frames come through with an invalid PTS; use the
            // host clock so LiveKit's encoder still gets a monotonic value.
            nanos = mach_absolute_time()
        }
        return GlassesVideoFrame(
            pixelBuffer: imageBuffer,
            timestampNs: nanos,
            rotation: 0
        )
    }
}
