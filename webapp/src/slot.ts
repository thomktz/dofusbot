// Pure slot geometry + SlotReport computation (protocol §3). No DOM access here: callers pass values in.
import type { Rect, SlotReport } from "./protocol";

export const DEFAULT_SLOT = { w: 1280, h: 800 } as const;
/** Gap between viewport edge and the slot border. */
export const SLOT_MARGIN = 12;
/** Solid border drawn OUTSIDE the slot rect (§3). */
export const SLOT_BORDER = 4;
/** Slot rect top-left in viewport CSS px: margin + border. Never changes with content. */
export const SLOT_ORIGIN = { x: SLOT_MARGIN + SLOT_BORDER, y: SLOT_MARGIN + SLOT_BORDER } as const;
export const MARKER_SIZE = 12;
export const MARKERS = { tl: "#FF00FF", tr: "#00FFFF", bl: "#FFFF00", br: "#00FF00" } as const;

const MIN_SIDE = 320;
const MAX_SIDE = 4096;

/** Parse `?slot=WxH` ("1280x800", "1440X900", "1280*800"). Invalid/out-of-range → default. */
export function parseSlotParam(value: string | null | undefined): { w: number; h: number } {
  if (!value) return { ...DEFAULT_SLOT };
  const m = /^\s*(\d{3,4})\s*[xX×*]\s*(\d{3,4})\s*$/.exec(value);
  if (!m) return { ...DEFAULT_SLOT };
  const w = Number(m[1]);
  const h = Number(m[2]);
  if (w < MIN_SIDE || h < MIN_SIDE || w > MAX_SIDE || h > MAX_SIDE) return { ...DEFAULT_SLOT };
  return { w, h };
}

export interface WindowMetrics {
  screen_x: number; screen_y: number;
  outer_w: number; outer_h: number;
  inner_w: number; inner_h: number;
}

/**
 * §3 estimate, in screen points.
 *   screen_x = window.screen_x + (outer_w - inner_w)/2 + rect_css.x
 *   screen_y = window.screen_y + (outer_h - inner_h)   + rect_css.y
 *   w = rect_css.w, h = rect_css.h
 * "× zoom_hint" is applied to the CSS-px quantities (inner sizes and the rect), because
 * window.screenX/Y and outerWidth/Height are already in screen points in Chromium/WebKit.
 * With zoom_hint = 1 (the normal case) this is exactly the formula in the doc.
 */
export function estimateScreen(rect: Rect, win: WindowMetrics, zoom = 1): Rect {
  const z = zoom > 0 && Number.isFinite(zoom) ? zoom : 1;
  const chromeX = Math.max(0, (win.outer_w - win.inner_w * z) / 2);
  const chromeY = Math.max(0, win.outer_h - win.inner_h * z);
  return {
    x: round2(win.screen_x + chromeX + rect.x * z),
    y: round2(win.screen_y + chromeY + rect.y * z),
    w: round2(rect.w * z),
    h: round2(rect.h * z),
  };
}

export function computeSlotReport(rect: Rect, win: WindowMetrics, dpr: number, zoom = 1): SlotReport {
  return {
    rect_css: { x: round2(rect.x), y: round2(rect.y), w: round2(rect.w), h: round2(rect.h) },
    window: { ...win },
    dpr,
    zoom_hint: zoom,
    estimate_screen: estimateScreen(rect, win, zoom),
  };
}

/** Read the live values from the DOM (thin impure wrapper, kept here so callers stay one-liners). */
export function readSlotReport(el: Element, w: Window = window, zoom = 1): SlotReport {
  const b = el.getBoundingClientRect();
  return computeSlotReport(
    { x: b.left, y: b.top, w: b.width, h: b.height },
    { screen_x: w.screenX, screen_y: w.screenY, outer_w: w.outerWidth, outer_h: w.outerHeight,
      inner_w: w.innerWidth, inner_h: w.innerHeight },
    w.devicePixelRatio || 1,
    zoom,
  );
}

export function rectEquals(a: Rect | null | undefined, b: Rect | null | undefined, tol = 0.5): boolean {
  if (!a || !b) return a === b;
  return Math.abs(a.x - b.x) <= tol && Math.abs(a.y - b.y) <= tol &&
         Math.abs(a.w - b.w) <= tol && Math.abs(a.h - b.h) <= tol;
}

/** True when a report differs in anything that matters to control (geometry, window, dpr, zoom). */
export function slotReportChanged(a: SlotReport | null, b: SlotReport): boolean {
  if (!a) return true;
  const wa = a.window, wb = b.window;
  return !rectEquals(a.rect_css, b.rect_css) || !rectEquals(a.estimate_screen, b.estimate_screen) ||
    wa.screen_x !== wb.screen_x || wa.screen_y !== wb.screen_y || wa.outer_w !== wb.outer_w ||
    wa.outer_h !== wb.outer_h || wa.inner_w !== wb.inner_w || wa.inner_h !== wb.inner_h ||
    a.dpr !== b.dpr || a.zoom_hint !== b.zoom_hint;
}

export function fmtRect(r: Rect | null | undefined): string {
  if (!r) return "—";
  return `${fmtN(r.x)}, ${fmtN(r.y)}  ${fmtN(r.w)}×${fmtN(r.h)}`;
}
const fmtN = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
const round2 = (n: number) => Math.round(n * 100) / 100;
