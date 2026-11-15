"""Sound effects, made here from plain maths (no downloads, no licences to worry about).

Each sound is synthesised once into <workdir>/sfx/<name>.wav (48 kHz mono) and mixed
over the clip's own sound by render.finish:

  boom    a deep hit that drops in pitch, for the peak moment
  whoosh  a short rush of air, for the title sliding in
  pop     a small bright blip"""

import wave
from pathlib import Path

import numpy as np

SR = 48000
NAMES = ("boom", "whoosh", "pop")


def _boom():
    n = int(SR * 0.9)
    t = np.arange(n) / SR
    freq = 42 + 68 * np.exp(-t / 0.09)                  # 110 Hz falling to ~42 Hz
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t / 0.28)
    click = np.random.default_rng(7).standard_normal(n) * np.exp(-t / 0.006) * 0.35
    return np.tanh(1.8 * (body + click))


def _whoosh():
    n = int(SR * 0.45)
    t = np.arange(n) / SR
    noise = np.random.default_rng(11).standard_normal(n)
    out = np.empty(n)
    low = band = 0.0
    for i in range(n):                                  # state-variable filter, cutoff sweeps up
        f = 2 * np.sin(np.pi * (300 + 3200 * (t[i] / 0.45) ** 1.5) / SR)
        high = noise[i] - low - 0.9 * band
        band += f * high
        low += f * band
        out[i] = band
    env = np.sin(np.pi * np.clip(t / 0.45, 0, 1)) ** 2
    return out * env


def _pop():
    n = int(SR * 0.09)
    t = np.arange(n) / SR
    freq = 520 + 900 * np.exp(-t / 0.015)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t / 0.025)


_MAKERS = {"boom": _boom, "whoosh": _whoosh, "pop": _pop}


def path(name, workdir):
    """The WAV for `name`, written on first use. None for an unknown name."""
    if name not in _MAKERS:
        return None
    dest = Path(workdir) / "sfx" / f"{name}.wav"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        x = _MAKERS[name]()
        x = (x / (np.abs(x).max() + 1e-9) * 0.9 * 32767).astype("<i2")
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(x.tobytes())
    return dest


def mix(events, first_input, workdir, volume, audio_label="a0", out_label="a"):
    """ffmpeg inputs and filters that lay `events` [(seconds, name)] over `audio_label`.
    Returns (input args, filter string). With no usable events it only renames the label."""
    inputs, chains, labels = [], [], []
    k = first_input
    for at, name in events:
        wav = path(name, workdir)
        if wav is None or at < 0:
            continue
        ms = int(round(at * 1000))
        inputs += ["-i", str(wav)]
        chains.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                      f"adelay={ms}:all=1,volume={float(volume):.3f}[sx{k}]")
        labels.append(f"[sx{k}]")
        k += 1
    if not labels:
        return [], f"[{audio_label}]anull[{out_label}]"
    body = ";".join(chains)
    body += (f";[{audio_label}]aresample=48000,aformat=channel_layouts=stereo[sxbase];"
             f"[sxbase]{''.join(labels)}amix=inputs={len(labels) + 1}:normalize=0:"
             f"duration=first,alimiter=limit=0.84:level=disabled[{out_label}]")
    # level=disabled: the limiter only catches the peaks of a hit; it must not raise the
    # whole clip above loudnorm's -1.5 dB ceiling
    return inputs, body
