"""The hook bar: the title pinned over the top of the clip.

Drawn with Pillow rather than libass because libass cannot render colour emoji (they
come out as empty boxes), and because this gets us smooth rounded corners.

Line breaks are balanced (no single word left on a line), and a long title shrinks in
steps before it is allowed a third line. Emoji are dropped unless writing.emoji is on.
One phrase may be marked *like this* to be drawn in the accent colour.

hook.style picks the look: "bar" (white plate, dark text), "pill" (coloured plate, white
text) or "text" (no plate: white outlined text, like a phone app's text tool)."""

import re

from PIL import Image, ImageDraw, ImageFilter

from . import text
from .theme import Theme

_ACCENT = re.compile(r"\*([^*]+)\*")


def split_accent(raw):
    """'He *crashes the server* live' -> (plain text, [accented word indexes])."""
    words, marked = [], set()
    pos = 0
    for m in _ACCENT.finditer(raw):
        words += raw[pos:m.start()].split()
        inner = m.group(1).split()
        if not marked:                       # only the first marked phrase counts
            marked = set(range(len(words), len(words) + len(inner)))
        words += inner
        pos = m.end()
    words += raw[pos:].replace("*", "").split()
    return " ".join(words), sorted(marked)


def render(raw, cfg, path, theme=None):
    """Draw the hook bar to `path`. Returns (path, width, height, margin) where margin
    is the transparent shadow border around the bar inside the image."""
    theme = theme or Theme(cfg)
    hk, role = cfg["hook"], theme.role("hook")
    plain, marked = split_accent(raw)
    if not cfg.get("writing", {}).get("emoji", False):
        kept, new_marked = [], []
        for i, word in enumerate(plain.split()):
            word = text.strip_emoji(word)
            if word:
                if i in marked:
                    new_marked.append(len(kept))
                kept.append(word)
        words, marked = kept, new_marked
    else:
        words = plain.split()
    if not words:
        raise ValueError("empty hook text")

    ss = 2                                    # draw at 2x, shrink: smooth edges and text
    top, low = theme.size("hook"), theme.size("hook", role.get("min_size", 42))
    sizes = [s * ss for s in range(top, low - 1, -max(2, theme.px(4)))] or [top * ss]
    pad_x = lambda s: int(s * 0.62)           # noqa: E731
    max_w = theme.W - 2 * theme.px(hk.get("margin_x", 110))
    font, size, lines = text.fit(
        words, lambda s: theme.font("hook", s), sizes,
        max_w=(max_w - 2 * pad_x(top)) * ss, max_lines=int(role.get("max_lines", 2)))
    em = int(size * 1.05)
    line_h = int(size * role.get("line_spacing", 1.24))
    px, py = pad_x(size), int(size * 0.36)
    widths = [text.line_width(ln, font, em) for ln in lines]
    box_w = int(max(widths) + 2 * px)
    box_h = int(len(lines) * line_h + 2 * py)
    style = hk.get("style", "bar")
    bare = style == "text"
    stroke = max(2, int(size * 0.10)) if bare else 0
    sh = int(size * 0.30) if hk.get("shadow", True) else 0     # room for the shadow
    if bare:
        sh = max(sh, stroke * 2)

    img = Image.new("RGBA", (box_w + 2 * sh, box_h + 2 * sh), (0, 0, 0, 0))
    radius = int(size * hk.get("radius", 0.32))
    if sh and not bare:
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (sh, sh + sh // 3, sh + box_w, sh + box_h + sh // 3), radius=radius,
            fill=(0, 0, 0, 70))
        img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(sh / 2.2)))
    fill = hk.get("pill_color", "E8343A") if style == "pill" else hk.get("background", "FFFFFF")
    if not bare:
        plate = text.rounded((box_w, box_h), radius,
                             text.rgb(fill, hk.get("background_opacity", 1)), ss=2)
        img.alpha_composite(plate, (sh, sh))
    draw = ImageDraw.Draw(img)
    if style == "bar":
        ink, accent = (text.rgb(hk.get("text_color", "111111")),
                       text.rgb(hk.get("accent_color", "E0362F")))
    else:
        ink, accent = text.rgb("FFFFFF"), text.rgb("FFE14D")
    k = 0
    for row, (line, width) in enumerate(zip(lines, widths)):
        colors = [accent if k + j in marked else ink for j in range(len(line))]
        text.draw_line(img, draw, line, sh + (box_w - width) / 2, sh + py + row * line_h,
                       font, em, line_h, ink, theme.emoji_font, colors,
                       stroke=stroke, stroke_fill=(0, 0, 0, 255))
        k += len(line)

    img = img.resize((img.width // ss, img.height // ss), Image.LANCZOS)
    img.save(path)
    return path, img.width, img.height, sh // ss


def overlay(base_label, index, top, fps, cfg, theme, out_label="vhk"):
    """ffmpeg filters that put the hook PNG (input `index`) on `base_label`.
    With the soft animation it fades in over a short slide down, then holds: only the
    first few frames of the PNG are generated, the last one is repeated for free."""
    if cfg["hook"].get("animation", "soft") != "soft":
        return f"[{base_label}][{index}:v]overlay=(W-w)/2:{top}[{out_label}]"
    d, slide = 0.28, theme.px(22)
    frames = int(round((d + 0.1) * fps))
    y = f"{top}-{slide}*pow(max(0,1-t/{d}),2)"
    return (f"[{index}:v]format=rgba,loop=loop={frames}:size=1:start=0,"
            f"fade=t=in:st=0:d={d}:alpha=1[hk];"
            f"[{base_label}][hk]overlay=x=(W-w)/2:y='{y}':eval=frame[{out_label}]")
