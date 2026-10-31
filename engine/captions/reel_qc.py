#!/usr/bin/env python3
"""reel_qc.py <take-dir> <name> [--strict-loudness] [--pace-gate off|report|enforce]
The never-see gates on a finished caption reel -- so nobody hand-verifies sync, safe-box, face
coverage or color tags on every take, so a person never has to check them by hand.

The caption chain takes an ALREADY-CUT, already-mixed video and burns in captions. There is
no EDL, no pieces, no splice joints -- output time == source time, frame for frame, so every gate
samples the one continuous reel.

Gates (a check with no input to measure is reported SKIP, never a silent pass):
 G-FACE  whole-reel face presence, over the SPOKEN SPAN only (first word start -> last word end);
         audit-only (never fails) when GATE.md says `subject: none` (a voiceover or screen recording has no face):
         fraction of that span's sampled seconds with a face >= 0.90. Seconds outside the spoken
         span are counted and shown but never fail the gate (report-only) -- a captioned product
         demo over a static product shot, or a cold open before anyone starts talking, is not "the
         subject went missing".
 G2      picture == sound: 0 freezes > 0.5s and frame-match >= 0.9, both against the mezzanine, sampled at three
         points spread across the reel (identity time, so reel[t] should equal mez[t] modulo caption ink), moved
         off any layout event, punch ramp or whip, where the picture is not comparable frame for frame; and the
         reel's own audio against the take's audio.wav (the mezzanine carries no audio), cross-correlated over the
         first 10 s and, on a reel of 20 s or more, the last 10 s as well: lag <= 60ms and not growing by more
         than 20ms. A window the audio does not match at all fails too, and a silent window is reported, not passed.
 G4      captions in the box: ink sweep -- nothing outside the safe box (plan-declared zone unioned
         in when the plan carries one), nothing on the face, nothing above the crown unless a hook/
         behind/lockup/above-crown word is live there, whip-cut frames skipped.
 G5      caption events: nothing under 3 frames, every non-filler word on screen when it is spoken (in a caption
         event that is up at that moment, or under a layout event; hyphen-joined tokens like "go-to" count for
         both halves).
 G-WORDS your do_not_say list (look.json): FAIL when a caption in the plan shows one of those words or
         phrases, unless GATE.md accepted it with `allow: "word"`. Skipped when you have no list.
 G6      the visual read -- HUMAN. Writes the strip; a person (or an agent) reads it.
 G7      loudness -- AUDIT-ONLY unless your look sets master_audio: loudnorm. The clip arrives
         already mixed and the engine leaves the mix alone, so the measured numbers go in the notes and never
         block delivery; but when they are off the target (outside -16 +/-1 LUFS, or true peak above -1.5
         dBTP) the gate carries one plain `check` sentence for the editor, which deliver.sh puts in NOTES.md
         and run_captions.sh prints as a CHECK line. Pass --strict-loudness when your look sets
         master_audio: loudnorm (render.sh then normalises to -16 LUFS, true peak -2 dBTP) and it becomes a
         hard gate at -16 +/-1 LUFS, true peak <= -1.5 dBTP.
 G8      AUDIO-ORIGIN -- the reel's own audio stream must start at (within 5ms of) t=0, and must be
         within one frame (1/30s, ~33ms) of audio.wav by cross-correlation. Named and reported
         separately from G2 so a failure says plainly "the audio wasn't remuxed in" rather than a
         generic sync failure -- the direct defense against Remotion's own documented 42.7ms audio
         mux delay (never ship Remotion's own audio; always remux the source audio.wav stream in).
 G9      COLOR -- pix_fmt yuv420p, color_range tv, color_space, color_primaries and color_transfer all
         bt709 (scripts/colortag_check.py). Guards the trap that shipped once: a chain of ffmpeg passes
         produced yuvj420p + bt470bg and Meta's uploader silently rejected the file. The render itself
         is asked for bt709 (render.sh), so a pass means the pixels are bt709 too, not just the labels.
 G-ACCENT your accent colour comes through the conform unchanged: where the source shows the accent as flat colour, the mezzanine shows the same colour at the same
         pixels, within 2 levels per channel (each file decoded the way a correct player does, by its own matrix and range). A drift is a FAIL; an accent that is
         not on screen is a visible SKIP, and so is a take whose source was HDR (the conversion moves colours on purpose). It reads pictures, not a report.
 G10     PACE (the README, "Pacing"), set by look.json's pace_gate (or --pace-gate): off skips it, report measures it and never fails, enforce fails
         the take. Median/p90/max gap between visual
         changes on the RENDERED reel, over the spoken span, measured with cadence.py's own spike
         detector (the same one that finds source cuts): median <= 1.8s, p90 <= 3.5s, max <= 5.0s.
         Report-only for takes under 8s (too short a sample to be meaningful).
Exit 0 PASS, 1 FAIL (any of G-FACE/G2/G4/G5/G-WORDS/G-ACCENT/G8/G9, G10 under pace_gate enforce, plus G7 only under --strict-loudness), 2 STOP
(a required input -- the reel or words.json -- is missing).

Punches (the README, "Pacing"): G2's frame-match compares the reel against the
mezzanine transformed by the plan's own punch scale/origin at that frame (a punched frame zooms the
picture, so comparing against the RAW mezzanine would read every punch as drift) -- see
_punch_at()/_one_gray_punched() below. G4 additionally asserts a punch's (cx, cy) origin sits
inside the face rect at that time -- the planner is supposed to always centre a punch on the face,
so the rect is unaffected by the zoom; a punch that drifted off the face is a WARNING in G4's own
detail, never a fail (the ink sweep itself is what fails G4) -- see punch_origin_warnings() below.

WHY IT IS FAST. The sweeps are the whole cost, and they are ONE decode each: the reel and the
mezzanine are opened once, piped as a 240-px-wide grayscale rawvideo stream, and differenced frame
against frame in numpy. The independent ffmpeg passes -- freezedetect, loudnorm, the strip -- are
started first and collected at the end, so they overlap the sweeps instead of queueing behind them.

Two measurement floors worth knowing before reading a number here. The ink threshold is 40/255 on
the DOWNSCALED difference: a 240-px sweep averages several source pixels per sweep pixel, which
puts codec noise far below it and still lights up on caption text, but it also means the "<= 3
stray pixels" allowance is 3 pixels at sweep scale, not at full resolution. And every distance
number here is measured against a file the render was made from -- the picture's against the
mezzanine, G2's and G8's audio lag against audio.wav (the mezzanine has no audio) -- so there is
no re-cut master to drift against."""
import json, math, os, re, subprocess, sys
import numpy as np
import cadence, colortag_check, facetrack, roomcheck
from keys import cli, EngineStop

CANVAS = (1080, 1920)          # the composition the safe box and the facetrack boxes are stated in
SAFE = roomcheck.SAFE          # single-sourced: x 65-940, y 270-1420
CROWN_H = roomcheck.CROWN_H    # 0.30 -- the crown sits this far above Vision's face box (D2)
FPS = 30
SWEEP_W = 240                  # sweep width; the whole speed budget rides on this staying small
SWEEP_STEP = 2                 # every 2nd frame
INK_DIFF = 40                  # 0-255 on the downscaled difference: above codec noise, below text
PUNCH_INK_DIFF = 110           # the same bar while a punch-in is active (resampling noise reaches ~90; glyph cores >110)
STRAY_PX = 3                   # stray pixels tolerated outside the box / on the face, per frame
FACE_MIN = 0.90                # G-FACE: fraction of the spoken span that must show a face
FRAME_MATCH_MIN = 0.9          # a stale/desynced picture reads ~0.4
LAG_MAX_S = 0.060
LAG_GROWTH_S = 0.020           # drift that accumulates is a different failure from a fixed offset
LAG_WINDOW_S = 10.0            # G2 measures the audio lag over this much audio at the start of the reel (and again at the end of a longer one)
LAG_MATCH_MIN = 0.3            # normalised correlation peak below this: the two audios are not the same sound
FREEZE_MIN_S = 0.5
MIN_EVENT_FRAMES = 3           # under this is a flash, not a caption
LUFS_TARGET, LUFS_TOL, TP_MAX = -16.0, 1.0, -1.5
AUDIO_ORIGIN_START_MAX = 0.005     # G8: the reel's own audio stream must start within 5ms of t=0
AUDIO_ORIGIN_LAG_MAX = 1.0 / FPS   # G8: within one frame (~33ms) of audio.wav
ABOVE_CROWN_MODES = ("behind", "front")   # the only layers that live above the head
# the README, "Devices": page/frame/igpost cover the face (or replace the whole
# frame) ON PURPOSE, so their span is exempt from G4's ink sweep entirely (like a whip-cut frame,
# not merely "allowed above the crown"). cta is NOT exempt -- its rows sit at chest, below the
# face, over the still-running video, so the normal ink checks stay meaningful there.
# cta joined the exempt set because its accent wash tints every pixel below mid-frame, which the
# diff-vs-mezzanine sweep reads as ink everywhere; the rows themselves are placed off the face by
# devices.py (_cta_y: below the chin, else above the crown).
DEVICE_G4_EXEMPT_MODES = ("page", "frame", "igpost", "cta")
DEVICE_LAYOUT_MODES = ("cta", "page", "frame", "igpost")
MACHINE_GATES = ("G-FACE", "G2", "G4", "G5", "G-WORDS", "G-ACCENT", "G7", "G8", "G9", "G10")   # G6 is the human read; it has no verdict
SHEET_N = 6
SHEET_W = 360
# G10 pace (the README, "Pacing") -- same thresholds cadence.py's own planning
# pass targets, just measured empirically on the rendered pixels instead of planned off the plan.
PACE_MEDIAN_MAX = 1.8
PACE_P90_MAX = 3.5
PACE_MAX_MAX = 5.0
PACE_REPORT_ONLY_UNDER_S = 8.0   # too short a spoken span to read median/p90 as meaningful
PACE_MODES = ("off", "report", "enforce")   # the look's pace_gate: off skips G10, report measures it and never fails, enforce fails a take over the limits
PUNCH_EASE_OUT_FRAMES = 6        # "eases back to 1 over 6 frames"


# --- gate records ---------------------------------------------------------------------------------

def gate(ok, numbers, detail=(), skipped=False, **extra):
    d = {"pass": ok, "numbers": numbers, "detail": list(detail), "skipped": skipped}
    d.update(extra)
    return d


def line(name, g):
    """One line per gate."""
    if g.get("human"): status = "HUMAN"
    elif g.get("skipped"): status = "SKIP"
    elif g.get("report_only") and g["pass"] is not False: status = "AUDIT"
    else: status = "PASS" if g["pass"] else "FAIL"
    return f"{name} {status} {g['numbers']}"


def verdict_of(gates):
    return "FAIL" if any((gates.get(k) or {}).get("pass") is False for k in MACHINE_GATES) else "PASS"


def plain(gate_name, d):
    """One plain sentence a non-engineer can act on, for a single failed-gate detail dict. Every
    FAIL line goes through this rather than a raw dict dump -- a social-media manager reads
    reel-qc.json, not a stack of {"why": ...} records."""
    why = d.get("why")
    if gate_name == "G-FACE":
        return (f"the subject isn't on camera for enough of the video -- a face was found in only "
                f"{round(d.get('fraction', 0) * 100)}% of the spoken part (needs "
                f"{round(d.get('threshold', FACE_MIN) * 100)}%).")
    if gate_name == "G-WORDS":
        return f"a caption shows \"{d.get('word')}\", which is on your do-not-say list (near {d.get('t')}s). Change it with fix: in GATE.md, or accept it with allow: \"{d.get('word')}\"."
    if gate_name == "G2":
        if why == "freeze": return f"the picture freezes for {d.get('s')}s at {d.get('at')}s into the mezzanine."
        if why == "frame_match": return f"the reel's picture drifted from the source near {d.get('t')}s."
        if why == "lag": return f"the audio is out of sync by about {abs(d.get('lag_s', 0)) * 1000:.0f}ms near {d.get('at')}s."
        if why == "lag_growth": return f"the audio sync drifts by about {abs(d.get('s', 0)) * 1000:.0f}ms across the video."
        if why == "no_match": return f"the reel's audio does not match the take's own audio near {d.get('at')}s, so its sync cannot be trusted -- it needs a fresh remux."
    if gate_name == "G4":
        if why == "outside": return f"captions spill outside the safe zone at {d.get('t')}s."
        if why == "face": return f"captions cover the face at {d.get('t')}s."
        if why == "crown": return f"captions sit above the head at {d.get('t')}s, where nothing should be."
    if gate_name == "G5":
        if why == "short": return f"the caption for \"{d.get('text')}\" flashes by too fast to read."
        if why == "uncovered": return f"the word \"{d.get('text')}\" never gets a caption."
    if gate_name == "G7":
        if why == "integrated": return f"the audio loudness is {d.get('lufs')} LUFS, outside the {d.get('target')} +/-{d.get('tol')} LUFS target."
        if why == "true_peak": return f"the audio peaks too hot at {d.get('dbtp')} dBTP."
    if gate_name == "G8":
        if why == "no_audio_stream": return "the reel has no audio track -- the source audio was never remuxed in."
        if why == "start_time": return "the reel's audio doesn't start at the very beginning of the file -- it needs a fresh remux."
        if why == "lag": return f"the reel's audio is out of sync with the source by about {abs(d.get('s', 0)) * 1000:.0f}ms -- it needs a fresh remux."
    if gate_name == "G-ACCENT":
        return (f"your accent colour {d.get('colour')} came back different from the source: pixels that were {tuple(d.get('source'))} are now {tuple(d.get('mezzanine'))}, "
                f"{d.get('shift')} levels off (limit {ACCENT_DRIFT_MAX:g}). The conform moved the colour; look at the contact sheet and re-export the source as plain Rec.709.")
    if gate_name == "G9":
        return d.get("plain") or "the color tags are wrong -- re-export the source."
    if gate_name == "G10":
        if why == "median": return f"the captions don't change often enough -- median gap {d.get('median')}s (needs <= {d.get('max')}s)."
        if why == "p90": return f"too many long quiet stretches -- p90 gap {d.get('p90')}s (needs <= {d.get('max')}s)."
        if why == "max": return f"a {d.get('gap')}s stretch near {d.get('at')}s has nothing changing on screen."
    return str(d)


# --- geometry helpers -----------------------------------------------------------------------------

def crown_y(face):
    """Top of the head, not the top of Vision's face box (which starts at the BROW): a word placed
    in the scalp gap landed ON him (D2). Same formula the caption planner and roomcheck use."""
    return None if not face else face["cy"] - face["h"] * (0.5 + CROWN_H)


def face_at(track, t):
    """The sampled box nearest t, or None when this take has no face at all."""
    if not track or not (track.get("samples") or {}): return None
    f = facetrack.at(track, t)
    return None if not f or f.get("fallback") else f


def face_fraction(track, t0, t1):
    """(fraction, sampled) over the facetrack seconds whose sampled frame (s+0.5) lands in [t0, t1].
    A span shorter than the 1 Hz sampling grid can land on no second at all -- that is unmeasured,
    not an empty frame, so it returns (None, 0) and the gate says so."""
    if not track: return None, 0
    samples = track.get("samples") or {}
    secs = [s for s in range(max(0, int(t0) - 1), int(t1) + 2) if t0 <= s + 0.5 <= t1]
    if track.get("extracted") is not None:                    # a second the video stream never had a frame for was not missing a face (a second the video never had a frame for)
        had = set(track["extracted"]); secs = [s for s in secs if s in had]
    if not secs: return None, 0
    have = sum(1 for s in secs if str(s) in samples)
    return have / len(secs), len(secs)


# --- G-FACE whole-reel face presence, over the spoken span --------------------------------------

def spoken_span(words, duration=None):
    """(first word start, last word end) of the transcript, the last end held to the reel's own length: a clip cut off mid-word ends with a word whose
    transcribed end runs past the last frame (6.06 s of picture, a last word "ending" at 7.36 s), and the seconds that were never there have no face."""
    t0, t1 = words[0]["start"], words[-1]["end"]
    return (t0, min(t1, duration) if duration else t1)


def g_face(words, track, threshold=FACE_MIN, audit_only=False, duration=None):
    """Replaces the old per-piece G1. There are no pieces any more, so the only thing left to gate
    is whether the subject is actually on camera while he's actually talking. Seconds before the
    first word or after the last -- an intro card, a product shot, dead air at the tail -- are
    counted and shown but never fail the gate: report-only outside the spoken span."""
    if not words:
        return gate(True, "no words (nothing spoken) -- face check has nothing to measure", skipped=True)
    if track is None:
        return gate(True, "face n/a (no facetrack)", skipped=True)
    t0, t1 = spoken_span(words, duration)
    frac, n = face_fraction(track, t0, t1)
    total = int(track.get("seconds") or 0)
    samples = track.get("samples") or {}
    outside = [s for s in range(total) if not (t0 <= s + 0.5 <= t1)]
    missing_outside = sum(1 for s in outside if str(s) not in samples)
    outside_txt = (f"; outside the spoken span: {missing_outside}/{len(outside)} second(s) with no "
                   f"face (report-only)" if outside else "")
    if frac is None:
        return gate(True, f"spoken span {t0:.2f}-{t1:.2f}s is too short to sample a face" + outside_txt,
                    skipped=True)
    bad = [] if frac >= threshold else [{"why": "face", "fraction": round(frac, 3), "seconds": n,
                                          "threshold": threshold}]
    numbers = (f"face {round(frac * 100)}% over the spoken span {t0:.2f}-{t1:.2f}s "
               f"({n} second(s) sampled, threshold {round(threshold * 100)}%)") + outside_txt
    if audit_only:
        return gate(True, numbers + " (audit only: GATE.md says subject: none)", report_only=True)
    return gate(not bad, numbers, bad)


# --- G-WORDS your do-not-say list ------------------------------------------------------------

def g_words(plan, do_not_say, allow=()):
    """FAIL when a caption in the plan shows a do_not_say word or phrase the operator did not allow."""
    if not do_not_say:
        return gate(True, "no do-not-say list", skipped=True)
    if plan is None:
        return gate(True, "no plan to read the captions from", skipped=True)
    import capgate
    hits = capgate.banned_hits(do_not_say, plan, allow)
    bad = [{"why": "banned", "word": w, "t": round(t, 2)} for w, t in hits]
    return gate(not bad, f"{len(do_not_say)} do-not-say word(s) checked against every caption, {len(bad)} shown"
                + (f", {len(allow)} allowed" if allow else ""), bad)


# --- G2 picture == sound --------------------------------------------------------------------------

def g2_picture_sound(freezes, matches, lags, match_min=FRAME_MATCH_MIN, lag_max=LAG_MAX_S, growth_max=LAG_GROWTH_S):
    """Picture and sound, checked three independent ways: held frames in the mezzanine itself, the
    reel's own picture against the mezzanine's at the same timestamps (a captions-only render never
    moves picture in time -- if they don't match at time t, something upstream is broken), and the
    shipped audio against the take's own audio.wav (the mezzanine has no audio track). The picture
    checks sample three points spread across the reel rather than every frame -- the sweep already
    reads every frame for ink; the audio lag (audio_lag) is measured over the first 10 s and, on a
    longer reel, the last 10 s, so a fixed offset and a drift are both seen."""
    skipped = freezes is None and not matches and not lags
    bad = []
    if freezes is None: freeze_txt = "freezes n/a (no mezzanine)"
    else:
        long = [f for f in freezes if f.get("duration", 0) > FREEZE_MIN_S]
        # An already-cut clip may HOLD a frame on purpose (a fade-to-black before a logo sting held 0.77 s).
        # Short holds are reported, not failed; only a hold of 2 s or more -- a stalled capture,
        # never an editorial choice -- fails the gate. Capture stalls are also caught earlier by intake's avskew check.
        broken = [f for f in long if f.get("duration", 0) >= 2.0]
        freeze_txt = f"freezes {len(long)} ({len(broken)} over 2s)"
        for f in broken[:5]:
            bad.append({"why": "freeze", "at": round(f.get("start", 0.0), 3), "s": round(f["duration"], 3)})
    if matches:
        worst = min(m["corr"] for m in matches)
        match_txt = f"frame-match min {worst:.2f} (>= {match_min})"
        for m in matches:
            if m["corr"] < match_min:
                bad.append({"why": "frame_match", "t": m["t"], "corr": m["corr"]})
    else:
        match_txt = "frame-match n/a"
    if lags:
        worst = max(abs(l["lag_s"]) for l in lags)
        growth = abs(lags[-1]["lag_s"]) - abs(lags[0]["lag_s"])
        lag_txt = f"audio lag vs audio.wav max {worst * 1000:.0f}ms (<= {lag_max * 1000:.0f}), growth {growth * 1000:+.0f}ms"
        for l in lags:
            if l.get("corr", 1.0) < LAG_MATCH_MIN:
                bad.append({"why": "no_match", "at": l["at"], "corr": l["corr"]})
            elif abs(l["lag_s"]) > lag_max:
                bad.append({"why": "lag", "at": l["at"], "lag_s": l["lag_s"]})
        if growth > growth_max:
            bad.append({"why": "lag_growth", "s": round(growth, 4), "max": growth_max})
    else:
        lag_txt = "audio lag n/a (no audio.wav to compare with, or no sound to measure)"
    return gate(not bad, f"{freeze_txt}, {match_txt}, {lag_txt}", bad[:12], skipped=skipped)


# --- G4 captions in the box and off the head ------------------------------------------------------

def ink_counts(diff, face, crown, thresh=INK_DIFF, safe=SAFE, canvas=CANVAS):
    """Caption ink in one swept frame: |reel - mezzanine| over the threshold, counted against the
    three places it is not allowed to be. `diff` is at sweep scale; `face`/`crown` are in canvas
    coordinates, which is what facetrack and the planner both speak."""
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


def above_crown_allowed(events, t, fps=FPS):
    """The behind and front cluster layers live above the head by design: ink up there is allowed while one is live."""
    pad = 1.0 / fps
    for e in (events or []):
        if e["start"] - pad <= t < e["end"] + pad and e.get("mode") in ABOVE_CROWN_MODES: return True
    return False


def punch_origin_warnings(punches, track, fps=FPS):
    """G4 sub-check (the README, "Pacing"): the planner is supposed to always
    set a punch's (cx, cy) origin to the face centre at that time, so a zoom about that point
    leaves the face rect's own position unaffected. This asserts that invariant on the finished
    plan instead of assuming it held -- a punch whose origin drifted off the face rect is a
    WARNING here (report-only), never a G4 fail; the ink sweep is what actually fails G4."""
    warnings = []
    for p in punches or []:
        f = face_at(track, p.get("at", 0) / fps) if track else None
        if not f:
            continue
        cx, cy = p.get("cx"), p.get("cy")
        if cx is None or cy is None:
            continue
        x0, x1 = f["cx"] - f["w"] / 2, f["cx"] + f["w"] / 2
        y0, y1 = f["cy"] - f["h"] / 2, f["cy"] + f["h"] / 2
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            warnings.append({"why": "punch_origin", "at": round(p.get("at", 0) / fps, 2), "cx": cx, "cy": cy,
                              "face": {"x0": round(x0), "x1": round(x1), "y0": round(y0), "y1": round(y1)}})
    return warnings


def g4_captions(frames, events, fps=FPS, stray=STRAY_PX, whips=(), punch_warnings=(), punches=()):
    """A caption leaking past the safe box, a slam rendered on the crown, and text across his mouth
    are all the same measurement on the shipped file, which is why they are one gate. `whips`
    (plan["whips"]): a whip cut blurs and pushes the WHOLE frame for a few frames on purpose; the
    diff against the source reads that as ink everywhere. Those frames are not caption ink and are
    skipped -- the count of skipped frames rides in the numbers so it is never silent. `punch_
    warnings` (punch_origin_warnings() above): report-only, never affects `pass` -- see its own
    docstring."""
    if not frames:
        d = gate(True, "no sweep (no mezzanine, or the reel decoded no frames)"
                 + (f", {len(punch_warnings)} punch origin warning(s) (report-only)" if punch_warnings else ""),
                 skipped=True)
        if punch_warnings:
            d["punch_warnings"] = list(punch_warnings)
        return d
    whip_skip = set()
    for w in whips or []:
        n = int(w.get("frames", 4)); whip_skip.update(range(int(w["at"]) - n, int(w["at"]) + n + 1))
    # the README, "Devices": page/frame/igpost cover the face (or the whole
    # frame) on purpose -- exempt their entire span from the ink sweep, the same way a whip-cut
    # frame is skipped above (but reported separately below -- it is not a whip). cta is
    # deliberately NOT in DEVICE_G4_EXEMPT_MODES: its rows sit at chest, below the face, over the
    # still-running video, so it keeps the normal G4 checks.
    device_skip = set()
    for e in events or []:
        if e.get("mode") in DEVICE_G4_EXEMPT_MODES:
            device_skip.update(range(int(round(e["start"] * fps)), int(round(e["end"] * fps)) + 1))
    # a punch's snap-in (`frames`, default 3) and ease-back (PUNCH_EASE_OUT_FRAMES) are eased curves the
    # sweep's linear stand-in cannot reproduce to the pixel; the HELD frames are compared exactly
    # (sweep() zooms the mezzanine like the render did). Ramp frames are skipped like whip frames and
    # counted in the numbers.
    ramp_skip = set()
    for p in punches or []:
        at = int(p.get("at", 0)); fi = int(p.get("frames", 3)); hold = int(p.get("hold", 24))
        ramp_skip.update(range(at - 1, at + fi + 2))
        ramp_skip.update(range(at + fi + hold - 1, at + fi + hold + PUNCH_EASE_OUT_FRAMES + 2))
    skip = whip_skip | device_skip | ramp_skip
    bad = []; skipped_whip = 0; skipped_device = 0; skipped_ramp = 0
    for f in frames:
        if f["n"] in skip:
            if f["n"] in device_skip: skipped_device += 1
            elif f["n"] in ramp_skip: skipped_ramp += 1
            else: skipped_whip += 1
            continue
        if f["outside"] > stray:
            bad.append({"why": "outside", "t": round(f["t"], 2), "n": f["n"], "px": f["outside"]})
        if f["on_face"] > stray:
            bad.append({"why": "face", "t": round(f["t"], 2), "n": f["n"], "px": f["on_face"]})
        if f["above_crown"] > stray and not above_crown_allowed(events, f["t"], fps):
            bad.append({"why": "crown", "t": round(f["t"], 2), "n": f["n"], "px": f["above_crown"]})
    n = {k: sum(1 for b in bad if b["why"] == k) for k in ("outside", "face", "crown")}
    numbers = (f"{len(frames)} frames swept, outside {n['outside']}, on face {n['face']}, "
               f"above crown {n['crown']}, max ink {max(f['ink'] for f in frames)} px"
               + (f", {skipped_whip} whip frames skipped" if skipped_whip else "")
               + (f", {skipped_device} device frames skipped" if skipped_device else "")
               + (f", {skipped_ramp} punch-ramp frames skipped" if skipped_ramp else "")
               + (f", {len(punch_warnings)} punch origin warning(s) (report-only)" if punch_warnings else ""))
    d = gate(not bad, numbers, bad[:12], frames=len(frames))
    if punch_warnings:
        d["punch_warnings"] = list(punch_warnings)
    return d


# --- G5 caption events ----------------------------------------------------------------------------

def _tok(t):
    return "".join(c for c in str(t).lower() if c.isalnum() or c == "'")


def covered(events, flags, w):
    """Is THIS spoken word on screen when it is spoken, or named in a flag? Matched by (text, start), never
    by text alone -- the same word spoken twice would let the second one's event mask the first
    one's drop. A merged event or a merged lockup row carries several words under one timestamp, so
    a word also counts when it is one of that event's tokens spoken while the event is up. And the event
    holding the word has to be up at the moment it is spoken: a row left in a cluster that was cut off
    before the word came is no caption at all (a page that ended mid-phrase left words like that, and
    only a word inside a multi-word row was ever caught)."""
    key = (_tok(w["text"]), round(w["start"], 3))
    pad = 1.0 / FPS
    for e in events or []:
        if e.get("mode") in ("behind", "front") and e["start"] - pad <= key[1] < e["end"] + pad:
            for x in e.get("words") or []:
                toks = [_tok(t) for t in re.split(r"[\s-]+", str(x.get("text", "")))]   # "go-to" covers go + to
                if key[0] in toks and (round(x.get("at", -99), 3) == key[1]
                                       or (len(toks) > 1 and x.get("at", 1e9) - 1e-6 <= key[1])):
                    return True
        # a layout event (cta/page/frame/igpost, the README, "Devices") covers
        # the whole frame for its span BY DESIGN -- devices.py's own clamp already dropped or
        # shortened any front/behind cluster it overlaps (a cta REPLACES the cluster it covers), so
        # a spoken word landing inside it is accounted for here, not left to read as "uncovered".
        if e.get("mode") in DEVICE_LAYOUT_MODES and e["start"] - 1e-6 <= key[1] < e["end"] + 1e-6:
            return True
    return any(key[0] in str(f).lower() for f in flags or [])


def _event_text(e):
    """What a caption event says: its own text, else its rows' words (a cluster has no text of its own)."""
    if e.get("text"): return str(e["text"])
    return " ".join(str(w.get("text", "")) for w in e.get("words") or []) or str(e.get("mode"))


def g5_events(events, words_doc, flags, fps=FPS):
    if events is None:
        return gate(True, "no plan.json", skipped=True)
    bad = []
    ordered = sorted(events, key=lambda e: e["start"])
    short_min = MIN_EVENT_FRAMES / fps - 1e-6
    for e in ordered:
        if e["end"] - e["start"] < short_min:
            bad.append({"why": "short", "text": _event_text(e), "t": round(e["start"], 3),
                        "frames": round((e["end"] - e["start"]) * fps, 2)})
    words = None if not words_doc else [w for w in words_doc.get("words", []) if not w.get("filler")]
    if words:
        for w in words:
            if not covered(ordered, flags, w):
                bad.append({"why": "uncovered", "text": w["text"], "t": round(w["start"], 3)})
    n = {k: sum(1 for b in bad if b["why"] == k) for k in ("short", "uncovered")}
    numbers = (f"{len(ordered)} events, under {MIN_EVENT_FRAMES} frames {n['short']}, "
               + (f"uncovered words {n['uncovered']}" if words else "no words.json"))
    return gate(not bad, numbers, bad[:12])


# --- G6 / G7 --------------------------------------------------------------------------------------

def g6_visual(strip):
    """Not automatable and not pretended to be. Wrong take, repeated line, dead footage, stale
    picture, does it open on the hook -- is a read, so this gate produces the thing to read and
    records that a person still owes it."""
    return gate(None, f"read the strip: {strip}", human=True)


def loudness_check(i, tp, target=LUFS_TARGET, tol=LUFS_TOL, tp_max=TP_MAX):
    """The one plain sentence an editor gets when the mix as delivered is off the loudness target (outside target +/- tol LUFS, or a
    true peak above tp_max), else None. Only said while G7 is audit-only: the engine leaves that mix alone, so a person has to know."""
    if i is None or tp is None or not (math.isfinite(i) and math.isfinite(tp)): return None
    parts = []
    if i < target - tol: parts.append(f"this cut is mixed at {i:.1f} LUFS; social platforms play it quieter than most videos.")
    elif i > target + tol: parts.append(f"this cut is mixed at {i:.1f} LUFS; social platforms turn it down, so it plays flatter than most videos.")
    if tp > tp_max:
        parts.append(f"It also peaks at {tp:.1f} dBTP, which can crackle when a platform re-encodes it." if parts
                     else f"this cut peaks at {tp:.1f} dBTP; it can crackle when a platform re-encodes it.")
    return " ".join(parts) or None


def g7_loudness(i, tp, strict=False, target=LUFS_TARGET, tol=LUFS_TOL, tp_max=TP_MAX):
    """The clip arrives already mixed, so by default this is an AUDIT: the numbers are measured and recorded, but they never block
    delivery -- `pass` reports True regardless of what was measured -- and when they are off the target the gate carries one plain
    `check` sentence (loudness_check) for the editor. Pass strict=True (CLI: --strict-loudness) only when your look sets
    master_audio: loudnorm, and it becomes a real hard gate. The record always carries lufs, true_peak and mode (audit | enforced)."""
    if i is None or tp is None:
        return gate(True, "loudness not measured", skipped=True)
    bad = []
    if abs(i - target) > tol: bad.append({"why": "integrated", "lufs": i, "target": target, "tol": tol})
    if tp > tp_max: bad.append({"why": "true_peak", "dbtp": tp, "max": tp_max})
    numbers = f"I {i} LUFS (target {target:g} +/-{tol:g}), TP {tp} dBTP (max {tp_max:g})"
    if not strict:
        d = gate(True, numbers + (" [audit only, not enforced]" if bad else ""), bad, report_only=True, lufs=i, true_peak=tp, mode="audit")
        check = loudness_check(i, tp, target, tol, tp_max)
        if check: d["check"] = check
        return d
    return gate(not bad, numbers, bad, lufs=i, true_peak=tp, mode="enforced")


# --- G8 AUDIO-ORIGIN -------------------------------------------------------------------------------

def probe_audio_start(path):
    """The reel's own first audio stream's start_time, straight from ffprobe. None when the file
    has no audio stream at all or ffprobe cannot read it -- that is itself a G8 failure, not a
    skip: a delivered reel always has audio."""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time",
              "-of", "json", path], "ffprobe")
    try:
        d = json.loads(r.stdout or b"{}")
        s = (d.get("streams") or [{}])[0]
        st = s.get("start_time")
        return None if st is None else float(st)
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def g8_audio_origin(reel, audio_wav, start_max=AUDIO_ORIGIN_START_MAX, lag_max=AUDIO_ORIGIN_LAG_MAX, sr=16000):
    """The direct defense against Remotion's documented 42.7ms audio-mux delay: Remotion's own
    render audio is never shipped, the source audio.wav is remuxed in instead
    (-map 0:v -map 1:a -c:a copy). If that step was skipped or landed wrong, this is the gate that
    says so in plain terms -- 'the audio wasn't remuxed in' -- rather than a generic sync failure
    folded into G2's wider tolerance."""
    if not reel or not os.path.exists(reel):
        return gate(True, "no reel to check", skipped=True)
    start = probe_audio_start(reel)
    bad = []
    if start is None:
        start_txt = "start_time n/a (no audio stream on the reel)"
        bad.append({"why": "no_audio_stream"})
    else:
        start_txt = f"start_time {start * 1000:+.1f}ms (<= {start_max * 1000:.0f}ms)"
        if abs(start) > start_max:
            bad.append({"why": "start_time", "s": round(start, 4), "max": start_max})
    lag_txt = "lag n/a (no audio.wav to compare against)"
    if audio_wav and os.path.exists(audio_wav) and start is not None:
        a, b = pcm16(reel, sr), pcm16(audio_wav, sr)
        if a.size >= sr and b.size >= sr:
            lag = lag_of(b, a, sr)
            lag_txt = f"lag {lag * 1000:+.1f}ms vs audio.wav (<= {lag_max * 1000:.0f}ms)"
            if abs(lag) > lag_max:
                bad.append({"why": "lag", "s": round(lag, 4), "max": lag_max})
        else:
            lag_txt = "lag n/a (audio too short to correlate)"
    return gate(not bad, f"{start_txt}, {lag_txt}", bad)


# --- G-ACCENT your accent colour through the picture path ------------------------------------------------

def g_accent(drift=None):
    """The accent gate: `drift` (accent_drift) over ACCENT_DRIFT_MAX is a FAIL, a fine one is stated in the numbers, and an accent that is not on screen (or a take whose
    source was HDR, which the conversion changes on purpose) is a visible SKIP, never a silent pass."""
    if not drift:
        return gate(True, "accent colour not measured (it is not flat on screen in the source, or the source was HDR)", [], skipped=True)
    if drift["shift"] > ACCENT_DRIFT_MAX:
        return gate(False, f"accent colour {drift['colour']} moved {drift['shift']} levels (limit {ACCENT_DRIFT_MAX:g})",
                    [{"why": "accent_colour", "colour": drift["colour"], "source": drift["source"], "mezzanine": drift["mezzanine"], "shift": drift["shift"]}], accent_colour=drift)
    return gate(True, f"accent colour {drift['colour']} within {drift['shift']} levels of the source", [], accent_colour=drift)


# --- the accent colour through the picture path (G-ACCENT) ---------------------------------------------------------------------------

ACCENT_DRIFT_MAX = 2.0    # levels per channel: your accent in the source and in the mezzanine may differ by this much
ACCENT_NEAR = 30          # a source pixel is "the accent" when each channel is within this many levels of it
ACCENT_MIN_PX = 300       # fewer flat pixels than this over the sampled frames: the colour is not on screen, nothing to compare
ACCENT_FRAMES = 10


def _erode(mask, n=2):
    """the interior of a boolean mask: a pixel survives when it and its 4 neighbours were set, n times (no edge pixels, which blend with their neighbours)"""
    for _ in range(n):
        e = mask.copy()
        e[1:, :] &= mask[:-1, :]; e[:-1, :] &= mask[1:, :]; e[:, 1:] &= mask[:, :-1]; e[:, :-1] &= mask[:, 1:]
        e[0, :] = e[-1, :] = False; e[:, 0] = e[:, -1] = False
        mask = e
    return mask


def _decode_as(path):
    """(matrix, range) a correct player decodes this file with: BT.601 for a bt470bg or smpte170m tag, BT.709 otherwise; full range for color_range=pc"""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space,color_range", "-of", "default=nw=1:nk=1", path], "ffprobe")
    tags = (r.stdout or b"").decode("utf-8", "replace").split()
    return ("bt601" if any(t in ("bt470bg", "smpte170m") for t in tags) else "bt709", "full" if "pc" in tags else "tv")


def _rgb_frame(path, t, w, h, matrix, rng):
    vf = f"scale={w}:{h}:in_color_matrix={matrix}:in_range={rng}:out_range=full:flags=accurate_rnd+full_chroma_int,format=rgb24"
    r = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], "ffmpeg")
    if len(r.stdout or b"") < w * h * 3: return None
    return np.frombuffer(r.stdout[:w * h * 3], np.uint8).reshape(h, w, 3).astype(np.int16)


def accent_drift(source, mez, accent, k=ACCENT_FRAMES, width=540):
    """Where the source shows your accent as flat colour, what the mezzanine shows at the same pixels and times. {colour, pixels, source, mezzanine, shift} with
    `shift` the largest per-channel difference of the two means, or None when the accent is not on screen. Each file is decoded the way a correct player does (its own
    matrix and range), so a conform that moved the colour shows here and a different coding of the same colour does not: this reads the pictures, not a report."""
    tgt = np.array([int(accent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], np.int16)
    meta = probe(mez)
    w, h = sweep_size(meta, width)
    src_m, mez_m = _decode_as(source), _decode_as(mez)
    dur = meta["duration"]
    times = np.linspace(0.3, max(0.3, dur - 0.3), k)
    a_all, b_all = [], []
    for t in times:
        a, b = _rgb_frame(source, t, w, h, *src_m), _rgb_frame(mez, t, w, h, *mez_m)
        if a is None or b is None: continue
        m = _erode((np.abs(a - tgt) <= ACCENT_NEAR).all(axis=2))
        if m.any():
            a_all.append(a[m]); b_all.append(b[m])
    if not a_all: return None
    a, b = np.concatenate(a_all), np.concatenate(b_all)
    if len(a) < ACCENT_MIN_PX: return None
    ma, mb = a.mean(axis=0), b.mean(axis=0)
    return {"colour": accent.upper(), "pixels": int(len(a)), "source": [round(float(x), 1) for x in ma], "mezzanine": [round(float(x), 1) for x in mb],
            "shift": round(float(np.abs(ma - mb).max()), 1)}


# --- G9 COLOR ---------------------------------------------------------------------------------------

COLOR_PIXEL_TOL = 4.0      # G9: the median per-channel difference (0-255) between reel and mezzanine pixels may not pass this
COLOR_PIXEL_FRAMES = 3


def calm_times(duration, plan, k=COLOR_PIXEL_FRAMES, fps=FPS):
    """k times spread across the reel where no punch or whip moves the picture (the reel and the mezzanine are the same
    picture there, apart from caption ink)."""
    busy = busy_frames(plan, fps)
    cands = [t for t in np.linspace(0.5, max(0.5, duration - 0.5), 12) if int(round(t * fps)) not in busy]
    if len(cands) <= k: return [float(t) for t in cands]
    return [float(cands[i]) for i in np.linspace(0, len(cands) - 1, k).round().astype(int)]


def pixel_color_drift(reel, mez, times, width=SWEEP_W):
    """Per-channel median |reel - mezzanine| over every pixel of the frames at `times`, worst frame, or None when no
    frame could be compared. The median ignores caption ink (a small share of the picture); a wrong colour conversion
    moves every pixel."""
    worst = None
    for t in times:
        a, b = _one_rgb(reel, t, width), _one_rgb(mez, t, width)
        if a is None or b is None or a.shape != b.shape: continue
        d = np.median(np.abs(a.astype(np.int16) - b.astype(np.int16)).reshape(-1, 3), axis=0)
        worst = d if worst is None else np.maximum(worst, d)
    return None if worst is None else [float(x) for x in worst]


def g9_color(reel, mez=None, times=()):
    """scripts/colortag_check.py (pix_fmt, range, matrix, primaries and transfer), called directly so its result rides inside
    the one reel-qc.json card. Same tag check runs standalone via `colortag_check.py <mp4>`. With the mezzanine and a few
    calm frames it also compares PIXELS: a stream relabelled bt709 over full-range BT.601 pictures (the old render.sh
    bug) has perfect tags and visibly shifted colours, and only this comparison sees it."""
    if not reel or not os.path.exists(reel):
        return gate(True, "no reel to check", skipped=True)
    r = colortag_check.check(reel)
    ok, numbers, detail = r["pass"], r["numbers"], list(r["detail"])
    drift = pixel_color_drift(reel, mez, times) if (mez and times) else None
    if drift is not None:
        numbers += f"; pixels vs mezzanine: median drift R{drift[0]:.1f} G{drift[1]:.1f} B{drift[2]:.1f} (limit {COLOR_PIXEL_TOL:g})"
        if max(drift) > COLOR_PIXEL_TOL:
            ok = False
            detail.append({"why": "pixels", "drift": [round(x, 1) for x in drift],
                           "plain": f"the picture's colours differ from the source by up to {max(drift):.0f} levels (limit {COLOR_PIXEL_TOL:g}): "
                                    f"the render's colour conversion is wrong even if the tags look right. Re-render."})
    return gate(ok, numbers, detail)


# --- G10 PACE (the README, "Pacing") --------------------------------------------

PACE_INK_CHANGE_PX = 20000        # canvas px of ink that must change between two sampled frames to count as a swap
PACE_INK_CHANGE_FRAC = 0.30      # ...and at least this fraction of the union of the two masks


def pace_change_timeline(frames, plan, mez, fps=FPS, canvas=CANVAS):
    """Seconds at which something the viewer sees changed, from four independent sources (see the
    G10 v2 note in run()): source cuts, punches, big ink swaps, layout events. Sorted, deduped to 0.2 s."""
    t = set()
    try:
        for c in cadence.detect_spikes(mez, fps):
            t.add(round(float(c), 2))
    except Exception:
        pass
    for p in (plan or {}).get("punches") or []:
        t.add(round(p.get("at", 0) / fps, 2))
    for e in (plan or {}).get("events") or []:
        if e.get("mode") in ("page", "frame", "igpost", "cta"):
            t.add(round(float(e["start"]), 2)); t.add(round(float(e["end"]), 2))
    prev = None
    for f in frames or []:
        sig, shape = f.get("_sig"), f.get("_sig_shape")
        if sig is None or shape is None:
            continue
        cur = np.unpackbits(np.frombuffer(sig, np.uint8))[: shape[0] * shape[1]].reshape(shape).astype(bool)
        if prev is not None:
            changed = int(np.logical_xor(cur, prev).sum()); union = int(np.logical_or(cur, prev).sum())
            # each signature cell is a 4x4 block at sweep scale; scale the count to canvas px
            px = changed * 16 * (canvas[0] / SWEEP_W) ** 2
            if px >= PACE_INK_CHANGE_PX and union and changed / union >= PACE_INK_CHANGE_FRAC:
                t.add(round(float(f["t"]), 2))
        prev = cur
    out = []
    for x in sorted(t):
        if not out or x - out[-1] >= 0.2:
            out.append(x)
    return out


def g10_pace(spikes, t0, t1, median_max=PACE_MEDIAN_MAX, p90_max=PACE_P90_MAX, max_max=PACE_MAX_MAX,
             report_only_under=PACE_REPORT_ONLY_UNDER_S, mode="enforce"):
    """spikes: cadence.detect_spikes() run on the RENDERED reel (the same 30fps/96x170 grey
    frame-diff detector that finds source cuts on the mezzanine). Measured over the SPOKEN SPAN
    only (t0 = first word start, t1 = last word end -- the same span G-FACE uses), never the whole
    reel: a cold open or a tail card is not what "every few seconds" is asking about. Report-only
    (never fails) for a spoken span under `report_only_under` seconds -- too short a sample for a
    median/p90 to mean anything. `mode` is the look's pace gate: "off" skips the gate, "report" measures and prints and never fails, "enforce"
    fails a take over the limits."""
    if mode == "off":
        return gate(True, "pace gate off (set pace_gate to report or enforce in your look to measure it)", skipped=True)
    if t0 is None or t1 is None or t1 <= t0:
        return gate(True, "no spoken span to measure pace against", skipped=True)
    span = t1 - t0
    in_span = sorted(s for s in (spikes or []) if t0 <= s <= t1)
    gaps = np.diff([t0] + in_span + [t1]) if in_span else np.array([span])
    median, p90, mx = float(np.median(gaps)), float(np.percentile(gaps, 90)), float(gaps.max())
    report_only = span < report_only_under or mode == "report"
    bad = []
    if median > median_max:
        bad.append({"why": "median", "median": round(median, 2), "max": median_max})
    if p90 > p90_max:
        bad.append({"why": "p90", "p90": round(p90, 2), "max": p90_max})
    if mx > max_max:
        worst_start = ([t0] + in_span)[int(np.argmax(gaps))]
        bad.append({"why": "max", "gap": round(mx, 2), "max": max_max, "at": round(worst_start, 2)})
    numbers = (f"median {median:.2f}s (<= {median_max:g}), p90 {p90:.2f}s (<= {p90_max:g}), "
               f"max {mx:.2f}s (<= {max_max:g}) over spoken span {t0:.2f}-{t1:.2f}s, {len(in_span)} change(s)")
    if report_only:
        why = "pace gate is report" if mode == "report" else f"spoken span under {report_only_under:g}s"
        suffix = f" [report-only, {why}]" if bad else ""
        return gate(True, numbers + suffix, bad, report_only=True)
    return gate(not bad, numbers, bad)


# --- numeric helpers behind G2 / G8 -----------------------------------------------------------------

def ncc(a, b):
    """Normalized correlation of two same-shape frames. A flat frame correlates with nothing, so it
    returns 0 rather than a divide-by-zero -- a held/blank frame must not read as a match."""
    x = a.astype(np.float64).ravel() - float(np.mean(a))
    y = b.astype(np.float64).ravel() - float(np.mean(b))
    dx, dy = math.sqrt(float(x @ x)), math.sqrt(float(y @ y))
    if dx == 0 or dy == 0: return 0.0
    return round(float(x @ y) / (dx * dy), 4)


def lag_and_strength(a, b, sr=16000, max_lag_s=0.5):
    """(seconds `b` lags `a`, how well they match: the normalised correlation at that lag, 0 to 1), by FFT cross-correlation. Direct
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


def clear_of_spans(times, spans, duration, pad=0.1):
    """The times with any that falls inside a span (plus pad) moved to just before or just after it, whichever is nearer and
    still inside the reel; a time that cannot be moved is dropped. A layout event replaces the picture and a punch ramp or a whip
    moves it, so a picture comparison sampled inside one reads as drift that is not there (see picture_busy_spans)."""
    spans = sorted(spans)
    def inside(t): return any(a - pad <= t <= b + pad for a, b in spans)
    out = []
    for t in times:
        if not inside(t):
            out.append(t); continue
        cands = [x for a, b in spans if a - pad <= t <= b + pad for x in (a - 2 * pad, b + 2 * pad)]
        cands = [round(x, 3) for x in cands if pad <= x <= duration - pad and not inside(x)]
        if cands: out.append(min(cands, key=lambda x: abs(x - t)))
    return sorted(set(out))


def sample_times(duration, k=3, edge=0.5):
    """k timestamps spread across the reel, each far enough from a hard edge to hold a comparison
    window. Replaces the old EDL piece-sampling (first/middle/last PIECE): with no pieces, the reel
    IS the piece, so this spreads across ITS whole duration instead. Degenerates gracefully for a
    very short reel -- everything collapses to the midpoint rather than raising."""
    if duration <= 0: return []
    half = duration / 2
    lo, hi = min(edge, half), max(duration - min(edge, half), min(edge, half))
    if hi <= lo: return [round(half, 3)]
    if k <= 1: return [round((lo + hi) / 2, 3)]
    return [round(lo + i * (hi - lo) / (k - 1), 3) for i in range(k)]


# --- ffmpeg ----------------------------------------------------------------------------------------

def _run(cmd, service, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, **kw)
    except OSError as e:
        raise EngineStop(f"{service} is down: {type(e).__name__}: {e}")


def _popen(cmd, service, **kw):
    try:
        return subprocess.Popen(cmd, **kw)
    except OSError as e:
        raise EngineStop(f"{service} is down: {type(e).__name__}: {e}")


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
        raise EngineStop(f"ffprobe could not read {os.path.basename(path)}: "
                         f"{(r.stderr or b'').decode('utf-8', 'replace').strip()[:160] or 'no video stream'}")


def sweep_size(meta, width=SWEEP_W):
    """The sweep box is pinned here rather than left to scale's -2, so the reel and the mezzanine
    are guaranteed to produce the same frame size and the diff is well defined."""
    return width, max(2, int(round(meta["h"] * width / meta["w"] / 2)) * 2)


def _gray_proc(path, w, h, step):
    vf = f"select='not(mod(n\\,{step}))',scale={w}:{h},format=gray" if step > 1 else f"scale={w}:{h},format=gray"
    return _popen(["ffmpeg", "-v", "error", "-an", "-i", path, "-vf", vf,
                   "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                  "ffmpeg", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def _read_frame(stream, size):
    buf = stream.read(size)
    return None if not buf or len(buf) < size else buf


def _punch_gray(fb, punch, canvas=CANVAS):
    """Apply the render's punch transform to one mezzanine gray frame (sweep scale): crop the region
    the zoom shows about (cx, cy) and resize it back to the frame size. Without this, a punched
    frame differs from the raw mezzanine EVERYWHERE and G4 reads the zoom itself as ink
    (without it, 45 'outside' + 42 'on face' frames on a sampler, all inside punches)."""
    from PIL import Image
    h, w = fb.shape
    sc = punch["scale"]
    cw, ch = w / sc, h / sc
    cx, cy = punch["cx"] * w / canvas[0], punch["cy"] * h / canvas[1]
    # CSS `scale(s)` with `transform-origin: cx cy` keeps (cx, cy) fixed on screen: the visible window's
    # top-left is (cx*(1-1/s), cy*(1-1/s)), NOT a window centred on (cx, cy) (the centred
    # crop was ~50 px off vertically, so every punched frame still diffed as ink).
    x0 = min(max(0.0, cx * (1 - 1 / sc)), w - cw); y0 = min(max(0.0, cy * (1 - 1 / sc)), h - ch)
    im = Image.fromarray(fb).crop((int(round(x0)), int(round(y0)), int(round(x0 + cw)), int(round(y0 + ch))))
    return np.asarray(im.resize((w, h), Image.BILINEAR), dtype=np.uint8)


def _ink_signature(mask, block=4):
    """The ink mask at 1/`block` resolution, packed: what G10 compares between consecutive sampled
    frames to find a real caption change (a big swap) as opposed to jitter."""
    h, w = mask.shape
    hh, ww = h - h % block, w - w % block
    small = mask[:hh, :ww].reshape(hh // block, block, ww // block, block).any(axis=(1, 3))
    return np.packbits(small).tobytes(), small.shape


def sweep(reel, src, track, fps, width=SWEEP_W, step=SWEEP_STEP, thresh=INK_DIFF, safe=SAFE, punches=()):
    """ONE decode of each file, differenced frame by frame. Returns the per-frame ink record the
    G4 gate reads (plus a coarse ink signature per frame for G10). The mezzanine frame is put through
    the plan's punch transform first when a punch is active at that time."""
    a = probe(reel); w, h = sweep_size(a, width)
    pa, pb = _gray_proc(reel, w, h, step), _gray_proc(src, w, h, step)
    size = w * h
    frames, n = [], 0
    try:
        while True:
            fa, fb = _read_frame(pa.stdout, size), _read_frame(pb.stdout, size)
            if fa is None or fb is None: break
            t = n / fps
            ga = np.frombuffer(fa, np.uint8).reshape(h, w)
            gb = np.frombuffer(fb, np.uint8).reshape(h, w)
            pu = _punch_at(punches, t, fps) if punches else None
            th = thresh
            if pu and pu.get("scale", 1.0) > 1.0 + 1e-6:
                gb = _punch_gray(gb, pu)
                # a re-sampled zoom leaves edge noise up to ~90/255 against the render's own resampling;
                # caption glyph cores sit above 110. Raise the bar only while a punch is active.
                th = PUNCH_INK_DIFF
            d = np.abs(ga.astype(np.int16) - gb.astype(np.int16))
            f = face_at(track, t)
            sig, shape = _ink_signature(d >= th)
            frames.append({"n": n, "t": round(t, 3), "crown": crown_y(f), "_sig": sig, "_sig_shape": shape,
                           **ink_counts(d, f, crown_y(f), th, safe=safe)})
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


def _punch_at(punches, t, fps=FPS):
    """The (scale, cx, cy) a punch imposes on the frame at time t, or None when no punch is active
    there. Timeline per punch (the README, "Pacing"): ease 1->scale over
    `frames` (default 3), hold at `scale` for `hold` frames, ease back to 1 over
    PUNCH_EASE_OUT_FRAMES. Mirrors the render's own transform closely enough for G2's frame-match
    to compare like for like -- it does not need to be exact, only in the same crop/scale family."""
    fr = t * fps
    for p in sorted(punches or [], key=lambda p: p.get("at", 0)):
        at = p.get("at", 0); frames_in = p.get("frames", 3); hold = p.get("hold", 24)
        scale = p.get("scale", cadence.PUNCH_SCALE)
        end_hold = at + frames_in + hold
        end = end_hold + PUNCH_EASE_OUT_FRAMES
        if at <= fr < end:
            if fr < at + frames_in:
                k = (fr - at) / max(1, frames_in)
                s = 1.0 + (scale - 1.0) * k
            elif fr < end_hold:
                s = scale
            else:
                k = (fr - end_hold) / PUNCH_EASE_OUT_FRAMES
                s = scale + (1.0 - scale) * k
            return {"scale": s, "cx": p.get("cx", CANVAS[0] / 2), "cy": p.get("cy", CANVAS[1] / 2)}
    return None


def _one_gray_punched(path, t, w, h, punch, canvas=CANVAS):
    """Like _one_gray, but when `punch` is active, crops the frame to what THAT scale x zoom about
    (cx, cy) would show (canvas space) before the final downscale -- the same region the render's
    own punch transform puts on screen, so a punched frame reads as a match, not as drift."""
    vf = []
    if punch and punch.get("scale", 1.0) > 1.0 + 1e-6:
        s = punch["scale"]
        cw, ch = canvas[0] / s, canvas[1] / s
        # transform-origin semantics (see _punch_gray): the origin point stays put, the window's top-left is origin*(1-1/s)
        cx0 = min(max(0.0, punch["cx"] * (1 - 1 / s)), canvas[0] - cw)
        cy0 = min(max(0.0, punch["cy"] * (1 - 1 / s)), canvas[1] - ch)
        vf.append(f"crop={cw:.2f}:{ch:.2f}:{cx0:.2f}:{cy0:.2f}")
    vf.append(f"scale={w}:{h},format=gray")
    r = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
              "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "gray", "-"], "ffmpeg")
    if len(r.stdout or b"") < w * h: return None
    return np.frombuffer(r.stdout[:w * h], np.uint8).reshape(h, w)


def _one_rgb(path, t, w):
    r = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1",
              "-vf", f"scale={w}:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], "ffmpeg")
    buf = r.stdout or b""
    h = len(buf) // (w * 3)
    if h < 2: return None
    return np.frombuffer(buf[:h * w * 3], np.uint8).reshape(h, w, 3).copy()


def frame_match(reel, mez, times, width=SWEEP_W, punches=None):
    """The reel's own picture against the mezzanine's, AT THE SAME TIMESTAMP -- identity time, no
    offset mapping needed, because a captions-only render never moves a frame in time. A conform or
    render step that silently broke shows up here as ~0.4 while every duration check still reads
    clean. `punches` (plan["punches"], the README, "Pacing"): when one is active
    at a sample timestamp, the MEZZANINE side is transformed by its scale/origin before comparing
    -- otherwise every punched frame reads as drift, since one side is zoomed and the other isn't."""
    if not (reel and mez and times): return []
    w, h = sweep_size(probe(reel), width)
    out = []
    for t in times:
        a = _one_gray(reel, t, w, h)
        punch = _punch_at(punches, t) if punches else None
        b = _one_gray_punched(mez, t, w, h, punch) if punch else _one_gray(mez, t, w, h)
        if a is None or b is None: continue
        out.append({"t": t, "corr": ncc(a, b)})
    return out


def pcm16(path, sr=16000):
    r = _run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
              "-f", "s16le", "-"], "ffmpeg")
    return np.frombuffer(r.stdout or b"", np.int16).astype(np.float32)


def audio_lag(reel, audio_wav, sr=16000, win_s=LAG_WINDOW_S, min_win_s=0.5):
    """The shipped audio against the take's own audio.wav (the file render.sh remuxed under the picture; the mezzanine is video only,
    so there is nothing to correlate against there). Output time == source time, so no offset mapping is needed. One window over the
    first `win_s` seconds and, when the audio runs at least twice that long, one over the last `win_s`: a fixed lag shows in both and
    a drift shows as the difference. A window with no sound in it is left out (reported by the gate as not measured), never read as 0 ms.
    Each record: {"at": window start s, "win_s": its length, "lag_s": seconds the reel lags audio.wav, "corr": how well they match}."""
    if not (reel and audio_wav and os.path.exists(reel) and os.path.exists(audio_wav)): return []
    a, b = pcm16(reel, sr), pcm16(audio_wav, sr)
    total = min(a.size, b.size) / sr
    starts = [0.0] + ([total - win_s] if total >= 2 * win_s else [])
    n = int(win_s * sr)
    out = []
    for t in starts:
        i = int(t * sr)
        x, y = a[i:i + n], b[i:i + n]
        if x.size < min_win_s * sr or y.size < min_win_s * sr: continue
        lag, corr = lag_and_strength(y, x, sr)
        if corr == 0.0: continue
        out.append({"at": round(t, 3), "win_s": round(min(x.size, y.size) / sr, 3), "lag_s": lag, "corr": corr})
    return out


def freezes_of(mez, min_s=FREEZE_MIN_S, width=SWEEP_W):
    """Started as a Popen by the caller so it runs alongside the sweeps instead of after them.
    Downscaled first -- freezedetect compares frames, and it compares 240-px frames just as well."""
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
        return round(float(d["input_i"]), 1), round(float(d["input_tp"]), 1)
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
        raise EngineStop(f"ffmpeg could not write {os.path.basename(path)}: "
                         f"{(p.stderr or b'').decode('utf-8', 'replace').strip()[:160]}")


def _rect(img, x0, y0, x1, y1, color, t=2):
    h, w = img.shape[:2]
    x0, x1 = max(0, min(w - 1, x0)), max(0, min(w, x1)); y0, y1 = max(0, min(h - 1, y0)), max(0, min(h, y1))
    img[y0:min(h, y0 + t), x0:x1] = color; img[max(0, y1 - t):y1, x0:x1] = color
    img[y0:y1, x0:min(w, x0 + t)] = color; img[y0:y1, max(0, x1 - t):x1] = color


def layout_spans(plan, fps=FPS):
    """[(start s, end s)] of the plan's layout events (cta, page, frame, igpost): the beats where the picture is replaced, shrunk or
    washed on purpose, so the reel is not the mezzanine's picture there."""
    return [(float(e["start"]), float(e["end"])) for e in (plan or {}).get("events") or [] if e.get("mode") in DEVICE_LAYOUT_MODES]


def chip_spans(plan):
    """[(start s, end s)] of the caption events that draw an opaque box over the picture: a negative word's red chip, and the brand's accent
    chip when the brand uses one. A big solid box changes enough of the frame to pull a correct reel under the match bar (a red NEVER chip
    read 0.896 against 0.9 on a reel whose frame differed from the mezzanine's only where the chip sits)."""
    chip_brand = bool((plan or {}).get("chip"))
    return [(float(e["start"]), float(e["end"])) for e in (plan or {}).get("events") or []
            if any(w.get("neg") or (chip_brand and w.get("accent")) for w in e.get("words") or [])]


def bar_spans(plan):
    """[(start s, end s)] of the soft build thoughts with a bar row: the bar is an opaque block of the accent behind its payoff, a box the
    mezzanine does not have, so it pulls a correct reel under the match bar the way a chip does (chip_spans)."""
    return [(float(e["start"]), float(e["end"])) for e in (plan or {}).get("events") or []
            if e.get("soft") and any(w.get("bar") for w in e.get("words") or [])]


def picture_busy_spans(plan, fps=FPS):
    """[(start s, end s)] where the reel's picture cannot be compared with the mezzanine's frame for frame: the layout events (replaced,
    shrunk or washed on purpose), a caption chip or a soft build bar (an opaque box, see chip_spans and bar_spans), a punch-in's snap-in and
    its ease back (eased curves the linear stand-in in _punch_at does not reproduce: sampled mid-ease a good reel read 0.896 against the 0.9
    bar) and a whip (the whole frame blurs and pushes). The HELD frames of a punch are not here: they are compared exactly, which also proves
    the render applied the planned scale."""
    spans = list(layout_spans(plan, fps)) + chip_spans(plan) + bar_spans(plan)
    for p in (plan or {}).get("punches") or []:
        at = int(p.get("at", 0)); fi = int(p.get("frames", 3)); hold = int(p.get("hold", 24))
        spans.append(((at - 1) / fps, (at + fi + 2) / fps))
        spans.append(((at + fi + hold - 1) / fps, (at + fi + hold + PUNCH_EASE_OUT_FRAMES + 2) / fps))
    for w in (plan or {}).get("whips") or []:
        n = int(w.get("frames", 4)); spans.append(((int(w["at"]) - n) / fps, (int(w["at"]) + n + 1) / fps))
    return spans


def busy_frames(plan, fps=FPS):
    """Frame numbers where the picture is being moved on purpose (a punch-in's snap, hold and ease, a whip, a layout event): the
    contact sheet's and the colour check's reel-vs-mezzanine difference lights the whole frame there, so it is no use as evidence."""
    busy = set()
    for a, b in layout_spans(plan, fps):
        busy.update(range(int(round(a * fps)), int(round(b * fps)) + 1))
    for p in (plan or {}).get("punches") or []:
        at = int(p.get("at", 0)); fi = int(p.get("frames", 3)); hold = int(p.get("hold", 24))
        busy.update(range(at - 1, at + fi + hold + PUNCH_EASE_OUT_FRAMES + 2))
    for w in (plan or {}).get("whips") or []:
        n = int(w.get("frames", 4)); busy.update(range(int(w["at"]) - n, int(w["at"]) + n + 1))
    return busy


def sheet_times(frames, k=SHEET_N, busy=()):
    """The inkiest frame of each of k equal slices (spread across the reel AND showing captions), preferring frames
    where no punch or whip is moving the picture."""
    if not frames: return []
    out = []
    for b in np.array_split(np.arange(len(frames)), min(k, len(frames))):
        if not len(b): continue
        calm = [i for i in b if frames[i]["n"] not in busy]
        out.append(frames[max(calm or b, key=lambda i: frames[i]["ink"])]["t"])
    return sorted(set(out))


def contact_sheet(reel, src, times, track, out, width=SHEET_W, thresh=INK_DIFF, canvas=CANVAS):
    """Annotated: green safe box, blue face box, cyan crown line, magenta caption ink. The ink is
    the same difference the sweep measures, drawn where it was found, so a FAIL on G4 can be READ
    instead of re-derived."""
    tiles = []
    for t in times:
        a = _one_rgb(reel, t, width); b = _one_rgb(src, t, width)
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
        raise EngineStop(f"could not read {os.path.basename(path)}: {type(e).__name__}: {e}")


def _first(*paths):
    for p in paths:
        if p and os.path.exists(p) and os.path.getsize(p) > 0: return p
    return None


def run(T, N, strict_loudness=False, pace_gate=None):
    T = os.path.abspath(T)
    reel = os.path.join(T, f"{N}-reel.mp4")
    if not os.path.exists(reel):
        raise EngineStop(f"no reel to check: {N}-reel.mp4 not found in {T}")
    mez = _first(os.path.join(T, "mez1080.mp4"), os.path.join(T, "mez.mp4"))
    words_p = os.path.join(T, "words.json")
    if not os.path.exists(words_p):
        raise EngineStop(f"no source transcript: {words_p}")
    words_doc = _load(words_p) or {}
    words_all = words_doc.get("words") or []
    audio_p = os.path.join(T, "audio.wav")
    plan = _load(os.path.join(T, "plan.json"))
    events = None if plan is None else (plan.get("events") or [])
    flags = [] if plan is None else (plan.get("flags") or [])
    whips = (plan.get("whips") if plan else None) or []
    punches = (plan.get("punches") if plan else None) or []
    gate_ov = ((_load(os.path.join(T, "gate.json")) or {}).get("overrides") or {})
    look = _load(os.path.join(T, "look.json")) or {}
    pace_mode = pace_gate or look.get("pace_gate") or "off"
    if pace_mode not in PACE_MODES:
        raise EngineStop(f"the pace gate is {pace_mode!r}, which is not one of {', '.join(PACE_MODES)}")
    ft_p = os.path.join(T, "facetrack.json")
    track = _load(ft_p)
    if track is None and mez:
        track = facetrack.build(mez, ft_p)      # only when the run_captions.sh facetrack stage did not run

    ev = os.path.join(T, "evidence"); os.makedirs(ev, exist_ok=True)
    strip, sheet = os.path.join(ev, "strip.png"), os.path.join(ev, "contact-sheet.png")

    reel_dur = probe(reel)["duration"]
    times = clear_of_spans(sample_times(reel_dur), picture_busy_spans(plan), reel_dur)

    # The independent ffmpeg passes go first so they run WHILE the sweep does.
    fz = freezes_of(mez) if mez else None
    loud = loudness_proc(reel)
    stripper = strip_proc(reel, strip, reel_dur)

    # a plan may carry its own safe box (cluster "top" zone: the top third uses x 60..1020, no IG controls there)
    frames = sweep(reel, mez, track, FPS, safe=([SAFE, plan["safe"]] if (plan and plan.get("safe")) else SAFE),
                   punches=punches) if mez else []
    matches = frame_match(reel, mez, times, punches=punches) if mez else []
    lags = audio_lag(reel, audio_p)
    intake = _load(os.path.join(T, "intake.json")) or {}
    src_p = intake.get("source") if os.path.exists(intake.get("source") or "") else os.path.join(T, "source.mp4")
    drift = accent_drift(src_p, mez, look["accent"]) if (mez and look.get("accent") and not intake.get("hdr") and os.path.exists(src_p)) else None

    if fz is None: freezes = None
    else:
        import avskew
        freezes = avskew.parse_freezes((fz.communicate()[1] or b"").decode("utf-8", "replace"))
    i_lufs, tp = parse_loudnorm((loud.communicate()[1] or b"").decode("utf-8", "replace"))
    stripper.communicate()
    sheet = contact_sheet(reel, mez, sheet_times(frames, busy=busy_frames(plan)), track, sheet) if (mez and frames) else None

    # G10 pace: a motion-adaptive spike detector on the whole picture hides caption
    # changes under a handheld camera (3 "changes" in 12.7 s of dense captions).
    # Count what the eye actually gets instead: cuts in the SOURCE (the detector on the mezzanine),
    # the plan's punches, big caption swaps measured on the rendered INK (consecutive sweep frames
    # whose ink masks differ by >= PACE_INK_CHANGE_PX canvas px), and layout events (page/frame/
    # igpost/cta own the frame; their spans are exempt from the sweep so their ink is not measured).
    pace_changes = pace_change_timeline(frames, plan, mez, FPS) if pace_mode != "off" else []
    t0, t1 = spoken_span(words_all, reel_dur) if words_all else (None, None)
    pwarn = punch_origin_warnings(punches, track, FPS)

    gates = {"G-FACE": g_face(words_all, track, audit_only=(gate_ov.get("subject") == "none"), duration=reel_dur),
             "G2": g2_picture_sound(freezes, matches, lags),
             "G4": g4_captions(frames, events, FPS, whips=whips, punch_warnings=pwarn, punches=punches),
             "G5": g5_events(events, words_doc, flags, FPS),
             "G-WORDS": g_words(plan, look.get("do_not_say"), gate_ov.get("allow") or []),
             "G-ACCENT": g_accent(drift),
             "G6": g6_visual(strip),
             "G7": g7_loudness(i_lufs, tp, strict=strict_loudness),
             "G8": g8_audio_origin(reel, audio_p),
             "G9": g9_color(reel, mez, calm_times(reel_dur, plan)),
             "G10": g10_pace(pace_changes, t0, t1, mode=pace_mode)}
    card = {"verdict": verdict_of(gates), "reel": reel, "mezzanine": mez, "gates": gates,
            "strip": strip if os.path.exists(strip) else None, "sheet": sheet}
    json.dump(card, open(os.path.join(T, "reel-qc.json"), "w"), indent=1)
    return card


GATE_ORDER = ("G-FACE", "G2", "G4", "G5", "G-WORDS", "G-ACCENT", "G6", "G7", "G8", "G9", "G10")


def _main():
    args = sys.argv[1:]
    strict = "--strict-loudness" in args
    args = [a for a in args if a != "--strict-loudness"]
    pace = None
    if "--pace-gate" in args:
        i = args.index("--pace-gate")
        pace = args[i + 1]
        del args[i:i + 2]
    T, N = os.path.abspath(args[0]), args[1]
    card = run(T, N, strict_loudness=strict, pace_gate=pace)
    for k in GATE_ORDER:
        print(line(k, card["gates"][k]))
    failed = [k for k in MACHINE_GATES if card["gates"][k]["pass"] is False]
    print(f"{card['verdict']}" + (f" ({', '.join(failed)})" if failed else "")
          + f". Strip {card['strip']}" + (f", sheet {card['sheet']}" if card["sheet"] else ""))
    for k in failed:
        for d in card["gates"][k]["detail"]:
            print(f"  {k}: {plain(k, d)}")
    sys.exit(0 if card["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    cli(_main, "reel_qc.py <take-dir> <name> [--strict-loudness] [--pace-gate off|report|enforce]")
