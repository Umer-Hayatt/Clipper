# Clipper: caption and visual quality overhaul (plan)

Status: **plan only, no code written yet** (2026-11-14). This is the working plan for the
overhaul brief. It is based on reading the current code and the existing test outputs,
not on new test runs. Anything marked *to measure* is a hypothesis until stage 1 is done.

Hard limits for the whole job: no rebuild, same architecture, Windows-first, `claude -p`
on the subscription only, no new Python dependencies, light enough for the i7-7500U / 8 GB
machine. Out of scope: memes, sound effects, reaction images, live-replay testing,
auto-posting, a dashboard, paid APIs.

The plan has two parts:
- **Part A (sections 1–6):** the quality and speed overhaul of what already exists.
- **Part B (section 7):** the remaining build items, added on 2026-11-14 at the user's
  request: facecam crop fix, session mode, montage, explainer mode, gameplay mode, and
  the open questions. Part B is built **after** Part A, on top of the new theme system,
  except the facecam fix, which is folded into stage 4.

---

## 1. What I found in the current code

### 1.1 Captions (`captions.py`)
- **The active word is scaled to 112 % inline.** In ASS that changes the word's width, so
  every other word on the line shifts each time the highlight moves. This is the main
  reason the captions feel jumpy.
- **Line breaks are left to libass** (`WrapStyle: 0`). At 82 px Arial Black in capitals,
  four words often don't fit in the 940 px text width, so the break lands wherever libass
  puts it and can change between words of the same phrase.
- **Grouping is a fixed word count** (4 words, or sentence end, or a 0.55 s gap). It knows
  nothing about pixel width, so long words overflow and short ones leave two-word orphans
  (the existing `.ass` shows "ENJOYING THE" on its own for 1.5 s).
- **Punctuation is shown as it arrives**: trailing commas and full stops stay on screen,
  and YouTube auto-captions have none at all, so phrases never break on sentences there.
- **Typography is hard-coded**: Arial Black, 6 px black outline, shadow 3, bold flag on.
  Only six flat config keys exist. The font is a system font, so the look changes per OS.
- **Sizes are absolute pixels**, so a different `output_width` gives a different look.
- **No safe-area logic.** `caption_y` is one fraction; nothing checks the hook bar, the
  facecam seam or the cards.

### 1.2 Hook bar (`hook.py`)
- Greedy wrap: lines can end up very uneven, with a single word on the last line (visible
  in the workshop test clip: "difference?" alone on line 3).
- Fixed 54 px size whatever the title length; no limit on the number of lines.
- Static: appears on frame 1 with no entrance.
- Emoji are always drawn, and the picking prompt asks for them.

### 1.3 Comment cards (`cards.py`)
- Arial regular/bold, sizes derived from one number, so there is little hierarchy.
- The reaction sticker is a 69 % black pill with no edge. On the dark blurred band it
  disappears: in the GTA test clip "NOT THE CRASH" reads as loose text, not a sticker.
- Zones are fixed fractions (`0.245`, `0.78`). Only the top zone adapts to the layout.
- Every card stays up to 4 s regardless of how long its text is.
- Each card is a looped PNG input that runs for the **whole clip**, through `format=rgba`
  and two `fade` filters, then an `overlay` with `eval=frame`. Five or six of those run on
  every frame even when the card is not on screen (*to measure*, likely a real cost).

### 1.4 Writing (`moments.py` prompts)
The prompt itself asks for the things the brief wants gone:
- system prompt: "paid per view";
- hook: "CAPITALISE the 2-3 words carrying the drama", "a 😭 or 💀", example with an emoji;
- caption: "a post caption that drives comments" (engagement bait);
- profiles suggest `#fyp`.

The saved test output shows the result: "has your game ever done this at the WORST
moment?", "who was driving??", `#fyp` on every clip.
There is no editable style file; the voice lives inside a Python string.

### 1.5 Moment picking and the 12-minute windows (`moments.find`)
- A 12-minute window is only about 90 lines (roughly 3–4k tokens). The window is far
  smaller than it needs to be, so a 3-hour stream costs 15 separate `claude -p` calls,
  each paying the CLI start-up cost.
- **No overlap and hard edges.** The prompt says "reject anything whose payoff is outside
  this excerpt", so a moment that straddles a window edge is lost by design.
- **Every window must return at least 2 fully written moments** (`want` is clamped to
  2–5), each with hook, caption, hashtags and reason. With 15 windows that is 30+ complete
  write-ups to keep 3. Output tokens are the slow part of a call, so most of the
  generation time is spent on text that is thrown away.
- **The cache is all-or-nothing.** `moments.json` is written only when every window is
  done. An interrupted run, or one window timing out, re-asks everything next time.
  A timed-out window is silently cached as "nothing here".
- **Bug:** `moments.json` is returned as-is when it exists, so re-running with a larger
  `--clips` still gives the old count, even though `candidates.json` has more.

### 1.6 Timing data that already exists
| run | video | result | total | where it went |
|---|---|---|---|---|
| GTA RP, comments style, picks cached | 8 min, YouTube captions | 2 clips (31 s, 32 s) | 391 s | audio download 47 s, **make clips 345 s** |
| workshop, captions style, local file | 10 min section | 1 clip | 122 s | **make clips 121 s** |

So on the only real numbers we have, **rendering is the bottleneck, not Claude**: about
170 s per 31 s clip, more than 5× real time. The earlier captions-style run of the same
GTA video was "about 3 minutes" end to end including picking (per the handoff), which
suggests the card overlays roughly doubled the make time.

What these reports can't tell us:
- "make clips" lumps together section download, audio alignment, caption Whisper, face
  analysis, PNG drawing and the encode.
- "pick" shows 0 because it was cached; there is no measured Claude time anywhere.
- The test video is one window long, so it says nothing about segmentation.

Likely render costs, all *to measure*:
1. **60 fps output.** The GTA clips are 1080×1920 at 60 fps because the source is. That is
   twice the frames to filter and encode.
2. **`gblur=sigma=32` on the full 1080×1920 background** in the `fit` layout, every frame.
3. **Card inputs alive for the whole clip** (1.3 above).
4. `libx264 -preset medium` on 2 cores when `--fast` isn't passed (`h264_qsv` exists here).
5. Whisper `base` with beam 5 for caption words on non-YouTube sources.

---

## 2. Design decisions

### 2.1 One small theme system (new `theme.py` + `text.py`)
- `text.py`: the shared text code, moved out of `hook.py`: measuring, emoji runs,
  **balanced wrapping** (minimise the difference between line widths, never leave one word
  alone, don't break after a short connecting word), fit-to-width shrinking.
  Used by captions, hook and cards so all three wrap the same way.
- `theme.py`: turns the config into resolved values: font files per role, sizes scaled by
  `output_width / 1080`, colours, outline, shadow, and the **safe area and zones**.
- Config becomes nested and readable, and every value is overridable in
  `<clips>/config.json`:

```json
{
  "typography": {
    "family": "Inter",
    "caption": {"weight": "ExtraBold", "size": 64, "line_spacing": 1.12, "letter_spacing": 0},
    "hook":    {"weight": "Bold",      "size": 52, "min_size": 40, "max_lines": 2},
    "card":    {"weight": "Medium",    "size": 38, "label_size": 26}
  },
  "captions": {
    "case": "sentence", "max_lines": 2, "max_width": 0.82, "position_y": 0.70,
    "text_color": "FFFFFF", "highlight_color": "FFD84D", "highlight_mode": "color",
    "outline": 3, "outline_color": "000000", "shadow": 2, "shadow_blur": 4,
    "animation": "soft", "show_punctuation": "minimal"
  },
  "safe_area": {"top": 0.07, "bottom": 0.18, "side": 0.06}
}
```

- Old flat keys (`font`, `font_size`, `highlight_color`, `caption_y`, `hook_font_size`...)
  keep working: `config.load` maps them onto the new keys, so an existing `config.json`
  doesn't break. `config.load` gets a deep merge for nested keys.
- Two or three named presets (`"caption_preset": "clean" | "bold" | "boxed"`) that are
  just dictionaries of the same keys, so a look can be switched in one line.

### 2.2 Fonts
- **Bundle the fonts in `clipper/fonts/`** instead of using system fonts, so clips look
  the same on every machine. Font files are data, not a dependency.
- Candidates, all SIL Open Font License (free to bundle and use commercially):
  **Inter**, **Montserrat**, **Poppins**. One family for captions, hook and cards gives
  the consistent hierarchy the brief asks for.
- Working choice: **Inter** (ExtraBold captions, Bold hook, Medium/SemiBold cards). It is
  designed for screens, has clear punctuation and a full weight range. Final choice is
  made in stage 2 from a specimen sheet rendered at phone size, not by guess.
- **Use static weight files, not variable fonts.** libass picks weights from variable
  fonts unreliably. Static files come from the official Google Fonts download. The copies
  in `supoclip/backend/fonts/` are mostly variable; the fonts themselves are OFL (not
  AGPL), so the static `Poppins-ExtraBold.ttf` there may be used as a fallback.
- libass gets the folder through `fontsdir=` with a relative path (ffmpeg already runs
  inside the work folder, which avoids the Windows drive-letter problem).
- Emoji stay on the system colour-emoji font, and only where emoji are enabled.

### 2.3 Captions: keep ASS/libass, do the layout ourselves
Staying on libass keeps captions in the single existing encode pass (no extra inputs, no
speed cost). The change is that Python decides the layout and libass only draws it:
- **Pixel-aware grouping.** Words are measured with Pillow using the same font file.
  A phrase is built until it would exceed 2 lines at `max_width`, or hits a sentence end,
  a clause comma, or a pause. Long sentences break at clause boundaries first.
- **Explicit line breaks** (`\N`) from the balanced wrapper, and a fixed block position
  (`\an5\pos`), so nothing moves while a phrase is on screen.
- **Highlight without reflow.** The active word changes colour with an ~80 ms ramp (`\t`).
  No inline scaling. Optional `highlight_mode: "box"` draws a rounded box behind the
  active word on a lower layer at measured coordinates. One accent colour only.
- **Subtle motion.** A phrase fades in over ~100 ms with a 3–4 % scale settle on the whole
  block. Nothing bounces per word. `animation: "none"` turns it off.
- **Punctuation.** Display drops trailing commas and full stops, keeps `?` and `!`, keeps
  apostrophes, removes `>>` and `[Music]`-type tags, never starts a line with punctuation.
  Timing still uses the original words.
- **Case** is configurable (`sentence` default, `upper`, `as-is`).
- **Shadow.** Thin outline plus a soft blurred drop shadow instead of the 6 px outline.
- **Resolution-independent.** All sizes scale with the output width.
- **Safe area.** The block is clamped between the top of the bottom safe margin and the
  hook bar; in the `stream` layout it is kept clear of the facecam seam.

### 2.4 Hook bar
- Balanced wrap, max 2 lines; long titles shrink in steps to `min_size` before a third
  line is allowed.
- Same family as the captions; tighter padding and radius on a consistent spacing scale.
- Optional single accent phrase (the prompt may mark one phrase with `*...*`); no
  capital-letter emphasis.
- Entrance: fade plus a short slide over ~0.25 s, done cheaply (the hook stays one still
  image input; only the first quarter second is animated).
- `hook.emoji: false` by default: the prompt doesn't ask for them and the renderer strips
  any that arrive. Setting it to `true` restores today's behaviour.

### 2.5 Comment cards (same behaviour, new look)
Kept exactly: real chat when there is a replay, word-for-word check, Claude reactions
otherwise, reactions never look like comments, generic avatar, no names, no counts,
cards alternate zones.
- **Chat card:** one family, clear hierarchy (small muted "viewer · live chat" label,
  message in Medium at reading size), padding and radius on the shared spacing scale,
  width between a minimum and ~78 % of the frame, slightly translucent with a soft shadow.
  The heart becomes optional (`cards.heart`).
- **Reaction sticker:** still no avatar, name or label, but given a proper surface (a
  translucent dark plate with a thin light edge) so it reads on dark and light footage.
- **Motion:** fade plus a 96 %→100 % settle on entry (~0.18 s), fade on exit. Matches the
  caption motion.
- **Timing:** hold time follows the text length (about 1.4 s + 0.3 s per word, capped by
  `comment_hold`), a minimum gap between cards, and nothing in the first second while the
  hook settles.
- **Placement:** zones are computed by `theme.py` from the real layout rectangles (hook
  bottom, gameplay band, safe area) for all three layouts. In the stacked layouts the face
  boxes that `layout.analyze` already finds are used to keep cards off faces.
- **Cost:** each card input lives only for its own on-screen seconds instead of the whole
  clip (see 2.8).

### 2.6 Writing quality
- New file **`clipper/editorial.md`**: the editable voice guide. Sections: voice, hook
  rules, caption rules, hashtag rules, phrases never to use, good and bad examples, and a
  "notes from past clips" list for the user to add to over time. Path overridable with
  `editorial_file`; a copy at `<clips>/editorial.md` wins if present, so personal edits
  survive skill updates.
- Prompts are rewritten to load that file instead of carrying the voice inline. Removed:
  "paid per view", forced capitals, emoji prompts, "drives comments", `#fyp` filler.
  Added: write about what actually happens, name the speaker only when known, 2–4 specific
  hashtags, no questions aimed at the viewer unless the clip itself raises one.
- **A cheap local check** after generation (no extra Claude call): strip emoji when
  disabled, cap hashtags, flag banned phrases and all-caps runs. Problems are fixed
  locally where that is safe and listed in the report otherwise.
- Profiles keep "what is a great moment"; their hashtag lines lose the filler tags.

### 2.7 Moment selection: adaptive windows, two steps, per-window cache
Replaces the fixed 12-minute cut; keeps chunking, snapping, dedupe and timestamps.
- **Window size by content, not minutes.** Target a word budget per call (starting value
  ~6,000 transcript words, roughly 25–40 min of speech; `pick_window_words`).
  A video under the budget is **one call**. Quiet stretches cost nothing extra.
- **Edges at silences.** A window ends at the longest pause near the target, so it
  doesn't cut through a moment.
- **Overlap.** Each window also gets about `max_duration + 30 s` of lines from its
  neighbours, marked as context. A moment must *start* in the window's own part; it may
  finish in the overlap. Existing dedupe removes doubles.
- **Step 1, find (per window):** ask only for `start`, `end`, `kind`, `score` and a
  one-line reason. Short replies, so calls return faster. `want` scales with the window's
  share of the video instead of a floor of 2.
- **Step 2, write (one call per video):** after snapping, global dedupe and top-N, one
  call writes hook, caption and hashtags for just the chosen clips, seeing each clip's
  exact final transcript and the editorial guide. In the comments style this is merged
  with the existing comment/reaction call, so that style costs **no extra call**; the
  captions style gains one short call. Writing from the final cut also fixes hooks that
  describe something trimmed off by snapping.
- **Caching.**
  - `picks/<hash>.json` per window, keyed on the window text, profile, model and limits.
    Interrupted runs resume; a failed window is retried, not cached as empty.
  - `candidates.json` is the ranked pool: changing `--clips` re-ranks with no Claude call
    (fixes the 1.5 bug).
  - `copy.json` keyed on the chosen clips plus a hash of `editorial.md`: editing the
    guide rewrites the text without re-picking. New `--rewrite` flag forces it.
- **The window size is decided by measurement** (stage 6): same long transcript at
  12 min, ~25 min and ~40 min windows, comparing wall time, call count and which moments
  are found. The smallest window that keeps the same top moments wins.

### 2.8 Render speed (changes made only after stage 1 confirms each one)
1. `output_fps` setting, proposed default **30** (`"source"` keeps 60). Needs a yes
   from the user because it changes the output (see section 5).
2. Blurred background at low resolution: scale down, blur, scale up. Same look, a
   fraction of the work.
3. Cards: per-card input trimmed to its own time span.
4. `--fast` (Quick Sync) recommended in the docs for this PC; quality compared in stage 7.
5. Caption Whisper: check whether beam 5 on `base` is worth its time on short clips.

### 2.9 Performance report
- `Timer` gains nested stages, counters and a cache hit/miss log; `_ask` records each
  Claude call (purpose, seconds, prompt and reply size).
- `report.json` and a table printed at the end of every run:

| reported as | covers |
|---|---|
| download | audio download; each clip's video section |
| audio processing | loudness curve; clip-to-audio alignment |
| transcript | platform captions or Whisper scan; chat replay |
| moment selection | step-1 Claude calls, snapping, dedupe |
| caption generation | step-2 Claude call; caption-word Whisper; ASS build |
| rendering | layout analysis; hook/card drawing; encode |
| total | wall time, plus the slowest stage named explicitly |
| cache | hits and misses by item (info, audio, transcript, loudness, chat, picks, copy, sections) |

- Per-clip breakdown under `clips[]`, and the same summary line in `POST.md` as today.

---

## 3. Stages

Each stage ends with something checkable. No stage starts by overwriting existing output.

**Grouping (user's instruction, 2026-11-14):** Part A is built as two steps in one go.
- **Step 1 = stages 1–5:** timers and baseline, theme and fonts, captions, hook and cards
  (with the facecam fix), writing.
- **Step 2 = stages 6–9:** picking, render speed, test and compare, docs.

The open decisions in section 5 were not answered before the build started, so the
recommended defaults are used and each stays a one-line config change: 30 fps output,
sentence-case captions, Inter as the font family.

| # | stage | main files | done when |
|---|---|---|---|
| 1 | **Baseline and instrumentation.** Add the timers and cache log first. Re-run the GTA video with today's visuals in both styles into `clips-baseline/` (the captions-style originals were overwritten, so "before" must be regenerated). | `util.py`, `run.py`, `render.py`, `moments.py` | A report with real numbers per sub-stage; "before" frames saved |
| 2 | **Theme system and fonts.** `theme.py`, `text.py`, nested config with old-key mapping, bundled fonts. A small `scripts/specimen.py` that draws hook, captions and cards over a saved frame in seconds, for design iteration without a full render. | `config.py`, new `theme.py`, `text.py`, `fonts/`, `specimen.py` | Specimen sheet of the font candidates; font chosen |
| 3 | **Captions.** New grouping, wrapping, highlight, motion, punctuation, safe area. | `captions.py` | No reflow between words; no clipped or overlapping text on test clips |
| 4 | **Hook and cards.** Balanced hook with entrance; card redesign, timing, zones. Includes the **facecam crop fix** (7.1). | `hook.py`, `cards.py`, `render.py`, `layout.py` | Short, medium and long titles all sit well; stickers readable on dark and light footage; no cut-off heads on the test clips |
| 5 | **Writing.** `editorial.md`, rewritten prompts, local check, profile clean-up. | `moments.py`, new `editorial.md`, `profiles/*.md` | New hooks and captions for the test clips read naturally; no emoji, bait or filler tags |
| 6 | **Picking.** Adaptive windows, overlap, two steps, per-window cache, `--rewrite`. Window size chosen from measurements. | `moments.py`, `run.py`, `config.py` | Timing table for 3 window sizes; re-run with changed `--clips` makes no Claude call |
| 7 | **Render speed.** Apply the fixes that stage 1 justified; compare quality. | `layout.py`, `cards.py`, `render.py` | Before/after seconds per clip; no visible quality loss |
| 8 | **Test and compare.** Full runs into `clips-v2/`; side-by-side frame sheets in `compare/`; A/V sync check. | none | Comparison sheet and timing table |
| 9 | **Docs.** `SKILL.md`, `CLIPPER_DESIGN.md` (styles section), config reference, `HANDOFF.md`. | docs | Docs match the code |

| 10 | **Session mode + session-edit style** (7.2). | new `session.py`, `crop.py`, `styles/session_edit` code in `render.py`, `run.py` | Private test clips from the Meet recording, cropped to the shared screen, no participant names or faces visible |
| 11 | **Montage cut type** (7.3). | `moments.py`, `render.py`, `run.py` | A 3–6 part montage from the GTA video with clean joins and in-sync captions |
| 12 | **Explainer mode** (7.4). | new `explainer.py` (prompt + profile), `run.py`, `profiles/explainer.md` | Clips from a public talk/info video that each open on a hook and finish the point |
| 13 | **Gameplay mode, no commentary** (7.5). | new `gameplay.py`, `moments.py`, `run.py` | Clips from a no-commentary gameplay video, picked from a shortlist with frames |
| 14 | **Final docs and handoff** for Part B. | docs | Docs match the code |

Order note: stage 1 comes first so every later claim about speed has a "before" number.
Stages 3–5 can be reviewed visually with `specimen.py` before any full render.
Stages 10–13 follow the build order already approved in `CLIPPER_DESIGN.md` section 9.
Each ends with real clips to watch before the next one starts.

## 4. Testing rules
- Material: the GTA RP video (`ObpdEjwQxys`), in comments style and captions style.
- **Same moments for before and after:** the small cache files in
  `clips-test/work/www_youtube_com_watch_v_ObpdEjwQxys/` are copied into the new output
  folder's `work/`, so both versions cut the identical seconds and only the look differs.
- Nothing in `clips-test/` is modified. New output goes to `clips-baseline/` and
  `clips-v2/`.
- The Google Meet recording is not used for any public-content test.
- No live-chat replay test unless a real replay link is supplied; it will be reported as
  skipped, not passed.
- **The segmentation test needs a long video** (1–2 h, public, with YouTube captions so
  no Whisper time is spent). The 8-minute GTA video is a single window and can't show it.
- Disk is tight (C: ~2 GB free, E: ~5.6 GB): test output is kept small, and downloaded
  sections are deleted as today.

## 5. Decisions needed from the user
**Answered 2026-11-14:** decisions 1, 2, 4 and 5 are approved as built (30 fps, sentence
case, Inter, the redesigned comments look), as are emoji off and 6,000-word pick windows.
Decision 3 was covered by the 75-minute test in 8.2. Decisions 6–8 are still open.
1. **Output frame rate.** Cap clips at 30 fps by default (about half the render work),
   with 60 fps as a setting? Recommended: yes, 30 by default.
2. **Caption case.** Sentence case by default (the calmer, editorial look) with capitals
   as a preset? Recommended: yes.
3. **A long test link** for the segmentation timing (any public 1–2 h video or stream VOD
   you are happy to test on).
4. **Font.** Fine to choose from the specimen sheet in stage 2, with Inter as the
   starting point?
5. **Comments-style look.** You haven't watched a comments-style clip yet. Rather than
   approve the old look, review the redesigned one at the end of stage 4 (specimen frames
   first, then one full clip). Stage 5 onward doesn't wait on this, but the look is not
   treated as final until you say so.
6. **Session-mode source.** Is the Meet workshop the kind of footage you want clipped, or
   will you record your own build sessions (and in which tool)? Needed before stage 10.
   The Meet recording is used as a private test either way.
7. **"Explainer" name.** Keep it, or rename the mode (for example `talk` or `info`)?
   Needed before stage 12; it only affects the flag name and docs.
8. **Test links for Part B:** one public talk/info video (stage 12) and one public
   no-commentary gameplay video (stage 13).

## 6. Risks
- **libass and bundled fonts on Windows:** `fontsdir` handling is checked in stage 2
  before any caption work depends on it. Fallback: keep a system font.
- **Pillow and libass measure text slightly differently.** The wrapper keeps a few
  percent of slack, and explicit `\N` breaks mean a small mismatch can't cause a re-wrap.
- **Bigger pick windows could lower recall** in dense videos. That is what the stage 6
  comparison is for; `pick_window_words` stays configurable.
- **Two-step picking adds one call in the captions style.** Expected to be repaid by the
  shorter step-1 replies; verified in stage 6, and reverted to one step for single-window
  videos if it isn't.

---

## 7. Part B: remaining build items

These follow `CLIPPER_DESIGN.md` (sections 4–6). They reuse the Part A work: the theme
system, the caption renderer, the two-step picking with per-window cache, and the timers.
No new paid services. Each item says which new dependency it needs, if any.

### 7.1 Facecam crop fix (in stage 4)
- **Problem:** on one test clip the cam rectangle was found "by border" at 374×592 and
  the top panel crop cut off the top of the streamer's head.
- **Cause to confirm:** `_stream` centres the crop on the cam rectangle and trims top and
  bottom equally when the cam is taller than the panel. It never looks at where the face
  is. `_cam_rect` may also return a rectangle that is too tight at the top.
- **Fix:**
  - Anchor the vertical crop on the detected faces: keep the highest face top plus
    headroom (about 35 % of the face height) inside the crop, then trim from the bottom
    first.
  - Check `_cam_rect`'s border result against the face boxes; if a face touches the top
    edge, grow the rectangle upward to the next strong edge or by a fixed margin.
  - New config: `cam_headroom`.
- **Done when:** the clip that showed the problem and the other GTA test clips all keep
  the full head with visible headroom.

### 7.2 Session mode and session-edit style (stage 10)
For screen recordings of building with an AI coding tool. Test file: the Meet recording
(private, never posted, never used as a public-content sample).
- **Auto-crop to the shared screen** (`--crop auto | x,y,w,h`), new `crop.py`:
  - Sample ~20 frames across the section, find the largest rectangle that stays in the
    same place while its contents change (the shared screen), versus the static Meet UI
    and the participant tiles.
  - Method: edge and frame-difference maps with OpenCV (already installed). Cached per
    video in `crop.json`. The result is logged, and `--crop x,y,w,h` overrides it.
  - Everything outside the crop is discarded before any later step, which also removes
    participant names, faces and the in-call chat.
- **Signals** (all local, new `session.py`), turned into a text timeline for Claude:
  - `PROMPT "…"`: text appearing in the prompt box, then sent (OCR on a few frames per
    change, not every frame).
  - `WORKING 3m10s`: status text or steady small changes in the editor.
  - `RESULT`: a large visual change in the preview/browser area after work ends.
  - Narration from the transcript, as today.
- **OCR:** RapidOCR, as named in the design spec. This is the one **new dependency** in
  Part B; it is added to `setup.py` and loaded only in session and gameplay modes.
  If it proves too heavy for this PC, the fallback is a session mode driven by narration
  and visual-change detection only, without prompt text.
- **Language:** the Meet narration is Urdu/Hindi, so it gets no word captions (existing
  rule). The edit must work from the visuals plus hook and cards alone.
- **Session-edit style:** hook (the final result, 1–2 s) → the prompt zoomed in (typing
  sped up) → a "3 minutes later" card with the working stretch at 10–20× → the result
  with a slow zoom and pan → captions where the language allows.
  - Built from ffmpeg `crop`/`zoompan`/`setpts` segments joined by the montage cutter
    (7.3), so 7.3's joiner is written first within this stage or the two are built together.
  - The time card and progress bar are drawn through the theme system. Whoosh sounds are
    **left out** (sound design is out of scope for now).
- **Claude Code logs:** optional, only when a session log overlaps the recording. Not
  available for the Meet file, so this path is built last and may be deferred.
- **Done when:** 1–2 clips from a section of the Meet recording show prompt → sped-up
  work → result, cropped cleanly, with no names or faces in frame.

### 7.3 Montage cut type (stage 11)
- **Flag:** `--cut single | montage | auto` (default `auto`, Claude decides per moment).
- **Picking:** step 1 may return `"parts": [[start, end], ...]` (3–6 ranges, one story)
  instead of one range. Each part is snapped to word edges like a single clip. Total
  length obeys `min_duration`/`max_duration`. Dedupe compares the union of parts.
- **Rendering:** each part is fetched and aligned as today, then joined in one ffmpeg
  pass (`trim`/`concat`), with a short zoom punch at each join (`montage_punch`, can be
  turned off). One layout decision is made for the whole montage so the framing doesn't
  jump.
- **Captions, cards and hook** run on the joined timeline: word times are shifted by the
  sum of the earlier parts.
- **Download cost:** parts close together are fetched as one section.
- **Done when:** a montage from the GTA video plays with clean joins, captions in sync
  across every join, and a correct `POST.md` entry listing the source times of each part.

### 7.4 Explainer mode (stage 12)
For talk and information videos. Name still to be confirmed (decision 7).
- **Flag:** `--mode explainer` (and `--mode auto` picks it when there is dense speech, no
  facecam-over-gameplay layout, and no chat).
- **Signals:** transcript with sentence boundaries, pace and emphasis (loudness), topic
  shifts. No new local detectors.
- **Prompt and profile:** a different picking prompt and `profiles/explainer.md`: find
  self-contained segments with hook → explanation → payoff, start on the claim not the
  preamble, end when the point lands. Montage is offered here (several short points in
  one clip).
- **Windows:** dense speech fills the word budget faster, so the adaptive windows from
  2.7 simply produce more, shorter windows; no special case.
- **Look:** captions style by default; `face` or `fit` layout from the existing chooser.
- **Done when:** clips from a public talk video each make sense alone and finish the point.

### 7.5 Gameplay mode, no commentary (stage 13)
- **Flag:** `--mode gameplay` (auto-picked when there is little or no speech).
- **Signals (local):** game-audio peaks (from the existing loudness curve, plus onset
  sharpness), scene cuts and motion bursts (one low-resolution ffmpeg pass), and OCR of
  big on-screen text such as "WASTED" or "MISSION PASSED" (keywords from the profile).
- **Shortlist:** the top ~15 windows by combined score. Cost depends on the shortlist,
  not the video length.
- **Claude with frames:** 3–4 small frames per shortlisted window plus the signal tags.
  This needs a `claude -p` call that can read image files, so the lean no-tools call
  can't be used here. **To investigate first** (before building the rest of the mode):
  the cheapest working way to pass frames on the subscription, and what it costs in
  usage. Fallback if it is too costly: rank by local signals only and have Claude write
  the hook from the tags.
- **For links:** frames need video, and today only audio is downloaded up front. Plan:
  fetch a low-resolution (360p or lower) video once for analysis, cached, then the
  normal per-clip sections for rendering.
- **Look:** no captions (no speech); hook plus reaction stickers; `fit` or full-crop
  layout.
- **Done when:** clips from a no-commentary video land on real action, with timings
  reported for the analysis pass.

### 7.6 Open questions
Tracked as decisions 5–8 in section 5. In short:
- comments-style look: reviewed by the user at the end of stage 4;
- session-mode source: answer needed before stage 10;
- "explainer" name: answer needed before stage 12.

### 7.7 Extra risks in Part B
- **RapidOCR on 8 GB RAM** with ~1.3 GB often free: run it on few, downscaled frames
  and never alongside Whisper.
- **Auto-crop could pick the wrong rectangle** (for example a large participant tile):
  the detected crop is saved as a preview image and can be overridden with `--crop`.
- **Frames for Claude** may use noticeably more subscription usage than text; measured
  before gameplay mode is committed to.
- **Disk:** a low-resolution analysis video for long streams still takes space; it is
  deleted after the run unless `--keep-audio` style caching is asked for.

---

## 8. Part A results (built and measured 2026-11-14)

Part A (stages 1–9) is built. Part B (section 7) is not started.

### 8.1 Where the time goes: measured, same two ~31 s clips, same picked moments
GTA RP test video (8 min, YouTube captions). "Before" is the old code (snapshot in
`clips-baseline/_code`), "after" is the new code, both on `libx264`.

| | captions before | captions after | comments before | comments after |
|---|---|---|---|---|
| total | 535 s | 233 s | 419 s | 232 s |
| encode | 409 s | 126 s | 324 s | 130 s |
| per-clip video download | 80 s | 66 s | 69 s | 60 s |
| layout analysis | 21 s | 23 s | 23 s | 17 s |
| Claude (write call) | cached | 14 s | cached | 22 s |

Cold run of the exact user command with `--fast` (Quick Sync), nothing cached:
**184 s** in total. Download 102 s (86 s of it the two clip sections), rendering 44 s
(encode 29 s), moment selection 12 s (one call), caption generation 13 s (one call),
transcript 8 s, audio 3 s.

What this says:
- **The encode was the bottleneck** (76–78 % of every baseline run). The 30 fps cap,
  the cheaper blur and the shorter-lived card inputs cut it by about two thirds on the
  same encoder; `--fast` cuts it again to about 15 s per clip.
- **After the changes the slowest stage is downloading each clip's video section**
  (~35–45 s per clip through yt-dlp). Not addressed in this round; see 8.5.
- **Claude was never the bottleneck on this video:** 12 s to pick, 13–22 s to write.
- The separate effect of each render change was not isolated; they were measured together.

### 8.2 Picking: window sizes on a 75-minute video (5,877 words, picking only)
| setting | Claude calls | wall time | Claude seconds (sum) | candidates | top 5 shared with 12-min |
|---|---|---|---|---|---|
| old code, 12-min windows | 7 | 29.6 s | ~95 s | 21 | n/a |
| new prompt, 12-min windows | 7 | 22.5 s | 70.7 s | 19 | 5/5 |
| new, 3,000-word windows | 2 | 21.7 s | 33.1 s | 16 | 4/5 |
| new, 6,000-word windows (default) | 1 | 9.6 s | 9.6 s | 8 | 3/5 |

- The three highest-scored moments are the same in every setting. The 4th and 5th picks
  differ, and one large call returns fewer candidates.
- One call instead of seven is 3× faster in wall time and about a tenth of the Claude
  time, which matters for subscription usage more than for the clock.
- **Default kept at 6,000 words.** This is one video; if a long stream seems to miss
  moments, lower `pick_window_words` to 3000. The test did not include a video long
  enough to need several 6,000-word windows, so the overlap logic ran only in the
  3,000-word case.
- Tool: `python clipper/scripts/bench_pick.py URL --out DIR`.

### 8.3 What was checked on the output
- Captions: sentence case, at most two lines, fixed position, colour-only highlight; no
  overlap with the hook bar on the test clips (`compare/captions_sheet.jpg`).
- Hook: balanced two-line wrap, accent phrase, fade-in; no emoji.
- Facecam: the clip that cut the streamer's head off now shows the whole face.
- Cards: reaction stickers readable on the dark band; top zone between hook and gameplay
  (`compare/comments_sheet.jpg`).
- Audio and video are the same length (32.0 s each on the checked clip).
- Writing, new vs old for the same clip: "He gets told to log out, then he actually
  crashes" with tags `#nopixel #gtarp #gtav`, against "He jokes 'log out FOR ME'... then
  this happens" plus an emoji and five tags ending in `#fyp`.

### 8.4 Not tested
- **Real chat cards in a full run:** no live-replay link was available. The chat card
  was only drawn in the specimen tool with made-up lines. Skipped, not passed.
- Other output sizes than 1080×1920.
- The workshop recording was not used.
- A source that is really not on YouTube (Twitch, Kick, a local English file). The
  Whisper path was tested on the GTA video with `platform_captions` off (8.6).

### 8.6 Gap tests (2026-11-14, full `--fast` renders in `clips-gaps/`)
Same GTA RP video. Frame sheets are in `clips-gaps/sheets/`. Audio and video lengths
match on all four clips (within 0.01 s).

| run | what it tested | clips | total | clip download | Whisper | encode |
|---|---|---|---|---|---|---|
| `boxed` | `caption_preset: boxed` (box highlight) | 2 | 237 s | 94 s + 72 s | none | 12 s + 14 s |
| `bold` | `caption_preset: bold` (capitals, Black weight) | 1 | 226 s | 182 s | none | 18 s |
| `whisper` | `platform_captions: false`, nothing cached | 1 (48 s) | 195 s | 58 s | scan 35 s, caption words 8 s | 28 s |

- **Box highlight: works.** The box sits on the spoken word, the text does not move,
  and nothing overlaps the hook bar.
- **Bold preset: works.** Capitals, heavier weight, two lines at most.
- **Whisper-word captions: work, with one flaw.** Captions are drawn and timed from
  Whisper base words. But a phrase can run across a sentence end and keep the capital
  letter of the next sentence ("one before You", "Have seen some people"). YouTube words
  have no capitals, so this only shows on Whisper sources. Not fixed. Likely fix in
  `captions.py`: break the phrase at a word that ends a sentence, or lower-case a
  capitalised word that is not first in its phrase (keep "I" and names).
- **Hook line-break tweak: confirmed in full renders.** Four different hooks, all two
  balanced lines, none ending on a short connecting word.
- **Whisper tiny was faster than the handoff says:** 35 s for 8 minutes of audio
  (model load included), not real time. One measurement.
- **`copy.json` copied from another folder was not reused** ("clip text" was a cache
  miss in the `boxed` and `bold` runs, one 4–6 s write call each). Not investigated;
  the key may include settings that differed.
- **Real chat cards: still skipped.** No live-replay link was given.

### 8.5 Left for later in Part A's area
- Per-clip video download is now the largest stage. Ideas, none tried: download the
  clips' sections in parallel, or fetch at 720p when the layout only needs that.
  **It also varies a lot:** the 8.6 runs measured 58 s, 72 s, 94 s and 182 s for one
  ~40 s section each (75–85 % of two of those runs), against 35–45 s earlier the same
  day. Where the time goes inside yt-dlp (start-up and YouTube's challenge, or the
  transfer itself) has not been measured; do that before choosing a fix.
- In the `bold` run (one clip) the last part of clip 1 was framed as `fit` ("7 faces on
  screen"), where the two-clip `boxed` run framed it as `stream` using the cam learned
  from clip 2. So the framing of a clip can depend on `--clips`. Not a new bug.
- `render.analyze` (~10 s per clip) is the next largest fixed cost.
- The face-follow part of one test clip shows a black bar at the bottom. It is in both
  the old and the new output, so it comes from the layout or the source, not this work.
  Seen again in `clips-gaps/sheets/boxed_01.jpg` (frames 4–5). `layout._follow` crops
  the full source height, so the bar is probably in the source video itself (it is a
  compilation), but no source frame has been checked yet. To check: download that
  section (1:13–1:44 of the video) and run ffmpeg `cropdetect` on the face part.

### 8.7 Leftovers closed (2026-11-14, `clips-dl-test/`)
- **Whisper mid-phrase capital: fixed** in `captions._tokens`. A capitalised common word
  (You, Have, The...) after a word with no sentence end is a sentence start, so the phrase
  breaks there; names and "I" are kept. Checked on a word list only, not in a render.
- **Clip download, measured** (one 31 s section, same video): yt-dlp start-up plus YouTube
  challenge 7 s and 18 s (`-J`; once 39 s to resolve stream URLs); the section transfer
  itself 24 s and 48 s with start-up skipped, ffmpeg at 0.7-1.4x real time (YouTube
  throttles). So both matter; start-up varies most.
- **Fix:** `Link.info()` keeps yt-dlp's full metadata as `work/info_full.json`; each
  section runs with `--load-info-json` (no start-up). Falls back to the old call if the
  file is missing or older than 5 h, or the links fail. Cold `--fast` run, 2 clips (22 s and
  18 s): sections 23.6 s and 15.3 s (`report.json` `download.sections` 38.9 s), against
  58-182 s per ~40 s section in 8.6. Different clip lengths and one run only, so not a
  clean before/after. Parallel section downloads not tried; transfer is still the cost.
- **Black bar: it is in the source** (full-screen cam, letterboxed; `cropdetect` on the
  face part gives about 972 of 1080 rows). `layout._follow` now measures black rows on
  the crop's columns and crops them out (3-15 % bars only). Checked on one frame.

### 8.8 Stage 10 started (2026-11-14): decisions and the auto-crop
- **Decision 6 answered:** the source is similar Google Meet sessions, which the user will
  record with OBS later. The Meet file stays a private test. Decisions 7 (mode name) and 8
  (public test links) are "later", asked again before stages 12 and 13.
- **`clipper/scripts/clipper/crop.py` built, not yet wired into `run.py`/`render.py`.**
  `detect(source, t0, t1, info, workdir, spec)` pulls 20 frames by fast seeks (about 9-11 s
  for a section, no full decode), finds the largest rectangle inside the area whose picture
  changes (so tiles touching the screen are not swallowed), then grows each edge while the
  strip beyond still changes and is not the call's flat background. Cached in `crop.json`;
  `crop_preview.jpg` shows the box with everything outside it blurred and darkened.
  `--crop x,y,w,h` is parsed by `crop.parse`.
- **Tested on three 20-minute sections of the Meet file** (`clips-session/s1..s3`, private,
  not to be posted): boxes (88,160,1472,664), (40,288,1072,536), (80,184,1488,656). One
  right edge was checked against column brightness (flat band at x=1568: matches). Known
  limits: the box is a little inside the real screen at the bottom and, in a slide section,
  at the right; the presenter's own Meet pop-up (with a participant tile and name) sits
  inside the shared screen in two sections, so a crop cannot remove it (blur it later).

### 8.9 `--crop` wired in (2026-11-14)
- `run.py --crop auto|x,y,w,h` (local files only): `crop.detect` runs once per section, the
  box goes in `cfg["crop_box"]`, `render.prepare` swaps `info` for the cropped size (with a
  `crop` entry), `layout._faces` crops before sampling, `render.finish` crops first in the
  filtergraph. Everything after that (faces, layout, hook, cards) sees only the shared screen.
  Without `--crop` nothing changes. Checked with one render using a manual crop
  (`clips-session/run1/wiretest/`, private): 1080x1920, 30 fps, h264_qsv, 9 s encode.
- **Auto-detect is NOT reliable on the Meet file.** Sections of 10 minutes found no screen
  (18 % of the frame) until the sampled span was widened to 20 minutes. Across eight sections
  the box was a consistent 1472x664-ish screen in four, and a different or narrow region in
  three (t1, t3, t5), because Meet's layout changes (screen pinned or not, tiles beside it)
  and the share is a dark IDE whose changes are small. A version that treats "not the call's
  background colour" as the screen merged the tile strip into the box and was dropped.
  Use `--crop x,y,w,h` (read from `work/.../crop_preview.jpg` or pick it by eye) when the
  preview looks wrong. Possible fix later: detect per clip instead of per section, or
  find the screen from the "Stop sharing" banner / window title bar.
- **Layout on screen recordings:** the face finder sees the presenter's own Meet pop-up
  inside the shared screen and picks the `stream` look. Session mode needs its own layout
  (no face logic) in `session.py`. Not done yet.
- `--crop auto` on a 10-minute section found nothing and `--redo` of Claude's picking found
  nothing either: Whisper tiny on Urdu is unusable for picking (as in HANDOFF). So no
  full session clip exists yet; session mode must pick from visuals (prompt/working/result
  signals), not the transcript.
