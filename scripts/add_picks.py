"""Add hand/LLM-picked moments to jobs and fetch only their video, in parallel.

picks.json: [{"job": "jobs/<id>", "start": 474, "end": 511, "tease": [505.2, 507.4], "title": "...",
               "titles": ["...", "...", "..."], "tag": "K1"}, ...]
  start/end: the clip (first word that makes sense alone -> right after the payoff); tease: the 1-3 s line
  played first (cut.py --tease, optional); titles: the hook options, title = the pick.
Writes jobs/<id>/candidates_audit.json (cut.py --set candidates_audit --pick N) and
sections/audit_<N>.mp4 for sources without a full source.mp4.
usage: python add_picks.py picks.json [--pad 2]
"""
import argparse, json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import load_json, save_json, ytdlp_json
import pipeline


def snap_pick(p, words, d):
    """Start on a word, the tease on whole words (0.6-3 s), and a tease for every pick: when Claude gave none, the
    loudest 1.6 s in the second half of the moment (the payoff is usually the loud part), on word boundaries."""
    ws = [w for w in words if p["start"] - 1.0 <= w["s"] < p["end"] + 1.0]
    first = next((w for w in ws if w["s"] >= p["start"] - 0.3), None)
    if first and first["s"] < p["end"]:
        p["start"] = round(max(0.0, first["s"] - 0.1), 2)
    t = p.get("tease")
    if t and len(t) == 2 and t[1] > t[0]:
        a = next((w["s"] for w in ws if w["s"] >= t[0] - 0.25), t[0])
        b = max([w["e"] for w in ws if w["s"] < t[1] and w["e"] <= t[1] + 0.35] or [t[1]])
        if 0.6 <= b - a <= 3.0:
            p["tease"] = [round(a - 0.05, 2), round(b + 0.1, 2)]
        return p
    try:   # no tease: the loudest moment in the second half
        import numpy as np
        from align import window_pcm
        mid = p["start"] + (p["end"] - p["start"]) * 0.45
        x, a0 = window_pcm(d, mid, p["end"])
        hop = 1600   # 0.1 s
        k = len(x) // hop
        if k >= 16:
            db = 20 * np.log10(np.sqrt((x[:k * hop].reshape(k, hop) ** 2).mean(1)) + 1e-9)
            win = np.convolve(db, np.ones(16) / 16, mode="valid")
            i = int(np.argmax(win))
            a, b = a0 + i * 0.1, a0 + i * 0.1 + 1.6
            inside = [w for w in ws if w["e"] > a and w["s"] < b]
            if inside:
                a, b = min(a, inside[0]["s"] - 0.05), max(b, inside[-1]["e"] + 0.1)
            if b - a > 3.0:
                b = a + 3.0
            p["tease"], p["tease_auto"] = [round(a, 2), round(min(b, p["end"]), 2)], True
    except Exception as ex:
        print(f"no auto tease for {p.get('tag', '')}: {ex}", flush=True)
    return p


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("picks"); ap.add_argument("--pad", type=float, default=2.0)
    a = ap.parse_args()
    picks = json.loads(Path(a.picks).read_text())
    by_job = {}
    for p in picks:
        by_job.setdefault(p["job"], []).append(p)
    jobs = {}
    for jpath, ps in by_job.items():
        d = Path(jpath)
        info = load_json(d / "info.json")
        out = d / "candidates_audit.json"
        c = load_json(out) if out.exists() else {"source": None, "info": info, "candidates": []}
        words = load_json(d / "transcript.json")
        n0 = max([x["n"] for x in c["candidates"]] or [0])
        for i, p in enumerate(ps, 1):
            snap_pick(p, words, d)
            text = " ".join(w["w"] for w in words if p["start"] <= w["s"] < p["end"])
            c["candidates"].append({"n": n0 + i, "start": p["start"], "end": p["end"], "title": p.get("title", ""),
                                    "titles": p.get("titles", []), "tease": p.get("tease"),
                                    "tag": p.get("tag", ""), "why": ["audit"], "score": 0, "text": text[:400],
                                    **({"tease_auto": True} if p.get("tease_auto") else {})})
            p["n"] = n0 + i
        full = d / "source.mp4"
        if not full.exists():   # a local file the pipeline read in place (its path is the source)
            try:
                src = load_json(d / "candidates.json").get("source")
            except (OSError, ValueError):
                src = None
            src = src or (info.get("webpage_url") if not str(info.get("webpage_url", "")).startswith("http") else None)
            if src and Path(src).is_file():
                full = Path(src)
        if full.exists():
            c["source"] = str(full)
        save_json(out, c)
        jobs[jpath] = (d, c, full.exists(), info)

    def need_urls(jpath):
        d, c, has_full, info = jobs[jpath]
        if has_full:
            return jpath, None
        return jpath, info["webpage_url"]

    with ThreadPoolExecutor(6) as ex:
        urls = dict(ex.map(need_urls, list(jobs)))
        todo = [(jp, p) for jp, ps in by_job.items() for p in ps if urls[jp]]

        def one(item):
            jp, p = item
            d = jobs[jp][0]
            (d / "sections").mkdir(exist_ok=True)
            out = d / "sections" / f"audit_{p['n']}.mp4"
            t = p.get("tease") or [p["start"], p["end"]]   # the section must also hold the tease
            s, e = max(0.0, min(p["start"], t[0]) - a.pad), max(p["end"], t[1]) + a.pad
            if not out.exists():
                fake = type("J", (), {"src": urls[jp]})
                tmp = out.with_suffix(".tmp.mp4")
                pipeline.section(fake, s, e, tmp)
                tmp.rename(out)
            print(f"{p.get('tag', '')} {d.name} #{p['n']} -> {out.name}", flush=True)
            return jp, p["n"], str(out), round(s, 2)
        results = list(ex.map(one, todo))
    for jp, n, f, off in results:
        c = load_json(Path(jp) / "candidates_audit.json")
        for x in c["candidates"]:
            if x["n"] == n:
                x["file"], x["offset"] = f, off
        save_json(Path(jp) / "candidates_audit.json", c)
    for jp in {r[0] for r in results}:   # a pipeline video step that failed earlier is resolved by these sections
        pf = Path(jp) / "progress.json"
        if pf.exists():
            pr = load_json(pf)
            if pr.get("stage") == "error" and pr.get("error_at") == "video":
                n = len(list((Path(jp) / "sections").glob("audit_*.mp4")))
                pr.update(stage="done", sections_total=n, sections_done=n,
                          note="pipeline video step failed; pick sections downloaded by add_picks.py")
                pr.pop("error", None); pr.pop("error_at", None)
                save_json(pf, pr)
    print(json.dumps([{k: p[k] for k in ("tag", "job", "n")} for p in picks]))


if __name__ == "__main__":
    main()
