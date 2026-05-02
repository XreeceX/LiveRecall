"""Ingest DailyMed (FDA Structured Product Labels) into a JSONL fixture.

DailyMed is the canonical FDA-hosted drug-label corpus, published by the
National Library of Medicine. Public REST API, no auth, no rate limits.

For each drug name in DRUG_LIST we:
  1. List candidate SPLs via /spls.json?drug_name=...
  2. Pick the most recent prescription label (skip OTC + outdated reprints)
  3. Download the SPL XML, parse the LOINC-coded sections we care about:
       34067-9  INDICATIONS & USAGE
       34068-7  DOSAGE & ADMINISTRATION
       34070-3  CONTRAINDICATIONS
       43685-7  WARNINGS AND PRECAUTIONS
       34073-7  DRUG INTERACTIONS
       34084-4  ADVERSE REACTIONS
       34066-1  BOXED WARNING
  4. Fetch the SPL's media attachments (/spls/{setid}/media.json), pick the
     best product photo (skip molecular-structure diagrams), downscale to a
     ~256 px JPEG and base64-encode it.
  5. Emit one JSONL line per (drug, section) chunk. EVERY chunk for a drug
     carries the SAME image_b64 so each retrieval result is independently
     displayable in the dashboard.

Output schema (References-retriever-ready, name + context + image):
  { name, category="medication", medication, source_doc, section, text,
    image_b64, image_mime, image_attribution, _provenance }

Run:
    python -m scripts.ingest_dailymed
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("ingest.dailymed")

API_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
HEADERS = {"User-Agent": "LiveRecall-hackathon/1.0 (educational)"}

# LOINC -> human-friendly section name. Only these sections are kept.
WANTED_SECTIONS = {
    "34066-1": "Boxed Warning",
    "34067-9": "Indications & Usage",
    "34068-7": "Dosage & Administration",
    "34070-3": "Contraindications",
    "43685-7": "Warnings and Precautions",
    "34073-7": "Drug Interactions",
    "34084-4": "Adverse Reactions",
}

# 10 common drugs that anchor the demo + give the Reranker realistic breadth.
# Lowercase; matched against DailyMed's drug_name index (case-insensitive).
DRUG_LIST = [
    "metformin",
    "insulin glargine",
    "lisinopril",
    "amlodipine",
    "warfarin",
    "atorvastatin",
    "albuterol",
    "omeprazole",
    "levothyroxine",
    "amoxicillin",
]

OUT_PATH = Path("data/fixtures/dailymed_sample.jsonl")
CACHE_DIR = Path("data/cache/dailymed")
MAX_LABELS_PER_DRUG = 1
MAX_CHUNK_CHARS = 1200
HTTP_TIMEOUT = 20.0
IMAGE_MAX_PX = 256
IMAGE_JPEG_QUALITY = 75
# We deprioritise molecular-structure renders — Vision can't usefully match a
# pill bottle to a chemical structure, so we want the actual product photo.
SKIP_IMAGE_NAME_PATTERNS = (
    re.compile(r"structure", re.IGNORECASE),
    re.compile(r"chemical", re.IGNORECASE),
    re.compile(r"formula", re.IGNORECASE),
)


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read()


def _list_spls_for_drug(drug: str) -> list[dict]:
    """Returns SPL metadata records for `drug`, preferring single-drug labels
    over combination products (so "metformin" returns the pure metformin SPL,
    not "glipizide AND metformin").
    """
    q = urllib.parse.urlencode({"drug_name": drug, "pagesize": 50, "page": 1})
    url = f"{API_BASE}/spls.json?{q}"
    raw = _http_get(url)
    data = json.loads(raw.decode("utf-8"))
    spls = data.get("data") or []
    form_rx = re.compile(r"\bTABLET|CAPSULE|SOLUTION|INJECTION|SUSPENSION|SOLUTION\b", re.IGNORECASE)

    # Score each SPL: lower is better.
    drug_u = drug.upper()
    def score(s: dict) -> tuple[int, str]:
        title = (s.get("title") or "").upper()
        words = re.split(r"[^A-Z0-9]+", title)
        starts_with_drug = title.startswith(drug_u)
        is_combination = " AND " in f" {title} "  # whole-word "AND"
        has_form = bool(form_rx.search(title))
        # Lower priority number = better. Then newer published_date wins.
        if not has_form or drug_u not in words:
            priority = 9
        elif starts_with_drug and not is_combination:
            priority = 0   # gold: "METFORMIN HYDROCHLORIDE TABLET ..."
        elif not is_combination:
            priority = 1   # decent: "DESCRIPTION METFORMIN TABLET ..."
        else:
            priority = 5   # combination product
        # Sort key: (priority asc, published_date desc as ISO-ish lex)
        return (priority, "9" * 20 + (s.get("published_date") or ""))

    spls.sort(key=score)
    return [s for s in spls if drug_u in (s.get("title") or "").upper()][:MAX_LABELS_PER_DRUG]


def _fetch_spl_xml(setid: str) -> bytes:
    cache = CACHE_DIR / f"{setid}.xml"
    if cache.exists():
        return cache.read_bytes()
    url = f"{API_BASE}/spls/{setid}.xml"
    raw = _http_get(url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return raw


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_xml(s: str) -> str:
    s = _TAG_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


_SECTION_RE = re.compile(
    r'<section[^>]*>.*?<code[^>]*code="([0-9\-]+)"[^>]*/>(.*?)</section>',
    re.DOTALL | re.IGNORECASE,
)


def _parse_sections(xml_bytes: bytes) -> dict[str, str]:
    """Returns {loinc_code: section_text} for sections in WANTED_SECTIONS."""
    text = xml_bytes.decode("utf-8", errors="replace")
    out: dict[str, str] = {}
    for m in _SECTION_RE.finditer(text):
        code = m.group(1)
        if code not in WANTED_SECTIONS:
            continue
        body = _strip_xml(m.group(2))
        if not body:
            continue
        # Keep first chunk per section. SPLs sometimes nest; the outer match
        # captures the headline section text.
        out.setdefault(code, body)
    return out


# --- Image fetching --------------------------------------------------------

def _fetch_media_list(setid: str) -> list[dict]:
    cache = CACHE_DIR / f"{setid}_media.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8")).get("data", {}).get("media") or []
    url = f"{API_BASE}/spls/{setid}/media.json"
    raw = _http_get(url)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    data = json.loads(raw.decode("utf-8"))
    return (data.get("data") or {}).get("media") or []


def _pick_product_image(media: list[dict]) -> dict | None:
    """Pick the best photographic product image. Skips structure diagrams."""
    candidates = []
    for m in media:
        mime = (m.get("mime_type") or "").lower()
        name = (m.get("name") or "")
        if not mime.startswith("image/"):
            continue
        if any(p.search(name) for p in SKIP_IMAGE_NAME_PATTERNS):
            continue
        candidates.append(m)
    return candidates[0] if candidates else None


def _download_and_downsize(url: str, *, cache_path: Path) -> bytes | None:
    """Returns JPEG bytes or None on failure."""
    if cache_path.exists() and cache_path.stat().st_size > 100:
        return cache_path.read_bytes()
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed; skipping image downscale")
        return None
    try:
        raw = _http_get(url)
    except Exception as e:  # noqa: BLE001
        log.warning("image fetch failed %s: %s", url, e)
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=IMAGE_JPEG_QUALITY, optimize=True)
        out = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        log.warning("image decode/resize failed %s: %s", url, e)
        return None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(out)
    return out


def _fetch_image_for_setid(setid: str) -> dict | None:
    """Returns {image_b64, image_mime, image_attribution, image_source_url} or None."""
    try:
        media = _fetch_media_list(setid)
    except Exception as e:  # noqa: BLE001
        log.warning("media list failed for %s: %s", setid, e)
        return None
    pick = _pick_product_image(media)
    if not pick:
        return None
    cache_path = CACHE_DIR / "images" / f"{setid}.jpg"
    jpeg = _download_and_downsize(pick["url"], cache_path=cache_path)
    if not jpeg:
        return None
    return {
        "image_b64": base64.b64encode(jpeg).decode("ascii"),
        "image_mime": "image/jpeg",
        "image_attribution": f"DailyMed ({pick.get('name', 'product image')})",
        "image_source_url": pick.get("url"),
    }


def _chunk(text: str, n: int = MAX_CHUNK_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= n:
        return [text]
    chunks: list[str] = []
    cur = ""
    for sentence in re.split(r"(?<=[\.\!\?])\s+", text):
        if len(cur) + len(sentence) + 1 <= n:
            cur = (cur + " " + sentence).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = sentence
    if cur:
        chunks.append(cur)
    return chunks


def ingest(drugs: list[str], out_path: Path) -> tuple[int, int]:
    """Returns (n_chunks_written, n_with_image)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_chunks = 0
    n_with_image = 0
    with out_path.open("w", encoding="utf-8") as f:
        for drug in drugs:
            log.info("fetching SPLs for %s", drug)
            try:
                spls = _list_spls_for_drug(drug)
            except Exception as e:  # noqa: BLE001
                log.warning("list_spls failed for %s: %s", drug, e)
                continue
            if not spls:
                log.warning("no SPL found for %s", drug)
                continue
            for spl in spls:
                setid = spl["setid"]
                title = spl.get("title", "")
                published = spl.get("published_date", "")
                log.info("  → %s (%s)", title, published)
                try:
                    xml = _fetch_spl_xml(setid)
                except Exception as e:  # noqa: BLE001
                    log.warning("  fetch_xml failed: %s", e)
                    continue
                image_data = _fetch_image_for_setid(setid) or {}
                if image_data:
                    log.info("    + product image (%d KB b64)",
                             len(image_data.get("image_b64", "")) // 1024)
                sections = _parse_sections(xml)
                for code, body in sections.items():
                    section_name = WANTED_SECTIONS[code]
                    for i, chunk in enumerate(_chunk(body)):
                        rec = {
                            "name": drug.lower(),
                            "category": "medication",
                            "medication": drug.lower(),
                            "source_doc": f"DailyMed: {title} ({published})",
                            "source_setid": setid,
                            "section": section_name,
                            "loinc": code,
                            "chunk_idx": i,
                            "text": chunk,
                            "_provenance": "dailymed.nlm.nih.gov",
                            **image_data,
                        }
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        n_chunks += 1
                        if image_data:
                            n_with_image += 1
                time.sleep(0.2)  # be polite to NLM
    return n_chunks, n_with_image


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--drugs", nargs="+", default=DRUG_LIST,
                   help="lowercase drug names; default is DRUG_LIST")
    p.add_argument("--out", default=str(OUT_PATH), help="output JSONL path")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    out = Path(args.out)
    n, n_img = ingest(args.drugs, out)
    log.info("wrote %d chunks (%d with images) → %s", n, n_img, out)


if __name__ == "__main__":
    main()
