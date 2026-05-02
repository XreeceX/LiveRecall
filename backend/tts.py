"""ElevenLabs Flash v2.5 streaming TTS.

Accepts an async iterator of text chunks (Answerer tokens) and yields PCM audio
chunks suitable for an `rtc.AudioSource`. First-byte target: ~200ms.

Two flavors:
  - `stream_to_audio_chunks(text_iter)`: yields raw PCM frames.
  - `publish_to_room(room, source, text_iter)`: writes those frames to a
    LiveKit `rtc.AudioSource` already attached to a published track.

We stream over the ElevenLabs websocket so first-byte fires before the LLM has
finished generating.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator

import websockets

from .config import settings

log = logging.getLogger("tts")

VOICE_MODEL = "eleven_flash_v2_5"
SAMPLE_RATE = 16_000     # what we ask ElevenLabs to send (PCM 16k mono)
NUM_CHANNELS = 1


def _ws_url(voice_id: str) -> str:
    return (
        f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
        f"?model_id={VOICE_MODEL}"
        f"&output_format=pcm_16000"
        f"&optimize_streaming_latency=3"
    )


async def stream_to_audio_chunks(text_iter: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """Stream tokens → PCM 16k mono chunks. Closes when text_iter is exhausted."""
    voice_id = settings.elevenlabs_voice_id
    headers = {"xi-api-key": settings.elevenlabs_api_key}
    async with websockets.connect(_ws_url(voice_id), extra_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps({
            "text": " ",
            "voice_settings": {"stability": 0.4, "similarity_boost": 0.7, "use_speaker_boost": True},
            "generation_config": {"chunk_length_schedule": [50, 90, 120, 160]},
            "xi_api_key": settings.elevenlabs_api_key,
        }))

        out_q: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def _sender() -> None:
            try:
                async for tok in text_iter:
                    if not tok:
                        continue
                    await ws.send(json.dumps({"text": tok, "try_trigger_generation": True}))
            finally:
                await ws.send(json.dumps({"text": ""}))  # EOS

        async def _receiver() -> None:
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    audio_b64 = msg.get("audio")
                    if audio_b64:
                        await out_q.put(base64.b64decode(audio_b64))
                    if msg.get("isFinal"):
                        break
            finally:
                await out_q.put(None)

        sender_task = asyncio.create_task(_sender())
        receiver_task = asyncio.create_task(_receiver())

        try:
            while True:
                chunk = await out_q.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            sender_task.cancel()
            receiver_task.cancel()
            for t in (sender_task, receiver_task):
                try:
                    await t
                except Exception:  # noqa: BLE001
                    pass


async def publish_to_room(audio_source, text_iter: AsyncIterator[str]) -> int:
    """Stream tokens through ElevenLabs into a LiveKit `rtc.AudioSource`.

    Returns total bytes written.
    """
    from livekit import rtc  # local import: keeps non-LiveKit paths importable

    total = 0
    bytes_per_frame = SAMPLE_RATE * 2 // 50   # 20ms @ 16k mono PCM s16
    buf = bytearray()
    async for chunk in stream_to_audio_chunks(text_iter):
        total += len(chunk)
        buf.extend(chunk)
        while len(buf) >= bytes_per_frame:
            frame_bytes = bytes(buf[:bytes_per_frame])
            del buf[:bytes_per_frame]
            frame = rtc.AudioFrame(
                data=frame_bytes,
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
                samples_per_channel=bytes_per_frame // 2,
            )
            await audio_source.capture_frame(frame)
    if buf:
        # flush a short final frame (pad to 20ms)
        pad = bytes_per_frame - len(buf)
        if pad > 0:
            buf.extend(b"\x00" * pad)
        frame = rtc.AudioFrame(
            data=bytes(buf),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            samples_per_channel=bytes_per_frame // 2,
        )
        await audio_source.capture_frame(frame)
    return total
