#!/usr/bin/env python3
"""bench_pick: time the moment-picking step on one video with different window sizes.

Only the video's own captions are fetched (no audio, no video, nothing is rendered), then
Claude picks moments once per setting. Prints, for each setting: windows, Claude calls,
wall time, candidates found, and how many of the top moments agree with the first setting.

  python bench_pick.py URL --out DIR
  python bench_pick.py URL --out DIR --settings 12min,3000,6000,10000 --clips 5

A setting is either "<N>min" (the old fixed windows) or a number of transcript words per
window. Results are cached per setting in DIR, so re-running prints the table again
without new Claude calls (times then show as 0)."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper import config, moments, scan, source  # noqa: E402
from clipper.util import Timer, log, set_timer  # noqa: E402


def overlap(a, b):
    """How many moments in `a` also appear in `b` (sharing at least half their length)."""
    hit = 0
    for m in a:
        for k in b:
            both = min(m["end"], k["end"]) - max(m["start"], k["start"])
            if both >= 0.5 * min(m["end"] - m["start"], k["end"] - k["start"]):
                hit += 1
                break
    return hit


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url")
    p.add_argument("--out", required=True)
    p.add_argument("--settings", default="12min,3000,6000")
    p.add_argument("--clips", type=int, default=5)
    p.add_argument("--profile", default="general")
    p.add_argument("--model", default=None)
    a = p.parse_args()

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = source.open_source(a.url)
    info = src.info(out)
    subs = src.captions(out, info)
    if not subs:
        sys.exit("this video has no captions of its own; pick one that does")
    words = scan._json3_words(subs)
    heard = {"words": words, "language": "en", "source": "youtube"}
    stream = {"name": info["name"], "title": info["title"], "url": a.url}
    profile = config.profile_text(a.profile)
    minutes = (words[-1]["e"] - words[0]["s"]) / 60
    log(f"{info['title'][:60]} · {minutes:.0f} min · {len(words)} words", "ok")

    rows, first = [], None
    for setting in a.settings.split(","):
        cfg = config.load({"clips_per_stream": a.clips, "claude_model": a.model})
        if setting.endswith("min"):
            cfg["chunk_minutes"] = float(setting[:-3])
            cfg["pick_overlap"] = 0                  # the old behaviour had no context
        else:
            cfg["pick_window_words"] = int(setting)
        work = out / f"pick_{setting}"
        work.mkdir(exist_ok=True)
        timer = Timer()
        set_timer(timer)
        t0 = time.time()
        chosen = moments.find(heard, [], [], work, cfg, stream, profile)
        took = time.time() - t0
        pool = json.loads((work / "candidates.json").read_text(encoding="utf-8"))
        calls = [c for c in timer.calls if c["purpose"] == "pick"]
        if first is None:
            first = chosen
        rows.append({
            "setting": setting, "windows": len(list((work / "picks").glob("*.json"))),
            "calls": len(calls), "wall_s": round(took, 1),
            "claude_s_sum": round(sum(c["seconds"] for c in calls), 1),
            "slowest_call_s": max([c["seconds"] for c in calls] or [0]),
            "prompt_chars": sum(c["prompt_chars"] for c in calls),
            "reply_chars": sum(c["reply_chars"] for c in calls),
            "candidates": len(pool), "top_scores": [m.get("score") for m in chosen],
            "same_as_first": f"{overlap(chosen, first)}/{len(chosen)}",
            "chosen": [[m["start"], m["end"], m.get("title")] for m in chosen]})
    (out / "bench.json").write_text(json.dumps(
        {"video": stream, "minutes": round(minutes), "words": len(words), "rows": rows},
        indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"{'setting':>9} {'windows':>7} {'calls':>5} {'wall s':>7} {'sum s':>7} "
          f"{'slowest':>7} {'cands':>5} {'same':>5}  top scores")
    for r in rows:
        print(f"{r['setting']:>9} {r['windows']:>7} {r['calls']:>5} {r['wall_s']:>7} "
              f"{r['claude_s_sum']:>7} {r['slowest_call_s']:>7} {r['candidates']:>5} "
              f"{r['same_as_first']:>5}  {r['top_scores']}")


if __name__ == "__main__":
    main()
