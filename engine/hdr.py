#!/usr/bin/env python3
"""hdr.py <clip> [--to <out.mov>]

Says whether a clip is HDR, and with --to turns it into plain BT.709 SDR (the picture, and the source's first audio track beside it).

An iPhone shooting HDR records HLG (BT.2020 colors, arib-std-b67), and some cameras record PQ (smpte2084). Cut and scaled straight through
yuv420p such a clip keeps its HDR tags and plays flat, washed or tinted. So prep.py sends an HDR clip through here first; an SDR clip never comes
here at all.

HDR is read from the probe: color_transfer arib-std-b67 (HLG) or smpte2084 (PQ), or color_primaries bt2020 (a wide-gamut picture that needs its
colors mapped to BT.709 whatever its transfer says).

The conversion is Apple's own tone mapping (the engine/swift/hdr2sdr helper: VideoToolbox through AVFoundation, HLG / PQ / Dolby Vision 8.x to
BT.709, every frame keeps its presentation time, video only), muxed back with the source's audio. It comes back as 8-bit H.264 tagged BT.709
limited range, at the source's size and frame rate.

Fallback, only when swiftc is missing: an ffmpeg `colorspace` filter chain. It maps BT.2020 to BT.709 and reads the HLG signal as the SDR picture
HLG was designed to be (an HLG signal is backward compatible with an SDR display, so no tone mapping is needed to get a plausible picture).
ffmpeg's `tonemap` filter needs linear light and most ffmpeg builds have no `zscale` to make it, and `colorspace` has no PQ transfer, so PQ cannot
be converted without the Apple helper: it stops with a line that says so.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import re
import shutil
import subprocess

import common

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "swift"))
import swiftbin  # noqa: E402

stop = common.stop
USAGE = "hdr.py <clip> [--to <out.mov>]"
HDR_TRC = {"arib-std-b67": "hlg", "smpte2084": "pq"}
COPYABLE_AUDIO = {"aac", "alac", "mp3", "ac3", "eac3", "opus"}   # kept as they are in the mov; anything else is encoded to AAC
BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
         "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709"]   # the flags alone leave primaries and transfer unknown in this ffmpeg build
FALLBACK_VF = "colorspace=all=bt709:iall=bt2020:format=yuv420p"   # BT.2020 to BT.709, the HLG signal read as the SDR picture it is compatible with


def info(path):
    """{"transfer", "primaries", "space", "pix_fmt", "codec", "audio_codec"} of a clip (empty strings where the file says nothing)"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,color_transfer,color_primaries,color_space,pix_fmt", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        streams = json.loads(r.stdout or "{}").get("streams") or []
    except ValueError:
        streams = []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        stop(f"ffprobe cannot read a video stream in {path}")
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {"transfer": v.get("color_transfer") or "", "primaries": v.get("color_primaries") or "", "space": v.get("color_space") or "",
            "pix_fmt": v.get("pix_fmt") or "", "codec": v.get("codec_name") or "", "audio_codec": a.get("codec_name") or ""}


def kind(inf):
    """"hlg", "pq", "wide" (BT.2020 primaries with an SDR transfer) or "" for an SDR clip"""
    if inf.get("transfer") in HDR_TRC:
        return HDR_TRC[inf["transfer"]]
    return "wide" if inf.get("primaries") == "bt2020" else ""


def is_hdr(inf):
    return bool(kind(inf))


def _audio_args(inf):
    return ["-c:a", "copy"] if inf["audio_codec"] in COPYABLE_AUDIO else ["-c:a", "aac", "-b:a", "320k"]


def _apple(src, out, inf):
    binary = swiftbin.ensure(os.path.join(HERE, "swift", "hdr2sdr"))
    video = out + ".video.mov"
    try:
        r = subprocess.run([binary, str(src), video, "--codec", "h264"], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(video):
            stop(f"the HDR conversion failed on {os.path.basename(str(src))}: {' '.join((r.stdout + r.stderr).split())[-240:]}")
        m = re.search(r"first_pts=([\d.]+)", r.stdout)
        first = float(m.group(1)) if m else 0.0
        has_audio = bool(inf["audio_codec"])
        cmd = ["ffmpeg", "-v", "error", "-y"] + (["-itsoffset", f"{first:.6f}"] if first > 0 else []) + ["-i", video]
        if has_audio:
            cmd += ["-i", str(src), "-map", "0:v:0", "-map", "1:a:0"] + ["-c:v", "copy"] + _audio_args(inf)
        else:
            cmd += ["-map", "0:v:0", "-c:v", "copy"]
        cmd += ["-movflags", "+faststart", out]
        r2 = subprocess.run(cmd, capture_output=True, text=True)
        if r2.returncode != 0:
            stop(f"ffmpeg could not put the audio back on the converted picture: {' '.join(r2.stderr.split())[-200:]}")
    finally:
        if os.path.exists(video):
            os.remove(video)
    return {"method": "apple" if sys.platform == "darwin" else "stand-in", "first_pts": first}


def _ffmpeg(src, out, inf):
    k = kind(inf)
    if k == "pq":
        stop(f"{os.path.basename(str(src))} is PQ HDR and swiftc is missing: the PQ conversion needs the Apple helper (run xcode-select --install, then try again)")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", FALLBACK_VF, "-c:v", "libx264", "-preset", "medium", "-crf", "14",
           "-pix_fmt", "yuv420p", *BT709] + _audio_args(inf) + ["-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        stop(f"the HDR conversion failed on {os.path.basename(str(src))}: {' '.join(r.stderr.split())[-240:]}")
    return {"method": "ffmpeg", "first_pts": 0.0}


def to_sdr(src, out, log=print):
    """Convert an HDR clip to a BT.709 SDR file at `out` (.mov). Returns {"method": "apple" | "ffmpeg", "kind", "first_pts", "out"}. An SDR clip stops:
    the caller decides whether to call."""
    inf = info(src)
    k = kind(inf)
    if not k:
        stop(f"{os.path.basename(str(src))} is SDR: there is nothing to convert")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    helper = sys.platform != "darwin" or bool(shutil.which("swiftc")) or os.path.isfile(swiftbin.binary_path(os.path.join(HERE, "swift", "hdr2sdr")))
    rep = _apple(src, out, inf) if helper else _ffmpeg(src, out, inf)
    log(f"hdr: {os.path.basename(str(src))} is {k.upper()}: converted to BT.709 SDR ({rep['method']})")
    return dict(rep, kind=k, out=out)


def convert(src, out, log=print):
    """What prep.py calls with every source: {"src": the file to use from here on, "converted": bool, ...}. An SDR clip is returned untouched and
    this module does nothing else; only a clip the probe says is HDR is converted."""
    inf = info(src)
    if not is_hdr(inf):
        return {"src": str(src), "converted": False, "kind": ""}
    rep = to_sdr(src, out, log)
    return dict(rep, src=out, converted=True)


def _main():
    a = sys.argv[1:]
    out = a[a.index("--to") + 1] if "--to" in a else None
    clip = a[0]
    inf = info(clip)
    k = kind(inf)
    print(f"{clip}: {k.upper() + ' (HDR)' if k else 'SDR'}")
    if out:
        rep = to_sdr(clip, out)
        print(f"-> {out} ({rep['method']})")


if __name__ == "__main__":
    support.cli(_main, USAGE)
