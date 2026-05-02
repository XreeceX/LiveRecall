"""MongoDB Atlas substrate for LiveRecall.

Owns:
  - 8 collections (document, time-series, GridFS)
  - vector indexes (Atlas Vector Search) on scene_context, transcripts, documents
  - TTL index on clips.files
  - change-stream subscribe helper used as the agent bus

This is rubric-critical. Every Mongo feature listed in shared/types.MONGO_FEATURES
is exercised here or in agents/retrievers.py.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from motor.motor_asyncio import (
    AsyncIOMotorChangeStream,
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
    AsyncIOMotorGridFSBucket,
)
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import CollectionInvalid, OperationFailure

from shared.types import (
    COL_AGENT_TRACES,
    COL_ANSWERS,
    COL_DOCUMENTS,
    COL_FINAL_CONTEXT,
    COL_MACHINES,
    COL_MAINTENANCE_EVENTS,
    COL_QUESTIONS,
    COL_RETRIEVAL_PLANS,
    COL_RETRIEVAL_RESULTS,
    COL_SCENE_CONTEXT,
    COL_SESSIONS,
    COL_TRANSCRIPTS,
    COL_VIDEO_FRAMES,
    EMBED_DIM,
    VEC_IDX_DOCS,
    VEC_IDX_SCENE,
    VEC_IDX_TRANSCRIPTS,
)

from .config import settings

log = logging.getLogger("mongo")


# Collections we want to fan out on the change-stream WebSocket.
DASHBOARD_COLLECTIONS = [
    COL_VIDEO_FRAMES,
    COL_SCENE_CONTEXT,
    COL_TRANSCRIPTS,
    COL_QUESTIONS,
    COL_RETRIEVAL_PLANS,
    COL_RETRIEVAL_RESULTS,
    COL_FINAL_CONTEXT,
    COL_ANSWERS,
    COL_AGENT_TRACES,
]


_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_uri, tz_aware=False)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongodb_db]


def collection(name: str) -> AsyncIOMotorCollection:
    return get_db()[name]


def gridfs() -> AsyncIOMotorGridFSBucket:
    return AsyncIOMotorGridFSBucket(get_db(), bucket_name="clips")


async def init_collections() -> None:
    """Create collections, indexes, and vector indexes idempotently.

    Safe to call multiple times. Runs at FastAPI startup and from `python -m
    backend.mongo` for manual bootstrap.
    """
    db = get_db()

    # --- Time Series collection (maintenance_events) ---
    try:
        await db.create_collection(
            COL_MAINTENANCE_EVENTS,
            timeseries={
                "timeField": "timestamp",
                "metaField": "machine_id",
                "granularity": "hours",
            },
        )
        log.info("created time-series collection: %s", COL_MAINTENANCE_EVENTS)
    except CollectionInvalid:
        pass
    except OperationFailure as e:
        if "already exists" not in str(e):
            raise

    # --- Standard collections ---
    plain = [
        COL_SESSIONS,
        COL_VIDEO_FRAMES,
        COL_SCENE_CONTEXT,
        COL_TRANSCRIPTS,
        COL_DOCUMENTS,
        COL_RETRIEVAL_PLANS,
        COL_RETRIEVAL_RESULTS,
        COL_FINAL_CONTEXT,
        COL_ANSWERS,
        COL_QUESTIONS,
        COL_AGENT_TRACES,
        COL_MACHINES,
    ]
    existing = set(await db.list_collection_names())
    for name in plain:
        if name not in existing:
            await db.create_collection(name)
            log.info("created collection: %s", name)

    # --- Indexes ---
    await collection(COL_VIDEO_FRAMES).create_index(
        [("session_id", ASCENDING), ("timestamp", DESCENDING)]
    )
    await collection(COL_SCENE_CONTEXT).create_index(
        [("session_id", ASCENDING), ("timestamp", DESCENDING)]
    )
    await collection(COL_TRANSCRIPTS).create_index(
        [("session_id", ASCENDING), ("timestamp", DESCENDING)]
    )
    await collection(COL_TRANSCRIPTS).create_index([("is_question", ASCENDING)])
    await collection(COL_QUESTIONS).create_index([("asked_at", DESCENDING)])
    await collection(COL_RETRIEVAL_PLANS).create_index([("question_id", ASCENDING)])
    await collection(COL_RETRIEVAL_RESULTS).create_index([("plan_id", ASCENDING)])
    await collection(COL_FINAL_CONTEXT).create_index([("question_id", ASCENDING)])
    await collection(COL_ANSWERS).create_index([("question_id", ASCENDING)])
    await collection(COL_AGENT_TRACES).create_index(
        [("question_id", ASCENDING), ("timestamp", ASCENDING)]
    )
    await collection(COL_MACHINES).create_index([("machine_id", ASCENDING)], unique=True)

    # --- TTL on GridFS clip files (24h) ---
    try:
        await db["clips.files"].create_index(
            [("uploadDate", ASCENDING)], expireAfterSeconds=24 * 3600
        )
    except OperationFailure as e:
        log.warning("ttl on clips.files: %s", e)

    # --- Atlas Vector Search indexes (best-effort; M0/local will skip) ---
    await _ensure_vector_index(COL_SCENE_CONTEXT, VEC_IDX_SCENE, "text_embedding")
    await _ensure_vector_index(COL_DOCUMENTS, VEC_IDX_DOCS, "text_embedding")
    await _ensure_vector_index(COL_TRANSCRIPTS, VEC_IDX_TRANSCRIPTS, "text_embedding")


async def _ensure_vector_index(coll: str, name: str, path: str) -> None:
    """Create an Atlas Vector Search index. No-op outside Atlas."""
    db = get_db()
    try:
        existing = [i async for i in db[coll].list_search_indexes()]
        if any(i.get("name") == name for i in existing):
            return
        await db[coll].create_search_index(
            {
                "name": name,
                "type": "vectorSearch",
                "definition": {
                    "fields": [
                        {
                            "type": "vector",
                            "path": path,
                            "numDimensions": EMBED_DIM,
                            "similarity": "cosine",
                        }
                    ]
                },
            }
        )
        log.info("created vector index %s on %s", name, coll)
    except (OperationFailure, AttributeError) as e:
        log.warning("vector index %s on %s skipped: %s", name, coll, e)


# --- Change stream helpers (the agent bus) -----------------------------------


async def watch(
    coll: str,
    *,
    pipeline: list[dict[str, Any]] | None = None,
    full_document: str = "updateLookup",
) -> AsyncIterator[dict[str, Any]]:
    """Yield change events for a single collection. Self-heals on resume errors."""
    while True:
        try:
            stream: AsyncIOMotorChangeStream = collection(coll).watch(
                pipeline or [], full_document=full_document
            )
            async with stream as cs:
                async for change in cs:
                    yield change
        except OperationFailure as e:
            log.warning("change stream on %s failed, retrying: %s", coll, e)
            await asyncio.sleep(1)
        except Exception as e:  # noqa: BLE001
            log.exception("change stream on %s crashed: %s", coll, e)
            await asyncio.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_collections())
