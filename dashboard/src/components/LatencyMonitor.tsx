"use client";

import { useEffect, useMemo, useState } from "react";
import { BACKEND } from "../lib/api";

interface Stage {
  label: string;
  agentMatch: (agent: string) => boolean;
  color: string;
}

const STAGES: Stage[] = [
  { label: "STT", agentMatch: (a) => a === "stt", color: "bg-sky-500/70" },
  { label: "Router", agentMatch: (a) => a === "router", color: "bg-violet-500/70" },
  { label: "Retrievers", agentMatch: (a) => a.startsWith("retriever:"), color: "bg-emerald-500/70" },
  { label: "Reranker", agentMatch: (a) => a === "reranker", color: "bg-amber-500/70" },
  { label: "Answerer", agentMatch: (a) => a === "answerer", color: "bg-rose-500/70" },
];

const TARGET_MS = 2000;
const CEILING_MS = 2500;

export function LatencyMonitor({ questionId }: { questionId: string | null }) {
  const [traces, setTraces] = useState<any[]>([]);

  useEffect(() => {
    setTraces([]);
    if (!questionId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${BACKEND}/trace/${questionId}`);
        if (r.ok) {
          const j = await r.json();
          if (!cancelled) setTraces(j.traces || []);
        }
      } catch {}
      if (!cancelled) setTimeout(tick, 700);
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, [questionId]);

  const { rows, total } = useMemo(() => {
    const rows = STAGES.map((s) => {
      const ms = traces
        .filter((t) => t.stage === "end" && s.agentMatch(t.agent))
        .reduce((acc, t) => acc + (t.latency_ms || 0), 0);
      return { stage: s, ms };
    });
    const total = rows.reduce((a, b) => a + b.ms, 0);
    return { rows, total };
  }, [traces]);

  const status =
    total === 0
      ? "idle"
      : total <= TARGET_MS
        ? "green"
        : total <= CEILING_MS
          ? "yellow"
          : "red";
  const statusColor =
    status === "green"
      ? "text-accent"
      : status === "yellow"
        ? "text-warn"
        : status === "red"
          ? "text-bad"
          : "text-slate-500";

  const max = Math.max(total, TARGET_MS);

  return (
    <div className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-widest text-slate-400">
          Latency monitor
        </div>
        <div className={"text-sm font-mono " + statusColor}>
          {total > 0 ? `${total} ms` : "—"} <span className="text-slate-600">/ ≤2000 target</span>
        </div>
      </div>
      <div className="flex gap-1 h-3 rounded-full overflow-hidden bg-ink-900">
        {rows.map(({ stage, ms }) => (
          <div
            key={stage.label}
            className={stage.color}
            style={{ width: `${(ms / max) * 100}%` }}
            title={`${stage.label}: ${ms}ms`}
          />
        ))}
      </div>
      <ul className="grid grid-cols-5 gap-2 text-[11px] font-mono text-slate-400">
        {rows.map(({ stage, ms }) => (
          <li key={stage.label} className="flex items-center gap-1.5">
            <span className={"h-2 w-2 rounded-sm " + stage.color} />
            <span>
              {stage.label} <span className="text-slate-200">{ms}ms</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
