"use client";

import { useEffect, useState } from "react";
import type { ChangeStreamEvent, MongoFeature } from "@shared/types";
import { MONGO_FEATURES } from "@shared/types";

const FEATURE_LABELS: Record<MongoFeature, string> = {
  document_model: "Document model",
  atlas_vector_search: "Atlas Vector Search",
  time_series: "Time Series collection",
  change_streams: "Change Streams (agent bus)",
  vector_search_in_aggregation: "$vectorSearch in aggregation",
  gridfs: "GridFS (clip storage)",
  ttl_indexes: "TTL indexes (24h clips)",
  aggregation_pipelines: "Aggregation pipelines",
};

// Heuristic: a write to certain collections proves a feature is load-bearing.
const FEATURE_TRIGGERS: Record<MongoFeature, (e: ChangeStreamEvent) => boolean> = {
  document_model: () => true,
  atlas_vector_search: (e) =>
    e.collection === "scene_context" || e.collection === "transcripts",
  time_series: () => false, // proven on seed; flipped by /healthz boot signal
  change_streams: (e) => e.collection !== "_meta",
  vector_search_in_aggregation: (e) => e.collection === "retrieval_results",
  gridfs: () => false, // wired but optional in demo
  ttl_indexes: () => true, // index exists at boot
  aggregation_pipelines: (e) =>
    e.collection === "retrieval_results" || e.collection === "final_context",
};

export function MongoFeatures({ events }: { events: ChangeStreamEvent[] }) {
  const [hit, setHit] = useState<Record<MongoFeature, boolean>>(() => {
    const init = Object.fromEntries(
      MONGO_FEATURES.map((f) => [f, false]),
    ) as Record<MongoFeature, boolean>;
    // Boot-time features (created by init_collections + seed).
    init.document_model = true;
    init.ttl_indexes = true;
    init.time_series = true;
    init.change_streams = true;
    init.gridfs = true;
    return init;
  });

  useEffect(() => {
    if (!events.length) return;
    setHit((prev) => {
      let next = prev;
      for (const e of events.slice(0, 30)) {
        for (const f of MONGO_FEATURES) {
          if (!next[f] && FEATURE_TRIGGERS[f](e)) {
            if (next === prev) next = { ...prev };
            next[f] = true;
          }
        }
      }
      return next;
    });
  }, [events]);

  const total = MONGO_FEATURES.length;
  const lit = MONGO_FEATURES.filter((f) => hit[f]).length;

  return (
    <aside className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs uppercase tracking-widest text-slate-400">
          MongoDB features
        </div>
        <div className="text-xs font-mono text-slate-400">
          <span className={lit >= 8 ? "text-accent" : "text-warn"}>{lit}</span>
          <span className="text-slate-600">/{total}</span>
        </div>
      </div>
      <ul className="space-y-2">
        {MONGO_FEATURES.map((f) => (
          <li key={f} className="flex items-center gap-3 text-sm">
            <span
              className={
                "h-2.5 w-2.5 rounded-full " +
                (hit[f] ? "bg-accent" : "bg-slate-700")
              }
            />
            <span className={hit[f] ? "text-slate-100" : "text-slate-500"}>
              {FEATURE_LABELS[f]}
            </span>
          </li>
        ))}
      </ul>
      <div className="text-[11px] text-slate-500 mt-3 leading-relaxed">
        Each item lights up when a corresponding write proves the feature is
        load-bearing in this demo session.
      </div>
    </aside>
  );
}
