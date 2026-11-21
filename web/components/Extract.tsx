"use client";
// Extract: YouTube video(s) -> pipeline.py (transcript, scored moments) -> Claude picks the moments -> cut.py renders.
// The page starts extract.py and follows its status file; trending videos come from trends.py.
import { useState } from "react";
import { api } from "@/lib/util";
import { ago, editUrl, kfmt, usePoll } from "@/lib/desk";
import Nav, { Toast } from "./Nav";

interface Pick { n: number; tag: string | null; title: string | null; start: number; end: number; clip: string | null; error: string | null }
interface Job {
  src: string; job: string; title?: string; channel?: string; stage: string; note: string; picks: Pick[];
  progress: { stage?: string; chunks_total?: number; chunks_done?: number; sections_total?: number; sections_done?: number; error?: string };
  claude_log: string[];
}
interface Run {
  run: string; stage: string; active: boolean; started: number; updated: number; finished?: number; urls: string[];
  opts: { max_minutes: number; picks: number; claude: boolean; render: boolean; power?: Power }; throttled?: boolean;
  jobs: Job[]; pipeline_log: string[]; render_log: string[];
}
interface Trend { url: string; title: string; channel: string; views: number; views_per_h: number; age_h: number; minutes: number; heatmap: boolean; thumb: string | null; job: string | null; picks: number }
interface Data { runs: Run[]; trending: { at: number | null; rows: Trend[]; refreshing: boolean }; claude: boolean }

type Power = "cool" | "balanced" | "full";
const POWER: [Power, string, string][] = [
  ["cool", "❄ Cool", "GPU only, at background priority: quiet and cool, about 3× slower"],
  ["balanced", "Balanced", "GPU + 1 CPU worker: noticeably cooler, a bit slower"],
  ["full", "⚡ Full power", "GPU + 3 CPU workers on every core: fastest, runs hot"],
];
const STEPS: [string, string][] = [["pipeline", "Transcribe + score"], ["picking", "Claude picks"], ["rendering", "Render"], ["done", "Done"]];
const JOBSTAGE: Record<string, string> = { waiting: "waiting", pipeline: "transcribing", picking: "Claude is reading", picked: "picked", rendering: "rendering", done: "done", error: "failed", stopped: "stopped" };
const PIPE: Record<string, string> = { info: "reading the video", audio: "downloading audio", transcribe: "transcribing", chat: "reading chat replay", score: "scoring moments", video: "downloading the moments", done: "transcribed" };

export default function Extract() {
  const { data: d, reload } = usePoll<Data>("/api/extract", 2000);
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  const active = d?.runs.find(r => r.active);
  return (
    <div className="desk">
      <Nav>{active && <span className="lv-rec"><i />Extracting</span>}</Nav>
      <main className="dk-main">
        {active && <RunCard r={active} open onStop={() => api("/api/extract/stop", { run: active.run }).then(() => { say("Stopping…"); reload(); }).catch(e => say(e.message))}
          onThrottle={on => api("/api/extract/throttle", { run: active.run, on }).then(() => { say(on ? "Cooling down: slower from now on" : "Back to full speed"); reload(); }).catch(e => say(e.message))} />}
        <NewRun d={d} busy={!!active} onStarted={() => { say("Started: transcribing first"); reload(); }} onErr={say} reload={reload} />
        {!!d?.runs.filter(r => !r.active).length && <>
          <div className="dk-h">Earlier runs</div>
          {d.runs.filter(r => !r.active).map(r => <RunCard key={r.run} r={r} busy={!!active}
            onRerender={() => api("/api/extract/rerender", { run: r.run }).then(() => { say("Rendering the failed picks again"); reload(); }).catch(e => say(e.message))} />)}
        </>}
      </main>
      <Toast m={msg} />
    </div>
  );
}

function NewRun({ d, busy, onStarted, onErr, reload }: { d: Data | null; busy: boolean; onStarted: () => void; onErr: (m: string) => void; reload: () => void }) {
  const [picked, setPicked] = useState<string[]>([]), [extra, setExtra] = useState("");
  const [claude, setClaude] = useState(true), [render, setRender] = useState(true);
  const [minutes, setMinutes] = useState(90), [picks, setPicks] = useState(5), [starting, setStarting] = useState(false);
  const [power, setPowerState] = useState<Power>(() => { try { return (localStorage.getItem("extract:power") as Power) || "balanced"; } catch { return "balanced"; } });
  const setPower = (v: Power) => { setPowerState(v); try { localStorage.setItem("extract:power", v); } catch { /* private mode */ } };
  const urls = [...new Set([...picked, ...extra.split(/[\s,]+/).filter(Boolean)])];
  const tr = d?.trending;
  const start = async () => {
    setStarting(true);
    try { await api("/api/extract/start", { urls, minutes, picks, power, claude: claude && !!d?.claude, render }); setPicked([]); setExtra(""); onStarted(); }
    catch (e: any) { onErr(e.message); }
    setStarting(false);
  };
  const refresh = () => api("/api/trending/refresh", {}).then(reload).catch(e => onErr(e.message));
  return (
    <section className="dk-panel">
      <div className="dk-ph">New extraction</div>
      <div className="field"><label>Video links · YouTube (VODs, uploads, shorts) or Twitch VODs, one per line</label>
        <textarea className="inp" rows={2} value={extra} onChange={e => setExtra(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" />
      </div>
      <div className="field">
        <label>Trending GTA 6 videos · ranked by views per hour{tr?.at ? ` · updated ${ago(tr.at)}` : ""}
          <button className="btnx sm" style={{ marginLeft: 10 }} disabled={tr?.refreshing} onClick={refresh}>{tr?.refreshing ? "Refreshing… (~1 min)" : "↻ Refresh"}</button></label>
        {!tr?.rows.length ? <div className="lv-none">{tr?.refreshing ? "Looking up the latest GTA 6 videos…" : "No trending list yet. Press Refresh."}</div> :
          <div className="lv-pick">
            {tr.rows.slice(0, 18).map(t => (
              <button key={t.url} className={"lv-opt" + (picked.includes(t.url) ? " on" : "")} onClick={() => setPicked(p => (p.includes(t.url) ? p.filter(x => x !== t.url) : [...p, t.url]))}>
                {t.thumb ? <img src={t.thumb} alt="" /> : <div className="lv-noimg" />}
                <div><b>{t.title}</b><span>{t.channel} · {kfmt(t.views_per_h)}/h · {t.minutes} min · {t.age_h < 1 ? "<1" : Math.round(t.age_h)} h old</span>
                  <em>{t.job ? `✓ extracted${t.picks ? ` · ${t.picks} picks` : ""}` : t.heatmap ? "has “most replayed” data" : ""}</em></div>
              </button>
            ))}
          </div>}
      </div>
      <div className="lv-row">
        <div className="field"><label>Who picks the moments</label><div className="seg">
          <button className={claude && d?.claude ? "on" : ""} disabled={!d?.claude} onClick={() => setClaude(true)} title={d?.claude ? "" : "the claude CLI isn't installed"}>Claude reads the transcript</button>
          <button className={!claude || !d?.claude ? "on" : ""} onClick={() => setClaude(false)}>Top scores only</button>
        </div></div>
        <div className="field"><label>Then</label><div className="seg">
          <button className={render ? "on" : ""} onClick={() => setRender(true)}>Render the clips</button>
          <button className={!render ? "on" : ""} onClick={() => setRender(false)}>Just pick</button>
        </div></div>
      </div>
      <div className="lv-row">
        <div className="field"><label>Scan up to</label><div className="seg">
          {[[30, "30 min"], [90, "90 min"], [180, "3 h"], [600, "whole video"]].map(([v, n]) => <button key={v} className={minutes === v ? "on" : ""} onClick={() => setMinutes(+v)}>{n}</button>)}
        </div></div>
        <div className="field"><label>Clips per video, at most</label><div className="seg">
          {[3, 5, 8].map(v => <button key={v} className={picks === v ? "on" : ""} onClick={() => setPicks(v)}>{v}</button>)}
        </div></div>
      </div>
      <div className="field"><label>Processor · {POWER.find(x => x[0] === power)![2]}</label><div className="seg">
        {POWER.map(([k, n, h]) => <button key={k} className={power === k ? "on" : ""} title={h} onClick={() => setPower(k)}>{n}</button>)}
      </div></div>
      <button className="lv-start" disabled={busy || starting || !urls.length || urls.length > 10} onClick={start}>
        {busy ? "An extraction is running" : starting ? "Starting…" : urls.length ? `Start: ${urls.length} video${urls.length === 1 ? "" : "s"}` : "Paste a link or pick a video"}
      </button>
      <div className="note">Transcribing takes a few minutes per video (faster with an NVIDIA GPU). Then Claude reads the transcript the way the moment-finder skill says (a first line that stands alone, a payoff, a 1–2 s hook, 3 titles) and every pick is rendered as a Split clip, or Fit when there's no webcam. You can close this page: it keeps going.</div>
    </section>
  );
}

function RunCard({ r, open = false, busy = false, onStop, onRerender, onThrottle }: {
  r: Run; open?: boolean; busy?: boolean; onStop?: () => void; onRerender?: () => void; onThrottle?: (on: boolean) => void;
}) {
  const [show, setShow] = useState(open);
  const [armed, setArmed] = useState(false);
  const clips = r.jobs.reduce((n, j) => n + j.picks.filter(p => p.clip).length, 0), picks = r.jobs.reduce((n, j) => n + j.picks.length, 0);
  const steps = STEPS.filter(([k]) => k !== "rendering" || r.opts.render);
  const cur = steps.findIndex(([k]) => k === r.stage);
  return (
    <section className={"dk-panel dk-run" + (r.active ? " active" : "")}>
      <div className="dk-rh" onClick={() => setShow(s => !s)}>
        <b>{new Date(r.started * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</b>
        <span className="lv-sub">{r.urls.length} video{r.urls.length === 1 ? "" : "s"} · {r.opts.claude ? "Claude picks" : "top scores"}{r.opts.power ? ` · ${r.opts.power}` : ""}</span>
        <span className={"lv-chip" + (r.stage === "done" ? " ok" : r.stage === "error" || r.stage === "stopped" ? " hot" : "")}>
          {r.active ? `${STEPS.find(s => s[0] === r.stage)?.[1] || r.stage}…` : r.stage}</span>
        <span className="lv-sub">{picks} pick{picks === 1 ? "" : "s"} · {clips} clip{clips === 1 ? "" : "s"}</span>
        <span className="lv-grow" />
        {!r.active && onRerender && r.jobs.some(j => j.picks.some(p => p.error)) &&
          <button className="btnx sm" disabled={busy} onClick={e => { e.stopPropagation(); onRerender(); }}>↻ Retry failed renders</button>}
        {r.active && onThrottle && <button className={"btnx sm" + (r.throttled ? " q-ok" : "")} onClick={e => { e.stopPropagation(); onThrottle(!r.throttled); }}
          title={r.throttled ? "Running at background priority (cool). Click for full speed." : "Switch every process of this run to background priority: much cooler, slower."}>
          {r.throttled ? "❄ Running cool · full speed" : "❄ Run cooler"}</button>}
        {r.active && onStop && <button className={"btnx sm" + (armed ? " danger" : "")} onClick={e => { e.stopPropagation(); if (armed) onStop(); else { setArmed(true); setTimeout(() => setArmed(false), 3000); } }}>{armed ? "Click again to stop" : "■ Stop"}</button>}
        <span className="dk-chev">{show ? "▾" : "▸"}</span>
      </div>
      {show && <>
        {r.active && <div className="dk-steps">{steps.map(([k, n], i) => <span key={k} className={i < cur ? "past" : i === cur ? "now" : ""}>{n}</span>)}</div>}
        {r.jobs.map(j => <JobRow key={j.job} j={j} active={r.active} />)}
        {r.active && r.stage === "pipeline" && !!r.pipeline_log.length && <div className="log">{r.pipeline_log.join("\n")}</div>}
        {r.active && r.stage === "rendering" && !!r.render_log.length && <div className="log">{r.render_log.join("\n")}</div>}
      </>}
    </section>
  );
}

function JobRow({ j, active }: { j: Job; active: boolean }) {
  const p = j.progress || {};
  const frac = j.stage === "pipeline" ? (p.stage === "video" && p.sections_total ? 0.85 + 0.15 * (p.sections_done || 0) / p.sections_total
    : p.chunks_total ? 0.05 + 0.8 * (p.chunks_done || 0) / p.chunks_total : 0.03) : null;
  return (
    <div className="dk-job">
      <div className="dk-jh">
        <div className="dk-jt"><b>{j.title || j.src}</b><span>{j.channel || ""}</span></div>
        <span className={"lv-chip" + (j.stage === "error" ? " hot" : j.stage === "done" ? " ok" : "")}>
          {j.stage === "pipeline" && p.stage ? PIPE[p.stage] || p.stage : JOBSTAGE[j.stage] || j.stage}</span>
      </div>
      {frac != null && active && <div className="dk-bar"><i style={{ width: `${Math.round(frac * 100)}%` }} /></div>}
      {j.note && j.stage === "error" && <div className="note warn">{j.note}</div>}
      {j.stage === "picking" && active && !!j.claude_log.length && <div className="log dk-claude">{j.claude_log.join("\n")}</div>}
      {!!j.picks.length && <div className="dk-picks">
        {j.picks.map(pk => (
          <div key={pk.n} className="dk-pk">
            <span className="dk-tag">{pk.tag || `#${pk.n}`}</span>
            <span className="dk-pt">{pk.title || "untitled"}</span>
            <span className="lv-sub">{Math.round(pk.end - pk.start)} s</span>
            {pk.clip ? <a className="btnx sm" href={editUrl(j.job, pk.clip)} target="_blank" rel="noreferrer">✓ Edit clip</a>
              : <a className="btnx sm" href={editUrl(j.job, null, pk.n)} target="_blank" rel="noreferrer">{pk.error ? "Render failed · edit" : "Edit pick"}</a>}
          </div>
        ))}
      </div>}
      {j.stage === "done" && !j.picks.length && <div className="lv-sub">{j.note || "No moments picked."}</div>}
    </div>
  );
}
