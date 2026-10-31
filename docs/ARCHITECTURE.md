# Architecture

Cutroom is three layers: skills (what Claude reads to run a job), the engine (the mechanics as scripts), and the renderer (Remotion). The user's own material lives in a workspace outside the repo.

```
~/cutroom/                      the repo (this)                 <workspace>/              the user's data
  skills/   eight SKILL.md files, symlinked into ~/.claude/skills   Drop/      a card dump lands here
  rules/    the craft rulebook the skills read                      Footage/   organized shoots, originals moved not re-encoded
  engine/   python + shell: every mechanical step                   Review/    proxies, decisions.json, the local page
  remotion/ the Edit, Montage and Captions compositions             Projects/  one video's working files, cleaned after delivery
  templates/ neutral DNA, schema, starter caption styles, a font     Finished/  the finals with transcript and caption file
  docs/     the guide and this page                                 Brand/     the DNA: dna.json, DNA.md, styles/, assets/, references/, graphics/
  tests/    pytest on synthetic media, renderer typecheck           Archive/   old shoots moved out (or an SSD path)
~/.cutroom/ config.json (paths), keys.env (optional services), venv/ (word timing), models/ (the transcriber's model)
```

## The engine, module by module

| Module | Job | Reads | Writes |
|---|---|---|---|
| `common.py` | config, workspace paths, probe, fingerprint, safe names, atomic JSON, STOP lines, Trash | `~/.cutroom/config.json` | |
| `dna.py` | the brand DNA: load, fill defaults, validate, the caption profile and renderer theme views, style loading with `set` and `look`, folder scan for colours and fonts, init and check | `Brand/dna.json`, `Brand/styles/`, `templates/` | `Brand/dna.json`, `DNA.md` |
| `footage/` | the organizer: probe and transcribe a dump, contact sheets, the classification contract Claude fills, plan and move with rollback, the index page, QC, word timing, take cutting with handles, per-clip notes | `Drop/`, `job.json` | `Footage/<shoot>/` |
| `review/` | inventory by content fingerprint, proxies, posters and filmstrips, the page, the local server with Range support and a save endpoint, `approved` (the kept list and the check), `gate` (an edit plan against the kept list), `apply` (trims as verified stream copies, cuts to the Trash) | `Footage/` | `Review/` |
| `words.py` | transcription and word timing: whisper.cpp segments, WhisperX alignment in the venv, fillers flagged | audio or video | `words.json` |
| `cut.py`, `script.py` | the cutter: plan to cut with every edge on the voice's own onset and decay, the joints ladder, the EDL and QC card, the script gate (SCRIPT.md strikes, whole words only) | a source clip, `words.json`, `plan.json` | the cut, EDL, QC, SCRIPT.md |
| `face.py`, `matte.py`, `reframe.py` | Apple Vision face boxes per second, room tiers, face lock, the person matte, a face-centred 9:16 crop of a landscape source | the mezzanine | JSON, mattes |
| `prep.py`, `avskew.py`, `hdr.py` | mezzanine, conform of stalled phone captures, HDR to SDR through the Apple helper | a source | `mez/`, `wx/`, `tx/` |
| `sound.py` | the voice master (-16 LUFS, -2 dBTP), downmix, the music bed envelope under the voice, the sound-effect budget, a synthesized click kit | wavs | wavs |
| `qc.py` | the gates: speech-only pieces, picture equals sound, clipped words, caption ink in the box, caption events, loudness, colour tags, the human strip and sheet | a final, its sources | `reel-qc.json`, evidence |
| `sidecars.py` | Transcript.md and Caption.txt beside every final, and the delivery check | a final, words | the two files |
| `run_take.sh` | one talking-head take through prep, words, face, plan, the script gate, the cut and ladder, the master, QC and sidecars | a take | the mastered cut |
| `join.py` | several takes to one working clip, conformed, with a parts map | takes | one clip |
| `render.py` | an `edit.json` to a rendered video: conform the inserts, link the brand assets, render the Edit composition, mix the bed and effects under the voice, normalise, tag, verify | `edit.json` | the edit video |
| `captions/` | the caption chain: intake, conform, the look, words, face, room, spec, plan, devices, pacing, legibility, GATE.md, matte, render, remux, QC, deliver; styles; a style preview | a final cut, the DNA | the captioned final with sidecars |
| `study.py` | a reference link or file to numbers, boards, moment strips, a transcript and a breakdown template | a URL or file | `Brand/references/<slug>/` |
| `finish.py` | cleanup after delivery with a receipt, Trash by default | a project | `CLEANUP.md` |
| `storage.py` | the storage report and a verified archive move | the workspace | |
| `swift/` | the Apple Vision and VideoToolbox helpers as sources, built on install: face rectangles, face landmarks, person mask, person matte, on-screen text, HDR to SDR | | binaries (never committed) |

Every script answers a bad call with one `STOP:` line and exit 2, writes nothing half-done (atomic writes), and resumes where it stopped when run again.

## The renderer

One Remotion project with three compositions. `Edit` plays one pre-cut file and layers emphasis zooms, b-roll inserts (cut, window, picture-in-picture, with transitions), the hook card, the brand mark, the user's graphics components, and the end card; its layout is derived from the output size so 9:16, 16:9 and 4:5 work. `Montage` layers clips for a story or b-roll piece. `Captions` draws the caption planner's events (clusters with a hero row, or soft-build thoughts, with the devices: count-up, strike, pins, icons) over a finished cut and loads the user's fonts or stops. Music and sound effects are mixed by python after the render; a Remotion render never mixes audio. Cutting is ffmpeg, never Remotion.

The user's graphics live in `Brand/graphics/` as components registered by id in the DNA; the repo ships two neutral examples (`LowerThird`, `TitleCard`).

## The DNA

`Brand/dna.json` is one closed-object document (a typo fails the check) with sections for identity, colours, fonts, logo, the caption preset, feed captions, the hook, formats, motion, graphics, b-roll, music, sound, words, grade, output, delivery, review, cleanup, storage, pacing, studies and notes. `Brand/DNA.md` is the judgment in words. `Brand/styles/<id>.json` are caption styles: `set` (layout and motion over the preset) and an optional `look` (colours and font slot). `dna.py` fills optional keys from the neutral template, never identity keys, so nothing in the repo ever leaks a look into a user's brand.

## Approvals and gates

Three human stops: `SCRIPT.md` (which lines stay in the cut), the edit plan (hook, inserts, graphics, music; shown before the render), `GATE.md` (the caption words, hero words, tier, zone, devices). Every stop is a text file the user can edit. After the render, the gates run; a FIX stops delivery and names the second and the gate, a CHECK needs an answer in words. The kept-clips rule is enforced by `review/approved.py` and `review/gate.py`, not by convention.

## Platform

macOS only: Apple Vision (face, person, text), VideoToolbox (fast decode and encode, HDR tone mapping), the Trash. Python 3.12 or newer for the engine, a Python 3.12 venv for word timing (torch), Node 18 or newer for the renderer, ffmpeg 7 or newer (the cutter uses `-/filter_complex`). No paid service in the core; `~/.cutroom/keys.env` turns optional ones on.
