"""Re-rank the unposted clips in the queue with Claude (the Queue page's "Re-rank" button runs this).

Gives `claude -p` each unposted clip: title, hook, what's said in it, creator, length, warnings, how fresh the source
is, and what was posted recently. Claude returns the posting order with a one-line reason per clip, and can put a
clip on hold (e.g. it needs a re-cut). Saved to queue/order.json; queue/uploader.py orders the queue by it, so the
Queue page and the auto-uploader both follow it.

Rendered clips that aren't in the queue yet (the Queue page's "Not queued") are ranked too: they're added to the queue
in Claude's order, and the ones not worth posting go on hold (the auto-uploader skips holds). Clips removed from the
queue on the Queue page (queue/removed.json) stay out. --queued-only ranks just the queue, as before.

usage: python scripts/rerank.py [--queued-only]      (from the clip desk folder)
"""
import json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uploader as U  # noqa: E402

ROOT, Q = U.ROOT, U.Q

ORDER, STATUS = Q / "order.json", Q / "uploads" / "rerank.json"
REMOVED = Q / "removed.json"   # ids taken out of the queue on the Queue page: never added back by a re-rank
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")


def not_queued(queued):
    """Rendered clips (jobs/<job>/edits/<name>.mp4 + .json) that aren't queue items and weren't removed from the queue."""
    removed = set(U.jload(REMOVED, []))
    out = []
    for mp4 in sorted((ROOT / "jobs").glob("*/edits/*.mp4")):
        job, name = mp4.parent.parent.name, mp4.stem
        cid = f"{job}/{name}"
        if job.startswith(".") or name.startswith(".") or not NAME_RE.match(name) or cid in queued or cid in removed:
            continue
        if not mp4.with_suffix(".json").exists():
            continue
        try:
            out.append(U.added_item(job, name))
        except SystemExit:
            pass
    return out
MAIN = ("tiktok", "youtube", "instagram")


def status(**kw):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    cur = U.jload(STATUS, {})
    cur.update(kw)
    U.jsave(STATUS, cur)


def spoken(job, name):
    """What's said in the clip, from the job transcript (source seconds in the clip's .json)."""
    d = ROOT / "jobs" / job
    meta = U.jload(d / "edits" / f"{name}.json", {})
    words = U.jload(d / "transcript.json", [])
    words = words if isinstance(words, list) else words.get("words", [])
    s, e = meta.get("start"), meta.get("end")
    if s is None or e is None:
        return ""
    return " ".join(w["w"] for w in words if s - 0.2 <= w["s"] <= e + 0.2)[:600]


def source_age(job):
    info = U.jload(ROOT / "jobs" / job / "info.json", {})
    t = info.get("timestamp") or (time.mktime(datetime.strptime(info["upload_date"], "%Y%m%d").timetuple()) if info.get("upload_date") else None)
    return round((time.time() - t) / 3600) if t else None


def main():
    status(state="running", started=time.time(), error=None)
    st, skips = U.jload(U.STATE, {}), U.jload(U.SKIPS, {})
    clips, posted = [], []
    queue = U.items()
    new = {} if "--queued-only" in sys.argv else {it["id"]: it for it in not_queued(set(queue))}
    for it in list(queue.values()) + list(new.values()):
        on = [p for p in MAIN if st.get(it["id"], {}).get(p, {}).get("posted")]
        if on:
            posted.append({"id": it["id"], "creator": it["creator"], "title": it["title"], "on": on,
                           "when": max(st[it["id"]][p].get("updated", 0) for p in on)})
            continue
        if it["id"] in skips:
            continue
        job, _, name = it["id"].partition("/")
        clips.append({"id": it["id"], "title": it["title"], "hook": it.get("hook"), "creator": it["creator"],
                      "seconds": it["duration"], "layout": U.jload(ROOT / "jobs" / job / "edits" / f"{name}.json", {}).get("layout"),
                      "warnings": list(dict.fromkeys((it.get("warnings") or []) + U.qa_warnings(it["id"]))),
                      "note": "rendered, not in the queue yet" if it["id"] in new else it.get("why"),
                      "source_age_hours": source_age(job), "said": spoken(job, name)})
    if not clips:
        status(state="done", finished=time.time(), count=0); print("nothing to rank"); return
    recent = sorted(posted, key=lambda x: -x["when"])[:8]
    learning = (ROOT / "learning.md").read_text(encoding="utf-8")[-2000:] if (ROOT / "learning.md").exists() else None
    try:   # real numbers of the posts so far (scoreboard.csv): read them now, then show Claude the best and worst
        import stats
        stats.refresh()
        numbers = stats.learning_examples(5)
    except Exception:
        numbers = ""
    prompt = f"""You run the posting order of a faceless GTA 6 clip page (TikTok, Reels, Shorts). Rank these unposted clips:
which one should go out next, then after it, and so on. It's {time.strftime('%Y-%m-%d %H:%M')}, GTA 6 launch week.

What matters, in order:
1. Hook: a clear, surprising first line that works with the sound off, a named streamer, a payoff.
2. Freshness: newer sources and launch-week moments beat older ones.
3. Length: 12-25 s is best; 25-40 s needs a much stronger hook; over 60 s goes last.
4. Variety: don't put the same creator back to back, and avoid repeating the creators of the most recent posts first.
5. Warnings: a warning that says the clip must be re-cut, or anything that makes it a bad post, means hold.
6. Not every clip deserves a post: hold the weak ones (no real hook or payoff, needs context, a near-duplicate of a
   better clip, spoilers/leaks) and say why in the reason. Holding a third of them is fine when they're weak.
7. A warning starting "QA:" comes from a check of the finished clip's frames: "don't post" and "re-cut" both mean hold.
{"8. These rules from the page's own results override the above:" + chr(10) + learning if learning else ""}
{numbers}
Most recent posts (newest first): {json.dumps([{k: r[k] for k in ("creator", "title", "on")} for r in recent], ensure_ascii=False)}

Clips (said = what's spoken in the clip):
{json.dumps(clips, ensure_ascii=False, indent=1)}

Reply with JSON only, no prose and no code fence: a list of every clip id above, best first:
[{{"id": "...", "reason": "one short line: why it's here", "hold": false}}, ...]
"""
    exe = os.environ.get("CLAUDE_BIN") or "claude"
    try:
        r = subprocess.run([exe, "-p", prompt, "--model", os.environ.get("CLAUDE_MODEL_RANK", "opus"), "--output-format", "json"], cwd=str(ROOT), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        out = json.loads(r.stdout)
        text = out.get("result", "") if isinstance(out, dict) else ""
        m = re.search(r"\[.*\]", text, re.S)
        order = json.loads(m.group(0)) if m else None
    except FileNotFoundError:
        status(state="error", error="claude CLI not found", finished=time.time()); sys.exit("claude CLI not found")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, AttributeError) as e:
        status(state="error", error=f"couldn't read Claude's ranking: {e}"[:200], finished=time.time()); sys.exit(1)
    ids = {c["id"] for c in clips}
    order = [{"id": o["id"], "reason": str(o.get("reason", ""))[:200], "hold": bool(o.get("hold"))}
             for o in order or [] if isinstance(o, dict) and o.get("id") in ids]
    order += [{"id": i, "reason": "", "hold": False} for i in [c["id"] for c in clips] if i not in {o["id"] for o in order}]
    if new:   # the not-yet-queued clips join the queue, in Claude's order (holds included, so they show as on hold)
        added = U.jload(U.ADDED, [])
        have = {x["id"] for x in added}
        U.jsave(U.ADDED, added + [it for cid, it in new.items() if cid not in have])
    U.jsave(ORDER, {"at": time.time(), "by": "claude", "order": order})
    U.log(event="reranked", count=len(order), first=order[0]["id"] if order else None)
    status(state="done", finished=time.time(), count=len(order), added=len(new), held=sum(1 for o in order if o["hold"]))
    for k, o in enumerate(order, 1):
        print(f"{k:2}. {'HOLD ' if o['hold'] else ''}{o['id']}: {o['reason']}")


if __name__ == "__main__":
    main()
