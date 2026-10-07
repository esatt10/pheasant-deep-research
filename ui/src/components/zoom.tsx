import { useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

/**
 * Zoom, pan and fit for the console's visuals — the same three controls, in
 * the same corner, as pheasant-kb's graph canvas.
 *
 * Two shapes: a 2-D viewport for the constellation (a transform on one
 * group, so every node keeps its own click handler), and a 1-D time window
 * for the timelines, where zooming means showing less of the run wider —
 * scaling the lanes vertically would only make the labels unreadable.
 *
 * A drag pans. A drag is also the start of a click, so a pointer that moved
 * further than a few pixels swallows the click that ends it: panning across a
 * node must not select it.
 */

const DRAG_THRESHOLD = 4;

export interface View2D {
  k: number;
  x: number;
  y: number;
}

const IDENTITY: View2D = { k: 1, x: 0, y: 0 };

/** Pointer drag that reports deltas, and swallows the click after a real drag. */
export function useDrag(
  onDelta: (dx: number, dy: number) => void,
  host: RefObject<HTMLElement | SVGElement | null>,
) {
  const state = useRef<{ x: number; y: number; moved: boolean; id: number } | null>(null);
  const dragged = useRef(false);
  const handler = useRef(onDelta);
  handler.current = onDelta;

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    // Capture phase, so the click is gone before any node's own handler.
    const swallow = (event: Event) => {
      if (dragged.current) {
        event.stopPropagation();
        event.preventDefault();
        dragged.current = false;
      }
    };
    node.addEventListener("click", swallow, true);
    return () => node.removeEventListener("click", swallow, true);
  }, [host]);

  const onPointerDown = useCallback((event: ReactPointerEvent) => {
    if (event.button !== 0) return;
    if ((event.target as Element).closest?.("[data-nodrag]")) return;
    state.current = { x: event.clientX, y: event.clientY, moved: false, id: event.pointerId };
    dragged.current = false;
  }, []);
  const onPointerMove = useCallback((event: ReactPointerEvent) => {
    const drag = state.current;
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    if (!drag.moved) {
      drag.moved = true;
      (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
    }
    drag.x = event.clientX;
    drag.y = event.clientY;
    handler.current(dx, dy);
  }, []);
  const end = useCallback((event: ReactPointerEvent) => {
    const drag = state.current;
    if (!drag || drag.id !== event.pointerId) return;
    dragged.current = drag.moved;
    state.current = null;
  }, []);
  return { onPointerDown, onPointerMove, onPointerUp: end, onPointerCancel: end, dragging: () => !!state.current?.moved };
}

/** Attach a non-passive wheel listener (React's is passive, so it cannot stop the page scrolling). */
export function useWheel(host: RefObject<HTMLElement | null>, onWheel: (event: WheelEvent) => void) {
  const handler = useRef(onWheel);
  handler.current = onWheel;
  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const listener = (event: WheelEvent) => handler.current(event);
    node.addEventListener("wheel", listener, { passive: false });
    return () => node.removeEventListener("wheel", listener);
  }, [host]);
}

export function usePanZoom(host: RefObject<HTMLElement | null>, { min = 0.3, max = 6 } = {}) {
  const [view, setView] = useState<View2D>(IDENTITY);
  const clamp = (k: number) => Math.min(max, Math.max(min, k));

  const zoomAt = useCallback(
    (factor: number, px?: number, py?: number) =>
      setView((current) => {
        const node = host.current;
        const cx = px ?? (node ? node.clientWidth / 2 : 0);
        const cy = py ?? (node ? node.clientHeight / 2 : 0);
        const k = clamp(current.k * factor);
        const ratio = k / current.k;
        // Keep the point under the cursor where it is.
        return { k, x: cx - (cx - current.x) * ratio, y: cy - (cy - current.y) * ratio };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [host, min, max],
  );
  const fit = useCallback(() => setView(IDENTITY), []);
  /** Put the content point (px, py) at the centre of the host, at scale k. */
  const centerOn = useCallback(
    (px: number, py: number, k = 2) =>
      setView(() => {
        const node = host.current;
        const scale = clamp(k);
        const w = node ? node.clientWidth : 0;
        const h = node ? node.clientHeight : 0;
        return { k: scale, x: w / 2 - px * scale, y: h / 2 - py * scale };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [host, min, max],
  );
  const drag = useDrag((dx, dy) => setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy })), host);
  useWheel(host, (event) => {
    event.preventDefault();
    const rect = host.current!.getBoundingClientRect();
    zoomAt(Math.exp(-event.deltaY * 0.0015), event.clientX - rect.left, event.clientY - rect.top);
  });
  return {
    view,
    transform: `translate(${view.x} ${view.y}) scale(${view.k})`,
    zoomAt,
    fit,
    centerOn,
    isFit: view.k === 1 && view.x === 0 && view.y === 0,
    drag,
  };
}

/**
 * A time window over ``[0, full]``. ``null`` is "fit": the whole run, which
 * while live keeps following "now" — a zoomed window stays where it was put.
 */
export function useTimeZoom(full: number, { minSpan = 0.05 } = {}) {
  const [win, setWin] = useState<[number, number] | null>(null);
  const range: [number, number] = win ?? [0, full];

  const zoomAt = useCallback(
    (factor: number, at?: number) =>
      setWin((current) => {
        const [a, b] = current ?? [0, full];
        const pivot = at ?? (a + b) / 2;
        const span = Math.min(full, Math.max(minSpan, (b - a) / factor));
        if (span >= full) return null;
        const left = pivot - ((pivot - a) / (b - a)) * span;
        const start = Math.min(Math.max(0, left), full - span);
        return [start, start + span];
      }),
    [full, minSpan],
  );
  const panBy = useCallback(
    (dt: number) =>
      setWin((current) => {
        if (!current) return current;
        const span = current[1] - current[0];
        const start = Math.min(Math.max(0, current[0] + dt), Math.max(0, full - span));
        return [start, start + span];
      }),
    [full],
  );
  const fit = useCallback(() => setWin(null), []);
  return { range, zoomAt, panBy, fit, isFit: win === null };
}

export function ZoomControls({
  onIn,
  onOut,
  onFit,
  fitted,
  hint,
  inline,
  children,
}: {
  onIn: () => void;
  onOut: () => void;
  onFit: () => void;
  fitted?: boolean;
  hint?: string;
  inline?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className={`zoomctl${inline ? " zoomctl--inline" : ""}`} data-nodrag onPointerDown={(e) => e.stopPropagation()}>
      {children}
      <button className="zoomctl__btn" aria-label="Zoom in" title={`Zoom in${hint ? ` (${hint})` : ""}`} onClick={onIn}>
        +
      </button>
      <button className="zoomctl__btn" aria-label="Zoom out" title="Zoom out" onClick={onOut}>
        −
      </button>
      <button
        className={`zoomctl__btn zoomctl__fit${fitted ? "" : " zoomctl__fit--on"}`}
        aria-label="Fit to view"
        title="Fit to view"
        onClick={onFit}
      >
        Fit
      </button>
    </div>
  );
}
