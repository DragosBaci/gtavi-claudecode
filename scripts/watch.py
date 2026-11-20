"""Watch several live streams at once and clip the moments their chat goes crazy.

Per stream, all in parallel:
  video  a rolling buffer: ffmpeg writes ~--chunk second pieces into a fixed set of files and overwrites the
         oldest (keeps --buffer seconds, e.g. 30 x 2 s). Nothing piles up on disk.
         Note: without re-encoding, pieces can only split on keyframes (Twitch/YouTube: every ~2 s), so a
         smaller --chunk is rounded up to the keyframe interval.
  chat   Twitch IRC (anonymous) or YouTube live chat, counted in 0.5 s bins. Hype messages (LMAO, W, KEKW, 💀...)
         count double; giveaway/raid/link spam is ignored.
  spike  the last 4 s of chat is --ratio times the stream's normal rate (median of the last 3 min), with at
         least --min-msgs messages.
  delay  chat reacts late: the moment is placed --reaction seconds before chat *started* climbing, then moved to
         the loudest second of audio just before (the scream / the reveal).
  clip   --before seconds of build-up and --after seconds after the moment, copied out of the buffer as soon as
         they are recorded. Each clip becomes its own job (jobs/live-<stream>-<time>/) with a transcript and a pick
         whose hook is the flagged moment, so it shows up in the editor as "New from pick" and cut.py can make the
         hook-first edit.

usage: python watch.py https://twitch.tv/<login> [more URLs] [--top 3] [--minutes 120]
                       [--buffer 60 --chunk 2 --before 18 --after 6 --reaction 2.5 --ratio 2.5 --min-msgs 10]
Summary of every flagged moment: jobs/live_watch.md. Stop with Ctrl+C.
"""
import argparse, json, queue, re, shutil, statistics, subprocess, sys, threading, time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, run, save_json, tool, ytdlp_base
from live import HYPE, NOISE, STOP, WORD, extract_text, twitch_login
import twitch

BIN = 0.5
JOBS = ROOT / "jobs"
LOG_LOCK = threading.Lock()


def log(tag, msg):
    with LOG_LOCK:
        print(f"{time.strftime('%H:%M:%S')} [{tag[:14]:<14}] {msg}", flush=True)


# ------------------------------------------------------------------ chat

class Feed:
    """Chat messages in 0.5 s bins (keyed by int(wall_time / 0.5))."""

    def __init__(self, start):
        self.start, self.raw, self.wgt, self.text = start, Counter(), Counter(), defaultdict(list)
        self.lock = threading.Lock()

    def add(self, ts, txt):
        txt = (txt or "").strip()
        if ts < self.start - 5 or not txt or NOISE.search(txt):
            return
        b = int(ts / BIN)
        with self.lock:
            self.raw[b] += 1
            self.wgt[b] += 2 if HYPE.search(txt) else 1
            self.text[b].append(txt)

    def sum(self, c, b0, b1):
        with self.lock:
            return sum(c.get(b, 0) for b in range(b0, b1))

    def top_words(self, b0, b1, n=4):
        cnt = Counter()
        with self.lock:
            for b in range(b0, b1):
                for t in self.text.get(b, []):
                    cnt.update(w.lower() for w in WORD.findall(t) if w.lower() not in STOP)
        return [w for w, _ in cnt.most_common(n)]


def tail_lines(path, state):
    """New complete lines appended to `path` since the last call."""
    try:
        with open(path, "rb") as fh:
            fh.seek(state.get("pos", 0)); data = fh.read()
    except OSError:
        return []
    if not data:
        return []
    state["pos"] = state.get("pos", 0) + len(data)
    buf = state.get("buf", b"") + data
    *lines, state["buf"] = buf.split(b"\n")
    return lines


class TwitchChat:
    def __init__(self, login, d, feed):
        self.f, self.feed, self.state, self.stop_ev = d / "chat.twitch.jsonl", feed, {}, threading.Event()
        self.f.unlink(missing_ok=True)
        threading.Thread(target=twitch.irc_chat, args=(login, self.f, self.stop_ev), daemon=True).start()

    def poll(self):
        for ln in tail_lines(self.f, self.state):
            try:
                m = json.loads(ln); self.feed.add(float(m["t"]), m.get("text"))
            except Exception:
                pass

    def stop(self):
        self.stop_ev.set()


class YouTubeChat:
    def __init__(self, url, d, feed):
        self.d, self.feed, self.states, self.seen = d, feed, defaultdict(dict), set()
        for f in d.glob("chat.live_chat.json*"):
            f.unlink(missing_ok=True)
        self.proc = subprocess.Popen(ytdlp_base() + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(d / "chat"), url],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def poll(self):
        for f in sorted(self.d.glob("chat.live_chat.json*")):
            for ln in tail_lines(f, self.states[f.name]):
                try:
                    acts = json.loads(ln)["replayChatItemAction"]["actions"]
                except Exception:
                    continue
                for a in acts:
                    r = (((a.get("addChatItemAction") or {}).get("item")) or {}).get("liveChatTextMessageRenderer")
                    if not r or r.get("id") in self.seen:
                        continue
                    self.seen.add(r.get("id"))
                    try:
                        ts = int(r["timestampUsec"]) / 1e6
                    except Exception:
                        ts = time.time()
                    self.feed.add(ts, extract_text(r))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()


# ------------------------------------------------------------------ rolling video buffer

class Ring:
    """ffmpeg writes ~chunk-second pieces into n files (ring_000.ts ...) and overwrites the oldest.
    Tracks each piece's stream time and when it was finished, which maps wall clock -> stream time."""

    def __init__(self, url, d, chunk, n):
        self.d, self.n, self.csv = d, n, d / "ring.csv"
        for f in list(d.glob("ring_*.ts")) + [self.csv]:
            f.unlink(missing_ok=True)
        r = run(ytdlp_base() + ["-g", "-f", "b[height<=1080]/bv*[height<=1080]+ba/b", url], capture=True, check=False)
        urls = [u for u in (r.stdout or "").split() if u.startswith("http")]
        if not urls:
            raise RuntimeError("no stream URL (offline?)")
        cmd = [tool("ffmpeg"), "-y", "-loglevel", "error"]
        for u in urls:
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", u]
        for i in range(len(urls)):
            cmd += ["-map", str(i)]
        cmd += ["-c", "copy", "-f", "segment", "-segment_time", str(chunk), "-segment_wrap", str(n),
                "-segment_list", str(self.csv), "-segment_list_size", str(n), "-segment_list_type", "csv",
                "-segment_format", "mpegts", "-reset_timestamps", "1", str(d / "ring_%03d.ts")]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=open(d / "ffmpeg.log", "w"))
        self.pieces = deque(maxlen=n)   # (file name, stream start, stream end)
        self.offsets = deque(maxlen=20)  # wall time - stream time, measured when each piece appears
        self.lock = threading.Lock()

    def alive(self):
        return self.proc.poll() is None

    def poll(self):
        try:
            rows = [l.split(",") for l in self.csv.read_text(errors="ignore").splitlines()]
        except OSError:
            return
        now = time.time()
        with self.lock:
            known = {(p[0], round(p[1], 2)) for p in self.pieces}
            for row in rows:
                if len(row) < 3:
                    continue
                name, st, en = row[0], float(row[1]), float(row[2])
                if (name, round(st, 2)) in known or (self.pieces and st < self.pieces[-1][1] - 0.01):
                    continue
                self.pieces.append((name, st, en)); self.offsets.append(now - en)

    def stream_time(self, wall):
        with self.lock:
            return None if not self.offsets else wall - min(self.offsets)   # earliest sighting = least polling delay

    def latest(self):
        with self.lock:
            return self.pieces[-1][2] if self.pieces else None

    def oldest(self):
        with self.lock:
            return self.pieces[0][1] if self.pieces else None

    def snapshot(self, t0, t1, out):
        """Copy the pieces covering stream time [t0, t1] (before ffmpeg overwrites them) and join them into out."""
        with self.lock:
            pick = [p for p in self.pieces if p[2] > t0 and p[1] < t1]
        if not pick:
            return None
        tmp = out.parent / ".pieces"; tmp.mkdir(parents=True, exist_ok=True)
        files = []
        for i, (name, st, en) in enumerate(pick):
            dst = tmp / f"{i:03d}.ts"; shutil.copyfile(self.d / name, dst); files.append(dst)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{f.resolve().as_posix()}'\n" for f in files))
        start = max(t0, pick[0][1])
        r = run([tool("ffmpeg"), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ss", f"{start - pick[0][1]:.2f}",
                 "-t", f"{min(t1, pick[-1][2]) - start:.2f}", "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(out)],
                check=False, capture=True)
        shutil.rmtree(tmp, ignore_errors=True)
        return start if r.returncode == 0 and out.exists() and out.stat().st_size > 0 else None

    def stop(self):
        if self.alive():
            try:
                self.proc.stdin.write(b"q\n"); self.proc.stdin.flush(); self.proc.wait(10)
            except Exception:
                self.proc.kill()


def loudest(clip, around, span=(-3.0, 7.0)):
    """Clip-relative time of the loudest 1 s of audio in [around+span0, around+span1], or None if nothing stands out."""
    import array
    r = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(clip), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True)
    a = array.array("h"); a.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    if not a:
        return None
    hop = 2000   # 0.25 s
    rms = [(sum(x * x for x in a[i:i + hop]) / max(1, len(a[i:i + hop]))) ** 0.5 for i in range(0, len(a), hop)]
    lo, hi = max(0, int((around + span[0]) * 4)), min(len(rms) - 4, int((around + span[1]) * 4))
    if hi <= lo:
        return None
    win = [(sum(rms[i:i + 4]), i) for i in range(lo, hi)]
    best, i = max(win)
    med = statistics.median(sum(rms[j:j + 4]) for j in range(0, len(rms) - 4)) if len(rms) > 8 else 0
    return (i + 2) / 4 if best > 1.6 * med else None


# ------------------------------------------------------------------ one stream

class Stream(threading.Thread):
    def __init__(self, url, a, outq, stop_ev):
        super().__init__(daemon=True)
        self.url, self.a, self.outq, self.stop_ev = url, a, outq, stop_ev
        self.login = twitch_login(url)
        self.tag = self.login or re.sub(r"\W+", "", url.split("v=")[-1])[:14] or "stream"
        self.d = JOBS / f"live-{self.tag}" / "ring"
        self.spikes, self.status = 0, "starting"

    def run(self):
        a, d = self.a, self.d
        d.mkdir(parents=True, exist_ok=True)
        start = time.time(); feed = Feed(start)
        try:
            ring = Ring(self.url, d, a.chunk, max(20, int(a.buffer / a.chunk + 0.5)))
        except Exception as e:
            self.status = f"not recording: {e}"; log(self.tag, self.status); return
        chat = TwitchChat(self.login, d, feed) if self.login else YouTubeChat(self.url, d, feed)
        log(self.tag, f"watching: {a.buffer:g} s buffer in ~{a.chunk:g} s pieces, chat in {BIN} s bins")
        last_spike, pending = 0.0, []
        W = int(4 / BIN)
        try:
            while not self.stop_ev.is_set() and ring.alive():
                time.sleep(BIN)
                chat.poll(); ring.poll()
                now = time.time(); judge = int((now - 1.5) / BIN)   # let chat batches land before judging
                if now - start > 60 and now - last_spike > a.cooldown:
                    raw = feed.sum(feed.raw, judge - W, judge); w = feed.sum(feed.wgt, judge - W, judge)
                    hist = [feed.sum(feed.wgt, b - W, b) for b in range(max(int(start / BIN) + W, judge - int(180 / BIN)), judge - int(10 / BIN), 2)]
                    base = statistics.median(hist) if len(hist) >= 20 else None
                    self.status = f"{raw / 4:.1f} msg/s" + (f", {w / max(base, 2):.1f}x normal" if base else ", learning normal rate")
                    if base is not None and raw >= a.min_msgs and w >= a.ratio * max(base, 2):
                        onset = judge   # walk back to where chat started climbing (2x normal)
                        while onset > judge - int(12 / BIN) and feed.sum(feed.wgt, onset - W - 1, onset - 1) >= 2 * max(base, 2):
                            onset -= 1
                        onset_wall = (onset - 2) * BIN   # chat started climbing about here
                        st = ring.stream_time(onset_wall)
                        if st is not None:
                            last_spike = now; self.spikes += 1
                            words = feed.top_words(judge - W, judge)
                            moment = st - a.reaction
                            log(self.tag, f"SPIKE {self.spikes}: {raw} msgs in 4 s, {w / max(base, 2):.1f}x normal  {', '.join(words)}")
                            pending.append({"n": self.spikes, "moment": moment, "wall": onset_wall, "rate": raw, "base": base,
                                            "ratio": round(w / max(base, 2), 1), "words": words})
                for p in [p for p in pending if (ring.latest() or 0) >= p["moment"] + 7 + a.after + 0.5]:
                    pending.remove(p); self.clip(ring, p)
        finally:
            for p in pending:
                self.clip(ring, p)
            ring.stop(); chat.stop()

    def clip(self, ring, p):
        a = self.a
        stamp = datetime.fromtimestamp(p["wall"]).strftime("%Y%m%d-%H%M%S")
        jd = JOBS / f"live-{self.tag}-{stamp}"; jd.mkdir(parents=True, exist_ok=True)
        src = jd / "source.mp4"
        # our recording can run a few seconds behind what viewers see, so the real moment may be up to ~7 s after the
        # chat estimate: keep room on both sides and let the audio peak decide
        t0 = max(p["moment"] - a.before - 4, ring.oldest() or 0)
        got = ring.snapshot(t0, p["moment"] + 7 + a.after, src)
        if got is None:
            log(self.tag, f"spike {p['n']}: could not copy the clip out of the buffer"); shutil.rmtree(jd, ignore_errors=True); return
        rel = p["moment"] - got                         # moment, in clip seconds
        peak = loudest(src, rel)
        anchor = peak if peak is not None else rel
        shift = anchor - rel
        how = (f"audio peak {abs(shift):.1f} s {'after' if shift >= 0 else 'before'} the chat estimate" if peak is not None
               else "chat estimate (no clear audio peak)")
        self.outq.put({"tag": self.tag, "url": self.url, "job": jd, "src": src, "anchor": anchor, "spike": p, "how": how})


# ------------------------------------------------------------------ after a clip: register it as a job + pick

def register(item, a, model_box):
    jd, src, anchor, p = item["job"], item["src"], item["anchor"], item["spike"]
    from common import ffprobe
    dur = float(ffprobe(str(src))["format"]["duration"])
    start = max(0.0, anchor - a.before)
    tease = [round(max(start, anchor - 0.7), 2), round(min(dur, anchor + 1.3), 2)]
    rel = lambda path: str(Path(path).resolve().relative_to(ROOT))
    when = datetime.fromtimestamp(p["wall"]).strftime("%H:%M:%S")
    save_json(jd / "info.json", {"id": jd.name, "title": f"{item['tag']} live · chat spike at {when}", "channel": item["tag"], "uploader": item["tag"],
                                 "webpage_url": item["url"], "duration": dur, "live_spike": {**p, "found_by": item["how"]}})
    if not a.no_transcribe:
        try:
            if "m" not in model_box:
                from faster_whisper import WhisperModel
                model_box["m"] = WhisperModel("small", device="cpu", compute_type="int8")
            import numpy as np   # decode with ffmpeg: faster-whisper's own file reader fails on some Python versions
            pcm = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
            audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], np.int16).astype(np.float32) / 32768
            segs, _ = model_box["m"].transcribe(audio, word_timestamps=True, vad_filter=True)
            words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)} for s in segs for w in (s.words or []) if w.word.strip()]
            save_json(jd / "transcript.json", words)
        except Exception as e:
            log(item["tag"], f"transcription skipped: {e}")
    save_json(jd / "candidates_audit.json", {"source": rel(src), "candidates": [{
        "n": 1, "tag": f"L{p['n']}", "start": round(start, 2), "end": round(min(dur, anchor + a.after), 2), "tease": tease,
        "title": "", "titles": [], "file": rel(src), "offset": 0, "chat": {k: p[k] for k in ("rate", "ratio", "words")}}]})
    url = f"http://127.0.0.1:8766/?job={jd.name}&set=candidates_audit&pick=1"
    row = f"| {when} | {item['tag']} | {p['rate']} msgs/4 s ({p['ratio']}x) | {', '.join(p['words'])} | {item['how']} | {rel(src)} | [edit]({url}) |\n"
    summary = JOBS / "live_watch.md"
    if not summary.exists():
        summary.write_text("| time | stream | chat | top words | moment found by | clip | editor |\n|---|---|---|---|---|---|---|\n")
    with open(summary, "a") as fh:
        fh.write(row)
    log(item["tag"], f"clip ready ({dur:.0f} s, hook at {anchor:.1f} s): {rel(src)}  ·  {url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--top", type=int, default=0, help="also watch the N biggest live GTA 6 streams on Twitch")
    ap.add_argument("--minutes", type=float, default=120)
    ap.add_argument("--buffer", type=float, default=60, help="seconds of video kept per stream (rolling)")
    ap.add_argument("--chunk", type=float, default=2.0, help="piece length; rounded up to the stream's keyframe interval")
    ap.add_argument("--before", type=float, default=18, help="build-up kept before the moment")
    ap.add_argument("--after", type=float, default=6, help="kept after the moment")
    ap.add_argument("--reaction", type=float, default=2.5, help="seconds chat lags behind what it reacts to")
    ap.add_argument("--ratio", type=float, default=2.5, help="spike = this many times the stream's normal chat rate")
    ap.add_argument("--min-msgs", type=int, default=10, help="minimum messages in the 4 s window")
    ap.add_argument("--cooldown", type=float, default=40)
    ap.add_argument("--no-transcribe", action="store_true")
    a = ap.parse_args()
    need = a.before + a.after + a.reaction + 30
    if a.buffer < need:
        print(f"--buffer raised to {need:.0f} s so a whole clip fits in it"); a.buffer = need
    urls = list(a.urls)
    if a.top:
        rows = twitch.top_streams("Grand Theft Auto VI", a.top * 3)
        urls += [r["url"] for r in rows if r["url"] not in urls][: a.top]
        for r in rows[: a.top]:
            print(f"  {r['name']}: {r['viewers']:,} viewers · {r['title'][:70]}")
    if not urls:
        sys.exit("give stream URLs or --top N")
    stop_ev, outq, model_box = threading.Event(), queue.Queue(), {}
    streams = [Stream(u, a, outq, stop_ev) for u in urls]
    for s in streams:
        s.start()
    end_at, last_status = time.time() + a.minutes * 60, 0
    try:
        while time.time() < end_at and any(s.is_alive() for s in streams):
            try:
                register(outq.get(timeout=1), a, model_box)
            except queue.Empty:
                pass
            if time.time() - last_status > 30:
                last_status = time.time()
                log("status", " · ".join(f"{s.tag}: {s.status}, {s.spikes} clip(s)" for s in streams))
    except KeyboardInterrupt:
        print("stopping...", flush=True)
    finally:
        stop_ev.set()
        for s in streams:
            s.join(30)
        while not outq.empty():
            register(outq.get(), a, model_box)
        for s in streams:
            shutil.rmtree(s.d.parent, ignore_errors=True)   # the rolling buffer is only scratch
        print(f"done: {sum(s.spikes for s in streams)} flagged moment(s), see {JOBS / 'live_watch.md'}")


if __name__ == "__main__":
    main()
