"""Shared helpers: running commands, decoding audio, logging, timing."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

if os.name == "nt":
    os.system("")                      # turn on ANSI colours in the Windows console
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_START = time.time()
_PRINT = threading.Lock()
_local = threading.local()


def set_tag(tag):
    """Prefix every log line from this thread (one thread per video)."""
    _local.tag = tag


def log(msg, level="info"):
    mark = {
        "info": "\033[36m·\033[0m",
        "ok": "\033[32m✓\033[0m",
        "warn": "\033[33m!\033[0m",
        "err": "\033[31m✗\033[0m",
        "step": "\033[35m▸\033[0m",
    }.get(level, "·")
    el = time.time() - _START
    tag = getattr(_local, "tag", "")
    tag = f"\033[1m{tag}\033[0m " if tag else ""
    with _PRINT:
        print(f"{mark} [{int(el // 60):02d}:{int(el % 60):02d}] {tag}{msg}", flush=True)


def die(msg):
    log(msg, "err")
    sys.exit(1)


def which(binary):
    return shutil.which(binary)


def run(cmd, capture=True, check=True, timeout=None, cwd=None, input=None):
    """Run a command. Returns stdout text (or '' when not capturing)."""
    proc = subprocess.run(cmd, capture_output=capture, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, cwd=cwd, input=input)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        tail = "\n".join(detail.splitlines()[-15:])
        raise RuntimeError(f"command failed ({proc.returncode}): {os.path.basename(str(cmd[0]))}\n{tail}")
    return (proc.stdout or "") if capture else ""


def require(binary, hint=""):
    if which(binary) is None:
        die(f"'{binary}' not found. {hint}")


def clean_env():
    """Environment for child `claude -p` calls (works even when launched from Claude Code)."""
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(key, None)
    return env


def pcm(path, rate, start=None, duration=None):
    """Decode audio to mono float32 at `rate` Hz through ffmpeg (never touches disk)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {path}: "
                           f"{proc.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def probe(path):
    """{width, height, duration, fps} for a media file (width/height 0 if audio only)."""
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_streams", "-show_format", str(path)])
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    fps = 30.0
    if video:
        num, _, den = video.get("avg_frame_rate", "0/0").partition("/")
        try:
            fps = float(num) / float(den) if float(den) else 30.0
        except (ValueError, ZeroDivisionError):
            fps = 30.0
    return {
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
        "duration": float(data["format"].get("duration") or 0),
        "fps": fps or 30.0,
    }


def hhmmss(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def seconds(stamp):
    """'1:02:03' / '62:03' / '3723' -> 3723.0"""
    total = 0.0
    for part in str(stamp).strip().split(":"):
        total = total * 60 + float(part)
    return total


def minutes(secs):
    """'4 h 09 min' / '12 min' / '40 s' — for the human summary."""
    secs = int(round(secs))
    if secs < 60:
        return f"{secs} s"
    h, m = divmod(secs // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def slugify(text, limit=48):
    keep = []
    for ch in (text or "").lower():
        if ch.isascii() and ch.isalnum():
            keep.append(ch)
        elif ch in " -_" and (not keep or keep[-1] != "-"):
            keep.append("-")
    return "".join(keep).strip("-")[:limit].strip("-") or "clip"


def even(n):
    n = int(round(n))
    return n - (n % 2)


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# Report groups, in the order they are shown. A stage is named "<group>.<what>".
GROUPS = [("download", "download"), ("audio", "audio processing"), ("transcript", "transcript"),
          ("moments", "moment selection"), ("captions", "caption generation"),
          ("render", "rendering")]


class Timer:
    """Records how long each stage took, plus cache hits/misses and Claude calls, for the
    end-of-run report. Stage names look like "render.encode"; the part before the dot is
    the report group. Safe to use from several threads."""

    def __init__(self):
        self.stages = {}
        self.clips = {}          # clip index -> {stage: seconds}
        self.hits, self.misses = [], []
        self.calls = []          # Claude calls: {purpose, seconds, prompt_chars, reply_chars}
        self.t0 = time.time()
        self._lock = threading.Lock()

    def add(self, name, secs, clip=None):
        with self._lock:
            self.stages[name] = self.stages.get(name, 0) + secs
            if clip is not None:
                per = self.clips.setdefault(clip, {})
                per[name] = per.get(name, 0) + secs

    def stage(self, name, clip=None):
        timer = self

        class _Ctx:
            def __enter__(self):
                self.start = time.time()

            def __exit__(self, *exc):
                timer.add(name, time.time() - self.start, clip)

        return _Ctx()

    def cache(self, item, hit):
        with self._lock:
            (self.hits if hit else self.misses).append(item)

    def call(self, purpose, secs, prompt_chars, reply_chars):
        with self._lock:
            self.calls.append({"purpose": purpose, "seconds": round(secs, 1),
                               "prompt_chars": prompt_chars, "reply_chars": reply_chars})

    def total(self):
        return time.time() - self.t0

    def groups(self):
        """{label: seconds} per report group, plus 'other' (time outside any stage)."""
        out = {}
        for key, label in GROUPS:
            out[label] = sum(v for k, v in self.stages.items() if k.split(".")[0] == key)
        known = {k for k, _ in GROUPS}
        extra = sum(v for k, v in self.stages.items() if k.split(".")[0] not in known)
        out["other"] = max(0.0, self.total() - sum(out.values()) - extra) + extra
        return out

    def report(self):
        groups = self.groups()
        slowest = max(groups, key=groups.get)
        return {
            "total_seconds": round(self.total(), 1),
            "stages_seconds": {k: round(v, 1) for k, v in groups.items()},
            "slowest_stage": slowest,
            "detail_seconds": {k: round(v, 1) for k, v in sorted(self.stages.items())},
            "per_clip_seconds": {str(i): {k: round(v, 1) for k, v in sorted(d.items())}
                                 for i, d in sorted(self.clips.items())},
            "cache": {"hits": list(self.hits), "misses": list(self.misses)},
            "claude_calls": list(self.calls),
        }

    def table(self):
        """The report as text lines, slowest stage marked."""
        groups, total = self.groups(), max(self.total(), 0.001)
        slowest = max(groups, key=groups.get)
        lines = []
        for label, secs in groups.items():
            mark = "  <- slowest" if label == slowest and secs > 0 else ""
            lines.append(f"    {label:<20}{secs:7.1f} s  {100 * secs / total:4.0f} %{mark}")
        lines.append(f"    {'total':<20}{total:7.1f} s")
        lines.append(f"    cache: {len(self.hits)} hit(s) [{', '.join(self.hits) or '-'}], "
                     f"{len(self.misses)} miss(es) [{', '.join(self.misses) or '-'}]")
        if self.calls:
            lines.append("    Claude calls: " + ", ".join(
                f"{c['purpose']} {c['seconds']:.0f} s" for c in self.calls))
        return lines


_NO_TIMER = Timer()


def set_timer(timer):
    """Make `timer` the one this thread reports to (one thread per video)."""
    _local.timer = timer


def timer():
    return getattr(_local, "timer", None) or _NO_TIMER
