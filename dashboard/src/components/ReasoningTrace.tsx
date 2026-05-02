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
                      <span>
                        {r.latency_ms}ms · {r.results.length} hits
                        {r.from_cache ? (
                          <span
                            className="ml-1 rounded bg-emerald-500/20 px-1 py-[1px] text-[10px] font-semibold text-emerald-300"
                            title="Served from local prefetched cache (Vision warmed it earlier)."
                          >
                            LOCAL
                          </span>
                        ) : null}
                      </span>
                    </div>
                    <ul className="mt-2 space-y-2 text-[12px] text-slate-300">
                      {r.results.slice(0, 3).map((h: any, i: number) => (
                        <li key={i} className="flex gap-2">
                          <ApparatusThumb meta={h.metadata} />
                          <div className="min-w-0 flex-1">
                            <ApparatusLabel meta={h.metadata} />
                            <div className="line-clamp-2">{h.snippet}</div>
                          </div>
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

          {data.final_context?.active_followups?.length ? (
            <Section title="Active retrieval · follow-up tool calls">
              <ul className="space-y-2 text-sm">
                {data.final_context.active_followups.map(
                  (f: any, i: number) => (
                    <li
                      key={i}
                      className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-2"
                    >
                      <div className="flex items-center justify-between text-[11px] font-mono text-amber-300">
                        <span className="uppercase">{f.tool}</span>
                        <span>{f.latency_ms}ms</span>
                      </div>
                      <div className="text-[11px] mt-1 text-amber-200/80">
                        because: {f.reason || "(no reason given)"}
                      </div>
                      <div className="text-slate-200 mt-1">{f.snippet}</div>
                    </li>
                  ),
                )}
              </ul>
            </Section>
          ) : null}

          <Section
            title={
              data.final_context?.rerank_passes === 2
                ? "Reranker · ranked results (Pass 2 — folds in active follow-ups)"
                : "Reranker · ranked results with boost reasons"
            }
          >
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
                        <span className="flex items-center gap-1">
                          {r.metadata?.from_cache ? (
                            <span className="rounded bg-emerald-500/20 px-1 py-[1px] text-[10px] font-semibold text-emerald-300">
                              LOCAL
                            </span>
                          ) : null}
                          {r.metadata?.from_active_followup ? (
                            <span className="rounded bg-amber-500/20 px-1 py-[1px] text-[10px] font-semibold text-amber-300">
                              ACTIVE
                            </span>
                          ) : null}
                          score {Number(r.boosted_score).toFixed(2)}
                        </span>
                      </div>
                      <div className="mt-1 flex gap-3">
                        <ApparatusThumb meta={r.metadata} size="lg" />
                        <div className="min-w-0 flex-1">
                          <ApparatusLabel meta={r.metadata} />
                          <div className="text-slate-200">{r.snippet}</div>
                          <div className="text-[11px] mt-1 text-warn">
                            ▲ {r.boost_reason}
                          </div>
                        </div>
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

// --- Apparatus visuals (medication / equipment thumbnail + label) ----------
// Catalog rows from `documents` carry metadata.image_b64 + image_mime + name
// + category. Medication rows show real FDA SPL product photos; equipment
// rows show Wikipedia/Commons device photos.

interface CatalogMeta {
  name?: string;
  category?: "medication" | "equipment" | "other" | string;
  image_b64?: string;
  image_mime?: string;
  image_attribution?: string;
  image_source_url?: string;
  source_doc?: string;
  section?: string;
}

function ApparatusThumb({
  meta,
  size = "sm",
}: {
  meta: CatalogMeta | undefined;
  size?: "sm" | "lg";
}) {
  if (!meta?.image_b64) return null;
  const dim = size === "lg" ? 64 : 40;
  const src = `data:${meta.image_mime || "image/jpeg"};base64,${meta.image_b64}`;
  return (
    <img
      src={src}
      alt={meta.name || "apparatus"}
      width={dim}
      height={dim}
      title={meta.image_attribution || meta.source_doc || meta.name}
      className="flex-none rounded border border-ink-700 bg-ink-900 object-cover"
      style={{ width: dim, height: dim }}
    />
  );
}

function ApparatusLabel({ meta }: { meta: CatalogMeta | undefined }) {
  if (!meta?.name && !meta?.category) return null;
  const isEquip = meta.category === "equipment";
  return (
    <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5">
      <span
        className={
          isEquip
            ? "rounded bg-sky-500/20 px-1 py-[1px] text-sky-300"
            : "rounded bg-fuchsia-500/20 px-1 py-[1px] text-fuchsia-300"
        }
      >
        {isEquip ? "EQUIPMENT" : meta.category === "medication" ? "MED" : "REF"}
      </span>{" "}
      {meta.name ? <span className="text-slate-300">{meta.name}</span> : null}
    </div>
  );
}
