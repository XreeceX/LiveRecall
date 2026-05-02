"""Retrievers — pure Mongo aggregation pipelines, NO LLM.

Three sources run in parallel via asyncio.gather:
  1. Manuals     — Atlas $vectorSearch on `documents`
  2. Logs        — Time Series filter + sort on `maintenance_events`
  3. History     — Atlas $vectorSearch + recency boost on `transcripts`

Every plan triggers exactly 3 result writes. Latency target: <400ms total.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import settings
from ..embeddings import embed
from ..mongo import collection, watch
from ..tracing import trace_event
from ..util import new_id, now_ms
from shared.types import VEC_IDX_DOCS, VEC_IDX_TRANSCRIPTS

log = logging.getLogger("retrievers")


# --- Manuals (vector search) -------------------------------------------------

async def retrieve_manuals(query: dict[str, Any]) -> list[dict[str, Any]]:
    vec = await embed(query.get("vector_query") or "")
    pipeline: list[dict[str, Any]] = [
        {
            "$vectorSearch": {
                "index": VEC_IDX_DOCS,
                "queryVector": vec,
                "path": "text_embedding",
                "numCandidates": settings.num_candidates,
                "limit": settings.retrieval_limit,
            }
        },
        {
            "$project": {
                "snippet": "$text",
                "score": {"$meta": "vectorSearchScore"},
                "metadata": {
                    "source_doc": "$source_doc",
                    "machine_id": "$machine_id",
                    "section": "$section",
                },
            }
        },
    ]
    f = query.get("filter") or {}
    if f.get("machine_id"):
        pipeline.append({"$match": {"metadata.machine_id": f["machine_id"]}})

    items: list[dict[str, Any]] = []
    async for d in collection("documents").aggregate(pipeline):
        items.append({
            "document_id": str(d.get("_id")),
            "score": float(d.get("score") or 0.0),
            "snippet": d.get("snippet") or "",
            "metadata": d.get("metadata") or {},
        })
    if not items:
        items = await _manuals_fallback(query)
    return items


async def _manuals_fallback(query: dict[str, Any]) -> list[dict[str, Any]]:
    """When Vector Search isn't ready (cold cluster), keyword-match instead."""
    q = (query.get("vector_query") or "").strip()
    machine = (query.get("filter") or {}).get("machine_id")
    match: dict[str, Any] = {}
    if machine:
        match["machine_id"] = machine
    if q:
        match["$text"] = {"$search": q}
    cur = collection("documents").find(match).limit(settings.retrieval_limit)
    return [
        {
            "document_id": str(d.get("_id")),
            "score": 0.5,
            "snippet": d.get("text", ""),
            "metadata": {
                "source_doc": d.get("source_doc"),
                "machine_id": d.get("machine_id"),
                "section": d.get("section"),
            },
        }
        async for d in cur
    ]


# --- Logs (Time Series) ------------------------------------------------------

async def retrieve_logs(query: dict[str, Any]) -> list[dict[str, Any]]:
    f = query.get("filter") or {}
    match: dict[str, Any] = {}
    if f.get("machine_id"):
        match["machine_id"] = f["machine_id"]
    pipeline = [
        {"$match": match},
        {"$sort": {"timestamp": -1}},
        {"$limit": settings.retrieval_limit},
    ]
    out: list[dict[str, Any]] = []
    async for d in collection("maintenance_events").aggregate(pipeline):
        ts = int(d.get("timestamp") or 0)
        if hasattr(d.get("timestamp"), "timestamp"):
            ts = int(d["timestamp"].timestamp() * 1000)
        out.append({
            "document_id": f"evt:{d.get('machine_id')}:{ts}",
            "score": 1.0,
            "snippet": (
                f"[{d.get('event_type')} • {d.get('severity')}] "
                f"{d.get('notes')} (machine {d.get('machine_id')})"
            ),
            "metadata": {
                "machine_id": d.get("machine_id"),
                "event_type": d.get("event_type"),
                "severity": d.get("severity"),
                "timestamp": ts,
            },
        })
    return out


# --- History (vector + recency boost) ----------------------------------------

async def retrieve_history(query: dict[str, Any]) -> list[dict[str, Any]]:
    vec = await embed(query.get("vector_query") or "")
    now = now_ms()
    pipeline: list[dict[str, Any]] = [
        {
            "$vectorSearch": {
                "index": VEC_IDX_TRANSCRIPTS,
                "queryVector": vec,
                "path": "text_embedding",
                "numCandidates": settings.num_candidates,
                "limit": 10,
            }
        },
        {
            "$addFields": {
                "vec_score": {"$meta": "vectorSearchScore"},
                "recency_boost": {
                    "$divide": [
                        1,
                        {
                            "$add": [
                                1,
                                {
                                    "$divide": [
                                        {"$subtract": [now, "$timestamp"]},
                                        86_400_000,
                                    ]
                                },
                            ]
                        },
                    ]
                },
            }
        },
        {
            "$addFields": {
                "boosted": {"$multiply": ["$vec_score", {"$add": [1, "$recency_boost"]}]}
            }
        },
        {"$sort": {"boosted": -1}},
        {"$limit": settings.retrieval_limit},
        {
            "$project": {
                "snippet": "$text",
                "score": "$boosted",
                "metadata": {
                    "timestamp": "$timestamp",
                    "session_id": "$session_id",
                    "kind": "transcript",
                },
            }
        },
    ]
    items: list[dict[str, Any]] = []
    async for d in collection("transcripts").aggregate(pipeline):
        items.append({
            "document_id": str(d.get("_id")),
            "score": float(d.get("score") or 0.0),
            "snippet": d.get("snippet") or "",
            "metadata": d.get("metadata") or {},
        })
    return items


# --- Fan-out -----------------------------------------------------------------

DISPATCH = {
    "manuals": retrieve_manuals,
    "logs": retrieve_logs,
    "history": retrieve_history,
}


async def _run_one(plan_id: str, qid: str, query: dict[str, Any]) -> None:
    src = query.get("source", "manuals")
    fn = DISPATCH.get(src)
    if not fn:
        return
    t0 = now_ms()
    try:
        results = await fn(query)
    except Exception as e:  # noqa: BLE001
        log.exception("retriever %s failed: %s", src, e)
        results = []
    latency = now_ms() - t0
    doc = {
        "_id": new_id("rr"),
        "plan_id": plan_id,
        "question_id": qid,
        "source": src,
        "results": results,
        "latency_ms": latency,
        "created_at": now_ms(),
    }
    await collection("retrieval_results").insert_one(doc)
    await trace_event(
        agent=f"retriever:{src}",
        stage="end",
        question_id=qid,
        latency_ms=latency,
        payload={"hits": len(results)},
    )


async def run_plan(plan_doc: dict[str, Any]) -> None:
    plan_id = plan_doc["_id"]
    qid = plan_doc["question_id"]
    queries = plan_doc.get("queries") or []
    await asyncio.gather(*[_run_one(plan_id, qid, q) for q in queries])


async def run_retrievers_loop() -> None:
    log.info("retrievers loop watching retrieval_plans change stream")
    async for change in watch("retrieval_plans"):
        if change.get("operationType") != "insert":
            continue
        plan = change.get("fullDocument") or {}
        try:
            await run_plan(plan)
        except Exception as e:  # noqa: BLE001
            log.exception("retrievers failed: %s", e)
