"use client";
import { useEffect, useRef, useState } from "react";
import { act, doUndo, loadClips, openTab, toggleHook } from "@/lib/actions";
import { E, seek, startLoop, tNow, togglePlay } from "@/lib/engine";
import { initMe, startPresence, trackMouse } from "@/lib/presence";
import { ed, setEd, useEd } from "@/lib/store";
import Bottom from "./Bottom";
import ClipHeader from "./ClipHeader";
import CropModal from "./CropModal";
import { Panel, Rail } from "./Panel";
import SelectionBar from "./SelectionBar";
import Stage from "./Stage";
import Toolbar from "./Toolbar";
import Transcript from "./Transcript";

export default function Editor() {
  const v = useRef<HTMLVideoElement>(null), v2 = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    E.v = v.current; E.v2 = v2.current;
    E.v!.addEventListener("ended", () => setEd({ playing: false }));
    initMe(); setReady(true);
    const offMouse = trackMouse(), offPresence = startPresence();
    Promise.all([document.fonts.load("900 40px Montserrat"), document.fonts.load("800 40px Montserrat")]).catch(() => {})
      .finally(() => { loadClips(true).catch(e => setEd({ loading: e.message })); startLoop(); });

    const key = (e: KeyboardEvent) => {
      const typing = /INPUT|TEXTAREA|SELECT/.test((document.activeElement as HTMLElement)?.tagName || "");
      const st = ed();
      if (st.crop) { if (e.key === "Escape") setEd({ crop: null }); return; }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !typing) { e.preventDefault(); doUndo(); return; }
      if (typing || !st.P) return;
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
      else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); act("cut"); }
      else if (e.key === "H" && e.shiftKey) toggleHook();
      else if (e.key.toLowerCase() === "h") act("hook");
      else if (e.key.toLowerCase() === "k") act("keep");
      else if (e.key === "[") act("start");
      else if (e.key === "]") act("end");
      else if (e.key === "Escape") { if (st.menu) setEd({ menu: null }); else if (st.sel) setEd({ sel: null }); else openTab(null); }
      else if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); seek(tNow() + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : 0.1)); }
    };
    const closeMenus = () => { if (ed().menu) setEd({ menu: null }); };
    document.addEventListener("keydown", key); document.addEventListener("click", closeMenus);
    return () => { offMouse(); offPresence(); document.removeEventListener("keydown", key); document.removeEventListener("click", closeMenus); };
  }, []);

  return (
    <>
      <div id="app">
        <div id="top">
          <section id="left">
            <ClipHeader />
            <Transcript />
          </section>
          <section id="center">
            <Toolbar />
            <Stage />
          </section>
          <Rail />
          <Panel />
        </div>
        <Bottom />
      </div>
      <SelectionBar />
      <CropModal />
      <div id="cursors" />
      <Toast />
      <video ref={v} playsInline preload="auto" style={{ display: "none" }} />
      <video ref={v2} playsInline preload="auto" muted style={{ display: "none" }} />
      {!ready && null}
    </>
  );
}

function Toast() {
  const t = useEd(s => s.toast); const [show, setShow] = useState(false);
  useEffect(() => { if (!t) return; setShow(true); const h = setTimeout(() => setShow(false), 1900); return () => clearTimeout(h); }, [t]);
  return <div className={"toast" + (show ? " show" : "")}>{t?.msg}</div>;
}
