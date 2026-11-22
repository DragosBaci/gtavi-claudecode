"""Read the real numbers of every posted clip into scoreboard.csv, and turn them into examples for Claude.

Posts come from queue/uploads/log.jsonl ("posted" events with a URL). yt-dlp reads each public post's counters
(YouTube Shorts and TikTok work without an account; Instagram only with browser cookies, CLIP_COOKIES_BROWSER).
A number that can't be read stays blank: never guessed.

scoreboard.csv: one row per (clip, platform), the learning-loop columns plus url and the latest read. views_12h,
views_24h and views_72h are filled by the first read made after that age (and never overwritten), views_now by every read.

learning_examples() is what the pick, rank and rerank prompts get: the best and worst posts that are at least 24 h old,
with their title, layout, length and views, so Claude learns what this page's audience actually watches.

usage: python stats.py            read every post (from the clip desk folder)
       python stats.py --show     print the examples block the prompts get
"""
import argparse, csv, json, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, load_json, ytdlp_json  # noqa: E402

LOG = ROOT / "queue" / "uploads" / "log.jsonl"
BOARD = ROOT / "scoreboard.csv"
COLS = ["post_id", "date", "time", "platform", "pillar", "format", "hook", "length_sec", "credit", "test_name",
        "views_12h", "views_24h", "views_72h", "likes", "shares", "replies", "saves", "followers_at_read",
        "url", "layout", "views_now", "read_at", "hours_at_read"]


def posts():
    out = {}
    if not LOG.exists():
        return []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "posted" and e.get("url"):
            out[(e["id"], e["platform"])] = e
    return list(out.values())


def clip_meta(cid):
    job, _, name = cid.partition("/")
    try:
        return load_json(ROOT / "jobs" / job / "edits" / f"{name}.json")
    except (OSError, ValueError):
        return {}


def read_counts(url):
    try:
        j = ytdlp_json(url, ["--skip-download", "--no-playlist"])
    except BaseException as e:   # common.run exits on failure
        if isinstance(e, KeyboardInterrupt):
            raise
        return None
    return {"views": j.get("view_count"), "likes": j.get("like_count"), "replies": j.get("comment_count"),
            "shares": j.get("repost_count")}


def load_board():
    if not BOARD.exists():
        return {}
    with open(BOARD, newline="", encoding="utf-8") as f:
        return {(r["post_id"], r["platform"]): r for r in csv.DictReader(f)}


def save_board(rows):
    tmp = BOARD.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r.get("date", ""), r.get("time", ""))):
            w.writerow({k: r.get(k, "") for k in COLS})
    tmp.replace(BOARD)


def refresh(max_age_days=14):
    """Read every post younger than max_age_days. Returns the number of posts read."""
    rows = load_board()
    todo = []
    for e in posts():
        at = datetime.strptime(e["at"], "%Y-%m-%d %H:%M:%S")
        age_h = (time.time() - at.timestamp()) / 3600
        key = (e["id"], e["platform"])
        r = rows.get(key) or {}
        meta = clip_meta(e["id"])
        r.update({"post_id": e["id"], "platform": e["platform"], "url": e["url"], "date": at.strftime("%Y-%m-%d"),
                  "time": at.strftime("%H:%M"), "hook": r.get("hook") or meta.get("title") or meta.get("hook") or "",
                  "format": r.get("format") or ("split-screen" if meta.get("layout") == "split" else "clip"),
                  "layout": meta.get("layout") or r.get("layout", ""), "credit": meta.get("source_channel") or r.get("credit", ""),
                  "length_sec": r.get("length_sec") or (round(sum((s[3] if len(s) > 2 else s[1] - s[0]) for s in meta.get("segments", [])), 1)
                                                          if meta.get("segments") else "")})
        rows[key] = r
        if age_h <= max_age_days * 24:
            todo.append((key, age_h))

    def one(item):
        key, age_h = item
        return key, age_h, read_counts(rows[key]["url"])
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(one, todo))
    n = 0
    for key, age_h, c in results:
        if not c or c.get("views") is None:
            continue
        r = rows[key]; n += 1
        r.update(views_now=c["views"], read_at=time.strftime("%Y-%m-%d %H:%M"), hours_at_read=round(age_h, 1))
        for k in ("likes", "replies", "shares"):
            if c.get(k) is not None:
                r[k] = c[k]
        for h in (12, 24, 72):   # the first read after that age fills it; later reads don't overwrite
            if age_h >= h and not r.get(f"views_{h}h"):
                r[f"views_{h}h"] = c["views"]
    save_board(rows)
    return n


def _views(r):
    for k in ("views_72h", "views_24h", "views_now"):
        try:
            return int(float(r[k]))
        except (KeyError, ValueError, TypeError):
            continue
    return None


def learning_examples(n=4):
    """The best and worst n posts that are at least 24 h old, as a prompt block ("" until there are enough)."""
    rows = [r for r in load_board().values() if r.get("views_24h") and _views(r) is not None]
    if len(rows) < 4:
        return ""
    rows.sort(key=lambda r: -_views(r))
    k = min(n, len(rows) // 2)

    def line(r):
        return (f"- {_views(r):,} views on {r['platform']}: \"{r.get('hook', '')}\" ({r.get('credit', '')}, "
                f"{r.get('length_sec', '?')} s, layout {r.get('layout', '?')})")
    med = sorted(_views(r) for r in rows)[len(rows) // 2]
    return ("What this page's audience actually watched (real numbers, posts at least 24 h old, median "
            f"{med:,} views):\nBest:\n" + "\n".join(line(r) for r in rows[:k]) +
            "\nWorst:\n" + "\n".join(line(r) for r in rows[-k:]) +
            "\nPick and judge like the best ones; avoid what the worst ones have in common.\n")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--show", action="store_true"); a = ap.parse_args()
    if a.show:
        print(learning_examples() or "not enough posts with 24 h numbers yet")
        return
    n = refresh()
    print(f"read {n} of {len(posts())} posts -> {BOARD}")
    print(learning_examples() or "not enough posts with 24 h numbers yet for examples")


if __name__ == "__main__":
    main()
