import { describe, expect, it } from "vitest";
import { computeSlotReport, estimateScreen, parseSlotParam, rectEquals, slotReportChanged, SLOT_ORIGIN } from "./slot";

const rect = { x: SLOT_ORIGIN.x, y: SLOT_ORIGIN.y, w: 1280, h: 800 };

describe("parseSlotParam", () => {
  it("defaults", () => {
    expect(parseSlotParam(null)).toEqual({ w: 1280, h: 800 });
    expect(parseSlotParam("")).toEqual({ w: 1280, h: 800 });
    expect(parseSlotParam("banana")).toEqual({ w: 1280, h: 800 });
    expect(parseSlotParam("10x10")).toEqual({ w: 1280, h: 800 });
    expect(parseSlotParam("99999x800")).toEqual({ w: 1280, h: 800 });
  });
  it("parses WxH variants", () => {
    expect(parseSlotParam("1440x900")).toEqual({ w: 1440, h: 900 });
    expect(parseSlotParam(" 1024X640 ")).toEqual({ w: 1024, h: 640 });
    expect(parseSlotParam("1152*720")).toEqual({ w: 1152, h: 720 });
  });
});

describe("estimateScreen (§3)", () => {
  it("full screen / app mode with no browser chrome: screen = window origin + rect", () => {
    const win = { screen_x: 0, screen_y: 0, outer_w: 1728, outer_h: 1117, inner_w: 1728, inner_h: 1117 };
    expect(estimateScreen(rect, win)).toEqual({ x: 16, y: 16, w: 1280, h: 800 });
  });
  it("windowed Chrome with a top toolbar: adds (outer-inner) height and half the width delta", () => {
    const win = { screen_x: 100, screen_y: 38, outer_w: 1602, outer_h: 1040, inner_w: 1600, inner_h: 952 };
    // x = 100 + (1602-1600)/2 + 16 = 117 ; y = 38 + (1040-952) + 16 = 142
    expect(estimateScreen(rect, win)).toEqual({ x: 117, y: 142, w: 1280, h: 800 });
  });
  it("Chrome --app window (title bar only, 28 pt)", () => {
    const win = { screen_x: 0, screen_y: 38, outer_w: 1728, outer_h: 1079, inner_w: 1728, inner_h: 1051 };
    expect(estimateScreen(rect, win)).toEqual({ x: 16, y: 38 + 28 + 16, w: 1280, h: 800 });
  });
  it("applies zoom_hint to CSS-px quantities only", () => {
    // 125% zoom: innerWidth/Height shrink in CSS px; outer stays in screen points.
    const win = { screen_x: 0, screen_y: 0, outer_w: 1728, outer_h: 1117, inner_w: 1382.4, inner_h: 893.6 };
    expect(estimateScreen(rect, win, 1.25)).toEqual({ x: 20, y: 20, w: 1600, h: 1000 });
  });
  it("negative screen origin (display left of main) is preserved", () => {
    const win = { screen_x: -1920, screen_y: 0, outer_w: 1920, outer_h: 1080, inner_w: 1920, inner_h: 1080 };
    expect(estimateScreen(rect, win).x).toBe(-1904);
  });
  it("ignores bogus zoom", () => {
    const win = { screen_x: 0, screen_y: 0, outer_w: 1728, outer_h: 1117, inner_w: 1728, inner_h: 1117 };
    expect(estimateScreen(rect, win, 0)).toEqual(estimateScreen(rect, win, 1));
  });
});

describe("computeSlotReport", () => {
  const win = { screen_x: 0, screen_y: 0, outer_w: 1728, outer_h: 1117, inner_w: 1728, inner_h: 1117 };
  it("has the exact SlotReport shape", () => {
    const r = computeSlotReport(rect, win, 2);
    expect(Object.keys(r).sort()).toEqual(["dpr", "estimate_screen", "rect_css", "window", "zoom_hint"]);
    expect(Object.keys(r.window).sort()).toEqual(["inner_h", "inner_w", "outer_h", "outer_w", "screen_x", "screen_y"]);
    expect(r.dpr).toBe(2);
    expect(r.zoom_hint).toBe(1);
    expect(r.rect_css).toEqual(rect);
  });
  it("detects changes", () => {
    const a = computeSlotReport(rect, win, 2);
    expect(slotReportChanged(null, a)).toBe(true);
    expect(slotReportChanged(a, computeSlotReport(rect, win, 2))).toBe(false);
    expect(slotReportChanged(a, computeSlotReport(rect, { ...win, screen_x: 5 }, 2))).toBe(true);
    expect(slotReportChanged(a, computeSlotReport(rect, win, 1))).toBe(true);
  });
  it("rectEquals tolerance", () => {
    expect(rectEquals({ x: 0, y: 0, w: 1, h: 1 }, { x: 0.4, y: 0, w: 1, h: 1 })).toBe(true);
    expect(rectEquals({ x: 0, y: 0, w: 1, h: 1 }, { x: 2, y: 0, w: 1, h: 1 })).toBe(false);
    expect(rectEquals(null, null)).toBe(true);
  });
});
