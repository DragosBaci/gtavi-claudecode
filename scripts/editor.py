"""Clip editor: edit a clip in the browser, render it with cut.py.

usage: python tool/scripts/editor.py [--port 8766]      (run from the clip desk folder, the one with jobs/)
then open http://127.0.0.1:8766

The page edits the same options cut.py takes (start/end, hook, cut and keep ranges, webcam crop, layout, title,
captions, transition) on the downloaded source video, previews them live, and renders with cut.py.
Standard library only (plus ffmpeg/ffprobe). Listens on 127.0.0.1 only.
"""
import argparse, json, mimetypes, re, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ROOT, ffprobe, load_json, tool  # noqa: E402

JOBS = (ROOT / "jobs").resolve()
PAGE = HERE.parent / "editor" / "index.html"
FONTS = (HERE.parent / "fonts").resolve()
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
TRANSITION_NAMES = ("zoom", "whip", "slide", "fade", "pixel", "circle", "dissolve", "flash", "cut", "rewind")
RENDERS = {}           # id -> {"proc", "log", "job", "name", "started"}
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
            if meta.get("layout") == "split" or not isinstance(meta.get("segments"), list):
                continue   # long split-screen clips are not editable here yet
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
        p = next((x for x in c["candidates"] if x["n"] == int(pick)), None)
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
        "transition": args.get("transition", "zoom"), "tighten": args.get("tighten", 0.45),
        "keep_after_tease": bool(args.get("keep_after_tease")),
        "punch": not args.get("no_punch", False), "trim": not args.get("no_trim", False),
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
            "game_x": meta.get("game_x"), "rendered": rendered, "is_new": not meta,
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
    job, name = body["job"], body["name"]
    d = job_dir(job)
    if not NAME_RE.match(name):
        raise ValueError("clip name: letters, digits, _ and - only")
    s = body["state"]
    layout = s["layout"]
    if layout not in ("react", "fill", "blur", "facecam"):
        raise ValueError("layout")
    cmd = [sys.executable, str(HERE / "cut.py"), str(d.relative_to(ROOT)), "--set", str(body["set"]),
           "--pick", str(int(body["pick"])), "--layout", layout, "--name", name,
           "--start", f"{num(s['start']):.3f}", "--end", f"{num(s['end']):.3f}",
           "--title", str(s.get("title", ""))[:120], "--title-style", "outline" if s.get("title_style") == "outline" else "box",
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
        RENDERS[rid] = {"proc": proc, "log": log, "job": job, "name": name, "started": time.time()}
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

    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                b = PAGE.read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b); return
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
        except (ValueError, KeyError, StopIteration, subprocess.CalledProcessError) as e:
            return self.send_json({"error": str(e) or e.__class__.__name__}, 400)
        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if u.path == "/api/render":
                return self.send_json(render(body))
        except (ValueError, KeyError, TypeError) as e:
            return self.send_json({"error": str(e) or e.__class__.__name__}, 400)
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args()
    if not JOBS.is_dir():
        sys.exit(f"no jobs/ folder in {ROOT}: run this from the clip desk folder")
    print(f"Clip editor: http://127.0.0.1:{a.port}  (jobs in {JOBS})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
