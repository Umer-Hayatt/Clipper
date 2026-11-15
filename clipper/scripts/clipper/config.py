"""Settings. Defaults below; override any of them in <clips folder>/config.json.

Nested groups (typography, captions, hook, cards, safe_area, writing) are merged key by
key, so a config.json only needs the values it changes, for example:

    {"captions": {"highlight_color": "7CF5C4", "case": "upper"},
     "typography": {"caption": {"size": 72}}}

Sizes are in pixels on a 1080-wide frame and scale with output_width."""

import copy
import hashlib
import json
import os
import sys
from pathlib import Path

# Where clips and the work cache go. Set CLIPPER_HOME to put them on another drive.
HOME = Path(os.environ.get("CLIPPER_HOME") or (Path.home() / "clips"))
# Private Python env + models (made by setup.py).
ENV_DIR = Path.home() / ".clipper"
SKILL_DIR = Path(__file__).resolve().parents[2]
PROFILES = SKILL_DIR / "profiles"
FONTS = SKILL_DIR / "fonts"
EDITORIAL = SKILL_DIR / "editorial.md"

if sys.platform == "win32":
    _SYS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    FALLBACK_FONT = str(_SYS / "arialbd.ttf")
    EMOJI_FONT = str(_SYS / "seguiemj.ttf")
elif sys.platform == "darwin":
    FALLBACK_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    EMOJI_FONT = "/System/Library/Fonts/Apple Color Emoji.ttc"
else:
    FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

DEFAULTS = {
    # --- download ---
    "max_height": 1080,

    # --- listening ---
    "scan_model": "tiny",            # fast pass over the whole video (what Claude reads)
    "caption_model": "base",         # careful pass over each chosen clip (the captions)
    "caption_beam": 5,               # Whisper beam for caption words (1 = faster, a bit rougher)
    "language": None,                # None = detect, or "en"
    "scan_block_minutes": 20,        # audio is decoded in blocks, so 10-hour streams fit in memory
    "loud_db": 6.0,                  # louder than the last 2 minutes by this much = a spike
    "chat": True,                    # YouTube replays: count chat messages per 5 s
    "chat_timeout": 420,
    "platform_captions": True,       # use YouTube's own captions when a video has them

    # --- picking moments ---
    "profile": "general",            # profiles/<name>.md: what a great moment is
    "clips_per_stream": 3,
    "min_duration": 15,
    "max_duration": 60,
    "pick_window_words": 6000,       # transcript words per Claude call (a shorter video = 1 call)
    "pick_overlap": None,            # seconds of neighbouring transcript shown as context
                                     # (None = max_duration + 30)
    "chunk_minutes": None,           # old fixed window in minutes; set it to force that behaviour
    "claude_parallel": 4,            # Claude calls at the same time (more = faster, more usage)
    "claude_model": None,            # None = your Claude Code default, or "sonnet" / "opus"
    "editorial_file": None,          # None = <clips>/editorial.md if present, else the skill's

    # --- writing (hook, caption, hashtags); the voice itself lives in editorial.md ---
    "writing": {
        "emoji": False,              # emoji in hooks, captions and reaction lines
        "max_hashtags": 4,
        "hook_max_words": 11,
    },

    # --- framing ---
    "layout": "auto",                # auto | stream | face | fit
    "output_width": 1080,
    "output_height": 1920,
    "output_fps": 30,                # cap on frames per second ("source" keeps the video's own)
    "cam_height": 760,               # stream layout: height of the facecam panel on top
    "cam_headroom": 0.45,            # stream layout: space kept above the face (in face heights)
    "fit_aspect": 1.7778,            # fit layout: 1.7778 keeps the whole 16:9 frame
    "fit_dim": 0.16,                 # fit layout: how much the blurred background is darkened
    "track_smoothing": 5,
    "safe_area": {"top": 0.06, "bottom": 0.16, "side": 0.06},   # kept clear of the apps' buttons

    # --- typography: one family, a weight and size per role ---
    "typography": {
        "fonts_dir": str(FONTS),     # <Family>-<Weight>.ttf files (static weights, not variable)
        "family": "Inter",
        "emoji_font_file": EMOJI_FONT,
        "caption": {"weight": "ExtraBold", "size": 66, "line_spacing": 1.16, "letter_spacing": 0},
        "hook": {"weight": "Bold", "size": 54, "min_size": 42, "line_spacing": 1.24,
                 "max_lines": 2},
        "card": {"weight": "Medium", "size": 40, "line_spacing": 1.28},
        "card_label": {"weight": "SemiBold", "size": 27},
        "sticker": {"weight": "Bold", "size": 46, "line_spacing": 1.2},
        "beat": {"weight": "Black", "size": 150},     # the big emphasis word
        # any role also takes "file": "C:/path/font.ttf" to use one specific font file
    },

    # --- captions ---
    "caption_languages": ["en"],     # only caption these languages ([] = caption everything)
    "caption_preset": None,          # clean | bold | boxed (see PRESETS); your own keys win
    "captions": {
        "case": "sentence",          # sentence | upper | as-is
        "max_lines": 2,
        "max_words": 5,              # words on screen at once
        "max_width": 0.80,           # of the frame width
        "position_y": 0.70,          # centre of the caption block, fraction of the height
        "text_color": "FFFFFF",
        "highlight_color": "FFD84D", # the word being spoken
        "highlight_mode": "color",   # color | box | none
        "highlight_text_color": "111111",   # box mode: the text on the box
        "outline": 3,
        "outline_color": "000000",
        "shadow": 3,                 # drop distance; 0 = no shadow
        "shadow_blur": 5,
        "shadow_opacity": 0.55,
        "animation": "soft",         # soft (short fade + settle per phrase) | none
        "punctuation": "minimal",    # minimal (keep ? ! ' only) | all | none
        "show": True,                # True | False | "auto" (Claude decides per clip)
        "chunk": "phrase",           # phrase (a few words) | word (one word, or max_words, at a time)
        "anchor": "fixed",           # fixed (position_y) | seam (on the cam/gameplay seam)
        "italic": False,
        "color_cycle": [],           # e.g. ["FFFFFF", "5CE1FF"]: each phrase takes the next colour
        "keyword_color": None,       # e.g. "8CFF3A": words Claude marks as key stay this colour
        "mask_profanity": False,     # "fuck" -> "F***" on screen (audio untouched)
    },

    # --- hook (the title bar) ---
    "hook": {
        "position_y": 0.09,          # top edge, fraction of the height (fit and face layouts)
        "margin_x": 110,             # space left and right of the bar
        "background": "FFFFFF",
        "background_opacity": 1.0,
        "text_color": "111111",
        "accent_color": "E0362F",    # one phrase Claude may mark with *stars*
        "radius": 0.32,              # of the font size
        "shadow": True,
        "animation": "soft",         # soft (fade + short slide in) | none
        "style": "bar",              # bar (white plate) | pill (coloured plate) | text (no plate)
        "pill_color": "E8343A",
        "placement": "auto",         # auto (on the seam in the stream layout) | top
    },

    # --- style ---
    "style": "auto",                 # auto (comments when there's a chat replay) | comments | captions
    "comment_hold": 4.0,             # longest a card stays (short lines leave sooner)
    "comments_min": 3,
    "comments_max": 6,               # cards per clip
    "comment_pool": 150,             # most chat messages shown to Claude per clip
    "comment_captions": False,       # word captions too in the comments style
    "comment_fit_aspect": 1.5,       # comments style: gameplay band (1.5 = 720 px tall)
    "comment_zones": None,           # card centres as fractions of the height; None = worked
                                     # out from the layout (hook bar, gameplay band, safe area)
    "cards": {
        "theme": "light",            # light | dark chat cards
        "max_width": 0.80,           # of the frame width
        "opacity": 0.96,
        "radius": 0.62,              # of the font size
        "heart": True,               # the small heart on chat cards (never a count)
        "sticker_opacity": 0.84,     # reaction stickers: dark plate
        "sticker_edge": True,        # thin light edge so stickers read on dark footage
        "animation": "soft",         # soft (fade + settle) | none
    },

    # --- emphasis beats: a big word flashed on the peak moment, with a punch-in zoom ---
    "beats": {
        "enabled": False,
        "max": 2,                    # per clip (Claude picks 0..max real peaks)
        "zoom": 0.12,                # punch-in amount (0 = no zoom)
        "hold": 0.9,                 # seconds the word stays
        "color": "FFE14D",           # size and font: typography.beat
        "rotate": -6,                # degrees
    },

    # --- sound effects and music (HANDOFF.md "Sound rules"; library: scripts/sound_library.py) ---
    "sfx": {
        "enabled": False,
        "volume": 0.45,              # 0-1 over the clip's own sound: about +10 dB vs the clip's median, like the refs
        "on_beat": "auto",           # auto = a library stinger that fits the clip's kind; or a sound's name;
                                     # thud | boom | whoosh | pop are made-up fallbacks; None = no stinger
        "per_clip": 1,               # stingers per clip (the references use 0-1): the latest beat(s) with a pause
        "pause_reach": 1.5,          # play it in the next pause within this many s (0 = exactly on the beat)
        "no_pause": "skip",          # talking all the way: skip (like the references) | keep (on the beat)
        "on_hook": None,             # sound when the title appears (e.g. "whoosh"); no reference video does this
        "library": None,             # sound library folder; None = ~/.clipper/sounds
        "confirmed_only": False,     # True = only sounds you have listened to and confirmed
    },
    "music": {
        "enabled": False,            # the references use music for compilations, not single moments
        "sound": "auto",             # auto = a library bed that fits the clip's kind; or a bed's name
        "volume": 0.6,               # 0-1 before ducking; the bed sits at -20 dBFS RMS in the library
        "library": None,
        "confirmed_only": False,
    },
    "mix_presets": ["clean", "seam", "punch", "keyword", "serif", "none"],

    # --- encoding ---
    "encoder": "libx264",            # --fast picks a hardware encoder (NVIDIA/Intel/AMD/Mac)
    "crf": 20,
    "preset": "medium",
    "audio_bitrate": "192k",
    "loudnorm": True,
}

_WINFONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# Ready-made caption looks. "caption_preset": "bold" applies one; keys you set yourself win.
# "mix" gives each clip one look from mix_presets (stable per clip), "none" means no captions.
PRESETS = {
    "clean": {},
    "bold": {"captions": {"case": "upper", "max_words": 4, "outline": 5},
             "typography": {"caption": {"weight": "Black", "size": 74}}},
    "boxed": {"captions": {"highlight_mode": "box", "highlight_color": "FFD84D", "outline": 0,
                           "shadow": 4, "shadow_opacity": 0.7}},
    # small capitals sitting on the cam/gameplay seam, no word highlight
    "seam": {"captions": {"anchor": "seam", "case": "upper", "max_words": 3, "max_lines": 1,
                          "highlight_mode": "none", "outline": 4, "shadow": 0},
             "typography": {"caption": {"weight": "Black", "size": 56}},
             "hook": {"placement": "top"}},
    # one word at a time, italic, each word a new colour, pops in
    "punch": {"captions": {"anchor": "seam", "chunk": "word", "max_words": 1, "case": "upper",
                           "italic": True, "highlight_mode": "none", "animation": "pop",
                           "color_cycle": ["FFFFFF", "5CE1FF", "FFE14D", "7CFF6B"],
                           "outline": 4, "shadow": 5, "shadow_blur": 0, "shadow_opacity": 0.9},
              "typography": {"caption": {"weight": "Black", "size": 72}},
              "hook": {"placement": "top"}},
    # tall condensed capitals, the key words in green (needs Impact, a Windows font)
    "keyword": {"captions": {"anchor": "seam", "case": "upper", "max_words": 4, "max_lines": 1,
                             "highlight_mode": "none", "keyword_color": "8CFF3A",
                             "outline": 4, "shadow": 0},
                "typography": {"caption": {"file": str(_WINFONTS / "impact.ttf"), "size": 78}},
                "hook": {"placement": "top"}},
    # one big serif word at a time with a heavy outline
    "serif": {"captions": {"anchor": "seam", "chunk": "word", "max_words": 1, "case": "upper",
                           "highlight_mode": "none", "animation": "pop", "outline": 5,
                           "shadow": 3, "shadow_blur": 2},
              "typography": {"caption": {"file": str(_WINFONTS / "georgiab.ttf"), "size": 74}},
              "hook": {"placement": "top"}},
    "none": {"captions": {"show": False}},
}

LOOK_GROUPS = ("captions", "typography", "hook")


def for_clip(cfg, key):
    """The settings for one clip. With caption_preset "mix" each clip gets one preset from
    mix_presets, chosen from `key` (the clip's start time) so a re-render keeps its look.
    The user's own captions/typography/hook keys still win. Returns (cfg, look name)."""
    if cfg.get("caption_preset") != "mix":
        return cfg, cfg.get("caption_preset") or "clean"
    names = [n for n in cfg.get("mix_presets") or [] if n in PRESETS] or ["clean"]
    name = names[int(hashlib.md5(str(key).encode()).hexdigest(), 16) % len(names)]
    out = copy.deepcopy(cfg)
    _merge(out, copy.deepcopy(PRESETS[name]))
    user = cfg.get("_user") or {}
    _merge(out, copy.deepcopy({k: v for k, v in user.items() if k in LOOK_GROUPS}))
    return out, name


def looks(cfg):
    """Every caption preset this run can use (one, or the mix list)."""
    if cfg.get("caption_preset") == "mix":
        return [n for n in cfg.get("mix_presets") or [] if n in PRESETS]
    return [cfg.get("caption_preset") or "clean"]


def text_needs(cfg):
    """Which extra fields Claude must write per clip: keywords, beats, captions on/off."""
    need = set()
    for name in looks(cfg):
        cap = copy.deepcopy(cfg["captions"])
        if cfg.get("caption_preset") == "mix":
            _merge(cap, copy.deepcopy(PRESETS[name].get("captions", {})))
            _merge(cap, copy.deepcopy((cfg.get("_user") or {}).get("captions", {})))
        if cap.get("keyword_color"):
            need.add("keywords")
        if cap.get("show") == "auto":
            need.add("captions")
    if cfg.get("beats", {}).get("enabled"):
        need.add("beats")
    return need

# Flat keys from before the theme system -> where they live now. Old config.json files
# keep working.
_LEGACY = {
    "font_size": ("typography", "caption", "size"),
    "text_color": ("captions", "text_color"),
    "highlight_color": ("captions", "highlight_color"),
    "outline_width": ("captions", "outline"),
    "caption_y": ("captions", "position_y"),
    "words_per_group": ("captions", "max_words"),
    "hook_font_size": ("typography", "hook", "size"),
    "hook_font_file": ("typography", "hook", "file"),
    "emoji_font_file": ("typography", "emoji_font_file"),
    "hook_y": ("hook", "position_y"),
    "hook_margin_x": ("hook", "margin_x"),
    "comment_theme": ("cards", "theme"),
    "comment_font_size": ("typography", "card", "size"),
    "comment_font_file": ("typography", "card", "file"),
    "comment_bold_font_file": ("typography", "card_label", "file"),
}


def _merge(base, extra):
    """Merge `extra` into `base` in place; dicts are merged key by key."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _modernise(user):
    """Move old flat keys in a user's settings to their new nested place."""
    out = {}
    for key, value in user.items():
        if key in _LEGACY:
            node = out
            path = _LEGACY[key]
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node.setdefault(path[-1], value)
        elif key == "uppercase":
            out.setdefault("captions", {}).setdefault("case", "upper" if value else "as-is")
        elif key == "word_pop":
            out.setdefault("captions", {}).setdefault("animation", "soft" if value else "none")
        elif key == "comment_width":
            out.setdefault("cards", {}).setdefault("max_width", round(float(value) / 1080, 3))
        elif key == "font":
            pass          # a system font name can't be measured; set typography.caption.file
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(overrides=None):
    cfg = copy.deepcopy(DEFAULTS)
    user = {}
    path = HOME / "config.json"
    if path.exists():
        try:
            user = _modernise(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path} is not valid JSON: {exc}")
    preset = user.get("caption_preset")
    if preset and preset != "mix":
        if preset not in PRESETS:
            raise SystemExit(f"no caption preset '{preset}'. Known: {', '.join(PRESETS)}")
        _merge(cfg, copy.deepcopy(PRESETS[preset]))
    _merge(cfg, user)
    for key, value in (overrides or {}).items():
        if value is not None:
            cfg[key] = value
    cfg["_user"] = user
    return cfg


def profile_text(name):
    """The profile file's text (what a great moment is for this game/topic)."""
    path = Path(name)
    if not path.suffix:
        path = PROFILES / f"{name}.md"
    if not path.exists():
        known = ", ".join(sorted(p.stem for p in PROFILES.glob("*.md")))
        raise SystemExit(f"no profile '{name}'. Known: {known}")
    return path.read_text(encoding="utf-8").strip()


def editorial_text(cfg):
    """The writing guide: cfg['editorial_file'], else <clips>/editorial.md, else the skill's."""
    for path in (cfg.get("editorial_file"), HOME / "editorial.md", EDITORIAL):
        if path and Path(path).exists():
            return Path(path).read_text(encoding="utf-8").strip()
    return ""
