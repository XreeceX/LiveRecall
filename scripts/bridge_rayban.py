"""Meta Ray-Ban → LiveKit bridge (file-source mode).

Publishes a pre-recorded MP4 (e.g. a clip exported from the Meta View app
after capturing through Ray-Ban Meta glasses) into a LiveKit room as the
``glasses`` participant. From the backend's point of view this is
indistinguishable from a real first-person Ray-Ban feed: the same audio +
video tracks show up in the room, the Worker's frame sampler writes
``video_frames`` at ~1 fps, Vision writes ``scene_context`` with
``capture_mode="glasses"``, and every downstream retriever runs unchanged.

This is the "pre-recorded MP4" integration path documented in
``DECISIONS.md`` entry (g). Two other options in that entry:

  - RTMP/WHIP Ingress via LiveKit Cloud (for an external live encoder).
  - Meta Live AI SDK (gated; requires Meta developer access).

Usage::

    # Backend must be running (so we can hit /token) and LiveKit credentials
    # must be in .env. `rayban.mp4` can be any H.264 / AAC MP4.
    python -m scripts.bridge_rayban rayban.mp4 \\
        --room liverecall-demo \\
        --identity rayban-bridge \\
        --loop

What it does, start to finish:

 1. ``POST /token`` with ``capture_mode="glasses"`` so the session doc is
    stamped correctly and Vision picks the first-person POV prompt.
 2. Opens the MP4 with PyAV. Auto-detects video size + fps; resamples audio
    to 48 kHz mono s16 (LiveKit's expected track format).
 3. Creates one ``VideoSource`` + one ``AudioSource`` and publishes them as
    ``CAMERA`` + ``MICROPHONE`` tracks from the bridge participant.
 4. Decodes video and audio in parallel; feeds every frame to an
    ``AVSynchronizer`` which keeps A/V in lock-step at the file's own fps.
 5. On ``--loop`` re-opens the file and continues forever (good for demos).

Design notes:
  - We deliberately read video at its *source* fps. The Worker's frame
    sampler rate (``FRAME_SAMPLE_HZ``) already throttles what reaches
    Vision, so pushing 24–30 fps here is fine and keeps the preview smooth.
  - AVSynchronizer is the SDK's official way to maintain A/V sync when
    publishing file-sourced tracks. We don't roll our own clock.
  - No dependency on Pillow or ffmpeg binaries; PyAV bundles libav.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

try:
    import av  # PyAV — decodes MP4/H.264/AAC
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "PyAV is required for the bridge. Install with `pip install 'av>=13.0'`\n"
        f"(import error: {e})"
    ) from e

from livekit import rtc

log = logging.getLogger("bridge_rayban")


# LiveKit's audio tracks are 48 kHz mono int16 by default. Resample to that
# so we don't have to negotiate anything at publish time.
AUDIO_SAMPLE_RATE = 48_000
AUDIO_CHANNELS = 1
# 10 ms per audio frame is the canonical LiveKit chunk size.
AUDIO_FRAME_MS = 10
AUDIO_SAMPLES_PER_FRAME = AUDIO_SAMPLE_RATE * AUDIO_FRAME_MS // 1000  # 480


# --- Token fetch ------------------------------------------------------------

async def fetch_token(backend_url: str, identity: str, room: str) -> dict[str, Any]:
    """POST /token with capture_mode="glasses" so the backend stamps the
    session correctly. Returns ``{token, url, room, capture_mode}``.
    """
    payload = {
        "identity": identity,
        "room": room,
        "capture_mode": "glasses",
        # Bridge publishes only; no need to subscribe to its own tracks. We
        # still leave can_subscribe=True because the worker publishes the
        # answer audio track back into the room and a future UI hook may
        # want to monitor it.
        "can_publish": True,
        "can_subscribe": True,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{backend_url.rstrip('/')}/token", json=payload)
        r.raise_for_status()
        return r.json()


# --- Video / audio decoders -------------------------------------------------

def _open_container(path: Path) -> av.container.InputContainer:
    if not path.exists():
        raise FileNotFoundError(f"missing MP4: {path}")
    return av.open(str(path))


async def _publish_video(
    sync: rtc.AVSynchronizer,
    container: av.container.InputContainer,
    video_stream: "av.video.stream.VideoStream",
    stop: asyncio.Event,
) -> None:
    """Decode the video stream and push I420 frames into the synchronizer.

    We run the actual decoding in a worker thread because ``container.decode``
    blocks, and we don't want to stall the event loop (especially the audio
    pipeline running next to us).
    """
    loop = asyncio.get_running_loop()
    width = video_stream.codec_context.width
    height = video_stream.codec_context.height
    log.info("video: %dx%d @ %s fps", width, height, video_stream.base_rate)

    # Generator yields decoded PyAV frames one at a time, in a thread.
    def _iter_frames():
        for frame in container.decode(video_stream):
            yield frame

    it = _iter_frames()
    while not stop.is_set():
        try:
            frame = await loop.run_in_executor(None, next, it, None)
        except StopIteration:
            frame = None
        if frame is None:
            return

        # I420 (yuv420p) is the native WebRTC format — zero extra conversion
        # on the LiveKit side. to_ndarray(format='yuv420p') returns a single
        # (H*3/2, W) uint8 array with Y then U then V packed contiguously,
        # which is byte-identical to the layout LiveKit's I420 buffer wants.
        arr = frame.reformat(format="yuv420p").to_ndarray()
        vframe = rtc.VideoFrame(
            width=width,
            height=height,
            type=rtc.VideoBufferType.I420,
            data=arr.tobytes(),
        )
        ts = float(frame.time) if frame.time is not None else None
        await sync.push(vframe, ts)


async def _publish_audio(
    sync: rtc.AVSynchronizer,
    container: av.container.InputContainer,
    audio_stream: "av.audio.stream.AudioStream",
    stop: asyncio.Event,
) -> None:
    """Decode + resample the audio stream and push 10 ms AudioFrames.

    PyAV's AudioResampler handles the rate / layout / format conversion in
    one pass and emits ``av.AudioFrame``s whose data is ready to hand to
    LiveKit. We re-chunk to 10 ms because that's the canonical WebRTC
    cadence; larger chunks work but waste jitter-buffer budget.
    """
    loop = asyncio.get_running_loop()
    resampler = av.AudioResampler(
        format="s16",
        layout="mono",
        rate=AUDIO_SAMPLE_RATE,
    )
    log.info(
        "audio: %s ch → mono %d Hz s16 (10ms frames, %d samples)",
        audio_stream.codec_context.channels,
        AUDIO_SAMPLE_RATE,
        AUDIO_SAMPLES_PER_FRAME,
    )

    def _iter_frames():
        for frame in container.decode(audio_stream):
            yield frame

    it = _iter_frames()
    # Rolling PCM buffer so we can emit fixed-size 10 ms chunks regardless of
    # what the input codec gave us (AAC frames are typically 1024 samples,
    # which at 48k is ~21.3 ms — awkward for WebRTC).
    leftover: bytes = b""
    bytes_per_frame = AUDIO_SAMPLES_PER_FRAME * 2  # s16 mono → 2 bytes/sample

    async def flush_chunk(chunk: bytes, end_time: float | None) -> None:
        aframe = rtc.AudioFrame(
            data=chunk,
            sample_rate=AUDIO_SAMPLE_RATE,
            num_channels=AUDIO_CHANNELS,
            samples_per_channel=AUDIO_SAMPLES_PER_FRAME,
        )
        await sync.push(aframe, end_time)

    while not stop.is_set():
        try:
            frame = await loop.run_in_executor(None, next, it, None)
        except StopIteration:
            frame = None
        if frame is None:
            break

        for resampled in resampler.resample(frame):
            # s16 mono → one plane.
            leftover += bytes(resampled.planes[0])
            while len(leftover) >= bytes_per_frame:
                chunk, leftover = leftover[:bytes_per_frame], leftover[bytes_per_frame:]
                await flush_chunk(chunk, None)

    # Flush the resampler + any trailing PCM so we don't drop the last ms.
    for resampled in resampler.resample(None):
        leftover += bytes(resampled.planes[0])
    while len(leftover) >= bytes_per_frame:
        chunk, leftover = leftover[:bytes_per_frame], leftover[bytes_per_frame:]
        await flush_chunk(chunk, None)
    if leftover:
        # Pad the final partial chunk with silence rather than drop it.
        pad = bytes(bytes_per_frame - len(leftover))
        await flush_chunk(leftover + pad, None)


# --- Main loop --------------------------------------------------------------

async def play_file_once(
    room: rtc.Room,
    path: Path,
    video_source: rtc.VideoSource,
    audio_source: rtc.AudioSource,
    stop: asyncio.Event,
) -> None:
    container = _open_container(path)
    try:
        video_streams = [s for s in container.streams if s.type == "video"]
        audio_streams = [s for s in container.streams if s.type == "audio"]
        if not video_streams:
            raise RuntimeError(f"no video stream in {path}")
        vstream = video_streams[0]
        astream = audio_streams[0] if audio_streams else None
        fps = float(vstream.base_rate) if vstream.base_rate else 24.0

        sync = rtc.AVSynchronizer(
            audio_source=audio_source,
            video_source=video_source,
            video_fps=fps,
        )

        tasks = [asyncio.create_task(_publish_video(sync, container, vstream, stop))]
        if astream:
            tasks.append(asyncio.create_task(_publish_audio(sync, container, astream, stop)))
        else:
            log.warning("no audio stream — publishing video only (STT will be silent)")
        try:
            await asyncio.gather(*tasks)
            await sync.wait_for_playout()
        finally:
            for t in tasks:
                t.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)
            await sync.aclose()
    finally:
        container.close()


async def run(args: argparse.Namespace) -> int:
    load_dotenv()

    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        log.error("file not found: %s", path)
        return 2

    backend = args.backend or "http://localhost:8000"
    log.info("fetching token from %s (room=%s identity=%s)", backend, args.room, args.identity)
    try:
        tok = await fetch_token(backend, args.identity, args.room)
    except httpx.HTTPError as e:
        log.error("token fetch failed: %s", e)
        return 3
    url = tok["url"]
    token = tok["token"]
    log.info("connecting to LiveKit room %s at %s as %s",
             args.room, url, args.identity)

    room = rtc.Room()
    await room.connect(url, token)
    log.info("connected. publishing 'glasses' tracks …")

    # Resolve the first video stream eagerly so we can pre-size VideoSource.
    probe = _open_container(path)
    vstreams = [s for s in probe.streams if s.type == "video"]
    if not vstreams:
        probe.close()
        log.error("no video stream in %s", path)
        await room.disconnect()
        return 4
    width = vstreams[0].codec_context.width
    height = vstreams[0].codec_context.height
    probe.close()

    video_source = rtc.VideoSource(width, height)
    audio_source = rtc.AudioSource(
        sample_rate=AUDIO_SAMPLE_RATE, num_channels=AUDIO_CHANNELS
    )

    video_track = rtc.LocalVideoTrack.create_video_track("rayban-pov", video_source)
    audio_track = rtc.LocalAudioTrack.create_audio_track("rayban-mic", audio_source)

    await room.local_participant.publish_track(
        video_track,
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA),
    )
    await room.local_participant.publish_track(
        audio_track,
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )

    stop = asyncio.Event()

    def _handle_signal(*_: Any) -> None:
        log.info("signal received — stopping bridge")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal)

    try:
        pass_num = 0
        while not stop.is_set():
            pass_num += 1
            log.info("playing %s (pass %d)", path.name, pass_num)
            await play_file_once(room, path, video_source, audio_source, stop)
            if not args.loop:
                break
            log.info("looping …")
    finally:
        log.info("disconnecting")
        await audio_source.aclose()
        await video_source.aclose()
        await room.disconnect()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Publish a pre-recorded Ray-Ban MP4 into a LiveKit room "
        "as the 'glasses' participant — see DECISIONS.md (g).",
    )
    p.add_argument("file", help="path to the MP4 to play")
    p.add_argument(
        "--room",
        default="liverecall-demo",
        help="LiveKit room name (default: liverecall-demo)",
    )
    p.add_argument(
        "--identity",
        default="rayban-bridge",
        help="LiveKit participant identity (default: rayban-bridge)",
    )
    p.add_argument(
        "--backend",
        default=None,
        help="LiveRecall backend URL (default: http://localhost:8000)",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        help="loop the file forever (useful for continuous demos)",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
