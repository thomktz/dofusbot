import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MockBackend } from "./mock/mockBackend";
import { parseServerMessage } from "./protocol";
import { initialState, reducer } from "./store";
import type { LinkState } from "./link";

const link: LinkState = { conn: "open", mock: true, forced: true, url: "ws://x", attempts: 0, nextRetryMs: null };

function harness() {
  const got: { type: string; id?: string; payload: any }[] = [];
  const mock = new MockBackend();
  mock.start((raw) => got.push(JSON.parse(raw)));
  let n = 0;
  const send = async (type: string, payload: object = {}) => {
    const id = `r${++n}`;
    mock.receive(JSON.stringify({ type, ts: 0, id, payload }));
    await vi.advanceTimersByTimeAsync(200);
    return got.find((m) => m.type === "ack" && m.id === id)!.payload as { ok: boolean; error?: string };
  };
  return { mock, got, send };
}

describe("combat flow (mock backend)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("locates NPCs, refuses to attack an NPC, attacks a monster in auto mode", async () => {
    const { mock, got, send } = harness();
    await vi.advanceTimersByTimeAsync(100);
    expect((await send("npcs.scan")).ok).toBe(false);            // not primed before Start
    expect((await send("session.start")).ok).toBe(true);
    expect(got.filter((m) => m.type === "planner.status").pop()!.payload.armed).toBe(true);
    expect((await send("npcs.scan")).ok).toBe(true);
    const list = got.filter((m) => m.type === "npcs.list").pop()!.payload;
    expect(list.entities.map((e: any) => e.kind)).toEqual(["npc", "monster", "monster"]);
    expect((await send("npcs.attack", { index: 0 })).ok).toBe(false);
    expect((await send("npcs.attack", { index: 1, auto: true })).ok).toBe(true);
    const st = got.filter((m) => m.type === "agent.status").pop()!.payload;
    expect(st.goal).toMatch(/^attack /);
    expect(st.mode).toBe("auto");
    const pl = got.filter((m) => m.type === "planner.status").pop()!.payload;
    expect(pl.skill).toBe("attack");
    expect(got.some((m) => m.type === "log.entry" && m.payload.action?.kind === "click")).toBe(true);
    mock.stop();
  });

  it("fight sequence goal, then the kill switch stops input and keep-on-top", async () => {
    const { mock, got, send } = harness();
    await vi.advanceTimersByTimeAsync(100);
    expect((await send("fight.start", { auto: true })).ok).toBe(false);
    expect((await send("session.start")).ok).toBe(true);
    expect((await send("fight.start", { auto: true })).ok).toBe(true);
    expect(got.filter((m) => m.type === "planner.status").pop()!.payload.goal).toBe("fight sequence");
    expect((await send("steer.kill")).ok).toBe(true);
    expect(got.filter((m) => m.type === "agent.status").pop()!.payload.state).toBe("stopped");
    expect(got.filter((m) => m.type === "overlay.status").pop()!.payload.keep_on_top).toBe(false);
    expect((await send("npcs.scan")).ok).toBe(false);           // nothing while killed
    expect((await send("fight.start")).ok).toBe(false);
    expect(got.filter((m) => m.type === "planner.status").pop()!.payload.armed).toBe(false);
    expect((await send("steer.resume")).ok).toBe(true);
    expect(got.filter((m) => m.type === "overlay.status").pop()!.payload.keep_on_top).toBe(false);   // only Start takes it again
    expect((await send("session.start")).ok).toBe(true);
    expect(got.filter((m) => m.type === "overlay.status").pop()!.payload.keep_on_top).toBe(true);
    mock.stop();
  });
});

describe("store", () => {
  it("keeps the NPC list and planner status, cleared on source switch", () => {
    let s = initialState({ ...link, mock: false });
    const npcs = parseServerMessage(JSON.stringify({ type: "npcs.list", ts: 0, payload: { coords: [3, -3], entities: [] } }));
    const pl = parseServerMessage(JSON.stringify({ type: "planner.status", ts: 0, payload: { goal: "fight sequence", skill: "fight_sequence", attention: null, halted: null, outcome: null, fight: null, attack_target: null } }));
    expect(npcs && pl).toBeTruthy();
    s = reducer(s, { t: "msg", m: npcs! });
    s = reducer(s, { t: "msg", m: pl! });
    expect(s.npcs?.coords).toEqual([3, -3]);
    expect(s.planner?.goal).toBe("fight sequence");
    s = reducer(s, { t: "link", s: link });
    expect(s.npcs).toBeNull();
    expect(s.planner).toBeNull();
  });
});
