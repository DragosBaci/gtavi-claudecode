---
name: moment-finder
description: Find the best clip moments in a long video, stream VOD or local file. Use when the user gives a YouTube URL or video file and wants highlights, "find the moments", or candidates to cut.
---

# Moment finder

Run:

`python "${CLAUDE_PLUGIN_ROOT}/scripts/moments.py" <url-or-file> --top 8`

It writes `jobs/<id>/candidates.md`. Signals used: transcript with word timestamps (faster-whisper), loudness spikes, the YouTube "most replayed" heatmap and live-chat spikes, when available. A missing signal is skipped, not faked.

The scores are only a hint. The good picks come from reading the transcript:

1. `python "${CLAUDE_PLUGIN_ROOT}/scripts/compact.py" jobs/<id>` writes `jobs/<id>/compact.txt`: one timestamped line per phrase, ▲ = loudness spike. Read it (with `candidates.md` as hints).
2. For every moment you would actually watch, write a pick into a picks file (see below), then run `python "${CLAUDE_PLUGIN_ROOT}/scripts/add_picks.py" picks.json`. It adds them as `candidates_audit` and downloads only the video they need.
3. Show the picks: start-end, length, tease line, the 3 title options. Drop spoilers for story moments in launch week, nudity, and anything that looks like a leak.
4. Next step: `clip-cutter` with `--set candidates_audit --pick <n>`.

## How to pick (people swipe in the first 2 seconds)

Viewers decide in the first 1-2 seconds. Average view time is the number that matters, so every pick must:

- **Start on a line that makes sense alone**, spoken in the first frame. Never start on chat reads, donation reads ("thank you for the dono"), "hold on", "chat", menus, loading or silence. Skip the lead-in and start at the setup's first real sentence.
- **Have a payoff and end right after it**: the reveal, the laugh, the punchline. If the payoff is later than the obvious end, extend the end; if there is no payoff, it is not a clip.
- **Have a hook (tease)**: the culminating 1-2 s of the moment: the reveal, the punchline, the scream. `cut.py` plays it first, transitions to the start (zoom by default), then the clip builds up to that same moment and ends right after it. So the tease must sit inside the clip, near its end, be understandable on its own and make you want the context. A cliffhanger works too ("Ooh, what if I rob…").
- **Be short**: 12-25 s of content for reactions (`cut.py` also removes pauses). Over 30 s only for tips and explanations.

picks.json (seconds in the source; take exact times from `compact.txt`, `cut.py` snaps to the words):

```json
[{"job": "jobs/<id>", "tag": "K1", "start": 5305.0, "end": 5327.0,
  "tease": [5320.4, 5322.35],
  "title": "Kai Cenat found the SHOWER in GTA 6",
  "titles": ["Kai Cenat found the SHOWER in GTA 6", "GTA 6 lets you shower and Kai went all in", "Jason finally took a shower in GTA 6"]}]
```

Titles come from `hook-writer` (reaction titles).

Record the source link and creator in `media/raw` notes. Credit the creator in the post caption.
