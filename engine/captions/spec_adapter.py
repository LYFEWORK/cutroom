#!/usr/bin/env python3
"""spec_adapter.py <tier.json> <look.json> <out.spec.json> [--behind behind.json] [--gate gate.json]

Translates roomcheck.py's tier.json + lookprofile.py's look.json (your look: the DNA's captions profile, with the take's style over it) + the
operator's gate.json (when there is one) into the spec.json cluster_plan.py reads via --spec: accent, zone, width,
top_y0, hero_max, row_px, chin_gap, font{family, faces, measure}, no_behind, case, the device switches, and a "look"
block of visual passthroughs. spec.json is a pure function of those three files: nothing else writes it, so it can
always be rebuilt.

There are no defaults in this file. A look.json that lacks a layout key stops with a plain line naming it; lookprofile.py
(the DNA's profile with the neutral defaults under it) always carries them.

The room check overrides the brand's own zone preference, never the other way round (tier_to_zone, unit-tested on
its own, all three tiers x two brand zones):
  MINIMAL -> zone forced to "chest", hero_max capped at 0.7x, no_behind: true
  REDUCED -> zone forced to "chest" (no_behind is NOT set here, only MINIMAL disables behind text outright)
  FULL    -> the brand's own zone
An operator's gate.json overrides.tier replaces the room check's tier, and overrides.zone replaces the zone
that tier would give (an explicit human choice at the gate beats the brand default and the room check).

--behind (optional) points at a take-specific hand-annotated hero-moment list, the shape of cluster_plan.py's
spec["behind"]. Passed through untouched.

Devices (README, "Devices"): hero_glow, places, neg_words, count_up and icon_words
sit at the top level of the spec because cluster_plan.py matches the transcript against them (pill_icons, the ordered
rows that choose a frame pill's icon, sits there too for devices.py); hero_tint, glow_px,
chip_neg, cta_wash, page_bg, page_ink, display_font, display_weight, pill_bg, pill_ink, whip_frames and whip_amount ride
in "look" (only when your look sets them). A device switch your look leaves out is off.

The soft build (README, "The soft build"): soft_build rides in "look" like the fields above, and only a spec with it on
also carries on_accent (the colour of text drawn on the accent, white when the profile has none) and font_variable (whether the display
font is one variable file: glyph.font_is_variable, asked of the display file the profile names). cluster_plan.py plans thoughts with
them; every other spec.json is what it was.

Layout events the gate accepted (a cta, page, frame or igpost line in GATE.md): their [start, end] spans ride in
"layout_spans", so cluster_plan.py plans the words after an event as a cluster of their own instead of leaving them in a
cluster that devices.py apply() cuts off where the event starts.

The gate's device_overrides pass through whole, among them "thoughts": the lines of GATE.md's Thoughts section a person
edited, which cluster_plan.py's soft_events draws instead of its own split (their [bar] is the bar).

Caption styles (styles.py, Brand/styles/<id>.json): a style's layout, motion, colour and font fields are already laid over your look when lookprofile.py
writes look.json (your look first, then the style's `set` and `look`; names and taught words never move), so this file reads them like any look field and has
nothing to apply. It only carries the style's id into spec.json as "caption_style" (cluster_plan.py flags the plan with it), and only when look.json has one,
the take's pick or your default style: a take made with no style gets the spec your look makes. When GATE.md asks for a style that look.json was not made
with, the spec stops with one plain line rather than drop the request.

"glyph" ({caps, word, hook}, px per pt per char) is what CaptionsDynamic caps a row's size with where the planner has no
measurement of its own (the renderer's width cap on every row, and the CTA and page words, which devices.py does not
measure). It is measured on your own font files by glyph.glyph_factors, and is written only when it differs from the renderer's own
numbers, so a look whose face matches them has no such key.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from keys import cli, EngineStop  # noqa: E402
import glyph  # noqa: E402

TIERS = ("MINIMAL", "REDUCED", "FULL")
CASES = ("upper", "mixed", "title")
LAYOUT_KEYS = ("accent", "case", "zone", "width", "top_y0", "hero_max", "row_px", "chin_gap", "font_family", "font_faces", "font_abs")
LOOK_KEYS = ("base", "hero_base", "chip", "hero_shadow", "text_opacity", "text_soften", "hero_stroke", "whip_frames",
             "whip_amount", "hero_tint", "glow_px", "chip_neg", "cta_wash", "page_bg", "page_ink", "display_weight",
             "pill_bg", "pill_ink", "soft_build")


def tier_to_zone(tier, brand_zone):
    """The room-check tier's zone override. Pure, no I/O.

    MINIMAL and REDUCED both mean roomcheck.py found too little room above the crown for hero text
    (roomcheck.BEHIND_ROOM), so neither can honour a brand's "top" zone preference. FULL defers to the brand."""
    tier = (tier or "").upper()
    if tier not in TIERS:
        raise EngineStop(f"spec_adapter: unknown tier {tier!r} (one of {', '.join(TIERS)})")
    if tier in ("MINIMAL", "REDUCED"):
        return "chest"
    return brand_zone


def glyph_factors(look):
    """Your look's measured width factors for the renderer's cap, or None when they are the renderer's own defaults. The measuring is glyph.glyph_factors."""
    got = glyph.glyph_factors(look)
    default = {"caps": glyph.GLYPH_CAPS, "word": glyph.GLYPH_RUN, "hook": glyph.GLYPH_RUN}
    return None if got == default else got


def font_variable(look):
    """Whether your display font is one variable file (glyph.font_is_variable), asked of the file the profile measures the display face with (its weight
    800 file, else the first). The soft build draws a variable font's lines at their own weights and a static font's at its one weight. A look with no font file
    on disk is one plain EngineStop line."""
    files = look.get("font_abs") or {}
    path = files.get("800") or next(iter(files.values()), None)
    if not path or not os.path.isfile(path):
        raise EngineStop("spec_adapter: look.json names no display font file on disk to tell whether it is variable; regenerate it with lookprofile.py")
    return glyph.font_is_variable(path)


def _face(family, faces, measure):
    if not family or not faces:
        raise EngineStop("spec_adapter: look.json names a font with no family or no files; regenerate it with lookprofile.py")
    return {"family": family, "faces": faces, "measure": measure}


def build(tier_doc, brand, behind=None, gate=None):
    """tier_doc = roomcheck.py's output; brand = lookprofile.py's output (your look); behind = the optional hero-moment list;
    gate = gate.json's content or None. Returns the spec dict. Raises EngineStop on a malformed tier, a look
    that lacks a layout key, or an out-of-range case."""
    ov = ((gate or {}).get("overrides") or {})
    tier = (ov.get("tier") or tier_doc.get("tier") or "").upper()
    if tier not in TIERS:
        raise EngineStop(f"spec_adapter: tier.json has no valid tier (got {tier_doc.get('tier')!r}, want one of {', '.join(TIERS)})")
    gone = [k for k in LAYOUT_KEYS if brand.get(k) in (None, "", [])]
    if gone:
        raise EngineStop(f"spec_adapter: look.json is missing {', '.join(gone)}; regenerate it with lookprofile.py")
    case = brand["case"]
    if case not in CASES:
        raise EngineStop(f"spec_adapter: the look's case {case!r} is not one of {', '.join(CASES)}")

    zone = ov.get("zone") or tier_to_zone(tier, brand["zone"])
    # MINIMAL: no usable space above the crown at all. Cap the hero size (0.7x) and drop every above-the-head cluster.
    hero_max = brand["hero_max"]
    no_behind = tier == "MINIMAL"
    if no_behind:
        hero_max = int(round(hero_max * 0.7))

    measure_display = brand["font_abs"].get("800") or next(iter(brand["font_abs"].values()))
    spec = {
        "accent": brand["accent"],
        "zone": zone,
        "tier": tier,
        "width": brand["width"],
        "top_y0": brand["top_y0"],
        "hero_max": hero_max,
        "row_px": brand["row_px"],
        "chin_gap": brand["chin_gap"],
        "font": _face(brand["font_family"], brand["font_faces"], measure_display),
        "no_behind": no_behind,
        "case": case,
        "hero_glow": bool(brand.get("hero_glow", False)),
        "places": list(brand.get("places") or []),
        "neg_words": list(brand.get("neg_words") or []),
        "count_up": bool(brand.get("count_up", False)),
        "icon_words": list(brand.get("icon_words") or []),
        "pill_icons": list(brand.get("pill_icons") or []),
        "look": {k: brand[k] for k in LOOK_KEYS if brand.get(k) not in (None, "", [])},
    }
    factors = glyph_factors(brand)
    if factors:
        spec["glyph"] = factors
    look = spec["look"]
    wf = brand.get("word_font")
    if wf:
        look["word_font"] = _face(wf.get("family"), wf.get("faces"), (wf.get("abs") or {}).get("800") or next(iter((wf.get("abs") or {}).values()), None))
    df = brand.get("display_font")
    if df and df.get("family") and df["family"] != brand["font_family"]:
        look["display_font"] = _face(df["family"], df.get("faces"), None)
    if look.get("soft_build"):
        look["on_accent"] = brand.get("on_accent") or "#FFFFFF"
        look["font_variable"] = font_variable(brand)
    if ov.get("zone") or ov.get("tier"):
        spec["zone_reason"] = "operator override via GATE.md"
    dev = (ov.get("devices") or {}).get("device_overrides") or {}
    if dev:
        spec["device_overrides"] = dev
    spans = [[e["start"], e["end"]] for e in (ov.get("devices") or {}).get("layout_events") or [] if "start" in e and "end" in e]
    if spans:
        spec["layout_spans"] = spans
    if ov.get("finale"):
        spec["finale"] = ov["finale"]
    if behind is not None:
        spec["behind"] = behind
    style, asked = brand.get("caption_style"), ov.get("style")
    if asked and asked != style:
        raise EngineStop(f"spec_adapter: GATE.md asks for caption style {asked} but look.json was made with {style or 'none'}; "
                         f"run lookprofile.py with --style {asked} (run_captions.sh does)")
    if style:
        spec["caption_style"] = style
    return spec


def _main():
    a = sys.argv[1:]
    behind_path = gate_path = None
    for flag in ("--behind", "--gate"):
        if flag in a:
            i = a.index(flag)
            val = a[i + 1]
            del a[i:i + 2]
            if flag == "--behind":
                behind_path = val
            else:
                gate_path = val
    tier_path, brand_path, out_path = a[0], a[1], a[2]
    with open(tier_path) as f:
        tier_doc = json.load(f)
    with open(brand_path) as f:
        brand = json.load(f)
    behind = json.load(open(behind_path)) if behind_path else None
    gate = json.load(open(gate_path)) if gate_path else None
    spec = build(tier_doc, brand, behind, gate)
    with open(out_path, "w") as f:
        json.dump(spec, f, indent=1)
    print(spec["zone"], "zone,", ("no_behind" if spec["no_behind"] else "behind ok") + ",", spec["case"], "case")


if __name__ == "__main__":
    cli(_main, "spec_adapter.py <tier.json> <look.json> <out.spec.json> [--behind behind.json] [--gate gate.json]")
