#!/usr/bin/env python3
"""hear_local.py <audio-or-video> <out.words.json> [--language auto|en|...]

The caption chain's default transcriber: a thin caller of engine/words.py, the local word engine (whisper.cpp for the text, WhisperX's aligner for each
word's own start and end, all on this Mac, no service, no key). It writes the words.json contract the rest of the chain reads:

  {"source", "transcript", "words": [{"text", "start", "end", "filler", "double", "cap"}]}

and checks what came back before the take goes on: a words list with a text, a start and an end on every word, in time order, each flagged. A word engine
that is not set up (no model, no word-timing python) stops with its own plain line. Without --language the engine's default is used (config.json
whisper_language, "en" unless changed); "auto" lets whisper decide.

Local transcription differs from a hosted one in ways the chain is built for: no spoken ums unless whisper writes them, word times that come from the
aligner, and punctuation that is whisper's own (the soft build reads sentence ends from it).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from keys import cli, EngineStop  # noqa: E402
import hear  # noqa: E402

WORDS_PY = os.path.join(os.path.dirname(HERE), "words.py")


def check(doc, where):
    """the words contract, or EngineStop with one plain line naming what is wrong"""
    words = doc.get("words") if isinstance(doc, dict) else None
    if not isinstance(words, list) or not words:
        raise EngineStop(f"no speech detected: the transcript of {where} came back empty. Check the audio track (silent, no dialogue, or the wrong stream)")
    prev = -1.0
    for i, w in enumerate(words):
        if not (isinstance(w, dict) and isinstance(w.get("text"), str) and isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float))):
            raise EngineStop(f"word {i} of {where} has no text, start and end")
        if w["end"] < w["start"] or w["start"] < prev - 1e-6:
            raise EngineStop(f"word {i} ({w['text']!r}) of {where} is out of time order")
        prev = w["start"]
    if any("filler" not in w for w in words):
        doc["words"] = hear.flag(words)
    doc.setdefault("source", "local")
    doc.setdefault("transcript", " ".join(w["text"] for w in doc["words"]))
    return doc


def transcribe(src, out, language=None):
    """Run the word engine over `src` and write the checked words.json to `out`. Returns the document."""
    if not os.path.isfile(WORDS_PY):
        raise EngineStop(f"the word engine is not installed ({WORDS_PY} is missing)")
    cmd = [sys.executable, WORDS_PY, src, "--out", out] + (["--language", language] if language else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        lines = [x.strip() for x in (r.stdout + "\n" + r.stderr).splitlines() if x.strip()]
        stop = next((x for x in lines if x.startswith("STOP:")), None)
        raise EngineStop((stop[len("STOP:"):].strip() if stop else (lines[-1] if lines else "the word engine failed with no output")))
    with open(out) as f:
        doc = check(json.load(f), os.path.basename(src))
    with open(out, "w") as f:
        json.dump(doc, f, indent=1)
    return doc


def _main():
    a = sys.argv[1:]
    language = None
    if "--language" in a:
        i = a.index("--language")
        language = a[i + 1]
        del a[i:i + 2]
    src, out = a[0], a[1]
    d = transcribe(src, out, language)
    print(f"{len(d['words'])} words, {sum(bool(w.get('filler')) for w in d['words'])} fillers, {sum(bool(w.get('double')) for w in d['words'])} doubles -> {out}")


if __name__ == "__main__":
    cli(_main, "hear_local.py <audio-or-video> <out.words.json> [--language auto|en|...]")
