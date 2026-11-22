"use client";
// Moments (on the Make page): the moments Claude picked that aren't clips yet, grouped by video. The keeps render and
// queue by themselves; render a "maybe" here when you want it too (extract.py --render-queue fetches the video first
// when it isn't on disk). Drops and hidden picks stay out of the way.
import { useMemo, useState } from "react";
import { api } from "@/lib/util";
import { ago, editUrl, usePoll } from "@/lib/desk";
import { Toast } from "./Nav";
import PickWatch, { WItem } from "./PickWatch";

interface Clip { name: string; video: string; updated: number }
interface QState { id: string; op: string; state: "queued" | "fetching" | "rendering" | "done" | "failed"; error?: string; clip?: string; started?: number }
interface Pick {
  n: number; tag: string; title: string; titles: string[]; start: number; end: number; text: string;
  preview: string | null; t0: number; offset: number; tease: [number, number] | null; clips: Clip[]; queue: QState | null; hidden: boolean;
  rank: Rank | null;
}
interface Rank { rank: number; verdict: "keep" | "maybe" | "drop"; why: string }
interface Video { job: string; title: string; channel: string | null; url: string; thumb: string | null; minutes: number; picked: number; picks: Pick[] }
interface RNow { job: string; n: number; frac: number; label: string; left: number }
interface Renders { now: RNow[]; queued: number; done: number; per: number; par: number; left: number }
interface Data { videos: Video[]; worker: boolean; now: number; renders: Renders | null }

// Ranked picks first, best first; unranked after, in pick order
const byRank = (a: Pick, b: Pick) => (a.rank?.rank ?? 1e9) - (b.rank?.rank ?? 1e9) || a.n - b.n;
const dur = (s: number) => s < 60 ? `${Math.max(1, Math.round(s))} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const busy = (p: Pick) => !!p.queue && ["queued", "fetching", "rendering"].includes(p.queue.state);

type Filter = "todo" | "done";
const SHOW: Record<Filter, (p: Pick) => boolean> = {
  todo: p => !p.hidden && !p.clips.length && p.rank?.verdict !== "drop",
  done: p => p.clips.length > 0,
};
const STATE: Record<string, string> = { queued: "waiting to render", fetching: "downloading", rendering: "rendering…", failed: "render failed" };

export default function Picks() {
  const { data: d, reload } = usePoll<Data>("/api/picks", 3000);
  const [filter, setFilter] = useState<Filter>("todo");
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  const [watch, setWatch] = useState<{ keys: string[]; at: number } | null>(null);

  const key = (job: string, n: number) => `${job}:${n}`;
  const all = useMemo(() => (d?.videos || []).flatMap(v => [...v.picks].sort(byRank).map(p => ({ v, p }))), [d]);
  const visible = all.filter(x => SHOW[filter](x.p));
  const count = (f: Filter) => all.filter(x => SHOW[f](x.p)).length;

  const render = (v: Video, p: Pick) => api("/api/picks/render", { items: [{ job: v.job, n: p.n }] })
    .then((r: any) => { say(r.queued ? "Queued to render" : "Already in the render queue"); reload(); }).catch(e => say(e.message));
  const hide = (v: Video, p: Pick) => api("/api/picks/hide", { job: v.job, ns: [p.n], hidden: true })
    .then(() => { say("Hidden"); reload(); }).catch(e => say(e.message));
  const cancel = (v: Video, p: Pick) => api("/api/picks/cancel", { job: v.job, n: p.n })
    .then(() => { say("Taken out of the render queue"); reload(); }).catch(e => say(e.message));

  // The watch view keeps the list it opened with (hiding a pick doesn't reshuffle it) but reads live state for each one.
  const openWatch = (at?: { v: Video; p: Pick }) => {
    const keys = visible.map(x => key(x.v.job, x.p.n));
    setWatch({ keys, at: at ? Math.max(0, keys.indexOf(key(at.v.job, at.p.n))) : 0 });
  };
  const byKey = new Map(all.map(x => [key(x.v.job, x.p.n), x]));
  const witems: WItem[] = (watch?.keys || []).map(k => byKey.get(k)).filter((x): x is { v: Video; p: Pick } => !!x).map(x => ({ job: x.v.job, video: x.v.title, p: x.p }));
  const back = (w: WItem) => byKey.get(key(w.job, w.p.n))!;
  const r = d?.renders, working = all.filter(x => busy(x.p)).length;

  return (
    <section id="moments">
      <div className="dk-h">Moments
        {!!working && <span className="lv-rec"><i />Rendering {working}{r?.left ? ` · ~${dur(r.left)} left` : ""}</span>}
      </div>
      <div className="pk-bar">
        <div className="seg">
          {([["todo", "To render"], ["done", "Rendered"]] as [Filter, string][]).map(([f, n]) =>
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{n} <span className="pk-n">{count(f)}</span></button>)}
        </div>
        <button className="pk-watch" disabled={!visible.length} onClick={() => openWatch()}>▶ Watch {visible.length}</button>
      </div>

      {!d ? <div className="lv-none">Loading…</div> : !visible.length ? <div className="lv-none">
        {filter === "todo" ? "Nothing waiting. Claude's keeps render by themselves; any maybes show up here." : "No rendered picks yet."}
      </div> : d.videos.map(v => {
        const ps = v.picks.filter(SHOW[filter]).sort(byRank);
        if (!ps.length) return null;
        return (
          <section key={v.job} className="dk-panel pk-video">
            <div className="pk-vh">
              {v.thumb ? <img src={v.thumb} alt="" /> : <div className="lv-noimg" />}
              <div className="pk-vt">
                <b>{v.title}</b>
                <span>{v.channel || ""}{v.minutes ? ` · ${v.minutes} min` : ""} · picked {ago(v.picked, d.now)}
                  {v.url.startsWith("http") && <> · <a href={v.url} target="_blank" rel="noreferrer">source ↗</a></>}</span>
              </div>
            </div>
            <div className="pk-grid">
              {ps.map(p => <PickCard key={p.n} v={v} p={p} onRender={() => render(v, p)} onHide={() => hide(v, p)} onCancel={() => cancel(v, p)}
                onWatch={() => openWatch({ v, p })} prog={r?.now.find(x => x.job === v.job && x.n === p.n) || null} />)}
            </div>
          </section>
        );
      })}
      {watch && !!witems.length && <PickWatch items={witems} at={watch.at} onClose={() => setWatch(null)}
        onRender={w => { const x = back(w); render(x.v, x.p); }} onHide={w => { const x = back(w); hide(x.v, x.p); }} />}
      <Toast m={msg} />
    </section>
  );
}

function PickCard({ v, p, onRender, onHide, onCancel, onWatch, prog }: {
  v: Video; p: Pick; onRender: () => void; onHide: () => void; onCancel: () => void; onWatch: () => void; prog: RNow | null;
}) {
  const [noThumb, setNoThumb] = useState(false);
  const q = p.queue, b = busy(p), clip = p.clips[0];
  return (
    <div className={"pk-card" + (b ? " busy" : "")}>
      <button className="pk-thumb" onClick={p.preview ? onWatch : undefined} title={p.preview ? "Watch it" : ""}>
        {p.preview && !noThumb ? <img loading="lazy" alt="" src={`/api/pick/thumb?job=${encodeURIComponent(v.job)}&n=${p.n}`} onError={() => setNoThumb(true)} />
          : <div className="pk-nothumb" />}
        {p.preview && <span className="pk-playbtn">▶</span>}
        <span className="pk-dur">{Math.round(p.end - p.start)} s</span>
        {p.rank && <span className={"pk-rkb v-" + p.rank.verdict}>#{p.rank.rank} · {p.rank.verdict}</span>}
      </button>
      <div className="pk-ch">
        <span className="dk-tag">{p.tag || `#${p.n}`}</span>
        <span className="lv-sub">{mmss(p.start)}–{mmss(p.end)}</span>
        <span className="lv-grow" />
        {!clip && q && q.state !== "done" && <span className={"lv-chip" + (q.state === "failed" ? " hot" : "")} title={q.error || ""}>
          {prog ? `${Math.floor(prog.frac * 100)}% · ${prog.label}` : STATE[q.state]}</span>}
      </div>
      <div className="pk-title">{p.title || "untitled"}</div>
      {p.rank?.why && <div className={"pk-why v-" + p.rank.verdict}>✦ {p.rank.why}</div>}
      {q?.state === "failed" && q.error && !clip && <div className="note warn">{q.error}</div>}
      <div className="pk-acts">
        {q?.state === "queued" ? <button className="btnx sm" onClick={onCancel}>Cancel</button>
          : !b && !clip && <button className="btnx sm" onClick={onRender}>{q?.state === "failed" ? "↻ Retry" : "Render"}</button>}
        {clip && <a className="btnx sm" href={editUrl(v.job, clip.name)} target="_blank" rel="noreferrer">Open clip</a>}
        <span className="lv-grow" />
        {!b && !clip && <button className="btnx sm ghost" onClick={onHide}>Hide</button>}
      </div>
    </div>
  );
}
