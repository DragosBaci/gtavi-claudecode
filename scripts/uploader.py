"""Helper for the /upload-next skill: Claude posts one clip per run to TikTok, YouTube Shorts and Instagram Reels.

usage (run from the clip desk folder, the one with jobs/; the data lives in its queue/ folder):
  python scripts/uploader.py status                 pending clips, the page account, when the last post went out (JSON)
  python scripts/uploader.py account [--name N --handle H]   show or set the account clips are posted as
  python scripts/uploader.py frames <id>            contact sheet of 6 frames for the visual check (prints the path)
  python scripts/uploader.py prepare <id>           upload copy under 9.5 MB + captions per pending platform (JSON)
  python scripts/uploader.py mark <id> <platform> [--url URL]
  python scripts/uploader.py skip <id> --reason "..."          never auto-post this clip
  python scripts/uploader.py fail <id> <platform> --reason "..."   log a failed attempt

Reads the clips added and the text edited on the editor's Queue page (queue/added.json, queue/overrides.json),
plus queue/queue.json if you keep a hand-ranked list there, and writes queue/state.json, so the Queue page shows
what the uploader posted. Everything it does is logged in queue/uploads/log.jsonl.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT  # noqa: E402   the clip desk folder (the current directory, or CLIP_DESK_HOME)

Q = ROOT / "queue"
UP = Q / "uploads"
STATE, SKIPS, LOG = Q / "state.json", UP / "skipped.json", UP / "log.jsonl"
ADDED, OVERRIDES = Q / "added.json", Q / "overrides.json"   # written by the editor's Queue page
ACCOUNT = Q / "accounts.json"   # {"name": ..., "handles": [...]}: the only account the uploader may post as
ORDER = Q / "order.json"   # the last re-rank (rerank.py): posting order, a reason per clip, holds
PLATFORMS = ("youtube", "tiktok", "instagram")
MAX_DUR = {"youtube": 180, "tiktok": 600, "instagram": 180}
LIMIT = 9_500_000          # Claude in Chrome uploads at most 10 MB per file
CHUNK = 9_000_000          # full-quality upload: the original in pieces this big (see prepare / the upload-next skill)
MIN_VIDEO_KBPS = 1500      # below this a 1080x1920 clip looks visibly soft: the upload copy is made at 720x1280
MIN_720_KBPS = 700         # below this even 720x1280 looks soft: too long to auto-upload
FF = shutil.which("ffmpeg") or "ffmpeg"
FP = shutil.which("ffprobe") or "ffprobe"


def jload(p, d):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return d


def jsave(p, obj):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def log(**kw):
    UP.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **kw}, ensure_ascii=False) + "\n")


def platform_text(title, hook, creator, src):
    """Per-platform post text, the same templates as build_queue.py."""
    tag = re.sub(r"[^a-z0-9]+", "", (creator or "").lower()) or "gta6clips"
    tags_core = ["gta6", "gtavi", tag, "gaming"]
    return {
        "tiktok": {"caption": f"{hook}\n\n🎥 {creator}\n" + " ".join("#" + t for t in tags_core + ["gta6clips"])},
        "youtube": {"title": (title if len(title) <= 92 else title[:89] + "…") + " #gta6",
                    "description": f"{hook}\n\n🎥 Credit: {creator}, full stream: {src}\n\n#gta6 #gtavi #{tag} #shorts",
                    "tags": ", ".join(["gta 6", "gta vi", (creator or "").lower(), "gta 6 gameplay", "gta 6 funny moments", "gta 6 reaction"])},
        "instagram": {"caption": f"{hook}\n\n🎥 {creator}\n.\n" + " ".join("#" + t for t in tags_core + ["gta6clips"])},
        "x": {"text": f"{title} 😭\n\n🎥 {creator}\n#GTA6"},
    }


def added_item(job, name):
    """A rendered clip (jobs/<job>/edits/<name>.mp4) as a queue item."""
    d = ROOT / "jobs" / job
    meta, info = jload(d / "edits" / f"{name}.json", {}), jload(d / "info.json", {})
    mp4 = d / "edits" / f"{name}.mp4"
    if not mp4.exists():
        sys.exit(f"no clip jobs/{job}/edits/{name}.mp4")
    dur = float(subprocess.run([FP, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                               capture_output=True, text=True).stdout.strip() or 0)
    creator = meta.get("source_channel") or info.get("channel") or info.get("uploader") or ""
    title = meta.get("title") or meta.get("hook") or name
    src = info.get("webpage_url", "")
    return {"id": f"{job}/{name}", "title": title, "hook": meta.get("hook") or title, "why": "added on the Queue page",
            "warnings": [], "creator": creator, "source_url": src, "source_title": info.get("title"),
            "format": "split-screen" if meta.get("layout") == "split" else "reaction", "duration": round(dur, 1),
            "long": dur > 60, "video": f"/media/{job}/edits/{name}.mp4", "added": time.time(),
            "platforms": platform_text(title, meta.get("hook") or title, creator, src)}


def items():
    """Every queued clip, in queue order: build_queue.py's ranking, then clips added on the Queue page.
    Text edited on the Queue page (overrides.json) replaces the generated text."""
    out = {it["id"]: it for it in jload(Q / "queue.json", {"items": []})["items"]}
    for it in jload(ADDED, []):
        if it["id"] not in out:
            out[it["id"]] = dict(it, rank=len(out) + 1)
    for cid, by_platform in jload(OVERRIDES, {}).items():
        if cid in out:
            for plat, fields in by_platform.items():
                out[cid]["platforms"].setdefault(plat, {}).update(fields)
    order = [o for o in jload(ORDER, {}).get("order", []) if o.get("id") in out]
    if order:   # re-ranked clips first, in Claude's order; everything else (posted, new since) after, as before
        first = {o["id"]: o for o in order}
        ids = [o["id"] for o in order] + [i for i in out if i not in first]
        out = {i: out[i] for i in ids}
        for k, i in enumerate(ids, 1):
            out[i]["rank"] = k
            if i in first:
                out[i]["rank_reason"], out[i]["hold"] = first[i]["reason"], first[i]["hold"]
                out[i]["pinned"] = bool(first[i].get("pinned"))
    return out


def item(cid):
    it = items().get(cid)
    if not it:
        sys.exit(f"unknown clip id {cid!r} (see `status`)")
    return it


def src_path(it):
    return ROOT / "jobs" / it["video"].split("/media/", 1)[1]


def safe(cid):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", cid)


def posted(st, cid, p):
    return bool(st.get(cid, {}).get(p, {}).get("posted"))


def kbps_for(dur):
    return int(LIMIT * 8 / dur / 1000) - 128 - 40   # audio 128k, ~40k container overhead


def status():
    st, skips, now = jload(STATE, {}), jload(SKIPS, {}), time.time()
    last = max((v.get("updated", 0) for d in st.values() for v in d.values() if v.get("posted")), default=0)
    recent = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines()[-30:]] if LOG.exists() else []
    pending = []
    for it in items().values():
        if it["id"] in skips or not src_path(it).exists():
            continue
        todo = [p for p in PLATFORMS if not posted(st, it["id"], p) and it["duration"] <= MAX_DUR[p]]
        if not todo:
            continue
        pending.append({k: it[k] for k in ("id", "rank", "title", "hook", "why", "warnings", "creator", "duration", "format")}
                       | {"rank_reason": it.get("rank_reason"), "hold": bool(it.get("hold")), "pinned": bool(it.get("pinned"))}
                       | {"pending": todo, "already_on": [p for p in PLATFORMS if posted(st, it["id"], p)],
                          "auto_ok": kbps_for(it["duration"]) >= MIN_720_KBPS})
    learning = ROOT / "learning.md"
    print(json.dumps({
        "now": time.strftime("%Y-%m-%d %H:%M"),
        "account": jload(ACCOUNT, None),
        "last_post": time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else None,
        "minutes_since_last_post": round((now - last) / 60) if last else None,
        "posts_last_24h": sum(1 for d in st.values() for v in d.values() if v.get("posted") and now - v.get("updated", 0) < 86400),
        "recent_creators": [e["id"].split("/")[0] for e in recent if e.get("event") == "posted"][-6:],
        "learning_rules": learning.read_text(encoding="utf-8")[-2500:] if learning.exists() else None,
        "skipped": skips, "pending": pending,
    }, ensure_ascii=False, indent=1))


def frames(cid):
    it = item(cid); src = src_path(it); dur = it["duration"]
    out = UP / "frames" / f"{safe(cid)}.jpg"; out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        shots = []
        for i, f in enumerate((0.04, 0.2, 0.4, 0.6, 0.8, 0.96)):
            p = Path(td) / f"{i}.jpg"
            subprocess.run([FF, "-v", "error", "-y", "-ss", f"{dur * f:.2f}", "-i", str(src), "-frames:v", "1",
                            "-vf", "scale=270:480", str(p)], check=True)
            shots.append(p)
        ins = sum((["-i", str(p)] for p in shots), [])
        subprocess.run([FF, "-v", "error", "-y", *ins, "-filter_complex",
                        "".join(f"[{i}]" for i in range(6)) + "xstack=inputs=6:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0",
                        "-q:v", "3", str(out)], check=True)
    print(json.dumps({"sheet": str(out), "times_s": [round(dur * f, 1) for f in (0.04, 0.2, 0.4, 0.6, 0.8, 0.96)],
                      "layout": "2 rows x 3, left to right, top row first"}))


def prepare(cid):
    it = item(cid); src = src_path(it); st = jload(STATE, {})
    out = UP / "files" / f"{safe(cid)}.mp4"; out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        if src.stat().st_size <= LIMIT:
            shutil.copy2(src, out)
        else:
            kb = min(kbps_for(it["duration"]), 8000)
            if kb < MIN_720_KBPS:
                sys.exit(f"{cid}: {it['duration']} s is too long to fit 9.5 MB at a watchable quality; upload it by hand")
            vf = ["-vf", "scale=720:1280:flags=lanczos"] if kb < MIN_VIDEO_KBPS else []   # longer clips: 720p looks cleaner at this size
            for attempt in range(3):
                with tempfile.TemporaryDirectory() as td:
                    common = [FF, "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kb}k", "-r", "30",
                              "-pix_fmt", "yuv420p", *vf, "-passlogfile", str(Path(td) / "p")]
                    subprocess.run(common + ["-pass", "1", "-an", "-f", "mp4", os.devnull], check=True)
                    subprocess.run(common + ["-pass", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
                if out.stat().st_size <= LIMIT:
                    break
                kb = int(kb * 0.9)
            else:
                sys.exit(f"{cid}: could not get under 9.5 MB")
    todo = [p for p in PLATFORMS if not posted(st, cid, p) and it["duration"] <= MAX_DUR[p]]
    # full quality: the original render in <10 MB pieces (Claude in Chrome hands a page at most ~10 MB per call);
    # the skill puts the pieces into the page one by one and joins them back into the original file there
    import hashlib
    for old in (UP / "chunks").glob("*") if (UP / "chunks").is_dir() else []:   # pieces of earlier uploads (~90 MB each)
        if time.time() - old.stat().st_mtime > 6 * 3600:
            shutil.rmtree(old, ignore_errors=True)
    cdir = UP / "chunks" / safe(cid)
    if cdir.exists():
        shutil.rmtree(cdir)
    cdir.mkdir(parents=True)
    data = src.read_bytes()
    chunks = []
    for i in range(0, len(data), CHUNK):
        f = cdir / f"part_{i // CHUNK:03d}.bin"; f.write_bytes(data[i:i + CHUNK]); chunks.append(str(f))
    full = {"name": f"{re.sub(r'[^a-z0-9]+', '-', it['title'].lower()).strip('-')[:60] or safe(cid)}.mp4",
            "bytes": len(data), "mb": round(len(data) / 1e6, 1), "sha256_16": hashlib.sha256(data).hexdigest()[:16], "chunks": chunks}
    print(json.dumps({"id": cid, "full": full, "file": str(out), "mb": round(out.stat().st_size / 1e6, 2), "duration": it["duration"],
                      "pending": todo, "text": {p: it["platforms"][p] for p in todo}}, ensure_ascii=False, indent=1))


def mark(cid, platform, url):
    item(cid)
    if platform not in PLATFORMS:
        sys.exit(f"platform: one of {PLATFORMS}")
    st = jload(STATE, {})
    e = st.setdefault(cid, {}).setdefault(platform, {})
    e.update(posted=True, updated=time.time(), by="claude-uploader")
    if url:
        e["url"] = url
    jsave(STATE, st)
    log(event="posted", id=cid, platform=platform, url=url)
    print(f"marked {cid} posted on {platform}" + (f": {url}" if url else ""))


def account(name, handles):
    if name:
        jsave(ACCOUNT, {"name": name.strip(), "handles": [h.strip().lstrip("@").lower() for h in handles if h.strip()]})
        log(event="account", name=name.strip())
    print(json.dumps(jload(ACCOUNT, None), ensure_ascii=False))


def unskip(cid):
    s = jload(SKIPS, {}); s.pop(cid, None); jsave(SKIPS, s); log(event="unskipped", id=cid)


def skip(cid, reason):
    item(cid)
    s = jload(SKIPS, {}); s[cid] = {"reason": reason, "at": time.strftime("%Y-%m-%d %H:%M")}
    jsave(SKIPS, s); log(event="skipped", id=cid, reason=reason)
    print(f"skipped {cid}: {reason}  (undo: remove it from {SKIPS})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    ac = sub.add_parser("account"); ac.add_argument("--name", default=""); ac.add_argument("--handle", action="append", default=[])
    for c in ("frames", "prepare"):
        sub.add_parser(c).add_argument("id")
    m = sub.add_parser("mark"); m.add_argument("id"); m.add_argument("platform"); m.add_argument("--url", default="")
    s = sub.add_parser("skip"); s.add_argument("id"); s.add_argument("--reason", required=True)
    f = sub.add_parser("fail"); f.add_argument("id"); f.add_argument("platform"); f.add_argument("--reason", required=True)
    a = ap.parse_args()
    if a.cmd == "status": status()
    elif a.cmd == "account": account(a.name, a.handle)
    elif a.cmd == "frames": frames(a.id)
    elif a.cmd == "prepare": prepare(a.id)
    elif a.cmd == "mark": mark(a.id, a.platform, a.url)
    elif a.cmd == "skip": skip(a.id, a.reason)
    elif a.cmd == "fail": log(event="failed", id=a.id, platform=a.platform, reason=a.reason); print("logged")


if __name__ == "__main__":
    main()
