"""Shared pieces for the face lock tests: a stub `facepts` helper that finds two coloured dots instead of a face, and a picture that carries them.

The real helper is Apple Vision and finds real faces only. These tests need a "face" they control, so the stub reads the same frames the engine
hands the real helper (JPEG files, one path per argument) and answers in the same nine-field format:

    <path>\\t<x>\\t<y>\\t<w>\\t<h>\\t<left eye x>\\t<left eye y>\\t<right eye x>\\t<right eye y>      or      <path>\\tnone

The "eyes" are a red dot and a green dot. A picture with no dots has no face. Everything else is the engine's own code on synthetic media.
"""
import os
import shutil
import stat
import subprocess
import sys

STUB_PY = r'''
import sys
import numpy as np
from PIL import Image

MODE = %(mode)r      # dots: find the red and green dots | none: never find a face | fail: exit 3 | short: answer one frame too few
MIN_PIXELS = 40      # a dot is at least this many pixels in a frame; anything smaller is noise


def eyes(path):
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    found = []
    for m in ((R > 180) & (G < 90) & (B < 90), (G > 180) & (R < 90) & (B < 90)):
        ys, xs = np.nonzero(m)
        if len(xs) < MIN_PIXELS:
            return None
        found.append((float(xs.mean()), float(ys.mean())))
    return found


paths = sys.argv[1:]
if MODE == "fail":
    print("the stub was told to fail", file=sys.stderr)
    sys.exit(3)
if MODE == "short":
    paths = paths[:-1]
for p in paths:
    e = None if MODE == "none" else eyes(p)
    if e is None:
        print(p + "\tnone")
        continue
    (lx, ly), (rx, ry) = e
    mx, my = (lx + rx) / 2, (ly + ry) / 2
    w = 2.6 * max(1.0, ((rx - lx) ** 2 + (ry - ly) ** 2) ** 0.5)
    print("\t".join([p] + ["%%.2f" %% v for v in (mx - w / 2, my - 0.4 * w, w, w, lx, ly, rx, ry)]))
'''


def make_stub(folder, mode="dots"):
    """an executable that answers like facepts: (folder)/facepts-<mode>. A one-line shell wrapper runs the python stub, so no path needs a shebang"""
    folder = str(folder)
    py = os.path.join(folder, f"facepts-{mode}.py")
    sh = os.path.join(folder, f"facepts-{mode}")
    with open(py, "w") as f:
        f.write(STUB_PY % {"mode": mode})
    with open(sh, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{py}" "$@"\n')
    os.chmod(sh, os.stat(sh).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return sh


def use_stub(monkeypatch, fl, folder, mode="dots"):
    """make facelock.track() run the stub instead of the Apple helper; returns the stub's path"""
    path = make_stub(folder, mode)
    monkeypatch.setattr(fl.swiftbin, "ensure", lambda helper_dir: path)
    return path


def ff(*args):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


def eyes_video(path, w=540, h=960, secs=3, at=(220, 300), gap=100, amp=(24, 14), dot=18, crf=12):
    """a picture with a "face": a red dot (left eye) and a green dot (right eye) `gap` px apart that sway around `at` (the left eye's top-left corner)
    by `amp` px across and up and down, over a grey moving test picture, with a sine track. 30 fps, BT.709."""
    x, y = f"{at[0]}+{amp[0]}*sin(2*PI*t/3)", f"{at[1]}+{amp[1]}*cos(2*PI*t/2)"
    ff("-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=30:d={secs},hue=s=0", "-f", "lavfi", "-i", f"color=c=red:s={dot}x{dot}:r=30", "-f", "lavfi", "-i", f"color=c=lime:s={dot}x{dot}:r=30",
       "-f", "lavfi", "-i", f"sine=d={secs}",
       "-filter_complex", f"[0:v][1:v]overlay=x='{x}':y='{y}':shortest=1[a];[a][2:v]overlay=x='{at[0] + gap}+{amp[0]}*sin(2*PI*t/3)':y='{y}':shortest=1,format=yuv420p[v]",
       "-map", "[v]", "-map", "3:a", "-t", str(secs), "-c:v", "libx264", "-crf", str(crf), "-c:a", "aac", *TAGS, str(path))
    return str(path)


def faceless_video(path, w=540, h=960, secs=1):
    """a grey moving picture with nobody in it"""
    ff("-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r=30:d={secs},hue=s=0", "-c:v", "libx264", "-pix_fmt", "yuv420p", *TAGS, str(path))
    return str(path)


def swiftc_works():
    return sys.platform == "darwin" and bool(shutil.which("swiftc"))
