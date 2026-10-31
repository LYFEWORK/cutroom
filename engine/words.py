#!/usr/bin/env python3
"""words.py <audio-or-video> --out words.json [--language auto|en|...] [--work DIR] [--logs DIR] [--device cpu]
words.py --pack <name> <wxdir> <out.txt>

The local word engine: every spoken word of a take with its own start and end, on this Mac, with no service and no key.

    1. a 16 kHz mono wav of the first audio track (a dead channel, 30 dB under the loudest, is dropped rather than averaged in)
    2. whisper-cli (the large-v3-turbo model) with a short punctuated, disfluent prompt (DEFAULT_PROMPT): segments, an SRT and a JSON
    3. wx_align.py under the word-timing python: WhisperX's wav2vec2 aligner gives every word a start and an end
    4. words.json, the engine's word contract:

       {"source": "whisper", "language": "en", "transcript": "...",
        "words": [{"text": "Hello,", "start": 0.52, "end": 0.9, "filler": false, "double": false, "cap": true}, ...]}

       filler   the word is um, uh or one of the others in cut.FILLERS (the ONE filler list)
       double   the same word again within 0.5 s of itself ("is is")
       cap      a capital that is not at the start of a sentence (a name, "I")
       unaligned (only when true) the aligner could not stamp the word (a number, a symbol): it sits between its neighbours, never dropped

Language is config.json "whisper_language" ("en" by default); --language overrides it, and "auto" lets whisper decide and aligns in what it heard.
--work keeps the wav, the whisper json and srt and the aligned json (<stem>.wav, .json, .srt, .wx.json) in a folder, and a second run over the same
audio reuses them instead of listening again; without it they live in a temporary folder. --logs keeps the two tools' output.
A missing model or word-timing python is one STOP line that says which.

--pack writes the transcript pack for one prepared take (numbered segments, the word stream, counts) from <wxdir>/<name>.json, .wx.json and .wav.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import re
import shutil
import subprocess
import tempfile

import common
import sound
import cut
from cut import FILLERS

load_words = cut.load_words   # the aligned-json reader lives with the cutter and is part of this module's API

HERE = os.path.dirname(os.path.abspath(__file__))
USAGE = ("words.py <audio-or-video> --out words.json [--language auto|en|...] [--work DIR] [--logs DIR] [--device cpu] | "
         "words.py --pack <name> <wxdir> <out.txt>")
DOUBLE_GAP_S = 0.5
DEFAULT_PROMPT = cut.DEFAULT_PROMPT   # the short punctuated, disfluent prompt whisper is shown: see cut.py
prompt_for = cut.prompt_for


def _norm(t):
    return re.sub(r"[^a-z0-9']", "", t.lower())


def wx_words(doc):
    """The aligned json -> words.json's word shape. A word the aligner could not stamp (it has no start or end: a number, a symbol) is kept, placed
    between its neighbours the way the cutter's own loader places it, and marked "unaligned": a spoken word never vanishes from the transcript."""
    rows = []
    for s in doc.get("segments", []):
        for w in s.get("words", []):
            text = (w.get("word") or w.get("text") or "").strip()
            if text:
                rows.append({"text": text, "start": w.get("start"), "end": w.get("end")})
    for i, w in enumerate(rows):
        if w["start"] is None or w["end"] is None:
            prev_end = next((rows[j]["end"] for j in range(i - 1, -1, -1) if rows[j]["end"] is not None), 0.0)
            nxt = next((rows[j]["start"] for j in range(i + 1, len(rows)) if rows[j]["start"] is not None), prev_end + 0.4)
            w["start"] = prev_end + 0.02
            w["end"] = max(w["start"] + 0.05, nxt - 0.02)
            w["unaligned"] = True
    return [dict(w, start=round(float(w["start"]), 3), end=round(float(w["end"]), 3)) for w in rows]


def flag(words):
    """each word with `filler`, `double` and `cap` added"""
    out, prev = [], None
    for w in words:
        n = _norm(w["text"])
        f = dict(w)
        f["filler"] = n in FILLERS
        f["double"] = bool(prev and n == prev["n"] and n and (w["start"] - prev["end"]) < DOUBLE_GAP_S)
        f["cap"] = w["text"][:1].isupper() and not (prev is None or prev["text"].endswith((".", "?", "!")))
        out.append(f)
        prev = {"n": n, "end": w["end"], "text": w["text"]}
    return out


def _is_16k_mono_wav(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return str(path).lower().endswith(".wav") and r.stdout.strip() == "pcm_s16le,16000,1"


def _tail(path, n=3):
    try:
        lines = [l.strip() for l in open(path, errors="replace").read().splitlines() if l.strip() and "ggml_" not in l and "load_backend" not in l]
    except OSError:
        return "no log"
    return " | ".join(lines[-n:]) or "no output"


def hear(src, out=None, language=None, work=None, logs=None, device=None):
    """The words.json contract for `src` (any file with an audio track), written to `out` when given. See the module doc for the steps and files."""
    cfg = support.config()
    language = language or support.language()
    device = device or str(cfg.get("align_device") or "cpu")
    model, py = support.whisper_model(), support.venv_python()
    if not shutil.which("whisper-cli"):
        common.stop("whisper-cli is not installed: run install.sh (it installs whisper-cpp)")
    src = os.path.abspath(str(src))
    if not os.path.isfile(src):
        common.stop(f"no such file: {src}")
    temp = work is None
    work = os.path.abspath(work or tempfile.mkdtemp(prefix="words-"))
    os.makedirs(work, exist_ok=True)
    if logs:
        os.makedirs(logs, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    base = os.path.join(work, stem)
    wav, wj, wx = (base + ".wav"), base + ".json", base + ".wx.json"
    try:
        if _is_16k_mono_wav(src):
            wav = src
        elif not (os.path.isfile(wav) and os.path.getmtime(wav) >= os.path.getmtime(src)):
            sound.downmix(src, wav, 16000)
        fresh = (os.path.isfile(wx) and os.path.getsize(wx) > 0 and os.path.isfile(wj) and os.path.getmtime(wx) >= os.path.getmtime(wav))
        if not fresh:
            wlog = os.path.join(logs or work, stem + ".whisper.log")
            with open(wlog, "w") as lf:
                prompt = prompt_for(language, cfg)
                cmd = ["whisper-cli", "-m", model, "-f", wav, "-l", language] + (["--prompt", prompt] if prompt else []) + ["-osrt", "-oj", "-of", base]
                r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
            if r.returncode != 0 or not os.path.isfile(wj):
                common.stop(f"whisper-cli failed on {os.path.basename(src)}: {_tail(wlog)}")
        with open(wj) as f:
            heard = ((json.load(f).get("result") or {}).get("language")) or "en"
        lang = heard if language == "auto" else language
        if not fresh:
            alog = os.path.join(logs or work, stem + ".align.log")
            with open(alog, "w") as lf:
                r = subprocess.run([py, os.path.join(HERE, "wx_align.py"), wav, wj, wx, "--language", lang, "--device", device], stdout=lf, stderr=subprocess.STDOUT)
            if r.returncode != 0 or not os.path.isfile(wx):
                common.stop(f"the word aligner failed on {os.path.basename(src)}: {_tail(alog, 2)} (log: {alog})")
        with open(wx) as f:
            words = flag(wx_words(json.load(f)))
        if not words:
            common.stop(f"no words were found in {os.path.basename(src)}")
        doc = {"source": "whisper", "language": lang, "transcript": " ".join(w["text"] for w in words), "words": words}
        if out:
            common.atomic_write_json(out, doc)
        return doc
    finally:
        if temp:
            shutil.rmtree(work, ignore_errors=True)


def transcribe(wav, language=None):
    """the flagged words of an audio file in its own time, nothing kept (the joint check's corroboration pass and the sidecar fallback use it)"""
    return hear(wav, language=language)["words"]


def transcript_pack(name, wxdir, out):
    """The transcript pack for one prepared take, for whoever plans the cut: numbered whisper segments with [start-end] source seconds, then the
    verbatim aligned word stream (what the cutter matches against), then counts (words, fillers, measured pauses of 0.4 s or more).
    Reads <wxdir>/<name>.json (whisper), <name>.wx.json (aligned) and <name>.wav. Returns the one-line summary."""
    with open(os.path.join(wxdir, f"{name}.json")) as f:
        seg = json.load(f)["transcription"]
    with open(os.path.join(wxdir, f"{name}.wx.json")) as f:
        wx = json.load(f)
    words = [w for s in wx["segments"] for w in s.get("words", []) if w.get("start") is not None]
    if not words:
        common.stop(f"the aligned words of {name} are empty: there is nothing to pack")
    wav = os.path.join(wxdir, f"{name}.wav")
    r = subprocess.run(["ffmpeg", "-i", wav, "-af", "silencedetect=noise=-35dB:d=0.40", "-f", "null", "-"], capture_output=True, text=True).stderr
    pauses = re.findall(r"silence_start: ([\d.]+)", r)
    lines = [f"# {name}: {len(words)} words, {words[-1]['end']:.1f}s", "",
             "## SEGMENTS (source seconds; verbatim whisper text: match strings must come from here)"]
    for s in seg:
        t = s["text"].strip()
        if t:
            lines.append(f"[{s['offsets']['from'] / 1000:6.2f}-{s['offsets']['to'] / 1000:6.2f}] {t}")
    lines += ["", "## WORD STREAM (aligned; start seconds every 10th word in brackets)"]
    buf = [(f"[{w['start']:.1f}]" if i % 10 == 0 else "") + w["word"].strip() for i, w in enumerate(words)]
    lines.append(" ".join(buf))
    fill = [f"{_norm(w['word'])}@{w['start']:.1f}" for w in words if _norm(w["word"]) in FILLERS]
    lines += ["", "## COUNTS", f"fillers: {len(fill)} {' '.join(fill)}", f"measured pauses >=0.40s: {len(pauses)} starting at {', '.join(p[:6] for p in pauses)}"]
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    return f"{name}: {len(words)} words, {len(fill)} fillers, {len(pauses)} pauses -> {out}"


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return default


def _main():
    a = sys.argv[1:]
    if a and a[0] == "--pack":
        print(transcript_pack(a[1], a[2], a[3]))
        return
    out, language, work, logs, device = _opt(a, "--out"), _opt(a, "--language"), _opt(a, "--work"), _opt(a, "--logs"), _opt(a, "--device")
    if not out or len(a) != 1:
        raise IndexError("words.py <audio-or-video> --out words.json")
    d = hear(a[0], out, language, work, logs, device)
    print(f"{len(d['words'])} words, {sum(w['filler'] for w in d['words'])} fillers, {sum(w['double'] for w in d['words'])} doubles ({d['language']}) -> {out}")


if __name__ == "__main__":
    support.cli(_main, USAGE)
