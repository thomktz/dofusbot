import { forwardRef, useMemo } from "react";
import type { SlotReport } from "../protocol";
import { CELL_COUNT, cellCenter, cellDiamond, fitGrid } from "../grid";
import { fmtRect, MARKER_SIZE, MARKERS, SLOT_ORIGIN } from "../slot";

interface Props { w: number; h: number; report: SlotReport | null; grid: boolean; calibrated: boolean }

/**
 * The Dofus slot (§3). Fixed position, fixed size, 4 px border outside (CSS outline does not
 * change the box), corner markers inside. Its box never depends on page content.
 */
export const Slot = forwardRef<HTMLDivElement, Props>(function Slot({ w, h, report, grid, calibrated }, ref) {
  const est = report?.estimate_screen ?? null;
  return (
    <div
      ref={ref}
      id="dofus-slot"
      className="slot"
      data-testid="slot"
      style={{ left: SLOT_ORIGIN.x, top: SLOT_ORIGIN.y, width: w, height: h }}
    >
      {grid && <GridOverlay w={w} h={h} />}
      {!grid && <div className="slot-ph">
        <div className="slot-ph-title">Dofus goes here</div>
        <div className="slot-ph-size">{w} × {h} CSS px</div>
        <div className="slot-ph-est">
          screen estimate <b>{fmtRect(est)}</b> pt{calibrated ? " · calibrated" : ""}
          {report && <> · dpr {report.dpr}</>}
        </div>
      </div>}
      <i className="mk" style={{ left: 0, top: 0, background: MARKERS.tl, width: MARKER_SIZE, height: MARKER_SIZE }} />
      <i className="mk" style={{ right: 0, top: 0, background: MARKERS.tr, width: MARKER_SIZE, height: MARKER_SIZE }} />
      <i className="mk" style={{ left: 0, bottom: 0, background: MARKERS.bl, width: MARKER_SIZE, height: MARKER_SIZE }} />
      <i className="mk" style={{ right: 0, bottom: 0, background: MARKERS.br, width: MARKER_SIZE, height: MARKER_SIZE }} />
    </div>
  );
});

/** Approximate 560-cell iso grid fitted to the map area (the slot minus Dofus' bottom HUD). */
function GridOverlay({ w, h }: { w: number; h: number }) {
  const hudH = Math.round(h * 0.115);
  const g = useMemo(() => fitGrid(w, h - hudH, 0), [w, h, hudH]);
  const cells = useMemo(() => Array.from({ length: CELL_COUNT }, (_, id) => id), []);
  return (
    <svg className="slot-grid" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden>
      {cells.map((id) => (
        <polygon key={id} points={cellDiamond(id, g)} className={Math.floor(id / 14) % 2 ? "odd" : ""} />
      ))}
      {g.cw >= 50 && cells.map((id) => {
        const c = cellCenter(id, g);
        return <text key={`t${id}`} x={c.x} y={c.y + 3}>{id}</text>;
      })}
      <rect x={0} y={h - hudH} width={w} height={hudH} className="hud" />
      <text x={w / 2} y={h - hudH / 2 + 4} className="hud-t">
        approx. HUD area · grid fitted, not calibrated (ox {g.ox.toFixed(1)} oy {g.oy.toFixed(1)} cw {g.cw.toFixed(1)} ch {g.ch.toFixed(1)})
      </text>
    </svg>
  );
}
