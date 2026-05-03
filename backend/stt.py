"""ElevenLabs Scribe v2 Realtime streaming STT.

Per-track usage (matches what backend/worker.py expects):

    stt = ScribeSession(session_id=..., on_question=...)
    await stt.start()
    while True:
        pcm = await track.next_frame()  # 16k mono PCM s16
        await stt.send(pcm)

ElevenLabs Scribe v2 Realtime is a websocket transcription service:
  - URL:    wss://api.elevenlabs.io/v1/speech-to-text/realtime
  - Auth:   xi-api-key header
  - Send:   {"message_type": "input_audio_chunk", "audio_base_64": "...",
             "sample_rate": 16000}
  - Recv:   {"message_type": "partial_transcript",  "text": "..."}
            {"message_type": "committed_transcript","text": "..."}

We use VAD-based commits so the model decides when an utterance is finished.
On each `committed_transcript` we embed + insert into `transcripts`. If the
text qualifies as a question (cf. util.is_question), we also insert a
`questions` doc — Router subscribes to that change stream.

Why ElevenLabs (not Deepgram): see DECISIONS.md.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from typing import Any

import websockets

from .config import settings
from .embeddings import embed
from .mongo import collection
from .tracing import trace_event
from .util import is_question, new_id, now_ms

log = logging.getLogger("stt")

SCRIBE_URL = (
    "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
    "?model_id=scribe_v2_realtime"
    "&audio_format=pcm_16000"
    "&commit_strategy=vad"
    "&language_code=en"
    "&vad_silence_threshold_secs=0.6"
    "&vad_threshold=0.5"
    "&min_speech_duration_ms=120"
    "&min_silence_duration_ms=200"
)


class ScribeSession:
    """One Scribe v2 Realtime websocket per LiveKit audio track."""

    def __init__(
        self,
        *,
        session_id: str,
        on_question: Callable[[dict[str, Any]], Any] | None = None,
        sample_rate: int = 16_000,
    ) -> None:
        self.session_id = session_id
        self.on_question = on_question
        self.sample_rate = sample_rate
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        headers = {"xi-api-key": settings.elevenlabs_api_key}
        self._ws = await websockets.connect(
            SCRIBE_URL,
            extra_headers=headers,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        log.info("scribe session started (session=%s)", self.session_id)

    async def send(self, pcm_bytes: bytes) -> None:
        if not self._ws or self._closed or not pcm_bytes:
            return
        try:
            await self._ws.send(json.dumps({
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(pcm_bytes).decode("ascii"),
                "sample_rate": self.sample_rate,
            }))
        except websockets.ConnectionClosed:
            self._closed = True

    async def close(self) -> None:
        self._closed = True
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._recv_task = None
        if self._ws:
            try:
                await self._ws.close()
            finally:
                self._ws = None

    # -- internals -------------------------------------------------------------

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("message_type")
                text = (msg.get("text") or "").strip()
                if not text:
                    if mtype == "session_started":
                        log.info(
                            "scribe session_started session_id=%s",
                            msg.get("session_id"),
                        )
                    elif mtype in {"error", "auth_error", "rate_limited", "quota_exceeded"}:
                        log.error("scribe error: %s", msg)
                    continue
                if mtype == "partial_transcript":
                    await self._record(text, is_final=False)
                elif mtype in ("committed_transcript", "committed_transcript_with_timestamps"):
                    await self._record(text, is_final=True)
                elif mtype in {"error", "auth_error", "rate_limited", "quota_exceeded"}:
                    log.error("scribe error: %s", msg)
        except websockets.ConnectionClosed as e:
            if not self._closed:
                log.warning("scribe ws closed: %s", e)
        except Exception as e:  # noqa: BLE001
            log.exception("scribe recv loop crashed: %s", e)

    async def _record(self, text: str, *, is_final: bool) -> None:
        question = is_final and is_question(text)
        vec: list[float] | None = None
        if is_final and (question or len(text) > 12):
            try:
                vec = await embed(text)
            except Exception as e:  # noqa: BLE001
                log.warning("embed failed: %s", e)

        tdoc = {
            "_id": new_id("ts"),
            "session_id": self.session_id,
            "timestamp": now_ms(),
            "text": text,
            "is_final": is_final,
            "is_question": question,
            "text_embedding": vec,
        }
        await collection("transcripts").insert_one(tdoc)

        if question:
            qdoc = {
                "_id": new_id("q"),
                "session_id": self.session_id,
                "transcript_id": tdoc["_id"],
                "text": text,
                "asked_at": now_ms(),
            }
            await collection("questions").insert_one(qdoc)
            await trace_event(
                agent="stt",
                stage="end",
                question_id=qdoc["_id"],
                session_id=self.session_id,
                payload={"text": text},
            )
            if self.on_question:
                await _maybe_await(self.on_question(qdoc))


async def _maybe_await(x: Any) -> None:
    if asyncio.iscoroutine(x):
        await x
