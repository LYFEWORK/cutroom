# captions: the caption chain

Captions on a video that is already cut and mixed, in one of your caption styles, with a human gate before the render and QC gates after. It never edits the picture or the sound: the original audio is remuxed under the captioned picture.

```bash
bash engine/captions/run_captions.sh <video> <take-dir> [--style <id>] [--title <name>] [--dest <folder>]
```

Run it twice. The first run plans everything and stops at `GATE.md`; you approve; the same command renders, checks and delivers.

| Exit | Meaning |
|---|---|
| 0 | delivered to `<dest>` |
| 1 | rendered, but a QC gate failed: nothing shipped (`reel-qc.json` and `evidence/` say why) |
| 2 | stopped before a render: the gate is not approved yet, or a stage failed (one `STOP:` line, the log is `<take-dir>/logs/<stage>.log`) |

## What each run does

Pass 1: `intake` (a 9:16 clip at 1080x1920 or 2160x3840; an HDR phone export is tone mapped) → `conform` (the mezzanine: H.264, BT.709, 30 fps, no audio; the audio as wav) → `look` (your DNA plus the style, `look.json`) → `words` (local transcription and word timing; Deepgram only when `transcribe.engine` is deepgram and its key is set) → `facetrack` → `roomcheck` (FULL, REDUCED or MINIMAL: how much room the face leaves) → `spec` → `plan` (phrases, hero words) → `devices-propose` (only with `captions.devices: on`) → `pacing` → `legible` (soft build only) → `GATE.md`, then it stops: "Open GATE.md, set approved: yes, run the same command again".

Pass 2: applies the gate → `spec` and `plan` again from it → `devices` (only the events you accepted) → `checks` (CHECK lines the gate did not show) → `matte` (only when a phrase sits behind the head) → `render` (Remotion, the `Captions` composition; a 4K source renders at 2x) → `qc` (`reel_qc.py`, the never-ship gates) → `deliver`.

Every stage is resumable: an output older than what it was built from is rebuilt, nothing else. Editing your DNA, a style file, `GATE.md` or `words.json` and running the same command rebuilds exactly what depends on the edit. `FORCE=1` rebuilds every derived file (never `GATE.md`); to re-transcribe, delete `words.json`.

## Names and places

- `<take-dir>` is the take's working folder; create it anywhere under your workspace's `Projects/`. Its basename is the take's name.
- The delivered video is `<dest>/<Title>.mp4` beside `<Title> - Transcript.md` and `<Title> - Caption.txt`, with the take's own `Build/<Title>/` (NOTES.md, GATE.md, gate.json, reel-qc.json, evidence/). `<Title>` is `--title` when given, else the take dir's basename. `--dest` defaults to `<workspace>/Finished/<Title>/`. A take that already delivered under a name keeps it; the run says so.
- A second take that wants a taken name becomes `<Title> Take 2`, never an overwrite; delivering the same take again replaces its own files.
- `caption-style.txt` in the take dir remembers `--style`; a `style:` line in `GATE.md` beats it; with neither, the DNA's `captions.default_style`.
- The post caption: write `<take-dir>/caption.md` and run the command again; `Caption.txt` is rewritten without a re-render. Until then it holds a CAPTION NEEDED placeholder and the video is not done.

## GATE.md

A flat text file you edit like a script. The header line `approved: no` becomes `approved: yes` when you are happy. A line inside a `<!-- -->` comment is inert; move it out, or add a new one.

| Section | What you can do |
|---|---|
| Tier & zone | `tier: MINIMAL|REDUCED|FULL` and `zone: top|chest`, edited away from the values the gate was built with |
| Transcript | read it; fix a word with `fix: "heard" -> "written"` under Overrides (spelling comes from the person, never from the transcript) |
| Hero words | the starred word is the hero of its cluster; `hero: "word"` and `no-hero: "word"` under Overrides move it |
| Devices | `page:`, `cta:`, `frame:`, `igpost:` lines become layout events; `neg:`, `no-neg:`, `strike:`, `pin: off`, `count: off`, `glow: off`, `front:`, `behind:`, `finale:` tune the word devices; a `propose:` line is the take's own suggestion, accept one by dropping `propose: ` |
| Pacing | `punch: <s>`, `no-punch: <s>`, `punch: off`, `logo: off`, `icon: off`; the `pace:` lines above the comment are a report, never read back |
| Thoughts (soft build only) | one line per thought (lead-in, payoff, tail, `[bar]` on the bar); regroup words or move the bar; an untouched section leaves the split to the planner |
| Overrides | `allow: "word"` accepts a do-not-say word for this take; `subject: none` says nobody is on camera (the face gate becomes audit only); `style: <id>` swaps the caption style; `note:` free text |

Every CHECK line in the gate (and in `Build/<Title>/NOTES.md`) is something a person must answer in words before shipping. `capgate.py` holds the full grammar in its docstring.

## The gates (`reel_qc.py`)

G2 picture equals sound · G4 caption ink inside the safe box and off the face · G5 every spoken word on screen, in order, no event under three frames · G6 the human read (the contact sheet in `evidence/`) · G7 loudness (audit only unless `captions.master_audio` is `loudnorm`) · G8 and G9 color tags and pixels · G10 pacing, as `captions.pace_gate` says (report by default) · G-FACE a face on camera · G-WORDS no banned word · G-ACCENT the look's accent is the one drawn. A FIX blocks delivery; a CHECK needs an answer.

## Styles

`python3 engine/captions/styles.py list | show <id> [--diff] | check`. Yours live in `<workspace>/Brand/styles/`, the starters in `templates/styles/`. `python3 engine/captions/preview_style.py <id> [--clip <file>] --out <jpg>` renders a sample. The style contract is `styles.py`'s docstring; the DNA fields it may set are the `captions` section of `templates/schema.json`.

## Batch

`bash engine/captions/batch_captions.sh "<folder of cuts>" "<workroot>" [--style <id>] [--dest <folder>]`: pass 1 leaves every clip at its gate, pass 2 delivers the approved ones, and the summary lists each clip's state.
