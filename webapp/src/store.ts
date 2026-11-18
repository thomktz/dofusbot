import type { AgentGoalStatus, AgentStatus, DofusStatus, GameState, LogEntry, NpcsList, NetworkScanStatus, OverlayStatus, PlannerStatus, ServerMessage, ServerPayloads, SlotReport } from "./protocol";
import type { LinkState } from "./link";

export const MAX_LOGS = 1500;

export interface Toast { id: number; kind: "error" | "info"; text: string; ts: number }

export interface AppState {
  link: LinkState;
  hello: { version: number; server: string; mock: boolean } | null;
  status: AgentStatus | null;
  statusAt: number | null;
  game: GameState | null;
  overlay: OverlayStatus | null;
  logs: LogEntry[];
  preview: { src: string; width: number; height: number; at: number } | null;
  slot: SlotReport | null;
  toasts: Toast[];
  npcs: NpcsList | null;
  planner: PlannerStatus | null;
  network: NetworkScanStatus;
  dofus: DofusStatus;
  transport: ServerPayloads["transport.status"];
  accounts: ServerPayloads["accounts.status"];
  calibration: { state: string; message: string; samples: number; cell?: number; screen?: [number, number]; relative?: [number, number] } | null;
  agentGoal: AgentGoalStatus;
}

export type AppAction =
  | { t: "link"; s: LinkState }
  | { t: "msg"; m: ServerMessage }
  | { t: "slot"; r: SlotReport }
  | { t: "toast"; kind: Toast["kind"]; text: string }
  | { t: "dismiss"; id: number }
  | { t: "clearPreview" };

export function initialState(link: LinkState): AppState {
  return { link, hello: null, status: null, statusAt: null, game: null, overlay: null, logs: [], preview: null, slot: null, toasts: [], npcs: null, planner: null,
    network: { state: "idle", result: null, error: null }, dofus: { state: "idle", app: "Dofus", error: null }, transport: { state: "offline", session_id: null, error: null, uid: 1, sent: 0 }, accounts: { active_account_id: 0, mode: "independent", model_runtime: "shared", accounts: [] }, calibration: null, agentGoal: { active: false, run: null } };
}

let toastSeq = 0;

export function reducer(s: AppState, a: AppAction): AppState {
  switch (a.t) {
    case "link": {
      // Switching source (mock <-> real) invalidates everything the old source told us.
      const switched = a.s.mock !== s.link.mock;
      return switched ? { ...s, link: a.s, hello: null, status: null, game: null, overlay: null, preview: null, logs: [], npcs: null, planner: null,
        network: { state: "idle", result: null, error: null }, dofus: { state: "idle", app: "Dofus", error: null }, transport: { state: "offline", session_id: null, error: null, uid: 1, sent: 0 }, accounts: { active_account_id: 0, mode: "independent", model_runtime: "shared", accounts: [] }, calibration: null, agentGoal: { active: false, run: null } } : { ...s, link: a.s };
    }
    case "slot": return { ...s, slot: a.r };
    case "toast": {
      const t: Toast = { id: ++toastSeq, kind: a.kind, text: a.text, ts: Date.now() };
      return { ...s, toasts: [...s.toasts.slice(-4), t] };
    }
    case "dismiss": return { ...s, toasts: s.toasts.filter((t) => t.id !== a.id) };
    case "clearPreview": return { ...s, preview: null };
    case "msg": return applyMessage(s, a.m);
  }
}

export function applyMessage(s: AppState, m: ServerMessage): AppState {
  switch (m.type) {
    case "hello": return { ...s, hello: m.payload };
    case "agent.status": return { ...s, status: m.payload, statusAt: Date.now() };
    case "game.state": return { ...s, game: m.payload };
    case "overlay.status": return { ...s, overlay: m.payload };
    case "log.history": return { ...s, logs: m.payload.entries.slice(-MAX_LOGS) };
    case "log.entry": {
      const logs = s.logs.length >= MAX_LOGS ? [...s.logs.slice(s.logs.length - MAX_LOGS + 1), m.payload] : [...s.logs, m.payload];
      return { ...s, logs };
    }
    case "frame.preview":
      return { ...s, preview: { src: `data:image/jpeg;base64,${m.payload.jpeg_b64}`, width: m.payload.width, height: m.payload.height, at: Date.now() } };
    case "npcs.list": return { ...s, npcs: m.payload };
    case "planner.status": return { ...s, planner: m.payload };
    case "network.scan.status": return { ...s, network: m.payload };
    case "dofus.status": return { ...s, dofus: m.payload };
    case "transport.status": return { ...s, transport: m.payload };
    case "accounts.status": return { ...s, accounts: m.payload };
    case "calibration.status": return { ...s, calibration: m.payload };
    case "agent.goal.status": return { ...s, agentGoal: m.payload };
    case "ack": return s; // handled by Link (promise) + toasts in App
  }
}
