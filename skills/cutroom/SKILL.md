---
name: cutroom
description: The front door of Cutroom, a video-editing system for short-form video. Use when the user asks for anything about their footage, videos, captions, reels, b-roll, review page, brand look, references, renders, cleanup or storage and no other cutroom skill is already named. It explains what the system can do, where things are, and hands the ask to exactly one of the other skills (cutroom-setup, cutroom-intake, cutroom-review, cutroom-edit, cutroom-captions, cutroom-study, cutroom-storage). Triggers - "what can I do", "where is my footage", "help with cutroom", "I dropped footage", "make a video", "caption this", "here is a reel I like", "clean up", "how much space".
---

# Cutroom

Footage in, finished videos out, in your own look. Everything mechanical is a script in `~/cutroom/engine/`; every judgment call is yours, and nothing posts anywhere.

## Where things live

| Thing | Where |
|---|---|
| Your workspace (footage, projects, finals, your look) | the folder named by `workspace` in `~/.cutroom/config.json`, set by `cutroom-setup` (it offers `~/Movies/Cutroom`) |
| Your look (the DNA) | `<workspace>/Brand/dna.json`, `DNA.md`, `styles/`, `assets/`, `references/` |
| New footage goes | `<workspace>/Drop/` |
| Organized shoots | `<workspace>/Footage/<YYYY-MM-DD Name>/` with `START HERE.html` |
| The review page (keep, cut, trim) | `<workspace>/Review/` served at `http://127.0.0.1:8765` |
| One video's working files | `<workspace>/Projects/<Title>/` |
| Finished videos | `<workspace>/Finished/<Title>/`: `<Title>.mp4` with `<Title> - Transcript.md` and `<Title> - Caption.txt` beside it, and `Build/<Title>/` (notes, gate, QC report, evidence) |
| The rules every edit follows | `~/cutroom/rules/` (read the file for the step you are on) |
| Health check | `bash ~/cutroom/bin/doctor.sh` |

## Which skill takes what

| The ask | Skill |
|---|---|
| First run, or changing the look, colors, fonts, caption styles, formats, graphics wishlist, music rules, folders | `cutroom-setup` |
| "I dropped footage", a card dump, a folder of clips, organizing, naming, cutting takes out | `cutroom-intake` |
| Keep, cut, trim clips; "open the review page"; apply the picks; "which clips may I use" | `cutroom-review` |
| Make a video from footage: a talking head, a graphic talking head, a story over b-roll, a montage, a screen walkthrough, a podcast clip | `cutroom-edit` |
| Captions on a cut that is already final | `cutroom-captions` |
| "Here is a reel I like", reference links, "make my captions look like this", study a video | `cutroom-study` |
| Cleanup after a video, free space, archive old shoots, "my disk is full" | `cutroom-storage` |

Pick one row, load that skill, let it run. If the ask spans two (footage just landed and they want a video), run intake, then review, then edit, in that order, and say so.

## Three things that never change

1. **A person approves before anything renders or ships.** The cut stops at `SCRIPT.md`, the captions stop at `GATE.md`, and the edit plan is shown before the render.
2. **Only kept clips are placed.** Every b-roll and take comes from the review page's Keep list, inside its kept range. Unreviewed footage is off limits until it is reviewed.
3. **Move, never delete.** Cleanup goes through the Trash with a receipt. Camera originals are moved into shoot folders, never re-encoded, never removed by a script. Two commands remove files outright, each only on the user's word: `finish.py --go --delete` (a delivered project's intermediates) and `intake.py reverse` (only the files the organizer itself made).

## How the scripts answer

A script that cannot go on prints one `STOP: ...` line and exits 2. Exit 1 means a QC or gate check failed and the lines above say which. `run_take.sh` waiting at `SCRIPT.md` exits 0. Every script needs `python3` 3.12 or newer (the installer checks).

## If something is red

Run `bash ~/cutroom/bin/doctor.sh` and read its lines. The fixes are in `~/cutroom/INSTALL.md`, "If something fails". Update with `git -C ~/cutroom pull && bash ~/cutroom/install.sh`.
