#!/usr/bin/env python3
"""prep.py: the mechanical prep of a shoot. Everything deterministic that must exist before anyone looks at a clip.

  python3 prep.py --job JOB.json [--skip-whisper] [--frames-per-clip-max 40]

Writes into <scratch>/ (default <shoot>/.work):
  probe.tsv          clip \\t duration_s \\t width \\t height \\t fps \\t ext \\t rotation   (width and height as the camera coded them)
  audio/<clip>.wav   16 kHz mono, the file's first audio stream
  audiostats/        volumedetect + silencedetect output per clip
  transcripts/<clip>.srt   whisper-cli, SRT, never plain text (-nt drops chunks)
  frames/<clip>/NN_<t>s.jpg   about 1 frame per 2 s (4 to 40), 480 px wide, upright
  sheets/<clip>.jpg  labeled contact sheet (clip name and time on every tile)
  thumbs/<clip>.jpg  mid-clip thumbnail for the index, fitted to the clip's own shape: vertical 240x426, horizontal 426x240, square 320x320
  evidence.json + ev/<clip>.json   one record per clip: duration, dB stats, digitally_silent, audio_unreadable, no_speech, transcript, sheet and frames paths, orientation
Ends by printing the evidence summary (clip, max dB, silent or not, the first 90 characters whisper heard).
A transcript with under 3 letters or digits, or only filler ("Thank you.", "."), counts as no speech.
Whisper failing on any clip (no model, a crash, no SRT) stops the run: a spoken clip must never be filed as silent.
A video file ffprobe cannot read is named in a warning and left where it is: it is not a clip of this run.
job.whisper_language (default "en", or "auto") is passed to whisper-cli.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from common import stop  # noqa: E402
from job import SILENT_MAX_DB, VIDEO_EXT, load_job, speech_text  # noqa: E402

TILE_W = 480
COLS = 4
SHEET_MAX_H = 4500   # a taller sheet is shrunk when it is read, and the tiles stop being legible: past this many rows the sheet samples the frames evenly (every frame stays in frames/)
BOXES = {"vertical": (240, 426), "horizontal": (426, 240), "square": (320, 320)}


# ---------------------------------------------------------------- the shape of a clip
def display_size(width, height, rotation=0):
    """What a viewer sees: a clip coded 3840x2160 with a display rotation of 90 is shown 2160x3840."""
    return (height, width) if abs(int(rotation or 0)) in (90, 270) else (width, height)


def orientation(width, height, rotation=0):
    """"vertical", "horizontal" or "square" from the coded size and the rotation tag; a size that is not known counts as vertical, the shape phones shoot"""
    w, h = display_size(width, height, rotation)
    if w <= 0 or h <= 0:
        return "vertical"
    r = w / h
    return "square" if 0.95 <= r <= 1.05 else ("horizontal" if w > h else "vertical")


def thumb_box(width, height, rotation=0):
    """The box a thumbnail is fitted into: vertical 240x426, horizontal 426x240, square 320x320. The picture keeps its own shape inside the box."""
    return BOXES[orientation(width, height, rotation)]


# ---------------------------------------------------------------- small helpers
def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def need_tools(skip_whisper):
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        stop(f"{' and '.join(missing)} not found: install ffmpeg first (brew install ffmpeg)")
    if not skip_whisper and not shutil.which("whisper-cli"):
        stop("whisper-cli not found: install whisper.cpp first (brew install whisper-cpp)")
    try:
        import PIL  # noqa: F401
    except ImportError:
        stop("Pillow is not installed: pip install pillow")


def font(sz):
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf", "/Library/Fonts/Arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf", "DejaVuSans.ttf", "C:\\Windows\\Fonts\\arial.ttf"):
        if p.startswith("/") and not os.path.exists(p):
            continue
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def atomic_run(cmd, dst):
    """run an ffmpeg command whose last argument is `dst` through a .part name, so a killed run never leaves a half-written file that a rerun would trust"""
    tmp = dst + ".part" + os.path.splitext(dst)[1]
    r = sh(cmd[:-1] + [tmp])
    if r.returncode == 0 and os.path.exists(tmp):
        os.replace(tmp, dst)
    elif os.path.exists(tmp):
        os.remove(tmp)
    return r


# ---------------------------------------------------------------- 1. probe
def scan(src):
    """-> (clips, unreadable). clips = {stem: {duration, width, height, fps, ext, rotation, path, has_audio}} for every video file directly inside src, in name order."""
    if not os.path.isdir(src):
        stop(f"source folder missing: {src}")
    for d, dirs, fs in os.walk(src):   # only top-level files are read; say so when a card dump has nested folders
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        if d != src:
            n = sum(1 for f in fs if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.startswith("."))
            if n:
                print(f"[warn] {n} video file(s) in the nested folder {os.path.relpath(d, src)}: prep reads only the top level of {src}. Move them up first.", flush=True)
    names = sorted(f for f in os.listdir(src) if not f.startswith(".") and os.path.splitext(f)[1].lower() in VIDEO_EXT and os.path.isfile(os.path.join(src, f)))
    stems = {}
    for f in names:
        s = os.path.splitext(f)[0]
        if s in stems:
            print(f"[warn] duplicate name {s}: {stems[s]} and {f}. organize.py will refuse until one is renamed.", flush=True)
        stems[s] = f
    clips, unreadable = {}, []
    for f in names:
        stem, ext = os.path.splitext(f)
        path = os.path.join(src, f)
        p = common.probe(path)
        if not p or p["dur"] <= 0:
            unreadable.append(f)
            continue
        cw, ch = display_size(p["w"], p["h"], p["rotation"])   # common.probe gives the shape a viewer sees; probe.tsv keeps the size the camera coded (the swap undoes itself)
        clips[stem] = dict(duration=p["dur"], width=cw, height=ch, fps=p["fps"], ext=ext, rotation=p["rotation"], path=path, has_audio=bool(p["audio"]))
    for f in unreadable:
        print(f"[warn] ffprobe cannot read {f}: it is left where it is and is not part of this shoot's clips.", flush=True)
    return clips, unreadable


# ---------------------------------------------------------------- 2. audio and levels
def audio_stats(c, m, scr):
    wav = f"{scr}/audio/{c}.wav"
    if m["has_audio"] and not os.path.exists(wav):
        atomic_run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", m["path"], "-vn", "-map", "0:a:0?", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav], wav)
    if os.path.exists(wav):
        vol = sh(["ffmpeg", "-nostdin", "-i", wav, "-af", "volumedetect", "-f", "null", "-"]).stderr
        sil = sh(["ffmpeg", "-nostdin", "-i", wav, "-af", "silencedetect=noise=-30dB:d=0.5", "-f", "null", "-"]).stderr
    else:
        vol = sil = ""
    mean = re.search(r"mean_volume: ([-\d.]+)", vol)
    mx = re.search(r"max_volume: ([-\d.]+)", vol)
    m["mean_db"] = float(mean.group(1)) if mean else -91.0
    m["max_db"] = float(mx.group(1)) if mx else -91.0
    m["silences"] = len(re.findall(r"silence_start", sil))
    m["has_audio_stream"] = os.path.exists(wav) and os.path.getsize(wav) > 1000
    m["audio_unreadable"] = bool(m["has_audio"] and not m["has_audio_stream"])   # the file has sound the tools could not decode: it must not be filed as silent without a word
    with open(f"{scr}/audiostats/{c}.txt", "w", encoding="utf-8") as fh:
        fh.write(vol + "\n" + sil)
    return c


# ---------------------------------------------------------------- 3. whisper
def run_whisper(clips, scr, model, lang):
    if not os.path.exists(model):
        stop(f"whisper model missing: {model} (run the setup, or set whisper_model in ~/.cutroom/config.json)")
    failed = []
    for c, m in clips.items():
        srt = f"{scr}/transcripts/{c}.srt"
        if os.path.exists(srt) or m["max_db"] < SILENT_MAX_DB or not m["has_audio_stream"]:
            continue
        r = sh(["whisper-cli", "-m", model, "-f", f"{scr}/audio/{c}.wav", "-l", lang, "-t", "8", "-np", "-osrt", "-of", f"{scr}/transcripts/{c}"])
        if r.returncode != 0 or not os.path.exists(srt):
            failed.append(c)
            print(f"[whisper] FAILED {c} (exit {r.returncode}): {r.stderr.strip()[-300:]}", flush=True)
            if os.path.exists(srt):
                os.remove(srt)
        else:
            print(f"[whisper] {c}", flush=True)
    if failed:
        stop(f"[whisper] FAILED on {len(failed)} clip(s): {', '.join(failed)}. Fix whisper (model path, whisper-cli) and run prep again; nothing downstream is trustworthy without transcripts.")
    print("[whisper] done", flush=True)


def srt_text(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = " ".join(l.strip() for l in fh.read().splitlines() if l.strip() and not re.match(r"^\d+$", l) and "-->" not in l)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- 4. frames, sheets, thumbs
def fit_label(draw, name, stamp, fnt, width):
    """the tile's label: the clip name, then the time. A long name is cut short (with a ~), never the time, which is what the label is for"""
    while len(name) > 3 and draw.textlength(f"{name}  {stamp}", font=fnt) > width:
        name = name[:-2].rstrip() + "~"
    return f"{name}  {stamp}"


def make_frames(c, m, scr, fmax):
    """-> the clip's frame files, written the first time and kept for every rerun"""
    dur = m["duration"]
    n = max(4, min(fmax, math.ceil(dur / 2)))
    step = dur / n
    d = f"{scr}/frames/{c}"
    os.makedirs(d, exist_ok=True)
    for i in range(n):
        t = round(step * i + step / 2, 2)
        out = f"{d}/{i:02d}_{t:06.2f}s.jpg"
        if not os.path.exists(out):
            atomic_run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(t), "-i", m["path"], "-frames:v", "1", "-vf", f"scale={TILE_W}:-1", "-q:v", "4", out], out)
    return sorted(f for f in os.listdir(d) if f.endswith(".jpg") and ".part." not in f)


def placeholder(c, m, scr, F):
    """a clip no frame could be read from (ffprobe sees a picture, ffmpeg cannot decode it) still gets a sheet and a thumbnail that say so, so the classifier, the index and the QC
    all have something to hold; the warning names the clip"""
    from PIL import Image, ImageDraw
    w, h = thumb_box(m["width"], m["height"], m["rotation"])
    for path, size in ((f"{scr}/sheets/{c}.jpg", (4 * 480, 270)), (f"{scr}/thumbs/{c}.jpg", (w, h))):
        im = Image.new("RGB", size, (40, 40, 48))
        ImageDraw.Draw(im).text((12, 12), "no picture could be read from this clip", fill=(230, 230, 230), font=F)
        im.save(path, quality=80)
    print(f"[warn] no frame could be read from {c}: it has a placeholder sheet and thumbnail, so classify it by what is said", flush=True)


def make_sheet_and_thumb(c, m, scr, fr, F):
    from PIL import Image, ImageDraw
    d = f"{scr}/frames/{c}"
    if not fr:
        placeholder(c, m, scr, F)
        return False
    with Image.open(f"{d}/{fr[0]}") as im0:
        tw, th = im0.size
    cap = max(COLS, (SHEET_MAX_H // (th + 30)) * COLS)
    tiles = fr if len(fr) <= cap else [fr[int(i * len(fr) / cap)] for i in range(cap)]
    rows = math.ceil(len(tiles) / COLS)
    sheet = Image.new("RGB", (COLS * tw, rows * (th + 30)), "black")
    dr = ImageDraw.Draw(sheet)
    for i, fn in enumerate(tiles):
        with Image.open(f"{d}/{fn}") as im:
            x, y = (i % COLS) * tw, (i // COLS) * (th + 30)
            sheet.paste(im, (x, y + 30))
            dr.text((x + 8, y + 4), fit_label(dr, c, f"t={fn.split('_')[1][:-4]}", F, tw - 12), fill="white", font=F)
    sheet.save(f"{scr}/sheets/{c}.jpg", quality=82)
    with Image.open(f"{d}/{fr[len(fr) // 2]}") as mid:
        mid.thumbnail(thumb_box(m["width"], m["height"], m["rotation"]))
        mid.save(f"{scr}/thumbs/{c}.jpg", quality=72, optimize=True)
    return True


# ---------------------------------------------------------------- the run
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--skip-whisper", action="store_true")
    ap.add_argument("--frames-per-clip-max", type=int, default=40)
    a = ap.parse_args(argv)
    t0 = time.time()
    J = load_job(a.job)
    SRC, SCR = J["src"], J["scratch"]
    need_tools(a.skip_whisper)
    for d in ("audio", "audiostats", "transcripts", "frames", "sheets", "thumbs", "ev", "results"):
        os.makedirs(f"{SCR}/{d}", exist_ok=True)

    clips, unreadable = scan(SRC)
    with open(f"{SCR}/probe.tsv", "w", encoding="utf-8") as fh:
        for c, m in clips.items():
            fh.write(f"{c}\t{m['duration']:.3f}\t{m['width']}\t{m['height']}\t{m['fps']}\t{m['ext']}\t{m['rotation']}\n")
    print(f"[probe] {len(clips)} clips, {sum(m['duration'] for m in clips.values()) / 60:.1f} min", flush=True)
    if not clips:
        stop(f"no video files ffprobe can read in {SRC}")

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda c: audio_stats(c, clips[c], SCR), clips))
    n_sil = sum(1 for m in clips.values() if m["max_db"] < SILENT_MAX_DB or not m["has_audio_stream"])
    print(f"[audio] done: {n_sil} silent, {len(clips) - n_sil} with sound", flush=True)
    for c, m in clips.items():
        if m["audio_unreadable"]:
            print(f"[warn] {c} has an audio stream ffmpeg could not decode: it counts as silent here, but it may not be. Say so in its record's notes and lower its confidence.", flush=True)

    if not a.skip_whisper:
        run_whisper(clips, SCR, J["whisper_model"], J["whisper_language"])

    F = font(22)

    def one(c):
        fr = make_frames(c, clips[c], SCR, a.frames_per_clip_max)
        make_sheet_and_thumb(c, clips[c], SCR, fr, F)
        return c
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, clips))
    print(f"[frames] {len(os.listdir(f'{SCR}/sheets'))} contact sheets", flush=True)

    ev = []
    for c, m in clips.items():
        text = srt_text(f"{SCR}/transcripts/{c}.srt")
        silent = m["max_db"] < SILENT_MAX_DB or not m["has_audio_stream"]
        heard = "" if silent else speech_text(text)
        ev.append(dict(clip=c, ext=m["ext"], duration_s=round(m["duration"], 2), width=m["width"], height=m["height"], fps=m["fps"], rotation=m["rotation"],
                       orientation=orientation(m["width"], m["height"], m["rotation"]),
                       mean_db=m["mean_db"], max_db=m["max_db"], digitally_silent=silent, audio_unreadable=m["audio_unreadable"], no_speech=not heard, transcript=heard,
                       raw_whisper_discarded=(text if text and not heard else ""), sheet=f"{SCR}/sheets/{c}.jpg", frames_dir=f"{SCR}/frames/{c}"))
        common.atomic_write_json(f"{SCR}/ev/{c}.json", ev[-1])
    common.atomic_write_json(f"{SCR}/evidence.json", ev)
    print(f"[evidence] {len(ev)} records -> {SCR}/evidence.json", flush=True)
    print("\nEVIDENCE SUMMARY  clip, max dB, silent or not, first 90 chars heard")
    for e in ev:
        heard = e["transcript"] or f"[no speech] {e['raw_whisper_discarded']}".strip()
        print(f"  {e['clip']}  {e['max_db']:>6}  {'SILENT' if e['digitally_silent'] else 'audio '}  {heard[:90]}")
    print(f"  {sum(1 for e in ev if e['digitally_silent'])} silent, {sum(1 for e in ev if not e['digitally_silent'])} with audio, {sum(1 for e in ev if not e['no_speech'])} with speech")
    print(f"[prep] done in {time.time() - t0:.1f} s", flush=True)
    return ev


if __name__ == "__main__":
    main()
