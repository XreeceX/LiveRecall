"""Ingest MTSamples (medical transcription samples) into a JSONL fixture.

MTSamples is a corpus of ~5,000 sample clinical-dictation notes scraped from
mtsamples.com by Tara Boyle and re-published under CC0 (public domain) on
Hugging Face: https://huggingface.co/datasets/harishnair04/mtsamples.

We download the auto-converted Parquet file directly (one HTTP call, ~7 MB)
and parse it with pyarrow. Filter to specialties + keywords relevant to the
bedside-medication-safety demo, then keep MAX_NOTES.

Output schema matches `transcripts`:
  { kind="transcript", session_id="historical", text, is_final, is_question,
    medical_specialty, sample_name, description, _provenance, _seq }

Run (one-time, ~10 s):
    python -m scripts.ingest_mtsamples
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq

log = logging.getLogger("ingest.mtsamples")

PARQUET_URL = (
    "https://huggingface.co/datasets/harishnair04/mtsamples/"
    "resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet"
)
HEADERS = {"User-Agent": "LiveRecall-hackathon/1.0 (educational)"}

OUT_PATH = Path("data/fixtures/mtsamples_sample.jsonl")
CACHE_DIR = Path("data/cache/mtsamples")
CACHE_PARQUET = CACHE_DIR / "mtsamples.parquet"

MAX_NOTES = 35
MAX_TEXT_CHARS = 1500

# Specialties relevant to the bedside-medication-safety demo. MTSamples
# medical_specialty values come straight from the mtsamples.com taxonomy and
# can include leading whitespace.
WANTED_SPECIALTIES = {
    "endocrinology",
    "nephrology",
    "general medicine",
    "internal medicine",
    "discharge summary",
    "hospital discharge summary",
    "cardiovascular / pulmonary",
    "soap / chart / progress notes",
    "consult - history and phy.",
}

# Anchor keywords. Bias toward notes that mention any of these so vector
# search lands on clinically relevant prose for the demo question.
ANCHOR_KEYWORDS = (
    "metformin", "diabetes", "creatinine", "egfr", "renal",
    "kidney", "insulin", "lisinopril", "hypertension", "warfarin",
    "discharge", "medication reconcil", "handoff",
)

_WS = re.compile(r"\s+")


def _download_parquet() -> None:
    if CACHE_PARQUET.exists() and CACHE_PARQUET.stat().st_size > 100_000:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("downloading MTSamples parquet → %s", CACHE_PARQUET)
    req = urllib.request.Request(PARQUET_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r, CACHE_PARQUET.open("wb") as f:
        shutil.copyfileobj(r, f)
    log.info("downloaded %d bytes", CACHE_PARQUET.stat().st_size)


def _load_rows() -> list[dict]:
    table = pq.read_table(CACHE_PARQUET)
    return table.to_pylist()


def _norm_specialty(s: str) -> str:
    return (s or "").strip().lower()


def _normalise_text(s: str) -> str:
    return _WS.sub(" ", (s or "").strip())


def _is_relevant(row: dict) -> bool:
    if _norm_specialty(row.get("medical_specialty")) not in WANTED_SPECIALTIES:
        return False
    text = (row.get("transcription") or "").lower()
    return any(k in text for k in ANCHOR_KEYWORDS)


def _row_to_transcript(row: dict, idx: int) -> dict | None:
    text = _normalise_text(row.get("transcription") or "")
    if not text or len(text) < 200:
        return None
    if len(text) > MAX_TEXT_CHARS:
        text = text[: MAX_TEXT_CHARS - 1] + "…"
    return {
        "kind": "transcript",
        "session_id": "historical",
        "text": text,
        "is_final": True,
        "is_question": False,
        "medical_specialty": (row.get("medical_specialty") or "").strip() or "Unknown",
        "sample_name": (row.get("sample_name") or "").strip(),
        "description": (row.get("description") or "").strip(),
        "_provenance": "MTSamples (CC0) via huggingface harishnair04/mtsamples",
        "_seq": idx,
    }


def ingest(out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _download_parquet()
    rows = _load_rows()
    log.info("total rows: %d", len(rows))
    relevant = [r for r in rows if _is_relevant(r)]
    log.info("relevant rows after filtering: %d", len(relevant))
    written = 0
    seen_specialties: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(relevant):
            spec = (row.get("medical_specialty") or "").strip()
            # Round-robin per specialty so we don't dump 30 cardiology notes
            # and 0 nephrology notes.
            if seen_specialties.get(spec, 0) >= 6:
                continue
            doc = _row_to_transcript(row, i)
            if not doc:
                continue
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            seen_specialties[spec] = seen_specialties.get(spec, 0) + 1
            written += 1
            if written >= MAX_NOTES:
                break
    log.info("breakdown by specialty: %s", seen_specialties)
    return written


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    n = ingest(Path(args.out))
    log.info("wrote %d notes → %s", n, args.out)


if __name__ == "__main__":
    main()
