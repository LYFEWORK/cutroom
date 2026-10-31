#!/usr/bin/env python3
"""intake.py: a card dump in, an organized shoot folder out. The one command the footage module is driven by.

  intake.py start <folder> [--name "Shoot Name"] [--date YYYY-MM-DD] [--type interview|ad] [--questions FILE] [--context "..."] [--people "..."]
  intake.py subjects <shoot> --set "Desk Work,Whiteboard,Street,Other"
  intake.py apply <shoot> [--allow-unverified]
  intake.py takes <shoot> [--cut] [--handle SECONDS] [--dry-run]
  intake.py notes <shoot>
  intake.py status <shoot> [--json]
  intake.py reverse <shoot>
  intake.py prep <shoot>                  (run the prep again, for example after a stop in `start`)

<shoot> is the shoot folder, or its name under <workspace>/Footage.

Two of the steps are done by Claude reading images and writing JSON, not by code, so the command stops and says exactly what to do next:
  1. after `start`: read the contact sheets in <shoot>/.work/sheets/, set the B-roll folders with `subjects`, then classify every clip by prompts/classify-batch.md
     into <shoot>/.work/results/<clip>.final.json (the workflow route, classify.js, writes the classify, visual and content files instead; both are accepted).
  2. after `takes <shoot>`: write <shoot>/.work/results/<clip>.segments.json for every interview clip by prompts/segment.md, then `takes <shoot> --cut`.

What `start` does: makes <workspace>/Footage/<date> <name>/, moves the dropped files into its RAW/ (copies them, checked, when the folder is on another volume such as an
SD card, and leaves the card untouched), writes job.json, and runs prep. Every stop is one plain line, never a stack trace (CUTROOM_DEBUG=1 shows the trace).
Exit codes: 0 done, 1 the QC found a problem, 2 stopped with a reason.
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from common import stop  # noqa: E402
import index as index_mod  # noqa: E402
import merge  # noqa: E402
import notes as notes_mod  # noqa: E402
import organize  # noqa: E402
import prep as prep_mod  # noqa: E402
import qc as qc_mod  # noqa: E402
import takes as takes_mod  # noqa: E402
import words as words_mod  # noqa: E402
import workflow_args  # noqa: E402
from job import MARK, TOP, VIDEO_EXT, check_questions, clean, is_junk, is_os_junk, load_job, read_csv, read_plan, title  # noqa: E402

PROMPTS = HERE / "prompts"
SELF = HERE / "intake.py"


def say(msg=""):
    print(msg, flush=True)


def cmd(args_text):
    """a command line the reader can paste"""
    return f'python3 "{SELF}" {args_text}'


def q(path):
    return '"' + str(path) + '"'


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        stop(f"{path} is not readable JSON ({e})")


# ---------------------------------------------------------------- finding the shoot
def resolve_shoot(arg):
    """the shoot folder from a path, or from a name under <workspace>/Footage (the date may be left off, a part of the name is enough when it is only one shoot)"""
    p = Path(arg).expanduser()
    if p.is_dir():
        if (p / "job.json").exists():
            return p.resolve()
        if os.sep in str(arg) or str(arg).startswith((".", "~")):
            stop(f"{p} is not a shoot folder: it has no job.json (start one with: intake.py start <folder>)")
    base = common.ws("Footage")
    if (base / arg / "job.json").exists():
        return (base / arg).resolve()
    found = sorted(d for d in base.iterdir() if d.is_dir() and (d / "job.json").exists() and arg.lower() in d.name.lower()) if base.is_dir() else []
    if len(found) == 1:
        return found[0].resolve()
    if not found:
        stop(f"no shoot called {arg!r}: pass the shoot folder, or its name as it appears under {base}")
    stop(f"{arg!r} matches {len(found)} shoots ({', '.join(d.name for d in found[:5])}): use the whole name or the folder path")


def refresh_evidence(job):
    """a shoot folder that was renamed or moved leaves the contact sheet and frame paths in the evidence files pointing at the old place: point them at this one"""
    scr = job["scratch"]
    ev_path = f"{scr}/evidence.json"
    ev = read_json(ev_path)
    if not ev:
        return
    changed = False
    for e in ev:
        want = {"sheet": f"{scr}/sheets/{e['clip']}.jpg", "frames_dir": f"{scr}/frames/{e['clip']}"}
        if any(e.get(k) != v for k, v in want.items()):
            e.update(want)
            common.atomic_write_json(f"{scr}/ev/{e['clip']}.json", e)
            changed = True
    if changed:
        common.atomic_write_json(ev_path, ev)


def open_shoot(arg):
    """-> (root, job.json path, job). job.json's root follows the folder if the folder moved"""
    root = resolve_shoot(arg)
    jp = root / "job.json"
    raw = read_json(jp, {})
    if isinstance(raw, dict) and raw.get("root") and os.path.abspath(os.path.expanduser(raw["root"])) != str(root):
        raw["root"] = str(root)
        common.atomic_write_json(jp, raw)
    job = load_job(jp)
    refresh_evidence(job)
    return root, str(jp), job


def write_job_field(jp, **fields):
    raw = read_json(jp, {})
    raw.update(fields)
    common.atomic_write_json(jp, raw)


# ---------------------------------------------------------------- the question sheet
_ID = re.compile(r"^([A-Za-z]{1,2}\d{1,3})\b[\s:.)\-]*(.*)$")
_QUOTED = re.compile(r'["\u201c](.+?)["\u201d]')
_FOLLOW = re.compile(r"follow[\s-]?ups?\s*:\s*(.+)$", re.I)


def short_title(text, words=6):
    t = re.sub(r"[^\w\s'+&-]", " ", text)
    t = " ".join(t.split()[:words])
    return title(clean(t, 60)) or "Untitled"


def parse_questions(path):
    """A question sheet as the job's list: [{id, title, question, followups}]. A .json file holds a list (or {"questions": [...]}) of objects or plain strings.
    Any other file is read line by line: `Q01 Title: "the question" Follow-ups: a; b`, or a plain list of questions (ids Q01, Q02 are given). Stops on a mistake."""
    p = Path(path).expanduser()
    if not p.is_file():
        stop(f"the question sheet {p} does not exist")
    text = p.read_text(encoding="utf-8", errors="replace")
    items = []
    if p.suffix.lower() == ".json":
        try:
            d = json.loads(text)
        except ValueError as e:
            stop(f"{p} is not valid JSON ({e})")
        d = d.get("questions") if isinstance(d, dict) else d
        if not isinstance(d, list):
            stop(f"{p} must hold a list of questions, or an object with a \"questions\" list")
        for i, q in enumerate(d, 1):
            if isinstance(q, str):
                items.append({"id": "", "title": "", "question": q.strip()})
            elif isinstance(q, dict):
                items.append({k: (str(v).strip() if isinstance(v, str) else v) for k, v in q.items()})
            else:
                stop(f"{p}: question {i} must be an object or a string")
    else:
        for line in text.splitlines():
            s = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
            if not s or s.startswith("#"):
                continue
            follow = ""
            m = _FOLLOW.search(s)
            if m:
                follow, s = m.group(1).strip(), s[:m.start()].strip()
            qid, rest = "", s
            m = _ID.match(s)
            if m:
                qid, rest = m.group(1).upper(), m.group(2).strip()
            quoted = _QUOTED.search(rest)
            if quoted:
                ttl = re.sub(r"[\s:\u2014\u2013-]+$", "", rest[:quoted.start()]).strip()
                question = quoted.group(1).strip()
            else:
                parts = re.split(r"\s+[\u2014\u2013-]\s+|:\s+", rest, maxsplit=1)
                ttl, question = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 and qid else ("", rest)
            items.append({"id": qid, "title": ttl, "question": question, "followups": follow})
    out, used = [], set()
    for q in items:
        if not q.get("question") and not q.get("title"):
            continue
        qid = str(q.get("id") or "").strip()
        if not qid:
            n = len(out) + 1
            qid = f"Q{n:02d}"
            while qid in used:
                n += 1
                qid = f"Q{n:02d}"
        q["id"] = qid
        q["question"] = q.get("question") or q.get("title")
        q["title"] = q.get("title") or short_title(q["question"])
        if not q.get("followups"):
            q.pop("followups", None)
        used.add(qid)
        out.append(q)
    if not out:
        stop(f"{p} holds no questions")
    return out


# ---------------------------------------------------------------- start
def gather(src):
    """[(absolute path, path relative to src)] of every file to take in: junk (.DS_Store, ._name, Thumbs.db and the folders a card keeps for itself) is left out, and so is a
    symbolic link. -> (files, skipped_links)"""
    src = Path(src)
    if src.is_file():
        return [(str(src), src.name)], 0
    out, links = [], 0
    for d, dirs, fs in os.walk(src):
        dirs[:] = sorted(x for x in dirs if not is_junk(x + "/") and not os.path.islink(os.path.join(d, x)))
        for f in sorted(fs):
            p = os.path.join(d, f)
            rel = os.path.relpath(p, src)
            if is_junk(rel):
                continue
            if os.path.islink(p):
                links += 1
                continue
            out.append((p, rel))
    return out, links


def clashing_dirs(named):
    """the dropped-folder-relative folders whose files would land on the same name in RAW as another folder's (a video's name counts without its extension too)"""
    seen, bad = {}, set()
    for rel, name in named:
        d = os.path.dirname(rel)
        stem, ext = os.path.splitext(name)
        for k in [name.lower()] + ([stem.lower() + "|video"] if ext.lower() in VIDEO_EXT else []):
            if k in seen and seen[k][1] != d:
                bad.update((d, seen[k][1]))
            elif k not in seen:
                seen[k] = (rel, d)
    return bad - {""}   # a file at the top level keeps its name; the folder it clashes with is the one that is renamed


def flat_names(files):
    """-> [(absolute path, name inside RAW)]. A card dump is flattened into RAW/. When two files would land on one name (two cards that both hold C0001.MP4), every file of the
    folders that clash gets its folder's name in front, so a clip and its sidecars stay together; files at the top level keep their names. A name still taken gets a number,
    so nothing in RAW is ever overwritten."""
    named = [(rel, p, os.path.basename(rel)) for p, rel in files]
    bad = clashing_dirs([(rel, name) for rel, p, name in named])
    if bad:
        named = [(rel, p, (re.sub(r"[\\/]+", "_", os.path.dirname(rel)) + " - " if os.path.dirname(rel) in bad else "") + name) for rel, p, name in named]
    out, seen, n = [], set(), 0
    for rel, p, name in named:
        cand = name
        stem, ext = os.path.splitext(name)
        while cand.lower() in seen:
            n += 1
            cand = f"{stem} ({n}){ext}"
        seen.add(cand.lower())
        out.append((p, cand))
    return out


def guess_date(files):
    """the day the first clip was recorded, from the files' own times; today when there is no video"""
    times = [os.path.getmtime(p) for p, rel in files if os.path.splitext(rel)[1].lower() in VIDEO_EXT]
    return time.strftime("%Y-%m-%d", time.localtime(min(times))) if times else time.strftime("%Y-%m-%d")


def tidy_source(src, keep_top):
    """after a move: delete the operating system's junk files (.DS_Store, ._name, Thumbs.db) left in the dropped folder and every folder that is now empty, deepest first.
    The top folder goes too unless keep_top. Nothing else is deleted: a file that was not taken stays, and is returned. -> what is left"""
    src = str(src)
    for d, dirs, fs in sorted(os.walk(src), key=lambda x: -len(x[0])):
        for f in fs:
            if is_os_junk(f) and os.path.isfile(os.path.join(d, f)) and not os.path.islink(os.path.join(d, f)):
                os.remove(os.path.join(d, f))
        if d == src and keep_top:
            continue
        try:
            os.rmdir(d)
        except OSError:
            pass
    return [os.path.relpath(os.path.join(d, f), src) for d, _, fs in os.walk(src) for f in fs] if os.path.isdir(src) else []


def cmd_start(a):
    t0 = time.time()
    src = Path(a.folder).expanduser()
    if not src.exists():
        stop(f"{src} does not exist")
    src = src.resolve()
    footage = common.ws("Footage")
    footage.mkdir(parents=True, exist_ok=True)
    footage = footage.resolve()
    if src == footage or footage in src.parents:
        stop(f"{src} is already inside the Footage folder: drop the card's folder into Drop/, or give the path of the folder itself")
    drop = common.ws("Drop").resolve()
    files, links = gather(src)
    if not files:
        stop(f"nothing to organize: {src} holds no files" + (f" (it has {links} symbolic link(s), which are never taken)" if links else ""))
    n_video = sum(1 for p, rel in files if os.path.splitext(rel)[1].lower() in VIDEO_EXT)
    if not n_video:
        stop(f"no video files in {src} ({len(files)} other file(s)): nothing to organize")
    questions = parse_questions(a.questions) if a.questions else []
    check_questions(questions)   # a question the job would refuse stops here, before a file moves
    shoot_type = a.type or "interview"
    date = a.date or guess_date(files)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        stop(f"--date must look like 2026-10-31 (got {date!r})")
    name = (a.name or (src.name if src.is_dir() and src != drop else "") or "Shoot").strip()
    folder_name = f"{date} {clean(name, 80) or 'Shoot'}"
    root = footage / folder_name
    if root.exists():
        stop(f"{root} already exists: choose another --name, or look at it with: intake.py status \"{folder_name}\"")
    named = flat_names(files)
    total = sum(os.path.getsize(p) for p, _ in named)
    copy = not organize.same_volume(str(src), str(footage))
    if copy:
        free = shutil.disk_usage(footage).free
        if free < total * 1.01 + organize.SPACE_MARGIN:
            stop(f"not enough room to copy: {total / 1e9:.1f} GB of footage and {free / 1e9:.1f} GB free where the Footage folder is")
    raw = root / "RAW"
    scratch = root / ".work"
    raw.mkdir(parents=True)
    scratch.mkdir()
    journal = [{"from": p, "to": str(raw / n)} for p, n in named]
    common.atomic_write_json(scratch / "start.json", {"source": str(src), "mode": "copy" if copy else "move", "files": journal})
    say(f"Shoot folder: {root}")
    say(f"{'Copying' if copy else 'Moving'} {len(named)} files ({total / 1e9:.2f} GB) from {src}")
    done = []
    try:
        for i, (p, n) in enumerate(named, 1):
            dst = str(raw / n)
            if copy:
                if os.path.getsize(p) > 20 << 20:
                    say(f"[copy {i}/{len(named)}] {n} ({os.path.getsize(p) / 1e9:.2f} GB)")
                organize.verified_copy(p, dst)
            else:
                os.rename(p, dst)
            done.append((p, dst))
    except (Exception, KeyboardInterrupt) as e:
        for p, dst in reversed(done):
            try:
                if copy:
                    os.remove(dst)
                else:
                    os.rename(dst, p)
            except OSError:
                pass
        left = [f for _, _, fs in os.walk(root) for f in fs if f not in ("start.json",)]
        if not left:
            shutil.rmtree(root, ignore_errors=True)
        what = "the copies were deleted and the source was not touched" if copy else "the files are back where they were"
        if isinstance(e, KeyboardInterrupt):
            stop(f"STOPPED by you; {what}")
        stop(f"could not take the files in ({e}); {what}")
    if copy:
        say(f"Copied {len(named)} files, each checked against the original. {src} was not changed: the card can be ejected.")
    else:
        left = tidy_source(src, keep_top=(src == drop or not str(src).startswith(str(drop) + os.sep)))
        if left:
            say(f"[warn] {len(left)} item(s) stay in {src} (never taken): {', '.join(left[:5])}")
    job = {"name": name, "root": str(root), "src": "<ROOT>/RAW", "scratch": "<ROOT>/.work", "shoot_date": date, "shoot_type": shoot_type, "camera_label": "",
           "context": a.context or "", "people": a.people or "", "questions": questions, "subjects": [], "shot_list": "", "subject_overrides": {}, "notes": "", "brand": {}}
    jp = root / "job.json"
    common.atomic_write_json(jp, job)
    J = load_job(jp)
    say(f"Running prep: probe, audio levels, whisper transcripts, contact sheets (job: {jp})")
    say("If prep stops, fix the reason it names and run: " + cmd(f'prep "{root}"'))
    ev = prep_mod.main(["--job", str(jp)])
    workflow_args.write_cheat_sheet(J)
    say(f"\nSHOOT STARTED in {time.time() - t0:.0f} s: {len(ev)} clips, {sum(e['duration_s'] for e in ev) / 60:.1f} min, {sum(1 for e in ev if not e['no_speech'])} with speech")
    next_after_prep(root, J, ev)


def next_after_prep(root, J, ev):
    scr = J["scratch"]
    clips = [e["clip"] for e in ev]
    say("\nNEXT: two steps for Claude, no script does them")
    say(f"  1. Read at least 5 contact sheets spread across the card, in {scr}/sheets/, then set the B-roll folders (8 to 13 for a big card, fewer for a small one, plus Other):")
    say("       " + cmd(f'subjects "{root}" --set "Desk Work,Whiteboard,Street,Other"'))
    say(f"  2. Classify every clip: follow {PROMPTS / 'classify-batch.md'} and write {scr}/results/<clip>.final.json for each clip:")
    for c in clips[:12]:
        say(f"       {c}")
    if len(clips) > 12:
        say(f"       ... and {len(clips) - 12} more (all of them are in {scr}/probe.tsv)")
    say("  Then: " + cmd(f'apply "{root}"'))


# ---------------------------------------------------------------- the stages
def cmd_prep(a):
    root, jp, J = open_shoot(a.shoot)
    ev = prep_mod.main(["--job", jp])
    workflow_args.write_cheat_sheet(J)
    next_after_prep(root, J, ev)


def cmd_subjects(a):
    root, jp, J = open_shoot(a.shoot)
    seen, names = set(), []
    for n in re.split(r"[,\n]", a.set):
        n = title(clean(n, 60))
        if n and n.lower() not in seen:
            seen.add(n.lower())
            names.append(n)
    if not names:
        stop('--set needs at least one folder name, for example --set "Desk Work,Whiteboard,Other"')
    if "other" not in seen:
        names.append("Other")
    if len(names) > 25:
        stop(f"{len(names)} folders is too many to be useful: group them into 8 to 13 and run this again")
    write_job_field(jp, subjects=names)
    J = load_job(jp)
    workflow_args.write_cheat_sheet(J)
    args = workflow_args.build_args(J)
    common.atomic_write_json(f"{J['scratch']}/workflow-args.json", args)
    say(f"B-roll folders set ({len(names)}): {', '.join(names)}")
    say(f"Cheat sheet: {J['scratch']}/cheat-sheet.txt   Workflow args: {J['scratch']}/workflow-args.json")
    todo = [c for c in args["clips"] if not (os.path.exists(f"{J['scratch']}/results/{c}.final.json") or os.path.exists(f"{J['scratch']}/results/{c}.classify.json"))]
    say(f"Next: classify {len(todo)} clip(s) by {PROMPTS / 'classify-batch.md'} into {J['scratch']}/results/<clip>.final.json, then: " + cmd(f'apply "{root}"'))


def cmd_apply(a):
    root, jp, J = open_shoot(a.shoot)
    if not os.path.exists(f"{J['scratch']}/evidence.json"):
        stop("prep has not run for this shoot: " + cmd(f'prep "{root}"'))
    merge.main(["--job", jp])
    organize.run(J, "apply", a.allow_unverified, say=say)
    out = index_mod.build(J, quiet=True)
    say(f"Index: {out} ({os.path.getsize(out) // 1024} KB)")
    if not qc_mod.report(J):
        sys.exit(1)


def split_by_line(J, clips):
    """-> (lined, bonus): the interview clips whose record names one of the job's lines or questions, and those that answer none of them (reel BONUS). A take is filed
    under its line, so only the first kind can be cut into takes; the second is segmented only to pull its bloopers out"""
    ids = {q["id"] for q in J["questions"]}
    lined, bonus = [], []
    for c in clips:
        rec = read_json(f"{J['scratch']}/results/{c}.final.json") or read_json(f"{J['scratch']}/results/{c}.classify.json") or {}
        (lined if rec.get("reel") in ids else bonus).append(c)
    return lined, bonus


def cmd_takes(a):
    root, jp, J = open_shoot(a.shoot)
    P = read_plan(J)
    if not P or not P.get("applied"):
        stop("takes come after the shoot is organized: " + cmd(f'apply "{root}"'))
    if not J["questions"]:
        stop("takes are filed under the question or script line they belong to, and this shoot has none: add them to job.json (questions: id, title, question) and run this again")
    scr = J["scratch"]
    if not a.cut:
        clips = words_mod.main(["--job", jp, "--skip-existing"])
        lined, bonus = split_by_line(J, clips)
        ev = {e["clip"]: e for e in read_json(f"{scr}/evidence.json", [])}

        def show(c):
            have = os.path.exists(f"{scr}/results/{c}.segments.json")
            say(f"  {c} ({ev.get(c, {}).get('duration_s', '?')} s){'  [done]' if have else ''}")
        say(f"\nNEXT: for each clip below, Claude writes {scr}/results/<clip>.segments.json following {PROMPTS / 'segment.md'}")
        say(f"  (word times: {scr}/words/<clip>.txt; the lines takes are filed under: {scr}/cheat-sheet.txt)")
        for c in lined:
            show(c)
        if not lined:
            say("  (no interview clip answers one of the listed lines or questions, so there is nothing to cut into takes)")
        if bonus:
            say("Optional: these answer no listed line, so they have no line to file takes under. Segment one only to pull out its bloopers:")
            for c in bonus:
                show(c)
        say("Then: " + cmd(f'takes "{root}" --cut'))
        return
    if not glob.glob(f"{scr}/results/*.segments.json"):
        stop(f"no segments yet: write {scr}/results/<clip>.segments.json for each interview clip (prompts/segment.md), then run this again")
    argv = ["--job", jp] + (["--handle", str(a.handle)] if a.handle is not None else []) + (["--dry-run"] if a.dry_run else [])
    takes_mod.main(argv)
    if a.dry_run:
        return
    out = index_mod.build(J, quiet=True)
    say(f"Index: {out} ({os.path.getsize(out) // 1024} KB)")
    if not qc_mod.report(J):
        sys.exit(1)


def cmd_notes(a):
    root, jp, J = open_shoot(a.shoot)
    P = read_plan(J)
    if not P or not P.get("applied"):
        stop("notes come after the shoot is organized: " + cmd(f'apply "{root}"'))
    notes_mod.main(["--job", jp])


def cmd_reverse(a):
    root, jp, J = open_shoot(a.shoot)
    P = read_plan(J)
    if not (P and P.get("applied")) and not os.path.exists(root / "Footage Manifest.csv"):
        stop("nothing to reverse: this shoot is not organized (its files are still in RAW/)")
    organize.reverse(J)
    say(f"The shoot is back as it was after `start`: camera files in {J['src'] if (P or {}).get('mode') != 'copy' else root / 'RAW'}. Organize it again with: " + cmd(f'apply "{root}"'))


def shoot_state(root, J):
    """what stage the shoot is at and what is missing, as a dict"""
    scr = J["scratch"]
    st = {"shoot": str(root), "name": J["name"], "date": J.get("shoot_date", ""), "type": J["shoot_type"]}
    P = read_plan(J)
    applied = bool(P and P.get("applied"))
    raw_files = [f for f in os.listdir(J["src"]) if not f.startswith(".") and os.path.isfile(os.path.join(J["src"], f))] if os.path.isdir(J["src"]) else []
    st["raw_files"] = len(raw_files)
    probe = []
    if os.path.exists(f"{scr}/probe.tsv"):
        with open(f"{scr}/probe.tsv", encoding="utf-8") as fh:
            probe = [l.split("\t")[0] for l in fh if l.strip()]
    st["prepped"] = os.path.exists(f"{scr}/evidence.json")
    st["clips"] = len(probe)
    st["subjects"] = J["subjects"]
    st["questions"] = len(J["questions"])
    res = f"{scr}/results"
    ready, partial, none = [], [], []
    for c in probe:
        final = os.path.exists(f"{res}/{c}.final.json")
        cls = os.path.exists(f"{res}/{c}.classify.json")
        trio = cls and os.path.exists(f"{res}/{c}.visual.json") and os.path.exists(f"{res}/{c}.content.json")
        (ready if final or trio else partial if cls else none).append(c)
    st["records_ready"], st["records_unverified"], st["records_missing"] = len(ready), partial, none
    st["organized"] = applied
    st["mode"] = (P or {}).get("mode", "")
    st["index"] = os.path.exists(root / "START HERE.html")
    cuts = read_csv(os.path.join(root, "Takes Manifest.csv"))
    st["cuts"] = len(cuts)
    interview = []
    if st["prepped"] and applied:
        interview = words_mod.interview_clips(J, {e["clip"]: e for e in read_json(f"{scr}/evidence.json", [])})
    lined, bonus = split_by_line(J, interview)
    st["interview_clips"] = interview
    st["lined_clips"] = lined
    st["words_ready"] = [c for c in interview if os.path.exists(f"{scr}/words/{c}.txt")]
    st["segments_ready"] = [c for c in interview if os.path.exists(f"{res}/{c}.segments.json")]
    n_notes = 0
    if applied:
        for d, dirs, fs in os.walk(root):
            if Path(d) == root:
                dirs[:] = [x for x in dirs if x not in (".work", "Edit", "RAW")]
            n_notes += sum(1 for f in fs if f.endswith(".txt") and not f.startswith(".") and organize.has_mark(os.path.join(d, f)))
    st["notes_files"] = n_notes
    st["left_alone"] = (P or {}).get("left_alone", [])
    if not st["prepped"]:
        st["stage"], st["next"] = "started", "prep did not finish: " + cmd("prep " + q(root))
    elif applied:
        if cuts:
            st["stage"] = "organized, takes cut"
        else:
            st["stage"] = "organized"
        miss_seg = [c for c in lined if c not in st["segments_ready"]]
        if not cuts and J["questions"] and lined:
            st["next"] = "to cut every take and blooper into its own file: " + cmd("takes " + q(root))
        elif cuts and miss_seg:
            st["next"] = f"{len(miss_seg)} interview clip(s) have no segments yet: " + ", ".join(miss_seg[:5])
        else:
            st["next"] = ("done. A notes file beside every video: " + cmd("notes " + q(root))) if not n_notes else "done"
    elif not J["subjects"]:
        st["stage"], st["next"] = "prepped, B-roll folders not set", "read the contact sheets, then: " + cmd("subjects " + q(root) + ' --set "A,B,C,Other"')
    elif none or partial:
        st["stage"] = "classifying"
        st["next"] = f"{len(none)} clip(s) have no record and {len(partial)} are unverified: write results/<clip>.final.json for them (prompts/classify-batch.md)"
    else:
        st["stage"], st["next"] = "ready to organize", cmd("apply " + q(root))
    return st


def cmd_status(a):
    root, jp, J = open_shoot(a.shoot)
    st = shoot_state(root, J)
    if a.json:
        say(json.dumps(st, indent=1))
        return
    say(f"Shoot: {st['shoot']}")
    say(f"Stage: {st['stage']}")
    say(f"  type {st['type']}, {st['clips']} clips prepped" + (f", {st['raw_files']} files in RAW/" if st["raw_files"] else "") + f", {st['questions']} questions or lines")
    say(f"  B-roll folders: {', '.join(st['subjects']) or 'not set'}")
    say(f"  records: {st['records_ready']} of {st['clips']} ready" + (f"; no record: {', '.join(st['records_missing'][:6])}" if st["records_missing"] else "")
        + (f"; unverified: {', '.join(st['records_unverified'][:6])}" if st["records_unverified"] else ""))
    if st["organized"]:
        say(f"  organized{' (copied from another volume)' if st['mode'] == 'copy' else ''}; index {'present' if st['index'] else 'MISSING'}; {st['cuts']} cuts; notes files {st['notes_files']}")
        if st["interview_clips"]:
            say(f"  takes: words for {len(st['words_ready'])} of {len(st['interview_clips'])} interview clips, segments for {len(st['segments_ready'])} of the {len(st['lined_clips'])} that answer a listed line")
    if st["left_alone"]:
        say(f"  not clips of this run (ffprobe could not read them, still in RAW/): {', '.join(st['left_alone'][:5])}")
    say(f"Next: {st['next']}")


# ---------------------------------------------------------------- the command line
def build_parser():
    ap = argparse.ArgumentParser(prog="intake.py", description=__doc__.split("\n")[0], epilog="Run `intake.py <command> -h` for a command's options.")
    sub = ap.add_subparsers(dest="command", required=True, metavar="command")
    s = sub.add_parser("start", help="make the shoot folder from a dropped folder, take the files in, run prep")
    s.add_argument("folder", help="the dropped folder (or a card's folder), or one file")
    s.add_argument("--name", help="the shoot's name (default: the folder's name)")
    s.add_argument("--date", help="YYYY-MM-DD (default: the day the first clip was recorded)")
    s.add_argument("--type", choices=["interview", "ad"], help='"ad" when the person reads a written script line by line (default: interview)')
    s.add_argument("--questions", help="the question sheet or script lines: a .json list, or a text file with one question or line per row")
    s.add_argument("--context", help="what the shoot is about, in a sentence or two")
    s.add_argument("--people", help="who is on camera and who asks the questions")
    s.set_defaults(fn=cmd_start)
    s = sub.add_parser("prep", help="run prep again for a shoot")
    s.add_argument("shoot")
    s.set_defaults(fn=cmd_prep)
    s = sub.add_parser("subjects", help="set the B-roll folders")
    s.add_argument("shoot")
    s.add_argument("--set", required=True, help='comma-separated folder names, for example "Desk Work,Whiteboard,Other"')
    s.set_defaults(fn=cmd_subjects)
    s = sub.add_parser("apply", help="merge the records, plan, file the clips, write the index, run the QC")
    s.add_argument("shoot")
    s.add_argument("--allow-unverified", action="store_true", help="file clips whose record nobody verified (only when told to)")
    s.set_defaults(fn=cmd_apply)
    s = sub.add_parser("takes", help="word timing, then (with --cut) cut every take and blooper into its own file")
    s.add_argument("shoot")
    s.add_argument("--cut", action="store_true", help="cut the takes from the segments files Claude wrote")
    s.add_argument("--handle", type=float, help="seconds of real footage on each side of a cut (default 5)")
    s.add_argument("--dry-run", action="store_true", help="with --cut: print the cuts, change nothing")
    s.set_defaults(fn=cmd_takes)
    s = sub.add_parser("notes", help="a notes .txt beside every video")
    s.add_argument("shoot")
    s.set_defaults(fn=cmd_notes)
    s = sub.add_parser("status", help="what stage the shoot is at and what is missing")
    s.add_argument("shoot")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("reverse", help="undo the organizing: camera files back, everything generated removed")
    s.add_argument("shoot")
    s.set_defaults(fn=cmd_reverse)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("STOP: stopped by you. Nothing was lost; run the same command again to carry on.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:   # one plain line, never a stack trace
        if os.environ.get("CUTROOM_DEBUG"):
            traceback.print_exc()
        print(f"STOP: {type(e).__name__}: {e}. Run `intake.py status <shoot>` to see where the shoot stands; CUTROOM_DEBUG=1 shows where the error came from.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
