import { useMemo, useState } from "react";
import type { DofusStatus, GameState, NetworkScanResult } from "../protocol";
import { KV, Panel } from "./ui";

type Tab = "overview" | "character" | "map" | "inventory" | "jobs" | "fight" | "protocol";

function value(v: unknown, fallback = "—") { return v == null || v === "" ? fallback : String(v); }
function Empty({ children }: { children: string }) { return <div className="env-empty">{children}</div>; }

/** A single place to inspect everything the network session knows about the game. */
export function EnvironmentPanel({ result, game, dofus, connected, onLaunch, onAuthenticate }: { result: NetworkScanResult | null; game: GameState | null; dofus: DofusStatus; connected: boolean; onLaunch: () => void; onAuthenticate: () => void }) {
  const [tab, setTab] = useState<Tab>("overview");
  const ch = result?.character;
  const self = ch?.entity_id != null ? result?.players.find(p => p.entity_id === ch.entity_id) : undefined;
  const fight = result?.fight;
  const protocol = useMemo(() => {
    const grouped = new Map<string, { count: number; bytes: number }>();
    for (const e of result?.protocol_events ?? []) {
      const old = grouped.get(e.alias) ?? { count: 0, bytes: 0 };
      grouped.set(e.alias, { count: old.count + 1, bytes: old.bytes + e.size });
    }
    return [...grouped.entries()].sort((a, b) => b[1].count - a[1].count);
  }, [result?.protocol_events]);
  const tabs: [Tab, string][] = [["overview", "Overview"], ["character", "Character"], ["map", "Map & actors"], ["inventory", "Inventory"], ["jobs", "Jobs"], ["fight", "Fight"]];
  return <Panel title="Environment" right={<span className="tag">{result ? "live data" : "no session"}</span>}>
    <nav className="env-tabs" aria-label="Environment sections">
      {tabs.map(([id, label]) => <button key={id} className={`env-tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>)}
    </nav>
    {tab === "overview" && <div className="env-launch"><div><strong>{dofus.launcher ?? "Ankama Launcher"} → {dofus.app}</strong><span className="dim"> · {dofus.state}</span>{dofus.error && <div className="bad small">{dofus.error}</div>}</div><div className="row gap"><button className="btn small" disabled={!connected || dofus.state === "authenticating"} onClick={onAuthenticate}>{dofus.state === "authenticating" ? "Authenticating…" : "Authenticate Ankama"}</button><button className="btn small" disabled={!connected || dofus.state === "launching"} onClick={onLaunch}>{dofus.state === "launching" ? "Launching…" : "Launch + listen"}</button></div></div>}
    {!result && <Empty>Launch Dofus + listen, or start a network scan to populate the environment.</Empty>}
    {result && tab === "overview" && <div className="env-grid">
      <KV k="Context">{fight?.active ? `Fight · ${fight.phase}` : game?.screen === "fight" ? "Fight (screen)" : "World"}</KV>
      <KV k="Map">{value(result.map_id ?? result.fight_map_id ?? game?.map.coords?.join(", "))}</KV>
      <KV k="Position">cell {value(ch?.cell ?? self?.cell)} · {game?.map.coords?.join(", ") ?? (result.map_position ? `${result.map_position.x}, ${result.map_position.y}` : "coords unknown")}</KV>
    </div>}
    {result && tab === "character" && <div className="env-grid">
      <KV k="Name">{value(ch?.name ?? self?.name ?? game?.character.name)}</KV><KV k="Level">{value(ch?.level ?? game?.character.level)}</KV>
      <KV k="XP">{ch?.experience != null ? `${ch.experience} / ${value(ch.experience_next)}` : "not in this capture"}</KV>
      <KV k="Health">{ch?.hp != null ? `${ch.hp} / ${value(ch.hp_max)}` : value(game?.character.hp)}</KV>
      <KV k="PA / PM">{value(ch?.pa ?? game?.character.pa)} / {value(ch?.pm ?? game?.character.pm)}</KV>
      <KV k="Entity / cell">{value(ch?.entity_id)} / {value(ch?.cell)}</KV>
      {(result.spells?.length ?? 0) > 0 && <div className="spell-bar" aria-label="Current spell bar">{result.spells!.map(spell => <div className="spell-chip" key={spell.spell_id} title={`${spell.name ?? `Spell ${spell.spell_id}`} · ${spell.ap_cost ?? "?"} PA`}><img src={`/assets/spells/${spell.icon_id}.png`} alt="" /><span>{spell.name ?? `Spell ${spell.spell_id}`}</span><b>{spell.ap_cost ?? "?"}</b></div>)}</div>}
    </div>}
    {result && tab === "map" && <div className="env-list">
      <div className="env-section-title">Players ({result.players.length})</div>
      {result.players.map(p => <div className="env-row" key={`p-${p.entity_id}`}><span>{value(p.name, `Entity ${p.entity_id}`)}</span><span className="dim">cell {value(p.cell)}</span></div>)}
      <div className="env-section-title">Mob groups ({result.groups.length})</div>
      {result.groups.map(g => <div className="env-row" key={`g-${g.entity_id}`}><span>{g.members.map(m => m.name ?? `Monster ${m.monster_id}`).join(", ") || "Unknown group"}</span><span className="dim">cell {value(g.cell)} · {g.members.length} members</span></div>)}
      <div className="env-section-title">World elements</div>
      <div className="dim small">{result.interactives?.length ?? 0} interactives · {result.stated_elements?.length ?? 0} stated elements</div>
    </div>}
    {result && tab === "inventory" && <div className="env-list">
      {result.inventory && (result.inventory.objects.length || result.inventory.weight != null || result.inventory.kamas != null) ? <><div className="env-grid"><KV k="Kamas">{value(result.inventory.kamas)}</KV><KV k="Weight">{value(result.inventory.weight)} / {value(result.inventory.weight_max)}</KV><KV k="Objects">{result.inventory.objects.length || "not captured"}</KV></div>{result.inventory.objects.slice(0, 30).map((o, i) => <div className="env-row" key={`i-${o.uid ?? i}`}><span>{o.name ?? `Item ${value(o.gid)}`}{o.level != null ? ` · lvl ${o.level}` : ""}</span><span className="dim">×{value(o.quantity)}</span></div>)}</> : <><Empty>Inventory data is not present in this capture.</Empty><div className="dim small">It appears automatically after the relevant character/world packet arrives.</div></>}
    </div>}
    {result && tab === "jobs" && <div className="env-list">{result.jobs?.length ? result.jobs.map(j => <div className="env-row" key={`job-${j.job_id}`}><span>Job {value(j.job_id)} · level {value(j.level)}</span><span className="dim">{value(j.xp)} XP / {value(j.xp_next)}</span></div>) : <><Empty>Job levels are not present in this capture.</Empty><div className="dim small">Job data arrives with the character/world-entry sequence. It will appear here once that one-shot packet is captured.</div></>}</div>}
    {result && tab === "fight" && <div className="env-list">
      {!fight?.active && !result.fight_events?.length ? <Empty>No fight context in this session.</Empty> : <>
        <KV k="State">{fight?.active ? `${fight.phase} · turn ${value(fight.turn)}` : "ended / recorded"}</KV>
        <div className="env-section-title">Fighters ({fight?.fighters.length ?? 0})</div>
        {(fight?.fighters ?? []).map(f => <div className="env-row" key={`f-${f.id}`}><span>{value(f.name, `Entity ${f.id}`)}</span><span className="dim">{f.hp != null ? `${f.hp}/${value(f.hp_max)} HP` : "HP ?"} · cell {value(f.cell)}</span></div>)}
        {(result.spells?.length ?? 0) > 0 && <><div className="env-section-title">Spells</div>{result.spells!.map(spell => <div className="env-row" key={spell.spell_id}><span>{spell.name ?? `Spell ${spell.spell_id}`}{spell.level != null ? ` · lvl ${spell.level}` : ""}</span><span className="dim">{spell.ap_cost != null ? `${spell.ap_cost} PA` : ""}{spell.range != null ? ` · range ${spell.min_range ?? 0}–${spell.range}` : ""}</span></div>)}</>}
        <div className="dim small">{result.fight_events?.length ?? 0} fight events · {result.placement_moves?.length ?? 0} placement moves</div>
      </>}
    </div>}
    {result && tab === "protocol" && <div className="env-list">
      <div className="dim small">Every server packet retained from the latest capture. Unknown aliases are still useful for extending the decoder.</div>
      {protocol.length ? protocol.map(([alias, stats]) => <div className="env-row" key={alias}><span className="mono">{alias}</span><span className="dim">{stats.count} packets · {stats.bytes} B</span></div>) : <Empty>No protocol events retained.</Empty>}
    </div>}
  </Panel>;
}
