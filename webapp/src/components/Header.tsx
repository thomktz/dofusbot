import type { LinkState } from "../link";
import { useNow } from "../useNow";

export function Header({ link, server, transport }: { link: LinkState; server: string | null; transport: "offline" | "connecting" | "ready" | "error" }) {
  const now = useNow(1000);
  let pill: JSX.Element;
  if (link.mock) pill = <span className="pill mock" data-testid="mock-badge" title={link.forced ? "forced by ?mock=1" : `backend ${link.url} unreachable — retrying in background`}>MOCK</span>;
  else if (link.conn === "open") pill = <span className="pill live">● LIVE</span>;
  else if (link.conn === "connecting") pill = <span className="pill conn">● connecting…</span>;
  else pill = <span className="pill down">● offline{link.nextRetryMs ? ` · retry ${Math.max(0, Math.ceil((link.nextRetryMs - now) / 1000))}s` : ""}</span>;
  return (
    <div className="hdr">
      <div className="brand">DOFUS<span>·</span>AGENT</div>
      <div className="hdr-conn" data-testid="conn">
        {pill}
        {!link.mock && <span className={`pill ${transport === "ready" ? "live" : transport === "error" ? "down" : "conn"}`} title="Astra-style game socket control">SOCKET · {transport}</span>}
        <span className="hdr-url" title={link.url}>
          {link.mock ? (link.forced ? "in-browser mock (?mock=1)" : `mock · ${link.url} unreachable`) : `${server ?? "—"} · ${link.url}`}
        </span>
      </div>
    </div>
  );
}
