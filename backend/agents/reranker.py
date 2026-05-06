"""Reranker agent — lightweight keyword-boosting (no LLM).

Reads scene_context + every retrieval_results bundle, boosts scores based on:
  1. Keyword matches (apparatus names, drug names, MRNs from visible text)
  2. Recency (notes are boosted if recent)
  3. Source quality (references/events > notes baseline)

Returns top 5 results in ~50ms. No LLM calls, no active followups.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..mongo import collection, watch
from ..tracing import trace_event
from ..util import new_id, now_ms

log = logging.getLogger("reranker")

EXPECTED_SOURCES = 3


async def _wait_for_all_results(plan_id: str, timeout_s: float = 4.0) -> list[dict[str, Any]]:
    deadline = asyncio.get_event_loop().time() + timeout_s
    log.info("reranker: waiting for %d result docs for plan_id=%s (timeout=%.1fs)", EXPECTED_SOURCES, plan_id, timeout_s)
    attempts = 0
    while True:
        docs = [d async for d in collection("retrieval_results").find({"plan_id": plan_id})]
        attempts += 1
        if len(docs) >= EXPECTED_SOURCES:
            log.info("reranker: got all %d results after %d attempts for plan_id=%s", len(docs), attempts, plan_id)
            return docs
        if asyncio.get_event_loop().time() >= deadline:
            log.warning("reranker: timeout waiting for results; got %d/%d docs for plan_id=%s", len(docs), EXPECTED_SOURCES, plan_id)
            return docs
        await asyncio.sleep(0.05)


def _trim(s: str, n: int = 220) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _ranked_with_metadata(ranked: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in ranked:
        base = by_id.get(r.get("document_id"), {})
        meta = dict(base.get("metadata") or {})
        out.append({**r, "metadata": meta})
    return out


async def _get_scene_context(scene_context_ids: list[str]) -> dict[str, Any]:
    """Fetch scene context docs and extract keywords for boosting."""
    if not scene_context_ids:
        return {"keywords": [], "objects": [], "text_visible": []}
    docs = [d async for d in collection("scene_context").find({"_id": {"$in": scene_context_ids}})]
    if not docs:
        return {"keywords": [], "objects": [], "text_visible": []}
    scene = docs[0]
    keywords = set()
    keywords.update([s.lower() for s in (scene.get("apparatus") or [])])
    keywords.update([s.lower() for s in (scene.get("objects") or [])])
    keywords.update([s.lower() for s in (scene.get("text_visible") or [])])
    return {
        "keywords": list(keywords),
        "objects": scene.get("objects") or [],
        "text_visible": scene.get("text_visible") or [],
    }


def _boost_score(item: dict[str, Any], keywords: list[str], source: str, now_ms_val: int) -> tuple[float, str]:
    """Boost retrieval score based on keyword matches + recency."""
    score = float(item.get("score") or 0.0)
    boost_reason = "baseline relevance"
    snippet = (item.get("snippet") or "").lower()

    # Keyword match boost
    matched_keywords = [kw for kw in keywords if kw and kw in snippet]
    if matched_keywords:
        score *= 1.5
        boost_reason = f"matches visible: {', '.join(matched_keywords[:2])}"

    # Recency boost for notes (decay over 24h)
    if source == "notes":
        ts = item.get("metadata", {}).get("timestamp", now_ms_val)
        age_hours = (now_ms_val - ts) / (1000 * 3600)
        if age_hours < 24:
            recency_factor = 1.0 - (age_hours / 24.0) * 0.3
            score *= recency_factor
            if matched_keywords:
                boost_reason += f" (recent {int(age_hours)}h)"

    return min(score, 1.0), boost_reason


async def rerank(plan: dict[str, Any]) -> dict[str, Any]:
    qid = plan["question_id"]
    session_id = plan.get("session_id")
    plan_id = plan["_id"]
    t0 = now_ms()
    log.info("reranker.rerank() starting: qid=%s plan_id=%s", qid, plan_id)

    results = await _wait_for_all_results(plan["_id"])
    log.info("reranker: got %d retrieval_results for qid=%s", len(results), qid)

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
    by_id = {b["document_id"]: b for b in bundle}
    log.info("reranker: bundled %d candidate items for qid=%s", len(bundle), qid)

    scene_info = await _get_scene_context(plan.get("scene_context_ids") or [])
    keywords = scene_info["keywords"]
    now_val = now_ms()

    ranked = []
    for item in bundle:
        boosted_score, boost_reason = _boost_score(item, keywords, item.get("source"), now_val)
        ranked.append({
            "document_id": item.get("document_id"),
            "snippet": item.get("snippet"),
            "source": item.get("source"),
            "boosted_score": boosted_score,
            "boost_reason": boost_reason,
        })

    ranked.sort(key=lambda x: x["boosted_score"], reverse=True)

    final = {
        "_id": new_id("fc"),
        "question_id": qid,
        "session_id": session_id,
        "ranked_results": _ranked_with_metadata(ranked, by_id)[:5],
        "active_followups": [],
        "rerank_passes": 1,
        "created_at": now_ms(),
    }
    latency = now_ms() - t0
    log.info("reranker: inserting final_context for qid=%s with %d ranked results (latency=%dms)", qid, len(final["ranked_results"]), latency)
    await collection("final_context").insert_one(final)
    await trace_event(
        agent="reranker",
        stage="end",
        question_id=qid,
        session_id=session_id,
        latency_ms=latency,
        payload={"ranked_count": len(final["ranked_results"])},
    )
    log.info("reranker: completed for qid=%s", qid)
    return final


async def run_reranker_loop() -> None:
    log.info("reranker loop watching retrieval_plans change stream")
    async for change in watch("retrieval_plans"):
        op_type = change.get("operationType")
        log.info("reranker: got change event, operationType=%s", op_type)
        if op_type != "insert":
            log.info("reranker: skipping non-insert operation %s", op_type)
            continue
        plan = change.get("fullDocument") or {}
        plan_id = plan.get("_id", "?")
        log.info("reranker: processing retrieval_plan insert plan_id=%s", plan_id)
        try:
            await rerank(plan)
            log.info("reranker: rerank completed successfully for plan_id=%s", plan_id)
        except Exception as e:  # noqa: BLE001
            log.exception("reranker failed for plan_id=%s: %s", plan_id, e)
