"""Focus layout: a big 4:5 window of the source over a blurred copy of it, aimed at what matters.

The old fallback (Fit) shows the whole 16:9 frame as a 1080x608 strip: a third of the screen. Focus shows a
1080x1350 window instead (more than twice the area), aimed at:
  cam     a webcam box beside a menu / non-game screen: the streamer, framed on the face inside the webcam
  face    a full-frame camera (IRL, a talking head): locked-off shots framed on the face (facecam.face_crop_expr)
  motion  gameplay: a full-height window that follows where the picture moves, with a pull to the centre
          (GTA's third-person character sits there), a dead zone and a speed limit so it pans calmly, never jitters

choose(video, start, end, sw, sh, cam_result, gameplay) -> {"mode", "w", "h", "x", "y", "note"}: the crop in source
pixels (x may be an ffmpeg expression in t, seconds from `start`) for cut.py's video_filter(layout="track").
"""
import subprocess
import numpy as np

OUT_W, OUT_H = 1080, 1350          # the window on screen (4:5)
AR = OUT_W / OUT_H
ANALYSE_FPS = 5
ANALYSE_W = 320


def even(n):
    return int(n) // 2 * 2


def lin_expr(ts, xs):
    """Piecewise-linear ffmpeg expression in t through (ts[i], xs[i]), held flat outside. Written as a flat sum of
    gated segments (no deep if() nesting); commas escaped for a filtergraph."""
    if len(xs) == 1 or max(xs) - min(xs) < 2:
        return str(even(xs[0]))
    terms = [f"lt(t\\,{ts[0]:.3f})*{xs[0]:.1f}"]
    for i in range(len(ts) - 1):
        t0, t1, x0, x1 = ts[i], ts[i + 1], xs[i], xs[i + 1]
        k = (x1 - x0) / max(1e-3, t1 - t0)
        terms.append(f"gte(t\\,{t0:.3f})*lt(t\\,{t1:.3f})*({x0:.1f}+(t-{t0:.3f})*{k:.3f})")
    terms.append(f"gte(t\\,{ts[-1]:.3f})*{xs[-1]:.1f}")
    return "trunc((" + "+".join(terms) + ")/2)*2"


def gray_frames(video, start, end, ah, ffmpeg="ffmpeg"):
    """Small grey frames at ANALYSE_FPS from [start, end] (ffmpeg decodes: much faster than seeking with OpenCV)."""
    r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-to", f"{end:.3f}", "-i", str(video),
                        "-vf", f"fps={ANALYSE_FPS},scale={ANALYSE_W}:{ah},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, np.uint8)


def motion_path(video, start, end, sw, sh, cw, ffmpeg="ffmpeg"):
    """Keyframes [(t, x)] (t from `start`, x = left edge in source px) for a full-height window cw px wide."""
    ah = even(ANALYSE_W * sh / sw)
    buf = gray_frames(video, start, end, ah, ffmpeg)
    n = len(buf) // (ANALYSE_W * ah)
    centre = (sw - cw) / 2
    if n < 3:
        return [(0.0, centre)]
    f = buf[: n * ANALYSE_W * ah].reshape(n, ah, ANALYSE_W).astype(np.float32)
    diff = np.abs(np.diff(f, axis=0))                       # (n-1, ah, W): what moves between samples
    static = f.std(0) < 4                                    # HUD, overlays, letterbox: never counts as action
    diff[:, static] = 0
    col = diff.mean(1)                                       # (n-1, W) motion per column
    k = max(1, ANALYSE_W // 40)
    col = np.apply_along_axis(lambda c: np.convolve(c, np.ones(k) / k, mode="same"), 1, col)
    win = max(2, int(round(cw / sw * ANALYSE_W)))
    span = ANALYSE_W - win
    if span <= 0:
        return [(0.0, centre)]
    cs = np.concatenate([np.zeros((col.shape[0], 1)), np.cumsum(col, 1)], 1)
    inside = cs[:, win:win + span + 1] - cs[:, :span + 1]   # motion inside the window at every offset
    pos = np.arange(span + 1)
    prior = 1 - 0.35 * (np.abs(pos - span / 2) / max(1, span / 2)) ** 2   # pull to the centre
    best = np.argmax(inside * prior[None, :] + 1e-6 * prior[None, :], 1).astype(float)
    quiet = inside.max(1) < 0.4                              # nothing moves: drift back to the centre
    best[quiet] = span / 2
    # smoothing: median, then a dead zone + speed limit, then an ease (so pans start and stop softly)
    pad = 3
    p = np.pad(best, pad, mode="edge")
    best = np.array([np.median(p[i:i + 2 * pad + 1]) for i in range(len(best))])
    dz, vmax, dt = 0.07 * span, 0.22 * span, 1 / ANALYSE_FPS   # dead zone; max pan per second (of the free span)
    x = [best[0]]
    for b in best[1:]:
        d = b - x[-1]
        move = 0.0 if abs(d) < dz else d - np.sign(d) * dz
        x.append(x[-1] + float(np.clip(move, -vmax * dt, vmax * dt)))
    x = np.array(x)
    g = np.exp(-0.5 * (np.arange(-6, 7) / 3.0) ** 2); g /= g.sum()
    x = np.convolve(np.pad(x, 6, mode="edge"), g, mode="valid")
    ts = (np.arange(len(x)) + 1) / ANALYSE_FPS              # diff i sits between samples i and i+1
    step = 3                                                 # a keyframe every 0.6 s is plenty after smoothing
    idx = list(range(0, len(x), step)) + ([len(x) - 1] if (len(x) - 1) % step else [])
    scale = sw / ANALYSE_W
    return [(round(float(ts[i]), 3), float(np.clip(x[i] * scale, 0, sw - cw))) for i in idx]


def choose(video, start, end, sw, sh, cam=None, gameplay=True, ffmpeg="ffmpeg", force_motion=False):
    """The Focus crop for [start, end] of `video` (see the module doc)."""
    cam = cam or {}
    if not force_motion and cam.get("mode") == "cam" and not gameplay:
        # webcam beside a menu / non-game screen: the 4:5 crop inside the webcam, centred on the face
        x, y, w, h = cam["box"]
        bx, by, bw, bh = x * sw, y * sh, w * sw, h * sh
        ch = bh; cw = ch * AR
        if cw > bw:
            cw = bw; ch = cw / AR
        f = cam.get("face") or [x + w / 2, y + h / 2, 0, 0]
        fcx = (f[0] + f[2] / 2) * sw
        cx = min(max(fcx - cw / 2, bx), bx + bw - cw)
        cy = by + (bh - ch) / 2
        return {"mode": "cam", "w": even(cw), "h": even(ch), "x": even(cx), "y": even(cy),
                "note": "the webcam, framed on the face (no game on screen)"}
    if not force_motion and cam.get("face_track") and not gameplay:
        from facecam import face_crop_expr
        fe = face_crop_expr(cam["face_track"], sw, sh, OUT_W, OUT_H, head_fill=0.30)
        if fe:
            return {"mode": "face", "w": fe["w"], "h": fe["h"], "x": fe["x"], "y": fe["y"],
                    "note": f"framed on the face, {len(fe['shots'])} shot(s)"}
    ch = even(sh); cw = even(min(sw, ch * AR))
    keys = motion_path(video, start, end, sw, sh, cw, ffmpeg)
    xs = [k[1] for k in keys]
    return {"mode": "motion", "w": cw, "h": ch, "x": lin_expr([k[0] for k in keys], xs), "y": 0,
            "note": f"follows the action, pans {max(xs) - min(xs):.0f}px", "path": [[k[0], round(k[1])] for k in keys]}
