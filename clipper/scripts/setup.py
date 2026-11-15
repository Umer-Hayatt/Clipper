#!/usr/bin/env python3
"""One-time setup for clipper. Safe to run again. Prints "All set" at the end.

    python scripts/setup.py

Builds a private Python env in ~/.clipper (faster-whisper, opencv, pillow, numpy,
yt-dlp), downloads the Whisper models (tiny + base, ~220 MB) and the facecam finder
(OpenCV YuNet, 0.2 MB). Uses only the system Python's standard library."""

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ENV_DIR = Path.home() / ".clipper"
VENV = ENV_DIR / "venv"
WIN = os.name == "nt"
PY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
PKGS = ["faster-whisper", "opencv-python-headless<5", "pillow", "numpy", "yt-dlp[default]"]
YUNET = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
         "face_detection_yunet_2023mar.onnx")
missing = False


def ok(msg):
    print(f"  OK  {msg}")


def bad(msg):
    global missing
    missing = True
    print(f"  !!  {msg}")


def main():
    print("clipper setup")
    hint = "winget install Gyan.FFmpeg" if WIN else "brew install ffmpeg"
    ok("ffmpeg") if shutil.which("ffmpeg") else bad(f"ffmpeg missing: {hint}")
    ok("Claude Code") if shutil.which("claude") else bad("claude missing: install Claude Code first")
    if any(shutil.which(r) for r in ("deno", "node", "bun")):
        ok("JavaScript runtime (YouTube downloads need one)")
    else:
        bad("no node/deno: winget install OpenJS.NodeJS.LTS  (YouTube downloads need it)")
    free = shutil.disk_usage(Path.home()).free / 1e9
    ok(f"disk: {free:.1f} GB free") if free >= 1.5 else bad(f"only {free:.1f} GB free; setup needs ~1 GB")
    if missing:
        print("\nFix the !! lines above, then run this again.")
        return 1

    ENV_DIR.mkdir(exist_ok=True)
    uv = shutil.which("uv")
    if not PY.exists():
        if uv:
            subprocess.run([uv, "venv", "-q", "--python", "3.12", str(VENV)], check=False)
        if not PY.exists():
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    if uv:   # --no-cache: uv's cache can fill up on Windows ("too many temporary files")
        subprocess.run([uv, "pip", "install", "-q", "--no-cache", "--python", str(PY), "-U", *PKGS],
                       check=True)
    else:
        subprocess.run([str(PY), "-m", "pip", "install", "-q", "-U", "pip"], check=True)
        subprocess.run([str(PY), "-m", "pip", "install", "-q", "-U", *PKGS], check=True)
    ok("Python packages (private env in ~/.clipper)")

    env = dict(os.environ, HF_HUB_DISABLE_SYMLINKS_WARNING="1", HF_HUB_VERBOSITY="error")
    subprocess.run([str(PY), "-c",
                    "from faster_whisper import WhisperModel\n"
                    "for n in ('tiny', 'base'): WhisperModel(n, device='cpu', compute_type='int8')"],
                   check=True, env=env)
    ok("Whisper models (tiny = listening, base = captions)")

    model = ENV_DIR / "models" / "yunet.onnx"
    model.parent.mkdir(exist_ok=True)
    if not model.exists() or model.stat().st_size == 0:
        try:
            urllib.request.urlretrieve(YUNET, model)
        except OSError as exc:
            print(f"  !   facecam model download failed ({exc}); a simpler face finder is used")
    if model.exists() and model.stat().st_size:
        ok("facecam finder (OpenCV YuNet, 0.2 MB)")

    here = Path(__file__).resolve().parents[1]
    launcher = here / ("clip.cmd" if WIN else "clip")
    print(f"\nAll set. Try:  \"{launcher}\" <link or video file>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
