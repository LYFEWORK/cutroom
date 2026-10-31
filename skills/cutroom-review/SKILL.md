---
name: cutroom-review
description: Keep, cut and trim footage on the review page, then apply the decisions. Use when new footage has landed and needs reviewing, when the user asks to open the review page, when they say their picks are done ("apply my picks"), or when any edit needs to know which clips may be used. Only kept clips, inside their kept ranges, are ever placed in a video. Triggers - "open the review page", "review the footage", "keep and cut", "apply my picks", "which clips can I use", "trim this clip", "the b-roll review".
---

# Review: keep, cut, trim

Every clip plays on one local page (queue left, player right with trim tabs). The user taps Keep or Cut, or trims to the parts worth keeping; every tap saves at once to `<workspace>/Review/decisions.json`. Then `apply` trims the kept parts (a stream copy from the keyframe at or before the start, verified by a full decode) and moves cut clips to the Trash. From then on, only kept clips can be placed, and `approved.py` is how every edit proves it.

Engine: `~/cutroom/engine/review/`. Read `~/cutroom/rules/footage.md` (the Keep/Cut rules) and `~/cutroom/rules/broll.md` once per session.

## Put new footage on the page

```bash
python3 ~/cutroom/engine/review/ingest.py --open
```

Inventory (incremental, by content fingerprint, so a renamed or moved clip keeps its decision), 360p proxies for new clips (progress lines; the first big library takes a while), posters and filmstrips, the page data, then the local server on `http://127.0.0.1:8765` (started in the background, log in `<workspace>/Review/logs/serve.log`) and the page in the browser (`--open` implies `--serve`; `--serve` alone starts the server without a browser). Other flags: `--root <folder>` (repeatable; replaces the configured footage folders, which come from `review.roots` in `~/.cutroom/config.json`), `--allow-empty` (a footage folder that is unplugged or emptied otherwise stops the run so no decision is forgotten), `--jobs N`, `--no-filmstrips`. Say how many clips are new. Clips that will not play as originals (10-bit 4:2:2 camera files) play fine as proxies; never point the user at an original.

Keys on the page: Space play, left and right 1 s, Shift with arrows one frame, I and O set the trim edges, K keep, X cut, U undo, N next, P previous. "Keep the rest" and "Cut the rest" per group. Download and Import of decisions.json (under More) carry picks between machines; picks made on a page opened from a file stay in that browser until they are imported on the served page.

## When the picks are done

```bash
python3 ~/cutroom/engine/review/apply.py plan [--shoot "<shoot>"]     # what gets trimmed, what goes to the Trash, with sizes; changes nothing
python3 ~/cutroom/engine/review/apply.py go [--sidecars] [--no-refresh]  # exactly that plan, once
```

Show the user the plan (GB to the Trash, trims) and run `go` only on their word. Trimmed parts are named `<name> - Trimmed` or `<name> - Part N` beside the original, which then goes to the Trash; a part that fails its decode check is removed and the original stays. Space comes back when the user empties the Trash. `go` runs a plan once, refreshes the review list itself and marks the new parts kept; run `ingest.py` again only if it prints `Could not refresh the list`. A trim that would remove under a second makes no new file: the kept range stays on the clip. Exit 1 means a step was not done; the log is `<workspace>/Review/logs/apply.jsonl`.

## Before any clip goes into a video

```bash
python3 ~/cutroom/engine/review/approved.py list --status keep --kind broll --json     # the only b-roll menu an edit may browse
python3 ~/cutroom/engine/review/approved.py list --status keep --kind take --json
python3 ~/cutroom/engine/review/approved.py check "<path>"                             # exit 0 keep (prints the kept range), 1 cut or unreviewed, 2 unknown
python3 ~/cutroom/engine/review/gate.py <edit.json> --allow-outside                     # every clip an edit plan names must be kept and inside its range; --allow-outside lets the plan's own cut and renders through
python3 ~/cutroom/engine/review/approved.py status                                      # how many clips still wait for a review, per shoot
```

An insert's `from_s`, `start_s` and `end_s` (or `in` and `out`) say which seconds of the clip play; a clip named with no times must be kept whole. Never browse the Footage folders for b-roll; the kept list is the menu. If the clip you want is unreviewed, stop and ask the user to review it first.

## Troubles

- The page shows no video: the server is not running (`ingest.py --serve` or `python3 ~/cutroom/engine/review/serve.py --open`); opened from a file, the page still works for tapping and saves in the browser only.
- A clip is missing: it is under a skipped folder (`RAW`, `Raw Clips`, or any folder starting with a dot such as `.work`), or the shoot was archived. `Bad Takes/` clips are listed under the Bad kind and `B-Roll/Bloopers/` under B-roll (group Bloopers): place a blooper only if the user kept it for that.
- The Trash on an external drive: the file moves to that drive's Trash; empty it to get the space back.
