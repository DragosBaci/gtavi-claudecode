"use client";
import { useEffect, useRef, useState } from "react";
import { applyCrop, autoCrop } from "@/lib/actions";
import { H, TOP, W } from "@/lib/constants";
import { E } from "@/lib/engine";
import { setEd, useEd } from "@/lib/store";
import { clamp } from "@/lib/util";

const ASPECTS: [string, string, React.CSSProperties][] = [
  ["free", "Custom", { width: 14, height: 14, borderStyle: "dashed" }], ["panel", "Panel", { width: 18, height: 12 }],
  ["orig", "Original", { width: 16, height: 16 }], ["0.5625", "9:16", { width: 10, height: 17 }],
  ["1", "1:1", { width: 15, height: 15 }], ["1.7778", "16:9", { width: 19, height: 11 }], ["1.3333", "4:3", { width: 18, height: 13 }],
];
type Box = [number, number, number, number];

/** Webcam crop: drag/resize the box on the current source frame, or let cut.py track the face (Reset). */
export default function CropModal() {
  const crop = useEd(s => s.crop), P = useEd(s => s.P), S = useEd(s => s.S);
  const cv = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState<[number, number]>([800, 450]);
  const drag = useRef<null | { hd: string; x: number; y: number; box: Box }>(null);

  useEffect(() => {
    if (!crop || !P || !cv.current || !E.v) return;
    const maxW = Math.min(1100, innerWidth * 0.86), maxH = innerHeight * 0.62, a = P.video.w / P.video.h;
    let w = maxW, h = w / a; if (h > maxH) { h = maxH; w = h * a; }
    setSize([Math.round(w), Math.round(h)]);
    requestAnimationFrame(() => { const c = cv.current; if (c && E.v) c.getContext("2d")!.drawImage(E.v, 0, 0, c.width, c.height); });
  }, [!!crop, P]);   // eslint-disable-line react-hooks/exhaustive-deps

  const aspectVal = (k: string) => {
    if (!P || !S || k === "free") return null;
    if (k === "panel") return S.layout === "facecam" ? W / Math.round(H * 0.38) : W / TOP;
    if (k === "orig") return P.video.w / P.video.h;
    return +k;
  };
  const setBox = (box: Box, aspect = crop!.aspect) => setEd({ crop: { box, aspect } });
  const pickAspect = (k: string) => {
    const a = aspectVal(k); if (!a || !P) { setBox(crop!.box, k); return; }
    let [x, y, w, h] = crop!.box; const cx = x + w / 2, cy = y + h / 2, pxA = a * P.video.h / P.video.w;
    if (w / h > pxA) w = h * pxA; else h = w / pxA;
    if (w > 1) { w = 1; h = w / pxA; } if (h > 1) { h = 1; w = h * pxA; }
    setBox([clamp(cx - w / 2, 0, 1 - w), clamp(cy - h / 2, 0, 1 - h), w, h], k);
  };

  useEffect(() => {
    const move = (e: MouseEvent) => {
      const d = drag.current, st = useEd.getState(); if (!d || !st.crop || !st.P) return;
      const dx = (e.clientX - d.x) / size[0], dy = (e.clientY - d.y) / size[1]; let [x, y, w, h] = d.box;
      if (d.hd === "move") { x = clamp(x + dx, 0, 1 - w); y = clamp(y + dy, 0, 1 - h); }
      else {
        const a = aspectVal(st.crop.aspect), pxA = a ? a * st.P.video.h / st.P.video.w : null; let x2 = x + w, y2 = y + h;
        if (d.hd.includes("w")) x = clamp(x + dx, 0, x2 - 0.03); else x2 = clamp(x2 + dx, x + 0.03, 1);
        if (d.hd.includes("n")) y = clamp(y + dy, 0, y2 - 0.03); else y2 = clamp(y2 + dy, y + 0.03, 1);
        w = x2 - x; h = y2 - y;
        if (pxA) {
          const nh = w / pxA; if (d.hd.includes("n")) y = y2 - nh; h = nh;
          if (y < 0 || y + h > 1) { h = Math.min(h, d.hd.includes("n") ? y2 : 1 - y); y = d.hd.includes("n") ? y2 - h : y; w = h * pxA; if (d.hd.includes("w")) x = x2 - w; }
        }
      }
      setEd({ crop: { ...st.crop, box: [x, y, w, h] } });
    };
    const up = () => { drag.current = null; };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  });

  if (!crop || !P) return <div className="modal" />;
  const [x, y, w, h] = crop.box;
  const start = (hd: string) => (e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation(); drag.current = { hd, x: e.clientX, y: e.clientY, box: [...crop.box] as Box }; };
  return (
    <div className="modal open">
      <div className="sheet">
        <div className="aspects">
          {ASPECTS.map(([k, n, st]) => <button key={k} className={crop.aspect === k ? "on" : ""} onClick={() => pickAspect(k)}><i style={st} />{n}</button>)}
        </div>
        <div id="cropwrap" style={{ width: size[0], height: size[1] }}>
          <canvas ref={cv} width={size[0]} height={size[1]} />
          <div id="cropbox" style={{ left: x * size[0], top: y * size[1], width: w * size[0], height: h * size[1] }} onMouseDown={start("move")}>
            {["nw", "ne", "sw", "se"].map(c => <i key={c} className={"h " + c} onMouseDown={start(c)} />)}
          </div>
        </div>
        <div className="sheetfoot">
          <button className="btnx" style={{ fontSize: 16, padding: "11px 20px" }} onClick={() => setEd({ crop: null })}>Close</button>
          <span style={{ color: "var(--soft)", fontSize: 13 }}>{Math.round(w * P.video.w)}×{Math.round(h * P.video.h)} px{w * P.video.w < 540 ? " · small, the panel will be soft" : ""}</span>
          <div className="grow" />
          <button className="btnx" style={{ fontSize: 16, padding: "11px 20px" }} title="Track: let cut.py find the webcam" onClick={autoCrop}>Reset</button>
          <button className="btnx white" id="cropApply" style={{ fontSize: 16, padding: "11px 24px" }} onClick={applyCrop}>Apply</button>
        </div>
      </div>
    </div>
  );
}
