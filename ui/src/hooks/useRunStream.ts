import { useEffect, useRef, useState } from "react";
import type { LabEvent, RunModel } from "../types";

const FEED_LIMIT = 800;

/**
 * One run, live: the server folds the trace and pushes `model`; `events` is
 * the raw append-only feed. A reconnect resumes after the last sequence it
 * saw, so the feed neither repeats nor skips.
 */
export function useRunStream(runId: string | undefined) {
  const [model, setModel] = useState<RunModel>();
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const lastSequence = useRef(0);

  useEffect(() => {
    if (!runId) return;
    setModel(undefined);
    setEvents([]);
    lastSequence.current = 0;
    let source: EventSource | null = null;
    let retry: number | undefined;
    let closed = false;

    const open = () => {
      source = new EventSource(`/api/runs/${runId}/stream?after=${lastSequence.current}`);
      source.onopen = () => setConnected(true);
      source.addEventListener("events", (message) => {
        const batch = JSON.parse((message as MessageEvent).data) as LabEvent[];
        if (!batch.length) return;
        lastSequence.current = batch[batch.length - 1].sequence;
        setEvents((current) => [...current, ...batch].slice(-FEED_LIMIT));
      });
      source.addEventListener("model", (message) => {
        setModel(JSON.parse((message as MessageEvent).data) as RunModel);
      });
      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) retry = window.setTimeout(open, 1500);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      source?.close();
    };
  }, [runId]);

  return { model, events, connected };
}
