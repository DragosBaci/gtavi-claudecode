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
from common import ROOT, ffprobe, load_json, tool  # noqa: E402

JOBS = (ROOT / "jobs").resolve()
WEB = (HERE.parent / "web" / "out").resolve()   # the Next.js editor UI, built with `npm run build` in tool/web
FONTS = (HERE.parent / "fonts").resolve()
FILLERS = (ROOT / "fillers").resolve()   # brainrot gameplay for the Split-with-filler layout (Subway Surfers, ramps...)
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
TRANSITION_NAMES = ("zoom", "whip", "slide", "fade", "pixel", "circle", "dissolve", "flash", "cut", "rewind")
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
        return ROOT / meta["video"], meta.get("video_offset", 0), args.get("set", "candidates_audit"), args.get("pick")
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
        "layout": meta.get("layout") or args.get("layout") or "react",
        "title": meta.get("title") if meta else p.get("title", ""),
        "title_style": args.get("title_style", "box"), "captions": args.get("captions", "word"),
        "title_secs": float(args.get("title_secs") or 0),   # 0 = the title stays the whole clip
        "transition": args.get("transition", "zoom"), "tighten": args.get("tighten", 0.45),
        "keep_after_tease": bool(args.get("keep_after_tease")),
        "punch": not args.get("no_punch", False), "trim": not args.get("no_trim", False),
        # brainrot layout: keep the filler the last render used (a "random" pick stays the same video)
        "filler": Path(meta["filler"]).stem if meta.get("filler") else (args.get("filler") or "random"),
        "split": float(args.get("split") or 0.5),
        "filler_start": meta.get("filler_start") if meta.get("filler_start") is not None else args.get("filler_start"),
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
            "game_x": meta.get("game_x"), "rendered": rendered, "is_new": not meta, "fillers": fillers(),
            "source": {"title": meta.get("source_title") or load_json(d / "info.json").get("title"),
                       "channel": meta.get("source_channel") or load_json(d / "info.json").get("channel")}}


def fillers():
    """The videos in fillers/ for the brainrot layout."""
    out = []
    for f in sorted(FILLERS.glob("*.mp4")) if FILLERS.is_dir() else []:
        try:
            v = probe(f)
        except Exception:
            continue
        out.append({"name": f.stem, "url": "/fillers/" + f.name, "dur": v["dur"], "w": v["w"], "h": v["h"]})
    return out


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
    job, name = body["job"], body["name"]
    d = job_dir(job)
    if not NAME_RE.match(name):
        raise ValueError("clip name: letters, digits, _ and - only")
    s = body["state"]
    layout = s["layout"]
    if layout not in ("react", "fill", "blur", "facecam", "split"):
        raise ValueError("layout")
    cmd = [sys.executable, str(HERE / "cut.py"), str(d.relative_to(ROOT)), "--set", str(body["set"]),
           "--pick", str(int(body["pick"])), "--layout", layout, "--name", name,
           "--start", f"{num(s['start']):.3f}", "--end", f"{num(s['end']):.3f}",
           "--title", str(s.get("title", ""))[:120], "--title-style", "outline" if s.get("title_style") == "outline" else "box",
           "--title-secs", f"{num(s.get('title_secs') or 0, 0, 600):.2f}",
           "--captions", s["captions"] if s.get("captions") in ("word", "bubble", "none") else "word",
           "--transition", s["transition"] if s.get("transition") in TRANSITION_NAMES else "zoom",
           "--tighten", f"{num(s.get('tighten', 0.45), 0, 5):.2f}"]
    if s.get("tease"):
        a, b = num(s["tease"][0]), num(s["tease"][1])
        cmd += ["--tease", f"{a:.3f}:{b:.3f}"]
    else:
        cmd += ["--no-tease"]
    cam = s.get("cam", "auto")
    if layout in ("react", "facecam") and cam != "auto":
        parts = [num(x, 0, 1) for x in cam.split(":")]
        if len(parts) != 4 or parts[2] <= 0.01 or parts[3] <= 0.01:
            raise ValueError("cam box")
        cmd += ["--cam", ":".join(f"{x:.4f}" for x in parts)]
    if layout == "react":
        cmd += ["--force"]
    for k in ("keep", "drop"):
        for a, b in s.get(k) or []:
            cmd += [f"--{k}", f"{num(a):.3f}:{num(b):.3f}"]
    if layout == "split":
        f = str(s.get("filler") or "random")
        if f != "random" and not (FILLERS / f"{f}.mp4").is_file():
            raise ValueError(f"no filler video fillers/{f}.mp4")
        cmd += ["--filler", f, "--split", f"{num(s.get('split', 0.5), 0.3, 0.7):.2f}"]
        if s.get("filler_start") is not None:   # the exact spot the editor previewed
            cmd += ["--filler-start", f"{num(s['filler_start'], 0, 1e6):.2f}"]
    if s.get("keep_after_tease"):
        cmd += ["--keep-after-tease"]
    if not s.get("punch", True):
        cmd += ["--no-punch"]
    if not s.get("trim", True):
        cmd += ["--no-trim"]
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


# ------------------------------------------------------------------ presence: live cursors, shared edits, agent steps
# Every open editor window posts its cursor ~7 times a second. Windows on the same clip see each other's cursors,
# share the latest edit state (last writer wins), and pick up agent jobs: scripted edits (POST /api/agent) that the
# page performs with a visible "Claude" cursor. Kept in memory; restarting the server clears it.

PRES, SHARED, AGENT = {}, {}, {}


def clip_render(clip):
    job, _, name = clip.partition("/")
    for rid, r in sorted(RENDERS.items(), reverse=True):
        if r["job"] == job and r["name"] == name:
            st = render_status(rid)
            if st["state"] == "running" or time.time() - r["started"] - st["elapsed"] < 0.5:
                return {"id": rid, "state": st["state"], "stage": st["stage"], "elapsed": st["elapsed"], "by": r.get("by")}
            return {"id": rid, "state": st["state"], "stage": st["stage"], "elapsed": st["elapsed"], "by": r.get("by")}
    return None


def presence(body):
    now = time.time()
    sid = str(body.get("sid", ""))[:40]
    clip = str(body.get("clip", ""))[:200]
    if not sid:
        raise ValueError("sid")
    with LOCK:
        me = PRES.setdefault(sid, {})
        me.update(sid=sid, name=str(body.get("name", "?"))[:24], color=str(body.get("color", "#ffffff"))[:16], clip=clip,
                  cursor=body.get("cursor"), action=str(body.get("action") or "")[:80] or None, at=now)
        for g in body.get("ghosts") or []:   # agent cursors this window is animating
            gid = "ghost:" + str(g.get("sid", ""))[:40]
            PRES[gid] = {"sid": gid, "name": str(g.get("name", "Claude"))[:24], "color": str(g.get("color", "#d97757"))[:16],
                         "clip": clip, "cursor": g.get("cursor"), "action": str(g.get("action") or "")[:80] or None,
                         "at": now, "owner": sid, "ghost": True}
        if body.get("state") is not None:
            cur = SHARED.get(clip, {"v": 0})
            SHARED[clip] = {"v": cur["v"] + 1, "state": body["state"], "by": sid, "name": me["name"],
                            "action": str(body.get("state_action") or "")[:80], "at": now}
        for s_ in [s_ for s_, p in PRES.items() if now - p["at"] > 30]:
            PRES.pop(s_, None)
        peers = [{k: p.get(k) for k in ("sid", "name", "color", "cursor", "action", "ghost")}
                 for s_, p in PRES.items() if s_ != sid and p.get("clip") == clip and now - p["at"] < 4 and p.get("owner") != sid]
        sh = SHARED.get(clip)
        shared = dict(sh) if sh and sh["v"] > int(body.get("known_v") or 0) and sh["by"] != sid else None
        jobs = []
        for j in AGENT.get(clip, []):
            if j["id"] in (body.get("agent_done") or []):
                j["state"] = "done"
            if j["state"] == "queued":
                j["state"], j["claimed_by"] = "running", sid
                jobs.append(j)
    return {"peers": peers, "shared": shared, "v": sh["v"] if sh else 0, "agent": jobs, "render": clip_render(clip)}


def agent_post(body):
    clip = str(body.get("clip", ""))
    job, _, name = clip.partition("/")
    job_dir(job)
    if not NAME_RE.match(name):
        raise ValueError("clip must be <job>/<clip name>")
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 60:
        raise ValueError("steps: a list of 1-60 steps")
    j = {"id": f"a{int(time.time() * 1000)}", "name": str(body.get("name") or "Claude")[:24], "steps": steps,
         "state": "queued", "at": time.time()}
    with LOCK:
        AGENT.setdefault(clip, []).append(j)
        AGENT[clip] = AGENT[clip][-20:]
    open_now = any(p.get("clip") == clip and time.time() - p["at"] < 4 and not p.get("ghost") for p in PRES.values())
    return {"id": j["id"], "state": j["state"], "editor_open": open_now,
            "note": None if open_now else f"open http://127.0.0.1:{PORT}/edit?job={job}&name={name} to watch it run"}


def agent_status(clip):
    return [{k: j.get(k) for k in ("id", "name", "state", "at")} for j in AGENT.get(clip, [])]


# ------------------------------------------------------------------ live: watch.py driven from the Live page
# watch.py writes jobs/.live/status.json and reads commands from jobs/.live/cmd/, so the page also sees (and can
# stop) a watch.py started from the terminal. Started from here it runs in its own session: restarting this
# server doesn't stop the recording.

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

def rendered_clips(limit=80, skip_ids=()):
    """Rendered clips (jobs/<job>/edits/<name>.mp4) across all jobs, newest first."""
    out = []
    for d in JOBS.iterdir():
        if not d.is_dir() or d.name.startswith(".") or not (d / "edits").is_dir():
            continue
        info = None
        for mp4 in (d / "edits").glob("*.mp4"):
            cid = f"{d.name}/{mp4.stem}"
            if mp4.name.startswith(".") or cid in skip_ids or not NAME_RE.match(mp4.stem):
                continue
            try:
                meta = load_json(mp4.with_suffix(".json")) if mp4.with_suffix(".json").exists() else {}
                info = info if info is not None else (load_json(d / "info.json") if (d / "info.json").exists() else {})
            except (json.JSONDecodeError, OSError):
                continue
            out.append({"id": cid, "job": d.name, "name": mp4.stem, "title": meta.get("title") or meta.get("hook") or mp4.stem,
                        "layout": meta.get("layout"), "updated": mp4.stat().st_mtime,
                        "creator": meta.get("source_channel") or info.get("channel") or info.get("uploader"),
                        "video": rel_media(mp4) + f"?v={int(mp4.stat().st_mtime)}"})
    return sorted(out, key=lambda x: -x["updated"])[:limit]


# ------------------------------------------------------------------ queue: the posting queue (queue/ in the clip desk folder)
# One source of truth with the auto-uploader: scripts/uploader.py merges the clips added here (added.json), text
# edited here (overrides.json) and an optional hand-ranked queue.json; state.json is shared with the uploader.

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
    rr = U.jload(U.UP / "rerank.json", {})
    rr["running"] = rr.get("state") == "running" and RERANK["proc"] is not None and RERANK["proc"].poll() is None
    rr["order_at"] = U.jload(QDIR / "order.json", {}).get("at")
    pn = U.jload(U.UP / "postnow.json", {})
    pn["running"] = pn.get("state") == "running" and pid_alive(pn.get("pid"))
    if pn.get("state") == "running" and not pn["running"]:
        pn["state"] = "stopped"
    pn["log"] = tail(U.UP / "postnow.log", 16, noise=False) if pn.get("id") else []
    return {"items": items, "other": rendered_clips(80, {x["id"] for x in items}), "max_dur": U.MAX_DUR, "rerank": rr, "postnow": pn,
            "account": U.jload(U.ACCOUNT, None),
            "limits": {"tiktok": 2200, "yt_title": 100, "yt_desc": 5000, "instagram": 2200, "x": 280},
            "last_post": last or None, "log": log}


RERANK = {"proc": None}


def queue_post(path, body):
    U = uploader()
    if path == "/api/queue/account":   # the account the uploader may post as (queue/accounts.json)
        nm = str(body.get("name", "")).strip()[:60]
        if not nm:
            raise ValueError("the account name is empty")
        hs = [h for h in re.split(r"[\s,]+", str(body.get("handles", ""))) if h][:6]
        U.jsave(U.ACCOUNT, {"name": nm, "handles": [h.lstrip("@").lower()[:40] for h in hs]})
        return {"ok": True}
    if path == "/api/queue/rerank":   # Claude orders the unposted clips (scripts/rerank.py -> queue/order.json)
        if RERANK["proc"] is not None and RERANK["proc"].poll() is None:
            raise ValueError("already re-ranking")
        U.UP.mkdir(parents=True, exist_ok=True)
        U.jsave(U.UP / "rerank.json", {"state": "running", "started": time.time()})
        RERANK["proc"] = subprocess.Popen([sys.executable, str(HERE / "rerank.py")], cwd=str(ROOT),
                                          stdout=open(U.UP / "rerank.log", "w"), stderr=subprocess.STDOUT)
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
        added = U.jload(U.ADDED, [])
        if not any(x["id"] == cid for x in added):
            raise ValueError("this clip comes from queue/queue.json (a hand-ranked list): remove it there, or skip it for the uploader")
        U.jsave(U.ADDED, [x for x in added if x["id"] != cid])
        return {"ok": True}
    raise ValueError("unknown queue action")


# ------------------------------------------------------------------ extract: YouTube video -> pipeline -> Claude's picks -> clips

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
            jd = JOBS / j["job"]
            pr = load_json(jd / "progress.json") if (jd / "progress.json").exists() else {}
            j["progress"] = {k: pr.get(k) for k in ("stage", "chunks_total", "chunks_done", "sections_total", "sections_done", "error")}
            if not j.get("title") and (jd / "info.json").exists():
                info = load_json(jd / "info.json"); j["title"], j["channel"] = info.get("title"), info.get("channel")
            j["claude_log"] = tail(d / f"claude_{j['job']}.log", 14, noise=False) if st.get("opts", {}).get("claude") else []
        st["pipeline_log"] = tail(d / "pipeline.log", 6)
        st["render_log"] = tail(d / "render.log", 4)
        runs.append(st)
    return runs


def extract_start(body):
    if any(r["active"] for r in extract_runs(3)):
        raise ValueError("an extraction is already running")
    urls = list(dict.fromkeys(str(u).strip() for u in body.get("urls") or [] if str(u).strip()))
    if not urls or len(urls) > 10:
        raise ValueError("1 to 10 video links")
    for u in urls:
        if not VIDEO_URL.match(u):
            raise ValueError(f"not a YouTube video link: {u[:80]}")
    cmd = [sys.executable, str(HERE / "extract.py"), *urls,
           "--max-minutes", str(int(max(10, min(600, float(body.get("minutes") or 90))))),
           "--picks", str(int(max(1, min(10, float(body.get("picks") or 5))))),
           "--power", body.get("power") if body.get("power") in ("full", "balanced", "cool") else "full"]
    if not body.get("claude", True):
        cmd.append("--no-claude")
    if not body.get("render", True):
        cmd.append("--no-render")
    EXR.mkdir(parents=True, exist_ok=True)
    with open(EXR / "last_start.log", "w") as log:
        subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"ok": True}


def extract_throttle(body):
    """Run cooler (macOS background priority for every process of the run) or back to full speed, mid-run."""
    for r in extract_runs(3):
        if r["run"] == body.get("run") and r["active"]:
            (EXR / r["run"] / "throttle").write_text("1" if body.get("on") else "0")
            return {"ok": True}
    raise ValueError("that run isn't running")


def extract_stop(body):
    for r in extract_runs(3):
        if r["run"] == body.get("run") and r["active"]:
            os.killpg(os.getpgid(int(r["pid"])), signal.SIGTERM)   # extract.py and everything it started
            return {"ok": True}
    raise ValueError("that run isn't running")


def extract_rerender(body):
    if any(r["active"] for r in extract_runs(3)):
        raise ValueError("an extraction is already running")
    run = str(body.get("run", ""))
    if not re.match(r"^\d{8}-\d{6}$", run) or not (EXR / run / "status.json").exists():
        raise ValueError("unknown run")
    with open(EXR / "last_start.log", "w") as log:
        subprocess.Popen([sys.executable, str(HERE / "extract.py"), "--rerender", run], cwd=str(ROOT), stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    return {"ok": True}


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


def trending_refresh():
    if TRENDS["proc"] is None or TRENDS["proc"].poll() is not None:
        EXR.mkdir(parents=True, exist_ok=True)
        TRENDS["proc"] = subprocess.Popen([sys.executable, str(HERE / "trends.py"), "--json", str(EXR / "trends.json"),
                                           "--out", str(EXR / "trends.md")], cwd=str(ROOT),
                                          stdout=subprocess.DEVNULL, stderr=open(EXR / "trends.log", "w"))
    return {"ok": True}


def home():
    q = {"to_post": 0, "posted_24h": 0, "next": None, "last_post": None}
    try:
        qd = queue_data()
        todo = [i for i in qd["items"] if not i["skipped"] and not i.get("hold")
                and not any(i["state"].get(p, {}).get("posted") for p in ("tiktok", "youtube", "instagram"))]
        now = time.time()
        q = {"to_post": len(todo), "total": len(qd["items"]), "next": todo[0]["title"] if todo else None, "last_post": qd["last_post"],
             "posted_24h": sum(1 for i in qd["items"] for v in i["state"].values() if v.get("posted") and now - v.get("updated", 0) < 86400)}
    except Exception:
        pass
    live = live_state()
    return {"live": {"running": bool(live), "streams": [s["tag"] for s in (live or {}).get("streams", []) if s.get("alive")],
                     "clips": len(live_clips(200))},
            "extract": (extract_runs(1) or [None])[0], "queue": q, "recent": rendered_clips(12), "now": time.time()}


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
            if not u.path.startswith(("/api/", "/media/", "/fonts/", "/fillers/")):
                return self.send_ui(u.path)
            if u.path.startswith("/media/"):
                p = (JOBS / u.path[len("/media/"):]).resolve()
                if JOBS not in p.parents or not p.is_file():
                    return self.send_error(404)
                return self.send_file(p)
            if u.path.startswith("/fillers/"):
                p = (FILLERS / u.path[len("/fillers/"):]).resolve()
                if p.parent != FILLERS or not p.is_file():
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
            if u.path == "/api/agent":
                return self.send_json(agent_status(q["clip"]))
            if u.path == "/api/live":
                return self.send_json(live_status())
            if u.path == "/api/live/top":
                return self.send_json(live_top())
            if u.path == "/api/home":
                return self.send_json(home())
            if u.path == "/api/queue":
                return self.send_json(queue_data())
            if u.path == "/api/extract":
                return self.send_json({"runs": extract_runs(), "trending": trending(), "claude": bool(shutil.which(os.environ.get("CLAUDE_BIN") or "claude"))})
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
            if u.path == "/api/extract/throttle":
                return self.send_json(extract_throttle(body))
            if u.path == "/api/extract/rerender":
                return self.send_json(extract_rerender(body))
            if u.path == "/api/trending/refresh":
                return self.send_json(trending_refresh())
            if u.path == "/api/render":
                return self.send_json(render(body))
            if u.path == "/api/presence":
                return self.send_json(presence(body))
            if u.path == "/api/agent":
                return self.send_json(agent_post(body))
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
