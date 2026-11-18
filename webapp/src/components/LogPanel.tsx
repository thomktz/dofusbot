import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { LogEntry, LogLevel, LogSource } from "../protocol";
import { describeAction } from "../protocol";
import { fmtTime } from "../format";
import { Panel } from "./ui";

const LEVELS: LogLevel[] = ["debug", "info", "warn", "error"];
const SOURCES: LogSource[] = ["brain", "planner", "llm", "perception", "control", "server", "user"];
const RANK: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 };

function load<T>(k: string, d: T): T {
  try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; }
}
function save(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } }

export function LogPanel({ logs }: { logs: LogEntry[] }) {
  const [minLevel, setMinLevel] = useState<LogLevel>(() => load("log.minLevel", "info"));
  const [hidden, setHidden] = useState<LogSource[]>(() => load("log.hidden", []));
  const [q, setQ] = useState("");
  const [hover, setHover] = useState(false);
  const [frozenAt, setFrozenAt] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => save("log.minLevel", minLevel), [minLevel]);
  useEffect(() => save("log.hidden", hidden), [hidden]);
  useEffect(() => { setFrozenAt(hover ? logs.length : null); }, [hover]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return logs.filter((e) => RANK[e.level] >= RANK[minLevel] && !hidden.includes(e.source) &&
      (!needle || e.message.toLowerCase().includes(needle)));
  }, [logs, minLevel, hidden, q]);

  useLayoutEffect(() => {
    if (!hover && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown, hover]);

  const pending = frozenAt != null ? logs.length - frozenAt : 0;
  const toggle = (s: LogSource) => setHidden((h) => (h.includes(s) ? h.filter((x) => x !== s) : [...h, s]));

  return (
    <Panel title="Action log" className="log" testId="log-panel" right={
      <span className="row gap-s">
        {hover ? <span className="tag warn">⏸ autoscroll paused{pending > 0 ? ` · ${pending} new` : ""}</span> : <span className="tag dim">{shown.length}/{logs.length}</span>}
      </span>
    }>
      <div className="log-f">
        <select value={minLevel} onChange={(e) => setMinLevel(e.target.value as LogLevel)} aria-label="minimum level" data-testid="log-level">
          {LEVELS.map((l) => <option key={l} value={l}>≥ {l}</option>)}
        </select>
        <div className="chips">
          {SOURCES.map((s) => (
            <button key={s} className={`chip src-${s} ${hidden.includes(s) ? "off" : ""}`} onClick={() => toggle(s)} title={`toggle ${s}`}>{s}</button>
          ))}
        </div>
        <input className="log-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter…" spellCheck={false} />
      </div>
      <div className="log-l" ref={box} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)} data-testid="log-list">
        {shown.map((e, i) => (
          <div key={`${e.ts}-${i}`} className={`le lv-${e.level}`}>
            <span className="le-t">{fmtTime(e.ts)}</span>
            <span className={`le-s src-${e.source}`}>{e.source}</span>
            <span className="le-m">
              {e.message}
              {e.action && <span className="le-a">{describeAction(e.action)}</span>}
            </span>
          </div>
        ))}
        {shown.length === 0 && <div className="empty">no entries match</div>}
      </div>
    </Panel>
  );
}
