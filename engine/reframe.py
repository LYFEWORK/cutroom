#!/usr/bin/env python3
"""reframe.py <src> <out.mp4> [--proxy-h 540]
Face-centerd 9:16 crop for LANDSCAPE talking-head sources. Scaling a landscape clip straight to 1080x1920 with no crop STRETCHES it, so prep.py
sends a landscape source through here first when asked to (--reframe).

Method: probe the source; stop if it is already portrait (that is the caller's job, never a silent copy). Build a small landscape proxy with ffmpeg
(native fps, proxy_h tall, no audio) and run face.build() on it to find the median face box.

face.build() quirk (read before touching the math below): its boxes are in a NOTIONAL canvas 1080 wide, not in the input's real pixels. Internally
it downscales every extracted frame to width 540 for face detection, then multiplies every box by the constant 1080/540 = 2 before writing it out.
So median["cx"] / 1080 is the face's FRACTIONAL x-position across the width of whatever video was handed to build(). The proxy is made with
`-vf scale=-2:{proxy_h}`, which preserves the source's aspect ratio exactly, so that fraction is identical for the proxy and the source.
face_cx_in_source() below multiplies it back out by the source's real width.

The crop is a single FIXED box for the whole clip (no per-second tracking or panning): crop height = full source height, crop width =
round(height * 9/16) (even), x centerd on the face and clamped to stay inside the frame.

ROTATION TRAP: a clip can report width=3840 / height=2160 in its stream header yet also carry a Display Matrix side-data tag with rotation +-90.
ffmpeg auto-applies that rotation BEFORE any -vf filter runs (a plain frame grab off such a file decodes to 2160x3840, and a `crop=3840:2160:0:0`
on it fails outright because the filter's actual input is already 2160 wide), so the RAW stream width/height ffprobe reports is not the coordinate
space the crop math (or the face proxy) will see. probe() below swaps width/height whenever the rotation is +-90 so every later check (the landscape
guard, crop_geometry, face_cx_in_source) works in the same DISPLAY-space pixels ffmpeg's filters use. A source that is only "landscape" by raw
sensor storage but rotate-tagged to portrait is treated as portrait: it does not need reframing.

No silent fallbacks: any ffmpeg/ffprobe failure, an already-portrait source, or a source with no detectable face is one STOP line."""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import shutil
import subprocess
import tempfile

import common
import face

stop = common.stop
CANVAS_W = 1080  # face.py's notional canvas width: see the module docstring above


def _run(cmd, service):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        stop(f"{service} is down: {type(e).__name__}: {e}")


def rotated_dims(w, h, rotation):
    """Swap width/height when the Display Matrix rotation is a quarter-turn (+-90): that's
    the orientation ffmpeg's decoder actually hands to -vf filters, not the raw stream size."""
    return (h, w) if rotation % 360 in (90, 270) else (w, h)


def probe(src):
    """(width, height, r_frame_rate string, duration) of the source's first video stream,
    in the DISPLAY orientation (rotation-corrected: see the module docstring)."""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
              "-show_streams", "-show_format", "-of", "json", src], "ffprobe")
    if r.returncode != 0 or not r.stdout.strip():
        stop(f"ffprobe is down: could not probe {src}: {r.stderr.strip()[:200] or 'no output'}")
    try:
        data = json.loads(r.stdout)
        s = data["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        rotation = 0
        for sd in s.get("side_data_list", []):
            if "rotation" in sd:
                rotation = int(sd["rotation"])
                break
        w, h = rotated_dims(w, h, rotation)
        fps = s["r_frame_rate"]
        dur = float(data["format"]["duration"])
    except (KeyError, IndexError, ValueError) as e:
        stop(f"ffprobe is down: could not parse stream info for {src}: {e}")
    return w, h, fps, dur


def ensure_landscape(w, h):
    if h >= w:
        stop("already portrait: use the source directly")


def build_proxy(src, proxy, proxy_h):
    """Small landscape proxy for face.track: native fps kept, proxy_h tall, no audio, fast encode."""
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"scale=-2:{proxy_h}",
              "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", proxy], "ffmpeg")
    if r.returncode != 0:
        stop(f"ffmpeg is down: failed building proxy for {src}: {r.stderr.strip()[:200]}")


def face_cx_in_source(track, src_w):
    """Median face center-x, converted from face.py's notional 1080px canvas fraction
    into the source's real pixel space. Raises when no face was ever detected."""
    if not track.get("samples"):
        stop("no face found: cannot reframe; use the montage lane")
    fraction = track["median"]["cx"] / CANVAS_W
    return round(fraction * src_w)


def crop_geometry(src_w, src_h, face_cx):
    """Fixed 9:16 crop box in SOURCE pixels: full height, width = round(h*9/16) rounded up
    to even, x centered on face_cx and clamped inside [0, src_w - w]."""
    h = src_h
    w = round(src_h * 9 / 16)
    if w % 2:
        w += 1
    x = round(face_cx - w / 2)
    x = max(0, min(x, src_w - w))
    return {"w": w, "h": h, "x": x}


def run_ffmpeg_crop(src, out, w, h, x, fps):
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", src,
              "-vf", f"crop={w}:{h}:{x}:0,scale=1080:1920",
              "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out],
             "ffmpeg")
    if r.returncode != 0:
        stop(f"ffmpeg is down: failed cropping {src}: {r.stderr.strip()[:200]}")


def reframe(src, out, proxy_h=540):
    w, h, fps, _dur = probe(src)
    ensure_landscape(w, h)
    tmp = tempfile.mkdtemp(prefix="reframe-")
    try:
        proxy = os.path.join(tmp, "proxy.mp4")
        build_proxy(src, proxy, proxy_h)
        track = face.build(proxy, os.path.join(tmp, "track.json"))
        face_cx = face_cx_in_source(track, w)
        geo = crop_geometry(w, h, face_cx)
        run_ffmpeg_crop(src, out, geo["w"], geo["h"], geo["x"], fps)
        sidecar = {"src": src, "crop": geo, "face_cx": face_cx, "proxy_h": proxy_h, "fps": fps}
        json.dump(sidecar, open(out + ".reframe.json", "w"), indent=1)
        print(f"reframe: {w}x{h} -> crop {geo['w']}x{geo['h']} at x={geo['x']} "
              f"(face cx {face_cx}) -> 1080x1920")
        return sidecar
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _main():
    src, out = sys.argv[1], sys.argv[2]
    proxy_h = 540
    if "--proxy-h" in sys.argv:
        proxy_h = int(sys.argv[sys.argv.index("--proxy-h") + 1])
    reframe(src, out, proxy_h)


if __name__ == "__main__":
    support.cli(_main, "reframe.py <src> <out.mp4> [--proxy-h 540]")
