import { useState } from "react";
import type { AgentStatus, GameState, LogEntry, NpcsList, NetworkScanResult, NetworkScanStatus, OverlayStatus, PlannerStatus } from "../protocol";
import { describeAction } from "../protocol";
import { KV, Panel } from "./ui";

function GameItemIcon({ id, alt }: { id: number | null | undefined; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (id == null || failed) return null;
  return <img className="game-item-icon" src={`/assets/items/${id}.png`} alt="" aria-label={alt} onError={() => setFailed(true)} />;
}

interface Props {
  s: AgentStatus | null;
  game: GameState | null;
  planner: PlannerStatus | null;
  npcs: NpcsList | null;
  network: NetworkScanStatus;
  overlay: OverlayStatus | null;
  logs: LogEntry[];
  connected: boolean;
  onStart: () => void;
  onScan: () => void;
  onAttack: (index: number) => void;
  onFight: () => void;
  onKill: () => void;
  onResume: () => void;
  onReset: () => void;
  onNetworkStart: () => void;
  onNetworkStop: () => void;
}

const PHASE: Record<string, string> = {
  placement: "placement (challenges → PRÊT)", my_turn: "our turn", enemy_turn: "monsters' turn", ended: "ended",
};

/** Dashboard fight flow: Locate NPCs → Attack → fight sequence, live status, kill switch. */
export function CombatPanel({ s, game, planner, npcs, network, overlay, logs, connected, onStart, onScan, onAttack, onFight, onKill, onResume, onReset, onNetworkStart, onNetworkStop }: Props) {
  const stopped = s?.state === "stopped";
  const armed = !!planner?.armed && !stopped;
  const primed = connected && armed;
  const busy = !!planner?.goal;
  const f = game?.fight ?? null;
  const me = f?.fighters.find((x) => x.is_self) ?? null;
  const enemies = f?.fighters.filter((x) => x.team === "enemy") ?? [];
  const actions = logs.filter((l) => l.action).slice(-6).reverse();
  const doing = s?.task ?? planner?.outcome?.reason ?? (busy ? "…" : "idle");
  return (
    <Panel title="Combat" testId="combat-panel"
      right={<span className={`tag ${stopped ? "err" : busy ? "ok" : "dim"}`} data-testid="combat-state">
        {stopped ? "KILLED" : busy ? (planner?.skill ?? "running") : "idle"}
      </span>}>
      <button className="kill big" data-testid="btn-combat-kill" onClick={onKill}
        title="Stops all input at once and leaves the Dofus window alone (no keep-on-top, no auto-align). ctrl+alt+cmd+K works too.">
        KILL SWITCH
        <small>stop everything · window left alone · Esc / ctrl+alt+cmd+K</small>
      </button>
      {stopped && (
        <div className="row gap combat-stopped">
          <span className="bad">Stopped. keep-on-top {overlay?.keep_on_top ? "on" : "off"}. Resume, then Start again.</span>
          <button className="btn" data-testid="btn-combat-resume" disabled={!connected} onClick={onResume}>▶ Resume (reset kill)</button>
        </div>
      )}

      <button className={`btn start ${armed ? "on" : ""}`} data-testid="btn-start" disabled={!connected || stopped || armed} onClick={onStart}
        title="Align Dofus onto the slot and keep it in front (~1x/s) until the kill switch">
        {armed ? "● STARTED: Dofus aligned and kept in front" : "▶ START: align Dofus and keep it in front"}
      </button>

      <div className="combat-btns">
        <button className="btn" data-testid="btn-locate" disabled={!primed} onClick={onScan}
          title={primed ? "Hold the entity key (Z), read the name plates" : "Control is not activated"}>🔍 Locate NPCs</button>
        <button className="btn" data-testid="btn-fight" disabled={!primed} onClick={onFight}
          title={primed ? "Start during character placement: Choisir les challenges → PRÊT → turns → FERMER" : "Control is not activated"}>⚔ Start fight sequence</button>
        <button className="btn reset" data-testid="btn-reset" disabled={!connected} onClick={onReset}
          title="Back to idle: clears the goal, the picked NPC and any fight state, mode → observe. Sends no click or key; the kill switch keeps working.">⟲ Reset (idle)</button>
      </div>

      <div className="network-scan" data-testid="network-scan">
        <div className="row gap">
          <span className="k">network scan</span>
          <span className={`tag ${network.state === "listening" || network.state === "capturing" ? "warn" : network.state === "ready" ? "ok" : network.state === "error" ? "err" : "dim"}`} data-testid="network-scan-state">{network.state}</span>
          {network.state === "listening" || network.state === "capturing" ? (
            <button className="btn small" data-testid="btn-network-stop" onClick={onNetworkStop}>■ Stop listener</button>
          ) : (
            <button className="btn small" data-testid="btn-network-start" disabled={!connected} onClick={onNetworkStart}>● Activate listener</button>
          )}
        </div>
        <div className="dim small">activated listener · read-only · live map and fight status from port 5555</div>
        {network.error && <div className="bad wrap small" data-testid="network-scan-error">{network.error}</div>}
        {network.result && (
          <div className="network-results" data-testid="network-results">
            <div className="mono small">{network.result.packets} packets · {network.result.bytes} stream bytes · {network.result.groups.length} group(s)</div>
            {network.result.groups.length === 0 ? <div className="dim small">No monster groups in this capture.</div> : network.result.groups.map((g) => (
              <div className="network-group" key={`${g.entity_id}-${g.cell}`}>
                <span className="tag warn">group {g.entity_id}</span>
                <span className="mono">cell {g.cell ?? "?"}</span>
                <span className="dim small">{g.members.length ? g.members.map((m) => `${m.monster_id} · raw4 ${m.raw_field_4 ?? "?"}`).join(", ") : "members unavailable"}</span>
              </div>
            ))}
            {network.result.players.length > 0 && <div className="dim small">players: {network.result.players.map((p) => `${p.name ?? "?"} · cell ${p.cell ?? "?"}`).join(", ")}</div>}
          </div>
        )}
      </div>

      <div className="npc-list" data-testid="npc-list">
        {!npcs || npcs.entities.length === 0 ? (
          <div className="dim">{npcs ? "nothing found on this map" : "press Locate NPCs"}</div>
        ) : npcs.entities.map((e, i) => (
          <div className="npc-row" key={`${e.name}-${i}`} data-testid={`npc-${i}`}>
            <span className={`tag ${e.kind === "monster" ? "warn" : "dim"}`}>{e.kind}</span>
            <span className="npc-name" title={e.members?.map((m) => `${m.name} (${m.level})`).join(", ")}>{e.name}</span>
            <span className="dim mono">{e.level != null ? `niv. ${e.level}` : ""}{e.cell != null ? ` · cell ${e.cell}` : ""}</span>
            {e.kind === "monster" && (
              <button className="btn small" data-testid={`btn-attack-${i}`} disabled={!primed}
                onClick={() => onAttack(i)}>Attack</button>
            )}
          </div>
        ))}
      </div>

      <div className="combat-status" data-testid="combat-status">
        <KV k="bot">{s ? `${s.state} · ${s.mode}` : "—"}</KV>
        <KV k="goal">{planner?.goal ?? s?.goal ?? "—"}</KV>
        <KV k="doing"><span data-testid="combat-doing">{doing}</span></KV>
        {s?.proposed && <KV k="next">{describeAction(s.proposed)}{s.proposed.reason ? ` — ${s.proposed.reason}` : ""}</KV>}
        <KV k="screen">{game ? `${game.screen} (${Math.round(game.confidence * 100)}%)` : "—"}</KV>
        {f && <KV k="fight"><span data-testid="combat-phase">{PHASE[f.phase] ?? f.phase}</span>{f.timer_s != null ? ` · ${f.timer_s}s` : ""}</KV>}
        {f && <KV k="board">{me ? `us on cell ${me.cell}` : "us: not found"} · {enemies.length} enemy(ies)</KV>}
        {game && <KV k="HP / PA / PM">{game.character.hp ?? "—"} / {planner?.fight?.pa_left ?? game.character.pa ?? "—"} / {planner?.fight?.pm_left ?? game.character.pm ?? "—"}</KV>}
        {planner?.preview && <KV k="preview"><span className={planner.preview.startsWith("preview parsed") ? "" : "bad"} data-testid="combat-preview">{planner.preview}</span></KV>}
        {planner?.fight?.plan && <KV k="plan">{planner.fight.plan}</KV>}
        {planner?.attention && <div className="bad wrap" data-testid="combat-attention">needs you: {planner.attention}</div>}
        {planner?.outcome && !busy && <div className="dim wrap">last: {planner.outcome.status} — {planner.outcome.reason}</div>}
      </div>

      <div className="combat-actions" data-testid="combat-actions">
        <div className="k">last actions</div>
        {actions.length === 0 ? <div className="dim">none yet</div> : actions.map((l, i) => (
          <div key={i} className="mono small">{describeAction(l.action!)}{l.action!.reason ? ` — ${l.action!.reason}` : ""}</div>
        ))}
      </div>
    </Panel>
  );
}

/** Read-only network perception surface. Kept separate while protocol decoding is under test. */
export function NetworkScanPanel({ network, connected, npcs, onAttackGroup, onTalkNpc, onUseInteractive, onStart, onStop, onSemiAutoFight }: {
  network: NetworkScanStatus;
  connected: boolean;
  npcs: NpcsList | null;
  onAttackGroup: (entityId: number | null) => void;
  onTalkNpc: (entityId: number, cell: number | null) => void;
  onUseInteractive: (id: number, cell: number | null) => void;
  onStart: () => void;
  onStop: () => void;
  onSemiAutoFight: () => void;
}) {
  const result = network.result;
  const activeFight = isActiveFight(result);
  const mapActors = Object.values((result?.world as { actors?: Record<string, { id: number; kind?: string; name?: string | null; cell?: number | null }> } | undefined)?.actors ?? {});
  const friendlyNpcs = mapActors.filter(a => a.kind === "npc");
  const npcIds = new Set(friendlyNpcs.map(a => a.id));
  const mapPlayers = result?.players.filter(p => !npcIds.has(p.entity_id)) ?? [];
  const xpByCell = new Map((npcs?.entities ?? []).filter(e => e.kind === "monster" && e.cell != null && e.xp != null).map(e => [e.cell!, e.xp!]));
  return (
    <Panel title="World" testId="network-scan-panel"
      right={<span className={`tag ${network.state === "listening" || network.state === "capturing" ? "warn" : network.state === "ready" ? "ok" : network.state === "error" ? "err" : "dim"}`} data-testid="network-scan-state">{network.state}</span>}>
      {network.state === "listening" || network.state === "capturing" ? (
        <div className="world-listener"><span><i className="dot on" /> Live game data is updating</span><button className="btn small" data-testid="btn-network-stop" onClick={onStop}>Stop & save</button></div>
      ) : (
        <div className="world-listener"><span className="dim">No live listener</span><button className="btn small" data-testid="btn-network-start" disabled={!connected} onClick={onStart}>Start listening</button></div>
      )}
      {network.error && <div className="bad wrap small" data-testid="network-scan-error">{network.error}</div>}
      {network.state === "ready" && result && <div className="world-capture-note">Showing the last saved scan. Start listening for current data.</div>}
      {result && (
        <div className="network-results" data-testid="network-results">
          {activeFight && result.fight?.phase === "placement" && <div className="fight-semi-auto">
            <div><strong>Character placement</strong><div className="dim small">Skip challenges, press PRÊT, then stop for your manual turn.</div></div>
            <button className="btn sword" disabled={!connected} onClick={onSemiAutoFight}>⚔ Fight</button>
          </div>}
          <div className="entity-section">
            <div className="entity-section-title"><span>Mobs</span><span className="tag warn">{result.groups.length}</span></div>
            {result.groups.length === 0 ? <div className="empty">No mobs detected on this map.</div> : result.groups.map((g) => {
              const total = g.members.reduce((sum, m) => sum + (m.level ?? 0), 0);
              const staticXp = g.members.reduce((sum, m) => sum + (m.xp ?? 0), 0);
              const xp = staticXp || (g.cell != null && xpByCell.has(g.cell) ? xpByCell.get(g.cell)! : null);
              return <div className="entity-card mob-card" key={`${g.entity_id}-${g.cell}`}>
                <div className="entity-card-head"><strong>Level {total || "?"} · XP {xp != null ? xp.toLocaleString() : "—"}</strong><span className="entity-card-actions"><span className="mono small">cell {g.cell ?? "?"}</span><button className="btn sword small" disabled={!connected || g.entity_id == null} onClick={() => onAttackGroup(g.entity_id)} title="Engage this protocol-identified monster group">⚔</button></span></div>
                <div className="entity-member-list">{g.members.length ? g.members.map((m, i) => <div className="entity-member" key={`${m.monster_id}-${i}`}>
                  <span className="ellip">{m.name ?? `Monster ${m.monster_id}`}</span><span className="entity-member-level">lvl {m.level ?? "?"}</span>
                </div>) : <div className="dim small">Members unavailable</div>}</div>
              </div>;
            })}
          </div>
          <div className="entity-section">
            <div className="entity-section-title"><span>Friendly NPCs</span><span className="tag dim">{friendlyNpcs.length}</span></div>
            {friendlyNpcs.length === 0 ? <div className="empty">No friendly NPCs detected on this map.</div> : friendlyNpcs.map(a => <div className="entity-row" key={`${a.id}-${a.cell}`}><span className="entity-icon npc">N</span><strong>{a.name ?? "Unnamed NPC"}</strong><span className="mono small">cell {a.cell ?? "?"}</span><button className="btn small" disabled={!connected || a.cell == null} onClick={() => onTalkNpc(a.id, a.cell ?? null)} title="Focus Dofus and click this NPC once">Talk</button></div>)}
          </div>
          <div className="entity-section">
            <div className="entity-section-title"><span>Clickables</span><span className="tag dim">{result.interactives?.length ?? 0}</span></div>
            {!result.interactives?.length ? <div className="empty">No interactive elements reported on this map.</div> : result.interactives.map((item) => {
              const state = item.state == null ? (item.available === true ? "ready" : "state unknown") : item.state === 0 ? "ready" : `server state ${item.state}`;
              const label = item.resource_name ?? item.name ?? (item.gfx_id != null ? `Interactive gfx ${item.gfx_id}` : "Interactive");
              return <div className="entity-row" key={`interactive-${item.id}`}><span className="entity-icon npc">✦</span><GameItemIcon id={item.icon_id} alt={label} /><strong>{label}</strong><span className="mono small">cell {item.cell ?? "?"} · {state}{item.job_name ? ` · ${item.job_name}${item.level_min != null ? ` lvl ${item.level_min}` : ""}` : ""}</span><button className="btn small" disabled={!connected || item.cell == null} onClick={() => onUseInteractive(item.id, item.cell ?? null)} title="Focus Dofus and click this interactive once">Use</button></div>;
            })}
          </div>
          <div className="entity-section">
            <div className="entity-section-title"><span>Players</span><span className="tag dim">{mapPlayers.length}</span></div>
            {mapPlayers.length === 0 ? <div className="empty">No other players detected on this map.</div> : mapPlayers.map((p) => <div className="entity-row" key={`${p.entity_id}-${p.cell}`}><span className="entity-icon player">P</span><strong>{p.name ?? "Unnamed player"}</strong><span className="mono small">cell {p.cell ?? "?"}</span></div>)}
          </div>
          {activeFight && <><div className="k">fight actors / confirmed fighter data</div>
          {result.fight_monsters.length === 0 ? <div className="dim small">No fight monster records decoded.</div> : result.fight_monsters.map((m) => (
            <div className="network-group" key={`${m.monster_id}-${m.grade}-${m.cell ?? "?"}`}>
              <span>{m.name ?? `monster ${m.monster_id}`}</span>
              <span className="mono">level {m.level ?? "?"} · cell {m.cell ?? "?"}</span>
            </div>
          ))}
          {(result.fight_players?.length ?? 0) > 0 && <div className="network-group">
            <span>fight player</span>
            <span className="mono">{result.fight_players?.map((p) => `${p.name ?? p.entity_id} · cell ${p.cell ?? "?"}`).join(", ")}</span>
          </div>}
          {(result.placement_moves?.length ?? 0) > 0 && <div className="network-group">
            <div className="dim small">placement cell requests (client → server)</div>
            <div className="mono small">{result.placement_moves?.join(" → ")}</div>
          </div>}
          {result.fight_map_id && <div className="dim small">fight map asset id: <span className="mono">{result.fight_map_id}</span></div>}
          {(result.fight_events?.length ?? 0) > 0 && <div className="network-group">
            <span>fight timeline</span>
            <span className="mono small">{result.fight_events?.map((e) => `${e.kind}${e.turn != null ? ` T${e.turn}` : ""}${e.action_id != null ? ` a${e.action_id}` : ""}`).join(" → ")}</span>
          </div>}</>}
          <div className="dim small">Source: Dofus protocol capture. The map state is read-only; action execution remains behind the safety gate.</div>
        </div>
      )}
    </Panel>
  );
}

export function isActiveFight(result: NetworkScanResult | null): boolean {
  if (!result) return false;
  if (result.fight) return result.fight.active;
  const events = result.fight_events ?? [];
  if (!events.length) return false;
  let active = false;
  for (const event of events) {
    if (event.kind === "fighter_show" || event.kind === "placement_update" || event.kind === "turn_marker" || event.kind === "spell_cast") active = true;
    if (event.kind === "fight_result") active = false;
  }
  return active;
}
