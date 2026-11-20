"use client";
import { useEffect, useRef } from "react";
import { setMode } from "@/lib/actions";
import { E, togglePlay } from "@/lib/engine";
import { PV } from "@/lib/preview";
import { useEd } from "@/lib/store";
import type { Mode } from "@/lib/types";

const MODES: [Mode, string, string][] = [["edit", "Edit preview", "Hook, transition, then the clip with your cuts"], ["free", "Source", "The source, straight through"], ["rendered", "Rendered", "The last rendered file"]];

/** The 9:16 preview: a live canvas (Edit preview, Source) or the rendered file, both driven by the editor. */
export default function Stage() {
  const mode = useEd(s => s.mode), P = useEd(s => s.P);
  const cv = useRef<HTMLCanvasElement>(null), rv = useRef<HTMLVideoElement>(null);
  useEffect(() => { PV.cv = cv.current; E.rv = rv.current; }, []);
  useEffect(() => {
    const r = rv.current; if (!r) return;
    const want = P?.rendered ? P.rendered.url : "";
    if (r.getAttribute("src") !== want) { if (want) r.src = want; else r.removeAttribute("src"); }
  }, [P]);
  useEffect(() => {
    const r = rv.current; if (!r) return;
    const end = () => useEd.setState({ playing: false });
    const pause = () => { if (useEd.getState().mode === "rendered") useEd.setState({ playing: false }); };
    r.addEventListener("ended", end); r.addEventListener("pause", pause);
    return () => { r.removeEventListener("ended", end); r.removeEventListener("pause", pause); };
  }, []);
  const rendered = mode === "rendered";
  return (
    <>
      <div id="stage" onClick={togglePlay}>
        <canvas ref={cv} width={540} height={960} style={{ display: rendered ? "none" : "block" }} />
        <video ref={rv} playsInline preload="auto" style={{ display: rendered ? "block" : "none" }} />
      </div>
      <div className="modes" id="modes">
        {MODES.map(([m, label, title]) => <button key={m} className={mode === m ? "on" : ""} title={title} onClick={() => setMode(m)}>{label}</button>)}
      </div>
    </>
  );
}
