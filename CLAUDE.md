# LiveRecall: Build Instructions

> **Amended 2026-05-02 (a):** STT vendor changed from Deepgram Nova-2 to
> **ElevenLabs Scribe v2 Realtime** (~150 ms first-token, single-vendor with
> our TTS). Latency math is unchanged.
>
> **Amended 2026-05-02 (b):** Domain pivoted from factory walkthrough to
> **point-of-care medical vision device** (clinician at the bedside or in
> clinic). Architecture, latency budget, and Mongo-feature count are
> unchanged — only the seed data, prompts, and demo scenario change.
>
> **Amended 2026-05-02 (c):** New capability: **single-image retrieval**
> (`POST /snap`). The phone has a "Snap & ask" button that captures one
> frame, runs Vision synchronously, and fires the rest of the pipeline.
> Streaming path still works in parallel.
>
> **Amended 2026-05-02 (d):** Added **Local Retrieval** (per-session in-process
> cache, prefetched by Vision when it sees a wristband or pill label —
> Retrievers serve hits in ~5 ms) and **Active Retrieval** (the Reranker may
> emit up to 2 targeted follow-up tool calls when it spots an information gap;
> a tight Pass-2 rerank folds the new facts in). All on **pre-trained models** —
> the medical domain is canonical enough (USAN drug names, LOINC labs, MRN
> wristbands) that we don't need to fine-tune anything. End-to-end stays inside
> the 2 s target on warm cache; bounded ~1.8 s when an active follow-up fires.
> See `backend/local_cache.py`, `backend/agents/active_tools.py`,
> `backend/agents/reranker.py`.
>
> **Amended 2026-05-02 (e):** Three **real public datasets** now back the
> three retrievers — **DailyMed** (FDA SPL drug labels, 136 chunks across 10
> drugs) for `documents`, **Synthea v3.3** (open-source MITRE synthetic FHIR
> patients, 25 patients + 917 events) for `patients` + `clinical_events`, and
> **MTSamples** (CC0 medical-transcription corpus, 35 notes across 7
> specialties) for `transcripts`. Ingest scripts in `scripts/ingest_*.py`
> emit JSONL fixtures under `data/fixtures/` (committed). `scripts/seed_mongo.py`
> calls `_seed_documents(dailymed, equipment)` which inserts both medication
> AND equipment rows (with `image_b64` intact) into `documents` and embeds all
> text in batches. Re-labels the most demo-relevant Synthea patient as `P-204`
> "Sarah Chen" with **three** hand-shaped headline events injected on top
> (metformin 500 mg admin ~47 h ago, eGFR=38 ~18 h ago, creatinine=1.7 ~18 h
> ago) for demo determinism. See `data/README.md`.
>
> **Amended 2026-05-02 (f):** References retriever upgraded to a **multimodal
> apparatus catalog** — every `documents` row now has shape
> `(name, context, image)` where `category ∈ {medication, equipment, other}`.
> DailyMed ingest pulls real FDA product-label photos via `/spls/{setid}/media`
> (136 medication chunks, 100% with images). `scripts/ingest_wikimedia_equipment.py`
> now fetches **110 bedside devices** across 14 clinical categories (airway,
> cardiac, vascular, monitoring, infusion, urological, dialysis, surgical,
> imaging, lab, neuro, neonatal, wound, misc) sourced from Wikipedia summaries
> + Commons thumbnails (CC-BY-SA); images downsized to 256 px JPEG + base64,
> cached under `data/cache/wikimedia/`, deduplicated by friendly name before
> fetching. `seed_mongo.py::_seed_documents()` inserts both medication and
> equipment rows with `image_b64` preserved — previously equipment `image_b64`
> was loaded from fixture but silently dropped at seed time (now fixed). Vision
> extracts an `apparatus` list (canonical lowercase names from catalog
> vocabulary) so retrieval fires for *unlabelled* equipment too, not just OCR'd
> drug names. Router can filter by `name` and `category`. Dashboard renders
> inline thumbnails next to each retrieved snippet. See
> `backend/agents/vision.py`, `backend/agents/router.py`,
> `dashboard/src/components/ReasoningTrace.tsx`.
>
> **Amended 2026-05-02 (g):** **Two capture modes** — the same Vision pipeline
> now ingests scenes from either **Meta Ray-Ban smart glasses** (first-person
> POV, hands-free, the headline product moment) **or** a plain
> **phone-browser camera** (universal fallback — every clinician on shift
> already has one, so the entire clinic adopts on day 1 with no hardware
> purchase). `shared/types.py` gains `CaptureMode = Literal["glasses",
> "phone"]` (default `"phone"`); `/token` and `/snap` accept `capture_mode`
> and the session/scene_context documents persist it. The Vision system
> prompt gets a tiny POV hint append (`_system_prompt_for(mode)`) so GPT-4o
> knows whether the frame is first-person or over-the-shoulder; the apparatus
> extraction from (f) is untouched. New `phone/glasses.html` is the Ray-Ban
> entry point (currently a first-person stand-in — laptop webcam / phone in
> head strap — with the documented swap path to a Meta Live AI → LiveKit
> ingress bridge). The dashboard shows a `GLASSES` (purple/blue) or `PHONE`
> (slate, "fallback" tooltip) pill near the session header. Latency budget
> and Mongo-feature count unchanged. See `DECISIONS.md` (g) and (f).
>
> Rationales live in `DECISIONS.md`. Everything else here is the source of truth.

You are building **LiveRecall**, an adaptive clinical-context retrieval system grounded in real-time visual memory. Read this whole file before writing code. Do not deviate from the architecture without asking.

## What we're building

A wearable point-of-care assistant for clinicians. Ray-Ban Meta v2 glasses (or phone-as-capture) stream audio + video via LiveKit to a backend. A Vision agent extracts structured `scene_context` from frames — drug names, patient identifiers, devices, environment. When the clinician asks a question, a Router agent uses *recent scene context* + the question to construct adaptive queries across multiple clinical sources. Retrievers run hybrid search in parallel. A Reranker reweights results based on what the clinician just saw (the medication on the cart, the wristband on the patient). An Answerer speaks the response back through the same LiveKit room.

There are two ways in:
1. **Continuous capture** — glasses or phone stream POV video; the system passively builds scene context as the clinician moves around.
2. **Single-image snap** — one tap captures one frame, runs Vision synchronously, and the next question is grounded against just that frame. This is the lower-friction interaction for "I'm looking at this pill bottle right now."

The product is **adaptive clinical retrieval grounded in real-time visual memory**, with **MongoDB Atlas as the substrate** and **LiveKit as the real-time transport**.

## Framing

Pitch as: *"Clinical decision support grounded in live visual memory. Multi-source agentic retrieval that reweights results based on what the clinician is looking at right now."*

Never as: "RAG with glasses," "image analyzer," "multimodal RAG," "AI doctor." This is **decision support**, not diagnosis. The clinician is in the loop.

## Hard constraints

- **Time budget: 6 hours of focused build, then submission.**
- **Latency target: <2 seconds end-to-end** from end-of-question to start-of-answer-audio.
- **MongoDB Atlas (Sandbox cluster) is the substrate.** Vector Search, Time Series, Change Streams, GridFS, $vectorSearch in aggregation, all load-bearing.
- **LiveKit is the transport.** Phone publishes audio + video to a LiveKit room, backend agent worker subscribes, TTS publishes back as audio track.
- **Theme: Adaptive Retrieval.** Every architectural choice serves "modify query approaches, alter chunking, reorder results based on input."

## Latency budget (target <2s, hard ceiling 3s)

| Stage | Budget |
|---|---|
| Phone mic → LiveKit ingress | 50-100 ms |
| Streaming STT (ElevenLabs Scribe v2 Realtime) | 150-300 ms |
| Router (GPT-4o-mini) | 300-500 ms |
| 3 Retrievers in parallel (Mongo) | 200-400 ms |
| Reranker (GPT-4o-mini, single call) | 300-500 ms |
| Answerer (GPT-4o-mini, streaming) | 200-400 ms first token |
| ElevenLabs TTS first byte (Flash v2.5) | 150-300 ms |
| LiveKit egress → phone playback | 50-100 ms |
| **Total: end-of-question to first audio byte** | **~1.5-2.5 s** |

**Latency-driven model choices:** GPT-4o-mini everywhere except where quality genuinely matters. Streaming everywhere it's available. ElevenLabs Scribe v2 Realtime over Whisper (Whisper is non-streaming, adds 1-2s; Scribe v2 Realtime delivers ~150ms first-token, comparable to Deepgram Nova-3, with the bonus that it's the same vendor as our TTS). Flash TTS over standard.

## Team

Four people, 6 hours.

- **Kazybek (Kalle)** — Stream A: Capture + LiveKit + audio I/O + demo
- **Teammate 2** — Stream B: Agent layer (LangChain + 5 agents)
- **Teammate 3** (Mongo-comfortable full-stack) — Stream C: Mongo + retrieval pipeline + STT/TTS
- **Teammate 4** (full-stack swing) — Stream D: Dashboard + integration glue

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Capture (POV + audio) | Phone browser → LiveKit | Sub-second, reliable, Ray-Bans as form factor + Bluetooth audio |
| Output (audio) | LiveKit audio track → phone → Ray-Bans Bluetooth | Clean WebRTC path back |
| Real-time transport | **LiveKit Cloud** | Free tier, managed SFU, agent SDK |
| Backend | Python (FastAPI + LiveKit Agents SDK) | Single process, agent worker joins LiveKit room |
| Database | **MongoDB Atlas Sandbox** (M10) | All vector + time series + change streams in one place |
| Embeddings | OpenAI text-embedding-3-small | 1536-dim, fast, Atlas Vector Search compatible |
| **STT** | **ElevenLabs Scribe v2 Realtime** | ~150ms first-token over WebSocket; same vendor as our TTS so one key + one SDK + one rate limit |
| **TTS** | **ElevenLabs Flash v2.5** | ~150ms first-byte latency, optimized for real-time |
| Agent framework | **LangChain (langchain-core + langgraph)** | Tool calling, agent loops, streaming, callbacks for tracing |
| Vision | GPT-4o (vision) | Multimodal native, structured JSON output |
| LLM (other agents) | GPT-4o-mini | 3-5x faster than 4o, sufficient quality for routing/reranking/answering at this latency budget |
| Dashboard | Next.js + Tailwind + WebSocket | Subscribes to Mongo change streams |

## AGENT MODELS (read carefully)

Five agents. Three different models. Choices are deliberate, biased toward latency.

| Agent | Model | Streaming | Why this choice |
|---|---|---|---|
| **Vision** | **GPT-4o** (vision) | No (frame-by-frame) | Only model with strong visual structured-output reliability. Runs on frame ingestion, not in question hot path, so its 1-2s latency is offline. |
| **Router** | **GPT-4o-mini** | No (single short JSON) | Routing is a small-token decision; mini is 3-5x faster than 4o, accuracy diff is negligible for this task. |
| **Retrievers (3 of them)** | **No LLM** | N/A | Pure Mongo aggregation pipelines. Zero LLM latency. |
| **Reranker** | **GPT-4o-mini** | No (short JSON) | Reranking 5-15 results is small-token. Mini handles it well, 4o adds 800ms for marginal quality gain. |
| **Answerer** | **GPT-4o-mini** (streaming) | **Yes, token-by-token** | Streamed tokens go to TTS which streams audio. First audio byte fires at ~200ms. |

**Why mini over 4o for non-vision agents:**
- Latency budget is 2s end-to-end. 4o averages 800-1200ms per call, mini averages 200-400ms. The math doesn't work with 4o.
- For routing decisions, reranking, and short conversational answers, mini quality is within noise of 4o.
- The Vision agent gets 4o because visual structured extraction is where mini falls short.

**If quality regresses noticeably during testing, escalate Reranker to 4o first** (it's the most quality-sensitive of the mini-using agents). Don't escalate Router or Answerer.

**Why LangChain:**
- LangChain `ChatOpenAI` and `ChatAnthropic` have built-in streaming + tool calling
- `langgraph` gives you typed agent state and checkpointing if you need it
- LangChain callbacks make it easy to log to MongoDB for the dashboard trace
- `langchain-mongodb` package wraps Atlas Vector Search natively (`MongoDBAtlasVectorSearch`)
- Saves time vs. hand-rolling tool-call loops

**LangChain components used:**
```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableParallel
from langchain_core.callbacks import BaseCallbackHandler
from langchain_mongodb import MongoDBAtlasVectorSearch
from langgraph.graph import StateGraph
```

## Architecture

Two entry paths. One Mongo bus. Five agents, each a change-stream subscriber.
One pre-published outbound audio track. One observer (the dashboard).

```
INGEST ─ two parallel entry paths, both land in the same collections
────────────────────────────────────────────────────────────────────
  A) Streaming  (Meta Ray-Ban OR phone browser → LiveKit room)
        audio ─► worker subscribes ─► Scribe v2 Realtime STT
                                          │
                                          ├─► transcripts        (every commit)
                                          └─► questions          (when is_question)
        video ─► worker subscribes ─► 1 fps sampler
                                          └─► video_frames

  B) Snap       (phone "Snap & ask"  →  POST /snap)
        image ─► Vision (synchronous on this path)
                    ├─► scene_context     (immediately)
                    └─► questions         (only if a question text was sent)


BUS ─ MongoDB Atlas. Every agent step is a Mongo write; every agent runs
       off a watch() change-stream subscription on the collection it cares about.
─────────────────────────────────────────────────────────────────────────────
                                    ┌─────────────────────────────────────┐
   subscribes to  ───────►  agent   │   writes  ───────►  collection      │
                                    │                                     │
   video_frames    ─►   Vision      │  GPT-4o     ─► scene_context        │
                                    │              + warms SessionCache   │
                                    │                (side channel,       │
                                    │                 in-process,         │
                                    │                 keyed on patient_id │
                                    │                 + apparatus name)   │
                                    │                                     │
   questions       ─►   Router      │  4o-mini    ─► retrieval_plans      │
                                    │  (reads last 30 s of scene_context) │
                                    │                                     │
   retrieval_plans ─►   Retrievers  │  no LLM, asyncio.gather:            │
                                    │   • references  ($vectorSearch on   │
                                    │      multimodal catalog;            │
                                    │      cache-first ~5 ms)             │
                                    │   • events     (Time Series +       │
                                    │      patient_id filter; cache-first)│
                                    │   • notes      ($vectorSearch +     │
                                    │      recency boost)                 │
                                    │             ─► retrieval_results    │
                                    │                                     │
   retrieval_plans ─►   Reranker    │  4o-mini, awaits the 3-result       │
                                    │  bundle (≤4 s).                     │
                                    │  Pass 1: ranks; may emit ≤2         │
                                    │          active_followups           │
                                    │  Active tools (parallel, 30–80 ms): │
                                    │   get_latest_lab,                   │
                                    │   get_last_administration,          │
                                    │   get_monograph_section             │
                                    │  Pass 2 (only if followups fired):  │
                                    │          re-ranks with new facts    │
                                    │             ─► final_context        │
                                    │                                     │
   final_context   ─►   Answerer    │  4o-mini STREAMING ─► answers       │
                                    │             ─► tokens (below)       │
                                    └─────────────────────────────────────┘


OUTPUT ─ one outbound audio track, pre-published at room-join, reused
         per answer. Tokens stream straight into it.
─────────────────────────────────────────────────────────────────────
   Answerer tokens
        ─► ElevenLabs Flash v2.5 (websocket, PCM 16 k mono)
            ─► capture_frame() into the pre-published rtc.AudioSource
                ─► LiveKit room  ─► phone speaker  (paired BT optional)


OBSERVABILITY ─ separate lane, never on the question hot path.
──────────────────────────────────────────────────────────────
   every agent ─► agent_traces  (MongoTraceCallback wraps every LLM call)

   dashboard ◄─ WS /stream  (multiplexes change streams across the
                              9 dashboard collections, throttled 10/s/coll)
```

Every state change is a Mongo write. Agents communicate only via change
streams — no in-process function calls between agents — so the dashboard
sees exactly what the pipeline sees, and any agent can be restarted in
isolation without losing in-flight work.

Two non-obvious wires worth knowing:

- **`questions` is the Router's trigger, not `transcripts`.** STT writes
  every committed transcript, but only utterances that pass `is_question()`
  also create a `questions` doc. This keeps idle chatter from triggering the
  retrieval pipeline. The same collection is the join point for `/snap`
  (with a question), `/ask` (text-only debug), and the streaming STT path.
- **Reranker watches `retrieval_plans`, not `retrieval_results`.** It then
  awaits all 3 result writes for the same `plan_id` (with a 4 s ceiling)
  before ranking once. This is why there's exactly one Reranker call per
  question even though there are 3 retrievers.

## Mongo collections

| Collection | Type | Purpose | Mongo feature |
|---|---|---|---|
| `sessions` | standard | One per LiveKit room session | Document model |
| `clips` | GridFS | Raw video chunks (24h TTL) | GridFS + TTL index |
| `video_frames` | standard | Sampled frames (continuous + snap path) | Change streams |
| `scene_context` | standard + Vector | Vision output (objects, **apparatus**, text_visible, embedding) | Atlas Vector Search |
| `transcripts` | standard + Vector | Live STT output + past clinical notes (seeded from **MTSamples**) | Atlas Vector Search |
| `documents` | standard + Vector | **Multimodal apparatus catalog** — `(name, context, image)` rows, `category ∈ {medication, equipment, other}`. Medication rows (136 chunks, 10 drugs) seeded from **DailyMed FDA SPL** with real product photos. Equipment rows (**110 entries**, 14 clinical categories) seeded from **Wikimedia Commons + Wikipedia** (CC-BY-SA) with device photos. Both inserted by `_seed_documents()` with `image_b64` intact. | Atlas Vector Search |
| `clinical_events` | **Time Series** | Per-patient timeline (vitals, meds, labs, notes) — seeded from **Synthea** | Time Series collection |
| `patients` | standard | Patient master records — seeded from **Synthea** (1 re-labelled as P-204 for demo) | Document model |
| `retrieval_plans` | standard | Router output | Change streams |
| `retrieval_results` | standard | Per-Retriever output | Change streams |
| `final_context` | standard | Reranker output | Aggregation pipelines |
| `answers` | standard | Final responses | Document model |

**Mongo features (target 8 load-bearing):**
- [x] Document model
- [x] Atlas Vector Search (3 collections)
- [x] Time Series collection
- [x] Change Streams (agent bus)
- [x] $vectorSearch in aggregation (Retrievers do hybrid: vector + filter + sort)
- [x] GridFS
- [x] TTL indexes
- [x] Aggregation pipelines (Reranker, recency boost)

## API contract (lock first 30 min)

```python
# shared/types.py (or .ts on dashboard)

class SceneContext:
    _id: str
    session_id: str
    timestamp: int  # unix ms
    source_frame_id: str
    objects: list[str]              # ["pill bottle", "wristband", "IV pump"]
    text_visible: list[str]         # ["METFORMIN 500 mg", "P-204", "eGFR 38"]
    environment: str                # "hospital_room" | "clinic" | "pharmacy" | ...
    activity: str                   # "reviewing medication"
    text_embedding: list[float]     # 1536-dim

class RetrievalPlan:
    _id: str
    question_id: str
    question_text: str
    scene_context_ids: list[str]
    queries: list[dict]             # [{source, filter, vector_query, weight}]

class RetrievalResult:
    _id: str
    plan_id: str
    source: str
    results: list[dict]             # [{document_id, score, snippet, metadata}]

class FinalContext:
    _id: str
    question_id: str
    ranked_results: list[dict]      # [{snippet, source, boosted_score, boost_reason}]

class Answer:
    _id: str
    question_id: str
    text: str
    confidence: float
    citations: list[str]
    audio_track_id: str             # LiveKit track published back
```

### Backend endpoints (REST + LiveKit room events)

- LiveKit room `liverecall-{session_id}` is the primary streaming interface
- `POST /snap` → `{session_id, image_b64, question?}` → runs Vision sync, optionally creates a `questions` doc. Used by the phone "Snap & ask" button and by the dashboard's image-upload tester.
- `GET /scene-context/recent?seconds=30` → JSON for dashboard
- `GET /trace/:question_id` → full reasoning chain for dashboard
- `WS /stream` → dashboard subscribes, change stream events fan out

## Build order (parallel, 6 hours)

### Hour 0 (kickoff, all four together, 30 min)

1. Lock `shared/types.py`, push public GitHub.
2. **Atlas Sandbox cluster** from the provided link (NOT a personal Atlas).
3. **LiveKit Cloud project** spun up, API key + URL in shared `.env`.
4. **ElevenLabs API key** in `.env` — one key powers both Scribe v2 Realtime STT *and* Flash v2.5 TTS. (Apply for ElevenLabs Creator tier early if not already.)
5. OpenAI API key in `.env`.
6. Decide demo scenario: bedside medication safety check (clinician + pill bottle + patient wristband).
7. **Confirm at least one teammate available May 7 for follow-up event.** If not, replan team commitment.

### Hours 1-6: parallel streams

See TEAM_SPLIT.md for hourly task lists. Sync points at +1, +2.5, +4, +5, +5.5 hours.

### Final 45 minutes: video production + submission

1. Record 1-min video showing dashboard + LiveKit live capture + agent flow.
2. Voiceover via ElevenLabs (Flash v2.5 for the demo, regular voice for the video).
3. Push final commit, public repo confirmed.
4. Submit form.

## Demo scenario

**Pre-recorded clinical clips** (Kazybek, by hour 1) — staged on a desk with mock-up assets, no real PHI:
1. Pill bottle close-up: label clearly readable as "METFORMIN 500 mg"
2. Patient wristband: visible MRN "P-204"
3. Wider shot of a med cart with the bottle + bedside table
4. Close-up of a vital-signs monitor mock-up showing eGFR / creatinine
5. A second pill bottle in the background ("LISINOPRIL 10 mg") for the multi-drug variant

**Seed Mongo** (Stream C, by hour 2):
- 5 patients (P-201..P-205) with allergies + active conditions; **P-204 "Sarah Chen" has chronic kidney disease and a recent eGFR=38 lab** (most demo-relevant Synthea patient re-labelled)
- Clinical events in the Time Series collection (Synthea-sourced + 3 hand-shaped headline events): metformin 500 mg admin **47 h ago**, eGFR=38 **18 h ago**, creatinine=1.7 **18 h ago**
- `documents` collection: DailyMed 136 medication chunks (10 drugs, with product photos) + Wikimedia **110 equipment entries** (with device photos) — all with `image_b64` + `text_embedding`
- Past clinical handoff notes seeded from MTSamples (CC0) via `_seed_past_notes()`

**Demo question**: *"Is it safe to give this dose now? When did they last receive it?"*

**Expected answer**: System sees `METFORMIN 500 mg` + `P-204` in scene. Router queries: drug references for metformin, time-series events filtered by `patient_id=P-204`, past notes about P-204. Reranker boosts the renal-contraindication chunk because the patient's recent `eGFR 38` event is also in context. Answerer says something like:

> *"Hold this dose — P-204's most recent eGFR is 38, which is below the 45 threshold for metformin. Last administration was 47 hours ago. Recommend rechecking renal function before giving."*

This is the headline rubric moment: the visual signal (the pill bottle on the cart + the wristband) **reweights** the answer — without seeing them, the system would just regurgitate the monograph.

## Demo script (3 min live)

1. (0:00-0:20) "Clinical decision support grounded in live visual memory. The clinician looks at what they're about to act on, and the system adapts retrieval to it."
2. (0:20-0:40) Phone in hand pointed at the pill bottle / wristband clip. LiveKit room is live. Dashboard shows frames streaming, Vision agent firing, `scene_context` populating with `METFORMIN 500 mg` and `P-204`.
3. (0:40-0:55) Speak question into phone: *"Is it safe to give this dose now?"*
4. (0:55-1:25) Dashboard: Router reads recent `scene_context` + question, fans out to 3 Retrievers in parallel — references, time-series events, past notes. Aggregation pipelines visible.
5. (1:25-1:55) Reranker reweights using visual signal. `boost_reason` cites the visible drug name and the patient's eGFR.
6. (1:55-2:15) ElevenLabs voice plays the cautionary answer through the phone speaker.
7. (2:15-2:35) **Single-image variant**: tap *"Snap & ask"* on the phone, frame the second bottle (Lisinopril), say *"any interaction?"* — show that one image is enough.
8. (2:35-2:50) Show the "Mongo features used" sidebar — all 8 highlighted — and the latency monitor under 2.5s.
9. (2:50-3:00) "Adaptive clinical retrieval. Live visual memory. MongoDB and LiveKit as the brain and the bloodstream."

**Compliance note for the pitch:** this is **decision support**, not a diagnosis or a prescription. The clinician acts on the recommendation; the system never auto-administers anything. Use that language verbatim if a judge asks about safety/regulation.

## Conventions

- Python type hints on backend, TypeScript strict on dashboard.
- All timestamps unix ms.
- Errors: throw, don't silently catch.
- Public GitHub repo from minute one.
- Commit after each task slot with the slot label.

## Don'ts

- Do NOT pitch as "RAG", "image analyzer", "AI doctor", or "diagnosis." This is **clinical decision support**.
- Do NOT use real PHI in seeds, logs, or screenshots. Mock patients only (P-201..P-205).
- Do NOT use Streamlit.
- Do NOT use a personal Atlas account. Use the sandbox link.
- Do NOT use Pinecone, Redis, or Kafka. Mongo only.
- Do NOT use GPT-4o for Router, Reranker, or Answerer. Mini only. Latency budget doesn't allow it.
- Do NOT use Whisper or any non-streaming STT. ElevenLabs Scribe v2 Realtime over WebSocket only.
- Do NOT use ElevenLabs Multilingual or Turbo for the live demo. Flash v2.5 only.
- Do NOT skip change streams. Agent message bus.
- Do NOT let any agent loop forever. Max 3 tool-call iterations (latency budget).

## Definition of done

- [ ] Phone joins LiveKit room, publishes audio + video
- [ ] Phone "Snap & ask" button captures a single frame and POSTs to `/snap`
- [ ] Backend agent worker subscribes, runs Scribe v2 Realtime STT and frame sampling
- [ ] `POST /snap` runs Vision synchronously and (optionally) creates a `questions` doc
- [ ] Vision agent (GPT-4o) writes scene_context with embeddings (drug names, MRNs, vitals)
- [ ] Router (GPT-4o-mini) reads question + scene, writes retrieval_plan with `patient_id` filter when wristband visible
- [ ] 3 Retrievers run hybrid Mongo aggregation in parallel (references, events, notes)
- [ ] Reranker (GPT-4o-mini) reweights using visual signal, boost reasons cite visible drug + MRN when present
- [ ] Answerer (GPT-4o-mini, streaming) generates response token-by-token; uses cautious "decision support" tone
- [ ] ElevenLabs Flash v2.5 streams TTS audio chunks
- [ ] LiveKit publishes audio back to room, phone plays through speaker
- [ ] Dashboard shows live pipeline + Mongo features highlighted + reasoning trace
- [ ] End-to-end latency ≤2.5s on streaming path; snap path ≤3.5s (Vision is in the critical path)
- [ ] At least 8 Mongo features demonstrably load-bearing
- [ ] LiveKit visibly handling real-time transport
- [ ] Public GitHub repo with README

## When you're stuck

Stop, post one-line blocker in team channel. Cuts in priority order:

1. Bluetooth audio to Ray-Bans (fall back to phone speaker)
2. Live video on stage (fall back to pre-recorded MP4 piped into LiveKit room as a virtual camera source)
3. Vision agent on every frame (fall back to scene-change-only frames, every ~5s)
4. Streaming Answerer (fall back to non-streaming, accept +500ms latency)
5. CLIP visual embeddings (already cut, text-only)

Never cut Mongo features. Never cut LiveKit (it's the second pillar of the pitch).

If latency budget blows past 3s, escalate one agent at a time:
- First: drop Vision agent re-runs on every frame, keep last scene_context for up to 10s
- Second: drop the Reranker, use raw retrieval scores
- Last resort: drop one Retriever (lose multi-source story, big rubric hit)
