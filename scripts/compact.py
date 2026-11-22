"""Readable timestamped transcript for picking clips by hand/LLM.

One line per phrase: [start-end] in source seconds (exact, so picks and teases can be placed to the word), then
markers, then the words. Lines break on pauses > 0.7 s, after a sentence once it has 8+ words, or at 14 words.
Markers: ▲ loudness spike (screams, laughs, explosions)  ◆ live-chat spike  ★ YouTube "most replayed".
Gaps of 3+ s without speech are shown as their own line ("... 6.2 s no speech ▲"): silent visual moments live there.
usage: python compact.py jobs/<id> [--out file]"""
import argparse, sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from common import load_json

ap = argparse.ArgumentParser(); ap.add_argument("job"); ap.add_argument("--out"); a = ap.parse_args()
d = Path(a.job)
words = load_json(d / "transcript.json")
words = words if isinstance(words, list) else words.get("words", [])
with wave.open(str(d / "audio.wav")) as w:
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
n = len(x) // 16000
db = 20 * np.log10(np.sqrt((x[: n * 16000].reshape(n, 16000) ** 2).mean(1) + 1e-9))
base = np.convolve(db, np.ones(31) / 31, mode="same")
spike = (db - base) > max(6.0, (db - base).std() * 2.2)
info = load_json(d / "info.json")

# chat spikes (YouTube chat replay / TwitchDownloader json next to the job) and the most-replayed heatmap
chat_hot = np.zeros(n, bool)
try:
    import moments
    c = moments.chat_series(d, n, None, info)
    if c.any():
        cs = np.convolve(c, np.ones(10) / 10, mode="same")
        chat_hot = cs > np.median(cs) + 2.5 * cs.std()
except Exception:
    pass
heat = np.zeros(n)
for h in info.get("heatmap") or []:
    heat[int(h["start_time"]):int(np.ceil(h["end_time"]))] = h["value"]
heat_hot = heat > max(0.6, np.percentile(heat, 95)) if heat.any() else np.zeros(n, bool)


def marks(s, e):
    i, j = max(0, int(s)), min(n, int(e) + 1)
    return "".join(m for m, arr in (("▲", spike), ("◆", chat_hot), ("★", heat_hot)) if arr[i:j].any())


lines, cur = [], []
for w in words:
    if cur:
        gap = w["s"] - cur[-1]["e"]
        sentence_end = cur[-1]["w"][-1:] in ".?!" and len(cur) >= 8
        if gap > 0.7 or sentence_end or len(cur) >= 14:
            lines.append(cur); cur = []
    cur.append(w)
if cur:
    lines.append(cur)
out, prev_e = [], 0.0
for l in lines:
    s, e = l[0]["s"], l[-1]["e"]
    if s - prev_e >= 3.0:
        out.append(f"[{prev_e:.1f}-{s:.1f}] {marks(prev_e, s)} ... {s - prev_e:.1f} s no speech")
    m = marks(s, e)
    out.append(f"[{s:.1f}-{e:.1f}] {m + ' ' if m else ''}" + " ".join(w["w"] for w in l))
    prev_e = e
legend = ("# times: source seconds [start-end]. ▲ loudness spike  ◆ chat spike  ★ most replayed. "
          "Lines '... no speech' are gaps where only the picture talks.")
txt = f"# {info.get('channel')} - {info.get('title')}\n{legend}\n" + "\n".join(out) + "\n"
Path(a.out or d / "compact.txt").write_text(txt, encoding="utf-8")
print(f"{d.name}: {len(out)} lines, {len(txt) // 1000}k chars")
