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
            text = " ".join(w["w"] for w in words if p["start"] <= w["s"] < p["end"])
            c["candidates"].append({"n": n0 + i, "start": p["start"], "end": p["end"], "title": p.get("title", ""),
                                    "titles": p.get("titles", []), "tease": p.get("tease"),
                                    "tag": p.get("tag", ""), "why": ["audit"], "score": 0, "text": text[:400]})
            p["n"] = n0 + i
        full = d / "source.mp4"
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
