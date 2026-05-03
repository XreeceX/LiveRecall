package com.liverecall.glasses.wearables

import com.liverecall.glasses.data.SessionLogger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Wraps the Meta Wearables Device Access Toolkit Android SDK.
 *
 * Same shape as the iOS [WearablesController]:
 *   1) register() — kicks off the Meta AI app handshake.
 *   2) requestCameraPermission() — opens the toolkit's permission UI.
 *   3) startStream() — opens a DeviceSession + StreamSession at 24 fps
 *      medium and pumps frames into [onVideoFrame].
 *
 * Until the WearablesDAT Maven dependency is enabled in
 * `app/build.gradle.kts` the methods stay in stub mode and the LiveKit
 * publisher falls back to the phone camera so the app still demos.
 *
 * Reference: https://wearables.developer.meta.com/docs/build-integration-android/
 */
class GlassesController {

    sealed interface State {
        data object Idle : State
        data object Registering : State
        data object AwaitingPermission : State
        data object Ready : State
        data object Streaming : State
        data class Unavailable(val reason: String) : State
        data class Failed(val reason: String) : State
    }

    /**
     * One frame from the glasses, normalized for the LiveKit publisher.
     * Carries an `org.webrtc.VideoFrame` because LiveKit's custom video
     * capturer surface accepts that type natively (no extra conversion
     * once we have raw YUV/I420 from the Meta SDK).
     */
    data class GlassesVideoFrame(
        val webrtcFrame: org.webrtc.VideoFrame,
    )

    var onVideoFrame: ((GlassesVideoFrame) -> Unit)? = null

    private val _state = MutableStateFlow<State>(State.Idle)
    val state: StateFlow<State> = _state

    private val useSdk: Boolean = USE_WEARABLES_SDK

    suspend fun register() {
        if (!useSdk) {
            _state.value = State.Unavailable("WearablesDAT dependency not enabled")
            SessionLogger.log("wearables: SDK not linked — falling back to phone camera",
                              SessionLogger.Level.WARN)
            return
        }
        _state.value = State.Registering
        // TODO(WearablesDAT): kick off registration flow:
        //   Wearables.create(context).register(...)
        SessionLogger.log("wearables: register() (TODO)", SessionLogger.Level.WARN)
    }

    suspend fun requestCameraPermission() {
        if (!useSdk) return
        _state.value = State.AwaitingPermission
        // TODO(WearablesDAT): wearables.requestPermission(Permission.CAMERA)
        SessionLogger.log("wearables: requestCameraPermission() (TODO)",
                          SessionLogger.Level.WARN)
    }

    suspend fun startStream() {
        if (!useSdk) return
        // TODO(WearablesDAT):
        //   val cfg = StreamSessionConfig(
        //       videoCodec = VideoCodec.RAW,
        //       resolution = Resolution.MEDIUM,
        //       frameRate = 24,
        //   )
        //   val stream = session.addStream(cfg)
        //   stream.videoFrameFlow.collect { frame ->
        //       val webrtcFrame = frame.toWebRTC()  // helper below
        //       onVideoFrame?.invoke(GlassesVideoFrame(webrtcFrame))
        //   }
        //   stream.start()
        _state.value = State.Streaming
        SessionLogger.log("wearables: startStream() (TODO)", SessionLogger.Level.WARN)
    }

    suspend fun stopStream() {
        if (!useSdk) return
        _state.value = State.Ready
        SessionLogger.log("wearables: stopStream()", SessionLogger.Level.INFO)
    }

    private companion object {
        // Flip to true after adding the WearablesDAT dependency + replacing
        // the stub bodies above with real toolkit calls.
        const val USE_WEARABLES_SDK = false
    }
}
