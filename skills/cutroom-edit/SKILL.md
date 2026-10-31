---
name: cutroom-edit
description: Make a finished short-form video from kept footage in the user's own look. Use when the user wants a video made, whether a talking head (clean or with graphics), a story over b-roll, a b-roll montage, a screen walkthrough or a podcast clip. It picks the take, cuts it without clipping a word, plans the hook card, b-roll inserts, graphics, music and end card, shows the plan, renders, captions in the user's style, runs the QC gates, delivers with a transcript and caption file, and cleans up. Triggers - "make a video", "edit this take", "turn this into a reel", "cut this down", "make a talking head from the shoot", "edit the interview answer about X".
---

# Make a video

Footage in the shoot folders, reviewed and kept, becomes one finished video in `<workspace>/Finished/<Title>/`. You decide the edit; the scripts do the mechanics; the user approves the cut and the captions before anything renders. Read these once per session: `~/cutroom/rules/cutting.md`, `hook.md`, `broll.md`, `graphics.md`, `sound.md`, `delivery.md`, and `formats.md` for the format at hand. The user's look is `<workspace>/Brand/dna.json` and `DNA.md`: read `DNA.md` before planning.

Working folder: `<workspace>/Projects/<Title>/` (`project.json` records the choices; a script reads only its `final` key). Name the project folder in plain Title Case with no `-` or `_`: `finish.py` looks for the final at `Finished/<Title>/<Title>.mp4`. Engine: `~/cutroom/engine/`.

## 1. What are we making

Settle with the user, in one exchange if you can: the title; the format (from the DNA's `formats`, default `formats.home`); which footage (a shoot, a question or line, or "the take about X"). Then list the candidates from the kept list only:

```bash
python3 ~/cutroom/engine/review/approved.py list --status keep --kind take --json
```

Pick the best take by the organizer's notes (Clean over Usable; the later of two clean reads) and the transcript, and prove each one may be used: `python3 ~/cutroom/engine/review/approved.py check "<take>"` (exit 0). Several takes of several lines become one working clip:

```bash
python3 ~/cutroom/engine/join.py "<project>/source.mp4" "<take 1>" "<take 2>" ...
```

Write `project.json` (title, format, sources, style, started).

## 2. Cut the talking head

```bash
bash ~/cutroom/engine/run_take.sh "<project>" take "<source clip>"
```

Prep (mezzanine, word timing, face anchor), then the plan and `SCRIPT.md`, and the run stops "waiting for approval" (exit 0). A landscape or square source needs `REFRAME=on` (a face-centered 9:16 crop) or `KEEP_LANDSCAPE=on` (a 1920x1080 mezzanine) in front of the command; a source with nobody on camera needs `FACELESS=on`. Open `SCRIPT.md` with the user: one `## NN` section per sentence, the words the cutter will strike shown `~~struck~~`. Wrap a word in `~~ ~~` to cut it, take the marks off to keep it, delete a whole section to drop that sentence (restarts, rambles, a weaker double; never rewrite a word), keep the strongest straight line as the opener, keep the user's call to action if they said one. Change the header to `approved: yes` (at the start of its line) and run the same command again. The cutter places every edge on the voice's own onset and decay, collapses pauses, re-checks every joint for a clipped word and re-cuts up to three times, masters the voice to the platform standard, and leaves `<project>/take/take-cut-master.mp4` with its words (`cut.words.json`), face track, EDL and QC card. Read the QC card (`<project>/take/qc.json`, `evidence/strip.png`, `evidence/contact-sheet.png`): each gate is PASS, FAIL, SKIP or HUMAN. Look at the strip for the `G6 HUMAN` read and say what you saw before step 3. A FAIL stops the run with exit 2 and names the gate; `JOINTS=warn` in front of the command lets an exhausted clipped-word ladder record itself and go on, never silently.

For a story or montage (voiceover over b-roll) there is no cut: the plan carries a `montage` block instead of `cut` (`{"clips": [{"src", "at", "dur", "from_s"?, "rate"?, "z"?, "pos"?, "in"?, "inDur"?, "dir"?}], "vo": [{"src", "at"}], "duration_s"?}`), every clip from the kept b-roll list, and the voiceover files are the voice the bed sits under.

## 3. Plan the edit (`edit.json`)

Write `<project>/edit.json` for the renderer (the docstring at the top of `~/cutroom/engine/render.py` lists every plan key; `~/cutroom/remotion/README.md` covers the props and the theme):

- `cut`: the mastered cut (`<project>/take/take-cut-master.mp4`); `theme`: the output of `python3 ~/cutroom/engine/dna.py theme` saved as a file, or the dict itself; `output` from the DNA.
- `words`: `<project>/take/cut.words.json` and `track`: `<project>/take/cut.facetrack.json`, named by path (the renderer reads the files); `captions_mode`: `"external"`, because the caption chain draws the captions in step 5.
- `hook`: the text and look. The card is fully formed from frame 0 and holds 3 to 6.5 s. Its words are words the speaker says in the first 5 s, whole or cut, never new words (the triple: text, first line and picture agree). At most 3 lines of 27 characters, 14 words. It never covers a face or touches the top of the head in any frame of its window: read the face track; if the DNA's spot would, move this one video's card below the chin and say so, or ship with no card.
- `zooms`: one to three emphasis zooms (1.12 to 1.22) on payoff lines only, held, never in the hook window, never two sentences in a row.
- `inserts`: b-roll that shows what the words say, or none. The menu is `python3 ~/cutroom/engine/review/approved.py list --status keep --kind broll --json`, nothing else; each insert inside its kept range, 1.5 to 4 s, landing on its phrase, one visual language per video, the speaker back for the key lines, never the same clip twice. A horizontal clip is center-cropped: pick ones that survive it.
- `graphics`: only components listed in the DNA's `graphics.built` (a lower third once after the hook; a title card on a section change). A wished-for graphic that is not built yet: build it now as a component in `~/cutroom/remotion/graphics/user/` (copy the shape of `LowerThird.tsx` or `TitleCard.tsx`, theme colors and fonts only), add it to `USER_GRAPHICS` in that folder's `index.ts`, run `npm run typecheck` in `~/cutroom/remotion`, test-render it, show the user a frame, and list it in `graphics.built` (`{"id", "component"}`) before use. Real content only, never placeholder bars.
- `end_card`: when the DNA's `delivery.end_card.on` is true, `{"at_s": <seconds>}`, the second the card starts, about 2 s before the end of the cut (its line and handle come from the theme; a plan may give its own). Leave it out otherwise.
- `music`: a track from the DNA's library that has not played in the last `repeat_gap_videos` videos (`<workspace>/Brand/ledger.jsonl`), picked for this video's mood; the renderer sits it 22 dB under the speech and 14 dB under where the voice is absent with 1.5 s eases. The renderer never reads the DNA: copy `music.under_speech_db`, `alone_db` and `ease_s` into the plan's `music` block when they differ from 22, 14 and 1.5, and name the file as `"music/<track>"` (found under `Brand/`). The ledger is a file you keep: create `<workspace>/Brand/ledger.jsonl` if it is missing. `null` when the DNA says talking heads get none.
- `sfx`: the DNA's `sound.sfx` (`"clicks"`: clicks and ticks on graphics, in gaps between words; `"off"`: none).

Check it: `python3 ~/cutroom/engine/review/gate.py "<project>/edit.json" --allow-outside` (every clip kept and inside its range; `--allow-outside` lets the plan's own cut through, which is why step 1 checks each take with `approved.py check`). The plan's `output.aspect` must be 9:16: the caption chain accepts nothing else. Then show the user the plan in plain words (the hook, where the inserts land and why, the graphics, the music) with a contact sheet of the insert moments (`ffmpeg` frames at their times). Change what they ask. Go only on their yes.

## 4. Render

```bash
python3 ~/cutroom/engine/render.py "<project>/edit.json" --out "<project>/edit.mp4"
```

The `Edit` composition draws the inserts, hook, graphics and end card over the cut; the music bed and sound effects are mixed under the mastered voice; the final is normalized to -16 LUFS with a -2 dBTP peak and tagged BT.709. Read two frames (the hook, an insert) before moving on.

## 5. Captions in the user's style

Run the `cutroom-captions` skill on `<project>/edit.mp4` with the style `DNA.md` names for this format (else the DNA's `captions.default_style`), destination `<workspace>/Finished/<Title>`:

```bash
bash ~/cutroom/engine/captions/run_captions.sh "<project>/edit.mp4" "<project>/captions" --style <id> --title "<Title>" --dest "<workspace>/Finished/<Title>"
```

Gate with the user, approve, run again. `--title` names the delivery after the video, not after the `captions` folder. The QC gates run on the result; a FIX stops delivery with the second and the gate named. On a PASS, `Finished/<Title>/` holds `<Title>.mp4`, `<Title> - Transcript.md`, `<Title> - Caption.txt` and `Build/<Title>/`. A changed `edit.mp4` over the same `captions` folder stops with a line asking for `FORCE=1`; with it the old `GATE.md` is kept as a `.bak` and the captions need approving again.

## 6. Finish

- If the captions run ends with `CAPTION NEEDED` (`python3 ~/cutroom/engine/sidecars.py --check "<workspace>/Finished/<Title>"` lists it too), write the post caption into `<project>/captions/caption.md` (in the speaker's voice, hook-first, no narration of the picture) and run the captions command again: it re-delivers the sidecar without a re-render.
- Append one line to `<workspace>/Brand/ledger.jsonl`: `{"title","date","format","style","music","duration_s","shoot"}` (create the file if it is missing; no script writes it).
- Tell the user what shipped and where, every CHECK and HUMAN read that was answered and how, and the music track used.
- Cleanup per the DNA's `cleanup.after_delivery`: `python3 ~/cutroom/engine/finish.py "<Title>"` (plan), then `--go` on `auto` or on the user's yes. Originals in `Footage/` and the final in `Finished/` are never touched.

## Hard rules

- Nothing renders before the user approved the cut (`SCRIPT.md`) and saw the edit plan; nothing ships before they approved the captions (`GATE.md`).
- Every clip placed is kept (the `gate.py` check is not optional).
- No invented numbers, names, reviews or results on screen. A name is spelled from the user, never from the transcript.
- One video, one format, one hook look in the DNA's spot. Videos in a batch never look the same twice: vary the lead move and the inserts, not the hook spot.
