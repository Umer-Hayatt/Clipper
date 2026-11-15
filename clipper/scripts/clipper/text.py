"""Shared text tools for the hook bar, the comment cards and the captions: emoji runs,
measuring, balanced line breaks, shrinking to fit, smooth rounded shapes. One place, so
all three wrap and space text the same way."""

import struct
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),   # pictographs, emoticons, symbols
    (0x2600, 0x27BF),     # misc symbols and dingbats
    (0x1F000, 0x1F2FF),   # tiles, enclosed characters
    (0xFE00, 0xFE0F),     # variation selectors
    (0x200D, 0x200D),     # zero-width joiner
    (0x2B00, 0x2BFF),
)
_JOINERS = {0x200D} | set(range(0xFE00, 0xFE10))

# Words a line should not end on (the next word belongs with them).
_WEAK_END = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "and", "or", "but", "so",
             "is", "it's", "i", "i'm", "my", "your", "his", "her", "this", "that", "with", "vs"}


def is_emoji(ch):
    point = ord(ch)
    return any(lo <= point <= hi for lo, hi in _EMOJI_RANGES)


def strip_emoji(text):
    """The text without emoji (and without the double spaces they leave behind)."""
    return " ".join("".join(ch for ch in text if not is_emoji(ch)).split())


def has_emoji(text):
    return any(is_emoji(ch) and ord(ch) not in _JOINERS for ch in text)


def runs(word):
    """Split a word into consecutive (is_emoji, text) runs."""
    out = []
    for ch in word:
        kind = is_emoji(ch)
        if out and out[-1][0] == kind:
            out[-1][1] += ch
        else:
            out.append([kind, ch])
    return [(kind, text) for kind, text in out]


@lru_cache(maxsize=4)
def _emoji_font(path):
    """(font, native size). Segoe UI Emoji / Noto scale freely; Apple's ships a single
    160 px bitmap strike and rejects every other size."""
    try:
        return ImageFont.truetype(path, 109), 109
    except OSError:
        return ImageFont.truetype(path, 160), 160


def emoji_image(ch, size, path):
    """One colour emoji, rendered at the font's native size then scaled."""
    font, native = _emoji_font(path)
    tile = Image.new("RGBA", (native * 2, native * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((native // 4, native // 4), ch, font=font, embedded_color=True)
    box = tile.getbbox()
    if not box:
        raise ValueError("empty glyph")
    return tile.crop(box).resize((size, size), Image.LANCZOS)


def measure(word, font, em=0, spacing=0.0):
    """Width of one word in pixels. `em` is the width given to each emoji; `spacing` is
    extra space after every character (letter spacing)."""
    width = 0.0
    for emoji, chunk in runs(word):
        if emoji:
            width += sum(em for ch in chunk if ord(ch) not in _JOINERS)
        else:
            width += font.getlength(chunk) + spacing * len(chunk)
    return width


def line_width(words, font, em=0, spacing=0.0):
    if not words:
        return 0.0
    space = font.getlength(" ") + spacing
    return sum(measure(w, font, em, spacing) for w in words) + space * (len(words) - 1)


def greedy_lines(words, font, max_w, em=0, spacing=0.0):
    """How many lines a plain first-fit wrap needs (the fewest possible)."""
    count, cur = 1, []
    for word in words:
        if cur and line_width(cur + [word], font, em, spacing) > max_w:
            count += 1
            cur = [word]
        else:
            cur.append(word)
    return count


def balanced(words, font, max_w, max_lines=None, em=0, spacing=0.0):
    """Break `words` into the fewest lines that fit `max_w`, then even the lines out:
    similar widths, no single word left alone on the last line, no line ending on a weak
    word like "the". Returns a list of word lists. A single word wider than `max_w` gets
    its own (overflowing) line; callers shrink the font before that happens."""
    words = list(words)
    if not words:
        return []
    n = greedy_lines(words, font, max_w, em, spacing)
    if max_lines:
        n = max(1, min(n, max_lines)) if n <= max_lines else n
    if n == 1:
        return [words]
    count = len(words)
    width = {}

    def w(i, j):                           # width of words[i:j]
        if (i, j) not in width:
            width[(i, j)] = line_width(words[i:j], font, em, spacing)
        return width[(i, j)]

    target = w(0, count) / n
    INF = float("inf")
    best = [[INF] * (count + 1) for _ in range(n + 1)]
    back = [[0] * (count + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for line in range(1, n + 1):
        for j in range(line, count + 1):
            for i in range(line - 1, j):
                if best[line - 1][i] == INF:
                    continue
                wide = w(i, j)
                if wide > max_w and j - i > 1:
                    continue
                cost = ((wide - target) / max_w) ** 2
                if wide > max_w:
                    cost += 4.0                           # one over-long word: unavoidable
                last = j == count
                end_word = words[j - 1].lower().strip(".,!?'\"")
                if not last and end_word in _WEAK_END:
                    # "the game" must not be split; "and" at a line end is only a little weak
                    cost += 0.06 if end_word in ("and", "or", "but", "so") else 0.18
                if not last and words[j - 1][-1:] in ",;:.!?":
                    cost -= 0.06                          # a break after punctuation reads best
                if last and j - i == 1 and count > 2:
                    cost += 0.30                          # an orphan word
                if last and wide > 1.25 * target:
                    cost += 0.05                          # a heavy last line reads worse
                total = best[line - 1][i] + cost
                if total < best[line][j]:
                    best[line][j], back[line][j] = total, i
    if best[n][count] == INF:                             # should not happen; be safe
        out, cur = [], []
        for word in words:
            if cur and line_width(cur + [word], font, em, spacing) > max_w:
                out.append(cur)
                cur = [word]
            else:
                cur.append(word)
        return out + [cur]
    lines, j = [], count
    for line in range(n, 0, -1):
        i = back[line][j]
        lines.append(words[i:j])
        j = i
    return lines[::-1]


def fit(words, make_font, sizes, max_w, max_lines, em_ratio=1.05):
    """The largest size in `sizes` at which `words` wrap into `max_lines`. Returns
    (font, size, lines). If even the smallest needs more lines, it gets them."""
    font = size = lines = None
    for size in sizes:
        font = make_font(size)
        em = int(size * em_ratio)
        if (greedy_lines(words, font, max_w, em) <= max_lines
                and all(measure(w, font, em) <= max_w for w in words)):
            break
    lines = balanced(words, font, max_w, None, int(size * em_ratio))
    return font, size, lines


def draw_line(img, draw, words, x, y, font, em, line_h, color, emoji_font, colors=None,
              stroke=0, stroke_fill=None):
    """Draw one line of words at (x, y), colour emoji included. `colors` may give a
    colour per word. Returns the x where the line ended."""
    space = font.getlength(" ")
    ascent, descent = font.getmetrics()
    text_y = y + (line_h - (ascent + descent)) / 2
    for k, word in enumerate(words):
        fill = colors[k] if colors else color
        for emoji, chunk in runs(word):
            if emoji:
                for ch in chunk:
                    if ord(ch) in _JOINERS:
                        continue
                    try:
                        glyph = emoji_image(ch, em, emoji_font)
                    except (OSError, ValueError):
                        continue               # unsupported glyph: skip, never draw tofu
                    img.alpha_composite(glyph, (int(x), int(y + (line_h - em) / 2)))
                    x += em
            else:
                if stroke:
                    draw.text((x, text_y), chunk, font=font, fill=fill, stroke_width=stroke,
                              stroke_fill=stroke_fill)
                else:
                    draw.text((x, text_y), chunk, font=font, fill=fill)
                x += font.getlength(chunk)
        x += space
    return x - space


def rounded(size, radius, fill, outline=None, width=0, ss=3):
    """A rounded rectangle as an RGBA image with smooth (anti-aliased) corners."""
    w, h = int(size[0]), int(size[1])
    big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=int(radius * ss), fill=fill,
                           outline=outline, width=int(width * ss))
    return big.resize((w, h), Image.LANCZOS)


def rgb(value, alpha=None):
    """'RRGGBB' -> (r, g, b) or (r, g, b, a) with alpha 0-1."""
    v = str(value).lstrip("#")
    out = tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    return out if alpha is None else out + (int(round(255 * max(0.0, min(1.0, alpha)))),)


@lru_cache(maxsize=16)
def ass_scale(font_file):
    """libass sizes a font by its full line height (ascent + descent), Pillow by its em
    square. Returns line height / em, so ASS size = Pillow size x this."""
    try:
        with open(font_file, "rb") as fh:
            data = fh.read()
        num = struct.unpack(">H", data[4:6])[0]
        tables = {}
        for i in range(num):
            tag, _, off, length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
            tables[tag] = (off, length)
        upm = struct.unpack(">H", data[tables[b"head"][0] + 18: tables[b"head"][0] + 20])[0]
        if b"OS/2" in tables:
            off = tables[b"OS/2"][0]
            win_a, win_d = struct.unpack(">HH", data[off + 74: off + 78])
            if win_a + win_d:
                return (win_a + win_d) / upm
        off = tables[b"hhea"][0]
        asc, desc = struct.unpack(">hh", data[off + 4: off + 8])
        return (asc - desc) / upm
    except (OSError, KeyError, struct.error, ZeroDivisionError):
        return 1.2
