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
    """Trigger on utterances starting with 'Recall'."""
    if not text:
        return False
    t = text.strip().lower()
    return t.startswith("recall")
