"""Multiplex Mongo change streams to the dashboard over a single WebSocket.

Subscribes to all 8 dashboard collections in parallel and pushes a normalized
event:

    {"collection": "...", "operation": "insert", "document_id": "...", "doc": {...}, "ts": 1714…}

Throttled to ~10 events/sec per collection so the browser doesn't drown.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import orjson
from fastapi import WebSocket, WebSocketDisconnect

from .mongo import DASHBOARD_COLLECTIONS, watch
from .util import now_ms

log = logging.getLogger("change_streams")

THROTTLE_PER_COLL_HZ = 10


class Hub:
    def __init__(self) -> None:
        self._subscribers: set[WebSocket] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._last_emit: dict[str, float] = {}

    async def start(self) -> None:
        if self._tasks:
            return
        for coll in DASHBOARD_COLLECTIONS:
            self._tasks.append(asyncio.create_task(self._stream(coll)))
        log.info("change-stream hub started for %d collections", len(DASHBOARD_COLLECTIONS))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    async def add(self, ws: WebSocket) -> None:
        self._subscribers.add(ws)
        await ws.send_text(orjson.dumps({
            "collection": "_meta",
            "operation": "hello",
            "document_id": "",
            "doc": {"watching": list(DASHBOARD_COLLECTIONS)},
            "ts": now_ms(),
        }).decode())

    def remove(self, ws: WebSocket) -> None:
        self._subscribers.discard(ws)

    async def _stream(self, coll: str) -> None:
        period = 1.0 / THROTTLE_PER_COLL_HZ
        async for change in watch(coll):
            now = time.monotonic()
            last = self._last_emit.get(coll, 0.0)
            if now - last < period:
                continue  # drop intermediate
            self._last_emit[coll] = now
            event = {
                "collection": coll,
                "operation": change.get("operationType", "insert"),
                "document_id": str((change.get("documentKey") or {}).get("_id", "")),
                "doc": _scrub(change.get("fullDocument") or {}),
                "ts": now_ms(),
            }
            await self._broadcast(event)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        if not self._subscribers:
            return
        payload = orjson.dumps(event, default=_default).decode()
        dead: list[WebSocket] = []
        for ws in list(self._subscribers):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


def _default(obj: Any) -> Any:
    try:
        return str(obj)
    except Exception:  # noqa: BLE001
        return None


def _scrub(doc: dict[str, Any]) -> dict[str, Any]:
    """Strip giant fields (raw image bytes, embedding vectors) before sending."""
    out: dict[str, Any] = {}
    for k, v in doc.items():
        if k in ("image_b64", "text_embedding"):
            out[k] = f"<{len(v) if hasattr(v, '__len__') else '?'} bytes>"
        else:
            out[k] = v
    return out


hub = Hub()


async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await hub.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(ws)
