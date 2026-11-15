"""Sound effects, made here from plain maths (no downloads, no licences to worry about).

Each sound is synthesised once into <workdir>/sfx/<name>.wav (48 kHz mono) and mixed
over the clip's own sound by render.finish:

  thud    a short punchy hit, for the peak moment (the default)
  boom    a longer deep hit that drops in pitch
  whoosh  a short rush of air
  pop     a small bright blip

Measured on the 13 reference videos (scripts/sound_events.py): added effects are rare,
they land in a pause rather than over talking, and they sit about 0-10 dB above the
clip's typical level. Phone speakers play almost nothing below ~150 Hz, so every hit
carries overtones up in the 150-600 Hz range, not just sub-bass."""

import wave
from pathlib import Path

import numpy as np

SR = 48000
NAMES = ("thud", "boom", "whoosh", "pop")
VERSION = 2                     # bump when any sound changes, so cached WAVs are remade


def _sweep(t, f_end, f_start, tau):
    """Phase of a tone that falls from f_start to f_end Hz with time constant tau."""
    freq = f_end + (f_start - f_end) * np.exp(-t / tau)
    return 2 * np.pi * np.cumsum(freq) / SR


def _thud():
    n = int(SR * 0.35)
    t = np.arange(n) / SR
    ph = _sweep(t, 90, 200, 0.035)                      # 200 Hz falling to ~90 Hz
    body = (np.sin(ph) + 0.8 * np.sin(2 * ph) + 0.5 * np.sin(3 * ph)) * np.exp(-t / 0.07)
    noise = np.random.default_rng(5).standard_normal(n)
    smack = np.convolve(noise, np.ones(12) / 12, "same") * np.exp(-t / 0.012) * 0.9
    return np.tanh(2.2 * (body + smack))


def _boom():
    n = int(SR * 0.9)
    t = np.arange(n) / SR
    ph = _sweep(t, 42, 110, 0.09)                       # 110 Hz falling to ~42 Hz
    body = np.sin(ph) * np.exp(-t / 0.28)
    over = (1.2 * np.sin(2 * ph) + 0.9 * np.sin(3 * ph) + 0.5 * np.sin(5 * ph)) \
        * np.exp(-t / 0.3)                              # what a phone speaker can play
    click = np.random.default_rng(7).standard_normal(n) * np.exp(-t / 0.006) * 0.35
    return np.tanh(1.8 * (body + over + click))


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


_MAKERS = {"thud": _thud, "boom": _boom, "whoosh": _whoosh, "pop": _pop}


def in_pause(at, wave, rate, reach=1.5, drop=14.0, hold=0.12):
    """When to play a hit meant for `at`: at the start of the first pause in the clip's own
    sound within `reach` seconds after it, so it does not land on top of the talking.
    A pause is `hold` seconds at least `drop` dB below the loud part around `at`.
    The sound is used, not word timings: platform captions have no gaps between words.
    With no pause in reach (or reach 0) the time is left as is."""
    if reach <= 0 or wave is None or len(wave) == 0:
        return at
    step = int(rate * 0.02)                             # 20 ms frames
    n = len(wave) // step
    if n == 0:
        return at
    frames = wave[:n * step].reshape(n, step)
    db = 10 * np.log10(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-10)
    i0 = int(at / 0.02)
    if i0 >= n:
        return at
    around = db[max(0, i0 - 100):min(n, i0 + 100)]      # +-2 s
    quiet = db < np.percentile(around, 90) - drop
    need = max(1, int(round(hold / 0.02)))
    last = min(n - need, i0 + int(reach / 0.02))
    for i in range(i0, last + 1):
        if quiet[i:i + need].all():
            return round(i * 0.02, 2)
    return at


def path(name, workdir):
    """The WAV for `name`, written on first use. None for an unknown name."""
    if name not in _MAKERS:
        return None
    dest = Path(workdir) / "sfx" / f"{name}_v{VERSION}.wav"   # new version = fresh file
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
    for at, name, *_ in events:                         # extra fields (e.g. "beat") ignored
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
