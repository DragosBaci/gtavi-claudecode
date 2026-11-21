// Claude in the editor: scripted edits (POST /api/agent) performed with a visible "Claude" cursor.
// Each step moves the cursor to the real control, clicks it and runs the same action a person would,
// so every step shows in the transcript/timeline and can be undone.
import { AGENT_COLOR } from "./constants";
import { act, applyCrop, doRender, openCrop, openTab, previewTransition, saveDraft, setMode, setSelUnits, sync, toggleHook } from "./actions";
import { play, seek, stop } from "./engine";
import { estLen, plan } from "./plan";
import { ME, cursorEl, dropCursor, markAgentDone } from "./presence";
import { ed, setEd } from "./store";
import { SH, SY, TL, X } from "./timeline";
import { clamp, sleep } from "./util";

interface Ghost {
  to(x: number, y: number, ms?: number): Promise<void>;
  toEl(t: Element | null, fx?: number, fy?: number, ms?: number): Promise<void>;
  click(): Promise<void>; say(t: string): void; report(): any; remove(): void;
}
function ghost(id: string, name: string, color: string): Ghost {
  const el = cursorEl("agent:" + id, name, color, true); let pos: [number, number] = [innerWidth * 0.82, innerHeight * 0.18], said = "";
  el.style.transition = "none"; el.style.transform = `translate(${pos[0]}px,${pos[1]}px)`; void el.offsetWidth; el.style.transition = "";
  return {
    async to(x, y, ms = 600) { pos = [x, y]; el.style.transitionDuration = ms + "ms"; el.style.transform = `translate(${x - 4}px,${y - 3}px)`; await sleep(ms + 60); },
    async toEl(t, fx = 0.5, fy = 0.55, ms) {
      if (!t) throw new Error("not on screen");
      (t as HTMLElement).scrollIntoView?.({ block: "nearest", inline: "nearest" });
      const r = t.getBoundingClientRect(); await this.to(r.left + r.width * fx, r.top + r.height * fy, ms);
    },
    async click() {
      const r = document.createElement("div"); r.className = "ripple"; r.style.left = pos[0] + "px"; r.style.top = pos[1] + "px"; r.style.borderColor = color;
      document.body.appendChild(r); setTimeout(() => r.remove(), 520); await sleep(180);
    },
    say(t) { said = t || ""; const s = el.querySelector(".say")!; s.textContent = said; s.classList.toggle("show", !!said); },
    report() { return { sid: id, name, color, cursor: { el: "xy", x: pos[0], y: pos[1] }, action: said }; },
    remove() { dropCursor("agent:" + id); },
  };
}

let queue: any[] = [], running = false, current: Ghost | null = null;
export const agentGhostReport = () => (current ? [current.report()] : []);
export function queueAgentJobs(jobs: any[]) { queue.push(...jobs); if (!running) run(); }
const log = (s: string) => setEd({ agentLog: [...ed().agentLog.slice(-40), s] });
const q = (s: string) => document.querySelector(s);

function findText(text: string): [number, number] | null {   // first run of words matching text (case/punctuation ignored)
  const units = ed().units;
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim();
  const want = norm(text).split(" "); const ws = units.filter(u => u.kind === "word");
  for (let i = 0; i + want.length <= ws.length; i++) if (want.every((w, j) => norm(ws[i + j].w) === w)) return [units.indexOf(ws[i]), units.indexOf(ws[i + want.length - 1])];
  return null;
}
function scrollWord(el: Element) {
  const box = document.getElementById("words")!, r = el.getBoundingClientRect(), b = box.getBoundingClientRect();
  if (r.top < b.top + 40 || r.bottom > b.bottom - 60) box.scrollTop += r.top - b.top - b.height / 3;
}
async function openPanel(g: Ghost, tab: string) {
  if (ed().tab === tab) return;
  await g.toEl(q(`#rail [data-tab="${tab}"]`)); await g.click(); openTab(tab as any); await sleep(200);
}

async function step(g: Ghost, st: any) {
  const d = st.do, { units } = ed();
  if (d === "say") { g.say(st.text); await sleep(Math.min(4000, 900 + (st.text || "").length * 35)); return; }
  if (d === "wait") { await sleep(clamp(+st.ms || 500, 0, 8000)); return; }
  if (d === "select") {
    let ks = st.text ? findText(st.text) : null;
    if (!ks && st.a != null) { const k0 = units.findIndex(u => u.e > +st.a), k1 = units.map(u => u.s < +st.b).lastIndexOf(true); if (k0 >= 0 && k1 >= k0) ks = [k0, k1]; }
    if (!ks) throw new Error(`can't find "${st.text || st.a}"`);
    const e0 = q(`#words .u[data-k="${ks[0]}"]`)!; scrollWord(e0); await sleep(150);
    g.say(st.text ? `selecting "${st.text}"` : "selecting");
    await g.toEl(e0, 0.05, 0.6); await g.click();
    for (let k = ks[0]; k <= ks[1]; k++) {
      if (units[k].kind !== "word" && k !== ks[1]) continue;
      setSelUnits(ks[0], k); await sleep(30); await g.toEl(q(`#words .u[data-k="${k}"]`), 0.95, 0.6, 140);
    }
    return;
  }
  if (["cut", "restore", "keep", "hook", "start", "end"].includes(d)) {
    if (!ed().sel && !["start", "end"].includes(d)) throw new Error("select something first");
    g.say(({ cut: "cutting", restore: "restoring", keep: "keeping the silent part", hook: "setting the hook", start: "clip starts here", end: "clip ends here" } as any)[d]);
    await sleep(80); await g.toEl(q(`#selbar [data-act="${d}"]`)); await g.click(); act(d, true); return;
  }
  if (d === "hook_on" || d === "hook_off") {
    if (!!ed().S!.tease === (d === "hook_on")) return;
    g.say(d === "hook_on" ? "turning the hook on" : "turning the hook off"); await g.toEl(q("#hookBtn")); await g.click(); toggleHook(true); return;
  }
  if (d === "set") {
    const tabFor: any = { layout: "layout", captions: "captions", title_style: "text", transition: "transitions", keep_after_tease: "hook", cam: "layout", filler: "layout" };
    const tb = tabFor[st.key]; if (!tb) throw new Error("unknown setting " + st.key);
    const val = st.key === "keep_after_tease" ? (st.value ? "1" : "0") : String(st.value);
    g.say(`${st.key.replace(/_/g, " ")} → ${st.value}`); await openPanel(g, tb);
    const card = q(`#panel [data-set="${st.key}"][data-v="${val}"]`) as HTMLElement | null; if (!card) throw new Error(`no option ${st.value}`);
    await g.toEl(card); await g.click(); card.click(); return;
  }
  if (d === "title") {
    g.say("writing the title"); await openPanel(g, "text");
    const ta = q("#pTitle") as HTMLTextAreaElement; await g.toEl(ta, 0.1, 0.4); await g.click();
    const before = JSON.stringify(ed().S); let s = "";
    for (const ch of String(st.text || "")) { s += ch; setEd({ S: { ...ed().S!, title: s }, version: ed().version + 1 }); await sleep(28); }
    setEd({ undo: [...ed().undo, before] }); saveDraft(); sync.dirty = "wrote the title"; return;
  }
  if (d === "crop") {
    const b = (st.box || []).map(Number); if (b.length !== 4) throw new Error("crop box x,y,w,h");
    g.say("cropping the webcam"); await g.toEl(q("#trackBtn")); await g.click(); openCrop(); await sleep(250);
    setEd({ crop: { box: b as any, aspect: "free" } }); await sleep(400); await g.toEl(q("#cropApply")); await g.click(); applyCrop(); return;
  }
  if (d === "seek") { const r = TL.cv!.getBoundingClientRect(); await g.to(r.left + X(+st.t), r.top + SY + SH / 2); await g.click(); seek(+st.t); return; }
  if (d === "play") {
    const { P, S } = ed(); g.say("playing it back"); await g.toEl(q("#playBtn")); await g.click();
    setMode("edit"); seek(plan(P!, S!)[0]?.a ?? S!.start); play(); await sleep(clamp(+st.ms || estLen(P!, S!) * 1000, 500, 40000)); stop(); return;
  }
  if (d === "preview_transition") { g.say("previewing the transition"); await g.toEl(q("#stage"), 0.5, 0.5); previewTransition(); await sleep(3200); stop(); return; }
  if (d === "render") { g.say("rendering"); await g.toEl(q("#renderBtn")); await g.click(); await doRender(ME.name + " via Claude"); return; }
  throw new Error("unknown step " + d);
}

async function run() {
  running = true;
  while (queue.length) {
    const j = queue.shift(); const g = ghost(j.id, j.name || "Claude", AGENT_COLOR); current = g;
    log(`${j.name || "Claude"}: ${j.steps.length} step(s)`); stop(); await sleep(300);
    for (const st of j.steps) {
      try { await step(g, st); log(`✓ ${st.do}${st.text ? ` "${st.text}"` : st.value != null ? ` ${st.value}` : ""}`); }
      catch (e: any) { g.say("couldn't: " + e.message); log(`✕ ${st.do}: ${e.message}`); await sleep(1600); }
      await sleep(260);
    }
    g.say("done"); await sleep(1400); g.remove(); current = null; markAgentDone(j.id);
  }
  running = false;
}
