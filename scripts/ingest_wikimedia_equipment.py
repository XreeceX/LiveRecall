"""Ingest Wikimedia / Wikipedia medical-equipment catalog into a JSONL fixture.

For each apparatus name we hit Wikipedia's REST summary endpoint:

  https://en.wikipedia.org/api/rest_v1/page/summary/{title}

Returns the article extract and a thumbnail URL.  We download the image,
downscale to ~256 px JPEG via Pillow, base64-encode it, and emit one JSONL
record per item:

  { name, category="equipment", text, source_doc, image_b64, image_mime,
    image_attribution, image_source_url, _provenance }

Wikipedia text is CC-BY-SA 4.0; Commons images vary (often CC-BY-SA 4.0 or
PD). Attribution is carried in each record so the dashboard can show it.

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
WP_API  = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "LiveRecall-hackathon/1.0 (educational; contact: hackathon-team)"}

OUT_PATH  = Path("data/fixtures/wikimedia_equipment_sample.jsonl")
CACHE_DIR = Path("data/cache/wikimedia")
IMAGE_MAX_PX       = 256
IMAGE_JPEG_QUALITY = 75
HTTP_TIMEOUT       = 30.0

# ---------------------------------------------------------------------------
# Curated list — (Wikipedia_page_title, friendly_name)
# Covers airway, cardiac, vascular, monitoring, infusion, mobility, surgical,
# respiratory, renal, neuro, lab, and misc bedside equipment.
# Duplicates removed; friendly names lowercase.
# ---------------------------------------------------------------------------
EQUIPMENT_LIST: list[tuple[str, str]] = [
    # --- Airway & respiratory -------------------------------------------------
    ("Tracheal_tube", "endotracheal tube"),
    ("Laryngeal_mask_airway", "laryngeal mask airway"),
    ("Laryngoscope", "laryngoscope"),
    ("Laryngeal_tube", "laryngeal tube"),
    ("Bag_valve_mask", "bag valve mask"),
    ("Medical_ventilator", "mechanical ventilator"),
    ("Nebulizer", "nebulizer"),
    ("Incentive_spirometer", "incentive spirometer"),
    ("Nasal_cannula", "nasal cannula"),
    ("Medical_oxygen_mask", "oxygen mask"),
    ("Non-rebreather_mask", "non-rebreather mask"),
    ("Oxygen_concentrator", "oxygen concentrator"),
    ("Portable_oxygen_concentrator", "portable oxygen concentrator"),
    ("Tracheotomy_tube", "tracheostomy tube"),
    ("Suction_(medicine)", "suction machine"),
    ("Peak_flow_meter", "peak flow meter"),
    ("Capnography", "capnography monitor"),
    ("High-flow_nasal_cannula", "high-flow nasal cannula"),

    # --- Cardiac & vascular ---------------------------------------------------
    ("Defibrillation", "defibrillator"),
    ("Automated_external_defibrillator", "AED"),
    ("Pacemaker", "pacemaker"),
    ("Implantable_cardioverter-defibrillator", "ICD"),
    ("Ventricular_assist_device", "ventricular assist device"),
    ("Extracorporeal_membrane_oxygenation", "ECMO machine"),
    ("Cardiopulmonary_bypass", "heart-lung bypass machine"),
    ("Arterial_line", "arterial line"),
    ("Central_venous_catheter", "central venous catheter"),
    ("Pulmonary_artery_catheter", "pulmonary artery catheter"),
    ("Peripherally_inserted_central_catheter", "PICC line"),
    ("Coronary_stent", "coronary stent"),
    ("Intra-aortic_balloon_pump", "intra-aortic balloon pump"),

    # --- Monitoring -----------------------------------------------------------
    ("Patient_monitor", "vital signs monitor"),
    ("Pulse_oximetry", "pulse oximeter"),
    ("Sphygmomanometer", "blood pressure cuff"),
    ("Electrocardiography", "ecg machine"),
    ("Electroencephalography", "eeg machine"),
    ("Bispectral_index", "BIS monitor"),
    ("Continuous_glucose_monitor", "continuous glucose monitor"),
    ("Glucose_meter", "glucose meter"),
    ("Medical_thermometer", "thermometer"),
    ("Impedance_cardiography", "impedance cardiograph"),

    # --- Infusion & vascular access -------------------------------------------
    ("Infusion_pump", "infusion pump"),
    ("Syringe_driver", "syringe driver"),
    ("Syringe", "syringe"),
    ("Hypodermic_needle", "hypodermic needle"),
    ("Cannula", "cannula"),
    ("Butterfly_needle", "butterfly needle"),
    ("IV_pole", "iv pole"),
    ("Insulin_pen", "insulin pen"),
    ("Insulin_pump", "insulin pump"),
    ("Port_(medical)", "implanted port"),
    ("Hickman_line", "Hickman line"),

    # --- Urological & drainage ------------------------------------------------
    ("Urinary_catheterization", "urinary catheter"),
    ("Foley_catheter", "Foley catheter"),
    ("Chest_tube", "chest tube"),
    ("Nasogastric_intubation", "nasogastric tube"),
    ("Feeding_tube", "feeding tube"),
    ("Jackson-Pratt_drain", "Jackson-Pratt drain"),

    # --- Dialysis & renal -----------------------------------------------------
    ("Hemodialysis", "hemodialysis machine"),
    ("Peritoneal_dialysis", "peritoneal dialysis system"),

    # --- Diagnostic instruments -----------------------------------------------
    ("Stethoscope", "stethoscope"),
    ("Otoscope", "otoscope"),
    ("Ophthalmoscope", "ophthalmoscope"),
    ("Reflex_hammer", "reflex hammer"),
    ("Speculum", "speculum"),
    ("Endoscope", "endoscope"),
    ("Medical_ultrasound", "ultrasound probe"),
    ("Plethysmograph", "plethysmograph"),

    # --- Imaging (bedside) ----------------------------------------------------
    ("Portable_X-ray", "portable x-ray machine"),
    ("Point-of-care_ultrasound", "point-of-care ultrasound"),

    # --- Lab (bedside) --------------------------------------------------------
    ("Blood_gas_analyzer", "blood gas analyzer"),
    ("I-STAT", "iStat analyser"),

    # --- Surgical instruments -------------------------------------------------
    ("Scalpel", "scalpel"),
    ("Forceps", "forceps"),
    ("Retractor_(medical)", "surgical retractor"),
    ("Trocar", "trocar"),
    ("Needle_holder", "needle holder"),
    ("Tourniquet", "tourniquet"),
    ("Surgical_staple", "surgical stapler"),
    ("Operating_table", "operating table"),
    ("Electrosurgery", "electrosurgical unit"),

    # --- Mobility & support ---------------------------------------------------
    ("Hospital_bed", "hospital bed"),
    ("Stretcher", "stretcher"),
    ("Wheelchair", "wheelchair"),
    ("Crutch", "crutch"),
    ("Patient_lift", "patient lift"),
    ("Crash_cart", "crash cart"),

    # --- Sterile / infection control ------------------------------------------
    ("Autoclave", "autoclave"),
    ("Surgical_mask", "surgical mask"),
    ("N95_respirator", "N95 respirator"),
    ("Medical_glove", "medical glove"),

    # --- Wound care -----------------------------------------------------------
    ("Negative-pressure_wound_therapy", "wound VAC"),
    ("Wound_closure_strip", "wound closure strip"),

    # --- Neuro / pain / rehab -------------------------------------------------
    ("Transcutaneous_electrical_nerve_stimulation", "TENS unit"),
    ("Deep_brain_stimulation", "DBS device"),
    ("Transcranial_magnetic_stimulation", "TMS device"),
    ("Transcranial_direct-current_stimulation", "tDCS device"),

    # --- Ophthalmology & ENT --------------------------------------------------
    ("Slit_lamp", "slit lamp"),
    ("Tonometry", "tonometer"),
    ("Cochlear_implant", "cochlear implant"),

    # --- Neonatal / paediatric ------------------------------------------------
    ("Infant_incubator", "infant incubator"),
    ("Phototherapy_in_neonates", "neonatal phototherapy lamp"),
    ("Neonatal_resuscitation", "neonatal resuscitator"),

    # --- Miscellaneous bedside ------------------------------------------------
    ("Wristband", "patient wristband"),
    ("Hyperbaric_medicine", "hyperbaric chamber"),
    ("Colostomy_bag", "colostomy bag"),
    ("Compression_stockings", "compression stockings"),
    ("Traction_(orthopedics)", "traction device"),
    ("External_fixation", "external fixator"),
    ("Splint_(medicine)", "splint"),
    ("Cervical_collar", "cervical collar"),
]


def _http_get(url: str, *, timeout: float = HTTP_TIMEOUT, retries: int = 3) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code == 429 and attempt < retries:
                wait = 8 * (attempt + 1)
                log.info("  429 rate-limited; sleeping %ds", wait)
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < retries:
                time.sleep(3)
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
    title   = summary.get("title") or friendly_name
    extract = (summary.get("extract") or "").strip()
    if not extract:
        return None

    img_meta = summary.get("thumbnail") or summary.get("originalimage") or {}
    img_url  = img_meta.get("source")
    image_data: dict = {}
    if img_url:
        slug       = title.replace("/", "_").replace(" ", "_")
        cache_path = CACHE_DIR / "images" / f"{slug}.jpg"
        jpeg       = _download_and_downsize(img_url, cache_path=cache_path)
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
        "name":       friendly_name,
        "category":   "equipment",
        "wiki_title": title,
        "text":       extract,
        "source_doc": f"Wikipedia: {title}",
        "_provenance": "en.wikipedia.org (CC-BY-SA 4.0 text)",
        **image_data,
    }


def ingest(out_path: Path) -> tuple[int, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Deduplicate by friendly name (last definition wins for identical names)
    seen: dict[str, tuple[str, str]] = {}
    for title, friendly in EQUIPMENT_LIST:
        seen[friendly] = (title, friendly)
    deduped = list(seen.values())
    log.info("ingest: %d unique equipment entries", len(deduped))

    written    = 0
    with_image = 0
    with out_path.open("w", encoding="utf-8") as f:
        for i, (title, friendly) in enumerate(deduped):
            log.info("[%d/%d] fetching %s (%s)", i + 1, len(deduped), title, friendly)
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
            time.sleep(0.8)  # polite to Wikipedia
    return written, with_image


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_PATH))
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    n, n_img = ingest(Path(args.out))
    log.info("wrote %d equipment entries (%d with images) → %s", n, n_img, args.out)


if __name__ == "__main__":
    main()
