"use client";

import { useMemo } from "react";
import type { CaptureMode, ChangeStreamEvent } from "@shared/types";

// Small badge that shows which capture device is currently driving the
// session — Meta Ray-Ban POV (preferred) or phone fallback. Reads
// `capture_mode` off the most recent scene_context event so it tracks the
// live state without an extra fetch. See DECISIONS.md (g) for why we run
// two parallel capture paths.
//
// Why scene_context and not sessions? Sessions writes happen at /token time
// (before the dashboard usually opens) and the change-stream hub doesn't
// fan sessions out today. scene_context streams continuously and is stamped
// with capture_mode by the Vision agent on every insert.
export function CaptureModePill({ events }: { events: ChangeStreamEvent[] }) {
  const captureMode = useMemo<CaptureMode | null>(() => {
    const latest = events.find((e) => e.collection === "scene_context")?.doc as
      | { capture_mode?: CaptureMode }
      | undefined;
    const m = latest?.capture_mode;
    return m === "glasses" || m === "phone" ? m : null;
  }, [events]);

  if (!captureMode) {
    return (
      <span
        className="text-[11px] uppercase tracking-widest px-2 py-1 rounded-full bg-slate-700/30 text-slate-500"
        title="Awaiting first scene_context — capture mode will resolve to GLASSES or PHONE."
      >
        capture · —
      </span>
    );
  }

  if (captureMode === "glasses") {
    return (
      <span
        className="text-[11px] uppercase tracking-widest px-2 py-1 rounded-full bg-gradient-to-r from-violet-500/25 to-blue-500/20 text-violet-200 border border-violet-400/40"
        title="Headline POV: Meta Ray-Ban first-person capture (or any first-person-framed stand-in)."
      >
        🕶 glasses
      </span>
    );
  }

  return (
    <span
      className="text-[11px] uppercase tracking-widest px-2 py-1 rounded-full bg-slate-500/20 text-slate-200 border border-slate-400/30"
      title="Phone fallback — universal safety mode. Every clinician already has one; no hardware purchase needed."
    >
      📱 phone <span className="ml-1 text-slate-400 normal-case tracking-normal">· fallback</span>
    </span>
  );
}
