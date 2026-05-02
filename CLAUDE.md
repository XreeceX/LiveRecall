# LiveRecall: Build Instructions

You are building **LiveRecall**, an adaptive retrieval system grounded in real-time visual memory. Read this whole file before writing code. Do not deviate from the architecture without asking.

## What we're building

A wearable memory system. Ray-Ban Meta v2 glasses (or phone-as-capture) stream audio + video via LiveKit to a backend. A Vision agent extracts structured `scene_context` from frames. When the user asks a question, a Router agent uses *recent scene context* + the question to construct adaptive queries across multiple sources. Retrievers run hybrid search in parallel. A Reranker reweights results based on what the user just saw. An Answerer speaks the response back through the same LiveKit room.

The product is **adaptive retrieval grounded in real-time visual memory**, with **MongoDB Atlas as the substrate** and **LiveKit as the real-time transport**.

## Framing

Pitch as: *"Adaptive retrieval grounded in live visual memory. Multi-source agentic retrieval that reweights results based on what the user just saw."*

Never as: "RAG with glasses," "image analyzer," "multimodal RAG."

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
| Streaming STT (Deepgram) | 200-400 ms |
| Router (GPT-4o-mini) | 300-500 ms |
| 3 Retrievers in parallel (Mongo) | 200-400 ms |
| Reranker (GPT-4o-mini, single call) | 300-500 ms |
| Answerer (GPT-4o-mini, streaming) | 200-400 ms first token |
| ElevenLabs TTS first byte (Flash v2.5) | 150-300 ms |
| LiveKit egress → phone playback | 50-100 ms |
| **Total: end-of-question to first audio byte** | **~1.5-2.5 s** |

**Latency-driven model choices:** GPT-4o-mini everywhere except where quality genuinely matters. Streaming everywhere it's available. Deepgram over Whisper (Whisper is non-streaming, adds 1-2s). Flash TTS over standard.

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
| **STT** | **Deepgram Nova-2 streaming** | Sub-300ms partial transcripts, beats Whisper for latency |
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

```
[Phone browser]
   |
   |  WebRTC: audio + video tracks
   v
[LiveKit Cloud room "liverecall"]
   |
   v
[Backend: LiveKit Agents Python worker]
   |
   |  Subscribes to room
   |  - Audio track  -->  Deepgram streaming STT  -->  transcripts (Mongo)
   |  - Video track  -->  Frame sampler (1 fps)   -->  video_frames (Mongo)
   |
   v
[MongoDB change streams trigger agents]
   |
   |  new video_frame  -->  Vision agent (GPT-4o)  -->  scene_context
   |  new transcript   -->  Router (GPT-4o-mini)   -->  retrieval_plan
   |
   v
[Retriever fan-out via change stream subscriptions]
   |
   |  Manuals     -->  Mongo $vectorSearch
   |  Logs        -->  Mongo Time Series + filter
   |  Transcripts -->  Mongo $vectorSearch + recency
   |
   v
[Reranker (GPT-4o-mini): scene_context + retrieval_results --> final_context]
   |
   v
[Answerer (GPT-4o-mini, streaming): final_context --> answer tokens]
   |
   v  (streamed token-by-token)
[ElevenLabs Flash v2.5 TTS streaming]
   |
   v  (audio chunks)
[LiveKit publishes audio track back into room]
   |
   v
[Phone subscribes, plays through Ray-Ban Bluetooth]
```

Every state change is a Mongo write. Agents communicate via change streams.

## Mongo collections

| Collection | Type | Purpose | Mongo feature |
|---|---|---|---|
| `sessions` | standard | One per LiveKit room session | Document model |
| `clips` | GridFS | Raw video chunks (24h TTL) | GridFS + TTL index |
| `video_frames` | standard | Sampled frames | Change streams |
| `scene_context` | standard + Vector | Vision output, embeddings | Atlas Vector Search |
| `transcripts` | standard + Vector | STT output | Atlas Vector Search |
| `documents` | standard + Vector | Manuals chunked | Atlas Vector Search |
| `maintenance_events` | **Time Series** | Machine logs | Time Series collection |
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
    objects: list[str]              # ["conveyor belt", "pressure gauge"]
    text_visible: list[str]         # ["C-204", "PSI 47"]
    environment: str                # "factory_floor"
    activity: str                   # "running"
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

- LiveKit room `liverecall-{session_id}` is the primary interface (no REST for capture)
- `GET /scene-context/recent?seconds=30` → JSON for dashboard
- `GET /trace/:question_id` → full reasoning chain for dashboard
- `WS /stream` → dashboard subscribes, change stream events fan out

## Build order (parallel, 6 hours)

### Hour 0 (kickoff, all four together, 30 min)

1. Lock `shared/types.py`, push public GitHub.
2. **Atlas Sandbox cluster** from the provided link (NOT a personal Atlas).
3. **LiveKit Cloud project** spun up, API key + URL in shared `.env`.
4. **Deepgram + ElevenLabs API keys** in `.env`. (Apply for ElevenLabs Creator tier early if not already.)
5. OpenAI API key in `.env`.
6. Decide demo scenario: factory walkthrough.
7. **Confirm at least one teammate available May 7 for follow-up event.** If not, replan team commitment.

### Hours 1-6: parallel streams

See TEAM_SPLIT.md for hourly task lists. Sync points at +1, +2.5, +4, +5, +5.5 hours.

### Final 45 minutes: video production + submission

1. Record 1-min video showing dashboard + LiveKit live capture + agent flow.
2. Voiceover via ElevenLabs (Flash v2.5 for the demo, regular voice for the video).
3. Push final commit, public repo confirmed.
4. Submit form.

## Demo scenario

**Pre-recorded factory clips** (Kazybek, by hour 1):
1. Conveyor belt with visible "C-204" label
2. Pressure gauge reading ~47 PSI
3. Wide factory floor with 3 machines
4. Worn belt close-up
5. Control panel display

**Seed Mongo** (Stream C, by hour 2):
- 5 machines (C-201..C-205)
- 50 maintenance events over 6 months (Time Series collection)
- 3 manuals chunked + embedded (~30 chunks)
- 5 past inspection transcripts

**Demo question**: *"What's the failure rate on this conveyor and when was it last serviced?"*
**Expected answer**: System sees C-204 + 47 PSI gauge, returns: 3% expected failure rate from spec + last service 47 days ago + gauge reading is mid-range normal. Reranker boosts gauge-related result via visual signal.

## Demo script (3 min live)

1. (0:00-0:20) "Adaptive retrieval grounded in live visual memory. Walk through any space, agents adapt retrieval to what you've just been looking at."
2. (0:20-0:40) Phone in hand pointed at pre-recorded factory clip. LiveKit room is live. Dashboard shows clips streaming, Vision agent firing, scene_context populating.
3. (0:40-1:00) Speak question into phone: "What's the failure rate on this conveyor and when was it last serviced?"
4. (1:00-1:30) Dashboard: Router reads recent scene_context + question, fans out to 3 Retrievers in parallel. Aggregation pipelines visible.
5. (1:30-2:00) Reranker reweights using visual signal. Boost reasons shown.
6. (2:00-2:20) ElevenLabs voice plays answer through phone speaker (or Ray-Bans BT if paired).
7. (2:20-2:50) Show "Mongo features used" sidebar, all 8 highlighted. Walk through architecture.
8. (2:50-3:00) "Adaptive retrieval. Live visual memory. MongoDB and LiveKit as the brain and the bloodstream."

## Conventions

- Python type hints on backend, TypeScript strict on dashboard.
- All timestamps unix ms.
- Errors: throw, don't silently catch.
- Public GitHub repo from minute one.
- Commit after each task slot with the slot label.

## Don'ts

- Do NOT pitch as "RAG" or "image analyzer."
- Do NOT use Streamlit.
- Do NOT use a personal Atlas account. Use the sandbox link.
- Do NOT use Pinecone, Redis, or Kafka. Mongo only.
- Do NOT use GPT-4o for Router, Reranker, or Answerer. Mini only. Latency budget doesn't allow it.
- Do NOT use Whisper. Deepgram streaming only.
- Do NOT use ElevenLabs Multilingual or Turbo for the live demo. Flash v2.5 only.
- Do NOT skip change streams. Agent message bus.
- Do NOT let any agent loop forever. Max 3 tool-call iterations (latency budget).

## Definition of done

- [ ] Phone joins LiveKit room, publishes audio + video
- [ ] Backend agent worker subscribes, runs Deepgram STT and frame sampling
- [ ] Vision agent (GPT-4o) writes scene_context with embeddings
- [ ] Router (GPT-4o-mini) reads question + scene, writes retrieval_plan
- [ ] 3 Retrievers run hybrid Mongo aggregation in parallel
- [ ] Reranker (GPT-4o-mini) reweights using visual signal, boost reasons logged
- [ ] Answerer (GPT-4o-mini, streaming) generates response token-by-token
- [ ] ElevenLabs Flash v2.5 streams TTS audio chunks
- [ ] LiveKit publishes audio back to room, phone plays through speaker
- [ ] Dashboard shows live pipeline + Mongo features highlighted + reasoning trace
- [ ] End-to-end latency ≤2.5s in testing (target ≤2s)
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
