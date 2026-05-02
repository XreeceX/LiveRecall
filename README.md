# LiveRecall

**Adaptive retrieval grounded in live visual memory.** Wear camera glasses, ask
a question, get an answer that's reweighted by what you just saw. **MongoDB
Atlas** is the substrate, **LiveKit Cloud** is the transport.

> Pitch: *"Multi-source agentic retrieval that reweights results based on what
> the user just looked at."* Not RAG. Not an image analyzer. Adaptive retrieval.

---

## What's in here

```
shared/         types.py + types.ts — the API contract
backend/        FastAPI + LiveKit Agents worker + 5 LangChain agents + Mongo
  agents/       vision · router · retrievers · reranker · answerer
  worker.py     LiveKit Agents entrypoint (joins room, samples frames, pipes audio)
  main.py       FastAPI app + change-stream WebSocket + REST endpoints
  mongo.py      8 collections, vector indexes, time series, TTL, change streams
  stt.py        Deepgram Nova-2 streaming STT (writes transcripts + questions)
  tts.py        ElevenLabs Flash v2.5 streaming TTS (PCM into LiveKit AudioSource)
  embeddings.py text-embedding-3-small batched + cached
  tracing.py    LangChain MongoTraceCallback → agent_traces
phone/          Single-file LiveKit client for any phone browser
dashboard/      Next.js + Tailwind judge-facing dashboard
scripts/        seed_mongo.py — 5 machines, 50 events (TS), 3 manuals, 5 transcripts
```

## Architecture (one screen)

```
[Phone]──audio+video──>[LiveKit room]──>[Worker]
                                          │
        ┌─────── frame sampler ──────────►│ video_frames ──► Vision (GPT-4o) ──► scene_context (vec)
        │                                 │
        │   Deepgram streaming STT ◄──────┤ transcripts ──┐
        │                                 │               └─► questions ──► Router (GPT-4o-mini)
        │                                 │                                       │
        │                                 │                                  retrieval_plans
        │                                 │                                       │
        │                                 │                  ┌──────── 3 Retrievers (no LLM)
        │                                 │                  │   manuals ($vectorSearch on documents)
        │                                 │                  │   logs    (Time Series filter+sort)
        │                                 │                  │   history ($vectorSearch + recency)
        │                                 │                  └──► retrieval_results
        │                                 │                                       │
        │                                 │                              Reranker (GPT-4o-mini)
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

## Latency target

End of question → first audio byte: **≤2 s** (hard ceiling 2.5 s).

| Stage | Budget |
|---|---|
| Phone → LiveKit | 50–100 ms |
| Deepgram streaming STT | 200–400 ms |
| Router (GPT-4o-mini) | 300–500 ms |
| 3 Retrievers (parallel Mongo) | 200–400 ms |
| Reranker (GPT-4o-mini) | 300–500 ms |
| Answerer first token (streaming) | 200–400 ms |
| ElevenLabs Flash v2.5 first byte | 150–300 ms |
| LiveKit → phone | 50–100 ms |

## Setup

### 0. Prereqs

- Python 3.11+
- Node 20+
- A **MongoDB Atlas Sandbox** cluster (use the hackathon link — *not* a personal Atlas).
- A **LiveKit Cloud** project (free tier).
- API keys for **OpenAI**, **Deepgram**, **ElevenLabs**.

### 1. Configure env

```bash
cp .env.example .env
# fill in LIVEKIT_*, MONGODB_URI, OPENAI_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY
```

### 2. Backend

```bash
cd backend
python -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements.txt
cd ..
python -m backend.mongo          # creates collections + vector indexes
python -m scripts.seed_mongo     # seeds 5 machines, 50 events, 3 manuals, 5 transcripts
python -m backend.main           # starts FastAPI + agent loops on :8000
# in a second terminal:
python -m backend.worker dev     # starts the LiveKit Agents worker
```

> **Atlas Vector Search** indexes can take a couple of minutes to come online
> after creation. The Manuals retriever falls back to a `$text` search if the
> vector index isn't ready, so the system still works — just less adaptive.

### 3. Dashboard

```bash
cd dashboard
npm install
npm run dev    # http://localhost:3000
```

### 4. Phone

Serve `phone/` with anything (the simplest):

```bash
cd phone
python -m http.server 8080      # then open http://<your-laptop-ip>:8080 on the phone
```

Important:
- The page asks for your backend URL on first launch — point it at the laptop IP, not `localhost`.
- iOS Safari requires HTTPS for camera/mic on remote hosts. Easiest path: tether the phone to the laptop via hotspot and use `localhost` via [`localhost.run`](https://localhost.run/) or `ngrok http 8080` to wrap it in HTTPS.

### 5. Try it

- Open `http://localhost:3000` (dashboard).
- Open the phone page, hit **Connect** → picks a LiveKit room.
- Speak a question or just type one in the dashboard's "Ask" box.
- Watch the lanes light up: Vision → Router → Retrievers → Reranker → Answerer.
- The phone plays the answer audio back through its speaker.

## Demo question

> *"What's the failure rate on this conveyor and when was it last serviced?"*

Expected behavior: while the phone is pointed at a label reading **C-204**, the
Router pulls `machine_id="C-204"` into the retrieval plan, the Logs retriever
returns the seeded service event from ~47 days ago, the Manuals retriever
returns the spec sheet (3% failure rate), the Reranker boosts both with a
`boost_reason` referencing the visible "C-204" token, and the Answerer says
something like:

> *"C-204 has a 3% expected failure rate per spec, and it was last serviced
> 47 days ago — the gauge reading you're seeing is mid-range normal."*

## MongoDB features used (8 load-bearing)

1. **Document model** — every collection
2. **Atlas Vector Search** — `scene_context`, `documents`, `transcripts`
3. **Time Series collection** — `maintenance_events` (`timeField=timestamp`, `metaField=machine_id`)
4. **Change Streams** — the agent bus (`video_frames` → Vision; `questions` → Router; etc.)
5. **`$vectorSearch` in aggregation** — Manuals + History retrievers
6. **GridFS** — `clips` bucket (24h TTL)
7. **TTL indexes** — `clips.files.uploadDate`
8. **Aggregation pipelines** — Retrievers + Reranker boosting

The dashboard sidebar lights each one up the moment it's actually exercised.

## Endpoints (backend, :8000)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness |
| POST | `/token` | LiveKit access token (phone or worker) |
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
