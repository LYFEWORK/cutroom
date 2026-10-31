#!/usr/bin/env python3
"""notes.py: a plain-text notes file beside every video, so whoever opens the clip finds the words right next to it.

   python3 notes.py --job JOB.json

Writes <video name>.txt beside every video file under the shoot folder (interview clips, cut takes, bloopers, B-roll, raw clips, off-topic, unusable):
what the clip is, what is said with m:ss times inside THAT file (a cut take is re-timed to the cut), what is on screen, the flags, and for a cut take its
question or script line, its take number and quality, and where in the file the speech sits ("The rest is handle").
A clip with no speech says so: "NO SPEECH. The camera recorded no sound on this clip." or "NO SPEECH. Natural sound only."
Built from the transcripts, the word timestamps and the two manifests, no agent. A notes file is recognised by its first line (job.MARK), so a re-run
rewrites its own files and reverse removes them, and a .txt of your own is never touched.

Everything here is rebuilt from the .work folder (transcripts, word timestamps, evidence.json). So it refuses, before it touches a single file, when that folder cannot
rebuild what is on the drive: without that check a run after the folder was cleaned would rewrite every notes file as NO SPEECH.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from common import stop  # noqa: E402
from job import MARK, VIDEO_EXT, load_job, read_csv, speech_text  # noqa: E402


def mss(s):
    m, s = divmod(int(max(0, s)), 60)
    return f"{m}:{s:02d}"


def secs(t):
    h, m, s = t.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def srt(scr, clip):
    """[(start seconds, text)] of the clip's whisper SRT, the lines that hold real speech"""
    p = f"{scr}/transcripts/{clip}.srt"
    out = []
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8", errors="replace") as fh:
        blocks = re.split(r"\n\s*\n", fh.read())
    for blk in blocks:
        ls = [l.strip() for l in blk.splitlines() if l.strip()]
        i = next((i for i, l in enumerate(ls) if "-->" in l), None)
        text = speech_text(" ".join(ls[i + 1:])) if i is not None else ""   # "." and "Thank you." over room tone are not speech
        if text:
            out.append((secs(ls[i].split("-->")[0].strip()), text))
    return out


def words(scr, clip, t0, t1):
    """word timestamps inside a cut, regrouped into lines: a new line on a pause, a sentence end, or 16 words"""
    p = f"{scr}/words/{clip}.txt"
    lines, cur = [], []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            for l in fh:
                m = re.match(r"t=([\d.]+)\s+(.+)", l.strip())
                if not m or not t0 <= float(m.group(1)) <= t1:
                    continue
                t, w = float(m.group(1)), m.group(2)
                if cur and (t - cur[-1][0] > 0.9 or re.search(r"[.?!]$", cur[-1][1]) or len(cur) >= 16):
                    lines.append(cur)
                    cur = []
                cur.append((t, w))
    return [(c[0][0], " ".join(w for _, w in c)) for c in lines + ([cur] if cur else [])]


def scratch_gap(J, foot_rows):
    """why the .work folder cannot rebuild the notes, or None"""
    scr = J["scratch"]
    if not os.path.isdir(scr):
        return f"the .work folder is gone ({scr})"
    if not os.path.exists(f"{scr}/evidence.json"):
        return "the .work folder has no evidence.json (what prep measured for every clip)"
    if not os.path.isdir(f"{scr}/transcripts"):
        return "the .work folder has no transcripts folder"
    with open(f"{scr}/evidence.json", encoding="utf-8") as fh:
        known = {e.get("clip") for e in json.load(fh)}
    lost = sorted({r["clip"] for r in foot_rows if r.get("clip")} - known)
    if lost:
        return f"the .work folder has no evidence for {len(lost)} clip(s) of this shoot ({', '.join(lost[:5])}{' ...' if len(lost) > 5 else ''})"
    return None


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    J = load_job(a.job)
    ROOT, SCR = J["root"], J["scratch"]
    foot_rows = read_csv(os.path.join(ROOT, "Footage Manifest.csv"))
    if not foot_rows:
        stop("Footage Manifest.csv is missing or empty: organize the shoot first (intake.py apply)")
    gap = scratch_gap(J, foot_rows)
    if gap:
        stop(f"REFUSED, nothing was touched: {gap}. notes.py rebuilds every notes file from it, so it needs that folder as the run left it.")
    foot_by_path = {r["new_path"]: r for r in foot_rows}
    cuts = {r["file"]: r for r in read_csv(os.path.join(ROOT, "Takes Manifest.csv"))}
    with open(f"{SCR}/evidence.json", encoding="utf-8") as fh:
        EV = {e["clip"]: e for e in json.load(fh)}
    QS = {q["id"]: q for q in J.get("questions", [])}
    label = "SCRIPT LINE  " if J.get("shoot_type") == "ad" else "QUESTION  "
    skip_top = {".work", "Edit", os.path.basename(J["src"])}
    n_txt, unknown = 0, []
    for d, dirs, fs in os.walk(ROOT):
        if os.path.abspath(d) == os.path.abspath(ROOT):
            dirs[:] = [x for x in dirs if x not in skip_top]
        vids = {os.path.splitext(f)[0] for f in fs if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.startswith(".") and ".part." not in f}
        for f in fs:   # a notes file whose video moved or was renamed
            p = os.path.join(d, f)
            if f.endswith(".txt") and not f.startswith(".") and os.path.splitext(f)[0] not in vids:
                with open(p, encoding="utf-8", errors="ignore") as fh:
                    if fh.readline().startswith(MARK):
                        os.remove(p)
        for f in sorted(fs):
            stem, ext = os.path.splitext(f)
            if stem not in vids or ext.lower() not in VIDEO_EXT:
                continue
            rel = os.path.relpath(os.path.join(d, f), ROOT)
            c = cuts.get(rel)
            m = foot_by_path.get(rel)
            if not c and not m:
                unknown.append(rel)   # a video neither manifest knows: there is nothing to say about it
                continue
            clip = c["source_clip"] if c else m["clip"]
            m = m or next((r for r in foot_rows if r["clip"] == clip), {})
            e = EV.get(clip, {})
            if c:
                t0, t1 = float(c["in_s"]), float(c["out_s"])
                said = [(t - t0, x) for t, x in words(SCR, clip, t0, t1)] or [(max(0, t - t0), x) for t, x in srt(SCR, clip) if t0 - 2 <= t <= t1]
                q = QS.get(c["line"])
                dur = t1 - t0
            else:
                said = [] if e.get("digitally_silent") else srt(SCR, clip)
                q = QS.get(m.get("reel", ""))
                dur = float(e.get("duration_s") or m.get("duration_s") or 0)
            out = [MARK + f, " · ".join(x for x in (J["name"], J.get("shoot_date", ""), f"runs {mss(dur)}") if x)
                   + (f" · cut from {clip} {mss(t0)} to {mss(t1)}" if c else ""), ""]
            if q:
                out.append(label + f"{q['id']} · {q.get('question') or q.get('title', '')}")
            if c and c["kind"] == "take":
                out.append(f"TAKE  {c['take']} · {c['quality'].capitalize()}" + (f" · {c['note']}" if c["note"] else ""))
            if c and c["kind"] != "take":
                out.append("BLOOPER" + (f"  {c['note']}" if c["note"] else ""))
            if c:
                out.append(f"SPOKEN  {mss(float(c['spoken_from_s']) - t0)} to {mss(float(c['spoken_to_s']) - t0)} in this file. The rest is handle.")
            if m.get("on_screen"):
                out.append(f"ON SCREEN  {m['on_screen']}")
            if m.get("flags"):
                out.append(f"FLAGS  {m['flags'].replace(';', ', ')}")
            candid = m.get("category") == "candid_talk"
            if said:
                out += ["", "WHAT IS SAID" + (" (candid talk, check it before using the sound)" if candid else "") + "  ·  times are m:ss in this file"]
                out += [f"{mss(t)}  {x}" for t, x in said]
            else:
                out += ["", "NO SPEECH. " + ("The camera recorded no sound on this clip." if e.get("digitally_silent") else "Natural sound only.")]
            write_text(os.path.join(d, stem + ".txt"), "\n".join(out) + "\n")
            n_txt += 1
    print(f"wrote {n_txt} notes files (a .txt beside every video)")
    if unknown:
        print(f"[warn] no notes for {len(unknown)} video(s) that neither manifest lists: {', '.join(unknown[:5])}", flush=True)
    return n_txt


if __name__ == "__main__":
    main()
