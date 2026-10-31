# Segment a clip into takes and bloopers

A camera keeps rolling, so one raw clip often holds many attempts at one line, plus chatter, laughter and direction. You mark every attempt, so each can be cut into its own file and reviewed. One agent per interview clip is the usual way; the clips are independent, so run them in parallel.

`<scratch>` is the shoot's `.work` folder. `intake.py takes` prints the clip list and the paths. Never run this on a silent clip or on B-roll. A clip whose record says `reel` is `BONUS` answers none of the lines, so a take from it has no line to be filed under: skip it, unless it holds bloopers (then write only its bloopers).

## What you read

- `<scratch>/words/<clip>.txt`: word-level timestamps, one word per line, `t=<seconds> <word>`. (`intake.py takes` makes them with `words.py`.)
- `<scratch>/cheat-sheet.txt`: the lines the shoot is about. On an ad shoot these are the script lines, `L01`, `L02`, ..., each with its words. On an interview shoot they are the questions, `Q01`, `Q02`, ....
- The clip's duration, from `<scratch>/ev/<clip>.json` (`duration_s`).

## What you write

One file, with the Write tool: `<scratch>/results/<clip>.segments.json`

    {"clip": "C0001", "segments": [
      {"start": 12.40, "end": 21.10, "kind": "take", "line": "L02", "quality": "clean", "note": ""},
      {"start": 25.70, "end": 33.90, "kind": "take", "line": "L02", "quality": "flub", "note": "says three-time instead of two-time"},
      {"start": 33.20, "end": 38.00, "kind": "blooper", "line": "L02", "note": "cracks up at the numbers"}
    ]}

- `start` and `end` are seconds into the clip, two decimals, in time order.
- `kind` is `take` or `blooper`.
- `line` is the id of the line or question the attempt is for. On an interview shoot it is the question id (`Q01`). A take whose `line` is not in the cheat sheet is refused. A blooper may have `""`.
- `quality` is for takes only: `clean`, `usable` or `flub`.
- `note`: 15 words or fewer. What happened ("says three-time instead of two-time", "cracks up at the numbers").

## Definitions

- A TAKE is one attempt at delivering a line, from the first word of that attempt to the last word of it (or to where it breaks off). Every attempt is a take, even a bad one. If the speaker reads two lines back to back without a break, that is ONE take: `line` is the first line and the note says "runs into L03".
- `quality`:
  - `clean`: the whole line delivered, no flub, on script (small wording drift is fine).
  - `usable`: complete, but with a stumble, a wrong word, or off-script wording that an editor might still cut around. A complete read with a small stumble is usable, never flub.
  - `flub`: it broke down, restarted, stopped in the middle of the line, or was the wrong line.
- A BLOOPER is a moment worth keeping for a blooper reel: a laugh, a swear, a self-roast, a tongue-twister meltdown, a joke with the crew, a physical break. Give it its own segment that covers the whole moment, from the flub that caused it through the laughter and the recovery. A flub take and its blooper may overlap.
- Ignore silence, the interviewer's or director's direction ("whenever you're ready", "one more"), and chatter about the setup, unless it is part of a blooper moment.

## Rules

- `start` is the time of the first word of the segment. `end` is the time of its last word plus about 0.6 seconds, so the last word is not clipped.
- Segments are in time order. Do not invent words that are not in the timestamps. If the transcript is garbled, cut at the clearest boundaries you can defend and say so in `note`.
- Every attempt gets its own segment. Never merge a bad read and the good read after it into one: the good read must come out on its own.
- Mark every attempt you find, including a first attempt that dies after two words. The cutter adds its own handles on both sides, so do not widen a segment to be safe.

When you are done, reply with the same JSON. Then run `intake.py takes <shoot> --cut`.
