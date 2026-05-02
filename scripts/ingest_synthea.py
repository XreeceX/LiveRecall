"""Ingest Synthea synthetic patient data into a JSONL fixture.

Synthea (https://github.com/synthetichealth/synthea) is the open-source
MITRE-built synthetic patient generator. It emits realistic FHIR/CSV records
with no PHI risk by construction (no real patient ever existed).

We download the prebuilt Synthea jar (no Java compile step), run it for ~80
patients in Massachusetts (the default catchment) seeded deterministically,
then transform a CSV-export subset into LiveRecall fixtures:

  - patients.csv     → patient master records
  - medications.csv  → med_administration events
  - observations.csv → vitals + lab_result events (LOINC-coded)

Output schema:
  patients_sample.jsonl entries → matches `patients` collection
  events_sample.jsonl  entries → matches `clinical_events` Time Series doc

We bundle them into one file `synthea_sample.jsonl` with a discriminator field:
  {"kind": "patient", ...}
  {"kind": "event",   ...}

Run (one-time, ~5 min the first run):
    python -m scripts.ingest_synthea
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ingest.synthea")

OUT_PATH = Path("data/fixtures/synthea_sample.jsonl")
CACHE_DIR = Path("data/cache/synthea")
JAR_URL = "https://github.com/synthetichealth/synthea/releases/download/v3.3.0/synthea-with-dependencies.jar"
JAR_PATH = CACHE_DIR / "synthea-with-dependencies.jar"
CSV_OUT_DIR = CACHE_DIR / "output" / "csv"

PATIENT_COUNT = 80      # generates ~25–35 patients with diabetes after filters
SEED = 42

# Limits to keep the fixture small.
MAX_PATIENTS = 25
MAX_EVENTS_PER_PATIENT = 40

# LOINC → friendly lab_name. We only emit observations whose code matches.
WANTED_LABS = {
    "33914-3": "eGFR",
    "2160-0": "creatinine",
    "4548-4": "HbA1c",
    "2823-3": "potassium",
    "2345-7": "glucose",
    "8480-6": "BP_systolic",
    "8462-4": "BP_diastolic",
    "8867-4": "heart_rate",
    "59408-5": "SpO2",
    "8310-5": "temperature",
}

# Conditions that bias us toward selecting diabetic / CKD patients first.
DEMO_BIAS_KEYWORDS = ("diabet", "renal", "kidney", "hypertens")


def _download_jar() -> None:
    if JAR_PATH.exists() and JAR_PATH.stat().st_size > 1_000_000:
        return
    JAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading Synthea jar (~70 MB) → %s", JAR_PATH)
    with urllib.request.urlopen(JAR_URL, timeout=120) as r, JAR_PATH.open("wb") as f:
        shutil.copyfileobj(r, f)
    log.info("downloaded %d bytes", JAR_PATH.stat().st_size)


def _run_synthea() -> None:
    """Run Synthea with CSV exporter on. Idempotent — only runs if no CSVs."""
    if (CSV_OUT_DIR / "patients.csv").exists():
        log.info("synthea CSV output already present; skipping generate")
        return
    if not shutil.which("java"):
        raise RuntimeError("java not found on PATH; install JDK 11+ to run Synthea")
    cmd = [
        "java",
        "-jar", str(JAR_PATH),
        "-p", str(PATIENT_COUNT),
        "-s", str(SEED),
        "--exporter.baseDirectory", str((CACHE_DIR / "output").resolve()),
        "--exporter.csv.export", "true",
        "--exporter.fhir.export", "false",
        "--exporter.hospital.fhir.export", "false",
        "--exporter.practitioner.fhir.export", "false",
        "--exporter.metadata.export", "false",
    ]
    log.info("running synthea: %s", " ".join(cmd[:5]) + " ...")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        log.error("synthea stderr: %s", proc.stderr[-2000:])
        raise RuntimeError(f"synthea failed exit={proc.returncode}")
    log.info("synthea done; CSVs in %s", CSV_OUT_DIR)


def _read_csv(name: str) -> list[dict]:
    p = CSV_OUT_DIR / name
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _parse_dt(s: str) -> int:
    """Synthea CSV dates: 'YYYY-MM-DDThh:mm:ssZ' or just 'YYYY-MM-DD'."""
    if not s:
        return 0
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def _select_patients(rows: list[dict], conds: list[dict]) -> list[dict]:
    """Pick MAX_PATIENTS patients, biased toward those with demo-relevant conditions."""
    by_pid = {p["Id"]: p for p in rows}
    interesting: dict[str, int] = {}
    for c in conds:
        desc = (c.get("DESCRIPTION") or "").lower()
        if any(k in desc for k in DEMO_BIAS_KEYWORDS):
            interesting[c["PATIENT"]] = interesting.get(c["PATIENT"], 0) + 1
    ranked_ids = sorted(by_pid, key=lambda pid: -interesting.get(pid, 0))
    return [by_pid[pid] for pid in ranked_ids[:MAX_PATIENTS]]


def _conditions_for(pid: str, conds: list[dict]) -> list[str]:
    out = []
    for c in conds:
        if c["PATIENT"] != pid or c.get("STOP"):
            continue
        d = (c.get("DESCRIPTION") or "").strip()
        if d:
            out.append(d)
    return sorted(set(out))


def _allergies_for(pid: str, allergies: list[dict]) -> list[str]:
    out = []
    for a in allergies:
        if a["PATIENT"] != pid:
            continue
        d = (a.get("DESCRIPTION") or "").strip()
        if d:
            out.append(d)
    return sorted(set(out))


_NAME_NUMBERS_RE = __import__("re").compile(r"\d+$")


def _strip_synthea_numbers(s: str) -> str:
    """Synthea names ship with a numeric suffix to disambiguate (e.g.,
    "Mariano761"). Strip it so the demo reads as "Mariano Tamez", not
    "Mariano761 Tamez493"."""
    return _NAME_NUMBERS_RE.sub("", s).strip()


def _patient_doc(p: dict, conds: list[dict], allergies: list[dict], obs: list[dict]) -> dict:
    pid_uuid = p["Id"]
    short_pid = "S-" + pid_uuid.replace("-", "")[:6].upper()
    first = _strip_synthea_numbers(p.get("FIRST", ""))
    last = _strip_synthea_numbers(p.get("LAST", ""))
    name = (first + " " + last).strip() or "Mock Patient"

    age = 0
    try:
        birth = datetime.fromisoformat(p.get("BIRTHDATE", "")).replace(tzinfo=timezone.utc)
        age = int((datetime.now(timezone.utc) - birth).days / 365.25)
    except Exception:  # noqa: BLE001
        pass

    weight_kg = 0.0
    for o in obs:
        if o["PATIENT"] != pid_uuid:
            continue
        if o.get("CODE") == "29463-7":   # LOINC body weight
            try:
                weight_kg = float(o.get("VALUE", "0"))
            except ValueError:
                continue
    return {
        "kind": "patient",
        "synthea_id": pid_uuid,
        "patient_id": short_pid,
        "name": name,
        "age": age,
        "weight_kg": round(weight_kg, 1),
        "allergies": _allergies_for(pid_uuid, allergies),
        "active_conditions": _conditions_for(pid_uuid, conds),
        "_provenance": "synthea v3.3 (Apache 2.0, MITRE)",
    }


def _med_event(row: dict, by_uuid: dict[str, str]) -> dict | None:
    pid_uuid = row["PATIENT"]
    short = by_uuid.get(pid_uuid)
    if not short:
        return None
    desc = (row.get("DESCRIPTION") or "").strip()
    if not desc:
        return None
    med = desc.split()[0].lower()
    return {
        "kind": "event",
        "patient_id": short,
        "timestamp": _parse_dt(row.get("START") or row.get("DATE", "")),
        "event_type": "med_administration",
        "severity": "low",
        "notes": f"{desc} administered.",
        "medication": med,
        "dose": desc,
        "lab_name": None,
        "lab_value": None,
        "lab_unit": None,
        "_provenance": "synthea",
    }


def _obs_event(row: dict, by_uuid: dict[str, str]) -> dict | None:
    pid_uuid = row["PATIENT"]
    short = by_uuid.get(pid_uuid)
    if not short:
        return None
    code = row.get("CODE", "")
    lab_name = WANTED_LABS.get(code)
    if not lab_name:
        return None
    try:
        val = float(row.get("VALUE", ""))
    except ValueError:
        return None
    unit = row.get("UNITS", "")
    desc = (row.get("DESCRIPTION") or lab_name).strip()
    is_vitals = lab_name in {"BP_systolic", "BP_diastolic", "heart_rate", "SpO2", "temperature"}
    return {
        "kind": "event",
        "patient_id": short,
        "timestamp": _parse_dt(row.get("DATE") or row.get("START", "")),
        "event_type": "vitals" if is_vitals else "lab_result",
        "severity": "low",
        "notes": f"{desc} {val} {unit}".strip(),
        "medication": None,
        "dose": None,
        "lab_name": None if is_vitals else lab_name,
        "lab_value": None if is_vitals else val,
        "lab_unit": None if is_vitals else unit,
        "_provenance": "synthea",
    }


def ingest(out_path: Path) -> tuple[int, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    patients = _read_csv("patients.csv")
    conds = _read_csv("conditions.csv")
    allergies = _read_csv("allergies.csv")
    meds = _read_csv("medications.csv")
    obs = _read_csv("observations.csv")
    if not patients:
        raise RuntimeError(f"no synthea CSVs at {CSV_OUT_DIR}")

    selected = _select_patients(patients, conds)
    by_uuid = {p["Id"]: "S-" + p["Id"].replace("-", "")[:6].upper() for p in selected}
    log.info("selected %d patients (bias: diabetes/CKD/HTN)", len(selected))

    # Group events per patient (keyed by short ID, not UUID, since that's what
    # _med_event / _obs_event emit) and cap to MAX_EVENTS_PER_PATIENT.
    events_by_pid: dict[str, list[dict]] = {short: [] for short in by_uuid.values()}
    for row in meds:
        ev = _med_event(row, by_uuid)
        if ev:
            events_by_pid[ev["patient_id"]].append(ev)
    for row in obs:
        ev = _obs_event(row, by_uuid)
        if ev:
            events_by_pid[ev["patient_id"]].append(ev)
    for pid in events_by_pid:
        events_by_pid[pid].sort(key=lambda e: e["timestamp"], reverse=True)
        events_by_pid[pid] = events_by_pid[pid][:MAX_EVENTS_PER_PATIENT]

    n_patients = 0
    n_events = 0
    with out_path.open("w", encoding="utf-8") as f:
        for p in selected:
            f.write(json.dumps(_patient_doc(p, conds, allergies, obs), ensure_ascii=False) + "\n")
            n_patients += 1
        for pid in events_by_pid:
            for ev in events_by_pid[pid]:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                n_events += 1
    return n_patients, n_events


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=str(OUT_PATH))
    p.add_argument("--skip-generate", action="store_true",
                   help="reuse cached CSVs, don't re-run java")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if not args.skip_generate:
        _download_jar()
        _run_synthea()
    np_, ne = ingest(Path(args.out))
    log.info("wrote %d patients + %d events → %s", np_, ne, args.out)


if __name__ == "__main__":
    main()
