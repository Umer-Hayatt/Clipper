"""16:9 stream -> 9:16 clip. Picks one of three looks per clip:

  stream - a small face in a corner = a facecam over gameplay. Facecam on top,
           gameplay underneath. This is how stream clips look on Shorts/TikTok.
  face   - a big face (just chatting / IRL) = follow the face with a smooth crop.
  fit    - no face = the whole frame over a blurred copy of itself.

Returns an ffmpeg filtergraph from [base] to [vout] and the name of the look."""

import re
import subprocess
from typing import NamedTuple

import numpy as np

from .config import ENV_DIR
from .footage import BASE
from .util import even, log

STEP = 0.5            # seconds between sampled frames
MAX_SAMPLES = 60
SMALL_FACE = 0.085    # face narrower than 8.5% of the frame = a facecam overlay


MODEL = ENV_DIR / "models" / "yunet.onnx"


class Face(NamedTuple):
    """One detected face: seconds into the clip, then its box in source pixels."""
    t: float
    x: float
    y: float
    w: float
    h: float


class Analysis(NamedTuple):
    """What sampling a clip's frames found: every face, the sample times, a greyscale
    copy of each sampled frame, and the shrink factor those frames were scaled by."""
    faces: list
    times: list
    grays: list
    scale: float


class Framing(NamedTuple):
    """How one clip is framed. `graph` is an ffmpeg filtergraph from [base] to [vout];
    `cam_h` the height of the top panel (stream look) or None; `cams` the facecam
    rectangles this clip found, for the caller to add to what it knows about the
    stream; `notes` the log lines to show."""
    graph: str
    look: str
    cam_h: int
    cams: list
    notes: list


def _faces(source, t0, t1, footage):
    """An Analysis: every face in every sampled frame, in source pixels.
    Uses OpenCV's YuNet model (good on small, tilted, capped faces in a facecam);
    falls back to the older Haar detector if the model file is missing."""
    try:
        import cv2
    except ImportError as exc:
        log(f"  face detection unavailable ({exc})", "warn")
        return Analysis([], [], [], 1.0)
    scale = 960.0 / footage.width if footage.width > 960 else 1.0
    size = (even(footage.width * scale), even(footage.height * scale))
    yunet = haar = None
    if MODEL.exists() and hasattr(cv2, "FaceDetectorYN"):
        yunet = cv2.FaceDetectorYN.create(str(MODEL), "", size, 0.6, 0.3, 5000)
    else:
        haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    step = max(STEP, (t1 - t0) / MAX_SAMPLES)
    # One ffmpeg pass pulls the sampled frames, already shrunk. Seeking frame by frame with
    # OpenCV re-decodes from the last keyframe every time: minutes per clip on AV1/VP9.
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}",
         "-i", str(source), "-an",
         "-vf", (footage.crop_filter + "," if footage.crop else "")
                + f"fps=1/{step:.4f},scale={size[0]}:{size[1]}",
         "-pix_fmt", "bgr24", "-f", "rawvideo", "-"], capture_output=True)
    frame_bytes = size[0] * size[1] * 3
    count = len(proc.stdout) // frame_bytes
    if proc.returncode != 0 or count == 0:
        log("  could not sample frames for face finding", "warn")
        return Analysis([], [], [], scale)
    frames = np.frombuffer(proc.stdout[: count * frame_bytes], np.uint8).reshape(
        count, size[1], size[0], 3)
    found, grays, times = [], [], []
    for i, small in enumerate(frames):
        t = round(i * step, 3)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        grays.append(gray)
        times.append(t)
        if yunet is not None:
            _, faces = yunet.detect(np.ascontiguousarray(small))
            faces = [] if faces is None else [f[:4] for f in faces]
        else:
            faces = haar.detectMultiScale(cv2.equalizeHist(gray), 1.1, 5, minSize=(20, 20))
        for x, y, w, h in faces:
            found.append(Face(t, x / scale, y / scale, w / scale, h / scale))
    return Analysis(found, times, grays, scale)


def _cam_rect(grays, scale, box, footage):
    """The facecam's own rectangle, found two ways:
    1. its border: straight lines that stay put while everything around them changes;
    2. its background: the still area around the face (a wall, a chair) that stops
       where the game or trailer starts changing.
    Falls back to a safe box ~5.5 face-widths wide, centred on the face."""
    fx, fy, fw, fh = [v * scale for v in box]
    sw, sh = footage.width * scale, footage.height * scale
    gw = 5.5 * fw
    guess = (fx + fw / 2 - gw / 2, fy + fh * 0.55 - gw * 0.35, gw, gw * 0.7)
    guess = [v / scale for v in guess]
    if len(grays) < 6:
        return guess, "guess"

    def plausible(x0, y0, x1, y1):
        w, h = x1 - x0, y1 - y0
        return (x0 <= fx and fx + fw <= x1 and y0 <= fy + fh * 0.3 and fy + fh * 0.8 <= y1
                and 2.2 * fw <= w <= 12 * fw and 1.8 * fh <= h <= 7.5 * fh
                and w <= 0.7 * sw and h <= 0.7 * sh)

    stack = np.stack([g.astype(np.int16) for g in grays])
    H, W = stack.shape[1:]

    # 1. border lines
    eh = (np.abs(np.diff(stack, axis=1)) > 10).mean(axis=0)
    ev = (np.abs(np.diff(stack, axis=2)) > 10).mean(axis=0)
    xs = slice(int(max(0, fx - 1.5 * fw)), int(min(W, fx + 2.5 * fw)))
    ys = slice(int(max(0, fy - 0.5 * fh)), int(min(H, fy + 1.5 * fh)))
    row = (eh[:, xs] > 0.5).mean(axis=1)
    col = (ev[ys, :] > 0.5).mean(axis=0)

    def border(scores, lo, hi, outward):
        lo, hi = int(max(0, lo)), int(min(len(scores) - 1, hi))
        idx = [i for i in range(lo, hi + 1) if scores[i] >= 0.55]
        if not idx:
            return None
        if outward == "low":
            outer = min(idx)
            return max(i for i in idx if i <= outer + 5) + 2
        outer = max(idx)
        return min(i for i in idx if i >= outer - 5) - 1

    y0 = border(row, fy - 3.5 * fh, fy - 0.2 * fh, "low")
    y1 = border(row, fy + 1.2 * fh, fy + 4.5 * fh, "high")
    x0 = border(col, fx - 7 * fw, fx - 0.2 * fw, "low")
    x1 = border(col, fx + 1.2 * fw, fx + 8 * fw, "high")
    y0 = y0 if y0 is not None else (0 if fy - 3.5 * fh <= 0 else None)
    y1 = y1 if y1 is not None else (sh if fy + 4.5 * fh >= sh else None)
    x0 = x0 if x0 is not None else (0 if fx - 7 * fw <= 0 else None)
    x1 = x1 if x1 is not None else (sw if fx + 8 * fw >= sw else None)
    if None not in (x0, x1, y0, y1) and plausible(x0, y0, x1, y1):
        return [x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale], "border"

    # 2. the still background around the face
    try:
        import cv2
        still = (stack.std(axis=0) < 9).astype(np.uint8)
        still = cv2.morphologyEx(still, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(still, connectivity=4)
        ring = labels[int(max(0, fy - 0.6 * fh)):int(min(H, fy + fh * 1.6)),
                      int(max(0, fx - 1.2 * fw)):int(min(W, fx + fw * 2.2))]
        ids, counts = np.unique(ring[ring > 0], return_counts=True)
        if len(ids):
            k = int(ids[np.argmax(counts)])
            x0, y0, w, h = stats[k][:4]
            if plausible(x0, y0, x0 + w, y0 + h):
                return [x0 / scale, y0 / scale, w / scale, h / scale], "still area"
    except Exception:
        pass
    return guess, "guess"


def fit(cfg, footage):
    W, H = cfg["output_width"], cfg["output_height"]
    pre = ""
    target = cfg.get("fit_aspect") or 0
    if target and footage.width / footage.height > target:
        cw = even(footage.height * target)
        pre = f"crop={cw}:{even(footage.height)}:{even((footage.width - cw) / 2)}:0,"
    # The background is blurred small and scaled back up: the same soft look as
    # blurring the full 1080x1920 frame, for a fraction of the work per frame.
    bw, bh = even(W / 6), even(H / 6)
    dim = float(cfg.get("fit_dim", 0.16))
    return (f"{BASE}split=2[bg][fg];"
            f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
            f"crop={bw}:{bh},gblur=sigma=5,eq=brightness=-{dim:.2f},"
            f"scale={W}:{H}:flags=bilinear[bgb];"
            f"[fg]{pre}scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]")


def _stream(cfg, footage, cam, face=None):
    """Facecam panel on top, gameplay panel below. `cam` = the facecam rectangle,
    `face` = the streamer's face box (x, y, w, h) when known. The cam rarely has the
    panel's shape, so part of it is trimmed: with a face, the trim keeps the whole head
    with some room above it instead of cutting the same amount off both ends."""
    W, H = cfg["output_width"], cfg["output_height"]
    top = even(cfg["cam_height"])
    bot = H - top
    sw, sh = footage.width, footage.height
    x, y, w, h = cam
    want = W / top                      # aspect of the top panel
    if w / h > want:                    # cam wider than the panel: trim its sides
        cw, ch = h * want, h
    else:                               # taller: trim top and bottom
        cw, ch = w, w / want
    cx, cy = x + (w - cw) / 2, y + (h - ch) / 2
    if face is not None:
        fx, fy, fw, fh = face
        if fx >= x - fw and fx + fw <= x + w + fw:          # the face is in this cam
            room = float(cfg.get("cam_headroom", 0.45)) * fh
            if ch < h:                  # trimming top/bottom: head near the top of the panel
                cy = fy - max(room, 0.16 * ch)
                cy = min(max(cy, y - 0.25 * fh), y + h - ch)
            elif fy - room < cy:        # the cam rect itself starts too low: reach up a bit
                cy = max(fy - room, y - 0.5 * fh)
            if cw < w:                  # trimming the sides: keep the face in the middle
                cx = min(max(fx + fw / 2 - cw / 2, x), x + w - cw)
    cx = min(max(cx, 0), sw - cw)
    cy = min(max(cy, 0), sh - ch)
    gh = sh
    gw = min(sw, gh * W / bot)
    gx = (sw - gw) / 2
    if x > sw / 2:                      # cam on the right: keep the gameplay crop left of it
        gx = max(0, min(gx, x - gw))
    else:                               # cam on the left: keep it right of the cam
        gx = min(sw - gw, max(gx, x + w))
    return (f"{BASE}split=2[a][b];"
            f"[a]crop={even(cw)}:{even(ch)}:{even(cx)}:{even(cy)},"
            f"scale={W}:{top}:flags=lanczos,setsar=1[cam];"
            f"[b]crop={even(gw)}:{even(gh)}:{even(gx)}:0,scale={W}:{bot}:flags=lanczos,setsar=1[game];"
            f"[cam][game]vstack=inputs=2,format=yuv420p[vout]")


def _black_rows(grays, scale, footage, faces, cw):
    """(top, height) of the picture without black bars above or below it, measured on the
    columns the face crop covers. Some sources (compilations) letterbox a full-screen cam.
    Only bars of 3-15 % of the height are removed; (0, full height) otherwise."""
    sh = footage.height
    if len(grays) < 3 or not faces:
        return 0, sh
    cx = float(np.median([f.x + f.w / 2 for f in faces]))
    gh, gw = grays[0].shape
    x0 = int(max(0, cx - cw / 2) * scale)
    x1 = max(x0 + 1, min(gw, int((cx + cw / 2) * scale)))
    rows = np.median([g[:, x0:x1].mean(axis=1) for g in grays], axis=0)
    lit = np.where(rows > 14)[0]
    if len(lit) == 0:
        return 0, sh
    top, bottom = int(lit[0]) / gh, (int(lit[-1]) + 1) / gh
    if (top < 0.03 and bottom > 0.97) or top + (1 - bottom) > 0.15:
        return 0, sh
    return even(top * sh), even((bottom - top) * sh)


def _follow(cfg, footage, faces, grays=(), scale=1.0, notes=None):
    """Big face: a 9:16 crop that follows it smoothly. Log lines go to `notes` (a list)."""
    sw, sh = footage.width, footage.height
    cw = even(min(sw, sh * cfg["output_width"] / cfg["output_height"]))
    top, sh = _black_rows(grays, scale, footage, faces, cw)
    if sh != footage.height:
        cw = even(min(sw, sh * cfg["output_width"] / cfg["output_height"]))
        if notes is not None:
            notes.append(f"  black bars in the picture: cropping to {sh} px of {footage.height}")
    max_x = sw - cw
    xs = [f.x + f.w / 2 - cw / 2 for f in faces]
    k = max(3, cfg["track_smoothing"] | 1)
    xs = np.convolve(np.pad(xs, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
    pts = [(faces[0].t, xs[0])]
    for f, x in zip(faces[1:], xs[1:]):
        if abs(x - pts[-1][1]) >= 12 and f.t - pts[-1][0] >= 0.75:
            pts.append((f.t, x))
    expr = f"{min(max(pts[-1][1], 0), max_x):.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(pts, pts[1:]))):
        a, b = min(max(x0, 0), max_x), min(max(x1, 0), max_x)
        expr = f"if(lt(t,{t1:.2f}),({a:.1f}+({b:.1f}-{a:.1f})*(t-{t0:.2f})/{max(t1 - t0, 0.01):.2f}),{expr})"
    return (f"{BASE}crop={cw}:{even(sh)}:x='{expr}':y={top},"
            f"scale={cfg['output_width']}:{cfg['output_height']}:flags=lanczos,setsar=1,format=yuv420p[vout]")


def _facecam(faces, sampled, footage, grays=(), scale=1.0):
    """The facecam face: shows up in the same spot in most frames, is ALIVE (a face on
    a poster or a thumbnail never moves) and usually sits toward a side or corner."""
    cells = {}
    for f in faces:
        key = (int((f.x + f.w / 2) / footage.width * 12), int((f.y + f.h / 2) / footage.height * 8))
        cells.setdefault(key, []).append(f)
    stack = np.stack([g.astype(np.float32) for g in grays]) if len(grays) >= 4 else None
    best, best_score, best_frames = None, 0.0, 0
    seen, strong = set(), 0
    for key in sorted(cells, key=lambda k: -len(cells[k])):
        group = [f for k, v in cells.items() if abs(k[0] - key[0]) <= 1 and abs(k[1] - key[1]) <= 1 for f in v]
        sig = frozenset(id(f) for f in group)
        if sig in seen:
            continue
        seen.add(sig)
        frames = len({round(f.t, 2) for f in group})
        if frames >= 0.6 * max(1, sampled):
            strong += 1
        box = np.median(np.array([(f.x, f.y, f.w, f.h) for f in group]), axis=0)
        score = float(frames)
        if stack is not None:
            x, y, w, h = [int(v * scale) for v in box]
            patch = stack[:, max(0, y):y + max(1, h), max(0, x):x + max(1, w)]
            if patch.size and float(patch.std(axis=0).mean()) < 3.0:
                score *= 0.15                       # frozen face: a poster, not a person
        cx = (box[0] + box[2] / 2) / footage.width
        if cx < 0.3 or cx > 0.7:
            score *= 1.25                           # cams sit toward the sides
        if score > best_score:
            best, best_score, best_frames = tuple(box), score, frames
    if best is None:
        return None, 0.0, strong
    return best, best_frames / max(1, sampled), strong


def _face_in(faces, cam):
    """The usual face box (x, y, w, h) inside a cam rectangle, or None."""
    x, y, w, h = cam
    inside = [(f.x, f.y, f.w, f.h) for f in faces
              if x <= f.x + f.w / 2 <= x + w and y <= f.y + f.h / 2 <= y + h]
    return tuple(np.median(np.array(inside), axis=0)) if inside else None


def _in_known(faces, times, known):
    """A cam rectangle learned from this stream's other clips that has a face in it
    for most of this stretch, or None. The newest one is tried first."""
    for cam in reversed(known):
        x, y, w, h = cam
        hit = {f.t for f in faces if x <= f.x + f.w / 2 <= x + w and y <= f.y + f.h / 2 <= y + h}
        if len(hit) >= 0.4 * max(1, len(times)):
            return cam
    return None


def _largest_per_frame(faces):
    """The biggest face of each sampled frame, in time order."""
    per = {}
    for f in faces:
        if f.t not in per or f.w * f.h > per[f.t].w * per[f.t].h:
            per[f.t] = f
    return [per[t] for t in sorted(per)]


def _decide(faces, times, grays, scale, footage, cfg, want, known, notes):
    """The look for one stretch of a clip. Returns (filtergraph [base]->[vout], look, cam),
    `cam` being a facecam rectangle newly found here (else None). Log lines are added to
    `notes`; `known` is only read."""
    if want == "fit":
        return fit(cfg, footage), "fit", None
    big = {f.t for f in faces if f.w / footage.width >= SMALL_FACE}
    if want in ("auto", "face") and len(big) >= 0.5 * max(1, len(times)):
        return _follow(cfg, footage, _largest_per_frame(faces), grays, scale, notes), "face", None
    box, share, strong = _facecam(faces, len(times), footage, grays, scale)
    learned = _in_known(faces, times, known)
    if box is None or share < 0.3 or (strong > 2 and want == "auto"):
        if learned:
            notes.append("  unclear scene, but the streamer's cam is where it was in the other "
                         "clips -> using it")
            return _stream(cfg, footage, learned, _face_in(faces, learned)), "stream", None
        if box is not None and share >= 0.3:
            notes.append(f"  {strong} faces on screen (thumbnails?) -> whole frame, to be safe")
        return fit(cfg, footage), "fit", None
    small = box[2] / footage.width < SMALL_FACE
    if want == "stream" or (want == "auto" and small):
        cam, how = _cam_rect(grays, scale, box, footage)
        found = None
        if how == "guess" and learned:
            cam, how = learned, "the stream's other clips"
        elif how != "guess":
            found = cam
        notes.append(f"  facecam {int(cam[2])}x{int(cam[3])} at {int(cam[0])},{int(cam[1])} "
                     f"(found by {how})")
        return _stream(cfg, footage, cam, box), "stream", found
    return _follow(cfg, footage, _largest_per_frame(faces), grays, scale, notes), "face", None


def _segments(faces, times, footage, t_len, min_len=2.5):
    """Split a clip where the stream changes scene: small facecam over a game, a big
    full-screen cam, or no face at all. Returns [(start, end)] in clip time."""
    if len(times) < 4:
        return [(0.0, t_len)]
    W = footage.width
    by_t = {}
    for f in faces:
        by_t.setdefault(f.t, []).append(f)
    states = []
    for t in times:
        fs = by_t.get(t, [])
        states.append("face" if any(f.w / W >= SMALL_FACE for f in fs) else "cam" if fs else "none")
    # smooth: each sample takes the majority of its 5 neighbours
    smooth = []
    for i in range(len(states)):
        win = states[max(0, i - 2):i + 3]
        smooth.append(max(set(win), key=win.count))
    runs = []
    for i, st in enumerate(smooth):
        if runs and runs[-1][0] == st:
            runs[-1][2] = i
        else:
            runs.append([st, i, i])
    bounds = [0.0] + [(times[r[1]] + times[r[1] - 1]) / 2 for r in runs[1:]] + [t_len]
    segs = [[runs[i][0], bounds[i], bounds[i + 1]] for i in range(len(runs))]
    # fold stretches shorter than min_len into a neighbour
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, (st, a, b) in enumerate(segs):
            if b - a < min_len:
                j = i - 1 if i > 0 else i + 1
                segs[j][1], segs[j][2] = min(segs[j][1], a), max(segs[j][2], b)
                segs.pop(i)
                changed = True
                break
    merged = []
    for st, a, b in segs:
        if merged and merged[-1][0] == st:
            merged[-1][2] = b
        else:
            merged.append([st, a, b])
    return [(a, b) for _, a, b in merged]


def _relabel(chain, i):
    chain = chain.replace(BASE, f"[in_{i}]")      # before the rename below, which would match it
    return re.sub(r"\[([A-Za-z]+)\]", lambda m: f"[{m.group(1)}_{i}]", chain)


def analyze(source, t0, t1, footage):
    """Sample the clip's frames once (faces + greyscale frames); reused by decide()."""
    return _faces(source, t0, t1, footage)


def decide(analysis, duration, footage, cfg, known=()):
    """How to frame one clip of `duration` seconds: a Framing.
    A clip whose stream changes scene part-way is cut into parts, each framed its own
    way, then joined. `known` is the list of facecam spots learned from the stream's other
    clips, so an unclear clip can still put the streamer on top. It is only read: add
    `framing.cams` to it, and show `framing.notes`, in the caller."""
    want = cfg["layout"]
    if want == "fit":
        return Framing(fit(cfg, footage), "fit", None, [], [])
    faces, times, grays, scale = analysis
    segs = _segments(faces, times, footage, duration) if want == "auto" else [(0.0, duration)]
    seen, cams, notes, parts = list(known), [], [], []
    for a, b in segs:
        idx = [i for i, t in enumerate(times) if a <= t < b]
        sub_f = [f._replace(t=round(f.t - a, 3)) for f in faces if a <= f.t < b]
        sub_t = [round(times[i] - a, 3) for i in idx]
        sub_g = [grays[i] for i in idx]
        chain, look, cam = _decide(sub_f, sub_t, sub_g, scale, footage, cfg, want, seen, notes)
        if cam:                      # later parts of this clip may use it
            seen.append(cam)
            cams.append(cam)
        parts.append((a, b, chain, look))
    stream_time = sum(b - a for a, b, _, look in parts if look == "stream")
    cam_h = even(cfg["cam_height"]) if stream_time >= 0.5 * duration else None
    looks = "+".join(dict.fromkeys(p[3] for p in parts))
    if len(parts) == 1:
        notes.append(f"  look: {looks}")
        return Framing(parts[0][2], looks, cam_h, cams, notes)
    notes.append(f"  scene changes inside the clip -> {len(parts)} parts: "
                 + ", ".join(f"{p[3]} {p[0]:.0f}-{p[1]:.0f}s" for p in parts))
    split = f"{BASE}split={len(parts)}" + "".join(f"[s_{i}]" for i in range(len(parts)))
    trims = ";".join(f"[s_{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[in_{i}]"
                     for i, (a, b, _, _) in enumerate(parts))
    bodies = ";".join(_relabel(chain, i) for i, (_, _, chain, _) in enumerate(parts))
    fix = ";".join(f"[vout_{i}]setsar=1,format=yuv420p[vs_{i}]" for i in range(len(parts)))
    join = (fix + ";" + "".join(f"[vs_{i}]" for i in range(len(parts)))
            + f"concat=n={len(parts)}:v=1:a=0[vout]")
    return Framing(f"{split};{trims};{bodies};{join}", looks, cam_h, cams, notes)
