"""Readable timestamped transcript for picking clips by hand/LLM.
One line per phrase (break on pauses > 0.9 s or 22 words), ▲ = loudness spike in that line.
usage: python compact.py jobs/<id> [--out file]"""
import argparse, sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from common import load_json

ap = argparse.ArgumentParser(); ap.add_argument("job"); ap.add_argument("--out"); a = ap.parse_args()
d = Path(a.job)
words = load_json(d / "transcript.json")
with wave.open(str(d / "audio.wav")) as w:
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
n = len(x) // 16000
db = 20 * np.log10(np.sqrt((x[: n * 16000].reshape(n, 16000) ** 2).mean(1) + 1e-9))
base = np.convolve(db, np.ones(31) / 31, mode="same")
spike = (db - base) > max(6.0, (db - base).std() * 2.2)
lines, cur = [], []
for w in words:
    if cur and (w["s"] - cur[-1]["e"] > 0.9 or len(cur) >= 22):
        lines.append(cur); cur = []
    cur.append(w)
if cur: lines.append(cur)
out = []
for l in lines:
    s, e = l[0]["s"], l[-1]["e"]
    hot = spike[int(s):int(e) + 1].any()
    out.append(f"[{int(s // 60)}:{int(s % 60):02d}]{'▲' if hot else ' '} " + " ".join(w["w"] for w in l))
info = load_json(d / "info.json")
txt = f"# {info.get('channel')} - {info.get('title')}\n" + "\n".join(out) + "\n"
Path(a.out or d / "compact.txt").write_text(txt, encoding="utf-8")
print(f"{d.name}: {len(out)} lines, {len(txt) // 1000}k chars")
