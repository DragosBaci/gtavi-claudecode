"use client";
import { useEd } from "@/lib/store";

/** Top left: the clip's title and the way back to the desk. The editor opens from a clip (Make, Post or Live). */
export default function ClipHeader() {
  const P = useEd(s => s.P);
  return (
    <>
      <div className="lhead">
        <span className="pill" id="clipBtn"><span className="t">{P ? P.state.title || P.name : "Loading…"}</span></span>
        <a className="pill" href="/" title="Back to the desk: Make, Post, Earn, Live" style={{ textDecoration: "none", fontSize: 13, padding: "6px 11px" }}>
          <i style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--lime)" }} />Desk
        </a>
      </div>
      <div className="src">{P ? `${P.source.channel || ""} · ${P.source.title || ""}` : ""}</div>
    </>
  );
}
