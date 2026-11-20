"""Accurate word timings for the captions of one clip.

The pipeline transcript (Whisper "small" over the whole VOD) is good enough to find moments, but its word timestamps
drift by a few hundred ms, some words get zero length and some stretch over seconds, so word-by-word captions
highlight the wrong word. For the 15-60 s of a clip we can afford better:

  1. re-transcribe the clip window with a stronger Whisper model (CLIP_CAPTION_MODEL, default large-v3-turbo),
     primed with GTA 6 names so they are spelled right;
  2. clean the tokens (Whisper splits "30,000" into "30" ",000" and "Hi-Veys" into "Hi" "-Veys");
  3. English: forced alignment with a wav2vec2 CTC model (the WhisperX method): every letter is placed on the
     audio at 20 ms resolution, so each word starts when it is actually said.
     Other languages, or when the models are missing: the Whisper timings, cleaned and clamped.

Cached per window in jobs/<id>/aligned/<start>-<end>.json.
usage: python align.py jobs/<id> START END      (prints the words)
"""
import json, os, re, sys, wave
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json, tool

SR = 16000
WHISPER = os.environ.get("CLIP_CAPTION_MODEL", "large-v3-turbo")
CTC = os.environ.get("CLIP_ALIGN_MODEL", "facebook/wav2vec2-base-960h")
PROMPT = "GTA 6, Grand Theft Auto VI, Vice City, Leonida, Jason, Lucia, Rockstar, chat, bro."
PAD = 0.6  # seconds of audio around the window so edge words are complete
_cache = {}


def window_pcm(d, a, b, src=None):
    """16 kHz mono float32 for [a, b] seconds of the source timeline (audio.wav, else decoded from src)."""
    wav = Path(d) / "audio.wav"
    a = max(0.0, a)
    if wav.exists():
        with wave.open(str(wav)) as w:
            n = w.getnframes()
            i, j = min(n, int(a * SR)), min(n, int(b * SR))
            w.setpos(i)
            x = np.frombuffer(w.readframes(j - i), np.int16)
    else:  # local source without a pipeline wav
        import subprocess
        x = np.frombuffer(subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i",
                                          str(src), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                                         capture_output=True).stdout, np.int16)
    return x.astype(np.float32) / 32768, a


def clean_tokens(words):
    """Glue Whisper's split tokens back together and drop empty ones.
    "30" ",000" -> "30,000"; "Hi" "-Veys" -> "Hi-Veys"; "y" "'all" -> "y'all"; lone punctuation joins the word before."""
    out = []
    for w in words:
        t = w["w"].strip()
        if not t:
            continue
        glue = out and (re.match(r"^[,.\-'’%!?…:;)]", t) or not re.search(r"\w", t))
        if glue and not (t[0] in "-" and len(t) > 1 and out[-1]["w"][-1:] in ".?!"):
            out[-1] = dict(out[-1], w=out[-1]["w"] + t, e=max(out[-1]["e"], w["e"]))
        else:
            out.append(dict(w, w=t))
    return out


def fix_timing(words, a, b):
    """Whisper-only fallback: monotonic starts, 80 ms minimum and 1.2 s maximum per word, inside [a, b]."""
    out = []
    for i, w in enumerate(words):
        s = max(w["s"], out[-1]["e"] if out else a)
        nxt = words[i + 1]["s"] if i + 1 < len(words) else b
        e = min(max(w["e"], s + 0.08), s + 1.2, max(nxt, s + 0.08))
        out.append(dict(w, s=round(s, 3), e=round(e, 3)))
    return out


def transcribe(pcm, offset, prompt):
    from faster_whisper import WhisperModel
    if "whisper" not in _cache:
        _cache["whisper"] = WhisperModel(WHISPER, device="cpu", compute_type="int8")
    segs, info = _cache["whisper"].transcribe(pcm, word_timestamps=True, vad_filter=True, initial_prompt=prompt,
                                              condition_on_previous_text=False, beam_size=5)
    words = [{"w": x.word.strip(), "s": round(x.start + offset, 3), "e": round(x.end + offset, 3)}
             for s in segs for x in (s.words or []) if x.word.strip()]
    return words, info.language


def ctc_align(pcm, offset, words):
    """Forced alignment of `words` (Whisper text) on `pcm` with a wav2vec2 CTC model. Returns words with new s/e.
    Words without letters (numbers, emoji) get the gap between their aligned neighbours."""
    import torch
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
    if "ctc" not in _cache:
        proc = Wav2Vec2Processor.from_pretrained(CTC)
        model = Wav2Vec2ForCTC.from_pretrained(CTC).eval()
        _cache["ctc"] = (proc, model)
    proc, model = _cache["ctc"]
    vocab = proc.tokenizer.get_vocab()
    blank = vocab.get("<pad>", 0)
    with torch.inference_mode():
        inp = proc(pcm, sampling_rate=SR, return_tensors="pt").input_values
        em = torch.log_softmax(model(inp).logits[0], dim=-1).numpy()          # (T, C)
    T = em.shape[0]
    frame = len(pcm) / SR / T                                                # ~0.02 s
    # token sequence: letters of each alignable word, "|" between words
    toks, owner = [], []                                                     # owner[k] = word index of token k
    spellings = [re.sub(r"[^A-Z']", "", w["w"].upper().replace("’", "'")) for w in words]
    for i, sp in enumerate(spellings):
        sp = "".join(ch for ch in sp if ch in vocab)
        if not sp:
            continue
        if toks:
            toks.append(vocab["|"]); owner.append(-1)
        for ch in sp:
            toks.append(vocab[ch]); owner.append(i)
    N = len(toks)
    if N == 0 or N > T:
        raise ValueError("nothing to align" if N == 0 else "transcript longer than audio")
    tk = np.array(toks)
    # trellis[t, j]: best log-prob after t frames with the first j tokens emitted
    tr = np.full((T + 1, N + 1), -np.inf, np.float32)
    tr[0, 0] = 0
    tr[1:, 0] = np.cumsum(em[:, blank])
    for t in range(T):
        stay = tr[t, 1:] + em[t, blank]
        move = tr[t, :-1] + em[t, tk]
        tr[t + 1, 1:] = np.maximum(stay, move)
    # backtrack from the end: the frame where each token was emitted
    at = np.zeros(N, int)
    j = N
    for t in range(T, 0, -1):
        if j == 0:
            break
        if tr[t - 1, j - 1] + em[t - 1, tk[j - 1]] >= tr[t - 1, j] + em[t - 1, blank]:
            j -= 1
            at[j] = t - 1
    if j:
        raise ValueError("alignment did not finish")
    out = [dict(w) for w in words]
    for i in range(len(words)):
        ks = [k for k in range(N) if owner[k] == i]
        if ks:
            out[i]["s"] = round(offset + at[ks[0]] * frame, 3)
            out[i]["e"] = round(offset + (at[ks[-1]] + 1) * frame, 3)
            out[i]["aligned"] = True
    # words without letters: between their neighbours
    for i, w in enumerate(out):
        if not w.get("aligned"):
            prev = next((out[k]["e"] for k in range(i - 1, -1, -1) if out[k].get("aligned")), w["s"])
            nxt = next((out[k]["s"] for k in range(i + 1, len(out)) if out[k].get("aligned")), w["e"])
            w["s"], w["e"] = round(prev, 3), round(max(prev + 0.08, nxt), 3)
    # a word is heard until shortly before the next one starts (CTC marks only the last letter's frame)
    for i, w in enumerate(out):
        nxt = out[i + 1]["s"] if i + 1 < len(out) else w["e"] + 0.3
        w["e"] = round(min(max(w["e"], min(w["e"] + 0.25, nxt - 0.02)), max(nxt, w["s"] + 0.08)), 3)
        w.pop("aligned", None)
    return out


def polish(words, min_on=0.10, max_on=1.2):
    """Readable highlight timing: each word is lit at least `min_on` s (short words like "I" are aligned to a
    single 20 ms frame and would only flash), at most `max_on` s, starts strictly increasing."""
    out = [dict(w) for w in words]
    for i, w in enumerate(out):
        if i and w["s"] < out[i - 1]["s"] + min_on:
            w["s"] = round(out[i - 1]["s"] + min_on, 3)
        w["e"] = round(min(max(w["e"], w["s"] + min_on), w["s"] + max_on), 3)
    return out


def clip_words(d, a, b, src=None, fallback=None, speaker=""):
    """Words (absolute source seconds) for captions of [a, b]. Cached. Falls back to `fallback` (pipeline words)."""
    d = Path(d)
    cache = d / "aligned" / f"{a:.2f}-{b:.2f}.json"
    if cache.exists():
        return polish(load_json(cache))
    fb = clean_tokens([w for w in (fallback or []) if a - PAD <= w["s"] < b + PAD])
    try:
        pcm, off = window_pcm(d, a - PAD, b + PAD, src)
        prompt = PROMPT + (f" {speaker}." if speaker else "")
        words, lang = transcribe(pcm, off, prompt)
        words = clean_tokens(words)
        how = f"whisper {WHISPER}"
        if lang == "en" and words:
            try:
                words = ctc_align(pcm, off, words)
                how += " + wav2vec2 alignment"
            except Exception as e:
                print(f"align: forced alignment skipped ({str(e)[:100]}), using Whisper timings")
                words = fix_timing(words, off, off + len(pcm) / SR)
        else:
            words = fix_timing(words, off, off + len(pcm) / SR)
    except Exception as e:
        print(f"align: re-transcription failed ({str(e)[:120]}), using the pipeline transcript")
        words, how = fix_timing(fb, a - PAD, b + PAD), "pipeline transcript (cleaned)"
    words = [w for w in polish(words) if w["e"] > a and w["s"] < b]
    print(f"align: {len(words)} words for {a:.1f}-{b:.1f}s via {how}")
    cache.parent.mkdir(exist_ok=True)
    save_json(cache, words)
    return words


if __name__ == "__main__":
    job, s, e = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    tr = Path(job) / "transcript.json"
    for w in clip_words(job, s, e, fallback=load_json(tr) if tr.exists() else None):
        print(f"{w['s']:9.2f} {w['e']:9.2f}  {w['w']}")
