"use client";

import { useEffect, useRef, useState } from "react";
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
    hint: "GPT-4o · OCR + apparatus recognition (10 meds, 23 devices) · MRNs · vitals",
  },
  {
    id: "router",
    label: "Router",
    collections: ["questions", "retrieval_plans"],
    hint: "GPT-4o-mini · 3 queries (filters by patient_id, apparatus name, category)",
  },
  {
    id: "retrievers",
    label: "Retrievers",
    collections: ["retrieval_results"],
    hint: "References (multimodal: name+context+image) · Events · Notes — local cache hits ~5ms",
  },
  {
    id: "reranker",
    label: "Reranker",
    collections: ["final_context"],
    hint: "GPT-4o-mini · boosts on visible drug + MRN + may fire active follow-up tool calls",
  },
  {
    id: "answerer",
    label: "Answerer",
    collections: ["answers"],
    hint: "GPT-4o-mini streaming · cautious decision-support tone → ElevenLabs Flash v2.5",
  },
];

// Non-vision lanes reset to idle when a new question arrives.
const RESETTABLE_LANES = ["router", "retrievers", "reranker", "answerer"];

export function Pipeline({ events, questionId }: { events: ChangeStreamEvent[]; questionId: string | null }) {
  const [activity, setActivity] = useState<Record<string, number>>({});
  const [laneFloor, setLaneFloor] = useState<Record<string, number>>({});

  // When a new question arrives, record the timestamp so we only show events
  // that came in AFTER this question was asked.
  const prevQuestionId = useRef<string | null>(null);
  useEffect(() => {
    if (questionId && questionId !== prevQuestionId.current) {
      prevQuestionId.current = questionId;
      const floor = Date.now();
      setLaneFloor((f) => Object.fromEntries(RESETTABLE_LANES.map((id) => [id, floor])));
      setActivity((a) => {
        const next = { ...a };
        RESETTABLE_LANES.forEach((id) => delete next[id]);
        return next;
      });
    }
  }, [questionId]);

  useEffect(() => {
    if (!events.length) return;
    const ev = events[0];
    const lane = LANES.find((l) => l.collections.includes(ev.collection));
    if (!lane) return;
    setActivity((a) => ({ ...a, [lane.id]: ev.ts }));
  }, [events]);

  const lastByLane = (laneId: string) => {
    const lane = LANES.find((l) => l.id === laneId)!;
    const floor = laneFloor[laneId] ?? 0;
    return events.find((e) => lane.collections.includes(e.collection) && e.ts >= floor);
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
