# footage: a card dump in, an organized shoot out

This module turns a folder of camera files into one organized shoot folder: interview answers filed by question with take numbers, B-roll filed by what is on screen, every file named for what it shows or says, a self-contained `START HERE.html` index that opens on a phone, and (when asked) every take and blooper of a scripted shoot cut into its own file. The camera originals are moved, never re-encoded, and one command puts them back byte for byte.

Two steps are done by Claude reading images and writing JSON, not by code: looking at the contact sheets to choose the B-roll folders, and classifying every clip. Everything else is a script. `intake.py` runs the scripts in order and stops, in one plain line, exactly where Claude has to act.

## Needs

`ffmpeg` and `ffprobe`, `whisper-cli` (whisper.cpp) with the model named by `whisper_model` in `~/.cutroom/config.json`, Python 3.12 or newer, and Pillow. `h264_videotoolbox` is used for take cuts when this Mac has it, `libx264` otherwise. Nothing else. The workspace (`Drop/`, `Footage/`, ...) comes from `engine/common.py`.

## The stages

| # | Stage | Who | Command |
|---|---|---|---|
| 1 | Make the shoot folder, take the files in, prep (probe, audio levels, whisper transcripts, frames, contact sheets, thumbnails) | script | `intake.py start <folder>` |
| 2 | Read at least 5 contact sheets and choose the B-roll folders | Claude | `intake.py subjects <shoot> --set "A,B,C,Other"` |
| 3 | Classify every clip into `.work/results/<clip>.final.json` | Claude, by `prompts/classify-batch.md` (or `classify.js` on the workflow tool) | |
| 4 | Merge disputed records, plan, file the clips, write the index, run the QC | script | `intake.py apply <shoot>` |
| 5 | Word timing for the interview clips | script | `intake.py takes <shoot>` |
| 6 | Mark every take and blooper into `.work/results/<clip>.segments.json` | Claude, by `prompts/segment.md` | |
| 7 | Cut every take and blooper into its own file, index again, QC | script | `intake.py takes <shoot> --cut [--handle 5]` |
| 8 | A notes `.txt` beside every video | script | `intake.py notes <shoot>` |
| | What stage is the shoot at, what is missing | script | `intake.py status <shoot> [--json]` |
| | Undo | script | `intake.py reverse <shoot>` |
| | Prep again (after a stop in `start`) | script | `intake.py prep <shoot>` |

`<shoot>` is the shoot folder, or its name under `<workspace>/Footage` (a part of the name is enough when it matches one shoot). Stages 5 to 8 are for shoots where takes matter (every scripted shoot, and an interview where each answer was tried several times); an ordinary interview card stops after stage 4.

`start` options: `--name "Shoot Name"` (default: the folder's name), `--date YYYY-MM-DD` (default: the day the first clip was recorded), `--type interview|ad` (`ad` = the person reads a written script line by line), `--questions FILE` (a `.json` list, or a text file with one question or script line per row: `Q01 Title: "the question" Follow-ups: a; b`, or a plain list), `--context "..."`, `--people "..."`.

Exit codes: 0 done, 1 the QC found a problem, 2 stopped with a reason. Every stop is one line starting `STOP:`, never a stack trace (`CUTROOM_DEBUG=1` shows the trace of an unexpected error).

## What the module guarantees

- **Nothing is deleted that it did not make.** Generated files are the index, the two manifests, the README, `Edit/plan.json`, the notes files (recognised by their first line), the cuts, and the folders that held them. The only files it ever deletes from a folder it did not make are the operating system's own junk (`.DS_Store`, `._name`, `Thumbs.db`, `desktop.ini`).
- **The plan, the manifest and the README are written before the first file moves**, so any crash can be reversed from them. A move that fails half way, or is stopped with Ctrl-C, is rolled back (renames renamed back, copies deleted).
- **Every move is a rename on one volume.** A destination that already exists, or two sidecars that would land on one name, refuses the whole run before anything moves.
- **A card on another volume is copied, not moved.** `start` and `apply` copy each file beside its destination under a `.part` name, flush it, check it against the original (size and the sha256 of the first and last MiB), then rename it into place. The card is left exactly as it was, and one line says it can be ejected. Free space is checked first.
- **`reverse` restores the camera files byte for byte**, then removes only what was generated. A file it did not make stays, and its folder is named. It refuses when the source folder already holds a file of the same name, and a manifest that names a path outside the shoot cannot make it move or delete anything there. After a copy from another volume it files the copies back in `<shoot>/RAW`, because the originals never left the card.
- **Takes are the only re-encoded files.** The camera originals are never cut: they move to `Raw Clips/`.

## The shoot folder

```
Footage/<YYYY-MM-DD Name>/
  RAW/                      the camera files, until they are organized (removed when empty; a photo, a voice memo or a file prep could not read stays here)
  job.json                  the shoot's settings (below)
  .work/                    what the run learned: transcripts, frames, contact sheets, records, plan.json (keep it to cut takes or rewrite notes later)
  Interview/<Q01 Title>/    <clip> - <Q01> - <name> - Take n.MP4      (no question list: Interview/<Topic>/<clip> - <name>)
  Interview/Bonus Answers/  <clip> - Bonus - <name>.MP4
  B-Roll/<Subject>/         <clip> - <what is on screen>[ - No Audio | - Candid Talk].MP4
  Off Topic/  Unusable/  Camera Sidecars/
  Edit/plan.json            the applied plan, kept so QC and reverse work after .work is gone
  START HERE.html  Footage Manifest.csv  README.txt
  after takes:  Interview/<Q01 Title>/<clip>-NN - Q01 - Take n - Clean|Usable|Flub - <note>.mov
                B-Roll/Bloopers/<clip>-NN - Blooper - Q01 - <note>.mov
                Bad Takes/    every flubbed take on an ad shoot (job.bad_takes_folder)
                Raw Clips/    the untouched camera files
                Takes Manifest.csv
  after notes:  <video name>.txt beside every video
```

A clip name keeps its original name as the first part of its file name, and it may hold spaces. Names are cleaned of `/ \ : * ? " < > | # %` and commas, and a long descriptive part gives way before the take number or the extension does (140 characters at most). A folder that no clip needed is not made.

## File contracts

### job.json

| Key | Meaning |
|---|---|
| `name` | the shoot's name (default: the folder's name without its date) |
| `root` | the shoot folder (default: the folder `job.json` sits in; a `job.json` inside a shoot folder always means that folder, so a renamed, moved or copied shoot works) |
| `src` | where the camera files are before they are organized (default `<ROOT>/RAW`) |
| `scratch` | where the run keeps what it learns (default `<ROOT>/.work`) |
| `shoot_date`, `camera_label`, `context`, `people`, `shot_list`, `notes` | text the classifier reads (`people` is a sentence, or `{interviewer, subject, extras}`) |
| `shoot_type` | `"interview"` or `"ad"` (required) |
| `questions` | `[{id, title, question, followups, hints}]`. Ids are letters, digits, dot, dash and underscore, unique, never `BONUS` or `NONE`. Empty: interviews are grouped by topic. On an ad shoot these are the script lines |
| `subjects` | the B-roll folders, set by `intake.py subjects` (`Other` is always one) |
| `subject_overrides` | `{clip: folder}`, beats the record |
| `bad_takes_folder` | one plain folder name; default `"Bad Takes"` on an ad shoot, `""` on an interview shoot (flubs stay in their line's folder) |
| `brand` | `{accent, ink, font_display}` for `START HERE.html`, all optional: `#4F8CFF`, `#0B0C10` (a dark colour), `system-ui` |
| `whisper_model`, `whisper_language` | default to `~/.cutroom/config.json` (`"en"`, or `"auto"`) |

`<ROOT>` in `src`, `scratch` or `whisper_model` is replaced by the shoot folder. A placeholder left in `root`, `src`, `scratch` or `name` stops the run.

### The classifier record: `.work/results/<clip>.final.json`

`<clip>` is the file name without its extension. All fields are required:

```json
{"clip": "C0001", "category": "interview", "reel": "Q02", "reel_title": "Why Our Process Takes Three Days",
 "question_asked": "...", "answer_summary": "...", "key_quote": "verbatim, 20 words at most", "on_screen": "who, doing what, where",
 "shot": "medium", "camera": "static", "subject_folder": "Workshop Bench", "subject_other": "", "people": "one woman",
 "name": "Why Our Process Takes Three Days", "flags": [], "usable": true, "confidence": 0.9, "notes": ""}
```

`category`: `interview`, `candid_talk`, `broll_with_sound`, `broll_silent`, `off_topic`, `unusable`. `shot`: `wide`, `medium`, `close_up`, `mixed`. `camera`: `static`, `handheld`, `pan`, `tilt`, `push_in`, `orbit`, `mixed`. `reel`: a question id, `BONUS` or `NONE`. `flags`: `profanity`, `confidential`, `false_start`, `cut_off_early`, `shaky`, `out_of_focus`, `interviewer_audible`, `scripted_take`, `freestyle_take`, `multiple_topics`, `staff_in_frame`, `phone_in_frame`, `off_topic_content`.

A clip is **verified** when it has a `final.json`, or when it has a `classify.json` and both verdict files. That is the workflow route (`classify.js`): one classifier, two adversarial verifiers writing `<clip>.visual.json` and `<clip>.content.json` (`{clip, agree, issues[], corrections{}, confidence}`), and a judge on a dispute. `merge.py` (run by `apply`) builds the `final.json` of a disputed clip whose judge never landed, by applying the verifiers' field corrections. `apply` stops, naming every clip, when one has no record or no verifier; a category that is not one of the six stops it too. A `subject_folder` in another case is the same folder, and one that is not on the list files under its own name.

### The segments file: `.work/results/<clip>.segments.json`

```json
{"clip": "C0007", "segments": [
  {"start": 0.0, "end": 1.5, "kind": "take", "line": "Q01", "quality": "flub", "note": "stops after three words"},
  {"start": 2.4, "end": 6.3, "kind": "take", "line": "Q01", "quality": "clean", "note": ""},
  {"start": 0.0, "end": 1.9, "kind": "blooper", "line": "Q01", "note": "false start"}]}
```

Seconds into the clip. `quality` is `clean`, `usable` or `flub` and is for takes only. A take whose `line` is not in `job.questions` is refused, naming it. A mistake in the file stops with one line naming the file and the segment.

### plan.json (`.work/plan.json`, and `Edit/plan.json` once applied)

`{job, records[], moves[{clip, rel, dst}], applied, mode ("move" or "copy"), src, left_alone[], pre_move_bytes, sidecars{clip: [names]}, organized_at}`. A file ffprobe could not read is in `left_alone` and is never touched.

### The tables

`Footage Manifest.csv`, one row per clip: `clip, new_path, original, category, reel, reel_title, subject_folder, duration_s, audio, name, question_asked, answer_summary, key_quote, on_screen, people, shot, camera, flags, usable, confidence, visual_agree, content_agree, source, notes`. `original` is `RAW/<file name>`; `new_path` is inside the shoot (and follows a clip into `Raw Clips/` after takes). `flags` are joined with `;`.

`Takes Manifest.csv`, one row per cut: `file, source_clip, raw_path, kind, line, take, quality, in_s, out_s, spoken_from_s, spoken_to_s, note`. `in_s` and `out_s` are the cut's span in the raw clip, handles included; `spoken_from_s` and `spoken_to_s` are the speech inside it.

`.work/probe.tsv`: `clip, duration_s, width, height, fps, ext, rotation` (width and height as the camera coded them). `.work/evidence.json`: one record per clip with `clip, ext, duration_s, width, height, fps, rotation, orientation, mean_db, max_db, digitally_silent, audio_unreadable, no_speech, transcript, raw_whisper_discarded, sheet, frames_dir`. A clip is digitally silent when its max volume is under -60 dB or it has no audio stream (`audio_unreadable` marks a clip whose audio stream ffmpeg could not decode: it counts as silent, with a warning); a transcript with under 3 letters or digits, or only filler ("Thank you."), is no speech and is kept in `raw_whisper_discarded`.

### The notes file

`<video name>.txt`, first line `CLIP NOTES · <file name>`, then the shoot, date and length (and `cut from <clip> m:ss to m:ss` for a cut), the question or script line, the take number and quality, where the speech sits ("The rest is handle."), what is on screen, the flags, and what is said with `m:ss` times inside that file (a cut is re-timed to the cut). A clip with no speech says `NO SPEECH.` and why. `notes` refuses, before it touches a file, when `.work` cannot rebuild what is on the drive.

## Take cutting

Handles: `takes_handle_s` in the config (5 s), or `--handle`, clamped to the clip edges, never black. Every cut is re-encoded frame-accurately at the clip's native size (a vertical phone clip stays vertical, rotation is honored) to H.264 on `h264_videotoolbox` at 1.3 times the camera clip's own video bitrate (8 Mbit/s floor), PCM audio from the first audio stream (a clip with none is cut without any). Each cut's video bitrate is measured from its packets against the same span of its camera clip; a cut under it is redone at 1.6 times, then with `libx264 -crf 9`; one still under it fails by name, and the run stops before the manifest and before the camera files move. Two takes are cut at once. A rerun keeps a take that is not under its camera clip, cuts a thin one again, and removes the cuts whose name or span changed.

## What this module changes from a plain port

- A card on another volume is copied and checked, not refused (`organize.py`, `intake.py start`).
- The audio map is `-map 0:a:0?`: the first audio stream, and none when there is none. (`-map 0:a?` fails on a phone that records spatial audio beside the normal track, because ffmpeg has no decoder for the second stream.)
- A thumbnail is fitted to the clip's own shape (vertical 240x426, horizontal 426x240, square 320x320) from the probe's size and rotation, and a horizontal clip gets a wide card in the index.
- The run's scratch folder is `<shoot>/.work`, so a later session finds it, and a renamed or copied shoot folder works (`job.json` inside a shoot folder means that folder).
- A `final.json` alone is a verified record; the three-file workflow route is still accepted.
- Contact sheets are capped at about 4,500 pixels tall (long clips sample their frames; every frame stays in `.work/frames/`).
- A file ffprobe cannot read, a photo, a voice memo, a card's system files and a symbolic link are never taken apart or lost: they are named and stay where they are (in the dropped folder, or in `RAW/`).
- A note in a take's file name loses its apostrophes instead of splitting a word ("don't" is "Dont", not "Don T").

## Tests

`python3 -m pytest tests/footage -q` from the repo root. The clips are made at test time (ffmpeg, Pillow, macOS `say`), so nothing is checked in but code; the real whisper model is used (`CUTROOM_TEST_WHISPER_MODEL`, else `~/.cutroom/models`, else `~/whisper-models`), and a test that needs speech skips when it, `whisper-cli` or `say` is missing. `CUTROOM_HOME` is a temporary folder, so the tests never touch `~/.cutroom`. A test of a card on another volume makes a small disk image with `hdiutil` and detaches it. The scripts import each other by plain name, so run this folder's tests on their own, not in one process with another engine folder's tests that has a module of the same name.
