"use client";
// Watch view for the Picks page: plays each pick the way the finished clip will roughly look (9:16, blurred
// background, title box, word-by-word captions, hook first) straight from the source video: nothing is rendered.
// Keys: ←/→ previous/next · space play/pause · S select for render · H hide · Esc close.
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/util";
import { editUrl } from "@/lib/desk";

export interface WPick {
  n: number; tag: string; title: string; titles: string[]; start: number; end: number; text: string;
  preview: string | null; offset: number; tease: [number, number] | null; clips: { name: string }[];
  queue: { state: string; error?: string } | null; hidden: boolean;
  rank: { rank: number; verdict: string; why: string } | null;
}
export interface WItem { job: string; video: string; p: WPick }
interface Word { w: string; s: number; e: number }

// Same grouping as cut.py: 2 words (3 if tiny), break on punctuation and pauses; a group stays up until the next one.
function groups(ws: Word[]) {
  const out: { ws: Word[]; end: number }[] = [];
  let cur: Word[] = [];
  for (const w of ws) {
    const chars = cur.reduce((a, x) => a + x.w.length, 0) + w.w.length;
    const full = cur.length >= 3 || (cur.length === 2 && chars > 11);
    if (cur.length && (full || w.s - cur[cur.length - 1].e > 0.35 || /[.?!,]$/.test(cur[cur.length - 1].w))) { out.push({ ws: cur, end: 0 }); cur = []; }
    cur.push(w);
  }
  if (cur.length) out.push({ ws: cur, end: 0 });
  out.forEach((g, i) => {
    const last = g.ws[g.ws.length - 1], nxt = out[i + 1]?.ws[0].s;
    g.end = nxt != null && nxt - last.e < 0.5 ? nxt : last.e + 0.25;
  });
  return out;
}

export default function PickWatch({ items, at, selected, onToggle, onRender, onHide, onFetch, onClose }: {
  items: WItem[]; at: number; selected: (it: WItem) => boolean;
  onToggle: (it: WItem) => void; onRender: (it: WItem) => void; onHide: (it: WItem) => void; onFetch: (it: WItem) => void; onClose: () => void;
}) {
  const [i, setI] = useState(at);
  const [hookFirst, setHookFirst] = useState(true), [autoNext, setAutoNext] = useState(true);
  const [words, setWords] = useState<Word[]>([]);
  const [t, setT] = useState(0), [seg, setSeg] = useState(0), [playing, setPlaying] = useState(false), [ended, setEnded] = useState(false);
  const fg = useRef<HTMLVideoElement>(null), bg = useRef<HTMLVideoElement>(null);
  const it = items[Math.min(i, items.length - 1)];
  const p = it?.p;
  const segs: [number, number][] = !p ? [] : hookFirst && p.tease && p.tease[1] - p.tease[0] <= 4 ? [p.tease, [p.start, p.end]] : [[p.start, p.end]];
  const segsRef = useRef(segs); segsRef.current = segs;
  const segRef = useRef(0); segRef.current = seg;
  const go = (d: number) => setI(x => Math.max(0, Math.min(items.length - 1, x + d)));

  // new pick: words, then play from the first segment
  useEffect(() => {
    if (!p) return;
    setWords([]); setSeg(0); setEnded(false);
    api(`/api/pick/words?job=${encodeURIComponent(it.job)}&n=${p.n}`).then(r => setWords(r.words || [])).catch(() => {});
  }, [it?.job, p?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  const seek = (src: number) => {
    const v = fg.current; if (!v || !p) return;
    v.currentTime = Math.max(0, src - p.offset);
    if (bg.current) bg.current.currentTime = v.currentTime;
  };
  const start = () => { setSeg(0); setEnded(false); seek(segsRef.current[0]?.[0] ?? 0); fg.current?.play().catch(() => {}); };

  // follow playback: segment ends, captions clock, background in sync
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = fg.current;
      if (v && p) {
        const src = v.currentTime + p.offset;
        setT(src);
        const sg = segsRef.current[segRef.current];
        if (sg && !v.paused && src >= sg[1]) {
          if (segRef.current + 1 < segsRef.current.length) { setSeg(segRef.current + 1); segRef.current += 1; seek(segsRef.current[segRef.current][0]); }
          else { v.pause(); setEnded(true); if (autoNext && i < items.length - 1) setTimeout(() => go(1), 700); }
        }
        const b = bg.current;
        if (b) { if (Math.abs(b.currentTime - v.currentTime) > 0.3) b.currentTime = v.currentTime; if (v.paused !== b.paused) (v.paused ? b.pause() : b.play().catch(() => {})); }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [p?.n, it?.job, autoNext, i, items.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === " ") { e.preventDefault(); const v = fg.current; if (v) ended ? start() : v.paused ? v.play() : v.pause(); }
      else if (e.key.toLowerCase() === "s" && it) onToggle(it);
      else if (e.key.toLowerCase() === "h" && it) { onHide(it); go(1); }
      else return;
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }); // re-bound each render: always sees the current pick

  if (!it || !p) return null;
  const gs = groups(words.filter(w => w.s >= (segs[seg]?.[0] ?? 0) - 0.05 && w.s < (segs[seg]?.[1] ?? 0)));
  const g = gs.find(x => x.ws[0].s <= t && t < x.end);
  const k = g ? Math.max(0, g.ws.findIndex((w, j) => t < (g.ws[j + 1]?.s ?? g.end)) ) : -1;
  const len = Math.round(p.end - p.start), on = selected(it), busy = !!p.queue && ["queued", "fetching", "rendering"].includes(p.queue.state);
  const inHook = segs.length > 1 && seg === 0;
  const prog = segs.length ? Math.min(1, Math.max(0, (t - segs[segs.length - 1][0]) / (segs[segs.length - 1][1] - segs[segs.length - 1][0]))) : 0;

  return (
    <div className="pw" onClick={onClose}>
      <div className="pw-in" onClick={e => e.stopPropagation()}>
        <button className="pw-nav" disabled={i === 0} onClick={() => go(-1)} title="Previous (←)">‹</button>
        <div className="pw-phone" onClick={() => { const v = fg.current; if (v) ended ? start() : v.paused ? v.play() : v.pause(); }}>
          {p.preview ? <>
            <video ref={bg} className="pw-bg" src={p.preview} muted playsInline preload="auto" />
            <video ref={fg} key={`${it.job}:${p.n}`} className="pw-fg" src={p.preview} playsInline preload="auto" autoPlay
              onLoadedMetadata={start} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
          </> : <div className="pw-missing">
            <b>Video not downloaded yet</b>
            {p.queue?.state === "fetching" || p.queue?.state === "queued" ? <span>Downloading… it plays here when it's in</span>
              : <button className="pk-go" onClick={e => { e.stopPropagation(); onFetch(it); }}>Download to preview (~20 s)</button>}
          </div>}
          {p.title && <div className="pw-title"><span>{p.title}</span></div>}
          {g && <div className="pw-cap">{g.ws.map((w, j) => <span key={j} className={j === k ? "on" : ""}>{w.w.toUpperCase()} </span>)}</div>}
          {inHook && p.preview && <div className="pw-hook">hook</div>}
          {p.preview && !playing && <div className="pw-play">{ended ? "↻" : "▶"}</div>}
          <div className="pw-prog"><i style={{ width: `${(inHook ? 0 : prog) * 100}%` }} /></div>
        </div>
        <aside className="pw-side">
          <div className="pw-count">{i + 1} / {items.length}<span>{it.video}</span></div>
          <div className="pw-meta"><span className="dk-tag">{p.tag || `#${p.n}`}</span>{len} s{p.clips.length ? <span className="lv-chip ok">✓ rendered</span> : null}
            {busy && <span className="lv-chip">{p.queue!.state === "rendering" ? "rendering…" : p.queue!.state === "fetching" ? "downloading…" : "in render queue"}</span>}</div>
          <div className="pw-t">{p.title || "untitled"}</div>
          {p.rank && <div className={"pk-why v-" + p.rank.verdict}>✦ Claude: #{p.rank.rank}, {p.rank.verdict}{p.rank.why ? ` · ${p.rank.why}` : ""}</div>}
          {p.titles.filter(x => x !== p.title).slice(0, 2).map(x => <div key={x} className="pk-alt">{x}</div>)}
          <button className={"pw-sel" + (on ? " on" : "")} disabled={busy} onClick={() => onToggle(it)}>
            {on ? "✓ Selected for render" : "Select for render"} <kbd>S</kbd></button>
          <div className="pw-acts">
            {!busy && <button className="btnx sm" onClick={() => onRender(it)}>{p.clips.length ? "Render again now" : "Render now"}</button>}
            {p.preview && <a className="btnx sm" href={p.clips[0] ? editUrl(it.job, p.clips[0].name) : editUrl(it.job, null, p.n)} target="_blank" rel="noreferrer">Edit</a>}
            {!busy && <button className="btnx sm" onClick={() => { onHide(it); go(1); }}>{p.hidden ? "Restore" : "Hide"} <kbd>H</kbd></button>}
          </div>
          <div className="pw-opts">
            <label><input type="checkbox" checked={hookFirst} onChange={e => { setHookFirst(e.target.checked); setTimeout(start, 0); }} /> Hook first, like the final clip</label>
            <label><input type="checkbox" checked={autoNext} onChange={e => setAutoNext(e.target.checked)} /> Play the next one automatically</label>
          </div>
          <div className="pw-keys"><kbd>←</kbd><kbd>→</kbd> previous / next · <kbd>space</kbd> play · <kbd>Esc</kbd> close</div>
          <div className="pw-note">A rough preview from the source video. The rendered clip adds the webcam split when there's a face, the rewind after the hook and tighter cuts.</div>
          <button className="btnx sm pw-close" onClick={onClose}>Close</button>
        </aside>
        <button className="pw-nav" disabled={i === items.length - 1} onClick={() => go(1)} title="Next (→)">›</button>
      </div>
    </div>
  );
}
