---
name: live-mode
description: Watch one or several live streams (Twitch or YouTube) and clip the moments their chat goes crazy, in near real time. Use when the user gives live URLs, says "go live mode" or "watch the streams", or wants clips from launch streams as they happen.
---

# Live mode

Run (several streams at once):

`python "${CLAUDE_PLUGIN_ROOT}/scripts/watch.py" https://twitch.tv/<login> https://twitch.tv/<login2> --minutes 120`

or `--top 3` for the biggest live GTA 6 streams on Twitch (check their language first: many are Spanish).

How it works (`watch.py`):
- Each stream is kept in a rolling buffer (default 60 s in ~2 s pieces, oldest overwritten), so nothing piles up.
- Chat is counted in 0.5 s bins. A spike = the last 4 s is `--ratio` (2.5) times that stream's normal rate.
- Chat reacts late: the moment is placed `--reaction` (2.5 s) before chat started climbing, then moved to the loudest second of audio nearby (the recording can run a few seconds behind viewers).
- Each flagged moment becomes `jobs/live-<stream>-<time>/` with a transcript and a pick whose hook is the moment, listed in `jobs/live_watch.md` with an editor link.

Single YouTube stream with the whole recording kept: `scripts/live.py <url> --minutes 60` (older, one stream, 60 s pieces).

Before starting:

1. Confirm the stream is public and not a leak, pirated copy or "play early" content. If unsure, do not start.
2. Note the streamer's handle for credit.

While it runs:

1. Check `jobs/live_watch.md` for new rows every few minutes. Review each one: pick the best, skip spoilers and nudity.
2. For each keeper: read its `transcript.json`, write a title with `hook-writer`, then `clip-cutter` with `--set candidates_audit --pick 1` (the pick already has the hook), then `rule-check`. If the human has the editor open, make the edit through `/api/agent` so they see it.
3. Send the human one message with the best 1 to 3 clips (caption, credit, file path). They say "yes" and post. Speed matters, so keep it to one message.

When done: list what was recorded, what was clipped, and log the posts via `learning-loop`. Ask the streamer for permission when they are reachable.
