import { useEffect, useMemo, useRef, useState } from "react";
import type { NetworkScanResult } from "../protocol";
import { Panel } from "./ui";

const WALKABLE = new Set([162,176,177,190,191,203,204,205,216,217,218,219,230,231,232,233,244,245,246,247,258,259,261,262,271,272,274,275,285,286,289,297,298,299,300,302,303,311,312,313,314,315,316,317,318,325,326,327,328,329,330,331,340,341,342,343,344,345,353,354,355,356,357,358,368,371,372,373,374,386,387,388,400,401,402,414,415,429,430,443]);

function point(cell: number) {
  const band = Math.floor(cell / 28), offset = cell % 28;
  const half = offset >= 14, col = offset % 14;
  return { x: (col + (half ? 0.5 : 0)) * 40 + 20, y: (band + (half ? 0.5 : 0)) * 20 + 10 };
}

export function FightReplay({ result }: { result: NetworkScanResult | null }) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const monsters = result?.fight_monsters ?? [];
  const player = result?.fight_players?.[0];
  const terrain = result?.terrain;
  const allEvents = result?.fight_events ?? [];
  const firstFighter = allEvents.findIndex((e) => e.kind === "fighter_show");
  const events = firstFighter >= 0 ? allEvents.slice(firstFighter) : allEvents;
  const steps = useMemo(() => ["placement recorded", "fighters revealed", "monster levels decoded", ...events.map((e) => `${e.kind}${e.turn != null ? ` · turn ${e.turn}` : ""}${e.action_id != null ? ` · action ${e.action_id}` : ""}`)], [events]);
  useEffect(() => {
    setStep(0);
    setPlaying(false);
  }, [result?.capture_path, events.length]);
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => setStep((s) => s >= steps.length - 1 ? (setPlaying(false), s) : s + 1), 1100);
    return () => window.clearInterval(id);
  }, [playing, steps.length]);

  if (!result) {
    return (
      <Panel title="Fight replay" testId="fight-replay">
        <div className="fight-replay-empty">
          <span className="tag dim">no saved fight</span>
          <span className="dim small">No archived capture was found. Start a recording only if you want to replace it with a new fight.</span>
        </div>
      </Panel>
    );
  }

  const shownMonsters = monsters.filter((_, i) => step >= 1 || i < 1);
  const cells = Array.from({ length: 560 }, (_, id) => id);
  const walkable = new Set(terrain?.walkable_cells ?? WALKABLE);
  const blocked = new Set(terrain?.blocked ?? []);
  const holes = new Set(terrain?.holes ?? []);
  const eventCount = Math.max(0, step - 2);
  const visibleEvents = events.slice(0, eventCount);
  const eventSlice = visibleEvents.slice(-8);
  // FighterShowEvent is the authoritative identity + starting disposition.
  // Movement is MapMovementEvent (jnz), whose packed path ends at end_cell.
  const initialActors = new Map<number, number>();
  for (const e of events) if (e.kind === "fighter_show" && e.actor_id != null && e.cell != null) initialActors.set(e.actor_id, e.cell);
  if (player?.entity_id != null && player.cell != null) initialActors.set(player.entity_id, player.cell);
  monsters.forEach((m, i) => { if (m.cell != null) initialActors.set(-(i + 1), m.cell); });
  const actorCells = new Map(initialActors);
  const deadActors = new Set<number>();
  const stateByActor = new Map<number, { hpLost: number; dead: boolean; cell?: number }>();
  for (const e of visibleEvents) {
    // Ignore map walking before combat. During combat, movement events carry
    // a turn number; a newly appearing actor is a summon/minion.
    if (e.actor_id != null && e.kind === "movement" && (e.end_cell ?? e.cell) != null && (initialActors.has(e.actor_id) || e.turn != null)) {
      if (!initialActors.has(e.actor_id) && e.start_cell != null) initialActors.set(e.actor_id, e.start_cell);
      actorCells.set(e.actor_id, e.end_cell ?? e.cell!);
    }
    if (e.target_id != null && e.kind === "slide" && e.end_cell != null) actorCells.set(e.target_id, e.end_cell);
    if (e.dead_id != null) deadActors.add(e.dead_id);
    const id = e.target_id ?? e.dead_id;
    if (id == null) continue;
    const s = stateByActor.get(id) ?? { hpLost: 0, dead: false };
    if (e.hp_loss != null) s.hpLost += e.hp_loss;
    if (e.dead_id != null) s.dead = true;
    if (e.cell != null) s.cell = e.cell;
    stateByActor.set(id, s);
  }
  return (
    <Panel title="Fight replay" testId="fight-replay" right={<span className="tag acc">map {result?.fight_map_id ?? "—"}</span>}>
      <div className="fight-replay-layout">
        <div className="fight-replay-board">
          <ReplayCanvas step={step} playerId={player?.entity_id ?? null} playerCell={player?.cell ?? null} monsters={shownMonsters} initialActors={initialActors} actors={actorCells} deadActors={deadActors} cells={cells} walkable={walkable} blocked={blocked} holes={holes} />
        </div>
        <div className="fight-replay-side">
          <div className="fight-replay-step"><span className="tag warn">{step + 1}/{steps.length}</span><span>{steps[step]}</span></div>
          <div className="mono small">{monsters.length} monster records · {events.length} events</div>
          <div className={`small ${events.filter((e) => e.cell != null).length < 2 ? "bad" : "dim"}`}>position updates: {events.filter((e) => e.cell != null).length} · movement paths are not present in this capture</div>
          {step > 2 && <div className="fight-replay-current"><span className="k">current event</span><strong>{steps[step]}</strong><span className="mono small">{eventSlice.length ? eventDescription(eventSlice[eventSlice.length - 1]) : "no decoded actor position in this packet"}</span></div>}
          {stateByActor.size > 0 && <div className="fight-replay-state mono small">{[...stateByActor.entries()].map(([id, s]) => <div key={id}>actor {id} · {s.dead ? "DEAD" : "alive"} · damage {s.hpLost}{s.cell != null ? ` · cell ${s.cell}` : ""}</div>)}</div>}
          {eventSlice.length > 0 && <div className="fight-replay-events mono small">{eventSlice.map((e, i) => <div key={`${e.offset}-${i}`}>{eventDescription(e)}</div>)}</div>}
          <input className="fight-replay-range" type="range" min="0" max={Math.max(0, steps.length - 1)} value={step}
            onChange={(e) => { setPlaying(false); setStep(Number(e.currentTarget.value)); }} aria-label="Replay step" />
          <div className="fight-replay-controls">
            <button type="button" className="btn" disabled={step <= 0} onClick={() => { setPlaying(false); setStep((s) => Math.max(0, s - 1)); }}>← Previous</button>
            <button type="button" className="btn" onClick={() => setPlaying((p) => !p)}>{playing ? "■ Pause" : "▶ Play"}</button>
            <button type="button" className="btn" disabled={step >= steps.length - 1} onClick={() => { setPlaying(false); setStep((s) => Math.min(steps.length - 1, s + 1)); }}>Next →</button>
          </div>
        </div>
      </div>
      <div className="dim small">Read-only reconstruction from the latest network capture; colored diamonds are starting cells{terrain?.source_path ? ` · terrain ${terrain.source_path}` : ""}.</div>
    </Panel>
  );
}

function eventDescription(e: NonNullable<NetworkScanResult["fight_events"]>[number]) {
  return [e.kind, e.turn != null && `T${e.turn}`, e.actor_id != null && `actor ${e.actor_id}`, e.source_id != null && `source ${e.source_id}`, e.target_id != null && `target ${e.target_id}`, e.spell_id != null && `spell ${e.spell_id}`, e.destination_cell != null && `to cell ${e.destination_cell}`, e.hp_loss != null && `-${e.hp_loss} HP`, e.delta != null && `Δ${e.delta}`, e.dead_id != null && `DEAD ${e.dead_id}`, e.start_cell != null && `${e.start_cell}→${e.end_cell}`, e.path_cells && e.path_cells.length > 2 && `${e.path_cells.length} cells`, e.action_id != null && `action ${e.action_id}`, e.effect_codes?.length && `effects ${e.effect_codes.join(",")}`, e.target_cells?.length && `cells ${e.target_cells.join(",")}`].filter(Boolean).join(" · ");
}

function ReplayCanvas({ step, playerId, playerCell, monsters, initialActors, actors, deadActors, cells, walkable, blocked, holes }: { step: number; playerId: number | null; playerCell: number | null; monsters: NetworkScanResult["fight_monsters"]; initialActors: Map<number, number>; actors: Map<number, number>; deadActors: Set<number>; cells: number[]; walkable: Set<number>; blocked: Set<number>; holes: Set<number> }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const width = 600, height = 420;
    canvas.width = width * dpr; canvas.height = height * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#080b10"; ctx.fillRect(0, 0, width, height);
    for (const id of cells) {
      const p = point(id); const walk = walkable.has(id); const isBlocked = blocked.has(id); const isHole = holes.has(id);
      ctx.beginPath(); ctx.moveTo(p.x, p.y - 6); ctx.lineTo(p.x + 12, p.y); ctx.lineTo(p.x, p.y + 6); ctx.lineTo(p.x - 12, p.y); ctx.closePath();
      ctx.fillStyle = isHole ? "rgba(0,0,0,0)" : isBlocked ? "rgba(160,120,90,.22)" : walk ? "rgba(74,163,255,.13)" : "rgba(80,95,115,.06)";
      ctx.strokeStyle = isHole ? "rgba(120,150,190,.05)" : isBlocked ? "rgba(210,160,110,.5)" : walk ? "rgba(74,163,255,.35)" : "rgba(120,150,190,.12)"; ctx.lineWidth = 1; ctx.fill(); ctx.stroke();
    }
    const token = (cell: number, label: string, color: string) => { const p = point(cell); ctx.beginPath(); ctx.arc(p.x, p.y, 7, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke(); ctx.fillStyle = "#fff"; ctx.font = "bold 8px monospace"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(label, p.x, p.y); };
    const startMark = (cell: number, color: string) => { const p = point(cell); ctx.beginPath(); ctx.moveTo(p.x, p.y - 8); ctx.lineTo(p.x + 8, p.y); ctx.lineTo(p.x, p.y + 8); ctx.lineTo(p.x - 8, p.y); ctx.closePath(); ctx.fillStyle = color; ctx.globalAlpha = .25; ctx.fill(); ctx.globalAlpha = 1; ctx.strokeStyle = color; ctx.stroke(); };
    for (const [id, cell] of initialActors) startMark(cell, id === playerId ? "#4aa3ff" : "#f2564a");
    if (step <= 2) {
      for (const [id, cell] of initialActors) {
        if (deadActors.has(id)) continue;
        token(cell, id === playerId ? "P" : "M", id === playerId ? "#4aa3ff" : "#f2564a");
      }
    } else {
      for (const [actor, cell] of actors) {
        if (deadActors.has(actor)) continue;
        token(cell, actor === playerId ? "P" : `M${Math.abs(actor)}`, actor === playerId ? "#4aa3ff" : "#f2564a");
      }
    }
  }, [actors, blocked, cells, deadActors, holes, initialActors, monsters, playerCell, playerId, step, walkable]);
  return <canvas ref={ref} className="fight-replay-canvas" aria-label="Dofus fight replay map" />;
}
