import { describe, expect, it } from "vitest";
import { DEFAULT_WS_URL, parseParams } from "./params";

describe("parseParams", () => {
  it("defaults", () => {
    expect(parseParams("")).toEqual({ wsUrl: DEFAULT_WS_URL, forceMock: false, slot: { w: 1280, h: 800 }, grid: false, zoom: 1 });
  });
  it("reads overrides", () => {
    const p = parseParams("?mock=1&ws=ws://10.0.0.2:9000/ws&slot=1440x900&grid=1&zoom=1.25");
    expect(p).toEqual({ wsUrl: "ws://10.0.0.2:9000/ws", forceMock: true, slot: { w: 1440, h: 900 }, grid: true, zoom: 1.25 });
  });
  it("mock=0 is off", () => expect(parseParams("?mock=0").forceMock).toBe(false));
});
