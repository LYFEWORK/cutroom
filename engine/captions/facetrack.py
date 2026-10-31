#!/usr/bin/env python3
"""facetrack.py <video.mp4> <out.json>
One face box per second (Apple Vision via engine/swift/facerect, built from main.swift on demand),
scaled to the 1080x1920 canvas. Lines from facerect: '<png>\\t<x>\\t<y>\\t<w>\\t<h>' (540-px wide frames).
Writes {"W","H","samples":{"<sec>":{"cx","cy","w","h"}},"median":{...},"seconds","extracted":[secs that had a frame]}. A frame with w==0 has no face.
No silent fallbacks: a swiftc/ffprobe/ffmpeg/facerect failure raises keys.EngineStop."""
import json, math, os, re, shutil, statistics, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from keys import cli, EngineStop
import swiftbin
HELPER = os.path.join(os.path.dirname(os.path.dirname(HERE)), "engine", "swift", "facerect")   # the Apple Vision helper's source folder; its binary is built into bin/ on first use
BIN = None   # the built helper, set by build()
W, H, SW = 1080, 1920, 540
def parse_facerect(lines, scale):
    out = {}
    for line in lines:
        p = line.strip().split("\t")
        if len(p) != 5: continue
        sec = int(re.search(r"s(\d{3})\.png$", p[0]).group(1)); x, y, w, h = [float(v) for v in p[1:]]
        if w <= 0 or h <= 0: continue
        out[sec] = {"cx": round((x + w / 2) * scale), "cy": round((y + h / 2) * scale), "w": round(w * scale), "h": round(h * scale)}
    return out
def median_box(samples):
    vals = list(samples.values())
    if not vals: return {"cx": W // 2, "cy": int(H * 0.45), "w": int(W * 0.4), "h": int(W * 0.4), "fallback": True}
    return {k: round(statistics.median(v[k] for v in vals)) for k in ("cx", "cy", "w", "h")}
SAMPLE_AT = 0.5   # sample "s" is the frame extracted at s + 0.5 s (see _sample); at() must read it at that time
def at(track, t):
    """Nearest sample to the literal float t, measuring each sample at the moment its frame was taken (second s is the frame at
    s + 0.5, not at s: reading it at s put every box half a second early, so the caption planner and the G4 sweep tested
    placement against the NEXT second's face on a moving subject - 151-162 px of "caption on the face" that was
    the face of half a second later). track['median'] when nothing is within 2 s."""
    s = track["samples"]
    anchor = int(t)
    best_v, best_d = None, None
    for sec in range(anchor - 3, anchor + 4):
        v = s.get(str(sec))
        if v is None: continue
        d = abs(sec + SAMPLE_AT - t)
        if d <= 2 and (best_d is None or d < best_d):
            best_v, best_d = v, d
    return best_v if best_v is not None else track["median"]
def piece_of(pieces, t):
    """index of the cut piece (out_in .. out_out) that contains output time t, or None"""
    for i, p in enumerate(pieces or []):
        if p.get("out_in", 0.0) - 1e-6 <= t < p.get("out_out", 0.0) + 1e-6: return i
    return None
def zone(track, t, pieces=None, pad=0.0):
    """The face box at output time t, ESTIMATED: the track is one frame a second, so the box is linearly interpolated between the two
    samples that bracket t (sample s is the frame at s + 0.5) and grown by `pad` of its size on every side. Samples count only when
    they were measured inside the same piece of the cut as t (`pieces`: the EDL pieces with out_in/out_out), because a joint is a
    different moment of the take and its face says nothing about this one; across a joint, at the ends of the track, or with no
    pieces, the nearest sample alone is used. Returns a {cx, cy, w, h} dict, or None when the take has no face at all."""
    near = at(track, t)
    if near is None or near.get("fallback"): return None
    box = near
    samples = track.get("samples") or {}
    lo = int(math.floor(t - SAMPLE_AT))
    a, b = samples.get(str(lo)), samples.get(str(lo + 1))
    if a and b and (not pieces or piece_of(pieces, lo + SAMPLE_AT) == piece_of(pieces, t) == piece_of(pieces, lo + 1 + SAMPLE_AT)):
        k = min(1.0, max(0.0, t - (lo + SAMPLE_AT)))
        box = {key: a[key] + (b[key] - a[key]) * k for key in ("cx", "cy", "w", "h")}
    return {"cx": box["cx"], "cy": box["cy"], "w": box["w"] * (1 + 2 * pad), "h": box["h"] * (1 + 2 * pad)}
def over(track, t0, t1, pieces=None, pad=0.0):
    """The box that holds the face for the whole of [t0, t1]: the union of zone() at both ends, at every sample frame in between and on
    both sides of every joint inside the window (the picture changes moment at a joint, so a caption that stays up across one has to
    clear the face on BOTH sides of it: a word placed for the first piece sat on the chin of the second)."""
    pts = [t0, t1] + [s + SAMPLE_AT for s in range(int(math.ceil(t0 - SAMPLE_AT)), int(math.floor(t1 - SAMPLE_AT)) + 1) if t0 < s + SAMPLE_AT < t1]
    for p in pieces or []:
        for edge in (p.get("out_in", 0.0), p.get("out_out", 0.0)):
            if t0 < edge < t1: pts += [edge - 1e-3, edge + 1e-3]
    boxes = [z for z in (zone(track, x, pieces, pad) for x in pts) if z]
    if not boxes: return None
    x0 = min(b["cx"] - b["w"] / 2 for b in boxes); x1 = max(b["cx"] + b["w"] / 2 for b in boxes)
    y0 = min(b["cy"] - b["h"] / 2 for b in boxes); y1 = max(b["cy"] + b["h"] / 2 for b in boxes)
    return {"cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2, "w": x1 - x0, "h": y1 - y0}
def _run(cmd, service):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        raise EngineStop(f"{service} is down: {type(e).__name__}: {e}")
def build(video, out):
    global BIN
    BIN = swiftbin.ensure(HELPER)   # rebuilt when facerect/main.swift is newer than the binary
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video], "ffprobe")
    try:
        dur = float(r.stdout.strip())
    except ValueError:
        raise EngineStop(f"ffprobe is down: could not read duration of {video}: {r.stderr.strip()[:200] or 'empty output'}")
    tmp = tempfile.mkdtemp(prefix="facetrack-")
    try:
        return _sample(video, out, dur, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # one PNG per second, twice per take, disk at 97% (I9)
def _sample(video, out, dur, tmp):
    frames = []
    for s in range(int(dur) + 1):
        f = os.path.join(tmp, f"s{s:03d}.png")
        r = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s + 0.5:.2f}", "-i", video, "-frames:v", "1", "-vf", f"scale={SW}:-1", f], "ffmpeg")
        if r.returncode != 0:
            raise EngineStop(f"ffmpeg is down: failed extracting frame at {s}s: {r.stderr.strip()[:200]}")
        if os.path.exists(f): frames.append(f)
    r = _run([BIN] + frames, "facerect")
    if r.returncode != 0:
        raise EngineStop(f"facerect is down: exit {r.returncode}: {r.stderr.strip()[:200]}")
    lines = r.stdout.splitlines()
    if frames and not lines:
        raise EngineStop("facerect is down: produced no output for any frame")
    samples = parse_facerect(lines, W / SW)
    track = {"W": W, "H": H, "samples": {str(k): v for k, v in samples.items()}, "median": median_box(samples), "seconds": int(dur) + 1,
             "extracted": sorted(int(re.search(r"s(\d{3})\.png$", f).group(1)) for f in frames)}   # the seconds that HAD a frame: the container can outlast its video stream
    json.dump(track, open(out, "w"), indent=1); return track
def _main():
    t = build(sys.argv[1], sys.argv[2])
    print(f"face samples: {len(t['samples'])}/{t['seconds']} median {t['median']}")
if __name__ == "__main__":
    cli(_main, "facetrack.py <video.mp4> <out.facetrack.json>")
