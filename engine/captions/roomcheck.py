#!/usr/bin/env python3
"""roomcheck.py <facetrack.json> <out.tier.json> [tier_max]
Room above the CROWN and below the chin inside the safe text box decides the caption tier
(spec 7): FULL (behind + slams + lockups + running), REDUCED (slams + running, no behind),
MINIMAL (small running words in the lowest safe band, or none).

The behind layer lives ABOVE the head, so it is gated on the room above ALONE: a chin at the safe-box
floor must not veto behind text on a framing that has the room above the head. Room below the chin only decides where the
running words and the slams sit — below when there is room, otherwise above, where the planner
already makes them yield to the hero windows in time.

`above` is measured from the CROWN, not from Apple Vision's face box, which starts at the BROW:
scalp above the face box counted as free room and a word placed there landed on the head (D2)."""
import json, sys
from keys import cli, EngineStop
SAFE = {"x0": 65, "x1": 940, "y0": 270, "y1": 1420}
ORDER = ["MINIMAL", "REDUCED", "FULL"]
# single-sourced: captions_plan.py reads THIS constant (roomcheck.CROWN_H) rather than keeping its own copy.
CROWN_H = 0.30   # measured on talking-head takes: real crown 0.18-0.25·h above the face box top; 0.30 keeps ~30 px margin
BEHIND_ROOM = 420  # px above the crown a hero word needs
def room(box):
    top, bottom = box["cy"] - box["h"] * (0.5 + CROWN_H), box["cy"] + box["h"] / 2
    left, right = box["cx"] - box["w"] / 2, box["cx"] + box["w"] / 2
    return {"above": int(top - SAFE["y0"]), "below": int(SAFE["y1"] - bottom), "left": int(left - SAFE["x0"]), "right": int(SAFE["x1"] - right)}
def tier_for(track, tier_max="FULL"):
    m = track["median"]
    if m.get("fallback") or not track.get("samples"):
        t, reason, r = "MINIMAL", "no face found; captions stay in the lowest safe band", {"above": 0, "below": 0, "left": 0, "right": 0}
    else:
        r = room(m)
        if r["above"] >= BEHIND_ROOM: t, reason = "FULL", f"{r['above']}px above the crown (behind text fits), {r['below']}px below the chin"
        elif r["below"] >= 260 or r["above"] >= 300: t, reason = "REDUCED", f"only {r['above']}px above the crown (behind text needs {BEHIND_ROOM}px): no behind text"
        else: t, reason = "MINIMAL", f"tight framing (above {r['above']}px, below {r['below']}px)"
    if ORDER.index(t) > ORDER.index(tier_max): t, reason = tier_max, reason + f"; capped at {tier_max} by the brand profile"
    return {"tier": t, "reason": reason, "room": r, "safe": SAFE}
def _main():
    track = json.load(open(sys.argv[1])); tm = sys.argv[3] if len(sys.argv) > 3 else "FULL"
    if tm.upper() not in ORDER: raise EngineStop(f"unknown tier_max {tm!r} (one of {', '.join(ORDER)})")
    t = tier_for(track, tm.upper()); json.dump(t, open(sys.argv[2], "w"), indent=1); print(t["tier"], "—", t["reason"])
if __name__ == "__main__":
    cli(_main, "roomcheck.py <facetrack.json> <out.tier.json> [MINIMAL|REDUCED|FULL]")
