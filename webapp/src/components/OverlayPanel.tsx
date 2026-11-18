import type { OverlayStatus, SlotReport } from "../protocol";
import { fmtRect, rectEquals } from "../slot";
import { Check, KV, Panel } from "./ui";

interface Props {
  o: OverlayStatus | null;
  report: SlotReport | null;
  connected: boolean;
  grid: boolean;
  preview: { src: string; width: number; height: number; at: number } | null;
  previewOn: boolean;
  onAlign: () => void;
  onCalibrate: () => void;
  onGrid: (v: boolean) => void;
  onPreview: (v: boolean) => void;
}

export function OverlayPanel({ o, report, connected, grid, preview, previewOn, onAlign, onCalibrate, onGrid, onPreview }: Props) {
  const win = o?.dofus_window;
  const est = report?.estimate_screen ?? null;
  const serverSlotMatches = o?.slot ? rectEquals(o.slot, est, 1) : null;
  return (
    <Panel title="Overlay" testId="overlay-panel" right={
      <span className={`tag ${o?.aligned ? "ok" : "warn"}`}>{o ? (o.aligned ? "aligned" : "not aligned") : "—"}</span>
    }>
      <div className="row gap wrap-row">
        <Check ok={win?.found} label="Dofus window" />
        <Check ok={o?.aligned} label="aligned" />
        <Check ok={o?.calibrated} label={o?.calibrated ? "calibrated" : "estimate"} />
        <Check ok={o?.keep_on_top} label="on top" />
      </div>
      {win?.found && <KV k="window"><span className="mono ellip" title={win.title ?? ""}>{win.title} · pid {win.pid}</span></KV>}
      <KV k="frame"><span className="mono">{fmtRect(win?.frame)}</span></KV>
      <KV k="slot (srv)" title="slot target as used by brain (screen points)">
        <span className="mono">{fmtRect(o?.slot)}</span>
        {serverSlotMatches === false && <span className="tag warn" title="differs from this page's estimate">≠ est</span>}
      </KV>
      <KV k="estimate" title="this page's own §3 estimate (screen points)"><span className="mono" data-testid="slot-estimate">{fmtRect(est)}</span></KV>
      {report && (
        <KV k="window" title="window.screenX/Y · outer · inner · dpr">
          <span className="mono dim">
            @{report.window.screen_x},{report.window.screen_y} out {report.window.outer_w}×{report.window.outer_h} in {report.window.inner_w}×{report.window.inner_h} ·{report.dpr}x
          </span>
        </KV>
      )}
      <div className="row gap">
        <button className="btn" data-testid="btn-align" disabled={!connected} onClick={onAlign}>Align</button>
        <button className="btn" data-testid="btn-calibrate" disabled={!connected} onClick={onCalibrate}>Calibrate</button>
        <label className="tgl"><input type="checkbox" checked={grid} onChange={(e) => onGrid(e.target.checked)} /> grid</label>
        <label className="tgl"><input type="checkbox" checked={previewOn} disabled={!connected} onChange={(e) => onPreview(e.target.checked)} /> preview</label>
      </div>
      {previewOn && (
        <div className="preview">
          {preview ? <img src={preview.src} width={preview.width} height={preview.height} alt="frame preview" /> : <div className="empty">waiting for frame.preview…</div>}
        </div>
      )}
    </Panel>
  );
}
