"""LiveKit Agents worker — the bridge between the room and the agent pipeline.

Per session/room:
  - Subscribes to remote audio track → ElevenLabs Scribe v2 Realtime STT.
  - Subscribes to remote video track → keeps latest frame in memory (NOT
    continuously written to DB). When STT commits a question the latest cached
    frame is flushed to `video_frames` exactly once, triggering the Vision
    loop for that question only.
  - Publishes one outbound audio track; Answerer streams TTS into it.

Why question-triggered (not continuous)?
  - Laptop / browser testing: the worker is the video source. Continuous 1 fps
    sampling + GPT-4o Vision on every frame burns API quota with no benefit
    when nobody has asked anything yet.
  - Real Ray-Ban: the bridge script (bridge_rayban_snap.py) sends frames via
    POST /snap on button-press; the worker video path is not involved.
  So in both real usage patterns, Vision should fire exactly once per question.

Run as: `python -m backend.worker dev`
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Any

import numpy as np

from livekit import rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
)
from PIL import Image

from .config import settings
from .mongo import collection, init_collections
from .stt import ScribeSession
from .tracing import trace_event
from .util import new_id, now_ms

log = logging.getLogger("worker")

# session_id -> (room, audio_source) so the Answerer loop can publish back
ROOM_REGISTRY: dict[str, dict[str, Any]] = {}

# At most one STT consumer per session — duplicate track_subscribed happens on some mobile reconnects.
_AUDIO_PIPE_TASKS: dict[str, asyncio.Task[None]] = {}
# Same for video sampler (bootstrap loop + events can both fire).
_VIDEO_PIPE_TASKS: dict[str, asyncio.Task[None]] = {}

# Latest sampled JPEG for each session — updated in memory at frame_sample_hz
# but NOT written to DB until a question is committed by STT.
# Tuple: (base64_jpeg, width_px, height_px)
_latest_frames: dict[str, tuple[str, int, int]] = {}


def get_audio_source(session_id: str) -> rtc.AudioSource | None:
    entry = ROOM_REGISTRY.get(session_id)
    return entry.get("audio_source") if entry else None


async def entrypoint(ctx: JobContext) -> None:
    await init_collections()
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    room = ctx.room
    session_id = _session_id_from_room(room.name)
    log.info("worker joined room=%s session=%s", room.name, session_id)
    # Use $setOnInsert for fields the /token endpoint may have already written
    # (capture_mode, started_at) so we don't blow them away when the worker
    # joins. /token is the authoritative writer for capture_mode; the worker
    # only refreshes the room/ended_at fields on every join.
    await collection("sessions").update_one(
        {"_id": session_id},
        {
            "$set": {"room": room.name, "ended_at": None},
            "$setOnInsert": {"_id": session_id, "started_at": now_ms()},
        },
        upsert=True,
    )

    # --- Outbound audio track ------------------------------------------------
    audio_source = rtc.AudioSource(sample_rate=16_000, num_channels=1)
    out_track = rtc.LocalAudioTrack.create_audio_track("liverecall-answer", audio_source)
    await room.local_participant.publish_track(
        out_track,
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )

    ROOM_REGISTRY[session_id] = {"room": room, "audio_source": audio_source}

    # --- Subscribe handlers --------------------------------------------------
    def _ensure_remote_subscribed(pub: rtc.RemoteTrackPublication) -> None:
        """Mobile + dynacast can publish before our subscribe completes; nudge explicitly."""
        try:
            pub.set_subscribed(True)
        except Exception as e:  # noqa: BLE001
            log.warning("set_subscribed failed (session=%s): %s", session_id, e)

    @room.on("participant_connected")
    def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        log.info(
            "remote participant connected identity=%s kind=%s (session=%s)",
            participant.identity,
            participant.kind,
            session_id,
        )
        for pub in participant.track_publications.values():
            _ensure_remote_subscribed(pub)

    @room.on("participant_disconnected")
    def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        log.info(
            "participant disconnected identity=%s (session=%s) — cancelling STT/video tasks",
            participant.identity,
            session_id,
        )
        audio_task = _AUDIO_PIPE_TASKS.pop(session_id, None)
        if audio_task and not audio_task.done():
            audio_task.cancel()
        video_task = _VIDEO_PIPE_TASKS.pop(session_id, None)
        if video_task and not video_task.done():
            video_task.cancel()

    @room.on("track_published")
    def _on_track_published(pub: rtc.RemoteTrackPublication, _participant: rtc.RemoteParticipant) -> None:
        _ensure_remote_subscribed(pub)

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, _pub, _participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            prev = _AUDIO_PIPE_TASKS.get(session_id)
            if prev is not None and not prev.done():
                log.warning(
                    "duplicate audio track_subscribed ignored (session=%s); "
                    "already running STT pipeline",
                    session_id,
                )
                return

            async def _run_audio() -> None:
                await _consume_audio(track, session_id)

            t = asyncio.create_task(_run_audio(), name=f"stt-{session_id}")

            def _drop_audio_task(done: asyncio.Task[None]) -> None:
                if _AUDIO_PIPE_TASKS.get(session_id) is done:
                    _AUDIO_PIPE_TASKS.pop(session_id, None)

            t.add_done_callback(_drop_audio_task)
            _AUDIO_PIPE_TASKS[session_id] = t
        elif track.kind == rtc.TrackKind.KIND_VIDEO:
            prev_v = _VIDEO_PIPE_TASKS.get(session_id)
            if prev_v is not None and not prev_v.done():
                log.warning(
                    "duplicate video track_subscribed ignored (session=%s); sampler already running",
                    session_id,
                )
                return

            async def _run_video() -> None:
                try:
                    await _sample_video_to_cache(track, session_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("video sampler exited with error (session=%s)", session_id)

            vt = asyncio.create_task(_run_video(), name=f"video-{session_id}")

            def _drop_video_task(done: asyncio.Task[None]) -> None:
                if _VIDEO_PIPE_TASKS.get(session_id) is done:
                    _VIDEO_PIPE_TASKS.pop(session_id, None)

            vt.add_done_callback(_drop_video_task)
            _VIDEO_PIPE_TASKS[session_id] = vt

    # Tracks already published — force subscribe then attach if media is already flowing.
    for participant in room.remote_participants.values():
        for pub in participant.track_publications.values():
            _ensure_remote_subscribed(pub)
            if pub.track:
                _on_track(pub.track, pub, participant)

    try:
        await ctx.wait_for_participant()
        log.info("publisher participant ready (session=%s)", session_id)
    except Exception as e:  # noqa: BLE001
        log.warning("wait_for_participant: %s — continuing for session=%s", e, session_id)

    await asyncio.Future()  # keep the worker running until the room ends.


async def _consume_audio(track: rtc.Track, session_id: str) -> None:
    log.info("audio track subscribed (session=%s)", session_id)
    try:
        stt = ScribeSession(
            session_id=session_id,
            on_question=lambda qdoc: _flush_frame_on_question(session_id, qdoc),
        )
        await stt.start()
        try:
            astream = rtc.AudioStream(track)
            async for ev in astream:
                frame: rtc.AudioFrame = ev.frame
                pcm = _to_16k_mono(frame, stt.sample_rate)
                await stt.send(pcm)
        finally:
            await stt.close()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        import websockets as _ws
        if isinstance(exc, _ws.ConnectionClosed):
            log.warning("scribe WS closed — STT task exiting so reconnect can start fresh (session=%s)", session_id)
        else:
            log.exception(
                "audio/STT pipeline crashed (session=%s). "
                "Check ELEVENLABS_API_KEY and network; transcripts will not reach Mongo.",
                session_id,
            )


def _to_16k_mono(frame: rtc.AudioFrame, target_rate: int) -> bytes:
    """Convert any livekit AudioFrame to 16-bit mono PCM at target_rate.

    livekit 1.x removed remix_and_resample; we use numpy instead.
    Typical browser audio arrives as 48 kHz stereo — we fold channels then
    linearly interpolate down to 16 kHz (ratio 3:1, no scipy needed).
    """
    arr = np.frombuffer(bytes(frame.data), dtype=np.int16)
    if frame.num_channels > 1:
        arr = arr.reshape(-1, frame.num_channels).mean(axis=1).astype(np.int16)
    if frame.sample_rate != target_rate:
        src_len = len(arr)
        dst_len = max(1, int(src_len * target_rate / frame.sample_rate))
        arr = np.interp(
            np.linspace(0, src_len - 1, dst_len),
            np.arange(src_len),
            arr,
        ).astype(np.int16)
    return arr.tobytes()


async def _sample_video_to_cache(track: rtc.Track, session_id: str) -> None:
    """Sample video at frame_sample_hz but only keep the latest frame in memory.

    Nothing is written to DB here — _flush_frame_on_question() does that when
    STT commits a question, so Vision fires exactly once per question rather
    than once per second regardless of activity.
    """
    log.info(
        "video track subscribed — caching frames at %.1f fps, "
        "flushing to DB on question only (session=%s)",
        settings.frame_sample_hz,
        session_id,
    )
    interval_s = 1.0 / max(settings.frame_sample_hz, 0.1)
    last_t = 0.0
    vstream = rtc.VideoStream(track)
    async for ev in vstream:
        now = asyncio.get_event_loop().time()
        if now - last_t < interval_s:
            continue
        last_t = now
        try:
            _cache_frame_sync(ev.frame, session_id)
        except Exception as e:  # noqa: BLE001
            log.exception("frame cache failed: %s", e)


def _cache_frame_sync(frame: rtc.VideoFrame, session_id: str) -> None:
    """Convert the latest LiveKit frame to JPEG and store in memory (no DB)."""
    rgb = frame.convert(rtc.VideoBufferType.RGB24)
    img = Image.frombytes("RGB", (rgb.width, rgb.height), bytes(rgb.data))
    img.thumbnail((640, 640))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=72)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    _latest_frames[session_id] = (b64, img.width, img.height)


async def _flush_frame_on_question(session_id: str, qdoc: dict[str, Any]) -> None:
    """Write the cached frame to `video_frames`, triggering the Vision loop once.

    Called by the STT on_question callback — fires exactly once per committed
    question utterance.  If no frame has been cached yet (audio-only session),
    we skip gracefully; the Router will use the most recent scene_context
    already in DB.
    """
    entry = _latest_frames.get(session_id)
    if not entry:
        log.warning(
            "no cached video frame for session=%s — Vision will use last known scene_context",
            session_id,
        )
        return
    b64, w, h = entry
    doc = {
        "_id": new_id("vf"),
        "session_id": session_id,
        "timestamp": now_ms(),
        "image_b64": b64,
        "width": w,
        "height": h,
        "triggered_by_question": qdoc["_id"],
    }
    await collection("video_frames").insert_one(doc)
    await trace_event(
        agent="frame_sampler",
        stage="end",
        session_id=session_id,
        payload={
            "frame_id": doc["_id"],
            "size": len(b64),
            "triggered_by_question": qdoc["_id"],
        },
    )
    log.info(
        "flushed frame %s for question=%s (session=%s)",
        doc["_id"],
        qdoc["_id"],
        session_id,
    )


def _session_id_from_room(room_name: str) -> str:
    return room_name.removeprefix("liverecall-") or room_name


def main() -> None:
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="liverecall"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
