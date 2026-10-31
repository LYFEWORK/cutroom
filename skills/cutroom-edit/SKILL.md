---
name: cutroom-edit
description: Make a finished short-form video from kept footage in the user's own look. Use when the user wants a video made, whether a talking head (clean or with graphics), a story over b-roll, a b-roll montage, a screen walkthrough or a podcast clip. It picks the take, cuts it without clipping a word, plans the hook card, b-roll inserts, graphics, music and end card, shows the plan, renders, captions in the user's style, runs the QC gates, delivers with a transcript and caption file, and cleans up. Triggers - "make a video", "edit this take", "turn this into a reel", "cut this down", "make a talking head from the shoot", "edit the interview answer about X".
---

# Make a video

Footage in the shoot folders, reviewed and kept, becomes one finished video in `<workspace>/Finished/<Title>/`. You decide the edit; the scripts do the mechanics; the user approves the cut and the captions before anything renders. Read these once per session: `~/cutroom/rules/cutting.md`, `hook.md`, `broll.md`, `graphics.md`, `sound.md`, `delivery.md`, and `formats.md` for the format at hand. The user's look is `<workspace>/Brand/dna.json` and `DNA.md`: read `DNA.md` before planning.

Working folder: `<workspace>/Projects/<Title>/` (`project.json` records the choices). Engine: `~/cutroom/engine/`.

## 1. What are we making

Settle with the user, in one exchange if you can: the title; the format (from the DNA's `formats`, default `formats.home`); which footage (a shoot, a question or line, or "the take about X"). Then list the candidates from the kept list only:

```bash
python3 ~/cutroom/engine/review/approved.py list --status keep --kind take --json
```

Pick the best take by the organizer's notes (Clean over Usable; the later of two clean reads) and the transcript. Several takes of several lines become one working clip:

```bash
python3 ~/cutroom/engine/join.py "<project>/source.mp4" "<take 1>" "<take 2>" ...
```

Write `project.json` (title, format, sources, style, started).

## 2. Cut the talking head

```bash
bash ~/cutroom/engine/run_take.sh "<project>" take "<source clip>"
```

Prep (mezzanine, word timing, face anchor), then the plan and `SCRIPT.md`, and the run stops "waiting for approval". Open `SCRIPT.md` with the user: strike whole lines that go (restarts, rambles, a weaker double; never rewrite a word), keep the strongest straight line as the opener, keep the user's call to action if they said one. Set `approved: yes`, run the same command again. The cutter places every edge on the voice's own onset and decay, collapses pauses, re-checks every joint for a clipped word and re-cuts up to three times, masters the voice to the platform standard, and leaves `<project>/take/take-cut-master.mp4` with its words (`cut.words.json`), face track, EDL and QC card. Read the QC card: every CHECK needs an answer from you or the user before step 3.

For a story or montage (voiceover over b-roll), the voiceover recording is the "take": cut it the same way, then the inserts in step 3 cover the whole frame.

## 3. Plan the edit (`edit.json`)

Write `<project>/edit.json` for the renderer (`~/cutroom/remotion/README.md` has every key):

- `cut`: the mastered cut; `theme`: `python3 ~/cutroom/engine/dna.py theme`; `output` from the DNA.
- `hook`: the text and look. The card is fully formed from frame 0 and holds 3 to 6.5 s. Its words are words the speaker says in the first 5 s, whole or cut, never new words (the triple: text, first line and picture agree). At most 3 lines of 27 characters, 14 words. It never covers a face or touches the top of the head in any frame of its window: read the face track; if the DNA's spot would, move this one video's card below the chin and say so, or ship with no card.
- `zooms`: one to three emphasis zooms (1.12 to 1.22) on payoff lines only, held, never in the hook window, never two sentences in a row.
- `inserts`: b-roll that shows what the words say, or none. The menu is `python3 ~/cutroom/engine/review/approved.py list --status keep --kind broll --json`, nothing else; each insert inside its kept range, 1.5 to 4 s, landing on its phrase, one visual language per video, the speaker back for the key lines, never the same clip twice. A horizontal clip is center-cropped: pick ones that survive it.
- `graphics`: only components listed in the DNA's `graphics.built` (a lower third once after the hook; a title card on a section change). A wished-for graphic that is not built yet: build it now as a component in `<workspace>/Brand/graphics/` from `~/cutroom/remotion/graphics/` examples, test-render it, show the user a frame, and register it in `graphics.built` before use. Real content only, never placeholder bars.
- `end_card`: from the DNA (`delivery.end_card`).
- `music`: a track from the DNA's library that has not played in the last `repeat_gap_videos` videos (`<workspace>/Brand/ledger.jsonl`), picked for this video's mood; the renderer sits it 22 dB under the speech and 14 dB under where the voice is absent with 1.5 s eases. `null` when the DNA says talking heads get none.
- `sfx`: leave to the renderer (clicks and ticks on graphics, in gaps between words) unless the DNA turns them off.

Check it: `python3 ~/cutroom/engine/review/gate.py "<project>/edit.json"` (every clip kept and inside its range). Then show the user the plan in plain words (the hook, where the inserts land and why, the graphics, the music) with a contact sheet of the insert moments (`ffmpeg` frames at their times). Change what they ask. Go only on their yes.

## 4. Render

```bash
python3 ~/cutroom/engine/render.py "<project>/edit.json" --out "<project>/edit.mp4"
```

The `Edit` composition draws the inserts, hook, graphics and end card over the cut; the music bed and sound effects are mixed under the mastered voice; the final is normalized to -16 LUFS with a -2 dBTP peak and tagged BT.709. Read two frames (the hook, an insert) before moving on.

## 5. Captions in the user's style

Run the `cutroom-captions` skill on `<project>/edit.mp4` with the format's style, destination `<workspace>/Finished/<Title>`:

```bash
bash ~/cutroom/engine/captions/run_captions.sh "<project>/edit.mp4" "<project>/captions" --style <id> --dest "<workspace>/Finished/<Title>"
```

Gate with the user, approve, run again. The QC gates run on the result; a FIX stops delivery with the second and the gate named. On a PASS, `Finished/<Title>/` holds `<Title>.mp4`, `<Title> - Transcript.md`, `<Title> - Caption.txt` and `Build/`.

## 6. Finish

- Write the post caption if the run asks for it (`caption.md`, in the speaker's voice, hook-first, no narration of the picture).
- Append one line to `<workspace>/Brand/ledger.jsonl`: `{"title","date","format","style","music","duration_s","shoot"}`.
- Tell the user what shipped and where, every CHECK that was answered and how, and the music track used.
- Cleanup per the DNA's `cleanup.after_delivery`: `python3 ~/cutroom/engine/finish.py "<Title>"` (plan), then `--go` on `auto` or on the user's yes. Originals in `Footage/` and the final in `Finished/` are never touched.

## Hard rules

- Nothing renders before the user approved the cut (`SCRIPT.md`) and saw the edit plan; nothing ships before they approved the captions (`GATE.md`).
- Every clip placed is kept (the `gate.py` check is not optional).
- No invented numbers, names, reviews or results on screen. A name is spelled from the user, never from the transcript.
- One video, one format, one hook look in the DNA's spot. Videos in a batch never look the same twice: vary the lead move and the inserts, not the hook spot.
