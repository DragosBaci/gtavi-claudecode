"""Extract clips from YouTube videos: what the editor's Extract page runs when you press Start.

  1. pipeline.py   audio, transcript, scored candidates and their video        (jobs/<id>/progress.json)
  2. Claude        `claude -p` reads each transcript like the moment-finder skill and picks the moments worth
                   posting (start on a line that stands alone, payoff, 1-2 s hook, 3 titles) -> add_picks.py.
                   They show up in the editor as "New from pick". With --no-claude the top scored candidates are used.
  3. cut.py        renders every pick: Split (webcam on top) when the streamer's face is on screen, Fit otherwise.

Status for the page: jobs/.extract/<run>/status.json, logs next to it (pipeline.log, claude_<job>.log, render.log).
Power (--power): full = GPU + 3 CPU transcription workers (fastest, hot); balanced = GPU + 1 CPU worker; cool = GPU
only and every process at macOS background priority (slow, quiet). A running extraction can be throttled or
unthrottled at any time by writing 1 or 0 to jobs/.extract/<run>/throttle (the Extract page's Run cooler button).

usage: python extract.py URL [URL ...] [--max-minutes 90] [--picks 5] [--power full|balanced|cool] [--no-claude] [--no-render]
       python extract.py --rerender <run>     render the picks of an earlier run that have no clip yet
"""
import argparse, json, os, re, signal, subprocess, sys, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import ROOT, job_dir, load_json, save_json  # noqa: E402

RUNS = ROOT / "jobs" / ".extract"
PY = sys.executable
CLAUDE_MINUTES = 20   # per video
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
        self.st = {"run": self.id, "pid": os.getpid(), "started": time.time(), "stage": "starting", "urls": urls,
                   "opts": {"max_minutes": a.max_minutes, "picks": a.picks, "claude": not a.no_claude, "render": not a.no_render,
                            "power": a.power},
                   "jobs": [{"src": u, "job": job_dir(u).name, "stage": "waiting", "note": "", "picks": []} for u in urls]}
        self.save()

    def save(self):
        self.st["updated"] = time.time()
        tmp = self.dir / "status.tmp"
        tmp.write_text(json.dumps(self.st, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.dir / "status.json")

    def set(self, j=None, **kw):
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


def render(run, j, pick):
    """Split layout if the streamer's face is on screen, else Fit. Returns the clip name or None."""
    name = f"pick_{pick['n']}"
    base = [PY, str(HERE / "cut.py"), f"jobs/{j['job']}", "--set", "candidates_audit", "--pick", str(pick["n"]), "--name", name,
            "--title", pick.get("title") or "", *hook_args(j["job"], pick["n"])]
    log = run.dir / "render.log"
    if sh(base + ["--layout", "react"], log) == 0:
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


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--rerender":
        return rerender(sys.argv[2])
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--max-minutes", type=int, default=90, help="scan at most this much of each video")
    ap.add_argument("--picks", type=int, default=5, help="moments per video, at most")
    ap.add_argument("--no-claude", action="store_true", help="use the scored candidates instead of Claude's picks")
    ap.add_argument("--no-render", action="store_true", help="only pick; render later in the editor")
    ap.add_argument("--power", choices=list(PIPELINE_POWER), default="full", help="how hard to run the machine (see above)")
    a = ap.parse_args()
    run = Run(a.urls, a)
    start_throttle(run, a.power == "cool")

    def stop(*_):
        run.set(stage="stopped")
        for j in run.st["jobs"]:
            if j["stage"] not in ("done", "error"):
                j["stage"] = "stopped"
        run.save(); sys.exit(1)
    signal.signal(signal.SIGTERM, stop)

    run.set(stage="pipeline")
    for j in run.st["jobs"]:
        j["stage"] = "pipeline"
    run.save()
    sh([PY, str(HERE / "pipeline.py"), *a.urls, "--max-minutes", str(a.max_minutes), "--no-long", *PIPELINE_POWER[a.power]],
       run.dir / "pipeline.log")
    for j in run.st["jobs"]:
        d = ROOT / "jobs" / j["job"]
        info = load_json(d / "info.json") if (d / "info.json").exists() else {}
        j["title"], j["channel"] = info.get("title"), info.get("channel") or info.get("uploader")
        pr = load_json(d / "progress.json") if (d / "progress.json").exists() else {}
        if not (d / "candidates.json").exists() or pr.get("stage") == "error":
            j["stage"], j["note"] = "error", (pr.get("error") or "the pipeline failed: see pipeline.log")[:300]
    run.save()

    for j in [j for j in run.st["jobs"] if j["stage"] != "error"]:
        run.set(stage="picking"); run.set(j, stage="picking")
        new = claude_pick(run, j, a.picks) if not a.no_claude else auto_pick(run, j, a.picks)
        if new is None:
            run.set(j, stage="error", note="claude CLI not found"); continue
        j["picks"] = [{"n": x["n"], "tag": x.get("tag"), "title": x.get("title"), "start": x["start"], "end": x["end"],
                       "clip": None, "error": None} for x in new]
        run.set(j, stage="picked", note=f"{len(new)} moment(s)" if new else "no moment worth clipping")

    if not a.no_render:
        run.set(stage="rendering")
        for j in [j for j in run.st["jobs"] if j["picks"]]:
            run.set(j, stage="rendering")
            for p in j["picks"]:
                p["clip"] = render(run, j, p)
                p["error"] = None if p["clip"] else "render failed: see render.log"
                run.save()
            run.set(j, stage="done")
    for j in run.st["jobs"]:
        if j["stage"] in ("picked", "pipeline", "picking"):
            j["stage"] = "done"
    run.set(stage="done", finished=time.time())


if __name__ == "__main__":
    main()
