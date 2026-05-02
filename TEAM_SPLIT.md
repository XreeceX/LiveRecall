# LiveRecall: 4-person team split

> **Amended 2026-05-02 (a):** STT vendor is **ElevenLabs Scribe v2 Realtime**,
> not Deepgram. Same first-token latency (~150 ms), one fewer vendor key.
>
> **Amended 2026-05-02 (b):** Domain is **point-of-care medical decision
> support**, not factory walkthrough. Stream A films pill bottles + wristbands,
> Stream C seeds patients + clinical events, Stream B's prompts target drug
> names + MRNs. Architecture and Mongo features unchanged.
>
> **Amended 2026-05-02 (c):** New capability — `POST /snap`. The phone has a
> "Snap & ask" button. Stream A wires the button, Stream B wires the endpoint.
> Streaming path still works in parallel.
>
> **Amended 2026-05-02 (d):** **Local Retrieval** + **Active Retrieval** added,
> entirely on **pre-trained models** (no fine-tuning per hospital — medical
> entities are canonical). Stream B owns both: a session-scoped prefetch cache
> warmed by Vision (`backend/local_cache.py`) and a Reranker that may emit up
> to 2 follow-up tool calls (`backend/agents/active_tools.py` +
> `backend/agents/reranker.py`). Stream D shows `LOCAL` / `ACTIVE` pills in the
> trace view. Latency budget on warm cache drops to ~1.4 s; with one active
> follow-up fired it stays inside ~1.8 s.
>
> **Amended 2026-05-02 (e):** Three **real public datasets** back the three
> retrievers (DailyMed → references, Synthea → patients/events, MTSamples →
> notes). Stream C owns the ingest pipeline + `seed_mongo.py`; fixtures live
> in `data/fixtures/` (committed). The demo headline patient `P-204` "Sarah
> Chen" is a re-labelled Synthea patient with two hand-shaped events injected
> for demo determinism. See `data/README.md`.
>
> **Amended 2026-05-02 (f):** References retriever is now a **multimodal
> apparatus catalog** — every row has `(name, context, image)`. DailyMed
> ingest pulls real FDA product photos for medications; new
> `scripts/ingest_wikimedia_equipment.py` adds ~23 bedside devices (infusion
> pump, pulse oximeter, defibrillator, …) with photos from Wikipedia +
> Commons (CC-BY-SA). **Stream B** owns the Vision prompt update: it now
> emits an `apparatus` list (canonical names from the catalog vocabulary)
> alongside `objects` and `text_visible`, and the prefetch path warms the
> cache for any recognised apparatus, not just OCR'd drug names. **Stream C**
> owns the new ingest script + Wikimedia rate-limit handling. **Stream D**
> renders inline product/device thumbnails in `ReasoningTrace.tsx` and
> apparatus chips in `ScenePanel.tsx` so the audience sees the visual
> matching working. No latency change — image bytes only travel out to the
> dashboard, never back through the LLM critical path.
>
> **Amended 2026-05-02 (g):** **Two capture modes** — Meta Ray-Ban
> first-person POV (headline) and a universal phone-browser fallback (every
> clinician already has one). `shared/types.py` adds `CaptureMode`; `/token`
> + `/snap` accept it; Vision's system prompt gets a tiny POV-hint append.
> **Stream A** owns both capture pages: the existing `phone/index.html` is
> labelled "Phone · fallback" with a slate badge, and the new
> `phone/glasses.html` is the Ray-Ban POV entry point (first-person webcam
> stand-in — see DECISIONS.md (g) for the path to a real Meta Live AI →
> LiveKit ingress bridge). **Stream B** owns the surgical Vision prompt
> append (`_system_prompt_for`) and the per-frame capture-mode threading;
> the apparatus extraction from (f) is untouched. **Stream D** renders the
> `GLASSES` / `PHONE` pill near the session header. **Streams C** is doc-only
> here — capture mode rides on the existing `sessions` + `scene_context`
> writes; no new collections. Latency budget unchanged.
>
> See `DECISIONS.md` for rationale.

6 hours of focused build, then video + submission. 4 builders, 1 substrate (MongoDB Atlas Sandbox), 1 transport (LiveKit Cloud).

The API contract in `shared/types.py` is the seam. Lock it in the first 30 minutes.

## Latency target

**End-to-end ≤2 seconds** from end-of-question to first audio byte. Every architecture choice serves this. If a stream's task threatens latency, raise immediately.

## Team

- **Stream A — Kazybek (Kalle)**: Capture + LiveKit + audio I/O + demo
- **Stream B — Teammate 2**: Agent layer (LangChain + 5 agents)
- **Stream C — Teammate 3** (Mongo-comfortable full-stack): Mongo + retrieval pipeline + STT/TTS
- **Stream D — Teammate 4** (full-stack swing): Dashboard + integration glue

## Agent assignments (memorize)

| Agent | Owner | Model | Streaming |
|---|---|---|---|
| Vision | Stream B | GPT-4o (vision) | No (frame-by-frame) |
| Router | Stream B | GPT-4o-mini | No |
| Retrievers (3) | Stream B + C | No LLM, pure Mongo aggregation | N/A |
| Reranker | Stream B | GPT-4o-mini | No |
| Answerer | Stream B | GPT-4o-mini | **Yes, token streaming** |

Stream B owns the agents. Stream C owns the Mongo aggregation pipelines that the Retrievers run.

---

## Hour 0 — All four together (KICKOFF, 30 min)

| Task | Owner |
|---|---|
| Lock `shared/types.py`, push to public GitHub | All |
| Atlas Sandbox cluster from provided link (NOT personal Atlas) | C |
| LiveKit Cloud project + API key | A |
| ElevenLabs + OpenAI keys in shared `.env` (one ElevenLabs key powers both Scribe v2 Realtime STT *and* Flash v2.5 TTS) | All |
| Confirm at least one teammate available May 7 | All |
| Decide demo scenario: bedside medication safety check (clinician + pill bottle + wristband) | All |
| Lock framing: "clinical decision support grounded in live visual memory" | All |
| **Compliance language** — never "diagnose," always "decision support." No real PHI in seeds. | All |

---

## Stream A · Kazybek · Capture + LiveKit + audio I/O + demo

You own the real-time transport, the demo flow, and the video. LiveKit is the second pillar of the pitch, your stream is high-leverage.

| Time | Task |
|---|---|
| H1 | **LiveKit Cloud project live.** Create room `liverecall-test`. Generate access tokens server-side via small token endpoint. Pre-record 5 POV clinical clips on Ray-Bans (or phone): (1) METFORMIN 500 mg pill bottle close-up, (2) patient wristband reading "P-204", (3) wider med-cart shot, (4) vital-signs / lab monitor mock-up showing eGFR, (5) second pill bottle (LISINOPRIL 10 mg) for the snap variant. **Use mock assets only — no real PHI.** Drop into shared folder. Unblocks B, C, D. |
| H2 | Phone web page (`phone/index.html`): joins LiveKit room, publishes mic + camera tracks, subscribes to backend's audio track for answer playback. ~50 lines using `livekit-client` CDN. **Add the "Snap & ask" button** that grabs the current frame from the active video track, opens an optional question textarea, and POSTs to `/snap`. Test sub-second round-trip with backend echo agent. |
| H3 | Wire phone-to-Ray-Bans Bluetooth pairing for answer audio. **HARD CAP 60 min.** If flaky, fall back to phone speaker. Glasses still worn for visual story. |
| H4 | Help B integrate LiveKit Agents Python SDK into backend worker. Worker joins room, subscribes to audio (→ ElevenLabs Scribe v2 Realtime), subscribes to video (→ frame sampler), publishes TTS audio back as a track. |
| H5 | Demo run-of-show. Polish stage flow. Cover **both** paths: (a) walking-around streaming with a question, (b) snap-and-ask on the second pill bottle. **Do a "test video record" at +5h to surface video production issues early.** |
| H5.5-H6 | **Record 1-min submission video** (dashboard + LiveKit room + agent flow + the snap variant). Use ElevenLabs for voiceover (Multilingual v2 here, Flash is for the live answer pipeline only). Upload, push final commit, confirm public repo, submit form. |

**Blocking on**: B's backend by H2, C's TTS by H4.
**Unblocks**: B and C have pre-recorded clips by H1. Without these, they work blind.

**Top risk**: video production at H5.5 fails because system isn't end-to-end. Mitigation: dry-run video record at H5 even if rough, expose problems with 30 min to fix.

---

## Stream B · Teammate 2 · Agent layer (LangChain)

You own the brain. ALL CODE NEW. Use **LangChain (langchain-core, langchain-openai, langchain-mongodb, langgraph)** for tool calling and streaming.

| Time | Task |
|---|---|
| H1 | FastAPI app + LiveKit Agents Python worker (`backend/main.py`, `backend/worker.py`). Worker joins LiveKit room, subscribes to tracks, calls placeholder STT/vision functions returning mocks. Smoke test: phone publishes audio, worker logs receipt, publishes silent track back. |
| H2 | Build agent classes using LangChain `ChatOpenAI` + `ChatPromptTemplate`. Wire callback handler that writes every LLM call to a `agent_traces` collection (for D's dashboard). Vision agent (`backend/agents/vision.py`, **GPT-4o vision**): takes frame, runs structured prompt extracting `{objects, apparatus, text_visible, environment, activity, text_summary}`. `apparatus` is a list of canonical lowercase names from a fixed catalog vocabulary (~10 medications + ~25 devices — see prompt for full list); Vision adds an entry whenever it identifies a known physical object even when its label is illegible. `text_visible` examples: drug names ("METFORMIN 500 mg"), MRNs ("P-204"), lab values ("eGFR 38"). Environment values include `hospital_room | clinic | pharmacy | medication_cart | exam_table`. Calls C's embedding helper. Writes `scene_context` AND schedules local-cache prefetches for every patient_id, drug name, AND apparatus name it just observed. Test against pre-recorded clips. **Wire `POST /snap` in `main.py` to call into this same Vision function synchronously**, then conditionally insert a `questions` doc. |
| H3 | Router agent (`backend/agents/router.py`, **GPT-4o-mini**): subscribes to new questions via change stream. Reads recent `scene_context` (last 30s). Emits 3 differentiated queries — `references` (drug monographs / protocols), `events` (per-patient time-series), `notes` (past clinical handoffs). When visible text contains a `P-2xx` MRN set `filter.patient_id`; when it contains a medication name set `filter.medication`. Writes to `retrieval_plans`. Use mini, not 4o, latency-critical. |
| H4 | Reranker (`backend/agents/reranker.py`, **GPT-4o-mini**) and Answerer (`backend/agents/answerer.py`, **GPT-4o-mini, STREAMING**). Reranker reads all retrieval_results + scene_context, single LLM call to rerank with `boost_reason` per result (must cite the visible drug name and/or MRN when present), writes `final_context`. Answerer reads, generates streaming response via `astream()`, pipes tokens to C's ElevenLabs streaming TTS in real time. **Tone: cautious decision-support language, never imperative ("recommend" not "give"; mention contraindications explicitly).** |
| H5 | Latency optimization. Profile end-to-end. Cut anything that pushes past 2.5s on the streaming path / 3.5s on the snap path (Vision is in the critical path for snap). Tune prompts for token brevity (shorter answers = faster TTS = faster perceived response). |
| H6 | Bug-fix support, video record support. |

**Blocking on**: nothing initially. Unblocks everyone.
**Unblocks**: A (LiveKit Agents integration), C (embedding calls), D (collections to subscribe to).

**Top risk**: LangChain streaming + ElevenLabs streaming + LiveKit publishing chain has multiple failure points. Mitigation: build the streaming pipe end-to-end at H4 with fake tokens before plugging in the real agents.

**LangChain pattern reference (use this style):**

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.callbacks import BaseCallbackHandler

class MongoTraceCallback(BaseCallbackHandler):
    def __init__(self, question_id, agent_name, mongo):
        self.question_id = question_id
        self.agent_name = agent_name
        self.mongo = mongo
    
    def on_llm_end(self, response, **kwargs):
        self.mongo.agent_traces.insert_one({
            "question_id": self.question_id,
            "agent": self.agent_name,
            "tokens": response.llm_output.get("token_usage"),
            "timestamp": int(time.time() * 1000),
        })

# Router
router_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
router_prompt = ChatPromptTemplate.from_messages([
    ("system", ROUTER_SYSTEM),
    ("user", "Question: {question}\nRecent scene: {scene}")
])
router_chain = router_prompt | router_llm | parse_json

# Answerer (streaming)
answerer_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3, streaming=True)
async for chunk in answerer_llm.astream(messages):
    elevenlabs_stream.send_text(chunk.content)
```

---

## Stream C · Teammate 3 · Mongo + retrieval pipeline + STT/TTS

You own the data substrate and the speech I/O. The rubric lives here. Latency lives here too.

| Time | Task |
|---|---|
| H1 | Atlas Sandbox cluster live. Define all 8 collection schemas in `backend/mongo.py`. Create vector indexes on `scene_context`, `documents`, `transcripts`. Create Time Series collection for `clinical_events` (timeField=`timestamp`, metaField=`patient_id`). TTL on `clips`. Confirm change streams emit events. |
| H2 | Embedding helper (`backend/embeddings.py`): batch embed via `text-embedding-3-small`. Run the **4 dataset ingest scripts** to populate `data/fixtures/` (one-time, ~10 min total): `python -m scripts.ingest_dailymed` (FDA SPL labels + product photos, 10 drugs → ~136 chunks, 100% with images), `python -m scripts.ingest_wikimedia_equipment` (Wikipedia + Commons → ~23 bedside devices with thumbnails; respects Wikimedia rate limits with backoff), `python -m scripts.ingest_synthea` (Synthea v3.3 → 25 patients + ~917 events; needs JDK 11+), `python -m scripts.ingest_mtsamples` (HuggingFace parquet → ~35 notes across 7 specialties). Then `python -m scripts.seed_mongo` — it loads from fixtures (preferred) or falls back to inline mock if any fixture is missing. The seeder UNIONs DailyMed + Wikimedia equipment into the `documents` collection so the References retriever can hit both medications and devices in a single $vectorSearch. It also picks the most demo-relevant Synthea patient (T2DM + CKD bias), re-labels them as **P-204 "Sarah Chen"**, and injects two hand-shaped headline events on top: metformin 500 mg admin ~47 h ago + eGFR=38 lab ~18 h ago. **Synthetic + public-domain data only — no real PHI.** See `data/README.md` and `DECISIONS.md` (e), (f). |
| H3 | **ElevenLabs Scribe v2 Realtime STT** (`backend/stt.py`). Async websocket to `wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id=scribe_v2_realtime&audio_format=pcm_16000&commit_strategy=vad&language_code=en`. Per LiveKit audio track, open one Scribe stream, send 16k mono PCM as base64 `input_audio_chunk` messages, receive `partial_transcript` + `committed_transcript` events. On `committed_transcript`, write to `transcripts` collection. Question detection: trigger Router on questions ending with `?` or starting with what/when/why/how/which. Target STT latency: 150ms partial, 300ms commit. |
| H4 | **ElevenLabs Flash v2.5 streaming TTS** (`backend/tts.py`). Open streaming connection, accept text chunks from Answerer, receive audio chunks (MP3 or PCM), pipe into LiveKit audio track publisher. **First-byte latency target: 200ms.** Test with mock text input before plugging into Answerer. |
| H5 | **Retriever aggregation pipelines** (`backend/agents/retrievers.py`). Build with B. Three Retrievers, each subscribing to `retrieval_plans` change stream filtered by source: |

```python
# References Retriever — drug monographs, protocols, guidelines
db.documents.aggregate([
    {"$vectorSearch": {
        "index": "doc_text_vec",
        "queryVector": embed(query.vector_query),
        "path": "text_embedding",
        "numCandidates": 50,        # tuned for latency
        "limit": 5
    }},
    # optional filter: medication name from scene
    {"$match": {"medication": query.filter.get("medication")}} if query.filter.get("medication") else {"$match": {}},
])

# Events Retriever (Time Series, per-patient)
db.clinical_events.aggregate([
    {"$match": {"patient_id": query.filter["patient_id"]}},
    {"$sort": {"timestamp": -1}},
    {"$limit": 5}
])

# Notes Retriever (vector + recency boost)
now = int(time.time() * 1000)
db.transcripts.aggregate([
    {"$vectorSearch": {
        "index": "transcript_text_vec",
        "queryVector": embed(query.vector_query),
        "path": "text_embedding",
        "numCandidates": 50,
        "limit": 10
    }},
    {"$addFields": {
        "recency_boost": {"$divide": [1, {"$add": [
            1, {"$divide": [{"$subtract": [now, "$timestamp"]}, 86400000]}
        ]}]}
    }},
    {"$sort": {"recency_boost": -1}},
    {"$limit": 5}
])
```

**These pipelines are the rubric-critical adaptive retrieval surface.** numCandidates tuned to 50 (not 100) for latency. Test query latency: target <150ms each. |

| H6 | Bug-fix support, latency tuning. |

**Blocking on**: nothing initially.
**Unblocks**: B (needs Mongo + embeddings), A (needs STT/TTS).

**Top risk**: Atlas Vector Search latency >300ms blows budget. Mitigation: pre-warm indexes by running the demo query 5 times before recording video, reduce numCandidates further if needed.

---

## Stream D · Teammate 4 · Dashboard + integration glue

You own the **judge-facing dashboard** and are the **roving integration referee**. Without your dashboard, the agent system is invisible. With it, the demo is undeniable.

| Time | Task |
|---|---|
| H1 | Next.js project bootstrapped. WebSocket server (`backend/change_streams.py` exposed via `/stream`) subscribes to all 8 Mongo change streams, fans events to dashboard. |
| H2 | **Pipeline diagram** (`dashboard/components/Pipeline.tsx`): 5 horizontal lanes (Vision, Router, Retrievers, Reranker, Answerer). Each lane lights up when its collection sees a write. Show document below the lane. |
| H3 | **Scene context panel**: live transcript, current `scene_context` (drug names, MRNs, vitals visible), current question. **Mongo features sidebar**: Vector Search, Time Series, Change Streams, GridFS, $vectorSearch in aggregation, TTL, Aggregation pipelines, Document model. Each item turns green when first used. **THIS IS THE RUBRIC CHECKLIST VISIBLE ON SCREEN.** |
| H4 | **Reasoning trace view**: when answer generates, show full chain frame → vision → router → retrievers (references / events / notes) → reranker → answer with timestamps and Mongo collections touched. Critical for judges to see the adaptation. |
| H5 | **Latency monitor**: render timeline of last question's latency by stage (STT → Router → Retrievers → Reranker → TTS). Visible on dashboard during demo. Numbers ≤2s = green, ≤2.5s = yellow, more = red. **Add an "upload image" tester** that POSTs to `/snap` so the dashboard can drive the snap path without a phone. |
| H6 | Polish for video. Bug-fix support. |

**Blocking on**: B's collections existing (by H1), C's Mongo cluster live (by H1).
**Unblocks**: nothing, you're a consumer.

**Secondary role: integration referee.** Around H3-H4, integration bugs surface (clips upload but Vision doesn't fire, Reranker output missing scene_context). Drop dashboard work for 30 min, fix the seam, return.

**Top risk**: change stream firehose floods browser. Mitigation: throttle WebSocket to 10/sec per collection.

---

## Critical sync points

| Time | Event | Owner |
|---|---|---|
| **H0:30** | `shared/types.py` locked, Atlas Sandbox + LiveKit + keys ready | All |
| **H1** | Pre-recorded clips ready, B's worker joins LiveKit, C's collections live | A, B, C |
| **H2.5** | End-to-end skeleton: phone publishes, worker subscribes, frame lands in Mongo, dashboard shows | All |
| **H4** | First full question-to-answer flow works (rough OK), ElevenLabs voice plays through phone | All |
| **H5** | Dashboard polished, latency under 3s, dry-run video record | All |
| **H5.5** | Final video record + submission | A primary |

If a sync point slips by 30+ min, raise immediately and replan.

---

## Handoff matrix

| Seam | Primary | Integrator | Test by |
|---|---|---|---|
| Phone audio → LiveKit → Scribe v2 Realtime → Router | C | A | H3 |
| Phone video → LiveKit → frame sampler → Vision | B | A | H2.5 |
| Phone Snap & ask button → `POST /snap` → Vision sync → questions doc | A | B | H4 |
| B's agent outputs → Mongo change streams → D's dashboard | C | D | H3 |
| Answerer streaming → ElevenLabs → LiveKit → phone speaker | C | A | H4 |

---

## Latency optimization checklist

If end-to-end exceeds 2.5s, work this list top-down:

1. Confirm Scribe v2 Realtime is **streaming** (websocket to `/v1/speech-to-text/realtime`), not the batch `/v1/speech-to-text` endpoint.
2. Confirm Answerer is using `astream()` not `ainvoke()`.
3. Confirm ElevenLabs is **streaming** (websocket), Flash v2.5 not Multilingual.
4. Confirm Mongo Vector Search numCandidates ≤50.
5. Confirm Retrievers run in parallel (`asyncio.gather`), not sequentially.
6. Confirm Router prompt is short (<500 tokens system + user).
7. Confirm Reranker prompt sends only top-5 per source, not full results.
8. Profile each stage with timestamps written to `agent_traces`.
9. Pre-warm Atlas Vector Search by running demo query 3x before recording.
10. **Local Retrieval cache hits** — confirm dashboard shows the green `LOCAL`
    pill on at least 1 of the 3 retrievers in the demo run (means Vision
    prefetched the patient_id / medication in time). Cache TTL is 90 s and
    dedup is automatic — a duplicate scene won't double-fetch.
11. **Active Retrieval bound** — the Reranker can fire up to 2 follow-up tool
    calls (Pass-2). If the demo question consistently triggers 0 follow-ups
    that's fine (Pass-1 was sufficient); if it triggers >0, confirm Pass-2 LLM
    + tool execution stays under ~300 ms total in `agent_traces`.

---

## Comms

- One Discord/Slack channel.
- Post on every commit with slot label.
- Raise blockers in big bold text.
- Pomodoros: 25-min focus, 5-min sync. Helps Kazybek (intense ADHD).

---

## Cuts list (in priority order if time slips)

Cut from the bottom up. Never cut Mongo features. Never cut LiveKit.

1. Bluetooth Ray-Bans audio output (fall back to phone speaker)
2. Real Ray-Ban capture (use only pre-recorded clips piped into LiveKit)
3. Streaming Answerer (fall back to non-streaming, +500ms latency)
4. Vision agent on every frame (fall back to scene-change detection, every ~5s)
5. Latency monitor on dashboard (cosmetic)

If latency budget blows past 3s, escalate one agent at a time:
- First: drop Vision agent re-runs on every frame, keep last scene_context for up to 10s
- Second: drop the Reranker, use raw retrieval scores
- Last resort: drop one Retriever (lose multi-source story, big rubric hit)
