export function fmtUptime(s: number): string {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}
export function fmtTime(ts: number): string {
  const d = new Date(ts);
  return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}
export function fmtInt(n: number | null | undefined): string {
  if (n == null) return "—";
  return Math.round(n).toLocaleString("fr-FR").replace(/ | /g, " ");
}
export const fmtCoords = (c: [number, number] | null | undefined) => (c ? `[${c[0]},${c[1]}]` : "—");
const p2 = (n: number) => String(n).padStart(2, "0");
