import { useState } from "react";
import type { AgentStatus, Mode } from "../protocol";
import { describeAction } from "../protocol";
import { Panel } from "./ui";

interface Props {
  s: AgentStatus | null;
  connected: boolean;
  onGoal: (t: string) => Promise<boolean>;
  onPause: () => void;
  onResume: () => void;
  onMode: (m: Mode) => void;
  onStep: () => void;
  onKill: () => void;
}

const MODES: { m: Mode; hint: string }[] = [
  { m: "observe", hint: "perceive + plan, never act" },
  { m: "assist", hint: "propose next action, wait for Step" },
  { m: "auto", hint: "act autonomously" },
];

export function SteeringPanel({ s, connected, onGoal, onPause, onResume, onMode, onStep, onKill }: Props) {
  const [goal, setGoal] = useState("");
  const state = s?.state;
  const canPause = state === "running" || state === "idle";
  const canResume = state === "paused" || state === "stopped" || state === "error" || state === "idle";
  const stepEnabled = s?.mode === "assist" && state === "running" && !!s?.proposed;
  return (
    <Panel title="Steering" testId="steering-panel" right={<span className="tag dim">Esc = kill</span>}>
      <form
        className="goal-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const t = goal.trim();
          if (t && (await onGoal(t))) setGoal("");
        }}
      >
        <input
          data-testid="goal-input"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="New goal… e.g. « farm Tofus near Astrub until 50 pods free »"
          spellCheck={false}
        />
        <button type="submit" disabled={!connected || !goal.trim()} data-testid="goal-submit">Set goal</button>
      </form>

      <div className="row gap">
        <button className="btn" data-testid="btn-pause" disabled={!connected || !canPause} onClick={onPause}>❚❚ Pause</button>
        <button className="btn" data-testid="btn-resume" disabled={!connected || !canResume} onClick={onResume}>
          ▶ {state === "stopped" ? "Resume (reset kill)" : "Resume"}
        </button>
      </div>

      <div className="seg" role="radiogroup" aria-label="mode">
        {MODES.map(({ m, hint }) => (
          <button
            key={m}
            role="radio"
            aria-checked={s?.mode === m}
            className={s?.mode === m ? "on" : ""}
            title={hint}
            data-testid={`mode-${m}`}
            disabled={!connected}
            onClick={() => onMode(m)}
          >{m}</button>
        ))}
      </div>

      <div className={`proposal ${s?.mode === "assist" ? "" : "muted"}`} data-testid="proposal">
        <div className="proposal-l">
          <div className="k">proposed action</div>
          {s?.proposed ? (
            <>
              <div className="mono">{describeAction(s.proposed)}</div>
              {s.proposed.reason && <div className="dim wrap">{s.proposed.reason}</div>}
            </>
          ) : (
            <div className="dim">{s?.mode === "assist" ? (state === "running" ? "none yet" : `agent ${state}`) : "only in assist mode"}</div>
          )}
        </div>
        <button className="btn step" data-testid="btn-step" disabled={!connected || !stepEnabled} onClick={onStep}>Step ⏎</button>
      </div>

      <button className="kill" data-testid="btn-kill" onClick={onKill} title="steer.kill — stops all input immediately (Esc)">
        KILL
        <small>stop all input · Esc</small>
      </button>
    </Panel>
  );
}
