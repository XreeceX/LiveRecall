# LiveRecall

> **🚧 Current scope (2026-05-02): equipment-only.** The live Atlas DB has
> been narrowed to the Wikimedia equipment catalog (63 entries in
> `documents`). DailyMed medications, Synthea patients/events, and MTSamples
> notes are no longer in Mongo. See [`EQUIPMENT_SCOPE.md`](./EQUIPMENT_SCOPE.md)
> for what works, what doesn't, how to reproduce, and how to revert to the
> full multi-source seed.
>
> - Reproduce equipment-only state: `make seed-equipment`
> - Revert to full multi-source: `make seed`

**Clinical decision support grounded in live visual memory.** A clinician
wears camera glasses (or holds a phone). They look at a pill bottle, a
patient wristband, a vital-signs monitor — and ask one question. The system
pulls drug references, the patient's clinical timeline, and prior notes,
*reweighted by what the clinician is looking at right now*. **MongoDB
Atlas** is the substrate, **LiveKit Cloud** is the transport.

> Pitch: *"Multi-source agentic retrieval that reweights answers based on
> what the clinician is looking at right now."* Decision support, not
> diagnosis. Not RAG. Not an image analyzer.

**Two ways in:**
1. **Continuous capture** — glasses or phone stream POV; the system passively builds scene context.
2. **Single-image snap** — one tap, one frame, one question. `POST /snap`.

**Two capture devices** (parallel entry points, same Vision pipeline):

| Mode | Entry point | When to use it |
|---|---|---|
| 🕶️ **Meta Ray-Ban (POV, headline)** | [`phone/glasses.html`](./phone/glasses.html) — `capture_mode="glasses"` | Hands-free, first-person — the bedside-medication-safety story we want to pitch. |
| 📱 **Phone browser (universal fallback)** | [`phone/index.html`](./phone/index.html) — `capture_mode="phone"` | Day-1 adoption: every clinician on shift already has one, no hardware purchase needed. |

The Ray-Ban path is currently a first-person *stand-in* (laptop webcam or
phone in a head strap, with the Vision prompt aware that the framing is
first-person). The path to a real Meta Live AI → LiveKit ingress integration
is documented in [`DECISIONS.md`](./DECISIONS.md) entry (g). Same compliance
rules apply to both: mock patients only, no real PHI.

---

## What's in here

```
shared/         types.py + types.ts — the API contract
backend/        FastAPI + LiveKit Agents worker + 5 LangChain agents + Mongo
  agents/       vision · router · retrievers · reranker · answerer
  worker.py     LiveKit Agents entrypoint (joins room, samples frames, pipes audio)
  main.py       FastAPI app + change-stream WebSocket + REST endpoints + /snap
  mongo.py      8 collections, vector indexes, time series, TTL, change streams
  stt.py        ElevenLabs Scribe v2 Realtime streaming STT (writes transcripts + questions)
  tts.py        ElevenLabs Flash v2.5 streaming TTS (PCM into LiveKit AudioSource)
  embeddings.py text-embedding-3-small batched + cached
  tracing.py    LangChain MongoTraceCallback → agent_traces
phone/          Single-file LiveKit client for any phone browser (incl. Snap & ask)
dashboard/      Next.js + Tailwind judge-facing dashboard
scripts/        seed_mongo.py — 5 patients, 50 clinical events (TS), 3 drug references, 5 past notes
```

## Architecture (one screen)

```
[Phone]──audio+video──>[LiveKit room]──>[Worker]
   │                                      │
   │   tap "Snap & ask" ──POST /snap──────┤ (synchronous Vision; bypass continuous sampling)
   │                                      │
        ┌─────── frame sampler ──────────►│ video_frames ──► Vision (GPT-4o) ──► scene_context (vec)
        │                                 │
        │   Scribe v2 Realtime STT ◄──────┤ transcripts ──┐
        │                                 │               └─► questions ──► Router (GPT-4o-mini)
        │                                 │                                       │
        │                                 │                                  retrieval_plans
        │                                 │                                       │
        │                                 │                  ┌──────── 3 Retrievers (no LLM)
        │                                 │                  │   references  ($vectorSearch on documents)
        │                                 │                  │   events      (Time Series filter+sort, per patient_id)
        │                                 │                  │   notes       ($vectorSearch + recency)
        │                                 │                  │
        │                                 │                  │   ↑ Local Retrieval: cache pre-warmed by Vision the
        │                                 │                  │     moment a wristband / pill label is seen.
        │                                 │                  │     Hit ≈5 ms vs cold Mongo ≈150–300 ms.
        │                                 │                  │
        │                                 │                  └──► retrieval_results (with from_cache flag)
        │                                 │                                       │
        │                                 │                              Reranker Pass-1 (GPT-4o-mini)
        │                                 │                              may emit ≤2 active_followups
        │                                 │                                       │
        │                                 │                  ┌── Active Retrieval (when needed) ──┐
        │                                 │                  │   get_latest_lab(patient, lab)     │
        │                                 │                  │   get_last_administration(...)     │
        │                                 │                  │   get_monograph_section(...)       │
        │                                 │                  └──► Reranker Pass-2 folds in facts ──┘
        │                                 │                                       │
        │                                 │                                final_context
        │                                 │                                       │
        │                                 │                          Answerer (GPT-4o-mini, streaming)
        │                                 │                                       │
        │                                 │                ElevenLabs Flash v2.5  │
        │                                 │                       │               │
        │                                 │◄──── publish audio ───┘               │
        ▼                                 │                                       │
[LiveKit room]──audio──>[Phone speaker]   └──── change streams ──► /stream WS ──► Dashboard
```

Every state change is a Mongo write. The agent bus is Mongo change streams.

## Datasets — multimodal apparatus catalog + clinical context

Four real public datasets back the three retrievers. Everything runs
**zero-shot** through pre-trained models — no fine-tuning per hospital —
because the entities are canonical (USAN drug names, LOINC labs, SNOMED
conditions, common bedside device shapes).

| Retriever  | Dataset                              | License                       | What we use                                            |
| ---------- | ------------------------------------ | ----------------------------- | ------------------------------------------------------ |
| References (medications) | [DailyMed](https://dailymed.nlm.nih.gov/) (FDA SPL labels + product images) | US Government work / public   | 10 drugs, 136 LOINC-coded chunks, **100% with real product photos** from `/spls/{setid}/media` |
| References (equipment)   | [Wikipedia + Wikimedia Commons](https://en.wikipedia.org) | CC-BY-SA 4.0                  | ~23 bedside devices (infusion pump, pulse oximeter, defibrillator, insulin pen, …), thumbnails downscaled to ≤256 px |
| Events     | [Synthea v3.3](https://github.com/synthetichealth/synthea) (synthetic FHIR) | Apache 2.0                    | 25 patients (T2DM/CKD-biased) + ~917 medications, vitals, labs |
| Notes      | [MTSamples](https://huggingface.co/datasets/harishnair04/mtsamples) (medical transcriptions) | CC0 (public domain)           | 35 notes across 7 specialties (endo, nephro, discharge…) |

The References sources (DailyMed + Wikimedia) are unioned into the
`documents` collection as a single multimodal apparatus catalog where every
row has `(name, context, image)` and `category ∈ {medication, equipment}`.
This is what lets the Vision agent recognise *both* labelled medications
*and* unlabelled equipment, and what lets the dashboard show the actual
product photo next to the matched snippet.

- Fixtures live in [`data/fixtures/`](./data/fixtures/) (committed; ~2.2 MB total — ~700 KB of inlined image thumbnails).
- Ingest scripts in [`scripts/ingest_*.py`](./scripts/) re-fetch from source on demand.
- The seeder picks the most demo-relevant Synthea patient and **re-labels them
  as `P-204` "Sarah Chen"** with two hand-shaped headline events injected on
  top (metformin admin 47 h ago + eGFR=38 lab 18 h ago) so the demo lands
  deterministically. Surrounding context is real-shaped Synthea data.
- See [`DECISIONS.md`](./DECISIONS.md) entries **(e)** + **(f)** for rationale +
  [`data/README.md`](./data/README.md) for refresh workflow + license details.

## Latency target

| Path | End-of-question → first audio byte |
|---|---|
| **Streaming** (continuous capture) | **≤2 s target / 2.5 s ceiling** |
| **Streaming + warm cache** (Local Retrieval hit) | **≈1.4 s** |
| **Streaming + active follow-up** (Reranker Pass-2 fired) | **≈1.8 s** |
| **Snap & ask** (Vision in critical path) | **≤3.5 s** |

| Stage | Budget |
|---|---|
| Phone → LiveKit | 50–100 ms |
| ElevenLabs Scribe v2 Realtime STT | ~150 ms partial · ~300 ms commit |
| Vision (GPT-4o, *only* on snap path) | 1000–1500 ms |
| Router (GPT-4o-mini) | 300–500 ms |
| 3 Retrievers (parallel Mongo) — cold | 200–400 ms |
| 3 Retrievers — **Local Retrieval cache hit** | **≈5 ms** |
| Reranker Pass-1 (GPT-4o-mini) | 300–500 ms |
| Reranker Pass-2 (when active follow-up fires) | +120–250 ms LLM + 30–80 ms tools (parallel, capped at 2) |
| Answerer first token (streaming) | 200–400 ms |
| ElevenLabs Flash v2.5 first byte | 150–300 ms |
| LiveKit → phone | 50–100 ms |

> **Local + Active Retrieval.** Vision drives a per-session prefetch cache the
> moment it reads a wristband or pill label, so the Retrievers usually serve
> from in-process memory by the time the question arrives. The Reranker can
> additionally fire up to 2 targeted follow-up tool calls when it spots a
> concrete information gap (e.g. has a renal-contraindication chunk but no
> recent eGFR). Both layered on **pre-trained models** — the medical domain is
> canonical (USAN drug names, LOINC labs, MRN wristbands), so we don't need to
> fine-tune anything per hospital. See [`DECISIONS.md`](./DECISIONS.md) entry
> (d).

> **STT vendor note.** The original spec called for Deepgram Nova-2. We swapped
> to ElevenLabs Scribe v2 Realtime (Jan 2026) — same first-token latency
> (~150 ms), one fewer vendor + key, single ElevenLabs SDK for both speech
> directions. See [`DECISIONS.md`](./DECISIONS.md).

## Setup

### 0. Prereqs

- Python 3.11+
- Node 20+
- A **MongoDB Atlas Sandbox** cluster (use the hackathon link — *not* a personal Atlas).
- A **LiveKit Cloud** project (free tier).
- API keys for **OpenAI** and **ElevenLabs** (one ElevenLabs key powers both Scribe v2 Realtime STT and Flash v2.5 TTS).
- **JDK 11+** *only* if you want to refresh the Synthea fixture from source. The committed fixture works without Java.

### 1. Configure env

```bash
cp .env.example .env
# fill in LIVEKIT_*, MONGODB_URI, OPENAI_API_KEY, ELEVENLABS_API_KEY
```

### 2. Backend

```bash
cd backend
python -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements.txt
cd ..
python -m backend.mongo          # creates collections + vector indexes
python -m scripts.seed_mongo     # loads from data/fixtures/, falls back to mock if any are missing
python -m backend.main           # starts FastAPI + agent loops on :8000
# in a second terminal:
python -m backend.worker dev     # starts the LiveKit Agents worker
```

> **Atlas Vector Search** indexes can take a couple of minutes to come online
> after creation. The References retriever falls back to a `$text` search if
> the vector index isn't ready, so the system still works — just less adaptive.

### 2.1. (Optional) Refresh the dataset fixtures

The repo ships pre-built JSONL fixtures under `data/fixtures/` so the seed
step works offline. To rebuild them from the public sources:

```bash
python -m scripts.ingest_dailymed             # ~40 s — FDA SPL labels + product images (REST API)
python -m scripts.ingest_wikimedia_equipment  # ~3–5 min — Wikipedia/Commons device thumbnails (rate-limited)
python -m scripts.ingest_mtsamples            # ~10 s — HuggingFace parquet (CC0)
python -m scripts.ingest_synthea              # ~5 min — runs the Synthea jar (needs JDK 11+)
```

See [`data/README.md`](./data/README.md) for layout, licenses, and the
provenance details for all four datasets.

### 3. Dashboard

```bash
cd dashboard
npm install
npm run dev    # http://localhost:3000
```

### 4. Phone or glasses

Serve `phone/` with anything (the simplest):

```bash
cd phone
python -m http.server 8080      # then open http://<your-laptop-ip>:8080 on the phone
```

Two pages, two capture modes, same backend:

- [`phone/index.html`](./phone/index.html) — **phone fallback** (universal, "safety mode"). Announces `capture_mode="phone"`.
- [`phone/glasses.html`](./phone/glasses.html) — **Meta Ray-Ban POV** (headline). Announces `capture_mode="glasses"`. First-person framing; works as a stand-in via laptop webcam or a phone in a head strap until a real Ray-Ban Live AI → LiveKit bridge is wired up.

Important:
- The pages ask for your backend URL on first launch — point it at the laptop IP, not `localhost`.
- iOS Safari requires HTTPS for camera/mic on remote hosts. Easiest path: tether the phone to the laptop via hotspot and use `localhost` via [`localhost.run`](https://localhost.run/) or `ngrok http 8080` to wrap it in HTTPS.

### 5. Try it

- Open `http://localhost:3000` (dashboard).
- Open the phone page, hit **Connect** → picks a LiveKit room.
- Either: (a) speak a question while pointing the camera, or (b) tap **Snap & ask**, frame a pill bottle / wristband, type or speak the question.
- Watch the lanes light up: Vision → Router → Retrievers (references / events / notes) → Reranker → Answerer.
- The phone plays the answer audio back through its speaker.

## Demo question

> *"Is it safe to give this dose now? When did they last receive it?"*

Expected behavior: while the phone is pointed at a pill-bottle label reading
**METFORMIN 500 mg** with patient wristband **P-204** visible, the Router
pulls `medication="metformin"` and `patient_id="P-204"` into the retrieval
plan. The References retriever returns the metformin monograph chunk on
renal contraindications. The Events retriever returns the last administration
(~47 hours ago) and a recent eGFR=38 lab from the time-series collection.
The Notes retriever returns prior handoff notes about P-204. The Reranker
boosts the renal-contraindication chunk with a `boost_reason` citing the
visible "METFORMIN" token *and* the patient's eGFR. The Answerer says
something like:

> *"Hold this dose — P-204's most recent eGFR is 38, which is borderline for
> metformin. Last administration was 47 hours ago. Recommend rechecking renal
> function before giving."*

**Compliance language**: this is **clinical decision support, not diagnosis**.
The clinician is in the loop; the system never auto-administers anything. All
seed data is mock — no real PHI. Use that wording verbatim if a judge asks.

## MongoDB features used (8 load-bearing)

1. **Document model** — every collection (`patients`, `sessions`, `answers`, …)
2. **Atlas Vector Search** — `scene_context`, `documents`, `transcripts`
3. **Time Series collection** — `clinical_events` (`timeField=timestamp`, `metaField=patient_id`, granularity=hours)
4. **Change Streams** — the agent bus (`video_frames` → Vision; `questions` → Router; etc.)
5. **`$vectorSearch` in aggregation** — References + Notes retrievers
6. **GridFS** — `clips` bucket (24h TTL)
7. **TTL indexes** — `clips.files.uploadDate`
8. **Aggregation pipelines** — Retrievers (vector+filter+sort, recency boost) + Reranker

The dashboard sidebar lights each one up the moment it's actually exercised.

## Endpoints (backend, :8000)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness |
| POST | `/token` | LiveKit access token (phone or worker). Optional `capture_mode: "glasses" \| "phone"` body field — persisted on the session and threaded onto every `scene_context`. Defaults to `"phone"`. |
| POST | `/snap` | single-image retrieval — body `{session_id, image_b64, question?, capture_mode?}`. Runs Vision sync, optionally fires the question. |
| GET | `/scene-context/recent?seconds=30` | dashboard read |
| GET | `/trace/:question_id` | full reasoning chain |
| GET | `/answers/:question_id` | final answer text |
| POST | `/ask` | text-only pipeline kick (debug) |
| WS | `/stream` | change-stream fan-out for dashboard |

## Cuts list (ordered, if time slips)

1. Bluetooth Ray-Bans output → phone speaker
2. Real Ray-Ban capture → pre-recorded MP4 piped into LiveKit
3. Streaming Answerer → non-streaming (+500 ms)
4. Vision per-frame → scene-change-only (every ~5 s)
5. Latency monitor on dashboard (cosmetic)

Never cut Mongo features. Never cut LiveKit.

## License

MIT (hackathon prototype).
