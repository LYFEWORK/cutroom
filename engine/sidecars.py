#!/usr/bin/env python3
"""sidecars.py <video.mp4> [--words words.json] [--caption caption.txt | --caption-text "..."] [--title "Title"]
   sidecars.py --check <folder>

Every finished video ships with two files beside it:
  <stem> - Transcript.md   what is said in THIS file, in timestamped paragraphs
  <stem> - Caption.txt     the post caption, paste-ready: the caption and nothing else

Transcript source: --words (any words file: {"words": [...]} or a bare list, with the keys text|word, start|start_s and end|end_s, in the video's
own time) or, without it, whisper on the video's audio. Either way it is the finished file's words, so a cutdown gets its own transcript. A new
paragraph starts after a pause of more than 1.2 s, or on a sentence end once about 12 s have run.

Caption source: --caption <file> or --caption-text. With neither, a caption already written is kept; otherwise a CAPTION NEEDED placeholder is
written and the script prints `CAPTION-NEEDED <path>`. A video with a placeholder caption is not finished: write the caption and run this again
with --caption.

--check <folder> lists every finished .mp4/.mov under the folder that is missing a sidecar, has a placeholder caption or an empty transcript, and
exits 1 if there is any. It STOPs (exit 2) when the folder does not exist or holds no finished video: a check that looked at nothing must not pass.
Not finished videos, matched on the path RELATIVE to the folder and case-insensitively: anything under a B-Roll, qc, build or evidence folder,
symlinks, and the cutter's working copies (*-cut.mp4, *.rawaudio.mov).
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import fnmatch
import glob
import json
import os
import re
import subprocess
import tempfile

import common

stop = common.stop
PENDING = "CAPTION NEEDED"
USAGE = "sidecars.py <video.mp4> [--words words.json] [--caption caption.txt | --caption-text \"...\"] [--title \"Title\"] | sidecars.py --check <folder>"
SKIP_DIRS = {"b-roll", "broll", "qc", "build", "evidence"}
SKIP_NAMES = ("*-cut.mp4", "*.rawaudio.mov")


def read_text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write_text(p, s):   # a caption carries emoji: utf-8 whatever the locale says
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def paths(video):
    stem = os.path.splitext(video)[0]
    return stem + " - Caption.txt", stem + " - Transcript.md"


def stamp(s):
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def load_words(p):
    """[(text, start, end)] from a words file in any of the shapes the engine writes"""
    d = json.load(open(p))
    ws = d.get("words", []) if isinstance(d, dict) else d
    out = []
    for w in ws:
        t = (w.get("text") or w.get("word") or "").strip()
        s = w.get("start_s", w.get("start"))
        e = w.get("end_s", w.get("end", s))
        if t and s is not None:
            out.append((t, float(s), float(e)))
    return out


def whisper_segments(video):
    """[(sentence, start, end)] from whisper on the video's own audio, one sentence at a time, each interpolated by its share of the segment"""
    import words as word_engine
    model = support.whisper_model()
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav], capture_output=True, text=True)
        if r.returncode:
            stop("sidecars: could not read the audio of the video: " + " ".join(r.stderr.split())[-300:])
        lang = support.language()
        prompt = word_engine.prompt_for(lang)
        r = subprocess.run(["whisper-cli", "-m", model, "-f", wav, "-l", lang] + (["--prompt", prompt] if prompt else []) + ["-oj", "-of", os.path.join(td, "t"), "-np"],
                           capture_output=True, text=True)
        if r.returncode:
            stop("sidecars: whisper-cli failed: " + " ".join(r.stderr.split())[-300:])
        seg = json.load(open(os.path.join(td, "t.json")))["transcription"]
    out = []
    for s in seg:
        a, b, t = s["offsets"]["from"] / 1000, s["offsets"]["to"] / 1000, s["text"].strip()
        parts = [x for x in re.split(r"(?<=[.?!])\s+", t) if x]
        total = sum(len(x) for x in parts) or 1
        c = 0
        for x in parts:
            out.append((x, a + (b - a) * c / total, a + (b - a) * (c + len(x)) / total))
            c += len(x)
    return out


def paragraphs(units):
    """units = (text, start, end). A new paragraph on a sentence end once about 12 s have run, or on a gap over 1.2 s."""
    paras, cur, t0, prev_end = [], [], None, None
    for t, s, e in units:
        if cur and prev_end is not None and s - prev_end > 1.2:
            paras.append((t0, cur))
            cur = []
        if not cur:
            t0 = s
        cur.append(t)
        prev_end = e
        if s - t0 >= 12 and t.rstrip().endswith((".", "?", "!")):
            paras.append((t0, cur))
            cur = []
    if cur:
        paras.append((t0, cur))
    cap = lambda t: re.sub(r"(^|[.?!]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    return [(a, cap(re.sub(r"\s+", " ", " ".join(ws)).strip())) for a, ws in paras]


def duration(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def write(video, words=None, caption=None, caption_text=None, title=None):
    """write the two sidecars beside `video`; returns the caption's state: "written", "kept" or "pending" """
    video = os.path.abspath(video)
    if not os.path.exists(video):
        stop(f"sidecars: no video {video}")
    title = title or os.path.splitext(os.path.basename(video))[0]
    cap_p, tx_p = paths(video)
    units = load_words(words) if words else []
    if not units:
        units = whisper_segments(video)    # no words file, or one that parsed to nothing: the file's own audio decides
    paras = paragraphs(units)
    n = sum(len(p.split()) for _, p in paras)
    body = [f"# {title}: Transcript", "", f"{stamp(duration(video))} · {n} words · {os.path.basename(video)}", ""]
    for a, p in paras:
        body += [f"**[{stamp(a)}]** {p}", ""]
    write_text(tx_p, "\n".join(body))
    if caption:
        caption_text = read_text(caption)
    if caption_text and caption_text.strip():
        write_text(cap_p, caption_text.strip() + "\n")
        state = "written"
    elif os.path.exists(cap_p) and PENDING not in read_text(cap_p):
        state = "kept"
    else:
        write_text(cap_p, f"{PENDING} for {title}: write the post caption, then run sidecars.py again with --caption.\n\nTranscript: {os.path.basename(tx_p)}\n")
        state = "pending"
        print(f"CAPTION-NEEDED {cap_p}")
    print(f"sidecars {os.path.basename(video)}: transcript {n} words, caption {state}")
    return state


def deliverables(folder):
    """every video under `folder` that ships: not a symlink, not under a skipped folder, not a working copy"""
    out = []
    for v in sorted(glob.glob(os.path.join(glob.escape(folder), "**", "*"), recursive=True)):
        if not v.lower().endswith((".mp4", ".mov")) or os.path.islink(v) or not os.path.isfile(v):
            continue
        rel = os.path.relpath(v, folder).lower().split(os.sep)
        if any(c in SKIP_DIRS for c in rel[:-1]):
            continue
        if any(fnmatch.fnmatch(rel[-1], p) for p in SKIP_NAMES):
            continue
        out.append(v)
    return out


def transcript_words(tx_p):
    m = re.search(r"(\d+) words", open(tx_p, encoding="utf-8").read(2000))
    return int(m.group(1)) if m else 0


def check(folder):
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        stop(f"sidecars check: no such folder: {folder}")
    vids = deliverables(folder)
    if not vids:
        stop(f"sidecars check: {folder} holds no finished video (looked under it for .mp4/.mov, skipping B-Roll, qc, build and evidence folders, symlinks and -cut working copies)")
    bad = []
    for v in vids:
        cap_p, tx_p = paths(v)
        if not os.path.exists(tx_p):
            bad.append(f"NO-TRANSCRIPT {v}")
        elif transcript_words(tx_p) == 0:
            bad.append(f"EMPTY-TRANSCRIPT {tx_p}")
        if not os.path.exists(cap_p):
            bad.append(f"NO-CAPTION {v}")
        elif PENDING in read_text(cap_p):
            bad.append(f"CAPTION-NEEDED {cap_p}")
    for b in bad:
        print(b)
    print(f"sidecars check {folder}: {'PASS' if not bad else f'{len(bad)} missing'} ({len(vids)} videos)")
    return 1 if bad else 0


def _main():
    a = sys.argv[1:]
    if a[0] == "--check":
        sys.exit(check(a[1]))
    opt, pos, i = {}, [], 0
    while i < len(a):
        if a[i] in ("--words", "--caption", "--caption-text", "--title"):
            opt[a[i][2:].replace("-", "_")] = a[i + 1]
            i += 2
        else:
            pos.append(a[i])
            i += 1
    write(pos[0], **opt)


if __name__ == "__main__":
    support.cli(_main, USAGE)
