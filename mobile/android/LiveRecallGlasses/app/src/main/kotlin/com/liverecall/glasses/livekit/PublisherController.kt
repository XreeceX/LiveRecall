package com.liverecall.glasses.livekit

import android.content.Context
import com.liverecall.glasses.data.SessionLogger
import com.liverecall.glasses.wearables.GlassesController
import io.livekit.android.ConnectOptions
import io.livekit.android.LiveKit
import io.livekit.android.LiveKitOverrides
import io.livekit.android.RoomOptions
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import io.livekit.android.room.track.LocalAudioTrack
import io.livekit.android.room.track.LocalVideoTrack
import io.livekit.android.room.track.LocalVideoTrackOptions
import io.livekit.android.room.track.video.CameraPosition
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * Owns the LiveKit Room. Connects, publishes phone mic + a video track, and
 * (via [Room]) automatically subscribes to the worker's TTS audio track so
 * the answer plays through the phone speaker.
 *
 * Two video paths:
 *   - Glasses: build a [GlassesVideoCapturer], wire it into Wearables'
 *     onVideoFrame callback, publish.
 *   - Phone fallback: ask LiveKit for a regular CameraVideoCapturer (back
 *     camera, 720p) so the app demos end-to-end before the toolkit dep is
 *     enabled.
 */
class PublisherController(private val appContext: Context) {

    sealed interface State {
        data object Disconnected : State
        data object Connecting : State
        data object Connected : State
        data class Failed(val reason: String) : State
    }

    private val _state = MutableStateFlow<State>(State.Disconnected)
    val state: StateFlow<State> = _state

    private val _localVideoTrack = MutableStateFlow<LocalVideoTrack?>(null)
    val localVideoTrack: StateFlow<LocalVideoTrack?> = _localVideoTrack

    private var room: Room? = null
    private var glassesCapturer: GlassesVideoCapturer? = null
    private var scope: CoroutineScope? = null
    private var eventJob: Job? = null

    suspend fun connect(
        url: String,
        token: String,
        useGlassesSource: Boolean,
        wearables: GlassesController?,
    ) {
        _state.value = State.Connecting

        val room = LiveKit.create(
            appContext = appContext,
            options = RoomOptions(adaptiveStream = true, dynacast = true),
            overrides = LiveKitOverrides(),
        )
        this.room = room
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

        eventJob = scope!!.launch {
            room.events.collect { ev -> handleEvent(ev) }
        }

        try {
            room.connect(url, token, ConnectOptions(autoSubscribe = true))
            _state.value = State.Connected
            SessionLogger.log("livekit: connected to $url", SessionLogger.Level.OK)

            // ---- audio (phone mic) -------------------------------------
            val mic: LocalAudioTrack = room.localParticipant.createAudioTrack(name = "phone-mic")
            room.localParticipant.publishAudioTrack(mic)
            SessionLogger.log("livekit: published phone mic", SessionLogger.Level.OK)

            // ---- video --------------------------------------------------
            if (useGlassesSource) publishGlasses(wearables) else publishPhoneCamera()
        } catch (t: Throwable) {
            _state.value = State.Failed(t.message ?: t::class.java.simpleName)
            SessionLogger.log("livekit: connect failed — ${t.message}", SessionLogger.Level.ERR)
        }
    }

    suspend fun disconnect() {
        try {
            room?.disconnect()
        } finally {
            eventJob?.cancel(); eventJob = null
            scope?.cancel(); scope = null
            room = null
            glassesCapturer = null
            _localVideoTrack.value = null
            _state.value = State.Disconnected
            SessionLogger.log("livekit: disconnected", SessionLogger.Level.INFO)
        }
    }

    private fun publishGlasses(wearables: GlassesController?) {
        val room = room ?: return
        val capturer = GlassesVideoCapturer()
        val track = room.localParticipant.createVideoTrack(
            name = "glasses-pov",
            capturer = capturer,
        )
        glassesCapturer = capturer
        _localVideoTrack.value = track
        room.localParticipant.publishVideoTrack(track)
        SessionLogger.log("livekit: published glasses-pov video track", SessionLogger.Level.OK)

        wearables?.onVideoFrame = { frame ->
            glassesCapturer?.pushFrame(frame.webrtcFrame)
        }
    }

    private fun publishPhoneCamera() {
        val room = room ?: return
        val track = room.localParticipant.createVideoTrack(
            name = "glasses-pov-fallback",
            options = LocalVideoTrackOptions(position = CameraPosition.BACK),
        )
        _localVideoTrack.value = track
        room.localParticipant.publishVideoTrack(track)
        SessionLogger.log(
            "livekit: published phone camera (glasses SDK unavailable)",
            SessionLogger.Level.WARN
        )
    }

    private fun handleEvent(ev: RoomEvent) {
        when (ev) {
            is RoomEvent.TrackSubscribed -> {
                SessionLogger.log(
                    "livekit: subscribed ${ev.track.kind} from ${ev.participant.identity?.value}",
                    SessionLogger.Level.OK
                )
            }
            is RoomEvent.Disconnected -> {
                _state.value = State.Disconnected
                SessionLogger.log("livekit: room disconnected", SessionLogger.Level.INFO)
            }
            else -> {}
        }
    }
}
