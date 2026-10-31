---
name: cutroom-storage
description: Cleanup and storage for Cutroom. Use after a video is delivered (clear its renders and intermediates), when the user asks how much space the footage takes or what can leave, when the disk is filling up, or to move old shoots to an external SSD. It reports sizes, moves heavy intermediates to the Trash with a receipt, archives shoots with a verified copy, and never deletes an original or a finished video. Triggers - "clean up", "free space", "my disk is full", "archive this shoot", "move old footage to the SSD", "what is taking space", "cleanup after delivery".
---

# Cleanup and storage

Three facts first: nothing here deletes outright (the Trash, then the user empties it); camera originals and finished videos are never touched by a script; every run prints a plan before it changes anything, and the user says go.

## The report

```bash
python3 ~/cutroom/engine/storage.py report
```

Sizes by folder, each shoot with its age, each project with "delivered" or "in progress" and how much of it can leave, review proxies, the Trash holding folder, free disk space, and the suggested archive commands for shoots over 60 days old and over 5 GB. Read it to the user in plain words: what is big, what is safe to clear, what they should decide.

## After a video is delivered

```bash
python3 ~/cutroom/engine/finish.py "<Title>"          # the plan: what would leave, with sizes
python3 ~/cutroom/engine/finish.py "<Title>" --go     # move it to the Trash, write CLEANUP.md
```

It refuses a project whose final is not in `Finished/<Title>/` (nothing is cleaned before it is delivered). It keeps the small files that say what was made (plan, words, gate, script, receipts, sheets) and removes the heavy intermediates (mezzanines, wavs, renders, proxies, mattes, frames). `--go --delete` removes outright when the user wants the space back now. `--all` runs every delivered project. The DNA's `cleanup.after_delivery` says whether `cutroom-edit` runs this with a question (`ask`) or on its own (`auto`); either way the receipt `CLEANUP.md` lands in the project folder.

## Archiving a shoot to the SSD

```bash
python3 ~/cutroom/engine/storage.py archive "<Shoot folder name>" --to "/Volumes/<SSD>/Cutroom Archive"        # plan
python3 ~/cutroom/engine/storage.py archive "<Shoot folder name>" --to "/Volumes/<SSD>/Cutroom Archive" --go   # copy, verify, trash the original
```

Every file is copied and verified by size and content hash before the original goes to the Trash; `ARCHIVED.txt` in the copy says when and from where. Without `--to` it uses the archive folder set in the DNA (`storage.archive`) or config. The review page forgets the archived clips on its next ingest; to keep reviewing archived b-roll, add the archive as a review root in the DNA.

## The monthly routine (what to tell the user)

1. Run the report. 2. `finish.py --all --go` for everything delivered. 3. Archive shoots older than about two months that they will not edit again. 4. Empty the Trash. 5. If the Mac's disk still sits under the DNA's `storage.warn_free_gb`, move the workspace itself to the SSD (`python3 ~/cutroom/engine/common.py set workspace "<new path>"` after moving the folder in Finder).

## Rules

`~/cutroom/rules/storage.md`. Never run a cleanup on a project that is not delivered unless the user says they are dropping it (`--force`).
