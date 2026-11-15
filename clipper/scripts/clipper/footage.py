"""The picture the rest of the pipeline works on.

A video file is probed once. Everything after that (face sampling, the look, the title bar
and cards) sees the *working picture*: the file's frame, or only the part of it inside the
crop box when a call recording is cropped to its shared screen. `width` and `height` are
always the working picture's size; the file's own size is `source_width`/`source_height`.

`prefilter()` is the one place that turns the file's frames into that working picture
(crop, then fewer frames per second). It ends in the label BASE, and every layout
filtergraph starts from BASE, so no later step edits filtergraph text."""

from dataclasses import dataclass

from .util import probe

BASE = "[base]"
MIN_CROP = 64          # px: a crop box smaller than this is a typing mistake


@dataclass(frozen=True)
class Footage:
    source_width: int
    source_height: int
    fps: float = 30.0
    duration: float = 0.0
    crop: tuple = None              # (x, y, w, h) in the file's pixels, or None

    @classmethod
    def from_probe(cls, meta):
        """From util.probe()'s result."""
        return cls(int(meta["width"]), int(meta["height"]), float(meta.get("fps") or 30.0),
                   float(meta.get("duration") or 0.0))

    @classmethod
    def probe(cls, path):
        return cls.from_probe(probe(path))

    @property
    def width(self):
        return self.crop[2] if self.crop else self.source_width

    @property
    def height(self):
        return self.crop[3] if self.crop else self.source_height

    def cropped(self, box):
        """The same file seen through `box` = (x, y, w, h) in the file's pixels. Values are
        rounded down to even numbers (what ffmpeg does for yuv420p anyway). Raises
        ValueError, with a message fit to show, when the box is not inside the picture."""
        x, y, w, h = [int(v) // 2 * 2 for v in box]
        if w < MIN_CROP or h < MIN_CROP:
            raise ValueError(f"crop box {w}x{h} is too small (at least {MIN_CROP} px each way)")
        if x < 0 or y < 0 or x + w > self.source_width or y + h > self.source_height:
            raise ValueError(f"crop box {x},{y},{w},{h} does not fit inside the "
                             f"{self.source_width}x{self.source_height} picture")
        return Footage(self.source_width, self.source_height, self.fps, self.duration,
                       (x, y, w, h))

    @property
    def crop_filter(self):
        """ffmpeg's crop filter for the crop box, or '' when there is none."""
        if not self.crop:
            return ""
        x, y, w, h = self.crop
        return f"crop={w}:{h}:{x}:{y}"

    def prefilter(self, out_fps):
        """'[0:v]<crop>,<fps>[base]': the working picture at `out_fps`. Frames are dropped
        here, before any scaling or blurring, the cheapest place to drop them. With nothing
        to do it is ffmpeg's `null` filter, which passes frames through untouched."""
        steps = [self.crop_filter] if self.crop else []
        if out_fps < self.fps - 0.5:
            steps.append(f"fps={out_fps:.3f}")
        return f"[0:v]{','.join(steps) or 'null'}{BASE}"

