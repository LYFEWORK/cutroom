#!/usr/bin/env python3
"""legible.py <take-dir>: the soft build's legibility pass (the README, "The soft build", Legibility). The soft captions have no
outline, so after the cadence pass and before the gate this reads the picture under every soft thought of plan.json and acts in the design's
order. Under BRIGHT, nothing. Up to VERY_BRIGHT, the deep shadow: the event's shadow is "deep" (softthought.tsx draws both shadow layers at 1.5
times their opacity). From VERY_BRIGHT, the deep shadow and the next zone down: the thought moves to the lowest legal spot inside the QC safe box
(its block's bottom on the safe box's) and is read again there; still very bright, the plan gains the flag legibility:<its first arrival>,
which GATE.md prints as a CHECK line. A thought already at the lowest spot does not move, and its own read decides the flag.

The box is the thought's lines as the renderer lays them out: each line its drawn width (cluster_plan.soft_drawn: the brand's display font at the
line's size and tracking, its mark and spread counted) about 540 plus its dx, the lines stacked down from y at their sizes with the soft gap
between two. A payoff on the bar sits on an opaque block of the accent, so the bar's line is left out: the lines above it and the lines below it
are read as two boxes, the brighter deciding. The read is the 90th percentile luma under a box across the thought's life on screen, start to end
(softbuild/legibility.py box_luma, on mez.mp4). The caption stays put while a punch-in zooms the picture, so the box is mapped through the inverse
of each punch the cadence pass wrote into plan.json, timed as the renderer's punchAt times it (its numbers read out of CaptionsDynamic.tsx): a
life that a punch starts or ends in is read in pieces, each through its own zoom, and the brightest piece decides. A thought whose picture cannot
be read is flagged the same way, for a person to look at, with the reason in its record; ffmpeg missing, or no mezzanine, stops the stage instead.

plan.json gains "legible": {"thoughts": one record a soft thought,
{"at": its first arrival, "luma", "remedy"} plus "moved_to", "luma_moved" and "remedy_moved" for a thought that moved, or "error" for one that
could not be read}. run_captions.sh's check reads it: a plan cluster_plan.py makes again has none, so the stage runs again.
GATE.md words its CHECK lines from it. A plan with no soft thought is left exactly as it is."""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cluster_plan
from softbuild import legibility, look as soft_look
from keys import cli, EngineStop

FPS = 30
FRAMES = 6                 # frames read across a thought's life, and across each piece of one a punch-in cuts (fewer in a piece under 6 frames)
PUNCH_NUMBERS = ("PUNCH_IN", "PUNCH_HOLD_MAX", "PUNCH_SCALE")   # the renderer's punch timings, read out of CaptionsDynamic.tsx (cluster_plan.renderer_numbers)


def text_boxes(e, measure):
    """[(x0, y0, x1, y1)] in canvas px of a soft thought's lines as the renderer lays them out (module notes): one box for each run of lines that
    are not on the bar, so a bar's line between a lead-in and a tail leaves two (softbuild legibility.lockup_boxes, the feed's layout too)."""
    return legibility.lockup_boxes([(540 + r.get("dx", 0), cluster_plan.soft_drawn(measure, r, r["size"]), r["size"], bool(r.get("bar")))
                                    for r in e["words"]], e["y"], soft_look.LINE_GAP)


def zooms(punches, fps=FPS):
    """[(t0, t1, punch)]: when each punch-in holds the picture zoomed, from its snap to the end of its hold, timed as the renderer's punchAt times it
    (its ease back to 1 counts as unzoomed): a punch with no frames of its own snaps in over PUNCH_IN, and one with no hold holds up to the next
    punch, at most PUNCH_HOLD_MAX frames."""
    n = cluster_plan.renderer_numbers(names=PUNCH_NUMBERS)
    ordered = sorted(punches or [], key=lambda p: p.get("at", 0))
    out = []
    for i, p in enumerate(ordered):
        at = p.get("at", 0)
        snap = max(1, p.get("frames", n["PUNCH_IN"]))
        hold = p.get("hold", min(n["PUNCH_HOLD_MAX"], ordered[i + 1].get("at", 0) - (at + snap)) if i + 1 < len(ordered) else n["PUNCH_HOLD_MAX"])
        out.append((at / fps, (at + snap + max(0, hold)) / fps, p))
    return out


def unzoom(box, punch):
    """The source box a punch-in puts under this screen box. The zoom is CSS scale(s) about the origin (cx, cy), so the screen point q shows the source
    point c + (q - c) / s; no punch, the box itself."""
    if not punch:
        return box
    s = float(punch.get("scale") or cluster_plan.renderer_numbers(names=PUNCH_NUMBERS)["PUNCH_SCALE"])
    cx, cy = float(punch.get("cx", 540)), float(punch.get("cy", 960))
    x0, y0, x1, y1 = box
    return (cx + (x0 - cx) / s, cy + (y0 - cy) / s, cx + (x1 - cx) / s, cy + (y1 - cy) / s)


def read(video, boxes, t0, t1, spans):
    """The 90th percentile luma under screen boxes from t0 to t1: the brightest box and piece (softbuild legibility.pieces, cut where a punch-in starts
    or ends, the latest to start zooming each), each piece read through its own zoom."""
    return legibility.brightest(video, [(unzoom(box, punch), a, b) for a, b, punch in legibility.pieces(t0, t1, spans) for box in boxes], FRAMES, FPS)


def judge(e, video, measure, spans):
    """Reads the picture under one soft thought and acts on it in place (module notes). -> (its record, whether it is flagged)."""
    rec = {"at": round(e["start"], 2)}
    try:
        luma = read(video, text_boxes(e, measure), e["start"], e["end"], spans)
        rec.update(luma=round(luma, 1), remedy=legibility.remedy(luma))
        if rec["remedy"] == "ok":
            return rec, False
        e["shadow"] = "deep"
        lowest = cluster_plan.SAFE["y1"] - cluster_plan.soft_block([r["size"] for r in e["words"]])
        if rec["remedy"] == "deeper" or lowest <= e["y"]:
            return rec, rec["remedy"] == "move"
        e["y"] = lowest
        luma = read(video, text_boxes(e, measure), e["start"], e["end"], spans)
        rec.update(moved_to=lowest, luma_moved=round(luma, 1), remedy_moved=legibility.remedy(luma))
        return rec, rec["remedy_moved"] == "move"
    except (OSError, ValueError) as err:
        rec["error"] = str(err).removeprefix("legibility: ")
        return rec, True


def run(take):
    """Reads every soft thought of <take>/plan.json and writes the plan back with what it found (module notes). Returns the plan."""
    path = os.path.join(take, "plan.json")
    with open(path) as f:
        plan = json.load(f)
    events = [e for e in plan.get("events") or [] if e.get("soft")]
    if not events:
        print("legible: no soft build thoughts in plan.json, nothing to read")
        return plan
    if not shutil.which("ffmpeg"):
        raise EngineStop("legible: ffmpeg is not installed")
    video = os.path.join(take, "mez.mp4")
    if not os.path.isfile(video):
        raise EngineStop(f"legible: no mezzanine at {video}; run the conform stage first")
    with open(os.path.join(take, "spec.json")) as f:
        measure = cluster_plan.soft_measure(json.load(f))
    spans, records = zooms(plan.get("punches")), []
    flags = [f for f in plan.get("flags") or [] if not str(f).startswith("legibility:")]
    for e in events:
        rec, flagged = judge(e, video, measure, spans)
        records.append(rec)
        if flagged:
            flags.append(f"legibility:{e['start']:.2f}")
    plan["flags"], plan["legible"] = flags, {"thoughts": records}
    with open(path, "w") as f:
        json.dump(plan, f, indent=1)
    print(f"legible: {len(events)} thought(s) read: {sum('shadow' in e for e in events)} with the deep shadow, {sum('moved_to' in r for r in records)} "
          f"moved down, {sum(str(f).startswith('legibility:') for f in flags)} flagged for a person to look at")
    return plan


def _main():
    run(os.path.abspath(sys.argv[1]))


if __name__ == "__main__":
    cli(_main, "legible.py <take-dir>")
