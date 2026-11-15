"""Find the shared screen inside a screen recording of a video call (Meet, Zoom, Teams).

The recording has the call's own interface around the shared screen: a strip of
participant tiles, bars, names. Everything outside the shared screen is thrown away before
any later step, which also removes the participants' faces and names.

Method: about 20 frames are pulled from across the section (fast seeks, no full decode).
The shared screen is the largest area whose picture really changes between those frames
(slides change, code scrolls). The call's interface stays put and the participant tiles
change less and in small separate boxes. The detected box is cached in crop.json and
drawn on a preview image in which everything OUTSIDE the box is blurred and darkened, so
the preview itself shows no names or faces. `--crop x,y,w,h` overrides the detection."""

import subprocess

import numpy as np

from .util import even, log, read_json, write_json

SAMPLES = 20
WIDTH = 960                 # detection resolution (source is shrunk to this width)


def parse(spec):
    """'x,y,w,h' (source pixels) -> (x, y, w, h), or None for 'auto'/empty."""
    if not spec or str(spec).lower() == "auto":
        return None
    try:
        x, y, w, h = [int(float(v)) for v in str(spec).split(",")]
    except ValueError:
        raise SystemExit("--crop takes auto or x,y,w,h in pixels, for example 0,60,1500,900")
    if w < 64 or h < 64:
        raise SystemExit("--crop: width and height must be at least 64 px")
    return x, y, w, h


def _frames(source, t0, t1, info):
    """~20 colour frames spread over [t0, t1], shrunk to WIDTH. One quick seek each."""
    scale = WIDTH / info["width"] if info["width"] > WIDTH else 1.0
    size = (even(info["width"] * scale), even(info["height"] * scale))
    frames = []
    span = max(1.0, t1 - t0)
    for i in range(SAMPLES):
        t = t0 + span * (i + 0.5) / SAMPLES
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", str(source),
             "-frames:v", "1", "-an", "-vf", f"scale={size[0]}:{size[1]}", "-pix_fmt", "bgr24",
             "-f", "rawvideo", "-"], capture_output=True)
        if len(proc.stdout) == size[0] * size[1] * 3:
            frames.append(np.frombuffer(proc.stdout, np.uint8).reshape(size[1], size[0], 3))
    return frames, scale, size


def _box_from_motion(grays, level=0.4):
    """Largest rectangle of a changing picture, in the grays' own pixels, or None."""
    import cv2
    stack = np.stack([g.astype(np.float32) for g in grays])
    # Per pixel: how much it differs from the middle picture over time. The median is used
    # so one odd frame (a pop-up, a transition) does not light up the whole screen.
    change = np.abs(stack - np.median(stack, axis=0)).mean(axis=0)
    change = cv2.GaussianBlur(change, (0, 0), 1.5)
    hi = float(np.percentile(change, 90))
    if hi < 3.0:
        return None                               # nothing changes: a still image
    mask = (change > max(2.0, hi * level)).astype(np.uint8)
    k = max(3, mask.shape[1] // 160)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k * 4, k * 4), np.uint8))
    # The biggest rectangle that fits inside the changing area. A box around the whole
    # area would swallow tiles that touch the screen; a rectangle cannot.
    f = 4
    small = cv2.resize(mask, (mask.shape[1] // f, mask.shape[0] // f), interpolation=cv2.INTER_AREA) > 0.6
    box = _largest_rect(small)
    if box is None:
        return None
    x, y, w, h = box
    return x * f, y * f, w * f, h * f


def _largest_rect(mask):
    """(x, y, w, h) of the largest all-True rectangle in a boolean grid (histogram method)."""
    heights = np.zeros(mask.shape[1], np.int32)
    best, box = 0, None
    for r in range(mask.shape[0]):
        heights = np.where(mask[r], heights + 1, 0)
        stack = []                                 # column indexes with rising heights
        for c in range(len(heights) + 1):
            cur = heights[c] if c < len(heights) else 0
            start = c
            while stack and stack[-1][1] >= cur:
                start, hgt = stack.pop()
                area = hgt * (c - start)
                if area > best:
                    best, box = area, (start, r - hgt + 1, c - start, hgt)
            stack.append((start, cur))
    return box


def _grow(grays, box):
    """The largest-rectangle step stays a little inside the real screen (quiet parts of a
    slide do not change). Grow each edge outward while the strip beyond it still changes
    like the inside and is not a flat band: the call's background between the screen and
    the participant tiles is flat, so growth stops there."""
    stack = np.stack([g.astype(np.float32) for g in grays])
    med = np.median(stack, axis=0)
    change = np.abs(stack - med).mean(axis=0)
    H, W = med.shape
    x, y, w, h = box
    ref = float(np.median(change[y:y + h, x:x + w]))
    if ref < 1.0:
        return box
    step = 4

    def live(x0, y0, x1, y1):                     # is this strip part of the screen?
        if x1 <= x0 or y1 <= y0 or x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            return False
        return (float(change[y0:y1, x0:x1].mean()) >= 0.5 * ref
                and float(med[y0:y1, x0:x1].std()) > 1.5)

    x0, y0, x1, y1 = x, y, x + w, y + h
    ys, ye = y + h // 10, y + h - h // 10         # span used when growing left or right
    xs, xe = x + w // 10, x + w - w // 10
    while live(x1, ys, x1 + step, ye):
        x1 += step
    while live(x0 - step, ys, x0, ye):
        x0 -= step
    while live(xs, y1, xe, y1 + step):
        y1 += step
    while live(xs, y0 - step, xe, y0):
        y0 -= step
    return x0, y0, x1 - x0, y1 - y0


def detect(source, t0, t1, info, workdir, spec="auto"):
    """The shared screen as (x, y, w, h) in source pixels, plus how it was found.
    Cached in <workdir>/crop.json. Returns (None, why) when there is nothing to crop."""
    manual = parse(spec)
    if manual:
        return manual, "given with --crop"
    cache = workdir / "crop.json"
    key = {"source": str(source), "t0": round(t0), "t1": round(t1)}
    if cache.exists():
        saved = read_json(cache)
        if saved.get("key") == key:
            return (tuple(saved["box"]) if saved.get("box") else None), saved["how"] + " (cached)"
    try:
        import cv2
    except ImportError:
        return None, "OpenCV is not installed"
    # A short section may hold too little movement to show where the screen is, so frames
    # are taken from at least 20 minutes around it (the call's layout rarely changes that fast).
    if t1 - t0 < 1200:
        pad = (1200 - (t1 - t0)) / 2
        t0, t1 = max(0.0, t0 - pad), min(info.get("duration") or t1 + pad, t1 + pad)
    frames, scale, size = _frames(source, t0, t1, info)
    if len(frames) < 6:
        return None, "could not sample frames"
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    box = _box_from_motion(grays)
    if box is None or box[2] * box[3] < 0.15 * grays[0].shape[0] * grays[0].shape[1]:
        box = _box_from_motion(grays, 0.25) or box      # quiet screen: be less strict
    if box is None:
        result, how = None, "nothing changes on screen, so no shared screen found"
    else:
        box = _grow(grays, box)
        x, y, w, h = [v / scale for v in box]
        x, y = max(0, int(x) // 2 * 2), max(0, int(y) // 2 * 2)
        w, h = even(min(w, info["width"] - x)), even(min(h, info["height"] - y))
        share = w * h / (info["width"] * info["height"])
        if share < 0.2:
            result, how = None, f"largest changing area is only {share:.0%} of the frame"
        elif share > 0.97:
            result, how = None, "the picture already fills the frame"
        else:
            result, how = (x, y, w, h), f"largest changing area ({share:.0%} of the frame)"
    write_json(cache, {"key": key, "box": list(result) if result else None, "how": how})
    preview(frames[len(frames) // 2], scale, result, workdir / "crop_preview.jpg")
    return result, how


def preview(frame, scale, box, path):
    """A picture of the detected box. Outside it everything is heavily blurred and darkened,
    so names and faces from the call's interface stay unreadable."""
    import cv2
    img = frame.copy()
    veil = cv2.GaussianBlur(cv2.resize(img, (48, 27)), (0, 0), 3)
    veil = (cv2.resize(veil, (img.shape[1], img.shape[0])) * 0.35).astype(np.uint8)
    if box:
        x, y, w, h = [int(v * scale) for v in box]
        veil[y:y + h, x:x + w] = img[y:y + h, x:x + w]
        cv2.rectangle(veil, (x, y), (x + w, y + h), (60, 60, 255), 3)
    cv2.imwrite(str(path), veil, [cv2.IMWRITE_JPEG_QUALITY, 85])
    log(f"  crop preview: {path}", "info")
