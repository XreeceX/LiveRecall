"""Per-session local retrieval cache.

Vision drives prefetch: every time a `scene_context` is written, we examine the
visible text and (in the background) pre-load Mongo data that's likely to be
asked about in the next ~5–60 seconds:

  - patient_id (e.g. "P-204") → last N clinical_events
  - medication name (e.g. "metformin") → monograph chunks for that drug

When a question lands, the Retrievers consult the cache first. A hit returns
in ~5 ms; a miss falls through to a normal Mongo $vectorSearch / time-series
query (~150–300 ms). The hit/miss outcome is recorded in
`retrieval_results[i].metadata.from_cache`, which the dashboard surfaces.

This is "local retrieval" in the latency sense: the data is held in-process,
keyed by what the wearer is already looking at, with no extra network hop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("local_cache")

ENTRY_TTL_S = 90.0          # patient context can move; refresh after 90s
MAX_ENTRIES_PER_SESSION = 64
MAX_SESSIONS = 32


@dataclass
class _Entry:
    value: list[dict[str, Any]]
    expires_at: float

    def alive(self, now: float) -> bool:
        return now < self.expires_at


@dataclass
class _Session:
    entries: OrderedDict[str, _Entry] = field(default_factory=OrderedDict)
    inflight: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    last_touched: float = 0.0

    def get(self, key: str, now: float) -> list[dict[str, Any]] | None:
        ent = self.entries.get(key)
        if not ent:
            return None
        if not ent.alive(now):
            self.entries.pop(key, None)
            return None
        self.entries.move_to_end(key)
        return ent.value

    def put(self, key: str, value: list[dict[str, Any]], ttl_s: float) -> None:
        self.entries[key] = _Entry(value=value, expires_at=time.monotonic() + ttl_s)
        self.entries.move_to_end(key)
        while len(self.entries) > MAX_ENTRIES_PER_SESSION:
            self.entries.popitem(last=False)


class SessionCache:
    """Process-local TTL+LRU cache, scoped per session_id."""

    def __init__(self) -> None:
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    # --- core ops ----------------------------------------------------------

    def _touch_session(self, session_id: str) -> _Session:
        sess = self._sessions.get(session_id)
        if sess is None:
            sess = _Session()
            self._sessions[session_id] = sess
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        sess.last_touched = time.monotonic()
        self._sessions.move_to_end(session_id)
        return sess

    def get(self, session_id: str, key: str) -> list[dict[str, Any]] | None:
        sess = self._sessions.get(session_id)
        if sess is None:
            self._misses += 1
            return None
        v = sess.get(key, time.monotonic())
        if v is None:
            self._misses += 1
        else:
            self._hits += 1
            log.debug("cache hit session=%s key=%s", session_id, key)
        return v

    def put(self, session_id: str, key: str, value: list[dict[str, Any]], *, ttl_s: float = ENTRY_TTL_S) -> None:
        sess = self._touch_session(session_id)
        sess.put(key, value, ttl_s)

    # --- prefetch coordination --------------------------------------------

    async def prefetch(
        self,
        session_id: str,
        key: str,
        loader,                     # async () -> list[dict]
        *,
        ttl_s: float = ENTRY_TTL_S,
    ) -> None:
        """Schedule a background prefetch. De-dups: if there's already an
        in-flight prefetch for the same key, this is a no-op.
        """
        sess = self._touch_session(session_id)
        if key in sess.entries and sess.entries[key].alive(time.monotonic()):
            return
        if key in sess.inflight and not sess.inflight[key].done():
            return

        async def _run() -> None:
            try:
                value = await loader()
                self.put(session_id, key, value, ttl_s=ttl_s)
                log.info("prefetched session=%s key=%s n=%d", session_id, key, len(value))
            except Exception as e:  # noqa: BLE001
                log.warning("prefetch failed session=%s key=%s: %s", session_id, key, e)
            finally:
                sess.inflight.pop(key, None)

        sess.inflight[key] = asyncio.create_task(_run())

    # --- introspection -----------------------------------------------------

    def stats(self) -> dict[str, int]:
        total = self._hits + self._misses
        rate = (self._hits / total) if total else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "rate_x100": int(rate * 100),
            "sessions": len(self._sessions),
        }


cache = SessionCache()


# --- key conventions --------------------------------------------------------
# Single source of truth for cache-key shape; both prefetch and read paths
# import these helpers so they can never drift apart.

def patient_events_key(patient_id: str, *, limit: int = 5) -> str:
    return f"events:{patient_id}:n={limit}"


def medication_refs_key(medication: str, *, limit: int = 5) -> str:
    return f"refs:med:{medication.lower()}:n={limit}"
