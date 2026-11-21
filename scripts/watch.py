"""Watch several live streams at once and clip the moments their chat goes crazy.

Per stream, all in parallel:
  video  a rolling buffer: ffmpeg writes ~--chunk second pieces into a fixed set of files and overwrites the
         oldest (keeps --buffer seconds, e.g. 30 x 2 s). Nothing piles up on disk.
         Note: without re-encoding, pieces can only split on keyframes (Twitch/YouTube: every ~2 s), so a
         smaller --chunk is rounded up to the keyframe interval.
  chat   Twitch IRC (anonymous) or YouTube live chat in 0.5 s bins, by person: someone spamming counts once, a hype
         message (LMAO, W, KEKW, 💀...) counts 1.5; giveaway/raid/link spam is ignored.
  spike  the number of different people chatting in the last 4 s stays above --ratio times the stream's normal
         (median of the last 5 min) for --sustain seconds, with at least --min-chatters people. --cooldown between
         clips; after --max-per-10min clips, only a bigger spike clips (see Detector).
  delay  chat reacts late: the moment is placed --reaction seconds before chat *started* climbing, then moved to
         the loudest second of audio just before (the scream / the reveal).
  clip   --before seconds of build-up and --after seconds after the moment, copied out of the buffer as soon as
         they are recorded. Each clip becomes its own job (jobs/live-<stream>-<time>/) with a transcript and a pick
         whose hook is the flagged moment, so it shows up in the editor as "New from pick" and cut.py can make the
         hook-first edit.

usage: python watch.py https://twitch.tv/<login> [more URLs] [--top 3] [--minutes 120]
                       [--buffer 60 --chunk 2 --before 18 --after 6 --reaction 2.5 --ratio 2.5 --min-msgs 10]
Summary of every flagged moment: jobs/live_watch.md. Stop with Ctrl+C.

The editor's Live page (http://127.0.0.1:8766/live) starts and drives this script. While it runs it writes
jobs/.live/status.json every 0.5 s (streams, chat rate, clips, a live preview playlist per stream) and takes
commands as JSON files dropped in jobs/.live/cmd/: {"do": "add", "url"}, {"do": "remove", "tag"},
{"do": "clip", "tag", "behind"} (clip what the viewer just saw), {"do": "extend", "minutes"}, {"do": "stop"}.
"""
import argparse, json, os, queue, re, shutil, statistics, subprocess, sys, threading, time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import ROOT, run, save_json, tool, ytdlp_base
from live import HYPE, NOISE, STOP, WORD, extract_text, twitch_login
import twitch

BIN = 0.5
JOBS = ROOT / "jobs"
LIVE = JOBS / ".live"            # status + commands for the editor's Live page
CMD, STATUS = LIVE / "cmd", LIVE / "status.json"
LOG_LOCK = threading.Lock()


def log(tag, msg):
    with LOG_LOCK:
        print(f"{time.strftime('%H:%M:%S')} [{tag[:14]:<14}] {msg}", flush=True)


# ------------------------------------------------------------------ chat

class Feed:
    """Chat messages in 0.5 s bins (keyed by int(wall_time / 0.5)): who wrote, whether it was hype, the text."""

    def __init__(self, start):
        self.start, self.msgs, self.lock, self._sig = start, defaultdict(list), threading.Lock(), {}

    def add(self, ts, txt, user=None):
        txt = (txt or "").strip()
        if ts < self.start - 5 or not txt or NOISE.search(txt):
            return
        with self.lock:
            self.msgs[int(ts / BIN)].append((user, bool(HYPE.search(txt)), txt))

    def count(self, b0, b1):
        with self.lock:
            return sum(len(self.msgs.get(b, ())) for b in range(b0, b1))

    def chatters(self, b0, b1):
        """(different people who wrote in bins [b0, b1), the same weighted: someone who sent a hype message counts 1.5).
        One person spamming counts once, so emote walls from a few accounts don't look like a moment."""
        seen = {}
        with self.lock:
            for b in range(b0, b1):
                for i, (u, hype, _) in enumerate(self.msgs.get(b, ())):
                    k = u or (b, i)   # no user name (some YouTube messages): each message counts
                    seen[k] = max(seen.get(k, 0.0), 1.5 if hype else 1.0)
        return len(seen), sum(seen.values())

    def sig(self, b_end, w, settled):
        """Weighted chatters in the w bins before b_end, cached once those bins are settled (<= settled)."""
        v = self._sig.get(b_end)
        if v is None:
            v = self.chatters(b_end - w, b_end)[1]
            if b_end <= settled:
                self._sig[b_end] = v
        return v

    def prune(self, before):
        """Forget bins older than `before` (memory over a long watch)."""
        with self.lock:
            for b in [b for b in self.msgs if b < before]:
                del self.msgs[b]
        for b in [b for b in self._sig if b < before]:
            del self._sig[b]

    def top_words(self, b0, b1, n=4):
        cnt = Counter()
        with self.lock:
            for b in range(b0, b1):
                for _, _, t in self.msgs.get(b, []):
                    cnt.update(w.lower() for w in WORD.findall(t) if w.lower() not in STOP)
        return [w for w, _ in cnt.most_common(n)]


class Detector:
    """Decides when a stream's chat explodes.

    Signal: how many different people wrote in the last 4 s (hype messages count 1.5), against the stream's normal
    (median of the last 5 min). A spike has to stay above --ratio x normal for --sustain seconds, with at least
    --min-chatters people: a few spammers or a 2-second emote wall don't count. After a clip, --cooldown seconds pass
    before the next, and once a stream has --max-per-10min clips in 10 minutes, only a spike bigger than all of them
    clips. Replayable on recorded chat (pass `now`), which is how the defaults were tuned."""

    W = int(4 / BIN)

    def __init__(self, a, start):
        self.a, self.start = a, start
        self.last, self.hot_since, self.peak, self.recent = -1e9, None, 0.0, deque()
        self.base = self.ratio = None
        self.rate, self.people = 0.0, 0

    def step(self, feed, now):
        """Call every BIN seconds. Returns a spike dict when a clip should be cut, else None."""
        a, W = self.a, self.W
        judge = int((now - 1.5) / BIN)   # let chat batches land before judging
        self.people, sig = feed.chatters(judge - W, judge)
        self.rate = feed.count(judge - W, judge) / 4
        lo = max(int(self.start / BIN) + W, judge - int(300 / BIN))
        hist = [feed.sig(b, W, judge) for b in range(lo, judge - int(10 / BIN), 2)]
        self.base = statistics.median(hist) if now - self.start > 90 and len(hist) >= 40 else None
        self.ratio = sig / max(self.base, 3) if self.base is not None else None
        if judge % int(60 / BIN) == 0:
            feed.prune(judge - int(360 / BIN))
        if self.ratio is None or self.ratio < a.ratio or self.people < a.min_chatters:
            self.hot_since, self.peak = None, 0.0
            return None
        if self.hot_since is None:
            self.hot_since = now
        self.peak = max(self.peak, self.ratio)
        if now - self.hot_since < a.sustain or now - self.last < a.cooldown:
            return None
        while self.recent and now - self.recent[0][0] > 600:
            self.recent.popleft()
        if len(self.recent) >= a.max_per_10min and self.peak <= max(x for _, x in self.recent):
            return None   # busy stream: only a bigger moment than the recent clips gets through
        onset = judge   # walk back to where chat started climbing (2x normal)
        while onset > judge - int((12 + a.sustain) / BIN) and feed.sig(onset - 1, W, judge) >= 2 * max(self.base, 3):
            onset -= 1
        spike = {"onset_wall": (onset - 2) * BIN, "strength": round(self.peak, 1), "people": self.people,
                 "rate": round(self.rate * 4), "base": round(self.base, 1), "words": feed.top_words(judge - W, judge)}
        self.last = now; self.recent.append((now, self.peak)); self.hot_since, self.peak = None, 0.0
        return spike


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
                m = json.loads(ln); self.feed.add(float(m["t"]), m.get("text"), m.get("user"))
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
                    self.feed.add(ts, extract_text(r), r.get("authorExternalChannelId") or ((r.get("authorName") or {}).get("simpleText")))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()


# ------------------------------------------------------------------ rolling video buffer

class Ring:
    """ffmpeg writes ~chunk-second pieces into n files (ring_000.ts ...) and overwrites the oldest.
    Tracks each piece's stream time and when it was finished, which maps wall clock -> stream time."""

    def __init__(self, url, d, chunk, n, preview=True):
        self.d, self.n, self.csv = d, n, d / "ring.csv"
        for f in list(d.glob("ring_*.ts")) + [self.csv]:
            f.unlink(missing_ok=True)
        shutil.rmtree(d / "hls", ignore_errors=True)
        r = run(ytdlp_base() + ["-g", "-f", "b[height<=1080]/bv*[height<=1080]+ba/b", url], capture=True, check=False)
        urls = [u for u in (r.stdout or "").split() if u.startswith("http")]
        if not urls:
            raise RuntimeError("no stream URL (offline?)")
        cmd = [tool("ffmpeg"), "-y", "-loglevel", "error"]
        for u in urls:
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", u]
        # video and audio only: Twitch also sends a timed-metadata track (ad markers) whose timestamps jump back
        # at ad breaks, which made ffmpeg stop recording
        maps = [x for i in range(len(urls)) for x in ("-map", f"{i}:v?", "-map", f"{i}:a?")]
        cmd += maps
        cmd += ["-c", "copy", "-f", "segment", "-segment_time", str(chunk), "-segment_wrap", str(n),
                "-segment_list", str(self.csv), "-segment_list_size", str(n), "-segment_list_type", "csv",
                "-segment_format", "mpegts", "-reset_timestamps", "1", str(d / "ring_%03d.ts")]
        if preview:   # second output of the same recording: a short live HLS playlist the Live page plays
            (d / "hls").mkdir()
            cmd += maps
            cmd += ["-c", "copy", "-f", "hls", "-hls_time", str(chunk), "-hls_list_size", "6",
                    "-hls_flags", "delete_segments+omit_endlist", "-hls_segment_filename", str(d / "hls" / "s%06d.ts"),
                    str(d / "hls" / "index.m3u8")]
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
    def __init__(self, url, a, outq, stop_ev, meta=None):
        super().__init__(daemon=True)
        self.url, self.a, self.outq, self.stop_ev = url, a, outq, stop_ev
        self.login = twitch_login(url)
        self.tag = self.login or re.sub(r"\W+", "", url.split("v=")[-1])[:14] or "stream"
        self.d = JOBS / f"live-{self.tag}" / "ring"
        self.spikes, self.status = 0, "starting"
        self.meta = meta or {}                         # name / title / viewers, when known
        self.halt = threading.Event()                  # stop just this stream (the Live page's Remove)
        self.manual = deque()                          # "Clip now" requests: seconds the viewer is behind live
        self.series = deque(maxlen=int(180 / BIN))     # (wall, msgs/s, x normal or None), for the chat graph
        self.clips, self.since = [], time.time()

    def stopped(self):
        return self.stop_ev.is_set() or self.halt.is_set()

    def run(self):
        """Record and watch chat; if the recording drops (stream hiccup, expired URL), reconnect with the same chat."""
        a, d = self.a, self.d
        d.mkdir(parents=True, exist_ok=True)
        start = time.time(); feed = Feed(start); chat, fails = None, 0
        self.det = Detector(a, start)
        try:
            while not self.stopped():
                try:
                    ring = Ring(self.url, d, a.chunk, max(20, int(a.buffer / a.chunk + 0.5)), preview=not a.no_preview)
                except Exception as e:
                    fails += 1
                    self.status = f"not recording: {e}"; log(self.tag, self.status)
                    if fails >= 3:
                        return
                    self.halt.wait(10); continue
                if chat is None:
                    chat = TwitchChat(self.login, d, feed) if self.login else YouTubeChat(self.url, d, feed)
                log(self.tag, f"watching: {a.buffer:g} s buffer in ~{a.chunk:g} s pieces, chat in {BIN} s bins")
                t0 = time.time()
                self._record(ring, chat, feed, start)
                if self.stopped():
                    break
                fails = 0 if time.time() - t0 > 60 else fails + 1
                if fails >= 3:
                    self.status = "stream ended (recording keeps failing)"; log(self.tag, self.status); return
                self.status = "reconnecting…"; log(self.tag, "recording dropped, reconnecting")
                self.halt.wait(3)
        finally:
            if chat is not None:
                chat.stop()

    def _record(self, ring, chat, feed, start):
        a, det, pending = self.a, self.det, []
        try:
            while not self.stop_ev.is_set() and not self.halt.is_set() and ring.alive():
                time.sleep(BIN)
                chat.poll(); ring.poll()
                now = time.time()
                spike = det.step(feed, now)
                ratio = det.ratio
                self.series.append((round(now, 1), round(det.rate, 2), None if ratio is None else round(ratio, 2)))
                self.status = f"{det.people} chatting" + (f", {ratio:.1f}x normal" if ratio is not None else ", learning normal rate")
                while self.manual:   # Clip now: what the viewer saw a moment ago, with the same build-up as a spike
                    behind, lat = self.manual.popleft(), ring.latest()
                    if lat is None:
                        continue
                    self.spikes += 1
                    judge = int((now - 1.5) / BIN)
                    pending.append({"n": self.spikes, "moment": lat - behind - 4, "wall": now - behind - 4, "rate": round(det.rate * 4),
                                    "base": det.base, "ratio": round(ratio or 0, 1), "people": det.people,
                                    "words": feed.top_words(judge - Detector.W, judge), "manual": True})
                    log(self.tag, f"CLIP NOW {self.spikes}: by hand, {behind:.1f} s behind live")
                if spike and (st := ring.stream_time(spike["onset_wall"])) is not None:
                    self.spikes += 1
                    log(self.tag, f"SPIKE {self.spikes}: {spike['people']} people in 4 s, {spike['strength']}x normal for {a.sustain:g}+ s  "
                                  f"{', '.join(spike['words'])}")
                    pending.append({"n": self.spikes, "moment": st - a.reaction, "wall": spike["onset_wall"], "rate": spike["rate"],
                                    "base": spike["base"], "ratio": spike["strength"], "people": spike["people"], "words": spike["words"]})
                for p in [p for p in pending if (ring.latest() or 0) >= p["moment"] + 7 + a.after + 0.5]:
                    pending.remove(p); self.clip(ring, p)
        finally:
            for p in pending:
                self.clip(ring, p)
            ring.stop()

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
        if p.get("manual"):
            how = "clipped by hand" + (f", audio peak {abs(shift):.1f} s {'after' if shift >= 0 else 'before'}" if peak is not None else "")
        self.outq.put({"tag": self.tag, "url": self.url, "job": jd, "src": src, "anchor": anchor, "spike": p, "how": how, "stream": self})


# ------------------------------------------------------------------ after a clip: register it as a job + pick

def register(item, a, model_box):
    jd, src, anchor, p = item["job"], item["src"], item["anchor"], item["spike"]
    from common import ffprobe
    dur = float(ffprobe(str(src))["format"]["duration"])
    start = max(0.0, anchor - a.before)
    tease = [round(max(start, anchor - 0.7), 2), round(min(dur, anchor + 1.3), 2)]
    rel = lambda path: str(Path(path).resolve().relative_to(ROOT))
    when = datetime.fromtimestamp(p["wall"]).strftime("%H:%M:%S")
    kind = "clipped by hand" if p.get("manual") else "chat spike"
    save_json(jd / "info.json", {"id": jd.name, "title": f"{item['tag']} live · {kind} at {when}", "channel": item["tag"], "uploader": item["tag"],
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
    url = f"http://127.0.0.1:8766/edit?job={jd.name}&set=candidates_audit&pick=1"
    row = f"| {when} | {item['tag']} | {p['rate']} msgs/4 s ({p['ratio']}x) | {', '.join(p['words'])} | {item['how']} | {rel(src)} | [edit]({url}) |\n"
    summary = JOBS / "live_watch.md"
    if not summary.exists():
        summary.write_text("| time | stream | chat | top words | moment found by | clip | editor |\n|---|---|---|---|---|---|---|\n")
    with open(summary, "a") as fh:
        fh.write(row)
    if item.get("stream"):
        item["stream"].clips.append(jd.name)
    log(item["tag"], f"clip ready ({dur:.0f} s, hook at {anchor:.1f} s): {rel(src)}  ·  {url}")


# ------------------------------------------------------------------ the Live page: status out, commands in

class Control(threading.Thread):
    """Writes jobs/.live/status.json and runs the commands the Live page drops in jobs/.live/cmd/."""

    def __init__(self, streams, a, outq, stop_ev, end_at):
        super().__init__(daemon=True)
        self.streams, self.a, self.outq, self.stop_ev, self.end_at = streams, a, outq, stop_ev, end_at
        self.quit, self.started = threading.Event(), time.time()
        CMD.mkdir(parents=True, exist_ok=True)
        for f in CMD.glob("*.json"):   # left over from an earlier run
            f.unlink(missing_ok=True)

    def find(self, tag):
        return next((s for s in list(self.streams) if s.tag == tag and not s.halt.is_set()), None)

    def do(self, c):
        d = c.get("do")
        if d == "add":
            url = str(c.get("url", "")).strip()
            s = Stream(url, self.a, self.outq, self.stop_ev, c.get("meta"))
            if not re.match(r"https?://", url) or any(x.tag == s.tag and x.is_alive() and not x.halt.is_set() for x in list(self.streams)):
                return
            for old in [x for x in list(self.streams) if x.tag == s.tag]:   # a dead or removed earlier try of the same stream
                self.streams.remove(old)
            s.start(); self.streams.append(s); log("live page", f"added {url}")
        elif d == "remove" and (s := self.find(str(c.get("tag")))):
            s.halt.set(); log("live page", f"removing {s.tag}")

            def drop():
                s.join(30)
                if s in self.streams:
                    self.streams.remove(s)
                if not any(x.tag == s.tag for x in list(self.streams)):   # re-added meanwhile: that one owns the folder now
                    shutil.rmtree(s.d.parent, ignore_errors=True)
            threading.Thread(target=drop, daemon=True).start()
        elif d == "clip" and (s := self.find(str(c.get("tag")))):
            s.manual.append(min(30.0, max(0.0, float(c.get("behind") or 0))))
        elif d == "extend":
            self.end_at = time.time() + max(5.0, min(24 * 60.0, float(c.get("minutes") or 60))) * 60
        elif d == "stop":
            self.quit.set()

    def write(self):
        rows = []
        for s in list(self.streams):
            hls = s.d / "hls" / "index.m3u8"
            rows.append({"tag": s.tag, "url": s.url, "meta": s.meta, "alive": s.is_alive() and not s.halt.is_set(), "removed": s.halt.is_set(),
                         "status": s.status, "spikes": s.spikes, "clips": s.clips[-30:], "since": s.since,
                         "series": list(s.series)[1::2],   # one point per second
                         "hls": "/media/" + hls.relative_to(JOBS).as_posix() if hls.exists() else None})
        tmp = STATUS.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "started": self.started, "end_at": self.end_at, "updated": time.time(),
                                   "ratio": self.a.ratio, "min_chatters": self.a.min_chatters, "streams": rows}))
        os.replace(tmp, STATUS)

    def run(self):
        while not self.stop_ev.is_set():
            for f in sorted(CMD.glob("*.json")):
                try:
                    c = json.loads(f.read_text())
                except Exception:
                    c = None
                f.unlink(missing_ok=True)
                if isinstance(c, dict):
                    try:
                        self.do(c)
                    except Exception as e:
                        log("live page", f"command {c.get('do')} failed: {e}")
            try:
                self.write()
            except OSError:
                pass
            time.sleep(0.5)


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
    ap.add_argument("--ratio", type=float, default=3.0, help="spike = this many times the usual number of people chatting")
    ap.add_argument("--sustain", type=float, default=3.0, help="seconds chat must stay above --ratio before it clips")
    ap.add_argument("--min-chatters", type=int, default=12, help="at least this many different people in the 4 s window")
    ap.add_argument("--cooldown", type=float, default=120, help="seconds between two clips of the same stream")
    ap.add_argument("--max-per-10min", type=int, default=3, help="after this many clips in 10 min, only a bigger spike clips")
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--no-preview", action="store_true", help="don't write the live preview playlist for the Live page")
    a = ap.parse_args()
    need = a.before + a.after + a.reaction + 30
    if a.buffer < need:
        print(f"--buffer raised to {need:.0f} s so a whole clip fits in it"); a.buffer = need
    urls, meta = list(a.urls), {}
    if a.top:
        rows = twitch.top_streams("Grand Theft Auto VI", a.top * 3)
        urls += [r["url"] for r in rows if r["url"] not in urls][: a.top]
        for r in rows[: a.top]:
            meta[r["url"]] = {"name": r["name"], "title": r["title"], "viewers": r["viewers"]}
            print(f"  {r['name']}: {r['viewers']:,} viewers · {r['title'][:70]}")
    if not urls:
        sys.exit("give stream URLs or --top N")
    import signal

    def on_term(*_):   # stop cleanly when killed: finish pending clips, delete the buffers
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    stop_ev, outq, model_box = threading.Event(), queue.Queue(), {}
    streams = [Stream(u, a, outq, stop_ev, meta.get(u)) for u in urls]
    for s in streams:
        s.start()
    ctl = Control(streams, a, outq, stop_ev, time.time() + a.minutes * 60)
    ctl.start()
    last_status, dead_since = 0, None
    try:
        while time.time() < ctl.end_at and not ctl.quit.is_set():
            if any(s.is_alive() for s in list(streams)):
                dead_since = None
            elif (dead_since := dead_since or time.time()) and time.time() - dead_since > 20:
                break   # every stream is offline or removed, and none was added for 20 s
            try:
                register(outq.get(timeout=1), a, model_box)
            except queue.Empty:
                pass
            if time.time() - last_status > 30:
                last_status = time.time()
                log("status", " · ".join(f"{s.tag}: {s.status}, {s.spikes} clip(s)" for s in list(streams)))
    except KeyboardInterrupt:
        print("stopping...", flush=True)
    finally:
        stop_ev.set()
        for s in list(streams):
            s.join(30)
        while not outq.empty():
            register(outq.get(), a, model_box)
        for s in list(streams):
            shutil.rmtree(s.d.parent, ignore_errors=True)   # the rolling buffer is only scratch
        ctl.join(2)
        STATUS.unlink(missing_ok=True)
        print(f"done: {sum(s.spikes for s in streams)} flagged moment(s), see {JOBS / 'live_watch.md'}")


if __name__ == "__main__":
    main()
