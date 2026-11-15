#!/usr/bin/env python3
"""sound_events: list the sounds in a video or audio file, and when each one plays.

Uses YAMNet (Google's AudioSet sound tagger, 521 sound names such as "Explosion",
"Whoosh, swoosh, swish", "Boing", "Laughter"), run locally on onnxruntime. This gives
a way to "hear" reference videos: which sound effects they use, and where.

  python sound_events.py VIDEO                      events, speech/music hidden
  python sound_events.py VIDEO --all                every label, nothing hidden
  python sound_events.py VIDEO --min 0.3 --json out.json
  python sound_events.py VIDEO --start 60 --duration 30

The model is not part of the repo. It lives in ~/.clipper/models/yamnet
(yamnet.onnx, yamnet.data, labels.txt): Qualcomm's MIT-licensed export from
huggingface.co/qualcomm/YamNet (release v0.66.0, yamnet-onnx-float.zip)."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper.util import pcm  # noqa: E402

MODEL_DIR = Path.home() / ".clipper" / "models" / "yamnet"

# YAMNet's feature settings (tensorflow/models research/audioset/yamnet/params.py)
RATE = 16000
WIN = 400            # 25 ms
HOP = 160            # 10 ms
NFFT = 512
MELS = 64
FMIN, FMAX = 125.0, 7500.0
LOG_OFFSET = 0.001
PATCH = 96           # frames per patch = 0.96 s
PATCH_HOP = 48       # 0.48 s between patches
STEP = PATCH_HOP * HOP / RATE

# Hidden unless --all: the ever-present background of a stream, not added effects.
BACKGROUND = {
    "Speech", "Conversation", "Narration, monologue", "Male speech, man speaking",
    "Female speech, woman speaking", "Child speech, kid speaking", "Silence",
    "Inside, small room", "Inside, large room or hall", "Music", "Speech synthesizer",
    "Babbling", "Whispering", "Shout", "Yell",
}


def _hz_to_mel(f):
    return 1127.0 * np.log1p(np.asarray(f, dtype=np.float64) / 700.0)


def mel_matrix():
    """Same as tf.signal.linear_to_mel_weight_matrix (HTK mel, DC bin zeroed)."""
    bins = NFFT // 2 + 1
    freqs = np.linspace(0.0, RATE / 2.0, bins)[1:]
    spec_mel = _hz_to_mel(freqs)[:, None]
    edges = np.linspace(_hz_to_mel(FMIN), _hz_to_mel(FMAX), MELS + 2)
    lo, mid, hi = edges[:-2], edges[1:-1], edges[2:]
    up = (spec_mel - lo) / (mid - lo)
    down = (hi - spec_mel) / (hi - mid)
    w = np.maximum(0.0, np.minimum(up, down))
    return np.vstack([np.zeros((1, MELS)), w]).astype(np.float32)


def log_mel(wave):
    """Waveform (16 kHz mono float) -> [frames, 64] log-mel, like YAMNet's frontend."""
    if len(wave) < WIN:
        wave = np.pad(wave, (0, WIN - len(wave)))
    n = 1 + (len(wave) - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(WIN) / WIN)   # periodic Hann
    mag = np.abs(np.fft.rfft(wave[idx] * window, n=NFFT))
    return np.log(mag @ mel_matrix() + LOG_OFFSET).astype(np.float32)


def patches(mel):
    if len(mel) < PATCH:
        mel = np.pad(mel, ((0, PATCH - len(mel)), (0, 0)), constant_values=np.log(LOG_OFFSET))
    starts = range(0, len(mel) - PATCH + 1, PATCH_HOP)
    return np.stack([mel[s:s + PATCH] for s in starts])


def load_model():
    onnx = MODEL_DIR / "yamnet.onnx"
    if not onnx.exists():
        sys.exit(f"YAMNet model not found in {MODEL_DIR}. See the top of sound_events.py.")
    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
    labels = (MODEL_DIR / "labels.txt").read_text(encoding="utf-8").splitlines()
    return sess, labels


def scores(path, start=None, duration=None):
    """Return (times, [patches, 521] scores in 0..1)."""
    sess, labels = load_model()
    wave = pcm(path, RATE, start, duration)
    p = patches(log_mel(wave))
    name = sess.get_inputs()[0].name
    logits = np.concatenate([sess.run(None, {name: x[None, None]})[0] for x in p])
    out = 1.0 / (1.0 + np.exp(-np.clip(logits, -50, 50)))   # this export outputs logits
    t0 = start or 0.0
    times = t0 + np.arange(len(out)) * STEP
    return times, out, labels


def events(times, sc, labels, min_score=0.2, hide=BACKGROUND):
    """Merge per-patch hits into events: one row per label per stretch of time.
    A single missed patch between two hits does not split the event."""
    rows = []
    for k, label in enumerate(labels):
        if label in hide:
            continue
        hit = sc[:, k] >= min_score
        i = 0
        while i < len(hit):
            if not hit[i]:
                i += 1
                continue
            j = i
            while True:
                if j + 1 < len(hit) and hit[j + 1]:
                    j += 1
                elif j + 2 < len(hit) and hit[j + 2]:
                    j += 2
                else:
                    break
            seg = sc[i:j + 1, k]
            rows.append({"label": label, "start": round(float(times[i]), 2),
                         "end": round(float(times[j]) + PATCH * HOP / RATE, 2),
                         "peak": round(float(seg.max()), 3)})
            i = j + 1
    rows.sort(key=lambda r: (r["start"], -r["peak"]))
    return rows


def _stamp(s):
    m, s = divmod(s, 60)
    return f"{int(m):02d}:{s:05.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--min", type=float, default=0.2, help="score threshold 0..1 (default 0.2)")
    ap.add_argument("--all", action="store_true", help="also show speech, music, room tone")
    ap.add_argument("--start", type=float, help="seconds into the file to begin")
    ap.add_argument("--duration", type=float, help="seconds to analyse")
    ap.add_argument("--json", help="also write the events to this JSON file")
    a = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    times, sc, labels = scores(a.file, a.start, a.duration)
    rows = events(times, sc, labels, a.min, set() if a.all else BACKGROUND)
    for r in rows:
        print(f"{_stamp(r['start'])} - {_stamp(r['end'])}  {r['peak']:.2f}  {r['label']}")
    if not rows:
        print("no sounds above the threshold (try --min 0.1 or --all)")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
