"use client";

import { useMemo } from "react";
import type { ChangeStreamEvent } from "@shared/types";

export function CameraFeed({ events }: { events: ChangeStreamEvent[] }) {
  const latestFrame = useMemo(
    () => events.find((e) => e.collection === "video_frames")?.doc as any,
    [events],
  );

  const latestScene = useMemo(
    () => events.find((e) => e.collection === "scene_context")?.doc as any,
    [events],
  );

  const isAnalyzed =
    latestScene && latestFrame && latestScene.source_frame_id === latestFrame._id;

  const hasImage =
    latestFrame?.image_b64 &&
    typeof latestFrame.image_b64 === "string" &&
    !latestFrame.image_b64.startsWith("<");

  return (
    <div className="rounded-2xl border border-ink-700 bg-ink-800/70 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-ink-700">
        <div className="text-xs uppercase tracking-widest text-slate-400">
          Live camera feed
        </div>
        <div className="flex items-center gap-2">
          {isAnalyzed && (
            <span className="text-[10px] uppercase tracking-widest px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400">
              analyzed
            </span>
          )}
          {latestFrame && (
            <span className="text-[10px] text-slate-500 font-mono">
              {latestFrame.capture_mode ?? "phone"}
            </span>
          )}
        </div>
      </div>

      {hasImage ? (
        <img
          src={`data:image/jpeg;base64,${latestFrame.image_b64}`}
          alt="Latest camera frame"
          className="w-full object-contain bg-black max-h-64"
        />
      ) : (
        <div className="aspect-video flex items-center justify-center bg-black/60">
          <span className="text-slate-500 text-sm">
            {latestFrame ? "frame data stripped" : "awaiting first frame…"}
          </span>
        </div>
      )}

      {latestScene && (
        <div className="px-4 py-2 text-[11px] text-slate-400 italic border-t border-ink-700 truncate">
          "{latestScene.text_summary}"
        </div>
      )}
    </div>
  );
}
