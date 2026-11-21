"use client";
// Home: what's running (Extract, Live), the posting queue at a glance, and the latest clips.
import { useEffect, useRef } from "react";
import { ago, editUrl, usePoll } from "@/lib/desk";
import Nav from "./Nav";

interface Clip { id: string; job: string; name: string; title: string; creator: string | null; video: string; updated: number; layout: string | null }
interface Run { run: string; stage: string; active: boolean; started: number; urls: string[]; jobs: { title?: string; stage: string; picks: { clip: string | null }[] }[] }
interface HomeData {
  live: { running: boolean; streams: string[]; clips: number };
  extract: Run | null;
  queue: { to_post: number; total?: number; next: string | null; last_post: number | null; posted_24h: number };
  recent: Clip[]; now: number;
  picks: { to_render: number; rendering: number } | null;
}

const STAGE: Record<string, string> = { starting: "Starting", pipeline: "Transcribing and scoring", picking: "Claude is picking moments", rendering: "Rendering clips", done: "Done", stopped: "Stopped", error: "Failed" };

export default function Home() {
  useEffect(() => {   // old links (/?job=…) open the editor
    if (new URLSearchParams(location.search).has("job")) location.replace("/edit" + location.search);
  }, []);
  const { data: d } = usePoll<HomeData>("/api/home", 3000);
  const ex = d?.extract, q = d?.queue, live = d?.live;
  const clips = ex ? ex.jobs.reduce((n, j) => n + j.picks.filter(p => p.clip).length, 0) : 0;

  return (
    <div className="desk">
      <Nav />
      <main className="dk-main">
        <div className="dk-cards">
          <a className="dk-card" href="/extract">
            <div className="dk-ct"><span className="dk-ico">⤓</span>Extract from YouTube</div>
            {ex?.active ? <div className="dk-big"><i className="dk-pulse" />{STAGE[ex.stage] || ex.stage}</div>
              : <div className="dk-big">{ex ? `Last run: ${clips} clip${clips === 1 ? "" : "s"}` : "No runs yet"}</div>}
            <div className="dk-sub">{ex ? `${ex.urls.length} video${ex.urls.length === 1 ? "" : "s"} · ${ago(ex.started, d?.now)}` : "Paste a video or pick a trending one; Claude finds the moments and cuts them."}</div>
            <span className="dk-go">{ex?.active ? "Watch progress →" : "New extraction →"}</span>
          </a>
          <a className="dk-card" href="/picks">
            <div className="dk-ct"><span className="dk-ico">✓</span>Picks</div>
            <div className="dk-big">{d?.picks?.rendering ? <><i className="dk-pulse" />Rendering {d.picks.rendering}</> : d?.picks ? `${d.picks.to_render} to render` : "…"}</div>
            <div className="dk-sub">Every moment Claude picked. Tick the ones worth posting and render them together.</div>
            <span className="dk-go">Choose what to render →</span>
          </a>
          <a className="dk-card" href="/live">
            <div className="dk-ct"><span className="dk-ico red">●</span>Live</div>
            <div className="dk-big">{live?.running ? <><i className="dk-pulse" />Watching {live.streams.length} stream{live.streams.length === 1 ? "" : "s"}</> : "Not watching"}</div>
            <div className="dk-sub">{live?.running ? live.streams.join(", ") : `${live?.clips ?? 0} live clips so far. Clips cut themselves when chat explodes.`}</div>
            <span className="dk-go">{live?.running ? "Open the split screen →" : "Watch streams →"}</span>
          </a>
          <a className="dk-card" href="/queue">
            <div className="dk-ct"><span className="dk-ico">☰</span>Queue</div>
            <div className="dk-big">{q ? `${q.to_post} to post` : "…"}</div>
            <div className="dk-sub">{q?.next ? <>Next: <b>{q.next}</b><br /></> : null}{q ? `${q.posted_24h} posts in 24 h · last ${ago(q.last_post, d?.now)}` : ""}</div>
            <span className="dk-go">Open the queue →</span>
          </a>
        </div>
        <div className="dk-h">Latest clips <a href="/queue?tab=other">all clips →</a></div>
        <div className="dk-grid">
          {d?.recent.map(c => <ClipTile key={c.id} c={c} now={d.now} />)}
          {d && !d.recent.length && <div className="lv-none">No clips yet. Start an extraction or watch a live stream.</div>}
        </div>
      </main>
    </div>
  );
}

export function ClipTile({ c, now, children }: { c: Clip; now?: number; children?: React.ReactNode }) {
  const v = useRef<HTMLVideoElement>(null);
  return (
    <div className="dk-tile">
      <a href={editUrl(c.job, c.name)} className="dk-vid" title="Open in the editor"
        onMouseEnter={() => v.current?.play().catch(() => {})} onMouseLeave={() => { if (v.current) { v.current.pause(); v.current.currentTime = 0.5; } }}>
        <video ref={v} src={c.video + "#t=0.5"} muted playsInline preload="metadata" loop />
      </a>
      <div className="dk-tt">{c.title}</div>
      <div className="dk-ts">{c.creator || c.job} · {ago(c.updated, now)}</div>
      {children}
    </div>
  );
}
