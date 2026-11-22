"use client";
// Make: the desk's main page. Paste links (or let Claude choose trending videos), press Start, and every video moves
// on by itself: transcribed -> Claude picks and looks at the frames -> the keeps render -> checked -> posting queue.
// Below: every moment Claude picked (Picks), to render a "maybe" by hand or open one in the editor.
import { useState } from "react";
import { api } from "@/lib/util";
import { ago, editUrl, kfmt, usePoll } from "@/lib/desk";
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
interface Camp { id: string; name: string; creators: string[]; any_gta: boolean; rate: number | null; pool: string | null }
interface Data { runs: Run[]; trending: { at: number | null; rows: Trend[]; refreshing: boolean; query?: string }; claude: boolean; campaigns: Camp[] }

// Run stages: "choosing" and "working" (every video at its own pace); pipeline/picking/rendering are older runs, one step at a time.
const RUNSTAGE: Record<string, string> = { choosing: "Claude is choosing videos", working: "working", starting: "starting", pipeline: "Transcribe + score", picking: "Claude picks", rendering: "Render" };
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
function eta(started: number | undefined, f: number): string {
  const left = etaSec(started, f);
  return left == null ? "" : fmtLeft(left);
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
      <Nav>{!!active.length && <span className="lv-rec"><i />Extracting · {pct(overall)}</span>}</Nav>
      <main className="dk-main">
        {active.map(r => <RunCard key={r.run} r={r} open
          onStop={r.terminal ? undefined : () => api("/api/extract/stop", { run: r.run }).then(() => { say("Stopping…"); reload(); }).catch(e => say(e.message))}
          onThrottle={r.terminal ? undefined : on => api("/api/extract/throttle", { run: r.run, on }).then(() => { say(on ? "Cooling down: slower from now on" : "Back to full speed"); reload(); }).catch(e => say(e.message))}
          onJob={r.terminal || !["working", "choosing"].includes(r.stage) ? undefined : (job, action) => api("/api/extract/job", { run: r.run, job, action })
            .then(() => { say("Skipped"); reload(); }).catch(e => say(e.message))} />)}
        {!active.length && <NewRun d={d} onStarted={m => { say(m); reload(); }} onErr={say} reload={reload} />}
        <Picks />
      </main>
      <Toast m={msg} />
    </div>
  );
}

function NewRun({ d, onStarted, onErr, reload }: { d: Data | null; onStarted: (m: string) => void; onErr: (m: string) => void; reload: () => void }) {
  const [picked, setPicked] = useState<string[]>([]), [extra, setExtra] = useState("");
  const [auto, setAuto] = useState(false), [autoN, setAutoN] = useState(3), [starting, setStarting] = useState(false);
  const [camp, setCamp] = useState("");   // a joined paid campaign these clips are for ("" = none)
  const C = d?.campaigns.find(c => c.id === camp);
  const urls = [...new Set([...(auto ? [] : picked), ...extra.split(/[\s,]+/).filter(Boolean)])];
  const tr = d?.trending;
  const fresh = tr?.rows.filter(t => !t.job).length || 0;
  const total = urls.length + (auto ? autoN : 0);
  const start = async () => {
    setStarting(true);
    try {
      await api("/api/extract/start", { urls, auto: auto ? autoN : 0, campaign: camp || undefined });
      setPicked([]); setExtra(""); onStarted(auto ? `Claude is choosing ${autoN} video${autoN === 1 ? "" : "s"}` : "Started: transcribing first");
    }
    catch (e: any) { onErr(e.message); }
    setStarting(false);
  };
  const refresh = (query = C && !C.any_gta && C.creators[0] ? `${C.creators[0]} gta 6` : "") =>
    api("/api/trending/refresh", { query }).then(reload).catch(e => onErr(e.message));
  const pickCamp = (id: string) => {   // a creator's campaign: list that creator's latest GTA 6 videos
    setCamp(id); setPicked([]);
    const c = d?.campaigns.find(x => x.id === id);
    refresh(c && !c.any_gta && c.creators[0] ? `${c.creators[0]} gta 6` : "");
  };
  return (
    <section className="dk-panel">
      <div className="dk-ph">Make clips</div>
      {!!d?.campaigns.length && <div className="field"><label>For a paid campaign</label><div className="seg">
        <button className={!camp ? "on" : ""} onClick={() => pickCamp("")}>No campaign</button>
        {d.campaigns.map(c => <button key={c.id} className={camp === c.id ? "on" : ""} onClick={() => pickCamp(c.id)}
          title="Claude picks by this campaign's rules and every clip gets its overlay">{c.name.split(" (")[0]} · {c.rate ? `$${c.rate}/1K` : c.pool || "pool"}</button>)}
      </div></div>}
      <div className="field"><label>Video links · YouTube (VODs, uploads, shorts) or Twitch VODs, one per line</label>
        <textarea className="inp" rows={2} value={extra} onChange={e => setExtra(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" />
      </div>
      <div className="lv-row">
        <div className="field"><label>Which trending videos</label><div className="seg">
          <button className={!auto ? "on" : ""} onClick={() => setAuto(false)}>I pick them</button>
          <button className={auto ? "on" : ""} onClick={() => setAuto(true)} title="Claude reads the trending list and chooses the videos most worth clipping">Claude chooses</button>
        </div></div>
        {auto && <div className="field"><label>How many</label><div className="seg">
          {[1, 2, 3, 5, 8].map(v => <button key={v} className={autoN === v ? "on" : ""} disabled={v > fresh} onClick={() => setAutoN(v)}>{v}</button>)}
        </div></div>}
      </div>
      <div className="field">
        <label>{tr?.query && tr.query !== "gta 6" ? `“${tr.query}” videos, last 3 days` : "Trending GTA 6 videos"} · ranked by views per hour{tr?.at ? ` · updated ${ago(tr.at)}` : ""}{auto ? ` · Claude chooses from the ${fresh} not extracted yet` : ""}
          <button className="btnx sm" style={{ marginLeft: 10 }} disabled={tr?.refreshing} onClick={() => refresh()}>{tr?.refreshing ? "Refreshing… (~1 min)" : "↻ Refresh"}</button></label>
        {!tr?.rows.length ? <div className="lv-none">{tr?.refreshing ? "Looking up the latest GTA 6 videos…" : "No trending list yet. Press Refresh."}</div> :
          <div className={"lv-pick" + (auto ? " dk-dim" : "")}>
            {tr.rows.slice(0, 18).map(t => (
              <button key={t.url} disabled={auto} className={"lv-opt" + (!auto && picked.includes(t.url) ? " on" : "")} onClick={() => setPicked(p => (p.includes(t.url) ? p.filter(x => x !== t.url) : [...p, t.url]))}>
                {t.thumb ? <img src={t.thumb} alt="" /> : <div className="lv-noimg" />}
                <div><b>{t.title}</b><span>{t.channel} · {kfmt(t.views_per_h)}/h · {t.minutes} min · {t.age_h < 1 ? "<1" : Math.round(t.age_h)} h old</span>
                  <em>{t.job ? `✓ extracted${t.picks ? ` · ${t.picks} picks` : ""}` : t.heatmap ? "has “most replayed” data" : ""}</em></div>
              </button>
            ))}
          </div>}
      </div>
      <button className="lv-start" disabled={starting || !total || total > 10 || (auto && !fresh) || !d?.claude} onClick={start}>
        {!d?.claude ? "Install Claude Code first (the claude command)" : starting ? "Starting…" : auto ? (fresh ? `Start: Claude chooses ${autoN} video${autoN === 1 ? "" : "s"}${urls.length ? ` + ${urls.length} link${urls.length === 1 ? "" : "s"}` : ""}` : "Refresh the trending list first")
          : urls.length ? `Start: ${urls.length} video${urls.length === 1 ? "" : "s"}` : "Paste a link or pick a video"}
      </button>
      <div className="note">Claude reads each transcript, looks at the frames of every moment it picks and keeps only the ones it would bet on. Those are cut (face on top when the streamer's webcam is over gameplay, otherwise a big window on the face or the action), checked once more, and go straight into the posting queue. You can close this page: it keeps going.</div>
    </section>
  );
}

function RunCard({ r, open = false, onStop, onThrottle, onJob }: {
  r: Run; open?: boolean; onStop?: () => void; onThrottle?: (on: boolean) => void; onJob?: (job: string, action: "skip") => void;
}) {
  const [show, setShow] = useState(open);
  const [armed, setArmed] = useState(false);
  const clips = r.jobs.reduce((n, j) => n + j.picks.filter(p => p.clip).length, 0), picks = r.jobs.reduce((n, j) => n + j.picks.length, 0);
  const f = r.active ? runFrac(r) : 1;
  // Where every video is right now, e.g. "1 transcribing · 2 Claude picking · 2 done"
  const inPipe = (j: Job) => RUNNING.includes(j.stage) && j.progress?.stage !== "done";   // older runs flag videos only at the end
  const groups: [string, (j: Job) => boolean][] = [
    ["transcribing", inPipe], ["transcribed", j => RUNNING.includes(j.stage) && !inPipe(j)], ["waiting for Claude", j => j.stage === "ready"], ["Claude picking", j => j.stage === "picking" || j.stage === "reviewing"],
    ["rendering", j => j.stage === "picked" || j.stage === "rendering"], ["done", j => j.stage === "done"],
    ["skipped", j => j.stage === "skipped"], ["failed", j => j.stage === "error"],
  ];
  const counts = groups.map(([n, t]) => [n, r.jobs.filter(t).length] as [string, number]).filter(([, c]) => c);
  const transcribed = r.jobs.filter(j => !inPipe(j) && j.stage !== "skipped" && j.stage !== "stopped" && j.stage !== "error").length;
  return (
    <section className={"dk-panel dk-run" + (r.active ? " active" : "")}>
      <div className="dk-rh" onClick={() => setShow(s => !s)}>
        <b>{new Date(r.started * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</b>
        <span className="lv-sub">{r.urls.length} video{r.urls.length === 1 ? "" : "s"}</span>
        {r.terminal && <span className="lv-chip" title="Started from a terminal or by Claude, not from this page. Stop it where it was started.">from terminal</span>}
        <span className={"lv-chip" + (r.stage === "done" ? " ok" : r.stage === "error" || r.stage === "stopped" ? " hot" : "")}>
          {r.active ? `${RUNSTAGE[r.stage] || r.stage}…` : r.stage}</span>
        {(!r.terminal || picks > 0) && <span className="lv-sub">{picks} pick{picks === 1 ? "" : "s"} · {clips} clip{clips === 1 ? "" : "s"}</span>}
        <span className="lv-grow" />
        {r.active && onThrottle && <button className={"btnx sm" + (r.throttled ? " q-ok" : "")} onClick={e => { e.stopPropagation(); onThrottle(!r.throttled); }}
          title={r.throttled ? "Running at background priority (cool). Click for full speed." : "Switch every process of this run to background priority: much cooler, slower."}>
          {r.throttled ? "❄ Running cool · full speed" : "❄ Run cooler"}</button>}
        {r.active && onStop && <button className={"btnx sm" + (armed ? " danger" : "")} onClick={e => { e.stopPropagation(); if (armed) onStop(); else { setArmed(true); setTimeout(() => setArmed(false), 3000); } }}>{armed ? "Click again to stop" : "■ Stop"}</button>}
        <span className="dk-chev">{show ? "▾" : "▸"}</span>
      </div>
      {r.active && <div className="dk-prog">
        <div className="dk-bar big"><i style={{ width: pct(f) }} /></div>
        <div className="dk-pl"><b>{pct(f)}</b><span>{r.stage === "choosing" ? "Claude is reading the trending list" : `${transcribed} of ${r.jobs.length} video${r.jobs.length === 1 ? "" : "s"} transcribed`}</span>
          <span className="lv-grow" /><span>{runEta(r)}</span></div>
      </div>}
      {show && <>
        {r.active && !!counts.length && <div className="dk-steps">{counts.map(([n, c]) =>
          <span key={n} className={n === "done" ? "past" : ["skipped", "failed"].includes(n) ? "" : "now"}>{c} {n}</span>)}</div>}
        {(r.stage === "choosing" || (!r.active && r.opts.auto)) && !!r.choose_log?.length && <div className="log dk-claude">{r.choose_log.join("\n")}</div>}
        {!r.active && r.note && <div className="note warn">{r.note}</div>}
        {r.jobs.map(j => <JobRow key={j.job} j={j} active={r.active} onJob={onJob && (a => onJob(j.job, a))} />)}
        {r.active && r.jobs.some(inPipe) && !!r.pipeline_log.length && <div className="log">{r.pipeline_log.join("\n")}</div>}
        {r.active && r.jobs.some(j => j.stage === "rendering") && !!r.render_log.length && <div className="log">{r.render_log.join("\n")}</div>}
      </>}
    </section>
  );
}

function JobRow({ j, active, onJob }: { j: Job; active: boolean; onJob?: (a: "skip") => void }) {
  const p = j.progress || {};
  const frac = pipeFrac(j);
  const running = frac != null && frac < 1 && active;
  const [armed, setArmed] = useState(false);
  const live = active && !!onJob && !GONE.includes(j.stage);
  const detail = p.stage === "transcribe" && p.chunks_total ? `${p.chunks_done || 0} / ${p.chunks_total} chunks`
    : p.stage === "video" && p.sections_total ? `${p.sections_done || 0} / ${p.sections_total} sections`
    : p.stage === "queued" ? "starts when a worker is free" : "";
  const mins = p.audio_sec ? ` · ${Math.round(p.audio_sec / 60)} min of audio` : "";
  return (
    <div className="dk-job">
      <div className="dk-jh">
        <div className="dk-jt"><b>{j.title || j.src}</b><span>{j.channel || ""}{mins}</span></div>
        {live && <button className={"btnx sm" + (armed ? " danger" : "")} title="Drop this video from the run; the others keep going"
          onClick={() => { if (armed) onJob!("skip"); else { setArmed(true); setTimeout(() => setArmed(false), 3000); } }}>{armed ? "Click again to skip" : "Skip"}</button>}
        <span className={"lv-chip" + (j.stage === "error" || p.stage === "error" ? " hot" : j.stage === "done" || frac === 1 ? " ok" : "")}>
          {frac != null && p.stage ? PIPE[p.stage] || p.stage : JOBSTAGE[j.stage] || j.stage}</span>
      </div>
      {j.why && <div className="lv-sub dk-why">Claude: {j.why}</div>}
      {j.note && j.stage !== "error" && j.stage !== "done" && <div className="lv-sub dk-why">{j.note}</div>}
      {running && <>
        <div className="dk-bar"><i style={{ width: pct(frac) }} /></div>
        <div className="dk-pl"><b>{pct(frac)}</b><span>{detail}</span><span className="lv-grow" /><span>{eta(p.started, frac)}</span></div>
      </>}
      {p.stage === "error" && p.error && <div className="note warn">{p.error.slice(-200)}</div>}
      {j.note && j.stage === "error" && <div className="note warn">{j.note}</div>}
      {j.stage === "picking" && active && !!j.claude_log.length && <div className="log dk-claude">{j.claude_log.join("\n")}</div>}
      {!!j.picks.length && <div className="dk-picks">
        {j.picks.map(pk => (
          <div key={pk.n} className="dk-pk">
            <span className="dk-tag">{pk.tag || `#${pk.n}`}</span>
            <span className="dk-pt">{pk.title || "untitled"}</span>
            <span className="lv-sub">{Math.round(pk.end - pk.start)} s</span>
            {pk.verdict && <span className={"pk-rkb v-" + pk.verdict} title={pk.why || ""}>{pk.verdict === "keep" ? "✦ keep" : pk.verdict === "maybe" ? "maybe · not rendered" : "drop"}</span>}
            {pk.qa?.verdict && pk.qa.verdict !== "ok" && <span className="lv-chip hot" title={(pk.qa.issues || []).join("\n")}>{pk.qa.verdict === "bad" ? "QA: don't post" : "QA: re-cut"}</span>}
            {pk.clip ? <a className="btnx sm" href={editUrl(j.job, pk.clip)} target="_blank" rel="noreferrer">✓ Edit clip</a>
              : <a className="btnx sm" href={editUrl(j.job, null, pk.n)} target="_blank" rel="noreferrer">{pk.error ? "Render failed · edit" : "Edit pick"}</a>}
          </div>
        ))}
      </div>}
      {j.stage === "done" && !j.picks.length && <div className="lv-sub">{j.note || "No moments picked."}</div>}
    </div>
  );
}
