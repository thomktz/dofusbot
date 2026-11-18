import { useEffect, useRef } from "react";
import type { SlotReport } from "./protocol";
import { readSlotReport, slotReportChanged } from "./slot";

/**
 * Sends `slot.report` (§2.2/§3): on connect (whenever `connKey` changes), on resize, every 2 s,
 * and as soon as window.screenX/screenY change (polled, since browsers have no "window moved" event).
 */
export function useSlotReporter(
  el: React.RefObject<HTMLElement>,
  connKey: string,
  canSend: boolean,
  zoom: number,
  send: (r: SlotReport) => void,
  onLocal: (r: SlotReport) => void,
) {
  const last = useRef<SlotReport | null>(null);
  const sendRef = useRef(send); sendRef.current = send;
  const localRef = useRef(onLocal); localRef.current = onLocal;
  const canRef = useRef(canSend); canRef.current = canSend;

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const measure = (force: boolean) => {
      const r = readSlotReport(node, window, zoom);
      const changed = slotReportChanged(last.current, r);
      if (changed) { last.current = r; localRef.current(r); }
      if ((changed || force) && canRef.current) sendRef.current(r);
    };
    measure(true); // on connect / mount
    const onResize = () => measure(false);
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(node);
    let sx = window.screenX, sy = window.screenY;
    const poll = setInterval(() => {
      if (window.screenX !== sx || window.screenY !== sy) { sx = window.screenX; sy = window.screenY; measure(false); }
    }, 250);
    const periodic = setInterval(() => measure(true), 2000);
    return () => { window.removeEventListener("resize", onResize); ro.disconnect(); clearInterval(poll); clearInterval(periodic); };
  }, [el, connKey, zoom]);
}
