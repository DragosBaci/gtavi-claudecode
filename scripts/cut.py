"""Cut one candidate into a finished vertical clip.

1080x1920 at the source frame rate (up to 60 fps), word-by-word burned captions timed by forced alignment
(align.py; slurs and swears masked), loudness normalised to -14 LUFS, high-quality encode for re-upload.

Hook-first editing (all on by default, each can be turned off):
  --tease S:E     the culminating moment (source seconds, 1-2 s) plays first, then a fast rewind (--transition), then the
                  clip restarts from the setup and builds up to that moment, ending right after it (--after-tease 0.8).
                  Default: the pick's "tease" field from picks.json (add_picks.py).
  trim            start on the first word, end shortly after the last one (longer while it is still loud: laughter).
  --tighten 0.45  cut pauses in the speech longer than this (kept when the pause is loud: screams, laughs, explosions).
  punch-in        quick zoom over the first 0.4 s so the first frame moves.
Writes jobs/<id>/edits/clip_<n>.mp4 + .json (metadata for check.py).

usage: python cut.py jobs/<id> --pick 1 [--layout fill|blur|facecam|react] [--hook "TEXT"]
                     [--cam auto|x:y:w:h] [--title "TEXT" --title-style box|outline --title-secs 0] [--captions word|bubble|none]
                     [--tease S:E | --no-tease] [--tighten 0.45 | --tighten 0] [--no-trim] [--no-punch] [--fast-words]
"""
import argparse, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, run, save_json, tool

W, H = 1080, 1920
FONTS = Path(__file__).resolve().parent.parent / "fonts"   # Montserrat (SIL OFL), loaded by libass via fontsdir
CAP_FONT, TITLE_FONT = "Montserrat Black", "Montserrat ExtraBold"
CAP_SIZE, TITLE_SIZE = 118, 80
TITLE_Y = {"facecam": int(1920 * 0.38), "fill": 300, "blur": 380, "track": 300}   # title box centre per layout (the editor preview matches)
LIBASS_SCALE = 0.786   # measured: libass draws Montserrat this much narrower than PIL at the same nominal size


def text_width(text, size, font_file="Montserrat-ExtraBold.ttf"):
    """Rendered width in px of `text` at ASS font size `size` (libass sizes fonts by ascent+descent, not em)."""
    try:
        from PIL import ImageFont
        probe = ImageFont.truetype(str(FONTS / font_file), 100)
        asc, desc = probe.getmetrics()
        px = ImageFont.truetype(str(FONTS / font_file), max(1, round(size * 100 / (asc + desc)))).getlength(text)
        return px * LIBASS_SCALE
    except Exception:
        return len(text) * size * 0.5


def wrap_px(text, size, max_w, font_file="Montserrat-ExtraBold.ttf"):
    """Fewest lines that fit max_w, with the words spread evenly over them (no lone "GTA 6" on line two).
    A short number stays with the word before it ("GTA 6" is never split)."""
    words = []
    for wd in text.split():
        if words and _re.fullmatch(r"\d{1,2}\W*", wd):
            words[-1] += " " + wd
        else:
            words.append(wd)
    lines, cur = [], ''
    for wd in words:
        cand = (cur + ' ' + wd).strip()
        if cur and text_width(cand, size, font_file) > max_w:
            lines.append(cur); cur = wd
        else:
            cur = cand
    lines += [cur] if cur else []
    if len(lines) == 2:   # balance two lines: the split with the narrowest wider line
        best = None
        for k in range(1, len(words)):
            a_, b_ = ' '.join(words[:k]), ' '.join(words[k:])
            wa, wb = text_width(a_, size, font_file), text_width(b_, size, font_file)
            if max(wa, wb) <= max_w and (best is None or max(wa, wb) < best[0]):
                best = (max(wa, wb), [a_, b_])
        if best:
            lines = best[1]
    return lines


def ts(t):
    t = max(0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


import re as _re
_SLURS = _re.compile(r"\b(nigg\w*|n[i1]gg?a\w*|f[a@]gg?\w*|retard\w*|tranny\w*|chink\w*|spic\w*|kike\w*)\b", _re.I)


_SWEARS = _re.compile(r"\b(fuck\w*|motherfuck\w*|shit\w*|bitch\w*|cunt\w*|dick\w*|pussy\w*|asshole\w*|bastard\w*|whore\w*|slut\w*)\b", _re.I)


def _mask(m):
    w = m.group(0)
    return w[0] + '*' * (len(w) - 1)


def clean(s, profanity=False):
    """Slurs are always masked (n-word -> n****); hard swears too unless profanity=True."""
    s = _SLURS.sub(_mask, s)
    return s if profanity else _SWEARS.sub(_mask, s)


def cap_word(w):
    """A word as shown in the word-by-word captions: trailing commas, periods, ellipses and colons dropped
    ("YEAH," -> "YEAH"); ? and ! stay, they carry the tone. Numbers like "30,000" or "2.5" are untouched."""
    return _re.sub(r"[,.;:…]+$", "", w) or w


def esc(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(text, width):
    lines, cur = [], ''
    for wd in text.split():
        if cur and len(cur) + 1 + len(wd) > width:
            lines.append(cur); cur = wd
        else:
            cur = (cur + ' ' + wd).strip()
    return lines + ([cur] if cur else [])


def build_ass(words, s0, e0, hook, cap_y, title=None, caps='word', profanity=False, clear_at=()):
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{CAP_FONT},{CAP_SIZE},&H00FFFFFF,&H0000E5FF,&H00000000,&H90000000,0,0,0,0,100,100,1,0,1,10,5,5,70,70,0,1
Style: Hook,{CAP_FONT},88,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,18,0,8,70,70,170,1
Style: TitleBox,{TITLE_FONT},{TITLE_SIZE},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,1,0,0,5,40,40,0,1
Style: TitleBoxBG,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: TitleOut,{CAP_FONT},100,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,9,4,8,50,50,0,1
Style: Bubble,{TITLE_FONT},76,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,3,16,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    ws = [dict(w, w=clean(w["w"], profanity)) for w in words if s0 <= w["s"] < e0 and w["w"]]
    # punchy groups: 2 words (3 if they are tiny), break on punctuation and on pauses
    groups, cur = [], []
    for w in ws:
        chars = sum(len(x["w"]) for x in cur) + len(w["w"])
        full = len(cur) >= 3 or (len(cur) == 2 and chars > 11)
        if cur and (full or w["s"] - cur[-1]["e"] > 0.35 or cur[-1]["w"][-1:] in ".?!,"):
            groups.append(cur); cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    hook_end = 2.6 if hook else 0
    for gi, g in enumerate(groups):
        # stay up until the next group unless there is a real pause (no flicker between groups)
        nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
        g_end = nxt if nxt is not None and nxt - g[-1]["e"] < 0.5 else g[-1]["e"] + 0.25
        g_end = min(g_end, e0)
        for c in clear_at:   # captions never run into the rewind (times on the clip timeline)
            if g[0]["s"] - s0 < c < g_end - s0:
                g_end = s0 + c
        for k, w in enumerate(g):
            a = w["s"] - s0; b = (g[k + 1]["s"] - s0) if k + 1 < len(g) else g_end - s0
            if b <= a:
                continue
            parts = []
            for j, x in enumerate(g):
                t = esc(cap_word(x["w"]).upper())
                parts.append("{\\c&H0000E5FF&}" + t + "{\\c&H00FFFFFF&}" if j == k else t)
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if k == 0 else ""
            if caps == 'bubble':
                if k == 0:
                    txt = esc(' '.join(x['w'] for x in g)).strip()
                    txt = txt[:1].upper() + txt[1:]
                    ev.append(f"Dialogue: 0,{ts(a)},{ts(g_end - s0)},Bubble,,0,0,0,,{{\\pos({W // 2},{cap_y})}}{pop}{txt}")
                continue
            ev.append(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{{\\pos({W // 2},{cap_y})}}{pop}{' '.join(parts)}")
    if title:  # reaction title: (style, text, y, seconds)
        st, text, ty, secs = title
        end = (e0 - s0) if secs <= 0 else secs
        text = ''.join(ch for ch in text if ord(ch) < 0x2190 or 0x2E80 <= ord(ch) < 0xFE00).strip()  # libass has no colour emoji
        padx = 46
        lines = (wrap_px(esc(text), TITLE_SIZE, W - 60 - 2 * padx) if st == 'box'
                 else wrap_px(esc(text), 100, W - 100, "Montserrat-Black.ttf"))
        body = '\\N'.join(lines)
        if st == 'box':  # white rounded box sized to the measured text, black bold text
            lh, pady, r = round(TITLE_SIZE * 0.80), 26, 26
            bw = min(W - 60, int(max(text_width(l, TITLE_SIZE) for l in lines)) + 2 * padx); bh = len(lines) * lh + 2 * pady
            x0, y0 = (W - bw) // 2, ty - bh // 2
            shape = (f"m {r} 0 l {bw - r} 0 b {bw} 0 {bw} 0 {bw} {r} l {bw} {bh - r} b {bw} {bh} {bw} {bh} {bw - r} {bh} "
                     f"l {r} {bh} b 0 {bh} 0 {bh} 0 {bh - r} l 0 {r} b 0 0 0 0 {r} 0")
            ev.append(f"Dialogue: 2,{ts(0)},{ts(end)},TitleBoxBG,,0,0,0,,{{\\an7\\pos({x0},{y0})\\p1}}{shape}")
            ev.append(f"Dialogue: 3,{ts(0)},{ts(end)},TitleBox,,0,0,0,,{{\\an5\\pos({W // 2},{ty})}}{body}")
        else:
            ev.append(f"Dialogue: 2,{ts(0)},{ts(end)},TitleOut,,0,0,0,,{{\\pos({W // 2},{ty})\\an8}}{body}")
        hook = ''
    if hook:
        ev.append(f"Dialogue: 1,{ts(0)},{ts(hook_end)},Hook,,0,0,0,,{{\\fad(0,180)\\fscx92\\fscy92\\t(0,120,\\fscx100\\fscy100)}}{esc(hook)}")
    return hdr + "\n".join(ev) + "\n"


CAP_Y = {"fill": 1380, "blur": 1460, "facecam": 1120, "react": 1560, "split": 960, "track": 1470}
FOCUS_Y, FOCUS_H = 300, 1350   # track (Focus): the 1080x1350 window's top edge and height
LNAMES = {"track": "Focus", "blur": "Fit"}
TOP = 730  # react layout: face panel height = seam y (even)
GAME_H = H - TOP  # 1190


def even(n):
    return int(n) // 2 * 2


SHARP = "unsharp=5:5:0.8:5:5:0.0"   # light luma sharpening for upscaled webcams


def video_filter(layout, cam, ass, band=None, cam_side='right', src=None, game_x=None, face_expr=None, split=0.5,
                 fps=30, punch=0.0, flashes=(), focus=None):
    """Layout + burned-caption filter graph ending in [vo]. Reused by translate.py.
    fps: output frame rate. punch: zoom-in amount at the start (0.10 = starts 10% zoomed, settles in 0.4 s).
    flashes: [(t, dur)] short white flashes (tease -> clip transition)."""
    L = ":flags=lanczos"
    if layout == "fill":
        v = f"[0:v]scale=-2:{H}{L},crop={W}:{H}[v]"
    elif layout == "blur":
        v = (f"[0:v]split[a][b];[a]scale=-2:{H},crop={W}:{H},boxblur=24:2,eq=brightness=-0.08[bg];"
             f"[b]scale={W}:-2{L}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80[v]")
    elif layout == "track":  # Focus: a 4:5 window (focus = crop w, h, x, y in source px; x may move) over a blur
        cw, chh, fx, fy = focus
        up = FOCUS_H / max(chh, 1)
        sharp = f",{SHARP}" if up > 1.3 else ""
        v = (f"[0:v]split[a][b];[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
             f"eq=brightness=-0.10:saturation=1.1,scale={W}:{H},setsar=1[bg];"
             f"[b]crop={cw}:{chh}:'{fx}':'{fy}',scale={W}:{FOCUS_H}{L}{sharp},setsar=1[fg];[bg][fg]overlay=0:{FOCUS_Y}[v]")
    elif layout == "split":  # content on top, filler gameplay (input 1) below
        th = even(H * split); bh = H - th
        v = (f"[0:v]scale={W}:{th}:force_original_aspect_ratio=increase{L},crop={W}:{th},setsar=1,format=yuv420p[top];"
             f"[1:v]scale={W}:{bh}:force_original_aspect_ratio=increase{L},crop={W}:{bh},setsar=1,format=yuv420p[bot];"
             f"[top][bot]vstack[v]")
    elif layout == 'react':
        sw, sh = src
        if face_expr:  # fixed crop around the face (no clean cam frame)
            cw, chh, ex, ey = face_expr
            top = f"crop={cw}:{chh}:{ex}:{ey}"
        else:
            x, y, w, h = map(float, cam.split(':'))
            cw, chh = even(sw * w), even(sh * h)
            top = f"crop={cw}:{chh}:{even(sw * x)}:{even(sh * y)}"
        up = max(W / max(cw, 1), TOP / max(chh, 1))           # how much the webcam is enlarged
        sharp = f",{SHARP}" if up > 1.3 else ""
        gw = even(sw * GAME_H / sh) if sw * GAME_H / sh >= W else W
        gx = game_x if game_x is not None else ((gw - W) if cam_side == 'left' else 0)
        v = (f"[0:v]split[a][b];[a]{top},scale={W}:{TOP}:force_original_aspect_ratio=increase{L},crop={W}:{TOP}{sharp},setsar=1[cam];"
             f"[b]scale={gw}:{GAME_H}{L},crop={W}:{GAME_H}:{even(gx)}:0,setsar=1[game];[cam][game]vstack[v]")
        if band:
            y0, bh, secs = band
            en = f":enable='lte(t,{secs})'" if secs > 0 else ''
            v = v[:-3] + f"[v0];[v0]drawbox=x=0:y={y0}:w=iw:h={bh}:color=white@1:t=fill{en}[v]"
    else:
        x, y, w, h = map(float, cam.split(":"))
        v = (f"[0:v]split[a][b];[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{int(H * 0.38)}:force_original_aspect_ratio=increase{L},"
             f"crop={W}:{int(H * 0.38)},{SHARP}[cam];[b]scale=-2:{H - int(H * 0.38)}{L},crop={W}:{H - int(H * 0.38)}[game];[cam][game]vstack[v]")
    post = f"fps={fps}"
    if punch > 0:  # zoom from 1+punch to 1 over the first 0.4 s (ease-out); the frame moves from frame one.
        # Only the first 0.4 s go through the zoom (a per-frame scale, sub-pixel smooth, then a centre crop); the
        # rest of the clip passes untouched. (zoompan over the whole clip cost a full extra pass and jittered.)
        z = f"(1+{punch}*pow(1-t/0.4\\,2))"
        v += (f";[v]{post},split[pz0][pr0];[pz0]trim=end=0.4,setpts=PTS-STARTPTS,"
              f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame:flags=bicubic,crop={W}:{H},setsar=1[pz];"
              f"[pr0]trim=start=0.4,setpts=PTS-STARTPTS[pr];[pz][pr]concat=n=2:v=1:a=0[vp]")
        lab, post = "vp", "null"
    else:
        lab = "v"
    for t, dur in flashes:
        post += f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.7:t=fill:enable='between(t\\,{t:.3f}\\,{t + dur:.3f})'"
    sub = str(ass).replace("\\", "/").replace(":", "\\:")
    fdir = str(FONTS).replace("\\", "/").replace(":", "\\:")
    return v + f";[{lab}]{post},subtitles='{sub}':fontsdir='{fdir}'[vo]"


def game_offset(video, s0, e0, src, cam_box):
    """Pick the x offset of the 1080-wide game crop that avoids static overlays (viewer counters,
    alerts, chat boxes) and the webcam. Static = pixels that barely change across sampled frames
    but carry edges (text/graphics)."""
    try:
        import cv2, numpy as np
    except ImportError:
        return None
    sw, sh = src
    gw = even(sw * GAME_H / sh)
    if gw <= W:
        return 0
    cap = cv2.VideoCapture(str(video)); frames = []
    for k in range(12):
        cap.set(cv2.CAP_PROP_POS_MSEC, (s0 + (e0 - s0) * (k + 0.5) / 12) * 1000)
        ok, f = cap.read()
        if ok:
            frames.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (gw, GAME_H)).astype(np.float32))
    cap.release()
    if len(frames) < 4:
        return None
    st = np.stack(frames)
    std = st.std(0); med = np.median(st, 0)
    edges = cv2.Canny(med.astype(np.uint8), 60, 160) > 0
    bad = (std < 6) & cv2.dilate(edges.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    x, y, w, h = cam_box  # the cam itself also counts as overlay
    bad[int(y * GAME_H):int((y + h) * GAME_H), int(x * gw):int((x + w) * gw)] = True
    col = bad.sum(0).astype(np.float64); cs = np.concatenate([[0], np.cumsum(col)])
    best, bx = None, 0
    camcols = np.zeros(gw); camcols[int(x * gw):int((x + w) * gw)] = 1; cc = np.concatenate([[0], np.cumsum(camcols)])
    for ox in range(0, gw - W + 1, 8):
        # never show the cam twice; otherwise stay near the centre (game subtitles and action sit there),
        # moving off-centre only when that clearly removes a static overlay (counters, alerts, chat)
        if cc[ox + W] - cc[ox] > 0 and (cc[-1] < gw - W):
            continue
        cost = (cs[ox + W] - cs[ox]) * 0.2 + abs(ox - (gw - W) / 2) * GAME_H * 0.05
        if best is None or cost < best:
            best, bx = cost, ox
    return bx


def loudness(d, a, b, src=None, hop=0.05):
    """dB per 50 ms for [a, b] source seconds (None if no audio)."""
    try:
        import numpy as np
        from align import window_pcm
        x, _ = window_pcm(d, a, b, src)
        n = int(len(x) / (16000 * hop))
        if n < 2:
            return None
        k = int(16000 * hop)
        return 20 * np.log10(np.sqrt((x[: n * k].reshape(n, k) ** 2).mean(1)) + 1e-9)
    except Exception:
        return None


def keep_intervals(words, s0, e0, trim=True, tighten=0.45, db=None, db_start=0.0, hop=0.05, keep=()):
    """[(a, b)] source-second pieces to keep from [s0, e0]: trimmed to the speech, long pauses cut.
    A pause or tail is kept when it is loud (laughter, screams, explosions are not in the transcript),
    and the parts of a pause inside a `keep` range (a silent visual moment: a backflip, a reveal) are never cut."""
    import numpy as np
    ws = [w for w in words if s0 <= w["s"] < e0]
    if not ws:
        return [(s0, e0)]
    med = float(np.median(db)) if db is not None else 0.0

    def loud(a, b):
        if db is None:
            return False
        i, j = int((a - db_start) / hop), int((b - db_start) / hop)
        seg = db[max(0, i):max(i + 1, j)]
        return len(seg) > 0 and float(np.mean(seg)) > med + 4

    a, b = s0, e0
    if trim:
        a = max(s0, ws[0]["s"] - 0.12)
        b = min(e0, ws[-1]["e"] + 0.35)
        while b < min(e0, ws[-1]["e"] + 1.5) and loud(b, b + 0.1):   # let a laugh finish
            b += 0.1
    out, cur = [], a
    if tighten > 0:
        for w, nxt in zip(ws, ws[1:]):
            gap_a, gap_b = w["e"] + 0.15, nxt["s"] - 0.10
            if nxt["s"] - w["e"] > tighten and gap_b - gap_a >= 0.25 and gap_a > cur and gap_b < b and not loud(gap_a, gap_b):
                cuts = [(gap_a, gap_b)]
                for ka, kb in keep:   # take the kept ranges out of the cut
                    cuts = [p for x, y in cuts for p in ((x, min(y, ka)), (max(x, kb), y)) if p[1] - p[0] >= 0.25]
                for x, y in cuts:
                    out.append((cur, x)); cur = y
    out.append((cur, b))
    return [(round(x, 3), round(y, 3)) for x, y in out if y - x > 0.05]


def seg_len(sg):
    """Length on the edited timeline: (a, b) plays b-a seconds, (a, b, "rewind", dur) plays dur seconds."""
    return sg[3] if len(sg) > 2 else sg[1] - sg[0]


# hook -> clip transitions blended with ffmpeg xfade: name -> (xfade transition, seconds, whoosh sound)
TRANSITIONS = {
    "zoom": ("zoomin", 0.35, True),        # zooms into the clip
    "whip": ("hblur", 0.30, True),         # motion-blur swipe
    "slide": ("slideup", 0.30, True),      # the clip pushes up from below
    "fade": ("fadeblack", 0.45, False),    # quick dip to black
    "pixel": ("pixelize", 0.40, True),     # pixelates through
    "circle": ("circleopen", 0.40, False), # circle opens onto the clip
    "dissolve": ("dissolve", 0.35, False), # grainy dissolve
}


def build_edit(src, off, segs, out_file):
    """Concatenate source pieces (source seconds; the file starts at `off`) into one high-quality intermediate.
    Tiny audio fades at every cut so jump cuts do not click.
    A piece (a, b, "rewind", dur) plays a..b backwards, sped up to last `dur` seconds, with the reversed audio
    as a rewind sound: the "it restarts" signal between the tease and the clip."""
    from common import ffprobe
    vs = [x for x in ffprobe(src)["streams"] if x.get("codec_type") == "video"][0]
    num, den = (vs.get("r_frame_rate") or "30/1").split("/")
    fps = min(60.0, float(num) / max(1.0, float(den)))
    seek = max(0.0, min(sg[0] for sg in segs) - off - 1.0)
    parts, labels = [], []
    norm = "format=yuv420p,setsar=1"
    for i, sg in enumerate(segs):
        a, b = sg[0], sg[1]
        la, lb = a - off - seek, b - off - seek
        if len(sg) > 2:  # rewind
            dur = sg[3]
            n = max(8, int(round(dur * fps)))
            speed = (lb - la) / dur
            k = max(0, int(math.floor(math.log10(max(speed, 1.0)))))          # atempo: chain x10 steps, then the rest
            tempo = ",".join(["atempo=10"] * k + [f"atempo={max(0.5, speed / 10 ** k):.4f}"])
            parts.append(f"[0:v]trim=start={la:.3f}:end={lb:.3f},setpts=PTS-STARTPTS,fps={n / (lb - la):.4f},reverse,"
                         f"settb=AVTB,setpts=N/({fps:.3f}*TB),fps={fps:.3f},trim=end_frame={n},eq=saturation=0.75:contrast=1.08,{norm}[v{i}];"
                         f"[0:a]atrim=start={la:.3f}:end={lb:.3f},asetpts=PTS-STARTPTS,areverse,{tempo},"
                         f"apad,atrim=end={dur:.3f},volume=0.55,afade=t=in:d=0.03,afade=t=out:st={max(0.0, dur - 0.06):.3f}:d=0.06[a{i}]")
        else:
            dur = lb - la
            parts.append(f"[0:v]trim=start={la:.3f}:end={lb:.3f},setpts=PTS-STARTPTS,{norm}[v{i}];"
                         f"[0:a]atrim=start={la:.3f}:end={lb:.3f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.012,afade=t=out:st={max(0.0, dur - 0.015):.3f}:d=0.015[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=1[v][a]"
    run([tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{seek:.3f}", "-i", str(src), "-filter_complex", fc,
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "10", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", str(out_file)])
    return float(ffprobe(out_file)["format"]["duration"])


def remap_words(words, segs):
    """Words (source seconds) -> times on the edited timeline; words in cut-out pauses are dropped."""
    out, o = [], 0.0
    for sg in segs:
        a, b = sg[0], sg[1]
        if len(sg) == 2:  # no captions over the rewind
            for w in words:
                if a - 0.05 <= w["s"] < b:
                    out.append(dict(w, s=round(max(0.0, w["s"] - a) + o, 3), e=round(min(w["e"], b) - a + o, 3)))
        o += seg_len(sg)
    return out


AUDIO_PRE = "highpass=f=60,acompressor=threshold=-21dB:ratio=2.5:attack=8:release=180:makeup=1"
LOUD_TARGET = "I=-14:TP=-1.5:LRA=20"


def finish_audio(master, mp4):
    """Two-pass loudness: measure the finished mix, then apply loudnorm in linear mode (one fixed gain, so no pumping)
    while copying the video. The master (.mov: video + PCM audio) is removed afterwards."""
    import json as _json, re as _r, subprocess as _sp
    has_audio = '"audio"' in _json.dumps(__import__("common").ffprobe(master))
    af = []
    if has_audio:
        r = _sp.run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(master), "-vn", "-af",
                     f"loudnorm={LOUD_TARGET}:print_format=json", "-f", "null", "-"], capture_output=True, text=True)
        m = _r.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
        try:
            j = _json.loads(m.group(0))
            if float(j["input_i"]) < -60:   # silence: nothing to normalise
                raise ValueError
            af = ["-af", f"loudnorm={LOUD_TARGET}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
                         f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:"
                         f"linear=true,aresample=48000"]
        except (AttributeError, ValueError, KeyError):
            af = ["-af", f"loudnorm={LOUD_TARGET},aresample=48000"]
    run([tool("ffmpeg"), "-y", "-v", "error", "-i", str(master), "-map", "0:v", *(["-map", "0:a"] + af + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if has_audio else []),
         "-c:v", "copy", "-movflags", "+faststart", str(mp4)])
    Path(master).unlink(missing_ok=True)


T0 = __import__("time").time()


def stage(name, extra=""):
    """A progress marker for the Picks page (it reads the render log): stage name and seconds since start."""
    print(f"stage: {name} t={__import__('time').time() - T0:.1f}{' ' + extra if extra else ''}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job"); ap.add_argument("--pick", type=int, default=1)
    ap.add_argument("--layout", choices=["fill", "blur", "facecam", "react", "split", "track"], default="blur",
                    help="track = Focus: a big 4:5 window aimed at the face or the action, over a blur (focus.py)")
    ap.add_argument("--fallback", choices=["track", "blur"], default="track",
                    help="react/facecam without a usable webcam (with --or-blur): Focus (default) or the old Fit")
    ap.add_argument("--focus", choices=["auto", "motion"], default="auto",
                    help="track: auto (face when there's no game on screen, else follow the action) or always motion")
    ap.add_argument("--force", action="store_true", help="react: skip the 'is there gameplay outside the cam' check")
    ap.add_argument("--set", default="candidates", help="candidates (shorts) or candidates_long (pipeline.py)")
    ap.add_argument("--filler", default="random", help="split: video path, name in fillers/ (gta_ramp, subway_vertical...) or random")
    ap.add_argument("--split", type=float, default=0.5, help="split: share of the height for the content")
    ap.add_argument("--filler-start", type=float, help="split: second of the filler video to start at (default random)")
    ap.add_argument("--name", help="output file name without extension (default clip_<pick> / long_<pick>)")
    ap.add_argument("--title", default="", help="reaction title, e.g. 'Speed REALIZED why GTA 6 took 13 YEARS'")
    ap.add_argument("--title-style", choices=["box", "outline"], default="box")
    ap.add_argument("--title-secs", type=float, default=0, help="0 = whole clip")
    ap.add_argument("--captions", choices=["word", "bubble", "none"], default="word")
    ap.add_argument("--hook", default="")
    ap.add_argument("--or-blur", action="store_true",
                    help="react: when there's no usable streamer face, render the --fallback layout (Focus by default) in "
                         "this same run instead of exiting (the title becomes the hook box) - saves running cut.py twice")
    ap.add_argument("--overlay", metavar="PNG:POS:WIDTH",
                    help="a campaign's logo or overlay on the whole clip: POS full|top-left|top-right|bottom-left|bottom-right|"
                         "top|bottom|center, WIDTH share of the video width (full ignores it)")
    ap.add_argument("--campaign", help="the paid campaign this clip is made for (saved in the .json; campaigns.py)")
    ap.add_argument("--allow-profanity", action="store_true", help="keep swear words in captions (slurs are always masked)")
    ap.add_argument("--cam", default="auto", help="auto (find the webcam) or x:y:w:h fractions")
    ap.add_argument("--start", type=float); ap.add_argument("--end", type=float)
    ap.add_argument("--tease", help="S:E source seconds to play first (default: the pick's tease)")
    ap.add_argument("--no-tease", action="store_true")
    ap.add_argument("--transition", choices=[*TRANSITIONS, "rewind", "flash", "cut"], default="zoom",
                    help="between the hook and the clip: " + ", ".join(TRANSITIONS) +
                         " (blended, with a whoosh on zoom/whip/slide/pixel), rewind (fast reverse + rewind sound), "
                         "flash (white flash) or cut (hard cut)")
    ap.add_argument("--after-tease", type=float, default=0.8,
                    help="when the tease is inside the clip, end this many seconds after it (the clip builds up to the hook)")
    ap.add_argument("--keep-after-tease", action="store_true", help="keep playing after the tease moment")
    ap.add_argument("--tighten", type=float, default=0.45, help="cut speech pauses longer than this (s); 0 = keep pauses")
    ap.add_argument("--keep", action="append", default=[], metavar="S:E",
                    help="source seconds never cut as a pause (a silent visual moment); repeatable")
    ap.add_argument("--drop", action="append", default=[], metavar="S:E",
                    help="source seconds to cut out (a repeated line, a chat read); repeatable")
    ap.add_argument("--no-trim", action="store_true", help="keep the exact start/end instead of snapping to the speech")
    ap.add_argument("--no-punch", action="store_true", help="no zoom-in on the first frames")
    ap.add_argument("--fps", default="auto", help="output frame rate: auto (source, max 60) or a number")
    ap.add_argument("--fast-words", action="store_true", help="use the pipeline transcript instead of re-transcribing + aligning")
    a = ap.parse_args()
    d = Path(a.job); c = load_json(d / f"{a.set}.json")
    pick = next(x for x in c["candidates"] if x["n"] == a.pick)
    s0 = a.start if a.start is not None else pick["start"]; e0 = a.end if a.end is not None else pick["end"]
    # pipeline.py long sources: only a section around each pick is downloaded (file + offset in the source)
    SRC = pick.get("file") or c["source"]; OFF = pick.get("offset", 0) if pick.get("file") else 0
    SRC0, OFF0 = SRC, OFF   # the downloaded video, kept for the metadata (the editor reopens it)
    if s0 - OFF < -0.05:
        sys.exit(f"start {s0} is before the downloaded section ({OFF}s)")
    base = a.name or (f"long_{a.pick}" if a.set != "candidates" else f"clip_{a.pick}")
    out = d / "edits"; out.mkdir(exist_ok=True)
    info = c.get("info", {})
    from common import ffprobe
    sdur = float(ffprobe(SRC)["format"]["duration"])

    # ---- tease: a 1-3 s moment (usually the payoff line) shown before the clip
    tease = None
    if not a.no_tease:
        t = a.tease or pick.get("tease")
        if isinstance(t, str):
            t = [float(v) for v in t.split(":")]
        if t:
            t = (float(t[0]), float(t[1]))
            if t[0] - OFF < -0.05 or t[1] - OFF > sdur + 0.05 or not 0.4 <= t[1] - t[0] <= 4:
                sys.exit(f"tease {t} must be 0.4-4 s inside the downloaded video ({OFF:.1f}-{OFF + sdur:.1f} s)")
            if t[1] - t[0] > 2.5:
                print(f"note: tease is {t[1] - t[0]:.1f}s; 1-2 s hooks hit harder")
            tease = t

    # ---- words: re-transcribed + force-aligned for this clip (cached), or the pipeline transcript
    stage("words")
    pipe_words = load_json(d / "transcript.json") if (d / "transcript.json").exists() else []
    if a.fast_words:
        words = pipe_words
    else:
        from align import clip_words
        speaker = info.get("channel") or info.get("uploader") or ""
        wsrc = SRC if OFF == 0 else None
        words = clip_words(d, s0, e0, src=wsrc, fallback=pipe_words, speaker=speaker)
        if tease and not (s0 <= tease[0] and tease[1] <= e0):
            words = sorted(words + clip_words(d, *tease, src=wsrc, fallback=pipe_words, speaker=speaker), key=lambda w: w["s"])

    # ---- edit list: trim to the speech, cut dead air, tease first
    stage("edit")
    # The tease is the culminating moment: shown first, then the clip restarts and builds up to it, ending right after.
    if tease and s0 <= tease[0] < e0 and not a.keep_after_tease:
        e0 = min(e0, tease[1] + a.after_tease)
        nxt = next((w["s"] for w in words if w["s"] > tease[1] + 0.1), None)   # never start the next sentence
        if nxt is not None and nxt < e0:
            e0 = max(tease[1] + 0.3, nxt - 0.05)
    elif tease and not s0 <= tease[0] < e0:
        print(f"note: the tease ({tease[0]:.1f}s) is outside the clip ({s0:.1f}-{e0:.1f}s), so the clip will not build up to it")
    db = loudness(d, s0, e0 + 1.6, SRC if OFF == 0 else None)
    keeps = [tuple(float(v) for v in k.split(":")) for k in a.keep]
    segs = keep_intervals(words, s0, e0, trim=not a.no_trim, tighten=a.tighten, db=db, db_start=s0, keep=keeps)
    for da, db_ in [tuple(float(v) for v in k.split(":")) for k in a.drop]:   # cut a repeated line / dead bit
        segs = [p for x, y in segs for p in ((x, min(y, da)), (max(x, db_), y)) if p[1] - p[0] > 0.05]
    if tease and not a.keep_after_tease and s0 <= tease[0] < e0:   # trimming must not cut the payoff short
        segs[-1] = (segs[-1][0], max(segs[-1][1], min(e0, tease[1] + 0.3)))
    flashes = []
    if tease:
        lead = [tease]
        if a.transition == "rewind":   # play the clip so far backwards, fast: "here is how we got there"
            rw = (segs[0][0], min(tease[1], segs[-1][1]), "rewind", 0.6)
            lead.append(rw)
            flashes = [(tease[1] - tease[0], 0.05)]
        elif a.transition == "flash":
            flashes = [(tease[1] - tease[0], 0.07)]
        segs = lead + segs
    xf = None   # blended transition: never longer than half the hook or half the clip
    if tease and a.transition in TRANSITIONS and len(segs) > 1:
        kind, d, whoosh = TRANSITIONS[a.transition]
        d = round(min(d, seg_len(segs[0]) * 0.5, sum(seg_len(sg) for sg in segs[1:]) * 0.5), 3)
        xf = (kind, d, whoosh)
    kept = sum(seg_len(sg) for sg in segs) - (xf[1] if xf else 0)
    print(f"edit: {len(segs)} piece(s), {e0 - s0:.1f}s -> {kept:.1f}s" + (f", tease {tease[0]:.1f}-{tease[1]:.1f}" if tease else "")
          + (f" + {a.transition}" if tease else ""))
    if len(segs) == 1:
        s0, e0 = segs[0]
        L0, L1 = max(0.0, s0 - OFF), e0 - OFF
    else:
        inter = out / f".{base}.edit.mkv"
        total = build_edit(SRC, OFF, segs, inter)
        words = remap_words(words, segs)   # captions are burned before the blend, on the un-overlapped timeline
        SRC, OFF, s0, e0, L0, L1 = str(inter), 0.0, 0.0, total, 0.0, total
    layout, cam, side = a.layout, a.cam, 'right'
    face_expr = None
    stage("layout")
    focus_cam, focus_gp = None, True   # what the Focus layout aims at (see focus.py)
    fb = 'blur' if a.fallback == 'blur' else 'track'
    if layout in ('react', 'facecam') and cam == 'auto':
        from facecam import find_cam
        r = find_cam(SRC, L0, L1)
        if layout == 'react' and r.get('mode') not in ('cam', 'face'):
            if not a.or_blur:
                sys.exit(f"skipped: no streamer face over gameplay in this moment ({r.get('mode')}: {r.get('reason', '')}). "
                         "React clips need the game on screen with the cam as a separate box. Pick another moment or stream.")
            print(f"react: no streamer face over gameplay ({r.get('reason', '')}), using the {LNAMES[fb]} layout")
            focus_cam = r
            layout, a.layout, a.hook = fb, fb, a.hook or a.title
        if layout == 'react':
            from facecam import has_gameplay, face_crop_expr
            if not a.force and not has_gameplay(SRC, L0, L1, r['box']):
                if not a.or_blur:
                    sys.exit("skipped: the area outside the webcam is not gameplay (same scene or no game on screen). "
                             "Pick a moment where GTA 6 is on screen.")
                print(f"react: the area outside the webcam is not gameplay, using the {LNAMES[fb]} layout")
                focus_cam, focus_gp = r, False
                layout, a.layout, a.hook = fb, fb, a.hook or a.title
            elif r.get('mode') == 'face' and r.get('face_track'):
                from common import ffprobe as _fp
                _vs = [x for x in _fp(SRC)['streams'] if x.get('codec_type') == 'video'][0]
                from facecam import inner_edges
                sw_, sh_ = int(_vs['width']), int(_vs['height'])
                bnd = inner_edges(SRC, L0, L1, r['face'], sw_, sh_)
                print(f'facecam: cam borders L{bnd[0]} T{bnd[1]} R{bnd[2]} B{bnd[3]} px')
                fe = face_crop_expr(r['face_track'], sw_, sh_, W, TOP, bounds=bnd)
                face_expr = (fe['w'], fe['h'], fe['x'], fe['y'])
                print(f"facecam: no clean cam frame, still crop around the face, {len(fe['shots'])} shot(s) "
                      f"cut at {[round(sh[0], 1) for sh in fe['shots'][1:]]} s ({r.get('summary', '')})")
        if layout in ('react', 'facecam'):
            if r.get('mode') in ('cam', 'face'):
                bx = list(r['box'])
                if r['mode'] == 'face':  # no webcam frame: widen the face crop so it is not a blurry close-up
                    cx, cy = bx[0] + bx[2] / 2, bx[1] + bx[3] / 2
                    bw, bh = min(1.0, bx[2] * 2.6), min(1.0, bx[3] * 2.2)
                    bx = [min(max(0.0, cx - bw / 2), 1 - bw), min(max(0.0, cy - bh / 2), 1 - bh), bw, bh]
                cam = ':'.join(f'{v:.4f}' for v in bx)
                side = 'left' if r['box'][0] + r['box'][2] / 2 < 0.5 else 'right'
                print(f"facecam: {r.get('mode')} box {cam} ({r.get('summary', '')})")
            else:
                print(f"facecam: no stable face found ({r.get('reason', '')}), using the {LNAMES[fb]} layout")
                focus_cam, layout = r, fb
    focus = None
    if layout == 'track':
        from common import ffprobe as _fp
        from facecam import find_cam, has_gameplay
        _vs = [x for x in _fp(SRC)['streams'] if x.get('codec_type') == 'video'][0]
        fsw, fsh = int(_vs['width']), int(_vs['height'])
        if focus_cam is None:   # chosen directly: look for a face first
            focus_cam = find_cam(SRC, L0, L1)
            if focus_cam.get('box'):
                focus_gp = has_gameplay(SRC, L0, L1, focus_cam['box'])
        elif focus_gp and focus_cam.get('mode') == 'none' and focus_cam.get('face_track'):
            focus_gp = False   # a full-frame face (IRL / talking head): frame the person
        import focus as _focus
        focus = _focus.choose(SRC, L0, L1, fsw, fsh, cam=focus_cam, gameplay=focus_gp, ffmpeg=tool("ffmpeg"),
                              force_motion=a.focus == 'motion')
        print(f"focus: {focus['mode']}: {focus['note']}")
    elif layout in ('react', 'facecam') and cam != 'auto':
        side = 'left' if float(cam.split(':')[0]) + float(cam.split(':')[2]) / 2 < 0.5 else 'right'
    cap_y = CAP_Y[layout]
    title, band = None, None
    if a.title and layout == 'react':
        n = len(wrap(a.title, 26 if a.title_style == 'box' else 24))
        if a.title_style == 'box':
            title = ('box', a.title, TOP, a.title_secs)
        else:
            title = ('outline', a.title, TOP + 40, a.title_secs)
    if a.title and layout == 'split':
        title = ('outline', a.title, 70, a.title_secs)
    if a.title and layout in TITLE_Y:   # Gameplay: on the seam under the webcam; Fill/Fit: near the top, clear of the captions
        ty = TITLE_Y[layout]
        title = ('box', a.title, ty, a.title_secs) if a.title_style == 'box' else ('outline', a.title, ty + 40, a.title_secs)
    ass = out / f"{base}.ass"
    caps = a.captions
    cap_words = words if caps != 'none' else []
    clears = [tease[1] - tease[0] - (xf[1] if xf else 0)] if tease else []   # hook captions end when the transition starts
    ass.write_text(build_ass(cap_words, s0, e0, a.hook, cap_y, title=title, caps=caps, profanity=a.allow_profanity,
                             clear_at=clears), encoding="utf-8")
    src = None; gx = None
    if layout == 'react':
        from common import ffprobe
        vs = [x for x in ffprobe(SRC)['streams'] if x.get('codec_type') == 'video'][0]
        src = (int(vs['width']), int(vs['height']))
        gx = game_offset(SRC, L0, L1, src, [float(t) for t in cam.split(':')])
    if a.fps == "auto":
        vs0 = [x for x in ffprobe(SRC)['streams'] if x.get('codec_type') == 'video'][0]
        num, den = (vs0.get('r_frame_rate') or '30/1').split('/')
        fps = min(60, max(24, round(float(num) / max(1.0, float(den)))))
    else:
        fps = int(float(a.fps))
    v = video_filter(layout, cam, ass, band=band, cam_side=side, src=src, game_x=gx, face_expr=face_expr, split=a.split,
                     fps=fps, punch=0 if a.no_punch else 0.10, flashes=flashes,
                     focus=focus and (focus["w"], focus["h"], focus["x"], focus["y"]))
    mp4 = out / f"{base}.mp4"
    extra, filler = [], None
    if layout == "split":
        import random
        from common import ROOT
        fdir = ROOT / "fillers"
        if a.filler == "random":
            filler = random.choice(sorted(fdir.glob("*.mp4")))
        else:
            filler = Path(a.filler) if Path(a.filler).exists() else fdir / f"{a.filler}.mp4"
        from common import ffprobe as _probe
        fd = float(_probe(filler)["format"]["duration"])
        fs = (a.filler_start % fd) if a.filler_start is not None else random.uniform(0, max(0.0, fd - (e0 - s0) - 1))
        extra = ["-stream_loop", "-1", "-ss", f"{fs:.2f}", "-i", str(filler)]
    # audio: gentle compression here (voice and game audio sit closer together), then finish_audio() measures the
    # finished mix and normalises it to -14 LUFS in linear mode (two-pass: no pumping, unlike one-pass loudnorm)
    LOUD = AUDIO_PRE
    amap, aopt = ["-map", "0:a?"], ["-af", LOUD]
    if xf:   # blend the finished hook into the finished clip (after the layout, so panels never mix)
        kind, d, whoosh = xf
        t0 = seg_len(segs[0])
        st = "aformat=sample_rates=48000:channel_layouts=stereo"
        v = (v[:v.rindex("[vo]")] + "[vfull];" +
             f"[vfull]split[x1][x2];[x1]trim=end={t0:.3f},setpts=PTS-STARTPTS,fps={fps},settb=AVTB[xh];"
             f"[x2]trim=start={t0:.3f},setpts=PTS-STARTPTS,fps={fps},settb=AVTB[xr];"
             f"[xh][xr]xfade=transition={kind}:duration={d:.3f}:offset={t0 - d:.3f},format=yuv420p[vo];"
             f"[0:a]asplit[a1][a2];[a1]atrim=end={t0:.3f},asetpts=PTS-STARTPTS,{st}[ah];"
             f"[a2]atrim=start={t0:.3f},asetpts=PTS-STARTPTS,{st}[ar];[ah][ar]acrossfade=d={d:.3f}:c1=tri:c2=tri[ax]")
        if whoosh:   # short filtered-noise whoosh peaking at the transition
            wd, ws = d + 0.3, max(0.0, t0 - d - 0.15)
            v += (f";anoisesrc=d={wd:.3f}:c=pink:a=0.6:r=48000,highpass=f=500,lowpass=f=4500,{st},"
                  f"afade=t=in:d={wd * 0.55:.3f}:curve=exp,afade=t=out:st={wd * 0.55:.3f}:d={wd * 0.45:.3f},volume=0.28,"
                  f"adelay={int(ws * 1000)}:all=1[wh];[ax][wh]amix=inputs=2:duration=first:normalize=0[am]")
        v += f";[{'am' if whoosh else 'ax'}]{LOUD}[ao]"
        amap, aopt = ["-map", "[ao]"], []
    if a.overlay:   # a campaign's logo / overlay over everything (hook, transition and clip)
        opath, opos, owidth = a.overlay.rsplit(":", 2)
        idx = 1 + (1 if extra else 0)
        extra = extra + ["-loop", "1", "-i", opath]
        if opos == "full":
            lg = f"scale={W}:{H}"
            xy = "0:0"
        else:
            lw = even(W * min(1.0, max(0.05, float(owidth))))
            lg = f"scale={lw}:-1"
            m = 40
            xs = {"left": f"{m}", "right": f"main_w-overlay_w-{m}", "mid": "(main_w-overlay_w)/2"}
            ys = {"top": "60", "bottom": "main_h-overlay_h-480", "mid": "(main_h-overlay_h)/2"}
            vert, horiz = (opos.split("-") + ["mid"])[:2] if "-" in opos else ((opos, "mid") if opos in ("top", "bottom") else ("mid", "mid"))
            xy = f"{xs[horiz]}:{ys[vert]}"
        i = v.rindex("[vo]")   # rename the finished picture in place (the audio chain may follow it)
        v = (v[:i] + "[vpre]" + v[i + 4:] + f";[{idx}:v]format=rgba,{lg}[lg];"
             f"[vpre][lg]overlay={xy}:shortest=1,format=yuv420p[vo]")
        print(f"overlay: {Path(opath).name} {opos}")
    tmp_mov = out / f".{base}.master.mov"   # video + uncompressed audio; finish_audio() makes the mp4 from it
    stage("encode", f"dur={e0 - s0:.2f}")
    # -progress: out_time lines on stdout, so the render log shows how far the encode is
    run([tool("ffmpeg"), "-y", "-v", "error", "-progress", "pipe:1", "-stats_period", "1", "-ss", f"{L0:.2f}", "-to", f"{L1:.2f}", "-i", SRC, *extra,
         "-t", f"{e0 - s0:.2f}", "-filter_complex", v, "-map", "[vo]", *amap, *aopt,
         # high quality on purpose: every platform re-compresses the upload, a clean master survives that better
         "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-maxrate", "16M", "-bufsize", "32M",
         "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ar", "48000", str(tmp_mov)])
    stage("audio")
    finish_audio(tmp_mov, mp4)
    if SRC.endswith(".edit.mkv"):
        Path(SRC).unlink(missing_ok=True)
    main_segs = [sg for sg in segs if len(sg) == 2][1 if tease else 0:]
    save_json(out / f"{base}.json", {"file": str(mp4), "start": main_segs[0][0], "end": main_segs[-1][1], "segments": segs,
                                     "tease": tease, "fps": fps,
                                     "transition": {"name": a.transition if tease else None, "overlap": xf[1] if xf else 0.0}, "layout": layout, "filler": filler and filler.name, "filler_start": round(fs, 2) if filler else None,
                                     "hook": a.hook or a.title, "title": a.title, "cam": cam,
                                     "campaign": a.campaign, "overlay": a.overlay,
                                     "source_url": info.get("webpage_url"), "source_title": info.get("title"),
                                     "source_channel": info.get("channel") or info.get("uploader"),
                                     # everything needed to reopen and re-render this clip in the editor
                                     "args": vars(a), "video": str(SRC0), "video_offset": OFF0, "game_x": gx,
                                     "focus": focus and {k: focus[k] for k in ("mode", "w", "h", "x", "y", "path") if k in focus}})
    stage("done")
    print(mp4)


if __name__ == "__main__":
    main()
