#!/usr/bin/env python3
"""personmatte.py <video.mp4> <windows.json> <outdir>
Person matte for the behind-text windows: frames -> Apple Vision person segmentation (personmask) ->
RGBA webp named by absolute frame (f00066.webp). Only frames inside the windows are produced.
No silent fallbacks: a swiftc/ffmpeg/personmask failure raises keys.EngineStop."""
import json, os, shutil, subprocess, sys, tempfile
from PIL import Image, ImageFilter
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from keys import cli, EngineStop
import swiftbin
HELPER = os.path.join(os.path.dirname(os.path.dirname(HERE)), "engine", "swift", "personmask")   # the Apple Vision helper's source folder; its binary is built into bin/ on first use
BIN = None   # the built helper, set by build()
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
        raise EngineStop(f"{service} is down: {type(e).__name__}: {e}")
def build(video, windows, outdir, fps=30):
    """Every frame the windows ask for, or EngineStop. Composing whatever listdir found meant a
    short extraction shipped a reel with the matte missing over those frames (I3)."""
    global BIN
    want = frame_names(windows)
    BIN = swiftbin.ensure(HELPER)   # rebuilt when personmask/main.swift is newer than the binary
    os.makedirs(outdir, exist_ok=True); tmp = tempfile.mkdtemp(prefix="matte-")
    try:
        return _extract(video, windows, outdir, fps, tmp, want)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)     # one 1080x1920 PNG per frame; the disk is at 97%
def _extract(video, windows, outdir, fps, tmp, want):
    for f0, f1 in windows:
        f0 = max(0, int(f0)); n = int(f1) - f0 + 1
        r = _run(["ffmpeg", "-y", "-v", "error", "-ss", _seek(f0, fps), "-i", video, "-frames:v", str(n), "-vf", f"fps={fps}", "-start_number", str(f0), os.path.join(tmp, "f%05d.png")], "ffmpeg")
        if r.returncode != 0:
            raise EngineStop(f"ffmpeg is down: failed extracting frames {f0}-{f1}: {r.stderr.strip()[:200]}")
    frames = sorted(os.path.join(tmp, x) for x in os.listdir(tmp) if x.endswith(".png") and ".mask." not in x)
    r = _run([BIN] + frames, "personmask")
    if r.returncode != 0:
        raise EngineStop(f"personmask is down: exit {r.returncode}: {r.stderr.strip()[:200]}")
    made = set()
    for f in frames:
        m = f.replace(".png", ".mask.png")
        if not os.path.exists(m):
            raise EngineStop(f"personmask is down: no mask produced for {os.path.basename(f)}")
        base = os.path.basename(f)
        compose(f, m, os.path.join(outdir, base.replace(".png", ".webp"))); made.add(base[:-4])
    missing = [i for i in want if f"f{i:05d}" not in made]
    if missing:
        raise EngineStop(f"personmatte produced {len(made)} of {len(want)} requested frames: "
                         f"{len(missing)} missing (first {missing[0]}, last {missing[-1]}). The reel "
                         f"would render with no matte over those frames.")
    return len(made)
def _main():
    video, wins, outdir = sys.argv[1:4]
    n = build(video, json.load(open(wins)), outdir)
    print(f"{n} matte frames -> {outdir}")
if __name__ == "__main__":
    cli(_main, "personmatte.py <video.mp4> <windows.json> <outdir>")
