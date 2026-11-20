"""Find sources for both clip formats, ranked by views per hour.

  stream: VODs of GTA 6 live streams (was_live) -> reaction shorts (webcam on top, game below)
  long:   regular videos 8-60 min                 -> split-screen long clips (+ normal shorts)

usage: python find.py [--hours 48] [--per-kind 4] [--out sources]
writes <out>.md (table) and <out>.txt (one URL per line, feed it to pipeline.py --from)
"""
import argparse, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote_plus
sys.path.insert(0, str(Path(__file__).parent))
from common import ytdlp_json

BY_DATE = "&sp=CAI%253D"
QUERIES = {
    "stream": ["gta 6 stream", "gta 6 livestream", "gta 6 first time playing", "playing gta 6 live", "gta 6 day 1 stream"],
    "long": ["gta 6 gameplay", "gta 6 funny moments", "gta 6 review", "gta 6 things you missed", "gta 6 impressions"],
}


def search(q, n):
    try:
        r = ytdlp_json(f"https://www.youtube.com/results?search_query={quote_plus(q)}{BY_DATE}",
                       ["--flat-playlist", "--playlist-end", str(n)])
        return [e.get("url") or f"https://www.youtube.com/watch?v={e['id']}" for e in r.get("entries") or [] if e.get("id")]
    except SystemExit:
        return []


def detail(url):
    try:
        i = ytdlp_json(url, ["--skip-download"])
    except SystemExit:
        return None
    return {k: i.get(k) for k in ("webpage_url", "title", "channel", "duration", "view_count", "timestamp",
                                  "release_timestamp", "live_status", "heatmap")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--per-query", type=int, default=12)
    ap.add_argument("--per-kind", type=int, default=4)
    ap.add_argument("--out", default="sources")
    a = ap.parse_args()
    with ThreadPoolExecutor(10) as ex:
        found = {k: list(ex.map(lambda q: search(q, a.per_query), qs)) for k, qs in QUERIES.items()}
        urls = list(dict.fromkeys(u for lists in found.values() for l in lists for u in l))
        infos = [x for x in ex.map(detail, urls) if x]
    now = time.time()
    rows = {"stream": [], "long": []}
    for x in infos:
        t = x.get("release_timestamp") or x.get("timestamp") or 0
        age = (now - t) / 3600 if t else None
        dur = x.get("duration") or 0
        if not age or age > a.hours or x.get("live_status") in ("is_live", "is_upcoming"):
            continue
        x["age_h"] = age
        x["vph"] = (x.get("view_count") or 0) / max(age, 0.5)
        x["has_heatmap"] = bool(x.get("heatmap"))
        if x.get("live_status") == "was_live" and dur >= 20 * 60:
            rows["stream"].append(x)
        elif 8 * 60 <= dur <= 60 * 60:
            rows["long"].append(x)
    md = [f"# Sources ({time.strftime('%Y-%m-%d %H:%M')}, last {a.hours:.0f} h)", ""]
    txt = []
    for kind, label in (("stream", "Stream VODs → reaction shorts (webcam on top)"), ("long", "Videos 8–60 min → split-screen long clips")):
        xs = sorted(rows[kind], key=lambda x: -x["vph"])
        md += [f"## {label}", "", "| # | views/h | views | age h | length | channel | title | replay map |", "|---|---|---|---|---|---|---|---|"]
        for i, x in enumerate(xs[:15], 1):
            md.append(f"| {i} | {x['vph']:,.0f} | {x.get('view_count') or 0:,} | {x['age_h']:.1f} | {(x.get('duration') or 0) / 60:.0f} min | "
                      f"{x.get('channel', '')} | [{(x.get('title') or '').replace('|', '/')[:70]}]({x['webpage_url']}) | {'yes' if x['has_heatmap'] else ''} |")
        md.append("")
        txt += [f"{x['webpage_url']}  # {kind}: {x.get('channel', '')} - {(x.get('title') or '')[:60]}" for x in xs[:a.per_kind]]
    Path(f"{a.out}.md").write_text("\n".join(md), encoding="utf-8")
    Path(f"{a.out}.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"\n{len(txt)} sources -> {a.out}.txt")


if __name__ == "__main__":
    main()
