"""LangChain callback that records every LLM call to `agent_traces` in Mongo.

Powers the dashboard's reasoning-trace view and latency monitor.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

from .mongo import collection
from .util import new_id, now_ms

log = logging.getLogger("trace")


async def trace_event(
    *,
    agent: str,
    stage: str,
    question_id: str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    latency_ms: int | None = None,
    tokens: dict[str, int] | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Direct trace insert (used outside LangChain too — Retrievers, STT, TTS)."""
    doc = {
        "_id": new_id("tr"),
        "question_id": question_id,
        "session_id": session_id,
        "agent": agent,
        "stage": stage,
        "model": model,
        "tokens": tokens,
        "latency_ms": latency_ms,
        "timestamp": now_ms(),
        "payload": payload or {},
    }
    try:
        await collection("agent_traces").insert_one(doc)
    except Exception as e:  # noqa: BLE001
        log.warning("trace insert failed: %s", e)


class MongoTraceCallback(AsyncCallbackHandler):
    """Async LangChain callback that records start/end + token usage per agent."""

    def __init__(self, *, agent: str, question_id: str | None, session_id: str | None) -> None:
        self.agent = agent
        self.question_id = question_id
        self.session_id = session_id
        self._t0: dict[str, int] = {}

    async def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id"))
        self._t0[run_id] = now_ms()
        await trace_event(
            agent=self.agent,
            stage="start",
            question_id=self.question_id,
            session_id=self.session_id,
            payload={"prompt_chars": sum(len(p) for p in prompts)},
        )

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        run_id = str(kwargs.get("run_id"))
        self._t0[run_id] = now_ms()
        chars = sum(len(getattr(m, "content", "") or "") for batch in messages for m in batch)
        await trace_event(
            agent=self.agent,
            stage="start",
            question_id=self.question_id,
            session_id=self.session_id,
            payload={"prompt_chars": chars},
        )

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id"))
        t0 = self._t0.pop(run_id, now_ms())
        usage = {}
        try:
            usage = response.llm_output.get("token_usage") or {}
        except Exception:  # noqa: BLE001
            pass
        await trace_event(
            agent=self.agent,
            stage="end",
            question_id=self.question_id,
            session_id=self.session_id,
            model=(response.llm_output or {}).get("model_name") if response.llm_output else None,
            latency_ms=now_ms() - t0,
            tokens={
                "input": int(usage.get("prompt_tokens") or 0),
                "output": int(usage.get("completion_tokens") or 0),
                "total": int(usage.get("total_tokens") or 0),
            },
        )

    async def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = str(kwargs.get("run_id"))
        t0 = self._t0.pop(run_id, now_ms())
        await trace_event(
            agent=self.agent,
            stage="end",
            question_id=self.question_id,
            session_id=self.session_id,
            latency_ms=now_ms() - t0,
            payload={"error": str(error)},
        )


def fire_and_forget(coro: Any) -> None:
    """Schedule a coroutine without awaiting (used inside sync hot paths)."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)
