"""FastAPI app — LiveRecall control plane.

  - GET  /healthz                       → quick liveness probe
  - POST /token                         → LiveKit access token for phone or worker
  - POST /snap                          → single-image retrieval (Vision sync, optional question)
  - GET  /scene-context/recent          → dashboard convenience read
  - GET  /trace/:question_id            → full reasoning chain
  - GET  /answers/:question_id          → final answer text
  - WS   /stream                        → change-stream fan-out for dashboard
  - POST /ask                           → text-only pipeline kick (debugging)

Also wires the agent loops (vision / router / retrievers / reranker / answerer)
into the FastAPI lifespan so a single `uvicorn backend.main:app` boots
everything except the LiveKit worker process (run that separately).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from livekit.api import AccessToken, VideoGrants
from pydantic import BaseModel

from shared.types import DEFAULT_CAPTURE_MODE, CaptureMode

from .agents.answerer import (
    answer_text_only,
    run_answerer_loop,
)
from .agents.reranker import run_reranker_loop
from .agents.retrievers import run_retrievers_loop
from .agents.router import run_router_loop
from .agents.vision import extract_scene, run_vision_loop
from .change_streams import hub, websocket_endpoint
from .config import settings
from .embeddings import embed
from .mongo import collection, init_collections
from .util import new_id, now_ms

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_collections()
    await hub.start()
    tasks = [
        asyncio.create_task(run_vision_loop(), name="vision"),
        asyncio.create_task(run_router_loop(), name="router"),
        asyncio.create_task(run_retrievers_loop(), name="retrievers"),
        asyncio.create_task(run_reranker_loop(), name="reranker"),
        asyncio.create_task(_text_only_answerer_loop(), name="answerer-text"),
    ]
    log.info("agent loops started: %s", [t.get_name() for t in tasks])
    try:
        yield
    finally:
        await hub.stop()
        for t in tasks:
            t.cancel()


app = FastAPI(title="LiveRecall", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Lightweight in-process answerer for text-only path ---------------------
# Real audio answers happen in the LiveKit worker process via answer_to_room.
async def _text_only_answerer_loop() -> None:
    await run_answerer_loop(audio_source_provider=None)


# --- Schemas -----------------------------------------------------------------

class TokenReq(BaseModel):
    identity: str
    room: str
    can_publish: bool = True
    can_subscribe: bool = True
    # Which capture device this client represents. Glasses is the headline POV
    # path (Ray-Ban first-person), phone is the universal fallback for any
    # clinic-issued device. We persist this on the session so downstream
    # scene_context inserts know which prompt POV hint to use. See
    # DECISIONS.md (g).
    capture_mode: CaptureMode | None = None


class TokenResp(BaseModel):
    token: str
    url: str
    room: str
    capture_mode: CaptureMode


class AskReq(BaseModel):
    text: str
    session_id: str = "demo"


class AskResp(BaseModel):
    question_id: str


class SnapReq(BaseModel):
    """Single-image retrieval. Send a frame and (optionally) a question.

    `image_b64` is the JPEG image as a plain base64 string (no data: prefix).
    If `question` is empty, only `scene_context` is written; the next ASR-detected
    question (or a follow-up `/ask`) will then be grounded against this fresh frame.
    """

    image_b64: str
    question: str | None = None
    session_id: str = "demo"
    # Optional override; falls back to the session's persisted mode and finally
    # to the safer "phone" default when neither is set.
    capture_mode: CaptureMode | None = None


class SnapResp(BaseModel):
    scene_context_id: str
    question_id: str | None
    objects: list[str]
    text_visible: list[str]
    text_summary: str
    capture_mode: CaptureMode


# --- Endpoints ---------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "ts": now_ms()}


@app.post("/token", response_model=TokenResp)
async def token(req: TokenReq) -> TokenResp:
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise HTTPException(500, "LiveKit credentials not configured")
    grants = VideoGrants(
        room=req.room,
        room_join=True,
        can_publish=req.can_publish,
        can_subscribe=req.can_subscribe,
        can_publish_data=True,
    )
    at = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(req.identity)
        .with_name(req.identity)
        .with_grants(grants)
        .with_ttl(timedelta(hours=2))
    )
    # Persist the requested capture_mode on the session document so the Vision
    # agent (running off a change stream) can stamp it onto every scene_context
    # without an extra param. Phone is the safe default when the client doesn't
    # advertise a mode; Ray-Ban Live AI clients explicitly ask for "glasses".
    capture_mode: CaptureMode = req.capture_mode or DEFAULT_CAPTURE_MODE
    session_id = req.room.removeprefix("liverecall-") or req.room
    await collection("sessions").update_one(
        {"_id": session_id},
        {
            "$set": {"room": req.room, "capture_mode": capture_mode},
            "$setOnInsert": {"_id": session_id, "started_at": now_ms(), "ended_at": None},
        },
        upsert=True,
    )
    return TokenResp(
        token=at.to_jwt(),
        url=settings.livekit_url,
        room=req.room,
        capture_mode=capture_mode,
    )


@app.post("/snap", response_model=SnapResp)
async def snap(req: SnapReq) -> SnapResp:
    """Single-image retrieval — runs Vision *synchronously* on the supplied
    frame, writes `scene_context` (so it's the most recent for the Router to
    pick up), and conditionally inserts a `questions` doc.

    This is the lower-friction interaction: phone "Snap & ask" or dashboard
    image upload. Streaming-path latency budget doesn't apply — Vision is in
    the critical path here, so end-to-end is ~3–3.5s instead of ~2s.
    """
    if not req.image_b64:
        raise HTTPException(400, "image_b64 is required")
    image = req.image_b64
    if image.startswith("data:"):
        image = image.split(",", 1)[1]

    session_id = req.session_id or "demo"
    t0 = now_ms()

    # Resolve capture_mode in this priority order: explicit body override →
    # whatever was persisted on the session at /token time → safe phone default.
    capture_mode: CaptureMode = req.capture_mode or DEFAULT_CAPTURE_MODE
    if not req.capture_mode:
        sess = await collection("sessions").find_one(
            {"_id": session_id}, {"capture_mode": 1}
        )
        if sess and sess.get("capture_mode") in ("glasses", "phone"):
            capture_mode = sess["capture_mode"]
    else:
        # Snap path may also be the first time we hear about this session
        # (e.g. dashboard upload tester) — keep the session doc in sync.
        await collection("sessions").update_one(
            {"_id": session_id},
            {
                "$set": {"capture_mode": capture_mode},
                "$setOnInsert": {"_id": session_id, "started_at": now_ms(), "ended_at": None},
            },
            upsert=True,
        )

    frame_id = new_id("vf")
    await collection("video_frames").insert_one({
        "_id": frame_id,
        "session_id": session_id,
        "timestamp": now_ms(),
        "image_b64": image,
        "width": 0,
        "height": 0,
        "source": "snap",
    })

    data = await extract_scene(
        image, session_id=session_id, capture_mode=capture_mode
    )
    summary = data["text_summary"] or " ".join(data["objects"])
    vec = await embed(summary)

    sc_id = new_id("sc")
    sc_doc = {
        "_id": sc_id,
        "session_id": session_id,
        "timestamp": now_ms(),
        "source_frame_id": frame_id,
        "objects": data["objects"][:8],
        "text_visible": data["text_visible"][:8],
        "environment": data["environment"],
        "activity": data["activity"],
        "text_summary": summary,
        "text_embedding": vec,
        "capture_mode": capture_mode,
    }
    await collection("scene_context").insert_one(sc_doc)

    qid: str | None = None
    if req.question and req.question.strip():
        qid = new_id("q")
        await collection("questions").insert_one({
            "_id": qid,
            "session_id": session_id,
            "transcript_id": "",
            "text": req.question.strip(),
            "asked_at": now_ms(),
        })

    log.info(
        "/snap completed in %dms (qid=%s, capture_mode=%s)",
        now_ms() - t0,
        qid,
        capture_mode,
    )
    return SnapResp(
        scene_context_id=sc_id,
        question_id=qid,
        objects=sc_doc["objects"],
        text_visible=sc_doc["text_visible"],
        text_summary=summary,
        capture_mode=capture_mode,
    )


@app.get("/scene-context/recent")
async def scene_recent(seconds: int = 30, session_id: str | None = None) -> dict[str, Any]:
    cutoff = now_ms() - seconds * 1000
    q: dict[str, Any] = {"timestamp": {"$gte": cutoff}}
    if session_id:
        q["session_id"] = session_id
    cur = collection("scene_context").find(q, {"text_embedding": 0}).sort("timestamp", -1).limit(20)
    return {"items": [d async for d in cur]}


@app.get("/trace/{question_id}")
async def trace(question_id: str) -> dict[str, Any]:
    plan = await collection("retrieval_plans").find_one({"question_id": question_id})
    results = [d async for d in collection("retrieval_results").find({"question_id": question_id})]
    final = await collection("final_context").find_one({"question_id": question_id})
    answer = await collection("answers").find_one({"question_id": question_id})
    traces = [
        d
        async for d in collection("agent_traces")
        .find({"question_id": question_id})
        .sort("timestamp", 1)
    ]
    return {
        "question_id": question_id,
        "plan": plan,
        "results": results,
        "final_context": final,
        "answer": answer,
        "traces": traces,
    }


@app.get("/answers/{question_id}")
async def answer(question_id: str) -> dict[str, Any]:
    a = await collection("answers").find_one({"question_id": question_id})
    if not a:
        raise HTTPException(404, "answer not yet ready")
    return a


@app.post("/ask", response_model=AskResp)
async def ask(req: AskReq) -> AskResp:
    """Text-only pipeline kick. Drops a question into Mongo; agents fan out."""
    qid = new_id("q")
    qdoc = {
        "_id": qid,
        "session_id": req.session_id,
        "transcript_id": "",
        "text": req.text,
        "asked_at": now_ms(),
    }
    await collection("questions").insert_one(qdoc)
    return AskResp(question_id=qid)


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    await websocket_endpoint(ws)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=False,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
