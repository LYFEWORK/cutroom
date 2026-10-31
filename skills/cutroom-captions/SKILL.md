---
name: cutroom-captions
description: Burn captions onto a video that is already cut and mixed to its final form, in one of the user's own caption styles, with a human gate before the render and QC gates after. Use when the user has a finished cut (theirs or from another editor) and wants captions only, or when cutroom-edit reaches its caption step. Never trims, reorders or removes anything from the footage. Triggers - "caption this", "captions on this cut", "burn in captions", "caption these in my style", a dropped finished 9:16 clip to caption.
---

# Captions on a final cut

The caption chain plans every phrase against the words and the face, stops at `GATE.md` for the user, then renders, remuxes the original sound, checks every gate and delivers. It never edits the picture. Read `~/cutroom/rules/captions.md` once per session.

Engine: `~/cutroom/engine/captions/`. Styles: `<workspace>/Brand/styles/` (the user's) and `~/cutroom/templates/styles/` (starters); `python3 ~/cutroom/engine/dna.py styles` lists them; the default is the DNA's `captions.default_style`.

## Pass 1: plan and stop

```bash
bash ~/cutroom/engine/captions/run_captions.sh "<final cut.mp4>" "<workspace>/Projects/<Title>/captions" [--style <id>] [--dest "<workspace>/Finished/<Title>"]
```

Stages: intake (9:16 at 1080x1920 or 2160x3840; an HDR phone export is tone-mapped), conform, the look (DNA plus style), words (local transcription and word timing), face track, room check (how much room the face leaves: FULL, REDUCED, MINIMAL), spec, plan (phrases, hero words, devices), pacing, legibility, then `GATE.md` is written and the run stops with exit 2: "waiting for approval".

## The gate (with the user)

Open `GATE.md`. Show the user the transcript as the plan reads it, the tier and zone with the reason, the hero word of each phrase (starred), the thoughts (soft build: lead-in, payoff, tail; `[bar]` on the one payoff that gets the bar), any CHECK line, and the proposed devices (never applied without a yes). Fix names with `fix:` lines (spelling comes from the user, never from the transcript), move a hero word, drop a device, change the zone or the style with a `style:` line (grammar: `~/cutroom/engine/captions/README.md`, GATE.md grammar). When they are happy, set `approved: yes` and save.

## Pass 2: render, check, deliver

Run the exact same command again. It applies the gate, builds the person matte only if a phrase sits behind the head, renders through Remotion (4K sources render at 2x), remuxes the source audio, runs the QC gates (face on camera, picture equals sound, caption ink inside the safe box and off the face, every spoken word on screen in order, no event under three frames, loudness, color tags, the pacing gate as the DNA sets it), and on a PASS files `<Title>.mp4`, `<Title> - Transcript.md` and `<Title> - Caption.txt` into the destination with a `Build/` folder beside them (notes, gate, QC report, evidence sheets).

Exit 0 delivered; 1 rendered but a gate failed (nothing shipped; the line names the second and the gate; fix in `GATE.md` and run again); 2 stopped before a render (the message says why).

## Write the post caption

If the run prints `CAPTION PENDING`, write the post caption (hook-first, in the speaker's own words, no narration of the picture, one plain ask only if the video earns it) into `<take-dir>/caption.md` and run the same command once more; it replaces the placeholder without re-rendering.

## Batch

```bash
bash ~/cutroom/engine/captions/batch_captions.sh "<folder of cuts>" "<workroot>" [--style <id>] [--dest <folder>]
```

Pass 1 leaves every clip at its gate; pass 2 delivers the approved ones; the summary lists each clip's state.
