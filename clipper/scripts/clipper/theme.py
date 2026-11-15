"""The look, resolved from the settings once per clip: which font file each role uses,
sizes scaled to the output, the safe area, and where the hook, captions and cards go.

Sizes in the settings are for a 1080-wide frame; `px()` scales them to the real width,
so a 720x1280 or 2160x3840 output looks the same."""

import shutil
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from . import text
from .config import FALLBACK_FONT
from .util import log

_warned = set()


@lru_cache(maxsize=64)
def _load(path, size):
    return ImageFont.truetype(path, size)


class Theme:
    def __init__(self, cfg):
        self.cfg = cfg
        self.W, self.H = int(cfg["output_width"]), int(cfg["output_height"])
        self.k = self.W / 1080.0
        self.typo = cfg["typography"]
        safe = cfg["safe_area"]
        self.safe_top = int(self.H * safe["top"])
        self.safe_bottom = int(self.H * (1 - safe["bottom"]))
        self.safe_side = int(self.W * safe["side"])
        self.emoji_font = self.typo.get("emoji_font_file") or ""

    def px(self, value):
        """A size from the settings (1080-wide reference) in output pixels."""
        return int(round(float(value) * self.k))

    # ---- fonts -------------------------------------------------------------
    def role(self, name):
        return self.typo.get(name) or {}

    def font_file(self, name):
        """The font file for a role: its own "file", else <fonts_dir>/<Family>-<Weight>.ttf,
        else a system font (with one warning)."""
        role = self.role(name)
        if role.get("file") and Path(role["file"]).exists():
            return str(role["file"])
        family = str(self.typo.get("family", "Inter")).replace(" ", "")
        weight = str(role.get("weight", "Bold")).replace(" ", "")
        folder = Path(self.typo.get("fonts_dir") or "")
        for cand in (folder / f"{family}-{weight}.ttf", folder / f"{family}-{weight}.otf",
                     folder / f"{family}-Bold.ttf", folder / f"{family}.ttf"):
            if cand.exists():
                return str(cand)
        if name not in _warned:
            _warned.add(name)
            log(f"font for '{name}' not found in {folder} -> using a system font", "warn")
        return FALLBACK_FONT

    def size(self, name, value=None):
        return self.px(value if value is not None else self.role(name).get("size", 48))

    def font(self, name, size_px=None):
        """A Pillow font for a role, at its configured size or `size_px` output pixels."""
        return _load(self.font_file(name), int(size_px or self.size(name)))

    def line_height(self, name, size_px=None):
        return int(round((size_px or self.size(name)) * self.role(name).get("line_spacing", 1.25)))

    def ass_font(self, name, workdir):
        """(font name for an ASS file, ASS font size). Copies the font file into
        <workdir>/fonts so libass can load it by a short relative path (Windows drive
        letters break ffmpeg filter arguments)."""
        src = Path(self.font_file(name))
        dest = Path(workdir) / "fonts"
        dest.mkdir(exist_ok=True)
        if not (dest / src.name).exists():
            shutil.copyfile(src, dest / src.name)
        family, style = _load(str(src), 40).getname()
        full = family if style in ("Regular", "", None) else f"{family} {style}"
        return full, self.size(name) * text.ass_scale(str(src))

    # ---- where things go ---------------------------------------------------
    def band(self, footage, look):
        """(top, bottom) of the gameplay band in the fit layout, else None."""
        if look != "fit":
            return None
        aspect = footage.width / max(1, footage.height)
        if self.cfg.get("fit_aspect"):
            aspect = min(aspect, self.cfg["fit_aspect"])     # _fit crops wider frames to this
        band_h = min(self.H, self.W / aspect)
        top = (self.H - band_h) / 2
        return top, top + band_h

    def seam(self, footage, look, cam_h):
        """y of the line between the facecam and the gameplay (stream layout), or the top
        of the gameplay band (fit layout); None in the face layout."""
        if cam_h:
            return int(cam_h)
        band = self.band(footage, look)
        return int(band[0]) if band and band[0] > self.px(40) else None

    def hook_top(self, cam_h, hook_h):
        """Top edge of the hook bar: on the cam/gameplay seam in the stream layout (unless
        hook.placement is "top"), else at hook.position_y, never above the safe area."""
        if cam_h and self.cfg["hook"].get("placement", "auto") != "top":
            return int(cam_h - hook_h / 2)
        return max(self.safe_top, int(self.H * self.cfg["hook"]["position_y"]))

    def caption_center(self, block_h, hook_bottom, cam_h, anchor_y=None):
        """Vertical centre for a caption block `block_h` tall: `anchor_y` (the seam) when
        given, else the configured position; kept below the hook bar and above the bottom
        safe margin."""
        y = anchor_y if anchor_y else self.H * self.cfg["captions"]["position_y"]
        gap = self.px(28)
        top_limit = (hook_bottom or self.safe_top) + gap + block_h / 2
        bottom_limit = self.safe_bottom - block_h / 2
        return int(max(top_limit, min(y, bottom_limit)))

    def card_zones(self, look, band, hook_bottom, faces_bottom=None, captions_on=False):
        """Centres (y) of the zones cards alternate between.
        fit: one above the gameplay band, one below it. Stacked layouts: one zone low on
        the frame, moved under the lowest face when that still fits the safe area."""
        custom = self.cfg.get("comment_zones")
        if custom:
            return [float(z) * self.H for z in custom]
        if band:
            top, bottom = band
            upper = ((hook_bottom or self.safe_top) + top) / 2
            lower = (bottom + self.safe_bottom) / 2
            # a thin band leaves little room: keep cards off the gameplay
            lower = max(lower, bottom + self.px(120))
            return [upper] if captions_on else [upper, min(lower, self.safe_bottom - self.px(90))]
        y = self.H * 0.80
        if faces_bottom:
            y = max(y, faces_bottom + self.px(150))
        return [min(y, self.safe_bottom - self.px(100))]
