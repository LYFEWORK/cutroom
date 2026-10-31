#!/usr/bin/env python3
"""face.py: where the speaker's face is, on this Mac (Apple Vision through the engine/swift/facerect helper, built on first use).

    face.py track  <video.mp4> <out.json>              one face box per second
    face.py anchor <video.mp4> <out.json> [samples]    the median face of N frames spread over the clip (24 by default)
    face.py room   <facetrack.json> <out.json> [MINIMAL|REDUCED|FULL]   the room tier of the framing

track writes
    {"W": 1080, "H", "samples": {"<sec>": {"cx", "cy", "w", "h"}}, "median": {...}, "seconds", "extracted": [the seconds that had a frame]}
The boxes are in a NOTIONAL canvas 1080 wide (a frame is read 540 px wide and its boxes doubled), H tall in the same proportion: 1920 for a 9:16
picture, 608 for a 16:9 one. They are not real pixels; a caller that needs real pixels multiplies by the file's own width / 1080. A second whose
frame has no face has no sample; the median is a fixed fallback (marked "fallback": true) when no second has one.

anchor writes {"cx", "cy", "w", "h", "hits", "frames", "fallback", "W", "H"} in the picture's own pixels: the median face centre and size over the
frames that had a face, and (0.5 W, 0.40 H) marked fallback when fewer than 3 did, so nothing downstream ever blocks on a take with no face.

room decides the tier from the room above the CROWN and below the chin inside the safe box (x 65-940, y 270-1420 of a 1080 x 1920 picture):
FULL (a hero word fits above the head), REDUCED, MINIMAL. The crown is measured, not read from Vision's face box (which starts at the brow):
scalp above the face box counted as free room and a word placed there landed on the head. `tier_max` caps it.
A swiftc, ffprobe, ffmpeg or facerect failure is one STOP line.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile

import common

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "swift"))
import swiftbin  # noqa: E402

stop = common.stop
USAGE = "face.py track <video.mp4> <out.json> | anchor <video.mp4> <out.json> [samples] | room <facetrack.json> <out.json> [MINIMAL|REDUCED|FULL]"
W, SW = 1080, 540            # the notional canvas width and the width a frame is read at
SAMPLE_AT = 0.5              # sample "s" is the frame extracted at s + 0.5 s (see _sample); at() has to read it at that time


def facerect_bin():
    return swiftbin.ensure(os.path.join(HERE, "swift", "facerect"))   # rebuilt when main.swift is newer than the binary


def _run(cmd, service):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        stop(f"{service} is down: {type(e).__name__}: {e}")


def canvas_h(vw, vh):
    """the notional canvas height for a picture vw x vh: 1080 wide, the same proportion (1920 for 9:16)"""
    return int(round(W * vh / vw))


# --- the per-second track --------------------------------------------------------------------------------------------------------

def parse_facerect(lines, scale):
    """facerect's lines ('<png>\\t<x>\\t<y>\\t<w>\\t<h>' in 540-px-wide frames) -> {second: box in the notional canvas}; a frame with no face has none"""
    out = {}
    for line in lines:
        p = line.strip().split("\t")
        if len(p) != 5:
            continue
        sec = int(re.search(r"s(\d{3})\.png$", p[0]).group(1))
        x, y, w, h = [float(v) for v in p[1:]]
        if w <= 0 or h <= 0:
            continue
        out[sec] = {"cx": round((x + w / 2) * scale), "cy": round((y + h / 2) * scale), "w": round(w * scale), "h": round(h * scale)}
    return out


def median_box(samples, H=1920):
    vals = list(samples.values())
    if not vals:
        return {"cx": W // 2, "cy": int(H * 0.45), "w": int(W * 0.4), "h": int(W * 0.4), "fallback": True}
    return {k: round(statistics.median(v[k] for v in vals)) for k in ("cx", "cy", "w", "h")}


def at(track, t):
    """Nearest sample to the time t, measuring each sample at the moment its frame was taken (second s is the frame at s + 0.5, not at s: reading it
    at s puts every box half a second early, and a placement test then judges against the NEXT second's face on a moving subject).
    track['median'] when nothing is within 2 s."""
    s = track["samples"]
    anchor = int(t)
    best_v, best_d = None, None
    for sec in range(anchor - 3, anchor + 4):
        v = s.get(str(sec))
        if v is None:
            continue
        d = abs(sec + SAMPLE_AT - t)
        if d <= 2 and (best_d is None or d < best_d):
            best_v, best_d = v, d
    return best_v if best_v is not None else track["median"]


def piece_of(pieces, t):
    """index of the cut piece (out_in .. out_out) that contains output time t, or None"""
    for i, p in enumerate(pieces or []):
        if p.get("out_in", 0.0) - 1e-6 <= t < p.get("out_out", 0.0) + 1e-6:
            return i
    return None


def zone(track, t, pieces=None, pad=0.0):
    """The face box at output time t, ESTIMATED: the track is one frame a second, so the box is linearly interpolated between the two samples that
    bracket t (sample s is the frame at s + 0.5) and grown by `pad` of its size on every side. Samples count only when they were measured inside
    the same piece of the cut as t (`pieces`: the EDL pieces with out_in/out_out), because a joint is a different moment of the take and its face
    says nothing about this one; across a joint, at the ends of the track, or with no pieces, the nearest sample alone is used. Returns a
    {cx, cy, w, h} dict, or None when the take has no face at all."""
    near = at(track, t)
    if near is None or near.get("fallback"):
        return None
    box = near
    samples = track.get("samples") or {}
    lo = int(math.floor(t - SAMPLE_AT))
    a, b = samples.get(str(lo)), samples.get(str(lo + 1))
    if a and b and (not pieces or piece_of(pieces, lo + SAMPLE_AT) == piece_of(pieces, t) == piece_of(pieces, lo + 1 + SAMPLE_AT)):
        k = min(1.0, max(0.0, t - (lo + SAMPLE_AT)))
        box = {key: a[key] + (b[key] - a[key]) * k for key in ("cx", "cy", "w", "h")}
    return {"cx": box["cx"], "cy": box["cy"], "w": box["w"] * (1 + 2 * pad), "h": box["h"] * (1 + 2 * pad)}


def over(track, t0, t1, pieces=None, pad=0.0):
    """The box that holds the face for the whole of [t0, t1]: the union of zone() at both ends, at every sample frame in between and on both sides of
    every joint inside the window (the picture changes moment at a joint, so anything that stays up across one has to clear the face on BOTH sides)."""
    pts = [t0, t1] + [s + SAMPLE_AT for s in range(int(math.ceil(t0 - SAMPLE_AT)), int(math.floor(t1 - SAMPLE_AT)) + 1) if t0 < s + SAMPLE_AT < t1]
    for p in pieces or []:
        for edge in (p.get("out_in", 0.0), p.get("out_out", 0.0)):
            if t0 < edge < t1:
                pts += [edge - 1e-3, edge + 1e-3]
    boxes = [z for z in (zone(track, x, pieces, pad) for x in pts) if z]
    if not boxes:
        return None
    x0 = min(b["cx"] - b["w"] / 2 for b in boxes)
    x1 = max(b["cx"] + b["w"] / 2 for b in boxes)
    y0 = min(b["cy"] - b["h"] / 2 for b in boxes)
    y1 = max(b["cy"] + b["h"] / 2 for b in boxes)
    return {"cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2, "w": x1 - x0, "h": y1 - y0}


def build(video, out):
    """Sample one frame a second of `video`, find the largest face in each, and write the track json to `out` (returned too)."""
    binary = facerect_bin()
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video], "ffprobe")
    try:
        dur = float(r.stdout.strip())
    except ValueError:
        stop(f"ffprobe is down: could not read duration of {video}: {r.stderr.strip()[:200] or 'empty output'}")
    p = common.probe(video)
    H = canvas_h(p["w"], p["h"]) if p else 1920
    tmp = tempfile.mkdtemp(prefix="facetrack-")
    try:
        return _sample(video, out, dur, tmp, binary, H)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # one PNG per second of the video


def _sample(video, out, dur, tmp, binary, H=1920):
    frames = []
    for s in range(int(dur) + 1):
        f = os.path.join(tmp, f"s{s:03d}.png")
        r = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s + 0.5:.2f}", "-i", video, "-frames:v", "1", "-vf", f"scale={SW}:-1", f], "ffmpeg")
        if r.returncode != 0:
            stop(f"ffmpeg is down: failed extracting frame at {s}s: {r.stderr.strip()[:200]}")
        if os.path.exists(f):
            frames.append(f)
    r = _run([binary] + frames, "facerect")
    if r.returncode != 0:
        stop(f"facerect is down: exit {r.returncode}: {r.stderr.strip()[:200]}")
    lines = r.stdout.splitlines()
    if frames and not lines:
        stop("facerect is down: produced no output for any frame")
    samples = parse_facerect(lines, W / SW)
    track = {"W": W, "H": H, "samples": {str(k): v for k, v in samples.items()}, "median": median_box(samples, H), "seconds": int(dur) + 1,
             "extracted": sorted(int(re.search(r"s(\d{3})\.png$", f).group(1)) for f in frames)}   # the seconds that HAD a frame: the container can outlast its video stream
    with open(out, "w") as f:
        json.dump(track, f, indent=1)
    return track


# --- the face anchor ---------------------------------------------------------------------------------------------------------------

def anchor(video, out, n=24):
    """The median face of `n` frames spread over the clip, in the picture's own pixels (see the module doc); written to `out` and returned."""
    binary = facerect_bin()
    p = common.probe(video)
    if not p:
        stop(f"no picture to find a face in: {video}")
    vw, vh, dur = p["w"], p["h"], p["dur"]
    tmp = tempfile.mkdtemp(prefix="faceanchor-")
    try:
        sc = vw / SW
        frames = []
        for i in range(n):
            f = os.path.join(tmp, f"f{i:02d}.png")
            r = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur * (i + 0.5) / n:.2f}", "-i", video, "-frames:v", "1", "-vf", f"scale={SW}:-1", f], "ffmpeg")
            if r.returncode != 0:
                stop(f"ffmpeg is down: failed extracting a frame of {video}: {r.stderr.strip()[:200]}")
            if os.path.exists(f):
                frames.append(f)
        res = _run([binary] + frames, "facerect").stdout.strip().splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    cs, ws = [], []
    for line in res:
        parts = line.split("\t")
        if len(parts) == 5:
            x, y, w, h = [float(q) for q in parts[1:]]
            cs.append(((x + w / 2) * sc, (y + h / 2) * sc))
            ws.append((w * sc, h * sc))
    hits = len(cs)
    if hits >= 3:
        cx, cy = statistics.median(c[0] for c in cs), statistics.median(c[1] for c in cs)
        fw, fh = statistics.median(w[0] for w in ws), statistics.median(w[1] for w in ws)
        fb = False
    else:
        cx, cy, fw, fh, fb = vw * 0.5, vh * 0.40, vw * 0.3, vw * 0.3, True
    doc = {"cx": round(cx), "cy": round(cy), "w": round(fw), "h": round(fh), "hits": hits, "frames": n, "fallback": fb, "W": vw, "H": vh}
    common.atomic_write_json(out, doc)
    return doc


# --- the room tier -------------------------------------------------------------------------------------------------------------------

SAFE = {"x0": 65, "x1": 940, "y0": 270, "y1": 1420}   # for a 1080 x 1920 picture; safe_box() scales the heights to other shapes
ORDER = ["MINIMAL", "REDUCED", "FULL"]
CROWN_H = 0.30       # the real crown sits 0.18 to 0.25 of the face height above Vision's face box top; 0.30 keeps about 30 px of margin
BEHIND_ROOM = 420    # px above the crown a hero word needs


def safe_box(H=1920):
    """SAFE for a canvas H tall (the heights keep their proportion of 1920)"""
    return dict(SAFE, y0=round(SAFE["y0"] * H / 1920), y1=round(SAFE["y1"] * H / 1920))


def room(box, H=1920):
    s = safe_box(H)
    top, bottom = box["cy"] - box["h"] * (0.5 + CROWN_H), box["cy"] + box["h"] / 2
    left, right = box["cx"] - box["w"] / 2, box["cx"] + box["w"] / 2
    return {"above": int(top - s["y0"]), "below": int(s["y1"] - bottom), "left": int(left - s["x0"]), "right": int(s["x1"] - right)}


def tier_for(track, tier_max="FULL"):
    H = track.get("H", 1920)
    m = track["median"]
    if m.get("fallback") or not track.get("samples"):
        t, reason, r = "MINIMAL", "no face found; text stays in the lowest safe band", {"above": 0, "below": 0, "left": 0, "right": 0}
    else:
        r = room(m, H)
        if r["above"] >= BEHIND_ROOM:
            t, reason = "FULL", f"{r['above']}px above the crown (a hero word fits), {r['below']}px below the chin"
        elif r["below"] >= 260 or r["above"] >= 300:
            t, reason = "REDUCED", f"only {r['above']}px above the crown (a hero word needs {BEHIND_ROOM}px): nothing goes behind the head"
        else:
            t, reason = "MINIMAL", f"tight framing (above {r['above']}px, below {r['below']}px)"
    if ORDER.index(t) > ORDER.index(tier_max):
        t, reason = tier_max, reason + f"; capped at {tier_max}"
    return {"tier": t, "reason": reason, "room": r, "safe": safe_box(H)}


def _main():
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "track":
        t = build(a[0], a[1])
        print(f"face samples: {len(t['samples'])}/{t['seconds']} median {t['median']}")
    elif cmd == "anchor":
        d = anchor(a[0], a[1], int(a[2]) if len(a) > 2 else 24)
        print(f"face @({d['cx']},{d['cy']}) size {d['w']}x{d['h']} hits {d['hits']}/{d['frames']}{' FALLBACK' if d['fallback'] else ''} -> {a[1]}")
    elif cmd == "room":
        tm = a[2].upper() if len(a) > 2 else "FULL"
        if tm not in ORDER:
            stop(f"unknown tier_max {tm!r} (one of {', '.join(ORDER)})")
        t = tier_for(json.load(open(a[0])), tm)
        common.atomic_write_json(a[1], t)
        print(t["tier"], "-", t["reason"])
    else:
        stop(f"unknown command {cmd!r}: usage: {USAGE}")


if __name__ == "__main__":
    support.cli(_main, USAGE)
