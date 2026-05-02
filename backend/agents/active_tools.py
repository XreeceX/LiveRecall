"""Active-retrieval tools — bounded, sub-100 ms point queries.

The Reranker can request up to 2 of these in one turn when it spots an
information gap (e.g. it has a renal-contraindication chunk for metformin but
no recent eGFR in the events list). The tools are deliberately narrow: each
one returns a single fact in a fixed shape so the Reranker can fold it into
its second pass without re-parsing.

Why "tools" and not "more retrievers"?
  - Retrievers fan out broad context up-front (no question yet, no targeting).
  - Tools execute *targeted* lookups *after* the Reranker has read the broad
    context and identified what's missing. That's the FLARE pattern adapted
    for our medical domain — and crucially, applied at the Reranker layer so
    it doesn't break the Answerer's TTS-streaming critical path.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..mongo import collection
from ..util import now_ms

log = logging.getLogger("active_tools")

ALLOWED_TOOLS = {"get_latest_lab", "get_last_administration", "get_monograph_section"}


def _ts_to_ms(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _hours_ago(ms: int) -> float:
    if not ms:
        return 0.0
    return (now_ms() - ms) / 3_600_000.0


# --- Tools ------------------------------------------------------------------

async def get_latest_lab(*, patient_id: str, lab_name: str) -> dict[str, Any]:
    doc = await collection("clinical_events").find_one(
        {"patient_id": patient_id, "event_type": "lab_result", "lab_name": lab_name},
        sort=[("timestamp", -1)],
    )
    if not doc:
        return {
            "snippet": f"No {lab_name} on record for {patient_id}.",
            "metadata": {"patient_id": patient_id, "lab_name": lab_name, "found": False},
        }
    ts = _ts_to_ms(doc.get("timestamp"))
    val, unit = doc.get("lab_value"), (doc.get("lab_unit") or "")
    age_h = _hours_ago(ts)
    return {
        "snippet": f"Latest {lab_name} for {patient_id}: {val} {unit} ({age_h:.1f} h ago).",
        "metadata": {
            "patient_id": patient_id,
            "lab_name": lab_name,
            "lab_value": val,
            "lab_unit": unit,
            "timestamp": ts,
            "age_hours": round(age_h, 2),
            "found": True,
        },
    }


async def get_last_administration(*, patient_id: str, medication: str) -> dict[str, Any]:
    doc = await collection("clinical_events").find_one(
        {
            "patient_id": patient_id,
            "event_type": "med_administration",
            "medication": medication.lower(),
        },
        sort=[("timestamp", -1)],
    )
    if not doc:
        return {
            "snippet": f"No prior {medication} administration on record for {patient_id}.",
            "metadata": {"patient_id": patient_id, "medication": medication, "found": False},
        }
    ts = _ts_to_ms(doc.get("timestamp"))
    age_h = _hours_ago(ts)
    return {
        "snippet": (
            f"Last {medication} for {patient_id}: {doc.get('dose') or 'unspecified dose'} "
            f"({age_h:.1f} h ago)."
        ),
        "metadata": {
            "patient_id": patient_id,
            "medication": medication.lower(),
            "dose": doc.get("dose"),
            "timestamp": ts,
            "age_hours": round(age_h, 2),
            "found": True,
        },
    }


async def get_monograph_section(*, medication: str, section_keyword: str) -> dict[str, Any]:
    """Keyword-filtered fetch from the drug monograph corpus. Cheap because
    it's a Mongo $match on small per-drug sub-corpora."""
    cur = collection("documents").find(
        {"medication": medication.lower(), "section": {"$regex": section_keyword, "$options": "i"}}
    ).limit(1)
    async for doc in cur:
        return {
            "snippet": (doc.get("text") or "").strip()[:300],
            "metadata": {
                "medication": medication.lower(),
                "section": doc.get("section"),
                "source_doc": doc.get("source_doc"),
                "found": True,
            },
        }
    return {
        "snippet": f"No '{section_keyword}' section found in {medication} monograph.",
        "metadata": {"medication": medication.lower(), "section_keyword": section_keyword, "found": False},
    }


# --- Dispatcher -------------------------------------------------------------

DISPATCH = {
    "get_latest_lab": get_latest_lab,
    "get_last_administration": get_last_administration,
    "get_monograph_section": get_monograph_section,
}


async def execute(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Returns {snippet, metadata, latency_ms}. Caller is responsible for
    enforcing the per-turn cap (max 2)."""
    if tool not in ALLOWED_TOOLS:
        return {
            "snippet": f"(unknown tool: {tool})",
            "metadata": {"error": "unknown_tool"},
            "latency_ms": 0,
        }
    fn = DISPATCH[tool]
    t0 = now_ms()
    try:
        out = await fn(**(args or {}))
    except TypeError as e:
        out = {"snippet": f"(bad args for {tool}: {e})", "metadata": {"error": "bad_args"}}
    except Exception as e:  # noqa: BLE001
        log.exception("active tool %s failed: %s", tool, e)
        out = {"snippet": f"(tool {tool} errored)", "metadata": {"error": str(e)}}
    out["latency_ms"] = now_ms() - t0
    return out
