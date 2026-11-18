import type { GameState, NetworkScanResult } from "../protocol";

function Progress({ value, max, tone, label }: { value?: number | null; max?: number | null; tone: string; label: string }) {
  const pct = value != null && max ? Math.max(0, Math.min(100, value / max * 100)) : 0;
  const display = value != null && max ? `${value.toLocaleString()} / ${max.toLocaleString()}` : "—";
  return <div className={`dash-meter ${tone}`}><div className="dash-meter-label"><span>{label}</span><strong>{display}</strong></div><div className="dash-progress"><i className={tone} style={{ width: `${pct}%` }} /></div></div>;
}

export function DashboardOverview({ game, result }: { game: GameState | null; result: NetworkScanResult | null }) {
  const fight = result?.fight?.active ? result.fight : null;
  const self = fight?.fighters.find(f => f.id > 0);
  const netChar = result?.character;
  const name = netChar?.name ?? game?.character.name ?? self?.name ?? "Your character";
  const level = netChar?.level ?? game?.character.level ?? null;
  const hp = game?.character.hp ?? self?.hp ?? netChar?.hp ?? null;
  const hpMax = game?.character.hp_max ?? self?.hp_max ?? netChar?.hp_max ?? null;
  const pa = game?.character.pa ?? self?.pa ?? netChar?.pa ?? null;
  const pm = game?.character.pm ?? self?.pm ?? netChar?.pm ?? null;
  const xp = netChar?.experience ?? null;
  const xpNext = netChar?.experience_next ?? null;
  return <section className="dashboard-overview" data-testid="dashboard-overview">
    <div className="dash-hero">
      <div className="dash-avatar" aria-label="Character crest"><span>{name.slice(0, 1).toUpperCase()}</span><b>{level ?? "—"}</b></div>
      <div className="dash-character">
        <div className="dash-identity"><strong>{name}</strong><span>{level != null ? `Level ${level}` : "Level —"} · {fight ? `In fight · turn ${fight.turn ?? "—"}` : "Exploring"}</span></div>
        <Progress value={hp} max={hpMax} tone="hp" label="Health" />
        <Progress value={xp} max={xpNext} tone="xp" label="Experience" />
      </div>
      <div className="dash-action-points"><div className="dash-orb pa"><span>PA</span><b>{pa ?? "—"}</b></div><div className="dash-orb pm"><span>PM</span><b>{pm ?? "—"}</b></div></div>
    </div>
  </section>;
}
