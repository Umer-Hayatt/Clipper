"""Make each clip: get only its video, line it up with the audio to the frame, write
exact captions, frame it 9:16, add the title bar, encode."""

from pathlib import Path

import numpy as np

from . import captions, cards, config, hook, layout, scan, sfx
from .footage import Footage
from .theme import Theme
from .util import log, pcm, run, slugify, timer, write_json

PAD = 4.0   # extra seconds fetched on each side of a linked clip, trimmed after alignment


def _envelope(x, hop=80):
    n = len(x) // hop
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)


def locate(clip_file, audio_file, guess, search=30.0):
    """Where clip_file's first frame sits in the full video (seconds), found by matching
    its sound against the full audio. Returns (seconds, confidence 0-1)."""
    a = _envelope(pcm(clip_file, 8000, 0, 20)).astype(np.float32)
    b_start = max(0.0, guess - search)
    b = _envelope(pcm(audio_file, 8000, b_start, 20 + 2 * search)).astype(np.float32)
    if len(a) < 200 or len(b) <= len(a):
        return guess, 0.0
    a = (a - a.mean()) / (a.std() + 1e-9)
    win = np.lib.stride_tricks.sliding_window_view(b, len(a))
    mu, sd = win.mean(axis=1), win.std(axis=1) + 1e-9
    corr = ((win - mu[:, None]) @ a) / (len(a) * sd)
    k = int(np.argmax(corr))
    return b_start + k / 100.0, float(corr[k])


_HW = [  # hardware encoders, best first, with their quality settings
    ("h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"]),
    ("h264_qsv", ["-preset", "medium", "-global_quality", "22"]),
    ("h264_amf", ["-quality", "quality", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22"]),
    ("h264_videotoolbox", ["-b:v", "10M", "-profile:v", "high"]),
]
_CHOSEN = {}


def pick_encoder(want):
    """'libx264' or, for 'fast', the first hardware encoder that actually works here."""
    if want != "fast":
        return want
    if "fast" not in _CHOSEN:
        _CHOSEN["fast"] = "libx264"
        for name, args in _HW:
            try:
                run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                     "color=black:s=256x256:d=0.2", "-c:v", name, *args, "-f", "null", "-"],
                    timeout=30)
                _CHOSEN["fast"] = name
                break
            except Exception:  # noqa: BLE001 - not available on this machine
                continue
        log(f"encoder: {_CHOSEN['fast']}", "info")
    return _CHOSEN["fast"]


def _video_args(cfg):
    enc = pick_encoder(cfg["encoder"])
    for name, args in _HW:
        if name == enc:
            return ["-c:v", name, *args]
    return ["-c:v", enc, "-crf", str(cfg["crf"]), "-preset", cfg["preset"]]


def _caption_words(heard, part, offset, start, dur, cfg):
    """Words for the on-screen captions, in clip time, or [] for no captions.
    Captions are only made in cfg['caption_languages'] (default English): Whisper's
    small models write poor text for other languages, and wrong captions hurt more
    than none."""
    allowed = [x.lower() for x in cfg.get("caption_languages") or []]
    lang = (heard.get("language") or "").split("-")[0].lower()
    if lang and allowed and lang not in allowed:
        return []
    if (heard.get("source") or "").startswith("youtube"):
        # the platform's own word timings, shifted into clip time: no extra listening
        return [{"s": round(w["s"] - start, 2), "e": round(w["e"] - start, 2), "w": w["w"]}
                for w in heard["words"] if start <= w["s"] < start + dur]
    words, detected = scan.words_from(cfg["caption_model"], pcm(part, 16000, offset, dur),
                                      heard.get("language"), beam=cfg.get("caption_beam"))
    if allowed and (detected or "").lower() not in allowed:
        return []
    return words


def prepare(src, audio_file, moment, index, workdir, cfg, heard):
    """Get one clip's video, line it up with the audio, write its captions and sample its
    frames. Nothing is encoded yet."""
    start, end = moment["start"], moment["end"]
    dur = end - start
    name = f"{index:02d}_{slugify(moment.get('title') or moment.get('hook_text'), 40)}"
    log(f"[clip {index}] {moment.get('hook_text', '')}  ({dur:.0f} s)", "step")
    if src.is_local:
        part, offset = src.path, start            # exact: same file, no download
        footage = Footage.probe(part)
    else:
        with timer().stage("download.sections", index):
            part = src.section(start - PAD, end + PAD, workdir / f"part_{index:02d}.mp4", cfg)
        footage = Footage.probe(part)
        guess = max(0.0, start - PAD)
        with timer().stage("audio.align", index):
            at, conf = locate(part, audio_file, guess)
        offset = start - at if conf >= 0.5 else start - guess
        offset = min(max(offset, 0.0), max(0.0, footage.duration - dur))
        if conf < 0.5:
            log(f"  sound match weak ({conf:.2f}) -> trusting the requested times", "warn")
    if not footage.source_width:
        raise RuntimeError("this video has no picture")
    if cfg.get("crop_box"):
        footage = footage.cropped(cfg["crop_box"])     # later steps see only the cropped picture
    words = []
    if cfg["style"] != "comments" or cfg["comment_captions"]:
        with timer().stage("captions.words", index):
            words = _caption_words(heard, part, offset, start, dur, cfg)
    if not words and cfg["style"] != "comments":
        log("  no captions on this clip (not English, or no speech)", "info")
    with timer().stage("render.analyze", index):
        analysis = layout.analyze(part, offset, offset + dur, footage)
    return {"index": index, "name": name, "moment": moment, "part": part, "footage": footage,
            "offset": offset, "dur": dur, "words": words, "downloaded": not src.is_local,
            "analysis": analysis}


def learn(p, cfg, known):
    """Let every clip teach `known` (a list) where this video's facecam sits."""
    known.extend(layout.decide(p["analysis"], p["dur"], p["footage"], cfg, known).cams)


def out_fps(cfg, footage):
    """Frames per second of the finished clip: the video's own, capped at output_fps."""
    cap = cfg.get("output_fps")
    src = footage.fps
    try:
        return min(src, float(cap)) if cap not in (None, "source") else src
    except (TypeError, ValueError):
        return src


def overlays(cur, n_in, m, words, dur, name, workdir, cfg, theme, look, cam_h, footage, fps,
             faces=()):
    """Everything drawn over the framed video: captions, hook bar, comment cards.
    `cur` is the filter label of the video so far and `n_in` the next free ffmpeg input.
    Returns (filters, input args, final label, notes)."""
    filters, inputs, notes = [], [], {}
    rate = f"{fps:.3f}".rstrip("0").rstrip(".")
    sounds = []                                   # (seconds, sfx name, kind) for finish()

    show = cfg["captions"].get("show", True)
    if show == "auto":
        show = m.get("captions_on", True)
    if words and not show:
        notes["captions_off"] = True
        words = []

    beat_items = [b for b in (m.get("beats") or []) if 0 <= float(b.get("at", -1)) < dur]
    if not cfg.get("beats", {}).get("enabled"):
        beat_items = []
    if beat_items:
        focus = (cam_h / 2 / theme.H) if cam_h else 0.42
        zoom = captions.zoom_filter(beat_items, cfg, theme, cur, "vzm", focus)
        if zoom:
            filters.append(zoom)
            cur = "vzm"

    hook_png = hook_top = None
    hook_bottom = 0
    if m.get("hook_text"):
        try:
            hook_png, _, hh, margin = hook.render(m.get("hook_markup") or m["hook_text"], cfg,
                                                  workdir / f"{name}_hook.png", theme)
            seam_caps = cfg["captions"].get("anchor") == "seam" and bool(words)
            hook_top = theme.hook_top(None if seam_caps else cam_h, hh - 2 * margin) - margin
            hook_bottom = hook_top + hh - margin
        except (OSError, ValueError) as exc:
            log(f"  title bar skipped ({exc})", "warn")

    if words:
        hold = float(cfg["beats"].get("hold", 0.9))
        ass, boxes = captions.build(words, 0.0, dur, workdir / f"{name}.ass", cfg, theme,
                                    hook_bottom, cam_h, keywords=m.get("keywords") or (),
                                    seam=theme.seam(footage, look, cam_h),
                                    quiet=[(float(b["at"]), float(b["at"]) + hold)
                                           for b in beat_items])
        # ffmpeg runs inside workdir, so the subtitle file and font folder are named
        # without a path: Windows drive letters ("C:") break filter arguments otherwise.
        filters.append(f"[{cur}]ass=f='{ass.name}':fontsdir='fonts'[vcap]")
        cur = "vcap"
        notes["caption_phrases"] = len(boxes)

    if beat_items:
        # midway between the title bar and the seam (or the frame's middle): over the face,
        # clear of the title and of the captions
        low = theme.seam(footage, look, cam_h) or theme.H * 0.5
        title_above = hook_top is not None and hook_bottom < low
        high = max(hook_bottom if title_above else 0, theme.safe_top)
        low = min(low, hook_top) if hook_top is not None and not title_above else low
        center = (high + low) / 2 if low - high > theme.px(260) else theme.H * 0.40
        bass = captions.beats(beat_items, dur, workdir / f"{name}_beats.ass", cfg, theme, center)
        if bass:
            filters.append(f"[{cur}]ass=f='{bass.name}':fontsdir='fonts'[vbt]")
            cur = "vbt"
            notes["beats"] = len(beat_items)
            want = cfg["sfx"].get("on_beat")
            if want:
                # the references put one stinger on the payoff, not one per beat; every beat
                # is a candidate and finish() keeps the latest one(s) with a real pause after
                if want == "auto":
                    want = str(sfx.choose("sfx", m.get("kind"), name, cfg["sfx"], "stinger")
                               or "thud")
                elif want not in sfx.NAMES:
                    want = str(sfx.choose("sfx", None, name, dict(cfg["sfx"], sound=want))
                               or "thud")
                sounds += [(float(b["at"]), want, "beat") for b in beat_items]
    if beat_items:
        notes["payoff"] = max(float(b["at"]) for b in beat_items)

    if hook_png:
        inputs += ["-framerate", rate, "-i", str(hook_png)]
        filters.append(hook.overlay(cur, n_in, hook_top, fps, cfg, theme, "vhk"))
        cur, n_in = "vhk", n_in + 1
        if cfg["sfx"].get("on_hook"):
            sounds.append((0.0, cfg["sfx"]["on_hook"], "hook"))
    if cfg["sfx"].get("enabled") and sounds:
        notes["sfx"] = sorted(sounds, key=lambda s: s[0])
    if cfg.get("music", {}).get("enabled"):
        bed = sfx.choose("music", m.get("kind"), name, cfg["music"])
        if bed:
            notes["music"] = {"path": str(bed), "dur": dur,
                              "volume": float(cfg["music"].get("volume", 0.6)),
                              "fade_out": notes.get("payoff") or dur}
        else:
            log("  music skipped: the sound library has no music (see sound_library.py)", "warn")

    if cfg["style"] == "comments" and m.get("comments"):
        faces_bottom = None
        if look == "face" and faces:              # keep cards under the lowest face
            lows = sorted((f.y + f.h) * theme.H / max(1, footage.height) for f in faces)
            faces_bottom = lows[int(0.9 * (len(lows) - 1))]
        zones = theme.card_zones(look, theme.band(footage, look), hook_bottom, faces_bottom,
                                 captions_on=bool(words))
        placed = []
        for k, (item, a, b, z) in enumerate(cards.schedule(m["comments"], dur, zones,
                                                            cfg["comment_hold"])):
            try:
                png, w, h = cards.render(item, cfg, workdir / f"{name}_card{k:02d}.png", theme)
            except (OSError, ValueError) as exc:
                log(f"  card skipped ({exc})", "warn")
                continue
            inputs += ["-framerate", rate, "-i", str(png)]
            placed.append((w, h, a, b, z))
        if placed:
            body, cur = cards.overlay_chain(
                cur, placed, n_in, zones, theme.W, fps,
                soft=cfg["cards"].get("animation", "soft") == "soft", slide=theme.px(14))
            filters.append(body)
            n_in += len(placed)
            notes["cards"] = len(placed)
            log(f"  {len(placed)} comment card(s)", "info")
    return filters, inputs, cur, notes


def _place_hits(sounds, cfg, part, offset, dur):
    """Pick where the stinger(s) go. Beats are tried from the last (the payoff) back; each
    kept one moves into the next real pause in the clip's own sound, up to sfx.per_clip.
    A beat with talking all the way through is passed over (sfx.no_pause "skip", like the
    references) or, with "keep", used on the beat itself."""
    beats = sorted((s for s in sounds if s[2] == "beat"), key=lambda s: -s[0])
    rest = [s for s in sounds if s[2] != "beat"]
    want = max(0, int(cfg["sfx"].get("per_clip", 1)))
    reach = float(cfg["sfx"].get("pause_reach", 1.5))
    if not beats or want == 0:
        return rest
    if reach <= 0:
        return sorted(rest + beats[:want], key=lambda s: s[0])
    try:
        wave = pcm(part, 16000, offset, dur)
    except RuntimeError:
        wave = None
    keep = cfg["sfx"].get("no_pause", "skip") == "keep"
    placed = []
    for at, name, kind in beats:
        t = sfx.in_pause(at, wave, 16000, reach)
        if t is None:
            log(f"  no stinger at {at:.1f}s: talking all the way through", "info")
            if not keep:
                continue
            t = at
        placed.append((t, name, kind))
        if len(placed) == want:
            break
    if not placed:
        log("  stinger skipped: no beat in this clip is followed by a pause", "info")
    return sorted(rest + placed, key=lambda s: s[0])


def finish(p, outdir, workdir, cfg, known):
    """Frame, title, caption and encode one prepared clip."""
    m, dur, part, name = p["moment"], p["dur"], p["part"], p["name"]
    dest = outdir / f"{name}.mp4"
    log(f"[clip {p['index']}] framing + encoding", "step")
    cfg, look_name = config.for_clip(cfg, m["start"])
    if cfg.get("caption_preset") == "mix":
        log(f"  look: {look_name}", "info")
    theme = Theme(cfg)
    fps = out_fps(cfg, p["footage"])
    framing = layout.decide(p["analysis"], dur, p["footage"], cfg, known)
    for line in framing.notes:
        log(line, "info")
    chain, look, cam_h = framing.graph, framing.look, framing.cam_h
    filters = [p["footage"].prefilter(fps), chain]
    with timer().stage("render.overlays", p["index"]):
        extra, inputs, cur, notes = overlays(
            "vout", 1, m, p["words"], dur, name, workdir, cfg, theme, look, cam_h, p["footage"],
            fps, faces=p["analysis"].faces)
    filters += extra
    filters.append(f"[{cur}]format=yuv420p[v]")
    if notes.get("sfx"):
        notes["sfx"] = _place_hits(notes["sfx"], cfg, part, p["offset"], dur)
    if notes.get("sfx") or notes.get("music"):
        n_in = 1 + sum(1 for x in inputs if x == "-i")
        sfx_inputs, mix = sfx.mix(notes.get("sfx") or [], n_in, workdir,
                                  cfg["sfx"].get("volume", 0.55), music=notes.get("music"))
        inputs += sfx_inputs
        filters.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a0]" if cfg["loudnorm"]
                       else "[0:a]anull[a0]")
        filters.append(mix)
        if notes.get("sfx"):
            log("  sound effects: " + ", ".join(f"{Path(str(n)).stem} at {t:.1f}s"
                                                 for t, n, _ in notes["sfx"]), "info")
        if notes.get("music"):
            log(f"  music: {Path(notes['music']['path']).stem}, fading out at "
                f"{notes['music']['fade_out']:.1f}s", "info")
    else:
        filters.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]" if cfg["loudnorm"]
                       else "[0:a]anull[a]")
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-ss", f"{p['offset']:.3f}",
           "-t", f"{dur:.3f}", "-i", str(part), *inputs,
           "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
           "-c:a", "aac", "-b:a", cfg["audio_bitrate"], "-ar", "48000",
           *_video_args(cfg), "-pix_fmt", "yuv420p", "-t", f"{dur:.3f}",
           "-movflags", "+faststart", str(dest)]
    with timer().stage("render.encode", p["index"]):
        run(cmd, timeout=1800, cwd=str(workdir))
    if p["downloaded"]:
        part.unlink(missing_ok=True)
    log(f"  -> {dest.name} ({look} layout, {fps:.0f} fps)", "ok")
    return {
        "file": dest.name, "kind": m.get("kind"), "hook_text": m.get("hook_text"),
        "caption": m.get("caption"), "hashtags": m.get("hashtags", []),
        "score": m.get("score"), "reason": m.get("reason"), "layout": look,
        "style": cfg["style"], "look": look_name, "comments": m.get("comments", []),
        "beats": m.get("beats", []), "keywords": m.get("keywords", []),
        "stream_start": round(m["start"], 1), "stream_end": round(m["end"], 1),
        "duration": round(dur, 1), "fps": round(fps, 2),
        "writing_notes": m.get("writing_notes", []),
    }


def post_sheet(outdir, stream, clips, summary):
    """POST.md: what to paste under each clip, plus where it came from."""
    lines = [f"# {stream['name']}: {stream['title']}", "", f"Source: {stream['url']}", "",
             summary, ""]
    for c in clips:
        m, s = divmod(int(c["stream_start"]), 60)
        h, m = divmod(m, 60)
        lines += [f"## {c['file']}",
                  f"**{c['hook_text']}** · {c['kind']} · {c['duration']:.0f} s · score {c['score']}"
                  + (f" · look: {c['look']}" if c.get("look") not in (None, "clean") else ""),
                  "", c.get("caption") or "", "", " ".join(c.get("hashtags") or []), "",
                  f"_Why: {c.get('reason') or ''}_",
                  f"_From the video at {h}:{m:02d}:{s:02d}_", "", "---", ""]
    (outdir / "POST.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(outdir / "clips.json", {"stream": stream, "clips": clips})
