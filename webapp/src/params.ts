import { parseSlotParam } from "./slot";

// The observe/network backend listens on 8765. The in-browser mock remains
// available explicitly with ?mock=1.
// Must match run.sh's default BRAIN_PORT. A non-default port is carried in
// the URL by run.sh as ?ws=ws://127.0.0.1:<port>/ws.
export const DEFAULT_WS_URL = "ws://127.0.0.1:8765/ws";

export interface AppParams {
  wsUrl: string;
  forceMock: boolean;
  slot: { w: number; h: number };
  grid: boolean;      // start with calibration grid overlay shown
  zoom: number;       // zoom_hint (only if the user knows the page is zoomed)
  pane: boolean;      // right-pane layout (default): no slot, Dofus sits beside the browser
}

export function parseParams(search: string): AppParams {
  const q = new URLSearchParams(search);
  const flag = (k: string) => { const v = q.get(k); return v !== null && v !== "0" && v !== "false"; };
  const zoom = Number(q.get("zoom") ?? "1");
  return {
    wsUrl: q.get("ws") || DEFAULT_WS_URL,
    forceMock: flag("mock"),
    slot: parseSlotParam(q.get("slot")),
    grid: flag("grid"),
    zoom: Number.isFinite(zoom) && zoom > 0.25 && zoom < 5 ? zoom : 1,
    pane: q.get("layout") !== "slot",
  };
}
