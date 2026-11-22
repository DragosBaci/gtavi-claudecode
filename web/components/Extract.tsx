"use client";
// Make: the desk's main page. Paste links (or let Claude choose trending videos), press Start, and every video moves
// on by itself: transcribed -> Claude picks and looks at the frames -> the keeps render -> checked -> posting queue.
// Below: every moment Claude picked (Picks), to render a "maybe" by hand or open one in the editor.
import { useState } from "react";
import { api } from "@/lib/util";
import { ago, kfmt, usePoll } from "@/lib/desk";
import Nav, { Toast } from "./Nav";
import Picks from "./Picks";

interface Pick {
  n: number; tag: string | null; title: string | null; start: number; end: number; clip: string | null; error: string | null;
  verdict?: "keep" | "maybe" | "drop" | null; why?: string | null; skipped?: boolean;   // Claude's look at the frames
  qa?: { verdict: "ok" | "fix" | "bad" | null; issues?: string[] } | null;           // Claude's check of the clip
}
interface Job {
  src: string; job: string; title?: string; channel?: string; stage: string; note: string; picks: Pick[]; why?: string;
  progress: {
    stage?: string; chunks_total?: number; chunks_done?: number; sections_total?: number; sections_done?: number; error?: string;
    started?: number; updated?: number; finished?: number; audio_sec?: number;
  };
  claude_log: string[];
}
interface Run {
  run: string; stage: string; active: boolean; terminal?: boolean; started: number; updated: number; finished?: number; urls: string[];
  opts: { max_minutes: number; picks: number; claude: boolean; render: boolean; power?: string; auto?: number }; throttled?: boolean;
  jobs: Job[]; pipeline_log: string[]; render_log: string[]; choose_log?: string[]; note?: string;
}
interface Trend { url: string; title: string; channel: string; views: number; views_per_h: number; age_h: number; minutes: number; heatmap: boolean; thumb: string | null; job: string | null; picks: number }
interface Camp { id: string; name: string; creators: string[]; any_gta: boolean; rate: number | null; pool: string | null; joined: boolean; youtube: boolean }
interface Data { runs: Run[]; trending: { at: number | null; rows: Trend[]; refreshing: boolean; query?: string }; claude: boolean; campaigns: Camp[] }


const JOBSTAGE: Record<string, string> = { waiting: "waiting", pipeline: "transcribing", ready: "waiting for Claude", picking: "Claude is reading", reviewing: "Claude is looking at the frames", picked: "waiting to render", rendering: "rendering", done: "done", error: "failed", stopped: "stopped", skipped: "skipped" };
const PIPE: Record<string, string> = { queued: "waiting its turn", info: "reading the video", audio: "downloading audio", transcribe: "transcribing", chat: "reading chat replay", score: "scoring moments", video: "downloading the moments", done: "transcribed", error: "failed", skipped: "skipped" };
const GONE = ["done", "error", "stopped", "skipped"];

// How far one video is through pipeline.py, 0..1 (null once it has left the pipeline: picking, rendering, done).
// Weights: download ~5%, transcription ~80%, scoring + fetching the picked sections the rest.
const RUNNING = ["waiting", "pipeline", "queued", "info", "audio", "transcribe", "chat", "score", "video"];
function pipeFrac(j: Job): number | null {
  const p = j.progress || {};
  if (!RUNNING.includes(j.stage) || p.stage === "error" || p.stage === "skipped") return null;
  if (!p.stage) return 0;
  if (p.stage === "done") return 1;
  switch (p.stage) {
    case "queued": return 0;
    case "info": return 0.01;
    case "audio": return 0.03;
    case "transcribe": return p.chunks_total ? 0.05 + 0.8 * (p.chunks_done || 0) / p.chunks_total : 0.05;
    case "chat": case "score": return 0.86;
    case "video": return p.sections_total ? 0.88 + 0.12 * (p.sections_done || 0) / p.sections_total : 0.88;
    default: return 0.03;
  }
}
// The run as a whole: pipeline is most of the work; picking and rendering fill the rest when the run does them.
function runFrac(r: Run): number {
  if (r.stage === "done") return 1;
  const share = r.terminal ? 1 : r.opts.render ? 0.7 : r.opts.claude ? 0.85 : 1;
  const per = r.jobs.map(j => {
    const f = pipeFrac(j);
    if (f != null) return f * share;
    if (j.stage === "ready") return share;
    if (j.stage === "picking") return share + (1 - share) * 0.2;
    if (j.stage === "reviewing") return share + (1 - share) * 0.35;
    if (j.stage === "picked" || j.stage === "rendering") {
      const n = j.picks.length, made = j.picks.filter(p => p.clip || p.error || p.skipped).length;
      return share + (1 - share) * (0.4 + 0.6 * (n ? made / n : 0));
    }
    return GONE.includes(j.stage) ? 1 : 0;
  });
  return per.length ? per.reduce((a, b) => a + b, 0) / per.length : 0;
}
const pct = (f: number) => `${Math.floor(f * 100)}%`;
function etaSec(started: number | undefined, f: number | null): number | null {
  if (!started || f == null || f < 0.08 || f >= 1) return null;
  return (Date.now() / 1000 - started) * (1 - f) / f;
}
// A run is done when its slowest video is: the largest time left among the videos still in the pipeline.
function runEta(r: Run): string {
  const left = r.jobs.map(j => etaSec(j.progress?.started, pipeFrac(j))).filter((x): x is number => x != null);
  return left.length ? fmtLeft(Math.max(...left)) : "";
}
function fmtLeft(left: number): string {
  return left < 60 ? "<1 min left" : left < 3600 ? `~${Math.round(left / 60)} min left` : `~${(left / 3600).toFixed(1)} h left`;
}

export default function Extract() {
  const { data: d, reload } = usePoll<Data>("/api/extract", 2000);
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  const active = d?.runs.filter(r => r.active) || [];
  const overall = active.length ? active.reduce((a, r) => a + runFrac(r), 0) / active.length : 0;
  return (
    <div className="desk">
      <Nav>{!!active.length && <span className="lv-rec"><i />Making clips · {pct(overall)}</span>}</Nav>
      <main className="dk-main">
        {active.map(r => <RunCard key={r.run} r={r}
          onStop={r.terminal ? undefined : () => api("/api/extract/stop", { run: r.run }).then(() => { say("Stopping…"); reload(); }).catch(e => say(e.message))} />)}
        {!active.length && <NewRun d={d} onStarted={m => { say(m); reload(); }} onErr={say} reload={reload} />}
        <Picks />
      </main>
      <Toast m={msg} />
    </div>
  );
}

function NewRun({ d, onStarted, onErr, reload }: { d: Data | null; onStarted: (m: string) => void; onErr: (m: string) => void; reload: () => void }) {
  const [picked, setPicked] = useState<string[]>([]), [extra, setExtra] = useState(""), [starting, setStarting] = useState(false);
  const [camp, setCamp] = useState("");   // a joined paid campaign these clips are for ("" = none)
  const C = d?.campaigns.find(c => c.id === camp);
  const urls = [...new Set([...picked, ...extra.split(/[\s,]+/).filter(Boolean)])];
  const tr = d?.trending;
  const fresh = tr?.rows.filter(t => !t.job).length || 0, autoN = Math.min(3, fresh);
  const start = async (auto: number) => {
    setStarting(true);
    try {
      await api("/api/extract/start", { urls: auto ? [] : urls, auto, campaign: camp || undefined });
      setPicked([]); setExtra(""); onStarted(auto ? `Claude is choosing ${auto} video${auto === 1 ? "" : "s"}` : "Started");
    }
    catch (e: any) { onErr(e.message); }
    setStarting(false);
  };
  const refresh = (c = C) => api("/api/trending/refresh", { query: c && !c.any_gta && c.creators[0] ? `${c.creators[0]} gta 6` : "" }).then(reload).catch(e => onErr(e.message));
  const pickCamp = (id: string) => {   // a creator's campaign: list that creator's latest GTA 6 videos
    setCamp(id); setPicked([]); refresh(d?.campaigns.find(x => x.id === id));
  };
  if (d && !d.claude) return <section className="dk-panel"><div className="note warn">Install Claude Code first (the <b>claude</b> command).</div></section>;
  return (
    <section className="dk-panel">
      <div className="dk-ph">Make clips</div>
      {!!d?.campaigns.length && <div className="field"><label>Make them for a campaign · Claude picks by its rules and the clips are filed under it</label>
        <select className="inp" value={camp} onChange={e => pickCamp(e.target.value)}>
          <option value="">No campaign</option>
          {d.campaigns.map(c => <option key={c.id} value={c.id}>{c.name} · {c.rate ? `$${c.rate}/1K` : c.pool || "pool"}{c.joined ? " · joined" : ""}{c.youtube ? "" : " · no YouTube"}</option>)}
        </select>
        {C && !C.joined && <div className="lv-sub" style={{ marginTop: 6 }}>Not joined yet: join it on the campaign page before posting these clips.</div>}
      </div>}
      <div className="field"><label>Video links · YouTube or Twitch VODs</label>
        <textarea className="inp" rows={2} value={extra} onChange={e => setExtra(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" />
      </div>
      <div className="field">
        <label>{tr?.query && tr.query !== "gta 6" ? `“${tr.query}” videos` : "Trending GTA 6 videos"}{tr?.at ? ` · updated ${ago(tr.at)}` : ""}
          <button className="btnx sm" style={{ marginLeft: 10 }} disabled={tr?.refreshing} onClick={() => refresh()}>{tr?.refreshing ? "Refreshing…" : "↻ Refresh"}</button>
          <button className="btnx sm" style={{ marginLeft: 6 }} disabled={starting || !autoN} onClick={() => start(autoN)}
            title="Claude reads the trending list and chooses the videos most worth clipping">✦ Let Claude choose {autoN || 3}</button></label>
        {!tr?.rows.length ? <div className="lv-none">{tr?.refreshing ? "Looking up the latest GTA 6 videos…" : "No trending list yet. Press Refresh."}</div> :
          <div className="lv-pick">
            {tr.rows.slice(0, 18).map(t => (
              <button key={t.url} className={"lv-opt" + (picked.includes(t.url) ? " on" : "")} onClick={() => setPicked(p => (p.includes(t.url) ? p.filter(x => x !== t.url) : [...p, t.url]))}>
                {t.thumb ? <img src={t.thumb} alt="" /> : <div className="lv-noimg" />}
                <div><b>{t.title}</b><span>{t.channel} · {kfmt(t.views_per_h)}/h · {t.minutes} min · {t.age_h < 1 ? "<1" : Math.round(t.age_h)} h old</span>
                  {t.job && <em>✓ already clipped</em>}</div>
              </button>
            ))}
          </div>}
      </div>
      <button className="lv-start" disabled={starting || !urls.length || urls.length > 10} onClick={() => start(0)}>
        {starting ? "Starting…" : urls.length ? `Start: ${urls.length} video${urls.length === 1 ? "" : "s"}` : "Paste a link or pick a video"}
      </button>
      <div className="note">Claude picks the moments, keeps only the best, cuts them and puts them in the posting queue. You can close this page.</div>
    </section>
  );
}

function RunCard({ r, onStop }: { r: Run; onStop?: () => void }) {
  const [armed, setArmed] = useState(false);
  const clips = r.jobs.reduce((n, j) => n + j.picks.filter(p => p.clip).length, 0);
  const f = runFrac(r);
  return (
    <section className="dk-panel dk-run active">
      <div className="dk-rh">
        <b>{r.stage === "choosing" ? "Claude is choosing videos…" : `${r.jobs.length} video${r.jobs.length === 1 ? "" : "s"}`}</b>
        <span className="lv-sub">{clips} clip{clips === 1 ? "" : "s"} so far</span>
        <span className="lv-grow" />
        {onStop && <button className={"btnx sm" + (armed ? " danger" : "")} onClick={() => { if (armed) onStop(); else { setArmed(true); setTimeout(() => setArmed(false), 3000); } }}>{armed ? "Click again to stop" : "■ Stop"}</button>}
      </div>
      <div className="dk-prog">
        <div className="dk-bar big"><i style={{ width: pct(f) }} /></div>
        <div className="dk-pl"><b>{pct(f)}</b><span className="lv-grow" /><span>{runEta(r)}</span></div>
      </div>
      {r.jobs.map(j => <JobRow key={j.job} j={j} />)}
    </section>
  );
}

function JobRow({ j }: { j: Job }) {
  const p = j.progress || {}, frac = pipeFrac(j);
  const label = frac != null && p.stage ? PIPE[p.stage] || p.stage : JOBSTAGE[j.stage] || j.stage;
  const clips = j.picks.filter(x => x.clip).length;
  const err = j.stage === "error" ? j.note : p.stage === "error" ? p.error?.slice(-200) : null;
  return (
    <div className="dk-job">
      <div className="dk-jh">
        <div className="dk-jt"><b>{j.title || j.src}</b><span>{j.channel || ""}</span></div>
        <span className={"lv-chip" + (err ? " hot" : j.stage === "done" ? " ok" : "")}>
          {j.stage === "done" ? `done · ${clips} clip${clips === 1 ? "" : "s"}` : label}</span>
      </div>
      {err && <div className="note warn">{err}</div>}
    </div>
  );
}
