# Classify the clips of a shoot

You catalogue raw clips so an editor can find each one by its name, without opening it. Read this whole file before the first clip. It says what to read, what to write, and the rules that decide every field.

`<scratch>` is the shoot's `.work` folder. `intake.py start` and `intake.py subjects` print its path. `<clip>` is a clip's name exactly as it is in `<scratch>/probe.tsv` (the file name without its extension; it may hold spaces).

## What you write

One JSON file per clip, written with the Write tool:

    <scratch>/results/<clip>.final.json

That one file is enough. `intake.py apply` files the clip from it. (The workflow route, `classify.js`, writes `<clip>.classify.json`, `<clip>.visual.json` and `<clip>.content.json` instead and `merge.py` builds the final from them; `apply` accepts either route and never mixes them up.)

Write the file for EVERY clip in `probe.tsv`. `apply` stops and names every clip that has none.

## Before the first clip

1. Read `<scratch>/cheat-sheet.txt` once. It lists the questions (or, on an ad shoot, the script lines) this shoot was meant to cover, each with an id such as `Q01` or `L01`, plus the shot list and any notes.
2. Read `subjects` in `job.json` (one folder up from `<scratch>`). Those are the B-roll folders. `subject_folder` must be one of them, or `Other`.

## For each clip

1. Read `<scratch>/ev/<clip>.json`. It holds the duration, the audio levels, `digitally_silent`, `no_speech`, the `transcript` (empty when the clip is silent or whisper heard no speech), `orientation`, the `sheet` path (a contact sheet image) and the `frames_dir`.
2. Read the contact sheet image at the `sheet` path. Every tile carries its time. When a tile is ambiguous, Read one or two single frames from `frames_dir`.
3. Fill in the record below and write it.

## Many clips: batches

A few clips: do them yourself, one after another. A card with many clips: hand batches of about 6 clips, in shoot order, to subagents (one Agent call per batch, several calls in one message so they run together). Give each agent its clip names, the path of this file and the path of `<scratch>`. Use a model that can read images and do not downgrade it. Each agent writes the final.json files of its clips and replies with one line per clip: `<clip> <category> <reel> "<name>"`.

## The record

All fields are required. Types matter: `flags` is a list, `usable` is true or false, `confidence` is a number.

    {
      "clip": "C0001",
      "category": "interview",
      "reel": "Q02",
      "reel_title": "Why Our Process Takes Three Days",
      "question_asked": "Why does the process take three days?",
      "answer_summary": "Each batch has to cure overnight, so rushing it ruins the finish.",
      "key_quote": "it has to cure overnight",
      "on_screen": "A woman in a green apron at a workshop bench, speaking to camera, shelves of jars behind her.",
      "shot": "medium",
      "camera": "static",
      "subject_folder": "Workshop Bench",
      "subject_other": "",
      "people": "one woman, the owner",
      "name": "Why Our Process Takes Three Days",
      "flags": [],
      "usable": true,
      "confidence": 0.9,
      "notes": ""
    }

Allowed values:

- `category`: `interview`, `candid_talk`, `broll_with_sound`, `broll_silent`, `off_topic`, `unusable`
- `shot`: `wide`, `medium`, `close_up`, `mixed`
- `camera`: `static`, `handheld`, `pan`, `tilt`, `push_in`, `orbit`, `mixed`
- `subject_folder`: one of the job's `subjects`, or `Other`

## Rules

- `digitally_silent` true means the camera recorded no audio (max volume below -60 dB). `no_speech` true means whisper heard nothing real (hum, room tone). Any text in `raw_whisper_discarded` is a whisper hallucination ("Thank you.", "Transcription by CastingWords", "."): ignore it completely. Silent clips are `broll_silent` unless they are `off_topic` or `unusable`. Clips with sound and no speech are `broll_with_sound` unless they are `off_topic` or `unusable`.
- `audio_unreadable` true means the file has an audio stream the tools could not decode, so it counts as silent in the evidence but may not be. Classify it from the picture, say so in `notes`, and lower `confidence`.
- `category`:
  - `interview`: the subject (or anyone) answering a question or speaking to camera with intent, including scripted intros and outros and walk-and-talk explanations.
  - `candid_talk`: real conversation on camera that is not an interview answer (troubleshooting, chit-chat, "keep going", directions).
  - `broll_with_sound`: a visual clip with ambient sound and no meaningful speech.
  - `broll_silent`: no audio at all.
  - `off_topic`: clearly a different subject or place than this shoot is about (another shoot on the same card, a personal clip).
  - `unusable`: genuinely nothing usable (black, accidental and under 2 seconds, lens cap). Be strict: almost everything is usable.
- `reel`: with a question list, map each interview answer to a question id from the cheat sheet by topic, even when the subject freestyles or the interviewer words it differently. An on-camera answer that matches none is `BONUS`, with a short `reel_title` you invent for its topic (reuse the same words for clips on the same topic). With no question list, every interview answer is `BONUS` with a `reel_title` naming its topic. Every clip that is not an interview answer: `reel` is `NONE` and `reel_title` is `""`.
- `question_asked`: quote what the interviewer actually asks, from the transcript. If the interviewer is not audible, infer the question from the answer and end it with " (inferred)".
- `answer_summary`: one or two plain sentences of what was said. For a clip that is not an answer, what happens in it.
- `key_quote`: one verbatim soundbite from the transcript, 20 words at most, or `""`.
- `name`: 3 to 9 words, Title Case, plain English an editor would search for. No clip ids, no question ids. Interview: the topic plus what sets this take apart ("Why Our Process Takes Three Days", "Welcome Intro Freestyle Take"). B-roll: what is on screen, subject first, specific ("Barista Steaming Milk Close Up", "Pouring Coffee Into Paper Cup"). Never a generic name like "Shop Footage" or "B-Roll Clip". Two clips must never get the same name: say what differs (angle, distance, take).
- `on_screen`: concrete. Who, doing what, with which tool or object, where, colors, visible text or logos. Say if a phone, the interviewer or a cheat sheet is visible.
- `shot` and `camera`: judge them from the timestamped tiles. A change of framing across the tiles is camera movement.
- `subject_folder`: where or what the clip shows, from the fixed list. Interview clips get one too (the place they were filmed). Use `Other` plus `subject_other` (a short folder name) only when nothing fits.
- `flags` (use `[]` for none): `profanity`, `confidential` (a named person or job that should not air, or someone says it cannot be on camera), `false_start`, `cut_off_early` (the answer is clearly cut off at the end of the clip), `shaky`, `out_of_focus`, `interviewer_audible`, `scripted_take`, `freestyle_take`, `multiple_topics`, `staff_in_frame`, `phone_in_frame`, `off_topic_content`.
- `people`: who is in frame. `"nobody"` for hands-only or object-only shots.
- `usable`: false only for a clip that belongs in Unusable.
- `confidence`: 0 to 1. Put anything you were unsure about in `notes`.

## Check yourself before you write

Look at the contact sheet a second time and try to prove your record wrong:

- Does `name` describe what is actually visible, specifically enough that an editor would pick this clip over a similar one?
- Does `on_screen` say who, what and where, or only "a person in a room"?
- Is `category` consistent with the audio facts (a silent clip is never an interview)?
- Is `reel` the best topical match, or does another question fit better?
- Is `key_quote` word for word in the transcript?

Fix the record, then write it. Nothing is filed from a guess: when you cannot tell, say so in `notes` and lower `confidence`.

## When all clips are written

Reply with one line per clip, `<clip> <category> <reel> "<name>"`, then run `intake.py apply <shoot>`.
