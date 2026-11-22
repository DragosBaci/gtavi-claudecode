"use client";
import { useEffect, useState } from "react";

// Make (start clips, see Claude's picks), Post (the posting queue), Earn (paid campaigns), Live (clip streams as they
// happen). The editor opens from a clip, so it has no tab.
const TABS: [string, string][] = [["/", "Make"], ["/queue", "Post"], ["/earn", "Earn"], ["/live", "Live"]];

/** The desk pages' header: brand, page tabs, then the page's own controls (children). */
export default function Nav({ children }: { children?: React.ReactNode }) {
  const [path, setPath] = useState("");
  useEffect(() => setPath(location.pathname.replace(/\.html$/, "").replace(/\/$/, "") || "/"), []);
  return (
    <header className="lv-top">
      <a className="dk-brand" href="/"><i />Clip Desk</a>
      <nav className="dk-nav">
        {TABS.map(([href, name]) => <a key={href} href={href} className={path === href ? "on" : ""}>{name}</a>)}
      </nav>
      {children}
    </header>
  );
}

/** Small toast, same look as the editor's. */
export function Toast({ m }: { m: { t: string; id: number } | null }) {
  const [show, setShow] = useState(false);
  useEffect(() => { if (!m) return; setShow(true); const h = setTimeout(() => setShow(false), 2600); return () => clearTimeout(h); }, [m]);
  return <div className={"toast" + (show ? " show" : "")}>{m?.t}</div>;
}
