"""Frame strips so Claude can SEE a moment before it is rendered, and check a clip after.

Picks are chosen from the transcript, but these are visual clips: a great line over a static menu is a bad clip.
  pick strip   jobs/<id>/review/pick_<n>.jpg: 8 frames of the source moment (the first is the tease/hook frame),
               each labelled with its time, plus picture stats: how much it moves, how much of it is static.
  clip strip   jobs/<id>/review/clip_<name>.jpg: 8 frames of a finished 1080x1920 clip (captions, title, crop as
               posted), plus frozen / black stretches found by ffmpeg.
usage: python frames.py jobs/<id> [--pick N ...]       strips for the picks in candidates_audit.json (all by default)
       python frames.py jobs/<id> --clip <name>        strip for jobs/<id>/edits/<name>.mp4
Prints JSON: {n or name: {"sheet": path, "motion": .., "static_share": .., ...}}
"""
import argparse, json, re, subprocess, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, ffprobe, load_json, tool

TILE_W = 384


def pick_video(d, c, p):
    """(video path, offset): the pick's downloaded section, or the job's full source. None if there's no video yet."""
    f = p.get("file")
    if f and (ROOT / f).exists():
        return ROOT / f, float(p.get("offset") or 0)
    if c.get("source") and Path(c["source"]).exists():
        return Path(c["source"]), 0.0
    full = Path(d) / "source.mp4"
    return (full, 0.0) if full.exists() else (None, 0.0)


def grab(video, t):
    """One frame at t seconds (BGR) via ffmpeg (accurate seek, any codec)."""
    r = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={TILE_W}:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    if not r.stdout:
        return None
    import cv2
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def tile(frames, labels, cols, out):
    import cv2
    frames = [f for f in frames if f is not None]
    if not frames:
        return None
    h, w = frames[0].shape[:2]
    rows = (len(frames) + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 3), np.uint8)
    for i, (f, lab) in enumerate(zip(frames, labels)):
        f = cv2.resize(f, (w, h))
        r, c = divmod(i, cols)
        cv2.rectangle(f, (0, 0), (len(lab) * 11 + 10, 26), (0, 0, 0), -1)
        cv2.putText(f, lab, (5, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        sheet[r * h:(r + 1) * h, c * w:(c + 1) * w] = f
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return str(out)


def picture_stats(video, a, b):
    """How alive the picture is in [a, b]: motion = mean frame-to-frame change (0-255, at 2 fps, 160 px wide);
    static_share = share of 0.5 s steps where almost nothing changed (menus, loading, paused game)."""
    w, h = 160, 90
    r = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{max(0.0, a):.3f}", "-to", f"{b:.3f}", "-i", str(video),
                        "-vf", f"fps=2,scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    buf = np.frombuffer(r.stdout, np.uint8)
    n = len(buf) // (w * h)
    if n < 3:
        return {"motion": None, "static_share": None}
    f = buf[:n * w * h].reshape(n, h, w).astype(np.float32)
    dif = np.abs(np.diff(f, axis=0)).mean((1, 2))
    return {"motion": round(float(dif.mean()), 2), "static_share": round(float((dif < 1.5).mean()), 2)}


def pick_sheet(d, c, p):
    video, off = pick_video(d, c, p)
    if video is None:
        return None
    t = p.get("tease")
    times, labels = [], []
    if t:
        times.append((t[0] + t[1]) / 2); labels.append(f"HOOK {t[0]:.1f}s")
    k = 8 - len(times)
    for i in range(k):
        s = p["start"] + (p["end"] - p["start"]) * (i + 0.5) / k
        times.append(s); labels.append(f"{s:.1f}s")
    frames = [grab(video, x - off) for x in times]
    out = Path(d) / "review" / f"pick_{p['n']}.jpg"
    res = {"sheet": tile(frames, labels, 4, out)}
    res.update(picture_stats(video, p["start"] - off, p["end"] - off))
    return res


def clip_sheet(d, name):
    mp4 = Path(d) / "edits" / f"{name}.mp4"
    dur = float(ffprobe(mp4)["format"]["duration"])
    times = [0.3, 1.2] + [dur * (i + 0.5) / 6 for i in range(6)]
    frames = [grab(mp4, x) for x in times]
    res = {"sheet": tile(frames, [f"{x:.1f}s" for x in times], 8, Path(d) / "review" / f"clip_{name}.jpg"),
           "duration": round(dur, 1)}
    res.update(picture_stats(mp4, 0, dur))
    # frozen or black stretches over 1.5 s (a stuck section download, a black transition gone wrong)
    r = subprocess.run([tool("ffmpeg"), "-v", "info", "-i", str(mp4), "-vf", "freezedetect=n=0.003:d=1.5,blackdetect=d=0.5",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    res["frozen"] = [round(float(x), 1) for x in re.findall(r"freeze_duration: ([\d.]+)", r.stderr)]
    res["black"] = [round(float(x), 1) for x in re.findall(r"black_duration:([\d.]+)", r.stderr)]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job"); ap.add_argument("--pick", type=int, action="append"); ap.add_argument("--clip")
    a = ap.parse_args()
    d = Path(a.job)
    if a.clip:
        print(json.dumps({a.clip: clip_sheet(d, a.clip)}))
        return
    c = load_json(d / "candidates_audit.json")
    out = {}
    for p in c["candidates"]:
        if a.pick and p["n"] not in a.pick:
            continue
        out[p["n"]] = pick_sheet(d, c, p)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
