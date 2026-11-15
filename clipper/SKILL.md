---
name: clipper
description: Turns long videos into ready-to-post 9:16 clips (TikTok, Shorts, Reels). Takes links (YouTube, Twitch, Kick) or video files on this computer, listens to the whole thing locally, has Claude pick the strongest moments (reactions, clutch plays, funny fails, discoveries, hot takes) from the transcript plus loudness and chat spikes, then renders each clip with the facecam on top and gameplay below (or a face-follow / blurred-fit look), a hook title bar and word-by-word captions, plus a POST.md with a caption and hashtags per clip. Profiles tune it per game (gta6, general). Runs on the user's own Claude subscription, no API keys. Use when someone wants to clip a stream, VOD or gaming video, make Shorts/TikToks/Reels from a long video, or says "clip this", "find the best moments", "make clips from this stream".
---

# Clipper

Long video in, ready-to-post vertical clips out. **You run it; the user gives links or files.**
`SKILL` = the folder this file is in. Command: `SKILL/clip.cmd` (Windows) or `bash SKILL/clip` (Git Bash, Mac, Linux).
Output goes to `~/clips/<date>/<source>__<title>/` (or `CLIPPER_HOME`, or `--out`).

## 0. First use on a machine
`python SKILL/scripts/setup.py` until it prints **All set**. It checks ffmpeg, Claude Code, node/deno and disk,
builds a private Python env in `~/.clipper`, and downloads Whisper tiny + base (~220 MB) and the facecam finder.
Install what's missing the free, official way (`winget install Gyan.FFmpeg`, `winget install OpenJS.NodeJS.LTS`).
The `claude` command must be logged in once (`claude` then `/login` in a terminal). That's the user's step:
never type their credentials.

## 1. Run it
Run long jobs in the background and tell the user what's happening in plain words.
```
clip URL                                   # one video, 3 clips, general profile
clip URL --profile gta6 --clips 4          # GTA 6 stream
clip URL URL URL                           # several videos, 2 at a time
clip "D:/recordings/rec.mp4" --section 1:30:00-1:45:00   # a local file, only that part
```
| flag | use |
|---|---|
| `--profile NAME` | `general` (default), `gta6`, or a path to your own `.md` (see `profiles/`) |
| `--clips N` | clips per video (default 3) |
| `--section A-B` | only that part (`h:mm:ss-h:mm:ss`). Use it for long videos and live streams |
| `--language xx` | spoken language (`en`, `ur`, `hi`...). Set it when it isn't English: detection is weak |
| `--cookies chrome` | browser login: age-restricted YouTube, Kick, sub-only Twitch VODs |
| `--style comments/captions` | `comments`: cards that pop in above and below the gameplay. `captions`: word-by-word captions. Default `auto` = comments when the video has a chat replay |
| `--look NAME` | caption look: `clean` (default), `bold`, `boxed`, `seam` (small capitals on the cam/gameplay seam), `punch` (one italic word at a time, changing colours), `keyword` (condensed capitals, key words in green), `serif`, `none` (no captions), or `mix` (a different look per clip, some with no captions) |
| `--title bar/pill/text` | title style: white plate (default), red plate, or plain outlined text with no plate |
| `--beats` | on each clip's peak (Claude picks 0-2), flash a big tilted word with a punch-in zoom |
| `--sfx` | one stinger per clip from the sound library (`~/.clipper/sounds`), picked to fit the clip's kind, on the payoff beat, in the next pause; skipped when the talking does not stop. Needs `--beats`. Empty library: a made-up thud |
| `--music` | a background music bed from the sound library, ducked under talking, fading out at the payoff. The references only use music for compilations, so it is off by default |
| `--layout stream/face/fit` | force a look (default `auto` decides per clip; the comments style uses `fit`) |
| `--model sonnet` | a lighter Claude model for picking, to save usage |
| `--fast` | hardware encoding (NVIDIA / Intel Quick Sync / AMD / Mac). Use it on laptops |
| `--crop auto/x,y,w,h` | local files only: keep only that part of the picture (e.g. the shared screen of a video-call recording). `auto` is a best guess, check `crop_preview.jpg` in the work folder and give x,y,w,h if it is wrong |
| `--fps N` | frames per second of the clips (default 30; `--fps source` keeps a 60 fps video at 60, about twice the render time) |
| `--parallel N` | videos at the same time (default 2; keep 1 on weak PCs) |
| `--redo` | pick the moments again (keeps the transcript) |
| `--rewrite` | write the hooks, captions and hashtags again (after editing `editorial.md`); keeps the picked moments |
| `--out DIR` | where clips and the cache go |

Re-running the same command resumes: finished steps are cached in `<clips>/work/`.

## 2. What happens
1. **Links:** only the audio is downloaded first. **Files** are read in place, never changed.
2. **Listen:** YouTube videos use their own captions (instant, no CPU). Everything else (Twitch, Kick,
   files, videos without captions) gets a local Whisper tiny transcript. Plus loudness per second, and on
   YouTube live replays, chat speed and the chat messages themselves.
3. **Pick:** `claude -p` (no tools, short system prompt, to save usage) reads the transcript with
   `[LOUD +NdB]` / `[CHAT xN]` marks plus the profile and returns only times, a kind, a score and one line
   of why. A video up to about 7,000 spoken words is one call. Longer ones are read in windows of about
   6,000 words (`pick_window_words`), cut at pauses, each with ~90 s of the neighbouring transcript as
   context so moments on an edge aren't lost. Every window's answer is cached on its own
   (`<work>/picks/`): a stopped run resumes, and changing `--clips` costs no new call.
   **Write:** one more call writes the hook, caption and hashtags for the clips that were kept (and, in
   the comments style, the cards), following `SKILL/editorial.md`. A local check then removes emoji and
   filler hashtags and flags phrases listed under "Never use"; anything it flags is shown in the log and
   in `report.json` under `writing_notes`.
4. **Make:** links download only each clip's minutes. The clip is aligned to the audio, framed 9:16 (facecam
   on top + gameplay, follow a big face, or the whole frame on blur), given a hook bar, word-by-word
   captions and loudness at −14 LUFS, at 30 fps unless told otherwise. Captions are made **only for
   English** (`caption_languages` in config): YouTube's word timings when it had captions, else Whisper
   base. Other languages get the hook bar but no captions, because small Whisper models write poor
   non-English text.
   **Comments style:** with a YouTube live-chat replay, the write call also picks 3-6 REAL chat messages
   per clip (copied word for word; anything reworded is dropped). They show as cards with a generic avatar,
   "viewer · live chat" and no like counts. With no chat, Claude writes short reaction lines, drawn as
   dark stickers with no avatar or name, so they can never pass for viewer comments. Never make
   invented lines look like real comments: platforms treat that as misleading content.
5. `POST.md` (caption + hashtags + timestamp per clip), `clips.json`, `report.json`. Every run ends with
   a table: seconds for download, audio processing, transcript, moment selection, caption generation and
   rendering, the slowest one marked, cache hits and misses, and each Claude call. `report.json` has the
   same plus a per-clip breakdown.

## 3. Before the user posts
Open a frame of each clip and check: the right layout, captions readable and in the right language, the hook bar
names the right person. **The user posts; never post for them.** Clip creators who allow clipping.

## 3a. The look and the writing
**Settings:** `<clips>/config.json` takes any key from `scripts/clipper/config.py`. Groups are merged key by
key, so only write what you change:
```json
{"captions": {"highlight_color": "7CF5C4", "case": "upper", "position_y": 0.68},
 "typography": {"family": "Inter", "caption": {"weight": "Black", "size": 72}},
 "hook": {"accent_color": "2F6BE0"}, "cards": {"theme": "dark"}, "output_fps": 30}
```
| group | what it controls |
|---|---|
| `typography` | font family and folder, and a weight + size per role: `caption`, `hook`, `card`, `card_label`, `sticker`. A role also takes `"file"` for one specific font file |
| `captions` | case, words per phrase, lines, width, position, colours, highlight (`color` / `box` / `none`), outline, shadow, animation, punctuation |
| `hook` | position, margins, colours, accent colour, radius, shadow, animation |
| `cards` | light/dark, width, opacity, radius, heart, sticker look, animation |
| `safe_area` | the margins kept clear of the apps' own buttons |
| `writing` | `emoji` (off by default), `max_hashtags`, `hook_max_words` |

Sizes are for a 1080-wide frame and scale with `output_width`. `"caption_preset": "clean" | "bold" | "boxed"`
switches the whole caption look in one line. Fonts are the bundled Inter weights in `SKILL/fonts/` (open
licence); to use another family, put `<Family>-<Weight>.ttf` files (static weights, not variable fonts) in a
folder and set `typography.fonts_dir` and `typography.family`.

**Preview without a full run:** `python SKILL/scripts/specimen.py --out DIR [--frame shot.jpg]
[--style captions|comments|chat] [--set captions.case=upper]` draws the hook, captions and cards over a
still in about 10 seconds, using the same code as the real render.

**The voice:** `SKILL/editorial.md` is the writing guide Claude follows for hooks, captions, hashtags and
reaction lines. The user edits it to change the style over time; a copy at `<clips>/editorial.md` is used
instead when it exists. After editing, run the same command with `--rewrite`.

## 4. Problems
- **"claude command isn't logged in"**: the user runs `claude` in a terminal once and signs in with `/login`.
- **Slow rendering**: the encode is the slowest stage on a laptop. Use `--fast`; keep the default 30 fps.
- **Slow listening**: Whisper runs on the CPU. On a 2-core laptop it is roughly real time (1 h of video
  ≈ 1 h). Use `--section`, close other apps, `--parallel 1`.
- **Non-English video**: pass `--language ur` (or the right code) so Claude gets a better transcript.
  It will have no captions, by design.
- **"Sign in to confirm your age"**, **Kick 403**: `--cookies chrome` (the browser the user is signed into).
- **"sound match weak"**: the clip may be a second off; `--redo` or check that clip.
- **Claude found nothing**: low-energy video; try another section or a lower `--clips`.

## 5. Adding a game
Copy `profiles/general.md` to `profiles/<game>.md`, describe the game and what wins (categories, hashtags),
then use `--profile <game>`.
