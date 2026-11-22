"""Parallel clip pipeline: many sources at once, audio first.

Per source (URL or local file), all sources concurrently:
  info -> audio-only download (chunked, fast) -> 16 kHz wav (first --max-minutes)
       + chat replay in the background (YouTube VODs that were live; stopped once past --max-minutes)
       + video in the background: the full file if the source is short (<= --full-under min),
         otherwise only the sections around the picked moments (no multi-GB VOD downloads)
The wav is split at quiet points into ~150 s chunks. Chunks from every source go into ONE worker pool
(GPU worker(s) on Apple Silicon/NVIDIA + CPU workers), so one long VOD and many short videos both use the whole machine.
The pool is fed shortest-remaining-first: a 10 h VOD doesn't hold up the short videos queued with it.
When a source's chunks are back: transcript.json -> candidates.json (shorts) + candidates_long.json (long clips).
Finished chunks are kept in jobs/<id>/parts/ until the transcript is whole, so a stopped run resumes where it was.

Per-source control (--control DIR, the Extract page's buttons): a file DIR/<job id> saying
  skip   drop this source (stage "skipped")
  trim   stop transcribing it and score what is transcribed so far (the first unbroken run of chunks);
         jobs/<id>/trim.json records where it was cut, and a later run picks the transcription up from there.

usage: python pipeline.py URL [URL ...] [--from sources.txt] [--max-minutes 90] [--gpu-workers 1] [--cpu-workers 3]
                          [--short 15:40] [--long 60:150] [--top 8] [--top-long 4] [--no-long] [--control DIR]
Progress: jobs/<id>/progress.json (read by the dashboard).
"""
import argparse, json, multiprocessing as mp, os, queue, shutil, signal, subprocess, sys, threading, time, traceback, wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from common import (SECTION_ENCODE, VIDEO_FMT, ffprobe, is_url, job_dir, load_json, run, save_json, tool,
                    ytdlp_base, ytdlp_json)
import moments

MODEL = os.environ.get("CLIP_WHISPER_MODEL", "small")   # CPU workers
# The GPU worker uses the stronger model: Claude picks moments by reading this transcript, so misheard slang and names
# cost picks. Measured on Apple M5 Pro (Metal): small 22x real time, large-v3-turbo 13x. (Batched inference was
# 2-4x faster but dropped 20-30% of the words on stream audio, so it is not used.)
GPU_MODEL = os.environ.get("CLIP_WHISPER_GPU_MODEL", "large-v3-turbo")
SR = 16000
INFO_KEYS = ("id", "title", "uploader", "channel", "uploader_id", "webpage_url", "duration", "view_count",
             "upload_date", "heatmap", "is_live", "was_live", "live_status", "timestamp")
LOCK = threading.Lock()


def log(job, msg):
    print(f"{time.strftime('%H:%M:%S')} [{job[:18]:<18}] {msg}", flush=True)


# ---------------------------------------------------------------- transcription workers (separate processes)

def worker(device, threads, tasks, results):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(GPU_MODEL if device != "cpu" else MODEL, device=device,
                             compute_type="float16" if device != "cpu" else "int8", cpu_threads=threads)
    except Exception as e:  # GPU backend unavailable: become a CPU worker
        results.put({"note": f"{device} worker unavailable ({str(e)[:80]}), using cpu"})
        device = "cpu"
        model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=threads)
    while True:
        t = tasks.get()
        if t is None:
            break
        t0 = time.time()
        try:
            with wave.open(t["wav"]) as w:
                w.setpos(t["a"])
                pcm = np.frombuffer(w.readframes(t["b"] - t["a"]), dtype=np.int16).astype(np.float32) / 32768
            segs, _ = model.transcribe(pcm, word_timestamps=True, vad_filter=True)
            off = t["a"] / SR
            words = [{"w": x.word.strip(), "s": round(x.start + off, 2), "e": round(x.end + off, 2)}
                     for s in segs for x in (s.words or []) if x.word.strip()]
            results.put({"job": t["job"], "i": t["i"], "words": words, "dev": device, "secs": time.time() - t0,
                         "audio": (t["b"] - t["a"]) / SR})
        except Exception as e:
            results.put({"job": t["job"], "i": t["i"], "error": repr(e)[:300], "dev": device})


def plan_chunks(db, target=150, search=12):
    """Cut points (seconds) at the quietest second near every `target` seconds."""
    n = len(db)
    cuts = [0]
    while cuts[-1] + target < n - 30:
        t = cuts[-1] + target
        lo, hi = max(cuts[-1] + 30, t - search), min(n - 1, t + search)
        cuts.append(lo + int(np.argmin(db[lo:hi + 1])))
    return cuts + [None]


# ---------------------------------------------------------------- per-source state

class Job:
    def __init__(self, src, a):
        self.src, self.a = src, a
        self.d = job_dir(src)
        self.id = self.d.name
        self.info, self.urls = {}, []
        self.prog = {"pid": os.getpid(), "src": src, "stage": "queued", "started": time.time()}
        self.chunks_total, self.parts = 0, {}
        self.chunks, self.cuts = [], []   # (a, b) frames per chunk; cut points in seconds
        self.backlog, self.inflight = [], 0   # chunk tasks not yet handed to a worker; handed out, not back
        self.cancel = None   # "skip" or "trim" once the user asked for it
        self.video_thread = self.chat_proc = None
        self.full_video = False
        self.db = None

    def update(self, **kw):
        with LOCK:
            self.prog.update(kw, updated=time.time())
            save_json(self.d / "progress.json", self.prog)


def fetch_info(job):
    job.update(stage="info")
    if not is_url(job.src):
        p = Path(job.src).resolve()
        job.info = {"title": p.stem, "webpage_url": str(p), "duration": float(ffprobe(p)["format"]["duration"])}
    else:
        job.info = ytdlp_json(job.src, ["-f", VIDEO_FMT])
        fmts = job.info.get("requested_formats") or [job.info]
        job.urls = [(f["url"], f.get("http_headers") or {}) for f in fmts if f.get("url")]
    save_json(job.d / "info.json", {k: job.info.get(k) for k in INFO_KEYS})


def start_chat(job, limit):
    """YouTube chat replay in the background; stop it once it is past `limit` seconds (file is chronological)."""
    if not (is_url(job.src) and job.info.get("was_live")) or list(job.d.glob("chat*.live_chat.json")):
        return
    proc = subprocess.Popen(ytdlp_base() + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o",
                                            str(job.d / "chat"), job.src], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job.chat_proc = proc

    def watch():
        part = job.d / "chat.live_chat.json.part"
        while proc.poll() is None:
            time.sleep(3)
            try:
                with open(part, "rb") as f:
                    f.seek(max(0, part.stat().st_size - 20000))
                    tail = f.read().decode("utf-8", "ignore").splitlines()
                last = next((json.loads(l)["replayChatItemAction"]["videoOffsetTimeMsec"] for l in reversed(tail[1:])
                             if "videoOffsetTimeMsec" in l), 0)
                job.update(chat_at=int(last) / 1000)
                if int(last) / 1000 > limit:
                    proc.terminate()
            except Exception:
                pass
        proc.wait()
        if part.exists() and not (job.d / "chat.live_chat.json").exists():
            part.rename(job.d / "chat.live_chat.json")
    threading.Thread(target=watch, daemon=True).start()


def start_full_video(job, limit):
    vid = job.d / "source.mp4"
    if vid.exists():
        job.full_video = True
        return
    if not is_url(job.src):   # a local file: into the job folder like a download (the desk only serves jobs/)
        try:
            os.link(Path(job.src).resolve(), vid)
        except OSError:
            shutil.copy2(Path(job.src).resolve(), vid)
        job.full_video = True
        return

    def go():
        full = job.d / "source.full.mp4"
        for attempt in range(3):  # YouTube sometimes answers 403 on a parallel chunk; a retry usually works
            try:
                run(ytdlp_base() + ["-N", "8", "-f", VIDEO_FMT, "--merge-output-format", "mp4", "-o", str(full), job.src])
                break
            except SystemExit as e:
                log(job.id, f"video download failed (try {attempt + 1}/3): {str(e)[-160:]}")
                time.sleep(3)
        else:
            job.full_video = False  # finalize() falls back to downloading only the sections
            return
        if (job.info.get("duration") or 0) > limit:
            run([tool("ffmpeg"), "-y", "-v", "error", "-i", str(full), "-t", str(limit), "-c", "copy",
                 "-movflags", "+faststart", str(vid)])
            full.unlink()
        else:
            full.rename(vid)
        log(job.id, f"video downloaded ({vid.stat().st_size / 1e6:.0f} MB)")
    job.full_video = True
    job.video_thread = threading.Thread(target=go, daemon=True)
    job.video_thread.start()


def part_file(job, i):
    a, b = job.chunks[i]
    return job.d / "parts" / f"{a}-{b}.json"


def prepare(job):
    """info, background chat + video, audio -> wav -> chunk tasks in job.backlog. True when already transcribed."""
    a = job.a
    limit = a.max_minutes * 60
    fetch_info(job)
    dur = job.info.get("duration") or 0
    log(job.id, f"{job.info.get('title', '')[:60]!r} {dur / 60:.0f} min, live_status={job.info.get('live_status')}")
    start_chat(job, limit)
    if dur <= a.full_under * 60 or not is_url(job.src):
        start_full_video(job, limit)
    wav = job.d / "audio.wav"
    if not wav.exists():
        job.update(stage="audio")
        if is_url(job.src):
            aud = job.d / "audio.m4a"
            if not aud.exists():
                run(ytdlp_base() + ["-N", "8", "-f", "ba[ext=m4a]/ba", "-o", str(aud), job.src])
        else:
            aud = Path(job.src).resolve()
        tmp = job.d / "audio.tmp.wav"
        run([tool("ffmpeg"), "-y", "-v", "error", "-i", str(aud), "-t", str(limit), "-vn", "-ac", "1", "-ar", str(SR), str(tmp)])
        tmp.rename(wav)
    _, job.db = moments.audio_rms(None, job.d)
    log(job.id, f"audio ready: {len(job.db) / 60:.1f} min")
    if (job.d / "transcript.json").exists() and not (job.d / "trim.json").exists():
        return True
    with wave.open(str(wav)) as w:
        nframes = w.getnframes()
    cuts = plan_chunks(job.db)
    job.chunks_total = len(cuts) - 1
    job.cuts = cuts[:-1] + [len(job.db)]
    job.chunks = [(cuts[i] * SR, nframes if cuts[i + 1] is None else cuts[i + 1] * SR) for i in range(job.chunks_total)]
    for i in range(job.chunks_total):   # chunks an earlier (stopped or cut-short) run already transcribed
        try:
            job.parts[i] = load_json(part_file(job, i))
        except (OSError, ValueError):
            pass
    job.update(stage="transcribe", chunks_total=job.chunks_total, chunks_done=len(job.parts), audio_sec=len(job.db))
    if job.parts:
        log(job.id, f"{len(job.parts)} of {job.chunks_total} chunks already transcribed")
    if len(job.parts) == job.chunks_total:
        save_transcript(job)
        return True
    job.backlog = [{"job": job.id, "i": i, "wav": str(wav), "a": a, "b": b}
                   for i, (a, b) in enumerate(job.chunks) if i not in job.parts]
    return False


def save_transcript(job, trim=False):
    """transcript.json from the finished chunks. trim: only the first unbroken run of them, and trim.json says
    where that ends so scoring stays inside it. False when there is nothing to keep."""
    k = job.chunks_total
    if trim:
        k = 0
        while k in job.parts:
            k += 1
        if not k:
            return False
    words = [w for i in range(k) for w in job.parts[i]]
    save_json(job.d / "transcript.json", words)
    if k < job.chunks_total:
        save_json(job.d / "trim.json", {"sec": job.cuts[k], "chunks": k, "of": job.chunks_total})
        log(job.id, f"cut short: {len(words)} words from the first {job.cuts[k] / 60:.0f} min ({k}/{job.chunks_total} chunks)")
    else:
        (job.d / "trim.json").unlink(missing_ok=True)
        shutil.rmtree(job.d / "parts", ignore_errors=True)
        log(job.id, f"transcribed {len(words)} words in {job.chunks_total} chunks")
    return True


# ---------------------------------------------------------------- scoring + video

STREAMS, STREAMS_LOCK = {}, threading.Lock()   # src -> (looked up at, [(stream url, headers)])


def stream_urls(src):
    """The video's stream URLs, looked up once per video and shared by all its sections. YouTube answers bursts of
    lookups with "confirm you're not a bot", and every yt-dlp call is a lookup; the stream URLs stay valid for hours."""
    with STREAMS_LOCK:
        hit = STREAMS.get(src)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        info = ytdlp_json(src, ["-f", VIDEO_FMT])
        fmts = [(f["url"], f.get("http_headers") or {}) for f in (info.get("requested_formats") or [info]) if f.get("url")]
        if not fmts:
            raise RuntimeError("no stream URL in yt-dlp's answer")
        STREAMS[src] = (time.time(), fmts)
        return fmts


def section(job, s, e, out):
    """Exact section of the remote video, re-encoded so frame 0 == s (timestamps stay accurate for captions).
    ffmpeg reads the streams directly (input seeking, one lookup per video, see stream_urls); if that fails, yt-dlp
    does the whole thing (--download-sections --force-keyframes-at-cuts), which is what it does internally too."""
    try:
        fmts = stream_urls(job.src)
        cmd = [tool("ffmpeg"), "-y", "-v", "error"]
        for u, h in fmts:
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            if h:
                cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in h.items())]
            cmd += ["-ss", f"{s:.2f}", "-t", f"{e - s:.2f}", "-i", u]
        enc = SECTION_ENCODE[1].split(":", 1)[1].split()
        run(cmd + ["-map", "0:v:0", "-map", f"{len(fmts) - 1}:a:0?", *enc, "-movflags", "+faststart", str(out)])
        return
    except BaseException as ex:   # common.run exits on failure
        if isinstance(ex, KeyboardInterrupt):
            raise
        with STREAMS_LOCK:
            STREAMS.pop(job.src, None)   # maybe expired: the next section looks it up again
        Path(out).unlink(missing_ok=True)
        print(f"direct section download failed, using yt-dlp: {str(ex)[-200:]}", flush=True)
    run(ytdlp_base() + ["-f", VIDEO_FMT, "--download-sections", f"*{s:.2f}-{e:.2f}", "--force-keyframes-at-cuts",
                        *SECTION_ENCODE, "--merge-output-format", "mp4", "-o", str(out), job.src])


def finalize(job, pool):
    a = job.a
    d = job.d
    words = load_json(d / "transcript.json")
    if job.chat_proc is not None:
        job.update(stage="chat")
        t0 = time.time()
        while job.chat_proc.poll() is None and time.time() - t0 < a.chat_wait:
            time.sleep(1)
        if job.chat_proc.poll() is None:
            job.chat_proc.terminate()
            log(job.id, "chat replay still downloading, scoring with the part we have")
        time.sleep(1.5)
    job.update(stage="score")
    db = job.db
    if (d / "trim.json").exists():   # cut short: score only the transcribed part
        db = db[:int(load_json(d / "trim.json")["sec"])]
    dur = float(len(db))
    smin, smax = map(float, a.short.split(":"))
    shorts = moments.score(d, "", job.info, dur, db, words, a.top, smin, smax, None, "candidates")
    sets = {"candidates": shorts}
    if not a.no_long:
        lmin, lmax = map(float, a.long.split(":"))
        sets["candidates_long"] = moments.score(d, "", job.info, dur, db, words, a.top_long, lmin, lmax, None, "candidates_long")
    # video for the picks
    if job.video_thread:
        job.update(stage="video")
        job.video_thread.join()
    if job.full_video and is_url(job.src) and not (d / "source.mp4").exists():
        job.full_video = False
    if job.full_video:
        job.update(stage="video")
        src = str(d / "source.mp4") if (d / "source.mp4").exists() else str(Path(job.src).resolve())
        for name in sets:
            c = load_json(d / f"{name}.json"); c["source"] = src; save_json(d / f"{name}.json", c)
    else:
        secdir = d / "sections"; secdir.mkdir(exist_ok=True)
        todo = [(name, x) for name, xs in sets.items() for x in xs]
        job.update(stage="video", sections_total=len(todo), sections_done=0)
        done = [0]

        def one(item):
            name, x = item
            s, e = max(0.0, x["start"] - a.pad), x["end"] + a.pad
            out = secdir / f"{name}_{x['n']}.mp4"
            if not out.exists():
                tmp = out.with_suffix(".tmp.mp4")
                section(job, s, e, tmp)
                tmp.rename(out)
            x["file"], x["offset"] = str(out), round(s, 2)
            with LOCK:
                done[0] += 1
            job.update(sections_done=done[0])
        list(pool.map(one, todo))
        for name, xs in sets.items():
            c = load_json(d / f"{name}.json"); c["candidates"] = xs; c["source"] = None; save_json(d / f"{name}.json", c)
    job.update(stage="done", finished=time.time())
    log(job.id, f"done: {len(shorts)} short + {len(sets.get('candidates_long', []))} long candidates "
                f"in {time.time() - job.prog['started']:.0f}s")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="*")
    ap.add_argument("--from", dest="from_file")
    ap.add_argument("--max-minutes", type=int, default=90)
    ap.add_argument("--full-under", type=float, default=45, help="download the whole video when shorter than this (min)")
    ap.add_argument("--gpu-workers", type=int, default=1)
    ap.add_argument("--cpu-workers", type=int, default=3)
    ap.add_argument("--threads", type=int, default=4, help="CPU threads per CPU worker")
    ap.add_argument("--downloads", type=int, default=4, help="sources prepared at the same time")
    ap.add_argument("--short", default="15:40"); ap.add_argument("--long", default="60:150")
    ap.add_argument("--top", type=int, default=8); ap.add_argument("--top-long", type=int, default=4)
    ap.add_argument("--no-long", action="store_true")
    ap.add_argument("--pad", type=float, default=3.0, help="seconds of extra video around each section")
    ap.add_argument("--chat-wait", type=float, default=120, help="max seconds to wait for chat replay after transcription")
    ap.add_argument("--control", help="folder of per-source control files (see above)")
    a = ap.parse_args()
    srcs = list(a.src)
    if a.from_file:
        srcs += [l.split()[0] for l in Path(a.from_file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    srcs = list(dict.fromkeys(srcs))
    if not srcs:
        sys.exit("give at least one URL or --from file")

    gpu = "mps" if sys.platform == "darwin" else "cuda"
    ctx = mp.get_context("spawn")
    tasks, results = ctx.Queue(), ctx.Queue()
    procs = [ctx.Process(target=worker, args=(gpu, 0, tasks, results), daemon=True) for _ in range(a.gpu_workers)]
    procs += [ctx.Process(target=worker, args=("cpu", a.threads, tasks, results), daemon=True) for _ in range(a.cpu_workers)]
    for p in procs:
        p.start()
    print(f"{len(srcs)} sources, workers: {a.gpu_workers}x {gpu} + {a.cpu_workers}x cpu ({a.threads} threads)", flush=True)

    jobs = {}
    for s in srcs:
        j = Job(s, a); jobs[j.id] = j; j.update(stage="queued")
    order = {jid: n for n, jid in enumerate(jobs)}
    prep_pool = ThreadPoolExecutor(a.downloads)
    fin_pool = ThreadPoolExecutor(len(jobs))
    sec_pool = ThreadPoolExecutor(6)
    pending = set(jobs)
    finals = []
    prepared = queue.Queue()   # jobs whose chunks are ready to hand out (main thread owns all dispatching)
    ctl = Path(a.control) if a.control else None
    applied = {}
    slots = a.gpu_workers + a.cpu_workers   # chunks out at once: one per worker, so Skip and cut-short act at once
    inflight = 0

    def drop(j):
        with LOCK:
            pending.discard(j.id)

    def prep(j):
        try:
            if prepare(j):
                if j.cancel != "skip":
                    finals.append(fin_pool.submit(fin, j))
            else:
                prepared.put(j)
        except BaseException as e:
            if j.cancel == "skip":
                return
            j.update(error_at=j.prog.get("stage"), stage="error", error=str(e)[-400:]); log(j.id, f"ERROR {str(e)[-300:]}")
            drop(j)

    def fin(j):
        try:
            finalize(j, sec_pool)
        except BaseException as e:
            traceback.print_exc()
            j.update(error_at=j.prog.get("stage"), stage="error", error=str(e)[-400:]); log(j.id, f"ERROR {str(e)[-300:]}")
        finally:
            drop(j)

    def check(j):
        """All of j's chunks that are going to come back are back: transcript, then scoring."""
        if j.backlog or j.inflight or j.prog.get("stage") != "transcribe" or j.cancel == "skip":
            return
        if len(j.parts) == j.chunks_total or j.cancel == "trim":
            if save_transcript(j, trim=len(j.parts) < j.chunks_total):
                j.update(chunks_done=len(j.parts))
                finals.append(fin_pool.submit(fin, j))
            else:
                j.update(stage="skipped", note="cut short before anything was transcribed"); drop(j)

    def control():
        for f in ctl.glob("*") if ctl and ctl.is_dir() else []:
            j = jobs.get(f.name)
            try:
                act = f.read_text().strip()
            except OSError:
                continue
            if not j or applied.get(j.id) == act or j.id not in pending:
                continue
            stage = j.prog.get("stage")
            if act == "skip" and stage in ("queued", "info", "audio", "transcribe"):
                applied[j.id] = act
                j.cancel, j.backlog = "skip", []
                j.update(stage="skipped"); log(j.id, "skipped")
                drop(j)
            elif act == "trim" and stage == "transcribe" and j.cancel is None:
                applied[j.id] = act
                j.cancel, j.backlog = "trim", []
                log(j.id, f"cutting short after {len(j.parts)}/{j.chunks_total} chunks")
                check(j)

    def feed():
        """Hand chunks to the workers, shortest-remaining source first (ties: the order they were given)."""
        nonlocal inflight
        while inflight < slots:
            live = [j for j in jobs.values() if j.backlog and not j.cancel]
            if not live:
                return
            j = min(live, key=lambda j: (len(j.backlog) + j.inflight, order[j.id]))
            tasks.put(j.backlog.pop(0)); j.inflight += 1; inflight += 1

    for j in jobs.values():
        prep_pool.submit(prep, j)

    stats = {}
    try:
        while True:
            with LOCK:
                if not pending:
                    break
            while not prepared.empty():
                j = prepared.get()
                if j.cancel == "skip":
                    j.backlog = []
                else:
                    check(j)   # cut short while it was still downloading
            control()
            feed()
            try:
                r = results.get(timeout=1)
            except queue.Empty:
                continue
            if "note" in r:
                print(r["note"], flush=True); continue
            j = jobs[r["job"]]
            j.inflight -= 1; inflight -= 1
            if j.cancel == "skip":
                continue
            if "error" in r:
                j.update(error_at=j.prog.get("stage"), stage="error", error=r["error"]); log(j.id, f"chunk {r['i']} failed: {r['error']}")
                j.backlog = []
                drop(j)
                continue
            j.parts[r["i"]] = r["words"]
            part_file(j, r["i"]).parent.mkdir(exist_ok=True)
            save_json(part_file(j, r["i"]), r["words"])
            st = stats.setdefault(r["dev"], [0.0, 0.0]); st[0] += r["audio"]; st[1] += r["secs"]
            j.update(chunks_done=len(j.parts))
            check(j)
    except KeyboardInterrupt:
        print("stopping…")
    for f in list(finals):
        f.result()
    for _ in procs:
        tasks.put(None)
    for p in procs:
        p.join(timeout=10)
    for dev, (aud, secs) in stats.items():
        print(f"{dev}: {aud / 60:.1f} min audio, {aud / max(secs, 1e-9):.1f}x real time per worker", flush=True)


if __name__ == "__main__":
    main()
