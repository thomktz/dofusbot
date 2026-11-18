import { describe, expect, it } from "vitest";
import { initialState, reducer, MAX_LOGS } from "./store";
import type { LinkState } from "./link";

const link: LinkState = { conn: "open", mock: false, forced: false, url: "ws://x", attempts: 0, nextRetryMs: null };
const entry = (i: number) => ({ ts: i, level: "info" as const, source: "brain" as const, message: `m${i}` });

describe("store", () => {
  it("caps logs", () => {
    let s = initialState(link);
    s = reducer(s, { t: "msg", m: { type: "log.history", ts: 0, payload: { entries: Array.from({ length: MAX_LOGS }, (_, i) => entry(i)) } } });
    s = reducer(s, { t: "msg", m: { type: "log.entry", ts: 0, payload: entry(99999) } });
    expect(s.logs).toHaveLength(MAX_LOGS);
    expect(s.logs[s.logs.length - 1].message).toBe("m99999");
  });
  it("switching to mock clears server state", () => {
    let s = initialState(link);
    s = reducer(s, { t: "msg", m: { type: "log.entry", ts: 0, payload: entry(1) } });
    s = reducer(s, { t: "link", s: { ...link, mock: true } });
    expect(s.logs).toHaveLength(0);
    expect(s.link.mock).toBe(true);
  });
});
