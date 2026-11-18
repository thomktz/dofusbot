import type { LogEntry } from "../protocol";
import { Panel } from "./ui";

/** A human-readable trace of the agent's narrated choices and confirmed packets. */
export function AgentActivityPanel({ logs }: { logs: LogEntry[] }) {
  const entries = logs.filter(entry => entry.source === "llm" || entry.source === "protocol").slice(-8).reverse();
  return <Panel title="Agent activity">
    {entries.length === 0 ? <span className="dim small">No agent activity yet.</span> :
      <div className="agent-activity">{entries.map((entry, index) => <div className={`agent-activity-row ${entry.source}`} key={`${entry.ts}-${index}`}>
        <span>{entry.source === "llm" ? "✦" : "✓"}</span><span>{entry.message}</span>
      </div>)}</div>}
  </Panel>;
}
