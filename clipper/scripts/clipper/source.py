"""Where a video comes from: a link (YouTube, Twitch, Kick... via yt-dlp) or a file on
this computer. For links, the whole video is fetched as AUDIO only; video is fetched only
for the few minutes that become clips. A 6-hour stream costs ~300 MB, not ~10 GB.
Local files are only read, never changed."""

import glob
import hashlib
import json
import re
import time
from pathlib import Path

from .util import log, probe, read_json, run, timer, which, write_json


def is_file(arg):
    return Path(arg).expanduser().is_file()


def work_key(arg):
    """A stable folder name for this input's cache."""
    if is_file(arg):
        p = Path(arg).expanduser().resolve()
        st = p.stat()
        tag = hashlib.sha1(f"{p}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:10]
        return "file_" + re.sub(r"[^A-Za-z0-9_-]", "_", p.stem)[-50:] + "_" + tag
    return re.sub(r"[^A-Za-z0-9_-]", "_", arg.split("://")[-1])[-80:]


class LocalFile:
    is_local = True

    def __init__(self, arg):
        self.path = Path(arg).expanduser().resolve()
        self.url = str(self.path)

    def info(self, workdir):
        meta = probe(self.path)
        return {"name": self.path.stem[:40], "title": self.path.stem, "duration": meta["duration"],
                "live_status": None, "extractor_key": "local", "webpage_url": str(self.path)}

    def audio(self, workdir):
        return self.path                  # ffmpeg reads the audio straight from the file

    def section(self, start, end, dest, cfg):
        return self.path                  # rendered straight from the file, no copy

    def captions(self, workdir, info):
        return None

    def chat_file(self, workdir, cfg, info):
        return None


class Link:
    is_local = False

    def __init__(self, url, cookies=None):
        self.url = url
        self.args = []
        self._full = None             # yt-dlp's full metadata with stream URLs (made by info())
        if "youtu" in url and not which("deno"):
            for runtime in ("node", "bun"):   # yt-dlp needs a JS runtime for YouTube
                path = which(runtime)
                if path:
                    self.args += ["--js-runtimes", f"{runtime}:{path}"]
                    break
        if cookies:
            self.args += ["--cookies-from-browser", cookies]

    def info(self, workdir):
        """Video metadata (title, channel, length, live status). Cached."""
        cache = workdir / "info.json"
        timer().cache("info", cache.exists())
        if cache.exists():
            if (workdir / "info_full.json").exists():
                self._full = workdir / "info_full.json"
            return read_json(cache)
        raw = run(["yt-dlp", "-J", "--no-playlist", *self.args, self.url], timeout=180)
        full = json.loads(raw)
        # Kept so each clip's section can skip yt-dlp's start-up (see section()).
        (workdir / "info_full.json").write_text(raw, encoding="utf-8")
        self._full = workdir / "info_full.json"
        keep = {k: full.get(k) for k in (
            "id", "title", "channel", "uploader", "duration", "view_count", "live_status",
            "was_live", "is_live", "webpage_url", "extractor_key", "timestamp", "language",
        )}
        keep["name"] = keep.get("channel") or keep.get("uploader") or "stream"
        keep["title"] = keep.get("title") or keep["name"]
        keep["caption_track"] = _pick_track(full)
        write_json(cache, keep)
        return keep

    def captions(self, workdir, info):
        """The platform's own transcript (YouTube auto/manual captions) as json3, or None."""
        track = info.get("caption_track")
        if not track:
            return None
        dest = workdir / f"subs.{track['lang']}.json3"
        timer().cache("platform captions", dest.exists())
        if dest.exists():
            return dest
        flag = "--write-auto-subs" if track["auto"] else "--write-subs"
        try:
            run(["yt-dlp", "--skip-download", flag, "--sub-langs", track["lang"],
                 "--sub-format", "json3", "-o", str(workdir / "subs"), *self.args, self.url],
                timeout=300)
        except Exception as exc:  # noqa: BLE001 - fall back to Whisper
            log(f"captions not downloaded ({str(exc).splitlines()[0][:80]})", "warn")
            return None
        return dest if dest.exists() else None

    def audio(self, workdir):
        """The whole video's audio. Reused if already downloaded. (Always the full audio:
        YouTube throttles partial downloads, so a slice is slower than the whole thing.)"""
        done = [p for p in workdir.glob("audio.*") if not p.name.endswith((".part", ".ytdl"))]
        timer().cache("audio", bool(done))
        if done:
            log(f"reusing downloaded audio ({done[0].stat().st_size / 1e6:.0f} MB)", "ok")
            return done[0]
        log("downloading the audio (video comes later, only for the clips)...", "step")
        run(["yt-dlp", "--no-playlist", "-f", "ba/wa/w", "-o", str(workdir / "audio.%(ext)s"),
             "--newline", "-N", "4", *self.args, self.url], timeout=7200)
        done = [p for p in workdir.glob("audio.*") if not p.name.endswith((".part", ".ytdl"))]
        if not done:
            raise RuntimeError("yt-dlp finished but no audio file appeared")
        log(f"audio: {done[0].stat().st_size / 1e6:.0f} MB", "ok")
        return done[0]

    def section(self, start, end, dest, cfg):
        """Video for one clip only: [start, end] seconds."""
        have = dest.exists() and dest.stat().st_size > 10000
        timer().cache(f"video section {dest.stem[-2:]}", have)
        if have:
            return dest
        h = cfg["max_height"]
        for leftover in glob.glob(str(dest) + "*"):
            Path(leftover).unlink(missing_ok=True)
        fmt = (f"bv*[height<={h}][vcodec^=avc1]+ba/bv*[height<={h}][vcodec!^=av01]+ba/"
               f"b[height<={h}]/bv*+ba/b")
        # Stream URLs from the metadata call last about 6 hours. Reusing them saves yt-dlp's
        # start-up and YouTube's challenge solving (7-40 s) on every clip. If they have
        # expired, or the file is missing, fall back to the normal call.
        if self._full and self._full.exists() and time.time() - self._full.stat().st_mtime < 5 * 3600:
            try:
                run(["yt-dlp", "--no-playlist", "--load-info-json", str(self._full), "-f", fmt,
                     "--merge-output-format", "mp4",
                     "--download-sections", f"*{max(0.0, start):.2f}-{end:.2f}",
                     "-o", str(dest)], timeout=1800)
                if dest.exists() and dest.stat().st_size > 10000:
                    return dest
            except Exception as exc:  # noqa: BLE001 - expired URLs: use the normal path
                log(f"cached stream links failed ({str(exc).splitlines()[0][:60]}), retrying", "warn")
            for leftover in glob.glob(str(dest) + "*"):
                Path(leftover).unlink(missing_ok=True)
        run(["yt-dlp", "--no-playlist",
             # H.264 first: it decodes several times faster than AV1/VP9 on a laptop CPU
             "-f", (f"bv*[height<={h}][vcodec^=avc1]+ba/bv*[height<={h}][vcodec!^=av01]+ba/"
                    f"b[height<={h}]/bv*+ba/b"),
             "--merge-output-format", "mp4",
             "--download-sections", f"*{max(0.0, start):.2f}-{end:.2f}",
             "-o", str(dest), *self.args, self.url], timeout=1800)
        if not dest.exists():
            raise RuntimeError(f"no video came back for {start:.0f}-{end:.0f}s")
        return dest

    def chat_file(self, workdir, cfg, info):
        """YouTube live-chat replay (JSON lines), or None. Best effort, time-boxed."""
        if not cfg.get("chat") or "youtube" not in (info.get("extractor_key") or "").lower():
            return None
        if info.get("live_status") not in ("was_live", "post_live"):
            return None
        keep = workdir / "chat.live_chat.json"
        if keep.exists():
            return keep
        log("reading the chat replay (how hard chat went off, minute by minute)...", "step")
        try:
            run(["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                 "-o", str(workdir / "chat"), *self.args, self.url], timeout=cfg["chat_timeout"])
        except Exception as exc:  # noqa: BLE001 - chat is optional
            log(f"chat replay not used ({str(exc).splitlines()[0][:80]})", "warn")
        return keep if keep.exists() else None


def _pick_track(full):
    """The caption track in the video's spoken language: YouTube's own speech recognition
    ('<lang>-orig', word-timed) first, then a human-made track. Translations are skipped."""
    auto = full.get("automatic_captions") or {}
    subs = {k: v for k, v in (full.get("subtitles") or {}).items() if k != "live_chat"}
    spoken = (full.get("language") or "").split("-")[0]
    orig = [k for k in auto if k.endswith("-orig")]
    if orig:
        return {"lang": orig[0], "auto": True, "spoken": orig[0][:-5].split("-")[0]}
    if spoken:
        for k in subs:
            if k.split("-")[0] == spoken:
                return {"lang": k, "auto": False, "spoken": spoken}
        if spoken in auto:
            return {"lang": spoken, "auto": True, "spoken": spoken}
    return None


def open_source(arg, cookies=None):
    return LocalFile(arg) if is_file(arg) else Link(arg, cookies)
