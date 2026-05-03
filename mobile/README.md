# LiveRecall · Mobile publishers

Native iOS and Android apps that pull live frames from **Ray-Ban Meta** glasses
via the [Meta Wearables Device Access Toolkit](https://wearables.developer.meta.com/)
and publish them into the existing LiveKit room as the **glasses** participant.
The backend (`backend/`) and worker (`backend/worker.py`) are unchanged — both
apps reuse the same `POST /token` contract that `phone/glasses.html` already
uses, with `capture_mode: "glasses"`.

```
Glasses ── Bluetooth ──▶ phone app ── Wi-Fi/LTE ──▶ LiveKit Cloud
                                                       │
                                  worker.py ◀──────────┘
                                  └─▶ Vision / Router / Retrievers / TTS
```

For the desktop screenshot-loop fallback (Mac/Windows/Linux) see
[`scripts/bridge_rayban_snap.py`](../scripts/bridge_rayban_snap.py) — it stays
the primary path on machines without a phone in the loop. Improvement ideas
are listed in [`DECISIONS.md`](../DECISIONS.md) (g) and the project plan.

---

## Layout

- [`ios/LiveRecallGlasses/`](./ios/LiveRecallGlasses) — Swift / SwiftUI app
  (XcodeGen project manifest at `project.yml`).
- [`android/LiveRecallGlasses/`](./android/LiveRecallGlasses) — Kotlin /
  Jetpack Compose app (Gradle / Android Gradle Plugin 8.5).

Both apps follow the same shape:

| Layer | iOS | Android |
|---|---|---|
| User config (backend URL, identity, room) | `AppConfig` (UserDefaults) | `AppConfig` (DataStore) |
| /token call | `BackendClient` | `BackendClient` |
| Wearables wrapper | `WearablesController` (stub w/ `USE_WEARABLES_SDK`) | `GlassesController` (stub w/ `USE_WEARABLES_SDK`) |
| LiveKit publisher | `LiveKitController` (BufferCapturer) | `PublisherController` (`GlassesVideoCapturer`) |
| Orchestrator | `SessionController` | `SessionController` |
| UI | SwiftUI: `ContentView` + Views/* | Compose: `ConnectScreen` + components/* |

The Wearables SDK call sites are stubbed by default so the app builds and
publishes the **phone camera** without any Meta credentials, which makes it
straightforward to verify the LiveKit half end-to-end first.

---

## Prerequisites

- **Approved Meta Wearables Dev Center project.** Grab `MetaAppID` /
  `ClientToken` (and the SDK package URL/credentials) from
  https://wearables.developer.meta.com/.
- **Apple Developer account** for iOS (TestFlight / device install).
- **Android Studio Hedgehog or newer** + an Android 10+ device.
- A running LiveRecall backend (`make backend && make worker && make
  dashboard` from the repo root).

---

## iOS — `ios/LiveRecallGlasses/`

### One-time setup

1. Install XcodeGen (used to generate `.xcodeproj` from `project.yml`):
   ```bash
   brew install xcodegen
   ```
   *(If you'd rather not use XcodeGen, just create a new SwiftUI iOS app in
   Xcode, drag in the contents of `LiveRecallGlasses/`, and add the LiveKit
   Swift package + the WearablesDAT package by hand. The rest of this
   section still applies.)*

2. Open `LiveRecallGlasses/Resources/Info.plist` and replace every
   `REPLACE_ME_*` value:
   - `MetaAppID` — from Wearables Dev Center.
   - `ClientToken` — from Wearables Dev Center.
   - `TeamID` — your Apple Developer team identifier.
   - `AppLinkURLScheme` — keep `liverecallglasses` unless you want to
     register a different URL scheme; if you change it, update the matching
     entry under `CFBundleURLTypes`.

3. (Once your Wearables Dev Center project is approved) edit
   `project.yml` and uncomment the `WearablesDAT` entry under `packages:`
   plus the matching `dependencies:` entry. Use the package URL Meta
   provides for your account.

4. Generate and open the project:
   ```bash
   cd LiveRecall/mobile/ios/LiveRecallGlasses
   xcodegen generate
   open LiveRecallGlasses.xcodeproj
   ```

5. In `Signing & Capabilities`, pick your team. Bundle id defaults to
   `com.liverecall.glasses` — change if you already use that id.

6. Flip `USE_WEARABLES_SDK` in `WearablesController.swift` to `true` once
   the Swift package is linked, and replace the TODO bodies with the real
   toolkit calls (`WearablesToolkit.shared.devicesStream()`,
   `session.addStream(config:)`, `stream.videoFramePublisher.listen`).

### Run

- **Simulator**: works for the LiveKit phone-camera fallback path. The
  Wearables toolkit is hardware-only, so glasses streaming requires a
  paired physical iPhone (or use the Meta-provided **Mock Device Kit** for
  registration testing).
- **Device**: USB-connect, build & run, accept Camera / Microphone /
  Bluetooth / Local Network prompts.

### Distribution

- **TestFlight (recommended for testers).** Archive in Xcode, upload to App
  Store Connect, distribute via TestFlight internal testers.
- **Direct USB install** for hackathon use is fine.
- Note (per Meta's iOS guide): the Wearables SDK uses `ExternalAccessory`,
  which currently makes App Store submission ineligible. TestFlight /
  internal distribution still works.

---

## Android — `android/LiveRecallGlasses/`

### One-time setup

1. Open the folder in Android Studio. The first sync downloads
   the AGP / Compose / LiveKit dependencies.

2. Replace `REPLACE_ME_META_APP_ID` and `REPLACE_ME_CLIENT_TOKEN` in
   [`app/build.gradle.kts`](./android/LiveRecallGlasses/app/build.gradle.kts)
   `defaultConfig.manifestPlaceholders` (and the matching `buildConfigField`
   lines) with the values from your Wearables Dev Center project.

3. (When your Meta access is approved) edit
   [`settings.gradle.kts`](./android/LiveRecallGlasses/settings.gradle.kts)
   and uncomment the `maven { ... }` block for the Meta SDK; add the
   matching `implementation("com.meta.wearables:dat-android:…")` line in
   `app/build.gradle.kts`. Put your Meta token in
   `~/.gradle/gradle.properties` as `META_USERNAME` / `META_TOKEN`.

4. Generate the Gradle wrapper jar (only needed once after a fresh clone;
   `gradle-wrapper.properties` is committed but the binary jar is not):
   ```bash
   cd LiveRecall/mobile/android/LiveRecallGlasses
   gradle wrapper
   ```

5. Flip `USE_WEARABLES_SDK` in `GlassesController.kt` to `true` once the
   dependency is in place, and replace the TODO bodies with the real
   `Wearables.create(context)` flow + `StreamSessionConfig` + frame flow
   collection.

### Run

- Build & run from Android Studio onto an Android 10+ device. Accept the
  Camera / Microphone / Bluetooth / Notifications prompts.
- The app starts a **foreground service** (`PublisherService`) so the
  publishing loop survives backgrounding — LiveKit's Android client docs
  recommend this pattern for any publisher.

### Distribution

- **Internal Track** on Play Console for testers (signed AAB upload).
- **Side-load** a debug APK from `app/build/outputs/apk/debug/` for quick
  hackathon iterations.

---

## Mock Device Kit (build without real glasses)

Both apps work end-to-end against the Meta **Mock Device Kit** described in
the [Wearables FAQ](https://developers.meta.com/wearables/faq):

> The Wearables Device Access Toolkit provides a Mock Device Kit for
> testing integrations without hardware. Developers can pair a mock device,
> change its state, and simulate permissions and media streaming.

That gives you the same callbacks the real glasses produce, so you can
iterate on the publisher flow on a desk without wearing the glasses. The
phone-camera fallback in this scaffold is a separate "second tier" — useful
for verifying the LiveKit half of the pipeline before the Meta SDK is even
linked.

## Pairing real glasses

1. Pair the Ray-Ban Meta with the **Meta AI** companion app on the phone.
2. In the Meta AI app, open `Settings → App Info` and tap the version
   number 5× to enable **Developer Mode**.
3. Launch this app. The Wearables SDK opens the Meta AI app for the
   permission handshake, which then deep-links back to us via the
   registered URL scheme (`liverecallglasses`). The handler logs the
   callback in the on-screen log feed.

## Backend reachability from the phone

Both apps default to `http://localhost:8000`, which only works on the same
host. For an actual phone:

- **Same Wi-Fi network** (most demos): point the Backend URL field at the
  laptop's LAN IP, e.g. `http://192.168.1.42:8000`. The backend already
  binds `0.0.0.0` and CORS is wide open in `backend/main.py`.
- **Cellular / hostile networks**: tunnel the backend with
  ```bash
  ngrok http 8000
  # or:  localhost.run --port 8000
  ```
  and use the HTTPS URL ngrok prints. iOS Safari requires HTTPS for
  cross-host media access; the native LiveKit iOS SDK does not, but the
  `/token` HTTPS hop avoids ATS warnings.

## Smoke test (5 minutes)

1. From repo root: `make backend`, `make worker`, `make dashboard`.
2. Launch the mobile app on a phone on the same Wi-Fi.
3. Set Backend URL to `http://<laptop-ip>:8000`, Room to `liverecall-demo`.
4. Tap **Connect (Ray-Ban POV)**.
5. Open `http://localhost:3000` in the browser:
   - The session should appear with the **GLASSES** purple/blue pill (see
     [`dashboard/src/components/CaptureModePill.tsx`](../dashboard/src/components/CaptureModePill.tsx)).
   - `scene_context` rows start landing as the worker samples frames.
   - The on-phone log shows `livekit: subscribed audio from worker-...` once
     the agent's TTS track is published — that's the answer playback.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `backend HTTP 500: LiveKit credentials not configured` | Backend `.env` is missing `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` / `LIVEKIT_URL`. |
| `livekit: connect failed — connection refused` | The phone can't reach the backend; switch from `localhost` to the laptop LAN IP or open an ngrok tunnel. |
| Black preview, but `livekit: connected` shows | No video track was published. Check the Wearables stream actually started (look for `wearables: startStream()` in the log) or flip Settings to phone-camera mode. |
| Dashboard pill shows **PHONE** instead of **GLASSES** | `/token` got `capture_mode="phone"` — confirm Settings has glasses-source on, and that the `BackendClient` request body still has `"capture_mode": "glasses"`. |
| Android: `app crashes on start` complaining about missing `BLUETOOTH_CONNECT` | API 31+ runtime permission. The launcher requests it on startup; if denied, re-grant from app settings. |
| iOS: `Info.plist` build error about missing `MetaAppID` | The plist still has `REPLACE_ME_META_APP_ID`. Either fill it in or temporarily comment out the toolkit Swift package so Xcode doesn't enforce the key. |
