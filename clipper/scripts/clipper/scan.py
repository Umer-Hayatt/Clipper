"""Listen to the whole video: a fast transcript, a loudness curve (screams, hype) and,
on YouTube live replays, how fast chat was moving plus what chat said. All local, free."""

import json
import os
import subprocess
import threading

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")   # Windows: harmless, noisy
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import numpy as np

from .util import log, pcm, read_json, timer, write_json

_MODELS = {}
_LOCK = threading.Lock()


def _model(name):
    """One Whisper model per size, shared between videos processed in parallel."""
    with _LOCK:
        if name not in _MODELS:
            from faster_whisper import WhisperModel
            log(f"loading Whisper '{name}' (the first run downloads it)...", "step")
            _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8",
                                         cpu_threads=os.cpu_count() or 4)
        return _MODELS[name]


def words_from(model_name, audio_array, language, offset=0.0, beam=None):
    np.seterr(divide="ignore", over="ignore", invalid="ignore")
    segments, info = _model(model_name).transcribe(
        audio_array, language=language, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}, beam_size=int(beam) if beam else (1 if model_name == "tiny" else 5),
        condition_on_previous_text=False,
    )
    out = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                out.append({"s": round(w.start + offset, 2), "e": round(w.end + offset, 2), "w": text})
    return out, info.language


def _json3_words(path):
    """Words with times from a YouTube json3 caption file. Auto (speech recognition)
    tracks are timed per word; human-made tracks per line, so words are spread over it."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for ev in data.get("events", []):
        segs, t0 = ev.get("segs") or [], ev.get("tStartMs", 0)
        if ev.get("aAppend") or not segs:
            continue
        timed = any("tOffsetMs" in s for s in segs)
        if timed:
            for s in segs:
                text = (s.get("utf8") or "").strip()
                if text and not (text.startswith("[") and text.endswith("]")):
                    out.append({"s": (t0 + s.get("tOffsetMs", 0)) / 1000, "w": text})
        else:
            words = " ".join(s.get("utf8") or "" for s in segs).split()
            words = [w for w in words if not (w.startswith("[") and w.endswith("]"))]
            dur = ev.get("dDurationMs", 2000) / 1000
            for i, w in enumerate(words):
                out.append({"s": t0 / 1000 + dur * i / max(1, len(words)), "w": w})
    out.sort(key=lambda w: w["s"])
    for i, w in enumerate(out):        # a word lasts until the next one starts (max 1.2 s)
        nxt = out[i + 1]["s"] if i + 1 < len(out) else w["s"] + 0.6
        w["e"] = round(min(nxt, w["s"] + 1.2), 2)
        w["s"] = round(w["s"], 2)
    return out


def transcript(src, audio_path, lo, hi, workdir, cfg, info):
    """Transcript of seconds [lo, hi) in video time. The platform's own captions when it
    has them (instant, no CPU), else local Whisper block by block. Each block is cached,
    so a stopped run resumes where it stopped."""
    cache = workdir / "scan.json"
    timer().cache("transcript", cache.exists())
    if cache.exists():
        data = read_json(cache)
        log(f"reusing transcript ({len(data['words'])} words)", "ok")
        return data
    if cfg.get("platform_captions", True):
        subs = src.captions(workdir.parent if workdir.name.startswith("part_") else workdir, info)
        if subs:
            words = [w for w in _json3_words(subs) if lo <= w["s"] < hi]
            if sum(1 for _ in words) >= 30:
                track = info["caption_track"]
                data = {"words": words, "language": cfg["language"] or track["spoken"],
                        "source": "youtube" if track["auto"] else "youtube-manual"}
                write_json(cache, data)
                log(f"using the video's own captions ({len(words)} words, no listening needed)", "ok")
                return data
    block = cfg["scan_block_minutes"] * 60
    words, language = [], cfg["language"]
    starts = list(np.arange(lo, hi, block))
    log(f"listening to {(hi - lo) / 60:.0f} min in {len(starts)} block(s)...", "step")
    for i, t in enumerate(starts):
        part = workdir / f"scan_{i:03d}.json"
        if part.exists():
            got = read_json(part)
        else:
            arr = pcm(audio_path, 16000, start=float(t), duration=float(min(block, hi - t)))
            got, lang = words_from(cfg["scan_model"], arr, language, offset=float(t))
            language = language or lang       # detect once, then keep it
            write_json(part, got)
        words += got
        log(f"  {(min(t + block, hi) - lo) / 60:.0f}/{(hi - lo) / 60:.0f} min heard", "info")
    if not words:
        raise RuntimeError("no speech found in this video")
    data = {"words": words, "language": language, "source": "whisper"}
    write_json(cache, data)
    for p in workdir.glob("scan_*.json"):
        p.unlink()
    return data


def loudness(audio_path, workdir):
    """Loudness per second in dB, streamed through ffmpeg so it never loads the whole file."""
    cache = workdir / "loud.json"
    timer().cache("loudness", cache.exists())
    if cache.exists():
        return read_json(cache)
    rate = 8000
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(audio_path), "-vn", "-ac", "1",
         "-ar", str(rate), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    out = []
    while True:
        buf = proc.stdout.read(rate * 2 * 60)   # one minute at a time
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        n = len(x) // rate
        if n:
            sec = x[: n * rate].reshape(n, rate)
            out += (20 * np.log10(np.sqrt((sec ** 2).mean(axis=1)) + 1e-6)).round(1).tolist()
    proc.wait()
    write_json(cache, out)
    return out


def relative(values, window=120):
    """How far each second sits above the median of the 2 minutes around it."""
    v = np.asarray(values, dtype=np.float32)
    if len(v) < 5:
        return np.zeros_like(v)
    half = window // 2
    padded = np.pad(v, (half, half), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window + 1)[: len(v)]
    return v - np.median(windows[:, ::4], axis=1)


def _emoji(e):
    """A chat emoji as text: the real character for standard emoji (YouTube keeps it in
    emojiId), else its :shortcut: (channel emotes, which can't be drawn)."""
    eid = e.get("emojiId") or ""
    if eid and len(eid) <= 8 and not eid.isascii():
        return eid
    return (e.get("shortcuts") or [""])[0]


def chat(source, workdir, cfg, info):
    """(messages per 5 s, [[second, text], ...]) from a live-chat replay, or (None, [])."""
    rate_cache, msg_cache = workdir / "chat.json", workdir / "chat_messages.json"
    if rate_cache.exists():
        return read_json(rate_cache), (read_json(msg_cache) if msg_cache.exists() else [])
    raw = source.chat_file(workdir, cfg, info)
    if not raw:
        return None, []
    buckets, messages = {}, []
    with open(raw, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                item = json.loads(line)["replayChatItemAction"]
                ms = int(item.get("videoOffsetTimeMsec", 0))
            except (ValueError, KeyError, TypeError):
                continue
            actions = item.get("actions", [])
            buckets[ms // 5000] = buckets.get(ms // 5000, 0) + len(actions)
            for act in actions:
                renderer = (act.get("addChatItemAction", {}).get("item", {})
                            .get("liveChatTextMessageRenderer"))
                if not renderer:
                    continue
                runs = renderer.get("message", {}).get("runs", [])
                text = "".join(r.get("text") or _emoji(r.get("emoji") or {}) for r in runs).strip()
                if text:
                    messages.append([round(ms / 1000, 1), text[:200]])
    raw.unlink(missing_ok=True)
    if not buckets:
        return None, []
    rate = [0] * (max(buckets) + 1)
    for k, n in buckets.items():
        rate[k] = n
    write_json(rate_cache, rate)
    write_json(msg_cache, messages)
    log(f"chat replay: {sum(rate):,} messages", "ok")
    return rate, messages
