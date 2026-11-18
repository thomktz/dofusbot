import { useMemo } from "react";
import type { FightState, GameState } from "../protocol";
import { CELL_COUNT, cellCenter, cellDiamond, fitGrid } from "../grid";
import { Panel } from "./ui";

const PHASE_LABEL: Record<FightState["phase"], string> = {
  placement: "placement", my_turn: "my turn", enemy_turn: "enemy turn", ended: "ended",
};

export function ScenePanel({ g }: { g: GameState | null }) {
  const f = g?.fight ?? null;
  if (f) {
    return (
      <Panel title="Fight" className="scene" testId="scene-panel" right={
        <span className="row gap-s">
          <span className={`tag ph-${f.phase}`}>{PHASE_LABEL[f.phase]}</span>
          <span className="tag dim">turn {f.turn}</span>
          {f.timer_s != null && <span className={`tag ${f.timer_s <= 5 ? "err" : "dim"}`}>{Math.ceil(f.timer_s)}s</span>}
        </span>
      }>
        <div className="fight">
          <MiniGrid fight={f} pois={null} />
          <ul className="fighters">
            {f.fighters.map((x) => {
              const dead = x.hp === 0;
              const pct = x.hp != null && x.hp_max ? (x.hp / x.hp_max) * 100 : 0;
              return (
                <li key={x.id} className={`fighter ${x.team} ${x.is_self ? "self" : ""} ${dead ? "dead" : ""}`}>
                  <div className="fighter-top">
                    <span className="fn ellip" title={x.name}>{x.is_self ? "★ " : ""}{x.name}</span>
                    <span className="mono dim">{x.cell != null ? `#${x.cell}` : "—"}</span>
                  </div>
                  <div className="fhp"><span style={{ width: `${pct}%` }} /></div>
                  <div className="fighter-bot mono dim">
                    <span>{x.hp ?? "?"}/{x.hp_max ?? "?"}</span>
                    {(x.pa != null || x.pm != null) && <span><b className="pa">{x.pa ?? "?"}</b> PA <b className="pm">{x.pm ?? "?"}</b> PM</span>}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </Panel>
    );
  }
  if (g?.dialog) {
    const d = g.dialog;
    return (
      <Panel title="Dialog" className="scene" testId="scene-panel" right={<span className="tag acc">{d.npc ?? "?"}</span>}>
        <div className="dialog">
          <p className="dialog-t">{d.text}</p>
          <ol className="dialog-o">
            {d.options.map((o) => <li key={o.index}><span className="mono acc">{o.index}</span> {o.text}</li>)}
          </ol>
        </div>
      </Panel>
    );
  }
  return (
    <Panel title="Map" className="scene" testId="scene-panel" right={<span className="tag dim">{g?.screen ?? "—"} · no fight</span>}>
      <div className="fight">
        <MiniGrid fight={null} pois={g?.pois.filter((p) => p.cell != null).map((p) => ({ cell: p.cell!, kind: p.kind, name: p.name })) ?? []} />
        <div className="scene-note dim">
          Cells of on-map POIs are shown. The fight view (fighters, obstacles, reachable cells) appears here when <span className="mono">screen = fight</span>.
        </div>
      </div>
    </Panel>
  );
}

const VB_W = 290, VB_H = 205;

function MiniGrid({ fight, pois }: { fight: FightState | null; pois: { cell: number; kind: string; name: string }[] | null }) {
  const g = useMemo(() => fitGrid(VB_W, VB_H, 1), []);
  const all = useMemo(() => Array.from({ length: CELL_COUNT }, (_, i) => cellDiamond(i, g, 0.3)), [g]);
  const blocked = new Set(fight?.blocked_cells ?? []);
  const reach = new Set(fight?.reachable_cells ?? []);
  return (
    <svg className="minigrid" viewBox={`0 0 ${VB_W} ${VB_H}`} data-testid="minigrid">
      {all.map((pts, id) => (
        <polygon key={id} points={pts} className={blocked.has(id) ? "c-block" : reach.has(id) ? "c-reach" : "c"} />
      ))}
      {pois?.map((p, i) => {
        const c = cellCenter(p.cell, g);
        return <g key={i}><title>{`${p.name} #${p.cell}`}</title><circle cx={c.x} cy={c.y} r={3.2} className={`p-${p.kind}`} /></g>;
      })}
      {fight?.fighters.filter((x) => x.cell != null && x.hp !== 0).map((x) => {
        const c = cellCenter(x.cell!, g);
        return (
          <g key={x.id}>
            <title>{`${x.name} #${x.cell} ${x.hp ?? "?"}/${x.hp_max ?? "?"}`}</title>
            {x.is_self && <circle cx={c.x} cy={c.y} r={6.5} className="f-ring" />}
            <circle cx={c.x} cy={c.y} r={4.3} className={x.team === "ally" ? "f-ally" : "f-enemy"} />
            <text x={c.x} y={c.y - 7} className="f-lbl">{x.name.slice(0, 3)}</text>
          </g>
        );
      })}
    </svg>
  );
}
