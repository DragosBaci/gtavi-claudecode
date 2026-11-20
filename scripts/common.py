"""Shared helpers: paths, tool lookup, yt-dlp and ffmpeg wrappers."""
import hashlib, json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(os.environ.get("CLIP_DESK_HOME", Path.cwd()))
JOBS = ROOT / "jobs"


# VP9 first: at the same resolution YouTube's VP9 is much cleaner than its H.264 (~1.8 Mbps at 1080p60).
# Up to 1440p so the small webcam area has more pixels to enlarge. No AV1: OpenCV (facecam.py) may not decode it.
VIDEO_FMT = ("bv*[height<=1440][vcodec^=vp09]+ba[ext=m4a]/bv*[height<=1440][vcodec^=vp9]+ba[ext=m4a]/"
             "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b")
# --force-keyframes-at-cuts re-encodes the section; without this ffmpeg uses its default (crf 23), a visible loss
SECTION_ENCODE = ["--downloader-args", "ffmpeg_o:-c:v libx264 -preset veryfast -crf 14 -pix_fmt yuv420p -c:a aac -b:a 192k"]

FULL_FFMPEG_DIRS = [os.environ.get("CLIP_FFMPEG_DIR", ""), "/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin"]


def tool(name):
    """Find a binary. ffmpeg/ffprobe prefer Homebrew's ffmpeg-full (has libass for burned captions)."""
    if name in ("ffmpeg", "ffprobe"):
        for d in FULL_FFMPEG_DIRS:
            if d and (Path(d) / name).exists():
                return str(Path(d) / name)
    p = shutil.which(name)
    if not p:
        sys.exit(f"{name} not found. Run the setup skill first (it installs ffmpeg and yt-dlp).")
    return p


def run(cmd, check=True, capture=False):
    r = subprocess.run(cmd, check=False, text=True, encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)
    if check and r.returncode != 0:
        msg = (r.stderr or "")[-1500:] if capture else ""
        sys.exit(f"command failed ({r.returncode}): {' '.join(map(str, cmd))[:200]}\n{msg}")
    return r


def ytdlp_base():
    import importlib.util
    exe = [sys.executable, "-m", "yt_dlp"] if importlib.util.find_spec("yt_dlp") else [tool("yt-dlp")]
    base = exe + ["--no-warnings", "--no-progress"]
    # opt-in, when YouTube asks to "confirm you're not a bot": CLIP_COOKIES_BROWSER=chrome (or a cookies.txt path)
    ck = os.environ.get("CLIP_COOKIES_BROWSER", "").strip()
    if ck:
        base += ["--cookies", ck] if os.path.isfile(ck) else ["--cookies-from-browser", ck]
    if sys.platform == "win32":
        base += ["--compat-options", "no-certifi"]  # use the Windows certificate store
    return base


def ytdlp_json(url, extra=()):
    r = run(ytdlp_base() + ["-J", *extra, url], capture=True)
    return json.loads(r.stdout)


def is_url(s):
    return bool(re.match(r"https?://", s))


def job_dir(src):
    key = hashlib.sha1(src.encode()).hexdigest()[:8]
    stem = re.sub(r"[^a-z0-9]+", "-", Path(src).stem.lower() if not is_url(src) else src.split("v=")[-1].split("/")[-1].lower())[:24].strip("-")
    d = JOBS / f"{stem or 'job'}-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ffprobe(path):
    r = run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
             "-of", "json", str(path)], capture=True)
    return json.loads(r.stdout)


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
