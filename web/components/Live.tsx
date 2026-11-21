"use client";
// Live: watch several streams at once (split screen), see each chat's hype level, and clip.
// watch.py does the recording and the chat-spike clipping; this page starts it, plays its live preview
// (the same recording the clips are cut from), sends "Clip now" and lists every clip with an Edit link.
import type Hls from "hls.js";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/util";
import { editUrl } from "@/lib/desk";
import Nav from "./Nav";

type Point = [number, number, number | null];   // wall time, msgs/s, x normal (null while learning)
interface LStream {
  tag: string; url: string; meta: { name?: string; title?: string; viewers?: number } | null; alive: boolean; removed?: boolean; status: string;
  spikes: number; clips: string[]; since: number; series: Point[]; hls: string | null;
}
interface LStatus { pid: number; started: number; end_at: number; updated: number; ratio: number; min_chatters: number; streams: LStream[] }
interface LClip {
  job: string; channel: string; wall: number; words: string[]; ratio: number | null; rate: number | null; manual: boolean;
  how: string | null; dur: number | null; video: string; hook: number | null; pick: number; edits: string[];
}
interface LiveResp { running: boolean; starting: boolean; status: LStatus | null; log: string[]; clips: LClip[]; now: number; exit_code: number | null }
interface Top { login: string; name: string; url: string; viewers: number; title: string; preview: string | null; lang?: string }

// x the usual number of people chatting, held for 3 s (watch.py Detector)
const SENS: [number, string, string][] = [[2.5, "Sensitive", "more clips, more misses"], [3, "Normal", "3× the usual people chatting, for 3+ s"], [4, "Big moments", "only when chat explodes"]];
const DURS: [number, string][] = [[30, "30 min"], [60, "1 h"], [120, "2 h"], [240, "4 h"]];
const hhmm = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const left = (s: number) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h} h ${m} min` : `${m} min`; };
const kfmt = (n: number) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "K" : String(n));
const thumb = (p: string | null) => (p ? p.replace("{width}", "320").replace("{height}", "180") : "");
/** Columns and tile size that make n 16:9 tiles as big as possible in a w x h box. */
function fit(n: number, w: number, h: number, gap = 10) {
  let best = { cols: 1, tw: 0, th: 0 };
  for (let c = 1; c <= Math.max(1, n); c++) {
    const r = Math.ceil(Math.max(1, n) / c), cw = (w - gap * (c - 1)) / c, ch = (h - gap * (r - 1)) / r;
    const tw = Math.floor(Math.min(cw, (ch * 16) / 9));
    if (tw > best.tw) best = { cols: c, tw, th: Math.floor((tw * 9) / 16) };
  }
  return best;
}

export default function Live() {
  const [d, setD] = useState<LiveResp | null>(null);
  const [top, setTop] = useState<Top[] | null>(null);
  const [audio, setAudio] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const [hit, setHit] = useState<Record<string, number>>({});
  const latency = useRef<Record<string, () => number>>({});
  const seenClips = useRef<Set<string> | null>(null), spikes = useRef<Record<string, number>>({});
  const wall = useRef<HTMLDivElement>(null), [box, setBox] = useState<[number, number]>([0, 0]);
  useEffect(() => {   // measure the wall so tiles can be sized to 16:9 without black bars
    const el = wall.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setBox([e.contentRect.width, e.contentRect.height])); ro.observe(el);
    return () => ro.disconnect();
  }, [!!d?.status]);   // eslint-disable-line react-hooks/exhaustive-deps

  const say = (t: string) => setMsg({ t, id: Date.now() });

  useEffect(() => {   // poll the watcher
    let stop = false;
    const tick = async () => {
      try {
        const r = await api<LiveResp>("/api/live"); if (stop) return;
        if (seenClips.current) for (const c of r.clips) if (!seenClips.current.has(c.job)) say(`New clip · ${c.channel}${c.words.length ? ` · “${c.words.slice(0, 3).join(", ")}”` : ""}`);
        seenClips.current = new Set(r.clips.map(c => c.job));
        for (const s of r.status?.streams || []) {   // flash a tile when its chat spikes (or it was clipped by hand)
          if (spikes.current[s.tag] != null && s.spikes > spikes.current[s.tag]) setHit(h => ({ ...h, [s.tag]: Date.now() }));
          spikes.current[s.tag] = s.spikes;
        }
        setD(r);
      } catch { /* editor.py restarting */ }
      if (!stop) setTimeout(tick, 1000);
    };
    tick();
    return () => { stop = true; };
  }, []);
  useEffect(() => { api<Top[]>("/api/live/top").then(setTop).catch(() => setTop([])); }, []);

  const st = d?.status, streams = (st?.streams || []).filter(s => !s.removed);
  const g = fit(streams.length, box[0], box[1]);
  const clipNow = (tag: string) => {
    const behind = latency.current[tag]?.() ?? 6;
    api("/api/live/cmd", { do: "clip", tag, behind: +behind.toFixed(2) }).then(() => say(`Clipping ${tag}: ready in ~20 s`)).catch(e => say(e.message));
  };
  const cmd = (body: Record<string, unknown>, ok?: string) => api("/api/live/cmd", body).then(() => ok && say(ok)).catch(e => say(e.message));

  useEffect(() => {   // 1–9 clips that tile
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setAdding(false); return; }
      if (/INPUT|TEXTAREA|SELECT/.test((document.activeElement as HTMLElement)?.tagName || "") || e.metaKey || e.ctrlKey) return;
      const n = +e.key; if (n >= 1 && n <= 9 && streams[n - 1]) { e.preventDefault(); clipNow(streams[n - 1].tag); }
    };
    document.addEventListener("keydown", key); return () => document.removeEventListener("keydown", key);
  });

  return (
    <div id="live">
      <Nav>
        {st ? (<>
          <span className="lv-rec"><i />Watching {streams.length} stream{streams.length === 1 ? "" : "s"}</span>
          <span className="lv-sub">ends in {left(st.end_at - (d?.now || 0))} · clips when {st.ratio}× the usual people chat for 3+ s · keys 1–{Math.min(9, Math.max(1, streams.length))} clip a stream</span>
          <span className="lv-grow" />
          <button className="btnx sm" onClick={() => setAdding(a => !a)}>＋ Add stream</button>
          <button className="btnx sm" onClick={() => cmd({ do: "extend", minutes: Math.round((st.end_at - (d?.now || 0)) / 60) + 60 }, "Extended by 1 hour")}>＋1 h</button>
          <StopButton onStop={() => cmd({ do: "stop" }, "Stopping: finishing pending clips")} />
        </>) : <span className="lv-sub">{d?.starting ? "Starting: finding the streams and connecting…" : "Watch several streams at once. Clips are cut automatically when chat explodes, or by hand."}</span>}
      </Nav>
      <div className="lv-body">
        <main className="lv-main">
          {st ? (
            <div className="lv-wall" ref={wall} style={box[0] ? { gridTemplateColumns: `repeat(${g.cols}, ${g.tw}px)`, gridAutoRows: `${g.th}px` } : undefined}>
              {streams.map((s, i) => (
                <Tile key={s.tag} s={s} i={i} ratio={st.ratio} top={top} audio={audio === s.tag} hit={Date.now() - (hit[s.tag] || 0) < 2600}
                  onAudio={() => setAudio(a => (a === s.tag ? null : s.tag))} onClip={() => clipNow(s.tag)}
                  onRemove={() => cmd({ do: "remove", tag: s.tag }, s.alive ? `Stopped watching ${s.tag}` : undefined)}
                  onRetry={() => cmd({ do: "add", url: s.url, meta: s.meta || undefined }, `Reconnecting ${s.tag}…`)}
                  onLatency={fn => { if (fn) latency.current[s.tag] = fn; else delete latency.current[s.tag]; }} />
              ))}
              {!streams.length && <div className="lv-empty">No streams left. Add one, or this stops in a few seconds.</div>}
            </div>
          ) : d?.starting ? <Starting log={d.log} /> : <Setup top={top} log={d?.exit_code ? d.log : []} onStarted={() => say("Starting…")} />}
          {st && adding && <AddPanel top={top} watching={streams.map(s => s.url)} onClose={() => setAdding(false)}
            onAdd={(url, meta) => cmd({ do: "add", url, meta }, `Adding ${meta?.name || url}…`)} />}
        </main>
        <aside className="lv-clips">
          <div className="lv-h">Clips <span>{d?.clips.length || 0}</span></div>
          <div className="lv-feed">
            {d?.clips.length ? d.clips.map(c => <ClipCard key={c.job} c={c} />) : <div className="lv-none">Clips show up here as they're cut. Each one opens in the editor with the moment set as its hook.</div>}
          </div>
        </aside>
      </div>
      <Toast m={msg} />
    </div>
  );
}

function Tile({ s, i, ratio, top, audio, hit, onAudio, onClip, onRemove, onRetry, onLatency }: {
  s: LStream; i: number; ratio: number; top: Top[] | null; audio: boolean; hit: boolean;
  onAudio: () => void; onClip: () => void; onRemove: () => void; onRetry: () => void; onLatency: (fn: (() => number) | null) => void;
}) {
  const t = top?.find(x => x.url === s.url);
  const name = s.meta?.name || t?.name || s.tag, viewers = s.meta?.viewers || t?.viewers;
  const last = s.series[s.series.length - 1], hot = last?.[2] != null && last[2] >= ratio;
  return (
    <div className={"lv-tile" + (hit ? " hit" : "") + (hot ? " hot" : "")}>
      {s.alive && s.hls ? <LivePlayer src={s.hls} muted={!audio} onLatency={onLatency} />
        : <div className="lv-wait">{s.alive ? (s.status === "reconnecting…" ? "Reconnecting…" : "Connecting to the stream…")
          : <div><div>Not recording: {s.status.replace(/^not recording: /, "")}</div><button className="btnx sm" style={{ marginTop: 10 }} onClick={onRetry}>Retry</button></div>}</div>}
      <div className="lv-ov lv-ovtop">
        <span className="lv-name"><i />{name}</span>
        {!!viewers && <span className="lv-chip">{kfmt(viewers)} watching</span>}
        <span className="lv-grow" />
        <span className={"lv-chip" + (hot ? " hot" : "")}>{s.status}</span>
      </div>
      {hit && <div className="lv-flash">✂ CLIP</div>}
      <div className="lv-ov lv-ovbot">
        <ChatGraph series={s.series} ratio={ratio} />
        <div className="lv-act">
          <button className="lv-ib" title={audio ? "Mute" : "Listen to this stream (mutes the others)"} onClick={onAudio}>{audio ? "🔊" : "🔇"}</button>
          <span className="lv-count">{s.clips.length} clip{s.clips.length === 1 ? "" : "s"}</span>
          <span className="lv-grow" />
          <button className="lv-ib" title="Stop watching this stream" onClick={onRemove}>✕</button>
          <button className="lv-clipbtn" disabled={!s.alive || !s.hls} onClick={onClip} title="Clip what you just saw (the last ~20 s)">✂ Clip now <kbd>{i + 1}</kbd></button>
        </div>
      </div>
    </div>
  );
}

/** Plays watch.py's live preview playlist. Reports how far behind live it is, so Clip now cuts what you saw. */
function LivePlayer({ src, muted, onLatency }: { src: string; muted: boolean; onLatency: (fn: (() => number) | null) => void }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const v = ref.current!; let hls: Hls | null = null, alive = true, timer: ReturnType<typeof setTimeout> | undefined;
    (async () => {
      const H = (await import("hls.js")).default; if (!alive) return;
      if (H.isSupported()) {
        hls = new H({ liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5, maxLiveSyncPlaybackRate: 1.3, backBufferLength: 10 });
        hls.on(H.Events.ERROR, (_e, data) => {
          if (!data.fatal) return;
          if (data.type === H.ErrorTypes.MEDIA_ERROR) hls?.recoverMediaError();
          else timer = setTimeout(() => setRetry(r => r + 1), 2500);   // playlist not there yet / stream hiccup: reload
        });
        hls.loadSource(src); hls.attachMedia(v);
        onLatency(() => (hls && Number.isFinite(hls.latency) ? hls.latency : 6));
      } else { v.src = src; onLatency(() => 6); }
      v.play().catch(() => {});
    })();
    return () => { alive = false; clearTimeout(timer); hls?.destroy(); onLatency(null); };
  }, [src, retry]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (ref.current) { ref.current.muted = muted; if (!muted) ref.current.play().catch(() => {}); } }, [muted]);
  return <video ref={ref} className="lv-video" muted playsInline autoPlay />;
}

/** The last 3 minutes of chat: how many times its normal rate (dashed line = the clip threshold). */
function ChatGraph({ series, ratio }: { series: Point[]; ratio: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current; if (!cv) return;
    const dpr = devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d")!; g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
    if (series.length < 2) return;
    const learning = series.every(p => p[2] == null);
    const val = (p: Point) => (learning ? p[1] : p[2] ?? 0);
    const now = series[series.length - 1][0], span = 180;
    const max = Math.max(learning ? 1 : ratio * 1.35, ...series.map(val));
    const X = (t: number) => w - ((now - t) / span) * w, Y = (v: number) => h - 2 - (v / max) * (h - 6);
    g.beginPath(); g.moveTo(X(series[0][0]), h);
    for (const p of series) g.lineTo(X(p[0]), Y(val(p)));
    g.lineTo(w, h); g.closePath(); g.fillStyle = "rgba(201,242,110,.16)"; g.fill();
    g.lineWidth = 1.6;
    for (let k = 1; k < series.length; k++) {
      const a = series[k - 1], b = series[k];
      g.strokeStyle = !learning && val(b) >= ratio ? "#ff6b6b" : "#c9f26e";
      g.beginPath(); g.moveTo(X(a[0]), Y(val(a))); g.lineTo(X(b[0]), Y(val(b))); g.stroke();
    }
    if (!learning) {
      g.setLineDash([4, 4]); g.strokeStyle = "rgba(255,255,255,.45)"; g.lineWidth = 1;
      g.beginPath(); g.moveTo(0, Y(ratio)); g.lineTo(w, Y(ratio)); g.stroke(); g.setLineDash([]);
    }
  }, [series, ratio]);
  return <canvas ref={ref} className="lv-graph" title="Chat rate, last 3 minutes. Dashed line: a clip is cut when chat crosses it" />;
}

function ClipCard({ c }: { c: LClip }) {
  const v = useRef<HTMLVideoElement>(null);
  const at = Math.max(0, (c.hook ?? 0) - 3);
  return (
    <div className="lv-card">
      <video ref={v} src={`${c.video}#t=${at}`} muted playsInline preload="metadata"
        onMouseEnter={() => { if (v.current) { v.current.currentTime = at; v.current.play().catch(() => {}); } }}
        onMouseLeave={() => v.current?.pause()} />
      <div className="lv-ci">
        <div className="lv-cl"><b>{c.channel}</b><span>{hhmm(c.wall)}</span>
          <span className={"lv-chip" + (c.manual ? "" : " hot")}>{c.manual ? "by hand" : `${c.ratio}× chat`}</span></div>
        {!!c.words.length && <div className="lv-words">“{c.words.join(", ")}”</div>}
        <div className="lv-cl">
          <a className="btnx sm" href={editUrl(c.job, null, c.pick)} target="_blank" rel="noreferrer">Edit</a>
          {c.edits.length ? <span className="lv-done">✓ {c.edits.length} cut</span> : <span className="lv-mute">{c.dur ? `${Math.round(c.dur)} s raw` : ""}</span>}
        </div>
      </div>
    </div>
  );
}

function StreamPicker({ top, picked, onToggle, exclude = [] }: { top: Top[] | null; picked: string[]; onToggle: (t: Top) => void; exclude?: string[] }) {
  const [lang, setLang] = useState<string>(() => { try { return localStorage.getItem("live:lang") || "EN"; } catch { return "EN"; } });
  const pick = (l: string) => { setLang(l); try { localStorage.setItem("live:lang", l); } catch { /* private mode */ } };
  if (top === null) return <div className="lv-none">Finding GTA 6 streams on Twitch…</div>;
  const langs = [...new Set(top.map(t => t.lang).filter(Boolean))] as string[];
  const rows = top.filter(t => !exclude.includes(t.url) && (lang === "ALL" || t.lang === lang || picked.includes(t.url)));
  return (<>
    {langs.length > 1 && <div className="lv-langs">{["EN", ...langs.filter(l => l !== "EN"), "ALL"].map(l =>
      <button key={l} className={lang === l ? "on" : ""} onClick={() => pick(l)}>{l === "ALL" ? "All languages" : l === "EN" ? "English" : l}
        <span>{l === "ALL" ? top.length : top.filter(t => t.lang === l).length}</span></button>)}</div>}
    {!rows.length ? <div className="lv-none">No {lang === "ALL" ? "" : lang + " "}GTA 6 streams on Twitch right now. Pick another language or paste a URL below.</div> :
    <div className="lv-pick">
      {rows.map(t => (
        <button key={t.url} className={"lv-opt" + (picked.includes(t.url) ? " on" : "")} onClick={() => onToggle(t)}>
          {t.preview ? <img src={thumb(t.preview)} alt="" /> : <div className="lv-noimg" />}
          <div><b>{t.name}</b><span>{kfmt(t.viewers)} watching{t.lang ? ` · ${t.lang}` : ""}</span><em>{t.title}</em></div>
        </button>
      ))}
    </div>}
  </>);
}

function Setup({ top, log, onStarted }: { top: Top[] | null; log: string[]; onStarted: () => void }) {
  const [picked, setPicked] = useState<string[]>([]), [extra, setExtra] = useState("");
  const [ratio, setRatio] = useState(3), [minutes, setMinutes] = useState(120);
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const urls = [...picked, ...extra.split(/[\s,]+/).filter(Boolean)];
  const start = async () => {
    setBusy(true); setErr("");
    try { await api("/api/live/start", { urls, ratio, minutes }); onStarted(); } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };
  return (
    <div className="lv-setup">
      <div className="field"><label>Top GTA 6 streams on Twitch right now · pick up to 9</label>
        <StreamPicker top={top} picked={picked} onToggle={t => setPicked(p => (p.includes(t.url) ? p.filter(x => x !== t.url) : p.length < 9 ? [...p, t.url] : p))} />
      </div>
      <div className="field"><label>Other streams · Twitch or YouTube live URLs, or Twitch names</label>
        <textarea className="inp" rows={2} value={extra} onChange={e => setExtra(e.target.value)} placeholder="https://twitch.tv/kaicenat   https://www.youtube.com/@IShowSpeed/live" />
      </div>
      <div className="lv-row">
        <div className="field"><label>Auto-clip sensitivity</label><div className="seg">
          {SENS.map(([v, n, h]) => <button key={v} className={ratio === v ? "on" : ""} title={h} onClick={() => setRatio(v)}>{n}</button>)}
        </div></div>
        <div className="field"><label>Watch for</label><div className="seg">
          {DURS.map(([v, n]) => <button key={v} className={minutes === v ? "on" : ""} onClick={() => setMinutes(v)}>{n}</button>)}
        </div></div>
      </div>
      {err && <div className="note warn">{err}</div>}
      {!!log.length && <div className="field"><label>Last run ended with an error</label><div className="log">{log.join("\n")}</div></div>}
      <button className="lv-start" disabled={!urls.length || urls.length > 9 || busy} onClick={start}>
        {busy ? "Starting…" : urls.length ? `Watch ${urls.length} stream${urls.length === 1 ? "" : "s"}` : "Pick streams to watch"}
      </button>
      <div className="note">Each stream keeps a rolling 60 s recording. When {ratio}× the usual number of people start chatting at once and it lasts 3+ seconds, the last ~20 s become a clip with a transcript and the moment set as the hook. Nothing is saved except the clips.</div>
    </div>
  );
}

function AddPanel({ top, watching, onAdd, onClose }: { top: Top[] | null; watching: string[]; onAdd: (url: string, meta?: Partial<Top>) => void; onClose: () => void }) {
  const [url, setUrl] = useState("");
  return (
    <div className="lv-add">
      <div className="ph">Add a stream<button onClick={onClose}>✕</button></div>
      <StreamPicker top={top} picked={[]} exclude={watching} onToggle={t => { onAdd(t.url, { name: t.name, title: t.title, viewers: t.viewers }); onClose(); }} />
      <div className="lv-row" style={{ marginTop: 10 }}>
        <input className="inp" value={url} onChange={e => setUrl(e.target.value)} placeholder="twitch.tv/name or a YouTube live URL"
          onKeyDown={e => { if (e.key === "Enter" && url.trim()) { onAdd(url.trim()); onClose(); } }} />
        <button className="btnx" disabled={!url.trim()} onClick={() => { onAdd(url.trim()); onClose(); }}>Add</button>
      </div>
    </div>
  );
}

function Starting({ log }: { log: string[] }) {
  return <div className="lv-setup"><div className="lv-none">Starting the recorder: finding the streams and connecting to chat…</div>{!!log.length && <div className="log">{log.join("\n")}</div>}</div>;
}

function StopButton({ onStop }: { onStop: () => void }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => { if (!armed) return; const h = setTimeout(() => setArmed(false), 3000); return () => clearTimeout(h); }, [armed]);
  return <button className={"btnx sm" + (armed ? " danger" : "")} onClick={() => (armed ? (setArmed(false), onStop()) : setArmed(true))}>{armed ? "Click again to stop" : "■ Stop"}</button>;
}

function Toast({ m }: { m: { t: string; id: number } | null }) {
  const [show, setShow] = useState(false);
  useEffect(() => { if (!m) return; setShow(true); const h = setTimeout(() => setShow(false), 2600); return () => clearTimeout(h); }, [m]);
  return <div className={"toast" + (show ? " show" : "")}>{m?.t}</div>;
}
