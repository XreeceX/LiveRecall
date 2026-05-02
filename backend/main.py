"""FastAPI app — LiveRecall control plane.

  - GET  /healthz                       → quick liveness probe
  - POST /token                         → LiveKit access token for phone or worker
  - GET  /scene-context/recent          → dashboard convenience read
  - GET  /trace/:question_id            → full reasoning chain
  - GET  /answers/:question_id          → final answer text
  - WS   /stream                        → change-stream fan-out for dashboard
  - POST /ask                           → text-only pipeline kick (debugging)

Agents run in worker.py (separate process). This server only needs MongoDB.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from livekit.api import AccessToken, VideoGrants
from pydantic import BaseModel

from .change_streams import hub, websocket_endpoint
from .config import settings
from .mongo import collection, init_collections
from .util import new_id, now_ms

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_collections()
    await hub.start()
    try:
        yield
    finally:
        await hub.stop()


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


class TokenResp(BaseModel):
    token: str
    url: str
    room: str


class AskReq(BaseModel):
    text: str
    session_id: str = "demo"


class AskResp(BaseModel):
    question_id: str


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
    return TokenResp(token=at.to_jwt(), url=settings.livekit_url, room=req.room)


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
