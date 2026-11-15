#!/usr/bin/env python3
"""clip: long videos in, ready-to-post vertical clips out.

  clip LINK-OR-FILE [more ...]          clip these (several run at the same time)
  clip URL --profile gta6               use the GTA 6 profile for picking moments
  clip "D:/rec.mp4" --section 1:30:00-1:45:00

Clips land in <clips folder>/<date>/<source>__<title>/ with a POST.md (caption +
hashtags per clip). Re-running the same command resumes; nothing is redone.
"""

import argparse
import datetime
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper import config, crop, moments, render, scan, source  # noqa: E402
from clipper.footage import Footage  # noqa: E402
from clipper.util import (Timer, die, hhmmss, log, minutes, probe, require,  # noqa: E402
                          seconds, set_tag, set_timer, slugify, write_json)


def parse(argv):
    p = argparse.ArgumentParser(prog="clip", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", help="links (YouTube, Twitch, Kick...) or video files")
    p.add_argument("--profile", help="what a great moment is: gta6, general, or a .md file")
    p.add_argument("--clips", type=int, help="clips per video (default 3)")
    p.add_argument("--parallel", type=int, default=2, help="videos processed at once (default 2)")
    p.add_argument("--section", metavar="RANGE",
                   help='only part of the video, e.g. "1:00:00-3:30:00"')
    p.add_argument("--cookies", choices=["chrome", "edge", "firefox", "brave", "safari"],
                   help="use your browser login (age-restricted YouTube, Kick)")
    p.add_argument("--layout", choices=["auto", "stream", "face", "fit"])
    p.add_argument("--style", choices=["auto", "comments", "captions"],
                   help="comments: chat cards above/below the gameplay; captions: word captions "
                        "(default auto: comments when the video has a chat replay)")
    p.add_argument("--model", dest="claude_model", help="Claude model for picking, e.g. sonnet")
    p.add_argument("--language", help='spoken language, e.g. en, ur, hi (default: detect)')
    p.add_argument("--min", dest="min_duration", type=int, help="shortest clip in seconds")
    p.add_argument("--max", dest="max_duration", type=int, help="longest clip in seconds")
    p.add_argument("--fast", action="store_true", help="hardware encoding (much faster)")
    p.add_argument("--out", help="folder for clips and cache (default ~/clips or CLIPPER_HOME)")
    p.add_argument("--keep-audio", action="store_true", help="keep downloaded audio after clipping")
    p.add_argument("--redo", action="store_true", help="pick the moments again (keeps the audio)")
    p.add_argument("--rewrite", action="store_true",
                   help="write the hooks/captions again (after editing editorial.md); "
                        "keeps the picked moments")
    p.add_argument("--crop", metavar="auto|x,y,w,h",
                   help="keep only this part of the picture, e.g. the shared screen in a "
                        "recording of a video call: auto finds it, or give x,y,w,h in pixels "
                        "(local files only). Saves crop_preview.jpg in the work folder")
    p.add_argument("--look", choices=sorted(config.PRESETS) + ["mix"],
                   help="caption look: clean (default), bold, boxed, seam, punch, keyword, serif, "
                        "none (no captions), or mix (a different look per clip)")
    p.add_argument("--title", choices=["bar", "pill", "text"],
                   help="title style: bar (white plate), pill (red plate), text (no plate)")
    p.add_argument("--beats", action="store_true",
                   help="flash a big word with a punch-in zoom on each clip's peak moment")
    p.add_argument("--sfx", action="store_true",
                   help="add one stinger per clip from the sound library, on the payoff beat, "
                        "in the next pause (needs --beats; skipped when there is no pause)")
    p.add_argument("--music", action="store_true",
                   help="add a background music bed from the sound library, ducked under "
                        "talking, fading out at the payoff")
    p.add_argument("--fps", dest="output_fps",
                   help='frames per second of the clips: a number (default 30) or "source"')
    return p.parse_args(argv)


def one_video(arg, cfg, opts, profile):
    """Everything for one video. Returns a summary dict (or raises)."""
    timer = Timer()
    set_timer(timer)
    src = source.open_source(arg, opts.cookies)
    work = config.HOME / "work" / source.work_key(arg)   # audio, loudness, transcript cache
    work.mkdir(parents=True, exist_ok=True)

    with timer.stage("download.info"):
        info = src.info(work)
    set_tag(info["name"][:18])
    if info.get("live_status") == "is_live":
        log("this stream is still live: clipping what has been streamed so far", "warn")

    with timer.stage("download.audio"):
        audio = src.audio(work)
    length = probe(audio)["duration"]
    lo, hi = 0.0, length
    if opts.section:
        a, _, b = opts.section.lstrip("*").partition("-")
        lo, hi = seconds(a), (min(seconds(b), length) if b else length)
        if not 0 <= lo < hi:
            raise RuntimeError(f"--section {opts.section} is outside the video (0-{hhmmss(length)})")
    pick_dir = work if not opts.section else work / f"part_{int(lo)}_{int(hi)}"
    pick_dir.mkdir(exist_ok=True)
    if opts.redo:
        moments.clear(pick_dir)                  # pick again and write again
    elif opts.rewrite:
        moments.clear(pick_dir, picks=False)     # keep the moments, write the text again
    log(f"{info['title'][:70]}  ·  {minutes(length)}"
        + (f", clipping {hhmmss(lo)}-{hhmmss(hi)}" if opts.section else ""), "ok")

    cfg = dict(cfg)
    if opts.crop:
        if not src.is_local:
            log("--crop works on local files only: ignored for links", "warn")
        else:
            meta = probe(src.path)
            with timer.stage("render.crop"):
                box, how = crop.detect(src.path, lo, hi, meta, pick_dir, opts.crop)
            if box:
                try:      # a box outside the picture fails here, not in the first render
                    box = Footage.from_probe(meta).cropped(box).crop
                except ValueError as exc:
                    raise RuntimeError(f"--crop: {exc}") from exc
            cfg["crop_box"] = box
            log("crop: " + (f"{box[2]}x{box[3]} at {box[0]},{box[1]} ({how})" if box
                            else f"none ({how}); using the whole picture"), "ok" if box else "warn")
    with timer.stage("transcript.scan"):
        heard = scan.transcript(src, audio, lo, hi, pick_dir, cfg, info)
    with timer.stage("audio.loudness"):
        loud = scan.relative(scan.loudness(audio, work))
    with timer.stage("transcript.chat"):
        chat, messages = scan.chat(src, work, cfg, info)
    stream = {"name": info["name"], "title": info["title"], "url": src.url}
    with timer.stage("moments.pick"):
        picked = moments.find(heard, loud, chat, pick_dir, cfg, stream, profile)
    if cfg["style"] == "auto":
        cfg["style"] = "comments" if messages else "captions"
    if cfg["style"] == "comments" and cfg["layout"] == "auto":
        # cards need the bands above and below the game
        cfg["layout"], cfg["fit_aspect"] = "fit", cfg["comment_fit_aspect"]
    with timer.stage("captions.text"):
        picked = moments.write(picked, messages, heard["words"], pick_dir, cfg, stream, profile,
                               config.editorial_text(cfg))
    log(f"style: {cfg['style']}" + (" (real chat messages)" if cfg["style"] == "comments"
                                     and messages else ""), "info")

    day = datetime.date.today().isoformat()
    outdir = config.HOME / day / f"{slugify(info['name'], 24)}__{slugify(info['title'], 40)}"
    outdir.mkdir(parents=True, exist_ok=True)
    made, ready, known = [], [], []
    if True:
        for i, m in enumerate(picked, 1):
            try:
                ready.append(render.prepare(src, audio, m, i, pick_dir, cfg, heard))
            except Exception as exc:  # noqa: BLE001 - one bad clip must not stop the rest
                log(f"[clip {i}] failed: {str(exc)[:300]}", "err")
        for p in ready:              # learn where this video's facecam sits from every clip
            render.learn(p, cfg, known)
        for p in ready:
            try:
                made.append(render.finish(p, outdir, pick_dir, cfg, known))
            except Exception as exc:  # noqa: BLE001
                log(f"[clip {p['index']}] failed: {str(exc)[:300]}", "err")

    summary = (f"{minutes(hi - lo)} of video -> {len(made)} clip(s) in {minutes(timer.total())} "
               f"(" + ", ".join(f"{k} {minutes(v)}" for k, v in timer.groups().items() if v >= 0.5)
               + ")")
    render.post_sheet(outdir, stream, made, summary)
    write_json(outdir / "report.json", {
        "source": stream, "section": [round(lo), round(hi)], "clips": len(made),
        **timer.report(), "style": cfg["style"],
        "profile": cfg["profile"], "claude_parallel": cfg["claude_parallel"],
        "scan_model": cfg["scan_model"], "encoder": render.pick_encoder(cfg["encoder"]),
        "output_fps": cfg.get("output_fps"),
        "writing_notes": {c["file"]: c["writing_notes"] for c in made if c.get("writing_notes")}})
    if not src.is_local and not opts.keep_audio and made:
        audio.unlink(missing_ok=True)
    log(summary, "ok")
    for line in timer.table():
        log(line, "info")
    set_tag("")
    return {"name": info["name"], "title": info["title"], "outdir": outdir, "clips": made,
            "summary": summary}


def main(argv=None):
    opts = parse(argv if argv is not None else sys.argv[1:])
    if opts.out:
        config.HOME = Path(opts.out).expanduser().resolve()
    require("ffmpeg", "Install it: winget install Gyan.FFmpeg  (Mac: brew install ffmpeg)")
    require("claude", "Install Claude Code first.")
    if any(not source.is_file(a) for a in opts.inputs):
        require("yt-dlp", "Run scripts/setup.py")
    cfg = config.load({
        "clips_per_stream": opts.clips, "layout": opts.layout, "claude_model": opts.claude_model,
        "min_duration": opts.min_duration, "max_duration": opts.max_duration,
        "encoder": "fast" if opts.fast else None, "profile": opts.profile,
        "style": opts.style, "output_fps": opts.output_fps,
        "language": opts.language,
    })
    if opts.look == "mix":
        cfg["caption_preset"] = "mix"
    elif opts.look:
        cfg, _ = config.for_clip(dict(cfg, caption_preset="mix", mix_presets=[opts.look]), 0)
        cfg["caption_preset"] = opts.look
    if opts.title:
        cfg["hook"]["style"] = opts.title
    if opts.beats:
        cfg["beats"]["enabled"] = True
    if opts.sfx:
        cfg["sfx"]["enabled"] = True
    if opts.music:
        cfg["music"]["enabled"] = True
    profile = config.profile_text(cfg["profile"])
    config.HOME.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(config.HOME).free / 1e9
    if free_gb < 2:
        log(f"only {free_gb:.1f} GB free on this disk; links need ~0.1 GB per hour", "warn")
    log(f"profile: {cfg['profile']}  ·  clips go to {config.HOME}", "info")

    results = []
    with ThreadPoolExecutor(max_workers=max(1, opts.parallel)) as pool:
        futures = {pool.submit(one_video, a, cfg, opts, profile): a for a in opts.inputs}
        for fut, arg in futures.items():
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "confirm your age" in msg or "Sign in to confirm" in msg:
                    msg = ("YouTube wants a login for this one (age-restricted, common for GTA). "
                           "Run it again with --cookies chrome (or the browser you're signed into).")
                elif "403" in msg and "kick" in arg:
                    msg = "Kick blocked the download. Run it again with --cookies chrome."
                log(f"{arg}: {msg[:400]}", "err")

    print()
    total = sum(len(r["clips"]) for r in results)
    log(f"done: {total} clip(s) from {len(results)} video(s)", "ok" if total else "warn")
    for r in results:
        print(f"\n  {r['name']} · {r['title'][:60]}\n  {r['summary']}\n  {r['outdir']}")
        for c in r["clips"]:
            print(f"    [{c['score']}] {c['duration']:>3.0f}s @{hhmmss(c['stream_start'])}  {c['hook_text']}")
    print()
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
