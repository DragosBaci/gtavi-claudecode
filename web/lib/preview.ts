// The 9:16 live preview: cut.py's layouts, title box, word captions and hook transitions, drawn on a canvas.
import { CAP_PX, CAP_Y, FOCUS_H, FOCUS_Y, H, K, TITLE_PX, TITLE_Y, TOP, W } from "./constants";
import { E, tNow } from "./engine";
import { editPos, plan } from "./plan";
import { ed } from "./store";
import { clamp, inside } from "./util";

export const PV = { cv: null as HTMLCanvasElement | null };

const off: { A?: HTMLCanvasElement; B?: HTMLCanvasElement; S?: HTMLCanvasElement } = {};
function offscreen() {
  if (!off.A) { off.A = document.createElement("canvas"); off.B = document.createElement("canvas"); off.S = document.createElement("canvas"); off.A.width = off.B!.width = 540; off.A.height = off.B!.height = 960; }
  return off as Required<typeof off>;
}

export function rr(c: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
}
function cover(c: CanvasRenderingContext2D, img: CanvasImageSource, sx: number, sy: number, sw: number, sh: number, dx: number, dy: number, dw: number, dh: number) {
  const ra = dw / dh, rs = sw / sh;
  if (rs > ra) { const nw = sh * ra; sx += (sw - nw) / 2; sw = nw; } else { const nh = sw / ra; sy += (sh - nh) / 2; sh = nh; }
  c.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
}
export function camBox() {
  const { S, crop } = ed();
  const c = crop?.box || (S && S.cam !== "auto" ? S.cam.split(":").map(Number) : null);
  return c && c.length === 4 ? { x: c[0], y: c[1], w: c[2], h: c[3] } : null;
}

/** cut.py --title-secs: the title shows for the first N seconds of the final clip (0 = all of it). */
function titleOn() {
  const { P, S, playing } = ed(); if (!P || !S || !S.title_secs) return true;
  const out = editPos(P, S, tNow(), playing, E.pi);
  return out == null || out < S.title_secs;
}

function drawScene(c: CanvasRenderingContext2D, src: HTMLVideoElement, t: number) {
  const { P, S } = ed(); if (!P || !S) return;
  const sw = P.video.w, sh = P.video.h, L = S.layout;
  c.fillStyle = "#000"; c.fillRect(0, 0, W, H);
  if (E.rewindUntil) c.filter = "saturate(0.6) contrast(1.08)";
  if (L === "react" || L === "facecam") {
    const ph = L === "react" ? TOP : Math.round(H * 0.38), gh = H - ph, cb = camBox();
    if (cb) cover(c, src, cb.x * sw, cb.y * sh, cb.w * sw, cb.h * sh, 0, 0, W, ph); else cover(c, src, 0, 0, sw, sh, 0, 0, W, ph);
    if (L === "react") { const gw = sw * gh / sh; const gx = P.game_x != null ? P.game_x : (gw - W) / 2; c.drawImage(src, gx * sh / gh, 0, W * sh / gh, sh, 0, ph, W, gh); }
    else cover(c, src, 0, 0, sw, sh, 0, ph, W, gh);
    c.filter = "none";
    if (!cb) { c.fillStyle = "rgba(0,0,0,.55)"; c.fillRect(0, ph - 64, W, 64); c.fillStyle = "#fff"; c.font = "600 30px -apple-system,sans-serif"; c.textAlign = "center"; c.fillText("Track ON: the webcam is found at render", W / 2, ph - 22); }
    if (S.title && L === "react" && titleOn()) drawTitle(c, S.title, TOP, S.title_style);
  } else if (L === "fill") {
    cover(c, src, 0, 0, sw, sh, 0, 0, W, H);
  } else if (L === "track") {
    // Focus: blurred frame behind, a 4:5 window in front. After a render, the window the render used (a moving
    // window is shown at its middle position); before, the centre (the render aims it at the face or the action).
    c.filter = (E.rewindUntil ? "saturate(0.6) " : "") + "blur(22px) brightness(0.85)"; cover(c, src, 0, 0, sw, sh, -40, -40, W + 80, H + 80); c.filter = E.rewindUntil ? "saturate(0.6)" : "none";
    const fo = P.focus && typeof P.focus.w === "number" && P.focus.w <= sw && P.focus.h <= sh ? P.focus : null;
    const ch = fo ? fo.h : sh, cw = fo ? fo.w : Math.min(sw, sh * W / FOCUS_H);
    const xs = fo?.path?.map(p => p[1]).sort((a, b) => a - b);
    const cx = fo && typeof fo.x === "number" ? fo.x : xs?.length ? xs[xs.length >> 1] : (sw - cw) / 2;
    const cy = fo && typeof fo.y === "number" ? fo.y : (sh - ch) / 2;
    c.drawImage(src, cx, cy, cw, ch, 0, FOCUS_Y, W, FOCUS_H);
  } else {
    c.filter = (E.rewindUntil ? "saturate(0.6) " : "") + "blur(22px) brightness(0.85)"; cover(c, src, 0, 0, sw, sh, -40, -40, W + 80, H + 80); c.filter = E.rewindUntil ? "saturate(0.6)" : "none";
    const fh = W * sh / sw; c.drawImage(src, 0, 0, sw, sh, 0, (H - fh) / 2 - 80, W, fh);
  }
  c.filter = "none";
  if (S.title && TITLE_Y[L] != null && titleOn()) drawTitle(c, S.title, TITLE_Y[L], S.title_style);   // Gameplay, Fill, Fit
  if (!E.rewindUntil && S.captions !== "none") drawCaption(c, t, L);
}

function overlays(c: CanvasRenderingContext2D, now: number) {
  const { P, S, mode, playing } = ed(); if (!P || !S) return;
  if (E.rewindUntil) { c.fillStyle = "rgba(0,0,0,.25)"; c.fillRect(0, 0, W, H); c.fillStyle = "#fff"; c.font = "900 120px Montserrat"; c.textAlign = "center"; c.fillText("◀◀", W / 2, H / 2 + 40); }
  if (now < E.flashUntil) { c.fillStyle = "rgba(255,255,255,.7)"; c.fillRect(0, 0, W, H); }
  const t = tNow();
  if (mode === "edit" && !playing && !plan(P, S).some(p => t >= p.a && t <= p.b)) {
    c.fillStyle = "rgba(0,0,0,.45)"; c.fillRect(0, 0, W, H); c.fillStyle = "#ddd"; c.font = "700 40px -apple-system,sans-serif"; c.textAlign = "center"; c.fillText("not in the edit", W / 2, 120);
  }
}

export function drawFrame() {
  const cv = PV.cv, v = E.v; if (!cv || !v) return;
  const c = cv.getContext("2d")!;
  if (v.readyState < 2) { c.fillStyle = "#000"; c.fillRect(0, 0, cv.width, cv.height); return; }
  const now = performance.now();
  if (E.trans) return drawTransition(c, now);
  c.save(); c.scale(K, K); drawScene(c, v, tNow()); overlays(c, now); c.restore();
}

function sceneTo(canvas: HTMLCanvasElement, src: HTMLVideoElement, t: number) {
  const c = canvas.getContext("2d")!; c.save(); c.scale(K, K); drawScene(c, src, t); c.restore();
}
function drawTransition(c: CanvasRenderingContext2D, now: number) {   // canvas versions of the xfade transitions cut.py renders
  const { P } = ed(); const tr = E.trans!, o = offscreen(), cv = PV.cv!;
  const p = clamp((now - tr.start) / (tr.D * 1000), 0, 1), e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
  sceneTo(o.A, E.v!, tNow());
  sceneTo(o.B, E.v2!.readyState >= 2 ? E.v2! : E.v!, E.v2!.currentTime + P!.video.offset);
  const w = cv.width, h = cv.height, mid = 1 - Math.abs(2 * p - 1);
  c.save(); c.fillStyle = "#000"; c.fillRect(0, 0, w, h);
  switch (tr.kind) {
    case "fade": c.drawImage(p < 0.5 ? o.A : o.B, 0, 0); c.fillStyle = `rgba(0,0,0,${mid})`; c.fillRect(0, 0, w, h); break;
    case "dissolve": c.drawImage(o.A, 0, 0); c.globalAlpha = e; c.drawImage(o.B, 0, 0); break;
    case "zoom": { c.drawImage(o.B, 0, 0); c.globalAlpha = 1 - e; const z = 1 + e * 1.2; c.translate(w / 2, h / 2); c.scale(z, z); c.drawImage(o.A, -w / 2, -h / 2); break; }
    case "slide": c.drawImage(o.A, 0, -e * h); c.drawImage(o.B, 0, (1 - e) * h); break;
    case "whip": c.filter = `blur(${mid * 16}px)`; c.drawImage(o.A, -e * w * 0.25, 0); c.globalAlpha = e; c.drawImage(o.B, (1 - e) * w * 0.25, 0); break;
    case "pixel": {
      const n = Math.max(1, Math.round(1 + mid * 36)); o.S.width = Math.ceil(w / n); o.S.height = Math.ceil(h / n);
      const sc = o.S.getContext("2d")!; sc.imageSmoothingEnabled = false; sc.drawImage(p < 0.5 ? o.A : o.B, 0, 0, o.S.width, o.S.height);
      c.imageSmoothingEnabled = false; c.drawImage(o.S, 0, 0, o.S.width, o.S.height, 0, 0, w, h); break;
    }
    case "circle": c.drawImage(o.A, 0, 0); c.beginPath(); c.arc(w / 2, h / 2, e * Math.hypot(w, h) / 2, 0, Math.PI * 2); c.clip(); c.drawImage(o.B, 0, 0); break;
    default: c.drawImage(o.B, 0, 0);
  }
  c.restore();
}

function wrap2(c: CanvasRenderingContext2D, text: string, font: string, maxW: number) {
  c.font = font; const words = text.split(/\s+/); if (c.measureText(text).width <= maxW) return [text];
  let best: { m: number; l: string[] } | null = null;   // balanced 2-line split, like cut.py's wrap_px
  for (let i = 1; i < words.length; i++) {
    const a = words.slice(0, i).join(" "), b = words.slice(i).join(" ");
    const m = Math.max(c.measureText(a).width, c.measureText(b).width);
    if (!best || m < best.m) best = { m, l: [a, b] };
  }
  return best ? best.l : [text];
}
function drawTitle(c: CanvasRenderingContext2D, text: string, seam: number, style: string) {
  const font = `800 ${TITLE_PX}px Montserrat`, lines = wrap2(c, text, font, W - 2 * 120), lh = TITLE_PX * 1.12;
  c.font = font; c.textAlign = "center"; c.textBaseline = "middle";
  const tw = Math.max(...lines.map(l => c.measureText(l).width)), bh = lines.length * lh + 34, bw = tw + 2 * 46;
  const y0 = style === "outline" ? seam + 40 : seam - bh / 2;
  if (style !== "outline") { c.fillStyle = "#fff"; rr(c, (W - bw) / 2, y0, bw, bh, 14); c.fill(); }
  lines.forEach((l, i) => {
    const y = y0 + 17 + lh * (i + 0.5);
    if (style === "outline") { c.lineWidth = 12; c.strokeStyle = "#000"; c.lineJoin = "round"; c.strokeText(l, W / 2, y); c.fillStyle = "#fff"; } else c.fillStyle = "#000";
    c.fillText(l, W / 2, y);
  });
  c.textBaseline = "alphabetic";
}
function drawCaption(c: CanvasRenderingContext2D, t: number, L: string) {
  const { S, groups, mode } = ed(); if (!S) return;
  if (S.tease && t > S.tease[1] - 0.02 && t < S.tease[1] + 0.05 && mode === "edit") return;
  const g = groups.find(g => t >= g[0].s && t < g.until!); if (!g) return;
  if (mode === "edit" && inside(S.drop, t - 0.01, t + 0.01)) return;
  const y = CAP_Y[L] || 1500;
  if (S.captions === "bubble") {
    const txt = g.map(w => w.w).join(" "); c.font = "800 64px Montserrat"; const tw = c.measureText(txt).width;
    c.fillStyle = "#fff"; rr(c, (W - tw) / 2 - 36, y - 70, tw + 72, 100, 40); c.fill(); c.fillStyle = "#111"; c.textAlign = "center"; c.fillText(txt, W / 2, y); return;
  }
  c.font = `900 ${CAP_PX}px Montserrat`; c.textAlign = "left";
  const parts = g.map(w => w.w.toUpperCase().replace(/^(F|SH|B|N)(\w*?)(UCK|IT|ITCH|IGGA)/, m => m[0] + "*".repeat(m.length - 1)));
  const sp = c.measureText(" ").width, widths = parts.map(p => c.measureText(p).width), total = widths.reduce((a, b) => a + b, 0) + sp * (parts.length - 1);
  let x = (W - total) / 2;
  parts.forEach((p, i) => {
    const w = g[i], on = t >= w.s && t < (g[i + 1] ? g[i + 1].s : g.until!);
    c.lineJoin = "round"; c.lineWidth = 20; c.strokeStyle = "#000"; c.shadowColor = "rgba(0,0,0,.6)"; c.shadowOffsetY = 6; c.shadowBlur = 0;
    c.strokeText(p, x, y); c.shadowColor = "transparent";
    c.fillStyle = on ? "#FFE500" : "#fff"; c.fillText(p, x, y);
    x += widths[i] + sp;
  });
}
