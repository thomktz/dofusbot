import type { GameState } from "../protocol";
import { fmtCoords, fmtInt } from "../format";
import { Bar, KV, Panel, Pips } from "./ui";
import { useNow } from "../useNow";

export function GamePanel({ g }: { g: GameState | null }) {
  const now = useNow(1000);
  if (!g) return <Panel title="Game" className="game"><div className="empty">waiting for game.state…</div></Panel>;
  const c = g.character;
  const age = Math.max(0, Math.round((now - g.updated_at) / 1000));
  const conf = Math.round(g.confidence * 100);
  return (
    <Panel title="Game" className="game" testId="game-panel" right={<span className={`tag ${age > 3 ? "warn" : "dim"}`}>{age > 3 ? `${age}s old` : "live"}</span>}>
      <div className="screen-row">
        <span className={`scr scr-${g.screen}`} data-testid="game-screen">{g.screen}</span>
        <span className="conf"><span style={{ width: `${conf}%` }} className={conf < 80 ? "low" : ""} /></span>
        <span className="mono dim">{conf}%</span>
      </div>
      <div className="map-row">
        <span className="coords mono">{fmtCoords(g.map.coords)}</span>
        <span className="area">
          <b>{g.map.area ?? "—"}</b>
          <span className="dim">{g.map.sub_area ?? ""}</span>
        </span>
      </div>
      <div className="char-row">
        <b>{c.name ?? "?"}</b> <span className="dim">niv. {c.level ?? "?"}</span>
        <span className="kamas mono">{fmtInt(c.kamas)} <span className="dim">K</span></span>
      </div>
      <Bar label="PV" value={c.hp} max={c.hp_max} color="var(--hp)" />
      <div className="pips2">
        <Pips label="PA" value={c.pa} max={7} color="var(--pa)" />
        <Pips label="PM" value={c.pm} max={4} color="var(--pm)" />
      </div>
      <Bar label="pods" value={c.pods} max={c.pods_max} color={c.pods != null && c.pods_max && c.pods / c.pods_max > 0.9 ? "var(--err)" : "var(--pods)"} />
    </Panel>
  );
}

const POI_ICON: Record<string, string> = { npc: "☺", zaap: "◎", zaapi: "◌", bank: "▣", workshop: "⚒", resource: "♣", monster: "✶", door: "⇲", other: "·" };

export function WorldPanel({ g }: { g: GameState | null }) {
  return (
    <Panel title="Quest & POIs" className="world" testId="world-panel" right={<span className="tag dim">{g?.pois.length ?? 0} POIs</span>}>
      {g?.quest ? (
        <div className="quest">
          <div className="quest-n">{g.quest.name}</div>
          <div className="dim">{[g.quest.step, g.quest.objective].filter(Boolean).join(" · ")}</div>
        </div>
      ) : <div className="empty">no active quest</div>}
      <ul className="pois">
        {g?.pois.map((p, i) => (
          <li key={i} className={`poi poi-${p.kind}`}>
            <span className="poi-i" title={p.kind}>{POI_ICON[p.kind] ?? "·"}</span>
            <span className="poi-n ellip" title={p.name}>{p.name}</span>
            <span className="mono dim">{fmtCoords(p.coords)}{p.cell != null ? ` #${p.cell}` : ""}</span>
          </li>
        ))}
      </ul>
      {g && <KV k="kinds"><span className="dim">{[...new Set(g.pois.map((p) => p.kind))].join(", ") || "—"}</span></KV>}
    </Panel>
  );
}
