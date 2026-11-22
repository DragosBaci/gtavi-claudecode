"""Paid clipping campaigns: which clips qualify, their rules at render, submissions and earnings.

Data (in the clip desk folder):
  campaigns.json          every campaign campaign-finder found: creators it pays for, platforms, rate, budget, link,
                          whether you joined, and its rules (pasted by you, structured by Claude: `parse`)
  campaigns/<id>/         files a campaign requires on the clip (overlay.png: a logo or a full-frame overlay)
  queue/submissions.json  one entry per (clip, platform, campaign): to_submit -> submitted -> approved / rejected -> paid
  payouts.csv             real payouts only: date, campaign, amount, note

A clip qualifies for a JOINED campaign when its source creator matches the campaign's creators (or the campaign takes
any GTA 6 streamer) and it passes the campaign's rules (length, required overlay). Matching is done when it's needed,
so joining a campaign later shows which existing clips already qualify and which need a re-render.

usage: python campaigns.py list                      campaigns and how many clips qualify
       python campaigns.py match <job>/<clip>        which joined campaigns this clip qualifies for, and why not
       python campaigns.py parse <id>                Claude turns the pasted rules text into structured rules
       python campaigns.py earnings                  views and estimated / paid earnings per campaign
       python campaigns.py submitted <job>/<clip> <platform> <campaign> [--status submitted|approved|rejected|paid] [--note ..]
"""
import argparse, csv, json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_json, save_json  # noqa: E402

FILE = ROOT / "campaigns.json"
FILES = ROOT / "campaigns"
SUBS = ROOT / "queue" / "submissions.json"
PAYOUTS = ROOT / "payouts.csv"
PLATFORMS = ("tiktok", "youtube", "instagram")
POSITIONS = ("full", "top-left", "top-right", "bottom-left", "bottom-right", "top", "bottom", "center")
STATUSES = ("to_submit", "submitted", "approved", "rejected", "paid", "skipped")


# ---------------------------------------------------------------- data

def load():
    try:
        return load_json(FILE)
    except (OSError, ValueError):
        return {"updated": None, "campaigns": []}


def save(d):
    d["updated"] = time.time()
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def get(cid):
    return next((c for c in load()["campaigns"] if c["id"] == cid), None)


def update(cid, **kw):
    d = load()
    c = next((c for c in d["campaigns"] if c["id"] == cid), None)
    if not c:
        raise ValueError(f"no campaign {cid}")
    if kw.get("joined") and not c.get("joined"):
        kw["joined_at"] = time.time()
    c.update(kw)
    save(d)
    return c


def joined():
    return [c for c in load()["campaigns"] if c.get("joined") and c.get("status", "live") == "live"]


def overlay_file(c):
    f = FILES / c["id"] / "overlay.png"
    return f if f.exists() else None


# ---------------------------------------------------------------- matching

def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def creator_match(c, *names):
    """True when one of the clip's creator names (channel, uploader, Twitch login) is one of the campaign's creators."""
    if c.get("any_gta"):
        return True
    keys = [_norm(k) for k in c.get("creators") or [] if _norm(k)]
    have = [_norm(n) for n in names if _norm(n)]
    return any(k in h or h in k for k in keys for h in have if len(h) >= 4)


def clip_meta(clip_id):
    job, _, name = clip_id.partition("/")
    try:
        meta = load_json(ROOT / "jobs" / job / "edits" / f"{name}.json")
    except (OSError, ValueError):
        meta = {}
    try:
        info = load_json(ROOT / "jobs" / job / "info.json")
    except (OSError, ValueError):
        info = {}
    return meta, info


def clip_seconds(meta):
    segs = meta.get("segments") or []
    if not segs:
        return None
    ov = (meta.get("transition") or {}).get("overlap") or 0
    return round(sum((s[3] if len(s) > 2 else s[1] - s[0]) for s in segs) - ov, 1)


def check(c, meta, info):
    """The campaign's rules this clip breaks ([] = it qualifies)."""
    r = c.get("rules") or {}
    out = []
    secs = clip_seconds(meta)
    if secs is not None and r.get("min_sec") and secs < r["min_sec"]:
        out.append(f"shorter than {r['min_sec']} s")
    if secs is not None and r.get("max_sec") and secs > r["max_sec"]:
        out.append(f"longer than {r['max_sec']} s")
    if r.get("overlay_required"):
        if not overlay_file(c):
            out.append("needs the campaign's overlay: add it on the Earn page")
        elif meta.get("campaign") != c["id"]:
            out.append("rendered without the campaign's overlay: re-render it")
    return out


def match(clip_id, only_joined=True):
    """[{id, name, rate, pool, platforms, eligible, problems}] for the campaigns this clip's creator is paid by."""
    meta, info = clip_meta(clip_id)
    names = (meta.get("source_channel"), info.get("channel"), info.get("uploader"), info.get("uploader_id"),
             clip_id.split("/")[0].split("-")[1] if clip_id.startswith("live-") else None)
    out = []
    for c in load()["campaigns"]:
        if only_joined and not c.get("joined"):
            continue
        if c.get("status", "live") != "live" or not creator_match(c, *names):
            continue
        problems = check(c, meta, info)
        out.append({"id": c["id"], "name": c["name"], "rate": c.get("rate_per_1k"), "pool": c.get("pool"),
                    "platforms": c.get("platforms") or list(PLATFORMS), "eligible": not problems, "problems": problems})
    out.sort(key=lambda m: (not m["eligible"], -(m["rate"] or 0)))
    return out


def for_render(job, force=None):
    """The campaign a new render of this job is made for: `force` (a campaign run), else the best-paying joined one
    whose creators match the source. Returns the campaign dict or None."""
    if force:
        return get(force)
    try:
        info = load_json(ROOT / "jobs" / job / "info.json")
    except (OSError, ValueError):
        info = {}
    names = (info.get("channel"), info.get("uploader"), info.get("uploader_id"), job.split("-")[1] if job.startswith("live-") else None)
    cs = [c for c in joined() if creator_match(c, *names) and not c.get("any_gta")] or \
         [c for c in joined() if c.get("any_gta")]
    return max(cs, key=lambda c: c.get("rate_per_1k") or 0) if cs else None


def render_args(c):
    """cut.py arguments for a campaign: its overlay, and the campaign id saved in the clip's .json."""
    if not c:
        return []
    args = ["--campaign", c["id"]]
    f = overlay_file(c)
    r = c.get("rules") or {}
    if f:
        pos = r.get("overlay_position") if r.get("overlay_position") in POSITIONS else "top-right"
        width = float(r.get("overlay_width") or (1.0 if pos == "full" else 0.22))
        args += ["--overlay", f"{f}:{pos}:{width:.2f}"]
    return args


def caption_extra(c):
    """Text a campaign requires in the post caption (its tag line, hashtags, a mention)."""
    if not c:
        return ""
    r = c.get("rules") or {}
    parts = [r.get("caption_text") or ""]
    tags = [t if t.startswith("#") else "#" + t for t in r.get("hashtags") or []]
    if tags:
        parts.append(" ".join(tags))
    return "\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------- submissions

def subs():
    try:
        return load_json(SUBS)
    except (OSError, ValueError):
        return []


def save_subs(lst):
    SUBS.parent.mkdir(parents=True, exist_ok=True)
    save_json(SUBS, lst)


def on_posted(clip_id, platform, url):
    """A clip went live on a platform: one to_submit entry for every joined campaign it qualifies for there."""
    if not url:
        return []
    lst, added = subs(), []
    for m in match(clip_id):
        if not m["eligible"] or platform not in m["platforms"]:
            continue
        if any(s["clip"] == clip_id and s["platform"] == platform and s["campaign"] == m["id"] for s in lst):
            continue
        e = {"clip": clip_id, "platform": platform, "campaign": m["id"], "url": url, "status": "to_submit",
             "posted_at": time.time(), "updated": time.time()}
        lst.append(e); added.append(e)
    if added:
        save_subs(lst)
    return added


def set_status(clip_id, platform, campaign, status, note=""):
    if status not in STATUSES:
        raise ValueError(f"status: one of {STATUSES}")
    lst = subs()
    e = next((s for s in lst if s["clip"] == clip_id and s["platform"] == platform and s["campaign"] == campaign), None)
    if not e:
        raise ValueError("no such submission")
    e.update(status=status, updated=time.time(), **({"note": note[:300]} if note else {}))
    if status == "submitted":
        e["submitted_at"] = time.time()
    save_subs(lst)
    return e


def pending_submissions():
    """to_submit entries with what the submitter needs: the campaign's submit page and the post link."""
    cs = {c["id"]: c for c in load()["campaigns"]}
    return [dict(s, campaign_name=cs.get(s["campaign"], {}).get("name"), submit_url=cs.get(s["campaign"], {}).get("submit_url")
                 or cs.get(s["campaign"], {}).get("url"))
            for s in subs() if s["status"] == "to_submit" and s["campaign"] in cs]


# ---------------------------------------------------------------- earnings

def payouts():
    if not PAYOUTS.exists():
        return []
    with open(PAYOUTS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def add_payout(campaign, amount, note=""):
    new = not PAYOUTS.exists()
    with open(PAYOUTS, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "campaign", "amount", "note"])
        w.writerow([time.strftime("%Y-%m-%d"), campaign, f"{float(amount):.2f}", note])


def earnings():
    """Per campaign: submissions by status, views of the submitted posts (scoreboard.csv), an ESTIMATE of what those
    views are worth (rate x views, the platform fee taken off, each post's min/max payout applied) and real payouts."""
    try:
        import stats
        board = stats.load_board()
    except Exception:
        board = {}
    paid = {}
    for p in payouts():
        try:
            paid[p["campaign"]] = paid.get(p["campaign"], 0.0) + float(p["amount"])
        except (KeyError, ValueError):
            pass
    out = []
    for c in load()["campaigns"]:
        mine = [s for s in subs() if s["campaign"] == c["id"]]
        if not c.get("joined") and not mine and c["id"] not in paid:
            continue
        r = c.get("rules") or {}
        fee = float(c.get("fee") or 0)
        views, est, below = 0, 0.0, 0
        for s in mine:
            if s["status"] in ("rejected", "skipped"):
                continue
            row = board.get((s["clip"], s["platform"])) or {}
            try:
                v = int(float(row.get("views_now") or 0))
            except ValueError:
                v = 0
            views += v
            if c.get("rate_per_1k"):
                e = v / 1000 * float(c["rate_per_1k"]) * (1 - fee)
                if r.get("min_payout") and e < float(r["min_payout"]):
                    below += 1; e = 0.0
                if r.get("max_payout"):
                    e = min(e, float(r["max_payout"]))
                est += e
        by = {k: sum(1 for s in mine if s["status"] == k) for k in STATUSES}
        out.append({"id": c["id"], "name": c["name"], "joined": bool(c.get("joined")), "rate": c.get("rate_per_1k"),
                    "pool": c.get("pool"), "subs": by, "views": views,
                    "estimate": round(est, 2) if c.get("rate_per_1k") else None, "below_min": below,
                    "paid": round(paid.get(c["id"], 0.0), 2)})
    return out


# ---------------------------------------------------------------- rules: pasted text -> structured

PARSE = """You read the rules of a paid clipping campaign and turn them into settings for a clip tool. Don't use tools.

Campaign: {name} ({url}). Creators it pays for (as listed): {creators}.
Rules text, pasted by the clipper from the campaign page:
---
{text}
---

Answer with only this JSON (null where the rules say nothing; never invent a number):
{{"creators": [<creator / channel names whose content may be clipped, or [] if any GTA 6 streamer>],
  "any_gta": <true if any GTA 6 streamer's content is allowed>,
  "platforms": [<of "tiktok", "youtube", "instagram" that pay>],
  "min_sec": <minimum clip length in seconds or null>, "max_sec": <maximum or null>,
  "overlay_required": <true if a logo / overlay / watermark of the campaign must be on the video>,
  "overlay_position": <"full" for a full-frame overlay, else "top-left"|"top-right"|"bottom-left"|"bottom-right"|"top"|"bottom"|"center", null if not said>,
  "overlay_width": <logo width as a share of the video width, e.g. 0.22, or null>,
  "caption_text": <exact text the post caption must contain (a mention, a link, a line), or null>,
  "hashtags": [<required hashtags>],
  "min_payout": <minimum payout per post in USD or null>, "max_payout": <maximum per post in USD or null>,
  "banned": [<short: content that is not allowed>],
  "notes": "<anything else a clipper must follow, under 60 words>"}}
"""


def parse(cid):
    c = get(cid)
    if not c or not (c.get("rules_text") or "").strip():
        raise ValueError("paste the campaign's rules first")
    update(cid, parse_state="running")
    try:
        exe = os.environ.get("CLAUDE_BIN") or "claude"
        prompt = PARSE.format(name=c["name"], url=c.get("url"), creators=", ".join(c.get("creators") or []) or "unknown",
                              text=c["rules_text"][:12000])
        r = subprocess.run([exe, "-p", prompt, "--model", os.environ.get("CLAUDE_MODEL_PICK", "opus"), "--output-format", "json"],
                           cwd=str(ROOT), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        text = json.loads(r.stdout).get("result") or ""
        j = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        rules = {k: j.get(k) for k in ("min_sec", "max_sec", "overlay_required", "overlay_position", "overlay_width",
                                       "caption_text", "hashtags", "min_payout", "max_payout", "banned", "notes")}
        kw = {"rules": {**(c.get("rules") or {}), **{k: v for k, v in rules.items() if v not in (None, [], "")}},
              "parse_state": "done", "parse_error": None}
        if j.get("creators"):
            kw["creators"] = j["creators"]
        if j.get("any_gta") is not None:
            kw["any_gta"] = bool(j["any_gta"])
        if j.get("platforms"):
            kw["platforms"] = [p for p in j["platforms"] if p in PLATFORMS]
        return update(cid, **kw)
    except Exception as e:
        update(cid, parse_state="failed", parse_error=str(e)[-300:])
        raise


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list"); sub.add_parser("earnings")
    sub.add_parser("match").add_argument("clip")
    sub.add_parser("parse").add_argument("id")
    s = sub.add_parser("submitted"); s.add_argument("clip"); s.add_argument("platform"); s.add_argument("campaign")
    s.add_argument("--status", default="submitted", choices=STATUSES); s.add_argument("--note", default="")
    sub.add_parser("pending")
    a = ap.parse_args()
    if a.cmd == "list":
        for c in load()["campaigns"]:
            print(f"{'✓' if c.get('joined') else ' '} {c['id']:<28} {c.get('rate_per_1k') or c.get('pool') or '?'!s:<10} {c['name']}")
    elif a.cmd == "match":
        print(json.dumps(match(a.clip, only_joined=False), indent=1))
    elif a.cmd == "parse":
        print(json.dumps(parse(a.id).get("rules"), indent=1))
    elif a.cmd == "earnings":
        print(json.dumps(earnings(), indent=1))
    elif a.cmd == "pending":
        print(json.dumps(pending_submissions(), indent=1))
    elif a.cmd == "submitted":
        print(json.dumps(set_status(a.clip, a.platform, a.campaign, a.status, a.note)))


if __name__ == "__main__":
    main()
