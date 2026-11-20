"use client";
import { Fragment, useEffect, useMemo, useRef } from "react";
import { clearSel, discardDraft, setSelUnits } from "@/lib/actions";
import { seek } from "@/lib/engine";
import { useEd } from "@/lib/store";
import { inside } from "@/lib/util";

/** The transcript: click a word to jump to it, drag across words to select them. */
export default function Transcript() {
  const units = useEd(s => s.units), S = useEd(s => s.S), sel = useEd(s => s.sel), curK = useEd(s => s.curK);
  const playing = useEd(s => s.playing), loading = useEd(s => s.loading), restored = useEd(s => s.restored);
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ anchor: number; moved: boolean } | null>(null);
  const anchor = useRef<number | null>(null);

  const cls = useMemo(() => {
    if (!S) return [];
    return units.map(u => {
      const mid = (u.s + u.e) / 2, c = ["u"];
      if (u.kind === "pause") c.push("pause");
      if (u.e <= S.start + 0.02 || u.s >= S.end - 0.02) c.push("out");
      if (inside(S.drop, Math.max(u.s, mid - 0.05), Math.min(u.e, mid + 0.05))) c.push("cut");
      if (u.kind === "pause" && S.keep.some(([a, b]) => a < u.e && b > u.s)) c.push("keep");
      if (S.tease && mid >= S.tease[0] && mid <= S.tease[1]) c.push("hook");
      if (sel && mid >= sel[0] && mid <= sel[1]) c.push("sel");
      return c.join(" ");
    });
  }, [units, S, sel]);

  useEffect(() => {   // keep the spoken word in view while playing
    if (!playing || curK < 0 || !box.current) return;
    const el = box.current.querySelector(`.u[data-k="${curK}"]`); if (!el) return;
    const r = el.getBoundingClientRect(), b = box.current.getBoundingClientRect();
    if (r.top < b.top + 40 || r.bottom > b.bottom - 60) box.current.scrollTop += r.top - b.top - b.height / 3;
  }, [curK, playing]);

  useEffect(() => {
    const up = () => {
      const d = drag.current; if (!d) return; drag.current = null;
      if (!d.moved) { seek(useEd.getState().units[d.anchor].s); if (useEd.getState().sel) clearSel(); }
    };
    window.addEventListener("mouseup", up); return () => window.removeEventListener("mouseup", up);
  }, []);

  const kOf = (e: React.MouseEvent) => { const el = (e.target as HTMLElement).closest(".u") as HTMLElement | null; return el ? +el.dataset.k! : null; };

  return (
    <>
      <div className={"restored" + (restored ? " show" : "")}>Edits restored <button onClick={discardDraft}>Discard</button></div>
      <div id="words" ref={box}
        onMouseDown={e => {
          const k = kOf(e); if (k == null) return; e.preventDefault();
          if (e.shiftKey && useEd.getState().sel && anchor.current != null) { setSelUnits(anchor.current, k); return; }
          anchor.current = k; drag.current = { anchor: k, moved: false };
        }}
        onMouseOver={e => {
          const d = drag.current; if (!d) return; const k = kOf(e); if (k == null) return;
          if (k !== d.anchor) d.moved = true; if (d.moved) setSelUnits(d.anchor, k);
        }}>
        {loading ? <div className="empty">{loading}</div> : !units.length ? <div className="empty">No transcript for this part.</div> :
          units.map((u, k) => (
            <Fragment key={k}>
              {u.kind === "pause" && u.e - u.s > 2.2 && <span className="para" />}
              <span className={cls[k] + (k === curK ? " cur" : "")} data-k={k} title={u.kind === "pause" ? `${(u.e - u.s).toFixed(1)} s pause` : undefined}>{u.w}</span>{" "}
            </Fragment>
          ))}
      </div>
    </>
  );
}
