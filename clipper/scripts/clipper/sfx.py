"""Sound effects and music for a clip: which sound, where, and when not to.

Sounds come from the sound library (~/.clipper/sounds, filled by scripts/sound_library.py
from the user's reference videos). When the library has nothing that fits, a few sounds
made here from plain maths are the fallback (so a fresh checkout and the tests still work):

  thud    a short punchy hit
  boom    a longer deep hit that drops in pitch
  whoosh  a short rush of air
  pop     a small bright blip

Placement rules, measured on the 13 references (HANDOFF.md "Sound rules"): one stinger per
clip, on the payoff (the last beat), in the first pause after it, never over unbroken
talking; music only when asked for, under the whole clip, ducked under speech, fading out
at the payoff. Phone speakers play almost nothing below ~150 Hz, so the made-up hits
carry overtones in the 150-600 Hz range."""

import hashlib
import json
import wave
from pathlib import Path

import numpy as np

SR = 48000
NAMES = ("thud", "boom", "whoosh", "pop")
VERSION = 2                     # bump when any sound changes, so cached WAVs are remade
LIBRARY = Path.home() / ".clipper" / "sounds"


# --- the sound library -------------------------------------------------------------

def manifest(lib=LIBRARY):
    p = Path(lib) / "manifest.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"sounds": {}}


def library(kind, lib=LIBRARY, confirmed_only=False):
    """[(name, path, entry)] of `kind` ("sfx" or "music") whose file exists."""
    out = []
    for name, e in sorted(manifest(lib)["sounds"].items()):
        if e.get("kind") != kind or (confirmed_only and not e.get("confirmed")):
            continue
        path = Path(lib) / kind / f"{name}.wav"
        if path.exists():
            out.append((name, path, e))
    return out


def choose(kind, mood, seed, cfg_part, role=None):
    """One library sound for a clip, or None. `cfg_part` is cfg["sfx"] or cfg["music"]:
    its "sound" may name one sound, or be "auto" to match the clip's `mood` (the moment's
    kind: funny, rage, reaction, ...) against the sounds' tags. `role` (e.g. "stinger")
    must be among the tags when given. `seed` (the clip name) spreads the choice over
    clips so a batch does not repeat one sound."""
    lib = Path(cfg_part.get("library") or LIBRARY)
    found = library(kind, lib, bool(cfg_part.get("confirmed_only")))
    want = cfg_part.get("sound", "auto")
    if want and want != "auto":
        named = [x for x in found if x[0] == want]
        return named[0][1] if named else None
    if role:
        found = [x for x in found if role in x[2].get("tags", [])]
    fits = [x for x in found if mood and mood in x[2].get("tags", [])] or found
    if not fits:
        return None
    k = int(hashlib.md5(str(seed).encode("utf-8")).hexdigest(), 16) % len(fits)
    return fits[k][1]


# --- fallback sounds made from maths -----------------------------------------------


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


def in_pause(at, wave, rate, reach=1.5, drop=14.0, hold=0.25):
    """When to play a hit meant for `at`: at the start of the first pause in the clip's own
    sound within `reach` seconds after it, so it does not land on top of the talking.
    A pause is `hold` seconds at least `drop` dB below the loud part around `at` (0.25 s:
    a breath between two words is shorter, and a hit there lands on the next word).
    The sound is used, not word timings: platform captions have no gaps between words.
    Returns None when it is talking all the way through `reach`; with reach 0 or no audio
    the time is left as is."""
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
    return None                                         # talking all the way: no place for it


def path(name, workdir):
    """The WAV for `name`: a file path is used as is (library sounds); a made-up sound's
    name is written on first use. None for anything else."""
    if str(name).lower().endswith(".wav"):
        return Path(name) if Path(name).exists() else None
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


def mix(events, first_input, workdir, volume, audio_label="a0", out_label="a", music=None):
    """ffmpeg inputs and filters that lay `events` [(seconds, name or .wav path, ...)] over
    `audio_label`, plus an optional `music` bed: {"path", "dur", "volume", "fade_out"}.
    The bed loops for the whole clip, is ducked under the clip's own sound (talking), and
    fades out at `fade_out` seconds. Returns (input args, filter string). With nothing
    usable it only renames the label."""
    inputs, chains, labels = [], [], []
    k = first_input
    for at, name, *_ in events:                         # extra fields (e.g. "beat") ignored
        wav = path(name, workdir)
        if wav is None or at is None or at < 0:
            continue
        ms = int(round(at * 1000))
        inputs += ["-i", str(wav)]
        chains.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                      f"adelay={ms}:all=1,volume={float(volume):.3f}[sx{k}]")
        labels.append(f"[sx{k}]")
        k += 1
    bed = music and music.get("path") and Path(music["path"]).exists()
    if not labels and not bed:
        return [], f"[{audio_label}]anull[{out_label}]"
    body = chains[:]
    body.append(f"[{audio_label}]aresample=48000,aformat=channel_layouts=stereo[sxbase]")
    base = "[sxbase]"
    if bed:
        dur = float(music["dur"])
        out_at = min(max(0.0, float(music.get("fade_out") or dur) - 0.8), max(0.0, dur - 0.8))
        inputs += ["-stream_loop", "-1", "-i", str(music["path"])]
        body.append(f"[sxbase]asplit=2[sxmain][sxkey]")
        body.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                    f"atrim=0:{dur:.3f},asetpts=N/SR/TB,volume={float(music['volume']):.3f},"
                    f"afade=t=in:d=0.5,afade=t=out:st={out_at:.3f}:d=0.8[mus]")
        # the clip's own sound pushes the music down while someone talks
        body.append("[mus][sxkey]sidechaincompress=threshold=0.03:ratio=8:attack=20:"
                    "release=400[musd]")
        base = "[sxmain]"
        labels.append("[musd]")
        k += 1
    body.append(f"{base}{''.join(labels)}amix=inputs={len(labels) + 1}:normalize=0:"
                f"duration=first,alimiter=limit=0.84:level=disabled[{out_label}]")
    # level=disabled: the limiter only catches the peaks of a hit; it must not raise the
    # whole clip above loudnorm's -1.5 dB ceiling
    return inputs, ";".join(body)
