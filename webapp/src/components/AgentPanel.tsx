import type { AgentStatus } from "../protocol";
import { fmtUptime } from "../format";
import { Check, Dot, KV, Panel } from "./ui";
import { useNow } from "../useNow";

export function AgentPanel({ s, at }: { s: AgentStatus | null; at: number | null }) {
  const now = useNow(1000);
  const stale = at != null && now - at > 5000;
  if (!s) return <Panel title="Agent"><div className="empty">waiting for agent.status…</div></Panel>;
  return (
    <Panel
      title="Agent"
      testId="agent-panel"
      right={stale ? <span className="tag warn">stale {Math.round((now - at!) / 1000)}s</span> : <span className="tag dim">up {fmtUptime(s.uptime_s)}</span>}
    >
      <div className="agent-top">
        <span className={`state st-${s.state}`} data-testid="agent-state">{s.state}</span>
        <span className={`mode md-${s.mode}`} data-testid="agent-mode">{s.mode}</span>
        <span className="hz">{s.loop_hz.toFixed(1)} Hz</span>
      </div>
      {s.error && <div className="err-box">{s.error}</div>}
      <KV k="goal"><span className="wrap" data-testid="agent-goal">{s.goal ?? <i className="dim">none</i>}</span></KV>
      <KV k="task"><span className="wrap mono">{s.task ?? <i className="dim">idle</i>}</span></KV>
      <KV k="llm">
        <Dot on={s.llm.busy ? true : null} title={s.llm.busy ? "thinking" : "idle"} />{" "}
        <span className="mono">{s.llm.backend}</span> · <span className="mono dim ellip" title={s.llm.model}>{s.llm.model}</span>
        {s.llm.busy && <span className="tag acc">thinking</span>}
      </KV>
      <KV k="decision">
        <span className="mono">{s.last_decision_ms == null ? "—" : `${s.last_decision_ms} ms`}</span>
        {s.last_decision_ms != null && <span className="lat"><span style={{ width: `${Math.min(100, s.last_decision_ms / 30)}%` }} className={s.last_decision_ms > 2000 ? "slow" : ""} /></span>}
      </KV>
      <KV k="control">
        <Check ok={s.control.connected} label="connected" />
        <Check ok={s.control.permissions.accessibility} label="accessibility" />
        <Check ok={s.control.permissions.screen_recording} label="screen rec." />
      </KV>
    </Panel>
  );
}
