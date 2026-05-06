"""Router agent — GPT-4o-mini.

Reads a fresh question + last 30s of `scene_context`, emits a `retrieval_plan`
with three differentiated queries (references, events, notes). One short JSON
call.

This is the *adaptive retrieval* surface — the system rewrites the query based
on what the clinician just saw (drug name on a bottle, MRN on a wristband, lab
on a monitor).
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

SYSTEM = """You are the Router for an adaptive clinical retrieval system grounded
in real-time visual memory. The user is a clinician wearing camera glasses or
holding a phone; we just extracted recent scene context (objects + visible text
+ recognised apparatus + environment). Now they asked a question.

The References retriever queries a UNIFIED apparatus catalog where each row
has (name, context, image) and is one of:
  - category="medication"  (FDA SPL drug labels with product photos)
  - category="equipment"   (medical devices with photos)
You can target it by `name` (canonical lowercase, e.g. "metformin",
"infusion pump") and/or `category`. Prefer `name` when scene context gives
you a confident match; fall back to `category` when the question is about a
device class but you don't have the exact model.

Output STRICT JSON, no prose, with this schema:
{
  "scene_summary": "one sentence",
  "queries": [
    {
      "source": "references",
      "filter": {"name"?: "metformin", "category"?: "medication"},
      "vector_query": "phrase to embed for monographs / device safety / protocols",
      "weight": 1.0
    },
    {
      "source": "events",
      "filter": {"patient_id": "P-204"},
      "vector_query": "",
      "weight": 1.0
    },
    {
      "source": "notes",
      "filter": {},
      "vector_query": "phrase to embed for past clinical handoff notes",
      "weight": 0.7
    }
  ]
}

Rules:
- Always emit exactly 3 queries, one per source: references, events, notes.
- If visible text contains a patient identifier like P-201..P-205, set
  filter.patient_id on the events query.
- If `apparatus` from scene context is non-empty, set filter.name on the
  references query to the most-relevant apparatus name (one only). Add
  filter.category ("medication" or "equipment") to match.
- If visible text contains a medication name not yet in `apparatus`, lowercase
  it and put it in filter.name on the references query.
- Vector queries should differ across sources — references = clinical/spec
  terms (dosing, contraindications, device safety alerts), notes =
  experiential phrasing (handoff, prior visit).
- Bias filters toward what the clinician *just saw*. That is the adaptation.
"""


def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        max_tokens=350,
        api_key=settings.openai_api_key,
        timeout=5,
    )


async def recent_scene_context(session_id: str, *, seconds: int = 10) -> list[dict[str, Any]]:
    cutoff = now_ms() - seconds * 1000
    cur = collection("scene_context").find(
        {"session_id": session_id, "timestamp": {"$gte": cutoff}}
    ).sort("timestamp", -1).limit(2)
    results = [d async for d in cur]
    if not results:
        # Widen to 60s if nothing in the last 10s (e.g. question asked after pause)
        cutoff2 = now_ms() - 60_000
        cur2 = collection("scene_context").find(
            {"session_id": session_id, "timestamp": {"$gte": cutoff2}}
        ).sort("timestamp", -1).limit(1)
        results = [d async for d in cur2]
    return results


def _scene_blob(scenes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for s in scenes:
        parts.append(
            f"- t={s.get('timestamp')} env={s.get('environment')} "
            f"objects={s.get('objects')} apparatus={s.get('apparatus') or []} "
            f"visible_text={s.get('text_visible')} summary={s.get('text_summary')}"
        )
    return "\n".join(parts) or "(no recent scene context)"


def _patch_queries_from_scene(queries: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Inject scene context into retriever filters: apparatus names → references, patient IDs → events."""
    if not scenes:
        return queries

    scene = scenes[0]  # Most recent
    apparatus: list[str] = scene.get("apparatus") or []
    visible_text: list[str] = scene.get("text_visible") or []

    # Extract patient ID from visible text (e.g., "P-204")
    patient_id: str | None = None
    for text in visible_text:
        if text and text.startswith("P-"):
            parts = text.split()
            for part in parts:
                if part.startswith("P-") and len(part) > 2 and part[2:].isdigit():
                    patient_id = part
                    break

    patched = []
    for q in queries:
        pq = dict(q)
        source = q.get("source", "")

        if source == "references" and apparatus:
            # Set filter.name to the first apparatus (most relevant)
            if "filter" not in pq:
                pq["filter"] = {}
            # Determine category based on apparatus name
            app_name = apparatus[0].lower()
            pq["filter"]["name"] = app_name
            # Heuristic: if it looks like a medication, mark as such
            if any(med in app_name for med in ["metformin", "lisinopril", "amoxicillin", "insulin", "aspirin"]):
                pq["filter"]["category"] = "medication"
            elif any(dev in app_name for dev in ["pump", "monitor", "ventilator", "defibrillator", "cart"]):
                pq["filter"]["category"] = "equipment"

        elif source == "events" and patient_id:
            # Set filter.patient_id for time-series queries
            if "filter" not in pq:
                pq["filter"] = {}
            pq["filter"]["patient_id"] = patient_id

        patched.append(pq)

    return patched


async def plan(question: dict[str, Any]) -> dict[str, Any]:
    """Build and persist a retrieval_plan from a question doc."""
    qid = question["_id"]
    session_id = question["session_id"]
    log.info("router.plan() starting: qid=%s session=%s", qid, session_id)

    scenes = await recent_scene_context(session_id)
    log.info("router: fetched scenes=%d for qid=%s", len(scenes), qid)
    if scenes:
        log.info("router: scene apparatus=%s visible=%s", scenes[0].get("apparatus"), scenes[0].get("text_visible"))

    cb = MongoTraceCallback(agent="router", question_id=qid, session_id=session_id)

    prompt = (
        f"Question: {question['text']}\n\n"
        f"Recent scene context (newest first):\n{_scene_blob(scenes)}"
    )
    log.info("router: calling llm for qid=%s", qid)
    resp = await _llm().ainvoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=prompt)],
        config={"callbacks": [cb]},
    )
    raw = (resp.content or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    log.info("router: llm response raw (first 300 chars): %s", raw[:300])

    try:
        parsed = json.loads(raw)
        log.info("router: parsed json successfully for qid=%s", qid)
    except json.JSONDecodeError as e:
        log.warning("router json parse failed for qid=%s, error=%s, falling back. raw=%s", qid, e, raw[:200])
        parsed = {"queries": _fallback_queries(question["text"])}

    queries = parsed.get("queries") or _fallback_queries(question["text"])
    log.info("router: got %d queries before patching for qid=%s", len(queries), qid)

    queries = _patch_queries_from_scene(queries[:3], scenes)
    log.info("router: patched queries for qid=%s, now have %d queries", qid, len(queries))

    plan_doc = {
        "_id": new_id("rp"),
        "question_id": qid,
        "session_id": session_id,
        "question_text": question["text"],
        "scene_context_ids": [s["_id"] for s in scenes],
        "queries": queries[:3],
        "created_at": now_ms(),
    }
    log.info("router: inserting retrieval_plan doc id=%s for qid=%s", plan_doc["_id"], qid)
    await collection("retrieval_plans").insert_one(plan_doc)
    log.info("router: retrieval_plan inserted successfully id=%s for qid=%s", plan_doc["_id"], qid)
    return plan_doc


def _fallback_queries(question_text: str) -> list[dict[str, Any]]:
    return [
        {"source": "references", "filter": {}, "vector_query": question_text, "weight": 1.0},
        {"source": "events", "filter": {}, "vector_query": "", "weight": 1.0},
        {"source": "notes", "filter": {}, "vector_query": question_text, "weight": 0.7},
    ]


async def run_router_loop() -> None:
    """Subscribe to new questions and produce retrieval_plans."""
    log.info("router loop watching questions change stream")
    async for change in watch("questions"):
        op_type = change.get("operationType")
        log.info("router: got change event, operationType=%s", op_type)
        if op_type != "insert":
            log.info("router: skipping non-insert operation %s", op_type)
            continue
        q = change.get("fullDocument") or {}
        qid = q.get("_id", "?")
        log.info("router: processing question insert qid=%s text='%s'", qid, q.get("text", "")[:50])
        try:
            await plan(q)
            log.info("router: plan completed successfully for qid=%s", qid)
        except Exception as e:  # noqa: BLE001
            log.exception("router failed for qid=%s: %s", qid, e)
