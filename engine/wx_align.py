#!/usr/bin/env python3
"""wx_align.py <audio.wav> <whisper.json> <out.wx.json> [--language en] [--device cpu]

Force-aligns whisper-cli's segments with WhisperX's wav2vec2 model: every word gets its own start and end in the audio's time. There is no voice
activity or speaker path, and nothing is downloaded but the alignment weights, once, the first time a language is used.

This script runs under the word-timing python (config.json "venv_python": the one with whisperx and torch), never under the system python.
words.py is the one that calls it. Any failure is one "STOP: ..." line and exit 2.
"""
import sys

if sys.version_info < (3, 12):   # plain code only above this line: it has to compile on the oldest Python that might run it
    print("STOP: Cutroom needs Python 3.12 or newer; this is Python %d.%d. Install a newer one (brew install python@3.12) and run again."
          % (sys.version_info[0], sys.version_info[1]), file=sys.stderr)
    sys.exit(2)

import json


def stop(msg):
    print("STOP: " + " ".join(str(msg).split()), file=sys.stderr)
    sys.exit(2)


def option(argv, name, default):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return default


def main(argv):
    language, device = option(argv, "--language", "en"), option(argv, "--device", "cpu")
    if len(argv) != 3:
        stop("usage: wx_align.py <audio.wav> <whisper.json> <out.wx.json> [--language en] [--device cpu]")
    wav, cli_json, out = argv
    try:
        import whisperx
    except ImportError as e:
        stop(f"whisperx is not installed under {sys.executable}: run install.sh ({e})")
    with open(cli_json) as f:
        d = json.load(f)
    segs = []
    for s in d["transcription"]:
        t = s["text"].strip()
        if t:
            segs.append({"start": s["offsets"]["from"] / 1000, "end": s["offsets"]["to"] / 1000, "text": t})
    if not segs:
        stop(f"whisper heard no speech in {wav}")
    audio = whisperx.load_audio(wav)
    try:
        model_a, meta = whisperx.load_align_model(language_code=language, device=device)
    except Exception as e:   # no alignment model for the language, or the weights could not be fetched
        stop(f"no alignment model for language {language!r} ({type(e).__name__}: {e}); set whisper_language in config.json to a language WhisperX can align")
    res = whisperx.align(segs, model_a, meta, audio, device, return_char_alignments=False)
    with open(out, "w") as f:
        json.dump(res, f, indent=1)
    n = sum(len(s.get("words", [])) for s in res["segments"])
    print("aligned words:", n, "->", out)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SystemExit:
        raise
    except Exception as e:   # a traceback is never the answer
        stop(f"{type(e).__name__}: {e}")
