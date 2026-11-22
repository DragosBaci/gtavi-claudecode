// Everything that changes the clip: undoable and saved as a browser draft.
import { LNAME } from "./constants";
import { E, play, seek, stop, tNow, vMax, vMin } from "./engine";
import { buildGroups, buildUnits, plan } from "./plan";
import { fitTimeline } from "./timeline";
import { ed, setEd } from "./store";
import type { ClipState, Mode, Project, Range, Tab } from "./types";
import { api, clamp, lsDel, lsGet, lsSet, merge, subtract } from "./util";

let toastId = 0;
export function toast(msg: string) { setEd({ toast: { msg, id: ++toastId } }); }

export const draftKey = () => { const P = ed().P; return P ? `clipeditor:${P.job}:${P.name}` : ""; };
export function saveDraft() { const { P, S } = ed(); if (P) lsSet(draftKey(), { S, at: Date.now(), base: P.rendered?.updated || 0 }); }
export function clearDraft() { lsDel(draftKey()); }


export function change(fn: (S: ClipState) => void, label?: string, quiet = false) {
  const { S, undo } = ed(); if (!S) return;
  const next: ClipState = JSON.parse(JSON.stringify(S));
  fn(next);
  setEd({ S: next, undo: [...undo.slice(-99), JSON.stringify(S)], version: ed().version + 1 });
  saveDraft();
  if (label && !quiet) toast(label);
}
/** Live updates while dragging (no undo entry until the drag ends). */
export function patch(fn: (S: ClipState) => void) {
  const S = ed().S; if (!S) return;
  const next = { ...S }; fn(next);
  setEd({ S: next, version: ed().version + 1 });
}
export function pushUndo() { const { S, undo } = ed(); setEd({ undo: [...undo.slice(-99), JSON.stringify(S)] }); }
export function doUndo() {
  const { undo } = ed(); const u = undo[undo.length - 1]; if (!u) return;
  setEd({ S: JSON.parse(u), undo: undo.slice(0, -1), version: ed().version + 1 }); saveDraft(); toast("Undone");
}

// ---------------------------------------------------------------- loading
export async function loadClips(openFirst: boolean) {
  const clips = await api("/api/clips"); setEd({ clips });
  if (!openFirst) return;
  const q = new URLSearchParams(location.search);
  let x: any = q.get("job") ? (q.get("name") ? { job: q.get("job"), name: q.get("name") } : { job: q.get("job"), set: q.get("set") || "candidates_audit", pick: q.get("pick") || "1" }) : null;
  if (!x) { const j = clips.find((j: any) => j.clips.some((c: any) => c.editable)); if (j) x = { job: j.job, name: j.clips.find((c: any) => c.editable).name }; }
  if (x) await openClip(x); else setEd({ loading: "No clips yet: cut one with cut.py first." });
}

export async function openClip(x: Record<string, any>) {
  stop(); setEd({ loading: "Loading…" });
  const q = new URLSearchParams(Object.entries(x).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)])).toString();
  let P: Project;
  try { P = await api("/api/project?" + q); } catch (e: any) { setEd({ loading: e.message }); return; }
  history.replaceState(null, "", "?" + new URLSearchParams({ job: P.job, name: P.name }));
  let S: ClipState = JSON.parse(JSON.stringify(P.state)), restored = false;
  const d: any = lsGet(`clipeditor:${P.job}:${P.name}`, null);
  if (d && d.base === (P.rendered?.updated || 0) && JSON.stringify(d.S) !== JSON.stringify(S)) { S = d.S; restored = true; }
  if (!restored) S = JSON.parse(JSON.stringify(P.state));
  setEd({
    P, S, restored, units: buildUnits(P.words), groups: buildGroups(P.words), sel: null, undo: [], loading: null,
    strip: null, wave: null, curK: -1, version: ed().version + 1,
    mode: !P.rendered && ed().mode === "rendered" ? "edit" : ed().mode,
  });
  E.loadVideos(P);
  const vq = `job=${encodeURIComponent(P.job)}&video=${encodeURIComponent(P.video.url)}`;
  api("/api/strip?" + vq).then(strip => setEd({ strip })).catch(() => {});
  api("/api/wave?" + vq).then(wave => setEd({ wave })).catch(() => {});
  requestAnimationFrame(fitTimeline);
}

// ---------------------------------------------------------------- selection + transcript actions
export function setSelUnits(i: number, j: number) {
  const { units } = ed(); const [a, b] = [Math.min(i, j), Math.max(i, j)];
  setEd({ sel: [+(units[a].s - 0.03).toFixed(3), +(units[b].e + 0.03).toFixed(3)] });
}
export const clearSel = () => setEd({ sel: null });

export function act(name: string, quiet = false) {
  const { P, sel } = ed(); if (!P) return;
  const r: Range | null = sel || (name === "start" || name === "end" ? [tNow(), tNow()] : null);
  if (!r) { toast("Select words first: drag across them in the transcript"); return; }
  const L = `${(r[1] - r[0]).toFixed(1)} s`;
  if (name === "cut") change(S => { S.drop = merge([...S.drop, r]); S.keep = subtract(S.keep, r); }, `cut ${L}`, quiet);
  if (name === "restore") change(S => { S.drop = subtract(S.drop, r); S.keep = subtract(S.keep, r); }, `restored ${L}`, quiet);
  if (name === "keep") change(S => { S.keep = merge([...S.keep, r]); S.drop = subtract(S.drop, r); }, `kept ${L} (pauses here stay)`, quiet);
  if (name === "start") change(S => { S.start = +clamp(r[0], vMin(), S.end - 1).toFixed(3); }, "clip starts here", quiet);
  if (name === "end") change(S => { S.end = +clamp(r[1], S.start + 1, vMax()).toFixed(3); }, "clip ends here", quiet);
  if (name === "hook") {
    const len = r[1] - r[0];
    if (len < 0.4 || len > 4) { toast(`A hook must be 0.4–4 s (this is ${len.toFixed(1)} s). 1–2 s works best.`); return; }
    change(S => { S.tease = [+r[0].toFixed(3), +(r[1] + 0.12).toFixed(3)]; }, len > 2.5 ? `hook set, ${L} (1–2 s hits harder)` : `hook set, ${L}`, quiet);
  }
  setEd({ sel: null });
}

export function toggleHook(quiet = false) {
  const { P, S } = ed(); if (!P || !S) return; const was = !!S.tease;
  change(S => {
    if (S.tease) { S.tease_saved = S.tease; S.tease = null; }
    else { const t = clamp(tNow(), vMin(), vMax() - 1.5); S.tease = S.tease_saved || P.state.tease || P.state.tease_saved || [+t.toFixed(3), +(t + 1.5).toFixed(3)]; }
  }, was ? "hook off" : "hook on", true);
  stop(); const S2 = ed().S!; seek(S2.tease ? S2.tease[0] : S2.start);
  if (!quiet) toast(was ? "Hook off: the clip starts at its beginning" : "Hook on: it plays first, then the transition");
}

export function setSetting(key: keyof ClipState, value: any, quiet = true) {
  const name = key === "layout" ? LNAME[value] : String(value);
  change(S => {
    (S as any)[key] = value;
  }, `${String(key).replace(/_/g, " ")}: ${name}`, quiet);
}
/** Back to how the clip was when it was opened (its last render, or the pick). One undo step: ⌘Z brings the edits back. */
export function resetEdits() {
  const { P } = ed(); if (!P) return;
  change(S => {
    const base = JSON.parse(JSON.stringify(P.state));
    for (const k of Object.keys(S)) delete (S as any)[k];
    Object.assign(S, base);
  }, P.rendered ? "reset to the last render (⌘Z to undo)" : "reset to the original pick (⌘Z to undo)");
  clearDraft(); setEd({ restored: false, sel: null });
}

export function discardDraft() {
  const { P } = ed(); if (!P) return; clearDraft();
  setEd({ S: JSON.parse(JSON.stringify(P.state)), restored: false, version: ed().version + 1 });
}

// ---------------------------------------------------------------- view
export function setMode(m: Mode) {
  stop(); setEd({ mode: m });
  if (m === "rendered" && !ed().P?.rendered) toast("Not rendered yet");
}
export const openTab = (t: Tab | null) => setEd({ tab: t, menu: null });

export function playHook() {
  const S = ed().S; if (!S?.tease) return;
  setMode("edit"); E.startPlan([{ a: S.tease[0], b: S.tease[1], kind: "clip" }]);
}

// ---------------------------------------------------------------- crop
export function openCrop() {
  const { P, S } = ed(); if (!P || !S) return;
  if (S.layout !== "react") { toast("The webcam crop is used by the Split layout"); return; }
  stop();
  const c = S.cam !== "auto" ? S.cam.split(":").map(Number) : [0.02, 0.4, 0.22, 0.22];
  setEd({ crop: { box: c as any, aspect: "free" } });
}
export function applyCrop() {
  const c = ed().crop; if (!c) return; const b = c.box.map(x => +x.toFixed(4));
  setEd({ crop: null }); change(S => { S.cam = b.join(":"); }, "webcam crop set (Track OFF)");
}
export function autoCrop() { setEd({ crop: null }); change(S => { S.cam = "auto"; }, "Track ON: webcam found at render"); }

// ---------------------------------------------------------------- render
export async function doRender() {
  const { P, S, render } = ed(); if (!P || !S || render) return;
  if (S.tease && (S.tease[1] - S.tease[0] < 0.4 || S.tease[1] - S.tease[0] > 4)) { toast("Hook must be 0.4–4 s"); return; }
  try {
    const r = await api("/api/render", { job: P.job, name: P.name, set: P.set, pick: P.pick, state: S });
    setEd({ render: { id: r.id, status: { state: "running", stage: "starting", elapsed: 0, log: [] } }, lastRender: null });
    pollRender(r.id);
  } catch (e: any) { toast(e.message); }
}
/** When the render is done the clip is in the posting queue: open it on the Post page. */
async function pollRender(id: string) {
  let st: any;
  try { st = await api("/api/render?id=" + id); } catch (e: any) { st = { state: "error", log: [e.message], elapsed: 0, stage: "" }; }
  setEd({ render: { id, status: st } });
  if (st.state === "running") { setTimeout(() => pollRender(id), 1000); return; }
  if (st.state === "done") {
    const P = ed().P!; clearDraft(); toast("Rendered ✓ It's in the posting queue");
    setTimeout(() => { location.href = `/queue?id=${encodeURIComponent(`${P.job}/${P.name}`)}`; }, 700);
  } else { toast("Render failed"); setEd({ render: null, lastRender: st }); }
}
