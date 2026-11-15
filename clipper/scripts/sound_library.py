#!/usr/bin/env python3
"""sound_library: keep the sound effects and music the clipper may use.

Sounds are cut from videos you give it (for example your reference clips) and kept in
~/.clipper/sounds (outside the repo: they are other people's audio):

  sfx/<name>.wav      short stingers (camera, air horn, slam, ...)
  music/<name>.wav    background music beds
  manifest.json       one entry per sound: kind, tags, source, time, level, confirmed

  python sound_library.py add VIDEO 12.4 --kind sfx --name camera --tags stinger,funny
  python sound_library.py add VIDEO 68.6 --kind sfx --name airhorn --tags stinger,rage \\
         --label "Air horn, truck horn"          find the exact spot with the sound model
  python sound_library.py add VIDEO 12.48 13.5 --kind sfx --name x --exact   exact cut
  python sound_library.py add VIDEO 58.1 63.8 --kind music --name tense-bed --tags tense
  python sound_library.py confirm camera          after you have listened to it
  python sound_library.py remove camera
  python sound_library.py list

Tags: "stinger" marks an effect for the payoff; the moment kinds (funny, rage, reaction,
story, ...) say which clips it fits. For an effect, give one time (near its start); the
start and end are found from the sound itself. For music, give a start and an end. New
sounds are "unconfirmed" until you confirm them: the tool cannot tell an added effect
from game audio, so a person must listen."""

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper.sfx import LIBRARY, manifest  # noqa: E402

SR = 48000


def save_manifest(data, lib=LIBRARY):
    Path(lib).mkdir(parents=True, exist_ok=True)
    (Path(lib) / "manifest.json").write_text(json.dumps(data, indent=1, ensure_ascii=False),
                                             encoding="utf-8")


def _decode(video, start, dur):
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, start):.3f}", "-i", str(video),
           "-t", f"{dur:.3f}", "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        sys.exit(f"ffmpeg could not read {video}: {p.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, 2)


def _envelope(x, frame=0.02):
    step = int(SR * frame)
    n = len(x) // step
    m = x[:n * step].mean(axis=1).reshape(n, step)
    return 10 * np.log10(np.mean(m.astype(np.float64) ** 2, axis=1) + 1e-10)


def trim_hit(x, look_before=0.3, max_len=2.0, rise=8.0, tail=-30.0):
    """Cut a stinger out of `x` (which starts `look_before` s before the rough time):
    start at the first sharp rise in level, end when it has faded `tail` dB below its peak."""
    db = _envelope(x)
    base = np.median(db[:max(1, int(look_before / 0.02))])
    on = 0
    for i in range(1, len(db)):
        if db[i] - max(base, db[max(0, i - 5):i].min()) >= rise:
            on = max(0, i - 1)
            break
    peak_i = on + int(np.argmax(db[on:on + int(0.5 / 0.02)]))
    end = min(len(db), on + int(max_len / 0.02))
    for i in range(peak_i, end):
        if db[i] < db[peak_i] + tail:
            end = i + 1
            break
    a, b = on * int(SR * 0.02), end * int(SR * 0.02)
    return x[a:b], a / SR


def finish(x, kind):
    """Fades and level: effects peak at -1 dBFS; music beds sit at -20 dBFS RMS.
    Music beds get only 20 ms edges (no clicks): they are looped, so longer fades would
    dip every loop; the mix fades the whole bed in and out instead."""
    x = x.copy()
    fin, fout = (0.005, 0.03) if kind == "sfx" else (0.02, 0.02)
    n_in, n_out = min(len(x) // 2, int(SR * fin)), min(len(x) // 2, int(SR * fout))
    if n_in:
        x[:n_in] *= np.linspace(0, 1, n_in)[:, None]
    if n_out:
        x[-n_out:] *= np.linspace(1, 0, n_out)[:, None]
    if kind == "sfx":
        x *= 0.891 / (np.abs(x).max() + 1e-9)
    else:
        x *= 0.1 / (np.sqrt(np.mean(x ** 2)) + 1e-9)
        x = np.clip(x, -0.98, 0.98)
    return x


def write_wav(path, x):
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def locate(video, near, label, span=1.5, hop=0.05):
    """Start of the 0.96 s window around `near` where the sound model scores `label` highest.
    Returns (start seconds, score), or None when the model or the label is missing."""
    try:
        import sound_events as se
        sess, labels = se.load_model()
    except SystemExit:
        return None
    if label not in labels:
        sys.exit(f"unknown label {label!r}; see ~/.clipper/models/yamnet/labels.txt")
    from clipper.util import pcm
    a = max(0.0, near - span)
    wave = pcm(video, se.RATE, a, 2 * span + 0.96)
    k, name = labels.index(label), sess.get_inputs()[0].name
    win = int(0.96 * se.RATE)
    best = (near, -99.0)
    for i in range(0, max(1, len(wave) - win), int(hop * se.RATE)):
        x = se.patches(se.log_mel(wave[i:i + win]))[0]
        s = float(sess.run(None, {name: x[None, None]})[0][0][k])
        if s > best[1]:
            best = (a + i / se.RATE, s)
    return round(best[0], 3), round(float(1 / (1 + np.exp(-best[1]))), 2)


def add(video, start, end, kind, name, tags, note="", lib=LIBRARY, label=None, exact=False):
    if exact:
        if not end or end <= start:
            sys.exit("--exact needs a start and an end")
        kind_x = _decode(video, start, end - start)
        return _store(kind_x, start, video, kind, name, tags, note, lib)
    if kind == "sfx" and label:
        found = locate(video, start, label)
        if found:
            start, score = found
            end = start + 0.96
            print(f"  '{label}' peaks in the window at {start:.2f} s (score {score})")
    if kind == "sfx":
        before = 0.3 if not label else 0.1
        raw = _decode(video, start - before, (end - start if end else 1.5) + before)
        x, off = trim_hit(raw, look_before=min(before, start))
        at = round(max(0.0, start - before) + off, 3)
    else:
        if not end or end <= start:
            sys.exit("music needs a start and an end")
        x, at = _decode(video, start, end - start), start
    return _store(x, at, video, kind, name, tags, note, lib)


def _store(x, at, video, kind, name, tags, note, lib):
    if len(x) < SR * 0.05:
        sys.exit("nothing usable there (too short after trimming)")
    x = finish(x, kind)
    write_wav(Path(lib) / kind / f"{name}.wav", x)
    data = manifest(lib)
    data["sounds"][name] = {"kind": kind, "tags": tags, "source": Path(video).name,
                            "at": at, "length": round(len(x) / SR, 2), "note": note,
                            "confirmed": False}
    save_manifest(data, lib)
    return data["sounds"][name]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("video")
    a.add_argument("start", type=float)
    a.add_argument("end", type=float, nargs="?")
    a.add_argument("--kind", choices=["sfx", "music"], required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--tags", default="")
    a.add_argument("--note", default="")
    a.add_argument("--label", help="effects: a sound_events label (e.g. 'Air horn, truck horn'); "
                                   "the cut is moved to where that sound is strongest")
    a.add_argument("--exact", action="store_true", help="cut exactly START to END, no trimming")
    for c in ("confirm", "remove"):
        sub.add_parser(c).add_argument("name")
    sub.add_parser("list")
    o = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if o.cmd == "add":
        tags = [t.strip() for t in o.tags.split(",") if t.strip()]
        e = add(o.video, o.start, o.end, o.kind, o.name, tags, o.note, label=o.label,
                exact=o.exact)
        print(f"added {o.kind}/{o.name}.wav  {e['length']} s from {e['source']} at {e['at']} s"
              " (unconfirmed: listen, then run 'confirm')")
    elif o.cmd in ("confirm", "remove"):
        data = manifest()
        e = data["sounds"].get(o.name) or sys.exit(f"no sound called {o.name}")
        if o.cmd == "confirm":
            e["confirmed"] = True
        else:
            (LIBRARY / e["kind"] / f"{o.name}.wav").unlink(missing_ok=True)
            del data["sounds"][o.name]
        save_manifest(data)
        print(f"{'confirmed' if o.cmd == 'confirm' else 'removed'} {o.name}")
    else:
        for name, e in sorted(manifest()["sounds"].items()):
            flag = "ok " if e.get("confirmed") else "?  "
            print(f"{flag}{e['kind']:5s} {name:18s} {e['length']:5.2f}s  [{','.join(e['tags'])}]  "
                  f"{e['source'][:40]} @ {e['at']}s  {e.get('note', '')}")


if __name__ == "__main__":
    main()
