import { useEffect, useRef, useState } from "react";
import { authHeaders, onAuthChanged, signalAuthRequired } from "../api";
import type { LabEvent, RunModel } from "../types";

const FEED_LIMIT = 800;

/** One parsed server-sent event. */
interface SseMessage {
  event: string;
  data: string;
}

/** Split an SSE byte stream into messages; returns the unparsed remainder. */
export function parseSse(buffer: string): { messages: SseMessage[]; rest: string } {
  const messages: SseMessage[] = [];
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) messages.push({ event, data: data.join("\n") });
  }
  return { messages, rest };
}

export type StreamState = "connecting" | "live" | "reconnecting" | "locked";

/**
 * One run, live: the server folds the trace and pushes `model`; `events` is
 * the raw append-only feed. A reconnect resumes after the last sequence it
 * saw, so the feed neither repeats nor skips.
 *
 * Read with `fetch` rather than `EventSource`, because an `EventSource`
 * cannot send an `Authorization` header and a console with an access key
 * refuses a stream without one. Putting the key in the URL instead would
 * write it into every proxy log the stream passes through.
 */
export function useRunStream(runId: string | undefined) {
  const [model, setModel] = useState<RunModel>();
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const [keyEpoch, setKeyEpoch] = useState(0);
  const lastSequence = useRef(0);

  useEffect(() => onAuthChanged(() => setKeyEpoch((n) => n + 1)), []);

  useEffect(() => {
    if (!runId) return;
    setModel(undefined);
    setEvents([]);
    lastSequence.current = 0;
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let closed = false;
    let retry: number | undefined;
    let controller: AbortController | null = null;
    // Events arrive in pages; applying each page as its own render would
    // re-lay out the constellation hundreds of times during a backfill.
    // Batch them into one state update per animation frame.
    let pending: LabEvent[] = [];
    let frame = 0;
    const flush = () => {
      frame = 0;
      const batch = pending;
      pending = [];
      if (batch.length) setEvents((current) => [...current, ...batch].slice(-FEED_LIMIT));
    };

    const open = async () => {
      controller = new AbortController();
      setState((s) => (s === "live" ? "reconnecting" : s));
      try {
        const response = await fetch(`/api/runs/${runId}/stream?after=${lastSequence.current}`, {
          headers: { Accept: "text/event-stream", ...authHeaders() },
          signal: controller.signal,
        });
        if (response.status === 401) {
          setState("locked");
          signalAuthRequired();
          return; // the key dialog's change re-opens the stream
        }
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        setState("live");
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          const parsed = parseSse(buffer + value);
          buffer = parsed.rest;
          for (const message of parsed.messages) {
            if (message.event === "events") {
              const batch = JSON.parse(message.data) as LabEvent[];
              if (!batch.length) continue;
              lastSequence.current = batch[batch.length - 1].sequence;
              pending.push(...batch);
              if (!frame) frame = window.requestAnimationFrame(flush);
            } else if (message.event === "model") {
              setModel(JSON.parse(message.data) as RunModel);
            }
          }
        }
        throw new Error("stream ended");
      } catch {
        if (closed) return;
        setState("reconnecting");
        retry = window.setTimeout(() => void open(), 1500);
      }
    };
    void open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.cancelAnimationFrame(frame);
      controller?.abort();
    };
  }, [runId, keyEpoch]);

  return { model, events, connected: state === "live", state };
}
