#!/usr/bin/env python3
"""Build the cheat sheet the classifying agents read, and the `args` object for classify.js, from a job.json.

  python3 workflow_args.py --job JOB.json      -> prints the args JSON; also writes <scratch>/workflow-args.json and <scratch>/cheat-sheet.txt

The cheat sheet lists the questions (or the script lines of an ad shoot) with their ids, the shot list and the notes. Both routes read it: the batch
prompt (prompts/classify-batch.md) and the workflow script (classify.js, which takes the printed JSON verbatim as its `args`). It needs job.subjects.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from common import stop  # noqa: E402
from job import load_job  # noqa: E402


def people_text(job):
    """who is on the shoot, as one sentence or two. job.people is free text, or {interviewer, subject, extras}"""
    p = job.get("people") or ""
    if isinstance(p, str):
        return p.strip()
    parts = []
    if p.get("interviewer"):
        parts.append(f"Off-camera interviewer: {p['interviewer']}.")
    if p.get("subject"):
        parts.append(f"On-camera subject: {p['subject']}.")
    if p.get("extras"):
        parts.append(str(p["extras"]).strip())
    return " ".join(parts)


def cheat_sheet_text(job):
    qs = job.get("questions") or []
    ad = job.get("shoot_type") == "ad"
    what = "SCRIPT LINES FOR THIS SHOOT. The person on camera reads each line, several attempts each." if ad else "QUESTION LIST FOR THIS SHOOT."
    lines = [f"{job['name'].upper()}: {what} {people_text(job)}".strip(), ""]
    for q in qs:
        lines.append(f"{q['id']}  {q['title']}: {q.get('question', '')}"
                     + (f" Follow-ups: {q['followups']}" if q.get("followups") else "")
                     + (f" ({q['hints']})" if q.get("hints") else ""))
    if qs:
        lines += ["", "ANYTHING ELSE answered on camera that is not one of the listed " + ("lines" if ad else "questions")
                  + " = BONUS (still an interview answer; invent a short reel_title for it)."]
    else:
        lines += ["No question list was supplied. Every on-camera answer gets reel=BONUS and a short reel_title naming its topic; clips on the same topic must reuse the same reel_title wording."]
    if job.get("shot_list"):
        lines += ["", "SHOT LIST / B-ROLL VOCAB: " + str(job["shot_list"])]
    if job.get("notes"):
        lines += ["", "NOTES: " + str(job["notes"])]
    return "\n".join(lines) + "\n"


def write_cheat_sheet(job):
    path = os.path.join(job["scratch"], "cheat-sheet.txt")
    os.makedirs(job["scratch"], exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cheat_sheet_text(job))
    return path


def context_text(job):
    return (f"Raw footage from a shoot called {job['name']} on {job.get('shoot_date') or '(date unknown)'} ({job.get('camera_label') or 'camera unknown'}). "
            f"{job.get('context', '')} {people_text(job)}").strip()


def build_args(job):
    probe = os.path.join(job["scratch"], "probe.tsv")
    if not os.path.exists(probe):
        stop(f"{probe} is missing: run prep first")
    with open(probe, encoding="utf-8") as fh:
        clips = [l.split("\t")[0] for l in fh if l.strip()]
    return {"scratch": job["scratch"], "clips": clips, "context": context_text(job), "subjects": job["subjects"], "has_questions": bool(job.get("questions"))}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    J = load_job(a.job)
    if not J.get("subjects"):
        stop("job.subjects is empty: read at least 5 contact sheets in <scratch>/sheets/ and write the B-roll folders (and Other) first: intake.py subjects <shoot> --set \"A,B,C,Other\"")
    write_cheat_sheet(J)
    args = build_args(J)
    common.atomic_write_json(os.path.join(J["scratch"], "workflow-args.json"), args)
    print(json.dumps(args))
    return args


if __name__ == "__main__":
    main()
