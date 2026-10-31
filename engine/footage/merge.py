#!/usr/bin/env python3
"""For any clip whose verifiers disputed the record but no judge's final landed (a stalled agent, an interrupted run),
build <clip>.final.json by applying the verifiers' field corrections onto the classify record.
Visual corrections first, content corrections second (the content lens owns the fields that come from the transcript).
A clip that already has a final.json is left alone, and a clip both verifiers agreed on needs none (its classify record is the record).

   python3 merge.py --job JOB.json

Only the workflow route (classify.js) and the two-pass batch route write verifier files; a clip written straight to final.json by the one-pass prompt is untouched here."""
import argparse
import glob
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
from job import CATEGORIES, load_job  # noqa: E402

CATS = set(CATEGORIES)
SHOTS = {"wide", "medium", "close_up", "mixed"}
CAMS = {"static", "handheld", "pan", "tilt", "push_in", "orbit", "mixed"}


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        stop(f"{path} is not valid JSON ({e}): rewrite that file, then run this again")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    J = load_job(a.job)
    R = f"{J['scratch']}/results"
    subjects = set(J["subjects"]) | {"Other"}   # a correction naming any other folder falls back to Other
    made = []
    for f in sorted(glob.glob(f"{R}/*.classify.json")):
        c = os.path.basename(f)[:-len(".classify.json")]
        if os.path.exists(f"{R}/{c}.final.json"):
            continue
        v = _load(f"{R}/{c}.visual.json", {"agree": True, "corrections": {}, "issues": []})
        t = _load(f"{R}/{c}.content.json", {"agree": True, "corrections": {}, "issues": []})
        if v.get("agree") and t.get("agree"):
            continue
        rec = _load(f, {})
        applied = []
        for lens, vd in (("visual", v), ("content", t)):
            for k, val in (vd.get("corrections") or {}).items():
                if k not in rec or k == "clip":
                    continue
                if k == "subject_folder" and val not in subjects:
                    val = "Other"
                if k == "category" and val not in CATS:
                    continue
                if k == "shot" and val not in SHOTS:
                    continue
                if k == "camera" and val not in CAMS:
                    continue
                if k == "flags" and not isinstance(val, list):
                    continue
                if k == "usable" and not isinstance(val, bool):
                    continue
                rec[k] = val
                applied.append(f"{lens}:{k}")
        rec["notes"] = (rec.get("notes", "") + f" | FINAL BY MERGE (no judge record): applied {', '.join(applied) or 'no field corrections'}").strip()
        common.atomic_write_json(f"{R}/{c}.final.json", rec)
        made.append((c, applied))
    for c, ap_ in made:
        print(c, "->", ap_)
    print(f"merged {len(made)} fallback finals")
    return made


if __name__ == "__main__":
    main()
