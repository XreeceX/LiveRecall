# Decisions log

Deltas from the original `CLAUDE (1).md`, with rationale. The amended sections
of `CLAUDE (1).md` and `TEAM_SPLIT.md` are now the source of truth; this file
explains *why* those amendments exist. Each entry is dated and labelled
**Reversible** (cheap to undo) or **Sticky** (touches multiple modules).

---

## 2026-05-02 (g) · Two capture modes: Ray-Ban preferred, phone as universal fallback

**Reversible.** Adds one shared-types literal (`CaptureMode`), one query/body
param on `/token` and `/snap`, a tiny POV-hint append in the Vision system
prompt, a new `phone/glasses.html` entry point, and a header pill on the
dashboard. The whole change is purely additive on the wire — old clients that
don't send `capture_mode` get the safe `"phone"` default and behave exactly as
before.

### What changed

- `shared/types.py` + `shared/types.ts`: new `CaptureMode = Literal["glasses",
  "phone"]` and a `DEFAULT_CAPTURE_MODE = "phone"`. `Session` and
  `SceneContext` gain an optional `capture_mode` field. Other fields untouched
  — coexists with the multimodal apparatus catalog (entry (f)).
- `backend/main.py`: `/token` and `/snap` accept `capture_mode`. `/token`
  upserts it onto the `sessions` document at session creation. `/snap` uses
  the explicit override, falls back to the session's stored mode, then to the
  phone default.
- `backend/worker.py`: session upsert switched to `$setOnInsert` for the
  fields the token endpoint already wrote, so the worker joining a room
  doesn't blow away the capture_mode that `/token` set.
- `backend/agents/vision.py`: SYSTEM is unchanged — we **append** a tiny POV
  hint at call time via `_system_prompt_for(capture_mode)`. Glasses → "scene
  comes from glasses (first-person, the clinician's natural view)"; phone →
  "scene comes from a phone held by the clinician or a colleague at bedside."
  `extract_scene` and `process_frame` thread `capture_mode` through, with a
  small in-process session→capture_mode cache so the per-frame loop doesn't
  round-trip Mongo. Every `scene_context` insert is now stamped with
  `capture_mode`.
- `phone/index.html`: marked as **Phone · fallback** with a slate badge in
  the header. Announces `capture_mode="phone"` on both `/token` and `/snap`.
- `phone/glasses.html` (new): Ray-Ban / first-person POV entry point. Wider
  16:9 viewport with a subtle reticle to evoke the headline POV; purple/blue
  accent. Announces `capture_mode="glasses"`.
- `dashboard/src/components/CaptureModePill.tsx` (new): small pill near the
  session header that reads `capture_mode` off the most recent
  `scene_context` event. GLASSES = purple/blue gradient, PHONE = slate with a
  "fallback" tooltip.

### Why two modes

Single-source-of-truth pitch line: **"Phone camera is safety, Meta Ray-Ban is
preferable, but also entire clinic staff likely to have phone."** Two
non-overlapping reasons make this a load-bearing product choice rather than
just a UX nicety:

1. **Glasses are the headline POV.** Bedside medication safety is a
   hands-free workflow — the clinician picks up the bottle, looks at the
   wristband, hangs the IV, all while talking. First-person video is the
   clinician's *actual field of view*. That's the moment we want to pitch:
   "the system sees what the clinician sees."
2. **Phones unlock 100% staff adoption with no hardware cost.** A clinic
   buying Ray-Bans for every clinician is a procurement project; a clinic
   pointing every clinician at a URL is a Tuesday. The phone path is what
   actually makes this thing deployable in week 1, even if the headline demo
   uses glasses.

### What we built now vs what a real Ray-Ban integration needs

We could not wire up Meta's actual Ray-Ban Live AI SDK in a hackathon — the
Live AI APIs are still partially gated and the View app does not expose a
public WebRTC publish endpoint we could ingest from in a 6-hour build. So
`phone/glasses.html` is a **stand-in**: it captures from any
first-person-framed local camera (laptop webcam, phone in a head strap, an
actual Ray-Ban screen mirror) and announces `capture_mode="glasses"` so the
Vision agent uses the first-person POV hint. The rest of the pipeline is
identical to the phone path.

Path to a real integration:

1. **Ray-Ban Live AI → LiveKit ingress.** Meta's Live AI streams audio +
   video over their proprietary connection. The cleanest seam is to bridge
   that into a LiveKit room as a publishing participant — either via a
   server-side adapter (Live AI WebSocket → `livekit-server-sdk` `IngressClient`)
   or a small native iOS/Android app that re-broadcasts the AR-eye stream as
   a standard WebRTC publish. Backend doesn't change: the worker is already
   subscribing to whatever audio + video tracks land in the room.
2. **`capture_mode="glasses"` on `/token`.** Already shipped — the bridge
   just calls `/token` with `capture_mode="glasses"` for its identity, the
   session document is stamped, and Vision's POV hint flips automatically.
3. **(Optional) Ray-Ban-specific UX.** Glasses-side UI affordances (wake
   word, gaze targets) live in the bridge app, not in this repo. The
   backend stays POV-source-agnostic.

So the work we do today is the *contract* with the real glasses path — the
swap is a bridge component, not a rewrite.

### Compliance

Both modes are subject to the **same de-identified-by-design rules** as the
rest of the system: mock patients only (P-201..P-205), no real PHI in seeds
or screenshots, decision support not diagnosis. The capture device does not
change the data-handling story.

### Pitch language

> *"Two ways the clinician's view enters the system. Meta Ray-Ban for the
> hands-free first-person headline — that's the bedside-medication-safety
> moment we're optimising for. And a plain phone-browser page as the
> universal fallback, because every clinician on shift already has one in
> their pocket. Same Vision agent, same retrievers, same answer pipeline.
> Day-1 adoption from the phone path; the wearable upgrade is purely
> upside."*

---

## 2026-05-02 (f) · Multimodal apparatus catalog: `name + context + image`

**Sticky.** Pivots the References retriever from a text-only drug monograph
corpus to a unified **multimodal apparatus catalog** where every row carries
`(name, context, image)`. Touches the ingest scripts, the `documents` schema,
the Vision and Router prompts, the Retrievers, and the dashboard.

### Why

The system's core job is to recognise the *physical things* a clinician is
looking at: a pill bottle on a med cart, a wristband on a wrist, an infusion
pump on a pole. Two failure modes the previous text-only catalog couldn't
handle:

1. **Unlabelled equipment.** A clinician points the camera at an IV pump;
   there's no OCR-legible drug name to match. The old References retriever
   could only return drug monograph text, so equipment was effectively
   invisible to retrieval.
2. **No visual confirmation.** Even when OCR succeeded ("METFORMIN 500 mg"),
   the dashboard could only show the matched monograph paragraph. The
   clinician (and the demo audience) had no way to see whether the *thing*
   the system thinks it sees matches the thing in front of the camera.

The fix is to make the References catalog look like the world: each row is
`(canonical name, clinical context, image)`.

### What changed

**1. DailyMed ingest now pulls product images.** `scripts/ingest_dailymed.py`
fetches `/spls/{setid}/media.json` for every SPL, picks the actual product
photo (skips chemical-structure diagrams), downscales to ≤256 px JPEG, and
base64-encodes it onto every chunk for that drug. Result: 136 medication
chunks across 10 drugs, each carrying the real FDA-published label image
(e.g. NDC 51655-555-96 metformin bottle).

**2. New Wikimedia equipment ingest.** `scripts/ingest_wikimedia_equipment.py`
walks a curated list of ~25 bedside apparatus (`infusion pump`,
`vital signs monitor`, `pulse oximeter`, `defibrillator`, `glucose meter`,
`syringe`, `insulin pen`, `patient wristband`, etc.). For each, it hits
Wikipedia's REST `/page/summary/{title}` endpoint and downloads both the
intro paragraph (CC-BY-SA text) and the thumbnail image. Result: ~23 device
entries with photos, written to `data/fixtures/wikimedia_equipment_sample.jsonl`.
Wikipedia's CDN aggressively rate-limits original-resolution requests, so
the script pulls the pre-cached thumbnail URL (per their own guidance).

**3. Unified `documents` schema.** Every catalog row now carries:

```
{
  name: "metformin" | "infusion pump" | …,
  category: "medication" | "equipment" | "other",
  text: <context paragraph>,
  text_embedding: [float; 1536],
  source_doc: <provenance line>,
  image_b64?: <downsized JPEG, base64>,
  image_mime?: "image/jpeg",
  image_attribution?: <author + license>,
  image_source_url?: <original DailyMed/Commons URL>
}
```

The legacy `medication` field stays populated for medication rows so the
existing active-retrieval tools (`get_monograph_section`, etc.) keep working
unchanged.

**4. Vision recognises apparatus, not just text.** The Vision agent's prompt
now extracts a third structured field, `apparatus`, populated with
**canonical lowercase names from the catalog vocabulary** when it identifies
a physical object by shape (e.g. an unlabelled pump → `["infusion pump"]`).
This lets retrieval fire even when OCR has nothing to chew on. Vision also
schedules local-cache prefetches for every recognised apparatus name, not
just OCR'd drug names.

**5. Router can filter by `name` + `category`.** The references query now
accepts `filter.name` (canonical apparatus name) and `filter.category`
(`medication` | `equipment`). When `apparatus` is non-empty in scene context,
the Router targets the most relevant entry directly — a much tighter
retrieval than vector-search-only.

**6. Dashboard shows the photo.** `ReasoningTrace.tsx` renders inline
thumbnails (40 px in the per-source hits panel, 64 px in the reranker
results) alongside every snippet that carries an image. Each thumbnail is
`<img src="data:image/jpeg;base64,…">` with the attribution string in the
`title` tooltip. Equipment vs. medication is distinguished with coloured
pills (`EQUIPMENT` blue, `MED` magenta). `ScenePanel.tsx` surfaces the
recognised `apparatus` list as inline blue chips so the audience can see
the recognition layer working in real time.

### Why pre-trained vision is enough (no fine-tuning)

The catalog vocabulary is bounded and canonical: ~10 medications by USAN
name, ~25 device classes by Wikipedia title. GPT-4o has seen all of these
in training corpora hundreds of thousands of times. We don't need a custom
classifier — we need a **gazetteer-style allowlist** in the prompt, which
is exactly what the apparatus list in `vision.py` provides. Recognition
quality is bounded by GPT-4o's vision capability, not by training data
specific to our deployment.

This is the same argument as decision (d): pre-trained models suffice when
the entities are canonical. We extended the argument from "drug names are
canonical" to "common bedside equipment shapes are canonical too."

### What we gave up

- **Fixture size.** Catalog grew from ~250 KB text-only to ~2.1 MB with
  inlined base64 thumbnails. Acceptable — still trivially commitable, the
  whole catalog (~159 rows) fits in two JSONL files under 2.2 MB total.
- **Wikimedia rate limits.** Common's `upload.wikimedia.org` enforces a
  per-IP burst limit on full-size image requests. We pull thumbnails per
  Wikimedia's documented guidance; even so, the ingest script needs
  retry-with-backoff. Run time: ~3–5 min for 25 entries on a fresh cache.
- **Image-similarity retrieval.** We deliberately did NOT add CLIP or
  another image-embedding model. Images are stored for visual confirmation
  + Vision-side recognition; vector retrieval still runs on the text
  embedding of the `(name + section + text)` blob. Adding image embeddings
  is a clean follow-up if needed.

### Acceptance

- `documents` collection holds 159 catalog rows: 136 medication (with
  product images) + 23 equipment (19 with device images).
- Reranker results panel shows the FDA metformin bottle photo next to the
  contraindication snippet on the demo question.
- ScenePanel shows `apparatus: [metformin, patient wristband]` chips
  during the bedside demo.
- All other latency / accuracy targets from (d) hold.

---

## 2026-05-02 (e) · Three real public datasets, one per retriever

**Sticky.** Adds a `data/` directory + three ingest scripts in `scripts/` and
rewires `scripts/seed_mongo.py` to prefer the real fixtures over inline mock
data. The mock data path remains as the fallback so the demo never breaks if
fixtures are missing.

### What changed

| Retriever | Dataset                              | License                       | Footprint                |
| --------- | ------------------------------------ | ----------------------------- | ------------------------ |
| References| **DailyMed** (FDA SPL drug labels)   | US Government work / public   | 136 chunks across 10 drugs |
| Events    | **Synthea v3.3** (synthetic FHIR)    | Apache 2.0                    | 25 patients + 917 events |
| Notes     | **MTSamples** (medical transcriptions)| CC0 (public domain)           | 35 notes across 7 specialties |

Three new ingest scripts (`scripts/ingest_dailymed.py`,
`scripts/ingest_synthea.py`, `scripts/ingest_mtsamples.py`) fetch from the
public sources and emit JSONL fixtures under `data/fixtures/`. The fixtures
are committed to the repo so the demo runs offline. The `data/cache/`
directory holds raw downloads and is gitignored.

`scripts/seed_mongo.py` was rewritten to:
- prefer fixtures when present, fall back to inline mock when missing,
- pick the most demo-relevant Synthea patient (T2DM + CKD bias) and re-label
  them as `P-204` with mock name "Sarah Chen",
- inject two hand-shaped headline events on top of that patient's real
  Synthea events: a metformin 500 mg administration ~47 h ago and an
  eGFR = 38 mL/min/1.73m² lab result ~18 h ago,
- log a per-collection provenance summary at end of run.

### Why these three specifically

The medical-domain pivot (entry b) only works without fine-tuning because the
**entities are canonical**. These three datasets are exactly the three
canonical sources a real clinical-decision-support stack would draw on:

| Source     | What's canonical about it                                     |
| ---------- | ------------------------------------------------------------- |
| DailyMed   | FDA-approved labels, USAN drug names, LOINC-coded sections    |
| Synthea    | SNOMED-coded conditions, RxNorm-coded meds, LOINC-coded labs  |
| MTSamples  | Real-style clinical-dictation conventions, specialty taxonomy |

Pre-trained models (GPT-4o, text-embedding-3-small, Scribe v2 Realtime) read
all three zero-shot because the standards bodies (FDA, NLM, MITRE, ASTM,
ISMP) have already done the standardisation work. *That* is what makes the
"no fine-tuning per hospital" pitch defensible — and what makes the medical
domain a fundamentally different category from factory deployments where
each plant ships bespoke equipment.

### Why the headline P-204 events are still hand-shaped

For demo determinism. The Synthea-generated patient gives us 30+ realistic
events as background context, but we need the metformin admin + eGFR=38
events to land on a **fixed wall-clock offset** so the Reranker reliably
spots the 47-hour-old administration and the 18-hour-old lab result. Mixing
real-shaped and hand-shaped data is normal practice in clinical demos
(MIMIC-IV uses similar hand-shaped events for evaluation tasks).

### What we gave up

- A few hundred KB in repo size (586 KB of fixtures committed).
- One-time tool dependency on `pyarrow` to parse the MTSamples parquet
  (added to `backend/requirements.txt`). Synthea ingest needs JDK 11+ to
  run the generator; we ship the *output* fixture so contributors don't
  need Java unless they want to refresh.
- Some Synthea patient names look mechanical ("Mariano761 Tamez493"); we
  strip the trailing numeric suffix during ingest so they read naturally
  ("Mariano Tamez").

### Refresh workflow

```
python -m scripts.ingest_dailymed    # ~30 s, 10 drugs from DailyMed REST API
python -m scripts.ingest_mtsamples   # ~10 s, parquet from Hugging Face
python -m scripts.ingest_synthea     # ~5 min first time (downloads jar + generates)
python -m scripts.seed_mongo         # always-runs entry point
```

Each ingest script is idempotent — safe to re-run. See `data/README.md`.

### Pitch language

> *"We didn't make our medical data up. The references retriever runs on
> **real FDA drug labels** from DailyMed — when the system says metformin
> is contraindicated below eGFR 30, that's the actual FDA text, you can
> verify it on dailymed.nlm.nih.gov. The events retriever runs on
> **Synthea-generated patients** — synthetic by construction, no PHI risk,
> but with realistic SNOMED + LOINC + RxNorm-coded conditions and labs. The
> notes retriever runs on **MTSamples** — public-domain real-style clinical
> dictations across all specialties. Three canonical datasets, one per
> retriever, all working zero-shot through pre-trained models."*

---

## 2026-05-02 (d) · Local Retrieval + Active Retrieval (no fine-tuning)

**Sticky.** Touches Vision, Retrievers, Reranker, shared types, and the
dashboard. Designed so the existing change-stream / agent topology is unchanged
— the new behaviour is layered, not rewritten.

### What changed

- **Local Retrieval** (`backend/local_cache.py`).
  Per-session in-process cache (TTL+LRU) keyed on `patient_id` and
  `medication`. Vision schedules a background prefetch every time it writes a
  `scene_context` (sees a wristband or a pill label). Retrievers consult the
  cache first; hits return in ~5 ms instead of ~150–300 ms Mongo round-trip.
  Hit/miss is recorded per result item (`metadata.from_cache`) and per
  `retrieval_results` doc (`from_cache`); the dashboard stamps a green `LOCAL`
  pill on hits.
- **Active Retrieval** (`backend/agents/active_tools.py` +
  `backend/agents/reranker.py`).
  The Reranker may emit up to 2 `active_followups` in its Pass-1 JSON output
  when it spots a concrete information gap (e.g. has the renal-contraindication
  monograph chunk for metformin but no recent eGFR in the events list).
  We execute those tool calls in parallel (~30–80 ms each) and run a tight
  Pass-2 rerank that folds the new facts in. Cap: one round, max 2 tools.
  Tools available:
    - `get_latest_lab(patient_id, lab_name)`
    - `get_last_administration(patient_id, medication)`
    - `get_monograph_section(medication, section_keyword)`
  Pass-2 results are tagged `metadata.from_active_followup=true`; the dashboard
  stamps an amber `ACTIVE` pill on those items and surfaces a dedicated
  "Active retrieval · follow-up tool calls" section in the trace view.

### Why this works without fine-tuning

The medical domain gives us **canonical entities** that pre-trained
foundation models read zero-shot:

| Entity              | Standard                      | Why pre-trained models nail it          |
| ------------------- | ----------------------------- | --------------------------------------- |
| Drug name           | USAN / INN nomenclature       | "METFORMIN" prints identically every time |
| MRN format          | Hospital convention (`P-204`) | Trivial OCR target for GPT-4o vision    |
| Lab name + unit     | LOINC + ISO 8000 / SI units   | "eGFR mL/min/1.73m²" is canonical text  |
| Reference text      | Drug monographs (FDA / RxNorm) | Public corpus, well represented in pre-training |

Factory deployments don't have that property — every plant ships a bespoke
asset (a custom C-204 pressure gauge, an OEM-specific HMI). To match
medical-grade accuracy in a factory, you'd have to fine-tune per facility,
which is exactly what we're avoiding.

So the per-deployment work moves from **fine-tuning a model** to **shaping a
prompt + populating a Mongo corpus**. That's hours, not weeks, and it carries
across hospitals because the entities are shared.

### Models touched (all pre-trained, all off-the-shelf)

| Layer            | Model                          | Domain shaping                       |
| ---------------- | ------------------------------ | ------------------------------------ |
| Vision           | GPT-4o                         | System prompt only                   |
| Embeddings       | text-embedding-3-small (1536d) | None — generic embedding works on canonical drug names |
| Router           | GPT-4o-mini                    | System prompt + Mongo filter shape   |
| Reranker         | GPT-4o-mini                    | System prompt + active-tool schema   |
| Answerer         | GPT-4o-mini                    | System prompt (cautious tone)        |
| STT              | ElevenLabs Scribe v2 Realtime  | None                                 |
| TTS              | ElevenLabs Flash v2.5          | None                                 |

### Latency math (streaming path)

| Stage                       | Before (a/b/c)            | After (d)                          |
| --------------------------- | ------------------------- | ---------------------------------- |
| Router                      | 250 ms                    | 250 ms (unchanged)                 |
| Retrievers (3, parallel)    | ~300 ms                   | ~5 ms when cache warm; ~300 ms cold |
| Reranker Pass-1             | 250 ms                    | 250 ms                             |
| Reranker Pass-2 (when fired)| —                         | +120–250 ms (LLM) +30–80 ms (tools, parallel) |
| Answerer first-token        | 350 ms                    | 350 ms                             |
| TTS first-byte              | 250 ms                    | 250 ms                             |
| **End-to-end (warm cache)** | ~1.7 s                    | **~1.4 s** (Pass-1 only) / ~1.8 s (with Pass-2) |

The cache is "warm" within ~1–2 s of Vision seeing the wristband. So in the
demo (`P-204` wristband visible 5 s before the question lands), Local
Retrieval is in effect and Pass-2 either skips or fires once with bounded
latency. Streaming path stays inside the 2 s target either way.

### Why active retrieval at the **Reranker**, not the **Answerer**

FLARE-style mid-generation retrieval would break the Answerer → TTS pipe (the
TTS streams first audio bytes as soon as the Answerer emits its first
sentence; pausing mid-sentence to retrieve breaks that contract). Putting
active retrieval at the rerank step keeps the Answerer purely
context-in-tokens-out, so the streaming TTS contract is intact.

### What we gave up

- Slight added LLM cost on questions that fire follow-ups (one extra
  GPT-4o-mini call). Bounded to ≤ 1 per question.
- A small chance of stale cache reads if a new lab is posted between
  Vision-prefetch and question-arrival. Mitigated by 90 s TTL and the fact
  that Active Retrieval can re-fetch the latest value on demand.

### Pitch language

> *"We don't fine-tune for each hospital. The pre-trained stack works zero-shot
> because medical entities are canonical — METFORMIN reads the same in every
> ward. On top of that we layer two retrieval patterns: **local** (Vision warms
> a per-session cache the moment the wristband is seen, so the Retrievers
> respond in 5 ms instead of 200), and **active** (the Reranker can fire
> targeted follow-up queries when it spots a clinically meaningful gap — like
> 'I have the renal-contraindication chunk but no eGFR'). Both are budgeted
> inside the 2 s end-to-end target."*

---

## 2026-05-02 (c) · New capability: single-image retrieval (`POST /snap`)

**Reversible.** Adds one endpoint, one button, and one frame-grab path. The
existing streaming pipeline is unchanged.

### What changed

- New endpoint `POST /snap` in `backend/main.py`. Body:
  `{ session_id, image_b64, question? }`. Synchronously runs Vision
  (`backend/agents/vision.py::extract_scene` + write to `scene_context`), then
  if `question` is non-empty, inserts a `questions` doc that triggers the rest
  of the pipeline via change streams.
- Phone "Snap & ask" button in `phone/index.html`. Captures the current frame
  from the active LiveKit video track (or directly from the camera if no
  track), shows a thumbnail, opens an optional textarea, and POSTs to `/snap`.
- Dashboard "Upload image" affordance for testing the snap path without a
  phone.

### Why

The streaming path is great for "show me everything I'm walking past." But the
clinician's actual interaction is usually deliberate: they pick up a pill
bottle, frame it, ask one question, get one answer. A snap-and-ask UI makes
that interaction first-class. Lower friction than holding the camera steady
while talking to a wearable.

### Latency note

Snap path puts Vision in the *critical path* (it has to finish before the
question is dispatched), so end-to-end runs ~3–3.5 s instead of ~2 s. We
publish that explicitly as the snap-path budget. Streaming path keeps the 2 s
target.

### Pitch language

> *"Two ways in. Walk around and the system passively builds context. Or snap
> one frame and ask one question — same agents, same Mongo features, same
> pipeline."*

---

## 2026-05-02 (b) · Domain pivot: factory walkthrough → point-of-care medical decision support

**Sticky.** Touches every prompt, the seed data, the demo script, the spec
docs, the type names (`Machine→Patient`, `MaintenanceEvent→ClinicalEvent`),
the collection names (`machines→patients`,
`maintenance_events→clinical_events`), and the retriever source labels
(`manuals/logs/history → references/events/notes`). Architecture, latency
budget, agent count, and the 8 Mongo features are unchanged.

### Why

Three reasons:

1. **More compelling demo.** A clinician at the bedside reading a pill bottle
   while their patient's labs and prior administrations are pulled in is a
   sharper, more legible moment than "factory worker stares at conveyor belt."
   The visual signal (`METFORMIN 500 mg` + wristband `P-204`) plus the
   per-patient time-series data (`eGFR=38`, `last admin 47h ago`) gives the
   Reranker an obvious, judge-legible reason to boost a specific result.
2. **Higher rubric ceiling for "adaptive."** In medicine, the same drug + the
   same question gets *different* answers for different patients (because of
   contraindications, allergies, prior admins). That's the literal definition
   of adaptive retrieval. The factory scenario could in principle be answered
   with a single context-free monograph; the medical scenario can't.
3. **Real product surface.** Point-of-care assistants for clinicians are a
   real, growing market. The hackathon prototype maps to a thing someone might
   actually build — easier to talk about as a product, not just a tech demo.

### Demo scenario (canonical)

Clinician looks at a pill bottle reading `METFORMIN 500 mg` with patient
wristband `P-204` visible nearby. Asks *"Is it safe to give this dose now?"*
The system sees the drug + MRN, pulls metformin contraindications from the
references collection, finds the patient's recent eGFR=38 in clinical events,
the last admin 47h ago, and answers something like:

> *"Hold this dose — P-204's most recent eGFR is 38, which is borderline for
> metformin. Last administration was 47 hours ago. Recommend rechecking renal
> function before giving."*

### What we give up

- Some teammates may already have the factory pitch memorised. ~10 minutes of
  reframing in the kickoff.
- Need to be careful with the language around "diagnosis" — see the
  compliance note below.

### Compliance language (use verbatim if a judge asks)

> *"This is clinical decision support, not diagnosis. The clinician is
> always in the loop and acts on the recommendation. The system never
> auto-administers anything. Demo data is mock — no real PHI."*

### Pitch language

> *"Clinical decision support grounded in live visual memory. Multi-source
> agentic retrieval that reweights answers based on what the clinician is
> looking at right now — the pill in their hand, the wristband in front of
> them, the monitor on the wall."*

---

## 2026-05-02 (a) · STT vendor: Deepgram → ElevenLabs Scribe v2 Realtime

**Reversible.** ~15 minutes of work to swap back; only `backend/stt.py`,
`backend/config.py`, `backend/requirements.txt`, `.env.example`, and one line
in `backend/worker.py` change.

### What changed

- `backend/stt.py` now opens
  `wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id=scribe_v2_realtime&audio_format=pcm_16000&commit_strategy=vad&language_code=en`
  per LiveKit audio track, sends 16 kHz mono PCM as base64 `input_audio_chunk`
  messages, and writes `transcripts` (+ `questions` when `is_question(text)`)
  on each `committed_transcript`.
- `class ScribeSession` replaces `class DeepgramSession`. A back-compat alias
  `DeepgramSession = ScribeSession` lives at the bottom of `stt.py` so any
  stale imports keep working through the swap.
- Dropped `deepgram-sdk` and `livekit-plugins-deepgram` from
  `backend/requirements.txt`.
- Dropped `DEEPGRAM_API_KEY` from `.env.example` and `backend/config.py`.

### Why

The spec was written before ElevenLabs shipped **Scribe v2 Realtime** (January
2026). The original "do not use Whisper, Deepgram only" rule was really a
rule against *non-streaming* STT — it lumped Whisper in with the slow path.
Scribe v2 Realtime is genuinely streaming (websocket, partial + committed
events) and posts the same first-token latency as Deepgram Nova-3 in
independent 2026 benchmarks:

| | Deepgram Nova-3 | Scribe v2 Realtime |
|---|---|---|
| First token (TTFT) | ~150 ms | ~150 ms (30–80 ms optimised) |
| Final / commit | ~280 ms | <150 ms typical |
| English-noisy WER | ~5.2 % | ~5.7 % |
| Languages | ~50 | 90+ |

Net latency impact on our pipeline: **negligible** (<50 ms vs. the spec's
budget of 200–400 ms for STT).

### What we get

- One vendor for both speech directions → one API key, one status page, one
  rate limit, one SDK to debug under demo pressure.
- One fewer hour of "why doesn't Deepgram authenticate" risk in the 6-hour
  window.
- Cleaner pitch line: *"ElevenLabs handles all voice I/O, OpenAI handles all
  cognition, MongoDB Atlas handles all memory, LiveKit handles all transport."*

### What we give up

- Marginal English-noisy accuracy edge (~0.5 WER points). Mitigation: factory
  demo audio is close-mic from the phone, not noisy.
- Slightly newer SDK surface (Scribe v2 Realtime launched Jan 2026 vs. Nova-3
  in Feb 2025). Mitigation: we wire the websocket directly rather than
  through a vendor SDK, so changes upstream are easy to track.

### Pitch language

Don't say *"we picked ElevenLabs because Deepgram was wrong."* Say:

> *"Scribe v2 Realtime gives us 150-millisecond first-token transcripts with
> the same vendor handling our Flash v2.5 TTS — one connection pool, one
> SDK, one less thing to break on stage."*

---

## 2026-05-02 · GridFS clip upload deferred

**Reversible.** The `clips` GridFS bucket and its 24h TTL index are created at
boot in `backend/mongo.py`, so the feature is *demonstrable* (we can show
the bucket and the TTL in `mongosh`) without piping live video bytes through
it on every frame. Wiring the worker to write each chunk costs ~30 minutes
and adds bandwidth; we decided that wasn't a rubric-critical use of the time
budget. Re-enable by adding a write to the `clips` bucket in
`backend/worker.py::_consume_video`.

---

## Format note

Add new entries at the top. Each one needs:

1. Date + one-line headline.
2. **Reversible** vs **Sticky** label.
3. *What changed* (files, lines).
4. *Why*.
5. *What we get* / *What we give up*.
6. (Optional) suggested pitch language for the demo.
