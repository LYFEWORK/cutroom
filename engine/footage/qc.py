#!/usr/bin/env python3
"""Proof gate. Prints `QC OK`, or one line per problem, and exits 1 on any problem.

   python3 qc.py --job JOB.json [-v]

Checks: every planned file exists · the byte total matches pre_move_bytes (a missing pre-move total FAILS) · no video left in the source folder (a file prep could
not read is named, not failed) · manifest row count · ffprobe plays EVERY clip and EVERY cut file · Takes Manifest.csv rows match the files on disk (both ways),
each cut's duration matches its in/out, every cut's raw clip is in Raw Clips/ · every START HERE.html link (URL-decoded) resolves, one card per clip and per take,
every card has a thumbnail · README present · no clip is left UNCLASSIFIED.
-v prints every check with ok or FAIL, not only the problems.
"""
import argparse
import html
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from job import VIDEO_EXT, bad_takes_folder, load_job, read_csv, read_plan  # noqa: E402


def probe_duration(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except ValueError:
        return None


def probe_all(paths):
    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(probe_duration, paths))


def run(job, say=None):
    """-> (problems, checks): every check as (ok, message). problems is the list of the messages that failed"""
    ROOT, SRC = job["root"], job["src"]
    checks = []

    def check(cond, msg):
        checks.append((bool(cond), msg))
    P = read_plan(job)   # the scratch copy, else Edit/plan.json
    if P is None:
        return ["no plan.json in the scratch folder or in Edit/ (organize the shoot first: intake.py apply)"], [(False, "plan.json")]
    moves = P["moves"]
    cuts = read_csv(os.path.join(ROOT, "Takes Manifest.csv"))
    missing = [m["rel"] for m in moves if not os.path.exists(os.path.join(ROOT, m["rel"]))]
    check(not missing, f"all {len(moves)} planned files exist" + (f": missing {missing[:3]}" if missing else ""))
    total = sum(os.path.getsize(os.path.join(ROOT, m["rel"])) for m in moves if os.path.exists(os.path.join(ROOT, m["rel"])))
    pre = P.get("pre_move_bytes")
    check(pre is not None and pre == total, f"byte total {total / 1e9:.2f} GB" + (": plan.json has no pre_move_bytes (re-plan lost it?)" if pre is None else f" == pre-move {pre / 1e9:.2f} GB"))
    left_alone = set(P.get("left_alone") or [])
    left = sorted(f for f in os.listdir(SRC) if not f.startswith(".") and os.path.splitext(f)[1].lower() in VIDEO_EXT and f not in left_alone) if os.path.isdir(SRC) else []
    check(not left, "no video left in the source folder" + (f": still there {left[:5]}" if left else ""))
    rows = read_csv(os.path.join(ROOT, "Footage Manifest.csv"))
    check(len(rows) == len(moves), f"manifest rows {len(rows)} == clips {len(moves)}")

    # every clip and every cut must play
    present = [m["rel"] for m in moves if os.path.exists(os.path.join(ROOT, m["rel"]))]
    bad_clips = [rel for rel, d in zip(present, probe_all([os.path.join(ROOT, r) for r in present])) if d is None]
    check(not bad_clips, f"ffprobe plays all {len(moves)} clips" + (f": unplayable {bad_clips[:3]}" if bad_clips else ""))

    if cuts:
        gone = [c["file"] for c in cuts if not os.path.exists(os.path.join(ROOT, c["file"]))]
        check(not gone, f"all {len(cuts)} cut files in Takes Manifest.csv exist" + (f": missing {gone[:3]}" if gone else ""))
        listed = {c["file"] for c in cuts}
        bad_home = bad_takes_folder(job)
        on_disk = set()
        for d, dirs, fs in os.walk(ROOT):
            rel_d = os.path.relpath(d, ROOT)
            if rel_d == ".":
                dirs[:] = [x for x in dirs if x not in (".work",)]
            for f in fs:
                if f.lower().endswith(".mov") and not f.startswith(".") and not f.endswith(".part.mov"):   # "._<cut>.mov" is the sidecar macOS writes beside a file on an exFAT drive, not a cut
                    if rel_d.startswith("Interview") or rel_d.startswith("B-Roll/Bloopers") or (bool(bad_home) and rel_d.split("/")[0] == bad_home):
                        on_disk.add(os.path.relpath(os.path.join(d, f), ROOT))
        stray = sorted(on_disk - listed - {m["rel"] for m in moves})   # a .MOV camera original no take was cut from is a filed clip, not a stray cut
        check(not stray, "every cut file on disk is listed in Takes Manifest.csv" + (f": unlisted {stray[:3]}" if stray else ""))
        leftovers = [os.path.relpath(os.path.join(d, f), ROOT) for d, dirs, fs in os.walk(ROOT) if ".work" not in os.path.relpath(d, ROOT).split(os.sep) for f in fs if f.endswith(".part.mov")]
        check(not leftovers, "no half-written .part.mov files" + (f": {leftovers[:3]}" if leftovers else ""))
        have = [c for c in cuts if os.path.exists(os.path.join(ROOT, c["file"]))]
        durs = probe_all([os.path.join(ROOT, c["file"]) for c in have])
        bad = [c["file"] for c, d in zip(have, durs) if d is None]
        off = [f"{c['file']} is {d:.1f}s, manifest says {float(c['out_s']) - float(c['in_s']):.1f}s" for c, d in zip(have, durs)
               if d is not None and abs(d - (float(c["out_s"]) - float(c["in_s"]))) > 1.0]
        check(not bad, f"ffprobe plays all {len(cuts)} cuts" + (f": unplayable {bad[:3]}" if bad else ""))
        check(not off, "every cut's duration matches its in/out (+-1 s)" + (f": {off[:2]}" if off else ""))
        raw_gone = [c["raw_path"] for c in cuts if not os.path.exists(os.path.join(ROOT, c["raw_path"]))]
        check(not raw_gone, "every cut's raw clip is in Raw Clips/" + (f": missing {raw_gone[:3]}" if raw_gone else ""))

    idx = os.path.join(ROOT, "START HERE.html")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as fh:
            h = fh.read()
        hrefs = re.findall(r'class="thumb" href="([^"]+)"', h)
        rels = [unquote(html.unescape(x)) for x in hrefs]   # index.py URL-encodes, so decode before looking at the disk
        broken = [x for x in rels if not os.path.exists(os.path.join(ROOT, x))]
        want = [m["rel"] for m in moves] + [c["file"] for c in cuts]
        check(len(hrefs) == len(want) and not broken, f"index has {len(hrefs)} links for {len(want)} clips and cuts, {len(broken)} broken")
        check(sorted(rels) == sorted(want), "index lists every clip and every cut exactly once"
              + ("" if sorted(rels) == sorted(want) else f": not listed {sorted(set(want) - set(rels))[:3]}, extra {sorted(set(rels) - set(want))[:3]}"))
        n_thumb = len(re.findall(r'<img src="data:image/jpeg;base64,[A-Za-z0-9+/=]{100,}"', h))
        check(n_thumb == len(want), f"index thumbnails embedded: {n_thumb}/{len(want)}")
    else:
        check(False, "START HERE.html exists")
    check(os.path.exists(os.path.join(ROOT, "README.txt")), "README.txt exists")
    unclassified = [m["rel"] for m in moves if "UNCLASSIFIED" in m["rel"]]
    check(not unclassified, "no UNCLASSIFIED clips" + (f": {unclassified}" if unclassified else ""))
    return [msg for ok, msg in checks if not ok], checks


def report(job, verbose=False, out=print):
    """run the checks and print the result the way the command line does. -> True when everything passed"""
    problems, checks = run(job)
    if verbose:
        for ok, msg in checks:
            out(("  ok   " if ok else "  FAIL ") + msg)
    if problems:
        for p in problems:
            out("QC: " + p)
        return False
    out("QC OK")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    ok = report(load_job(a.job), a.verbose)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
