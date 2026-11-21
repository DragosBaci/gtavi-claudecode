// Timeline canvas (source time): ruler, labeled bars (clip, hook, cuts, keeps), filmstrip, waveform, playhead.
// Drag the clip edges or the hook, click to seek, shift-drag (or drag on the waveform) to select a range.
import { LNAME } from "./constants";
import { clearSel, pushUndo, sync, saveDraft, patch } from "./actions";
import { E, seek, tNow, vMax, vMin } from "./engine";
import { mainPieces } from "./plan";
import { rr } from "./preview";
import { ed, setEd } from "./store";
import { clamp, cutOut, inside } from "./util";

export const TL = { cv: null as HTMLCanvasElement | null, wrap: null as HTMLDivElement | null, pps: 10, last: 0, img: null as HTMLImageElement | null, imgUrl: "" };
export const PAD = 4, RH = 30, LY = 36, LH = 24, SY = 66, SH = 76, WY = SY + SH + 10, WH = 36, TLH = WY + WH + 4;
export const X = (t: number) => PAD + (t - ed().P!.video.offset) * TL.pps;
export const T = (x: number) => (x - PAD) / TL.pps + ed().P!.video.offset;
let drag: null | { h: string; t0: number; tease?: [number, number] | null } = null;
export const isDragging = () => !!drag;

export function fitTimeline() {
  const { P, zoom } = ed(); const cv = TL.cv, wrap = TL.wrap; if (!P || !cv || !wrap) return;
  const dpr = devicePixelRatio || 1, cw = wrap.clientWidth - 44;
  TL.pps = (cw - 2 * PAD) / P.video.dur * zoom;
  const w = Math.max(cw, Math.ceil(P.video.dur * TL.pps + 2 * PAD));
  cv.width = w * dpr; cv.height = TLH * dpr; cv.style.width = w + "px"; cv.style.height = TLH + "px";
  cv.getContext("2d")!.setTransform(dpr, 0, 0, dpr, 0, 0); drawTimeline();
}
export function setZoom(z: number) {
  const wrap = TL.wrap; const P = ed().P;
  const c = wrap ? wrap.scrollLeft + wrap.clientWidth / 2 : 0, t = P ? T(c) : 0;
  setEd({ zoom: clamp(z, 1, 12) }); fitTimeline();
  if (P && wrap) wrap.scrollLeft = X(t) - wrap.clientWidth / 2;
}

function bar(c: CanvasRenderingContext2D, a: number, b: number, y: number, h: number, fill: string, label?: string, ink?: string) {
  const x0 = X(a), x1 = X(b); if (x1 - x0 < 1) return;
  c.fillStyle = fill; rr(c, x0 + 1, y, x1 - x0 - 2, h, 7); c.fill();
  if (label && x1 - x0 > 40) { c.save(); c.beginPath(); c.rect(x0, y, x1 - x0 - 6, h); c.clip(); c.fillStyle = ink || "#d9ccc8"; c.font = "500 13px -apple-system,sans-serif"; c.textAlign = "left"; c.fillText(label, x0 + 12, y + h / 2 + 4.5); c.restore(); }
}

export function drawTimeline(throttle = false) {
  const { P, S, strip, wave, sel, playing } = ed(); const cv = TL.cv; if (!P || !S || !cv) return;
  const now = performance.now(); if (throttle && !playing && !drag && now - TL.last < 120) return; TL.last = now;
  if (strip && strip.url !== TL.imgUrl) { TL.imgUrl = strip.url; const im = new Image(); im.onload = () => { TL.img = im; drawTimeline(); }; im.src = strip.url; }
  const c = cv.getContext("2d")!, w = cv.width / (devicePixelRatio || 1), full = P.video.dur * TL.pps;
  c.clearRect(0, 0, w, TLH);
  // ruler: numbers with dots between
  const step = [1, 2, 5, 10, 15, 30, 60].find(s => s * TL.pps >= 90) || 60;
  c.font = "15px -apple-system,sans-serif"; c.textAlign = "center";
  for (let s = 0; s <= P.video.dur + 0.01; s += step) {
    const x = PAD + s * TL.pps; c.fillStyle = "#b7aaa5"; c.fillText(String(Math.round(s)), x, 20);
    c.fillStyle = "rgba(255,255,255,.22)";
    for (let k = 1; k < 5; k++) { const xx = x + k * step * TL.pps / 5; if (xx < PAD + full) { c.beginPath(); c.arc(xx, 15, 1.6, 0, Math.PI * 2); c.fill(); } }
  }
  c.fillStyle = "rgba(255,255,255,.06)"; c.fillRect(0, RH, w, 1);
  // label row
  bar(c, S.start, S.end, LY, LH, "rgba(255,255,255,.07)", LNAME[S.layout] || "Clip");
  for (const [a, b] of S.drop) bar(c, a, b, LY, LH, "rgba(255,107,107,.22)", "Cut", "#ffb4b4");
  for (const [a, b] of S.keep) bar(c, a, b, LY, LH, "rgba(110,231,183,.18)", "Keep", "#b8f5dc");
  if (S.tease) bar(c, S.tease[0], S.tease[1], LY, LH, "rgba(201,242,110,.9)", "Hook · " + (S.tease[1] - S.tease[0]).toFixed(1) + "s", "#1d2408");
  // filmstrip
  c.save(); rr(c, PAD, SY, full, SH, 12); c.clip();
  c.fillStyle = "#2a201e"; c.fillRect(PAD, SY, full, SH);
  if (TL.img && strip) {
    const tw = SH * 16 / 9, n = strip.n;
    for (let x = PAD; x < PAD + full; x += tw) {
      const k = clamp(Math.floor(((x - PAD + tw / 2) / TL.pps) / P.video.dur * n), 0, n - 1);
      c.drawImage(TL.img, k * strip.tile_w, 0, strip.tile_w, strip.tile_h, x, SY, Math.min(tw, PAD + full - x), SH);
    }
  }
  c.fillStyle = "rgba(22,15,14,.66)"; c.fillRect(PAD, SY, X(S.start) - PAD, SH); c.fillRect(X(S.end), SY, PAD + full - X(S.end), SH);
  const mp = mainPieces(P, S), gaps: [number, number][] = [];   // pauses the render trims
  for (let i = 0; i < mp.length - 1; i++) gaps.push([mp[i][1], mp[i + 1][0]]);
  if (mp.length) { gaps.push([S.start, mp[0][0]]); gaps.push([mp[mp.length - 1][1], S.end]); }
  for (const g of gaps) { let gs = [g]; for (const d of S.drop) gs = cutOut(gs, d); for (const [x, y] of gs) { c.fillStyle = "rgba(22,15,14,.5)"; c.fillRect(X(x), SY, X(y) - X(x), SH); } }
  for (const [a, b] of S.drop) {
    c.fillStyle = "rgba(40,10,10,.55)"; c.fillRect(X(a), SY, X(b) - X(a), SH);
    c.save(); c.beginPath(); c.rect(X(a), SY, X(b) - X(a), SH); c.clip(); c.strokeStyle = "rgba(255,107,107,.75)"; c.lineWidth = 2;
    for (let x = X(a) - SH; x < X(b); x += 11) { c.beginPath(); c.moveTo(x, SY + SH); c.lineTo(x + SH, SY); c.stroke(); }
    c.restore();
  }
  if (S.tease) { c.fillStyle = "rgba(201,242,110,.16)"; c.fillRect(X(S.tease[0]), SY, X(S.tease[1]) - X(S.tease[0]), SH); }
  c.restore();
  // clip window
  c.strokeStyle = "#fff"; c.lineWidth = 2.5; rr(c, X(S.start), SY - 1, X(S.end) - X(S.start), SH + 2, 12); c.stroke();
  for (const [t, dx] of [[S.start, 9], [S.end, -9]]) { c.fillStyle = "rgba(255,255,255,.85)"; c.fillRect(X(t) + dx - 1.5, SY + SH / 2 - 10, 3, 20); }
  // waveform
  if (wave) {
    const pk = wave.peaks, hop = wave.hop, mid = WY + WH / 2;
    for (let x = PAD; x < PAD + full; x += 3) {
      const t0 = (x - PAD) / TL.pps, i0 = Math.floor(t0 / hop), i1 = Math.max(i0 + 1, Math.floor((t0 + 3 / TL.pps) / hop));
      let m = 0; for (let i = i0; i < i1 && i < pk.length; i++) m = Math.max(m, pk[i]);
      const t = T(x), inClip = t >= S.start && t <= S.end, isCut = inside(S.drop, t, t);
      c.fillStyle = isCut ? "rgba(255,107,107,.35)" : inClip ? "rgba(236,200,190,.45)" : "rgba(236,200,190,.14)";
      const hh = Math.max(1.5, m * WH * 0.95); c.fillRect(x, mid - hh / 2, 2, hh);
    }
  }
  // last render pieces
  (P.rendered?.segments || []).forEach((sg: any[], i: number) => {
    if (sg.length !== 2) return;
    c.fillStyle = i === 0 && P.rendered!.tease ? "rgba(201,242,110,.9)" : "rgba(124,196,255,.85)";
    c.fillRect(X(sg[0]), SY + SH + 4, Math.max(1.5, X(sg[1]) - X(sg[0])), 3);
  });
  if (sel) { c.fillStyle = "rgba(201,242,110,.16)"; c.fillRect(X(sel[0]), LY, X(sel[1]) - X(sel[0]), TLH - LY); c.strokeStyle = "rgba(201,242,110,.8)"; c.lineWidth = 1; c.strokeRect(X(sel[0]) + 0.5, LY + 0.5, X(sel[1]) - X(sel[0]) - 1, TLH - LY - 1); }
  // playhead
  const px = X(tNow());
  c.fillStyle = "#fff"; c.fillRect(px - 0.75, 20, 1.5, TLH - 20);
  c.fillStyle = "#1e1716"; rr(c, px - 8, 2, 16, 30, 7); c.fill(); c.strokeStyle = "#fff"; c.lineWidth = 2; rr(c, px - 8, 2, 16, 30, 7); c.stroke();
  const wrap = TL.wrap;
  if (playing && wrap) { const L = wrap.scrollLeft, Wv = wrap.clientWidth; if (px < L + 30 || px > L + Wv - 80) wrap.scrollLeft = px - Wv / 3; }
}

function hit(x: number, y: number) {
  const S = ed().S!; const near = (t: number) => Math.abs(X(t) - x) <= 8;
  if (S.tease && y >= LY && y <= LY + LH) { if (near(S.tease[0])) return "tease0"; if (near(S.tease[1])) return "tease1"; if (x > X(S.tease[0]) && x < X(S.tease[1])) return "teaseMove"; }
  if (y >= SY - 4 && y <= SY + SH + 4) { if (near(S.start)) return "start"; if (near(S.end)) return "end"; }
  return null;
}
const EDGES = ["start", "end", "tease0", "tease1", "teaseMove"];

/** Mouse handling for the timeline canvas. Returns a cleanup function. */
export function attachTimeline(cv: HTMLCanvasElement) {
  const local = (e: MouseEvent) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const hover = (e: MouseEvent) => {
    if (drag || !ed().P) return; const [x, y] = local(e); const h = hit(x, y);
    cv.style.cursor = h === "teaseMove" ? "grab" : h ? "ew-resize" : e.shiftKey || y > WY - 4 ? "crosshair" : "pointer";
  };
  const down = (e: MouseEvent) => {
    const { P, S } = ed(); if (!P || !S) return; const [x, y] = local(e), t = clamp(T(x), vMin(), vMax()); const h = hit(x, y);
    if (h) { pushUndo(); drag = { h, t0: t, tease: S.tease && [...S.tease] as [number, number] }; }
    else if (e.shiftKey || y > WY - 4) { drag = { h: "sel", t0: t }; setEd({ sel: [t, t] }); }
    else { drag = { h: "scrub", t0: t }; seek(t); if (ed().sel) clearSel(); }
    e.preventDefault();
  };
  const move = (e: MouseEvent) => {
    if (!drag || !ed().P) return; const [x] = local(e); const t = clamp(T(x), vMin(), vMax()); const d = drag;
    if (d.h === "scrub") seek(t);
    if (d.h === "sel") setEd({ sel: [Math.min(d.t0, t), Math.max(d.t0, t)] });
    if (d.h === "start") patch(S => { S.start = +clamp(t, vMin(), S.end - 1).toFixed(3); });
    if (d.h === "end") patch(S => { S.end = +clamp(t, S.start + 1, vMax()).toFixed(3); });
    if (d.h === "tease0") patch(S => { S.tease = [+clamp(t, S.tease![1] - 4, S.tease![1] - 0.4).toFixed(3), S.tease![1]]; });
    if (d.h === "tease1") patch(S => { S.tease = [S.tease![0], +clamp(t, S.tease![0] + 0.4, S.tease![0] + 4).toFixed(3)]; });
    if (d.h === "teaseMove") patch(S => { const len = d.tease![1] - d.tease![0]; const a = clamp(d.tease![0] + t - d.t0, vMin(), vMax() - len); S.tease = [+a.toFixed(3), +(a + len).toFixed(3)]; });
    if (EDGES.includes(d.h)) seek(d.h === "end" || d.h === "tease1" ? t - 0.05 : t);
    drawTimeline();
  };
  const up = () => {
    if (!drag) return; const d = drag; drag = null;
    if (d.h === "sel") { const s = ed().sel; if (s && s[1] - s[0] < 0.08) clearSel(); }
    if (EDGES.includes(d.h)) { saveDraft(); sync.dirty = d.h.startsWith("tease") ? "moved the hook" : "trimmed the clip"; }
    drawTimeline();
  };
  const wheel = (e: WheelEvent) => { if (!e.ctrlKey && !e.metaKey) return; e.preventDefault(); setZoom(ed().zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)); };
  cv.addEventListener("mousemove", hover); cv.addEventListener("mousedown", down);
  window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  TL.wrap?.addEventListener("wheel", wheel, { passive: false });
  return () => {
    cv.removeEventListener("mousemove", hover); cv.removeEventListener("mousedown", down);
    window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
    TL.wrap?.removeEventListener("wheel", wheel);
  };
}
