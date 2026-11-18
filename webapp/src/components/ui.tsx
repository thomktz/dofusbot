import type { ReactNode } from "react";

export function Panel(props: { title: string; right?: ReactNode; className?: string; children: ReactNode; testId?: string }) {
  return (
    <section className={`panel ${props.className ?? ""}`} data-testid={props.testId}>
      <header className="panel-h">
        <h2>{props.title}</h2>
        {props.right && <div className="panel-h-right">{props.right}</div>}
      </header>
      <div className="panel-b">{props.children}</div>
    </section>
  );
}

export function KV(props: { k: string; children: ReactNode; title?: string }) {
  return (
    <div className="kv" title={props.title}>
      <span className="k">{props.k}</span>
      <span className="v">{props.children}</span>
    </div>
  );
}

export function Bar(props: { label: string; value: number | null; max: number | null; color: string; text?: string }) {
  const { value, max } = props;
  const pct = value != null && max ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="bar">
      <span className="bar-l">{props.label}</span>
      <div className="bar-t"><div className="bar-f" style={{ width: `${pct}%`, background: props.color }} /></div>
      <span className="bar-v">{props.text ?? (value == null ? "—" : max ? `${value} / ${max}` : String(value))}</span>
    </div>
  );
}

export function Pips(props: { label: string; value: number | null; max: number; color: string }) {
  const v = props.value ?? 0;
  return (
    <div className="pips">
      <span className="bar-l">{props.label}</span>
      <div className="pips-row">
        {Array.from({ length: Math.max(props.max, v) }, (_, i) => (
          <span key={i} className="pip" style={i < v ? { background: props.color, borderColor: props.color } : undefined} />
        ))}
      </div>
      <span className="bar-v">{props.value ?? "—"}</span>
    </div>
  );
}

export function Dot(props: { on: boolean | null; warn?: boolean; title?: string }) {
  const cls = props.on == null ? "dot" : props.on ? "dot on" : props.warn ? "dot warn" : "dot off";
  return <span className={cls} title={props.title} />;
}

export function Check(props: { ok: boolean | null | undefined; label: string }) {
  return <span className={`chk ${props.ok ? "ok" : props.ok === false ? "bad" : ""}`}>{props.ok ? "✓" : props.ok === false ? "✗" : "?"} {props.label}</span>;
}
