// Playback: the source video (v), the incoming clip during a blended transition (v2), the rendered file (rv),
// and one requestAnimationFrame loop that draws the preview, the timeline and the cursors.
import { BLEND, WHOOSH } from "./constants";
import { editPos, estLen, outFromSrc, plan, srcFromOut, type Piece } from "./plan";
import { drawFrame } from "./preview";
import { drawPeers } from "./presence";
import { ed, setEd } from "./store";
import { drawTimeline } from "./timeline";
import type { Project } from "./types";
import { clamp, fmt2 } from "./util";

export const E = {
  v: null as HTMLVideoElement | null,
  v2: null as HTMLVideoElement | null,
  rv: null as HTMLVideoElement | null,
  pl: [] as Piece[], pi: 0,
  rewindUntil: 0, flashUntil: 0,
  trans: null as null | { kind: string; D: number; start: number },
  AC: null as AudioContext | null,
  timeEl: null as HTMLElement | null,
  running: false,
  loadVideos(P: Project) {
    const { v, v2, rv } = E; if (!v || !v2 || !rv) return;
    v.src = P.video.url; v.load(); v2.src = P.video.url; v2.load(); v.muted = ed().muted;
    rv.src = P.rendered ? P.rendered.url : "";
    v.addEventListener("loadeddata", () => { const S = ed().S; if (S) seek(S.tease ? S.tease[0] : S.start); }, { once: true });
  },
  startPlan(p: Piece[]) {
    const P = ed().P; if (!P || !E.v) return;
    E.pl = p; E.pi = 0; seek(p[0].a);
    if (p[1] && E.v2) E.v2.currentTime = p[1].a - P.video.offset;
    setEd({ playing: true }); E.v.play().catch(() => {});
  },
};

export const inRendered = () => { const { mode, P } = ed(); return mode === "rendered" && !!P?.rendered; };
export const tNow = () => {
  const { P } = ed(); if (!P || !E.v) return 0;
  return inRendered() ? srcFromOut(P, E.rv!.currentTime) : E.v.currentTime + P.video.offset;
};
export function seek(t: number) {
  const { P } = ed(); if (!P || !E.v) return;
  if (inRendered()) { E.rv!.currentTime = clamp(outFromSrc(P, t), 0, P.rendered!.dur - 0.02); return; }
  E.v.currentTime = clamp(t - P.video.offset, 0, P.video.dur - 0.02);
}
export const vMin = () => ed().P!.video.offset;
export const vMax = () => { const P = ed().P!; return P.video.offset + P.video.dur; };

export function play() {
  const { P, S, mode, muted } = ed(); if (!P || !S || !E.v) return;
  if (mode === "rendered") {
    if (!P.rendered) return;
    if (E.rv!.currentTime >= P.rendered.dur - 0.05) E.rv!.currentTime = 0;
    E.rv!.muted = muted; setEd({ playing: true }); E.rv!.play().catch(() => {}); return;
  }
  if (mode === "edit") {
    E.pl = plan(P, S); if (!E.pl.length) return;
    const t = tNow(); E.pi = E.pl.findIndex(p => t >= p.a - 0.05 && t < p.b - 0.05);
    if (E.pi < 0) { E.pi = 0; seek(E.pl[0].a); }
    if (E.pl[E.pi].kind === "hook" && E.pl[E.pi + 1] && E.v2) E.v2.currentTime = E.pl[E.pi + 1].a - P.video.offset;
  }
  setEd({ playing: true }); E.v.play().catch(() => {});
}
export function stop() {
  E.v?.pause(); E.v2?.pause(); E.rv?.pause(); E.rewindUntil = 0; E.trans = null;
  if (E.v) E.v.volume = 1;
  if (ed().playing) setEd({ playing: false });
}
export const togglePlay = () => (ed().playing ? stop() : play());

export function whoosh(D: number) {   // filtered-noise sweep, like the whoosh cut.py mixes in
  try {
    const AC = (E.AC = E.AC || new AudioContext());
    const len = D + 0.3, n = Math.floor(AC.sampleRate * len), buf = AC.createBuffer(1, n, AC.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
    const src = AC.createBufferSource(); src.buffer = buf;
    const bp = AC.createBiquadFilter(); bp.type = "bandpass"; bp.Q.value = 0.8;
    const t0 = AC.currentTime; bp.frequency.setValueAtTime(600, t0); bp.frequency.exponentialRampToValueAtTime(3800, t0 + len * 0.6);
    const g = AC.createGain(); g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.3, t0 + len * 0.55); g.gain.exponentialRampToValueAtTime(0.0001, t0 + len);
    src.connect(bp).connect(g).connect(AC.destination); src.start();
  } catch { /* no audio */ }
}

function stepPlayback(now: number) {
  const { P, S, muted } = ed(); const v = E.v!, v2 = E.v2!;
  if (E.rewindUntil && now >= E.rewindUntil) { E.rewindUntil = 0; v.play().catch(() => {}); }
  if (E.trans) {   // blended hook -> clip transition
    const k = (now - E.trans.start) / (E.trans.D * 1000); v.volume = clamp(1 - k, 0, 1);
    if (k >= 1) { E.pi++; const nt = v2.currentTime + P!.video.offset; E.trans = null; v2.pause(); v.volume = 1; seek(nt); }
    return;
  }
  if (E.rewindUntil) return;
  const t = tNow(), p = E.pl[E.pi];
  const D = p && p.kind === "hook" && E.pl[E.pi + 1] && BLEND[S!.transition] ? Math.min(BLEND[S!.transition], (p.b - p.a) * 0.5) : 0;
  if (D && t >= p.b - D) {
    E.trans = { kind: S!.transition, D, start: now };
    v2.currentTime = E.pl[E.pi + 1].a - P!.video.offset; v2.play().catch(() => {});
    if (WHOOSH.has(S!.transition) && !muted) whoosh(D);
  } else if (p && t >= p.b - 0.015) {
    E.pi++;
    if (E.pi >= E.pl.length) { stop(); seek(E.pl[E.pl.length - 1].b - 0.02); }
    else {
      if (p.kind === "hook" && S!.transition === "rewind") { E.rewindUntil = now + 600; v.pause(); }
      if (p.kind === "hook" && S!.transition === "flash") E.flashUntil = now + 90;
      seek(E.pl[E.pi].a);
    }
  }
}

function updateTime() {
  const el = E.timeEl; const { P, S, mode, playing } = ed(); if (!el || !P || !S) return;
  let a: number, b: number;
  if (inRendered()) { a = E.rv!.currentTime; b = P.rendered!.dur; }
  else if (mode === "edit") { const p = editPos(P, S, tNow(), playing, E.pi); if (p == null) { a = tNow() - P.video.offset; b = P.video.dur; } else { a = p; b = estLen(P, S); } }
  else { a = tNow() - P.video.offset; b = P.video.dur; }
  const html = `${fmt2(a)} <span>/ ${fmt2(b)}</span>`;
  if (el.innerHTML !== html) el.innerHTML = html;
}

function currentWord() {
  const { units, curK } = ed(); const t = tNow();
  const k = units.findIndex(u => u.kind === "word" && t >= u.s && t < u.e + 0.05);
  if (k !== curK) setEd({ curK: k });
}

export function startLoop() {
  if (E.running) return; E.running = true;
  const tick = () => {
    const { P, mode, playing } = ed();
    if (P && E.v) {
      if (playing && mode === "edit") stepPlayback(performance.now());
      if (mode !== "rendered") drawFrame();
      currentWord(); drawTimeline(true); updateTime();
    }
    drawPeers();
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
