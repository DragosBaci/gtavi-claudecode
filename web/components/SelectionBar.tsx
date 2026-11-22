"use client";
import { useLayoutEffect, useRef, useState } from "react";
import { act } from "@/lib/actions";
import { useEd } from "@/lib/store";
import { TL, X } from "@/lib/timeline";
import { clamp } from "@/lib/util";

const ACTIONS: [string, string, string][] = [["cut", "✂ Cut", "Delete"], ["hook", "⚡ Hook", "H"], ["keep", "◼ Keep", "K"]];

/** Floating toolbar over the selected words (or the selected range on the timeline). */
export default function SelectionBar() {
  const sel = useEd(s => s.sel), P = useEd(s => s.P);
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<[number, number]>([0, 0]);
  const [, bump] = useState(0);

  useLayoutEffect(() => {
    if (!sel || !P || !ref.current) return;
    const place = () => {
      const els = document.querySelectorAll("#words .u.sel"), bar = ref.current; if (!bar) return;
      const box = document.getElementById("words")!.getBoundingClientRect();
      let x: number, y: number;
      if (els.length) { const r1 = els[0].getBoundingClientRect(), r2 = els[els.length - 1].getBoundingClientRect(); x = (r1.left + r2.right) / 2; y = r1.top - 52; if (y < box.top) y = r2.bottom + 10; }
      else if (TL.cv) { const r = TL.cv.getBoundingClientRect(); x = r.left + (X(sel[0]) + X(sel[1])) / 2; y = r.top - 52; }
      else return;
      const w = bar.offsetWidth; setPos([clamp(x - w / 2, 10, innerWidth - w - 10), clamp(y, 10, innerHeight - 60)]);
    };
    place();
    const words = document.getElementById("words"); words?.addEventListener("scroll", place); window.addEventListener("resize", place);
    const id = setInterval(place, 300);
    return () => { words?.removeEventListener("scroll", place); window.removeEventListener("resize", place); clearInterval(id); };
  }, [sel, P]);

  return (
    <div id="selbar" ref={ref} className={sel ? "show" : ""} style={{ left: pos[0], top: pos[1] }}
      onMouseDown={e => e.preventDefault()} onClick={() => bump(x => x + 1)}>
      <span className="len">{sel ? `${(sel[1] - sel[0]).toFixed(1)} s` : ""}</span>
      {ACTIONS.map(([k, label, key]) => <button key={k} data-act={k} title={key || undefined} onClick={() => act(k)}>{label}</button>)}
    </div>
  );
}
