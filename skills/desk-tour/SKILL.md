---
name: desk-tour
description: Set up and start the Clip Desk (the browser app on http://127.0.0.1:8766), then walk the user through it page by page (Make, Editor, Post, Live) while they make their first clip. Use when the user says "show me around", "how do I use this", "walk me through the app", "tour", "start the desk", "open the editor", or is new to the app. Also use when the desk won't start, a page says "not built yet", or Extract or Render fails because a tool is missing.
---

# Clip Desk tour

Two parts: **set up** (you do all of it, no questions), then **the tour** (one page per message, the user clicks, you explain).
The user should end with one finished clip they made, and should know which page to open for each job.

## 0. Paths

- **TOOL**: `${CLAUDE_PLUGIN_ROOT}` when it is set. Otherwise the folder that holds `scripts/editor.py` (usually `./tool`, or `.` when the user is inside the plugin repo).
- **DESK**: the folder that holds `jobs/`. Every script uses the current directory as the desk, so **run all commands from DESK**. If there is no `jobs/` anywhere nearby, DESK is the current directory: create `jobs/` there.
- **PY**: `DESK/.venv/bin/python` (Windows: `DESK\.venv\Scripts\python.exe`).

## 1. Set up (no questions, install only what is missing)

Check everything first in one pass, then fix what failed. Long installs (pip with torch, npm) go in the background. Tell the user in one line what is installing and roughly how long it takes.

| Check | Fix |
|---|---|
| `python3 --version` is 3.10+ | macOS `brew install python`, Windows `winget install -e --id Python.Python.3.12`, Linux `sudo apt install -y python3 python3-venv` |
| `PY` exists | `python3 -m venv .venv` |
| `PY -c "import yt_dlp, faster_whisper, numpy, cv2, torch, transformers"` | `PY -m pip install -U -r TOOL/requirements.txt` (torch is about 1-2 GB, 3-10 min) |
| ffmpeg can burn captions: `ffmpeg -hide_banner -filters` lists `subtitles`, or `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` exists | macOS `brew install ffmpeg-full` (the scripts find it by themselves), Windows `winget install --id Gyan.FFmpeg -e`, Linux `sudo apt install -y ffmpeg` |
| `node --version` is 20+ and `npm` exists | macOS `brew install node`, Windows `winget install -e --id OpenJS.NodeJS.LTS`, Linux: nodejs from NodeSource |
| UI is built: `TOOL/web/out/index.html` exists and `find TOOL/web/app TOOL/web/components TOOL/web/lib -newer TOOL/web/out/index.html` prints nothing | `cd TOOL/web && npm install && npm run build` |
| `claude --version` works | Needed to make clips (Claude picks, looks at and checks every clip), for Post now and for `upload-next`. Without it only Live and the editor work. If missing, point to https://claude.com/claude-code; don't install it yourself. |
| Chrome with the Claude in Chrome extension | Only for posting (Post now, `upload-next`). Mention it at the Queue stop; nothing to install now. |
| `DESK/fillers/*.mp4` | Optional: the user's own background gameplay videos for the Split-with-filler layout. Nothing ships with the plugin. |

If a new install needs a fresh terminal for PATH, say so in one line and re-check. Never spend money, sign in to anything or add a card.

## 2. Start the desk

1. **Already running?** `curl -s -m 2 http://127.0.0.1:8766/api/health` returns `python` (the interpreter the desk starts every job with), `missing` (packages it can't import) and `ui_built`.
   An older desk without `/api/health` (404) predates these fixes: restart it.
   If `missing` isn't empty, Extract, Render and Live will fail. Tell the user and offer to restart it with `PY`. If `ui_built` is false, build the UI (part 1), then reload the page. Ask before a restart if `/api/live` shows a watch running or `/api/extract` an active run. Don't judge the interpreter from `ps`: on macOS a venv's python shows up as the system binary.
2. **Not running:** start it in the background from DESK: `PY TOOL/scripts/editor.py --port 8766`.
   Wait for it: `curl -s --retry 15 --retry-connrefused --retry-delay 1 http://127.0.0.1:8766/api/health`, and check `missing` is empty.
   If it exits with "no jobs/ folder", you are in the wrong directory.
3. **Open it:** macOS `open http://127.0.0.1:8766`, Windows `start http://127.0.0.1:8766`, Linux `xdg-open http://127.0.0.1:8766`.

Finish part 1 with **3 lines**: what you installed (or "all set already"), the address, and "Say **next** when the page is open."

## 3. The tour

Before the first stop, read `GET /api/extract` (runs) and `GET /api/queue` (items) and tailor the tour to it:
- **No clips yet**: do the stops in order. The Make run is the user's first clip.
- **Clips already exist**: start a Make run (stop 1), then use the newest clip in the queue for the Editor stop while it works.

**Format for every stop.** One message: the page's address, what it's for (1 line), the 3-5 controls that matter (use the exact labels below), and **one small thing to do now**. End with "Say **next**", or answer what they ask.
Don't paste the whole tour at once. Skip a stop when the user says they know it.

**What you may do yourself:** read `/api/*` to see what happened.
**What only the user does:** start a run or a live watch, Render, Post now, and Remove. Each of these runs real work or posts publicly. Explain the button; let them press it.

The nav is the same on every page: **Make · Post · Earn · Live**. The editor opens from any clip.

### Stop 1. Make, `/`: clips from YouTube videos

- Paste one or more YouTube links, or pick from the trending GTA 6 list (or **Claude chooses** N of them).
- **Start**. From there it runs by itself: transcribe, Claude picks moments and looks at their frames, only the ones it would bet on are rendered, each finished clip is checked once more and goes straight into the posting queue.
- While it runs: progress per video, **Skip** on a video, **❄ Run cooler**, **■ Stop**.
- **Moments** (below): every moment Claude picked, with its verdict (keep / maybe / drop and why). Keeps render by themselves; tick a *maybe* and **Render** it if you want it too. **▶ Watch** plays them; **Hide** clears the list.

Do now: pick one trending video under 20 minutes and press **Start**. The page can be closed and the run keeps going, so carry on with the tour. Check `GET /api/extract` when the user comes back.

### Stop 2. Editor, `/edit?job=<job>&name=<clip name>`: fix a clip

Open the newest clip (the first item of `GET /api/queue`, or a moment's **Open clip**) and give the user that exact URL.

- **Transcript**: drag across words, then ✂ Cut (Delete), Restore, Set as hook (**H**), Start/End here (**[** **]**), Keep silent (**K**) for a moment with no talking (a jump, a reveal).
- **Preview** (9:16): the layout, title and captions, drawn live without rendering. **Hook first** on/off is **Shift+H**.
- **Side panel**: *Title*, *Layout* (Split, Focus, Fill, Fit; *Crop webcam* when auto-detect picked the wrong face), *Trim and hook*, *Export*. The look (captions, title box, zoom into the clip) is the same on every clip.
- **Timeline** at the bottom: drag the clip edges and the hook, Shift-drag selects a range, **⌘Z / Ctrl+Z** undoes, Space plays.
- **Render**: when it's done the clip is in the posting queue and the Post page opens on it. Keep the same file name to replace the clip there.

Do now: the user changes the title or cuts one dead line, then presses **Render**.

### Stop 3. Post, `/queue`: what gets posted

- **Posting as** (top bar): the only account Claude may post as. Post now and `upload-next` stop if YouTube, TikTok or Instagram is signed in to any other account. If it says **⚠ Set the account you post as**, ask the user for the page name and handle, and save them there (or `PY TOOL/scripts/uploader.py account --name "<name>" --handle <handle>`).
- Left: **To post** (every clip Claude kept and checked, and every clip rendered in the editor), **Posted**, **On hold**.
- Select a clip to watch it, and edit its caption per platform (character limits shown, **Copy** buttons). The text is saved for the auto-uploader too.
- **▶ Post next** moves the clip to the front of the line (nothing is posted yet). **Remove** takes it out for good.
- **🚀 Post now** (press twice to confirm): Claude posts the clip in Chrome on YouTube Shorts, TikTok and Instagram. These are real public posts, on the accounts signed in to Chrome. **■ Stop** halts it. Before the first post, the user signs in to all three in Chrome (with Claude in Chrome installed). Claude never types a password or solves a CAPTCHA.
- Mark a clip posted by hand: paste the **post link (optional)**.

Do now: set **Posting as** if it's empty, and read the captions of the clip from stop 2. Don't post during the tour unless the user says to.

### Stop 3b. Earn, `/earn`: paid campaigns

- The campaigns `campaign-finder` found, with their rate, budget and how many of your clips already qualify.
- Join one on its own page (only the user does this), then **I joined it**, paste its rules (Claude reads them into settings) and add its overlay PNG if it needs one.
- From then on its creators' clips get the overlay, go out first, and their links are submitted after posting (**Submit with Claude** does the pending ones). Views, estimated and paid money show at the top; record real payouts at the bottom.

Do now: open the campaign the user is most likely to join and read its rate and rules together.

### Stop 4. Live, `/live`: clip streams while they happen

- **Top GTA 6 streams on Twitch right now**: pick up to 9, or paste Twitch / YouTube live URLs under *Other streams*.
- **Auto-clip sensitivity** and **Watch for** (how long), then **Watch N streams**.
- Split screen: every stream plays side by side with its chat-rate graph. A clip is cut when chat crosses the dashed line (about 3× the usual number of different people chatting).
- **✂ Clip now** (keys **1-9**) clips the last ~20 s of that stream. New clips land in the feed on the right with an **Edit** link that opens them with the moment as the hook; render one and it joins the posting queue.
- **＋ Add stream**, **＋1 h** and Stop work while it runs. Streams that drop reconnect by themselves.

Do now: only if the user wants to. A watch streams every channel the whole time (bandwidth and CPU), but it keeps only a rolling 60 s buffer and saves nothing except the clips. Otherwise explain it and finish.

## 4. Finish

One short message:
- **Cheat sheet:** Make = clips from videos (picked, checked and queued by Claude) · Post = what gets posted · Earn = paid campaigns and money · Live = clips from streams · Editor = fix and render. Editor keys: Delete cut, H hook, [ ] start/end, K keep silent, Shift+H hook on/off, Space play, ⌘Z undo.
- **Where files are:** `jobs/<job>/edits/<name>.mp4` (finished clips; the `.json` next to each one reopens it exactly as it was made).
- **Restart later:** `cd DESK && PY TOOL/scripts/editor.py`, then open http://127.0.0.1:8766.
- **Automatic posting:** `/loop 2h /gtavi-claudecode:upload-next` posts the best queued clip every 2 hours (chooses the best one, uploads, records the links). It needs Claude in Chrome, the three platforms signed in, and **Posting as** set. Each run asks for permissions the first time; offer to add `ClaudeInChromeDomain(www.youtube.com)`, `ClaudeInChromeDomain(studio.youtube.com)`, `ClaudeInChromeDomain(www.tiktok.com)`, `ClaudeInChromeDomain(www.instagram.com)` and the UPL command to `.claude/settings.json` so it can run unattended.
- **Next:** the skills that fit what they did. `setup` builds the page folder and playbook, `campaign-finder` finds paid campaigns, `rule-check` runs before posting.

Rules: no leaks, "play early" or piracy clips, and credit the creator in every post (see `rule-check`).
