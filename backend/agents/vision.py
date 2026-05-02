"""Vision agent — GPT-4o (vision).

Triggered on every sampled video frame written to `video_frames`. Extracts
structured `scene_context` (objects, visible text, environment, activity), then
embeds a one-line summary for vector search.

Runs OFFLINE w.r.t. the question hot path. Latency target: <1500 ms per frame.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ..config import settings
from ..embeddings import embed
from ..mongo import collection, watch
from ..tracing import MongoTraceCallback, trace_event
from ..util import new_id, now_ms

log = logging.getLogger("vision")

SYSTEM = """You extract structured scene context from a single first-person frame.
Return STRICT JSON with these keys, nothing else:
{
  "objects": ["short noun phrases, max 6"],
  "text_visible": ["any legible labels, IDs, gauges, signs"],
  "environment": "one of: factory_floor | warehouse | office | outdoors | vehicle | unknown",
  "activity": "one short verb phrase",
  "text_summary": "one short sentence describing the scene for retrieval"
}
Be concise. No prose, no markdown, no preamble."""


def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-4o",
        temperature=0,
        max_tokens=400,
        api_key=settings.openai_api_key,
        timeout=8,
    )


async def extract_scene(image_b64: str, *, session_id: str) -> dict[str, Any]:
    """One Vision call. Returns parsed JSON dict (best-effort)."""
    cb = MongoTraceCallback(agent="vision", question_id=None, session_id=session_id)
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "Extract scene context as JSON."},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            },
        ]
    )
    resp = await _llm().ainvoke([SystemMessage(content=SYSTEM), msg], config={"callbacks": [cb]})
    raw = (resp.content or "").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "objects": [],
            "text_visible": [],
            "environment": "unknown",
            "activity": "",
            "text_summary": raw[:200],
        }
    data.setdefault("objects", [])
    data.setdefault("text_visible", [])
    data.setdefault("environment", "unknown")
    data.setdefault("activity", "")
    data.setdefault("text_summary", "")
    return data


async def process_frame(frame_doc: dict[str, Any]) -> str | None:
    """Run Vision on one `video_frames` doc, write `scene_context`. Returns id."""
    t0 = now_ms()
    session_id = frame_doc["session_id"]
    image_b64 = frame_doc.get("image_b64")
    if not image_b64:
        return None

    data = await extract_scene(image_b64, session_id=session_id)
    summary = data["text_summary"] or " ".join(data["objects"])
    vec = await embed(summary)

    doc = {
        "_id": new_id("sc"),
        "session_id": session_id,
        "timestamp": now_ms(),
        "source_frame_id": frame_doc["_id"],
        "objects": data["objects"][:8],
        "text_visible": data["text_visible"][:8],
        "environment": data["environment"],
        "activity": data["activity"],
        "text_summary": summary,
        "text_embedding": vec,
    }
    await collection("scene_context").insert_one(doc)
    await trace_event(
        agent="vision",
        stage="end",
        session_id=session_id,
        latency_ms=now_ms() - t0,
        payload={"frame_id": frame_doc["_id"], "objects": doc["objects"]},
    )
    return doc["_id"]


async def run_vision_loop() -> None:
    """Subscribe to `video_frames` change stream and run Vision on every insert."""
    log.info("vision loop watching video_frames change stream")
    async for change in watch("video_frames"):
        if change.get("operationType") != "insert":
            continue
        frame = change.get("fullDocument") or {}
        try:
            await process_frame(frame)
        except Exception as e:  # noqa: BLE001
            log.exception("vision failed: %s", e)
