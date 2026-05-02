"""Router agent — GPT-4o-mini.

Reads a fresh question + last 30s of `scene_context`, emits a `retrieval_plan`
with three differentiated queries (manuals, logs, history). One short JSON call.

This is the *adaptive retrieval* surface — the system rewrites the query based
on what the wearer just saw.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..config import settings
from ..mongo import collection, watch
from ..tracing import MongoTraceCallback
from ..util import new_id, now_ms

log = logging.getLogger("router")

SYSTEM = """You are the Router for an adaptive retrieval system grounded in
real-time visual memory. The user is wearing camera glasses; we just extracted
recent scene context (objects + visible text + environment). Now they asked a
question.

Output STRICT JSON, no prose, with this schema:
{
  "scene_summary": "one sentence",
  "queries": [
    {
      "source": "manuals",
      "filter": {"machine_id"?: "C-204", "topic"?: "..."},
      "vector_query": "phrase to embed for vector search across product manuals",
      "weight": 1.0
    },
    {
      "source": "logs",
      "filter": {"machine_id": "C-204"},
      "vector_query": "",
      "weight": 1.0
    },
    {
      "source": "history",
      "filter": {},
      "vector_query": "phrase to embed for past inspection transcripts",
      "weight": 0.7
    }
  ]
}

Rules:
- Always emit exactly 3 queries, one per source: manuals, logs, history.
- If visible text contains a machine ID like C-201..C-205, set filter.machine_id.
- Vector queries should differ across sources — manuals = product/spec terms,
  history = experiential phrasing.
- Bias filters toward what was *just seen*. That is the adaptation.
"""


def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        max_tokens=350,
        api_key=settings.openai_api_key,
        timeout=5,
    )


async def recent_scene_context(session_id: str, *, seconds: int = 30) -> list[dict[str, Any]]:
    cutoff = now_ms() - seconds * 1000
    cur = collection("scene_context").find(
        {"session_id": session_id, "timestamp": {"$gte": cutoff}}
    ).sort("timestamp", -1).limit(5)
    return [d async for d in cur]


def _scene_blob(scenes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for s in scenes:
        parts.append(
            f"- t={s.get('timestamp')} env={s.get('environment')} "
            f"objects={s.get('objects')} visible_text={s.get('text_visible')} "
            f"summary={s.get('text_summary')}"
        )
    return "\n".join(parts) or "(no recent scene context)"


async def plan(question: dict[str, Any]) -> dict[str, Any]:
    """Build and persist a retrieval_plan from a question doc."""
    qid = question["_id"]
    session_id = question["session_id"]
    scenes = await recent_scene_context(session_id, seconds=30)
    cb = MongoTraceCallback(agent="router", question_id=qid, session_id=session_id)

    prompt = (
        f"Question: {question['text']}\n\n"
        f"Recent scene context (newest first):\n{_scene_blob(scenes)}"
    )
    resp = await _llm().ainvoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=prompt)],
        config={"callbacks": [cb]},
    )
    raw = (resp.content or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("router json parse failed, falling back. raw=%s", raw[:200])
        parsed = {"queries": _fallback_queries(question["text"])}

    queries = parsed.get("queries") or _fallback_queries(question["text"])
    plan_doc = {
        "_id": new_id("rp"),
        "question_id": qid,
        "session_id": session_id,
        "question_text": question["text"],
        "scene_context_ids": [s["_id"] for s in scenes],
        "queries": queries[:3],
        "created_at": now_ms(),
    }
    await collection("retrieval_plans").insert_one(plan_doc)
    return plan_doc


def _fallback_queries(question_text: str) -> list[dict[str, Any]]:
    return [
        {"source": "manuals", "filter": {}, "vector_query": question_text, "weight": 1.0},
        {"source": "logs", "filter": {}, "vector_query": "", "weight": 1.0},
        {"source": "history", "filter": {}, "vector_query": question_text, "weight": 0.7},
    ]


async def run_router_loop() -> None:
    """Subscribe to new questions and produce retrieval_plans."""
    log.info("router loop watching questions change stream")
    async for change in watch("questions"):
        if change.get("operationType") != "insert":
            continue
        q = change.get("fullDocument") or {}
        try:
            await plan(q)
        except Exception as e:  # noqa: BLE001
            log.exception("router failed: %s", e)
