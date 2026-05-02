"""LiveKit Agents worker — the bridge between the room and the agent pipeline.

Per session/room:
  - Subscribes to remote audio track → ElevenLabs Scribe v2 Realtime STT.
  - Subscribes to remote video track → 1 fps frame sampler → `video_frames`.
  - Publishes one outbound audio track; Answerer streams TTS into it.

Run as: `python -m backend.worker dev`
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Any

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


def get_audio_source(session_id: str) -> rtc.AudioSource | None:
    entry = ROOM_REGISTRY.get(session_id)
    return entry.get("audio_source") if entry else None


async def entrypoint(ctx: JobContext) -> None:
    await init_collections()
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_AND_VIDEO)

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
    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, _pub, _participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(_consume_audio(track, session_id))
        elif track.kind == rtc.TrackKind.KIND_VIDEO:
            asyncio.create_task(_consume_video(track, session_id))

    # If tracks were already published before we attached the handler.
    for participant in room.remote_participants.values():
        for pub in participant.tracks.values():
            if pub.track:
                _on_track(pub.track, pub, participant)

    await asyncio.Future()  # keep the worker running until the room ends.


async def _consume_audio(track: rtc.Track, session_id: str) -> None:
    log.info("audio track subscribed (session=%s)", session_id)
    stt = ScribeSession(session_id=session_id)
    await stt.start()
    try:
        astream = rtc.AudioStream(track)
        async for ev in astream:
            frame: rtc.AudioFrame = ev.frame
            if frame.sample_rate != stt.sample_rate or frame.num_channels != 1:
                # remix to 16k mono
                frame = frame.remix_and_resample(stt.sample_rate, 1)
            await stt.send(bytes(frame.data))
    finally:
        await stt.close()


async def _consume_video(track: rtc.Track, session_id: str) -> None:
    log.info("video track subscribed (session=%s)", session_id)
    interval_s = 1.0 / max(settings.frame_sample_hz, 0.1)
    last_t = 0.0
    vstream = rtc.VideoStream(track)
    async for ev in vstream:
        now = asyncio.get_event_loop().time()
        if now - last_t < interval_s:
            continue
        last_t = now
        try:
            await _ingest_frame(ev.frame, session_id)
        except Exception as e:  # noqa: BLE001
            log.exception("frame ingest failed: %s", e)


async def _ingest_frame(frame: rtc.VideoFrame, session_id: str) -> None:
    # Convert to JPEG via Pillow.
    rgb = frame.convert(rtc.VideoBufferType.RGB24)
    img = Image.frombytes("RGB", (rgb.width, rgb.height), bytes(rgb.data))
    img.thumbnail((640, 640))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=72)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    doc = {
        "_id": new_id("vf"),
        "session_id": session_id,
        "timestamp": now_ms(),
        "image_b64": b64,
        "width": img.width,
        "height": img.height,
    }
    await collection("video_frames").insert_one(doc)
    await trace_event(
        agent="frame_sampler",
        stage="end",
        session_id=session_id,
        payload={"frame_id": doc["_id"], "size": len(b64)},
    )


def _session_id_from_room(room_name: str) -> str:
    return room_name.removeprefix("liverecall-") or room_name


def main() -> None:
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
