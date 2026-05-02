"""Seed Mongo for the factory-walkthrough demo.

Creates:
  - 5 machines (C-201..C-205)
  - 50 maintenance events over 6 months in the Time Series collection
  - 3 manuals chunked (~30 chunks total) and embedded
  - 5 past inspection transcripts embedded
  - 1 demo session row

Idempotent: clears+rewrites the demo data on each run.

Usage:
    python -m scripts.seed_mongo
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from backend.embeddings import embed_batch
from backend.mongo import collection, init_collections
from backend.util import new_id, now_ms

log = logging.getLogger("seed")
random.seed(42)


MACHINES = [
    {
        "machine_id": "C-201",
        "kind": "conveyor",
        "location": "factory_floor_north",
        "spec_failure_rate": 0.018,
    },
    {
        "machine_id": "C-202",
        "kind": "conveyor",
        "location": "factory_floor_north",
        "spec_failure_rate": 0.024,
    },
    {
        "machine_id": "C-203",
        "kind": "press",
        "location": "factory_floor_east",
        "spec_failure_rate": 0.041,
    },
    {
        "machine_id": "C-204",
        "kind": "conveyor",
        "location": "factory_floor_south",
        "spec_failure_rate": 0.030,
    },
    {
        "machine_id": "C-205",
        "kind": "compressor",
        "location": "utility_room",
        "spec_failure_rate": 0.012,
    },
]

EVENT_TYPES = ["service", "alert", "inspection"]
SEVERITIES = ["low", "medium", "high"]

NOTES_BY_TYPE = {
    "service": [
        "Replaced drive belt; tension calibrated to 47 N.",
        "Lubricated bearings, checked alignment.",
        "Swapped pressure sensor — old unit drifted.",
        "Tightened mounting bolts after vibration alert.",
    ],
    "alert": [
        "Pressure briefly spiked to 73 PSI; auto-reset.",
        "Belt tension low warning, no downtime.",
        "Temperature sensor reading 4°C above baseline.",
        "Vibration RMS exceeded 1.2g for 3 seconds.",
    ],
    "inspection": [
        "Routine quarterly inspection — within tolerance.",
        "Visual check on welds, no cracks observed.",
        "Pressure gauge reads in mid-range (42–49 PSI).",
        "Conveyor belt wear at 18%, replace at 30%.",
    ],
}


MANUALS = [
    {
        "machine_id": "C-204",
        "source_doc": "Acme C-Series Conveyor — Operator Manual rev 4.2",
        "sections": [
            ("Specifications",
             "C-204 conveyor: nominal belt speed 0.8 m/s, motor 2.2 kW, "
             "expected mean time between failures 2,800 hours, spec failure "
             "rate 3% annually. Operating pressure window 35–55 PSI."),
            ("Pressure Gauge Interpretation",
             "The pressure gauge mounted forward of the drive pulley reads "
             "system air-line pressure. Mid-range 42–49 PSI is normal. "
             "Below 35 PSI indicates tension loss; above 60 PSI triggers an "
             "auto-stop on the controller."),
            ("Belt Wear and Replacement",
             "Inspect belt monthly. Replace at 30% wear. A worn belt at 47 PSI "
             "is still safe to operate but service window should be scheduled."),
            ("Service Interval",
             "Recommended service every 1,200 hours of operation. Lubricate "
             "bearings, check belt tension to 47 N, inspect drive coupling."),
            ("Troubleshooting C-204",
             "If conveyor stalls: check pressure gauge, verify reading is in "
             "the 42–49 PSI band, then inspect drive belt for slip."),
        ],
    },
    {
        "machine_id": "C-201",
        "source_doc": "Acme C-Series Conveyor — Operator Manual rev 4.2",
        "sections": [
            ("Specifications",
             "C-201 conveyor: nominal belt speed 1.0 m/s, motor 1.8 kW, "
             "spec failure rate 1.8% annually."),
            ("Service Interval",
             "Recommended service every 1,500 hours. Belt tension to 42 N."),
        ],
    },
    {
        "machine_id": "C-205",
        "source_doc": "Acme A-Series Compressor — Service Guide rev 1.9",
        "sections": [
            ("Specifications",
             "C-205 compressor: 7.5 kW, working pressure 80–110 PSI, spec "
             "failure rate 1.2% annually."),
            ("Pressure Gauge Interpretation",
             "Compressor head gauge should read 80–110 PSI under load. Below "
             "70 PSI indicates a leak in the circuit."),
            ("Service Interval",
             "Inspect filters every 500 hours; full service every 4,000 hours."),
        ],
    },
]


PAST_TRANSCRIPTS = [
    "Looked at C-204 last Tuesday — gauge was 46 PSI, sounded fine, no issues.",
    "C-203 press has been making a knocking sound under load, scheduled inspection.",
    "Rolled by the compressor in the utility room, head gauge at 92 PSI, normal.",
    "Belt on C-204 looks slightly worn at the edge; I'd estimate 17 percent.",
    "C-201 conveyor service done last month, replaced bearings, ran fine afterward.",
]


async def _wipe() -> None:
    for c in (
        "machines",
        "documents",
        "transcripts",
        "maintenance_events",
        "scene_context",
        "video_frames",
        "questions",
        "retrieval_plans",
        "retrieval_results",
        "final_context",
        "answers",
        "agent_traces",
        "sessions",
    ):
        await collection(c).delete_many({})
    log.info("wiped existing demo data")


async def _seed_machines() -> None:
    docs = []
    for m in MACHINES:
        docs.append({
            "_id": new_id("m"),
            **m,
            "installed_at": now_ms() - 2 * 365 * 86_400_000,
        })
    await collection("machines").insert_many(docs)
    log.info("seeded %d machines", len(docs))


async def _seed_events() -> None:
    """50 events over 6 months → Time Series collection."""
    docs = []
    six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
    for i in range(50):
        days_ago = random.randint(0, 180)
        ts = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=random.randint(0, 23))
        machine = random.choice(MACHINES)["machine_id"]
        # Bias C-204 to having a recent service ~47 days ago.
        if i == 0:
            machine = "C-204"
            ts = datetime.now(timezone.utc) - timedelta(days=47)
            ev_type = "service"
            sev = "low"
            note = "Replaced drive belt on C-204; tension recalibrated to 47 N. Pressure verified at 46 PSI."
        else:
            ev_type = random.choice(EVENT_TYPES)
            sev = random.choices(SEVERITIES, weights=[6, 3, 1])[0]
            note = random.choice(NOTES_BY_TYPE[ev_type])
        docs.append({
            "timestamp": ts,
            "machine_id": machine,
            "event_type": ev_type,
            "severity": sev,
            "notes": note,
        })
    await collection("maintenance_events").insert_many(docs)
    log.info("seeded %d maintenance events into time-series", len(docs))


async def _seed_manuals() -> None:
    """Chunk + embed manuals."""
    chunks: list[dict] = []
    texts: list[str] = []
    for manual in MANUALS:
        for section, body in manual["sections"]:
            chunks.append({
                "_id": new_id("doc"),
                "machine_id": manual["machine_id"],
                "source_doc": manual["source_doc"],
                "section": section,
                "text": body,
            })
            texts.append(f"{manual['source_doc']} — {section}: {body}")
    vectors = await embed_batch(texts)
    for c, v in zip(chunks, vectors):
        c["text_embedding"] = v
    await collection("documents").insert_many(chunks)
    # Plain text index as a fallback for non-Atlas environments.
    try:
        await collection("documents").create_index([("text", "text")])
    except Exception:  # noqa: BLE001
        pass
    log.info("seeded %d manual chunks", len(chunks))


async def _seed_past_transcripts() -> None:
    vectors = await embed_batch(PAST_TRANSCRIPTS)
    docs = []
    for t, v in zip(PAST_TRANSCRIPTS, vectors):
        docs.append({
            "_id": new_id("ts"),
            "session_id": "historical",
            "timestamp": now_ms() - random.randint(1, 60) * 86_400_000,
            "text": t,
            "is_final": True,
            "is_question": False,
            "text_embedding": v,
        })
    await collection("transcripts").insert_many(docs)
    log.info("seeded %d past transcripts", len(docs))


async def _seed_session() -> None:
    await collection("sessions").update_one(
        {"_id": "demo"},
        {"$set": {"_id": "demo", "room": "liverecall-demo", "started_at": now_ms(), "ended_at": None}},
        upsert=True,
    )


async def main() -> None:
    await init_collections()
    await _wipe()
    await _seed_machines()
    await _seed_events()
    await _seed_manuals()
    await _seed_past_transcripts()
    await _seed_session()
    log.info("seed complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(main())
