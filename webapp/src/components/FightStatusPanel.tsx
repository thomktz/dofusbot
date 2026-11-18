import type { NetworkFightSnapshot, NetworkScanResult } from "../protocol";
import { Panel } from "./ui";

function Bar({ value, max, tone }: { value?: number | null; max?: number | null; tone: string }) {
  const known = value != null && max != null && max > 0;
  const pct = known ? Math.max(0, Math.min(100, (value! / max!) * 100)) : 0;
  return <div className="fight-stat-bar"><div className={`fight-stat-fill ${tone}`} style={{ width: `${pct}%` }} /><span>{known ? `${value}/${max}` : "? / ?"}</span></div>;
}

export function FightStatusPanel({ result }: { result: NetworkScanResult | null }) {
  const fight = result?.fight as NetworkFightSnapshot | undefined;
  if (!fight?.active) return null;
  const fighters = fight.fighters ?? [];
  const self = fighters.find(f => f.id > 0);
  const enemies = fighters.filter(f => f.id < 0 || f.team === 1);
  const label = (f: typeof fighters[number]) => f.name ?? (f.monster_id ? `monster ${f.monster_id}` : `actor ${f.id}`);
  return <Panel title="Live fight" right={<span className="tag warn">{fight.phase}{fight.turn != null ? ` · T${fight.turn}` : ""}</span>}>
    <div className="fight-status-head mono small">map {fight.map_id ?? "—"} · {enemies.filter(f => f.alive !== false).length} alive enemy/enemies · {fight.events.length} live events</div>
    {self && <div className="fight-fighter self"><div><strong>{label(self)}</strong><span className="mono small">cell {self.cell ?? "?"} · PA {self.pa ?? "?"} · PM {self.pm ?? "?"}</span></div><Bar value={self.hp} max={self.hp_max} tone="ally" /></div>}
    <div className="fight-enemies">{enemies.map(enemy => <div className={`fight-fighter ${enemy.alive === false ? "dead" : ""}`} key={enemy.id}>
      <div><strong>{label(enemy)}</strong><span className="mono small">{enemy.level != null ? `level ${enemy.level}` : ""} · cell {enemy.cell ?? "?"} · {enemy.alive === false ? "DEAD" : `PA ${enemy.pa ?? "?"} · PM ${enemy.pm ?? "?"}`}</span></div>
      <Bar value={enemy.hp} max={enemy.hp_max} tone="enemy" />
    </div>)}</div>
    <div className="dim small">Stats are protocol-backed only. A `?` means the current fight messages have not exposed that value yet.</div>
  </Panel>;
}
