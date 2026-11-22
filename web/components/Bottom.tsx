"use client";
import { useEffect, useRef } from "react";
import { act, doRender, doUndo, toast } from "@/lib/actions";
import { E, seek, stop, tNow, togglePlay, vMax, vMin } from "@/lib/engine";
import { editPoints, estLen } from "@/lib/plan";
import { ed, setEd, useEd } from "@/lib/store";
import { TL, attachTimeline, fitTimeline, setZoom } from "@/lib/timeline";
import { I } from "./Icons";

/** Bottom: timeline controls, the timeline canvas and the floating Render dock. */
export default function Bottom() {
  const playing = useEd(s => s.playing), zoom = useEd(s => s.zoom), tlHidden = useEd(s => s.tlHidden), muted = useEd(s => s.muted);
  const P = useEd(s => s.P), version = useEd(s => s.version), strip = useEd(s => s.strip), wave = useEd(s => s.wave), sel = useEd(s => s.sel);
  const cv = useRef<HTMLCanvasElement>(null), wrap = useRef<HTMLDivElement>(null), time = useRef<HTMLDivElement>(null);

  useEffect(() => {
    TL.cv = cv.current; TL.wrap = wrap.current; E.timeEl = time.current;
    const off = attachTimeline(cv.current!);
    const onResize = () => fitTimeline(); window.addEventListener("resize", onResize);
    return () => { off(); window.removeEventListener("resize", onResize); };
  }, []);
  useEffect(() => { fitTimeline(); }, [P, tlHidden]);
  useEffect(() => { requestAnimationFrame(() => import("@/lib/timeline").then(m => m.drawTimeline())); }, [version, strip, wave, sel]);

  const prev = () => { const S = ed().S; if (!S) return; const t = tNow(), p = editPoints(S).filter(x => x < t - 0.08); seek(p.length ? p[p.length - 1] : vMin()); };
  const next = () => { const S = ed().S; if (!S) return; const t = tNow(), p = editPoints(S).find(x => x > t + 0.08); seek(p ?? vMax() - 0.05); };
  const mute = () => { const m = !muted; setEd({ muted: m }); if (E.v) E.v.muted = m; if (E.rv) E.rv.muted = m; toast(m ? "Muted" : "Sound on"); };

  return (
    <div id="bottom" className={tlHidden ? "collapsed" : ""}>
      <div className="tlbar">
        <div className="tl-left">
          <button className="ib" onClick={() => setEd({ tlHidden: !tlHidden })}>{I.hide}<span>{tlHidden ? "Show timeline" : "Hide timeline"}</span></button>
          <button className="ib" title="Cut the selection (Delete)" onClick={() => act("cut")}>{I.trash}</button>
          <button className="ib" title="Mute" style={{ opacity: muted ? 0.45 : 1 }} onClick={mute}>{I.volume}</button>
          <button className="ib" title="Undo (⌘Z)" onClick={doUndo}>{I.undo}</button>
        </div>
        <div className="transport">
          <button className="ib" title="Previous edit point" onClick={prev}>{I.prev}</button>
          <button className="ib" id="playBtn" title="Play (Space)" onClick={togglePlay}>{playing ? I.pause : I.play}</button>
          <button className="ib" title="Next edit point" onClick={next}>{I.next}</button>
          <div className="time" ref={time}>00:00.00 <span>/ 00:00.00</span></div>
        </div>
        <div className="tl-right zoomer">
          <input type="range" min={1} max={12} step={0.1} value={zoom} onChange={e => setZoom(+e.target.value)} />
        </div>
      </div>
      <div id="tlwrap" ref={wrap}><canvas id="tl" ref={cv} /></div>
      <Dock />
    </div>
  );
}

function Dock() {
  const P = useEd(s => s.P), S = useEd(s => s.S), render = useEd(s => s.render), failed = useEd(s => s.lastRender);
  if (!P || !S) return <div id="dock"><div className="info">{I.add}<span>—</span></div><button id="renderBtn" disabled>Render</button></div>;
  const len = estLen(P, S), busy = !!render;
  const st = render?.status;
  return (
    <div id="dock">
      <div className="info">{I.add}<span>edits/<b>{P.name}</b>.mp4</span><span className={"len " + (len >= 12 && len <= 25 ? "ok" : "warn")}>≈ {len.toFixed(1)} s</span>
        {failed?.state === "error" && !busy && <span className="len warn" title={(failed.log || []).join("\n")}>✕ last render failed (hover for the log)</span>}</div>
      <button id="renderBtn" disabled={busy} onClick={() => { stop(); doRender(); }}>{st ? `Rendering · ${st.stage} · ${Math.round(st.elapsed)}s` : "Render"}</button>
    </div>
  );
}
