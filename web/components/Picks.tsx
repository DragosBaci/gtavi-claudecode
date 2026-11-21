"use client";
// Picks: every moment Claude picked, across all extractions, grouped by video. Tick the ones worth posting and render
// them in one go (extract.py --render-queue renders one at a time; a pick whose video isn't downloaded yet is fetched first).
import { useMemo, useState } from "react";
import { api } from "@/lib/util";
import { ago, editUrl, usePoll } from "@/lib/desk";
import Nav, { Toast } from "./Nav";
import PickWatch, { WItem } from "./PickWatch";

interface Clip { name: string; video: string; updated: number }
interface QState { id: string; op: string; state: "queued" | "fetching" | "rendering" | "done" | "failed"; error?: string; clip?: string; started?: number }
interface Pick {
  n: number; tag: string; title: string; titles: string[]; start: number; end: number; text: string;
  preview: string | null; t0: number; offset: number; tease: [number, number] | null; clips: Clip[]; queue: QState | null; hidden: boolean;
  rank: Rank | null;
}
interface Rank { rank: number; verdict: "keep" | "maybe" | "drop"; why: string }
interface Ranking { state: "queued" | "running" | "done" | "failed"; at?: number; error?: string; of?: number }
interface Video { job: string; title: string; channel: string | null; url: string; thumb: string | null; minutes: number; picked: number; picks: Pick[]; ranking: Ranking | null }
// Ranked picks first, best first; unranked after, in pick order
const byRank = (a: Pick, b: Pick) => (a.rank?.rank ?? 1e9) - (b.rank?.rank ?? 1e9) || a.n - b.n;
const VERDICT: Record<string, string> = { keep: "keep", maybe: "maybe", drop: "drop" };
interface RNow { job: string; n: number; frac: number; label: string; left: number }
interface Renders { now: RNow[]; queued: number; done: number; per: number; par: number; left: number }
interface Data { videos: Video[]; worker: boolean; now: number; renders: Renders | null }
const dur = (s: number) => s < 60 ? `${Math.max(1, Math.round(s))} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;

type Filter = "todo" | "done" | "all" | "hidden";
const key = (job: string, n: number) => `${job}:${n}`;
const busy = (p: Pick) => !!p.queue && ["queued", "fetching", "rendering"].includes(p.queue.state);
const isDone = (p: Pick) => p.clips.length > 0;
const SHOW: Record<Filter, (p: Pick) => boolean> = {
  todo: p => !p.hidden && !isDone(p), done: p => isDone(p), all: p => !p.hidden, hidden: p => p.hidden,
};
const STATE: Record<string, string> = { queued: "waiting to render", fetching: "downloading the video", rendering: "rendering…", failed: "render failed" };
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function Picks() {
  const { data: d, reload } = usePoll<Data>("/api/picks", 3000);
  const [filter, setFilter] = useState<Filter>("todo");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  const [watch, setWatch] = useState<{ keys: string[]; at: number } | null>(null);

  const all = useMemo(() => (d?.videos || []).flatMap(v => [...v.picks].sort(byRank).map(p => ({ v, p }))), [d]);
  const count = (f: Filter) => all.filter(x => SHOW[f](x.p)).length;
  const working = all.filter(x => busy(x.p));
  const now = working.find(x => x.p.queue?.state === "rendering" || x.p.queue?.state === "fetching");
  const visible = all.filter(x => SHOW[filter](x.p));
  const chosen = all.filter(x => sel.has(key(x.v.job, x.p.n)));

  const toggle = (k: string, on?: boolean) => setSel(s => { const n = new Set(s); (on ?? !n.has(k)) ? n.add(k) : n.delete(k); return n; });
  const render = (items: { v: Video; p: Pick }[]) =>
    api("/api/picks/render", { items: items.map(x => ({ job: x.v.job, n: x.p.n })) })
      .then((r: any) => { say(r.queued ? `${r.queued} clip${r.queued === 1 ? "" : "s"} queued to render` : "Already in the render queue"); setSel(new Set()); reload(); })
      .catch(e => say(e.message));
  const hide = (items: { v: Video; p: Pick }[], hidden: boolean) => {
    const by: Record<string, number[]> = {};
    items.forEach(x => (by[x.v.job] ||= []).push(x.p.n));
    Promise.all(Object.entries(by).map(([job, ns]) => api("/api/picks/hide", { job, ns, hidden })))
      .then(() => { say(hidden ? `Hid ${items.length}` : `Restored ${items.length}`); setSel(new Set()); reload(); }).catch(e => say(e.message));
  };
  const fetchVideo = (items: { v: Video; p: Pick }[]) =>
    api("/api/picks/render", { op: "fetch", items: items.map(x => ({ job: x.v.job, n: x.p.n })) })
      .then((r: any) => { say(r.queued ? `Downloading ${r.queued} video${r.queued === 1 ? "" : "s"} for preview` : "Already downloading"); reload(); }).catch(e => say(e.message));
  const missing = visible.filter(x => !x.p.preview && !busy(x.p));
  // The watch view keeps the list it opened with (hiding a pick doesn't reshuffle it) but reads live state for each one.
  const openWatch = (at?: { v: Video; p: Pick }) => {
    const keys = visible.map(x => key(x.v.job, x.p.n));
    setWatch({ keys, at: at ? Math.max(0, keys.indexOf(key(at.v.job, at.p.n))) : 0 });
  };
  const byKey = new Map(all.map(x => [key(x.v.job, x.p.n), x]));
  const witems: WItem[] = (watch?.keys || []).map(k => byKey.get(k)).filter((x): x is { v: Video; p: Pick } => !!x).map(x => ({ job: x.v.job, video: x.v.title, p: x.p }));
  const back = (w: WItem) => byKey.get(key(w.job, w.p.n))!;
  // Rank all: the groups on screen that Claude hasn't ranked yet (or whose ranking failed)
  const groupsOn = (d?.videos || []).filter(v => v.picks.some(SHOW[filter]) && v.picks.some(p => !p.hidden));
  const unranked = groupsOn.filter(v => !v.ranking || v.ranking.state === "failed");
  const ranking = (d?.videos || []).filter(v => v.ranking?.state === "running" || v.ranking?.state === "queued").length;
  const allDrops = all.filter(x => x.p.rank?.verdict === "drop" && !x.p.hidden && !busy(x.p) && !x.p.clips.length);
  const rankAll = () => api("/api/picks/rank", { jobs: unranked.map(v => v.job) })
    .then((r: any) => { say(`Claude is ranking ${r.started} group${r.started === 1 ? "" : "s"}, three at a time`); reload(); }).catch(e => say(e.message));
  const rankGroup = (v: Video) => api("/api/picks/rank", { job: v.job })
    .then(() => { say("Claude is ranking these picks (about 30 s)"); reload(); }).catch(e => say(e.message));
  const cancel = (v: Video, p: Pick) => api("/api/picks/cancel", { job: v.job, n: p.n }).then(() => { say("Taken out of the queue"); reload(); }).catch(e => say(e.message));

  return (
    <div className="desk">
      <Nav>{!!working.length && <span className="lv-rec"><i />{working.every(x => x.p.queue?.op === "fetch") ? "Downloading" : "Rendering"} · {working.length} left</span>}</Nav>
      <main className="dk-main">
        <div className="pk-bar">
          <div className="seg">
            {([["todo", "To render"], ["done", "Rendered"], ["all", "All"], ["hidden", "Hidden"]] as [Filter, string][]).map(([f, n]) =>
              <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{n} <span className="pk-n">{count(f)}</span></button>)}
          </div>
          <button className="pk-watch" disabled={!visible.length} onClick={() => openWatch()}>▶ Watch {visible.length}</button>
          {!!missing.length && <button className="btnx sm" onClick={() => fetchVideo(missing)} title="Some picks have no video on disk yet: download just their moments so they can be previewed">
            ⤓ Download {missing.length} missing</button>}
          <button className={"pk-rank" + (ranking ? " busy" : "")} disabled={!unranked.length}
            title="Claude ranks every group on this tab that isn't ranked yet, three groups at a time"
            onClick={rankAll}>{ranking ? <><i className="dk-pulse" />Ranking {ranking} group{ranking === 1 ? "" : "s"}…</>
              : unranked.length ? `✦ Rank all ${unranked.length} group${unranked.length === 1 ? "" : "s"}` : "✦ All ranked"}</button>
          {!!allDrops.length && <button className="btnx sm danger" title="Moves every pick Claude marked drop to the Hidden tab; you can restore them there"
            onClick={() => hide(allDrops, true)}>Hide all {allDrops.length} drops</button>}
          <span className="lv-grow" />
          {sel.size > 0 ? <>
            <span className="lv-sub">{sel.size} selected</span>
            <button className="btnx sm" onClick={() => setSel(new Set())}>Clear</button>
            {filter === "hidden" ? <button className="btnx sm" onClick={() => hide(chosen, false)}>Restore</button>
              : <button className="btnx sm" onClick={() => hide(chosen, true)}>Hide</button>}
            <button className="pk-go" onClick={() => render(chosen)}>Render {sel.size} clip{sel.size === 1 ? "" : "s"}</button>
          </> : <button className="btnx sm" disabled={!visible.length}
            onClick={() => setSel(new Set(visible.filter(x => !busy(x.p)).map(x => key(x.v.job, x.p.n))))}>Select all {visible.length}</button>}
        </div>

        {now && <RenderBanner all={all} r={d?.renders || null} fetching={working.filter(x => x.p.queue?.state === "fetching").length} />}

        {!d ? <div className="lv-none">Loading…</div> : !visible.length ? <div className="lv-none">
          {filter === "todo" ? <>Nothing left to render. Start an extraction on the <a href="/extract">Extract</a> page and Claude's picks land here.</>
            : filter === "done" ? "No rendered picks yet." : filter === "hidden" ? "No hidden picks." : "No picks yet."}
        </div> : d.videos.map(v => {
          const ps = v.picks.filter(SHOW[filter]).sort(byRank);
          if (!ps.length) return null;
          const free = ps.filter(p => !busy(p)), allOn = free.length > 0 && free.every(p => sel.has(key(v.job, p.n)));
          const rk = v.ranking, ranked = v.picks.filter(p => p.rank && !p.hidden);
          const drops = ranked.filter(p => p.rank!.verdict === "drop" && !busy(p) && !p.clips.length);
          const keeps = ps.filter(p => p.rank?.verdict === "keep" && !busy(p));
          const n = (vd: string) => ranked.filter(p => p.rank!.verdict === vd).length;
          return (
            <section key={v.job} className="dk-panel pk-video">
              <div className="pk-vh">
                {v.thumb ? <img src={v.thumb} alt="" /> : <div className="lv-noimg" />}
                <div className="pk-vt">
                  <b>{v.title}</b>
                  <span>{v.channel || ""}{v.minutes ? ` · ${v.minutes} min` : ""} · picked {ago(v.picked, d.now)} · {v.picks.filter(isDone).length} of {v.picks.length} rendered
                    {v.url.startsWith("http") && <> · <a href={v.url} target="_blank" rel="noreferrer">source ↗</a></>}</span>
                </div>
                <button className={"pk-rank" + (rk?.state === "running" || rk?.state === "queued" ? " busy" : "")} disabled={rk?.state === "running" || rk?.state === "queued"}
                  onClick={() => rankGroup(v)} title="Claude reads every pick's transcript, ranks them best first and marks the weak ones to drop">
                  {rk?.state === "running" ? <><i className="dk-pulse" />Claude is ranking…</> : rk?.state === "queued" ? "Waiting to rank…"
                    : rk?.state === "done" ? "✦ Re-rank" : "✦ Claude: rank these"}</button>
                <button className="btnx sm" disabled={!free.length} onClick={() => free.forEach(p => toggle(key(v.job, p.n), !allOn))}>{allOn ? "Unselect" : "Select"} all {free.length}</button>
              </div>
              {rk?.state === "failed" && <div className="note warn">Ranking failed: {rk.error || "unknown error"}</div>}
              {!!ranked.length && <div className="pk-rk">
                <span>Claude ranked {ranked.length} pick{ranked.length === 1 ? "" : "s"}{rk?.at ? ` ${ago(rk.at, d.now)}` : ""}:</span>
                <b className="v-keep">{n("keep")} keep</b><b className="v-maybe">{n("maybe")} maybe</b><b className="v-drop">{n("drop")} drop</b>
                <span className="lv-grow" />
                {!!keeps.length && <button className="btnx sm" onClick={() => keeps.forEach(p => toggle(key(v.job, p.n), true))}>Select {keeps.length} keep{keeps.length === 1 ? "" : "s"}</button>}
                {!!drops.length && <button className="btnx sm danger" onClick={() => hide(drops.map(p => ({ v, p })), true)}
                  title="Moves them to the Hidden tab; you can restore them there">Hide {drops.length} drop{drops.length === 1 ? "" : "s"}</button>}
              </div>}
              <div className="pk-grid">
                {ps.map(p => <PickCard key={p.n} v={v} p={p} on={sel.has(key(v.job, p.n))} onToggle={() => toggle(key(v.job, p.n))}
                  onRender={() => render([{ v, p }])} onHide={h => hide([{ v, p }], h)} onCancel={() => cancel(v, p)}
                  onWatch={() => openWatch({ v, p })} onFetch={() => fetchVideo([{ v, p }])}
                  prog={d.renders?.now.find(x => x.job === v.job && x.n === p.n) || null} />)}
              </div>
            </section>
          );
        })}
      </main>
      {watch && !!witems.length && <PickWatch items={witems} at={watch.at} onClose={() => setWatch(null)}
        selected={w => sel.has(key(w.job, w.p.n))} onToggle={w => toggle(key(w.job, w.p.n))}
        onRender={w => render([back(w)])} onHide={w => hide([back(w)], !w.p.hidden)} onFetch={w => fetchVideo([back(w)])} />}
      <Toast m={msg} />
    </div>
  );
}

// What "render" is doing right now (several clips at once), and how long the queue has left
function RenderBanner({ all, r, fetching }: { all: { v: Video; p: Pick }[]; r: Renders | null; fetching: number }) {
  const cur = (r?.now || []).map(x => ({ x, it: all.find(a => a.v.job === x.job && a.p.n === x.n) })).filter(y => y.it);
  const total = r ? r.done + r.queued + cur.length : 0;
  const overall = r && total ? (r.done + cur.reduce((a, y) => a + y.x.frac, 0)) / total : 0;
  return (
    <div className="dk-panel pk-now">
      <div className="pk-nowh"><i className="dk-pulse" />
        <b>{cur.length ? `Rendering ${cur.length} clip${cur.length === 1 ? "" : "s"} at once` : "Downloading"}</b>
        {!!fetching && <span className="lv-sub">· downloading {fetching} video{fetching === 1 ? "" : "s"}</span>}
      </div>
      {cur.map(({ x, it }) => <div key={`${x.job}:${x.n}`} className="pk-nowi">
        <div className="pk-nowt"><b>{it!.p.title || it!.p.tag}</b><span>{it!.v.title}</span></div>
        <div className="dk-bar"><i style={{ width: `${x.frac * 100}%` }} /></div>
        <div className="dk-pl"><b>{Math.floor(x.frac * 100)}%</b><span>{x.label}</span><span className="lv-grow" /><span>~{dur(x.left)} left</span></div>
      </div>)}
      {r && total > 1 && <div className="pk-nowi">
        <div className="dk-bar big"><i style={{ width: `${overall * 100}%` }} /></div>
        <div className="dk-pl"><b>{r.done} of {total} clips rendered</b><span>{r.queued} waiting · {r.par} at a time · ~{dur(r.per)} each</span>
          <span className="lv-grow" /><span>~{dur(r.left)} left for all</span></div>
      </div>}
    </div>
  );
}

function PickCard({ v, p, on, onToggle, onRender, onHide, onCancel, onWatch, onFetch, prog }: {
  v: Video; p: Pick; on: boolean; onToggle: () => void; onRender: () => void; onHide: (h: boolean) => void; onCancel: () => void;
  onWatch: () => void; onFetch: () => void; prog: RNow | null;
}) {
  const [noThumb, setNoThumb] = useState(false);
  const q = p.queue, b = busy(p), clip = p.clips[0];
  const dur = Math.round(p.end - p.start);
  return (
    <div className={"pk-card" + (on ? " on" : "") + (b ? " busy" : "") + (p.rank?.verdict === "drop" && !on ? " dropped" : "")}>
      <button className="pk-thumb" onClick={p.preview ? onWatch : undefined} title={p.preview ? "Watch it" : ""}>
        {p.preview && !noThumb ? <img loading="lazy" alt="" src={`/api/pick/thumb?job=${encodeURIComponent(v.job)}&n=${p.n}`} onError={() => setNoThumb(true)} />
          : <div className="pk-nothumb" />}
        {p.preview ? <span className="pk-playbtn">▶</span>
          : q?.state === "fetching" || q?.state === "queued" ? <span className="pk-missing">Downloading…</span>
          : <span className="pk-missing" onClick={e => { e.stopPropagation(); onFetch(); }}>⤓ Download to preview</span>}
        <span className="pk-dur">{dur} s</span>
        {p.rank && <span className={"pk-rkb v-" + p.rank.verdict}>#{p.rank.rank} · {VERDICT[p.rank.verdict]}</span>}
        {clip && <span className="pk-done" style={p.rank ? { top: 32 } : undefined}>✓ rendered</span>}
      </button>
      <div className="pk-ch" onClick={() => !b && onToggle()}>
        <span className={"pk-check" + (on ? " on" : "")}>{on ? "✓" : ""}</span>
        <span className="dk-tag">{p.tag || `#${p.n}`}</span>
        <span className="lv-sub">{mmss(p.start)}–{mmss(p.end)}</span>
        <span className="lv-grow" />
        {!clip && q && q.state !== "done" && <span className={"lv-chip" + (q.state === "failed" ? " hot" : "")} title={q.error || ""}>{STATE[q.state]}</span>}
      </div>
      {prog && <div className="pk-prog"><div className="dk-bar"><i style={{ width: `${prog.frac * 100}%` }} /></div>
        <span>{Math.floor(prog.frac * 100)}% · {prog.label}</span></div>}
      <div className="pk-title">{p.title || "untitled"}</div>
      {p.rank?.why && <div className={"pk-why v-" + p.rank.verdict}>✦ {p.rank.why}</div>}
      {p.text && <div className="pk-quote">“{p.text}”</div>}
      {q?.state === "failed" && q.error && !clip && <div className="note warn">{q.error}</div>}
      <div className="pk-acts">
        {q?.state === "queued" ? <button className="btnx sm" onClick={onCancel}>Cancel</button>
          : !b && <button className="btnx sm" onClick={onRender}>{clip ? "Render again" : q?.state === "failed" ? "↻ Retry" : "Render"}</button>}
        {clip && <a className="btnx sm" href={editUrl(v.job, clip.name)} target="_blank" rel="noreferrer">Open clip</a>}
        <span className="lv-grow" />
        {!b && <button className="btnx sm ghost" onClick={() => onHide(!p.hidden)}>{p.hidden ? "Restore" : "Hide"}</button>}
      </div>
    </div>
  );
}
