"use client";

import { useEffect, useRef, useState } from "react";
import type { ChangeStreamEvent } from "@shared/types";

export interface StreamState {
  events: ChangeStreamEvent[];
  byCollection: Record<string, ChangeStreamEvent[]>;
  status: "connecting" | "open" | "closed";
}

export function useStream(url: string, max = 200): StreamState {
  const [events, setEvents] = useState<ChangeStreamEvent[]>([]);
  const [status, setStatus] = useState<StreamState["status"]>("connecting");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let backoff = 500;

    function open() {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      setStatus("connecting");
      ws.onopen = () => {
        backoff = 500;
        setStatus("open");
      };
      ws.onmessage = (m) => {
        try {
          const ev = JSON.parse(m.data) as ChangeStreamEvent;
          setEvents((prev) => {
            const next = [ev, ...prev];
            if (next.length > max) next.length = max;
            return next;
          });
        } catch {
          /* ignore */
        }
      };
      ws.onclose = () => {
        setStatus("closed");
        if (cancelled) return;
        backoff = Math.min(backoff * 2, 8000);
        setTimeout(open, backoff);
      };
      ws.onerror = () => ws.close();
    }
    open();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, [url, max]);

  const byCollection: Record<string, ChangeStreamEvent[]> = {};
  for (const e of events) {
    (byCollection[e.collection] ||= []).push(e);
  }

  return { events, byCollection, status };
}
