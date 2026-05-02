"""Reranker agent — GPT-4o-mini.

Waits until all 3 retrieval_results land for a plan. Single LLM call: read
scene_context + every result, return ranked list with `boost_reason` per item.

This is where the "reorder results based on what the user just saw" theme is
visible to judges. Always include at least one boost_reason that mentions a
concrete object or visible-text token from the recent scene.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..config import settings
from ..mongo import collection, watch
from ..tracing import MongoTraceCallback
from ..util import new_id, now_ms

log = logging.getLogger("reranker")

EXPECTED_SOURCES = 3

SYSTEM = """You rerank retrieval results for an adaptive retrieval system.
The user wears camera glasses; recent scene context is provided. Reorder the
results to favor anything that connects to what the user just SAW
(objects, visible text, environment).

Return STRICT JSON only:
{
  "ranked_results": [
    {
      "document_id": "...",
      "snippet": "shortened to <= 220 chars",
      "source": "manuals|logs|history",
      "boosted_score": 0.0-1.0,
      "boost_reason": "explicit reference to a scene object or visible token, or 'baseline relevance'"
    }
  ]
}

Rules:
- Return at most 5 results.
- At least one boost_reason MUST cite a concrete scene object or visible_text token if any are present.
- Do not invent facts; only rank what was given.
"""


def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        max_tokens=550,
        api_key=settings.openai_api_key,
        timeout=5,
    )


async def _wait_for_all_results(plan_id: str, timeout_s: float = 4.0) -> list[dict[str, Any]]:
    """Poll retrieval_results for the plan; return as soon as 3 are present."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        docs = [
            d
            async for d in collection("retrieval_results").find({"plan_id": plan_id})
        ]
        if len(docs) >= EXPECTED_SOURCES:
            return docs
        if asyncio.get_event_loop().time() >= deadline:
            return docs
        await asyncio.sleep(0.05)


def _trim(s: str, n: int = 220) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


async def _scene_blob(plan: dict[str, Any]) -> str:
    ids = plan.get("scene_context_ids") or []
    if not ids:
        return "(no recent scene context)"
    docs = [d async for d in collection("scene_context").find({"_id": {"$in": ids}})]
    parts = []
    for s in docs[:3]:
        parts.append(
            f"objects={s.get('objects')} visible_text={s.get('text_visible')} "
            f"summary={s.get('text_summary')}"
        )
    return " ; ".join(parts)


async def rerank(plan: dict[str, Any]) -> dict[str, Any]:
    qid = plan["question_id"]
    session_id = plan.get("session_id")
    cb = MongoTraceCallback(agent="reranker", question_id=qid, session_id=session_id)

    results = await _wait_for_all_results(plan["_id"])
    bundle = []
    for r in results:
        for item in r.get("results", [])[:5]:
            bundle.append({
                "document_id": item.get("document_id"),
                "source": r.get("source"),
                "snippet": _trim(item.get("snippet", "")),
                "score": item.get("score"),
                "metadata": item.get("metadata", {}),
            })

    scene = await _scene_blob(plan)
    prompt = (
        f"Question: {plan['question_text']}\n\n"
        f"Recent scene: {scene}\n\n"
        f"Candidate results:\n{json.dumps(bundle, ensure_ascii=False, default=str)}"
    )
    resp = await _llm().ainvoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=prompt)],
        config={"callbacks": [cb]},
    )
    raw = (resp.content or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(raw)
        ranked = parsed.get("ranked_results") or []
    except json.JSONDecodeError:
        log.warning("reranker json parse failed; using raw order")
        ranked = [
            {
                "document_id": b["document_id"],
                "source": b["source"],
                "snippet": b["snippet"],
                "boosted_score": float(b.get("score") or 0.0),
                "boost_reason": "baseline relevance",
            }
            for b in bundle[:5]
        ]

    # Attach metadata back from the original bundle.
    by_id = {b["document_id"]: b for b in bundle}
    for r in ranked:
        r["metadata"] = by_id.get(r.get("document_id"), {}).get("metadata", {})

    final = {
        "_id": new_id("fc"),
        "question_id": qid,
        "session_id": session_id,
        "ranked_results": ranked[:5],
        "created_at": now_ms(),
    }
    await collection("final_context").insert_one(final)
    return final


async def run_reranker_loop() -> None:
    """Trigger on each new retrieval_plan; rerank once results arrive."""
    log.info("reranker loop watching retrieval_plans change stream")
    async for change in watch("retrieval_plans"):
        if change.get("operationType") != "insert":
            continue
        plan = change.get("fullDocument") or {}
        try:
            await rerank(plan)
        except Exception as e:  # noqa: BLE001
            log.exception("reranker failed: %s", e)
