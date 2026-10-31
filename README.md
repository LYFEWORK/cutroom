# Cutroom

Footage in, finished videos out, in your own look. A video-editing system for short-form video that runs inside Claude Code on a Mac: it organizes your footage, lets you keep or cut every clip, cuts your talking heads without clipping a word, captions them in styles you designed, lays in b-roll, a hook card, music and an end card, checks the result against a hard set of quality gates, and cleans up after itself.

Nothing posts anywhere. You approve the cut and the captions before anything renders. The core runs on the Mac with no paid service.

## Install (three steps)

1. Get the repo. It is public: no account or sign-in is needed. Git comes with Apple's command-line tools, which the install checks for.
2. Open Claude Code (on Opus 5.5) and paste:

   > Clone https://github.com/LYFEWORK/cutroom to ~/cutroom (or `git pull` it if it is already there), then read ~/cutroom/INSTALL.md and follow it exactly.

   Claude installs the tools (about 6 GB, 10 to 20 minutes), links the skills and runs the doctor. It is finished when the doctor's last line says `0 failed`.
3. Start a new Claude Code session and type `/cutroom-setup`. The wizard builds your look, your caption styles, your formats and your folders with you, from questions and from reference videos you paste in.

The full guide, with what to gather before you start and how a video gets made: `docs/GUIDE.pdf`.

## What is in the box

| Folder | What |
|---|---|
| `skills/` | eight Claude Code skills: `cutroom` (the front door), `cutroom-setup`, `cutroom-intake`, `cutroom-review`, `cutroom-edit`, `cutroom-captions`, `cutroom-study`, `cutroom-storage` |
| `engine/` | the mechanics in Python: word timing, the cutter, face tracking, the caption planner and gate, QC gates, sound, the footage organizer, the review page, reference study, cleanup, storage |
| `remotion/` | the renderer: the `Edit` composition (cut, inserts, hook card, graphics, end card), `Montage`, and `Captions` |
| `rules/` | the craft rulebook every skill reads: cutting, hook, captions, b-roll, graphics, sound, color, delivery, review, footage, storage, formats, QC gates |
| `templates/` | the neutral DNA, starter caption styles, the schema |
| `docs/` | the guide (PDF and source: install, setup, day to day, quality, storage, troubleshooting, reference) and the architecture page |
| `tests/` | pytest on synthetic media, plus the renderer typecheck |

Your own material never lives here. It lives in your workspace (default `~/Movies/Cutroom`): `Drop/ Footage/ Projects/ Finished/ Brand/ Review/ Archive/`. An update (`git -C ~/cutroom pull && bash ~/cutroom/install.sh`) never touches it.

## Requirements

- macOS 14 or newer, Apple Silicon recommended (face tracking and the person matte run on Apple Vision; encodes on VideoToolbox).
- Claude Code 2.x with the main session on Opus 5.5.
- About 12 GB of disk for the toolchain, network for the install.
- Optional, off by default: a Deepgram key (verbatim transcripts), ElevenLabs or Epidemic Sound (sounds and music), fal (AI b-roll of objects and places). Keys live in `~/.cutroom/keys.env`, never in this repo.
- Remotion (the renderer) is free for individuals and companies of up to three people; a larger company needs Remotion's company license.

## Health and tests

```bash
bash ~/cutroom/bin/doctor.sh            # proves the stack; exit 1 on anything the pipeline needs
python3 -m pytest ~/cutroom/tests -q    # the engine's tests on synthetic media
cd ~/cutroom/remotion && npm run typecheck
bash ~/cutroom/check/clean.sh           # the repo carries no one else's brand
```
