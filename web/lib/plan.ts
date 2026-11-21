// The edit list, computed the way scripts/cut.py computes it (minus its loudness check), so the
// preview, the length estimate and the timeline match the render.
import { BLEND } from "./constants";
import type { ClipState, Group, Project, Range, Unit, Word } from "./types";
import { clamp, cutOut } from "./util";

export interface Piece { a: number; b: number; kind: "hook" | "clip" }

export function keepIntervals(P: Project, S: ClipState, s0: number, e0: number): Range[] {
  const ws = P.words.filter(w => w.s >= s0 && w.s < e0);
  if (!ws.length) return [[s0, e0]];
  let a = s0, b = e0;
  if (S.trim) { a = Math.max(s0, ws[0].s - 0.12); b = Math.min(e0, ws[ws.length - 1].e + 0.35); }
  const out: Range[] = []; let cur = a;
  if (S.tighten > 0) for (let i = 0; i < ws.length - 1; i++) {
    const w = ws[i], nx = ws[i + 1], ga = w.e + 0.15, gb = nx.s - 0.1;
    if (nx.s - w.e > S.tighten && gb - ga >= 0.25 && ga > cur && gb < b) {
      let cuts: Range[] = [[ga, gb]];
      for (const k of S.keep) cuts = cutOut(cuts, k, 0.25 - 1e-9);
      for (const [x, y] of cuts) { out.push([cur, x]); cur = y; }
    }
  }
  out.push([cur, b]);
  return out.filter(([x, y]) => y - x > 0.05);
}

export function mainPieces(P: Project, S: ClipState): Range[] {
  let e = S.end;
  const hookIn = !!S.tease && !S.keep_after_tease && S.tease[0] >= S.start && S.tease[0] < S.end;
  if (hookIn) {
    e = Math.min(e, S.tease![1] + 0.8);
    const nxt = P.words.find(w => w.s > S.tease![1] + 0.1);   // never start the next sentence
    if (nxt && nxt.s < e) e = Math.max(S.tease![1] + 0.3, nxt.s - 0.05);
  }
  let pcs = keepIntervals(P, S, S.start, e);
  for (const d of S.drop) pcs = cutOut(pcs, d);
  if (hookIn && pcs.length) pcs[pcs.length - 1][1] = Math.max(pcs[pcs.length - 1][1], Math.min(e, S.tease![1] + 0.3));
  return pcs;
}

export function plan(P: Project, S: ClipState): Piece[] {
  const m = mainPieces(P, S).map(([a, b]) => ({ a, b, kind: "clip" as const }));
  return S.tease ? [{ a: S.tease[0], b: S.tease[1], kind: "hook" as const }, ...m] : m;
}
export const blendLen = (S: ClipState) => S.tease && BLEND[S.transition] ? Math.min(BLEND[S.transition], (S.tease[1] - S.tease[0]) * 0.5) : 0;
export const estLen = (P: Project, S: ClipState) =>
  plan(P, S).reduce((s, x) => s + x.b - x.a, 0) + (S.tease && S.transition === "rewind" ? 0.6 : 0) - blendLen(S);

/** Where source time t lands in the edit (null when it is cut). */
export function editPos(P: Project, S: ClipState, t: number, playing: boolean, pi: number): number | null {
  const p = plan(P, S); let acc = 0;
  for (let i = 0; i < p.length; i++) {
    const x = p[i];
    if (i === 1 && S.tease) acc += S.transition === "rewind" ? 0.6 : -blendLen(S);
    if (t >= x.a && t <= x.b && (!playing || i >= pi)) return acc + t - x.a;
    acc += x.b - x.a;
  }
  return null;
}

// The rendered file: output time <-> source time through the pieces cut.py saved (hook, rewind, clip pieces).
interface RPiece { a: number; b: number; rw: boolean; o: number; len: number; hook: boolean }
export function rPieces(P: Project): RPiece[] {
  let o = 0; const ov = +(P?.rendered?.transition?.overlap || 0);
  return (P?.rendered?.segments || []).map((sg: any[], i: number) => {
    if (i === 1) o -= ov;
    const rw = sg.length > 2, len = rw ? +sg[3] : sg[1] - sg[0];
    const r = { a: +sg[0], b: +sg[1], rw, o, len, hook: i === 0 && !!P.rendered!.tease };
    o += len; return r;
  });
}
export function srcFromOut(P: Project, t: number) {
  const ps = rPieces(P); const r = ps.find(p => t >= p.o && t < p.o + p.len) || ps[ps.length - 1];
  if (!r) return P.video.offset;
  const k = clamp((t - r.o) / r.len, 0, 1);
  return r.rw ? r.b - k * (r.b - r.a) : r.a + (t - r.o);
}
export function outFromSrc(P: Project, t: number) {
  const ps = rPieces(P).filter(p => !p.rw);
  const r = ps.find(p => !p.hook && t >= p.a && t <= p.b) || ps.find(p => t >= p.a && t <= p.b);
  if (r) return r.o + (t - r.a);
  const nx = ps.filter(p => !p.hook).find(p => p.a > t);
  return nx ? nx.o : ps.length ? ps[ps.length - 1].o + ps[ps.length - 1].len - 0.05 : 0;
}

export function buildUnits(words: Word[]): Unit[] {
  const units: Unit[] = [];
  for (let i = 0; i < words.length; i++) {
    if (i && words[i].s - words[i - 1].e > 0.8) units.push({ kind: "pause", s: words[i - 1].e, e: words[i].s, w: "•" });
    units.push({ kind: "word", s: words[i].s, e: words[i].e, w: words[i].w, wi: i });
  }
  return units;
}
/** Caption word groups like cut.py's build_ass: 2 words, break on pauses and punctuation. */
export function buildGroups(words: Word[]): Group[] {
  const groups: Group[] = []; let cur: Group = [];
  words.forEach((w, i) => {
    cur.push(w); const nx = words[i + 1];
    if (cur.length >= 2 || /[.,!?…]$/.test(w.w) || !nx || nx.s - w.e > 0.35) { groups.push(cur); cur = []; }
  });
  groups.forEach((g, i) => { const nx = groups[i + 1]; const e = g[g.length - 1].e; g.until = nx && nx[0].s - e < 0.5 ? nx[0].s : e + 0.3; });
  return groups;
}
export function editPoints(S: ClipState) {
  const pts = [S.start, S.end, ...(S.tease || []), ...S.drop.flat(), ...S.keep.flat()];
  return [...new Set(pts.map(x => +x.toFixed(2)))].sort((a, b) => a - b);
}
