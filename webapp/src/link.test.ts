import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { backoffDelay, Link, type InProcessServer } from "./link";

class DeadSocket {
  static OPEN = 1; static instances = 0;
  readyState = 0; onopen: (() => void) | null = null; onclose: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null; onerror: (() => void) | null = null;
  constructor() { DeadSocket.instances++; setTimeout(() => this.onclose?.(), 10); }
  send() {} close() {}
}

function echoMock(): InProcessServer & { got: string[] } {
  let emit: ((raw: string) => void) | null = null;
  const got: string[] = [];
  return {
    got,
    start(e) { emit = e; },
    stop() { emit = null; },
    receive(raw) {
      got.push(raw);
      const m = JSON.parse(raw);
      const ok = m.type !== "steer.step";
      emit?.(JSON.stringify({ type: "ack", ts: 1, id: m.id, payload: ok ? { ok } : { ok, error: "nope" } }));
    },
  };
}

describe("Link", () => {
  beforeEach(() => { vi.useFakeTimers(); (globalThis as unknown as { WebSocket: unknown }).WebSocket = DeadSocket; });
  afterEach(() => vi.useRealTimers());

  it("falls back to mock after 3 s of no backend and keeps retrying", async () => {
    const mock = echoMock();
    const link = new Link({ url: "ws://127.0.0.1:1/ws", forceMock: false, createMock: () => mock, WebSocketImpl: DeadSocket as unknown as typeof WebSocket });
    link.start();
    await vi.advanceTimersByTimeAsync(2900);
    expect(link.state.mock).toBe(false);
    await vi.advanceTimersByTimeAsync(200);
    expect(link.state.mock).toBe(true);
    const before = DeadSocket.instances;
    await vi.advanceTimersByTimeAsync(15000);
    expect(DeadSocket.instances).toBeGreaterThan(before);
    link.stop();
  });

  it("requests carry an id and resolve with the ack", async () => {
    const mock = echoMock();
    const link = new Link({ url: "x", forceMock: true, createMock: () => mock });
    link.start();
    const ok = await link.send("steer.pause", {});
    expect(ok).toEqual({ ok: true });
    const bad = await link.send("steer.step", {});
    expect(bad).toEqual({ ok: false, error: "nope" });
    const sent = JSON.parse(mock.got[0]);
    expect(sent.type).toBe("steer.pause");
    expect(typeof sent.id).toBe("string");
    expect(typeof sent.ts).toBe("number");
    expect(sent.payload).toEqual({});
    link.stop();
  });

  it("backoff grows and caps", () => {
    const fixed = () => 0.5;
    expect(backoffDelay(0, 500, 10000, fixed)).toBe(500);
    expect(backoffDelay(3, 500, 10000, fixed)).toBe(4000);
    expect(backoffDelay(20, 500, 10000, fixed)).toBe(10000);
  });
});
