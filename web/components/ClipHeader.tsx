"use client";
import { openClip } from "@/lib/actions";
import { LNAME } from "@/lib/constants";
import { ME, renameMe } from "@/lib/presence";
import { setEd, useEd } from "@/lib/store";
import { fmt } from "@/lib/util";
import { useState } from "react";
import { I } from "./Icons";

/** Top left: the clip picker and who else is here. */
export default function ClipHeader() {
  const P = useEd(s => s.P), clips = useEd(s => s.clips), menu = useEd(s => s.menu), peers = useEd(s => s.peers);
  const [, bump] = useState(0);
  const open = menu === "clip";
  const people = peers.filter(p => !p.ghost);
  return (
    <>
      <div className="lhead">
        <button className="pill" id="clipBtn" onClick={e => { e.stopPropagation(); setEd({ menu: open ? null : "clip" }); }}>
          {I.plus}<span className="t">{P ? P.state.title || P.name : "Open a clip"}</span>{I.chevDown}
        </button>
        <div className="peers">
          {ME.name && <i className="me" title={`You (${ME.name}) · click to rename`} style={{ background: ME.color }}
            onClick={() => { const n = prompt("Your name on other people's screens", ME.name); if (n && n.trim()) { renameMe(n); bump(x => x + 1); } }}>{ME.name[0]}</i>}
          {people.map(p => <i key={p.sid} title={p.name} style={{ background: p.color }}>{p.name[0]}</i>)}
        </div>
        <div className={"menu" + (open ? " open" : "")} id="clipMenu" onClick={e => e.stopPropagation()}>
          {!clips.length && <div className="grp">No clips yet</div>}
          {clips.map(j => (
            <div key={j.job}>
              <div className="grp">{((j.channel ? j.channel + " · " : "") + (j.title || j.job)).slice(0, 70)}</div>
              {j.clips.filter(c => c.editable).map(c => (
                <button key={c.name} className="mi" onClick={() => { setEd({ menu: null }); openClip({ job: j.job, name: c.name }); }}>
                  <span>{c.title}<span className="d">{c.name} · {LNAME[c.layout] || c.layout || ""}</span></span>
                </button>
              ))}
              {j.picks.map(p => (
                <button key={`${p.set}-${p.n}`} className="mi" onClick={() => { setEd({ menu: null }); openClip({ job: j.job, set: p.set, pick: p.n }); }}>
                  <span>＋ New from pick {p.tag || p.n}<span className="d">{p.title || fmt(p.start)}</span></span>
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="src">{P ? `${P.source.channel || ""} · ${P.source.title || ""}` : ""}</div>
    </>
  );
}
