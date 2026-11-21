// Live cursors: every window on the same clip posts its cursor, sees the others' (named), shares edits
// (last writer wins), shows a cut.py cursor while a render runs, and picks up Claude's agent jobs.
// The cursor layer (#cursors) is managed here directly, outside React, because it moves every frame.
import { NAMES, PALETTE } from "./constants";
import { openClip, saveDraft, sync, toast } from "./actions";
import { queueAgentJobs, agentGhostReport } from "./agent";
import { ed, setEd } from "./store";
import { T, TL, X, isDragging } from "./timeline";
import { api, clamp, lsGet, lsSet } from "./util";

export interface Me { name: string; color: string; sid: string }
export const ME: Me = { name: "", color: "", sid: "" };
export function initMe() {
  let m = lsGet<{ name: string; color: string } | null>("clipeditor:me", null);
  if (!m) { const i = Math.floor(Math.random() * NAMES.length); m = { name: NAMES[i], color: PALETTE[i % PALETTE.length] }; lsSet("clipeditor:me", m); }
  Object.assign(ME, m, { sid: Math.random().toString(36).slice(2, 10) });
}
export function renameMe(n: string) { ME.name = n.trim().slice(0, 20); lsSet("clipeditor:me", { name: ME.name, color: ME.color }); }

const CUR_SVG = (c: string) => `<svg viewBox="0 0 24 24"><path d="M4 2.5 20 11l-7 1.8L9.6 20z" fill="${c}" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
const esc = (s: string) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
function tagBg(c: string) { const n = parseInt(c.slice(1), 16); return `rgb(${((n >> 16) * 0.42 + 30) | 0},${(((n >> 8) & 255) * 0.42 + 26) | 0},${((n & 255) * 0.42 + 22) | 0})`; }
const cursorEls: Record<string, HTMLDivElement> = {};
export function cursorEl(id: string, name: string, color: string, agent = false) {
  let el = cursorEls[id];
  if (!el) {
    el = document.createElement("div"); el.className = "pcur" + (agent ? " agent" : "");
    el.innerHTML = `${CUR_SVG(color)}<span class="tag" style="background:${tagBg(color)};color:#fff">${esc(name)}</span><span class="say"></span>`;
    document.getElementById("cursors")?.appendChild(el); cursorEls[id] = el;
  }
  return el;
}
export function dropCursor(id: string) { cursorEls[id]?.remove(); delete cursorEls[id]; }

// cursor positions are sent as anchors (a word, a time on the timeline, a spot on the preview) so they land on
// the same thing in windows of different sizes
function myAnchor(x: number, y: number): any {
  const u = (document.elementFromPoint(x, y) as HTMLElement | null)?.closest?.("#words .u") as HTMLElement | null;
  if (u) { const r = u.getBoundingClientRect(); return { el: "u", k: +u.dataset.k!, fx: (x - r.left) / r.width, fy: (y - r.top) / r.height }; }
  const cv = TL.cv;
  if (cv && ed().P && !ed().tlHidden) { const r = cv.getBoundingClientRect(); if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return { el: "tl", t: +T(x - r.left).toFixed(3), fy: (y - r.top) / r.height }; }
  const st = document.getElementById("stage")?.getBoundingClientRect();
  if (st && x >= st.left && x <= st.right && y >= st.top && y <= st.bottom) return { el: "stage", fx: (x - st.left) / st.width, fy: (y - st.top) / st.height };
  const ar = document.getElementById("app")!.getBoundingClientRect();
  return { el: "page", fx: (x - ar.left) / ar.width, fy: (y - ar.top) / ar.height };
}
function resolveAnchor(a: any): [number, number] | null {
  if (!a) return null;
  if (a.el === "u") {
    const u = document.querySelector(`#words .u[data-k="${a.k}"]`); const box = document.getElementById("words");
    if (u && box) { const r = u.getBoundingClientRect(), b = box.getBoundingClientRect(); return [r.left + a.fx * r.width, clamp(r.top + a.fy * r.height, b.top, b.bottom)]; }
  }
  if (a.el === "tl" && ed().P && TL.cv && TL.wrap) { const r = TL.cv.getBoundingClientRect(), b = TL.wrap.getBoundingClientRect(); return [clamp(r.left + X(a.t), b.left, b.right), r.top + a.fy * r.height]; }
  if (a.el === "stage") { const r = document.getElementById("stage")!.getBoundingClientRect(); return [r.left + a.fx * r.width, r.top + a.fy * r.height]; }
  if (a.el === "xy") return [a.x, a.y];
  const r = document.getElementById("app")!.getBoundingClientRect(); return [r.left + (a.fx ?? 0.5) * r.width, r.top + (a.fy ?? 0.5) * r.height];
}

let mouse: [number, number] | null = null, myAction: string | null = null, myActionAt = 0;
export function trackMouse() {
  const mv = (e: MouseEvent) => { mouse = [e.clientX, e.clientY]; };
  const lv = () => { mouse = null; };
  document.addEventListener("mousemove", mv); document.addEventListener("mouseleave", lv);
  return () => { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseleave", lv); };
}

export function drawPeers() {
  const { peers, remoteRender } = ed(); const seen = new Set<string>();
  for (const p of peers) {
    if (!p.cursor) continue; const xy = resolveAnchor(p.cursor); if (!xy) continue;
    const id = "p:" + p.sid; seen.add(id);
    const el = cursorEl(id, p.name, p.color, p.ghost); el.style.transform = `translate(${xy[0] - 4}px,${xy[1] - 3}px)`;
    const say = el.querySelector(".say")!; say.textContent = p.action || ""; say.classList.toggle("show", !!p.action);
  }
  if (remoteRender?.state === "running") {   // cut.py works on the Render button while a render runs
    const b = document.getElementById("renderBtn");
    if (b) {
      const r = b.getBoundingClientRect(), id = "render"; seen.add(id);
      const el = cursorEl(id, "cut.py", "#a7b0b8"); el.style.transform = `translate(${r.left + r.width * 0.72}px,${r.top + r.height * 0.55}px)`;
      const say = el.querySelector(".say")!; say.textContent = `${remoteRender.stage} · ${Math.round(remoteRender.elapsed)} s`; say.classList.add("show");
    }
  }
  for (const id of Object.keys(cursorEls)) if (!seen.has(id) && !id.startsWith("agent:")) dropCursor(id);
}

let agentDone: string[] = [];
export const markAgentDone = (id: string) => agentDone.push(id);

export function startPresence() {
  let stopped = false, inflight = false;
  const loop = async () => {
    if (stopped) return;
    const { P, S } = ed();
    if (!inflight && P && S) {
      inflight = true;
      const body: any = {
        sid: ME.sid, name: ME.name, color: ME.color, clip: `${P.job}/${P.name}`, known_v: sync.knownV,
        cursor: mouse ? myAnchor(mouse[0], mouse[1]) : null,
        action: performance.now() - myActionAt < 2500 ? myAction : null,
        ghosts: agentGhostReport(), agent_done: agentDone,
      };
      if (sync.dirty) { body.state = S; body.state_action = sync.dirty; myAction = sync.dirty; myActionAt = performance.now(); sync.dirty = null; }
      try {
        const r = await api("/api/presence", body); agentDone = [];
        if (body.state) sync.knownV = r.v;
        const patch: any = { peers: r.peers || [] };
        if (r.shared && r.shared.v > sync.knownV && !isDragging()) {   // someone else edited this clip: take their version
          sync.knownV = r.shared.v;
          patch.S = r.shared.state; patch.undo = [...ed().undo, JSON.stringify(ed().S)]; patch.version = ed().version + 1;
          toast(`${r.shared.name}: ${r.shared.action || "edited"}`);
        } else if (r.v > sync.knownV && !r.shared) sync.knownV = r.v;
        const was = ed().remoteRender?.state; patch.remoteRender = r.render;
        setEd(patch); if (patch.S) saveDraft();
        if (was === "running" && r.render?.state === "done" && !ed().myRenders.includes(r.render.id)) {
          toast("Render finished ✓"); const keep = JSON.stringify(ed().S); await openClip({ job: P.job, name: P.name }); setEd({ S: JSON.parse(keep) });
        }
        if (r.agent?.length) queueAgentJobs(r.agent);
      } catch { /* server restarting */ }
      inflight = false;
    }
    setTimeout(loop, 140);
  };
  loop();
  return () => { stopped = true; };
}
