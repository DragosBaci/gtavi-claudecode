// Shared bits for the desk pages (Home, Extract, Live, Queue).
"use client";
import { useEffect, useRef, useState } from "react";
import { api } from "./util";

/** GET `url` every `ms` (and right away). `reload()` fetches now. */
export function usePoll<T>(url: string, ms: number) {
  const [data, setData] = useState<T | null>(null), [err, setErr] = useState<string | null>(null);
  const tick = useRef<() => void>(() => {});
  useEffect(() => {
    let stop = false, h: ReturnType<typeof setTimeout> | undefined;
    const go = async () => {
      clearTimeout(h);
      try { const r = await api<T>(url); if (!stop) { setData(r); setErr(null); } } catch (e: any) { if (!stop) setErr(e.message); }
      if (!stop) h = setTimeout(go, ms);
    };
    tick.current = go; go();
    return () => { stop = true; clearTimeout(h); };
  }, [url, ms]);
  return { data, err, reload: () => tick.current() };
}

export const kfmt = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K" : String(n));
export function ago(t: number | null | undefined, now = Date.now() / 1000) {
  if (!t) return "never";
  const s = Math.max(0, now - t);
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
}
export const editUrl = (job: string, name?: string | null, pick?: number) =>
  `/edit?job=${encodeURIComponent(job)}` + (name ? `&name=${encodeURIComponent(name)}` : `&set=candidates_audit&pick=${pick ?? 1}`);
