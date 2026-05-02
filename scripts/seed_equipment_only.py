"""Equipment-only seed.

Loads ONLY the Wikimedia equipment fixture into MongoDB, embeds the text
via OpenAI `text-embedding-3-small`, and drops every other collection so
the database holds nothing but the apparatus catalog.

Use this when the team has narrowed scope to the visual-equipment-recognition
slice of LiveRecall — i.e. the dashboard / Vision agent should ONLY surface
hits from the Wikimedia equipment catalog and there's no DailyMed,
Synthea, MTSamples, or pipeline state in the way.

To restore the full multi-source seed (DailyMed medications + Synthea
patients + MTSamples notes + Wikimedia equipment), run the original:

    python -m scripts.seed_mongo

Run:
    python -m scripts.seed_equipment_only
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from backend.embeddings import embed_batch
from backend.mongo import collection, get_db, init_collections
from backend.util import new_id

log = logging.getLogger("seed.equipment_only")

FIXTURE = Path("data/fixtures/wikimedia_equipment_sample.jsonl")
EMBED_BATCH_SIZE = 50

# Every collection the multi-source seeder touches. We drop them all so the
# DB is reduced to just `documents` (equipment-only) + system collections.
DROP_COLLECTIONS = [
    "agent_traces",
    "answers",
    "clinical_events",
    "clips.files",
    "clips.chunks",
    "final_context",
    "patients",
    "questions",
    "retrieval_plans",
    "retrieval_results",
    "scene_context",
    "sessions",
    "transcripts",
    "video_frames",
]

# Vector indexes on collections we're dropping; explicit drop avoids
# orphaned Atlas Search indexes that linger after the collection is gone.
DROP_VECTOR_INDEXES = [
    ("scene_context", "scene_text_vec"),
    ("transcripts", "transcript_text_vec"),
]


def _load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        raise FileNotFoundError(
            f"missing fixture: {p}. Re-run `python -m scripts.ingest_wikimedia_equipment` "
            "to regenerate it from Wikipedia, or restore it from git."
        )
    out: list[dict] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning("skipping bad line in %s: %s", p, e)
    return out


async def _drop_unwanted() -> None:
    db = get_db()
    # Documents collection: keep, but only purge non-equipment rows. This
    # lets a partial run (e.g. equipment + medications already loaded) be
    # reduced to equipment-only without losing the embedding work already
    # done on the equipment rows.
    res = await collection("documents").delete_many({"category": {"$ne": "equipment"}})
    if res.deleted_count:
        log.info("purged %d non-equipment rows from documents", res.deleted_count)

    for name in DROP_COLLECTIONS:
        try:
            await db.drop_collection(name)
            log.info("dropped collection %s", name)
        except Exception as e:  # noqa: BLE001
            log.debug("skip drop %s: %s", name, e)

    for coll, idx in DROP_VECTOR_INDEXES:
        try:
            await db[coll].drop_search_index(idx)
            log.info("dropped vector index %s on %s", idx, coll)
        except Exception:
            pass


async def _seed_equipment(rows: list[dict]) -> None:
    # Idempotent: clear the equipment slice first so re-running is clean.
    await collection("documents").delete_many({"category": "equipment"})

    docs: list[dict] = []
    for e in rows:
        docs.append({
            "_id": new_id("doc"),
            "name": e["name"],
            "category": "equipment",
            "source_doc": e.get("source_doc") or f"Wikipedia: {e['name']}",
            "section": "Overview",
            "text": e["text"],
            "image_b64": e.get("image_b64"),
            "image_mime": e.get("image_mime"),
            "image_attribution": e.get("image_attribution"),
            "image_source_url": e.get("image_source_url"),
            "_provenance": e.get("_provenance") or "en.wikipedia.org",
        })

    texts = [
        f"{d['source_doc']} — {d.get('section','')}: {d['text']}"
        for d in docs
    ]
    log.info("embedding %d equipment entries (batched, OpenAI text-embedding-3-small)…", len(texts))
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        vectors.extend(await embed_batch(batch))
    for d, v in zip(docs, vectors):
        d["text_embedding"] = v

    await collection("documents").insert_many(docs)

    # $text fallback for cold-vector-index environments. Safe to call on
    # an existing index — Mongo will no-op.
    try:
        await collection("documents").create_index([("text", "text")])
    except Exception:  # noqa: BLE001
        pass

    with_img = sum(1 for d in docs if d.get("image_b64"))
    log.info("seeded %d equipment docs (%d with images)", len(docs), with_img)


async def main() -> None:
    # init_collections() is the same one main.py runs at startup; it's
    # idempotent and ensures the vector index on documents.text_embedding
    # exists. We then drop everything else.
    await init_collections()
    rows = _load_jsonl(FIXTURE)
    log.info("loaded %d equipment rows from %s", len(rows), FIXTURE)
    await _drop_unwanted()
    await _seed_equipment(rows)

    # Final state report.
    db = get_db()
    n = await collection("documents").count_documents({"category": "equipment"})
    img = await collection("documents").count_documents({
        "category": "equipment",
        "image_b64": {"$exists": True, "$ne": None},
    })
    emb = await collection("documents").count_documents({
        "category": "equipment",
        "text_embedding": {"$exists": True},
    })
    user_colls = sorted(
        c for c in await db.list_collection_names() if not c.startswith("system.")
    )
    log.info(
        "done. documents.equipment=%d (image=%d, embedding=%d). user_collections=%s",
        n, img, emb, user_colls,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(main())
