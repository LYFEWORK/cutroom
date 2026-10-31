#!/usr/bin/env python3
"""facelock.py <video.mp4> <out.mp4> --strength S [--edl <cut.edl.json>] [--cap 0.15] [--sigma 2.5] [--hold 15] [--width N]
Face lock-on ("subject tracking"): the footage moves against the speaker's head, so the face stays pinned and the background drifts.
The anchor is the midpoint between the eyes, read on every frame (Apple Vision through the engine/swift/facepts helper, built on demand).
  strength  how much of the head movement the frame follows: 0 leaves the picture untouched, 1 pins the face. Required: there is no default
  edl       the cut's EDL (cut.py). Each piece (out_in .. out_out) gets its own rest point, the spot the face is held at: a joint is another
            moment of the take, so a rest point for the whole cut would have to cover every place the head ever was. Without it the video is one piece
  cap       the most the picture may be punched in (0.15 = 15%). The punch-in is ONE number for the whole video, the smallest that keeps every
            frame edge off screen, so the head is the same size on both sides of every joint. A piece that would need more than the cap follows
            less instead (its own lower strength, named in the report): a frame edge never shows
  sigma     zero-lag Gaussian smoothing of the anchor, in frames: it takes the detector's jitter out without trailing the head
  hold      a run of frames with no face longer than this eases the frame back to rest instead of guessing where the speaker went
  width     output width (default: the video's own). A 4K take rendered at 1080 pays for the punch-in with spare pixels, not sharpness
The picture has to be SDR BT.709 at a constant frame rate (the mezzanine and the cut are); the audio is copied as it is, and so is the video's
comment tag. Writes <out>.json: the punch-in, each piece's rest point and strength, how far the eye line travelled before and after, and where the
frames were read. plan() is the maths, with no video in it.

A cut made from a bigger camera file (lock(sources=...); the command line has no flag for it): the tracker and the plan still run on the cut, and
each piece's frames are read from its camera file instead, conformed the way the cut's source was (cover-scaled and centre-cropped to the cut's
shape, at the cut's frame rate) but only down to the width the punch-in needs, so the 1080 output is sampled from more than 1080 pixels. Every
frame read that way is checked against the cut's own frame on a small copy: a piece that is not the same picture (another take, another grade) or
is a frame out of step makes the whole video fall back to its own frames, and the report says why. So does a camera file that is no bigger than
the video, or one ffmpeg will not read. Never a silent difference: summary() says which way it went.

Camera moves (lock(moves=...)): a push, a pull-out or a punch on top of the lock, each a size that changes over its own window, scaled about the
eye line so the face stays where the lock holds it. They are read from the camera file with the lock's frames, so a 4K take pays for them in pixels
too. A punch fakes a second camera, and an enlarged one reads soft: it is kept only when the camera file pays for at least PUNCH_MIN (brought down
to what the file pays, past that), and summary() says when one is dropped. Pushes and pulls enlarge from the cut when they have to, like an emphasis
zoom. With moves the strength may be 0: the picture is then moved by them alone.
No silent fallbacks past that: a swiftc/ffprobe/ffmpeg/facepts failure, an HDR picture or a video with no face in any frame is one STOP line."""
import sys

import support  # first: stops with one line on a Python older than 3.12

import glob
import json
import math
import os
import shutil
import subprocess
import tempfile

import numpy as np

import common

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "swift"))
import swiftbin  # noqa: E402

stop = common.stop
CAP, SIGMA, HOLD = 0.15, 2.5, 15
TRACK_W = 540     # the tracker reads a 540-px copy: its eye centres land within about a pixel of the full-size read
MARGIN = 1.004    # the punch-in holds the frame edge about two source pixels off screen at the far end of the move: the bicubic resample reads that far
JUMP = 0.5        # a read more than this many face widths from its neighbours is another face or a bad read (reject)
SAME = 3.0        # a camera-file frame and the cut's frame, both an eighth of the size, differ by at most this many levels of 255 on average when they are one
                  # picture: 0.4 to 1.3 over the pieces of real cuts (joined clips, 8 and 10 bit camera files, an HLG phone clip), where the same pieces read one
                  # frame off came out 1.3 to 21 times further apart; another take or a missing grade reads 8 and up
LEAD = 30         # frames read ahead of a piece in its camera file, so the frame-rate conversion has the frame before the piece's first one
PUNCH_MIN = 1.25  # a punch the camera file pays for less than this reads as a nudge, not a second camera (a cut reads as a new shot from about a 20 to 30% change in size)
RGB = "in_color_matrix=bt709:in_range=tv:flags={}+accurate_rnd+full_chroma_int,format=rgb24"   # YUV to RGB the accurate way: ffmpeg's quick path for an 8-bit 4:2:0
                  # picture at its own size reads about 2 levels of 255 dark on every channel, and the locked picture carried that
USAGE = "facelock.py <video.mp4> <out.mp4> --strength <0 to 1> [--edl <cut.edl.json>] [--cap 0.15] [--sigma 2.5] [--hold 15] [--width N]"


def _run(cmd, service, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, **kw)
    except OSError as e:
        stop(f"{service} is down: {type(e).__name__}: {e}")


def probe(video):
    """(width, height, frame rate as ffmpeg writes it, frame rate as a number) of the picture; an HDR picture stops here"""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,color_transfer", "-of", "json", video], "ffprobe", text=True)
    try:
        s = json.loads(r.stdout)["streams"][0]
        n, d = s["r_frame_rate"].split("/")
        W, H, fps = int(s["width"]), int(s["height"]), float(n) / float(d)
    except (ValueError, KeyError, IndexError, ZeroDivisionError):
        stop(f"ffprobe is down: could not read the picture of {video}: {r.stderr.strip()[:200] or 'no video stream'}")
    if s.get("color_transfer") in ("arib-std-b67", "smpte2084"):
        stop(f"{video} is HDR ({s['color_transfer']}): lock the SDR cut, not the camera file")
    return W, H, s["r_frame_rate"], fps


def parse_facepts(lines, scale):
    """facepts lines, one a frame -> (anchor (n, 2): the midpoint between the eyes, NaN where no face was found; size (n,): the face box's width)"""
    anchor, size = np.full((len(lines), 2), np.nan), np.full(len(lines), np.nan)
    for i, line in enumerate(lines):
        p = line.rstrip("\n").split("\t")
        if len(p) != 9: continue
        v = [float(x) * scale for x in p[1:]]
        if v[2] <= 0: continue
        anchor[i] = ((v[4] + v[6]) / 2, (v[5] + v[7]) / 2); size[i] = v[2]
    return anchor, size


def track(video, W):
    """every frame of the video through facepts -> (anchor, size) in the video's own pixels (parse_facepts)"""
    binary = swiftbin.ensure(os.path.join(HERE, "swift", "facepts"))   # rebuilt when facepts/main.swift is newer than the binary
    tmp = tempfile.mkdtemp(prefix="facelock-")
    try:
        r = _run(["ffmpeg", "-v", "error", "-y", "-i", video, "-map", "0:v:0", "-fps_mode", "passthrough", "-vf", f"scale={TRACK_W}:-2", "-q:v", "3",
                  os.path.join(tmp, "f%06d.jpg")], "ffmpeg", text=True)
        frames = sorted(glob.glob(os.path.join(tmp, "f*.jpg")))
        if r.returncode != 0 or not frames:
            stop(f"ffmpeg is down: could not read the frames of {video}: {r.stderr.strip()[:200] or 'no frames'}")
        lines = []
        for i in range(0, len(frames), 400):   # a whole cut is thousands of paths: a few hundred a call keeps the command line short
            r = _run([binary] + frames[i:i + 400], "facepts", text=True)
            if r.returncode != 0:
                stop(f"facepts is down: exit {r.returncode}: {r.stderr.strip()[:200]}")
            lines += r.stdout.splitlines()
        if len(lines) != len(frames):
            stop(f"facepts is down: {len(lines)} lines for {len(frames)} frames")
        return parse_facepts(lines, W / TRACK_W)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # a JPEG a frame of the whole cut


def spans_of(pieces, fps, n):
    """the cut's pieces as frame ranges [(f0, f1)] that cover frames 0 .. n with no gap: each piece starts on its out_in and runs to the next one's.
    No usable pieces (no EDL, a montage's stub): the whole video is one piece"""
    starts = sorted({min(n, max(0, int(round(p["out_in"] * fps)))) for p in pieces or []
                     if isinstance(p, dict) and isinstance(p.get("out_in"), (int, float)) and isinstance(p.get("out_out"), (int, float))} | {0})
    starts = [s for s in starts if s < n] or [0]
    return list(zip(starts, starts[1:] + [n]))


def gauss(a, sigma, carry=False):
    """a (n, k) smoothed down its length with a Gaussian of sigma frames, centred on each frame (no lag). Past each end the first and last value are held;
    with `carry` the movement carries on instead (the track mirrored through its end point), so a head that is moving when a piece starts or ends
    is not held back there: a cut has two such ends for every joint, and holding the value left the face sliding for the first frames after each one"""
    if sigma <= 0 or len(a) < 2: return a.copy()
    r = int(np.ceil(sigma * 3)); k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
    q = min(r, len(a) - 1) if carry else 0   # a piece shorter than the window carries on as far as it has frames to mirror, then holds
    pad = lambda x: np.pad(np.pad(x, q, mode="reflect", reflect_type="odd") if q else x, r - q, mode="edge")
    return np.stack([np.convolve(pad(a[:, i]), k, mode="valid") for i in range(a.shape[1])], axis=1)


def trust(found, hold):
    """1 where the track is good; eases to 0 and back across any run of frames with no face longer than `hold`"""
    n = len(found); w = np.ones(n); i = 0
    while i < n:
        if found[i]: i += 1; continue
        j = i
        while j < n and not found[j]: j += 1
        if j - i > hold: w[i:j] = 0
        i = j
    return np.clip(gauss(w[:, None], hold / 2)[:, 0], 0, 1) if (w == 0).any() else w


def reject(anchor, size, spans, jump=JUMP, win=3):
    """the anchor with every jumping read taken out (NaN): a frame whose eye midpoint sits more than `jump` face widths from the median of its
    neighbours in the same piece (`win` frames either side) is someone walking behind the speaker or a bad read, and following it would throw the frame"""
    out = anchor.copy()
    if not np.isfinite(size).any(): return out
    far = jump * float(np.nanmedian(size))
    for f0, f1 in spans:
        a = anchor[f0:f1]
        for i in range(len(a)):
            if np.isnan(a[i, 0]): continue
            nb = a[max(0, i - win):i + win + 1]; nb = nb[~np.isnan(nb[:, 0])]
            if len(nb) >= 3 and np.hypot(*(a[i] - np.median(nb, axis=0))) > far: out[f0 + i] = np.nan
    return out


def zoom_for(reach, W, H):
    """the smallest punch-in that keeps every edge off screen when the frame moves `reach` (x, y) pixels either side of rest; 1.0 when it does not move"""
    m = max(reach[0] / W, reach[1] / H)
    return MARGIN / max(1e-6, 1 - 2 * m) if m > 0 else 1.0


def plan(anchor, spans, W, H, strength, cap=CAP, sigma=SIGMA, hold=HOLD, size=None):
    """the lock for every frame, with no video in it. anchor: (n, 2) eye midpoints in source pixels, NaN where no face was found; spans: the pieces
    as frame ranges; size: each frame's face width, when the jumping reads should be thrown out first (reject).
    -> {"zoom", "follow": (n, 2), "pin": (n, 2), "pieces": [{"f0", "f1", "rest", "pin", "strength", "want", "found"}]}. Output pixel p of frame i shows
    source pixel (p - pin[i]) / zoom + follow[i] (box() is the source rectangle that makes). Inside a piece `follow` is the smoothed anchor pulled toward
    the piece's rest point by 1 - strength, so at strength 0 it IS the rest point, the zoom is 1 and every frame is the source frame.
    A piece's `want` is the punch-in it would need at the strength asked for; past 1 + cap its own `strength` comes down until it fits"""
    anchor = np.asarray(anchor, dtype=float)
    if size is not None: anchor = reject(anchor, np.asarray(size, dtype=float), spans)
    centre = np.array([W / 2, H / 2]); rows = []
    for f0, f1 in spans:
        a = anchor[f0:f1]; found = ~np.isnan(a[:, 0])
        rest, d = centre.copy(), np.zeros((f1 - f0, 2))   # no face in the piece: it sits at the centre of the punch-in and does not move
        if found.any():
            idx = np.arange(f1 - f0)
            smooth = gauss(np.stack([np.interp(idx, idx[found], a[found, i]) for i in (0, 1)], axis=1), sigma, carry=True)   # filled inside the piece only, never across a joint
            rest = (smooth.max(axis=0) + smooth.min(axis=0)) / 2          # midrange: the rest point that costs the least punch-in
            d = (smooth - rest) * trust(found, hold)[:, None]             # where the frame goes at strength 1
        rows.append({"f0": f0, "f1": f1, "rest": rest, "d": d, "reach": np.abs(d).max(axis=0) if len(d) else np.zeros(2), "found": int(found.sum())})
    zoom = min(max([zoom_for(r["reach"] * strength, W, H) for r in rows] or [1.0]), 1 + cap)
    room = np.array([W / 2, H / 2]) * max(0.0, 1 - MARGIN / zoom)         # how far the frame may move either side of rest at this punch-in
    follow, pin = np.zeros((len(anchor), 2)), np.zeros((len(anchor), 2))
    for r in rows:
        reach = r["reach"] * strength
        k = min([1.0] + [room[i] / reach[i] for i in (0, 1) if reach[i] > room[i]])   # past the cap: this piece follows less
        r["strength"], r["want"] = strength * k, zoom_for(reach, W, H)
        r["pin"] = centre + zoom * (r["rest"] - centre)                   # where the eye line sits in the output: where it sat, punched in about the centre
        follow[r["f0"]:r["f1"]] = r["rest"] + r.pop("d") * r["strength"]
        pin[r["f0"]:r["f1"]] = r["pin"]
        del r["reach"]
    return {"zoom": float(zoom), "follow": follow, "pin": pin, "pieces": rows}


def box(pl, i, W, H):
    """the source rectangle (x0, y0, x1, y1) that output frame i shows: it has to stay inside 0 .. W, 0 .. H or a frame edge is on screen"""
    z = zooms(pl)[i]
    x0, y0 = pl["follow"][i] - pl["pin"][i] / z
    return x0, y0, x0 + W / z, y0 + H / z


def zooms(pl):
    """each frame's punch-in: the lock's one number times that frame's camera move (pl["scale"], 1 where there is none)"""
    return pl["zoom"] * pl.get("scale", np.ones(len(pl["follow"])))


def ease(u):
    """a camera move's curve from 0 to 1: slow off the mark and slow into its end, with no snap in the middle"""
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, u)))


def curve(m):
    """(the size a move opens at, the size it eases to): a push grows to its z, a pull-out comes down from it, a punch holds it"""
    return {"push": (1.0, m["z"]), "pull": (m["z"], 1.0), "punch": (m["z"], m["z"])}[m["kind"]]


def scales(moves, n, fps):
    """the camera moves as one size a frame (n,), 1 outside them. A move {"kind", "start_s", "end_s", "ramp": [r0, r1], "z"} holds its opening size
    from start_s, eases across the ramp and holds its last size until end_s, where the next frame is back at 1 (the cut there hides it)"""
    s = np.ones(n)
    for m in moves:
        a, b = curve(m)
        r0, r1 = m["ramp"]
        for i in range(max(0, int(round(m["start_s"] * fps))), min(n, int(round(m["end_s"] * fps)))):
            s[i] = a + (b - a) * ease((i / fps - r0) / max(1e-6, r1 - r0))
    return s


def paid(moves, pay):
    """the moves with each punch held to what the camera file pays for (`pay`: the most the frames read can be punched in sharp; None when they are
    the video's own). A punch below PUNCH_MIN is dropped ("on": False, with "why"); between that and its own size it comes down to `pay`. Pushes and
    pulls are always kept: they enlarge from the video like an emphasis zoom when nothing pays"""
    out = []
    for m in moves:
        m = {**m, "on": True, "asked": m.get("asked", m["z"])}
        if m["kind"] == "punch":
            if pay is None:
                m.update(on=False, why="the frames are the cut's own, and a punch enlarged from it reads soft")
            elif pay < PUNCH_MIN:
                m.update(on=False, why=f"the camera file pays for {pay:.2f}x, under the {PUNCH_MIN:g}x a punch needs to read as a second camera")
            else:
                m["z"] = round(min(m["asked"], pay), 3)
        out.append(m)
    return out


def even(v): return int(round(v / 2)) * 2


class NotTheCut(Exception):
    """a camera file's frames are not the video's own picture: lock() goes back to the video's frames and its report says why"""


class NotRead(NotTheCut):
    """a camera file would not read this time (a drive that dropped, a copy that stopped half way): the same fallback, and the report marks it "again"
    so the caller does not take this locked picture for the best the file can give"""


def shown(path):
    """(width, height) of a file's picture as it plays: a camera file stored on its side with a quarter-turn tag plays upright. None when ffprobe cannot read it"""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation", "-of", "json", path], "ffprobe", text=True)
    try:
        st = json.loads(r.stdout)["streams"][0]
        w, h = int(st["width"]), int(st["height"])
        rot = next((int(float(d["rotation"])) for d in st.get("side_data_list") or [] if "rotation" in d), 0) or int((st.get("tags") or {}).get("rotate") or 0)
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return (h, w) if rot % 180 == 90 else (w, h)


def fits(sw, sh, W, H, most=None):
    """the widest picture of the shape W:H that a source sw x sh fills without being enlarged (`most`: and no wider than this, rounded up) -> (w, h), both even"""
    step = 2 * W // math.gcd(W, H)   # the widths whose height is a whole, even number of pixels at exactly W:H
    w = int(min(sw, sh * W / H)) // step * step
    if most is not None: w = min(w, int(math.ceil(most / step)) * step)
    return w, w * H // W


def comment_of(video):
    """the video's comment tag ("" when it has none): copied to the locked picture as it is"""
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "default=nw=1:nk=1", video], "ffprobe", text=True)
    return r.stdout.strip()


def _stop(*procs):
    for p in procs:
        if p is None: continue
        if p.poll() is None: p.kill()
        for f in (p.stdout, p.stderr, p.stdin):
            if f:
                try: f.close()
                except OSError: pass
        p.wait()


def _own(video, DW, DH):
    """the video's own frames, DW x DH (ffmpeg does the downscale of a bigger video, so the move is close to a pure shift)"""
    from PIL import Image
    size, dec = DW * DH * 3, None
    try:
        try:
            dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-map", "0:v:0", "-fps_mode", "passthrough", "-vf",
                                    f"scale={DW}:{DH}:" + RGB.format("lanczos"), "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            stop(f"ffmpeg is down: {type(e).__name__}: {e}")
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size: break
            yield Image.frombuffer("RGB", (DW, DH), buf)
        err = dec.stderr.read().decode(errors="replace")
        if dec.wait() != 0:
            stop(f"ffmpeg is down: the locked picture did not render: {err.strip()[-300:]}")
    finally:
        _stop(dec)


def _camera(video, pl, W, H, rate, fps, DW, DH, feeds, seen):
    """each piece's frames from its camera file, DW x DH, in the cut's order: feeds is one (file, first frame, filters) a piece of pl. The file is
    conformed the way the cut's source was (the frame rate first, so a frame number means what it meant there; then cover-scaled and centre-cropped
    to the cut's shape) and its timestamps are kept through the seek, so the frame-rate conversion drops and repeats the frames it did then. Every
    frame is compared with the cut's own on a copy an eighth of the size; NotTheCut says which piece is a frame out of step (the read one frame
    off fits better), another picture (further apart than SAME) or too still to tell (a tie), so only a piece shown to be in step is used.
    seen gets one (levels apart, levels apart one frame early, one frame late) a piece: the averages the check was made on"""
    from PIL import Image
    TW, TH = max(16, W // 8), max(16, H // 8)
    size, tsize, chk, dec = DW * DH * 3, TW * TH * 3, None, None
    said = tempfile.TemporaryFile()   # what ffmpeg says of a camera file goes to a file, not a pipe: a damaged file can say more than a pipe holds, and a full pipe would hang the read
    try:
        try:
            chk = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-map", "0:v:0", "-fps_mode", "passthrough", "-vf",
                                    f"scale={TW}:{TH}:" + RGB.format("area"), "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as e:
            stop(f"ffmpeg is down: {type(e).__name__}: {e}")
        for i, (r, (file, first, vf)) in enumerate(zip(pl["pieces"], feeds), 1):
            n = r["f1"] - r["f0"]
            where = f"piece {i} ({r['f0'] / fps:.2f}-{r['f1'] / fps:.2f} s) of {os.path.basename(file)}"
            t = (first - LEAD) / fps
            cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{t:.6f}"] if t > 0 else []) + ["-copyts", "-start_at_zero", "-i", file, "-map", "0:v:0", "-fps_mode", "passthrough", "-vf",
                   f"fps={rate},trim=start_pts={first}:end_pts={first + n},scale={DW}:{DH}:force_original_aspect_ratio=increase:flags=lanczos,crop={DW}:{DH}," + (vf + "," if vf else "")
                   + "scale=" + RGB.format("bicubic"), "-f", "rawvideo", "-"]
            said.seek(0); said.truncate()
            try:
                dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=said)
            except OSError as e:
                stop(f"ffmpeg is down: {type(e).__name__}: {e}")
            d0 = back = ahead = 0.0
            was = None                      # the frame before: (the camera file's, the cut's)
            for k in range(n):
                buf = dec.stdout.read(size)
                if len(buf) < size:
                    dec.wait(); said.seek(0)
                    err = said.read().decode(errors="replace").strip()
                    if err: raise NotRead(f"{where} would not read ({err[-160:]})")
                    raise NotTheCut(f"{where} ends {n - k} frame{'s' if n - k > 1 else ''} short of the cut")
                img = Image.frombuffer("RGB", (DW, DH), buf)
                cb = chk.stdout.read(tsize)
                if len(cb) < tsize: return   # the cut has fewer frames than the tracker read: lock() stops on the count
                a = np.asarray(img.resize((TW, TH), Image.BOX), dtype=np.int16)
                c = np.frombuffer(cb, np.uint8).reshape(TH, TW, 3).astype(np.int16)
                d0 += float(np.abs(a - c).mean())
                if was is not None:
                    back += float(np.abs(was[0] - c).mean())     # the cut's frame against the camera frame before it
                    ahead += float(np.abs(a - was[1]).mean())    # the cut's frame before against this camera frame
                was = (a, c)
                yield img
            _stop(dec); dec = None
            d0 /= max(1, n); back /= max(1, n - 1); ahead /= max(1, n - 1)
            seen.append((d0, back, ahead) if n > 1 else (d0, None, None))
            near = min(back, ahead) if n > 1 else None
            if near is not None and near < d0 and near <= SAME:
                raise NotTheCut(f"{where} is a frame out of step with the cut (read one frame {'earlier' if back < ahead else 'later'} it fits: {near:.2f} levels of 255 apart against {d0:.2f})")
            if d0 > SAME:
                raise NotTheCut(f"{where} is not the picture in the cut (they differ by {d0:.1f} levels of 255, and two copies of one frame differ by {SAME:g} at most)")
            if near is not None and near <= d0:
                raise NotTheCut(f"{where} is too still to tell one frame from the next, so it cannot be checked against the cut")
    finally:
        _stop(chk, dec); said.close()


def render(video, out, pl, W, H, rate, OW, big=None):
    """every frame moved by the plan and encoded again; the audio and the comment tag are copied. Returns the frames written.
    Without `big` the frames are the video's own, moved by a bicubic resample (sub-pixel). big (bigger()'s first value) reads them from the camera
    files instead (_camera) and cuts each output frame out of the bigger frame in one Lanczos resample; NotTheCut passes through with no file left"""
    from PIL import Image
    zf = zooms(pl); OH = even(OW * H / W)
    if big: DW, DH = big["size"]
    else: DW = min(W, even(OW * float(zf.max()))); DH = even(DW * H / W)
    kx, ky = DW / W, DH / H
    frames = _camera(video, pl, W, H, rate, big["fps"], DW, DH, big["feeds"], big["seen"]) if big else _own(video, DW, DH)
    note = comment_of(video)
    try:
        enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OW}x{OH}", "-r", rate, "-i", "-",
                                "-i", video, "-map", "0:v", "-map", "1:a?", "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                                "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-g", "15", "-c:a", "copy", "-movflags", "+faststart", "-color_range", "tv",
                                "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"]   # the filter's output carries the matrix only: this writes all three tags
                               + (["-metadata", f"comment={note}"] if note else []) + [out], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        stop(f"ffmpeg is down: {type(e).__name__}: {e}")
    n = 0
    try:
        for img in frames:
            if n < len(pl["follow"]):
                a, p, zoom = pl["follow"][n], pl["pin"][n], zf[n]
                # the source rectangle box() gives, held inside the frame: a camera move about an eye line near an edge slides over rather than show the edge
                x0, y0 = min(max(0.0, a[0] - p[0] / zoom), W - W / zoom), min(max(0.0, a[1] - p[1] / zoom), H - H / zoom)
                if big:   # in the bigger frame's pixels
                    frame = img.resize((OW, OH), Image.LANCZOS, box=(kx * x0, ky * y0, kx * x0 + DW / zoom, ky * y0 + DH / zoom))
                else:
                    # output pixel q shows source pixel q * W / (OW * zoom) + x0; kx, ky take that into the decoded frame
                    m = (kx * W / (OW * zoom), 0, kx * x0, 0, ky * H / (OH * zoom), ky * y0)
                    frame = img.transform((OW, OH), Image.AFFINE, m, resample=Image.BICUBIC)
                enc.stdin.write(frame.tobytes())
            n += 1
        enc.stdin.close()
    except BrokenPipeError:
        frames.close()   # the encoder died: its own message is read below, and a decoder must not sit on a pipe nobody reads
    except BaseException:
        frames.close(); _stop(enc)
        if os.path.exists(out): os.remove(out)
        raise
    ee = enc.stderr.read().decode(errors="replace")
    if enc.wait() != 0:
        if os.path.exists(out): os.remove(out)   # half a locked picture is not one: nothing downstream may find it
        stop(f"ffmpeg is down: the locked picture did not render: {ee.strip()[-300:]}")
    return n


def bigger(sources, pieces, spans, fps, W, H, OW, zoom):
    """what render() needs to read the cut's frames from its camera files ({"size", "feeds", "fps", "seen"}) and what the report says of it
    ({"mode": "camera", ...}), or (None, why) when they cannot pay for the punch-in (NotRead when one of them is not a picture ffprobe can read).
    sources: one {"file", "frame", "vf"} for each of `pieces`, in order: the piece's first frame is frame `frame` of the file once it is at the cut's
    frame rate, and `vf` (optional) is whatever makes its colours the cut's (HDR to SDR, a grade)"""
    by = {}
    for p, s in zip(pieces or [], sources):
        if isinstance(p, dict) and isinstance(p.get("out_in"), (int, float)) and isinstance(s, dict) and s.get("file") and isinstance(s.get("frame"), int) and s["frame"] >= 0:
            by.setdefault(int(round(p["out_in"] * fps)), s)
    feeds = [by.get(f0) for f0, _ in spans]
    if len(sources) != len(pieces or []) or None in feeds:
        return None, "its pieces do not each have a camera file"
    wide = {}
    for f in dict.fromkeys(s["file"] for s in feeds):
        sz = shown(f) if os.path.exists(f) else None
        if not sz: raise NotRead(f"the camera file would not read ({f})")
        wide[f] = fits(sz[0], sz[1], W, H)
    small = min(wide, key=lambda f: wide[f][0])
    if wide[small][0] <= W:
        return None, f"the camera file is no bigger than the cut ({os.path.basename(small)} fills {wide[small][0]} x {wide[small][1]}, the cut is {W} x {H})"
    DW, DH = fits(wide[small][0], wide[small][1], W, H, most=OW * zoom)
    return ({"size": (DW, DH), "feeds": [(s["file"], s["frame"], s.get("vf") or "") for s in feeds], "fps": fps, "seen": []},
            {"mode": "camera", "width": DW, "height": DH, "files": [{"file": f, "fills": list(wide[f])} for f in wide]})


def travel(anchor, pl):
    """how far the eye line moves on screen inside one piece, at most: [(x, y) as shot, (x, y) locked], in pixels of the picture at its own size, over the
    frames that have a face"""
    raw, locked = np.zeros(2), np.zeros(2)
    out = pl["pin"] + zooms(pl)[:, None] * (anchor - pl["follow"])   # where each tracked eye midpoint lands on the output
    for r in pl["pieces"]:
        a, o = anchor[r["f0"]:r["f1"]], out[r["f0"]:r["f1"]]
        ok = ~np.isnan(a[:, 0])
        if ok.sum() < 2: continue
        raw = np.maximum(raw, np.ptp(a[ok], axis=0)); locked = np.maximum(locked, np.ptp(o[ok], axis=0))
    return [[round(float(v), 1) for v in raw], [round(float(v), 1) for v in locked]]


def lock(video, out, strength, pieces=None, cap=CAP, sigma=SIGMA, hold=HOLD, width=None, sources=None, moves=()):
    """track the video, plan the lock and render it to `out`; writes <out>.json and returns it (summary() turns it into plain lines).
    sources: where a bigger copy of each piece's picture is (bigger()). The tracker and the plan stay on the video; only the frames that are
    moved come from there, and rep["source"] says whether they did ("camera") or why not ("cut", with "why", and "again" when the file would not
    read this time). moves: the camera moves on top of the lock (scales()); rep["moves"] says which were made and at what size (paid())"""
    if not (0 < strength <= 1 or (strength == 0 and moves)): stop(f"strength {strength:g} locks nothing: it runs from above 0 to 1")
    if not 0 < cap < 1: stop(f"cap {cap:g} is not a punch-in: 0.15 is 15%")
    W, H, rate, fps = probe(video)
    anchor, size = track(video, W)
    if strength and not np.isfinite(anchor[:, 0]).any(): stop(f"no face in any frame of {video}: there is nothing to lock on")   # moves alone scale about the frame's centre then
    spans = spans_of(pieces, fps, len(anchor))
    anchor = reject(anchor, size, spans)
    pl = plan(anchor, spans, W, H, strength, cap, sigma, hold)
    OW, n = width or W, len(anchor)
    big, source = None, {"mode": "cut"}
    if sources is not None:
        try:
            big, source = bigger(sources, pieces, spans, fps, W, H, OW, pl["zoom"] * float(scales(moves, n, fps).max()))   # read wide enough for the biggest move asked
            if big is None: source = {"mode": "cut", "why": source}
            else:
                made = paid(moves, big["size"][0] / (OW * pl["zoom"]))
                pl["scale"] = scales([m for m in made if m["on"]], n, fps)
                frames = render(video, out, pl, W, H, rate, OW, big)
        except NotTheCut as e:
            big, source = None, {"mode": "cut", "why": str(e)}
            if isinstance(e, NotRead): source["again"] = True   # not a fact about the file's picture: worth another try once it reads
    if not big:
        made = paid(moves, None)
        pl["scale"] = scales([m for m in made if m["on"]], n, fps)
        frames = render(video, out, pl, W, H, rate, OW)
    if frames != len(anchor):
        if os.path.exists(out): os.remove(out)
        stop(f"the tracker read {len(anchor)} frames of {video} and the renderer read {frames}: the lock would slide off the face")
    if big:   # what the check read, for the record: the piece furthest from the cut's own frames, and the closest a one-frame-off read came to passing
        off = [min(b, a) / max(d, 1e-9) for d, b, a in big["seen"] if b is not None]
        source["check"] = {"levels_apart": round(max(d for d, _, _ in big["seen"]), 2), "limit": SAME, "a_frame_off_is_worse_by": round(min(off), 2) if off else None}
    rep = {"strength": strength, "cap": cap, "sigma": sigma, "hold": hold, "zoom": round(pl["zoom"], 4), "W": W, "H": H, "fps": round(fps, 3), "frames": frames,
           "frames_without_face": int(np.isnan(anchor[:, 0]).sum()), "travel": travel(anchor, pl), "source": source,
           "pieces": [{"out_in": round(r["f0"] / fps, 4), "out_out": round(r["f1"] / fps, 4), "frames_with_face": r["found"], "strength": round(float(r["strength"]), 3),
                       "want": round(float(r["want"]), 4), "rest": [round(float(v), 1) for v in r["rest"]], "pin": [round(float(v), 1) for v in r["pin"]]} for r in pl["pieces"]],
           "moves": made}
    with open(out + ".json", "w") as f:
        json.dump(rep, f, indent=1)
    return rep


def summary(rep):
    """the report as plain lines: the punch-in and what it bought, where the frames were read when a camera file was offered, then one line for every
    piece that follows less than asked or has no face, and one for every camera move: made at what size, or dropped and why"""
    n, (raw, locked) = len(rep["pieces"]), rep["travel"]
    lines = [f"strength {rep['strength']:g}: punch-in {100 * (rep['zoom'] - 1):.1f}% on the whole video, {n} piece{'s' if n != 1 else ''} each with its own rest point, "
             f"a face in {rep['frames'] - rep['frames_without_face']} of {rep['frames']} frames; inside a piece the eye line moved up to {raw[0]:.0f} px across and "
             f"{raw[1]:.0f} px up and down as shot, and {locked[0]:.0f} and {locked[1]:.0f} px now"] if rep["strength"] else []
    src, what = rep.get("source") or {}, "the punch-in" if rep["strength"] else "the camera moves"
    if src.get("mode") == "camera":
        names = ", ".join(f"{os.path.basename(f['file'])} ({f['fills'][0]} x {f['fills'][1]} at the cut's shape)" for f in src["files"])
        lines.append(f"{what} {'is' if rep['strength'] else 'are'} paid from the camera file: every frame is read {src['width']} px wide from {names} and checked against the cut, not enlarged from the {rep['W']} px cut")
    elif src.get("why"):
        lines.append(f"{what} {'is' if rep['strength'] else 'are'} enlarged from the {rep['W']} px cut, not paid from the camera file: {src['why']}")
    for m in rep.get("moves") or ():
        where = f"{m['kind']} {m['start_s']:.2f}-{m['end_s']:.2f} s" + (f" ('{m['label']}')" if m.get("label") else "")
        if not m["on"]: lines.append(f"{where} dropped: {m['why']}")
        else: lines.append(f"{where} at {m['z']:.2f}x" + (f", down from {m['asked']:.2f}x: that is what the camera file pays for" if m["z"] < m["asked"] - 1e-6 and m["kind"] == "punch" else ""))
    for i, p in enumerate(rep["pieces"] if rep["strength"] else (), 1):
        where = f"piece {i} ({p['out_in']:.2f}-{p['out_out']:.2f} s)"
        if not p["frames_with_face"]: lines.append(f"{where} has no face to lock on: it plays as shot, at the same punch-in")
        elif p["strength"] < rep["strength"] - 5e-4:
            lines.append(f"{where} would need a {100 * (p['want'] - 1):.1f}% punch-in, past the {100 * rep['cap']:.0f}% cap: it follows at {p['strength']:.2f}, not {rep['strength']:g}")
    return lines


def _main():
    args = sys.argv[1:]; opt = {}
    while len(args) > 2:
        k, v = args[-2:]; args = args[:-2]
        if k not in ("--strength", "--edl", "--cap", "--sigma", "--hold", "--width"): raise ValueError(k)
        opt[k[2:]] = v
    video, out = args
    if "strength" not in opt: raise ValueError("--strength")
    pieces = None
    if opt.get("edl"):
        with open(opt["edl"]) as f:
            pieces = json.load(f).get("pieces")
    rep = lock(video, out, float(opt["strength"]), pieces, float(opt.get("cap", CAP)), float(opt.get("sigma", SIGMA)), int(opt.get("hold", HOLD)),
               int(opt["width"]) if opt.get("width") else None)
    for line in summary(rep): print(line)


if __name__ == "__main__":
    support.cli(_main, USAGE)
