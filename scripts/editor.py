"""Clip editor: edit a clip in the browser, render it with cut.py.

usage: python tool/scripts/editor.py [--port 8766]      (run from the clip desk folder, the one with jobs/)
then open http://127.0.0.1:8766

The page edits the same options cut.py takes (start/end, hook, cut and keep ranges, webcam crop, layout, title,
captions, transition) on the downloaded source video, previews them live, and renders with cut.py.
Standard library only (plus ffmpeg/ffprobe). Listens on 127.0.0.1 only.
"""
import argparse, json, mimetypes, os, re, shutil, signal, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ROOT, ffprobe, load_json, save_json, tool  # noqa: E402

JOBS = (ROOT / "jobs").resolve()
WEB = (HERE.parent / "web" / "out").resolve()   # the Next.js editor UI, built with `npm run build` in tool/web
FONTS = (HERE.parent / "fonts").resolve()
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
LAYOUTS = ("react", "track", "fill", "blur")   # Split, Focus, Fill, Fit
LEGACY_LAYOUT = {"facecam": "react", "split": "track"}   # removed layouts: older clips reopen in the nearest one
RENDERS = {}           # id -> {"proc", "log", "job", "name", "started", "by"}
PORT = 8766
LOCK = threading.Lock()
_probe_cache = {}


def job_dir(job):
    d = (JOBS / job).resolve()
    if d.parent != JOBS or not d.is_dir():
        raise ValueError(f"unknown job {job!r}")
    return d


def rel_media(p):
    """URL for a file under jobs/."""
    p = (ROOT / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
    return "/media/" + p.relative_to(JOBS).as_posix()


def probe(p):
    p = str(Path(p).resolve())
    key = (p, Path(p).stat().st_mtime)
    if key not in _probe_cache:
        info = ffprobe(p)
        v = next(s for s in info["streams"] if s.get("codec_type") == "video")
        num, den = (v.get("r_frame_rate") or "30/1").split("/")
        _probe_cache[key] = {"dur": float(info["format"]["duration"]), "w": int(v["width"]), "h": int(v["height"]),
                             "fps": round(float(num) / max(1.0, float(den)), 2)}
    return _probe_cache[key]


def candidates(d, set_):
    f = d / f"{set_}.json"
    return load_json(f) if f.exists() else {"candidates": []}


# ------------------------------------------------------------------ clip list

def clip_list():
    out = []
    for d in sorted(JOBS.iterdir(), key=lambda p: -p.stat().st_mtime):
        if not d.is_dir() or not (d / "info.json").exists():
            continue
        info = load_json(d / "info.json")
        clips = []
        for m in sorted((d / "edits").glob("*.json"), key=lambda p: -p.stat().st_mtime) if (d / "edits").exists() else []:
            mp4 = m.with_suffix(".mp4")
            if not mp4.exists() or m.name.startswith("."):
                continue
            meta = load_json(m)
            if not isinstance(meta.get("segments"), list):
                continue   # rendered before cut.py saved its edit: not editable here
            clips.append({"name": m.stem, "title": meta.get("title") or meta.get("hook") or m.stem,
                          "layout": meta.get("layout"), "editable": bool(find_video(d, meta)[0])})
        picks = []
        for set_ in ("candidates_audit",):
            for p in candidates(d, set_)["candidates"]:
                if p.get("file") and (ROOT / p["file"]).exists():
                    picks.append({"set": set_, "n": p["n"], "tag": p.get("tag", ""), "title": p.get("title", ""),
                                  "start": p["start"], "end": p["end"]})
        if clips or picks:
            out.append({"job": d.name, "title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
                        "clips": clips, "picks": picks})
    return out


def find_video(d, meta):
    """(video path, offset in source seconds, set, pick n) for a rendered clip."""
    args = meta.get("args") or {}
    if meta.get("video") and (ROOT / meta["video"]).exists():
        v = (ROOT / meta["video"]).resolve()
        if JOBS in v.parents:
            return ROOT / meta["video"], meta.get("video_offset", 0), args.get("set", "candidates_audit"), args.get("pick")
        if (d / "source.mp4").exists() and not meta.get("video_offset"):   # a local file, now linked into the job
            return d / "source.mp4", 0, args.get("set", "candidates_audit"), args.get("pick")
    # clips rendered before cut.py saved its options: the pick whose downloaded section covers the clip
    s, e = meta.get("start"), meta.get("end")
    for set_ in ("candidates_audit", "candidates"):
        c = candidates(d, set_)
        for p in sorted(c["candidates"], key=lambda x: -x["n"]):
            if p.get("file") and (ROOT / p["file"]).exists():
                off = p.get("offset", 0)
                dur = probe(ROOT / p["file"])["dur"]
                if s is not None and off - 0.5 <= s and e <= off + dur + 0.5:
                    return ROOT / p["file"], off, set_, p["n"]
        if c.get("source") and (ROOT / c["source"]).exists() and set_ == "candidates":
            near = min(c["candidates"], key=lambda x: abs(x["start"] - (s or 0)), default=None)
            if near:
                return ROOT / c["source"], 0, set_, near["n"]
    return None, 0, None, None


def words_for(d, a, b):
    """Words in [a, b] (source seconds): force-aligned caption words where cached, the pipeline transcript elsewhere."""
    aligned, spans = [], []
    for f in sorted((d / "aligned").glob("*.json")) if (d / "aligned").exists() else []:
        m = re.match(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$", f.stem)
        if not m:
            continue
        x, y = float(m.group(1)), float(m.group(2))
        if y < a or x > b:
            continue
        spans.append((x, y))
        aligned += [w for w in load_json(f) if a <= w["s"] <= b]
    covered = lambda t: any(x <= t <= y for x, y in spans)
    pipe = load_json(d / "transcript.json") if (d / "transcript.json").exists() else []
    pipe = pipe if isinstance(pipe, list) else pipe.get("words", [])
    ws = aligned + [w for w in pipe if a <= w["s"] <= b and not covered(w["s"])]
    ws.sort(key=lambda w: w["s"])
    out, last = [], -1.0
    for w in ws:   # overlapping aligned windows repeat words: keep the first of near-identical starts
        if w["s"] - last < 0.04 and out and out[-1]["w"].lower().strip(".,!?") == w["w"].lower().strip(".,!?"):
            continue
        out.append({"w": w["w"].strip(), "s": round(w["s"], 3), "e": round(w["e"], 3)})
        last = w["s"]
    return out


def project(job, name=None, set_=None, pick=None):
    d = job_dir(job)
    meta, rendered = {}, None
    if name:
        if not NAME_RE.match(name):
            raise ValueError("bad clip name")
        mf = d / "edits" / f"{name}.json"
        if mf.exists():
            meta = load_json(mf)
            mp4 = mf.with_suffix(".mp4")
            if mp4.exists():
                rendered = {"url": rel_media(mp4) + f"?v={int(mp4.stat().st_mtime)}", "segments": meta.get("segments"),
                            "tease": meta.get("tease"), "dur": probe(mp4)["dur"], "fps": meta.get("fps"),
                            "transition": meta.get("transition") or {"name": "rewind" if meta.get("tease") else None, "overlap": 0},
                            "updated": mp4.stat().st_mtime}
    if meta:
        video, off, set_, pick = find_video(d, meta)
    else:
        c = candidates(d, set_ or "candidates_audit")
        if pick is None and c["candidates"]:   # a new (unrendered) clip reopened by name: its job's first pick
            pick = c["candidates"][0]["n"]
        p = next((x for x in c["candidates"] if x["n"] == int(pick)), None) if pick is not None else None
        if not p:
            raise ValueError("unknown pick")
        video, off = (ROOT / p["file"], p.get("offset", 0)) if p.get("file") else (ROOT / c["source"], 0)
        set_ = set_ or "candidates_audit"
    if not video:
        raise ValueError("the downloaded video for this clip is missing; run add_picks.py for its pick again")
    c = candidates(d, set_)
    p = next((x for x in c["candidates"] if x["n"] == int(pick)), {}) if pick is not None else {}
    v = probe(video)
    args = meta.get("args") or {}

    def rng(lst):
        return [[float(x) for x in s.split(":")] for s in lst or []]

    tease = meta.get("tease") if meta else p.get("tease")
    if args:
        if args.get("no_tease"):
            tease = None
        elif args.get("tease"):
            tease = [float(x) for x in args["tease"].split(":")]
    st = {
        "start": args.get("start") if args.get("start") is not None else (
            round(meta["start"] - 0.15, 2) if meta else p.get("start")),
        "end": args.get("end") if args.get("end") is not None else (round(meta["end"] + 0.3, 2) if meta else p.get("end")),
        "tease": tease, "keep": rng(args.get("keep")), "drop": rng(args.get("drop")),
        "cam": args.get("cam") if args.get("cam") not in (None, "auto") else (meta.get("cam") or "auto"),
        "layout": LEGACY_LAYOUT.get(meta.get("layout") or args.get("layout") or "react", meta.get("layout") or args.get("layout") or "react"),
        "title": meta.get("title") if meta else p.get("title", ""),
        # the look is fixed (what cut.py renders by default), so the preview always matches the render
        "title_style": "box", "captions": "word", "title_secs": 0, "transition": "zoom", "tighten": 0.45,
        "keep_after_tease": bool(args.get("keep_after_tease")), "punch": True, "trim": True,
    }
    if meta and not args and meta.get("tease") and meta.get("end", 0) > meta["tease"][1] + 1.2:
        st["keep_after_tease"] = True   # older renders: it kept playing after the hook
    st["start"] = max(st["start"], off)
    st["end"] = min(st["end"], off + v["dur"])
    pad = 8.0
    words = words_for(d, off, off + v["dur"] + pad)
    titles = p.get("titles") or ([st["title"]] if st["title"] else [])
    return {"job": job, "name": name or f"e_{(p.get('tag') or 'pick' + str(pick)).lower()}", "set": set_, "pick": pick,
            "video": {"url": rel_media(video), "offset": off, **v}, "words": words, "state": st, "titles": titles,
            "game_x": meta.get("game_x"), "focus": meta.get("focus"), "rendered": rendered, "is_new": not meta,
            "source": {"title": meta.get("source_title") or load_json(d / "info.json").get("title"),
                       "channel": meta.get("source_channel") or load_json(d / "info.json").get("channel")}}


def strip(job, video_url, n=0):
    """One wide JPEG of evenly spaced frames for the timeline."""
    d = job_dir(job)
    path = (JOBS / video_url.split("/media/", 1)[1].split("?")[0]).resolve()
    if JOBS not in path.parents or not path.exists():
        raise ValueError("bad video")
    v = probe(path)
    n = int(n) or max(12, min(160, round(v["dur"])))
    out = d / "editor" / f"strip_{path.stem}_{n}.jpg"
    if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:
        out.parent.mkdir(exist_ok=True)
        subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(path), "-vf",
                        f"fps={n / v['dur']:.5f},scale=160:90:force_original_aspect_ratio=increase,crop=160:90,tile={n}x1",
                        "-frames:v", "1", "-q:v", "5", str(out)], check=True)
    return {"url": rel_media(out) + f"?v={int(out.stat().st_mtime)}", "n": n, "tile_w": 160, "tile_h": 90}


# ------------------------------------------------------------------ rendering

def num(x, lo=-1e9, hi=1e9):
    x = float(x)
    if not lo <= x <= hi:
        raise ValueError(f"value out of range: {x}")
    return x


def render(body):
    """Render from the editor. The look is fixed (word captions, white title box, zoom into the clip, pauses over
    0.45 s cut, punch-in): the editor only sets what changes per clip: the moment, hook, cuts, title, layout, webcam."""
    job, name = body["job"], body["name"]
    d = job_dir(job)
    if not NAME_RE.match(name):
        raise ValueError("clip name: letters, digits, _ and - only")
    s = body["state"]
    layout = s["layout"]
    if layout not in LAYOUTS:
        raise ValueError("layout")
    cmd = [sys.executable, str(HERE / "cut.py"), str(d.relative_to(ROOT)), "--set", str(body["set"]),
           "--pick", str(int(body["pick"])), "--layout", layout, "--name", name,
           "--start", f"{num(s['start']):.3f}", "--end", f"{num(s['end']):.3f}",
           "--title", str(s.get("title", ""))[:120]]
    if s.get("tease"):
        a, b = num(s["tease"][0]), num(s["tease"][1])
        cmd += ["--tease", f"{a:.3f}:{b:.3f}"]
    else:
        cmd += ["--no-tease"]
    cam = s.get("cam", "auto")
    if layout == "react" and cam != "auto":
        parts = [num(x, 0, 1) for x in cam.split(":")]
        if len(parts) != 4 or parts[2] <= 0.01 or parts[3] <= 0.01:
            raise ValueError("cam box")
        cmd += ["--cam", ":".join(f"{x:.4f}" for x in parts)]
    if layout == "react":
        cmd += ["--force"]
    for k in ("keep", "drop"):
        for a, b in s.get(k) or []:
            cmd += [f"--{k}", f"{num(a):.3f}:{num(b):.3f}"]
    if s.get("keep_after_tease"):
        cmd += ["--keep-after-tease"]
    rid = f"{int(time.time() * 1000)}"
    log = d / "editor" / f"render_{name}_{rid}.log"
    log.parent.mkdir(exist_ok=True)
    f = open(log, "w")
    f.write("$ " + " ".join(cmd) + "\n\n"); f.flush()
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
    with LOCK:
        RENDERS[rid] = {"proc": proc, "log": log, "job": job, "name": name, "started": time.time(), "by": str(body.get("by") or "")[:24]}
    return {"id": rid}


NOISE = re.compile(r"frame=|libx264|aac @|Loading weights|Key +\||wav2vec2\.|MISSING|Notes:|newly initialized|objc\[|"
                   r"torch/distributed|^-+\+|^\s*$|\[youtube\]|Press \[q\]|ctranslate2|LOAD REPORT|\x1b\[")


def render_status(rid):
    r = RENDERS.get(rid)
    if not r:
        raise ValueError("unknown render")
    code = r["proc"].poll()
    lines = [l for l in r["log"].read_text(errors="replace").splitlines() if not NOISE.search(l)]
    out = None
    if code == 0:
        mp4 = JOBS / r["job"] / "edits" / f"{r['name']}.mp4"
        out = rel_media(mp4) + f"?v={int(mp4.stat().st_mtime)}"
        if not r.get("queued"):   # a clip you rendered by hand goes straight into the posting queue
            r["queued"] = True
            try:
                uploader().enqueue(r["job"], r["name"], why="rendered in the editor")
            except (SystemExit, Exception):
                pass
    stage = "starting"
    for l in lines:
        if l.startswith("align:"): stage = "captions aligned"
        if l.startswith("edit:"): stage = "edit built, encoding"
        if l.startswith("facecam:"): stage = "webcam found, encoding"
    return {"state": "running" if code is None else ("done" if code == 0 else "error"), "code": code,
            "elapsed": round(time.time() - r["started"], 1), "stage": stage, "log": lines[-14:], "output": out}


# ------------------------------------------------------------------ waveform

def wave(job, video_url):
    """Peak level per 25 ms of the video's audio, 0..1, for the timeline waveform. Cached."""
    d = job_dir(job)
    path = (JOBS / video_url.split("/media/", 1)[1].split("?")[0]).resolve()
    if JOBS not in path.parents or not path.exists():
        raise ValueError("bad video")
    out = d / "editor" / f"wave_{path.stem}.json"
    if out.exists() and out.stat().st_mtime >= path.stat().st_mtime:
        return load_json(out)
    import array
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = array.array("h"); a.frombytes(raw[: len(raw) // 2 * 2])
    hop = 200   # 25 ms at 8 kHz
    peaks = [max((abs(x) for x in a[i:i + hop]), default=0) for i in range(0, len(a), hop)]
    top = sorted(peaks)[int(len(peaks) * 0.98)] if peaks else 1
    res = {"hop": 0.025, "peaks": [round(min(1.0, p / max(1, top)), 3) for p in peaks]}
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res))
    return res


LIVE = JOBS / ".live"
WATCH = {"proc": None}
STREAM_URL = re.compile(r"^https://(?:www\.|m\.)?(?:twitch\.tv/[A-Za-z0-9_]{2,25}|youtube\.com/(?:watch\?v=[\w-]{11}|live/[\w-]{11}|@[\w.-]{2,40}/live)|youtu\.be/[\w-]{11})/?$")
TAG_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_top = {"at": 0.0, "rows": []}


def stream_url(u):
    u = str(u or "").strip()
    if re.match(r"^[A-Za-z0-9_]{2,25}$", u):   # a bare Twitch login
        u = "https://twitch.tv/" + u
    u = re.sub(r"^http://", "https://", u if "://" in u else "https://" + u)
    if not STREAM_URL.match(u):
        raise ValueError(f"not a Twitch or YouTube live URL: {u[:80]}")
    return u


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def live_state():
    try:
        st = load_json(LIVE / "status.json")
    except (FileNotFoundError, json.JSONDecodeError):
        st = None
    return st if st and pid_alive(st.get("pid")) else None


def live_clips(limit=60):
    out = []
    for d in JOBS.glob("live-*-*"):
        if not (d / "info.json").exists() or not (d / "candidates_audit.json").exists() or not (d / "source.mp4").exists():
            continue
        try:
            info, c = load_json(d / "info.json"), candidates(d, "candidates_audit")["candidates"]
        except (json.JSONDecodeError, OSError):
            continue
        sp, p = info.get("live_spike") or {}, (c[0] if c else {})
        out.append({"job": d.name, "channel": info.get("channel"), "wall": sp.get("wall") or d.stat().st_mtime,
                    "words": sp.get("words") or [], "ratio": sp.get("ratio"), "rate": sp.get("rate"), "manual": bool(sp.get("manual")),
                    "how": sp.get("found_by"), "dur": info.get("duration"), "video": rel_media(d / "source.mp4"),
                    "hook": (p.get("tease") or [None])[0], "pick": p.get("n", 1),
                    "edits": sorted(m.stem for m in (d / "edits").glob("*.mp4")) if (d / "edits").is_dir() else []})
    return sorted(out, key=lambda x: -x["wall"])[:limit]


def live_status():
    st, proc = live_state(), WATCH["proc"]
    starting = st is None and proc is not None and proc.poll() is None
    log = []
    if (LIVE / "watch.log").exists():
        log = [l for l in (LIVE / "watch.log").read_text(errors="replace").splitlines() if l.strip() and not NOISE.search(l)][-12:]
    return {"running": st is not None, "starting": starting, "status": st, "log": log, "clips": live_clips(), "now": time.time(),
            "exit_code": proc.poll() if proc is not None and not starting and st is None else None}


def live_start(body):
    if live_state() or (WATCH["proc"] and WATCH["proc"].poll() is None):
        raise ValueError("already watching: add streams to it instead")
    urls = list(dict.fromkeys(stream_url(u) for u in body.get("urls") or []))
    top = max(0, min(9, int(body.get("top") or 0)))
    if not urls and not top:
        raise ValueError("pick at least one stream")
    if len(urls) + top > 9:
        raise ValueError("9 streams at most")
    minutes = max(5.0, min(24 * 60.0, float(body.get("minutes") or 120)))
    ratio = max(1.5, min(8.0, float(body.get("ratio") or 3.0)))
    LIVE.mkdir(exist_ok=True)
    cmd = [sys.executable, str(HERE / "watch.py"), *urls, "--minutes", f"{minutes:g}", "--ratio", f"{ratio:g}"] + (["--top", str(top)] if top else [])
    log = open(LIVE / "watch.log", "w")
    log.write("$ " + " ".join(cmd) + "\n"); log.flush()
    WATCH["proc"] = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    return {"ok": True}


def live_cmd(body):
    st = live_state()
    if not st:
        raise ValueError("not watching")
    d = body.get("do")
    if d == "add":
        m = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        c = {"do": "add", "url": stream_url(body.get("url")),
             "meta": {"name": str(m.get("name", ""))[:40], "title": str(m.get("title", ""))[:140], "viewers": int(m.get("viewers") or 0)}}
        if sum(1 for s in st["streams"] if s["alive"]) >= 9:
            raise ValueError("9 streams at most")
    elif d in ("remove", "clip"):
        tag = str(body.get("tag", ""))
        if not TAG_RE.match(tag):
            raise ValueError("tag")
        c = {"do": d, "tag": tag, "behind": max(0.0, min(30.0, float(body.get("behind") or 0)))}
    elif d == "extend":
        c = {"do": "extend", "minutes": max(5.0, min(24 * 60.0, float(body.get("minutes") or 60)))}
    elif d == "stop":
        c = {"do": "stop"}
        pid = st["pid"]

        def fallback():   # watch.py finishes its pending clips first; force it only if it hangs
            time.sleep(90)
            if pid_alive(pid):
                os.kill(pid, signal.SIGTERM)
        threading.Thread(target=fallback, daemon=True).start()
    else:
        raise ValueError("do: add, remove, clip, extend or stop")
    (LIVE / "cmd").mkdir(parents=True, exist_ok=True)
    tmp = LIVE / "cmd" / f".{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(c))
    os.replace(tmp, tmp.with_name(tmp.name[1:].replace(".tmp", ".json")))
    return {"ok": True}


def live_top():
    if time.time() - _top["at"] > 60:
        import twitch
        _top["rows"] = [{k: r.get(k) for k in ("login", "name", "url", "viewers", "title", "preview", "lang")}
                        for r in twitch.top_streams("Grand Theft Auto VI", 30)]
        _top["at"] = time.time()
    return _top["rows"]


# ------------------------------------------------------------------ rendered clips (Home, Queue)

QDIR = ROOT / "queue"
_upl = {}
PLATS = ("tiktok", "youtube", "instagram", "x")


def uploader():
    if "m" not in _upl:
        import uploader as m   # scripts/uploader.py: its data lives in the desk's queue/ folder
        _upl["m"] = m
    return _upl["m"]


def queue_data():
    U = uploader()
    st, skips = U.jload(U.STATE, {}), U.jload(U.SKIPS, {})
    added = {x["id"] for x in U.jload(U.ADDED, [])}
    items = []
    for it in U.items().values():
        job, _, name = it["id"].partition("/")
        mp4 = JOBS / job / "edits" / f"{name}.mp4"
        items.append({**{k: it.get(k) for k in ("id", "rank", "title", "hook", "why", "warnings", "creator", "duration", "format",
                                                 "source_url", "source_title", "platforms", "rank_reason", "hold", "pinned")},
                      "job": job, "name": name, "video": rel_media(mp4) + f"?v={int(mp4.stat().st_mtime)}" if mp4.exists() else None,
                      "state": st.get(it["id"], {}), "skipped": skips.get(it["id"]), "added": it["id"] in added})
    last = max((v.get("updated", 0) for d in st.values() for v in d.values() if v.get("posted")), default=0)
    log = []
    if U.LOG.exists():
        log = [json.loads(l) for l in U.LOG.read_text(encoding="utf-8").splitlines()[-12:] if l.strip()]
    pn = U.jload(U.UP / "postnow.json", {})
    pn["running"] = pn.get("state") == "running" and pid_alive(pn.get("pid"))
    if pn.get("state") == "running" and not pn["running"]:
        pn["state"] = "stopped"
    pn["log"] = tail(U.UP / "postnow.log", 16, noise=False) if pn.get("id") else []
    return {"items": items, "max_dur": U.MAX_DUR, "postnow": pn,
            "account": U.jload(U.ACCOUNT, None),
            "limits": {"tiktok": 2200, "yt_title": 100, "yt_desc": 5000, "instagram": 2200, "x": 280},
            "last_post": last or None, "log": log}


def queue_post(path, body):
    U = uploader()
    if path == "/api/queue/account":   # the account the uploader may post as (queue/accounts.json)
        nm = str(body.get("name", "")).strip()[:60]
        if not nm:
            raise ValueError("the account name is empty")
        hs = [h for h in re.split(r"[\s,]+", str(body.get("handles", ""))) if h][:6]
        U.jsave(U.ACCOUNT, {"name": nm, "handles": [h.lstrip("@").lower()[:40] for h in hs]})
        return {"ok": True}
    cid = str(body.get("id", ""))
    job, _, name = cid.partition("/")
    if path == "/api/queue/add":
        job, name = str(body.get("job", "")), str(body.get("name", ""))
        job_dir(job)
        if not NAME_RE.match(name):
            raise ValueError("bad clip name")
        added = U.jload(U.ADDED, [])
        if f"{job}/{name}" in U.items():
            raise ValueError("already in the queue")
        try:
            added.append(U.added_item(job, name))
        except SystemExit as e:
            raise ValueError(str(e))
        U.jsave(U.ADDED, added)
        rm = U.jload(QDIR / "removed.json", [])
        if f"{job}/{name}" in rm:
            U.jsave(QDIR / "removed.json", [x for x in rm if x != f"{job}/{name}"])
        return {"ok": True}
    if path == "/api/queue/postnow":   # Claude posts this clip now, in Chrome (scripts/post_now.py)
        pn = U.jload(U.UP / "postnow.json", {})
        if pn.get("state") == "running" and pid_alive(pn.get("pid")):
            raise ValueError("already posting " + str(pn.get("id")))
        if cid not in U.items():
            queue_post("/api/queue/add", {"job": job, "name": name})
        if not (U.jload(U.ACCOUNT, None) or {}).get("name"):
            raise ValueError("set the account you post as first (Posting as, at the top of this page)")
        U.jsave(U.UP / "postnow.json", {"state": "running", "id": cid, "started": time.time(), "pid": None})
        pr = subprocess.Popen([sys.executable, str(HERE / "post_now.py"), cid], cwd=str(ROOT), stdout=subprocess.DEVNULL,
                              stderr=open(U.UP / "postnow.err", "w"), start_new_session=True)
        U.jsave(U.UP / "postnow.json", {"state": "running", "id": cid, "started": time.time(), "pid": pr.pid})
        return {"ok": True}
    if path == "/api/queue/postnow/stop":
        pn = U.jload(U.UP / "postnow.json", {})
        if pn.get("state") == "running" and pid_alive(pn.get("pid")):
            os.killpg(os.getpgid(int(pn["pid"])), signal.SIGTERM)
            U.jsave(U.UP / "postnow.json", dict(pn, state="stopped", finished=time.time()))
        return {"ok": True}
    if path == "/api/queue/next":   # "Post next": front of the posting order (added to the queue first if needed)
        if cid not in U.items():
            if not (JOBS / job / "edits" / f"{name}.mp4").exists():
                raise ValueError("no such clip")
            queue_post("/api/queue/add", {"job": job, "name": name})
        with LOCK:
            order = U.jload(U.ORDER, {"at": time.time(), "by": "you", "order": []})
            order["order"] = [{"id": cid, "reason": "You picked it to post next", "hold": False, "pinned": True}] + \
                             [dict(o, pinned=False, reason="You picked it earlier; a newer pick goes first") if o.get("pinned") else o
                              for o in order["order"] if o.get("id") != cid]
            U.jsave(U.ORDER, order)
        U.unskip(cid)
        return {"ok": True}
    if cid not in U.items():
        raise ValueError("not in the queue")
    if path == "/api/queue/state":
        plat = str(body.get("platform"))
        if plat not in PLATS:
            raise ValueError("platform")
        with LOCK:
            st = U.jload(U.STATE, {})
            e = st.setdefault(cid, {}).setdefault(plat, {})
            for k in ("posted", "url"):
                if k in body:
                    e[k] = bool(body[k]) if k == "posted" else str(body[k])[:300]
            e["updated"] = time.time()
            U.jsave(U.STATE, st)
        return {"ok": True}
    if path == "/api/queue/text":
        plat, field = str(body.get("platform")), str(body.get("field"))
        if plat not in PLATS or field not in ("caption", "title", "description", "tags", "text"):
            raise ValueError("field")
        with LOCK:
            ov = U.jload(U.OVERRIDES, {})
            ov.setdefault(cid, {}).setdefault(plat, {})[field] = str(body.get("value", ""))[:5000]
            U.jsave(U.OVERRIDES, ov)
        return {"ok": True}
    if path == "/api/queue/skip":
        if body.get("skip"):
            U.skip(cid, str(body.get("reason") or "skipped on the Queue page")[:200])
        else:
            U.unskip(cid)
        return {"ok": True}
    if path == "/api/queue/remove":
        U.jsave(U.ADDED, [x for x in U.jload(U.ADDED, []) if x["id"] != cid])
        removed = U.jload(QDIR / "removed.json", [])   # so "rank all" doesn't put it back
        U.jsave(QDIR / "removed.json", sorted(set(removed) | {cid}))
        return {"ok": True}
    raise ValueError("unknown queue action")


# ------------------------------------------------------------------ extract: YouTube video -> pipeline -> Claude's picks -> clips

# ------------------------------------------------------------------ campaigns: paid clipping campaigns (campaigns.py)
# The Earn page: join a campaign, paste its rules (Claude structures them), add its overlay, see which clips qualify,
# what was submitted, real views, an estimate of what they earn and real payouts. "Submit with Claude" runs
# submit_now.py (Claude in Chrome, the campaign-submit skill).

SUBMIT = {"proc": None}
STATS = {"thread": None, "at": None}
CAMP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def campaigns_mod():
    import campaigns
    return campaigns


def campaigns_data():
    C, U = campaigns_mod(), uploader()
    items = U.items()
    counts = {}   # campaign id -> {"eligible": n, "fix": n}
    for cid in items:
        for m in C.match(cid, only_joined=False):
            k = counts.setdefault(m["id"], {"eligible": 0, "fix": 0})
            k["eligible" if m["eligible"] else "fix"] += 1
    earn = {e["id"]: e for e in C.earnings()}
    out = []
    for c in C.load()["campaigns"]:
        f = C.overlay_file(c)
        out.append({**c, "overlay": f"/api/campaigns/overlay?id={c['id']}&v={int(f.stat().st_mtime)}" if f else None,
                    "clips": counts.get(c["id"], {"eligible": 0, "fix": 0}), "earn": earn.get(c["id"])})
    out.sort(key=lambda c: (not c.get("joined"), -(c.get("rate_per_1k") or 0)))
    subs = []
    for e in sorted(C.subs(), key=lambda e: -e.get("updated", 0))[:200]:
        it = items.get(e["clip"]) or {}
        subs.append({**e, "title": it.get("title") or e["clip"]})
    sb = U.jload(U.UP / "submit.json", {})
    sb["running"] = sb.get("state") == "running" and pid_alive(sb.get("pid"))
    if sb.get("state") == "running" and not sb["running"]:
        sb["state"] = "stopped"
    sb["log"] = tail(U.UP / "submit.log", 16, noise=False) if sb.get("started") else []
    tot = {"estimate": round(sum((e.get("estimate") or 0) for e in earn.values()), 2),
           "paid": round(sum(e.get("paid") or 0 for e in earn.values()), 2),
           "views": sum(e.get("views") or 0 for e in earn.values()),
           "to_submit": sum(1 for e in C.subs() if e["status"] == "to_submit")}
    return {"campaigns": out, "submissions": subs, "payouts": C.payouts()[-30:], "submit": sb, "totals": tot,
            "stats": {"running": bool(STATS["thread"] and STATS["thread"].is_alive()), "at": STATS["at"]}}


def campaigns_post(path, body):
    C = campaigns_mod()
    if path == "/api/campaigns/update":
        cid = str(body.get("id", ""))
        if not CAMP_ID.match(cid) or not C.get(cid):
            raise ValueError("unknown campaign")
        kw = {}
        if "joined" in body:
            kw["joined"] = bool(body["joined"])
        if "submit_url" in body:
            u = str(body["submit_url"] or "").strip()
            if u and not re.match(r"^https://", u):
                raise ValueError("the submit page must be an https:// link")
            kw["submit_url"] = u or None
        if "rules_text" in body:
            kw["rules_text"] = str(body["rules_text"] or "")[:20000]
        C.update(cid, **kw)
        if kw.get("rules_text", "").strip():   # Claude turns the pasted rules into settings, in the background
            C.update(cid, parse_state="running")
            subprocess.Popen([sys.executable, str(HERE / "campaigns.py"), "parse", cid], cwd=str(ROOT),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True}
    if path == "/api/campaigns/overlay":   # the campaign's logo / overlay, as a PNG data URL
        import base64
        cid = str(body.get("id", ""))
        if not CAMP_ID.match(cid) or not C.get(cid):
            raise ValueError("unknown campaign")
        if body.get("remove"):
            (C.FILES / cid / "overlay.png").unlink(missing_ok=True)
            return {"ok": True}
        data = str(body.get("data", ""))
        if not data.startswith("data:image/png;base64,"):
            raise ValueError("the overlay must be a PNG (a transparent background works best)")
        raw = base64.b64decode(data.split(",", 1)[1])
        if not raw.startswith(b"\x89PNG") or len(raw) > 12_000_000:
            raise ValueError("not a PNG, or over 12 MB")
        (C.FILES / cid).mkdir(parents=True, exist_ok=True)
        (C.FILES / cid / "overlay.png").write_bytes(raw)
        return {"ok": True}
    if path == "/api/campaigns/submission":
        C.set_status(str(body.get("clip")), str(body.get("platform")), str(body.get("campaign")), str(body.get("status")),
                     str(body.get("note") or ""))
        return {"ok": True}
    if path == "/api/campaigns/payout":
        cid = str(body.get("campaign", ""))
        if not C.get(cid):
            raise ValueError("unknown campaign")
        amount = float(body.get("amount") or 0)
        if not 0 < amount < 1_000_000:
            raise ValueError("amount")
        C.add_payout(cid, amount, str(body.get("note") or "")[:200])
        return {"ok": True}
    if path == "/api/campaigns/stats":   # read the posts' real views now (scoreboard.csv)
        if not (STATS["thread"] and STATS["thread"].is_alive()):
            def go():
                try:
                    import stats
                    stats.refresh()
                finally:
                    STATS["at"] = time.time()
            STATS["thread"] = threading.Thread(target=go, daemon=True)
            STATS["thread"].start()
        return {"ok": True}
    if path == "/api/campaigns/submit":   # Claude submits the pending links in Chrome (submit_now.py)
        U = uploader()
        sb = U.jload(U.UP / "submit.json", {})
        if sb.get("state") == "running" and pid_alive(sb.get("pid")):
            raise ValueError("already submitting")
        if not C.pending_submissions():
            raise ValueError("nothing to submit")
        U.UP.mkdir(parents=True, exist_ok=True)
        U.jsave(U.UP / "submit.json", {"state": "running", "started": time.time(), "pid": None})
        pr = subprocess.Popen([sys.executable, str(HERE / "submit_now.py")], cwd=str(ROOT), stdout=subprocess.DEVNULL,
                              stderr=open(U.UP / "submit.err", "w"), start_new_session=True)
        U.jsave(U.UP / "submit.json", {"state": "running", "started": time.time(), "pid": pr.pid})
        return {"ok": True}
    if path == "/api/campaigns/submit/stop":
        U = uploader()
        sb = U.jload(U.UP / "submit.json", {})
        if sb.get("state") == "running" and pid_alive(sb.get("pid")):
            os.killpg(os.getpgid(int(sb["pid"])), signal.SIGTERM)
            U.jsave(U.UP / "submit.json", dict(sb, state="stopped", finished=time.time()))
        return {"ok": True}
    raise ValueError("unknown campaign action")


EXR = JOBS / ".extract"
TRENDS = {"proc": None}
VIDEO_URL = re.compile(r"^https://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=[\w-]{11}|live/[\w-]{11}|shorts/[\w-]{11})|youtu\.be/[\w-]{11}|twitch\.tv/videos/\d+)(?:[&?#].*)?$")


def job_name(src):
    """jobs/<name> that common.job_dir() uses for this source, without creating it."""
    import hashlib
    key = hashlib.sha1(src.encode()).hexdigest()[:8]
    stem = re.sub(r"[^a-z0-9]+", "-", src.split("v=")[-1].split("/")[-1].lower())[:24].strip("-")
    return f"{stem or 'job'}-{key}"


def tail(p, n, noise=True):
    if not p.exists():
        return []
    lines = [l for l in p.read_text(errors="replace").splitlines() if l.strip() and not (noise and NOISE.search(l))]
    return lines[-n:]


def extract_runs(limit=6):
    runs = []
    for d in sorted((x for x in EXR.glob("2*") if x.is_dir()), reverse=True)[:limit] if EXR.is_dir() else []:
        try:
            st = load_json(d / "status.json")
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        st["active"] = st.get("stage") not in ("done", "stopped", "error") and pid_alive(st.get("pid"))
        if not st["active"] and st.get("stage") not in ("done", "stopped", "error"):
            st["stage"] = "stopped"   # the process died without saying so
        for j in st.get("jobs", []):
            job_progress(j)
            j["claude_log"] = tail(d / f"claude_{j['job']}.log", 14, noise=False) if st.get("opts", {}).get("claude") else []
        st["pipeline_log"] = tail(d / "pipeline.log", 6)
        st["choose_log"] = tail(d / "choose.log", 8, noise=False)
        st["render_log"] = tail(d / "render.log", 4)
        runs.append(st)
    return terminal_runs({j["job"] for r in runs if r["active"] for j in r.get("jobs", [])}) + runs


PROGRESS_KEYS = ("stage", "chunks_total", "chunks_done", "sections_total", "sections_done", "error", "error_at",
                 "started", "updated", "finished", "audio_sec")


def job_progress(j):
    """Attach jobs/<id>/progress.json (written by pipeline.py) plus the title/channel to a run's job entry."""
    jd = JOBS / j["job"]
    try:
        pr = load_json(jd / "progress.json") if (jd / "progress.json").exists() else {}
    except json.JSONDecodeError:   # pipeline.py is mid-write
        pr = {}
    j["progress"] = {k: pr.get(k) for k in PROGRESS_KEYS}
    if not j.get("title") and (jd / "info.json").exists():
        try:
            info = load_json(jd / "info.json"); j["title"], j["channel"] = info.get("title"), info.get("channel")
        except json.JSONDecodeError:
            pass
    return j


def terminal_runs(seen):
    """pipeline.py runs started from a terminal (or by Claude) rather than this page: every job whose progress.json
    belongs to a live pipeline process, grouped by that process, so the Extract page shows them too."""
    by_pid, now = {}, time.time()
    for f in JOBS.glob("*/progress.json"):
        if f.parent.name in seen or now - f.stat().st_mtime > 86400:
            continue
        try:
            pr = load_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not pid_alive(pr.get("pid")):
            continue
        st = pr.get("stage")
        by_pid.setdefault(int(pr["pid"]), []).append(job_progress({
            "src": pr.get("src", ""), "job": f.parent.name, "stage": st if st in ("done", "error") else "pipeline",
            "note": (pr.get("error") or "")[-200:] if st == "error" else "", "picks": [], "claude_log": []}))
    out = []
    for pid, jobs in by_pid.items():
        if all(j["stage"] in ("done", "error") for j in jobs):
            continue   # the process is alive but its videos are finished (e.g. it's waiting on chat replay)
        started = min((j["progress"].get("started") or now) for j in jobs)
        out.append({"run": f"terminal-{pid}", "pid": pid, "terminal": True, "active": True, "stage": "pipeline",
                    "started": started, "updated": now, "urls": [j["src"] for j in jobs],
                    "opts": {"max_minutes": 0, "picks": 0, "claude": False, "render": False},
                    "jobs": sorted(jobs, key=lambda j: j["src"]), "pipeline_log": [], "render_log": []})
    return out


def extract_start(body):
    if any(r["active"] for r in extract_runs(3)):
        raise ValueError("an extraction is already running")
    urls = list(dict.fromkeys(str(u).strip() for u in body.get("urls") or [] if str(u).strip()))
    auto = int(max(0, min(10, float(body.get("auto") or 0))))
    if not (urls or auto) or len(urls) + auto > 10:
        raise ValueError("1 to 10 videos")
    if auto and not trending()["rows"]:
        raise ValueError("no trending list yet: press Refresh first")
    for u in urls:
        if not VIDEO_URL.match(u):
            raise ValueError(f"not a YouTube video link: {u[:80]}")
    # one way to run: Claude picks, looks, keeps the best, they render and join the queue (extract.py defaults)
    cmd = [sys.executable, str(HERE / "extract.py"), *urls, *(["--auto", str(auto)] if auto else []), "--power", "balanced"]
    camp = str(body.get("campaign") or "")
    if camp:   # make these clips for a joined paid campaign: picked by its rules, rendered with its overlay
        c = campaigns_mod().get(camp)
        if not c or not c.get("joined"):
            raise ValueError("join that campaign first (Earn page)")
        cmd += ["--campaign", camp]
    EXR.mkdir(parents=True, exist_ok=True)
    with open(EXR / "last_start.log", "w") as log:
        subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"ok": True}


def extract_job(body):
    """One video of a running extraction: skip it, or (while transcribing) pick from what's transcribed so far."""
    act = body.get("action")
    if act not in ("skip", "trim"):
        raise ValueError("unknown action")
    for r in extract_runs(3):
        if r["run"] == body.get("run") and r["active"] and not r.get("terminal"):
            j = next((j for j in r["jobs"] if j["job"] == body.get("job")), None)
            if not j:
                raise ValueError("that video isn't in this run")
            (EXR / r["run"] / "ctl").mkdir(exist_ok=True)
            (EXR / r["run"] / "ctl" / j["job"]).write_text(act)
            return {"ok": True}
    raise ValueError("that run isn't running")


def extract_throttle(body):
    """Run cooler (macOS background priority for every process of the run) or back to full speed, mid-run."""
    for r in extract_runs(3):
        if r["run"] == body.get("run") and r["active"]:
            if r.get("terminal"):
                raise ValueError("this run was started from a terminal: it can't be cooled from here")
            (EXR / r["run"] / "throttle").write_text("1" if body.get("on") else "0")
            return {"ok": True}
    raise ValueError("that run isn't running")


def extract_stop(body):
    for r in extract_runs(3):
        if r["run"] == body.get("run") and r["active"]:
            if r.get("terminal"):
                raise ValueError("this run was started from a terminal: stop it there")
            os.killpg(os.getpgid(int(r["pid"])), signal.SIGTERM)   # extract.py and everything it started
            return {"ok": True}
    raise ValueError("that run isn't running")


def trending():
    f = EXR / "trends.json"
    data = load_json(f) if f.exists() else {"at": None, "rows": []}
    for r in data["rows"]:
        jd = JOBS / job_name(r["url"])
        c = candidates(jd, "candidates_audit")["candidates"] if jd.is_dir() else []
        r["job"] = jd.name if (jd / "candidates.json").exists() else None
        r["picks"] = len(c)
    data["refreshing"] = TRENDS["proc"] is not None and TRENDS["proc"].poll() is None
    return data


def trending_refresh(body=None):
    """The trending list; a campaign run asks for one creator's GTA 6 videos (query) over the last 3 days."""
    q = re.sub(r"[^\w\s'.-]", "", str((body or {}).get("query") or ""))[:60].strip()
    if TRENDS["proc"] is None or TRENDS["proc"].poll() is not None:
        EXR.mkdir(parents=True, exist_ok=True)
        TRENDS["proc"] = subprocess.Popen([sys.executable, str(HERE / "trends.py"), "--json", str(EXR / "trends.json"),
                                           "--out", str(EXR / "trends.md"), *(["--query", q, "--hours", "72"] if q else [])], cwd=str(ROOT),
                                          stdout=subprocess.DEVNULL, stderr=open(EXR / "trends.log", "w"))
    return {"ok": True}


# ------------------------------------------------------------------ picks: every moment Claude picked, to render in bulk
# The render queue is append-only (jobs/.extract/render_queue.jsonl); `extract.py --render-queue` works through it and
# writes render_state.json. Hidden picks: jobs/<job>/picks_hidden.json.

RQ, RQS = EXR / "render_queue.jsonl", EXR / "render_state.json"
RQW = {"proc": None, "at": 0}
RQLOCK = threading.Lock()


def rq_lines():
    return [json.loads(l) for l in RQ.read_text().splitlines() if l.strip()] if RQ.exists() else []


def rq_worker_alive():
    if RQW["proc"] is not None and RQW["proc"].poll() is None:
        return True
    try:
        return pid_alive(load_json(RQS).get("pid")) if RQS.exists() else False
    except (json.JSONDecodeError, OSError):
        return True   # mid-write: it's running


def rq_start():
    """Start the render worker unless it's running (not more often than every 20 s, in case it dies at once)."""
    with RQLOCK:
        if rq_worker_alive() or time.time() - RQW["at"] < 20:
            return
        RQW["at"] = time.time()
        EXR.mkdir(parents=True, exist_ok=True)
        RQW["proc"] = subprocess.Popen([sys.executable, str(HERE / "extract.py"), "--render-queue"], cwd=str(ROOT),
                                       stdout=open(EXR / "render_queue.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)


def rq_states():
    """(job, n) -> the latest queue entry for that pick with its state: queued | fetching | rendering | done | failed."""
    try:
        items = load_json(RQS)["items"] if RQS.exists() else {}
    except (json.JSONDecodeError, OSError, KeyError):
        items = {}
    lines = rq_lines()
    cancelled = {l["cancel"] for l in lines if "cancel" in l}
    alive = rq_worker_alive()
    out = {}
    for l in lines:
        if "id" not in l:
            continue
        key = (l["job"], l["n"])
        if l["id"] in cancelled:
            if out.get(key, {}).get("id") == l["id"]:
                out.pop(key)
            continue
        it = items.get(l["id"]) or {"state": "queued"}
        if it["state"] in ("fetching", "rendering") and not alive:
            it = {**it, "state": "failed", "error": "stopped before it finished"}
        out[key] = {"id": l["id"], "op": l.get("op", "render"), "at": l.get("at"), **it}
    if any(v["state"] == "queued" for v in out.values()) and not alive:
        rq_start()   # something is waiting and nothing is working on it
    return out


RSTAGES = {"start": "loading the caption models", "words": "timing the captions word by word", "edit": "editing: hook first, cutting pauses",
           "layout": "finding the webcam", "encode": "encoding the 1080×1920 clip", "audio": "evening out the sound",
           "done": "finishing", "qa": "Claude is checking the finished clip"}
STAGE_RE = re.compile(r"^stage: (\w+) t=([\d.]+)(?: dur=([\d.]+))?")


def render_log(job, n):
    """The stages cut.py reached so far in jobs/.extract/renders/<job>_<n>.log and how far its encode is."""
    f = EXR / "renders" / f"{job}_{n}.log"
    st, out, dur = {}, 0.0, None
    try:
        lines = f.read_text(errors="replace").splitlines()
    except OSError:
        return st, out, dur
    for l in lines:
        m = STAGE_RE.match(l)
        if m:
            st[m.group(1)] = float(m.group(2))
            dur = float(m.group(3)) if m.group(3) else dur
        elif l.startswith("out_time_us=") and l[12:].strip().isdigit():
            out = int(l[12:]) / 1e6
    return st, out, dur


def render_progress(states):
    """The render queue for the page: each pick rendering now (fraction, stage, time left) and the time for the rest.
    Before the encode, progress follows the clock against the usual time to reach the encode (it never passes 50%);
    the encode (the second half) follows ffmpeg's position in the clip."""
    active = [v for v in states.values() if v["state"] in ("queued", "fetching", "rendering")]
    if not active:
        return None
    try:
        items = load_json(RQS).get("items", {}) if RQS.exists() else {}
    except (json.JSONDecodeError, OSError):
        items = {}
    took = sorted((v for v in items.values() if v.get("clip") and v.get("finished") and v.get("started")), key=lambda v: v["finished"])
    recent = took[-10:]
    per = sum(v["finished"] - v["started"] for v in recent) / len(recent) if recent else 40.0
    encs = [v["stages"]["done"] - v["stages"]["encode"] for v in recent if "done" in (v.get("stages") or {}) and "encode" in v["stages"]]
    enc = sum(encs) / len(encs) if encs else 15.0   # seconds the encode usually takes
    pre = max(5.0, per - enc)                       # seconds from start until the encode begins
    since = min(v.get("at") or time.time() for v in active)
    done_now = sum(1 for v in took if v["finished"] >= since - 5)
    now = []
    for (job, n), v in states.items():
        if v["state"] != "rendering":
            continue
        el = time.time() - (v.get("started") or time.time())
        st, out, dur = render_log(job, n)
        last = max(st, key=st.get) if st else "start"
        label = RSTAGES.get(last, last)
        if last == "qa":
            frac, left = 0.97, 15
        elif last in ("encode", "audio", "done") and dur:
            f = min(1.0, out / dur)
            frac, left = 0.5 + 0.5 * f, enc * (1 - f)
            label += f" · {round(100 * f)}%"
        else:
            frac, left = min(0.48, 0.5 * el / pre), max(0.0, pre - el) + enc
        now.append({"job": job, "n": n, "frac": round(frac, 3), "label": label, "left": round(left)})
    queued = sum(1 for v in active if v["state"] != "rendering" and v.get("op", "render") == "render")
    par = max(1, int(os.environ.get("CLIP_RENDERS_AT_ONCE", "3")))
    left = (max((x["left"] for x in now), default=0)) + queued * per / par
    return {"now": now, "queued": queued, "done": done_now, "per": round(per), "par": par, "left": round(left)}


def picks_data():
    states = rq_states()
    rendered = {}   # (job, pick n) -> clips rendered from it
    for m in JOBS.glob("*/edits/*.json"):
        mp4 = m.with_suffix(".mp4")
        if m.name.startswith(".") or not mp4.exists():
            continue
        try:
            args = load_json(m).get("args") or {}
        except (json.JSONDecodeError, OSError):
            continue
        if args.get("pick") and args.get("set", "candidates_audit") == "candidates_audit":
            rendered.setdefault((m.parent.parent.name, int(args["pick"])), []).append(
                {"name": m.stem, "video": rel_media(mp4) + f"?v={int(mp4.stat().st_mtime)}", "updated": mp4.stat().st_mtime})
    videos = []
    files = sorted(JOBS.glob("*/candidates_audit.json"), key=lambda f: -f.stat().st_mtime)[:40]
    for f in files:
        d = f.parent
        try:
            info, c = load_json(d / "info.json"), load_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        hidden = set(load_json(d / "picks_hidden.json")) if (d / "picks_hidden.json").exists() else set()
        rk = pick_rank_state(d)
        src = c.get("source") if c.get("source") and (ROOT / c["source"]).exists() else None
        picks = []
        for p in c["candidates"]:
            fl = p.get("file") if p.get("file") and (ROOT / p["file"]).exists() else None
            vid, off = (fl, p.get("offset") or 0) if fl else (src, 0) if src else (None, 0)
            picks.append({"n": p["n"], "tag": p.get("tag") or "", "title": p.get("title") or "", "titles": p.get("titles") or [],
                          "start": p["start"], "end": p["end"], "text": (p.get("text") or "")[:320],
                          "preview": rel_media(vid) if vid else None, "t0": max(0.0, p["start"] - off), "offset": off,
                          "tease": p.get("tease"),
                          "clips": sorted(rendered.get((d.name, p["n"]), []), key=lambda x: -x["updated"]),
                          "queue": None if (vid and (states.get((d.name, p["n"])) or {}).get("op") == "fetch"
                                            and states[(d.name, p["n"])]["state"] == "failed") else states.get((d.name, p["n"])),
                          "hidden": p["n"] in hidden,
                          "rank": (rk.get("ranks") or {}).get(str(p["n"]))})
        if not picks:
            continue
        url = info.get("webpage_url") or ""
        vid_id = info.get("id") if "youtu" in url else None
        videos.append({"job": d.name, "title": info.get("title") or d.name, "channel": info.get("channel") or info.get("uploader"),
                       "url": url, "thumb": f"https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg" if vid_id else None,
                       "minutes": round((info.get("duration") or 0) / 60), "picked": f.stat().st_mtime, "picks": picks,
                       "ranking": {k: rk.get(k) for k in ("state", "at", "error", "of")} if rk else None})
    return {"videos": videos, "worker": rq_worker_alive(), "now": time.time(), "renders": render_progress(states)}


THUMBS = threading.Semaphore(4)


def pick_rank_state(d):
    """jobs/<job>/picks_rank.json (written by `extract.py --rank`), with a dead "running" turned into "failed"."""
    f = d / "picks_rank.json"
    try:
        rk = load_json(f) if f.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {"state": "running"}   # mid-write
    if rk.get("state") in ("running", "queued") and not pid_alive(rk.get("pid")):
        rk = {"state": "failed", "error": "stopped before it finished"}
    return rk


def pick_find(job, n):
    """(job dir, the pick, its video path or None, offset of that video in source seconds)."""
    if not NAME_RE.match(job or "") or not (JOBS / job / "candidates_audit.json").exists():
        raise ValueError("unknown video")
    d = JOBS / job
    c = load_json(d / "candidates_audit.json")
    p = next((x for x in c["candidates"] if x["n"] == int(n)), None)
    if not p:
        raise ValueError("unknown pick")
    if p.get("file") and (ROOT / p["file"]).exists():
        return d, p, ROOT / p["file"], p.get("offset") or 0
    if c.get("source") and (ROOT / c["source"]).exists():
        return d, p, ROOT / c["source"], 0
    return d, p, None, 0


def pick_words(q):
    """What the Picks page's watch view needs to fake the finished clip: words (source seconds) and the hook."""
    d, p, vid, off = pick_find(q.get("job"), q.get("n", 0))
    t = p.get("tease")
    a = min(p["start"], t[0]) if t else p["start"]
    b = max(p["end"], t[1]) if t else p["end"]
    return {"words": words_for(d, a, b), "start": p["start"], "end": p["end"], "tease": t, "offset": off}


def pick_thumb(job, n):
    """A still of the pick (its payoff, or its middle), cached in jobs/<job>/thumbs/. None when there's no video."""
    d, p, vid, off = pick_find(job, n)
    out = d / "thumbs" / f"pick_{p['n']}.jpg"
    if out.exists():
        return out
    if vid is None:
        return None
    t = p.get("tease")
    at = (t[1] - 0.6 if t else (p["start"] + p["end"]) / 2) - off
    out.parent.mkdir(exist_ok=True)
    tmp = out.with_suffix(".tmp.jpg")
    with THUMBS:
        subprocess.run([tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{max(0, at):.2f}", "-i", str(vid), "-frames:v", "1",
                        "-vf", "scale=480:-2", "-q:v", "4", str(tmp)], capture_output=True, timeout=30)
    if not tmp.exists():
        return None
    tmp.rename(out)
    return out


def picks_render(body):
    items = body.get("items") or []
    op = "fetch" if body.get("op") == "fetch" else "render"
    if not items or len(items) > 200:
        raise ValueError("choose 1 to 200 picks")
    busy = {k for k, v in rq_states().items() if v["state"] in ("queued", "fetching", "rendering")}
    lines = []
    for it in items:
        job, n = str(it.get("job", "")), int(it.get("n", 0))
        if not NAME_RE.match(job) or not (JOBS / job / "candidates_audit.json").exists():
            raise ValueError(f"unknown video: {job[:40]}")
        if (job, n) in busy:
            continue
        lines.append({"id": f"{time.time():.6f}-{job}-{n}", "job": job, "n": n, "op": op, "at": time.time()})
    with RQLOCK, open(RQ, "a") as f:
        f.write("".join(json.dumps(l) + "\n" for l in lines))
    rq_start()
    return {"ok": True, "queued": len(lines)}


def picks_cancel(body):
    st = rq_states().get((str(body.get("job")), int(body.get("n", 0))))
    if not st or st["state"] != "queued":
        raise ValueError("that pick isn't waiting in the queue")
    with RQLOCK, open(RQ, "a") as f:
        f.write(json.dumps({"cancel": st["id"]}) + "\n")
    return {"ok": True}


def picks_hide(body):
    job = str(body.get("job", ""))
    if not NAME_RE.match(job) or not (JOBS / job / "candidates_audit.json").exists():
        raise ValueError("unknown video")
    f = JOBS / job / "picks_hidden.json"
    h = set(load_json(f)) if f.exists() else set()
    ns = {int(n) for n in (body.get("ns") or [body.get("n")]) if n is not None}
    h = h | ns if body.get("hidden", True) else h - ns
    save_json(f, sorted(h))
    return {"ok": True}


# ------------------------------------------------------------------ http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path):
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        if path.suffix == ".m3u8":
            self.send_header("Cache-Control", "no-store")   # live playlist: changes every couple of seconds
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk); left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def send_ui(self, path):
        """The Next.js editor (static export in tool/web/out)."""
        if not (WEB / "index.html").exists():
            b = (b"<!doctype html><meta charset=utf-8><title>Clip editor</title><body style='font:15px system-ui;padding:40px'>"
                 b"<h2>The editor UI is not built yet</h2><p>Run <code>npm install &amp;&amp; npm run build</code> in <code>tool/web</code>, "
                 b"then reload. For development: <code>npm run dev</code> there and open http://localhost:3000 (this server stays the API).</p>")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b); return
        rel = path.lstrip("/") or "index.html"
        p = (WEB / rel).resolve()
        if p.is_dir() and (p / "index.html").exists():
            p = p / "index.html"
        elif (not p.exists() or p.is_dir()) and (WEB / (rel.rstrip("/") + ".html")).exists():
            p = WEB / (rel.rstrip("/") + ".html")   # /live -> live.html (the export also makes a live/ folder)
        if WEB not in p.parents and p != WEB or not p.is_file():
            return self.send_error(404)
        return self.send_file(p)

    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if not u.path.startswith(("/api/", "/media/", "/fonts/")):
                return self.send_ui(u.path)
            if u.path.startswith("/media/"):
                p = (JOBS / u.path[len("/media/"):]).resolve()
                if JOBS not in p.parents or not p.is_file():
                    return self.send_error(404)
                return self.send_file(p)
            if u.path.startswith("/fonts/"):
                p = (FONTS / u.path[len("/fonts/"):]).resolve()
                if p.parent != FONTS or not p.is_file():
                    return self.send_error(404)
                return self.send_file(p)
            if u.path == "/api/clips":
                return self.send_json(clip_list())
            if u.path == "/api/project":
                return self.send_json(project(q["job"], q.get("name"), q.get("set"), q.get("pick")))
            if u.path == "/api/strip":
                return self.send_json(strip(q["job"], q["video"]))
            if u.path == "/api/render":
                return self.send_json(render_status(q["id"]))
            if u.path == "/api/wave":
                return self.send_json(wave(q["job"], q["video"]))
            if u.path == "/api/live":
                return self.send_json(live_status())
            if u.path == "/api/live/top":
                return self.send_json(live_top())
            if u.path == "/api/queue":
                return self.send_json(queue_data())
            if u.path == "/api/picks":
                return self.send_json(picks_data())
            if u.path == "/api/pick/words":
                return self.send_json(pick_words(q))
            if u.path == "/api/pick/thumb":
                f = pick_thumb(q.get("job"), q.get("n", 0))
                return self.send_file(f) if f else self.send_error(404)
            if u.path == "/api/extract":
                return self.send_json({"runs": extract_runs(), "trending": trending(), "claude": bool(shutil.which(os.environ.get("CLAUDE_BIN") or "claude")),
                                       "campaigns": [{"id": c["id"], "name": c["name"], "creators": c.get("creators") or [], "any_gta": bool(c.get("any_gta")),
                                                      "rate": c.get("rate_per_1k"), "pool": c.get("pool")} for c in campaigns_mod().joined()]})
            if u.path == "/api/campaigns":
                return self.send_json(campaigns_data())
            if u.path == "/api/campaigns/overlay":
                C = campaigns_mod(); c = C.get(q.get("id", ""))
                f = C.overlay_file(c) if c else None
                return self.send_file(f) if f else self.send_error(404)
            if u.path == "/api/health":   # the Python this server starts every job with, and what it can't import
                import importlib.util
                mods = ("yt_dlp", "faster_whisper", "numpy", "cv2", "torch", "transformers")
                return self.send_json({"python": sys.executable, "missing": [m for m in mods if importlib.util.find_spec(m) is None],
                                       "ui_built": (WEB / "index.html").exists(), "claude": bool(shutil.which(os.environ.get("CLAUDE_BIN") or "claude")),
                                       "prank": prank_delay()})   # a joke paywall, only while DESK/.prank exists
        except (ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as e:
            return self.send_json({"error": str(e) or e.__class__.__name__}, 400)
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        # browsers send Origin on every POST: refuse other websites (they could start renders or recordings);
        # curl and scripts send none and stay allowed
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return self.send_json({"error": "cross-site request refused"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if u.path == "/api/live/start":
                return self.send_json(live_start(body))
            if u.path == "/api/live/cmd":
                return self.send_json(live_cmd(body))
            if u.path.startswith("/api/queue/"):
                return self.send_json(queue_post(u.path, body))
            if u.path == "/api/extract/start":
                return self.send_json(extract_start(body))
            if u.path == "/api/extract/stop":
                return self.send_json(extract_stop(body))
            if u.path == "/api/picks/render":
                return self.send_json(picks_render(body))
            if u.path == "/api/picks/cancel":
                return self.send_json(picks_cancel(body))
            if u.path == "/api/picks/hide":
                return self.send_json(picks_hide(body))
            if u.path == "/api/extract/job":
                return self.send_json(extract_job(body))
            if u.path == "/api/extract/throttle":
                return self.send_json(extract_throttle(body))
            if u.path == "/api/trending/refresh":
                return self.send_json(trending_refresh(body))
            if u.path.startswith("/api/campaigns/"):
                return self.send_json(campaigns_post(u.path, body))
            if u.path == "/api/render":
                return self.send_json(render(body))
        except (ValueError, KeyError, TypeError) as e:
            return self.send_json({"error": str(e) or e.__class__.__name__}, 400)
        self.send_error(404)


def prank_delay():
    """Seconds on the app before the joke paywall shows (a number in DESK/.prank, default 30), or None when it's off."""
    f = ROOT / ".prank"
    if not f.is_file():
        return None
    try:
        return max(0, min(3600, float(f.read_text(encoding="utf-8").strip() or 30)))
    except ValueError:
        return 30


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args()
    global PORT
    PORT = a.port
    if not JOBS.is_dir():
        sys.exit(f"no jobs/ folder in {ROOT}: run this from the clip desk folder")
    print(f"Clip editor: http://127.0.0.1:{a.port}  (jobs in {JOBS})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
