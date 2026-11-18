// WebSocket client with reconnect/backoff, request ids + acks, and automatic in-browser mock fallback.
import type { ClientPayloads, ClientType, ServerMessage } from "./protocol";
import { parseServerMessage } from "./protocol";

export type ConnState = "connecting" | "open" | "closed";
export interface LinkState { conn: ConnState; mock: boolean; forced: boolean; url: string; attempts: number; nextRetryMs: number | null }
export interface AckResult { ok: boolean; error?: string }

/** Anything that can play the server role in-process (the mock backend). */
export interface InProcessServer {
  start(emit: (raw: string) => void): void;
  stop(): void;
  receive(raw: string): void;
}

export interface LinkOptions {
  url: string;
  forceMock: boolean;
  createMock: () => InProcessServer;
  fallbackMs?: number;   // real backend unreachable this long → mock (default 3000)
  ackTimeoutMs?: number; // default 5000
  WebSocketImpl?: typeof WebSocket;
}

export function backoffDelay(attempt: number, base = 500, max = 10_000, rnd = Math.random): number {
  const d = Math.min(max, base * 2 ** Math.min(attempt, 10));
  return Math.round(d * (0.8 + 0.4 * rnd()));
}

const WS_OPEN = 1;
let seq = 0;
export const newRequestId = () => `req-${Date.now().toString(36)}-${(++seq).toString(36)}`;

export class Link {
  private ws: WebSocket | null = null;
  private mock: InProcessServer | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  private pending = new Map<string, { resolve: (r: AckResult) => void; timer: ReturnType<typeof setTimeout> }>();
  private msgListeners = new Set<(m: ServerMessage) => void>();
  private stateListeners = new Set<(s: LinkState) => void>();
  private stopped = true;
  state: LinkState;

  constructor(private opts: LinkOptions) {
    this.state = { conn: "closed", mock: false, forced: opts.forceMock, url: opts.url, attempts: 0, nextRetryMs: null };
  }

  onMessage(fn: (m: ServerMessage) => void) { this.msgListeners.add(fn); return () => { this.msgListeners.delete(fn); }; }
  onState(fn: (s: LinkState) => void) { this.stateListeners.add(fn); fn(this.state); return () => { this.stateListeners.delete(fn); }; }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    if (this.opts.forceMock) { this.startMock(); return; }
    this.armFallback();
    this.connect();
  }

  stop() {
    this.stopped = true;
    this.clearTimers();
    this.stopMock();
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
    for (const [, p] of this.pending) { clearTimeout(p.timer); p.resolve({ ok: false, error: "link stopped" }); }
    this.pending.clear();
    this.setState({ conn: "closed" });
  }

  /** Send a request. Resolves with the ack (or a synthetic failure on timeout / not connected). */
  send<K extends ClientType>(type: K, payload: ClientPayloads[K], wantAck = true): Promise<AckResult> {
    const id = wantAck ? newRequestId() : undefined;
    const raw = JSON.stringify({ type, ts: Date.now(), ...(id ? { id } : {}), payload });
    const target = this.mock ? "mock" : this.ws && this.ws.readyState === WS_OPEN ? "ws" : null;
    if (!target) return Promise.resolve({ ok: false, error: "not connected" });
    const p = id
      ? new Promise<AckResult>((resolve) => {
          const timer = setTimeout(() => { this.pending.delete(id); resolve({ ok: false, error: `no ack for ${type} (timeout)` }); },
            this.opts.ackTimeoutMs ?? 5000);
          this.pending.set(id, { resolve, timer });
        })
      : Promise.resolve<AckResult>({ ok: true });
    if (target === "mock") this.mock!.receive(raw); else this.ws!.send(raw);
    return p;
  }

  // ---- internals ----

  private setState(patch: Partial<LinkState>) {
    this.state = { ...this.state, ...patch };
    for (const fn of this.stateListeners) fn(this.state);
  }

  private dispatch(raw: string) {
    const m = parseServerMessage(raw);
    if (!m) { console.warn("[link] ignored frame", raw.slice(0, 200)); return; }
    if (m.type === "ack" && m.id) {
      const p = this.pending.get(m.id);
      if (p) { clearTimeout(p.timer); this.pending.delete(m.id); p.resolve(m.payload); }
    }
    for (const fn of this.msgListeners) fn(m);
  }

  private connect() {
    if (this.stopped) return;
    this.retryTimer = null;
    if (!this.mock) this.setState({ conn: "connecting", nextRetryMs: null });
    const WS = this.opts.WebSocketImpl ?? WebSocket;
    let ws: WebSocket;
    try { ws = new WS(this.opts.url); } catch { this.scheduleRetry(); return; }
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.clearFallback();
      this.stopMock();
      this.setState({ conn: "open", mock: false, attempts: 0, nextRetryMs: null });
    };
    ws.onmessage = (ev) => { if (this.ws === ws && !this.mock && typeof ev.data === "string") this.dispatch(ev.data); };
    ws.onerror = () => { /* onclose follows */ };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      const wasOpen = this.state.conn === "open" && !this.state.mock;
      if (!this.mock) this.setState({ conn: "closed" });
      if (wasOpen) this.armFallback();
      this.scheduleRetry();
    };
  }

  private scheduleRetry() {
    if (this.stopped || this.retryTimer) return;
    const attempts = this.state.attempts + 1;
    const delay = backoffDelay(attempts - 1);
    this.setState({ attempts, nextRetryMs: Date.now() + delay });
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }

  private armFallback() {
    this.clearFallback();
    this.fallbackTimer = setTimeout(() => {
      this.fallbackTimer = null;
      if (!this.stopped && !(this.ws && this.ws.readyState === WS_OPEN)) this.startMock();
    }, this.opts.fallbackMs ?? 3000);
  }

  private startMock() {
    if (this.mock) return;
    this.mock = this.opts.createMock();
    this.setState({ conn: "open", mock: true });
    this.mock.start((raw) => this.dispatch(raw));
  }

  private stopMock() {
    if (!this.mock) return;
    this.mock.stop();
    this.mock = null;
  }

  private clearFallback() { if (this.fallbackTimer) { clearTimeout(this.fallbackTimer); this.fallbackTimer = null; } }
  private clearTimers() {
    this.clearFallback();
    if (this.retryTimer) { clearTimeout(this.retryTimer); this.retryTimer = null; }
  }
}
