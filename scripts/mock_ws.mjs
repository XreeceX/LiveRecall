/**
 * Mock WebSocket server — simulates the Python FastAPI /stream endpoint.
 *
 * Lets you develop and test the dashboard UI without a real MongoDB or backend.
 *
 *   node scripts/mock_ws.mjs
 *
 * Binds to 0.0.0.0:8000/stream — same address the dashboard points at.
 * Cycles through all 9 change-stream collections in a realistic sequence.
 */

import { createServer } from "http";
import { WebSocketServer } from "ws";

const PORT = 8000;
const PATH = "/stream";

// ── demo data ─────────────────────────────────────────────────────────────────

const SESSION = "demo";
let questionId = null;

function nowMs() {
  return Date.now();
}

function fakeId(prefix) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

// Each step in a realistic pipeline run, fired in sequence.
function* pipeline() {
  const frameId = fakeId("frame");
  const sceneId = fakeId("scene");
  questionId = fakeId("q");
  const planId = fakeId("plan");
  const resManuals = fakeId("res");
  const resLogs = fakeId("res");
  const resHistory = fakeId("res");
  const finalId = fakeId("final");
  const answerId = fakeId("ans");

  yield {
    collection: "video_frames",
    doc: { _id: frameId, session_id: SESSION, timestamp: nowMs(), width: 640, height: 480, image_b64: "<11248 bytes>" },
  };

  yield {
    collection: "scene_context",
    doc: {
      _id: sceneId, session_id: SESSION, timestamp: nowMs(),
      source_frame_id: frameId,
      objects: ["conveyor belt", "pressure gauge", "drive pulley"],
      text_visible: ["C-204", "PSI 47"],
      environment: "factory_floor", activity: "running",
      text_summary: "Factory floor conveyor C-204 running normally, pressure gauge reads 47 PSI.",
      text_embedding: "<1536 floats>",
    },
  };

  yield {
    collection: "transcripts",
    doc: {
      _id: fakeId("ts"), session_id: SESSION, timestamp: nowMs(),
      text: "What's the failure rate on this conveyor and when was it last serviced?",
      is_final: true, is_question: false,
    },
  };

  yield {
    collection: "questions",
    doc: {
      _id: questionId, session_id: SESSION, transcript_id: fakeId("ts"),
      text: "What's the failure rate on this conveyor and when was it last serviced?",
      asked_at: nowMs(),
    },
  };

  yield {
    collection: "agent_traces",
    doc: { _id: fakeId("tr"), question_id: questionId, session_id: SESSION, agent: "router", stage: "start", model: "gpt-4o-mini", tokens: null, latency_ms: null, timestamp: nowMs(), payload: null },
  };

  yield {
    collection: "retrieval_plans",
    doc: {
      _id: planId, question_id: questionId, session_id: SESSION,
      question_text: "What's the failure rate on this conveyor and when was it last serviced?",
      scene_context_ids: [sceneId],
      queries: [
        { source: "manuals", filter: { machine_id: "C-204" }, vector_query: "conveyor failure rate C-204", weight: 0.5 },
        { source: "logs", filter: { machine_id: "C-204" }, vector_query: "C-204 last service date", weight: 0.35 },
        { source: "history", filter: {}, vector_query: "C-204 inspection pressure gauge 47 PSI", weight: 0.15 },
      ],
      created_at: nowMs(),
    },
  };

  yield {
    collection: "agent_traces",
    doc: { _id: fakeId("tr"), question_id: questionId, session_id: SESSION, agent: "router", stage: "end", model: "gpt-4o-mini", tokens: { input: 312, output: 88, total: 400 }, latency_ms: 341, timestamp: nowMs(), payload: null },
  };

  // Three retrievers fire in parallel — stagger by 20ms
  yield {
    collection: "retrieval_results",
    doc: {
      _id: resManuals, plan_id: planId, question_id: questionId, source: "manuals",
      results: [
        { document_id: fakeId("doc"), score: 0.91, snippet: "C-204 conveyor: spec failure rate 3% annually. Operating pressure window 35–55 PSI.", metadata: { machine_id: "C-204", section: "Specifications" } },
        { document_id: fakeId("doc"), score: 0.83, snippet: "The pressure gauge mounted forward of the drive pulley reads system air-line pressure. Mid-range 42–49 PSI is normal.", metadata: { machine_id: "C-204", section: "Pressure Gauge Interpretation" } },
      ],
      latency_ms: 187, created_at: nowMs(),
    },
  };

  yield {
    collection: "retrieval_results",
    doc: {
      _id: resLogs, plan_id: planId, question_id: questionId, source: "logs",
      results: [
        { document_id: fakeId("ev"), score: 0.88, snippet: "C-204 service 47 days ago: Replaced drive belt; tension recalibrated to 47 N. Pressure verified at 46 PSI.", metadata: { machine_id: "C-204", event_type: "service", days_ago: 47 } },
      ],
      latency_ms: 203, created_at: nowMs(),
    },
  };

  yield {
    collection: "retrieval_results",
    doc: {
      _id: resHistory, plan_id: planId, question_id: questionId, source: "history",
      results: [
        { document_id: fakeId("ts"), score: 0.77, snippet: "Looked at C-204 last Tuesday — gauge was 46 PSI, sounded fine, no issues.", metadata: { session_id: "historical" } },
      ],
      latency_ms: 178, created_at: nowMs(),
    },
  };

  yield {
    collection: "agent_traces",
    doc: { _id: fakeId("tr"), question_id: questionId, session_id: SESSION, agent: "reranker", stage: "start", model: "gpt-4o-mini", tokens: null, latency_ms: null, timestamp: nowMs(), payload: null },
  };

  yield {
    collection: "final_context",
    doc: {
      _id: finalId, question_id: questionId, session_id: SESSION,
      ranked_results: [
        { snippet: "C-204 service 47 days ago: Replaced drive belt; tension recalibrated to 47 N.", source: "logs", boosted_score: 0.94, boost_reason: "Visual match: C-204 label + 47 PSI gauge confirms recent service is this machine", document_id: fakeId("ev"), metadata: {} },
        { snippet: "C-204 conveyor: spec failure rate 3% annually. Operating pressure window 35–55 PSI.", source: "manuals", boosted_score: 0.88, boost_reason: "Direct spec match for failure rate query", document_id: fakeId("doc"), metadata: {} },
        { snippet: "Pressure gauge mid-range 42–49 PSI is normal.", source: "manuals", boosted_score: 0.79, boost_reason: "Visual context: gauge reading at 47 PSI confirms normal operating state", document_id: fakeId("doc"), metadata: {} },
      ],
      created_at: nowMs(),
    },
  };

  yield {
    collection: "agent_traces",
    doc: { _id: fakeId("tr"), question_id: questionId, session_id: SESSION, agent: "reranker", stage: "end", model: "gpt-4o-mini", tokens: { input: 521, output: 110, total: 631 }, latency_ms: 388, timestamp: nowMs(), payload: null },
  };

  yield {
    collection: "agent_traces",
    doc: { _id: fakeId("tr"), question_id: questionId, session_id: SESSION, agent: "answerer", stage: "start", model: "gpt-4o-mini", tokens: null, latency_ms: null, timestamp: nowMs(), payload: null },
  };

  yield {
    collection: "answers",
    doc: {
      _id: answerId, question_id: questionId, session_id: SESSION,
      text: "Conveyor C-204 has a spec failure rate of 3% annually. It was last serviced 47 days ago — belt replaced and tension recalibrated to 47 N. Your current gauge reading of 47 PSI is mid-range normal (35–55 PSI window), so no immediate concern. Belt wear was at 17% on last inspection; replace threshold is 30%.",
      confidence: 0.91,
      citations: [resLogs, resManuals],
      audio_track_id: null,
      created_at: nowMs(),
    },
  };

  yield {
    collection: "agent_traces",
    doc: { _id: fakeId("tr"), question_id: questionId, session_id: SESSION, agent: "answerer", stage: "end", model: "gpt-4o-mini", tokens: { input: 610, output: 95, total: 705 }, latency_ms: 312, timestamp: nowMs(), payload: null },
  };
}

// ── server ────────────────────────────────────────────────────────────────────

const clients = new Set();

const httpServer = createServer((req, res) => {
  if (req.url === "/healthz") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, ts: nowMs() }));
    return;
  }
  // Minimal CORS for /ask and /trace
  const origin = req.headers.origin || "*";
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }

  if (req.method === "POST" && req.url === "/ask") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      // kick a new pipeline run in 200ms
      setTimeout(runPipeline, 200);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ question_id: fakeId("q") }));
    });
    return;
  }

  if (req.url?.startsWith("/trace/")) {
    // Return the last pipeline state
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ question_id: questionId || "", plan: null, results: [], final_context: null, answer: null, traces: [] }));
    return;
  }

  res.writeHead(404); res.end();
});

const wss = new WebSocketServer({ server: httpServer, path: PATH });

wss.on("connection", (ws) => {
  clients.add(ws);
  // send hello handshake matching change_streams.py Hub.add()
  ws.send(JSON.stringify({
    collection: "_meta",
    operation: "hello",
    document_id: "",
    doc: { watching: ["video_frames", "scene_context", "transcripts", "questions", "retrieval_plans", "retrieval_results", "final_context", "answers", "agent_traces"] },
    ts: nowMs(),
  }));
  ws.on("close", () => clients.delete(ws));
  ws.on("error", () => clients.delete(ws));
});

function broadcast(event) {
  const payload = JSON.stringify(event);
  for (const ws of clients) {
    if (ws.readyState === 1 /* OPEN */) ws.send(payload);
  }
}

async function runPipeline() {
  let delay = 0;
  const DELAYS = [400, 1100, 600, 300, 200, 400, 150, 250, 100, 250, 80, 350, 120, 400, 150, 500, 200];
  let i = 0;
  for (const step of pipeline()) {
    const d = DELAYS[i++ % DELAYS.length];
    await new Promise((r) => setTimeout(r, d));
    broadcast({
      collection: step.collection,
      operation: "insert",
      document_id: step.doc._id || "",
      doc: step.doc,
      ts: nowMs(),
    });
  }
}

httpServer.listen(PORT, () => {
  console.log(`mock backend listening on http://localhost:${PORT}`);
  console.log(`  WS  ws://localhost:${PORT}${PATH}`);
  console.log(`  GET http://localhost:${PORT}/healthz`);
  console.log(`  POST http://localhost:${PORT}/ask  (triggers a demo pipeline run)`);
  console.log("");
  console.log("start the dashboard:  cd dashboard && npm run dev");
  console.log("then open:            http://localhost:3000");
  console.log("");
  // Auto-run one pipeline pass after 2s so the board populates immediately.
  setTimeout(runPipeline, 2000);
});
