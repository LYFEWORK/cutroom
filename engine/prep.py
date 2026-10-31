#!/usr/bin/env python3
"""prep.py <workroot> <name> <src> [--reframe | --keep-landscape] [--hw]

Prepares ONE take for the cutter: the shared first stage of every talking-head cut. Writes <workroot>/{mez,wx,tx,logs}/ (safe to run several takes
in parallel lanes, each under its own name):

    mez/<name>.mp4          the mezzanine: 1080x1920 at 30 fps (1920x1080 with --keep-landscape), x264 crf 16, BT.709 limited range and tagged so,
                            AAC 192k at 48 kHz, the FIRST audio track only (a phone file carries a spatial track and data streams besides)
    wx/<name>.wav           a 16 kHz mono wav of it (a dead channel is dropped, not averaged in)
    wx/<name>.json, .srt, .wx.json, .words.json   the words (words.py): whisper's segments and subtitles, the aligned words, the words.json contract
    wx/<name>.avskew.json   what avskew.py measured on the source, and the conform that followed when it had to
    wx/<name>.face.json     the median face (face.py anchor) of 16 frames
    tx/<name>.txt           the transcript pack
    logs/                   the tools' own output

Steps, in order: wait for an iCloud placeholder to download (up to 30 minutes), probe, check the orientation, build the mezzanine, check its
picture against its sound, make the wav, find the words, pack the transcript, find the face.

  orientation   a portrait source goes on as it is. A landscape or square one STOPs with the two ways out: --reframe crops it to a face-centred 9:16
                first (reframe.py), --keep-landscape builds a 1920x1080 mezzanine for 16:9 formats instead of stretching it. A portrait picture that is
                not exactly 9:16 is scaled to cover 1080x1920 and centre-cropped, never stretched.
  hardware      HEVC sources and anything 3000 px or wider decode on VideoToolbox (--hw forces it).
  stalls        a stalled capture (a phone's thermal or storage stall) must never be frozen into fake picture time: avskew.py measures the video
                and audio timelines BEFORE the mezzanine. A clean source encodes as it is. A disagreeing one has its frames renumbered contiguously
                at the source's nominal rate so the stall time is dropped and mezzanine video time equals audio time, and the result is VERIFIED
                (durations match, no held frame over 0.5 s); the prep STOPs if the conform did not hold.
  HDR           an HLG or PQ source (the probe says arib-std-b67 or smpte2084, or BT.2020 primaries) is converted to BT.709 SDR by hdr.py before the
                mezzanine, so the mezzanine is always plain SDR. An SDR source never touches hdr.py. A full-range or BT.601 SDR picture is
                converted to limited-range BT.709 in the same scale step.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import subprocess
import time

import avskew
import common
import face
import hdr
import reframe
import sound
import words as words_engine

stop = common.stop
USAGE = "prep.py <workroot> <name> <src> [--reframe | --keep-landscape] [--hw]"
ICLOUD_POLL_S = float(os.environ.get("CUTROOM_ICLOUD_POLL_S", "15"))
ICLOUD_WAIT_S = float(os.environ.get("CUTROOM_ICLOUD_WAIT_S", "1800"))
BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
         "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709"]   # the flags alone leave primaries and transfer unknown in this ffmpeg build


def say(name, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {name} {msg}", flush=True)


def is_dataless(path):
    """True while a file is an iCloud placeholder that has not been downloaded (macOS marks it with the `dataless` flag)"""
    try:
        r = subprocess.run(["stat", "-f", "%Sf", str(path)], capture_output=True, text=True)
    except OSError:
        return False
    return "dataless" in r.stdout


def wait_for_download(path, name):
    if not is_dataless(path):
        return
    say(name, "is evicted to iCloud: requesting the download")
    try:
        subprocess.run(["brctl", "download", str(path)], capture_output=True)
    except OSError:
        pass
    waited = 0.0
    while is_dataless(path):
        time.sleep(ICLOUD_POLL_S)
        waited += ICLOUD_POLL_S
        if waited >= ICLOUD_WAIT_S:
            stop(f"{path} is still evicted to iCloud after {int(ICLOUD_WAIT_S // 60)} minutes: download it in Finder and run again")


def color_of(path):
    """the video stream's colour tags: {"space", "range", "pix_fmt"} ("" where the file says nothing)"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space,color_range,pix_fmt", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        s = json.loads(r.stdout)["streams"][0]
    except (ValueError, KeyError, IndexError):
        s = {}
    return {"space": s.get("color_space") or "", "range": s.get("color_range") or "", "pix_fmt": s.get("pix_fmt") or ""}


def video_filter(w, h, landscape, color, conform_nominal=None):
    """The ffmpeg -vf chain of the mezzanine. Scale-to-cover and centre-crop (the same as a plain scale for an exact 9:16 or 16:9 picture), the
    colour matrix and range made BT.709 limited where the source is something else, 30 fps, 8-bit 4:2:0. A conformed source is renumbered first."""
    tw, th = (1920, 1080) if landscape else (1080, 1920)
    opts = []
    if color["space"] in ("smpte170m", "bt470bg"):
        opts += ["in_color_matrix=bt601", "out_color_matrix=bt709"]
    elif color["space"] == "smpte240m":
        opts += ["in_color_matrix=smpte240m", "out_color_matrix=bt709"]
    if color["range"] == "pc" or color["pix_fmt"].startswith("yuvj"):
        opts += ["in_range=pc", "out_range=tv"]
    exact = w * th == h * tw
    vf = "scale=" + ":".join([f"{tw}:{th}"] + ([] if exact else ["force_original_aspect_ratio=increase"]) + ["flags=lanczos"] + opts)
    if not exact:
        vf += f",crop={tw}:{th}"
    vf += ",fps=30,format=yuv420p"
    if conform_nominal:
        vf = f"setpts=N/{conform_nominal}/TB," + vf
    return vf


def prep(root, name, src, reframe_it=False, keep_landscape=False, hw=False):
    root = os.path.abspath(root)
    src = os.path.abspath(src)
    for d in ("mez", "wx", "tx", "logs"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    if not os.path.isfile(src):
        stop(f"source not found: {src}")
    wait_for_download(src, name)
    mez = os.path.join(root, "mez", f"{name}.mp4")
    p = common.probe(src)
    if not p:
        stop(f"ffprobe cannot read a video stream in {src}")
    # what the source is: display size (after the rotation tag) and codec. Portrait goes on; landscape needs --reframe or --keep-landscape.
    w, h, codec = p["w"], p["h"], p["codec"]
    landscape = False
    if h <= w:
        if reframe_it:
            say(name, f"is {w}x{h} (not portrait): reframing to a face-centred 9:16")
            reframed = os.path.join(root, "mez", f"{name}.reframed.mp4")
            reframe.reframe(src, reframed)
            src, codec, w, h = reframed, "h264", 1080, 1920
        elif keep_landscape:
            landscape = True
            say(name, f"is {w}x{h}: keeping it landscape (a 1920x1080 mezzanine)")
        else:
            stop(f"{src} is {w}x{h}, not portrait. A 9:16 take needs a portrait source: pass --reframe to crop it around the face first "
                 f"(prep.py {root} {name} <src> --reframe), pass --keep-landscape for a 1920x1080 mezzanine, or film it upright.")
    if not hw and (codec == "hevc" or w >= 3000 or h >= 3000):
        hw = True
    # picture against sound, BEFORE the mezzanine
    avj = os.path.join(root, "wx", f"{name}.avskew.json")
    if os.path.exists(avj):
        os.remove(avj)                        # a stale json would fool the conform test below
    d = avskew.probe(src)
    avskew._write(avj, d)
    conform = not d["ok"]
    if conform:
        # The timelines disagree by something MEASURED: conform and carry on rather than refuse (the conform is verified below and stops there if it
        # does not hold). A stall that cannot be removed is a stop; a verified conform is not shipping something wrong silently.
        say(name, f"NOTE: {d['reason']}")
    else:
        say(name, f"avskew ok: {d['reason']} ({d['video_frames']} frames @ {d['nominal_fps']:.3f} fps)")
    # HDR: only when the probe says so
    inf = hdr.info(src)
    if hdr.is_hdr(inf):
        src = hdr.convert(src, os.path.join(root, "mez", f"{name}.sdr.mov"), log=lambda m: say(name, m))["src"]
    say(name, f"mezzanine start{' (hardware decode)' if hw else ''}")
    vf = video_filter(w, h, landscape, color_of(src), d["nominal_fps"] if conform else None)
    cmd = ["ffmpeg", "-v", "error", "-y"] + (["-hwaccel", "videotoolbox"] if hw else []) + ["-i", src, "-map", "0:v:0", "-map", "0:a:0", "-vf", vf]
    if conform:
        say(name, f"conforming: dropping {d['holes_total']}s of stall time, frames renumbered at {d['nominal_fps']} fps")
        cmd += ["-fps_mode", "cfr", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "16", *BT709, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", mez]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        stop(f"the mezzanine did not encode: {' '.join(r.stderr.split())[-300:]}")
    mp = common.probe(mez)
    say(name, f"mezzanine done: {mp['w']}x{mp['h']} {mp['dur']:.2f}s")
    if conform:
        v = avskew.verify(mez)
        v["dropped_s"] = round(d["holes_total"], 4)
        avskew._merge(avj, "conform", v)
        if not v["ok"]:
            stop(v["reason"])
        say(name, "avskew verify ok: " + v["reason"])
    wav = os.path.join(root, "wx", f"{name}.wav")
    sound.downmix(mez, wav, 16000)
    words_engine.hear(wav, out=os.path.join(root, "wx", f"{name}.words.json"), work=os.path.join(root, "wx"), logs=os.path.join(root, "logs"))
    say(name, "words done")
    words_engine.transcript_pack(name, os.path.join(root, "wx"), os.path.join(root, "tx", f"{name}.txt"))
    fa = face.anchor(mez, os.path.join(root, "wx", f"{name}.face.json"), 16)
    say(name, f"face @({fa['cx']},{fa['cy']}) hits {fa['hits']}/{fa['frames']}{' FALLBACK' if fa['fallback'] else ''}")
    say(name, "PREP DONE")
    return {"mez": mez, "conformed": conform, "landscape": landscape}


def _main():
    a = sys.argv[1:]
    flags = {x for x in a if x.startswith("--")}
    pos = [x for x in a if not x.startswith("--")]
    bad = flags - {"--reframe", "--keep-landscape", "--hw"}
    if bad:
        stop(f"unknown option {sorted(bad)[0]}: usage: {USAGE}")
    if "--reframe" in flags and "--keep-landscape" in flags:
        stop("--reframe and --keep-landscape are two different answers to a landscape source: pick one")
    root, name, src = pos[0], pos[1], pos[2]
    prep(root, name, src, "--reframe" in flags, "--keep-landscape" in flags, "--hw" in flags)


if __name__ == "__main__":
    support.cli(_main, USAGE)
