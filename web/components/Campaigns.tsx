"use client";
// Campaigns: one category per paid clipping campaign (campaigns.json, scripts/campaigns.py). Left: the campaigns, the
// ones paying on YouTube first. Right: what the campaign pays for, the clips made for it (a campaign run tags them)
// that wait in the queue, and the posted ones with their views and submission status. Setup (joined, rules, overlay)
// sits at the bottom. Join a campaign on its own page first: views from before you join don't count.
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/util";
import { ago, kfmt, usePoll } from "@/lib/desk";
import Nav, { Toast } from "./Nav";

interface Rules {
  min_sec?: number; max_sec?: number; overlay_required?: boolean; overlay_position?: string; overlay_width?: number;
  caption_text?: string; hashtags?: string[]; min_payout?: number; max_payout?: number; banned?: string[]; notes?: string;
}
interface Earn { views: number; estimate: number | null; paid: number; below_min: number; subs: Record<string, number> }
interface Post { platform: string; url: string | null; at: number | null; views: number | null; sub: string | null }
interface Tracked { id: string; title: string; creator: string; duration: number; hold: boolean; video: string | null; posts: Post[]; made_for: boolean }
interface Campaign {
  id: string; name: string; site: string; url: string; creators: string[]; any_gta: boolean; status?: string;
  platforms: string[]; rate_per_1k: number | null; pool: string | null; budget: string | null; checked: string;
  joined: boolean; rules: Rules; rules_text: string; parse_state?: string; parse_error?: string; warning?: string | null;
  overlay: string | null; earn: Earn | null; tracked: Tracked[];
}
interface Submit { running: boolean; result?: string; error?: string | null; log: string[]; submitted?: number; left?: number }
interface Data { campaigns: Campaign[]; submit: Submit; totals: { estimate: number; paid: number; views: number; to_submit: number } }

const SNAME: Record<string, string> = { to_submit: "to submit", submitted: "submitted", approved: "approved", rejected: "rejected", paid: "paid", skipped: "skipped" };
const PNAME: Record<string, string> = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram", x: "X" };
const SITE: Record<string, string> = { contentrewards: "Content Rewards", clip: "Clip Money (Discord)" };
const money = (n: number | null | undefined) => n == null ? "—" : `$${n.toFixed(2)}`;
const pay = (c: Campaign) => c.rate_per_1k ? `$${c.rate_per_1k.toFixed(2)} / 1K views` : c.pool || "rate unknown";
const fmtDur = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
const ended = (c: Campaign) => c.status === "ended" || /ended/i.test(c.warning || "");
const yt = (c: Campaign) => c.platforms.includes("youtube");
const posted = (t: Tracked) => t.posts.length > 0;

export default function Campaigns() {
  const { data: d, reload } = usePoll<Data>("/api/campaigns", 5000);
  const [sel, setSel] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  const post = (path: string, body: Record<string, unknown>, ok?: string) => api(path, body).then(() => { if (ok) say(ok); reload(); }).catch(e => say(e.message));
  useEffect(() => { const id = new URLSearchParams(location.search).get("id"); if (id) setSel(id); }, []);
  const was = useRef(false);
  useEffect(() => {   // say when a submit run ends
    if (!d) return;
    if (was.current && !d.submit.running) say(d.submit.error ? `Submit stopped: ${d.submit.error}` : `Submitted ${d.submit.submitted ?? 0}${d.submit.left ? `, ${d.submit.left} need you` : ""}`);
    was.current = d.submit.running;
  }, [d?.submit.running]);   // eslint-disable-line react-hooks/exhaustive-deps

  // Joined first, then the ones with clips, then by rate; the ones that don't pay on YouTube and ended ones last.
  const score = (c: Campaign) => (c.joined ? 0 : 1) * 1e6 + (c.tracked.length ? 0 : 1) * 1e5 - (c.rate_per_1k || 0.9) * 100;
  const live = (d?.campaigns || []).filter(c => !ended(c)).sort((a, b) => score(a) - score(b));
  const groups: [string, Campaign[]][] = [["Pays on YouTube", live.filter(yt)], ["Other platforms", live.filter(c => !yt(c))]];
  const c = d?.campaigns.find(x => x.id === sel) || live[0] || null;
  const t = d?.totals;

  return (
    <div className="desk">
      <Nav>
        <span className="lv-grow" />
        {t && (t.paid > 0 || t.estimate > 0) && <span className="lv-sub">paid {money(t.paid)} · estimated {money(t.estimate)}</span>}
        {d && !!t?.to_submit && (d.submit.running
          ? <span className="lv-rec"><i />Claude is submitting… <button className="btnx sm" onClick={() => post("/api/campaigns/submit/stop", {}, "Stopped")}>■ Stop</button></span>
          : <SubmitButton n={t.to_submit} onGo={() => post("/api/campaigns/submit", {}, "Claude is opening the campaign pages…")} />)}
      </Nav>
      <div className="q-body">
        <aside className="q-list">
          <div className="q-rows">
            {groups.map(([g, cs]) => !!cs.length && <div key={g} className="cp-group">
              <div className="cp-gh">{g}</div>
              {cs.map(x => {
                const ready = x.tracked.filter(k => !posted(k)).length, out = x.tracked.filter(posted).length;
                return (
                  <button key={x.id} className={"q-row cp-row" + (c?.id === x.id ? " on" : "")} onClick={() => setSel(x.id)}>
                    <div>
                      <b>{x.name}</b>
                      <span>{pay(x)}{x.joined ? " · ✓ joined" : ""}</span>
                      {(ready > 0 || out > 0) && <span className="q-dots">{ready > 0 && <em>{ready} ready</em>}{out > 0 && <em className="q-camp">{out} posted</em>}</span>}
                    </div>
                  </button>);
              })}
            </div>)}
          </div>
        </aside>
        <main className="q-detail">
          {c ? <Detail key={c.id} c={c} post={post} /> : <div className="lv-none">{d ? "No campaigns yet." : "Loading…"}</div>}
        </main>
      </div>
      <Toast m={msg} />
    </div>
  );
}

function SubmitButton({ n, onGo }: { n: number; onGo: () => void }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => { if (!armed) return; const h = setTimeout(() => setArmed(false), 4000); return () => clearTimeout(h); }, [armed]);
  return <button className={"btnx sm" + (armed ? " danger" : "")} onClick={() => { if (!armed) { setArmed(true); return; } setArmed(false); onGo(); }}>
    {armed ? `Click again: submit ${n} link${n === 1 ? "" : "s"}` : `Submit ${n} link${n === 1 ? "" : "s"} with Claude`}</button>;
}

function Detail({ c, post }: { c: Campaign; post: (p: string, b: Record<string, unknown>, ok?: string) => void }) {
  const r = c.rules || {}, e = c.earn;
  const ready = c.tracked.filter(k => !posted(k)), out = c.tracked.filter(posted);
  const who = c.any_gta ? "Any GTA 6 streamer or gameplay" : `Clips of ${c.creators.filter((x, i, a) => a.findIndex(y => y.toLowerCase() === x.toLowerCase()) === i).slice(0, 3).join(", ") || "unknown"}`;
  return (
    <div className="cp-detail">
      <div className="q-title">{c.name}</div>
      <div className="lv-sub">{pay(c)} · {c.platforms.map(p => PNAME[p] || p).join(", ")}{c.budget ? ` · ${c.budget}` : ""} · {SITE[c.site] || c.site} · checked {c.checked}
        {" · "}<a href={c.url} target="_blank" rel="noreferrer">campaign page ↗</a></div>

      <div className="note" style={{ marginTop: 12 }}>
        <b>{who}.</b> {r.notes || ""}
        {r.caption_text && <div>Caption adds: “{r.caption_text}”</div>}
      </div>
      {c.joined ? <div className="note">✓ Joined. Posted clips are submitted to it after they go live.{e ? ` ${kfmt(e.views)} views · estimated ${money(e.estimate)} · paid ${money(e.paid)}` : ""}</div>
        : <div className="note warn">Not joined yet. Join it on the campaign page before posting these clips: views from before you join don't count.</div>}
      {c.warning && <div className="note warn">⚠ {c.warning}</div>}

      <div className="dk-h">Ready to post <span className="lv-sub">{ready.length}</span></div>
      {!ready.length ? <div className="lv-none">No clips waiting for this campaign.</div> :
        <div className="cp-grid">{ready.map(k => (
          <a key={k.id} className="cp-clip" href={`/queue?id=${encodeURIComponent(k.id)}`}>
            {k.video ? <video src={k.video + "#t=0.5"} muted preload="metadata" /> : <div className="q-novid" />}
            <b>{k.title}</b><span>{k.creator} · {fmtDur(k.duration)}{k.hold ? " · on hold" : ""}</span>
          </a>))}</div>}

      <div className="dk-h">Posted <span className="lv-sub">{out.length}</span></div>
      {!out.length ? <div className="lv-none">Nothing posted for this campaign yet. Posted clips show up here with their views.</div> :
        <section className="dk-panel ern-subs">{out.map(k => (
          <div key={k.id} className="ern-sub cp-post">
            <div><b>{k.title}</b><span className="lv-sub">{k.creator} · {fmtDur(k.duration)}</span></div>
            <div className="cp-plats">{k.posts.map(p => (
              <span key={p.platform} className="cp-plat">
                {p.url ? <a href={p.url} target="_blank" rel="noreferrer">{PNAME[p.platform] || p.platform} ↗</a> : PNAME[p.platform] || p.platform}
                <span className="lv-sub">{p.views != null ? `${kfmt(p.views)} views` : "no views read yet"}{p.at ? ` · ${ago(p.at)}` : ""}</span>
                {p.sub && <span className={"lv-chip" + (p.sub === "paid" || p.sub === "approved" ? " ok" : p.sub === "rejected" ? " hot" : "")}>{SNAME[p.sub] || p.sub}</span>}
              </span>))}</div>
          </div>))}</section>}

      <details className="cp-setup">
        <summary>Set up</summary>
        <Setup c={c} post={post} />
      </details>
    </div>
  );
}

function Setup({ c, post }: { c: Campaign; post: (p: string, b: Record<string, unknown>, ok?: string) => void }) {
  const [rules, setRules] = useState(c.rules_text || "");
  useEffect(() => setRules(c.rules_text || ""), [c.rules_text]);
  const r = c.rules || {};
  const upload = (f: File | undefined) => {
    if (!f) return;
    if (f.type !== "image/png") { post("/api/campaigns/overlay", { id: c.id, data: "" }); return; }
    const rd = new FileReader();
    rd.onload = () => post("/api/campaigns/overlay", { id: c.id, data: rd.result }, "Overlay saved: new clips of this campaign get it");
    rd.readAsDataURL(f);
  };
  return (<>
    <div className="ern-row">
      <button className={"btnx sm" + (c.joined ? " q-ok" : "")} onClick={() => post("/api/campaigns/update", { id: c.id, joined: !c.joined }, c.joined ? "Marked not joined" : "Marked joined")}
        title="Join on the campaign's own page first; this only tells the desk you did">{c.joined ? "✓ Joined" : "I joined it"}</button>
      <span className="lv-sub">Join on the campaign page first (Claude never joins for you), then mark it here.</span>
    </div>
    <div className="field" style={{ marginTop: 12 }}><label>Rules · paste them from the campaign page; Claude turns them into settings</label>
      <textarea className="inp" rows={4} value={rules} onChange={ev => setRules(ev.target.value)} placeholder="Everything the campaign page says about what to clip, length, overlay, captions, payouts…" />
      <div className="ern-row" style={{ marginTop: 8 }}>
        <button className="btnx sm" disabled={rules === (c.rules_text || "") || c.parse_state === "running"}
          onClick={() => post("/api/campaigns/update", { id: c.id, rules_text: rules }, "Saved: Claude is reading the rules")}>Save rules</button>
        {c.parse_state === "running" && <span className="lv-sub"><i className="dk-pulse" />Claude is reading them…</span>}
        {c.parse_state === "failed" && <span className="lv-chip hot" title={c.parse_error || ""}>couldn't read them</span>}
      </div>
    </div>
    <div className="field"><label>Overlay / logo (PNG) {r.overlay_required ? "· required" : "· if the campaign asks for one"}</label>
      <div className="ern-row">
        {c.overlay && <img className="ern-ov" src={c.overlay} alt="overlay" />}
        <label className="btnx sm">{c.overlay ? "Replace" : "Add PNG"}<input type="file" accept="image/png" hidden onChange={ev => upload(ev.target.files?.[0])} /></label>
        {c.overlay && <button className="btnx sm ghost" onClick={() => post("/api/campaigns/overlay", { id: c.id, remove: true }, "Overlay removed")}>Remove</button>}
      </div>
    </div>
  </>);
}
