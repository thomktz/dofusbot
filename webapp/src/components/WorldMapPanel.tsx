import { useEffect, useRef } from "react";
import type { NetworkScanResult } from "../protocol";
import { Panel } from "./ui";

type World = {
  map_id?: number | null;
  actors?: Record<string, { id: number; kind: string; name?: string | null; cell?: number | null; alive?: boolean }>;
  interactives?: Record<string, { id: number; cell?: number | null; gfx_id?: number | null; available?: boolean | null }>;
  movement_paths?: { actor_id?: number; cells?: number[] }[];
  events?: { kind?: string; actor_id?: number; end_cell?: number; path_cells?: number[] }[];
  cells?: Record<string, { movable?: boolean | null; obstacle?: boolean; start_team?: string | null }>;
};

function point(cell: number, width: number, height: number) {
  const band = Math.floor(cell / 28), offset = cell % 28;
  const half = offset >= 14, col = offset % 14;
  const cellWidth = width / 14, cellHeight = height / 20;
  return { x: (col + (half ? 0.5 : 0)) * cellWidth + cellWidth / 2, y: (band + (half ? 0.5 : 0)) * cellHeight + cellHeight / 2 };
}

export function WorldMapPanel({ result, onMove }: { result: NetworkScanResult | null; onMove: (direction: "up" | "down" | "left" | "right") => void }) {
  const world = result?.world as World | undefined;
  const inFight = Boolean(result?.fight?.active);
  const actors = Object.values(world?.actors ?? {});
  const characterId = result?.character?.entity_id;
  const paths = (world?.movement_paths ?? []).filter(path => characterId != null && path.actor_id === characterId);
  const cells = world?.cells ?? {};
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1, width = 720, height = 500;
    canvas.width = width * dpr; canvas.height = height * dpr;
    const ctx = canvas.getContext("2d"); if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.fillStyle = "#080b10"; ctx.fillRect(0, 0, width, height);
    for (let id = 0; id < 560; id++) {
      const p = point(id, width, height), c = cells[String(id)] ?? {};
      // Role-play maps should not be covered by the asset's brown blocked
      // cells; those are tactical/fight terrain markers, not visible map
      // tiles. Keep them in fight mode where they affect positioning.
      if (!inFight && c.obstacle) continue;
      ctx.beginPath(); ctx.moveTo(p.x, p.y - height / 40 * .6); ctx.lineTo(p.x + width / 28 * .6, p.y); ctx.lineTo(p.x, p.y + height / 40 * .6); ctx.lineTo(p.x - width / 28 * .6, p.y); ctx.closePath();
      const knownTerrain = c.movable !== undefined;
      ctx.fillStyle = c.obstacle ? "rgba(170,120,80,.35)" : c.movable ? "rgba(74,163,255,.12)" : "rgba(80,95,115,.08)";
      ctx.strokeStyle = c.start_team === "red" ? "rgba(242,86,74,.7)" : c.start_team === "blue" ? "rgba(74,163,255,.7)" : "rgba(120,150,190,.13)";
      if (!knownTerrain) ctx.strokeStyle = "rgba(120,150,190,.28)";
      ctx.fill(); ctx.stroke();
    }
    // The normal map is deliberately centred on the player: only their latest
    // move is visualised.  Other players would make this a noisy activity map.
    for (const path of paths.slice(-1)) {
      const pathCells = path.cells ?? []; if (pathCells.length < 2) continue;
      ctx.beginPath(); pathCells.forEach((cell, i) => { const p = point(cell, width, height); i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); });
      ctx.strokeStyle = "rgba(74,163,255,.78)"; ctx.lineWidth = 2; ctx.stroke();
    }
    for (const item of Object.values(world?.interactives ?? {})) {
      if (item.cell == null) continue; const p = point(item.cell, width, height);
      ctx.fillStyle = item.available === false ? "#657080" : "#d2a84f"; ctx.fillRect(p.x - 3, p.y - 3, 6, 6);
    }
    for (const actor of actors) {
      if (actor.cell == null || actor.alive === false) continue; const p = point(actor.cell, width, height);
      const isSelf = characterId != null && actor.id === characterId;
      const isMob = actor.kind === "monster_group" || actor.kind === "monster";
      if (!isSelf && !isMob) continue;
      const color = isSelf ? "#4aa3ff" : "#f2564a";
      ctx.beginPath(); ctx.arc(p.x, p.y, 7, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke();
      ctx.fillStyle = "#fff"; ctx.font = "bold 8px monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(isSelf ? "P" : "M", p.x, p.y);
    }
  }, [world, inFight, characterId]);
  if (!world) return <Panel title={inFight ? "Fight map" : "Current map"}><div className="empty">No protocol map state in the selected capture.</div></Panel>;
  return <Panel title={inFight ? "Fight map" : "Current map"}>
    <div className="world-map-layout">
      <canvas ref={canvasRef} className="world-map-canvas" aria-label="Protocol world map" />
      {!inFight && <div className="map-nav" aria-label="Move to adjacent map">
          <button className="map-arrow up" title="Move up" onClick={() => onMove("up")}>▲</button>
          <button className="map-arrow left" title="Move left" onClick={() => onMove("left")}>◀</button>
          <button className="map-arrow down" title="Move down" onClick={() => onMove("down")}>▼</button>
          <button className="map-arrow right" title="Move right" onClick={() => onMove("right")}>▶</button>
      </div>}
    </div>
  </Panel>;
}
