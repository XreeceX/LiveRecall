"use client";

import { useMemo } from "react";
import type { ChangeStreamEvent } from "@shared/types";

export function ScenePanel({ events }: { events: ChangeStreamEvent[] }) {
  const latestScene = useMemo(
    () => events.find((e) => e.collection === "scene_context")?.doc as any,
    [events],
  );
  const latestTranscript = useMemo(
    () =>
      events
        .filter(
          (e) =>
            e.collection === "transcripts" &&
            (e.doc as any)?.is_final !== false,
        )
        .slice(0, 5)
        .map((e) => e.doc as any),
    [events],
  );
  const latestQuestion = useMemo(
    () => events.find((e) => e.collection === "questions")?.doc as any,
    [events],
  );

  return (
    <section className="grid grid-cols-1 md:grid-cols-3 gap-3">
      <div className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4">
        <div className="text-xs uppercase tracking-widest text-slate-400 mb-2">
          Live scene context
        </div>
        {latestScene ? (
          <div className="space-y-2 text-sm">
            <div>
              <div className="text-slate-500 text-[11px] uppercase">objects</div>
              <div>{(latestScene.objects || []).join(" · ") || "—"}</div>
            </div>
            <div>
              <div className="text-slate-500 text-[11px] uppercase">visible text</div>
              <div className="font-mono">
                {(latestScene.text_visible || []).join("  ·  ") || "—"}
              </div>
            </div>
            <div>
              <div className="text-slate-500 text-[11px] uppercase">env / activity</div>
              <div>
                {latestScene.environment} · {latestScene.activity}
              </div>
            </div>
            <div className="text-slate-300 text-[13px] italic pt-1">
              “{latestScene.text_summary}”
            </div>
          </div>
        ) : (
          <div className="text-slate-500 text-sm">awaiting first frame…</div>
        )}
      </div>

      <div className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4">
        <div className="text-xs uppercase tracking-widest text-slate-400 mb-2">
          Live transcript
        </div>
        <ul className="space-y-1 text-sm">
          {latestTranscript.length ? (
            latestTranscript.map((t, i) => (
              <li
                key={i}
                className={
                  "leading-relaxed " +
                  (t.is_question ? "text-accent" : "text-slate-200")
                }
              >
                {t.is_question ? "❯ " : ""}
                {t.text}
              </li>
            ))
          ) : (
            <li className="text-slate-500">awaiting speech…</li>
          )}
        </ul>
      </div>

      <div className="rounded-2xl border border-ink-700 bg-ink-800/70 p-4">
        <div className="text-xs uppercase tracking-widest text-slate-400 mb-2">
          Current question
        </div>
        {latestQuestion ? (
          <div className="space-y-2">
            <div className="text-lg text-slate-100">{latestQuestion.text}</div>
            <div className="text-xs font-mono text-slate-500">
              id: {latestQuestion._id}
            </div>
          </div>
        ) : (
          <div className="text-slate-500 text-sm">no question yet</div>
        )}
      </div>
    </section>
  );
}
