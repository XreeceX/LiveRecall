"""Deepgram Nova-2 streaming STT.

Per-track usage:

    stt = DeepgramSession(session_id=..., on_question=...)
    await stt.start()
    while True:
        pcm = await track.next_frame()  # 16k mono PCM s16
        await stt.send(pcm)

Final transcripts are embedded and written to `transcripts`. When a transcript
qualifies as a question (cf. util.is_question), a `questions` doc is inserted —
which the Router picks up via change stream.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

from .config import settings
from .embeddings import embed
from .mongo import collection
from .tracing import trace_event
from .util import is_question, new_id, now_ms

log = logging.getLogger("stt")

LiveResultEvent = LiveTranscriptionEvents.Transcript


class DeepgramSession:
    """One Deepgram websocket per LiveKit audio track."""

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
        self._connection = None
        self._dg = DeepgramClient(settings.deepgram_api_key)
        self._closed = False

    async def start(self) -> None:
        opts = LiveOptions(
            model="nova-2",
            language="en-US",
            encoding="linear16",
            sample_rate=self.sample_rate,
            channels=1,
            punctuate=True,
            interim_results=True,
            smart_format=True,
            endpointing=300,
            vad_events=True,
        )
        conn = self._dg.listen.asynclive.v("1")
        conn.on(LiveTranscriptionEvents.Transcript, self._on_transcript)
        conn.on(LiveTranscriptionEvents.Error, self._on_error)
        ok = await conn.start(opts)
        if not ok:
            raise RuntimeError("Deepgram failed to start")
        self._connection = conn
        log.info("deepgram session started (session=%s)", self.session_id)

    async def send(self, pcm_bytes: bytes) -> None:
        if self._connection and not self._closed:
            await self._connection.send(pcm_bytes)

    async def close(self) -> None:
        self._closed = True
        if self._connection:
            await self._connection.finish()
            self._connection = None

    # -- handlers --------------------------------------------------------------

    async def _on_transcript(self, _conn, result, **_kwargs) -> None:
        try:
            alt = result.channel.alternatives[0]
            text = (alt.transcript or "").strip()
            if not text:
                return
            is_final = bool(getattr(result, "is_final", False))
            await self._record(text, is_final=is_final)
        except Exception as e:  # noqa: BLE001
            log.exception("transcript handler failed: %s", e)

    async def _on_error(self, _conn, error, **_kwargs) -> None:
        log.error("deepgram error: %s", error)

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
