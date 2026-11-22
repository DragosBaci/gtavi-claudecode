---
name: clip-cutter
description: Cut a picked moment into a finished 1080x1920 vertical clip with word-by-word captions, a hook-first edit and a hook box. Use after moment-finder, or when the user says "cut it", "make the clip", "make it vertical".
---

# Clip cutter

Run:

`python "${CLAUDE_PLUGIN_ROOT}/scripts/cut.py" jobs/<id> --set candidates_audit --pick <n> --layout react|track|fill|blur|facecam --title "TEXT"`

Layouts:

- `track` (Focus): a big 4:5 window (1080x1350) over a blurred copy, aimed by itself: the streamer's face when there is no game on screen (menus, IRL), otherwise it follows the action with calm pans. The default for clips without a webcam over gameplay; `react --or-blur` falls back to it. `--focus motion` always follows the action.
- `fill`: crop the game video to fill the frame (fixed centre crop).
- `blur` (Fit): full video centered over a blurred copy, as a 1080x608 strip. Only when the whole 16:9 frame must show (a map, a menu explained).
- `facecam`: gameplay with the streamer's camera shown.
- `react`: streamer's face on top, game below, title on the seam (see `twitch-reaction`).

What `cut.py` does by default (each can be turned off):

- **Hook first**: plays the pick's `tease` (the culminating 1-2 s, from picks.json or `--tease S:E` in source seconds), then a transition into the clip from its start: `--transition zoom` (default), `whip`, `slide`, `fade`, `pixel`, `circle`, `dissolve` (blended on the finished 9:16 picture, with a whoosh on zoom/whip/slide/pixel), or `flash`, `cut`, `rewind`. When the tease is inside the clip, the clip ends right after it (`--after-tease 0.8`, never starting the next sentence), so it builds up to the hook. `--keep-after-tease` keeps playing; `--no-tease` skips the hook.
- **Trim**: starts on the first word and ends just after the last one (it keeps a laugh or scream that continues). `--no-trim` keeps the exact start/end.
- **Removes pauses** in the speech longer than 0.45 s, unless the pause is loud. `--tighten 0` keeps them. `--keep S:E` protects a silent visual moment (a backflip, a reveal) while the other pauses are still cut; `--drop S:E` cuts a repeated line or a chat read. Both take source seconds and can be repeated.
- **Webcam**: `--cam auto` can lock onto the wrong face (a thumbnail, the game character) or a cam that moves during a screen-share. Check the contact sheet; pass `--cam x:y:w:h` (fractions of the source frame) to fix it. A manual box shows the whole cam, which is what full-body moments need.
- **Punch-in**: a quick zoom over the first 0.4 s so the first frame moves. `--no-punch` to skip.
- **Captions**: re-transcribes the clip with Whisper large-v3-turbo and pins every word to the audio (wav2vec2 forced alignment, `align.py`), so the yellow word matches the voice. Cached per clip in `jobs/<id>/aligned/`. `--fast-words` uses the pipeline transcript instead (faster, less accurate).
- **Sound**: gentle compression (voice and game audio closer together), then two-pass loudness to -14 LUFS (measured, one fixed gain: no pumping).
- **Quality**: source frame rate up to 60 fps, sharpened webcam when it is enlarged, a high-bitrate master (platforms re-compress uploads).

Before running:

1. Get 3 hook options from the `hook-writer` skill and use the best one.

To fine-tune by hand (cut words, move the hook, fix the webcam crop), run `python "${CLAUDE_PLUGIN_ROOT}/scripts/editor.py"` and open http://127.0.0.1:8766: it edits the same options and renders with `cut.py`.

A clip rendered in the editor goes straight into the posting queue. One made with `cut.py` from the terminal joins it, once it looks right, with `python "${CLAUDE_PLUGIN_ROOT}/scripts/uploader.py" queue <job>/<clip name>`.

After running:

1. Output is an mp4 in `jobs/<id>/edits`. It prints `edit: N piece(s), 34.2s -> 26.7s`. Look at it: `python "${CLAUDE_PLUGIN_ROOT}/scripts/frames.py" jobs/<id> --clip <name>` writes `jobs/<id>/review/clip_<name>.jpg` (8 frames as posted) plus motion, static share and frozen/black stretches; Read the image. Check the first 2 seconds: speech from the first frame, the tease makes sense alone, captions readable, the face or action in frame. No credit text is burned into the video; the poster credits the creator in the post.
2. Run `rule-check` on it. Fix and re-cut until it passes.
3. 12 to 25 seconds for reactions, up to 30 for tips. It must end on the payoff.
4. Show the human the file and the caption text, and ask for "yes". Do not upload anything.
