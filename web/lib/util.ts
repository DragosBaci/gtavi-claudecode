import type { Range } from "./types";

export const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));
export const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
export const fmt = (t: number) => { t = Math.max(0, t); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`; };
export const fmt2 = (t: number) => { t = Math.max(0, t); const m = Math.floor(t / 60); return `${String(m).padStart(2, "0")}:${(t - m * 60).toFixed(2).padStart(5, "0")}`; };

export const lsGet = <T,>(k: string, d: T): T => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } };
export const lsSet = (k: string, v: unknown) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };
export const lsDel = (k: string) => { try { localStorage.removeItem(k); } catch { /* private mode */ } };

export async function api<T = any>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json();
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}

// ranges of source seconds
export function merge(list: Range[]): Range[] {
  const s = list.filter(([a, b]) => b - a > 0.02).map(([a, b]) => [+a, +b] as Range).sort((x, y) => x[0] - y[0]);
  const out: Range[] = [];
  for (const r of s) { const l = out[out.length - 1]; if (l && r[0] <= l[1] + 0.01) l[1] = Math.max(l[1], r[1]); else out.push(r); }
  return out.map(([a, b]) => [+a.toFixed(3), +b.toFixed(3)] as Range);
}
export const subtract = (list: Range[], [a, b]: Range) => merge(list.flatMap(([x, y]) => [[x, Math.min(y, a)], [Math.max(x, b), y]] as Range[]));
export const inside = (list: Range[], a: number, b: number) => list.some(([x, y]) => x <= a + 0.01 && b - 0.01 <= y);
export const cutOut = (pcs: Range[], [x, y]: Range, min = 0.05) =>
  pcs.flatMap(([p, q]) => [[p, Math.min(q, x)], [Math.max(p, y), q]] as Range[]).filter(([p, q]) => q - p > min);
