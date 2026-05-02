"use client";

import { useEffect, useState } from "react";
import type { ChangeStreamEvent } from "@shared/types";

interface Lane {
  id: string;
  label: string;
  collections: string[];
  hint: string;
}

const LANES: Lane[] = [
  {
    id: "vision",
    label: "Vision",
    collections: ["video_frames", "scene_context"],
    hint: "GPT-4o · structured scene extraction",
  },
  {
    id: "router",
    label: "Router",
    collections: ["questions", "retrieval_plans"],
    hint: "GPT-4o-mini · 3 differentiated queries",
  },
  {
    id: "retrievers",
    label: "Retrievers",
    collections: ["retrieval_results"],
    hint: "Manuals · Logs · History · parallel Mongo aggregation",
  },
  {
    id: "reranker",
    label: "Reranker",
    collections: ["final_context"],
    hint: "GPT-4o-mini · boosts based on what was just seen",
  },
  {
    id: "answerer",
    label: "Answerer",
    collections: ["answers"],
    hint: "GPT-4o-mini streaming → ElevenLabs Flash v2.5",
  },
];

export function Pipeline({ events }: { events: ChangeStreamEvent[] }) {
  // Track the most recent event timestamp per lane to drive a glow.
  const [activity, setActivity] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!events.length) return;
    const ev = events[0];
    const lane = LANES.find((l) => l.collections.includes(ev.collection));
    if (!lane) return;
    setActivity((a) => ({ ...a, [lane.id]: ev.ts }));
  }, [events]);

  const lastByLane = (laneId: string) => {
    const lane = LANES.find((l) => l.id === laneId)!;
    return events.find((e) => lane.collections.includes(e.collection));
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
      {LANES.map((lane) => {
        const active = activity[lane.id] && Date.now() - activity[lane.id] < 2200;
        const last = lastByLane(lane.id);
        return (
          <div
            key={lane.id}
            className={
              "rounded-2xl border border-ink-700 bg-ink-800/70 p-4 min-h-[150px] flex flex-col gap-2 transition " +
              (active ? "lane-glow" : "")
            }
          >
            <div className="flex items-center justify-between">
              <div className="text-xs uppercase tracking-widest text-slate-400">
                {lane.label}
              </div>
              <span
                className={
                  "h-2 w-2 rounded-full " +
                  (active ? "bg-accent" : "bg-slate-700")
                }
              />
            </div>
            <div className="text-sm font-medium text-slate-100">{lane.hint}</div>
            <div className="text-[11px] text-slate-500 font-mono mt-auto">
              {last ? (
                <>
                  <div>{last.collection}</div>
                  <div>{new Date(last.ts).toLocaleTimeString()}</div>
                </>
              ) : (
                "idle"
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
