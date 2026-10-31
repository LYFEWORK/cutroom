#!/usr/bin/env python3
"""qc.py: the quality gates on a finished video, so a person never has to find a clipped word, a drifting voice or a wrong color by watching.

    qc.py <take folder> <name> [--faceless] [--video FILE] [--render-source FILE] [--reference FILE]
    qc.py color <video.mp4> [<reference.mp4>]                the color gate alone
    qc.py zones <final.mp4> <cut.mp4> [--profile organic|ads] [--every 0.5]     no overlay text under a platform's interface

The first form reads a take folder as run_take.sh leaves it (`<root>/<name>/`, with `<root>/mez/<name>.mp4` and `<root>/wx/<name>.avskew.json`
from prep) and checks the finished video, `<name>-cut-master.mp4` unless --video names another. It writes <take folder>/qc.json and
<take folder>/evidence/strip.png and contact-sheet.png, prints one line per gate, and exits 0 PASS, 1 FAIL, 2 STOP (a required input is missing).
A gate with nothing to measure says SKIP; it is never a silent pass.

Gates:
 G1 speech-only pieces      each EDL piece plays speech only: lead-in 0.35 s at most, tail 0.40 s at most, words in every piece, and a face in
                            95% of the 1 Hz samples. The face rule is for a talking head: --faceless (or a plan with "speaker": false) turns it off
 G2 picture == sound        the source conformed or clean, no frozen frame over 0.5 s in the mezzanine, the cut's frame matches the mezzanine it
                            was cut from (0.9 or better), and the audio lag is 60 ms at most and not growing
 G3 no clipped onsets       the joint check's geometry on the aligned stamps, 0.08 s tolerance; a clip the stamps claim is re-read off the waveform
                            (stretched stamps are not clips, a cut inside a 90 ms pause is not one, a word the plan struck is never played); a
                            JOINTS=warn waiver recorded in joints.json turns a G3 fail into a noted pass
 G4 captions in the box     ink sweep: nothing outside the safe box, nothing on the face, nothing above the crown unless a hook layer is live.
                            Only when <take folder>/captions-plan.json exists and a render source is given (the video the captions were drawn over)
 G5 caption events          no overlaps, nothing under 3 frames, spoken order, every word covered. Only when captions-plan.json exists
 G6 the visual read         HUMAN: this writes the strip (one frame a second) and the contact sheet (six frames with the safe box, the face box and
                            the crown line drawn on them) and records that a person still owes the read
 G7 loudness                -16 +/- 1 LUFS and a true peak of -1.5 dBTP at most
 COLOR                      yuv420p, limited range, BT.709 tags on the picture, and the picture's colors within 5 levels of the cut's own
 WORDS                      the finished file still says what the cut says: its own words (cut.words.json) against the cut's QC transcript
                            (ratio above 0.9), and the two durations within 0.2 s

WHY IT IS FAST. The sweeps are the whole cost, and they are ONE decode each: the video and its render source are opened once, piped as a 240-px-wide
grayscale rawvideo stream, and differenced frame against frame in numpy. The independent ffmpeg passes (freezedetect, loudnorm, the strip) are started
first and collected at the end, so they overlap the sweeps instead of queueing behind them. The only seeks are the 18 or fewer single frames the
frame match and the contact sheet need.

G3 calls cut.geometry() rather than cut.check_joints(): check_joints indexes joints off first_word_after(), which only looks 0.02 s back from c0, so a
clip deeper than that reports on a LATER word, and on a piece with no word after the joint it emits no joint at all. geometry() reads every piece's own
edges. It also never re-hears anything, so G3 costs nothing.

Two measurement floors. The ink threshold is 40/255 on the DOWNSCALED difference: a 240-px sweep averages about 4.5 x 4.5 source pixels, which puts
codec noise far below it and still lights up on caption text, but it also means the "3 stray pixels" allowance is 3 pixels at sweep scale, not at 1080.
And the audio lag inherits the AAC encoder's priming delay on both files (about 40 ms, constant); it cancels when both sides are AAC and does not
accumulate, which is why the gate cares about growth as well as magnitude.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import difflib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

import common
import cut
import face

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "swift"))
import swiftbin  # noqa: E402

stop = common.stop
USAGE = ("qc.py <take folder> <name> [--faceless] [--video FILE] [--render-source FILE] [--reference FILE] | qc.py color <video.mp4> [<reference.mp4>] | "
         "qc.py zones <final.mp4> <cut.mp4> [--profile organic|ads] [--every 0.5]")

CANVAS = (1080, 1920)          # the composition the safe box and face.py's boxes are stated in
SAFE = face.SAFE               # single-sourced: x 65-940, y 270-1420
CROWN_H = face.CROWN_H         # 0.30: the crown sits this far above Vision's face box
FPS = 30
SWEEP_W = 240                  # sweep width; the whole speed budget rides on this staying small
SWEEP_STEP = 2                 # every 2nd frame
INK_DIFF = 40                  # 0-255 on the downscaled difference: above codec noise, below text
STRAY_PX = 3                   # stray pixels tolerated outside the box / above the crown, per frame
STRAY_FACE_PX = 12             # ...and on the face box: it is a rectangle round an oval, placed from a 1 Hz track, so its corners are not face.
                               # 12 px at sweep scale is about 5% of one letter; a caption across the face lights up hundreds
LEAD_IN_MAX = 0.35             # dead footage before the first word
TAIL_MAX = 0.40                # dead footage after the last word
FACE_MIN = 0.95
STAMP_TOL = cut.STAMP_TOL      # 0.08: aligner onset jitter
FRAME_MATCH_MIN = 0.9          # a stale picture reads about 0.4
LAG_MAX_S = 0.060
LAG_GROWTH_S = 0.020           # drift that accumulates is a different failure from an offset
FREEZE_MIN_S = 0.5
CONTIG_S = 0.02                # the cutter snaps c0/c1 to the frame grid; closer than this is one edge, not two
MIN_EVENT_FRAMES = 3           # under this is a flash, not a caption
LUFS_TARGET, LUFS_TOL, TP_MAX = -16.0, 1.0, -1.5
ABOVE_CROWN_MODES = ("texthook", "behind", "front")   # the only layers that live above the head
MACHINE_GATES = ("G1", "G2", "G3", "G4", "G5", "G7", "COLOR", "WORDS")   # G6 is the human read; it has no verdict
SHEET_N = 6
SHEET_W = 360
WORDS_RATIO_MIN = 0.9
DURATION_DELTA_MAX_S = 0.2


# --- gate records ---------------------------------------------------------------------------------

def gate(ok, numbers, detail=(), skipped=False, **extra):
    d = {"pass": ok, "numbers": numbers, "detail": list(detail), "skipped": skipped}
    d.update(extra)
    return d


def line(name, g):
    """One line per gate."""
    if g.get("human"): status = "HUMAN"
    elif g.get("skipped"): status = "SKIP"
    else: status = "PASS" if g["pass"] else "FAIL"
    return f"{name} {status} {g['numbers']}"


def verdict_of(gates):
    return "FAIL" if any((gates.get(k) or {}).get("pass") is False for k in MACHINE_GATES) else "PASS"


# --- geometry helpers -----------------------------------------------------------------------------

def crown_y(face):
    """Top of the head, not the top of Vision's face box (which starts at the BROW): a word placed in the scalp gap lands ON the speaker. The same
    formula face.py uses."""
    return None if not face else face["cy"] - face["h"] * (0.5 + CROWN_H)


def face_at(track, t):
    """The sampled box nearest t, or None when this take has no face at all."""
    if not track or not (track.get("samples") or {}): return None
    f = face.at(track, t)
    return None if not f or f.get("fallback") else f


FACE_PAD = 0.06                # the interpolated face box is grown by this fraction of its size on every side


def face_zone(track, t, pieces=None, pad=FACE_PAD):
    """The face box a frame's ink is tested against: face.zone (the box interpolated between the two samples that bracket t, only inside one
    piece of the cut), grown by `pad`. The nearest-sample box alone put frames of "caption on the face" on a moving subject, half of them an
    off-by-half-second read, the rest the face between two samples."""
    return face.zone(track, t, pieces, pad) if track else None


def words_inside(src_words, c0, c1, eps=0.02):
    """The source words a piece actually plays -- the same rule cut.geometry uses."""
    return [w for w in src_words if w["end"] > c0 + eps and w["start"] < c1 - eps]


def face_fraction(track, t0, t1):
    """(fraction, sampled) over the face track's seconds whose sampled frame (s+0.5) lands in the piece. A piece shorter than the 1 Hz sampling
    grid can land on no second at all: that is unmeasured, not an empty frame, so it returns (None, 0) and the gate says so."""
    if not track: return None, 0
    samples = track.get("samples") or {}
    secs = [s for s in range(max(0, int(t0) - 1), int(t1) + 2) if t0 <= s + 0.5 < t1 - 0.02]   # a frame AT the end of the file does not exist
    if track.get("extracted") is not None:                    # and neither does one past the end of the video stream, which can stop short of the container (23.43 s of picture in a 23.54 s file, say)
        had = set(track["extracted"]); secs = [s for s in secs if s in had]
    if not secs: return None, 0
    have = sum(1 for s in secs if str(s) in samples)
    return have / len(secs), len(secs)


# --- G1 speech-only pieces ------------------------------------------------------------------------

def g1_speech_only(pieces, src_words, track, lead_max=LEAD_IN_MAX, tail_max=TAIL_MAX, face_min=FACE_MIN, check_face=True):
    """A piece whose c0 sits seconds before its first word, or whose c1 runs past its last word, plays dead footage (a stray clip of the speaker
    checking the camera) and passes every gate that only bounds NEGATIVE heads (clipping). `check_face` False (a faceless take) leaves the face rule out."""
    if not pieces: return gate(True, "no pieces in the EDL", skipped=True)
    bad, leads, tails, fracs, unmeasured = [], [], [], [], 0
    for i, p in enumerate(pieces):
        c0, c1 = p["c0"], p["c1"]
        inside = words_inside(src_words, c0, c1)
        if not inside:
            bad.append({"piece": i, "why": "no_words", "c0": round(c0, 3), "c1": round(c1, 3),
                        "out_in": p.get("out_in")})
        else:
            lead = round(inside[0]["start"] - c0, 3); tail = round(c1 - inside[-1]["end"], 3)
            leads.append(lead); tails.append(tail)
            if lead > lead_max:
                bad.append({"piece": i, "why": "lead_in", "s": lead, "max": lead_max, "out_in": p.get("out_in")})
            if tail > tail_max:
                bad.append({"piece": i, "why": "tail", "s": tail, "max": tail_max, "out_out": p.get("out_out")})
        frac, n = face_fraction(track, p.get("out_in", 0.0), p.get("out_out", 0.0)) if check_face else (None, 0)
        if frac is None: unmeasured += 1
        else:
            fracs.append(frac)
            if frac < face_min:
                bad.append({"piece": i, "why": "face", "face": round(frac, 3), "seconds": n,
                            "out_in": p.get("out_in")})
    if not check_face: face_txt = "face rule off (faceless)"
    elif track is None: face_txt = "face n/a (no face track)"
    elif fracs: face_txt = "face %d%%" % round(min(fracs) * 100)
    else: face_txt = "face n/a (no sampled second inside any piece)"
    numbers = (f"{len(pieces)} pieces, lead-in max {max(leads, default=0.0):.2f} (<= {lead_max}), "
               f"tail max {max(tails, default=0.0):.2f} (<= {tail_max}), {face_txt}")
    if unmeasured and check_face: numbers += f", {unmeasured} piece(s) too short to sample a face"
    return gate(not bad, numbers, bad[:12])


# --- G2 picture == sound --------------------------------------------------------------------------

def _skew_read(skew):
    """(ok|None, text). None = nothing on disk said anything, which is not a failure."""
    if not skew: return None, "no skew sidecar"
    c = skew.get("conform")
    if isinstance(c, dict):
        return bool(c.get("ok")), ("source conformed cleanly" if c.get("ok")
                                   else "conform FAILED: " + str(c.get("reason"))[:100])
    return bool(skew.get("ok")), ("no skew" if skew.get("ok") else "skew: " + str(skew.get("reason"))[:100])


def g2_picture_sound(skew, freezes, matches, lags,
                     match_min=FRAME_MATCH_MIN, lag_max=LAG_MAX_S, growth_max=LAG_GROWTH_S):
    """One root cause behind two kinds of complaint: a stalled capture whose picture runs late against its own audio. Four independent readings,
    because each one catches a different half of it: the source's own timelines, held frames in the mezzanine, the cut's picture against the mezzanine
    it was cut from, and the shipped audio against the same."""
    skipped = skew is None and freezes is None and not matches and not lags
    ok, skew_txt = _skew_read(skew)
    bad = []
    if ok is False: bad.append({"why": "skew", "detail": skew_txt})
    if freezes is None: freeze_txt = "freezes n/a (no mezzanine)"
    else:
        long = [f for f in freezes if f.get("duration", 0) > FREEZE_MIN_S]
        freeze_txt = f"freezes {len(long)}"
        for f in long[:5]:
            bad.append({"why": "freeze", "at": round(f.get("start", 0.0), 3), "s": round(f["duration"], 3)})
    if matches:
        worst = min(m["corr"] for m in matches)
        match_txt = f"frame-match min {worst:.2f} (>= {match_min})"
        for m in matches:
            if m["corr"] < match_min:
                bad.append({"why": "frame_match", "piece": m["piece"], "corr": m["corr"]})
    else:
        match_txt = "frame-match n/a"
    if lags:
        worst = max(abs(l["lag_s"]) for l in lags)
        growth = abs(lags[-1]["lag_s"]) - abs(lags[0]["lag_s"])
        lag_txt = f"lag max {worst * 1000:.0f}ms (<= {lag_max * 1000:.0f}), growth {growth * 1000:+.0f}ms"
        for l in lags:
            if abs(l["lag_s"]) > lag_max:
                bad.append({"why": "lag", "at": l["at"], "lag_s": l["lag_s"]})
        if growth > growth_max:
            bad.append({"why": "lag_growth", "s": round(growth, 4), "max": growth_max})
    else:
        lag_txt = "lag n/a"
    return gate(not bad, f"{skew_txt}, {freeze_txt}, {match_txt}, {lag_txt}", bad[:12], skipped=skipped)


# --- G3 no clipped onsets -------------------------------------------------------------------------

def splice_edges(pieces, contig=CONTIG_S):
    """(head_is_a_splice, tail_is_a_splice) per piece.

    The cutter splits a long segment at an interior silence and leaves the two halves ADJACENT in the source (one piece's c1 == the next's c0). The
    audio plays straight through that boundary: nothing is cut out of it, yet a word straddling it reads as a tail clip on one side and a head clip on
    the other, and the joint check reports both. A clip needs an edge to happen at; only a boundary that actually drops source is one. The video's
    own first head and last tail always count: nothing continues into them."""
    out = []
    for i, p in enumerate(pieces):
        head = i == 0 or abs(pieces[i - 1]["c1"] - p["c0"]) > contig
        tail = i == len(pieces) - 1 or abs(pieces[i + 1]["c0"] - p["c1"]) > contig
        out.append((head, tail))
    return out


def g3_onsets(pieces, src_words, stamps, tol=STAMP_TOL, contig=CONTIG_S, audio=None, waived=None, dropped=None):
    """`audio` (a cut.Audio of the mezzanine) re-reads an edge the stamps call clipped off the waveform: an aligner that stretched a short word into
    the silence before it is not a clip, and neither is a cut inside a pause of 90 ms or more. `dropped` is the EDL's list of struck words: never a
    word a piece plays, so never a clipped one. `waived` is the text run_take.sh recorded in joints.json when JOINTS=warn let a take continue past its
    clipped-word ladder: the clips are listed and the gate passes with the waiver in its numbers, so the human read knows to listen for them."""
    if not pieces: return gate(True, "no pieces in the EDL", skipped=True)
    geo = cut.geometry(pieces, src_words, tol=tol, audio=audio, dropped=dropped)
    edges = splice_edges(pieces, contig)
    bad, heads, tails = [], [], []
    for i, g in enumerate(geo):
        if g["head"] is None: continue
        head_splice, tail_splice = edges[i]
        if head_splice:
            heads.append(g["head"])
            if g["head"] < -tol:
                bad.append({"piece": i, "why": "head", "s": g["head"], "word": g["first"], "fix": "lead_extra"})
        if tail_splice:
            tails.append(g["tail"])
            if g["tail"] < -tol:
                bad.append({"piece": i, "why": "tail", "s": g["tail"], "word": g["last"], "fix": "tail_extra"})
    joined = sum(1 for i in range(len(pieces) - 1)
                 if abs(pieces[i]["c1"] - pieces[i + 1]["c0"]) <= contig)
    numbers = (f"{len(pieces)} pieces, worst head {min(heads, default=0.0):+.3f}s, "
               f"worst tail {min(tails, default=0.0):+.3f}s, tolerance {tol}s, stamps {stamps}")
    if joined: numbers += f", {joined} non-splice edge{'' if joined == 1 else 's'} skipped"
    if audio is not None: numbers += ", edges read off the waveform"
    if bad and waived:
        return gate(True, numbers + f", WAIVED ({waived}): {len(bad)} clip(s) recorded, listen for them", bad[:12], waived=True)
    return gate(not bad, numbers, bad[:12])


# --- G4 captions in the box and off the head ------------------------------------------------------

def ink_counts(diff, face, crown, thresh=INK_DIFF, safe=SAFE, canvas=CANVAS):
    """Caption ink in one swept frame: |reel - render source| over the threshold, counted against
    the three places it is not allowed to be. `diff` is at sweep scale; `face`/`crown` are in
    canvas coordinates, which is what face.py speaks."""
    h, w = diff.shape
    sx, sy = w / canvas[0], h / canvas[1]
    def bx(x): return max(0, min(w, int(round(x * sx))))
    def by(y): return max(0, min(h, int(round(y * sy))))
    m = diff >= thresh
    total = int(m.sum())
    # `safe` may be one box or several (the default box plus a plan-declared zone, e.g. cluster captions' top
    # third): ink is "inside" if it sits in ANY of them
    boxes = safe if isinstance(safe, (list, tuple)) else [safe]
    inmask = np.zeros_like(m)
    for b in boxes: inmask[by(b["y0"]):by(b["y1"]), bx(b["x0"]):bx(b["x1"])] = True
    inside = int((m & inmask).sum())
    on_face = 0
    if face:
        on_face = int(m[by(face["cy"] - face["h"] / 2):by(face["cy"] + face["h"] / 2),
                        bx(face["cx"] - face["w"] / 2):bx(face["cx"] + face["w"] / 2)].sum())
    above = int(m[:by(crown), :].sum()) if crown is not None else 0
    return {"ink": total, "outside": total - inside, "on_face": on_face, "above_crown": above}


def above_crown_allowed(events, t, fps=FPS, crown=None):
    """The hook layers live above the head by design. So does a running WORD a caption planner sent up there when the chin sits too low for the
    chest band: the planner writes such a word with its center `y` above the crown, and the sweep would otherwise count that ink as a crown fail.
    Accept a word event only when its planned y is above the crown measured at that frame: a word that merely drifted up is still a fail."""
    pad = 1.0 / fps
    for e in (events or []):
        if not (e["start"] - pad <= t < e["end"] + pad): continue
        if e.get("mode") in ABOVE_CROWN_MODES: return True
        if e.get("mode") == "word" and crown is not None and e.get("y") is not None and e["y"] < crown: return True
    return False


def g4_captions(frames, events, fps=FPS, stray=STRAY_PX, whips=(), stray_face=STRAY_FACE_PX):
    """A caption leaking past the safe box, a slam rendered on the crown and text across the speaker's mouth are all the same measurement on the
    shipped file, which is why they are one gate.
    `whips` (plan["whips"]): frames where the WHOLE picture blurs and pushes on purpose (a whip transition); the diff against the source reads that as
    ink everywhere. Those frames are not caption ink and are skipped; the count of skipped frames rides in the numbers so it is never silent."""
    if not frames:
        return gate(True, "no sweep (no render source, or the reel decoded no frames)", skipped=True)
    skip = set()
    for w in whips or []:
        n = int(w.get("frames", 4)); skip.update(range(int(w["at"]) - n, int(w["at"]) + n + 1))
    bad = []; skipped = 0
    for f in frames:
        if f["n"] in skip: skipped += 1; continue
        if f["outside"] > stray:
            bad.append({"why": "outside", "t": round(f["t"], 2), "n": f["n"], "px": f["outside"]})
        if f["on_face"] > stray_face:
            bad.append({"why": "face", "t": round(f["t"], 2), "n": f["n"], "px": f["on_face"]})
        if f["above_crown"] > stray and not above_crown_allowed(events, f["t"], fps, f.get("crown")):
            bad.append({"why": "crown", "t": round(f["t"], 2), "n": f["n"], "px": f["above_crown"]})
    n = {k: sum(1 for b in bad if b["why"] == k) for k in ("outside", "face", "crown")}
    numbers = (f"{len(frames)} frames swept, outside {n['outside']}, on face {n['face']}, "
               f"above crown {n['crown']}, max ink {max(f['ink'] for f in frames)} px"
               + (f", {skipped} whip frames skipped" if skipped else ""))
    return gate(not bad, numbers, bad[:12], frames=len(frames))


# --- G5 caption events ----------------------------------------------------------------------------

def _tok(t):
    return "".join(c for c in str(t).lower() if c.isalnum() or c == "'")


def covered(events, flags, w):
    """Is THIS spoken word on screen somewhere, or named in a flag? Matched by (text, start), never
    by text alone -- the same word spoken twice let the second one's event mask the first one's
    drop. A merged event or a merged lockup row carries several words under one
    timestamp, so a word also counts when it is one of that event's tokens and the event is on
    screen at the moment it was spoken."""
    key = (_tok(w["text"]), round(w["start"], 3))
    for e in events or []:
        if e.get("mode") in ("word", "keyword"):
            toks = [_tok(t) for t in str(e.get("text", "")).split()]
            # a glued emphasis token ("7 LEADS", "SHOWING UP?") is ONE spoken entry whose text spans several
            # tokens: whole-string equality at its own onset counts, before the per-token membership test
            if _tok(e.get("text", "")) == key[0] and abs(e["start"] - key[1]) <= 1.0 / FPS + 1e-6: return True
            if key[0] in toks and (round(e["start"], 3) == key[1]
                                   or (len(toks) > 1 and e["start"] - 1e-6 <= key[1] < e["end"] + 1e-6)):
                return True
        if e.get("mode") in ("behind", "front"):
            for x in e.get("words") or []:
                toks = [_tok(t) for t in re.split(r"[\s-]+", str(x.get("text", "")))]   # "go-to" covers go + to
                if key[0] in toks and (round(x.get("at", -99), 3) == key[1]
                                       or (len(toks) > 1 and x.get("at", 1e9) - 1e-6 <= key[1] < e["end"] + 1e-6)):
                    return True
    return any(key[0] in str(f).lower() for f in flags or [])


WHOLE_NEAR_S = 1.0     # an event that matches one spoken token as a whole must start within this of that word: "set up" is not the "setup" 11 s earlier


def _word_index(words, ev):
    """The spoken word an event is captioning: same token, nearest onset."""
    toks = [_tok(t) for t in str(ev.get("text", "")).split()]
    whole = _tok(ev.get("text", ""))
    cands = [i for i, w in enumerate(words) if _tok(w["text"]) == whole and abs(w["start"] - ev["start"]) <= WHOLE_NEAR_S]   # a glued token matches as a whole first
    if not cands: cands = [i for i, w in enumerate(words) if _tok(w["text"]) in toks]
    if not cands: return None
    return min(cands, key=lambda i: abs(words[i]["start"] - ev["start"]))


def g5_events(events, cut_words, flags, fps=FPS, samples=3):
    if events is None:
        return gate(True, "no captions-plan.json", skipped=True)
    bad = []
    ordered = sorted(events, key=lambda e: e["start"])
    running = [e for e in ordered if e.get("mode") == "word"]
    for a, b in zip(running, running[1:]):
        if b["start"] < a["end"] - 1e-6:
            bad.append({"why": "overlap", "a": a.get("text"), "b": b.get("text"),
                        "t": round(b["start"], 3), "by_s": round(a["end"] - b["start"], 3)})
    short_min = MIN_EVENT_FRAMES / fps - 1e-6
    for e in ordered:
        if e["end"] - e["start"] < short_min:
            bad.append({"why": "short", "text": e.get("text"), "t": round(e["start"], 3),
                        "frames": round((e["end"] - e["start"]) * fps, 2)})
    words = None if not cut_words else [w for w in cut_words.get("words", []) if not w.get("filler")]
    worst_dt = 0.0
    if words:
        idx = [(_word_index(words, e), e) for e in running]
        seen = [(i, e) for i, e in idx if i is not None]
        for (i, a), (j, b) in zip(seen, seen[1:]):
            if j < i:
                bad.append({"why": "order", "spoken": words[i]["text"], "then": words[j]["text"],
                            "t": round(b["start"], 3)})
        for w in words:
            if not covered(ordered, flags, w):
                bad.append({"why": "uncovered", "text": w["text"], "t": round(w["start"], 3)})
        if seen:
            step = max(1, len(seen) // max(1, samples))
            for i, e in seen[::step][:samples]:
                dt = abs(e["start"] - words[i]["start"])
                worst_dt = max(worst_dt, dt)
                if dt > 1.0 / fps + 1e-6:
                    bad.append({"why": "timing", "text": e.get("text"), "t": round(e["start"], 3),
                                "frames_late": round(dt * fps, 2)})
    n = {k: sum(1 for b in bad if b["why"] == k) for k in ("overlap", "short", "order", "uncovered", "timing")}
    numbers = (f"{len(ordered)} events, overlaps {n['overlap']}, under {MIN_EVENT_FRAMES} frames "
               f"{n['short']}, out of order {n['order']}, uncovered words {n['uncovered']}, "
               + (f"timing max {worst_dt * fps:.2f} frames" if words else "no cut.words.json"))
    return gate(not bad, numbers, bad[:12])


# --- G6 / G7 --------------------------------------------------------------------------------------

def g6_visual(strip):
    """Not automatable and not pretended to be. The list (wrong take, repeated line, dead footage, stale picture, does it open on the hook, is the
    payoff on screen at the end) is a read, so this gate produces the thing to read and records that a person still owes it."""
    return gate(None, f"read the strip: {strip}", human=True)


def g7_loudness(i, tp, target=LUFS_TARGET, tol=LUFS_TOL, tp_max=TP_MAX):
    if i is None or tp is None:
        return gate(True, "loudness not measured (silent or unreadable audio)", skipped=True)
    bad = []
    if abs(i - target) > tol: bad.append({"why": "integrated", "lufs": i, "target": target, "tol": tol})
    if tp > tp_max: bad.append({"why": "true_peak", "dbtp": tp, "max": tp_max})
    return gate(not bad, f"I {i} LUFS (target {target:g} +/-{tol:g}), TP {tp} dBTP (max {tp_max:g})", bad)


# --- numeric helpers behind G2 ---------------------------------------------------------------------

def ncc(a, b):
    """Normalized correlation of two same-shape frames. A flat frame correlates with nothing, so it
    returns 0 rather than a divide-by-zero -- a held/blank frame must not read as a match."""
    x = a.astype(np.float64).ravel() - float(np.mean(a))
    y = b.astype(np.float64).ravel() - float(np.mean(b))
    dx, dy = math.sqrt(float(x @ x)), math.sqrt(float(y @ y))
    if dx == 0 or dy == 0: return 0.0
    return round(float(x @ y) / (dx * dy), 4)


def lag_and_strength(a, b, sr=16000, max_lag_s=0.5):
    """(seconds `b` lags `a`, how well they match: the normalized correlation at that lag, 0 to 1), by FFT cross-correlation. Direct
    correlation over a +/-0.5 s search on 2 s windows is ~1e8 multiplies per sample point; this is three transforms. (0.0, 0.0) when
    either side is silent or too short: nothing to measure is not a lag of zero."""
    n = min(len(a), len(b))
    if n < 64: return 0.0, 0.0
    x = np.asarray(a[:n], dtype=np.float64); y = np.asarray(b[:n], dtype=np.float64)
    x = x - x.mean(); y = y - y.mean()
    ex, ey = float(x @ x), float(y @ y)
    if ex == 0.0 or ey == 0.0: return 0.0, 0.0
    m = 1 << int(math.ceil(math.log2(2 * n)))
    c = np.fft.irfft(np.fft.rfft(y, m) * np.conj(np.fft.rfft(x, m)), m)
    lim = min(int(max_lag_s * sr), n - 1)
    cc = np.concatenate([c[-lim:], c[:lim + 1]])          # lags -lim .. +lim
    k = int(np.argmax(cc))
    return round((k - lim) / sr, 4), round(float(cc[k]) / math.sqrt(ex * ey), 4)


def lag_of(a, b, sr=16000, max_lag_s=0.5):
    """Seconds `b` lags `a`."""
    return lag_and_strength(a, b, sr, max_lag_s)[0]


# --- ffmpeg ----------------------------------------------------------------------------------------

def _run(cmd, service, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, **kw)
    except OSError as e:
        stop(f"{service} is down: {type(e).__name__}: {e}")


def _popen(cmd, service, **kw):
    try:
        return subprocess.Popen(cmd, **kw)
    except OSError as e:
        stop(f"{service} is down: {type(e).__name__}: {e}")


def probe(path):
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height,r_frame_rate", "-show_entries", "format=duration",
              "-of", "json", path], "ffprobe")
    try:
        d = json.loads(r.stdout or b"{}")
        s = (d.get("streams") or [{}])[0]
        num, _, den = (s.get("r_frame_rate") or "30/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else FPS
        return {"w": int(s["width"]), "h": int(s["height"]), "fps": fps,
                "duration": float((d.get("format") or {}).get("duration") or 0.0)}
    except (ValueError, KeyError, IndexError, TypeError):
        stop(f"ffprobe could not read {os.path.basename(path)}: "
                         f"{(r.stderr or b'').decode('utf-8', 'replace').strip()[:160] or 'no video stream'}")


def sweep_size(meta, width=SWEEP_W):
    """The sweep box is pinned here rather than left to scale's -2, so the reel and the render
    source are guaranteed to produce the same frame size and the diff is well defined."""
    return width, max(2, int(round(meta["h"] * width / meta["w"] / 2)) * 2)


def _gray_proc(path, w, h, step):
    vf = f"select='not(mod(n\\,{step}))',scale={w}:{h},format=gray" if step > 1 else f"scale={w}:{h},format=gray"
    return _popen(["ffmpeg", "-v", "error", "-an", "-i", path, "-vf", vf,
                   "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                  "ffmpeg", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def _read_frame(stream, size):
    buf = stream.read(size)
    return None if not buf or len(buf) < size else buf


def sweep(reel, src, track, fps, width=SWEEP_W, step=SWEEP_STEP, thresh=INK_DIFF, safe=SAFE, pieces=None):
    """ONE decode of each file, differenced frame by frame. Returns the per-frame ink record the
    G4 gate reads. Nothing is kept in memory but the counts."""
    a = probe(reel); w, h = sweep_size(a, width)
    pa, pb = _gray_proc(reel, w, h, step), _gray_proc(src, w, h, step)
    size = w * h
    frames, n = [], 0
    try:
        while True:
            fa, fb = _read_frame(pa.stdout, size), _read_frame(pb.stdout, size)
            if fa is None or fb is None: break
            d = np.abs(np.frombuffer(fa, np.uint8).astype(np.int16).reshape(h, w)
                       - np.frombuffer(fb, np.uint8).astype(np.int16).reshape(h, w))
            t = n / fps
            f = face_at(track, t)
            frames.append({"n": n, "t": round(t, 3), "crown": crown_y(f), **ink_counts(d, face_zone(track, t, pieces), crown_y(f), thresh, safe=safe)})
            n += step
    finally:
        for p in (pa, pb):
            try:
                p.stdout.close()
            except OSError:
                pass
            p.wait()
    return frames


def _one_gray(path, t, w, h):
    r = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
              "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"], "ffmpeg")
    if len(r.stdout or b"") < w * h: return None
    return np.frombuffer(r.stdout[:w * h], np.uint8).reshape(h, w)


def _one_rgb(path, t, w):
    r = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
              "-vf", f"scale={w}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], "ffmpeg")
    buf = r.stdout or b""
    h = len(buf) // (w * 3)
    if h < 2: return None
    return np.frombuffer(buf[:h * w * 3], np.uint8).reshape(h, w, 3).copy()


def sample_pieces(pieces, k=3, min_dur=None):
    """First, middle and last. `min_dur` restricts the choice to pieces long enough to hold a measurement window: a 2 s cross-correlation opened in
    a 0.6 s piece runs straight over the next splice, and the correlation then aligns on content the mezzanine plays earlier (a "drift" that was the
    cut doing exactly its job). When nothing is long enough, measure what there is rather than reporting nothing."""
    if not pieces: return []
    idx = list(range(len(pieces)))
    if min_dur is not None:
        long = [i for i in idx if pieces[i]["c1"] - pieces[i]["c0"] >= min_dur]
        if long: idx = long
    n = len(idx)
    return sorted({idx[0], idx[n // 2], idx[n - 1]})[:k]


def _offset(p, want=0.5):
    return min(want, max(0.0, (p["c1"] - p["c0"]) / 2))


def frame_match(cut, mez, pieces, width=SWEEP_W):
    """The picture half of the stall check: the cut's own frame against the mezzanine it was cut from. A conform that silently failed shows up here
    as about 0.4 while every duration check still reads clean."""
    if not (cut and mez and pieces): return []
    w, h = sweep_size(probe(cut), width)
    out = []
    for i in sample_pieces(pieces):
        p = pieces[i]; off = _offset(p)
        a = _one_gray(cut, p["out_in"] + off, w, h); b = _one_gray(mez, p["c0"] + off, w, h)
        if a is None or b is None: continue
        out.append({"piece": i, "t_cut": round(p["out_in"] + off, 3), "t_mez": round(p["c0"] + off, 3),
                    "corr": ncc(a, b)})
    return out


def pcm16(path, sr=16000):
    r = _run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
              "-f", "s16le", "-"], "ffmpeg")
    return np.frombuffer(r.stdout or b"", np.int16).astype(np.float32)


def audio_lag(reel, mez, pieces, sr=16000, win_s=2.0, min_win_s=0.5):
    """The shipped audio against the mezzanine's, at the start, the middle and the end. Both files
    are AAC, so the encoder's priming delay largely cancels and what is left is the drift.

    The window is kept strictly INSIDE one piece: it is comparing a stretch of the reel against the
    source stretch it was cut from, and the moment it reaches past a splice the reel is playing
    audio the mezzanine plays somewhere else entirely. That is a cut, not a desync.

    A window with no sound in it on either side is left out, never read as a lag of 0 ms: nothing to measure is not "in sync". Each record also carries `corr`, how well the two sounds match at that lag (0 to 1).
    It is recorded, not gated: the mastered voice against the source needs real takes before a bar can be set."""
    if not (reel and mez and pieces): return []
    a, b = pcm16(reel, sr), pcm16(mez, sr)
    if a.size < sr or b.size < sr: return []
    out = []
    for i in sample_pieces(pieces, min_dur=win_s + 0.5):
        p = pieces[i]; off = _offset(p)
        n = int(min(win_s, (p["c1"] - p["c0"]) - off) * sr)
        if n < min_win_s * sr: continue
        ia, ib = int((p["out_in"] + off) * sr), int((p["c0"] + off) * sr)
        x, y = a[ia:ia + n], b[ib:ib + n]
        if x.size < n // 2 or y.size < n // 2: continue
        lag, corr = lag_and_strength(y, x, sr)
        if corr == 0.0: continue
        out.append({"piece": i, "at": round(p["out_in"] + off, 3), "win_s": round(n / sr, 3), "lag_s": lag, "corr": corr})
    return out


def freezes_of(mez, min_s=FREEZE_MIN_S, width=SWEEP_W):
    """Started as a Popen by the caller: it is the single longest pass (a mezzanine is the whole
    source, not the reel), so it runs alongside the sweeps instead of after them. Downscaled
    first -- freezedetect compares frames, and it compares 240-px frames just as well."""
    return _popen(["ffmpeg", "-v", "info", "-nostats", "-an", "-i", mez, "-map", "0:v:0",
                   "-vf", f"scale={width}:-2,freezedetect=n=-60dB:d={min_s}", "-f", "null", "-"],
                  "ffmpeg", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def loudness_proc(reel):
    return _popen(["ffmpeg", "-hide_banner", "-nostats", "-i", reel, "-vn",
                   "-af", f"loudnorm=I={LUFS_TARGET:g}:TP={TP_MAX:g}:LRA=11:print_format=json",
                   "-f", "null", "-"], "ffmpeg", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def parse_loudnorm(text):
    i = text.rfind("{")
    if i < 0: return None, None
    try:
        d = json.loads(text[i:text.rfind("}") + 1])
        return round(float(d["input_i"]), 1), round(float(d["input_tp"]), 1)   # a silent file reads -inf: G7 fails on it, and the card writes it as null
    except (ValueError, KeyError):
        return None, None


# --- evidence ---------------------------------------------------------------------------------------

def strip_proc(reel, out, duration, cols=10, thumb=180):
    rows = max(1, int(math.ceil(max(1.0, duration) / cols)))
    return _popen(["ffmpeg", "-v", "error", "-y", "-an", "-i", reel, "-vf",
                   f"fps=1,scale={thumb}:-2,tile={cols}x{rows}:margin=4:padding=2:color=black",
                   "-frames:v", "1", out], "ffmpeg", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def write_png(img, path):
    p = _run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
              "-s", f"{img.shape[1]}x{img.shape[0]}", "-i", "-", "-frames:v", "1", path],
             "ffmpeg", input=img.tobytes())
    if p.returncode != 0:
        stop(f"ffmpeg could not write {os.path.basename(path)}: "
                         f"{(p.stderr or b'').decode('utf-8', 'replace').strip()[:160]}")


def _rect(img, x0, y0, x1, y1, color, t=2):
    h, w = img.shape[:2]
    x0, x1 = max(0, min(w - 1, x0)), max(0, min(w, x1)); y0, y1 = max(0, min(h - 1, y0)), max(0, min(h, y1))
    img[y0:min(h, y0 + t), x0:x1] = color; img[max(0, y1 - t):y1, x0:x1] = color
    img[y0:y1, x0:min(w, x0 + t)] = color; img[y0:y1, max(0, x1 - t):x1] = color


def sheet_times(frames, k=SHEET_N):
    """The inkiest frame of each of k equal slices: spread across the reel AND showing captions."""
    if not frames: return []
    out = []
    for b in np.array_split(np.arange(len(frames)), min(k, len(frames))):
        if len(b): out.append(frames[max(b, key=lambda i: frames[i]["ink"])]["t"])
    return sorted(set(out))


def contact_sheet(reel, src, times, track, out, width=SHEET_W, thresh=INK_DIFF, canvas=CANVAS):
    """Annotated: green safe box, blue face box, cyan crown line, magenta caption ink. The ink is
    the same difference the sweep measures, drawn where it was found, so a FAIL on G4 can be READ
    instead of re-derived."""
    tiles = []
    for t in times:
        a = _one_rgb(reel, t, width)
        b = _one_rgb(src, t, width) if src and os.path.abspath(src) != os.path.abspath(reel) else None
        if a is None: continue
        h, w = a.shape[:2]
        sx, sy = w / canvas[0], h / canvas[1]
        if b is not None and b.shape == a.shape:
            d = np.abs(a.astype(np.int16).mean(axis=2) - b.astype(np.int16).mean(axis=2))
            a[d >= thresh] = (255, 0, 255)
        _rect(a, int(SAFE["x0"] * sx), int(SAFE["y0"] * sy), int(SAFE["x1"] * sx), int(SAFE["y1"] * sy), (0, 255, 0))
        f = face_at(track, t)
        if f:
            _rect(a, int((f["cx"] - f["w"] / 2) * sx), int((f["cy"] - f["h"] / 2) * sy),
                  int((f["cx"] + f["w"] / 2) * sx), int((f["cy"] + f["h"] / 2) * sy), (0, 120, 255))
            cy = int(crown_y(f) * sy)
            if 0 <= cy < h: a[cy:min(h, cy + 2), :] = (0, 255, 255)
        tiles.append(a)
    if not tiles: return None
    cols = min(3, len(tiles)); rows = int(math.ceil(len(tiles) / cols))
    th, tw = tiles[0].shape[:2]
    sheet = np.zeros((rows * th, cols * tw, 3), np.uint8)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet[r * th:r * th + tile.shape[0], c * tw:c * tw + tile.shape[1]] = tile[:th, :tw]
    write_png(sheet, out)
    return out


# --- inputs ------------------------------------------------------------------------------------------

def _load(path, default=None):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0: return default
    try:
        return json.load(open(path))
    except (OSError, json.JSONDecodeError) as e:
        stop(f"could not read {os.path.basename(path)}: {type(e).__name__}: {e}")


def _first(*paths):
    for p in paths:
        if p and os.path.exists(p) and os.path.getsize(p) > 0: return p
    return None


def recorded_freezes(skew):
    """avskew measured this exact mezzanine at prep time; re-running freezedetect on a 4-minute
    source to learn the same thing costs ~5 s for nothing."""
    c = (skew or {}).get("conform")
    return c["freezes_over_min"] if isinstance(c, dict) and "freezes_over_min" in c else None


# --- COLOR: the color gate ---------------------------------------------------------------------------------------------------------

COLOR_WANT = {"pix_fmt": "yuv420p", "color_range": "tv", "color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709"}
PIXEL_TOL = 5.0      # levels (0-255) the per-channel median of a frame may differ from the reference
COLOR_FRAMES = (0.25, 0.5, 0.75)
COLOR_SCALE_W = 270


def color_tags(path):
    """the picture's pix_fmt, range, matrix, primaries and transfer ("unknown" where the file says nothing)"""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=" + ",".join(COLOR_WANT), "-of", "json", path], "ffprobe")
    try:
        s = (json.loads(r.stdout or b"{}").get("streams") or [{}])[0]
    except (ValueError, TypeError):
        s = {}
    if not s:
        stop(f"ffprobe could not read a video stream from {path}")
    return {k: (s.get(k) or "unknown") for k in COLOR_WANT}


def _duration(path):
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], "ffprobe")
    try:
        return float(r.stdout.decode().strip())
    except ValueError:
        stop(f"ffprobe could not read the duration of {path}")


def frame_medians(path, t):
    """per-channel median (R, G, B) of the frame at t seconds, decoded the way a player decodes it (the file's own tags)"""
    r = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={COLOR_SCALE_W}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], "ffmpeg")
    a = np.frombuffer(r.stdout, np.uint8)
    if a.size < 3 or a.size % 3:
        return None
    return np.median(a.reshape(-1, 3), axis=0)


def pixel_drift(video, ref):
    """worst per-channel median difference over three frames, or None when no frame could be compared"""
    d_video, d_ref = _duration(video), _duration(ref)
    worst = None
    for f in COLOR_FRAMES:
        t = min(d_video, d_ref) * f
        a, b = frame_medians(video, t), frame_medians(ref, t)
        if a is None or b is None:
            continue
        d = np.abs(a - b)
        worst = d if worst is None else np.maximum(worst, d)
    return None if worst is None else [float(x) for x in worst]


def color_check(video, ref=None):
    """{"pass", "numbers", "bad"}. Tags: pix_fmt yuv420p, range tv, matrix, primaries and transfer all bt709 (a platform's uploader rejects anything
    else, and a stream left untagged shifts every color against the BT.709 mezzanine the cut was made from). Pixels (with a reference, normally the cut
    the video was made from): the per-channel median of three decoded frames compared with the same frames of the reference. A stream relabeled
    bt709 over full-range BT.601 pictures has perfect tags and shifted colors; only this comparison sees it. The median ignores caption ink and a
    punch-in's crop."""
    got = color_tags(video)
    bad = [f"{k} is {v}, not {COLOR_WANT[k]}" for k, v in got.items() if v != COLOR_WANT[k]]
    numbers = "tags " + ", ".join(f"{k.replace('color_', '')} {v}" for k, v in got.items())
    if ref:
        drift = pixel_drift(video, ref)
        if drift is None:
            bad.append("no frame could be decoded to compare the picture's colors with the cut")
        else:
            numbers += f"; pixels vs the cut: median drift R{drift[0]:.1f} G{drift[1]:.1f} B{drift[2]:.1f} (limit {PIXEL_TOL:g})"
            if max(drift) > PIXEL_TOL:
                bad.append(f"the picture's colors differ from the cut by up to {max(drift):.0f} levels (limit {PIXEL_TOL:g}): a color conversion went wrong")
    return {"pass": not bad, "numbers": numbers, "bad": bad}


def g_color(video, ref=None):
    r = color_check(video, ref)
    return gate(r["pass"], r["numbers"], r["bad"])


# --- WORDS: the finished file still says what the cut says --------------------------------------------------------------------------

def _norm_words(text):
    return re.sub(r"[^a-z0-9 ]", "", str(text).lower()).split()


def g_words(heard, expected, dur_video=None, dur_cut=None):
    """`heard` is the finished file's own transcript, `expected` the cut's QC transcript: the same words in the same order (ratio above
    WORDS_RATIO_MIN), and the two durations within DURATION_DELTA_MAX_S. A master that clips a word, or a mix that buries one, fails here; every
    difference is listed. With either transcript missing the gate is a SKIP."""
    if not heard or not expected:
        return gate(True, "words not compared (no transcript of the finished file or of the cut)", skipped=True)
    a, b = _norm_words(expected), _norm_words(heard)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ratio = round(sm.ratio(), 4)
    bad = []
    if ratio <= WORDS_RATIO_MIN:
        bad.append({"why": "transcript", "ratio": ratio, "min": WORDS_RATIO_MIN})
    delta = None if dur_video is None or dur_cut is None else round(abs(dur_video - dur_cut), 3)
    if delta is not None and delta >= DURATION_DELTA_MAX_S:
        bad.append({"why": "duration", "delta_s": delta, "max": DURATION_DELTA_MAX_S})
    diffs = [{"why": "word", "cut": " ".join(a[i1:i2]), "file": " ".join(b[j1:j2])} for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]
    numbers = f"words match {ratio:.2f} (above {WORDS_RATIO_MIN:g})" + ("" if delta is None else f", duration delta {delta:.2f}s (under {DURATION_DELTA_MAX_S:g})")
    return gate(not bad, numbers, (bad + diffs)[:12])


# --- zones: no overlay text under a platform's interface ---------------------------------------------------------------------------

ZW, ZH = 1080, 1920
ZONES = {
    "organic": [((0, 0, 1080, 220), "the handle band at the top"),
                ((0, 1650, 1080, 1920), "the caption and audio band at the bottom"),
                ((980, 1100, 1080, 1920), "the like / comment / share column")],
    "ads": [((0, 0, 1080, 269), "the top 14%"),
            ((0, 1248, 1080, 1920), "the bottom 35%"),
            ((0, 0, 65, 1920), "the left 6%"),
            ((1015, 0, 1080, 1920), "the right 6%")],
}
# organic: the Reels interface on organic posts (a handle band on top, the caption and audio band at the bottom, the like / comment / share / audio
# column up the right edge). ads: the published 9:16 safe zone for Reels ads: keep text, logos and key elements out of the top 14%, the bottom 35%
# and 6% on each side.


def _zone_norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def text_boxes(paths):
    """{path: [(x, y, w, h, text)]} in 1080x1920 pixels: every line of text Apple Vision can read in each image (the engine/swift/textboxes helper)"""
    binary = swiftbin.ensure(os.path.join(HERE, "swift", "textboxes"))
    out = {p: [] for p in paths}
    for i in range(0, len(paths), 40):
        r = subprocess.run([binary] + paths[i:i + 40], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            p = line.split("\t")
            if len(p) >= 6 and p[0] in out:
                out[p[0]].append((*[int(v) for v in p[1:5]], p[5]))
    return out


def _zone_frames(video, every, tmp, tag, scale):
    sub = os.path.join(tmp, tag)
    os.makedirs(sub)
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps=1/{every},scale={int(ZW * scale)}:-2", os.path.join(sub, "f%05d.png")], capture_output=True, text=True)
    if r.returncode:
        stop(f"ffmpeg could not read frames from {video}: {' '.join(r.stderr.split())[-200:]}")
    return sorted(os.path.join(sub, f) for f in os.listdir(sub))


def zones_check(final, cut_, profile="organic", every=0.5, ours=None):
    """[(t, text, zone)] for every overlay line under a platform zone. Text that is also readable in the raw cut at that moment is footage, not an
    overlay, and is dropped. `ours` (a set of normalized strings the caller drew): only lines that match something drawn can fail; without it,
    anything not readable in the raw cut counts. A line has to sit in a zone on two samples in a row: an entrance or exit passing through does not count."""
    if profile not in ZONES:
        stop(f"zones: profile {profile!r} is not one of {', '.join(ZONES)}")
    if ours is not None and not ours:
        ours = None   # nothing drawn was found: judge every new line, never let an empty set pass everything
    tmp = tempfile.mkdtemp(prefix="zones-")
    try:
        s = 2 / 3   # 720 px wide reads the captions fine and halves the OCR time
        fa, fb = _zone_frames(final, every, tmp, "final", s), _zone_frames(cut_, every, tmp, "cut", s)
        bf, bc = text_boxes(fa), text_boxes(fb)
        hits = []
        for i, p in enumerate(fa):
            t = round(i * every, 2)
            src = [_zone_norm(b[4]) for b in bc.get(fb[i], [])] if i < len(fb) else []
            for x, y, w, h, text in bf[p]:
                n = _zone_norm(text)
                if not n or any(difflib.SequenceMatcher(None, n, c).ratio() >= 0.75 for c in src if c):
                    continue   # also in the raw cut: part of the footage, not an overlay
                if ours is not None and not any(difflib.SequenceMatcher(None, n, o).ratio() >= 0.75 for o in ours):
                    continue   # matches nothing that was drawn: text in the footage
                X, Y, X2, Y2 = x / s, y / s, (x + w) / s, (y + h) / s
                for (zx, zy, zx2, zy2), zone in ZONES[profile]:
                    if X < zx2 - 4 and X2 > zx + 4 and Y < zy2 - 4 and Y2 > zy + 4:
                        hits.append((i, t, text, zone))
                        break
        seen = {(i, _zone_norm(text)) for i, _, text, _ in hits}
        return [(t, text, zone) for i, t, text, zone in hits if (i - 1, _zone_norm(text)) in seen or (i + 1, _zone_norm(text)) in seen]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- running the gates on a take ---------------------------------------------------------------------------------------------------

def _json_safe(x):
    """x with every non-finite float (a silent file measures -inf LUFS) as None, so qc.json is strict JSON a browser or jq can read"""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return x


def run(T, N, faceless=False, video=None, render_from=None, reference=None):
    """Check take `N` in folder `T` (see the module doc for the files it reads). Writes T/qc.json and T/evidence/; returns the card."""
    T = os.path.abspath(T)
    root = os.path.dirname(T)
    plan = _load(os.path.join(T, "plan.json")) or {}
    faceless = bool(faceless or plan.get("speaker") is False)
    reel = _first(video, os.path.join(T, f"{N}-final.mp4"), os.path.join(T, f"{N}-cut-master.mp4"))
    if not reel:
        stop(f"no video to check: pass --video, or put {N}-cut-master.mp4 in {T}")
    edl_p = os.path.join(T, f"{N}-cut.edl.json")
    if not os.path.exists(edl_p):
        stop(f"no EDL: {edl_p}: the gates read the cut's own piece boundaries")
    words_p = os.path.join(T, "words.json")
    if not os.path.exists(words_p):
        stop(f"no source transcript: {words_p}")
    edl = _load(edl_p) or {}
    pieces = edl.get("pieces") or []
    fps = float(edl.get("fps") or FPS)
    src_words, stamps = cut.load_src_words(words_p)
    plan_raw = _load(os.path.join(T, "captions-plan.json"))
    cap_plan = plan_raw or {}                  # no captions plan: G5 is a SKIP, G4 a SKIP, nothing crashes
    events = None if plan_raw is None else (cap_plan.get("events") or [])
    flags = cap_plan.get("flags") or []
    cut_words = _load(os.path.join(T, "cut.words.json"))
    skew = _load(os.path.join(root, "wx", f"{N}.avskew.json"))
    cut_p = _first(os.path.join(T, f"{N}-cut.mp4"))
    mez = _first(os.path.join(root, "mez", f"{N}.mp4"))
    src = _first(render_from) if render_from else None
    ft_p = os.path.join(T, "cut.facetrack.json")
    track = None if faceless else _load(ft_p)
    if track is None and not faceless:
        track = face.build(reel, ft_p)         # only when the chain's face-on-the-master stage did not run
    ev = os.path.join(T, "evidence")
    os.makedirs(ev, exist_ok=True)
    strip, sheet = os.path.join(ev, "strip.png"), os.path.join(ev, "contact-sheet.png")
    meta = probe(reel)

    # The independent ffmpeg passes go first so they run WHILE the sweeps do.
    known = recorded_freezes(skew)
    fz = freezes_of(mez) if (mez and known is None) else None
    loud = loudness_proc(reel)
    stripper = strip_proc(reel, strip, meta["duration"])
    sweeping = bool(plan_raw is not None and src and os.path.abspath(src) != os.path.abspath(reel))
    frames = sweep(reel, src, track, fps, safe=([SAFE, cap_plan["safe"]] if cap_plan.get("safe") else SAFE), pieces=pieces) if sweeping else []
    matches = frame_match(cut_p, mez, pieces)
    lags = audio_lag(reel, mez, pieces)

    if known is not None:
        freezes = known
    elif fz is None:
        freezes = None
    else:
        import avskew
        freezes = avskew.parse_freezes((fz.communicate()[1] or b"").decode("utf-8", "replace"))
    i_lufs, tp = parse_loudnorm((loud.communicate()[1] or b"").decode("utf-8", "replace"))
    stripper.communicate()
    times = sheet_times(frames) if frames else [meta["duration"] * (i + 0.5) / SHEET_N for i in range(SHEET_N)]
    sheet = contact_sheet(reel, src, times, track, sheet)

    struck = cut.dropped_indices(src_words, edl.get("dropped"))
    cut_card = _load(os.path.join(T, f"{N}-cut.mp4.qc.json")) or {}
    heard = (cut_words or {}).get("transcript") or " ".join(w.get("text", "") for w in (cut_words or {}).get("words", []))
    gates = {"G1": g1_speech_only(pieces, [w for k, w in enumerate(src_words) if k not in struck], track, check_face=not faceless),
             "G2": g2_picture_sound(skew, freezes, matches, lags),
             "G3": g3_onsets(pieces, src_words, stamps, audio=(cut.Audio(mez) if mez else None),
                             waived=(_load(os.path.join(T, "joints.json")) or {}).get("waived"), dropped=edl.get("dropped")),
             "G4": g4_captions(frames, events, fps, whips=cap_plan.get("whips") or []) if sweeping or events is None else
             gate(True, "captions plan found but no render source to difference against (pass --render-source)", skipped=True),
             "G5": g5_events(events, cut_words, flags, fps),
             "G6": g6_visual(strip),
             "G7": g7_loudness(i_lufs, tp),
             "COLOR": g_color(reel, _first(reference, cut_p)),
             "WORDS": g_words(heard, cut_card.get("transcript"), meta["duration"], probe(cut_p)["duration"] if cut_p else None)}
    card = {"verdict": verdict_of(gates), "video": reel, "render_source": src, "mezzanine": mez, "pieces": len(pieces), "faceless": faceless, "gates": gates,
            "strip": strip if os.path.exists(strip) else None, "sheet": sheet}
    common.atomic_write_json(os.path.join(T, "qc.json"), _json_safe(card))   # -inf (a silent file) is not JSON: it goes on the card as null
    return card


def _opt(a, name, default=None):
    if name in a:
        i = a.index(name)
        v = a[i + 1]
        del a[i:i + 2]
        return v
    return default


def _main():
    a = sys.argv[1:]
    if a[0] == "color":
        r = color_check(a[1], a[2] if len(a) > 2 else None)
        print(("COLOR PASS " if r["pass"] else "COLOR FAIL ") + r["numbers"])
        for b in r["bad"]:
            print("  " + b)
        sys.exit(0 if r["pass"] else 1)
    if a[0] == "zones":
        prof, every = _opt(a, "--profile", "organic"), float(_opt(a, "--every", 0.5))
        hits = zones_check(a[1], a[2], prof, every)
        for t, text, zone in hits:
            print(f"ZONE {t:6.1f}s \"{text}\" sits under {zone}")
        print(f"zones ({prof}): {len(hits)} hit(s)")
        sys.exit(1 if hits else 0)
    faceless = "--faceless" in a
    if faceless:
        a.remove("--faceless")
    video, render_from, reference = _opt(a, "--video"), _opt(a, "--render-source"), _opt(a, "--reference")
    T, N = os.path.abspath(a[0]), a[1]
    card = run(T, N, faceless, video, render_from, reference)
    for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "COLOR", "WORDS"):
        print(line(k, card["gates"][k]))
    failed = [k for k in MACHINE_GATES if card["gates"][k]["pass"] is False]
    print(f"{card['verdict']}" + (f": {', '.join(failed)}" if failed else "") + f": strip {card['strip']}" + (f", sheet {card['sheet']}" if card["sheet"] else ""))
    for k in failed:
        for d in card["gates"][k]["detail"]:
            print(f"  {k} {d}")
    sys.exit(0 if card["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    support.cli(_main, USAGE)
