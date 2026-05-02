"""Ingest Wikimedia / Wikipedia medical-equipment catalog into a JSONL fixture.

For each apparatus name in EQUIPMENT_LIST we hit Wikipedia's REST summary
endpoint:

  https://en.wikipedia.org/api/rest_v1/page/summary/{title}

Returns the article extract (intro paragraph) and a thumbnail/originalimage
URL. We download the original image, downscale to ~256 px JPEG via Pillow,
base64-encode it, and emit one JSONL record per item:

  { name, category="equipment", text, source_doc, image_b64, image_mime,
    image_attribution, image_source_url, _provenance }

Wikipedia text is CC-BY-SA 4.0; Commons images vary (often CC-BY-SA 4.0 or
PD). We carry attribution in the record so the dashboard can show it.

Run:
    python -m scripts.ingest_wikimedia_equipment
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("ingest.wikimedia")

WP_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary"
HEADERS = {"User-Agent": "LiveRecall-hackathon/1.0 (educational; contact: hackathon-team)"}

OUT_PATH = Path("data/fixtures/wikimedia_equipment_sample.jsonl")
CACHE_DIR = Path("data/cache/wikimedia")
IMAGE_MAX_PX = 256
IMAGE_JPEG_QUALITY = 75
HTTP_TIMEOUT = 30.0

# Curated bedside / clinical apparatus the Vision agent is most likely to
# encounter. Each entry is (page_title, friendly_name). We use the friendly
# name as `name` because Wikipedia titles can be over-specific
# (e.g. "Sphygmomanometer" → "blood pressure cuff").
EQUIPMENT_LIST: list[tuple[str, str]] = [
    ("Infusion_pump", "infusion pump"),
    ("Patient_monitor", "vital signs monitor"),
    ("Pulse_oximetry", "pulse oximeter"),
    ("Sphygmomanometer", "blood pressure cuff"),
    ("Stethoscope", "stethoscope"),
    ("Glucose_meter", "glucose meter"),
    ("Defibrillation", "defibrillator"),
    ("Medical_ventilator", "mechanical ventilator"),
    ("IV_pole", "iv pole"),
    ("Syringe", "syringe"),
    ("Insulin_pen", "insulin pen"),
    ("Insulin_pump", "insulin pump"),
    ("Hospital_bed", "hospital bed"),
    ("Wheelchair", "wheelchair"),
    ("Crash_cart", "crash cart"),
    ("Otoscope", "otoscope"),
    ("Medical_thermometer", "thermometer"),
    ("Urinary_catheterization", "urinary catheter"),
    ("Tracheal_tube", "endotracheal tube"),
    ("Bag_valve_mask", "bag valve mask"),
    ("Electrocardiography", "ecg machine"),
    ("Medical_ultrasound", "ultrasound probe"),
    ("Suction_(medicine)", "suction machine"),
    ("Nasal_cannula", "nasal cannula"),
    ("Wristband", "patient wristband"),
]


def _http_get(url: str, *, timeout: float = HTTP_TIMEOUT, retries: int = 3) -> bytes:
    """Fetch with retry-on-429 backoff. Wikipedia's rate limit is generous
    (~200 req/s) but bursts get throttled briefly; back off and try again.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code == 429 and attempt < retries:
                wait = 6 * (attempt + 1)
                log.info("  429 rate-limited; sleeping %ds before retry", wait)
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < retries:
                time.sleep(2)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")


def _fetch_summary(title: str) -> dict | None:
    cache = CACHE_DIR / "summary" / f"{title}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    url = f"{WP_BASE}/{urllib.parse.quote(title, safe='')}"
    try:
        raw = _http_get(url)
    except Exception as e:  # noqa: BLE001
        log.warning("summary fetch failed %s: %s", title, e)
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def _download_and_downsize(url: str, *, cache_path: Path) -> bytes | None:
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


def _summary_to_record(summary: dict, friendly_name: str) -> dict | None:
    title = summary.get("title") or friendly_name
    extract = (summary.get("extract") or "").strip()
    if not extract:
        return None

    # Prefer the thumbnail (pre-cached, served without per-IP burst limits) over
    # the original. Wikimedia returns 429 on bursty originalimage fetches.
    img_meta = summary.get("thumbnail") or summary.get("originalimage") or {}
    img_url = img_meta.get("source")
    image_data: dict = {}
    if img_url:
        slug = title.replace("/", "_").replace(" ", "_")
        cache_path = CACHE_DIR / "images" / f"{slug}.jpg"
        jpeg = _download_and_downsize(img_url, cache_path=cache_path)
        if jpeg:
            image_data = {
                "image_b64": base64.b64encode(jpeg).decode("ascii"),
                "image_mime": "image/jpeg",
                "image_attribution": (
                    f"Wikimedia Commons via Wikipedia ({title}); "
                    "image typically CC-BY-SA. See source URL for author."
                ),
                "image_source_url": img_url,
            }

    return {
        "name": friendly_name,
        "category": "equipment",
        "wiki_title": title,
        "text": extract,
        "source_doc": f"Wikipedia: {title}",
        "_provenance": "en.wikipedia.org (CC-BY-SA 4.0 text)",
        **image_data,
    }


def ingest(out_path: Path) -> tuple[int, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with_image = 0
    with out_path.open("w", encoding="utf-8") as f:
        for title, friendly in EQUIPMENT_LIST:
            log.info("fetching %s (%s)", title, friendly)
            summary = _fetch_summary(title)
            if not summary:
                continue
            rec = _summary_to_record(summary, friendly)
            if not rec:
                log.warning("no extract for %s; skipping", title)
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
            if "image_b64" in rec:
                with_image += 1
                log.info("  + image (%d KB b64)", len(rec["image_b64"]) // 1024)
            time.sleep(1.0)  # be polite to Wikipedia (avoids burst 429s)
    return written, with_image


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    n, n_img = ingest(Path(args.out))
    log.info("wrote %d equipment entries (%d with images) → %s", n, n_img, args.out)


if __name__ == "__main__":
    main()
