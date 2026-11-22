"""Submit posted clips to the paid campaigns they qualify for, now (the Earn page's "Submit with Claude" button).

Starts a headless Claude with Claude in Chrome (`claude -p --chrome`) that follows the plugin's skills/campaign-submit
for every to_submit entry in queue/submissions.json: it opens each campaign's page signed in as you, pastes the post
link and submits it, and records the result. It never joins a campaign, accepts terms, or touches payout settings.
Permissions for this run only: Chrome on the campaign sites, scripts/uploader.py.

Status for the page: queue/uploads/submit.json, log: queue/uploads/submit.log.
usage: python scripts/submit_now.py      (from the clip desk folder)
"""
import json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import uploader as U  # noqa: E402
import campaigns as C  # noqa: E402

ROOT = U.ROOT
SKILL = HERE.parent / "skills" / "campaign-submit" / "SKILL.md"
STATUS, LOG, SETTINGS = U.UP / "submit.json", U.UP / "submit.log", U.UP / "submit.settings.json"
MINUTES = 25
SITES = ["contentrewards.com", "www.contentrewards.com", "whop.com", "www.whop.com", "clip.tech", "www.clip.tech"]


def status(**kw):
    cur = U.jload(STATUS, {}); cur.update(kw); U.jsave(STATUS, cur)


def main():
    todo = C.pending_submissions()
    status(state="running", started=time.time(), pid=os.getpid(), finished=None, error=None, count=len(todo))
    if not todo:
        status(state="done", finished=time.time(), result="nothing to submit"); return
    U.log(event="submit_now", count=len(todo))
    py = sys.executable if Path(sys.executable).name.lower().startswith("python") else "python3"
    upl = f"{py} {HERE / 'uploader.py'}"
    U.jsave(SETTINGS, {"permissions": {"allow": [*(f"ClaudeInChromeDomain({d})" for d in SITES), "mcp__claude-in-chrome", "Read",
                                                  f"Bash({upl}:*)"]}})
    prompt = f"""Submit the posted clips to their paid campaigns now. The human pressed "Submit with Claude" on the Earn page:
that is their approval to submit every pending entry. Nobody is watching the run: don't stop to ask.

Read {SKILL} and follow it. In it, UPL means exactly `{upl}`. There are {len(todo)} entries to submit
(`{upl} submissions` lists them). Every hard stop in the skill applies: never join a campaign, accept terms, sign in,
solve a CAPTCHA or open payout settings; put what the human must do in your final reply.
End with one line per entry: campaign, platform, submitted / skipped / needs you + why.
"""
    exe = os.environ.get("CLAUDE_BIN") or "claude"
    model = os.environ.get("CLAUDE_MODEL_POST", "sonnet")
    cmd = [exe, "-p", prompt, "--model", model, "--chrome", "--settings", str(SETTINGS), "--output-format", "stream-json", "--verbose"]
    t0, final = time.time(), ""
    with open(LOG, "w", encoding="utf-8") as f:
        try:
            p = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            status(state="error", error="claude CLI not found", finished=time.time()); return
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                f.write(line); f.flush(); continue
            if ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "text" and c["text"].strip():
                        f.write("» " + c["text"].strip() + "\n")
                    elif c.get("type") == "tool_use":
                        inp = c.get("input") or {}
                        name = str(c.get("name", "")).replace("mcp__claude-in-chrome__", "chrome ")
                        what = inp.get("url") or inp.get("command") or inp.get("action") or inp.get("query") or inp.get("file_path") or ""
                        f.write(f"  {name}: {str(what)[:140]}\n")
            elif ev.get("type") == "result":
                final = ev.get("result") or ""
                f.write(f"\n{'done' if not ev.get('is_error') else 'error'} in {time.time() - t0:.0f} s\n")
            f.flush()
            if time.time() - t0 > MINUTES * 60:
                p.kill(); f.write("stopped: took too long\n"); break
        p.wait()
    left = len(C.pending_submissions())
    status(state="done", finished=time.time(), result=final[-1500:], submitted=len(todo) - left, left=left)


if __name__ == "__main__":
    main()
