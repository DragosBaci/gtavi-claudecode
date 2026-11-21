"""Extract clips from YouTube videos: what the editor's Extract page runs when you press Start.

  0. --auto N      Claude chooses N videos from the trending list (jobs/.extract/trends.json) first.
  1. pipeline.py   audio, transcript, scored candidates and their video        (jobs/<id>/progress.json)
  2. Claude        `claude -p` reads each transcript like the moment-finder skill and picks the moments worth
                   posting (start on a line that stands alone, payoff, 1-2 s hook, 3 titles) -> add_picks.py.
                   They show up in the editor as "New from pick". With --no-claude the top scored candidates are used.
  3. cut.py        renders every pick: Split (webcam on top) when the streamer's face is on screen, Fit otherwise.

Every video moves on by itself: as soon as one is transcribed Claude picks it (two at a time) and its picks are
rendered (one at a time), while the others are still transcribing. Per-video control from the page: a file
jobs/.extract/<run>/ctl/<job> saying "skip" (drop it, wherever it is) or "trim" (stop transcribing it and pick from
what is transcribed so far); pipeline.py reads the same folder.

Status for the page: jobs/.extract/<run>/status.json, logs next to it (pipeline.log, choose.log, claude_<job>.log, render.log).
Power (--power): full = GPU + 3 CPU transcription workers (fastest, hot); balanced = GPU + 1 CPU worker; cool = GPU
only and every process at macOS background priority (slow, quiet). A running extraction can be throttled or
unthrottled at any time by writing 1 or 0 to jobs/.extract/<run>/throttle (the Extract page's Run cooler button).

usage: python extract.py [URL ...] [--auto N] [--max-minutes 90] [--picks 5] [--power full|balanced|cool] [--no-claude] [--no-render]
       python extract.py --rerender <run>     render the picks of an earlier run that have no clip yet
       python extract.py --render-queue       work through the Picks page's render queue (see render_queue below)
       python extract.py --rank <job>         Claude ranks one video's picks and marks the weak ones (jobs/<job>/picks_rank.json)
       python extract.py --rank-all <job> ... the same for several videos, three at a time
"""
import argparse, json, os, re, signal, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ROOT, job_dir, job_name, load_json, save_json  # noqa: E402

RUNS = ROOT / "jobs" / ".extract"
PY = sys.executable
CLAUDE_MINUTES = 20   # per video
PICKERS = 2           # transcripts Claude reads at the same time
DONE = ("done", "error", "skipped", "stopped")
PROCS = {}            # job -> its running claude process (so Skip can stop it)
PIPELINE_POWER = {   # pipeline.py transcription workers per power mode
    "full": ["--gpu-workers", "1", "--cpu-workers", "3", "--threads", "4", "--downloads", "4"],
    "balanced": ["--gpu-workers", "1", "--cpu-workers", "1", "--threads", "4", "--downloads", "2"],
    "cool": ["--gpu-workers", "1", "--cpu-workers", "0", "--threads", "2", "--downloads", "2"],
}


def group_pids():
    """This run's processes: extract.py and everything it started (same process group)."""
    me = os.getpgid(0)
    out = subprocess.run(["ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True).stdout
    return [int(a) for a, b in (l.split() for l in out.splitlines() if l.strip()) if int(b) == me]


def throttle_loop(run):
    """Keep every process of the run at macOS background priority while jobs/.extract/<run>/throttle says 1.
    Background priority runs them slowly on the low-power schedule: much cooler, a few times slower."""
    done, was = set(), None
    while True:
        try:
            on = (run.dir / "throttle").read_text().strip() == "1"
        except OSError:
            on = False
        if on != was:
            done.clear()
            if was is not None:
                run.st["throttled"] = on; run.save()
            if not on:   # back to full speed
                for pid in group_pids():
                    subprocess.run(["taskpolicy", "-B", "-p", str(pid)], capture_output=True)
            was = on
        if on:   # new processes (pipeline workers, cut.py, ffmpeg) get throttled as they appear
            for pid in group_pids():
                if pid not in done:
                    subprocess.run(["taskpolicy", "-b", "-p", str(pid)], capture_output=True); done.add(pid)
        time.sleep(5)


def start_throttle(run, cool):
    (run.dir / "throttle").write_text("1" if cool else "0")
    run.st["throttled"] = cool; run.save()
    if sys.platform == "darwin":
        threading.Thread(target=throttle_loop, args=(run,), daemon=True).start()


class Run:
    def __init__(self, urls, a):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.dir = RUNS / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.st = {"run": self.id, "pid": os.getpid(), "started": time.time(), "stage": "starting", "urls": [],
                   "opts": {"max_minutes": a.max_minutes, "picks": a.picks, "claude": not a.no_claude, "render": not a.no_render,
                            "power": a.power, "auto": a.auto},
                   "jobs": []}
        self.add(urls)

    def add(self, urls, why=None):
        with self.lock:
            for u in urls:
                if u not in self.st["urls"]:
                    self.st["urls"].append(u)
                    self.st["jobs"].append({"src": u, "job": job_dir(u).name, "stage": "waiting", "note": "", "picks": [],
                                            "why": (why or {}).get(u, "")})
            self.save()

    def save(self):
        with getattr(self, "lock", threading.RLock()):
            self.st["updated"] = time.time()
            tmp = self.dir / "status.tmp"
            tmp.write_text(json.dumps(self.st, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.dir / "status.json")

    def set(self, j=None, **kw):
        with getattr(self, "lock", threading.RLock()):
            (j if j is not None else self.st).update(kw)
            self.save()


def sh(cmd, log, timeout=None):
    """Run cmd from the clip desk folder, appending its output to log. Returns the exit code."""
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(map(str, cmd))[:400]}\n"); f.flush()
        try:
            return subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            f.write("timed out\n")
            return -1


def prompt(job, info, n):
    py = PY
    return f"""You pick clip moments for a faceless GTA 6 clip page (TikTok, Reels, Shorts). Nobody is watching this run:
don't ask questions, decide.

Video: "{info.get('title')}" by {info.get('channel') or info.get('uploader')} ({info.get('webpage_url')}).
Job folder: jobs/{job}

1. Read tool/skills/moment-finder/SKILL.md (how to pick) and tool/skills/hook-writer/SKILL.md (titles).
2. Run: {py} tool/scripts/compact.py jobs/{job}
   Then read jobs/{job}/compact.txt (in parts if it is long), with jobs/{job}/candidates.md as hints only.
3. Choose at most {n} moments you would actually watch to the end. Fewer is fine; zero is fine if nothing is good.
   The tease (hook) is the culminating 1-2 s, never longer than 3 s, and it sits inside start-end.
   Skip story spoilers, nudity, leaks, chat/donation reads and anything that needs context you can't see.
4. Write them to jobs/{job}/picks_claude.json in the picks.json format from the skill ("job": "jobs/{job}",
   tags C1, C2, ... in order of how good they are), then run:
   {py} tool/scripts/add_picks.py jobs/{job}/picks_claude.json
5. Finish with one line per pick: tag, start-end (seconds), title. Don't render, post or touch other jobs.
"""


def claude_pick(run, j, n):
    d = ROOT / "jobs" / j["job"]
    info = load_json(d / "info.json")
    log = run.dir / f"claude_{j['job']}.log"
    exe = os.environ.get("CLAUDE_BIN") or "claude"
    cmd = [exe, "-p", prompt(j["job"], info, n), "--model", os.environ.get("CLAUDE_MODEL_PICK", "sonnet"),
           "--output-format", "stream-json", "--verbose",
           "--allowedTools", "Read", "Glob", "Grep", f"Edit(jobs/{j['job']}/**)", f"Write(jobs/{j['job']}/**)",
           f"Bash({PY} tool/scripts/compact.py:*)", f"Bash({PY} tool/scripts/add_picks.py:*)"]
    before = {x["n"] for x in (load_json(d / "candidates_audit.json")["candidates"] if (d / "candidates_audit.json").exists() else [])}
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as f:
        try:
            p = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            f.write("claude CLI not found: install Claude Code or set CLAUDE_BIN\n")
            return None
        PROCS[j["job"]] = p
        for line in p.stdout:   # stream-json: keep what a person would want to read
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                f.write(line); f.flush(); continue
            for c in ((ev.get("message") or {}).get("content") or []) if ev.get("type") == "assistant" else []:
                if c.get("type") == "text" and c["text"].strip():
                    f.write("» " + c["text"].strip() + "\n")
                elif c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    what = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                    f.write(f"  {c.get('name')}: {str(what)[:160]}\n")
            if ev.get("type") == "result":
                f.write(f"\n{'done' if not ev.get('is_error') else 'error'} in {time.time() - t0:.0f} s\n")
            f.flush()
            if time.time() - t0 > CLAUDE_MINUTES * 60:
                p.kill(); f.write("stopped: took too long\n"); break
        p.wait()
        PROCS.pop(j["job"], None)
    c = load_json(d / "candidates_audit.json") if (d / "candidates_audit.json").exists() else {"candidates": []}
    return [x for x in c["candidates"] if x["n"] not in before]


def auto_pick(run, j, n):
    """--no-claude: the top scored candidates become editor picks (hook = the loudest moment)."""
    d = ROOT / "jobs" / j["job"]
    cands = load_json(d / "candidates.json")["candidates"][:n]
    picks = [{"job": f"jobs/{j['job']}", "tag": f"A{i}", "start": x["start"], "end": x["end"],
              "tease": [round(x["peak"] - 0.7, 2), round(x["peak"] + 1.3, 2)] if x.get("peak") else None, "title": "", "titles": []}
             for i, x in enumerate(cands, 1)]
    if not picks:
        return []
    pf = d / "picks_auto.json"; pf.write_text(json.dumps(picks))
    before = {x["n"] for x in (load_json(d / "candidates_audit.json")["candidates"] if (d / "candidates_audit.json").exists() else [])}
    sh([PY, str(HERE / "add_picks.py"), str(pf.relative_to(ROOT))], run.dir / "pipeline.log")
    return [x for x in load_json(d / "candidates_audit.json")["candidates"] if x["n"] not in before]


def hook_args(job, n):
    """cut.py takes a 0.4-4 s hook: a longer one becomes its last 2 s (the payoff), a broken one none."""
    c = load_json(ROOT / "jobs" / job / "candidates_audit.json")
    t = next((x.get("tease") for x in c["candidates"] if x["n"] == n), None)
    if not t:
        return ["--no-tease"]
    a, b = float(t[0]), float(t[1])
    if 0.4 <= b - a <= 4:
        return []
    return ["--tease", f"{max(a, b - 2):.2f}:{b:.2f}"] if b - a > 4 else ["--no-tease"]


def render(run, j, pick, log=None):
    """Split layout if the streamer's face is on screen, else Fit. Returns the clip name or None."""
    name = f"pick_{pick['n']}"
    base = [PY, str(HERE / "cut.py"), f"jobs/{j['job']}", "--set", "candidates_audit", "--pick", str(pick["n"]), "--name", name,
            "--title", pick.get("title") or "", *hook_args(j["job"], pick["n"])]
    log = log or run.dir / "render.log"
    if sh(base + ["--layout", "react", "--or-blur"], log) == 0:   # no face: cut.py makes it a Fit clip in the same run
        return name
    # Fit draws its text as the hook box (--title only shows in Split)
    return name if sh(base + ["--layout", "blur"] + (["--hook", pick["title"]] if pick.get("title") else []), log) == 0 else None


def rerender(run_id):
    """Render the picks of an earlier run that have no clip yet (after a failed render)."""
    run = Run.__new__(Run)
    run.id, run.dir = run_id, RUNS / run_id
    run.st = load_json(run.dir / "status.json")
    run.st.update(pid=os.getpid(), stage="rendering"); run.save()
    start_throttle(run, (run.dir / "throttle").exists() and (run.dir / "throttle").read_text().strip() == "1")
    for j in run.st["jobs"]:
        todo = [p for p in j["picks"] if not p.get("clip")]
        if not todo:
            continue
        run.set(j, stage="rendering")
        for p in todo:
            p["clip"] = render(run, j, p)
            p["error"] = None if p["clip"] else "render failed: see render.log"
            run.save()
        run.set(j, stage="done")
    run.set(stage="done", finished=time.time())


CHOOSE = """You choose which trending GTA 6 YouTube videos a faceless GTA 6 clip page (TikTok, Reels, Shorts) should cut
clips from next. Nobody is watching this run: don't ask questions, don't use tools, decide.

Choose {n} of the videos below ({n} at most; fewer only if fewer are worth it). Clips are found by reading the
transcript, and only the first {minutes} min of each video are read.
Good sources: a creator talking or reacting, funny moments, chaos and fails, discoveries, news people argue about,
and trending hard right now (high views per hour). Poor sources: no-commentary walkthroughs or silent gameplay (no
transcript to pick from), music, leaks, story spoilers, and very long videos whose good part may be past {minutes} min.
Prefer a mix of channels over several videos from one channel.

#  | views/h | length | age | channel | title
{rows}

Answer with only a JSON array, best first: [{{"i": <#>, "why": "<reason, under 12 words>"}}, ...]
"""


def choose(run, n, minutes, have):
    """Claude chooses n videos from the trending list (not extracted before, not already in the run).
    Returns ([url], {url: why}). Without Claude: the fastest-growing ones that aren't no-commentary walkthroughs."""
    f = RUNS / "trends.json"
    rows = load_json(f)["rows"] if f.exists() else []
    rows = [r for r in rows if r["url"] not in have and not (ROOT / "jobs" / job_name(r["url"]) / "candidates.json").exists()][:30]
    log = run.dir / "choose.log"
    if not rows:
        log.write_text("no trending video left that hasn't been extracted: refresh the trending list\n")
        return [], {}
    table = "\n".join(f"{i} | {r['views_per_h']} | {r['minutes']} min | {r['age_h']:.0f} h | {r['channel']} | {r['title']}"
                      for i, r in enumerate(rows))
    chosen, why = [], {}
    if run.st["opts"]["claude"]:
        exe = os.environ.get("CLAUDE_BIN") or "claude"
        with open(log, "w", encoding="utf-8") as lf:
            lf.write(f"Claude is choosing {n} of {len(rows)} trending videos…\n"); lf.flush()
            try:
                out = subprocess.run([exe, "-p", CHOOSE.format(n=n, minutes=minutes, rows=table), "--model",
                                      os.environ.get("CLAUDE_MODEL_PICK", "sonnet"), "--output-format", "json"],
                                     cwd=str(ROOT), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=240).stdout
                text = json.loads(out).get("result") or ""
                m = re.search(r"\[.*\]", text, re.S)
                for x in json.loads(m.group(0)) if m else []:
                    i = int(x.get("i", -1))
                    if 0 <= i < len(rows) and rows[i]["url"] not in chosen and len(chosen) < n:
                        chosen.append(rows[i]["url"]); why[rows[i]["url"]] = str(x.get("why") or "")[:120]
                        lf.write(f"» {rows[i]['title'][:70]} · {why[rows[i]['url']]}\n")
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError) as e:
                lf.write(f"Claude couldn't choose ({str(e)[:160]}): taking the fastest-growing videos instead\n")
    if not chosen:
        ok = sorted((r for r in rows if "no commentary" not in r["title"].lower()), key=lambda r: -r["views_per_h"]) or rows
        for r in ok[:n]:
            chosen.append(r["url"]); why[r["url"]] = f"{r['views_per_h']:,} views/h"
        with open(log, "a", encoding="utf-8") as lf:
            lf.write("".join(f"» {r['title'][:70]} · {why[r['url']]}\n" for r in ok[:n]))
    return chosen, why


def pick_job(run, j, a, render_pool, futures):
    """Claude (or the top scores) picks this video's moments, then its picks go to the render queue."""
    if j["stage"] in DONE:
        return
    run.set(j, stage="picking")
    new = claude_pick(run, j, a.picks) if not a.no_claude else auto_pick(run, j, a.picks)
    if j["stage"] in DONE:   # skipped (or stopped) while Claude was reading
        return
    if new is None:
        return run.set(j, stage="error", note="claude CLI not found")
    with run.lock:
        j["picks"] = [{"n": x["n"], "tag": x.get("tag"), "title": x.get("title"), "start": x["start"], "end": x["end"],
                       "clip": None, "error": None} for x in new]
    note = f"{len(new)} moment(s)" if new else "no moment worth clipping"
    if not new or a.no_render:
        return run.set(j, stage="done", note=note)
    run.set(j, stage="picked", note=note)
    futures.append(render_pool.submit(render_job, run, j))


def render_job(run, j):
    if j["stage"] in DONE:
        return
    run.set(j, stage="rendering")
    for p in j["picks"]:
        if j["stage"] in DONE:
            return
        p["clip"] = render(run, j, p)
        p["error"] = None if p["clip"] else "render failed: see render.log"
        run.save()
    run.set(j, stage="done")


def controls(run, applied):
    """The page's per-video buttons: jobs/.extract/<run>/ctl/<job> = skip | trim."""
    for f in (run.dir / "ctl").glob("*"):
        j = next((x for x in run.st["jobs"] if x["job"] == f.name), None)
        try:
            act = f.read_text().strip()
        except OSError:
            continue
        if not j or applied.get(f.name) == act:
            continue
        applied[f.name] = act
        if act == "skip" and j["stage"] not in DONE:
            run.set(j, stage="skipped", note="skipped")
            p = PROCS.get(j["job"])
            if p and p.poll() is None:
                p.kill()
        elif act == "trim" and j["stage"] == "pipeline":
            run.set(j, note="cut short: picking from what's transcribed so far")


def work(run, a):
    jobs = run.st["jobs"]
    (run.dir / "ctl").mkdir(exist_ok=True)
    with open(run.dir / "pipeline.log", "a", encoding="utf-8") as log:
        pipe = subprocess.Popen([PY, str(HERE / "pipeline.py"), *run.st["urls"], "--max-minutes", str(a.max_minutes), "--no-long",
                                 *PIPELINE_POWER[a.power], "--control", str(run.dir / "ctl")],
                                cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    for j in jobs:
        j["stage"] = "pipeline"
    run.set(stage="working")
    pickers, renderer = ThreadPoolExecutor(PICKERS), ThreadPoolExecutor(1)
    futures, applied = [], {}
    while True:
        controls(run, applied)
        ended = pipe.poll() is not None   # before reading progress: a video finished just before exit still counts
        for j in [j for j in jobs if j["stage"] == "pipeline"]:
            d = ROOT / "jobs" / j["job"]
            if not j.get("title") and (d / "info.json").exists():
                try:
                    info = load_json(d / "info.json"); j["title"], j["channel"] = info.get("title"), info.get("channel") or info.get("uploader")
                except ValueError:
                    pass
            try:
                pr = load_json(d / "progress.json")
            except (OSError, ValueError):
                pr = {}
            if pr.get("pid") != pipe.pid:   # still last run's file
                pr = {}
            if pr.get("stage") == "done" and (d / "candidates.json").exists():
                run.set(j, stage="ready")
                futures.append(pickers.submit(pick_job, run, j, a, renderer, futures))
            elif pr.get("stage") == "error":
                run.set(j, stage="error", note=(pr.get("error") or "the pipeline failed: see pipeline.log")[:300])
            elif pr.get("stage") == "skipped":
                run.set(j, stage="skipped", note=j.get("note") or "skipped")
            elif ended:
                run.set(j, stage="error", note="the pipeline stopped before this video was done: see pipeline.log")
        run.save()
        if all(j["stage"] in DONE for j in jobs) and all(f.done() for f in list(futures)):
            break
        time.sleep(2)
    if pipe.poll() is None:   # e.g. a skipped video's download still running
        pipe.terminate()
    for f in futures:
        if f.exception():
            print(f"error: {f.exception()!r}", file=sys.stderr)


CAND_LOCK = threading.Lock()   # parallel downloads update the same candidates_audit.json


def has_video(job, n):
    d = ROOT / "jobs" / job
    c = load_json(d / "candidates_audit.json")
    if c.get("source") and Path(c["source"]).exists():
        return True
    p = next((x for x in c["candidates"] if x["n"] == n), None)
    return bool(p and p.get("file") and (ROOT / p["file"]).exists())


def ensure_video(job, n, pad=2.0):
    """The pick's video is on disk: the job's full source, or a section around the pick (downloaded here if it
    isn't yet, the way add_picks.py does). True when there is video to render from."""
    import pipeline
    from types import SimpleNamespace
    d = ROOT / "jobs" / job
    c = load_json(d / "candidates_audit.json")
    if c.get("source") and Path(c["source"]).exists():
        return True
    p = next(x for x in c["candidates"] if x["n"] == n)
    if p.get("file") and (ROOT / p["file"]).exists():
        return True
    info = load_json(d / "info.json")
    t = p.get("tease") or [p["start"], p["end"]]
    s, e = max(0.0, min(p["start"], t[0]) - pad), max(p["end"], t[1]) + pad
    out = d / "sections" / f"audit_{n}.mp4"
    out.parent.mkdir(exist_ok=True)
    if not out.exists():
        tmp = out.with_suffix(".tmp.mp4")
        for attempt in range(3):
            try:
                pipeline.section(SimpleNamespace(src=info["webpage_url"]), s, e, tmp)
                break
            except BaseException as ex:   # pipeline.run exits on failure; YouTube sometimes answers 403, a retry helps
                print(f"{job} #{n}: download failed (try {attempt + 1}/3): {str(ex)[-200:]}", flush=True)
                time.sleep(3)
        else:
            return False
        tmp.rename(out)
    with CAND_LOCK:
        c = load_json(d / "candidates_audit.json")   # re-read: the editor may have changed it meanwhile
        for x in c["candidates"]:
            if x["n"] == n:
                x["file"], x["offset"] = str(Path("jobs") / job / "sections" / out.name), round(s, 2)
        save_json(d / "candidates_audit.json", c)
    return True


QUEUE = RUNS / "render_queue.jsonl"   # the Picks page appends {"id", "job", "n", "op": render|fetch} or {"cancel": id}
QSTATE = RUNS / "render_state.json"   # this worker: {"pid", "items": {id: {"state", "clip", "error", ...}}}
RENDERS_AT_ONCE = int(os.environ.get("CLIP_RENDERS_AT_ONCE", "3"))   # renders/<job>_<n>.log each (cut.py stage lines)
STAGE_LINE = re.compile(r"^stage: (\w+) t=([\d.]+)")


def render_queue():
    """Render (or only download) the picks queued from the Picks page, oldest first, one at a time, until none is left."""
    st = load_json(QSTATE) if QSTATE.exists() else {"items": {}}
    run = Run.__new__(Run)
    run.id, run.dir, run.st = "renders", RUNS / "renders", {}
    run.dir.mkdir(parents=True, exist_ok=True)
    run.save = lambda: None

    lock = threading.RLock()

    def save():
        with lock:
            st["updated"] = time.time()
            tmp = QSTATE.with_suffix(".tmp"); tmp.write_text(json.dumps(st, indent=1)); os.replace(tmp, QSTATE)

    def fetch(q):
        """Download one pick's video. A download-only item is finished by this; a render item goes back in line."""
        try:
            ok = ensure_video(q["job"], q["n"])
            err = None if ok else "couldn't download the video for this moment"
        except Exception as ex:
            ok, err = False, str(ex)[-300:]
        with lock:
            if not ok:
                st["items"][q["id"]].update(state="failed", error=err, finished=time.time())
            elif q.get("op") == "fetch":
                st["items"][q["id"]].update(state="done", finished=time.time())
            else:
                st["items"].pop(q["id"], None)   # has its video now: rendered on a later pass
        save()

    def render_one(q):
        log = run.dir / f"{q['job']}_{q['n']}.log"
        log.unlink(missing_ok=True)   # one render per log: the page reads its stage lines for the progress bar
        try:
            c = load_json(ROOT / "jobs" / q["job"] / "candidates_audit.json")
            p = next(x for x in c["candidates"] if x["n"] == q["n"])
            name = render(run, {"job": q["job"]}, {"n": q["n"], "title": p.get("title") or ""}, log=log)
            upd = {"state": "done", "clip": name} if name else {"state": "failed", "error": f"render failed: see jobs/.extract/renders/{log.name}"}
        except Exception as ex:
            upd = {"state": "failed", "error": str(ex)[-300:]}
        try:   # how long each stage took (seconds into cut.py), for the next progress bars
            upd["stages"] = {m.group(1): float(m.group(2)) for m in map(STAGE_LINE.match, log.read_text(errors="replace").splitlines()) if m}
        except OSError:
            pass
        with lock:
            st["items"][q["id"]].update(upd, finished=time.time())
        save()

    # work a previous worker left half done (it was stopped or crashed) is done again
    st["items"] = {k: v for k, v in st["items"].items() if v.get("state") not in ("rendering", "fetching")}
    st["pid"] = os.getpid(); save()
    fetchers, renderers = ThreadPoolExecutor(3), ThreadPoolExecutor(RENDERS_AT_ONCE)
    busy = {}   # id -> future
    try:
        while True:
            for k in [k for k, f in busy.items() if f.done()]:
                busy.pop(k)
            lines = [json.loads(l) for l in QUEUE.read_text().splitlines() if l.strip()] if QUEUE.exists() else []
            cancelled = {l["cancel"] for l in lines if "cancel" in l}
            with lock:
                todo = [l for l in lines if "id" in l and l["id"] not in st["items"] and l["id"] not in cancelled and l["id"] not in busy]
            if not todo and not busy:
                break
            started = False
            for q in todo:
                if not has_video(q["job"], q["n"]):   # download first (each video looked up once: pipeline.stream_urls)
                    with lock:
                        st["items"][q["id"]] = {"state": "fetching", "started": time.time()}
                    busy[q["id"]] = fetchers.submit(fetch, q); started = True
                elif q.get("op") == "fetch":
                    with lock:
                        st["items"][q["id"]] = {"state": "done", "started": time.time(), "finished": time.time()}
                    started = True
                elif sum(1 for k in busy if (st["items"].get(k) or {}).get("state") == "rendering") < RENDERS_AT_ONCE:
                    with lock:
                        st["items"][q["id"]] = {"state": "rendering", "started": time.time()}
                    busy[q["id"]] = renderers.submit(render_one, q); started = True
            if started:
                save()
            time.sleep(1)
    finally:
        st["pid"] = None; save()


RANK = """You judge clip picks for a faceless GTA 6 clip page (TikTok, Reels, Shorts). Nobody is watching this run:
don't ask questions, don't use tools, decide.

Video: "{title}" by {channel}.
Below are the moments picked from it. For each: its tag, length, the title it would be posted with, and what is said
(the transcript of the moment; the finished clip opens with the "hook" line, then plays the moment from the start).

Rank them all, best first, as clips a stranger scrolling would watch to the end and share. Judge:
- the first 2 seconds: does the hook grab without context?
- a real payoff (a joke landing, a reaction, chaos, a reveal), not a setup that fizzles
- understandable on its own; no story spoilers, leaks, chat/donation reads, nothing that needs the rest of the video
- the title: specific and curious, not generic
- near-duplicates: if two picks are the same moment or the same joke, keep the better one and drop the other
Verdict per pick: "keep" (worth rendering), "maybe" (fine if there's room), "drop" (not worth posting: say why).
Be honest: dropping half of them is fine when they're weak.

{picks}

Answer with only a JSON array, best first, one entry per pick:
[{{"n": <n>, "verdict": "keep"|"maybe"|"drop", "why": "<under 15 words>"}}, ...]
"""


def rank(job):
    """Claude ranks a video's picks (not the hidden ones) -> jobs/<job>/picks_rank.json:
    {"state": "running"|"done"|"failed", "pid", "at", "ranks": {n: {"rank", "verdict", "why"}}, "error"}."""
    d = ROOT / "jobs" / job
    out = d / "picks_rank.json"
    prev = load_json(out) if out.exists() else {}
    save_json(out, {**prev, "state": "running", "pid": os.getpid(), "started": time.time()})
    try:
        info, c = load_json(d / "info.json"), load_json(d / "candidates_audit.json")
        hidden = set(load_json(d / "picks_hidden.json")) if (d / "picks_hidden.json").exists() else set()
        words = load_json(d / "transcript.json") if (d / "transcript.json").exists() else []
        words = words if isinstance(words, list) else words.get("words", [])
        ps = [p for p in c["candidates"] if p["n"] not in hidden]
        if not ps:
            raise ValueError("no picks to rank")
        blocks = []
        for p in ps:
            t = p.get("tease")
            a, b = min(p["start"], t[0]) if t else p["start"], max(p["end"], t[1]) if t else p["end"]
            said = " ".join(w["w"] for w in words if a <= w["s"] <= b)[:1400] or p.get("text") or ""
            hook = " ".join(w["w"] for w in words if t and t[0] <= w["s"] <= t[1])
            blocks.append(f"n={p['n']} · {p.get('tag') or ''} · {round(p['end'] - p['start'])} s · title: {p.get('title') or '(none)'}\n"
                          f"hook: {hook or '(no words)'}\nsaid: {said}")
        prompt = RANK.format(title=info.get("title"), channel=info.get("channel") or info.get("uploader"), picks="\n\n".join(blocks))
        exe = os.environ.get("CLAUDE_BIN") or "claude"
        r = subprocess.run([exe, "-p", prompt, "--model", os.environ.get("CLAUDE_MODEL_PICK", "sonnet"), "--output-format", "json"],
                           cwd=str(ROOT), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        text = json.loads(r.stdout).get("result") or ""
        m = re.search(r"\[.*\]", text, re.S)
        known = {p["n"] for p in ps}
        ranks, k = {}, 0
        for x in json.loads(m.group(0)) if m else []:
            n = int(x.get("n", -1))
            if n in known and n not in ranks:
                k += 1
                v = x.get("verdict") if x.get("verdict") in ("keep", "maybe", "drop") else "maybe"
                ranks[n] = {"rank": k, "verdict": v, "why": str(x.get("why") or "")[:160]}
        if not ranks:
            raise ValueError("Claude's answer had no ranking in it")
        save_json(out, {"state": "done", "at": time.time(), "ranks": ranks, "of": len(ps)})
    except Exception as e:
        save_json(out, {"state": "failed", "at": time.time(), "error": str(e)[-300:], "ranks": prev.get("ranks")})


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--rank":
        return rank(sys.argv[2])
    if len(sys.argv) >= 3 and sys.argv[1] == "--rank-all":
        with ThreadPoolExecutor(3) as ex:
            list(ex.map(rank, sys.argv[2:]))
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--rerender":
        return rerender(sys.argv[2])
    if len(sys.argv) == 2 and sys.argv[1] == "--render-queue":
        return render_queue()
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--auto", type=int, default=0, help="Claude chooses this many videos from the trending list")
    ap.add_argument("--max-minutes", type=int, default=90, help="scan at most this much of each video")
    ap.add_argument("--picks", type=int, default=5, help="moments per video, at most")
    ap.add_argument("--no-claude", action="store_true", help="use the scored candidates instead of Claude's picks")
    ap.add_argument("--no-render", action="store_true", help="only pick; render later in the editor")
    ap.add_argument("--power", choices=list(PIPELINE_POWER), default="full", help="how hard to run the machine (see above)")
    a = ap.parse_args()
    if not a.urls and not a.auto:
        ap.error("give video links or --auto N")
    run = Run(a.urls, a)
    start_throttle(run, a.power == "cool")

    def stop(*_):
        with run.lock:
            run.st["stage"] = "stopped"
            for j in run.st["jobs"]:
                if j["stage"] not in DONE:
                    j["stage"] = "stopped"
            run.save()
        os._exit(1)
    signal.signal(signal.SIGTERM, stop)

    if a.auto:
        run.set(stage="choosing")
        urls, why = choose(run, a.auto, a.max_minutes, set(a.urls))
        run.add(urls, why)
        if not run.st["jobs"]:
            return run.set(stage="error", note="no trending video to choose from: refresh the trending list", finished=time.time())
    work(run, a)
    run.set(stage="done", finished=time.time())


if __name__ == "__main__":
    main()
