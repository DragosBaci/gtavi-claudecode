"use client";
// Post: the posting queue. Every clip Claude kept and checked lands here by itself (and every clip you render in the
// editor). Select one to watch it, edit its text per platform, post it now or remove it. X is posted by hand.
// The auto-uploader (upload-next) decides the order when it posts. Text edits go to queue/overrides.json, which it uses too.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/util";
import { ago, editUrl, usePoll } from "@/lib/desk";
import Nav, { Toast } from "./Nav";

type Plat = "tiktok" | "youtube" | "instagram" | "x";
interface PState { posted?: boolean; url?: string; updated?: number; by?: string }
interface Item {
  id: string; rank: number; title: string; hook: string; why: string; warnings: string[]; creator: string; duration: number;
  format: string; source_url: string; source_title: string; platforms: Record<string, Record<string, string>>;
  job: string; name: string; video: string | null; state: Partial<Record<Plat, PState>>; skipped: { reason: string; at: string } | null; added: boolean;
  rank_reason?: string | null; hold?: boolean; pinned?: boolean; target?: string | null; cats: string[];
  campaigns?: { id: string; name: string; rate: number | null; pool: string | null; eligible: boolean; problems: string[] }[];
}
const paid = (i: Item) => (i.campaigns || []).filter(c => c.eligible);
const pay = (i: Item) => Math.max(0, ...paid(i).map(c => c.rate || 0.01));
interface PostNow { state?: string; running: boolean; id?: string; started?: number; finished?: number | null; result?: string; posted?: string[]; error?: string | null; log: string[] }
interface Account { name: string; handles: string[] }
interface Camp { id: string; name: string; rate_per_1k: number | null; pool: string | null; joined: boolean; platforms: string[]; url: string }
const short = (c: Camp) => c.name.split(" (")[0];
const cpay = (c: Camp) => c.rate_per_1k ? `$${c.rate_per_1k}/1K` : c.pool ? "pool" : "";
interface Data { campaigns: Camp[]; items: Item[]; max_dur: Record<string, number>; limits: Record<string, number>; last_post: number | null; log: any[]; postnow: PostNow; account: Account | null }

const PLATS: { k: Plat; short: string; name: string; fields: [string, string, string | null][]; max: number }[] = [
  { k: "tiktok", short: "TT", name: "TikTok", fields: [["Caption", "caption", "tiktok"]], max: 600 },
  { k: "youtube", short: "YT", name: "YouTube Shorts", fields: [["Title", "title", "yt_title"], ["Description", "description", "yt_desc"], ["Tags", "tags", null]], max: 180 },
  { k: "instagram", short: "IG", name: "Instagram Reels", fields: [["Caption", "caption", "instagram"]], max: 180 },
  { k: "x", short: "X", name: "X", fields: [["Post", "text", "x"]], max: 140 },
];
const MAIN: Plat[] = ["tiktok", "youtube", "instagram"];
type Tab = "todo" | "posted" | "skipped";
/** The only account Claude may post as (queue/accounts.json). Post now and the auto-uploader stop on any other account. */
function PostingAs({ account, save }: { account: Account | null; save: (name: string, handles: string) => void }) {
  const [open, setOpen] = useState(false), [name, setName] = useState(""), [handles, setHandles] = useState("");
  const edit = () => { setName(account?.name || ""); setHandles((account?.handles || []).map(h => "@" + h).join(" ")); setOpen(true); };
  if (!open) return (
    <button className={"btnx sm" + (account ? "" : " warn")} onClick={edit}
      title="Claude only posts when the signed-in account on YouTube, TikTok and Instagram matches this">
      {account ? `Posting as ${account.name}` : "⚠ Set the account you post as"}</button>);
  return (
    <form className="q-acct" onSubmit={e => { e.preventDefault(); if (name.trim()) { save(name.trim(), handles); setOpen(false); } }}>
      <input autoFocus placeholder="Page name, e.g. GTA Daily" value={name} onChange={e => setName(e.target.value)} />
      <input placeholder="@handles (optional)" value={handles} onChange={e => setHandles(e.target.value)} />
      <button className="btnx sm" type="submit" disabled={!name.trim()}>Save</button>
      <button className="btnx sm" type="button" onClick={() => setOpen(false)}>Cancel</button>
    </form>);
}

const fmtDur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const done = (i: Item) => MAIN.some(p => i.state[p]?.posted);   // posted anywhere: the uploader finishes the rest
const left = (i: Item) => MAIN.filter(p => !i.state[p]?.posted).map(p => PLATS.find(x => x.k === p)!.short);

export default function Queue() {
  const { data: d, reload } = usePoll<Data>("/api/queue", 5000);
  const [tab, setTab] = useState<Tab>("todo"), [sel, setSel] = useState<string | null>(null), [camp, setCamp] = useState("");
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  useEffect(() => { const q = new URLSearchParams(location.search); const t = q.get("tab") as Tab | null; if (t) setTab(t); if (q.get("id")) setSel(q.get("id")); }, []);
  const post = (path: string, body: Record<string, unknown>, ok?: string) => api(path, body).then(() => { if (ok) say(ok); reload(); }).catch(e => say(e.message));
  const pn = d?.postnow, wasPosting = useRef(false);
  useEffect(() => {   // live log while Claude posts; say when it's done
    if (!pn) return;
    if (wasPosting.current && !pn.running) say(pn.posted?.length ? `Posted on ${pn.posted.join(", ")}` : "Post now finished: see the result");
    wasPosting.current = pn.running;
    if (pn.running) { const h = setTimeout(reload, 1500); return () => clearTimeout(h); }
  }, [pn]);   // eslint-disable-line react-hooks/exhaustive-deps

  const list = useMemo(() => {
    if (!d) return [];
    const l = d.items.filter(i => (!camp || i.cats.includes(camp)) && (tab === "skipped" ? !done(i) && (!!i.skipped || !!i.hold) : tab === "posted" ? done(i) : !done(i) && !i.skipped && !i.hold));
    return tab === "todo" ? [...l].sort((a, b) => pay(b) - pay(a)) : l;   // paid campaign clips go out first
  }, [d, tab, camp]);
  const counts = d ? { todo: d.items.filter(i => !done(i) && !i.skipped && !i.hold).length, posted: d.items.filter(done).length,
    skipped: d.items.filter(i => !done(i) && (i.skipped || i.hold)).length } : null;
  const item = d?.items.find(i => i.id === sel) || null;
  const opened = useRef(false);
  useEffect(() => {   // a link to one clip (?id=, e.g. right after a render): open the tab it's in
    if (!d || opened.current || !sel) return; opened.current = true;
    const it = d.items.find(i => i.id === sel);
    if (it) setTab(done(it) ? "posted" : it.skipped || it.hold ? "skipped" : "todo");
  }, [d, sel]);
  useEffect(() => {   // keep a selection
    if (!d || item) return;
    const first = list[0]?.id; if (first) setSel(first);
  }, [d, tab, item, list]);

  return (
    <div className="desk">
      <Nav>
        {d && <PostingAs account={d.account} save={(name, handles) => post("/api/queue/account", { name, handles }, `Posting as ${name}`)} />}
        <span className="lv-grow" />
        {d && <span className="lv-sub">last post {ago(d.last_post)}</span>}
      </Nav>
      <div className="q-body">
        <aside className="q-list">
          <div className="lv-langs q-tabs">
            {([["todo", "To post"], ["posted", "Posted"], ["skipped", "On hold"]] as [Tab, string][]).map(([k, n]) =>
              <button key={k} className={tab === k ? "on" : ""} onClick={() => { setTab(k); setSel(null); }}>{n}<span>{counts?.[k] ?? ""}</span></button>)}
          </div>
          {!!d?.campaigns.length && <select className="inp q-camps" value={camp} onChange={e => { setCamp(e.target.value); setSel(null); }}>
            <option value="">All campaigns · {d.items.length}</option>
            {d.campaigns.map(c => <option key={c.id} value={c.id}>{short(c)} · {cpay(c)} · {d.items.filter(i => i.cats.includes(c.id)).length}</option>)}
          </select>}
          <div className="q-rows">
            {list.map((i, k) => (
              <button key={i.id} className={"q-row" + (sel === i.id ? " on" : "")} onClick={() => setSel(i.id)}>
                {i.video ? <video src={i.video + "#t=0.5"} muted preload="metadata" /> : <div className="q-novid" />}
                <div>
                  <b><span className="q-rank">{tab === "todo" ? `#${k + 1}` : done(i) ? "✓" : `#${i.rank}`}</span>{i.title}</b>
                  <span>{i.creator} · {fmtDur(i.duration)}</span>
                  <span className="q-dots">{PLATS.map(p => <i key={p.k} className={i.state[p.k]?.posted ? "on" : ""} title={p.name}>{p.short}</i>)}
                    {!!i.warnings.length && <em title={i.warnings.join("\n")}>⚠ {i.warnings.length}</em>}{i.skipped && <em>skipped</em>}
                    {i.hold && <em title={i.rank_reason || ""}>hold</em>}{!!paid(i).length && <em className="q-camp" title={paid(i).map(c => c.name).join(", ")}>$ {paid(i)[0].rate ? `${paid(i)[0].rate}/1K` : "pool"}</em>}{done(i) && left(i).length > 0 && <em className="q-left">{left(i).join(" ")} left</em>}
                    {i.cats[0] && d?.campaigns.find(c => c.id === i.cats[0]) && <em className="q-camp">{short(d.campaigns.find(c => c.id === i.cats[0])!)}</em>}</span>
                </div>
              </button>
            ))}
            {d && !list.length && <div className="lv-none">{tab === "todo" ? "Nothing to post yet: clips Claude keeps land here by themselves." : "Nothing here."}</div>}
          </div>
        </aside>
        <main className="q-detail">
          {item ? <ItemDetail key={item.id} i={item} d={d!} post={post} /> :
            <div className="lv-none">Select a clip.</div>}
        </main>
      </div>
      <Toast m={msg} />
    </div>
  );
}

function Player({ src }: { src: string | null }) {
  return src ? <video className="q-player" src={src} controls playsInline preload="metadata" /> : <div className="q-player q-novid">video missing</div>;
}

function ItemDetail({ i, d, post }: { i: Item; d: Data; post: (p: string, b: Record<string, unknown>, ok?: string) => void }) {
  const [plat, setPlat] = useState<Plat>("tiktok");
  const P = PLATS.find(p => p.k === plat)!, st = i.state[plat] || {};
  const tooLong = i.duration > (d.max_dur[plat] ?? P.max);
  const manual = !MAIN.includes(plat);   // X: you post it yourself; the uploader posts the others and records the link
  return (
    <div className="q-grid">
      <div className="q-left">
        <Player src={i.video} />
        <PostNowBox id={i.id} pn={d.postnow} post={post} left={left(i)} />
        <div className="q-acts">
          <a className="btnx" href={editUrl(i.job, i.name)} target="_blank" rel="noreferrer">✎ Edit clip</a>
          {i.skipped && <button className="btnx" onClick={() => post("/api/queue/skip", { id: i.id, skip: false }, "The auto-uploader may post it again")}>Allow auto-upload</button>}
          {!done(i) && <button className="btnx" onClick={() => post("/api/queue/remove", { id: i.id }, "Removed from the queue")}>Remove</button>}
        </div>
      </div>
      <div className="q-right">
        <div className="q-title">{i.title}</div>
        <div className="lv-sub">{i.creator} · {fmtDur(i.duration)} · {i.format}{i.source_url && <> · <a href={i.source_url} target="_blank" rel="noreferrer">source</a></>}</div>
        {(i.rank_reason || i.why) && <div className={"note" + (i.hold ? " warn" : "")} style={{ marginTop: 10 }}>
          {i.hold && <b>On hold: </b>}{i.rank_reason || i.why}</div>}
        {i.cats[0] && d.campaigns.find(c => c.id === i.cats[0]) && (() => { const c = d.campaigns.find(x => x.id === i.cats[0])!; return (
          <div className={"note" + (c.joined ? "" : " warn")}><b>{i.target === c.id ? "Made for" : "Fits"} {c.name}</b> · {cpay(c) || "rate unknown"}
            {c.joined ? "" : ": not joined yet, join it before posting or the views won't count"} · <a href={`/campaigns?id=${c.id}`}>campaign ↗</a></div>); })()}
        {(i.campaigns || []).filter(c => c.id !== i.cats[0]).map(c => <div key={c.id} className={"note" + (c.eligible ? "" : " warn")}>
          <b>$ {c.name}</b> · {c.rate ? `$${c.rate} per 1K views` : c.pool || "pool"}{c.eligible ? ": its link is submitted after posting" : `: ${c.problems.join("; ")}`}</div>)}
        {i.skipped && <div className="note warn">Skipped by the auto-uploader: {i.skipped.reason}</div>}
        {i.warnings.map(w => <div key={w} className="note warn">⚠ {w}</div>)}
        <div className="seg q-ptabs">
          {PLATS.map(p => <button key={p.k} className={plat === p.k ? "on" : ""} onClick={() => setPlat(p.k)}>{p.name}{i.state[p.k]?.posted ? " ✓" : ""}</button>)}
        </div>
        {tooLong && <div className="note warn">{fmtDur(i.duration)} is longer than {P.name} allows ({fmtDur(d.max_dur[plat] ?? P.max)}): skip it there or trim it in the editor.</div>}
        {P.fields.map(([label, f, lim]) => <TextField key={plat + f} i={i} plat={plat} label={label} field={f} limit={lim ? d.limits[lim] : null} post={post} copy={manual} />)}
        {manual && <div className="note">You post on {P.name} yourself: copy the text, then save the post link here.</div>}
        {manual ? <div className="q-posted">
          <button className={"btnx" + (st.posted ? " q-ok" : "")} onClick={() => post("/api/queue/state", { id: i.id, platform: plat, posted: !st.posted }, st.posted ? "Marked not posted" : `Marked posted on ${P.name}`)}>
            {st.posted ? `✓ Posted${st.by === "claude-uploader" ? " by the uploader" : ""}` : "Mark as posted"}</button>
          <UrlField key={plat + (st.url || "")} url={st.url || ""} onSave={u => post("/api/queue/state", { id: i.id, platform: plat, url: u, posted: true }, "Link saved")} />
          {st.url && <a className="btnx sm" href={st.url} target="_blank" rel="noreferrer">Open</a>}
        </div> : st.url ? <div className="q-posted"><span className="lv-chip ok">✓ Posted</span><a className="btnx sm" href={st.url} target="_blank" rel="noreferrer">Open post ↗</a></div> : null}
      </div>
    </div>
  );
}

function TextField({ i, plat, label, field, limit, post, copy }: { i: Item; plat: Plat; label: string; field: string; limit: number | null; post: (p: string, b: Record<string, unknown>, ok?: string) => void; copy: boolean }) {
  const orig = i.platforms[plat]?.[field] || "";
  const [v, setV] = useState(orig);
  useEffect(() => setV(orig), [orig]);
  const n = [...v].length, over = limit != null && n > limit;
  return (
    <div className="field">
      <label>{label}<span className={"q-count" + (over ? " over" : "")}>{limit != null ? `${n} / ${limit}` : `${n}`}</span>
        {copy && <button className="btnx sm" style={{ marginLeft: 8 }} onClick={() => navigator.clipboard.writeText(v).catch(() => {})}>Copy</button>}</label>
      <textarea className="inp" rows={field === "description" ? 5 : field === "title" ? 2 : 4} value={v} onChange={e => setV(e.target.value)}
        onBlur={() => { if (v !== orig) post("/api/queue/text", { id: i.id, platform: plat, field, value: v }, "Saved: the auto-uploader will use it"); }} />
    </div>
  );
}

function UrlField({ url, onSave }: { url: string; onSave: (u: string) => void }) {
  const [v, setV] = useState(url);
  return <input className="inp" placeholder="post link (optional)" value={v} onChange={e => setV(e.target.value)}
    onKeyDown={e => { if (e.key === "Enter" && v.trim() && v !== url) onSave(v.trim()); }} onBlur={() => { if (v.trim() && v !== url) onSave(v.trim()); }} />;
}

/** "Post now": Claude opens the platforms in Chrome and uploads this clip (queue/post_now.py). Two clicks: it publishes. */
function PostNowBox({ id, pn, post, left: todo }: { id: string; pn: PostNow; post: (p: string, b: Record<string, unknown>, ok?: string) => void; left: string[] }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => { if (!armed) return; const h = setTimeout(() => setArmed(false), 4000); return () => clearTimeout(h); }, [armed]);
  const mine = pn?.id === id, busyElse = pn?.running && !mine;
  if (mine && pn.running) return (
    <div className="q-pn">
      <div className="q-pnh"><i className="dk-pulse" /><b>Claude is posting this clip in Chrome</b><span className="lv-grow" />
        <button className="btnx sm" onClick={() => post("/api/queue/postnow/stop", {}, "Stopped")}>■ Stop</button></div>
      <div className="log dk-claude">{pn.log.length ? pn.log.join("\n") : "Starting Claude…"}</div>
    </div>);
  if (!todo.length) return null;
  return (<>
    <button className={"q-postnow" + (armed ? " armed" : "")} disabled={busyElse}
      onClick={() => { if (!armed) { setArmed(true); return; } setArmed(false); post("/api/queue/postnow", { id }, "Claude is opening the tabs…"); }}>
      {busyElse ? "Posting another clip…" : armed ? `Click again: post on ${todo.join(", ")} now` : "🚀 Post now"}
    </button>
    {mine && !pn.running && (pn.result || pn.error) && <div className={"note" + (pn.error ? " warn" : "")}><b>Last Post now:</b> {pn.error || pn.result}</div>}
  </>);
}
