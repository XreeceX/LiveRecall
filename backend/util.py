"""Tiny utilities used across the backend."""

from __future__ import annotations

import re
import time
import uuid

# Match Wh-questions anywhere in the utterance so wake-style prefixes
# ("hey meta, what am I looking at") still route to the question pipeline.
_WH_ANYWHERE = re.compile(r"\b(what|when|where|why|how|which|who)\b")


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str = "") -> str:
    base = uuid.uuid4().hex[:16]
    return f"{prefix}_{base}" if prefix else base


def is_question(text: str) -> bool:
    """Cheap question detector for the STT pipeline."""
    if not text:
        return False
    t = text.strip().lower()
    if t.endswith("?"):
        return True
    starters = (
        "what",
        "when",
        "where",
        "why",
        "how",
        "which",
        "who",
        "is ",
        "are ",
        "can ",
        "could ",
        "should ",
        "do ",
        "does ",
        "did ",
    )
    if t.startswith(starters):
        return True
    return bool(_WH_ANYWHERE.search(t))
