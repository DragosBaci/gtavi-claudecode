"use client";
import { openClip } from "@/lib/actions";
import { LNAME } from "@/lib/constants";
import { setEd, useEd } from "@/lib/store";
import { fmt } from "@/lib/util";
import { I } from "./Icons";

/** Top left: the clip picker and the way back to the desk. */
export default function ClipHeader() {
  const P = useEd(s => s.P), clips = useEd(s => s.clips), menu = useEd(s => s.menu);
  const open = menu === "clip";
  return (
    <>
      <div className="lhead">
        <button className="pill" id="clipBtn" onClick={e => { e.stopPropagation(); setEd({ menu: open ? null : "clip" }); }}>
          {I.plus}<span className="t">{P ? P.state.title || P.name : "Open a clip"}</span>{I.chevDown}
        </button>
        <a className="pill" href="/" title="Back to the desk: Make, Post, Live" style={{ textDecoration: "none", fontSize: 13, padding: "6px 11px" }}>
          <i style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--lime)" }} />Desk
        </a>
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
