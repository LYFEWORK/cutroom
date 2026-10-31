"""The soft build's legibility read. The soft captions drop the dark outline, so before render both planners (this chain's
cluster_plan.py and the video editor's own caption planner) measure how bright the picture is under each lockup's box across its life on screen. A bright
patch calls first for the deeper shadow, then for a move to the next zone down, then for a flag. This module only lays out the boxes to read
(lockup_boxes), cuts a lockup's life into the pieces it is read in (pieces), measures (box_luma, brightest) and classifies: where a lockup goes is
the planner's call. ffmpeg reads the frames and numpy takes the percentile."""
import math
import os
import subprocess

import numpy as np

BRIGHT = 170          # 8-bit luma from here up, a lockup needs the deeper shadow
VERY_BRIGHT = 215     # from here up, the deeper shadow is not enough and the lockup moves
_PERCENTILE = 90      # of the luma under the box: a patch that fills a tenth of it decides, a stray pixel does not


def box_luma(video: str, box: tuple[int, int, int, int], t0: float, t1: float, n: int = 6,
             canvas: tuple[int, int] = (1080, 1920)) -> float:
    """The 90th percentile of 8-bit luma inside box (x0, y0, x1, y1) over n frames spread evenly from t0 to t1 seconds, every pixel of every
    frame counted once. Each frame is scaled to canvas (width, height) first, so box is in canvas pixels with its right and bottom edges
    left out; a box past the canvas is cut to it and a float box takes every pixel it touches. A time before the start reads the first
    frame, a time past the last frame is left out, and n of 1 reads the middle of the span.

    It refuses, each time in one plain line that starts "legibility: ": no video at the path, and no ffmpeg (FileNotFoundError); an n under
    1, a time or a box edge that is not a real number, a span that ends before it starts, a box with no pixel of the canvas, and a span with
    no frame of the video in it (ValueError); and a file ffmpeg cannot read (OSError)."""
    if not os.path.isfile(video):
        raise FileNotFoundError(f"legibility: no video at {video}")
    if n < 1:
        raise ValueError(f"legibility: n is how many frames to read, so at least 1, not {n}")
    if not (math.isfinite(t0) and math.isfinite(t1)):
        raise ValueError(f"legibility: the span needs real times, not {t0} and {t1}")
    if t1 < t0:
        raise ValueError(f"legibility: the span ends at {t1:.2f} s, before it starts at {t0:.2f} s")
    if not all(math.isfinite(v) for v in box):
        raise ValueError(f"legibility: the box needs real numbers, not {tuple(box)}")
    w, h = canvas
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = max(0, math.floor(x0)), max(0, math.floor(y0)), min(w, math.ceil(x1)), min(h, math.ceil(y1))
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"legibility: the box {tuple(box)} holds no pixel of the {w}x{h} canvas")
    under = []
    for t in _times(t0, t1, n):
        frame = _frame(video, t, w, h)
        if frame is not None:
            under.append(frame[y0:y1, x0:x1].ravel())
    if not under:
        raise ValueError(f"legibility: no frame of {video} between {t0:.2f} s and {t1:.2f} s")
    return float(np.percentile(np.concatenate(under), _PERCENTILE))


def brightest(video: str, reads: list[tuple], n: int = 6, fps: float = 30) -> float:
    """The highest box_luma of reads, each (box, t0, t1) on video: a read takes n frames, or one a frame it lasts when it is shorter (at least
    one)."""
    return max(box_luma(video, box, t0, t1, max(1, min(n, int((t1 - t0) * fps) + 1))) for box, t0, t1 in reads)


def pieces(t0: float, t1: float, spans: list[tuple]) -> list[tuple]:
    """[(a, b, tag or None)]: t0 to t1 cut where a span (start, end, tag) starts or ends inside it, each piece with the tag of the last span in
    spans that holds its middle, or None. A planner reads a lockup's life in these pieces: the engine each through the punch-in that zooms it
    (its spans in order of start, so the latest to start wins, as the renderer takes it), the video editor only where no full-screen scene covers his
    picture."""
    cuts = sorted({t0, t1} | {x for a, b, _ in spans for x in (a, b) if t0 < x < t1})
    out = []
    for a, b in zip(cuts, cuts[1:]):
        held = [tag for s0, s1, tag in spans if s0 <= (a + b) / 2 < s1]
        out.append((a, b, held[-1] if held else None))
    return out or [(t0, t1, None)]


def lockup_boxes(lines: list[tuple[float, float, float, bool]], top: float, gap: float) -> list[tuple[float, float, float, float]]:
    """The boxes to read under a lockup, (x0, y0, x1, y1) in canvas px, both planners' one layout of it. Its lines are (centre x, drawn width,
    size, on the bar), stacked down from top, each as tall as its size, gap px between two. A line on the bar sits on the opaque accent, so it
    is left out and each run of lines off the bar is one box, from its leftmost edge to its rightmost: a bar between a lead-in and a tail
    leaves two boxes."""
    boxes, run = [], []
    for line in list(lines) + [None]:
        if line is None or line[3]:
            if run:
                boxes.append((min(c - w / 2 for (c, w, _, _), _ in run), run[0][1], max(c + w / 2 for (c, w, _, _), _ in run),
                              run[-1][1] + run[-1][0][2]))
            run = []
        else:
            run.append((line, top))
        if line is not None:
            top += line[2] + gap
    return boxes


def remedy(luma: float) -> str:
    """What a lockup over this luma needs: "ok" under BRIGHT, "deeper" (the deeper shadow) under VERY_BRIGHT, else "move"."""
    if luma < BRIGHT:
        return "ok"
    if luma < VERY_BRIGHT:
        return "deeper"
    return "move"


def _times(t0: float, t1: float, n: int) -> list[float]:
    """n times spread evenly from t0 to t1 (the middle of the span when n is 1), none before 0, to the millisecond, a repeat once."""
    spread = [(t0 + t1) / 2] if n == 1 else [t0 + (t1 - t0) * k / (n - 1) for k in range(n)]
    return list(dict.fromkeys(max(0.0, round(t, 3)) for t in spread))


# _frame is a near twin of reel_qc._one_gray (the same ffmpeg read of one gray frame); it stays separate, with its own refusals in this module's words
def _frame(video: str, t: float, w: int, h: int) -> np.ndarray | None:
    """The gray frame at t seconds, scaled to w x h as h rows of w, or None when the video has no frame there."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-an", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
           "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError as e:
        raise FileNotFoundError("legibility: ffmpeg is not installed") from e
    if done.returncode != 0:
        why = (done.stderr.decode("utf-8", "replace").strip().splitlines() or ["it gave no reason"])[-1]
        raise OSError(f"legibility: ffmpeg could not read {video}: {why}")
    if len(done.stdout) < w * h:
        return None
    return np.frombuffer(done.stdout[:w * h], np.uint8).reshape(h, w)
