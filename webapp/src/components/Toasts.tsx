import { useEffect } from "react";
import type { Toast } from "../store";

export function Toasts({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: number) => void }) {
  useEffect(() => {
    if (!toasts.length) return;
    const t = setTimeout(() => dismiss(toasts[0].id), Math.max(500, 6000 - (Date.now() - toasts[0].ts)));
    return () => clearTimeout(t);
  }, [toasts, dismiss]);
  return (
    <div className="toasts" data-testid="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} onClick={() => dismiss(t.id)} role="alert">{t.text}</div>
      ))}
    </div>
  );
}
