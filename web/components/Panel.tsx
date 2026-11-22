"use client";
import { useEffect, useState } from "react";
import { change, openCrop, openTab, playHook, renameClip, saveDraft, setMode, setSetting, toggleHook } from "@/lib/actions";
import { LAYOUTS } from "@/lib/constants";
import { vMax, vMin } from "@/lib/engine";
import { estLen } from "@/lib/plan";
import { setEd, useEd } from "@/lib/store";
import type { Tab } from "@/lib/types";
import { clamp, fmt } from "@/lib/util";
import { I } from "./Icons";

// The look (word captions, white title box, zoom into the clip, pause trimming, punch-in) is fixed: the editor only
// changes what differs per clip.
const RAIL: [Tab, string, React.ReactNode][] = [
  ["text", "Title", I.text], ["layout", "Layout", I.layout], ["trim", "Trim", I.scissors], ["export", "Export", I.upload],
];
const TITLES: Record<Tab, string> = { text: "Title", layout: "Layout", trim: "Trim and hook", export: "Export" };

export function Rail() {
  const tab = useEd(s => s.tab);
  return (
    <nav id="rail">
      {RAIL.map(([k, label, icon]) => <button key={k} data-tab={k} className={tab === k ? "on" : ""} onClick={() => openTab(tab === k ? null : k)}>{icon}{label}</button>)}
    </nav>
  );
}

/** A clickable option card. data-set/data-v let Claude's cursor find it. */
function Card({ k, v, on, title, desc, wide, onPick }: { k: string; v: string; on: boolean; title: string; desc: string; wide?: boolean; onPick: () => void }) {
  return <button className={"card" + (on ? " on" : "") + (wide ? " wide" : "")} data-set={k} data-v={v} onClick={onPick}><div className="t">{title}</div><div className="d">{desc}</div></button>;
}

export function Panel() {
  const tab = useEd(s => s.tab), P = useEd(s => s.P), S = useEd(s => s.S);
  const render = useEd(s => s.render), lastRender = useEd(s => s.lastRender);
  const [titleDraft, setTitleDraft] = useState("");
  useEffect(() => { if (S) setTitleDraft(S.title); }, [S?.title, tab]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!tab || !P || !S) return <aside id="panel" />;
  const len = estLen(P, S), off = P.video.offset;
  const status = render?.status || lastRender;

  const body = (() => {
    switch (tab) {
      case "text": return (<>
        <div className="field"><label>Title on the video</label>
          <textarea className="inp" id="pTitle" rows={3} maxLength={120} value={titleDraft}
            onChange={e => { setTitleDraft(e.target.value); setEd({ S: { ...S, title: e.target.value } }); saveDraft(); }} />
          <div className="sugg">{(P.titles || []).filter(t => t !== S.title).map(t => <button key={t} onClick={() => change(s => { s.title = t; }, "changed the title", true)}>{t}</button>)}</div>
        </div>
        <div className="note">4–9 words, CAPS on 1–2 key words. Name the streamer, leave out the outcome.</div>
      </>);
      case "layout": return (<>
        <div className="cards">{LAYOUTS.map(([k, n, d]) => <Card key={k} k="layout" v={k} on={S.layout === k} title={n} desc={d} onPick={() => setSetting("layout", k)} />)}</div>
        {S.layout === "react" && (
        <div className="field" style={{ marginTop: 16 }}><label>Webcam</label>
          <div className="row"><button className="btnx" onClick={openCrop}>Crop webcam</button><button className="btnx" data-set="cam" data-v="auto" disabled={S.cam === "auto"} onClick={() => setSetting("cam", "auto")}>Track ON</button></div>
          <div className="note" style={{ marginTop: 10 }}>{S.cam === "auto" ? "Track ON: cut.py finds the face at render time. Crop it yourself when it picks the wrong face (a thumbnail, the game character) or for full-body moments." : `Track OFF: your crop ${S.cam}.`}</div>
        </div>)}
        {S.layout === "track" && <div className="note" style={{ marginTop: 16 }}>Focus aims itself at render time: at the streamer's face when there's no game on screen, otherwise it follows the action.</div>}
      </>);
      case "trim": return (<>
        <div className="toggle"><input type="checkbox" id="pHookOn" checked={!!S.tease} onChange={() => toggleHook()} /><label htmlFor="pHookOn">Play a hook first</label></div>
        {S.tease ? (<>
          <div className="field"><label>Hook (source seconds)</label>
            <NumPair a={S.tease[0]} b={S.tease[1]} onChange={(a, b) => { if (b - a >= 0.4 && b - a <= 4) change(s => { s.tease = [a, b]; }, "moved the hook", true); }} />
            <div style={{ marginTop: 6, color: S.tease[1] - S.tease[0] > 2.5 ? "#ffd99a" : "var(--mute)", fontSize: 13 }}>{(S.tease[1] - S.tease[0]).toFixed(1)} s{S.tease[1] - S.tease[0] > 2.5 ? " · 1–2 s hits harder" : ""}</div>
            <button className="btnx sm" style={{ marginTop: 8 }} onClick={playHook}>▶ Play the hook</button>
          </div>
          <div className="field"><label>After the hook moment</label><div className="seg">
            <button data-set="keep_after_tease" data-v="0" className={!S.keep_after_tease ? "on" : ""} onClick={() => setSetting("keep_after_tease", false)}>End on it</button>
            <button data-set="keep_after_tease" data-v="1" className={S.keep_after_tease ? "on" : ""} onClick={() => setSetting("keep_after_tease", true)}>Keep playing</button>
          </div></div>
        </>) : <div className="note">Select the culminating 1–2 s in the transcript (the reveal, the punchline) and press ⚡ Hook. It plays first, then the transition, then the clip builds up to it.</div>}
              <div className="field" style={{ marginTop: 14 }}><label>Clip (source seconds)</label>
          <NumPair a={S.start} b={S.end} onChange={(a, b) => { if (b - a >= 1) change(s => { s.start = clamp(a, vMin(), vMax()); s.end = clamp(b, vMin(), vMax()); }, "trimmed the clip", true); }} />
        </div>
        <div className="field" style={{ marginTop: 14 }}><label>Cuts ({S.drop.length})</label><div className="list">
          {S.drop.length ? S.drop.map(([a, b], i) => <div className="li" key={i}><i style={{ background: "var(--red)" }} />{fmt(a - off)}–{fmt(b - off)} · {(b - a).toFixed(1)} s<button className="x" onClick={() => change(s => { s.drop.splice(i, 1); }, "removed a cut", true)}>✕</button></div>)
            : <div style={{ color: "var(--mute)", fontSize: 13 }}>Select words, then ✂ Cut (Delete)</div>}
        </div></div>
        <div className="field"><label>Keep silent ({S.keep.length})</label><div className="list">
          {S.keep.length ? S.keep.map(([a, b], i) => <div className="li" key={i}><i style={{ background: "var(--green)" }} />{fmt(a - off)}–{fmt(b - off)}<button className="x" onClick={() => change(s => { s.keep.splice(i, 1); }, "removed a keep", true)}>✕</button></div>)
            : <div style={{ color: "var(--mute)", fontSize: 13 }}>For a silent visual moment (a backflip, a reveal) that pause trimming would cut</div>}
        </div></div>
      </>);
      case "export": return (<>
        <div className="field"><label>File name</label>
          <NameField name={P.name} />
          <div style={{ color: "var(--mute)", fontSize: 12, marginTop: 6 }}>edits/<b>{P.name}</b>.mp4 · the same name replaces that clip in the post queue</div>
        </div>
        <div className={"note" + (len < 12 || len > 25 ? " warn" : "")}>About {len.toFixed(1)} s (loud pauses are kept at render, so it can come out a little longer). Reactions: 12–25 s.</div>
        {status && (<>
          <div className={"note" + (status.state === "error" ? " warn" : "")} style={status.state === "done" ? { color: "var(--lime)" } : undefined}>
            {status.state === "running" ? `Rendering · ${status.stage} · ${Math.round(status.elapsed)} s` : status.state === "done" ? `✓ Done in ${Math.round(status.elapsed)} s` : "✕ Failed"}
          </div>
          {!!status.log?.length && <div className="log">{status.log.join("\n")}</div>}
        </>)}
        {P.rendered && <div className="note">Last render: {P.rendered.dur.toFixed(1)} s · {new Date(P.rendered.updated * 1000).toLocaleString()} <button className="btnx sm" style={{ marginLeft: 6 }} onClick={() => setMode("rendered")}>View</button></div>}
      </>);
    }
  })();

  return (
    <aside id="panel" className="open">
      <div className="ph">{TITLES[tab]}<button title="Close" onClick={() => openTab(null)}>✕</button></div>
      <div className="pb">{body}</div>
    </aside>
  );
}

function NumPair({ a, b, onChange }: { a: number; b: number; onChange: (a: number, b: number) => void }) {
  const [x, setX] = useState(a.toFixed(2)), [y, setY] = useState(b.toFixed(2));
  useEffect(() => { setX(a.toFixed(2)); setY(b.toFixed(2)); }, [a, b]);
  const commit = () => onChange(+x, +y);
  return (
    <div className="row">
      <input className="inp" type="number" step={0.05} value={x} onChange={e => setX(e.target.value)} onBlur={commit} onKeyDown={e => e.key === "Enter" && commit()} />
      <input className="inp" type="number" step={0.05} value={y} onChange={e => setY(e.target.value)} onBlur={commit} onKeyDown={e => e.key === "Enter" && commit()} />
    </div>
  );
}
function NameField({ name }: { name: string }) {
  const [v, setV] = useState(name);
  useEffect(() => setV(name), [name]);
  const commit = () => { if (v.trim() !== name && renameClip(v.trim()) === false) setV(name); };
  return <input className="inp" type="text" value={v} onChange={e => setV(e.target.value)} onBlur={commit} onKeyDown={e => e.key === "Enter" && commit()} />;
}
