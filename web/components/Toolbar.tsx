"use client";
import { openCrop, openTab, resetEdits, setSetting, toggleHook } from "@/lib/actions";
import { LAYOUTS, LNAME } from "@/lib/constants";
import { setEd, useEd } from "@/lib/store";
import { I, LAYOUT_ICON } from "./Icons";

/** Top center: 9:16 · Layout menu · Track (webcam auto/manual) · Hook on/off. */
export default function Toolbar() {
  const S = useEd(s => s.S), P = useEd(s => s.P), menu = useEd(s => s.menu);
  const dirty = !!(S && P && JSON.stringify(S) !== JSON.stringify(P.state));
  const open = menu === "layout";
  return (
    <div id="toolbar">
      <span className="tb" title="Output format">{I.phone}9:16</span>
      <button className={"tb" + (open ? " open" : "")} id="layoutBtn" onClick={e => { e.stopPropagation(); setEd({ menu: open ? null : "layout" }); }}>
        {I.frame}<span>Layout: {S ? LNAME[S.layout] || S.layout : "—"}</span>
      </button>
      <button className="tb" id="trackBtn" title="Webcam: auto-detect (ON) or your own crop (OFF)" onClick={openCrop}>
        {I.track}<span>Track: {S?.cam === "auto" ? "ON" : "OFF"}</span>
      </button>
      <button className="tb" id="hookBtn" title="Play the hook first (Shift+H)" onClick={() => toggleHook()}>
        {I.bolt}<span>Hook: <span className={S?.tease ? "on" : ""}>{S?.tease ? "ON" : "OFF"}</span></span>
      </button>
      <button className="tb" id="resetBtn" disabled={!dirty} onClick={resetEdits}
        title={P?.rendered ? "Back to the last render, before your edits. ⌘Z brings them back." : "Back to the original pick, before your edits. ⌘Z brings them back."}>
        {I.undo}<span>Reset</span>
      </button>
      <div className={"menu" + (open ? " open" : "")} id="layoutMenu" style={{ left: 60 }} onClick={e => e.stopPropagation()}>
        <div className="mh" onClick={() => openTab("layout")}>Global layout settings {I.chevRight}</div>
        {LAYOUTS.map(([k, n, d]) => (
          <button key={k} className={"mi" + (S?.layout === k ? " on" : "")} onClick={() => { setEd({ menu: null }); setSetting("layout", k, false); }}>
            {LAYOUT_ICON[k]}<span>{n}<span className="d">{d}</span></span>{I.check}
          </button>
        ))}
      </div>
    </div>
  );
}
