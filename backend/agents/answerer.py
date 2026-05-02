"""Answerer agent — GPT-4o-mini, STREAMING.

Reads `final_context` for a question and streams tokens. Each token is forwarded
to the TTS websocket so first audio byte fires before the LLM finishes.

The Answerer is shared between two callers:
  - answer_to_room(room): used by the LiveKit worker to publish audio back.
  - answer_text_only(): used by HTTP /ask for dashboard testing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..config import settings
from ..mongo import collection, watch
from ..tracing import MongoTraceCallback, trace_event
from ..tts import publish_to_room
from ..util import new_id, now_ms

log = logging.getLogger("answerer")

SYSTEM = """You are LiveRecall, a point-of-care decision-support assistant for
a clinician wearing camera glasses or holding a phone. The clinician just
asked a question. You're given the ranked clinical context the retrieval
system selected, biased toward what the clinician just SAW (drug name on the
bottle, MRN on the wristband, lab on the monitor).

Rules:
- Speak naturally, conversationally. Two short sentences max. <=45 words total.
- Cite the most concrete fact from the ranked context (a number, an MRN, a
  dose, a date — e.g. "eGFR 38 from yesterday's lab", "last admin 47 hours ago").
- If the context references a visible drug name or MRN, mention it explicitly
  by name.
- Use cautious decision-support language: "recommend", "consider", "hold and
  recheck" — never "give", "prescribe", or "diagnose". The clinician acts; you
  inform.
- If a contraindication is present in context, say so plainly and early.
- Never apologize for what you don't know. If context is thin, give the best
  summary you have in one sentence.
- No markdown, no lists, no preamble like "Based on the context".
"""


def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.2,
        max_tokens=180,
        streaming=True,
        api_key=settings.openai_api_key,
        timeout=8,
    )


def _format_context(final_ctx: dict[str, Any]) -> str:
    rows = []
    for r in final_ctx.get("ranked_results", [])[:5]:
        rows.append(
            f"- [{r.get('source')}] {r.get('snippet')} "
            f"(score={r.get('boosted_score'):.2f}, why={r.get('boost_reason')})"
        )
    return "\n".join(rows) or "(no context retrieved)"


async def stream_tokens(question: str, final_ctx: dict[str, Any]) -> AsyncIterator[str]:
    """Yield text chunks from the streaming LLM."""
    qid = final_ctx.get("question_id")
    cb = MongoTraceCallback(agent="answerer", question_id=qid, session_id=final_ctx.get("session_id"))
    msgs = [
        SystemMessage(content=SYSTEM),
        HumanMessage(content=f"Question: {question}\n\nRanked context:\n{_format_context(final_ctx)}"),
    ]
    full: list[str] = []
    async for chunk in _llm().astream(msgs, config={"callbacks": [cb]}):
        text = getattr(chunk, "content", "") or ""
        if text:
            full.append(text)
            yield text
    # Persist the final answer text.
    text = "".join(full).strip()
    await collection("answers").insert_one({
        "_id": new_id("an"),
        "question_id": qid,
        "session_id": final_ctx.get("session_id"),
        "text": text,
        "confidence": 0.85,
        "citations": [r.get("document_id") for r in final_ctx.get("ranked_results", [])][:3],
        "audio_track_id": None,
        "created_at": now_ms(),
    })


async def answer_text_only(question: str, final_ctx: dict[str, Any]) -> str:
    chunks: list[str] = []
    async for tok in stream_tokens(question, final_ctx):
        chunks.append(tok)
    return "".join(chunks).strip()


async def answer_to_room(audio_source, question: str, final_ctx: dict[str, Any]) -> int:
    """Stream LLM tokens → ElevenLabs → LiveKit audio source."""
    qid = final_ctx.get("question_id")
    t0 = now_ms()

    async def _gen() -> AsyncIterator[str]:
        async for tok in stream_tokens(question, final_ctx):
            yield tok

    bytes_written = await publish_to_room(audio_source, _gen())
    await trace_event(
        agent="answerer",
        stage="end",
        question_id=qid,
        latency_ms=now_ms() - t0,
        payload={"audio_bytes": bytes_written},
    )
    return bytes_written


async def run_answerer_loop(audio_source_provider) -> None:
    """Subscribe to `final_context`; for each, stream answer audio.

    `audio_source_provider(session_id) -> rtc.AudioSource | None` returns the
    LiveKit audio source for that session's room, or None for text-only fall-back.
    """
    log.info("answerer loop watching final_context change stream")
    async for change in watch("final_context"):
        if change.get("operationType") != "insert":
            continue
        ctx = change.get("fullDocument") or {}
        question = await _question_text_for(ctx.get("question_id"))
        if not question:
            continue
        try:
            audio_source = audio_source_provider(ctx.get("session_id")) if audio_source_provider else None
            if audio_source is None:
                await answer_text_only(question, ctx)
            else:
                await answer_to_room(audio_source, question, ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("answerer failed: %s", e)


async def _question_text_for(question_id: str | None) -> str | None:
    if not question_id:
        return None
    q = await collection("questions").find_one({"_id": question_id})
    return (q or {}).get("text")
