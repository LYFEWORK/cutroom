#!/usr/bin/env python3
"""sidecars_fallback.py <video> [--words words.json] [--caption file | --caption-text "..."]

What deliver.sh runs when engine/sidecars.py is not in the install: it writes the two files that ship beside every video, in the video's own folder,
named after it: "<name> - Transcript.md" (the take's words, from words.json) and "<name> - Caption.txt" (the post caption, or a CAPTION NEEDED line
when there is none). Same command line as sidecars.py's own; delete this file once that one is in place."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from keys import cli, EngineStop  # noqa: E402


def _main():
    a = sys.argv[1:]
    video = a[0]
    opt = {}
    for flag in ("--words", "--caption", "--caption-text"):
        if flag in a:
            opt[flag] = a[a.index(flag) + 1]
    if not os.path.isfile(video):
        raise EngineStop(f"no video at {video}")
    stem = os.path.splitext(video)[0]
    title = os.path.basename(stem)
    transcript = ""
    if opt.get("--words"):
        with open(opt["--words"]) as f:
            doc = json.load(f)
        transcript = doc.get("transcript") or " ".join(w["text"] for w in doc.get("words") or [])
    caption = opt.get("--caption-text") or ""
    if opt.get("--caption"):
        with open(opt["--caption"]) as f:
            caption = f.read().strip()
    with open(f"{stem} - Transcript.md", "w") as f:
        f.write(f"# {title}\n\n{transcript.strip() or '(no transcript)'}\n")
    with open(f"{stem} - Caption.txt", "w") as f:
        f.write((caption.strip() or "CAPTION NEEDED") + "\n")
    print(f"sidecars: {title} - Transcript.md, {title} - Caption.txt")


if __name__ == "__main__":
    cli(_main, "sidecars_fallback.py <video> [--words words.json] [--caption file | --caption-text \"...\"]")
