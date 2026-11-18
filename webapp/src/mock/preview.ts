// Fake "capture" for frame.preview in mock mode: a small iso scene drawn on a canvas, JPEG-encoded.
import type { GameState } from "../protocol";
import { cellCenter, CELL_COUNT, fitGrid } from "../grid";

export function renderPreviewJpeg(g: GameState): { jpeg_b64: string; width: number; height: number } | null {
  if (typeof document === "undefined") return null;
  const width = 320, height = 200;
  const cv = document.createElement("canvas");
  cv.width = width; cv.height = height;
  const ctx = cv.getContext("2d");
  if (!ctx) return null;
  const grd = ctx.createLinearGradient(0, 0, 0, height);
  const tint = g.screen === "fight" ? ["#3b4a2a", "#26301c"] : g.screen === "dialog" ? ["#3d3a2c", "#24221a"] : ["#4b6b34", "#2b4220"];
  grd.addColorStop(0, tint[0]); grd.addColorStop(1, tint[1]);
  ctx.fillStyle = grd; ctx.fillRect(0, 0, width, height);
  const gc = fitGrid(width, height - 24, 2);
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  for (let id = 0; id < CELL_COUNT; id++) {
    const { x, y } = cellCenter(id, gc);
    ctx.beginPath();
    ctx.moveTo(x, y - gc.ch / 2); ctx.lineTo(x + gc.cw / 2, y); ctx.lineTo(x, y + gc.ch / 2); ctx.lineTo(x - gc.cw / 2, y);
    ctx.closePath(); ctx.stroke();
  }
  for (const f of g.fight?.fighters ?? []) {
    if (f.cell == null) continue;
    const { x, y } = cellCenter(f.cell, gc);
    ctx.fillStyle = f.team === "ally" ? "#4aa3ff" : "#ff5a4a";
    ctx.beginPath(); ctx.arc(x, y - 3, 4, 0, Math.PI * 2); ctx.fill();
  }
  ctx.fillStyle = "#14161a"; ctx.fillRect(0, height - 24, width, 24);
  ctx.fillStyle = "#c9d1d9"; ctx.font = "10px sans-serif";
  const coords = g.map.coords ? `[${g.map.coords.join(",")}]` : "";
  ctx.fillText(`MOCK CAPTURE  ${g.screen}  ${coords} ${g.map.sub_area ?? ""}`, 6, height - 9);
  ctx.fillText(new Date().toLocaleTimeString(), width - 56, height - 9);
  const url = cv.toDataURL("image/jpeg", 0.7);
  return { jpeg_b64: url.slice(url.indexOf(",") + 1), width, height };
}
