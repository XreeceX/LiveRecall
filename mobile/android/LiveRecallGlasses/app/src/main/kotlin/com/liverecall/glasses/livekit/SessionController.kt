package com.liverecall.glasses.livekit

import android.content.Context
import com.liverecall.glasses.data.AppConfig
import com.liverecall.glasses.data.SessionLogger
import com.liverecall.glasses.net.BackendClient
import com.liverecall.glasses.wearables.GlassesController
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Single object the Compose UI talks to. Composes [GlassesController],
 * [PublisherController], and [BackendClient] into the same flow as the
 * iOS [SessionController]:
 *
 *   1. Start the Wearables stream (or skip if user picked phone-camera).
 *   2. POST /token to the backend with capture_mode="glasses".
 *   3. Connect LiveKit + publish audio/video.
 */
class SessionController(appContext: Context) {

    sealed interface Status {
        data object Idle : Status
        data object Connecting : Status
        data object Live : Status
        data class Error(val message: String) : Status
    }

    val wearables = GlassesController()
    val publisher = PublisherController(appContext)

    private val _status = MutableStateFlow<Status>(Status.Idle)
    val status: StateFlow<Status> = _status

    val isLive: Boolean get() = _status.value is Status.Live

    suspend fun connect(snapshot: AppConfig.Snapshot) {
        _status.value = Status.Connecting

        if (snapshot.preferGlasses) {
            wearables.register()
            wearables.requestCameraPermission()
            wearables.startStream()
        } else {
            SessionLogger.log("session: phone-camera mode (glasses skipped)",
                              SessionLogger.Level.INFO)
        }

        try {
            val token = BackendClient.requestToken(
                backendUrl = snapshot.backendUrl,
                identity = snapshot.identity,
                room = snapshot.room,
                captureMode = "glasses",
            )
            SessionLogger.log(
                "backend: token ok (room=${token.room}, mode=${token.capture_mode})",
                SessionLogger.Level.OK
            )

            val useGlasses = wearables.state.value is GlassesController.State.Streaming
            publisher.connect(
                url = token.url,
                token = token.token,
                useGlassesSource = useGlasses,
                wearables = if (useGlasses) wearables else null,
            )

            _status.value = when (val s = publisher.state.value) {
                is PublisherController.State.Connected -> Status.Live
                is PublisherController.State.Failed -> Status.Error(s.reason)
                else -> Status.Error("LiveKit did not reach Connected state")
            }
        } catch (t: Throwable) {
            _status.value = Status.Error(t.message ?: t::class.java.simpleName)
            SessionLogger.log("session: connect failed — ${t.message}",
                              SessionLogger.Level.ERR)
        }
    }

    suspend fun disconnect() {
        publisher.disconnect()
        wearables.stopStream()
        _status.value = Status.Idle
    }
}
