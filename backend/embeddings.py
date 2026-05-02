"""OpenAI text-embedding-3-small wrapper. Batched + cached."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from openai import AsyncOpenAI

from .config import settings

log = logging.getLogger("embeddings")

MODEL = "text-embedding-3-small"

_client: AsyncOpenAI | None = None


def _client_singleton() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def embed(text: str) -> list[float]:
    if not text.strip():
        return [0.0] * 1536
    cached = _embed_cache_get(text)
    if cached is not None:
        return cached
    resp = await _client_singleton().embeddings.create(model=MODEL, input=text)
    vec = resp.data[0].embedding
    _embed_cache_put(text, vec)
    return vec


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed many strings in one request (cheaper + faster)."""
    if not texts:
        return []
    resp = await _client_singleton().embeddings.create(model=MODEL, input=texts)
    return [d.embedding for d in resp.data]


# Tiny in-process cache for repeat queries (Vision summaries vs Router questions
# often overlap, e.g. "P-204" or "metformin"). Bounded by lru_cache.
@lru_cache(maxsize=1024)
def _embed_cache_key(text: str) -> str:
    return text


_embed_cache: dict[str, list[float]] = {}


def _embed_cache_get(text: str) -> list[float] | None:
    return _embed_cache.get(text)


def _embed_cache_put(text: str, vec: list[float]) -> None:
    if len(_embed_cache) > 2048:
        _embed_cache.clear()
    _embed_cache[text] = vec
