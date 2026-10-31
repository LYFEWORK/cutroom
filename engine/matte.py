#!/usr/bin/env python3
"""matte.py: person mattes from Apple Vision (the engine/swift/personmask and personmatte helpers, built on first use).

    matte.py frames <video.mp4> <windows.json> <outdir>             RGBA webp frames of the person, only for the frames inside the windows
    matte.py video  <video.mp4> <outdir> [fast|balanced|accurate]   one gray matte png a frame for the whole video (m00000.png ...)
    matte.py fix    <video.mp4> <masks_in/> <masks_out/> [--white-until SEC] [--median 5]    clean a matte for compositing

frames   windows.json is [[first frame, last frame], ...] at 30 fps. Each frame is named by its absolute frame number (f00066.webp) and is the
         video's own picture with the person's matte as its alpha (a 1 px blur on the edge). Every requested frame is produced or the run stops:
         a short extraction would ship a matte with holes in it.
video    the person matte of every frame, 8-bit gray, at the video's own size.
fix      cleans a Vision matte for compositing: (1) a temporal median over N frames (kills single-frame flips), (2) optional "held white object"
         union: bright, low-saturation blobs that touch the person and are 2k to 150k px (a bucket, a sheet of paper) join the matte, until
         --white-until seconds (after that scene, white facades would leak in), (3) a 2 px dilation and a feather. Writes m%05d.png.
         It needs scipy, which lives in the word-timing environment (config.json "venv_python"), so the command re-runs itself under that python
         when the one it was started with has none.
A swiftc, ffmpeg or helper failure is one STOP line.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageFilter

import common

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "swift"))
import swiftbin  # noqa: E402

stop = common.stop
USAGE = ("matte.py frames <video.mp4> <windows.json> <outdir> | video <video.mp4> <outdir> [fast|balanced|accurate] | "
         "fix <video.mp4> <masks_in/> <masks_out/> [--white-until SEC] [--median 5]")


# --- frames: the windows -> RGBA webp ----------------------------------------------------------------------------------------------

def frame_names(windows):
    s = set()
    for f0, f1 in windows: s.update(range(int(f0), int(f1) + 1))
    return sorted(s)
def compose(rgb_png, mask_png, out_webp):
    im = Image.open(rgb_png).convert("RGB"); m = Image.open(mask_png).convert("L").filter(ImageFilter.GaussianBlur(1.0))
    im.putalpha(m); im.save(out_webp, "WEBP", quality=88, method=4)
SEEK_EARLY = 0.001   # s: seek this far before a frame's own time, so the accurate seek keeps it
def _seek(f0, fps):
    """the -ss time that makes frame f0 the first frame out. `{f0 / fps:.4f}` rounds up for one frame in three at 30 fps (0.0667 for 0.06666...), and the
    accurate seek then drops frame f0 itself, so the whole window came out one frame late and the matte ghosted at the person's edges. A frame is
    over 8 ms long at any rate up to 120 fps, so a millisecond early never reaches the frame before."""
    return f"{max(0.0, f0 / fps - SEEK_EARLY):.4f}"
def _run(cmd, service):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        stop(f"{service} is down: {type(e).__name__}: {e}")


def build(video, windows, outdir, fps=30):
    """Every frame the windows ask for, or a STOP. Composing whatever listdir found would let a short extraction ship a
    matte with holes in it."""
    want = frame_names(windows)
    binary = swiftbin.ensure(os.path.join(HERE, "swift", "personmask"))   # rebuilt when personmask/main.swift is newer than the binary
    os.makedirs(outdir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="matte-")
    try:
        return _extract(video, windows, outdir, fps, tmp, want, binary)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)     # one PNG per frame
def _extract(video, windows, outdir, fps, tmp, want, binary):
    for f0, f1 in windows:
        f0 = max(0, int(f0)); n = int(f1) - f0 + 1
        r = _run(["ffmpeg", "-y", "-v", "error", "-ss", _seek(f0, fps), "-i", video, "-frames:v", str(n), "-vf", f"fps={fps}", "-start_number", str(f0), os.path.join(tmp, "f%05d.png")], "ffmpeg")
        if r.returncode != 0:
            stop(f"ffmpeg is down: failed extracting frames {f0}-{f1}: {r.stderr.strip()[:200]}")
    frames = sorted(os.path.join(tmp, x) for x in os.listdir(tmp) if x.endswith(".png") and ".mask." not in x)
    r = _run([binary] + frames, "personmask")
    if r.returncode != 0:
        stop(f"personmask is down: exit {r.returncode}: {r.stderr.strip()[:200]}")
    made = set()
    for f in frames:
        m = f.replace(".png", ".mask.png")
        if not os.path.exists(m):
            stop(f"personmask is down: no mask produced for {os.path.basename(f)}")
        base = os.path.basename(f)
        compose(f, m, os.path.join(outdir, base.replace(".png", ".webp"))); made.add(base[:-4])
    missing = [i for i in want if f"f{i:05d}" not in made]
    if missing:
        stop(f"personmatte produced {len(made)} of {len(want)} requested frames: "
                         f"{len(missing)} missing (first {missing[0]}, last {missing[-1]}). The reel "
                         f"would render with no matte over those frames.")
    return len(made)


def video_mattes(video, outdir, quality="accurate"):
    """one gray matte png per frame of the whole video (m00000.png ...) from the personmatte helper; returns the helper's last line"""
    if quality not in ("fast", "balanced", "accurate"):
        stop(f"quality {quality!r} is not one of fast, balanced, accurate")
    binary = swiftbin.ensure(os.path.join(HERE, "swift", "personmatte"))
    os.makedirs(outdir, exist_ok=True)
    r = _run([binary, str(video), str(outdir), quality], "personmatte")
    if r.returncode != 0 or "done:" not in r.stdout:
        stop(f"personmatte failed on {os.path.basename(str(video))}: {' '.join((r.stdout + r.stderr).split())[-200:]}")
    return r.stdout.strip().splitlines()[-1]


# --- fix: temporal median, held white objects, dilation and feather (numpy, scipy.ndimage and Pillow only) ---------------------------

BOX5, BOX15, BOX3 = np.ones((5, 5), bool), np.ones((15, 15), bool), np.ones((3, 3), bool)


def temporal_median(masks, i, k):
    n = len(masks)
    return np.median(np.stack([masks[max(0, min(n - 1, i + d))] for d in range(-k, k + 1)], axis=0), axis=0).astype(np.uint8)


def white_objects(frame_rgb, m, min_area=2000, max_area=150000):
    from scipy import ndimage
    """union (bool mask) of bright, low-saturation blobs of the frame that touch the person matte `m`, and how many there were"""
    mn, mx = frame_rgb.min(axis=2), frame_rgb.max(axis=2)
    white = (mn > 185) & ((mx.astype(np.int16) - mn.astype(np.int16)) < 38)
    white = ndimage.binary_opening(white, structure=BOX5)
    lab, num = ndimage.label(white, structure=BOX3)               # 8-connectivity
    if num == 0: return np.zeros(m.shape, bool), 0
    area = np.bincount(lab.ravel(), minlength=num + 1)
    touch = ndimage.binary_dilation(m > 128, structure=BOX15)
    touching = np.bincount(lab[touch].ravel(), minlength=num + 1)
    keep = np.zeros(num + 1, bool)
    for c in range(1, num + 1):
        if min_area <= area[c] <= max_area and touching[c] / area[c] > 0.02:   # touches the person
            keep[c] = True
    return keep[lab], int(keep.sum())


def finish(m):
    from scipy import ndimage
    """2 px dilation (a 5x5 box) and a 1.6 px feather"""
    m = ndimage.grey_dilation(m, footprint=BOX5)
    return np.clip(np.rint(ndimage.gaussian_filter(m.astype(np.float32), sigma=1.6, mode="nearest")), 0, 255).astype(np.uint8)


def frames(vid, W, H, count):
    """the first `count` frames of the video as H x W x 3 RGB arrays, one ffmpeg decode"""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", vid, "-frames:v", str(count), "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE)
    size = W * H * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size: return
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    finally:
        p.stdout.close(); p.wait()


def fix(vid, din, dout, white_until=0.0, median=5):
    """clean the matte pngs in `din` and write them to `dout`; returns (frames, white-object unions). Needs scipy (see the module doc)."""
    os.makedirs(dout, exist_ok=True)
    files = sorted(f for f in os.listdir(din) if f.endswith(".png"))
    if not files:
        stop(f"no .png masks in {din}")
    fps, n = 30.0, len(files)
    masks = [np.array(Image.open(os.path.join(din, f)).convert("L")) for f in files]
    H, W = masks[0].shape
    k, whites = median // 2, 0
    need = min(n, int(np.ceil(white_until * fps))) if white_until > 0 else 0
    vf = frames(vid, W, H, need) if need else iter(())
    for i in range(n):
        m = temporal_median(masks, i, k)
        if i / fps < white_until:
            frame = next(vf, None)
            if frame is not None:
                add, c = white_objects(frame, m)
                m[add] = 255
                whites += c
        Image.fromarray(finish(m)).save(os.path.join(dout, files[i]))
    return n, whites


def _has_scipy():
    try:
        import scipy.ndimage  # noqa: F401
        return True
    except ImportError:
        return False


def _main():
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "frames":
        n = build(a[0], json.load(open(a[1])), a[2])
        print(f"{n} matte frames -> {a[2]}")
    elif cmd == "video":
        print(video_mattes(a[0], a[1], a[2] if len(a) > 2 else "accurate"))
    elif cmd == "fix":
        if not _has_scipy():
            if os.environ.get("CUTROOM_MATTE_REEXEC"):
                stop(f"scipy is not installed under {sys.executable}: run install.sh (the word-timing environment has it)")
            r = subprocess.run([support.venv_python(), os.path.abspath(__file__)] + sys.argv[1:], env=dict(os.environ, CUTROOM_MATTE_REEXEC="1"))
            sys.exit(r.returncode)
        rest = a[3:]
        wu = float(rest[rest.index("--white-until") + 1]) if "--white-until" in rest else 0.0
        med = int(rest[rest.index("--median") + 1]) if "--median" in rest else 5
        n, whites = fix(a[0], a[1], a[2], wu, med)
        print(f"done {n} masks, {whites} white-object unions -> {a[2]}")
    else:
        stop(f"unknown command {cmd!r}: usage: {USAGE}")


if __name__ == "__main__":
    support.cli(_main, USAGE)
