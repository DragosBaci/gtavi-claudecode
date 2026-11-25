"""Post one clip right now (the Queue page's "Post now" button).

Starts a headless Claude with Claude in Chrome (`claude -p --chrome`) that follows the plugin's skills/upload-next for
this one clip: frame check, upload copy, then YouTube, TikTok and Instagram in new Chrome tabs, links recorded in
queue/state.json. Every safety rule of the skill applies (right account, no passwords, no CAPTCHAs, stop on
copyright flags). Permissions for this run only: Chrome on those three sites, scripts/uploader.py, frame grabs.

Status for the page: queue/uploads/postnow.json, log: queue/uploads/postnow.log.
usage: python scripts/post_now.py <job>/<clip name> [--only youtube,tiktok]      (from the clip desk folder)
"""
import json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import uploader as U  # noqa: E402

ROOT = U.ROOT
SKILL = HERE.parent / "skills" / "upload-next" / "SKILL.md"

STATUS, LOG, SETTINGS = U.UP / "postnow.json", U.UP / "postnow.log", U.UP / "postnow.settings.json"
MINUTES = 25


def status(**kw):
    cur = U.jload(STATUS, {}); cur.update(kw); U.jsave(STATUS, cur)


def main():
    cid = sys.argv[1]
    only = []   # --only youtube,tiktok: post on these platforms and leave the others alone
    if "--only" in sys.argv:
        only = [p for p in sys.argv[sys.argv.index("--only") + 1].split(",") if p in ("youtube", "tiktok", "instagram")]
    names = {"youtube": "YouTube Shorts", "tiktok": "TikTok", "instagram": "Instagram Reels"}
    where = ", ".join(names[p] for p in (only or names))
    it = U.item(cid)
    status(state="running", id=cid, title=it["title"], started=time.time(), pid=os.getpid(), finished=None, error=None, only=only)
    acct = U.jload(U.ACCOUNT, None)
    if not (acct or {}).get("name"):
        status(state="error", error="Set the account you post as first (Posting as, at the top of the Queue page)", finished=time.time())
        return
    U.log(event="post_now", id=cid)
    py = sys.executable if Path(sys.executable).name.lower().startswith("python") else "python3"
    upl = f"{py} {HERE / 'uploader.py'}"
    U.jsave(SETTINGS, {"permissions": {"allow": [
        "ClaudeInChromeDomain(www.youtube.com)", "ClaudeInChromeDomain(studio.youtube.com)",
        "ClaudeInChromeDomain(www.tiktok.com)", "ClaudeInChromeDomain(www.instagram.com)",
        # paid campaigns this clip qualifies for: its links are submitted right after posting (skill step 5b)
        "ClaudeInChromeDomain(contentrewards.com)", "ClaudeInChromeDomain(www.contentrewards.com)",
        "ClaudeInChromeDomain(whop.com)", "ClaudeInChromeDomain(www.whop.com)", "ClaudeInChromeDomain(www.clip.tech)",
        "mcp__claude-in-chrome", "Read", "Glob",
        f"Bash({upl}:*)", "Bash(ffmpeg -v error -y -ss:*)",
        # the same uploader by its short paths: the headless run sometimes types it that way, and a prompt nobody answers stops it
        "Bash(.venv/bin/python tool/scripts/uploader.py:*)", f"Bash({py} tool/scripts/uploader.py:*)", "Bash(python3 tool/scripts/uploader.py:*)",
    ]}})
    prompt = f"""Post this clip now: {cid} ("{it['title']}").
The human pressed "Post now" on the Queue page for exactly this clip ({cid}). That click is their approval to publish
THIS clip only. Never choose a clip from `status` or the queue order, never post any other clip: the uploader refuses
every other id during this run. Publish it:
go all the way and actually publish it on every pending platform among: {where}.{' Post ONLY there: do not open or post to any other platform, even if it is pending.' if only else ''}
Don't save drafts, don't schedule, don't stop to ask; nobody is watching the run.

Read {SKILL} and follow it for this one clip, skipping steps 1 and 2 (no timing check,
no ranking: this clip was chosen). In it, UPL means exactly `{upl}`. Post only as the account
{acct['name']!r} (handles: {', '.join(acct.get('handles') or []) or 'none given'}). Then:
- Step 3, frame check: only stop if the video is broken (black, frozen or corrupted frames, a missing panel) or shows
  something the skill forbids (nudity, gore, personal info, another clipper's watermark, leaks). Then run
  `{upl} fail {cid} all --reason "..."` and stop. Taste is not a reason to stop: the human chose it.
- Step 4: prepare.
- Step 5: on each pending platform, upload, fill the text exactly, and click the final button (YouTube: Publish with
  Visibility Public; TikTok: Post; Instagram: Share). A plain "Continue to post?" / "Post now" confirmation dialog:
  confirm it. Wait until the platform shows it's published, record it with `{upl} mark ...`, then
  move on. Skip a platform only for the skill's hard stops: wrong account, logged out, CAPTCHA or verification code
  (never type a password or solve a CAPTCHA), or an actual copyright claim / block shown by the platform. A failed
  upload or a page that didn't load: retry that platform once before skipping it.
- Links: copy each post's link from the page text (read_page, find or javascript on the share/link field), never by
  reading it off a screenshot: I and l, O and 0 look alike there and one wrong letter makes the link dead.
- Step 5b: if `mark` printed "to submit", submit those campaign links (campaign-submit skill, its hard stops apply).
  Campaigns only take a post within 30 minutes of publishing. If one answers that it can't read a brand-new Short yet,
  wait 2 minutes and try again, up to 4 times, before giving up.
- Step 6: finish and close the tabs you opened.
Notifications aren't available here: put anything the human must know in your final reply instead.
End with one line per platform: posted + link, or skipped + why.
"""
    exe = os.environ.get("CLAUDE_BIN") or "claude"
    model = os.environ.get("CLAUDE_MODEL_POST", "sonnet")   # Sonnet: lighter on the plan's usage limits than Opus
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
    st = U.jload(U.STATE, {}).get(cid, {})
    posted = [k for k in ("youtube", "tiktok", "instagram") if st.get(k, {}).get("posted")]
    status(state="done", finished=time.time(), result=final[-1500:], posted=posted)


if __name__ == "__main__":
    main()
