---
name: cutroom-intake
description: Footage in. Use when the user dropped a card dump or any folder of clips into Drop/ (or names a folder of raw clips) and wants it organized, with originals moved into a dated shoot folder, every clip named by what is said or shown, interview answers filed by question, b-roll by subject, every take of a scripted read cut into its own file with handles and bad takes in one folder, an index page, and the review page opened for keep/cut. Triggers - "I dropped footage", "organize this footage", "sort these clips", "new shoot", "cut the takes out", "make the footage index", a folder of camera files.
---

# Footage in

A card dump becomes an organized shoot under `<workspace>/Footage/<YYYY-MM-DD Name>/`. Camera originals are moved, never re-encoded, never removed; a take cut out of a clip is a new re-encoded file in its line's folder, and the camera original moves to `Raw Clips/`. Two steps are yours (classifying clips from contact sheets, segmenting takes); the scripts do the rest. Read `~/cutroom/rules/footage.md` before the first run of a session.

Engine: `~/cutroom/engine/footage/`, entry point `intake.py`. Every stop is one plain line.

## 1. Start the shoot

Ask only what the files cannot tell you: the shoot's name, whether it was an interview (answers to questions) or a scripted shoot (lines read in takes), and for an interview the question list (a file or a paste), for a scripted shoot the script lines. Save a pasted list or script to a file: `--questions <file>` takes a `.json` list or a text file with one question or line per row (`Q01 Title: "the question" Follow-ups: a; b`, or a plain list), and it is required for a scripted shoot (`--type ad`) and for any interview whose answers should be cut into takes. Then:

```bash
python3 ~/cutroom/engine/footage/intake.py start "<workspace>/Drop/<folder>" --name "<Shoot Name>" --type interview|ad [--questions <file>] [--context "<what the business is, who is on camera>"] [--people "<who is in front of the camera>"] [--date YYYY-MM-DD]
```

It makes the shoot folder, moves the files into `RAW/` (a card on another volume is copied and verified instead; it then says the card can be ejected), writes `job.json`, probes and transcribes every clip, and makes contact sheets and thumbnails in `.work/`. Then it prints what you do next. `start` takes one path, a folder or one file; for loose files pass `"<workspace>/Drop"` itself with `--name`. `--date` defaults to the first clip's recording day. If prep stops, fix the reason it names and run `intake.py prep "<shoot>"`. Exit codes everywhere: 0 done, 1 the QC listed problems, 2 stopped with one `STOP:` line.

## 2. Classify every clip (your job, from pictures)

1. Read at least five contact sheets in `<shoot>/.work/sheets/` and write the subject folders (8 to 13 plain nouns of what the camera saw, plus Other):
   ```bash
   python3 ~/cutroom/engine/footage/intake.py subjects "<shoot>" --set "Front Counter,Press Room,Street Walk,Hands Close Up,Other"
   ```
2. Classify each clip per `~/cutroom/engine/footage/prompts/classify-batch.md`: read its sheet and transcript, write `<shoot>/.work/results/<clip>.final.json` with every field the prompt names (`clip, category, reel, reel_title, question_asked, answer_summary, key_quote, on_screen, shot, camera, subject_folder, subject_other, people, name, flags, usable, confidence, notes`; `reel` is a question or line id, `BONUS` or `NONE`, and only clips whose `reel` is a listed id get takes cut). Do it in batches of about six clips, and have a second pass look for mistakes on anything you were unsure of (a different reader than the writer). Names say what the clip is, in plain Title Case, 3 to 9 words, with no clip id or question id in them; the script puts the camera's clip id first when it files the clip.

## 3. Apply

```bash
python3 ~/cutroom/engine/footage/intake.py apply "<shoot>"
```

Plan (it refuses with the list if a clip has no record), move (manifest written before the first move, rollback on any failure), index (`START HERE.html`), QC (`QC OK` or one line per problem). Open the index for the user: `open "<shoot>/START HERE.html"`.

## 4. Scripted shoots: cut the takes

```bash
python3 ~/cutroom/engine/footage/intake.py takes "<shoot>"
```

It times every word and stops for you: for each take clip, read `.work/words/<clip>.txt` and the transcript, and write `.work/results/<clip>.segments.json` per `prompts/segment.md` (every attempt is a take, from the first scripted word to where the speaker stops, the stumble that ends it included; a complete read with a small stumble is Usable, never a flub; a laugh or a meltdown is a blooper). Then:

```bash
python3 ~/cutroom/engine/footage/intake.py takes "<shoot>" --cut [--handle SECONDS] [--dry-run]
```

Every take becomes its own file with 5 s of real footage either side (`--handle` changes it, `--dry-run` only lists the cuts), good takes in their line's folder, flubs in `Bad Takes/` on an `ad` shoot (on an `interview` shoot a flub stays in its question's folder, marked Flub in its name), bloopers in `B-Roll/Bloopers/`, the camera originals in `Raw Clips/`, and the index is rebuilt. A take whose `line` is not one of the job's questions is refused by name.

## 5. Notes and status

```bash
python3 ~/cutroom/engine/footage/intake.py notes "<shoot>"     # a .txt beside every video: what it is, what is said with times
python3 ~/cutroom/engine/footage/intake.py status "<shoot>"    # where the shoot is, what is missing
python3 ~/cutroom/engine/footage/intake.py reverse "<shoot>"   # undo: camera files back into RAW/ byte for byte; the cut takes, notes, index and manifests it made are removed
```

## 6. Hand to the review

Every new clip must be reviewed before any edit uses it. Run the `cutroom-review` skill's ingest now and open the page:

```bash
python3 ~/cutroom/engine/review/ingest.py --open
```

(`--open` starts the server and opens the page; `--serve` alone starts the server.) Tell the user: "N new clips are on the review page; keep, cut or trim each one." Nothing from this shoot is placed in a video until that is done.

## What to tell the user when you are done

The shoot folder, the index page, how many clips in each folder, how many takes were cut (and how many went to Bad Takes), anything the QC flagged, and that the review page is open.
