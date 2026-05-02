"use client";

import { useEffect, useState } from "react";
import { BACKEND } from "../lib/api";

interface TraceResponse {
  question_id: string;
  plan: any | null;
  results: any[];
  final_context: any | null;
  answer: any | null;
  traces: any[];
}

export function ReasoningTrace({ questionId }: { questionId: string | null }) {
  const [data, setData] = useState<TraceResponse | null>(null);
  const [polling, setPolling] = useState(false);

  useEffect(() => {
    setData(null);
    if (!questionId) return;
    setPolling(true);
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetch(`${BACKEND}/trace/${questionId}`);
        if (r.ok) {
          const j = (await r.json()) as TraceResponse;
          if (!cancelled) setData(j);
          if (j.answer) {
            setPolling(false);
            return;
          }
        }
      } catch {
        /* ignore */
      }
      if (!cancelled) setTimeout(poll, 600);
    };
    poll();
    return () => {
      cancelled = true;
      setPolling(false);
    };
  }, [questionId]);

  if (!questionId) {
    return (
      <div className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4 text-sm text-slate-500">
        Ask a question (via phone or the box above) to see the full reasoning chain.
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-widest text-slate-400">
          Reasoning trace
        </div>
        <div className="text-xs font-mono text-slate-500">{questionId}</div>
      </div>

      {!data ? (
        <div className="text-slate-500 text-sm">loading…</div>
      ) : (
        <>
          <Section title="Router · retrieval plan">
            {data.plan ? (
              <ul className="text-sm space-y-1">
                {(data.plan.queries || []).map((q: any, i: number) => (
                  <li key={i} className="font-mono text-slate-300">
                    <span className="text-accent">{q.source}</span>{" "}
                    filter={JSON.stringify(q.filter)}{" "}
                    vec=<span className="text-slate-100">"{q.vector_query}"</span>
                  </li>
                ))}
              </ul>
            ) : (
              <Pending />
            )}
          </Section>

          <Section title="Retrievers · per-source hits">
            {data.results.length ? (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                {data.results.map((r: any) => (
                  <div
                    key={r._id}
                    className="rounded-lg border border-ink-700 p-2"
                  >
                    <div className="flex items-center justify-between text-[11px] font-mono text-slate-400">
                      <span className="text-accent uppercase">{r.source}</span>
                      <span>{r.latency_ms}ms · {r.results.length} hits</span>
                    </div>
                    <ul className="mt-2 space-y-1 text-[12px] text-slate-300">
                      {r.results.slice(0, 3).map((h: any, i: number) => (
                        <li key={i} className="line-clamp-2">
                          {h.snippet}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            ) : (
              <Pending />
            )}
          </Section>

          <Section title="Reranker · ranked results with boost reasons">
            {data.final_context ? (
              <ul className="space-y-2 text-sm">
                {(data.final_context.ranked_results || []).map(
                  (r: any, i: number) => (
                    <li
                      key={i}
                      className="rounded-lg border border-ink-700 p-2"
                    >
                      <div className="flex items-center justify-between text-[11px] font-mono text-slate-400">
                        <span className="text-accent uppercase">{r.source}</span>
                        <span>score {Number(r.boosted_score).toFixed(2)}</span>
                      </div>
                      <div className="text-slate-200 mt-1">{r.snippet}</div>
                      <div className="text-[11px] mt-1 text-warn">
                        ▲ {r.boost_reason}
                      </div>
                    </li>
                  ),
                )}
              </ul>
            ) : (
              <Pending />
            )}
          </Section>

          <Section title="Answerer · response">
            {data.answer ? (
              <div className="text-base text-slate-100 leading-relaxed">
                “{data.answer.text}”
              </div>
            ) : (
              <Pending />
            )}
          </Section>
        </>
      )}
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-widest text-slate-500 mb-2">
        {title}
      </div>
      {children}
    </div>
  );
}

function Pending() {
  return <div className="text-slate-500 text-sm">…</div>;
}
