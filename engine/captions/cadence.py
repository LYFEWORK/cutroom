#!/usr/bin/env python3
"""cadence.py <mez.mp4> <plan.json> <facetrack.json> <out.plan.json> [--cuts cuts.json] [--max-gap 3.0] [--auto on|off]

the README, "Pacing": the pacing pass. Runs after devices-propose, before the
gate is built on a take's first pass through run_captions.sh, and again inside its post-gate re-derive
path once an operator's overrides have rebuilt plan.json. <plan.json> and <out.plan.json> may be
the same path (read-then-write-same-file, the pattern devices.py apply() already uses).

1. Source cuts: 30 fps frame-diff spikes on the mezzanine (diff > max(6, 2.2x the 15-frame local
   baseline), one event per 6 frames -- see detect_spikes() below). Written to <take-dir>/cuts.json (or --cuts);
   reused, not recomputed, when that file already exists (an unchanged mezzanine has the same cuts
   every time this stage runs).
2. Change timeline = cuts UNION cluster onsets/ends UNION device onsets (a glow/neg/count/strike/
   pin/spaced/logo/icon tag's own row `at`) UNION layout-event onsets/ends (cta/page/frame/igpost)
   UNION whips.
3. Every gap over --max-gap (default 3.0s) gets a punch-in at the first cluster onset inside it,
   or gap_start+1.5s if nothing lands there and that spot itself is not within 8 frames of a cut,
   a whip, a reel edge, or another punch; re-scanned pass by pass -- inserting a punch changes the
   timeline, which can split one long gap into several short ones, or leave a genuinely stuck gap
   that no candidate can land in without breaking the 8-frame rule -- until nothing more can move.
4. plan["punches"] gets the survivors (frame `at`, always scale PUNCH_SCALE = 1.12 -- see _make_punch()'s own
   note on why this file never emits a paired scale:1.0 "out" entry) and plan["pace"] reports
   median/p90/max gap over the FINAL (post-punch) timeline, punches_added, and any quiet stretch
   still over --max-gap as [t0, t1] pairs. capgate.py's own "## Pacing" section is where a human
   reads these, per GATE.md's `pace: <t0>-<t1> quiet` line.

--auto on|off: whether step 3's AUTOMATIC gap-filling runs at all (default on, which is what the pass always did). run_captions.sh passes `off` unless
your DNA's auto_punches is true, so by default a take gets no punch-in it did not ask for; the cuts, the pace numbers and the quiet report are made either way, and a
GATE.md `punch: <time>` line still adds one. A GATE.md `punch: off` line turns the automatic pass off for that take whatever --auto says.

spec.json["device_overrides"] (written by capgate.py apply from the GATE.md Pacing section, read
straight from <plan-dir>/spec.json -- not a CLI arg, same co-located-optional-file convention as
capgate.py's own devices-propose.json lookup): `punch: false` skips step 3's AUTOMATIC gap-filling
entirely (the quiet report still runs, against the un-punched timeline, so a human still sees
where the gaps are); `punch_add` is a list of operator-requested times, inserted before step 3 and
exempt from the 8-frames-from-cut/whip guard (an explicit ask is honored, never silently
dropped); `punch_off` is a list of times whose nearest surviving punch (within 1s) is removed
after step 3.
"""
import json
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import facetrack
from keys import cli

FPS = 30
MAX_GAP_DEFAULT = 3.0
PUNCH_GUARD_FRAMES = 8         # never within this many frames of a cut / whip / reel edge / another punch
PUNCH_SCALE = 1.12             # 1.08 reads as a nudge; 1.12 is the least that reads as a punch
PUNCH_HOLD_DEFAULT = 24        # frames: schema default "until the next punch or 24"
PUNCH_HOLD_MIN = 4
FALLBACK_OFFSET = 1.5
STRONG_KEYS = ("neg", "count", "strike", "pin", "spaced", "logo", "icon")   # devices with visual mass (see _structural_timeline)
BIG_HERO_PX = 180                                                             # a glow hero this big is a change on its own          # gap_start + this, when no cluster onset lands inside the gap
DEVICE_KEYS = ("glow", "neg", "count", "strike", "pin", "spaced", "logo", "icon")
LAYOUT_MODES = ("cta", "page", "frame", "igpost")


# ---------------------------------------------------------------------------------------------
# source-cut / spike detection
# ---------------------------------------------------------------------------------------------

def detect_spikes(video_path, fps=FPS):
    """30 fps, 96x170 gray frame-diff spike timeline. diff > max(6, 2.2x the 15-frame local
    baseline); one event per 6 frames (0.2s). Returns sorted spike times in seconds. Reused
    verbatim (same numbers, same shapes) by reel_qc.py's G10 on the rendered reel."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video_path, "-vf", f"fps={fps},scale=96:170",
                          "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (96 * 170)
    if n < 2:
        return []
    fr = np.frombuffer(raw[: n * 96 * 170], dtype=np.uint8).reshape(n, 170, 96).astype(np.float32)
    diff = np.abs(fr[1:] - fr[:-1]).mean(axis=(1, 2))
    base = np.convolve(diff, np.ones(15) / 15, mode="same")
    spikes = []
    i = 0
    while i < len(diff):
        if diff[i] > max(6.0, base[i] * 2.2):
            spikes.append(round((i + 1) / fps, 2))
            i += 6
        else:
            i += 1
    return spikes


def cuts_for(mez_path, cuts_path, fps=FPS):
    """cuts.json is a plain {"cuts": [t, ...]} cache next to the plan -- reused, not recomputed,
    whenever it already exists (the mezzanine is immutable once conform.sh has produced it)."""
    if cuts_path and os.path.exists(cuts_path) and os.path.getsize(cuts_path) > 0:
        try:
            return json.load(open(cuts_path)).get("cuts", [])
        except (OSError, json.JSONDecodeError):
            pass
    cuts = detect_spikes(mez_path, fps) if mez_path and os.path.exists(mez_path) else []
    if cuts_path:
        json.dump({"cuts": cuts}, open(cuts_path, "w"), indent=1)
    return cuts


# ---------------------------------------------------------------------------------------------
# change timeline -- pure, no I/O, unit-testable on a synthetic plan
# ---------------------------------------------------------------------------------------------

def change_timeline(plan, cuts, fps=FPS):
    """cuts UNION cluster onsets/ends UNION device onsets UNION layout-event onsets/ends UNION
    whips, in seconds, sorted and deduped."""
    t = set(round(float(c), 3) for c in (cuts or []))
    for e in plan.get("events") or []:
        mode = e.get("mode")
        if mode in ("front", "behind"):
            if e.get("start") is not None:
                t.add(round(float(e["start"]), 3))
            if e.get("end") is not None:
                t.add(round(float(e["end"]), 3))
            for w in e.get("words") or []:
                if any(k in w for k in DEVICE_KEYS):
                    t.add(round(float(w.get("at", e.get("start", 0.0))), 3))
        elif mode in LAYOUT_MODES:
            if e.get("start") is not None:
                t.add(round(float(e["start"]), 3))
            if e.get("end") is not None:
                t.add(round(float(e["end"]), 3))
    for w in plan.get("whips") or []:
        if w.get("at") is not None:
            t.add(round(w["at"] / fps, 3))
    return sorted(t)


def cluster_onsets(plan):
    return sorted({round(float(e["start"]), 3) for e in (plan.get("events") or [])
                   if e.get("mode") in ("front", "behind") and e.get("start") is not None})


def _structural_timeline(plan, cuts, fps=FPS):
    """The subset of change_timeline() actually used to DETECT a too-long gap: cuts, device
    onsets, layout-event onsets/ends, and whips -- the "something deliberately happened" events.
    Plain cluster onsets/ends (a caption simply landing/leaving, with no device on it) are left
    OUT of this set on purpose: change_timeline() already includes them, so a cluster's own start
    is always a member of the FULL timeline, which would make "the first cluster onset inside the
    gap" impossible to ever satisfy -- it can't be strictly between two
    ADJACENT points of a timeline it already belongs to. Excluding plain clusters from gap
    DETECTION (while keeping cluster_onsets() below as the preferred LANDING spot for a punch once
    a gap is found) is what makes that clause reachable: a punch syncs to a caption that is about
    to land anyway rather than an arbitrary offset, in a stretch where the FOOTAGE itself is still
    static. Plain clusters still count toward the final pace numbers (change_timeline(), point 4)
    and toward reel_qc.py's G10, which measures the rendered pixels, not this structure."""
    t = set(round(float(c), 3) for c in (cuts or []))
    for e in plan.get("events") or []:
        mode = e.get("mode")
        if mode in ("front", "behind"):
            for w in e.get("words") or []:
                # Only changes with visual MASS count here. A glow hero at 100-140 px is a caption swap
                # the spike detector (and the eye, at phone size) does not register as "something
                # happened" -- the reference's changes are cuts, pages, chips, pins, counts and huge
                # words. Counting every glow hero made the MCA sampler look paced (structural max gap
                # 2.6 s) while its render measured 5.9 s quiet. So: strong devices always, glow only
                # when the row is big (>= BIG_HERO_PX).
                strong = any(k in w for k in STRONG_KEYS)
                big_glow = w.get("glow") and float(w.get("size", 0) or 0) >= BIG_HERO_PX
                if strong or big_glow:
                    t.add(round(float(w.get("at", e.get("start", 0.0))), 3))
        elif mode in LAYOUT_MODES:
            if e.get("start") is not None:
                t.add(round(float(e["start"]), 3))
            if e.get("end") is not None:
                t.add(round(float(e["end"]), 3))
    for w in plan.get("whips") or []:
        if w.get("at") is not None:
            t.add(round(w["at"] / fps, 3))
    return sorted(t)


# ---------------------------------------------------------------------------------------------
# punch insertion -- pure, no I/O, unit-testable on a synthetic plan/cuts pair
# ---------------------------------------------------------------------------------------------

def _make_punch(t, timeline_after, track, fps=FPS):
    """One punch object. Always scale PUNCH_SCALE (1.12; 1.08 read as a nudge) -- "alternating in/out"
    describes what CONSECUTIVE punches look like once each one eases back to 1.0 after its own
    `hold` ("eases back to 1 over 6 frames"), not a literal instruction to emit a
    separate scale:1.0 entry here; a run of standalone punches already breathes in/hold/out/
    in/hold/out on its own once the render applies that per-punch ease-back."""
    at = int(round(t * fps))
    nxt = next((e for e in timeline_after if e > t + 1e-6), None)
    hold = PUNCH_HOLD_DEFAULT if nxt is None else int(round((nxt - t) * fps))
    hold = max(PUNCH_HOLD_MIN, min(PUNCH_HOLD_DEFAULT, hold))
    f = facetrack.at(track, t) if track else None
    cx = f["cx"] if f else 540
    cy = f["cy"] if f else 960
    return {"at": at, "scale": PUNCH_SCALE, "hold": hold, "cx": cx, "cy": cy}


def insert_punches(plan, cuts, track, max_gap=MAX_GAP_DEFAULT, fps=FPS, extra_at=(),
                    guard_frames=PUNCH_GUARD_FRAMES):
    """Returns (punches[], quiet[[t0,t1]...]). `extra_at` are operator-requested times
    (device_overrides.punch_add) -- inserted first, exempt from the cut/whip guard below, then the
    automatic gap-filling pass runs on top of them."""
    end_t = (plan.get("durationInFrames") or 0) / fps
    base = _structural_timeline(plan, cuts, fps)
    onsets = cluster_onsets(plan)
    forbid = sorted(set(round(float(c), 3) for c in (cuts or []))
                     | {round(w["at"] / fps, 3) for w in (plan.get("whips") or [])}
                     | {0.0, end_t})

    def blocked(t, dynamic):
        return (any(abs(t - f) * fps < guard_frames for f in forbid)
                or any(abs(t - f) * fps < guard_frames for f in dynamic))

    punch_times = [round(float(t), 3) for t in extra_at if 0.0 <= t <= end_t]
    changed, passes = True, 0
    while changed and passes < 64:
        changed = False
        passes += 1
        timeline = sorted(set(x for x in base + punch_times if 0.0 <= x <= end_t))
        pts = sorted(set([0.0] + timeline + [end_t]))
        for t0, t1 in zip(pts, pts[1:]):
            if t1 - t0 <= max_gap + 1e-9:
                continue
            cand = next((c for c in onsets if t0 < c < t1 and not blocked(c, punch_times)), None)
            if cand is None:
                fb = round(t0 + FALLBACK_OFFSET, 3)
                if t0 < fb < t1 and not blocked(fb, punch_times):
                    cand = fb
            if cand is not None:
                punch_times.append(cand)
                changed = True
                break   # the timeline just changed under us -- restart the scan
    timeline = sorted(set(x for x in base + punch_times if 0.0 <= x <= end_t))
    pts = sorted(set([0.0] + timeline + [end_t]))
    quiet = [[round(t0, 2), round(t1, 2)] for t0, t1 in zip(pts, pts[1:]) if t1 - t0 > max_gap + 1e-9]
    punches = []
    for t in sorted(punch_times):
        later = [x for x in timeline if x > t + 1e-6]
        punches.append(_make_punch(t, later, track, fps))
    return punches, quiet


def pace_numbers(plan, cuts, punches, fps=FPS):
    end_t = (plan.get("durationInFrames") or 0) / fps
    # Report what we can vouch for: the STRUCTURAL changes (cuts, strong devices, layout events, whips)
    # plus the punches -- not every caption swap. The rendered reel is judged empirically by G10.
    timeline = sorted(set(t for t in _structural_timeline(plan, cuts, fps) + [p["at"] / fps for p in punches]
                          if 0.0 <= t <= end_t))
    pts = sorted(set([0.0] + timeline + [end_t]))
    gaps = np.diff(pts) if len(pts) > 1 else np.array([end_t])
    if not len(gaps):
        gaps = np.array([0.0])
    return {"median_gap": round(float(np.median(gaps)), 2), "p90_gap": round(float(np.percentile(gaps, 90)), 2),
            "max_gap": round(float(gaps.max()), 2), "punches_added": len(punches)}


# ---------------------------------------------------------------------------------------------
# overrides + CLI
# ---------------------------------------------------------------------------------------------

def _spec_overrides(plan_path):
    """spec.json lives next to plan.json in every take-dir this pipeline ever builds -- looked up
    by convention, not a CLI arg, the same co-located-optional-file pattern capgate.py's own
    devices-propose.json lookup already uses. Absent/unreadable spec.json is not an error here:
    cadence.py must still work standalone (and in tests) against a bare plan.json."""
    spec_path = os.path.join(os.path.dirname(os.path.abspath(plan_path)), "spec.json")
    if not os.path.exists(spec_path):
        return {}
    try:
        return json.load(open(spec_path)).get("device_overrides") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def run(mez_path, plan_path, track_path, out_path, cuts_path=None, max_gap=MAX_GAP_DEFAULT, auto=True):
    plan = json.load(open(plan_path))
    track = json.load(open(track_path)) if track_path and os.path.exists(track_path) else None
    if cuts_path is None:
        cuts_path = os.path.join(os.path.dirname(os.path.abspath(plan_path)), "cuts.json")
    cuts = cuts_for(mez_path, cuts_path)

    ov = _spec_overrides(plan_path)
    punch_on = bool(ov.get("punch", auto))
    punch_add = [float(t) for t in (ov.get("punch_add") or [])]
    punch_off = [float(t) for t in (ov.get("punch_off") or [])]

    if punch_on:
        punches, quiet = insert_punches(plan, cuts, track, max_gap=max_gap, extra_at=punch_add)
    else:
        # punch: off -- no AUTOMATIC gap-filling, but an explicit punch_add is still honored, and
        # the quiet report still runs (against the un-punched timeline) so a human still sees the
        # gaps even though nothing was inserted for them.
        end_t = (plan.get("durationInFrames") or 0) / FPS
        base = _structural_timeline(plan, cuts, FPS)
        add_times = sorted(round(float(t), 3) for t in punch_add if 0.0 <= t <= end_t)
        timeline = sorted(set(base + add_times))
        pts = sorted(set([0.0] + timeline + [end_t]))
        quiet = [[round(t0, 2), round(t1, 2)] for t0, t1 in zip(pts, pts[1:]) if t1 - t0 > max_gap + 1e-9]
        punches = [_make_punch(t, [x for x in timeline if x > t + 1e-6], track) for t in add_times]

    if punch_off:
        punches = [p for p in punches if not any(abs(p["at"] / FPS - t) <= 1.0 for t in punch_off)]

    plan["punches"] = punches
    plan["pace"] = {**pace_numbers(plan, cuts, punches), "quiet": quiet}
    json.dump(plan, open(out_path, "w"), indent=1)
    return plan


def _main():
    a = sys.argv[1:]
    cuts_path = a[a.index("--cuts") + 1] if "--cuts" in a else None
    max_gap = float(a[a.index("--max-gap") + 1]) if "--max-gap" in a else MAX_GAP_DEFAULT
    auto = True
    if "--auto" in a:
        auto = a[a.index("--auto") + 1].lower() not in ("off", "no", "false", "0")
    pos, skip = [], False
    for x in a:
        if skip:
            skip = False
            continue
        if x in ("--cuts", "--max-gap", "--auto"):
            skip = True
            continue
        pos.append(x)
    mez_path, plan_path, track_path, out_path = pos[0], pos[1], pos[2], pos[3]
    plan = run(mez_path, plan_path, track_path, out_path, cuts_path=cuts_path, max_gap=max_gap, auto=auto)
    p = plan["pace"]
    print(f"cadence: median {p['median_gap']}s, p90 {p['p90_gap']}s, max {p['max_gap']}s, "
          f"{p['punches_added']} punch(es) added, {len(p['quiet'])} quiet stretch(es)")


if __name__ == "__main__":
    cli(_main, "cadence.py <mez.mp4> <plan.json> <facetrack.json> <out.plan.json> [--cuts cuts.json] [--max-gap 3.0] [--auto on|off]")
