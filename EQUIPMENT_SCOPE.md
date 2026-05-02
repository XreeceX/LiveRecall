# Equipment-only scope

> **Status:** as of 2026-05-02, the live MongoDB Atlas (`liverecall` DB) holds
> **only the Wikimedia equipment catalog** — 63 apparatus entries from
> `data/fixtures/wikimedia_equipment_sample.jsonl`. All other datasets
> (DailyMed medications, Synthea patients/events, MTSamples notes) and all
> pipeline-state collections have been dropped.

This is a deliberate scope reduction so the team can focus on the
visual-equipment-recognition slice end-to-end before re-introducing the
other retrievers.

## What's currently in Mongo

| Collection | Count | Notes |
|---|---:|---|
| `documents` | **63** | every row is `category: "equipment"`, sourced from Wikipedia. 57 carry inline `image_b64` thumbnails (≤256 px JPEG, base64). 63/63 have OpenAI embeddings (`text-embedding-3-small`, 1536-dim). |

Indexes still in place:

- Default `_id_` index on `documents`
- Text index `text_text` on `documents.text` (`$text` fallback)
- Atlas Vector Search index `doc_text_vec` on `documents.text_embedding` (status: `READY`)

Everything else has been dropped (`agent_traces`, `answers`,
`clinical_events`, `clips.{files,chunks}`, `final_context`, `patients`,
`questions`, `retrieval_plans`, `retrieval_results`, `scene_context`,
`sessions`, `transcripts`, `video_frames`).

## How to reproduce this state

```bash
make seed-equipment
# or:
python -m scripts.seed_equipment_only
```

Requires `MONGODB_URI` and `OPENAI_API_KEY` in `.env` (see `.env.example`).
The script is idempotent — re-running just rewrites the same 63 rows.

## How to revert to the full multi-source seed

The original DailyMed + Synthea + MTSamples + Wikimedia fixtures are still
in `data/fixtures/`. To restore the full demo dataset:

```bash
make seed
# equivalent to:
python -m backend.mongo          # recreates collections + vector indexes
python -m scripts.seed_mongo     # loads all 4 fixtures, embeds, inserts
```

This will recreate the `patients`, `clinical_events`, `transcripts`,
`sessions`, etc. collections and re-insert the headline `P-204` /
metformin demo data.

## What still works in equipment-only mode

- ✅ **Vector search** on the equipment catalog (`$vectorSearch` against
  `doc_text_vec`). Verified: query `"airway intubation device"` returns
  `laryngeal tube` (score 0.779), `"IV drip pump"` returns `infusion pump`
  (score 0.784), etc.
- ✅ **Text fallback** (`$text`) on `documents.text` if the vector index
  isn't ready yet.
- ✅ **Filter-by-name** (`{name: "infusion pump"}`) for the cache-prefetch
  path — see `backend/agents/retrievers.py::_query_apparatus_refs_mongo`.

## What's broken / no-op in equipment-only mode

- ❌ **`Events` retriever** — empty result set (no `clinical_events`).
  Anything calling `retrieve_events` returns `[]`.
- ❌ **`Notes` retriever** — empty result set (no `transcripts`).
- ❌ **Any code path that reads `patients`** — empty.
- ❌ **GridFS clip storage** — `clips` bucket is gone.

Until the team re-broadens scope, treat the References retriever (equipment
slice) as the single source of truth.

## Known environment issues

- **Python 3.14 + Pillow.** Pillow has no prebuilt Windows wheel for
  Python 3.14, and source builds fail (no zlib headers). This blocks
  `python -m backend.worker dev` (which imports `PIL` for frame sampling)
  and `python -m scripts.ingest_wikimedia_equipment` (image downscaling).
  Workaround: install Python 3.11 or 3.12 alongside, point a venv at it.
  The seeding + vector search path itself does NOT need Pillow.

- **Agent loops not wired.** `run_vision_loop`, `run_router_loop`,
  `run_retrievers_loop`, `run_reranker_loop`, `run_answerer_loop` are
  defined under `backend/agents/*.py` but never invoked anywhere in the
  codebase. POSTing to `/ask` or `/snap` writes to Mongo but no loop picks
  it up. This is unfinished wiring — likely needs a `lifespan` hook in
  `backend/main.py` that fires the loops as background tasks.

- **No `ELEVENLABS_API_KEY`.** Streaming STT (Scribe v2 Realtime) and TTS
  (Flash v2.5) won't work without it. Voice in/out is offline.

## Smoke tests

Verify Atlas state without leaving the terminal:

```bash
python -c "
import asyncio
from backend.mongo import collection, get_db

async def main():
    db = get_db()
    print('database:', db.name)
    print('collections:', sorted(c for c in await db.list_collection_names() if not c.startswith('system.')))
    print('documents.equipment:', await collection('documents').count_documents({'category': 'equipment'}))

asyncio.run(main())
"
```

Try a real vector search:

```bash
python -c "
import asyncio
from backend.embeddings import embed
from backend.mongo import collection

async def main():
    vec = await embed('IV drip pump')
    pipe = [
        {'\$vectorSearch': {'index': 'doc_text_vec', 'queryVector': vec, 'path': 'text_embedding', 'numCandidates': 50, 'limit': 3}},
        {'\$project': {'name': 1, 'score': {'\$meta': 'vectorSearchScore'}}},
    ]
    async for d in collection('documents').aggregate(pipe):
        print(f'  [{d[\"score\"]:.3f}] {d[\"name\"]}')

asyncio.run(main())
"
```

Expected: `infusion pump` ~0.78, `syringe driver` ~0.78, `insulin pump` ~0.74.
