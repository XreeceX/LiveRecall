"""Tiny utilities used across the backend."""

from __future__ import annotations

import time
import uuid


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
    starters = ("what", "when", "where", "why", "how", "which", "who", "is ", "are ", "can ", "could ", "should ", "do ", "does ", "did ")
    return t.startswith(starters)
