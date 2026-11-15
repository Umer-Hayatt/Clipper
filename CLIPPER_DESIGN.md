# Clipper: design spec (draft for review)

A Claude Code skill. You provide a link or a local video file; you get back finished
9:16 clips plus a `POST.md` (caption and hashtags per clip). Everything runs on your
own computer (Windows first). Picking moments uses your Claude subscription through
`claude -p`, so there are no API keys and no per-clip cost.

Status: **build steps 1 and 2 of section 9 are built** (engine, streamer mode, captions
and comments styles), followed by a quality overhaul of the look, the writing and the
speed (2026-11-14). `HANDOFF.md` has the current state and `OVERHAUL_PLAN.md` the plan
for what was changed and what comes next. Where this spec and the code differ, the notes
marked "As built" below are right.

---

## 1. Goals

- **MVP:** links or files go in, edited clips ready to post come out. You only review
  them and post them.
- **One engine** with plug-ins, so new games, footage types and editing styles can be
  added without rewriting it.
- **Low token use.** Most of the work (transcripts, loudness, scene changes, face and
  OCR detection) runs locally. Claude only sees compact text, plus a few frames when
  it has to.
- **Windows-native.** PowerShell or Python launcher, no Mac-only tools; NVIDIA/Intel
  hardware encoding where available.

Out of scope for the MVP: auto-finding streams (`--find`), auto-posting, any web UI.

---

## 2. The command

```
clip <link-or-file> [more...] [options]
```

| option | values | default |
|---|---|---|
| `--mode` | `streamer` · `gameplay` · `explainer` · `session` · `auto` | `auto` |
| `--cut` | `single` · `montage` · `auto` | `auto` |
| `--style` | `comments` · `captions` · `session-edit` | depends on the mode |
| `--profile` | `gta6` · `general` (later: one file per game) | `general` |
| `--clips N` | clips per video | 3 |
| `--section A-B` | only this part (`h:mm:ss-h:mm:ss`) | whole video |
| `--crop` | `auto` · `x,y,w,h` | `auto`, finds the useful region (e.g. the shared screen inside a Meet recording) |
| `--model` | Claude model used for picking | your default |
| `--cookies` | browser name, for age-restricted YouTube and Kick | off |

Output: `~/clips/<date>/<source>__<title>/` with `clip_01.mp4 ...`, `POST.md`,
`clips.json` and `report.json`. Re-running the same command resumes; finished steps
are cached.

---

## 3. Pipeline

```
 input ─► fetch ─► detect mode ─► find candidates ─► Claude picks ─► cut ─► render ─► POST.md
 (link/   (audio   (speech? face?  (mode plug-in,     (profile        (single/  (style
  file)   first)   screen/IDE?)    all local)          prompt)         montage)  plug-in)
```

1. **Fetch.** For links: download the audio only, then just the minutes of video that
   become clips (same approach as gta6-clipper). Local files are read in place and
   never modified.
2. **Detect mode** (when `--mode auto`), from cheap checks on about 20 sampled frames
   plus the audio: is there speech, is there a facecam, does the screen look like
   code or a chat UI (OCR), how much motion is there.
3. **Find candidates.** Each mode collects local signals into a short text timeline
   (section 4).
4. **Claude picks.** The timeline goes to Claude in chunks, together with the
   profile's "what makes a great moment" text. Claude returns JSON: time ranges
   (several ranges for a montage), hook text, comments to show, caption, hashtags
   and a score. **As built:** two steps. First only time ranges, kind, score and a
   reason, in windows sized by word count with overlapping context, each cached. Then
   one call writes hook, caption, hashtags and cards for the clips that were kept.
5. **Cut.** Times are snapped to word and scene boundaries. Montage parts are joined
   with quick cuts.
6. **Render.** The style plug-in builds the 9:16 frame; captions are added; loudness
   is normalised to −14 LUFS.
7. **POST.md.** A caption, hashtags and the source timestamp for each clip.

---

## 4. Modes (how candidates are found)

### 4.1 `streamer`: streamer with facecam or voice
- **Signals:** Whisper transcript, loudness spikes, chat speed (YouTube live-chat
  replay, Twitch chat replay) and the **real chat messages** around each moment.
- **Claude sees:** about 8-second transcript lines tagged `[LOUD +9dB]` and
  `[CHAT x4]`, plus the top chat messages for the hottest seconds.
- **Cost:** low to medium, roughly 15–20k tokens per stream hour.

### 4.2 `gameplay`: no commentary
- **Signals (all local):** game-audio peaks (gunfire, explosions, crashes), scene cuts,
  motion bursts, and OCR of on-screen text ("WASTED", "MISSION PASSED", kill feed).
- **Shortlist:** the top ~15 candidate windows by signal score.
- **Claude sees:** 3–4 small frames per shortlisted window, plus their signal tags.
  Vision is used only on the shortlist, which keeps the cost manageable.
- **Cost:** medium, depending on the shortlist size, not on the video length.

### 4.3 `explainer`: someone explaining a game, a topic or news (YouTube style)
- **Signals:** transcript, sentence boundaries, topic shifts, emphasis (loudness and
  pace) and on-screen visuals.
- **Claude looks for:** self-contained segments with a clear hook → explanation →
  payoff. These are the "information" shorts (e.g. "3 hidden things in the GTA 6
  map"). Montage is useful here: several short points stitched into one short.
- **Cost:** medium, since speech is dense.

### 4.4 `session`: screen recording of building with an AI coding tool
- **Signals:**
  - **Prompt moments:** OCR of the AI chat/prompt panel shows text being typed, then
    sent.
  - **Agent working:** spinner or status text ("Working", "Analyzed ..."), or files
    changing in the editor.
  - **Result moments:** a large visual change in a browser or preview window after
    the agent finishes.
  - **Narration:** the presenter's own voice (Whisper).
  - **Claude Code session logs,** when the session ran in Claude Code on this
    machine: exact prompt and finish times. This is optional; it is used only if a
    log overlaps the recording's time window.
- **Claude sees:** a timeline of `PROMPT "…"`, `WORKING 3m10s`, `RESULT (preview
  changed)` and the narration lines.
- **Output shape:** hook (final result, 1–2 s) → prompt zoomed in (3–4 s, typing sped
  up) → "⚡ 3 minutes later" (agent working sped up 10–20×) → result with zoom and pan
  → narration and captions throughout.
- **Cost:** low to medium.

---

## 5. Cut types

- **`single`:** one continuous range, 20–60 s.
- **`montage`:** 3–6 short ranges that tell one story (setup → escalation → payoff,
  or prompt → result → better prompt → final app), joined with quick cuts and zoom
  punches. Claude returns the list of ranges and a one-line story for each.
- **`auto`:** Claude chooses for each moment.

---

## 6. Styles (the editing)

### 6.1 `comments` (MVP): comment bubbles above and below the gameplay
- The gameplay sits in the middle band, with comment cards in the top and bottom
  bands that **pop in when the moment happens**: a slight scale and fade animation,
  1–3 cards visible at a time.
- **The cards look like a real app:** rounded corners, an avatar circle, a bold name
  line, the message text, a like count or heart, and light/dark variants. They are
  designed from scratch and do not copy any platform's logo or trademarks.
- **Where the text comes from:**
  1. **Real chat messages** from the moment (streams with a chat replay). This is the
     default because it is authentic. Usernames are hidden by default (generic
     avatar and handle); real names are an option.
  2. **No chat available** (gameplay, explainer, session): Claude writes
     reaction-style lines, rendered as **captions/reactions, not as fake user
     comments**. Presenting invented comments as real viewer comments risks breaking
     TikTok's and YouTube's rules on misleading content.
- A hook bar at the top and word-by-word captions are optional on top of this.

**As built:** cards fade in with a short slide instead of scaling, one card per zone at
a time, held for as long as the line takes to read (at most 4 s). Usernames are always
hidden and cards carry no like counts; a real-names option was not built. Chat cards are
labelled "viewer · live chat". Zones are worked out from the layout (hook bar, gameplay
band, safe area). Look and sizes come from the `cards` and `typography` settings.

### 6.2 `captions`
A hook title on top, the gameplay or facecam layout, and big word-by-word captions.
This is roughly what gta6-clipper already makes.

**As built:** captions are sentence case by default, at most two balanced lines, in a
fixed position inside the safe area; the spoken word changes colour and nothing moves.
The hook bar wraps evenly, shrinks for long titles and may show one phrase in an accent
colour. Everything (font, sizes, colours, outline, shadow, position, animation) is in
`config.py` under `typography`, `captions`, `hook` and `safe_area`. The words on the
hook, the caption and the hashtags follow `clipper/editorial.md`.

### 6.3 `session-edit`
A smart crop of the wide screen to 9:16 that follows the active area (prompt box,
then the result), with zoom punches, speed ramps, "⚡ X minutes later" cards, whoosh
sound effects, a progress bar and captions.

**Later:** more styles as separate plug-ins (split facecam, Reddit-style story,
meme zoom, and so on).

---

## 7. Profiles (game and topic knowledge)

A profile is a small text file with no code:
- what a great moment is for this game or topic, with categories and examples;
- hook-writing tips and hashtags;
- keywords for detection (e.g. GTA's "WASTED" and "BUSTED" screens).

The MVP ships `gta6.md` and `general.md`. Adding a game means adding one file.

---

## 8. Reuse plan

| From | What | Notes |
|---|---|---|
| gta6-clipper | audio-first download, Whisper transcript, loudness, chat-rate, facecam layout, audio sync, caption renderer, Claude picking loop | Adapted for Windows (it is Mac-only today). No license file was included; it is for personal use. |
| SupoClip (already in this folder) | Ideas only: caption templates, reframing, transitions, layout code; its fonts (OFL, free) | **AGPL-3.0:** copying its code means our tool must also be AGPL if it is ever shared or hosted. It is also a full web platform (Postgres, Redis, Next.js, workers), which is too heavy to run as a skill. |
| New | mode detection, `gameplay` and `session` candidate finders, OCR, montage cutter, `comments` and `session-edit` styles, profiles | |

**Dependencies (Windows):** ffmpeg (already installed), uv/Python 3.12 (installed),
faster-whisper, opencv, an OCR engine (RapidOCR, no extra install), yt-dlp, and node
for YouTube downloads (installed).

---

## 9. Build order

1. **Engine + `streamer` mode + `captions` style, on Windows**, using the gta6 and
   general profiles. A port of what already works, finished before the GTA 6 launch
   on Nov 19.
2. **`comments` style** with real chat messages.
3. **`session` mode + `session-edit` style.** Tested on the Nov 13 recording.
4. **`montage` cut type** for all modes.
5. **`explainer` mode.**
6. **`gameplay` mode** (vision on the shortlist).

Each step ends with real clips you can watch before the next step starts.

---

## 10. Test material on hand

`2026-11-13 18-05-01.mp4`: an OBS recording, 2 h 05 m, 1920×1080, 30 fps, stereo.
- It is a **Google Meet workshop** ("Modern Web Development with AI" slides), with a
  live build in **Antigravity IDE using the Gemini agent** starting around 7:35 PM.
- The shared screen is only part of the frame (Meet's UI is around it), so the
  **auto-crop** is needed.
- **No Claude Code log matches it** (it wasn't Claude Code), so session mode must work
  from video and OCR alone. This is a good test of the hardest path.
- **Privacy:** the frames show participants' names, faces and the in-call chat. Clips
  should crop to the shared screen and blur names. Ask the presenter before posting.
  The narration is the presenter's voice, not yours.

---

## 11. Open questions

1. **Base:** build the lightweight skill described here (recommended), or extend
   SupoClip, which is more polished but is a full web platform under AGPL?
2. **Comments style:** is "real chat when available, otherwise clearly-captions" OK,
   or do you want Claude-written lines styled as comments anyway, accepting the
   platform risk?
3. **Session test:** is the Nov 13 workshop the clip source you actually want, or do
   you plan to record your own build sessions (and in which tool)?
4. **Is "explainer" the right name** for YouTube talk/information videos?
