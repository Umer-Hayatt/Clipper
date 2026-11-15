"""Pick the moments worth clipping, then write their text. Claude does both through
your own `claude` CLI, so it runs on your Claude subscription.

Two steps, so little is written that gets thrown away:
1. find():  Claude reads the transcript (marked where the speaker got loud and where
            chat went off) and returns only times, a kind, a score and one line of why.
            Long videos are read in windows sized by how much is said, cut at pauses,
            with a little of the neighbouring transcript as context. Each window's
            answer is cached on its own.
2. write(): one call for the clips that were kept: hook, caption, hashtags and, in the
            comments style, the cards. It follows editorial.md and sees each clip's
            exact final transcript.

Token use is kept low: ~8 s transcript lines, no tools, a short system prompt."""

import hashlib
import json
import math
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import config
from . import text as textlib
from .util import clean_env, log, read_json, timer, which, write_json

SYSTEM_PICK = ("You are an experienced short-form video editor who chooses which moments of "
               "a long video work as standalone clips for TikTok, Reels and YouTube Shorts. "
               "You answer with a JSON array only.")
SYSTEM_WRITE = ("You are an editor who writes the on-screen title and the post text for short "
                "video clips. You follow the editorial guide you are given exactly and you "
                "answer with JSON only.")

PICK_PROMPT = """This is part of a video by {streamer}, titled "{title}".

WHAT THIS CHANNEL / GAME IS ABOUT AND WHAT WINS:
{profile}

Below is a timestamped transcript (seconds). It may mix the speaker's voice with game, \
trailer or video audio, so judge who is talking from context. Markers on a line:
- [LOUD +NdB] the audio jumped N dB above the last two minutes (yelling, screaming, hype)
- [CHAT xN] chat was moving N times faster than usual (the audience reacted)
Markers are strong hints, not proof: a loud gunshot is not a reaction.

{scope}

Find up to {want} of the STRONGEST standalone clips. A clip works when it opens on the \
hook (never on "uh so yeah"), makes sense with zero context, and pays off before it ends. \
Reject: menus, loading, reading donations, sponsor reads, quiet stretches, and anything \
whose payoff you cannot see in the transcript below.

Rules:
- start and end are seconds, {min_d}-{max_d} s apart, no overlaps.
- Start ~1 s before the hook so it doesn't feel cut off. End right after the payoff lands.
- Fewer than {want} is fine if fewer are genuinely strong. [] if none.

Return ONLY a JSON array, no prose, no code fence:
[{{"start": 1312.5, "end": 1349.0, "kind": "reaction", "title": "short internal name",
  "score": 88, "reason": "one sentence: what happens and why it holds attention"}}]

kind is one of the categories above. score is 1-100 predicted retention; be harsh, \
85+ only for clips you'd bet on. Do not write viewer-facing titles, captions or \
hashtags here; that is done later for the clips that are kept.

TRANSCRIPT:
{transcript}
"""

WRITE_PROMPT = """These are {n} clip(s) cut from a video by {streamer}, titled "{title}". \
Write the text for each one.

EDITORIAL GUIDE (follow it exactly; it overrides your own habits):
{editorial}

ABOUT THIS CHANNEL / GAME (for names and hashtags):
{profile}

Settings for this run: emoji are {emoji}. At most {max_tags} hashtags per clip. \
The hook is at most {hook_words} words.

For each clip write:
- "hook": the title bar shown on the video
- "caption": the text under the post
- "hashtags": a list
{cards}
Base everything on what is actually said in that clip. "at" values are seconds in the \
video, inside the clip.

Return ONLY JSON, no prose, no code fence:
{example}

CLIPS:
{clips}
"""

CARDS_CHAT = """- "cards": pick the {n_min}-{n_max} REAL chat messages (listed under "chat") that \
make the best on-screen comment cards: funny, hyped, or reacting to exactly what happens, \
readable in 2 seconds. Skip spam, links, ads, @mentions, emote-only codes like ":yt:", \
personal info, slurs, harassment, sexual content and anything mean about a real person. \
Copy each message EXACTLY as given (do not fix or rewrite it). "at" is when the card should \
appear: the second the message reacts to (usually a few seconds before it was sent)."""

CARDS_REACT = """- "cards": there is no chat replay, so write {n_min}-{n_max} short reaction lines \
(see "Reaction lines" in the guide) to appear right after the moment each one reacts to. \
They are shown as on-screen reactions, NOT as viewer comments. Spread them over the clip."""


def _lines(words, loud, chat, cfg, line_seconds=8):
    """~8 s lines with [LOUD]/[CHAT] markers. Cheap in tokens, easy to read.
    Returns [{t0, t1, text, n}] (n = words on the line)."""
    lines, cur, start = [], [], None
    chat_base = float(np.median([c for c in chat if c > 0])) if chat and any(chat) else 0

    def flush(t0, t1, said):
        tags = []
        if len(loud):
            a, b = int(t0), max(int(t0) + 1, int(np.ceil(t1)))
            peak = float(np.max(loud[a:b])) if a < len(loud) else 0
            if peak >= cfg["loud_db"]:
                tags.append(f"[LOUD +{peak:.0f}dB]")
        if chat_base:
            a, b = int(t0 // 5), int(t1 // 5) + 2      # chat lags the moment a little
            seg = chat[a:b]
            if seg:
                ratio = max(seg) / chat_base
                if ratio >= 2.5:
                    tags.append(f"[CHAT x{ratio:.0f}]")
        lines.append({"t0": t0, "t1": t1, "n": len(said),
                      "text": (" ".join(tags) + " " if tags else "") + " ".join(said)})

    for w in words:
        if start is None:
            start = w["s"]
        cur.append(w["w"])
        if w["e"] - start >= line_seconds:
            flush(start, w["e"], cur)
            cur, start = [], None
    if cur:
        flush(start, words[-1]["e"], cur)
    return lines


def _windows(lines, cfg):
    """Cut the transcript into windows for Claude: [(lo, hi)] in seconds.
    Sized by words, not minutes (quiet stretches cost nothing), and each cut is placed
    at the longest pause near the target so it doesn't land inside a moment. A video
    that fits one window is not cut at all."""
    first, last = lines[0]["t0"], lines[-1]["t1"]
    if cfg.get("chunk_minutes"):                        # the old fixed-length behaviour
        step = float(cfg["chunk_minutes"]) * 60
        return [(first + i * step, min(first + (i + 1) * step, last))
                for i in range(int(math.ceil((last - first) / step)))]
    budget = max(500, int(cfg.get("pick_window_words") or 6000))
    total = sum(ln["n"] for ln in lines)
    if total <= budget * 1.2:
        return [(first, last)]
    count = int(math.ceil(total / budget))
    target = total / count
    cuts, acc, i = [], 0, 0
    while len(cuts) < count - 1 and i < len(lines) - 1:
        best, best_gap = None, -1.0
        while i < len(lines) - 1:
            acc += lines[i]["n"]
            if acc >= 0.85 * target:
                gap = lines[i + 1]["t0"] - lines[i]["t1"]
                if gap > best_gap:
                    best, best_gap = i, gap
            i += 1
            if acc >= 1.15 * target:
                break
        if best is None:
            break
        cuts.append(lines[best + 1]["t0"])
        # words already counted past the cut belong to the next window
        acc = sum(ln["n"] for ln in lines[best + 1:i])
    edges = [first] + cuts + [last]
    return [(edges[k], edges[k + 1]) for k in range(len(edges) - 1) if edges[k + 1] > edges[k]]


def _body(lines, lo, hi, overlap, whole, final):
    """(transcript text for one window, the prompt's scope sentence, words in the core)."""
    fmt = lambda ln: f"[{ln['t0']:.1f}] {ln['text']}"   # noqa: E731
    core = [ln for ln in lines if lo <= ln["t0"] < hi or (final and ln["t0"] >= hi)]
    n = sum(ln["n"] for ln in core)
    if whole:
        return "\n".join(fmt(ln) for ln in core), "This is the whole part to search.", n
    before = [ln for ln in lines if lo - overlap <= ln["t0"] < lo]
    after = [] if final else [ln for ln in lines if hi <= ln["t0"] < hi + overlap]
    out = []
    if before:
        out += ["=== CONTEXT BEFORE YOUR PART (do not start a clip here) ==="]
        out += [fmt(ln) for ln in before]
    out += [f"=== YOUR PART: {lo:.0f}-{hi:.0f} s ==="] + [fmt(ln) for ln in core]
    if after:
        out += ["=== CONTEXT AFTER YOUR PART (a clip may END here, not start) ==="]
        out += [fmt(ln) for ln in after]
    scope = (f"Your part of the video is {lo:.0f}-{hi:.0f} s. The lines marked CONTEXT are "
             "the neighbouring parts, shown so you can see how a moment begins or ends. "
             "Every clip must START inside your part; it may end in the context after it.")
    return "\n".join(out), scope, n


_LEAN = None   # whether this claude CLI accepts the token-saving flags


def _ask(prompt, cfg, purpose="pick", clock=None, system=SYSTEM_PICK):
    """One `claude -p` call. Tries the lean form first (no tools, short system prompt,
    no MCP servers), and falls back to a plain call on CLIs that don't know the flags."""
    global _LEAN
    exe = which("claude") or "claude"
    base = [exe, "-p", "--no-session-persistence"]
    if cfg.get("claude_model"):
        base += ["--model", cfg["claude_model"]]
    lean = base + ["--tools", "", "--strict-mcp-config", "--system-prompt", system]
    clock = clock or timer()
    for cmd in ([lean, base] if _LEAN is not False else [base]):
        t0 = time.time()
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900, env=clean_env())
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and out:
            if cmd is lean:
                _LEAN = True
            clock.call(purpose, time.time() - t0, len(prompt), len(out))
            return out
        err = (proc.stderr or proc.stdout or "").strip()
        if "login" in err.lower() or "logged in" in err.lower():
            raise RuntimeError("the claude command isn't logged in. Run `claude` in a terminal "
                               "once and sign in with /login, then run this again.")
        if cmd is lean and ("unknown option" in err.lower() or "error: option" in err.lower()):
            _LEAN = False
            continue
        raise RuntimeError(f"claude -p failed: {err[:300]}")
    raise RuntimeError("claude -p returned nothing")


def _parse(raw):
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("["):
        m = re.search(r"\[\s*\{.*\}\s*\]", text, re.S)
        if not m:
            return []
        text = m.group(0)
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        return []
    return out if isinstance(out, list) else []


def _parse_obj(raw):
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0) if m else text)
    except (json.JSONDecodeError, AttributeError):
        return {}
    return out if isinstance(out, dict) else {}


def _snap(m, words, cfg):
    """Move Claude's rough times onto real word edges; enforce the length limits."""
    start, end = float(m["start"]), float(m["end"])
    near = [w for w in words if w["s"] >= start - 1.5]
    if not near:
        return None
    first = min(near, key=lambda w: abs(w["s"] - start))
    inside = [w for w in words if first["s"] <= w["s"] and w["e"] <= end + 1.5]
    if not inside:
        return None
    last = min(inside, key=lambda w: abs(w["e"] - end))
    lo, hi = cfg["min_duration"], cfg["max_duration"]
    if last["e"] - first["s"] > hi:
        fit = [w for w in inside if w["e"] - first["s"] <= hi]
        if not fit:
            return None
        ends = [w for w in fit if w["w"].rstrip().endswith((".", "!", "?"))]
        last = ends[-1] if ends else fit[-1]
    if last["e"] - first["s"] < lo:
        after = [w for w in words if w["e"] > last["e"] and w["e"] - first["s"] <= hi]
        reach = [w for w in after if w["e"] - first["s"] >= lo]
        if reach:
            last = reach[0]
        elif after:
            last = after[-1]
        if last["e"] - first["s"] < lo:
            return None
    m = dict(m)
    m["start"] = round(max(0.0, first["s"] - 0.6), 2)
    m["end"] = round(min(last["e"] + 0.6, m["start"] + hi), 2)
    return m


def _dedupe(cands):
    kept = []
    for m in sorted(cands, key=lambda m: -float(m.get("score", 0) or 0)):
        span = m["end"] - m["start"]
        if all(min(m["end"], k["end"]) - max(m["start"], k["start"])
               <= 0.3 * min(span, k["end"] - k["start"]) for k in kept):
            kept.append(m)
    return kept


def _key(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def clear(workdir, picks=True):
    """Forget what Claude said for this video: the written text, and the picks too
    unless picks=False."""
    names = ["copy.json", "comments.json"]
    if picks:
        names += ["moments.json", "candidates.json"]
        if (workdir / "picks").exists():
            for f in (workdir / "picks").glob("*.json"):
                f.unlink(missing_ok=True)
    for name in names:
        (workdir / name).unlink(missing_ok=True)


def find(scan, loud_rel, chat, workdir, cfg, stream, profile):
    """The best moments of this video: [{start, end, kind, title, score, reason}].
    Every window's answer is cached in <workdir>/picks/, so a stopped run resumes, a
    failed window is asked again next time, and asking for a different number of clips
    costs no new Claude call."""
    picks_dir = workdir / "picks"
    words = scan["words"]
    want_clips = int(cfg["clips_per_stream"])

    # A cache from before the per-window picks: use it as the pool, as before.
    if not picks_dir.exists():
        for old in ("candidates.json", "moments.json"):
            if (workdir / old).exists():
                pool = read_json(workdir / old)
                timer().cache("moments (earlier run)", True)
                chosen = _dedupe(pool)[:want_clips]
                log(f"reusing {len(chosen)} picked moment(s) from an earlier run", "ok")
                return chosen

    lines = _lines(words, loud_rel, chat or [], cfg)
    if not lines:
        raise RuntimeError("not enough talking in this video to clip")
    windows = _windows(lines, cfg)
    overlap = cfg.get("pick_overlap")
    overlap = float(overlap) if overlap is not None else cfg["max_duration"] + 30.0
    whole = len(windows) == 1
    jobs = []
    for k, (lo, hi) in enumerate(windows):
        body, scope, n = _body(lines, lo, hi, overlap, whole, k == len(windows) - 1)
        if n < 30:
            continue
        want = max(3, min(8, round((hi - lo) / 240)))
        if whole:
            want = max(want, min(want_clips, 10))
        prompt = PICK_PROMPT.format(
            streamer=stream["name"], title=stream["title"], profile=profile, want=want,
            scope=scope, min_d=cfg["min_duration"], max_d=cfg["max_duration"], transcript=body)
        jobs.append((lo, hi, prompt, picks_dir / f"{_key(prompt, cfg.get('claude_model'))}.json"))
    if not jobs:
        raise RuntimeError("not enough talking in this video to clip")
    picks_dir.mkdir(exist_ok=True)

    clock = timer()                      # worker threads report to this video's timer
    todo = [j for j in jobs if not j[3].exists()]
    for j in jobs:
        clock.cache(f"picks {j[0] / 60:.0f}-{j[1] / 60:.0f} min", j[3].exists())
    if todo:
        log(f"Claude is reading {len(todo)} part(s) of {len(jobs)}"
            + (f", {cfg['claude_parallel']} at a time" if len(todo) > 1 else "") + "...", "step")
    else:
        log(f"reusing Claude's picks for all {len(jobs)} part(s)", "ok")

    def work(job):
        lo, hi, prompt, cache = job
        if cache.exists():
            return read_json(cache)["picks"]
        try:
            got = _parse(_ask(prompt, cfg, "pick", clock, SYSTEM_PICK))
        except subprocess.TimeoutExpired:
            log(f"  part {lo / 60:.0f}-{hi / 60:.0f} min: Claude timed out "
                "(it will be asked again next run)", "warn")
            return None
        write_json(cache, {"lo": lo, "hi": hi, "picks": got})
        return got

    cands, failed = [], 0
    with ThreadPoolExecutor(max_workers=max(1, int(cfg["claude_parallel"]))) as pool:
        for (lo, hi, _, _), got in zip(jobs, pool.map(work, jobs)):
            if got is None:
                failed += 1
                continue
            for m in got:
                try:
                    if lo - 2 <= float(m["start"]) < hi + 2:
                        s = _snap(m, words, cfg)
                        if s:
                            cands.append(s)
                except (KeyError, TypeError, ValueError):
                    continue
    if not cands:
        if failed:
            raise RuntimeError("Claude timed out on every part; run the same command again")
        raise RuntimeError("Claude found nothing strong enough to clip in this video")

    pool = _dedupe(cands)
    chosen = pool[:want_clips]
    log(f"picked {len(chosen)} of {len(cands)} candidate moments"
        + (f" ({failed} part(s) still to read)" if failed else ""), "ok")
    write_json(workdir / "moments.json", chosen)
    write_json(workdir / "candidates.json", cands)
    return chosen


# ---------------------------------------------------------------- writing the text

_EMOTE = re.compile(r":[A-Za-z0-9_\-]+:")


def banned_phrases(editorial):
    """The bullet list under '## Never use' in the editorial guide, lower-cased."""
    m = re.search(r"^##\s*Never use\s*$(.*?)(?=^##\s|\Z)", editorial, re.S | re.M | re.I)
    if not m:
        return []
    return [ln.strip()[1:].strip().lower() for ln in m.group(1).splitlines()
            if ln.strip().startswith("-") and ln.strip()[1:].strip()]


def _calm(line):
    """Lower words written in capitals for emphasis (5+ letters; GTA, NPC, LSPD stay)."""
    out, changed = [], False
    for word in line.split():
        core = re.sub(r"[^A-Za-z]", "", word)
        if len(core) >= 5 and core.isupper():
            word, changed = word.lower(), True
        out.append(word)
    return " ".join(out), changed


def polish(item, cfg, banned):
    """Local checks on one clip's text, no Claude call. Fixes what is safe to fix
    (emoji, shouting, filler hashtags, too many hashtags) and returns notes for the rest."""
    w = cfg.get("writing", {})
    notes = []
    for field in ("hook", "caption"):
        value = " ".join(str(item.get(field) or "").split())
        if not w.get("emoji", False) and textlib.has_emoji(value):
            value = textlib.strip_emoji(value)
            notes.append(f"{field}: emoji removed")
        value, changed = _calm(value)
        if changed:
            notes.append(f"{field}: capital-letter emphasis lowered")
        low = value.lower()
        hits = [b for b in banned if not b.startswith("#") and b in low]
        if hits:
            notes.append(f"{field}: contains '{hits[0]}' (listed under Never use)")
        item[field] = value.strip()
    hook = item["hook"].rstrip(".")
    limit = int(w.get("hook_max_words", 11))
    count = len(hook.replace("*", "").split())
    if count > limit + 3:
        notes.append(f"hook: {count} words (the guide asks for at most {limit})")
    item["hook"] = hook
    tags, seen = [], set()
    for tag in item.get("hashtags") or []:
        tag = "#" + re.sub(r"[^\w]", "", str(tag), flags=re.UNICODE)
        if len(tag) < 3 or tag.lower() in seen:
            continue
        if tag.lower() in banned:
            notes.append(f"hashtag {tag} removed (listed under Never use)")
            continue
        seen.add(tag.lower())
        tags.append(tag)
    cap = int(w.get("max_hashtags", 4))
    if len(tags) > cap:
        notes.append(f"hashtags cut from {len(tags)} to {cap}")
    item["hashtags"] = tags[:cap]
    return item, notes


def _said(words, a, b, limit=320, line_seconds=5):
    """What is said in [a, b) as short timestamped lines, so cards can be timed."""
    out, cur, t0, n = [], [], None, 0
    for w in words:
        if not a <= w["s"] < b:
            continue
        if t0 is None:
            t0 = w["s"]
        cur.append(w["w"])
        n += 1
        if w["e"] - t0 >= line_seconds:
            out.append(f"  [{t0:.0f}] {' '.join(cur)}")
            cur, t0 = [], None
        if n >= limit:
            break
    if cur:
        out.append(f"  [{t0:.0f}] {' '.join(cur)}")
    return "\n".join(out)


def _chat_pool(messages, a, b, cap):
    """Chat messages for one clip, cleaned for Claude: [(second, text)]."""
    pool = []
    for t, txt in messages:
        txt = " ".join(_EMOTE.sub(" ", txt).split())            # channel emote codes
        # chat lags what it reacts to, so look a little past the end
        if a - 2 <= t <= b + 12 and txt and "http" not in txt and len(txt) <= 140:
            pool.append((t, txt))
    seen, uniq = set(), []
    for t, txt in pool:
        if txt.lower() not in seen:
            seen.add(txt.lower())
            uniq.append((t, txt))
    if len(uniq) > cap:                                          # keep it cheap: an even sample
        uniq = [uniq[int(k * len(uniq) / cap)] for k in range(cap)]
    return uniq


def _beats(raw, m, words, cfg):
    """Claude's peak moments -> [{"at": clip seconds, "text"}], each moved onto the start
    of the matching spoken word when one is said within 2.5 s."""
    out = []
    if not isinstance(raw, list):
        return out
    for b in raw[: int(cfg.get("beats", {}).get("max", 2))]:
        try:
            at, txt = float(b["at"]), str(b["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not txt or not m["start"] <= at <= m["end"]:
            continue
        want = re.sub(r"[^a-z0-9']", "", txt.split()[0].lower())
        near = [w for w in words if abs(w["s"] - at) <= 2.5
                and re.sub(r"[^a-z0-9']", "", w["w"].lower()) == want]
        if near:
            at = min(near, key=lambda w: abs(w["s"] - at))["s"]
        out.append({"at": round(at - m["start"], 2), "text": " ".join(txt.split()[:2])})
    return out


def write(chosen, messages, words, workdir, cfg, stream, profile, editorial):
    """Hook, caption and hashtags for every chosen clip and, in the comments style, its
    cards: one Claude call, cached in <workdir>/copy.json. The cache is keyed on the
    clips, the style and the editorial guide, so editing the guide rewrites the text
    without picking the moments again. Fills m["hook_text"], m["hook_markup"],
    m["caption"], m["hashtags"], m["comments"] and m["writing_notes"]."""
    w = cfg.get("writing", {})
    with_cards = cfg["style"] == "comments"
    kind = "chat" if messages else "reaction"
    blocks, pools = [], {}
    for i, m in enumerate(chosen, 1):
        a, b = m["start"], m["end"]
        block = [f"CLIP {i}: {a:.0f}-{b:.0f} s · kind: {m.get('kind', '')} · "
                 f"why it was picked: {m.get('reason', '')}",
                 "said:\n" + _said(words, a, b)]
        if with_cards and messages:
            uniq = _chat_pool(messages, a, b, int(cfg["comment_pool"]))
            pools[str(i)] = {txt for _, txt in uniq}
            block.append("chat:\n" + ("\n".join(f"  [{t:.0f}] {txt}" for t, txt in uniq)
                                      or "  (no messages)"))
        blocks.append("\n".join(block))
    cards = ""
    example = '{"1": {"hook": "...", "caption": "...", "hashtags": ["#tag"]}, "2": {...}}'
    if with_cards:
        cards = (CARDS_CHAT if messages else CARDS_REACT).format(
            n_min=cfg["comments_min"], n_max=cfg["comments_max"])
        example = ('{"1": {"hook": "...", "caption": "...", "hashtags": ["#tag"], '
                   '"cards": [{"at": 1320.5, "text": "..."}]}, "2": {...}}')
    needs = config.text_needs(cfg)
    extra_fields = {}
    if "keywords" in needs:
        cards += ('\n- "keywords": 2-6 words or short names SAID in the clip that carry its meaning '
                  '(names, game titles, the key noun or verb). Copy them exactly as spoken. They '
                  'are coloured in the on-screen captions.')
        extra_fields["keywords"] = ["..."]
    if "beats" in needs:
        cards += (f'\n- "beats": 0-{int(cfg["beats"].get("max", 2))} PEAK moments only (the big '
                  'laugh, the scream, the punchline). Each is {"at": seconds in the video, '
                  '"text": 1-2 words flashed huge on screen}: a word said right then, or a '
                  'reaction like "HAHA!" or "NO WAY!". [] when nothing peaks.')
        extra_fields["beats"] = [{"at": 0, "text": "..."}]
    if "captions" in needs:
        cards += ('\n- "captions": true or false. false when on-screen words would add nothing '
                  '(mostly action, screaming or music); true when what is said matters.')
        extra_fields["captions"] = True
    if extra_fields:
        tail = json.dumps(extra_fields)[1:-1]
        example = example.replace('["#tag"]', '["#tag"], ' + tail, 1)
    prompt = WRITE_PROMPT.format(
        n=len(chosen), streamer=stream["name"], title=stream["title"], editorial=editorial,
        profile=profile, emoji="allowed, sparingly" if w.get("emoji") else "NOT allowed",
        max_tags=w.get("max_hashtags", 4), hook_words=w.get("hook_max_words", 11),
        cards=cards, example=example, clips="\n\n".join(blocks))
    key = _key(prompt, cfg.get("claude_model"), json.dumps(w, sort_keys=True))

    cache = workdir / "copy.json"
    got = None
    if cache.exists():
        saved = read_json(cache)
        if saved.get("key") == key:
            got = saved["clips"]
    timer().cache("clip text", got is not None)
    if got is None:
        log("Claude is writing the hooks and captions"
            + (" and picking chat messages" if with_cards and messages
               else " and reaction lines" if with_cards else "") + "...", "step")
        try:
            raw = _parse_obj(_ask(prompt, cfg, "write", None, SYSTEM_WRITE))
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            log(f"text not written ({str(exc)[:120]}); using plain titles", "warn")
            raw = {}
        banned = banned_phrases(editorial)
        got = {}
        for i, m in enumerate(chosen, 1):
            item = raw.get(str(i)) if isinstance(raw.get(str(i)), dict) else {}
            fallback = not item.get("hook")
            item = {"hook": item.get("hook") or m.get("hook_text") or m.get("title") or "",
                    "caption": item.get("caption") or m.get("caption") or "",
                    "hashtags": item.get("hashtags") or m.get("hashtags") or [],
                    "cards": item.get("cards") or [],
                    "keywords": [str(k) for k in item.get("keywords") or [] if str(k).strip()][:6],
                    "beats": _beats(item.get("beats"), m, words, cfg),
                    "captions": item.get("captions", True) is not False}
            item, notes = polish(item, cfg, banned)
            if fallback:
                notes.insert(0, "Claude returned no text for this clip; fallback title used")
            keep = []
            for c in item["cards"] if with_cards else []:
                try:
                    at, line = float(c["at"]), str(c["text"]).strip()
                except (KeyError, TypeError, ValueError):
                    continue
                if not line:
                    continue
                if messages and line not in pools.get(str(i), ()):
                    continue        # real chat only: drop anything Claude reworded
                if not messages:
                    if not w.get("emoji", False):
                        line = textlib.strip_emoji(line)
                    line, _ = _calm(line)
                at = min(max(at, m["start"]), m["end"])
                keep.append({"at": round(at - m["start"], 2), "text": line, "kind": kind})
            item["cards"] = keep[: int(cfg["comments_max"])]
            item["notes"] = notes
            got[str(i)] = item
        if raw:                         # never cache a failed call
            write_json(cache, {"key": key, "clips": got})

    n_cards = 0
    for i, m in enumerate(chosen, 1):
        item = got.get(str(i)) or {}
        markup = item.get("hook") or m.get("title") or ""
        m["hook_markup"] = markup
        m["hook_text"] = " ".join(markup.replace("*", "").split())
        m["caption"] = item.get("caption", "")
        m["hashtags"] = item.get("hashtags", [])
        m["writing_notes"] = item.get("notes", [])
        m["comments"] = item.get("cards", []) if with_cards else []
        m["keywords"] = item.get("keywords", [])
        m["beats"] = item.get("beats", [])
        m["captions_on"] = item.get("captions", True)
        n_cards += len(m["comments"])
        for note in m["writing_notes"]:
            log(f"  clip {i} text check: {note}", "info")
    if with_cards:
        log(f"{n_cards} comment card(s) for {len(chosen)} clip(s)", "ok" if n_cards else "warn")
    return chosen
