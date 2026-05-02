"use client";

import { useEffect, useState } from "react";
import { LatencyMonitor } from "../components/LatencyMonitor";
import { MongoFeatures } from "../components/MongoFeatures";
import { Pipeline } from "../components/Pipeline";
import { ReasoningTrace } from "../components/ReasoningTrace";
import { ScenePanel } from "../components/ScenePanel";
import { ask, STREAM_URL } from "../lib/api";
import { useStream } from "../lib/useStream";

const DEMO_QUESTION =
  "What's the failure rate on this conveyor and when was it last serviced?";

export default function DashboardPage() {
  const { events, status } = useStream(STREAM_URL);
  const [questionId, setQuestionId] = useState<string | null>(null);
  const [text, setText] = useState(DEMO_QUESTION);
  const [busy, setBusy] = useState(false);

  // Auto-select the most recent question that flows through the change stream.
  useEffect(() => {
    const q = events.find((e) => e.collection === "questions");
    if (q && (q.doc as any)?._id && (q.doc as any)._id !== questionId) {
      setQuestionId((q.doc as any)._id);
    }
  }, [events, questionId]);

  async function onAsk() {
    setBusy(true);
    try {
      const r = await ask(text);
      setQuestionId(r.question_id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">LiveRecall</h1>
          <p className="text-sm text-slate-400">
            Adaptive retrieval grounded in live visual memory · MongoDB Atlas + LiveKit
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={
              "text-[11px] uppercase tracking-widest px-2 py-1 rounded-full " +
              (status === "open"
                ? "bg-accent/10 text-accent"
                : "bg-slate-700/40 text-slate-400")
            }
          >
            change stream · {status}
          </span>
        </div>
      </header>

      <Pipeline events={events} />

      <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6">
        <div className="space-y-6">
          <ScenePanel events={events} />

          <div className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4 space-y-3">
            <div className="text-xs uppercase tracking-widest text-slate-400">
              Ask (text-only kickoff)
            </div>
            <div className="flex gap-2">
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                className="flex-1 bg-ink-900 border border-ink-700 rounded-lg px-3 py-2 text-sm"
              />
              <button
                disabled={busy}
                onClick={onAsk}
                className="bg-accent text-emerald-950 font-semibold px-4 py-2 rounded-lg disabled:opacity-50"
              >
                {busy ? "asking…" : "Ask"}
              </button>
            </div>
            <div className="text-[11px] text-slate-500">
              Same path the phone takes — STT inserts a <code>questions</code> doc; agents fan out.
            </div>
          </div>

          <LatencyMonitor questionId={questionId} />
          <ReasoningTrace questionId={questionId} />
        </div>

        <MongoFeatures events={events} />
      </div>

      <footer className="text-[11px] text-slate-500 text-center pt-6">
        “Adaptive retrieval. Live visual memory. MongoDB and LiveKit as the brain and the bloodstream.”
      </footer>
    </main>
  );
}
