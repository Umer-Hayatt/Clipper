"""Word-by-word captions as an ASS subtitle file (drawn by ffmpeg's libass in the same
pass as the encode, so they cost almost nothing).

Python decides everything about the layout: which words form a phrase, where the lines
break and where the block sits. libass only draws. That is what keeps the text still:
the spoken word changes colour, nothing moves or re-wraps while a phrase is on screen.

Look and behaviour come from cfg["captions"] and cfg["typography"]["caption"]."""

import re

from . import text
from .theme import Theme

# Pauses shorter than this keep the previous phrase on screen instead of blinking off.
# Longer silences do clear the caption, which reads as intentional rather than broken.
HOLD_THROUGH_PAUSE = 1.2
PAUSE_BREAK = 0.55          # a gap this long between words starts a new phrase
LEAD = 0.03                 # a phrase appears this much before its first word

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size:.1f},{primary},{primary},{outline_c},&H00000000,0,0,0,0,100,100,\
{spacing},0,1,{outline},0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_TAG = re.compile(r"^[\[(].*[\])]$")          # [Music], (laughs)
_EDGE = "\"“”‘()[]{}<>«»-–—"
_TRAIL = ".,;:!?…"
_STARTERS = set("""you we they he she it the this that these those there here so and but or if
when what why how who where have has had do does did is are was were be a an in on at to of for
with now then well yeah yes no okay oh just like can could would will my your our his her their
its as not all some any one""".split())      # "I" is never listed: it is capital anyway

# Swear words masked on screen when captions.mask_profanity is on ("fuck" -> "F***").
_SWEAR = re.compile(r"^(fuck|shit|bitch|asshole|bastard|dick|pussy|cunt|motherfuck|"
                    r"nigg|fag|retard)", re.I)


def _mask(word):
    if not _SWEAR.match(word):
        return word
    core = word.rstrip("?!'")
    return core[0] + "*" * (len(core) - 1) + word[len(core):]


def _norm(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def mark_keywords(tokens, keywords):
    """Flag tokens that are part of a keyword phrase (whole words, in order)."""
    phrases = [[_norm(w) for w in str(k).split() if _norm(w)] for k in keywords or ()]
    norms = [_norm(t["w"]) for t in tokens]
    for ph in (ph for ph in phrases if ph):
        n = len(ph)
        for i in range(len(tokens) - n + 1):
            if norms[i:i + n] == ph:
                for t in tokens[i:i + n]:
                    t["key"] = True
    return tokens


def _color(rrggbb):
    """RRGGBB (human) -> &HBBGGRR& (ASS byte order)."""
    v = str(rrggbb).lstrip("#")
    return f"&H{v[4:6]}{v[2:4]}{v[0:2]}&".upper()


def _alpha(opacity):
    """Opacity 0-1 -> ASS alpha (&H00& = solid, &HFF& = invisible)."""
    return f"&H{int(round(255 * (1 - max(0.0, min(1.0, opacity))))):02X}&"


def _timestamp(seconds):
    seconds = max(0.0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):d}:{int(minutes):02d}:{secs:05.2f}"


def _escape(word):
    return word.replace("\\", "").replace("{", "(").replace("}", ")")


def _tokens(words, cap):
    """Clean the raw words for display. Returns [{s, e, w, end, comma}] where `end`
    marks the end of a sentence and `comma` a clause break. Timing is untouched."""
    mode = cap.get("punctuation", "minimal")
    out = []
    for raw in words:
        t = raw["w"].replace(">>", " ").strip()
        if not t or _TAG.match(t):
            continue
        core = t.strip(_EDGE)
        base = core.rstrip(_TRAIL)
        tail = core[len(base):]
        base = base.lstrip(_TRAIL + _EDGE)
        end = any(ch in tail for ch in ".!?…")
        comma = any(ch in tail for ch in ",;:")
        if not base:                              # a lone "." or "-": pass its meaning back
            if out:
                out[-1]["end"] = out[-1]["end"] or end
                out[-1]["comma"] = out[-1]["comma"] or comma
            continue
        if mode == "all":
            shown = core
        elif mode == "none":
            shown = base
        else:                                     # minimal: only ? and ! carry meaning
            shown = base + ("?" if "?" in tail else "!" if "!" in tail else "")
        out.append({"s": raw["s"], "e": max(raw["e"], raw["s"] + 0.05), "w": _escape(shown),
                    "end": end, "comma": comma})
    # Whisper sometimes leaves out a full stop but still capitalises the next sentence's
    # first word ("one before You"). A capitalised common word after a word that did not end
    # a sentence is treated as a sentence start, so the phrase breaks there. Names are kept
    # because only the common words in _STARTERS count.
    for i in range(1, len(out)):
        w = out[i]["w"]
        if w[:1].isupper() and w.lower().rstrip("?!") in _STARTERS and not out[i - 1]["end"]:
            out[i - 1]["end"] = True
    case = cap.get("case", "sentence")
    for i, tok in enumerate(out):
        if case == "upper":
            tok["w"] = tok["w"].upper()
        elif case == "sentence":
            first = i == 0 or out[i - 1]["end"] or tok["s"] - out[i - 1]["e"] >= 1.0
            if first and tok["w"][:1].islower():
                tok["w"] = tok["w"][:1].upper() + tok["w"][1:]
            elif tok["w"] == "i" or tok["w"].startswith("i'"):
                tok["w"] = "I" + tok["w"][1:]
    if cap.get("mask_profanity"):
        for tok in out:
            tok["w"] = _mask(tok["w"])
    return out


def _groups(tokens, fits, cap):
    """Phrases: as many words as fit the caption block, broken at sentence ends, clause
    commas and pauses. Then single leftover words are folded into a neighbour (not in
    the "word" chunk mode, which shows max_words words at a time, usually one)."""
    limit = max(1, int(cap.get("max_words", 5)))
    if cap.get("chunk", "phrase") == "word":
        groups, cur = [], []
        for tok in tokens:
            if cur and (len(cur) >= limit or not fits(cur + [tok])):
                groups.append(cur)
                cur = []
            cur.append(tok)
            if tok["end"] or tok["comma"]:
                groups.append(cur)
                cur = []
        return groups + ([cur] if cur else [])
    # 1. natural stretches: up to a sentence end, a pause, or a clause comma
    stretches, cur = [], []
    for i, tok in enumerate(tokens):
        cur.append(tok)
        gap = tokens[i + 1]["s"] - tok["e"] if i + 1 < len(tokens) else 99
        if tok["end"] or gap > PAUSE_BREAK or (tok["comma"] and len(cur) >= 3):
            stretches.append(cur)
            cur = []
    if cur:
        stretches.append(cur)
    # 2. a long stretch is cut into even parts (7 words -> 4 + 3, not 5 + 2)
    groups = []
    for stretch in stretches:
        n = len(stretch)
        parts = -(-n // limit)
        size, extra, at = n // parts, n % parts, 0
        for p in range(parts):
            take = size + (1 if p < extra else 0)
            piece, cur = stretch[at:at + take], []
            at += take
            for tok in piece:                     # still too wide for the block: split again
                if cur and not fits(cur + [tok]):
                    groups.append(cur)
                    cur = []
                cur.append(tok)
            if cur:
                groups.append(cur)

    merged = []
    for g in groups:                              # a lone word joins the phrase before it
        prev = merged[-1] if merged else None
        if (prev and len(g) == 1 and not prev[-1]["end"]
                and g[0]["s"] - prev[-1]["e"] <= 0.35 and len(prev) <= limit
                and fits(prev + g)):
            prev.extend(g)
        else:
            merged.append(list(g))
    out = []
    for i, g in enumerate(merged):                # ...or the phrase after it
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        if (nxt and len(g) == 1 and not g[0]["end"] and nxt[0]["s"] - g[0]["e"] <= 0.35
                and len(nxt) <= limit and fits(g + nxt)):
            nxt[:0] = g
        else:
            out.append(g)
    return out


def _box(x0, y0, x1, y1, r):
    """ASS drawing commands for a rounded rectangle."""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    k = r * 0.45                                   # control-point offset for a round corner
    p = lambda *v: " ".join(f"{n:.0f}" for n in v)  # noqa: E731
    return (f"m {p(x0 + r, y0)} l {p(x1 - r, y0)} b {p(x1 - k, y0, x1, y0 + k, x1, y0 + r)} "
            f"l {p(x1, y1 - r)} b {p(x1, y1 - k, x1 - k, y1, x1 - r, y1)} "
            f"l {p(x0 + r, y1)} b {p(x0 + k, y1, x0, y1 - k, x0, y1 - r)} "
            f"l {p(x0, y0 + r)} b {p(x0, y0 + k, x0 + k, y0, x0 + r, y0)}")


def build(words, start, end, path, cfg, theme=None, hook_bottom=0, cam_h=None, keywords=(),
          seam=None, quiet=()):
    """Write an ASS file covering [start, end); timings are rebased to the clip.
    `keywords` are words/short phrases drawn in captions.keyword_color; `seam` is the y of
    the cam/gameplay seam (used when captions.anchor is "seam"); phrases that overlap a
    `quiet` window (clip seconds, a big beat word is on screen) are left out.
    Returns (path, info) where info lists each phrase's on-screen box for checks."""
    theme = theme or Theme(cfg)
    cap = cfg["captions"]
    role = theme.role("caption")
    W = theme.W
    font = theme.font("caption")
    spacing = theme.px(role.get("letter_spacing", 0))
    line_h = theme.line_height("caption")
    max_w = W * float(cap.get("max_width", 0.8)) * 0.97      # 3 % slack: Pillow vs libass
    max_lines = max(1, int(cap.get("max_lines", 2)))
    ass_name, ass_size = theme.ass_font("caption", path.parent)

    base, hi = _color(cap["text_color"]), _color(cap["highlight_color"])
    mode = cap.get("highlight_mode", "color")
    on_box = _color(cap.get("highlight_text_color", "111111"))
    outline = theme.px(cap.get("outline", 3))
    shadow = theme.px(cap.get("shadow", 0))
    blur = theme.px(cap.get("shadow_blur", 4))
    anim = cap.get("animation", "soft")
    soft = anim in ("soft", "pop")
    italic = "\\i1" if cap.get("italic") else ""
    cycle = [_color(c) for c in cap.get("color_cycle") or []]
    key_c = _color(cap["keyword_color"]) if cap.get("keyword_color") else None
    anchor_y = seam if cap.get("anchor") == "seam" else None

    def fits(group):
        ws = [t["w"] for t in group]
        return (text.greedy_lines(ws, font, max_w, 0, spacing) <= max_lines
                and all(text.measure(w, font, 0, spacing) <= max_w for w in ws))

    inside = [w for w in words if w["e"] > start and w["s"] < end]
    tokens = _tokens(inside, cap)
    if key_c:
        mark_keywords(tokens, keywords)
    groups = _groups(tokens, fits, cap)
    dur = end - start

    out = [HEADER.format(w=W, h=theme.H, font=ass_name, size=ass_size, primary=base,
                         outline_c=_color(cap.get("outline_color", "000000")),
                         spacing=spacing, outline=outline)]
    boxes = []
    prev_end = 0.0
    for gi, group in enumerate(groups):
        g_start = max(prev_end, group[0]["s"] - start - LEAD, 0.0)
        nxt = groups[gi + 1][0]["s"] - start - LEAD if gi + 1 < len(groups) else dur
        last_e = group[-1]["e"] - start
        into_silence = nxt - last_e > HOLD_THROUGH_PAUSE
        g_end = min(last_e + 0.35 if into_silence else nxt, dur)
        if g_end - g_start < 0.08:
            continue
        prev_end = g_end
        if any(g_start < q1 and g_end > q0 for q0, q1 in quiet):
            continue

        lines = text.balanced([t["w"] for t in group], font, max_w, max_lines, 0, spacing)
        block_h = len(lines) * line_h
        cy = theme.caption_center(block_h, hook_bottom, cam_h, anchor_y)
        g_base = cycle[gi % len(cycle)] if cycle else base
        widest = max(text.line_width(ln, font, 0, spacing) for ln in lines)
        boxes.append({"start": round(g_start, 2), "end": round(g_end, 2), "lines": lines,
                      "box": [int(W / 2 - widest / 2), int(cy - block_h / 2),
                              int(W / 2 + widest / 2), int(cy + block_h / 2)]})

        fade_out = 110 if into_silence else 0
        intro = ""
        pop_blur = ""
        if anim == "pop":
            intro = f"\\fad(40,{fade_out})\\fscx128\\fscy128\\t(0,110,\\fscx100\\fscy100)"
            pop_blur = "\\blur6\\t(0,110,\\blur0)"
        elif soft:
            intro = f"\\fad(80,{fade_out})"
            if mode != "box":
                intro += "\\fscx96\\fscy96\\t(0,120,\\fscx100\\fscy100)"
        intro += italic
        t0, t1 = _timestamp(g_start), _timestamp(g_end)

        k = 0                                      # index of the word within the phrase
        for row, line in enumerate(lines):
            y = cy - block_h / 2 + line_h * (row + 0.5)
            width = text.line_width(line, font, 0, spacing)
            x = W / 2 - width / 2
            space = font.getlength(" ") + spacing
            parts = []
            for word in line:
                tok = group[k]
                on = max(0.0, tok["s"] - start - g_start)
                off = (group[k + 1]["s"] - start - g_start if k + 1 < len(group)
                       else min(tok["e"] - start + 0.15, g_end) - g_start)
                off = max(off, on + 0.08)
                a, b = int(on * 1000), int(off * 1000)
                word_w = text.measure(word, font, 0, spacing)
                if key_c and tok.get("key"):
                    parts.append(f"{{\\1c{key_c}}}{word}")
                elif mode == "none":
                    parts.append(f"{{\\1c{g_base}}}{word}" if cycle or key_c else word)
                elif mode == "box":
                    parts.append(f"{{\\1c{base}\\t({a},{a + 60},\\1c{on_box})"
                                 f"\\t({b},{b + 80},\\1c{base})}}{word}")
                    pad_x, pad_y = theme.px(8), theme.px(5)
                    shape = _box(x - pad_x, y - line_h / 2 + pad_y, x + word_w + pad_x,
                                 y + line_h / 2 - pad_y, theme.px(14))
                    s0 = _timestamp(g_start + on)
                    s1 = _timestamp(min(g_start + off + 0.06, g_end))
                    out.append(f"Dialogue: 1,{s0},{s1},Cap,,0,0,0,,{{\\an7\\pos(0,0)\\bord0"
                               f"\\shad0\\1c{hi}\\fad(60,70)\\p1}}{shape}{{\\p0}}")
                else:
                    parts.append(f"{{\\1c{g_base}\\t({a},{a + 70},\\1c{hi})"
                                 f"\\t({b},{b + 90},\\1c{g_base})}}{word}")
                x += word_w + space
                k += 1
            pos = f"\\an5\\pos({W / 2:.0f},{y:.0f})"
            if shadow:                             # a soft drop shadow on its own layer
                plain = " ".join(line)
                a_sh = _alpha(cap.get("shadow_opacity", 0.55))
                out.append(f"Dialogue: 0,{t0},{t1},Cap,,0,0,0,,{{\\an5"
                           f"\\pos({W / 2:.0f},{y + shadow:.0f}){intro}\\1c&H000000&"
                           f"\\3c&H000000&\\1a{a_sh}\\3a{a_sh}\\bord{outline}\\blur{blur}}}"
                           f"{plain}")
            out.append(f"Dialogue: 2,{t0},{t1},Cap,,0,0,0,,{{{pos}{intro}{pop_blur}}}"
                       + " ".join(parts))

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path, boxes


def beats(items, dur, path, cfg, theme, center_y):
    """Write an ASS file with the big emphasis words: each pops in tilted, holds, fades.
    `items` are [{"at": clip seconds, "text": "HAHA!"}]. Returns the path, or None."""
    bc = cfg["beats"]
    hold = float(bc.get("hold", 0.9))
    font = theme.font("beat")
    ass_name, ass_size = theme.ass_font("beat", path.parent)
    max_w = theme.W * 0.74          # room for the tilt and the 112 % overshoot of the pop
    lines = []
    for item in items:
        at = float(item.get("at", -1))
        word = _escape(str(item.get("text", "")).strip().upper())
        if not word or at < 0 or at >= dur:
            continue
        width = text.measure(word, font)
        scale = min(100, int(100 * max_w / max(1.0, width)))      # long words shrink to fit
        sh = theme.px(9)
        rot = float(bc.get("rotate", -6))
        tags = (f"\\an5\\pos({theme.W / 2:.0f},{center_y:.0f})\\frz{-rot:g}"
                f"\\i1\\1c{_color(bc.get('color', 'FFE14D'))}\\3c&H000000&\\4c&H000000&"
                f"\\bord{theme.px(7)}\\shad{sh}\\fad(0,140)"
                f"\\fscx{scale * 0.4:.0f}\\fscy{scale * 0.4:.0f}"
                f"\\t(0,90,\\fscx{scale * 1.12:.0f}\\fscy{scale * 1.12:.0f})"
                f"\\t(90,170,\\fscx{scale}\\fscy{scale})")
        lines.append(f"Dialogue: 5,{_timestamp(at)},{_timestamp(min(at + hold, dur))},Cap,,0,0,0,,"
                     f"{{{tags}}}{word}")
    if not lines:
        return None
    head = HEADER.format(w=theme.W, h=theme.H, font=ass_name, size=ass_size,
                         primary=_color("FFFFFF"), outline_c="&H000000&", spacing=0, outline=0)
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def zoom_filter(items, cfg, theme, label_in, label_out, focus_y=0.5):
    """A punch-in zoom on every beat (scale up for the hold, ease back), or None.
    `focus_y` is where the zoom centres, as a fraction of the height (the facecam)."""
    z = float(cfg["beats"].get("zoom", 0))
    hold = float(cfg["beats"].get("hold", 0.9))
    times = [float(i["at"]) for i in items if float(i.get("at", -1)) >= 0]
    if z <= 0 or not times:
        return None
    # the whole expression sits inside '...' in the filter graph, so its commas are safe
    bumps = "+".join(f"clip((t-{a:.3f})/0.08,0,1)*clip(({a + hold:.3f}-t)/0.3,0,1)"
                     for a in times)
    k = f"(1+{z:.3f}*min(1,{bumps}))"
    W, H = theme.W, theme.H
    return (f"[{label_in}]scale=w='trunc(iw*{k}/2)*2':h='trunc(ih*{k}/2)*2':eval=frame,"
            f"crop={W}:{H}:(iw-{W})/2:(ih-{H})*{focus_y:.3f},setsar=1[{label_out}]")
