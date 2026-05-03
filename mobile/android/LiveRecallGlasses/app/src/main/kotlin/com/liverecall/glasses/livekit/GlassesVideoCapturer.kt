package com.liverecall.glasses.livekit

import org.webrtc.CapturerObserver
import org.webrtc.SurfaceTextureHelper
import org.webrtc.VideoCapturer
import org.webrtc.VideoFrame
import android.content.Context

/**
 * Tiny [VideoCapturer] that just relays whatever frames we hand it via
 * [pushFrame] into LiveKit's pipeline. Used to bridge between the Wearables
 * toolkit's frame stream and the LiveKit Room.
 *
 * Most of the [VideoCapturer] interface is no-ops — we don't manage a real
 * camera here, we're just a stream of frames produced elsewhere.
 */
class GlassesVideoCapturer : VideoCapturer {

    @Volatile private var observer: CapturerObserver? = null
    @Volatile private var started: Boolean = false

    override fun initialize(
        helper: SurfaceTextureHelper?,
        appContext: Context?,
        observer: CapturerObserver?,
    ) {
        this.observer = observer
    }

    override fun startCapture(width: Int, height: Int, fps: Int) {
        started = true
        observer?.onCapturerStarted(true)
    }

    override fun stopCapture() {
        started = false
        observer?.onCapturerStopped()
    }

    override fun changeCaptureFormat(width: Int, height: Int, fps: Int) { /* fixed */ }
    override fun isScreencast(): Boolean = false
    override fun dispose() { observer = null }

    fun pushFrame(frame: VideoFrame) {
        if (!started) return
        observer?.onFrameCaptured(frame)
    }
}
