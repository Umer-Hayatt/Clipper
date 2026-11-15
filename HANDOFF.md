# Clipper: handoff

To continue in a new chat, start with something like:
> Read `E:\clipping tool\HANDOFF.md`, `OVERHAUL_PLAN.md`, `CLIPPER_DESIGN.md` and `clipper\SKILL.md`, then continue with the next step.

---

**Status (2026-11-14): paused by the user after stage 10 step 1.** Done and usable: Part A, the 8.7 leftovers, `--crop x,y,w,h` (manual). Not done: reliable `--crop auto`, `session.py`/OCR, montage, explainer, gameplay modes.

## What this project is
A free Claude Code skill that turns long videos (YouTube, Twitch or Kick links, or local files) into ready-to-post
9:16 clips: hook title bar, word-by-word captions, facecam on top and gameplay below, plus a `POST.md` with a
caption and hashtags per clip. Claude picks the moments through `claude -p` on the user's own subscription
(no API keys). The user provides the links and does all posting; the skill never posts.

It started as a port of a Mac-only "gta6-clipper" skill (original zip: `E:\clipping tool\gta6-clipper-skill.zip`)
and was made generic and Windows-native.

## Files
| path | what |
|---|---|
| `E:\clipping tool\CLIPPER_DESIGN.md` | **The approved spec**: modes, cut types, styles, profiles, build order. Read first. |
| `E:\clipping tool\OVERHAUL_PLAN.md` | **Current work:** plan for the caption/visual quality overhaul (not yet implemented) |
| `E:\clipping tool\clipper\` | The skill itself |
| `clipper\SKILL.md` | How Claude runs it (flags, problems) |
| `clipper\clip.cmd` / `clipper\clip` | Launchers (Windows / bash) |
| `clipper\scripts\setup.py` | One-time setup: private env in `~/.clipper`, Whisper models, face model |
| `clipper\scripts\run.py` | CLI entry: `one_video()` does the whole pipeline per input |
| `clipper\scripts\clipper\source.py` | `LocalFile` / `Link` (yt-dlp): info, audio, per-clip video section, YouTube captions, chat replay |
| `clipper\scripts\clipper\scan.py` | Transcript (YouTube json3 captions first, else Whisper), loudness, chat rate and messages |
| `clipper\scripts\clipper\moments.py` | Step 1 picking (windows, cache, snapping, dedupe) and step 2 writing (hook, caption, hashtags, cards) |
| `clipper\scripts\clipper\render.py` | Align, caption words, encode (`--fast` hardware encoder), `POST.md` |
| `clipper\scripts\clipper\layout.py` | 9:16 framing: stream (cam on top), face (follow), fit (blur) |
| `clipper\scripts\clipper\captions.py` / `hook.py` | ASS word captions (layout done in Python) / Pillow hook bar |
| `clipper\scripts\clipper\theme.py` / `text.py` | The look resolved from config (fonts, sizes, safe area, zones) / shared measuring and balanced wrapping |
| `clipper\editorial.md` | The editable writing guide for hooks, captions, hashtags, reaction lines |
| `clipper\fonts\` | Bundled Inter weights (open licence) |
| `clipper\scripts\specimen.py` / `bench_pick.py` | Look preview on a still / picking timing test |
| `clipper\scripts\sound_library.py` | Adds/confirms/removes the stingers and music beds the clipper may use. Cuts them from the user's reference videos into `~/.clipper/sounds` (never in git). `--label` finds the exact spot with YAMNet; `--exact` cuts a fixed range. New sounds are "unconfirmed" until the user listens. |
| `clipper\scripts\sound_events.py` | Lists the sounds in a video and when each plays (YAMNet, 521 labels, ~3 s per minute of audio). Use it to measure reference videos instead of guessing. Hears effects that stand out; effects buried under loud speech are masked, and deep sub-bass booms read as "Music/Drum". |
| `clipper\scripts\clipper\cards.py` | Comments style: chat cards / reaction stickers, timing, ffmpeg pop-in overlay |
| `clipper\scripts\clipper\config.py` | All defaults; overrides go in `<clips>/config.json` |
| `clipper\profiles\gta6.md`, `general.md` | What a great moment is, per game |
| `E:\clipping tool\clips-test\` | Test outputs (good run: `2026-11-14\nopixel-moments__...`) |
| `E:\clipping tool\supoclip\` | SupoClip (AGPL). **Ideas only, never copy code.** |
| `E:\clipping tool\2026-11-13 18-05-01.mp4` | OBS recording of a Google Meet web-dev workshop (Urdu/Hindi, 2 h). Test file for the session mode. Contains other people's names and faces: never post it. |

## How to run
```
"E:\clipping tool\clipper\clip.cmd" <link-or-file> [--profile gta6] [--clips 3] [--section 1:00:00-1:20:00] [--fast] [--language ur] [--out DIR]
```
In bash: `./clipper/clip ...`. Output goes to `~/clips/<date>/...` by default; tests used `--out "E:/clipping tool/clips-test"`.
Re-running resumes from the cache in `<out>/work/`. Use `--redo` to re-pick moments, `--rewrite` to redo only
the text, `--fast` for the hardware encoder (recommended on this PC), `--fps source` to keep 60 fps.

## Build status (spec section 9)
1. **DONE:** engine, streamer mode and captions style on Windows, with gta6/general profiles. Tested end to end:
   an 8-minute GTA RP YouTube video gave 2 good clips in about 3 minutes.
2. **DONE (2026-11-14):** `comments` style (`--style comments|captions|auto`, `clipper/cards.py`).
   - `auto` = comments when the video has a chat replay, else captions. Comments style forces the `fit`
     layout with a 1.5 band (`comment_fit_aspect`) so cards have room above and below the gameplay.
     Word captions are off in this style (`comment_captions`).
   - `moments.comments()`: one extra lean `claude -p` call for all clips, cached in `<work>/comments.json`
     (`--redo` clears it). With chat it sends at most 150 messages per clip (emote codes, links, spam
     stripped) and **keeps only messages that match the originals verbatim**. Without chat Claude writes
     reaction lines, drawn as dark stickers (no avatar, name or heart).
   - Chat cards: generic avatar, "viewer · live chat", heart, no like counts (none are made up).
   - Pop-in: fade + 60 px slide-up over 0.2 s, fade out; cards alternate top/bottom zones, max 4 s each.
     The top zone centres itself between the hook bar and the gameplay band.
   - `scan.chat()` now keeps real emoji characters (YouTube's `emojiId`) instead of `:shortcut:` codes.
   - Tested: the GTA RP video (reaction path, real run) and the chat path with a made-up chat log in a
     throwaway folder (deleted). **Not yet tested on a real YouTube live replay**: that needs a link
     to a stream VOD with chat replay. The `clips-test/.../nopixel-moments...` clips are now the
     comments-style versions (the earlier captions-style files were overwritten).
2b. **DONE (2026-11-14): caption and visual quality overhaul (Part A of `OVERHAUL_PLAN.md`).**
   Results and measurements are in `OVERHAUL_PLAN.md` section 8. In short:
   - **Look:** theme system (`theme.py`, `text.py`, nested config groups `typography`, `captions`, `hook`,
     `cards`, `safe_area`, `writing`; old flat keys still work), bundled Inter fonts in `clipper/fonts/`.
     Captions: Python does the layout (phrases, balanced line breaks, fixed position), the spoken word
     only changes colour, sentence case, soft shadow. Hook: balanced wrap, shrinks for long titles, one
     accent phrase (`*stars*` in the text), fade-in, emoji off. Cards: redesigned chat card and reaction
     sticker, hold time by text length, zones worked out from the layout. Same comment rules as before.
   - **Facecam head-crop fix** in `layout._stream` (anchors on the face, `cam_headroom`). Verified on the
     clip that showed the problem.
   - **Writing:** `clipper/editorial.md` is the editable voice guide (a copy at `<clips>/editorial.md`
     wins). `moments.polish()` strips emoji, lowers shouting, drops tags listed under "Never use" and
     flags banned phrases; notes go to the log and `report.json`. `--rewrite` redoes only the text.
   - **Picking:** `moments.find()` returns only times/kind/score/reason, in windows of
     `pick_window_words` (6000) cut at pauses with overlap, each cached in `<work>/picks/`.
     `moments.write()` is one call for hook/caption/hashtags (+ cards), cached in `copy.json` keyed on
     the editorial text. Old caches (`moments.json` without `picks/`) are still read. Changing `--clips`
     no longer needs a new call.
   - **Speed:** `output_fps` 30 by default (`--fps source` keeps 60), blur at low resolution, card inputs
     that live only while shown. Same two clips: 535 s -> 233 s (captions) and 419 s -> 232 s (comments)
     on libx264; a cold `--fast` run of the whole pipeline took 184 s.
   - **Report:** `util.Timer` records stages, per-clip times, cache hits/misses and Claude calls; a table
     prints at the end of every run and is saved in `report.json`.
   - **Tools:** `scripts/specimen.py` (look preview in ~10 s), `scripts/bench_pick.py` (picking timing).
   - **Test folders:** `clips-baseline/` (old look + old code snapshot in `_code`), `clips-v2/captions`,
     `clips-v2/comments`, `clips-v2/fresh` (cold `--fast` run), `clips-v2/bench/` (picking test),
     `compare/` (before/after sheets), `clips-gaps/` (gap tests, 72 MB). `clips-test/` was not touched.
   - **Gap tests done (2026-11-14, `clips-gaps/`, details in `OVERHAUL_PLAN.md` 8.6):** the `boxed` and
     `bold` presets, Whisper-word captions (`platform_captions: false`) and the hook-wrap tweak all work
     in full renders. One flaw found and not fixed: on Whisper sources a caption phrase can cross a
     sentence end and show a capital mid-phrase ("one before You").
   - **Still not tested:** real chat cards in a full run (no live-replay link), other output sizes, a
     source that is really not on YouTube.
   - **Known leftovers (measured, not fixed; `OVERHAUL_PLAN.md` 8.5):** per-clip video download is the
     slowest stage and varies widely (58-182 s per clip in the gap tests, 35-45 s earlier); where the
     time goes inside yt-dlp is not measured yet. `render.analyze` ~10 s per clip. The black bar under
     the face-follow part of one test clip is probably in the source video; not confirmed.
   - **Look and defaults APPROVED by the user (2026-11-14)** after seeing `compare/*.jpg` and one clip
     of each style, with no changes: `output_fps` 30, `captions.case` "sentence", family Inter,
     `writing.emoji` false, `pick_window_words` 6000. Don't re-ask.
   - Still waiting on the user for Part B: session-mode source (before stage 10), the "explainer" name
     (before stage 12), public test links for explainer and gameplay modes, a live-replay link.
   - Out of scope, by the user's instruction: memes, reaction images, auto-posting, dashboard, paid
     APIs. (Sound effects were out of scope until 2026-11-15; the user then asked for them, see 2e.)
   - Test rules: never write into `clips-test/`; the Meet recording is not used for public-content tests.
2c. **DONE (2026-11-14, `OVERHAUL_PLAN.md` 8.7):** fix the Whisper mid-phrase capital; measure the clip download
   (yt-dlp start-up vs transfer) and fix it only if cheap; confirm the black bar with `cropdetect`.
2d. **DONE (2026-11-14): architecture review, candidates A and B only** (the rest were not chosen; nothing else changed).
   - **`clipper/footage.py` (new): `Footage`** = the picture the pipeline works on. `width`/`height` are the working
     picture (inside the crop box, if any); `source_width`/`source_height` are the file's. `cropped(box)` rounds to
     even numbers and raises a readable `ValueError` for a box outside the frame (run.py checks a manual `--crop`
     right after probing). `prefilter(out_fps)` is the only place that crops and drops frames; it ends in `[base]` and
     every layout graph now starts from `[base]` (no more `.replace` on filtergraph text). It replaces the old `info` dict
     past the probe (`render`, `layout`, `theme.band`, `specimen.py`); `crop.py` still takes the probe dict.
   - **`layout.decide(analysis, duration, footage, cfg, known)` -> `Framing`** (`graph, look, cam_h, cams, notes`)
     replaces `layout.graph`. It is pure: it only reads `known` (now a plain list of cam rectangles); `render.learn`
     adds `framing.cams`, `render.finish` prints `framing.notes`. No `quiet` flag, no thread-local. `Face` and
     `Analysis` are NamedTuples. `layout._fit` is now public `layout.fit`. `render.learn` and `finish` each call `decide`
     (about 0.4 s per call, one clip, measured once).
   - **Tests (stdlib `unittest`, no new dependency):** `python -m unittest discover -s clipper/scripts/tests` from
     `E:\clipping tool` with `~/.clipper/venv/Scripts/python.exe -I`. 22 tests: Footage, layout graphs against
     `tests/golden_layout.json` (recorded from the original code), Framing.
   - **Checked:** two real renders of `clips-dl-test\c_cur.mp4` (no crop, manual crop `160,90,1600,900`, libx264)
     decode to the same 1538 frames (video and audio) as the original code, after A and again after B.
   - **Not tested:** `specimen.py` (edited, not run), odd crop values in a render, a real auto-crop, two different
     cams matching one unclear clip (the old finish pass re-added a clip's cam to `known`; the new one does not).
   - **Left alone on purpose** (code-quality review, not chosen): `if True:` in run.py, the duplicated yt-dlp format
     string in source.py, `except Exception: pass` in `layout._cam_rect`, dead `notes`/`boxes` returns, the
     `layout.analyze` pass-through. Architecture candidates C-F and H are not built.
   - **Safety copies still on disk:** `clipper-scripts-backup-2026-11-14\` (original), `clipper-scripts-after-A-2026-11-14\`,
     and the test renders in `clips-arch-check\`. Delete when happy.
2e. **DONE (2026-11-15): editing styles from the user's 13 reference shorts** (`E:\clipping tool\New folder\`).
   - **What the references do:** 4 of 13 have no word captions. Captions mostly sit on the cam/gameplay seam:
     1-4 words, often capitals, one word at a time, italic with changing colours, a condensed font with green key
     words, or a serif. There are plain-text "POV:" titles, red pill titles, big tilted pop words with a face
     punch-in, and profanity masked as "F***". Sound effects could not be confirmed, because the clips can't be
     heard here.
   - **Built (all opt-in; default output is byte-identical, checked on specimen ASS and hook PNG):**
     - New presets `seam`, `punch`, `keyword` (Impact), `serif` (Georgia Bold) and `none`, plus `caption_preset:
       "mix"`. Mix gives each clip a look from `mix_presets` (stable per clip start; the user's own
       captions/typography/hook keys still win; `config.for_clip`).
     - New caption keys: `show` (true/false/"auto" = Claude decides), `chunk: word`, `anchor: seam`, `italic`,
       `color_cycle`, `keyword_color`, `mask_profanity`, and `animation: pop`.
     - Titles: `hook.style` bar|pill|text, `hook.placement` auto|top. Seam captions push the title to the top.
     - Beats: `beats.enabled`. Claude returns 0-2 peaks per clip; each gets a big tilted word (typography role
       `beat`) and a punch-in zoom on the cam (`captions.beats`, `captions.zoom_filter`).
     - `clipper/sfx.py` makes thud/boom/whoosh/pop from plain maths (no downloads). With `sfx.enabled` they are
       mixed after loudnorm: a thud on each beat, moved to the next pause within 1.5 s (`sfx.pause_reach`). Pauses
       are found in the clip's own sound (`sfx.in_pause`: 120 ms at 14 dB under the loud part), not in word
       timings: YouTube caption words have no gaps and the last word of a line is stretched.
     - **Superseded later on 2026-11-15 (user's direction):** sounds now come from the sound library cut from
       the references (`sound_library.py`), the made-up sounds are only a fallback, and the rules in "Sound rules"
       apply: one stinger per clip on the last beat, skipped with no pause (`sfx.no_pause`), music opt-in
       (`--music`).
     - Library on 2026-11-15 (all unconfirmed, the user has not listened yet): stingers `camera` (CaseOh cow),
       `airhorn` (RDR so intense), `boom` (Roger Clark); music beds `tense-bed` (5.7 s) and `jynxzi-bed` (2.9 s).
       Rejected: the iShowSpeed slam (YAMNet finds no slam there, music under it) and the "Top 5 GTA RP" bed
       (cars and skidding, not music). The beds are short loops; longer clean music needs more references.
       `airhorn` was checked to be a horn, not the streamer's scream: Air horn 0.60, Screaming/Yell/Shout 0.00,
       pitch steady at 431 Hz (spread 0.46 semitones; a voice glides).
       No title whoosh by default (`sfx.on_hook`). Retuned on 2026-11-15 from `sound_events.py` measurements of
       the 13 references: added effects are rare, sit in pauses (speech score near 0), are 0 to +10 dB vs the clip's
       median level, and none plays a whoosh with the title. The old boom had 1% of its energy above 150 Hz
       (inaudible on phones); every sound now has at least 30% there (test in `test_styles.py`). Cached WAVs carry
       `sfx.VERSION` in their name, so changed sounds are remade. Music beds (in 7 of 13 refs) are not done.
     - `moments.write` asks only for the extra fields a run needs (`config.text_needs`), so default prompts and
       caches are unchanged.
     - CLI: `--look`, `--title`, `--beats`, `--sfx`. `specimen.py`: `--preset`, `--keywords`, `--beats`.
       Tests: `tests/test_styles.py` (34 tests in total).
   - **Deferred (pipeline features, not caption styles):** numbered-list overlays (refs 07, 12), compilation title
     cards (08), b-roll/chat inserts (06), "time skip" cards (11), cam-bottom layout (02, 11), speaker colours (09).
   - **Tested end to end:** `clips-styles-test\` (a copy of the CaseOh work cache), run with
     `--look mix --beats --sfx --title text --fast`. It made 3 clips in 5 minutes, which got the clean, punch and serif looks.
     The flash words, zoom and seam captions show in the frames. The booms are in the audio (+21 dB of bass at each beat,
     none in the original).
     The first run turned up three faults: the limiter was adding about 0.4 dB to every clip, the caption repeated the
     beat word underneath it, and a long beat word reached the frame edge. All three are fixed (`level=disabled`,
     `quiet` windows in `captions.build`, beat width 0.74), and the clips were re-rendered. Speech now matches the
     original (−12.9 LUFS, −1.4 dBFS, against −12.8 and −1.4).
     **Not done:** the seam/keyword looks and the pill title in a real render (preview tool only), `show: auto`,
     and a listen by the user.
   - **User decision (2026-11-15):** the default caption look stays `clean`. The new looks are only used when asked
     for (`--look` or `caption_preset`). Don't ask again.
   - Safety copy: `clipper-scripts-before-styles-2026-11-15\`.
3. **IN PROGRESS (stage 10; crop.py built and `--crop` wired, auto-detect unreliable, see `OVERHAUL_PLAN.md` 8.8-8.9; next: user reviews crop previews, then wire `--crop`, then session.py/OCR):** (Part B, stage 10; ask decisions 6-8 of `OVERHAUL_PLAN.md` section 5 first):** session mode and session-edit style (screen recordings of AI coding: OCR the prompt box, show the
   "working" stretch sped up, zoom into the result). The Meet recording needs an auto-crop to the shared screen.
4. Montage cut type (several short ranges stitched into one story).
5. Explainer mode (YouTube talk/info videos).
6. Gameplay mode with no commentary (audio peaks + scene cuts, then Claude looks at frames from a shortlist).

Items 3-6 are planned in detail in `OVERHAUL_PLAN.md` section 7 (stages 10-13).

**Small known issue (planned fix: `OVERHAUL_PLAN.md` 7.1, stage 4):** on one test clip the facecam crop cut off the top of the streamer's head (the cam rect was
found "by border" at 374x592). Worth tuning `_cam_rect`/`_stream` in `layout.py`.

## Sound rules (measured from the 13 references, 2026-11-15)
**PARKED (user, 2026-11-15): do not work on background music or sound effects until the user brings it back.**
The user was not happy with the music results and will provide their own background music later. The code stays
as it is, and both stay off by default (`sfx.enabled`, `music.enabled` false; `--sfx`, `--music` opt-in).
The listening tool `scripts/sound_events.py` stays in use: it lets the agent check what is in a video's audio.
Source: `sound_events.py` per reference, Whisper words, ffmpeg scene cuts, and frames at each candidate.
User's direction: use sounds taken from the references (no generated sounds); the tool's job is
to decide when and where to place them, and when not to. The user will add more references later.

**Sound effects (stingers)**
- Rare: 0-1 added stinger per clip. The clear ones are the camera shutter (CaseOh cow, 79% of the clip), the air horn
  (RDR "so intense", 86%) and the slam (iShowSpeed, 91%). The boom under flash words is at 5% (Roger Clark).
- Each sits on the payoff: right after the punchline line ends ("Is that true?", "could you imagine I died fully?",
  "Here it is."), in a pause (speech score 0.00-0.01), with something on screen at the same time (a meme caption,
  a scream reaction, a blur transition, big flash words with a cut).
- Level: about 0 to +10 dB vs the clip's median.
- **When not to:** never over unbroken talking (no reference does it); never on game action that already has its
  own loud sound (gunfire, explosions, sirens are game audio, not edits); not on the title.
- Tool rule: one stinger per clip, on the latest beat that has a real pause (at least 0.25 s, 14 dB down) within
  1.5 s after it, played at the start of that pause. No beat with a pause = no stinger. (A 0.12 s minimum let
  a breath between "YOU" and "$500" count, and the air horn was buried under "$500": YAMNet score 0.02.)
- The sound fits the clip's kind: library tags carry the moment kinds (funny, rage, reaction, story).

**Music**
- Compilations and list videos carry background music. Clearly added: Jynxzi "1. Who tf is this" and the
  Talking Ben memes. The RDR2 references (RDR "so intense", Dutch, Emiru, Roger Clark) may be the game's own
  score, so `tense-bed` (from RDR "so intense") may be game music. "Top 5 GTA RP" was cars, not music.
- "Loud in the gaps, lower under talking" is unconfirmed: YAMNet cannot hear music under speech at all.
  The tool's ducking was measured on the music track alone instead: threshold 0.06 gives about 7 dB down under
  talking and within ~2 dB of full level in the gaps.
- Single-moment reaction clips (Adin, Cyberpunk, GTA bros, CaseOh cow) have none.
- Calm build-up: "Emiru's Moment Of Peace" has music for 0.5-10.6 s, then it stops before the twist. Not
  confirmed whether this is game music.
- Tool rule: music is off by default (the tool makes single-moment clips). When it is turned on, it plays under
  the whole clip, ducked under speech, and fades out at the payoff. **Music is manual for now** (`--music`): the
  tool has no compilation/montage mode to switch it on by itself (`--cut` in the spec is not built).
- Beds are looped, so they carry only 20 ms edge fades (longer baked fades made the 2.9 s bed dip to -39 dB
  every loop).
- YAMNet "Music" can be game or radio music, so music cut from the references stays "unconfirmed" until the user
  listens. Clean music under talking needs source separation (a multi-GB torch download); not done.

## Decisions already made (don't re-ask)
- **Own lightweight skill, not built on SupoClip.** Borrow ideas only, because SupoClip is AGPL and a full web platform.
- **Input:** the user provides the links/files. No auto-find (`--find` was dropped).
- **Output:** 9:16 reels, ready to post. The user posts; never post for them.
- **Captions:** English only (`caption_languages: ["en"]`). Other languages get the hook bar and no captions.
- **Transcripts:** YouTube's own captions when the video has them; local Whisper tiny otherwise.
- **Session mode:** the voice is already in the recording, so no AI voiceover. The user records with OBS.
- **Gameplay:** support both single clips and montages (`--cut single|montage|auto`).
- **Reverse-engineering plugin (morluto/rea):** reviewed and NOT installed (it's for binaries, not needed here).

## Environment facts (this PC)
- Windows 11, i7-7500U (2 cores, 4 threads), 8 GB RAM (often ~1.3 GB free). Disk is tight: C: about 4.5 GB free,
  E: about 5 GB.
- Tools: ffmpeg 9 (winget Gyan), Python 3.12, uv, node (`E:\New Folder\node.exe`). Hardware encoder: `h264_qsv`.
- Private env: `~/.clipper/venv` (uv needs `--no-cache` here, or it fails with "too many temporary files").
  Models: `~/.clipper/models/yunet.onnx` and Whisper tiny/base in the HF cache.
  `~/.clipper/models/yamnet/` (yamnet.onnx + yamnet.data + labels.txt): Qualcomm's MIT export from
  huggingface.co/qualcomm/YamNet v0.66.0, `yamnet-onnx-float.zip`, sha256 cf08212c...d309. Input is a
  1x1x96x64 log-mel patch (frontend is in `sound_events.py`); output is 521 logits.
- Git: repo `github.com/Umer-Hayatt/Clipper`, branch `main`. `.gitignore` is an allowlist (only `clipper/` and the
  three design docs); videos, clip outputs, backups and models stay local.
- `yt-dlp[default]` is required (EJS challenge solver), otherwise YouTube "signature solving failed".
- The `claude` CLI is logged in. Picking uses `claude -p --no-session-persistence --tools "" --strict-mcp-config
  --system-prompt ...` (lean, to save usage), with a fallback to plain `claude -p`.
- Performance lessons:
  - Whisper tiny runs at about real time on this CPU.
  - OpenCV frame seeking on AV1 took minutes per clip, so frames are now sampled with one ffmpeg pass.
  - Downloads prefer H.264.
- Whisper tiny is poor at Urdu/Hindi (with no hint it detects "hi" and writes garbled English). Use `--language ur`.

## Open questions from the spec (section 11)
These are now tracked as decisions 5-7 in `OVERHAUL_PLAN.md` section 5, each with the stage it blocks.
- Is the comments-style rule OK (real chat when available, otherwise reaction stickers, never fake comments)?
  It is built that way now. The look is our own design; the user hasn't reviewed a clip yet.
- Is the Meet workshop really the session-mode source the user wants, or will they record their own build sessions?
- Is "explainer" the right name for the YouTube talk/info mode?
