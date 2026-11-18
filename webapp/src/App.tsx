import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { Link } from "./link";
import { MockBackend } from "./mock/mockBackend";
import { parseParams } from "./params";
import type { ClientPayloads, ClientType } from "./protocol";
import { initialState, reducer } from "./store";
import { Header } from "./components/Header";
import { isActiveFight, NetworkScanPanel } from "./components/CombatPanel";
import { FightReplay } from "./components/FightReplay";
import { WorldMapPanel } from "./components/WorldMapPanel";
import { FightStatusPanel } from "./components/FightStatusPanel";
import { Toasts } from "./components/Toasts";
import { DashboardOverview } from "./components/DashboardOverview";
import { AdventurePanel } from "./components/AdventurePanel";
import { AgentGoalPanel } from "./components/AgentGoalPanel";
import { AgentActivityPanel } from "./components/AgentActivityPanel";

const params = parseParams(window.location.search);

export function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<number | null>(null);
  const [calibrationBusy, setCalibrationBusy] = useState<string | null>(null);
  const [calibrationSamples, setCalibrationSamples] = useState(0);
  const [profileLabel, setProfileLabel] = useState("");
  const [editingProfile, setEditingProfile] = useState<string | null>(null);
  const [editedLabel, setEditedLabel] = useState("");
  const link = useMemo(() => new Link({ url: params.wsUrl, forceMock: params.forceMock, createMock: () => new MockBackend() }), []);
  const [st, dispatch] = useReducer(reducer, link.state, initialState);
  useEffect(() => {
    const offS = link.onState((s) => dispatch({ t: "link", s }));
    const offM = link.onMessage((m) => dispatch({ t: "msg", m }));
    link.start();
    return () => { offS(); offM(); link.stop(); };
  }, [link]);
  const connected = st.link.conn === "open";
  const activeAccount = selectedAccount ?? st.accounts.active_account_id;
  const request = useCallback(<K extends ClientType>(type: K, payload: ClientPayloads[K]) =>
    link.send(type, payload).then((r) => {
      if (!r.ok) dispatch({ t: "toast", kind: "error", text: `${type} failed: ${r.error ?? "unknown error"}` });
      return r.ok;
    }), [link]);
  const attackGroup = useCallback((entityId: number | null) => {
    if (entityId == null) {
      dispatch({ t: "toast", kind: "error", text: "This mob group has no live protocol identity." });
      return;
    }
    void request("npcs.attack", { entity_id: entityId, auto: false });
  }, [request]);
  const calibrate = useCallback(async (kind: "cell" | "player", cell?: number) => {
    setCalibrationBusy(kind === "cell" ? `cell ${cell}` : "player movement");
    const ok = await request(kind === "cell" ? "grid.calibrate.cell" : "grid.calibrate.player", kind === "cell" ? { cell: cell! } : {});
    if (ok) {
      await new Promise(resolve => setTimeout(resolve, 10200));
      setCalibrationSamples(n => n >= 2 ? 0 : n + 1);
    }
    setCalibrationBusy(null);
  }, [request]);
  return (
    <main className="pane network-only">
      <div className="app-header-row"><Header link={st.link} server={st.hello?.server ?? null} transport={st.transport.state} /><button className="btn account-button" onClick={() => setSettingsOpen(true)}>Accounts</button><button className="btn settings-button" aria-label="Settings" onClick={() => setSettingsOpen(true)}>⚙</button></div>
        <nav className="account-tabs" aria-label="Accounts"><button className={`account-tab ${selectedAccount === null ? "active" : ""}`} onClick={() => { setSelectedAccount(null); setSettingsOpen(true); }}>Accounts</button>{st.accounts.accounts.map(account => <button key={account.account_id} className={`account-tab ${activeAccount === account.account_id ? "active" : ""}`} onClick={() => { setSelectedAccount(account.account_id); void request("accounts.select", { account_id: account.account_id }); }}>{account.character.name || `Account ${account.account_id}`}<small>{account.character.level != null ? ` · Lv ${account.character.level}` : " · connecting"}</small></button>)}<span className="dim small">independent · one shared model</span></nav>
        <DashboardOverview game={st.game} result={st.network.result} />
        <AgentGoalPanel status={st.agentGoal} connected={connected}
          onStart={(goal) => void request("agent.goal.start", { goal })}
          onPause={() => void request("agent.goal.pause", {})}
          onResume={() => void request("agent.goal.resume", {})} />
        <AgentActivityPanel logs={st.logs} />
        <AdventurePanel result={st.network.result} onReply={(replyId) => void request("dialog.reply", { reply_id: replyId })} onLeave={() => void request("dialog.leave", {})} onUseItem={(uid) => void request("inventory.use", { object_uid: uid })} onQuestNext={() => void request("quest.next", {})} />
        <WorldMapPanel result={st.network.result} onMove={(direction) => void request("map.move", { direction })} />
        <FightStatusPanel result={st.network.result} />
        <NetworkScanPanel network={st.network} connected={connected} npcs={st.npcs} onAttackGroup={attackGroup}
          onTalkNpc={(entityId, cell) => { if (cell != null) void request("npcs.talk", { entity_id: entityId, cell }); }}
          onUseInteractive={(id, cell) => { if (cell != null) void request("interactives.use", { id, cell }); }}
          onStart={() => void request("network.scan.start", {})}
          onStop={() => void request("network.scan.stop", {})}
          onSemiAutoFight={() => void request("fight.semi_auto", {})} />
        {isActiveFight(st.network.result) && <FightReplay result={st.network.result} />}
      <Toasts toasts={st.toasts} dismiss={(id) => dispatch({ t: "dismiss", id })} />
      {settingsOpen && <div className="modal-backdrop" onClick={() => setSettingsOpen(false)}><section className="settings-modal" onClick={e => e.stopPropagation()}>
        <div className="settings-head"><h2>Accounts & settings</h2><div><button className="btn small" onClick={() => void request("network.scan.refresh", {})}>↻ Refresh targets</button><button className="btn small" onClick={() => setSettingsOpen(false)}>Close</button></div></div>
        <h3>Account profiles</h3>
        <p className="dim small">One profile = one Ankama login + one Dofus client. Authenticate and launch each profile separately. When a launched character reaches the game, it appears as a character tab in the top bar; click that tab to switch the dashboard/LLM to that game only.</p>
        <div className="connection-settings"><div><strong>Main account</strong><span className="dim small"> · default profile · {st.dofus.state}</span>{st.dofus.error && <div className="bad small">{st.dofus.error}</div>}</div><div className="row gap"><button className="btn small" disabled={!connected || st.dofus.state === "authenticating"} onClick={() => void request("dofus.authenticate", {})}>Authenticate main</button><button className="btn small" disabled={!connected || st.dofus.state === "launching"} onClick={() => void request("dofus.launch", {})}>{st.dofus.state === "launching" ? "Launching…" : "Launch main game"}</button></div></div>
        <p className="dim small">Add a second account below, then use that account’s own Authenticate and Launch buttons. Tokens remain in separate local files and are never shown here.</p>
        <div className="row gap"><input aria-label="New account profile" value={profileLabel} onChange={e => setProfileLabel(e.target.value)} placeholder="Profile name" /><button className="btn small" disabled={!connected || !profileLabel.trim()} onClick={() => { void request("accounts.profile.create", { label: profileLabel.trim() }); setProfileLabel(""); }}>Add profile</button></div>
        {(st.accounts.profiles ?? []).map(profile => { const client = st.accounts.clients?.find(item => item.profile_id === profile.id); const live = st.accounts.accounts.find(account => account.profile_id === profile.id); const editing = editingProfile === profile.id; return <div className="connection-settings" key={profile.id}><div>{editing ? <div className="row gap"><input aria-label={`Rename ${profile.label}`} value={editedLabel} onChange={e => setEditedLabel(e.target.value)} /><button className="btn small" disabled={!editedLabel.trim()} onClick={() => { void request("accounts.profile.rename", { profile_id: profile.id, label: editedLabel.trim() }); setEditingProfile(null); }}>Save</button><button className="btn small" onClick={() => setEditingProfile(null)}>Cancel</button></div> : <><strong>{profile.label}</strong><span className="dim small"> · {profile.authenticated ? "ready to launch" : "needs authentication"}{client ? ` · game client: ${client.state}` : ""}{live ? ` · ${live.character.name ?? "connected"}` : " · no character connected"}</span></>}{client?.error && <div className="bad small">{client.error}</div>}</div><div className="row gap"><button className="btn small" onClick={() => { setEditingProfile(profile.id); setEditedLabel(profile.label); }}>Rename</button><button className="btn small" disabled={!connected} onClick={() => void request("dofus.authenticate", { profile_id: profile.id })}>Authenticate</button><button className="btn small" disabled={!connected || !profile.authenticated || client?.state === "launched"} onClick={() => void request("dofus.launch", { profile_id: profile.id })}>Launch</button>{live && <button className="btn small" onClick={() => { setSelectedAccount(live.account_id); void request("accounts.select", { account_id: live.account_id }); }}>Switch to {live.character.name ?? profile.label}</button>}</div></div>; })}
        <h3>Calibration</h3>
        <div className="mono small">current {st.overlay?.dofus_window?.frame ? `${Math.round(st.overlay.dofus_window.frame.w)}×${Math.round(st.overlay.dofus_window.frame.h)} at ${Math.round(st.overlay.dofus_window.frame.x)},${Math.round(st.overlay.dofus_window.frame.y)}` : "not detected"}</div>
        <div className="mono small">saved {st.overlay?.slot ? `${Math.round(st.overlay.slot.w)}×${Math.round(st.overlay.slot.h)} at ${Math.round(st.overlay.slot.x)},${Math.round(st.overlay.slot.y)}` : "none"}</div>
        <p className="dim small">Save the current size, then calibrate three moves to different cells. During each 10-second wait, centre the mouse on your character before the timer ends. {calibrationBusy ? `Waiting 10 seconds for ${calibrationBusy} — move in Dofus now.` : `${calibrationSamples}/3 movement samples recorded.`}</p>
        {st.calibration && <div className={`calibration-feedback ${st.calibration.state === "error" ? "bad" : st.calibration.state === "fitted" ? "good" : ""}`}><span>{st.calibration.message}</span><span className="mono small">{st.calibration.samples}/3 samples</span></div>}
        <button className="btn small" disabled={!connected || !st.overlay?.dofus_window?.found} onClick={() => void request("overlay.adopt_current", {})}>Save current Dofus size as default</button>
        <div className="calibration-targets">
          {st.network.result?.character?.entity_id != null ? <div className="calibration-row"><div><strong>Player movement</strong><span className="dim small"> · {st.network.result.character.name ?? "Your character"} · current cell {st.network.result.character.cell ?? "?"}</span></div><button className="btn small" disabled={!connected || !!calibrationBusy} onClick={() => void calibrate("player")}>{calibrationBusy === "player movement" ? "Waiting…" : "Move + calibrate"}</button></div> : <div className="dim small">Your character has not been identified in the current capture yet.</div>}
        </div>
        <h3>Session safety</h3><div className="connection-settings"><span className="dim small">Create a three-hour account session. It checkpoints pods/map and pauses at the bank threshold.</span><button className="btn small" disabled={!connected} onClick={() => void request("session.plan", { duration_minutes: 180 })}>Start 3h session</button></div>
      </section></div>}
    </main>
  );
}
