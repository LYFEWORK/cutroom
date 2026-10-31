#!/usr/bin/env python3
"""Word-level whisper for the clips that will be cut into takes. One word per line, `t=<seconds> <word>`, which takes.py and notes.py read.

   python3 words.py --job JOB.json [--clips C0001 C0002] [--skip-existing]

Default clips = every clip the records call `interview` (results/<clip>.final.json, else .classify.json), minus digitally silent ones.
Runs whisper-cli on <scratch>/audio/<clip>.wav with -ml 1 -sow -oj, then flattens the JSON to <scratch>/words/<clip>.txt.
Stops (exit 2) on a missing wav, a whisper error or an empty result: a clip with no words cannot be cut into takes.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
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


def flatten(raw_json, out_txt):
    with open(raw_json, encoding="utf-8") as fh:
        d = json.load(fh)
    n = 0
    with open(out_txt, "w", encoding="utf-8") as fh:
        for seg in d.get("transcription", []):
            w = re.sub(r"\s+", " ", seg.get("text", "")).strip()
            if not w or re.fullmatch(r"\[[^\]]*\]|\([^)]*\)", w):
                continue   # [_BEG_], [BLANK_AUDIO], (music)
            fh.write(f"t={seg['offsets']['from'] / 1000:.2f} {w}\n")
            n += 1
    return n


def interview_clips(job, ev):
    """the clips the records call interview and that are not digitally silent, in clip order"""
    clips = []
    for c in sorted(ev):
        for suf in ("final", "classify"):
            f = f"{job['scratch']}/results/{c}.{suf}.json"
            if os.path.exists(f):
                with open(f, encoding="utf-8") as fh:
                    if json.load(fh).get("category") == "interview" and not ev[c]["digitally_silent"]:
                        clips.append(c)
                break
    return clips


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--clips", nargs="*")
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args(argv)
    J = load_job(a.job)
    SCR = J["scratch"]
    model, lang = J["whisper_model"], J["whisper_language"]
    if not shutil.which("whisper-cli"):
        stop("whisper-cli not found: install whisper.cpp first (brew install whisper-cpp)")
    if not os.path.exists(model):
        stop(f"whisper model missing: {model} (run the setup, or set whisper_model in ~/.cutroom/config.json)")
    if not os.path.exists(f"{SCR}/evidence.json"):
        stop(f"{SCR}/evidence.json is missing: run prep first")
    with open(f"{SCR}/evidence.json", encoding="utf-8") as fh:
        ev = {e["clip"]: e for e in json.load(fh)}
    clips = a.clips
    if not clips:
        clips = interview_clips(J, ev)
        if not clips:
            stop("no interview clips in results/ (classify the clips first) and none named with --clips")
    os.makedirs(f"{SCR}/words", exist_ok=True)
    bad = []
    for c in clips:
        wav, out = f"{SCR}/audio/{c}.wav", f"{SCR}/words/{c}"
        if a.skip_existing and os.path.exists(out + ".txt"):
            continue
        if not os.path.exists(wav):
            bad.append(f"{c}: {wav} missing (run prep)")
            continue
        for f in (out + ".json", out + ".txt"):
            if os.path.exists(f):
                os.remove(f)
        r = subprocess.run(["whisper-cli", "-m", model, "-f", wav, "-l", lang, "-t", "8", "-np", "-ml", "1", "-sow", "-oj", "-of", out], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(out + ".json"):
            bad.append(f"{c}: whisper-cli exit {r.returncode} {r.stderr.strip()[-200:]}")
            continue
        n = flatten(out + ".json", out + ".txt")
        if not n:
            bad.append(f"{c}: no words came back")
            continue
        print(f"[words] {c}: {n} words -> {out}.txt", flush=True)
    if bad:
        stop("[words] FAILED\n  " + "\n  ".join(bad))
    print(f"[words] done, {len(clips)} clips")
    return clips


if __name__ == "__main__":
    main()
