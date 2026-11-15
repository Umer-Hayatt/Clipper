#!/usr/bin/env python3
"""specimen: preview the look (hook bar, captions, comment cards) in seconds, without
running the whole pipeline. Draws them over a still image or a plain background with
exactly the code the real render uses, and saves a few PNG frames.

  python specimen.py --out DIR                          captions over a plain background
  python specimen.py --frame shot.jpg --style comments  cards over your own frame
  python specimen.py --hook "A *long* title to test wrapping" --set captions.case=upper

--set KEY=VALUE changes any setting for this preview (dots reach nested keys)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper import config, layout, render  # noqa: E402
from clipper.footage import Footage  # noqa: E402
from clipper.theme import Theme  # noqa: E402
from clipper.util import run  # noqa: E402

SAY = ("I'm just relaxing and enjoying the drive, okay? Nothing can go wrong. "
       "Wait, why is the screen frozen? No way, there's absolutely no way it crashed!")
CHAT = ["he really said it", "the timing though", "chat called it 10 minutes ago lol",
        "NO WAY that just happened on stream"]
REACT = ["he really said it", "not the crash", "the timing is criminal", "server heard him"]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="specimen-out")
    p.add_argument("--frame", help="a still image to draw over (any size)")
    p.add_argument("--style", choices=["captions", "comments", "chat"], default="captions",
                   help="comments = reaction stickers, chat = real-chat cards")
    p.add_argument("--layout", choices=["fit", "stream", "full"], default="fit")
    p.add_argument("--hook", default="He jokes about logging out, then *the server crashes*")
    p.add_argument("--say", default=SAY)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--times", default="0.15,2.2,5.0,8.5")
    p.add_argument("--name", default="specimen")
    p.add_argument("--preset", help="a caption preset: " + ", ".join(config.PRESETS))
    p.add_argument("--keywords", default="screen frozen,crashed",
                   help="comma list Claude would mark as key words (keyword preset)")
    p.add_argument("--beats", default="",
                   help='big emphasis words, e.g. "6.0:NO WAY!" (comma list of at:text)')
    a = p.parse_args()

    cfg = config.load()
    if a.preset:
        cfg, _ = config.for_clip(dict(cfg, caption_preset="mix", mix_presets=[a.preset]), 0)
    for item in a.set:
        key, _, value = item.partition("=")
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        node = cfg
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    cfg["style"] = "captions" if a.style == "captions" else "comments"
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    theme = Theme(cfg)
    W, H, dur, fps = theme.W, theme.H, 10.0, 30.0

    tokens = a.say.split()
    step = (dur - 0.6) / len(tokens)
    words = [{"s": round(0.3 + i * step, 2), "e": round(0.3 + (i + 0.85) * step, 2), "w": t}
             for i, t in enumerate(tokens)]
    lines = CHAT if a.style == "chat" else REACT
    moment = {"hook_text": a.hook.replace("*", ""), "hook_markup": a.hook, "comments": [
        {"at": 1.0 + 2.1 * i, "text": t, "kind": "chat" if a.style == "chat" else "reaction"}
        for i, t in enumerate(lines)]}
    moment["keywords"] = [k for k in a.keywords.split(",") if k]
    moment["beats"] = [{"at": float(b.split(":", 1)[0]), "text": b.split(":", 1)[1]}
                       for b in a.beats.split(",") if ":" in b]
    if moment["beats"]:
        cfg["beats"]["enabled"] = True
    if a.style != "captions" and not cfg["comment_captions"]:
        words = []

    footage = Footage(1920, 1080, fps, dur)
    cam_h = None
    if a.frame:
        src = ["-loop", "1", "-framerate", "30", "-t", str(dur), "-i", str(Path(a.frame).resolve())]
    else:
        src = ["-f", "lavfi", "-t", str(dur), "-i",
               "gradients=s=1920x1080:c0=0x2b3a55:c1=0x6b4a3a:x0=0:y0=0:x1=1920:y1=1080:r=30"]
    if a.layout == "fit":
        look = "fit"
        if a.style != "captions":
            cfg["fit_aspect"] = cfg["comment_fit_aspect"]
        base = "[0:v]scale=1920:1080,setsar=1[base];" + layout.fit(cfg, footage)
    elif a.layout == "stream":
        look, cam_h = "stream", cfg["cam_height"]
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[vout]"
    else:
        look = "face"
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[vout]"

    filters, inputs, cur, notes = render.overlays(
        "vout", 1, moment, words, dur, a.name, out, cfg, theme, look, cam_h, footage, fps)
    graph = ";".join([base] + filters + [f"[{cur}]format=yuv420p[v]"])
    clip = out / f"{a.name}.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-nostdin", *src, *inputs, "-filter_complex", graph,
         "-map", "[v]", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
         str(clip)], cwd=str(out), timeout=600)
    for t in a.times.split(","):
        shot = out / f"{a.name}_{float(t):05.2f}.png"
        run(["ffmpeg", "-y", "-v", "error", "-nostdin", "-ss", t, "-i", str(clip),
             "-frames:v", "1", str(shot)], timeout=120)
        print(shot)
    print(clip, notes)


if __name__ == "__main__":
    main()
