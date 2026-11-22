"use client";
// Earn: paid clipping campaigns (campaigns.json, scripts/campaigns.py). Join a campaign on its own page, then tick it
// here, paste its rules (Claude turns them into settings: length, overlay, caption) and add its overlay. From then on
// clips of its creators render with its overlay, go out first, and their links are submitted after posting
// (Claude in Chrome, the campaign-submit skill). Views are real (scoreboard.csv); earnings are an estimate until paid.
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/util";
import { ago, kfmt, usePoll } from "@/lib/desk";
import Nav, { Toast } from "./Nav";

interface Rules {
  min_sec?: number; max_sec?: number; overlay_required?: boolean; overlay_position?: string; overlay_width?: number;
  caption_text?: string; hashtags?: string[]; min_payout?: number; max_payout?: number; banned?: string[]; notes?: string;
}
interface Earn { views: number; estimate: number | null; paid: number; below_min: number; subs: Record<string, number> }
interface Campaign {
  id: string; name: string; site: string; url: string; submit_url: string | null; creators: string[]; any_gta: boolean;
  platforms: string[]; rate_per_1k: number | null; pool: string | null; budget: string | null; fee: number; checked: string;
  joined: boolean; rules: Rules; rules_text: string; parse_state?: string; parse_error?: string; warning?: string | null;
  overlay: string | null; clips: { eligible: number; fix: number }; earn: Earn | null;
}
interface Sub { clip: string; title: string; platform: string; campaign: string; url: string; status: string; note?: string; updated: number }
interface Submit { state?: string; running: boolean; started?: number; result?: string; error?: string | null; log: string[]; submitted?: number; left?: number }
interface Data {
  campaigns: Campaign[]; submissions: Sub[]; payouts: { date: string; campaign: string; amount: string; note: string }[];
  submit: Submit; totals: { estimate: number; paid: number; views: number; to_submit: number }; stats: { running: boolean; at: number | null };
}
const STATUS = ["to_submit", "submitted", "approved", "rejected", "paid", "skipped"];
const SNAME: Record<string, string> = { to_submit: "to submit", submitted: "submitted", approved: "approved", rejected: "rejected", paid: "paid", skipped: "skipped" };
const money = (n: number | null | undefined) => n == null ? "—" : `$${n.toFixed(2)}`;
const rate = (c: Campaign) => c.rate_per_1k ? `$${c.rate_per_1k.toFixed(2)} / 1K views` : c.pool || "rate unknown";

export default function Earn() {
  const { data: d, reload } = usePoll<Data>("/api/campaigns", 4000);
  const [msg, setMsg] = useState<{ t: string; id: number } | null>(null);
  const say = (t: string) => setMsg({ t, id: Date.now() });
  const post = (path: string, body: Record<string, unknown>, ok?: string) => api(path, body).then(() => { if (ok) say(ok); reload(); }).catch(e => say(e.message));
  const [armed, setArmed] = useState(false);
  useEffect(() => { if (!armed) return; const h = setTimeout(() => setArmed(false), 4000); return () => clearTimeout(h); }, [armed]);
  const was = useRef(false);
  useEffect(() => {   // say when a submit run ends
    if (!d) return;
    if (was.current && !d.submit.running) say(d.submit.error ? `Submit stopped: ${d.submit.error}` : `Submitted ${d.submit.submitted ?? 0}${d.submit.left ? `, ${d.submit.left} need you` : ""}`);
    was.current = d.submit.running;
  }, [d?.submit.running]);   // eslint-disable-line react-hooks/exhaustive-deps

  const mine = d?.campaigns.filter(c => c.joined) || [], other = d?.campaigns.filter(c => !c.joined) || [];
  const t = d?.totals;
  return (
    <div className="desk">
      <Nav>{t && <span className="lv-sub">Paid {money(t.paid)} · estimated {money(t.estimate)}</span>}</Nav>
      <main className="dk-main">
        <div className="dk-cards">
          <div className="dk-card"><div className="dk-ct">Paid</div><div className="dk-big">{money(t?.paid ?? 0)}</div>
            <div className="dk-sub">Real payouts you recorded below.</div></div>
          <div className="dk-card"><div className="dk-ct">Estimated</div><div className="dk-big">{money(t?.estimate ?? 0)}</div>
            <div className="dk-sub">Views × rate, the platform fee and each post's min/max payout applied. Campaigns approve views; this is not money yet.</div></div>
          <div className="dk-card"><div className="dk-ct">Views on submitted posts</div><div className="dk-big">{kfmt(t?.views ?? 0)}</div>
            <div className="dk-sub">Read {d?.stats.at ? ago(d.stats.at) : "from scoreboard.csv"}.{" "}
              <button className="btnx sm" disabled={d?.stats.running} onClick={() => post("/api/campaigns/stats", {}, "Reading the views of every post…")}>
                {d?.stats.running ? "Reading…" : "↻ Read now"}</button></div></div>
          <div className="dk-card"><div className="dk-ct">To submit</div><div className="dk-big">{t?.to_submit ?? 0}</div>
            {d?.submit.running ? <div className="dk-sub"><i className="dk-pulse" />Claude is submitting in Chrome…{" "}
              <button className="btnx sm" onClick={() => post("/api/campaigns/submit/stop", {}, "Stopped")}>■ Stop</button></div>
              : <button className={"pk-go" + (armed ? " armed" : "")} disabled={!t?.to_submit}
                onClick={() => { if (!armed) { setArmed(true); return; } setArmed(false); post("/api/campaigns/submit", {}, "Claude is opening the campaign pages…"); }}>
                {armed ? `Click again: submit ${t?.to_submit} link${t?.to_submit === 1 ? "" : "s"}` : "Submit with Claude"}</button>}
          </div>
        </div>
        {d && (d.submit.running || d.submit.result) && <div className="log dk-claude" style={{ marginTop: 12 }}>
          {d.submit.running ? (d.submit.log.join("\n") || "Starting Claude…") : d.submit.result}</div>}

        <div className="dk-h">Your campaigns</div>
        {d && !mine.length && <div className="note">None joined yet. Join one on its own page (Claude never joins for you), then press <b>I joined it</b> below and paste its rules.</div>}
        {mine.map(c => <CampaignCard key={c.id} c={c} post={post} />)}

        <div className="dk-h">Campaigns you can join <span className="lv-sub">from campaign-finder · re-check before joining, budgets change daily</span></div>
        {other.map(c => <CampaignCard key={c.id} c={c} post={post} />)}

        <div className="dk-h">Submissions</div>
        {d && !d.submissions.length ? <div className="note">Nothing yet. When a clip of a joined campaign goes live, its link lands here to submit.</div> :
          <section className="dk-panel ern-subs">
            {d?.submissions.map(s => (
              <div key={`${s.clip}|${s.platform}|${s.campaign}`} className="ern-sub">
                <div><b>{s.title}</b><span className="lv-sub">{d.campaigns.find(c => c.id === s.campaign)?.name || s.campaign} · {s.platform} · {ago(s.updated)}{s.note ? ` · ${s.note}` : ""}</span></div>
                <a className="btnx sm" href={s.url} target="_blank" rel="noreferrer">Post ↗</a>
                <select className="inp ern-sel" value={s.status} onChange={e => post("/api/campaigns/submission", { clip: s.clip, platform: s.platform, campaign: s.campaign, status: e.target.value })}>
                  {STATUS.map(k => <option key={k} value={k}>{SNAME[k]}</option>)}
                </select>
              </div>))}
          </section>}

        <div className="dk-h">Payouts</div>
        <Payouts d={d} post={post} />
      </main>
      <Toast m={msg} />
    </div>
  );
}

function CampaignCard({ c, post }: { c: Campaign; post: (p: string, b: Record<string, unknown>, ok?: string) => void }) {
  const [rules, setRules] = useState(c.rules_text || ""), [submitUrl, setSubmitUrl] = useState(c.submit_url || "");
  useEffect(() => setRules(c.rules_text || ""), [c.rules_text]);
  const r = c.rules || {}, e = c.earn;
  const upload = (f: File | undefined) => {
    if (!f) return;
    if (f.type !== "image/png") { post("/api/campaigns/overlay", { id: c.id, data: "" }); return; }
    const rd = new FileReader();
    rd.onload = () => post("/api/campaigns/overlay", { id: c.id, data: rd.result }, "Overlay saved: new clips of this campaign get it");
    rd.readAsDataURL(f);
  };
  const facts = [
    r.min_sec || r.max_sec ? `${r.min_sec || 0}–${r.max_sec || "∞"} s` : null,
    r.overlay_required ? `overlay required (${r.overlay_position || "position not given"})` : null,
    r.caption_text ? `caption: “${r.caption_text}”` : null,
    r.hashtags?.length ? r.hashtags.map(h => h.startsWith("#") ? h : "#" + h).join(" ") : null,
    r.min_payout || r.max_payout ? `payout per post $${r.min_payout ?? 0}–${r.max_payout ?? "∞"}` : null,
  ].filter(Boolean) as string[];
  return (
    <section className={"dk-panel ern-c" + (c.joined ? " on" : "")}>
      <div className="ern-ch">
        <div className="ern-ct"><b>{c.name}</b>
          <span className="lv-sub">{rate(c)} · {c.platforms.join(", ")}{c.budget ? ` · ${c.budget}` : ""} · checked {c.checked}</span>
          <span className="lv-sub">{c.any_gta ? "any GTA 6 streamer" : `clips of ${c.creators.join(", ") || "unknown"}`} · {c.clips.eligible} of your clips qualify{c.clips.fix ? ` · ${c.clips.fix} need a re-render` : ""}</span>
        </div>
        <a className="btnx sm" href={c.url} target="_blank" rel="noreferrer">Campaign page ↗</a>
        <button className={"btnx sm" + (c.joined ? " q-ok" : "")} onClick={() => post("/api/campaigns/update", { id: c.id, joined: !c.joined }, c.joined ? "Marked not joined" : "Joined: paste its rules next")}
          title="Join on the campaign's own page first; this only tells the desk you did">{c.joined ? "✓ Joined" : "I joined it"}</button>
      </div>
      {c.warning && <div className="note warn">⚠ {c.warning}</div>}
      {c.joined && <>
        {e && <div className="ern-earn">
          <span>{kfmt(e.views)} views</span><span>estimated {money(e.estimate)}{e.below_min ? ` (${e.below_min} post${e.below_min === 1 ? "" : "s"} under the minimum payout)` : ""}</span>
          <span>paid {money(e.paid)}</span>
          <span>{STATUS.filter(k => e.subs[k]).map(k => `${e.subs[k]} ${SNAME[k]}`).join(" · ") || "no submissions yet"}</span>
        </div>}
        <div className="field"><label>Rules · paste them from the campaign page; Claude turns them into settings</label>
          <textarea className="inp" rows={4} value={rules} onChange={ev => setRules(ev.target.value)} placeholder="Everything the campaign page says about what to clip, length, overlay, captions, payouts…" />
          <div className="ern-row" style={{ marginTop: 8 }}>
            <button className="btnx sm" disabled={rules === (c.rules_text || "") || c.parse_state === "running"}
              onClick={() => post("/api/campaigns/update", { id: c.id, rules_text: rules }, "Saved: Claude is reading the rules")}>Save rules</button>
            {c.parse_state === "running" && <span className="lv-sub"><i className="dk-pulse" />Claude is reading them…</span>}
            {c.parse_state === "failed" && <span className="lv-chip hot" title={c.parse_error || ""}>couldn't read them</span>}
          </div>
        </div>
        {(facts.length > 0 || r.notes) && <div className="note">{facts.map(f => <div key={f}>· {f}</div>)}{r.notes && <div>{r.notes}</div>}
          {!!r.banned?.length && <div>Not allowed: {r.banned.join("; ")}</div>}</div>}
        <div className="lv-row">
          <div className="field"><label>Overlay / logo (PNG) {r.overlay_required ? "· required" : "· if the campaign asks for one"}</label>
            <div className="ern-row">
              {c.overlay && <img className="ern-ov" src={c.overlay} alt="overlay" />}
              <label className="btnx sm">{c.overlay ? "Replace" : "Add PNG"}<input type="file" accept="image/png" hidden onChange={ev => upload(ev.target.files?.[0])} /></label>
              {c.overlay && <button className="btnx sm ghost" onClick={() => post("/api/campaigns/overlay", { id: c.id, remove: true }, "Overlay removed")}>Remove</button>}
            </div>
          </div>
          <div className="field"><label>Submit page · where Claude pastes the post links</label>
            <input className="inp" placeholder={c.url} value={submitUrl} onChange={ev => setSubmitUrl(ev.target.value)}
              onBlur={() => { if (submitUrl !== (c.submit_url || "")) post("/api/campaigns/update", { id: c.id, submit_url: submitUrl }, "Saved"); }} />
          </div>
        </div>
      </>}
    </section>
  );
}

function Payouts({ d, post }: { d: Data | null; post: (p: string, b: Record<string, unknown>, ok?: string) => void }) {
  const [camp, setCamp] = useState(""), [amount, setAmount] = useState(""), [note, setNote] = useState("");
  const list = d?.campaigns.filter(c => c.joined || c.earn) || [];
  return (
    <section className="dk-panel">
      <div className="ern-row">
        <select className="inp" style={{ maxWidth: 320 }} value={camp} onChange={e => setCamp(e.target.value)}>
          <option value="">Campaign…</option>{list.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input className="inp" style={{ maxWidth: 120 }} placeholder="$ amount" value={amount} onChange={e => setAmount(e.target.value)} />
        <input className="inp" style={{ maxWidth: 260 }} placeholder="note (optional)" value={note} onChange={e => setNote(e.target.value)} />
        <button className="btnx sm" disabled={!camp || !(+amount > 0)}
          onClick={() => { post("/api/campaigns/payout", { campaign: camp, amount: +amount, note }, "Payout recorded"); setAmount(""); setNote(""); }}>Add payout</button>
      </div>
      <div className="lv-sub" style={{ marginTop: 8 }}>Only amounts the campaign actually paid you (payouts.csv).</div>
      {!!d?.payouts.length && <div className="list" style={{ marginTop: 10 }}>
        {[...d.payouts].reverse().map((p, i) => <div className="li" key={i}>{p.date} · {d.campaigns.find(c => c.id === p.campaign)?.name || p.campaign} · <b>${p.amount}</b>{p.note ? ` · ${p.note}` : ""}</div>)}
      </div>}
    </section>
  );
}
