---
name: upload-next
description: Post the next best clip from the queue to YouTube Shorts, TikTok and Instagram Reels through Claude in Chrome. Chooses the best clip, checks its frames unless Claude already did when it was rendered, uploads, records the links. Built to run unattended on an interval (/loop 2h /upload-next). Also what the Post page's Post now button runs.
---

# Upload the next clip

One run posts **one clip** to every platform where it isn't posted yet (YouTube Shorts, TikTok, Instagram Reels).
This runs unattended: nobody is watching. When something is unclear, do less: skip, log it and notify. Never guess.

Run every command from the clip desk folder (the one with `jobs/` and `queue/`).

- **TOOL**: `${CLAUDE_PLUGIN_ROOT}` when that is a real path; otherwise the folder that holds `scripts/uploader.py` (usually `./tool`).
- **UPL**: the helper, `<python> TOOL/scripts/uploader.py`. `<python>` is `.venv/bin/python` (Windows `.venv\Scripts\python.exe`) when the desk has a `.venv`, else `python3`. When a prompt gives you UPL, use exactly that.
- **The account**: `UPL status` prints `account` (`name` and `handles`), the only account you may post as. Set it with `UPL account --name "<page name>" --handle <handle>`, or with **Posting as** on the Post page.

## Post now (one chosen clip)

When the human pressed **Post now** on the Post page (`scripts/post_now.py`), the clip is already chosen: skip steps
1 and 2 and do steps 3-6 for that clip only. Everything else here still applies.

## 1. Should this run post at all?

`UPL status`

Stop (one-line summary, no notification) when:
- `minutes_since_last_post` < 100. A post went out recently, so a double tick or a manual post happened.
- `account` is empty. Send one PushNotification: "Set the account you post as: Posting as, on the Post page". Never guess an account.
- `pending` is empty. Send one PushNotification: "Upload queue is empty: cut or queue more clips". Don't send it again if the last log entry in `queue/uploads/log.jsonl` is already about an empty queue.

## 2. Pick the best clip

Rank **every** pending clip yourself: this is the one place the posting order is decided. Start from `rank` (the
order clips joined the queue, or an older re-rank with `rank_reason`), but clips may have been added or posted since. A clip with `hold: true` is
never posted: the re-rank decided it needs a human. A clip with `pinned: true` was picked by the human to post
next: take it first (it still has to pass the visual check). Otherwise weigh, in order:
1. **Hard no:** `auto_ok: false` (too long to upload in under 10 MB), or a warning that says the clip must be re-cut ("Old cut… Re-cut before posting"). Don't post these; leave them for the human. They aren't skipped, so they stay visible on the queue page.
2. **Finish what's started:** a clip already live on 1–2 platforms (`already_on`) goes first, so each clip goes out on all three while it's fresh.
3. **Paid first:** a clip whose `campaigns` isn't empty earns money per view (a paid campaign the human joined): it goes before clips without one, the higher `rate` first, as long as its hook holds up.
4. **Hook strength:** a clear, surprising one-line hook that works muted, a named streamer, and a payoff.
5. **Freshness:** newer streams and launch-week moments beat older ones.
6. **Length:** 12–25 s is the sweet spot; 25–40 s needs a much stronger hook.
7. **Variety:** don't pick the same creator as the last 2 entries in `recent_creators` if a comparable clip from someone else exists.
8. **Warnings:** a platform-specific warning (e.g. "TikTok restricts vapes", "Instagram may limit reach") means skipping **that platform** for this clip, not the whole clip, unless the warning makes the clip a bad post overall.
9. If `learning_rules` is set, its Current Rules override the guidance above.

Write 2–3 lines: the top 3 in your order and why #1 won.

## 3. Visual check (required before any upload, unless already done)

If the clip's `qa` is `"ok"`, Claude already checked the finished clip's frames when it was rendered: skip this step.
Otherwise (older clips, `qa` empty): `UPL frames <id>` then **Read** the sheet image it prints (6 frames, 2 rows × 3).

The clip passes only if **all** of these hold:
- There is real picture in every frame: no black, frozen, green or corrupted frames, and no missing panel.
- **Split layout:** the top panel shows the credited streamer's webcam (a face or person), not the game, a menu or a different streamer. The bottom panel shows GTA 6 gameplay.
- The title box is readable, spelled right, fully inside the frame, and matches the clip.
- Captions appear (word-by-word, big) and aren't cut off at the edges.
- No nudity, gore, slurs on screen, personal info (a phone number, an address, a DM), another clipper's watermark, or a "leak"/"download"/"play early" message.
- Nothing contradicts the title: e.g. a title says "backflip" but no frame suggests it.

If it fails, run `UPL skip <id> --reason "<what you saw>"` and go back to step 2 for the next clip. Try at most 3 clips per run; then stop and notify: "3 clips failed the visual check, see queue/uploads/log.jsonl".
If you're unsure (e.g. one frame is ambiguous), check one more frame: `ffmpeg -v error -y -ss <t> -i <source mp4> -frames:v 1 -vf scale=540:960 queue/uploads/frames/extra.jpg` and Read it.

## 4. Prepare

`UPL prepare <id>` prints:
- `full`: the **original render** (full quality), split into pieces under 10 MB: `chunks` (paths, in order), `name`,
  `bytes`, `sha256_16`. Always upload this way (see "Full-quality upload" below).
- `file`: a compressed copy under 9.5 MB. Only a fallback, when the full-quality upload fails on a platform: it
  looks visibly worse.
- `pending`: the platforms to post to.
- `text`: the exact caption, title, description and tags for each platform.

Use that text **verbatim**: no rewriting, no extra hashtags or emojis.

## 5. Post to each pending platform

Order: YouTube → TikTok → Instagram. For each one:
- Use a new tab (`tabs_context_mcp` with `createIfEmpty`, then `tabs_create_mcp`) and close it when done.
- Batch predictable steps with `browser_batch`, and take a screenshot before each irreversible click.
- Never click a native "Select file" button: it opens a picker you can't use. Find the `input[type=file]` with `find` or `read_page`.

### Full-quality upload (do this wherever a step says "upload the video")
Claude in Chrome hands a page at most ~10 MB per `file_upload` call, so the original goes in pieces and is joined
back into the exact original inside the page. Do it once the platform's upload field is on screen:
1. `javascript_tool`: add a hidden helper input that collects the pieces:
   `window.__parts=[];const i=document.createElement('input');i.type='file';i.id='__chunkin';i.style.cssText='position:fixed;left:-9999px;top:0';i.addEventListener('change',()=>{for(const f of i.files)window.__parts.push(f)});document.body.appendChild(i);'ready'`
2. `find` "file input with id __chunkin" to get its ref. Then `file_upload` **one piece per call**, in order, all
   of `full.chunks` (never several pieces in one call or one `browser_batch`: the 10 MB cap counts the whole call).
3. `javascript_tool`: join, check, and put the file into the platform's own input (pass `full.name`):
   `const ps=window.__parts,b=new Blob(ps,{type:'video/mp4'}),h=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await b.arrayBuffer()))].map(x=>x.toString(16).padStart(2,'0')).join('').slice(0,16);const f=new File([b],'NAME',{type:'video/mp4'});const inp=[...document.querySelectorAll('input[type=file]')].find(x=>x.id!=='__chunkin');const dt=new DataTransfer();dt.items.add(f);inp.files=dt.files;inp.dispatchEvent(new Event('change',{bubbles:true}));({parts:ps.length,mb:(b.size/1e6).toFixed(1),sha:h})`
   `parts` must equal the number of chunks and `sha` must equal `full.sha256_16`; otherwise reload the upload page and
   start over once. If the platform's input sits inside a dialog that isn't open yet, open it first.
4. Screenshot: the platform must show the upload going (TikTok shows the full size and 1080P).
If this fails twice on a platform, fall back to `file_upload` of the compressed `file` and say so in the summary.

### Safety rules (all platforms)
- **Right account first.** Before uploading, confirm the signed-in account is the one in `account`: the display name matches `account.name`, or the handle is one of `account.handles`. If it's any other account, don't switch accounts and don't post. Run `fail <id> <platform> --reason "wrong account: <name>"`, notify "Instagram is signed in as <name>, not <account.name>: skipped", and move to the next platform.
- **Logged out, CAPTCHA, verification code, "suspicious activity", or a new terms/consent dialog:** stop all platforms for this run. Never type a password or solve a CAPTCHA. Run `fail …` and notify with the platform and what it asked for.
- **Content warning or copyright claim shown before posting** (TikTok "content check", a YouTube copyright "Checks" issue): don't post that platform. Run `fail` with the reason and notify.
- **Anything else unexpected:** take a screenshot, decide whether it blocks posting, and when in doubt run `fail` and move on.
- Never change account or channel settings, never reply to comments, never follow anyone.

### YouTube Shorts
1. Go to `https://www.youtube.com/upload` (it opens the Studio upload dialog). Check the channel avatar or name is the page account.
2. Upload the video (full quality, above) into the upload dialog's file input.
3. Title: select all in the title box and type `text.youtube.title`. Description: type `text.youtube.description`.
4. Audience: **"No, it's not made for kids"**.
5. "Show more" → Tags: type `text.youtube.tags`. Leave everything else at its default.
6. Next → Next → Next (the Checks step: if it shows a copyright claim, stop per the safety rules) → Visibility: **Public** → Publish.
7. Read the video link in the dialog (`https://youtu.be/<ID>`) and record it as `https://youtube.com/shorts/<ID>`.
8. `UPL mark <id> youtube --url <link>`

### TikTok
1. Go to `https://www.tiktok.com/tiktokstudio/upload`. Check the account (avatar menu or profile) is the page account.
2. Upload the video (full quality, above). Wait until the upload shows as complete (up to ~2 min for a big file; screenshot to confirm).
3. Caption (the description editor): click it, select all (cmd+a), Backspace, then type `text.tiktok.caption`. Hashtags open a suggestion popup: press Escape after typing, and check the final text with a screenshot. Fix it if a suggestion replaced a tag.
4. Leave the cover, music and settings at their defaults. Who can watch: Everyone. Comments: on.
5. Click **Post**. If a "Continue to post?" / content check dialog appears, follow the safety rules.
6. Wait for the "posted" confirmation. Then on `https://www.tiktok.com/tiktokstudio/content`, open the newest post and copy its link if it's easy to get; otherwise leave the URL empty.
7. `UPL mark <id> tiktok [--url <link>]`

### Instagram Reels
1. Go to `https://www.instagram.com/`. Check the account (the profile link in the sidebar, or the name in the right column) is the page account. Instagram is often signed in to a **personal** account, so check every time.
2. Click Create (+) → Post (or Reel). Upload the video (full quality, above) into the dialog's file input. If it says "Video posts are now shared as reels", click OK.
3. Crop: choose the **original** or 9:16 ratio so nothing is cut, then click Next. On the edit step, click Next.
4. Caption: type `text.instagram.caption`. Leave the cover, location, collaborators and advanced settings alone.
5. Click **Share** and wait for "Your reel has been shared".
6. Get the link from the newest reel on the profile if it's easy; otherwise leave it empty.
7. `UPL mark <id> instagram [--url <link>]`

## 5b. Paid campaigns

`UPL mark` prints `to submit: <campaign> (<platform>)` when the post qualifies for a paid campaign the human joined.
If it did, submit those links now, in the same Chrome session, by the `campaign-submit` skill
(`${CLAUDE_PLUGIN_ROOT}/skills/campaign-submit/SKILL.md`), only for this clip's entries. Its hard stops apply: never
join a campaign, accept terms, sign in or open payout settings; leave anything that needs the human as to_submit.

## 6. Finish

- Close every tab you opened.
- Print one line, e.g. `Posted v3_social (Jynxzi) → YT ✓ TT ✓ IG ✕ wrong account. Next up: v3_shower.`
- Notify only when something needs the human: a failure, the wrong account, a login wall, or an empty queue. A clean run needs no notification.
- Don't edit the files in `queue/` (except through UPL) or the clip files. Re-ranking happens fresh each run and is never saved.
