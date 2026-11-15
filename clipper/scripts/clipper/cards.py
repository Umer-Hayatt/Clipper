"""Comment cards for the `comments` style, drawn with Pillow (like hook.py).

Two kinds, kept visibly different on purpose:
- "chat": a REAL message from the stream's live-chat replay. Rounded card, generic
  avatar, a small "viewer · live chat" label and (optionally) a heart. The name is hidden
  and the card says where the message came from, so it never claims to be a comment on
  the post itself. No like counts: chat has none, so none are made up.
- "reaction": a line Claude wrote (no chat available). Drawn as a plain dark sticker
  with no avatar, name, label or heart, so it can't pass for a viewer's comment.

Designed from scratch; no platform logos or trademarks. Fonts, sizes and colours come
from the theme (cfg["typography"], cfg["cards"])."""

import hashlib
import math

from PIL import Image, ImageDraw, ImageFilter

from . import text
from .theme import Theme

THEMES = {
    "light": {"card": (255, 255, 255), "text": (17, 17, 20), "sub": (112, 114, 124),
              "heart": (236, 72, 100), "shadow": 80},
    "dark": {"card": (26, 27, 31), "text": (244, 244, 247), "sub": (150, 152, 164),
             "heart": (255, 92, 120), "shadow": 130},
}
AVATARS = [(255, 159, 67), (84, 160, 255), (46, 204, 113), (155, 89, 182), (255, 107, 129),
           (0, 184, 148), (253, 203, 110), (108, 92, 231)]
SS = 2          # draw at 2x and shrink: smooth corners, avatar and text


def _wrap(words, font, em, width, max_lines):
    lines = text.balanced(words, font, width, None, em)
    if len(lines) > max_lines:
        flat = [w for ln in lines for w in ln]
        while flat and text.greedy_lines(flat + ["…"], font, width, em) > max_lines:
            flat.pop()
        lines = text.balanced(flat + ["…"], font, width, None, em)
    return lines


def _heart(draw, cx, cy, r, color):
    draw.ellipse((cx - r, cy - r * 0.6, cx, cy + r * 0.4), fill=color)
    draw.ellipse((cx, cy - r * 0.6, cx + r, cy + r * 0.4), fill=color)
    draw.polygon([(cx - r * 0.95, cy + r * 0.05), (cx + r * 0.95, cy + r * 0.05),
                  (cx, cy + r * 1.05)], fill=color)


def _finish(card, shadow_alpha, radius, path):
    """Put the card on a soft shadow, shrink from 2x and save."""
    m = int(18 * SS)
    img = Image.new("RGBA", (card.width + 2 * m, card.height + 2 * m), (0, 0, 0, 0))
    if shadow_alpha:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            (m, m + m // 3, m + card.width, m + card.height + m // 3), radius=radius,
            fill=(0, 0, 0, shadow_alpha))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(m / 2.4)))
    img.alpha_composite(card, (m, m))
    img = img.resize((img.width // SS, img.height // SS), Image.LANCZOS)
    img.save(path)
    return path, img.width, img.height


def _chat(item, cfg, theme, path):
    cd = cfg["cards"]
    look = THEMES.get(cd.get("theme", "light"), THEMES["light"])
    size = theme.size("card") * SS
    font = theme.font("card", size)
    label_size = theme.size("card_label") * SS
    label_font = theme.font("card_label", label_size)
    em = int(size * 1.05)
    line_h = int(size * theme.role("card").get("line_spacing", 1.28))
    label_h = int(label_size * 1.5)
    pad = int(size * 0.62)
    av = int(size * 1.75)
    gap = int(size * 0.5)
    heart = int(size * 1.15) if cd.get("heart", True) else 0
    max_w = int(theme.W * cd.get("max_width", 0.8)) * SS
    text_w = max_w - 2 * pad - av - gap - heart

    name = item.get("name") or "viewer"
    tag = "  ·  live chat"
    lines = _wrap(item["text"].split(), font, em, text_w, 3)
    body_w = max([text.line_width(ln, font, em) for ln in lines]
                 + [label_font.getlength(name + tag)])
    card_w = int(min(max_w, max(2 * pad + av + gap + body_w + heart, theme.px(380) * SS)))
    body_h = label_h + len(lines) * line_h
    card_h = int(2 * pad + max(body_h, av))
    radius = int(size * cd.get("radius", 0.62))

    alpha = int(255 * cd.get("opacity", 0.96))
    card = text.rounded((card_w, card_h), radius, look["card"] + (alpha,), ss=2)
    draw = ImageDraw.Draw(card)
    x0 = pad
    y0 = pad + max(0, (av - body_h) // 2) if body_h < av else pad
    ay = pad + max(0, (body_h - av) // 2) if body_h > av * 1.6 else pad

    # generic avatar: a coloured disc with a plain head-and-shoulders shape, no initials
    seed = int(hashlib.md5(item["text"].encode("utf-8")).hexdigest(), 16)
    disc = Image.new("RGBA", (av, av), (0, 0, 0, 0))
    dd = ImageDraw.Draw(disc)
    dd.ellipse((0, 0, av - 1, av - 1), fill=AVATARS[seed % len(AVATARS)] + (255,))
    c = av / 2
    dd.ellipse((c - av * 0.17, c - av * 0.29, c + av * 0.17, c + av * 0.05),
               fill=(255, 255, 255, 235))
    dd.ellipse((c - av * 0.31, c + av * 0.12, c + av * 0.31, c + av * 0.80),
               fill=(255, 255, 255, 235))
    mask = Image.new("L", (av, av), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, av - 1, av - 1), fill=255)
    disc.putalpha(mask)                      # the shoulders are cut off by the disc edge
    card.alpha_composite(disc, (x0, ay))

    tx = x0 + av + gap
    draw.text((tx, y0 + (label_h - label_size * 1.2) / 2), name, font=label_font,
              fill=look["text"])
    draw.text((tx + label_font.getlength(name), y0 + (label_h - label_size * 1.2) / 2), tag,
              font=label_font, fill=look["sub"])
    for row, ln in enumerate(lines):
        text.draw_line(card, draw, ln, tx, y0 + label_h + row * line_h, font, em, line_h,
                       look["text"], theme.emoji_font)
    if heart:
        _heart(draw, card_w - pad - size * 0.34, card_h / 2, size * 0.30, look["heart"])
    return _finish(card, look["shadow"], radius, path)


def _sticker(item, cfg, theme, path):
    cd = cfg["cards"]
    size = theme.size("sticker") * SS
    font = theme.font("sticker", size)
    em = int(size * 1.05)
    line_h = int(size * theme.role("sticker").get("line_spacing", 1.2))
    pad_x, pad_y = int(size * 0.72), int(size * 0.40)
    max_w = int(theme.W * cd.get("max_width", 0.8)) * SS
    lines = _wrap(item["text"].split(), font, em, max_w - 2 * pad_x, 2)
    widths = [text.line_width(ln, font, em) for ln in lines]
    card_w = int(max(widths) + 2 * pad_x)
    card_h = int(len(lines) * line_h + 2 * pad_y)
    radius = int(min(card_h / 2, size * 0.62))
    edge = (255, 255, 255, 60) if cd.get("sticker_edge", True) else None
    card = text.rounded((card_w, card_h), radius,
                        (16, 17, 20, int(255 * cd.get("sticker_opacity", 0.84))),
                        outline=edge, width=max(1, theme.px(2)) * SS // 2 if edge else 0, ss=2)
    draw = ImageDraw.Draw(card)
    for row, (ln, width) in enumerate(zip(lines, widths)):
        text.draw_line(card, draw, ln, (card_w - width) / 2, pad_y + row * line_h, font, em,
                       line_h, (255, 255, 255), theme.emoji_font)
    return _finish(card, 90, radius, path)


def render(item, cfg, path, theme=None):
    """Draw one card to `path`. item = {"kind": "chat"|"reaction", "text", "name"?}.
    Returns (path, width, height)."""
    theme = theme or Theme(cfg)
    if not cfg.get("writing", {}).get("emoji", False) and item.get("kind") != "chat":
        item = dict(item, text=text.strip_emoji(item["text"]))     # real chat is never edited
    if not item["text"].strip():
        raise ValueError("empty card")
    return (_chat if item.get("kind") == "chat" else _sticker)(item, cfg, theme, path)


def schedule(items, dur, zones, hold, first=1.0):
    """Show times for each card: [(item, start, end, zone_index)]. Cards alternate
    between zones. A card stays long enough to read (about 1.4 s + 0.3 s per word, at
    most `hold`), or until the next card in its zone; none shows in the first second,
    while the hook bar settles."""
    items = sorted(items, key=lambda c: c["at"])
    out, last = [], -9.0
    for i, it in enumerate(items):
        a = min(max(first, it["at"], last + 0.6), max(first, dur - 1.6))
        last = a
        out.append([it, a, None, i % len(zones)])
    for i, row in enumerate(out):
        words = len(str(row[0]["text"]).split())
        need = min(max(1.8, 1.4 + 0.3 * words), hold)
        nxt = [r[1] for r in out[i + 1:] if r[3] == row[3]]
        row[2] = min(row[1] + need, nxt[0] - 0.25 if nxt else dur, dur - 0.1)
    return [tuple(r) for r in out if r[2] - r[1] >= 1.2]


def overlay_chain(base_label, placed, first_input, zones, W, fps=30, soft=True, slide=14):
    """ffmpeg filters that bring each card in (fade + short slide up) and fade it out.
    placed = [(png_w, png_h, start, end, zone)]; card PNGs are inputs first_input..
    Each card's stream only exists for its own seconds on screen, so a card costs
    nothing while it isn't showing."""
    parts, cur = [], base_label
    for k, (w, h, a, b, z) in enumerate(placed):
        idx = first_input + k
        life = b - a
        frames = int(math.ceil(life * fps)) + 1
        zy = int(zones[z] - h / 2)
        x = int((W - w) / 2)
        if soft:
            parts.append(f"[{idx}:v]format=rgba,loop=loop={frames}:size=1:start=0,"
                         f"fade=t=in:st=0:d=0.18:alpha=1,"
                         f"fade=t=out:st={max(0.2, life - 0.22):.2f}:d=0.22:alpha=1,"
                         f"setpts=PTS+{a:.2f}/TB[c{k}]")
            y = f"{zy}+{slide}*pow(max(0,1-(t-{a:.2f})/0.2),2)"
        else:
            parts.append(f"[{idx}:v]format=rgba[c{k}]")
            y = str(zy)
        nxt = f"cv{k}"
        parts.append(f"[{cur}][c{k}]overlay=x={x}:y='{y}':eof_action=pass:"
                     f"enable='between(t,{a:.2f},{b:.2f})':eval=frame[{nxt}]")
        cur = nxt
    return ";".join(parts), cur
