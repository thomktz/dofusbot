import { useState } from "react";
import type { AgentGoalStatus } from "../protocol";
import { Panel } from "./ui";

export function AgentGoalPanel({ status, connected, onStart, onPause, onResume }: {
  status: AgentGoalStatus; connected: boolean;
  onStart: (goal: string) => void; onPause: () => void; onResume: () => void;
}) {
  const [goal, setGoal] = useState("");
  const run = status.run;
  return <Panel title="Local agent" right={<span className={`tag ${status.active ? "good" : "dim"}`}>{run?.state ?? "idle"}</span>}>
    <form className="goal-form" onSubmit={event => { event.preventDefault(); if (goal.trim()) { onStart(goal.trim()); setGoal(""); } }}>
      <input value={goal} onChange={event => setGoal(event.target.value)} disabled={!connected || status.active}
        placeholder="e.g. Complete Incarnam quests" />
      <button disabled={!connected || status.active || !goal.trim()}>Run</button>
    </form>
    {run && <>
      <div className="small"><strong>{run.goal}</strong> · step {run.steps}</div>
      {run.last_narration && <div className="dim wrap">{run.last_narration}</div>}
      {run.last_action?.action && <div className="mono small">{run.last_action.action}</div>}
      {run.error && <div className="bad small">{run.error}</div>}
      {status.active ? <button className="btn small" onClick={onPause}>Pause</button> : run.state !== "completed" && <button className="btn small" onClick={onResume}>Resume</button>}
    </>}
  </Panel>;
}
