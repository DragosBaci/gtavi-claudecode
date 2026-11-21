"use client";
// Queue: the posting queue (clips added here, plus an optional hand-ranked queue/queue.json). Select a clip to watch it, edit its text per
// platform, mark it posted, skip it for the auto-uploader or open it in the editor. Unqueued clips can be added.
// Text edits are saved to queue/overrides.json, which the auto-uploader uses too.
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
  rank_reason?: string | null; hold?: boolean; pinned?: boolean;
}
interface Other { id: string; job: string; name: string; title: string; creator: string | null; video: string; updated: number; layout: string | null }
interface Rerank { state?: string; running: boolean; started?: number; finished?: number; error?: string | null; count?: number; added?: number; held?: number; order_at?: number | null }
interface PostNow { state?: string; running: boolean; id?: string; started?: number; finished?: number | null; result?: string; posted?: string[]; error?: string | null; log: string[] }
interface Account { name: string; handles: string[] }
interface Data { items: Item[]; other: Other[]; max_dur: Record<string, number>; limits: Record<string, number>; last_post: number | null; log: any[]; rerank: Rerank; postnow: PostNow; account: Account | null }

const PLATS: { k: Plat; short: string; name: string; fields: [string, string, string | null][]; max: number }[] = [
  { k: "tiktok", short: "TT", name: "TikTok", fields: [["Caption", "caption", "tiktok"]], max: 600 },
  { k: "youtube", short: "YT", name: "YouTube Shorts", fields: [["Title", "title", "yt_title"], ["Description", "description", "yt_desc"], ["Tags", "tags", null]], max: 180 },
  { k: "instagram", short: "IG", name: "Instagram Reels", fields: [["Caption", "caption", "instagram"]], max: 180 },
  { k: "x", short: "X", name: "X", fields: [["Post", "text", "x"]], max: 140 },
];
const MAIN: Plat[] = ["tiktok", "youtube", "instagram"];
type Tab = "todo" | "posted" | "skipped" | "other" | "all";
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
  const [tab, setTab] = useState<Tab>("todo"), [sel, setSel] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  useEffect(() => { const q = new URLSearchParams(location.search); const t = q.get("tab") as Tab | null; if (t) setTab(t); if (q.get("id")) setSel(q.get("id")); }, []);
  const post = (path: string, body: Record<string, unknown>, ok?: string) => api(path, body).then(() => { if (ok) say(ok); reload(); }).catch(e => say(e.message));
  const rr = d?.rerank, wasRunning = useRef(false);
  useEffect(() => {   // tell when a re-rank finishes; poll faster while it runs
    if (!rr) return;
    if (wasRunning.current && !rr.running) say(rr.state === "error" ? `Re-rank failed: ${rr.error}`
      : `Ranked ${rr.count ?? ""} clips${rr.added ? `, ${rr.added} added to the queue` : ""}${rr.held ? ` · ${rr.held} on hold` : ""}`);
    wasRunning.current = rr.running;
    if (rr.running) { const h = setTimeout(reload, 2000); return () => clearTimeout(h); }
  }, [rr]);   // eslint-disable-line react-hooks/exhaustive-deps
  const pn = d?.postnow, wasPosting = useRef(false);
  useEffect(() => {   // live log while Claude posts; say when it's done
    if (!pn) return;
    if (wasPosting.current && !pn.running) say(pn.posted?.length ? `Posted on ${pn.posted.join(", ")}` : "Post now finished: see the result");
    wasPosting.current = pn.running;
    if (pn.running) { const h = setTimeout(reload, 1500); return () => clearTimeout(h); }
  }, [pn]);   // eslint-disable-line react-hooks/exhaustive-deps

  const list = useMemo(() => {
    if (!d) return [];
    if (tab === "other") return [];
    return d.items.filter(i => tab === "all" || (tab === "skipped" ? !done(i) && (!!i.skipped || !!i.hold) : tab === "posted" ? done(i) : !done(i) && !i.skipped && !i.hold));
  }, [d, tab]);
  const counts = d ? { todo: d.items.filter(i => !done(i) && !i.skipped && !i.hold).length, posted: d.items.filter(done).length,
    skipped: d.items.filter(i => !done(i) && (i.skipped || i.hold)).length, other: d.other.length, all: d.items.length } : null;
  const item = d?.items.find(i => i.id === sel) || null, other = d?.other.find(o => o.id === sel) || null;
  const opened = useRef(false);
  useEffect(() => {   // a link to one clip (?id=, e.g. right after a render): open the tab it's in
    if (!d || opened.current || !sel) return; opened.current = true;
    const it = d.items.find(i => i.id === sel);
    if (d.other.some(o => o.id === sel)) setTab("other");
    else if (it) setTab(done(it) ? "posted" : it.skipped || it.hold ? "skipped" : "todo");
  }, [d, sel]);
  useEffect(() => {   // keep a selection
    if (!d || item || other) return;
    const first = tab === "other" ? d.other[0]?.id : list[0]?.id; if (first) setSel(first);
  }, [d, tab, item, other, list]);

  return (
    <div className="desk">
      <Nav>
        <button className="btnx sm" disabled={!d || rr?.running}
          onClick={() => post("/api/queue/rerank", { all: true }, `Claude is ranking ${d ? d.items.filter(i => !done(i)).length + d.other.length : ""} clips…`)}
          title="Claude ranks every unposted clip, including the rendered ones not in the queue yet (they join the queue). Weak ones go on hold, which the auto-uploader skips. The auto-uploader follows this order.">
          {rr?.running ? "Claude is ranking…" : `✦ Rank all ${d ? d.items.filter(i => !done(i)).length + d.other.length : ""} with Claude`}</button>
        {rr?.order_at && !rr.running && <span className="lv-sub">ranked {ago(rr.order_at)}</span>}
        {d && <PostingAs account={d.account} save={(name, handles) => post("/api/queue/account", { name, handles }, `Posting as ${name}`)} />}
        <span className="lv-grow" />
        {d && <span className="lv-sub">{MAIN.map(p => `${PLATS.find(x => x.k === p)!.short} ${d.items.filter(i => i.state[p]?.posted).length}/${d.items.length}`).join(" · ")} · last post {ago(d.last_post)}</span>}
      </Nav>
      <div className="q-body">
        <aside className="q-list">
          <div className="lv-langs q-tabs">
            {([["todo", "To post"], ["posted", "Posted"], ["skipped", "On hold"], ["other", "Not queued"], ["all", "All"]] as [Tab, string][]).map(([k, n]) =>
              <button key={k} className={tab === k ? "on" : ""} onClick={() => { setTab(k); setSel(null); }}>{n}<span>{counts?.[k] ?? ""}</span></button>)}
          </div>
          <div className="q-rows">
            {tab === "other" ? d?.other.map(o => (
              <button key={o.id} className={"q-row" + (sel === o.id ? " on" : "")} onClick={() => setSel(o.id)}>
                <video src={o.video + "#t=0.5"} muted preload="metadata" />
                <div><b>{o.title}</b><span>{o.creator || o.job} · {ago(o.updated)}</span><span className="q-name">{o.name}</span></div>
              </button>
            )) : list.map((i, k) => (
              <button key={i.id} className={"q-row" + (sel === i.id ? " on" : "")} onClick={() => setSel(i.id)}>
                {i.video ? <video src={i.video + "#t=0.5"} muted preload="metadata" /> : <div className="q-novid" />}
                <div>
                  <b><span className="q-rank">{tab === "todo" ? `#${k + 1}` : done(i) ? "✓" : `#${i.rank}`}</span>{i.title}</b>
                  {tab === "todo" && i.rank_reason && <span className="q-why">{i.rank_reason}</span>}
                  <span>{i.creator} · {fmtDur(i.duration)}{i.added ? " · added here" : ""}</span>
                  <span className="q-dots">{PLATS.map(p => <i key={p.k} className={i.state[p.k]?.posted ? "on" : ""} title={p.name}>{p.short}</i>)}
                    {!!i.warnings.length && <em title={i.warnings.join("\n")}>⚠ {i.warnings.length}</em>}{i.skipped && <em>skipped</em>}
                    {i.hold && <em title={i.rank_reason || ""}>hold</em>}{i.pinned && !done(i) && <em className="q-next">next</em>}{done(i) && left(i).length > 0 && <em className="q-left">{left(i).join(" ")} left</em>}</span>
                </div>
              </button>
            ))}
            {d && (tab === "other" ? !d.other.length : !list.length) && <div className="lv-none">Nothing here.</div>}
          </div>
        </aside>
        <main className="q-detail">
          {item ? <ItemDetail key={item.id} i={item} d={d!} post={post} /> : other ? <OtherDetail key={other.id} o={other} post={post} pn={d!.postnow} /> :
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
  return (
    <div className="q-grid">
      <div className="q-left">
        <Player src={i.video} />
        <PostNowBox id={i.id} pn={d.postnow} post={post} left={left(i)} />
        {!done(i) && (i.pinned
          ? <div className="note q-nextnote">▶ Posts next: the auto-uploader takes it on its next run, after the frame check.</div>
          : <button className="lv-clipbtn q-postnext" onClick={() => post("/api/queue/next", { id: i.id }, "Posts next: first in line for the auto-uploader")}>▶ Post next</button>)}
        <div className="q-acts">
          <a className="btnx" href={editUrl(i.job, i.name)} target="_blank" rel="noreferrer">✎ Edit clip</a>
          {i.skipped ? <button className="btnx" onClick={() => post("/api/queue/skip", { id: i.id, skip: false }, "The auto-uploader may post it again")}>Allow auto-upload</button>
            : <button className="btnx" onClick={() => post("/api/queue/skip", { id: i.id, skip: true }, "The auto-uploader will skip it")}>Skip auto-upload</button>}
          {i.added && <button className="btnx" onClick={() => post("/api/queue/remove", { id: i.id }, "Removed from the queue")}>Remove</button>}
        </div>
      </div>
      <div className="q-right">
        <div className="q-title">{i.title}</div>
        <div className="lv-sub">{i.creator} · {fmtDur(i.duration)} · {i.format}{i.source_url && <> · <a href={i.source_url} target="_blank" rel="noreferrer">source</a></>}</div>
        {i.rank_reason && <div className={"note" + (i.hold ? " warn" : "")} style={{ marginTop: 10 }}>
          <b>{i.hold ? "On hold" : `#${i.rank} in the posting order`}</b>: {i.rank_reason}</div>}
        {i.why && <div className="note" style={{ marginTop: i.rank_reason ? 0 : 10 }}>{i.why}</div>}
        {i.skipped && <div className="note warn">Skipped by the auto-uploader: {i.skipped.reason}</div>}
        {i.warnings.map(w => <div key={w} className="note warn">⚠ {w}</div>)}
        <div className="seg q-ptabs">
          {PLATS.map(p => <button key={p.k} className={plat === p.k ? "on" : ""} onClick={() => setPlat(p.k)}>{p.name}{i.state[p.k]?.posted ? " ✓" : ""}</button>)}
        </div>
        {tooLong && <div className="note warn">{fmtDur(i.duration)} is longer than {P.name} allows ({fmtDur(d.max_dur[plat] ?? P.max)}): skip it there or trim it in the editor.</div>}
        {P.fields.map(([label, f, lim]) => <TextField key={plat + f} i={i} plat={plat} label={label} field={f} limit={lim ? d.limits[lim] : null} post={post} />)}
        <div className="q-posted">
          <button className={"btnx" + (st.posted ? " q-ok" : "")} onClick={() => post("/api/queue/state", { id: i.id, platform: plat, posted: !st.posted }, st.posted ? "Marked not posted" : `Marked posted on ${P.name}`)}>
            {st.posted ? `✓ Posted${st.by === "claude-uploader" ? " by the uploader" : ""}` : "Mark as posted"}</button>
          <UrlField key={plat + (st.url || "")} url={st.url || ""} onSave={u => post("/api/queue/state", { id: i.id, platform: plat, url: u, posted: true }, "Link saved")} />
          {st.url && <a className="btnx sm" href={st.url} target="_blank" rel="noreferrer">Open</a>}
        </div>
      </div>
    </div>
  );
}

function TextField({ i, plat, label, field, limit, post }: { i: Item; plat: Plat; label: string; field: string; limit: number | null; post: (p: string, b: Record<string, unknown>, ok?: string) => void }) {
  const orig = i.platforms[plat]?.[field] || "";
  const [v, setV] = useState(orig);
  useEffect(() => setV(orig), [orig]);
  const n = [...v].length, over = limit != null && n > limit;
  return (
    <div className="field">
      <label>{label}<span className={"q-count" + (over ? " over" : "")}>{limit != null ? `${n} / ${limit}` : `${n}`}</span>
        <button className="btnx sm" style={{ marginLeft: 8 }} onClick={() => navigator.clipboard.writeText(v).catch(() => {})}>Copy</button></label>
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

function OtherDetail({ o, post, pn }: { o: Other; post: (p: string, b: Record<string, unknown>, ok?: string) => void; pn: PostNow }) {
  return (
    <div className="q-grid">
      <div className="q-left"><Player src={o.video} /></div>
      <div className="q-right">
        <div className="q-title">{o.title}</div>
        <div className="lv-sub">{o.creator || o.job} · edits/{o.name}.mp4 · rendered {ago(o.updated)}</div>
        <div className="note" style={{ marginTop: 12 }}>Not in the posting queue yet. <b>Post next</b> adds it and puts it first in line for the auto-uploader; <b>Add to queue</b> just adds it. Either way it gets post text for every platform (from its title and the creator), which you can edit here.</div>
        <div style={{ marginTop: 12 }}><PostNowBox id={o.id} pn={pn} post={post} left={["TT", "YT", "IG"]} /></div>
        <div className="q-acts">
          <button className="lv-clipbtn" onClick={() => post("/api/queue/next", { id: o.id }, "Added and posts next")}>▶ Post next</button>
          <button className="btnx" onClick={() => post("/api/queue/add", { job: o.job, name: o.name }, "Added to the queue")}>＋ Add to queue</button>
          <a className="btnx" href={editUrl(o.job, o.name)} target="_blank" rel="noreferrer">✎ Edit clip</a>
        </div>
      </div>
    </div>
  );
}
