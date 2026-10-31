"""cluster_plan.py: measuring with the look's own font, holding rows inside the QC safe box, the running face on non-hero rows, hero choice (weak words,
GATE.md hero:/no-hero:), whip settings, the renderer's row-size cap, the optional platform marks, and the absence of any default look."""
import json
import math
import os
import re
import subprocess
import sys

import pytest
from PIL import ImageFont

import cap_helpers
import cluster_plan
import roomcheck
from keys import EngineStop

SCRIPT = os.path.join(cap_helpers.REPO, "engine", "captions", "cluster_plan.py")
TRACKING = 0.96   # the planner's -0.04 em letter spacing, applied to every measured width
TRACK = {"samples": {str(s): {"cx": 540, "cy": 700, "w": 300, "h": 300} for s in range(0, 40)},
         "median": {"cx": 540, "cy": 700, "w": 300, "h": 300}}


def W(text, start, end):
    return {"text": text, "start": start, "end": end, "filler": False}


def seq(*texts, start=0.0, step=0.4):
    return [W(t, round(start + i * step, 3), round(start + i * step + 0.35, 3)) for i, t in enumerate(texts)]


def plan_for(words, brand_id="upstatic", tier="REDUCED", **over):
    spec = cap_helpers.spec(brand_id, tier=tier, **over)
    cut = words[-1]["end"] + 0.3
    return cluster_plan.build(words, TRACK, spec, cut_s=cut, total_frames=int(cut * 30) + 1), spec


def rows_of(plan):
    return [(e, r) for e in plan["events"] if e["mode"] in ("front", "behind") for r in e["words"]]


def pillow_width(path, text, px, weight=None):
    """What Pillow measures for a font file, worked out without the planner: the weight goes on the weight axis of a variable font and the font's own default on
    every other axis, a static font is left alone; times the planner's tracking"""
    face = ImageFont.truetype(path, px)
    if weight is not None:
        face.set_variation_by_axes([weight if a["name"] == b"Weight" else a["default"] for a in face.get_variation_axes()])
    return face.getlength(text) * TRACKING


# --- measuring with the look's own font -----------------------------------------------------------------------------

@pytest.mark.parametrize("brand_id,face_file,axes", [("upstatic", "Inter-ExtraBold.ttf", None), ("varsans", "Inter-Variable.ttf", [14, 800])])
def test_width_is_measured_with_the_looks_own_font_file(brand_id, face_file, axes):
    s = cap_helpers.spec(brand_id)
    cluster_plan.set_measure(s["font"]["measure"])
    assert s["font"]["measure"].endswith(face_file)
    got = cluster_plan.width("CUSTOM CLOTHING", 100)
    want = ImageFont.truetype(s["font"]["measure"], 100)
    if axes:                                          # a variable face is set on every axis: optical size first, then weight
        want.set_variation_by_axes(axes)
    assert got == pytest.approx(want.getlength("CUSTOM CLOTHING") * TRACKING)


def test_each_look_measures_by_its_own_file_and_a_variable_font_follows_the_weight():
    static, variable = cap_helpers.spec("upstatic")["font"]["measure"], cap_helpers.spec("varsans")["font"]["measure"]
    cluster_plan.set_measure(variable)
    light, heavy = cluster_plan.width("Orders", 120, weight=400), cluster_plan.width("Orders", 120, weight=900)
    assert heavy > light                                                       # Inter is variable
    cluster_plan.set_measure(static)
    assert cluster_plan.width("Orders", 120, weight=900) == cluster_plan.width("Orders", 120, weight=400)   # a static face is what it is
    # the two fixture faces are one design (the static file is the variable one at weight 800), so the same words only part where the weight asked for does:
    # at 400 the look on the static face measures them wider than the look on the variable face does
    assert cluster_plan.width("Orders", 120, weight=400) > light
    assert cluster_plan.width("Orders", 120, weight=400) == pytest.approx(pillow_width(static, "Orders", 120))
    cluster_plan.set_measure(variable)
    assert cluster_plan.width("Orders", 120, weight=400) == light                 # pointing the measure back follows the look again


def test_the_running_face_is_measured_by_its_own_file_and_fits_bigger_where_it_is_narrower():
    s = cap_helpers.spec("upstatic")
    display, running = s["font"]["measure"], s["look"]["word_font"]["measure"]
    assert (os.path.basename(display), os.path.basename(running)) == ("Inter-ExtraBold.ttf", "Inter-Variable.ttf")
    cluster_plan.set_measure(display, running)
    text, thin = "than most people", 100
    # the running face is the variable one, so at a light weight it is narrower than the display face, which has the one weight
    want_run, want_display = pillow_width(running, text, 120, thin), pillow_width(display, text, 120)
    assert cluster_plan.width(text, 120, thin, face="running") == pytest.approx(want_run)
    assert cluster_plan.width(text, 120, thin, face="display") == pytest.approx(want_display)
    assert want_run < 0.95 * want_display
    assert cluster_plan.fit(text, 140, 900, thin, face="running") > cluster_plan.fit(text, 140, 900, thin, face="display")


def test_a_look_with_no_running_face_measures_every_row_with_the_display_face():
    cluster_plan.set_measure(cap_helpers.spec("varsans")["font"]["measure"], None)
    assert cluster_plan.width("hello there", 100, face="running") == cluster_plan.width("hello there", 100, face="display")


# --- a variable font is set on every axis, the weight on the weight axis ----------------------------------------------------

def test_the_weight_goes_on_the_weight_axis_of_a_font_whose_first_axis_is_optical_size(monkeypatch):
    variable, static = cap_helpers.font("varsans")["measure"], cap_helpers.font("upstatic")["measure"]
    axes = ImageFont.truetype(variable, 100).get_variation_axes()
    assert [(a["name"], a["minimum"], a["default"], a["maximum"]) for a in axes] == [(b"Optical size", 14, 14, 32), (b"Weight", 100, 400, 900)]
    monkeypatch.setattr(cluster_plan, "_fonts", {})                       # the cache must not hide what an axis does
    cluster_plan.set_measure(variable)
    text, px = "Orders", 120
    heavy, light = cluster_plan.width(text, px, 800), cluster_plan.width(text, px, 400)
    assert heavy > light
    want = ImageFont.truetype(variable, px)
    want.set_variation_by_axes([14, 800])
    assert heavy == pytest.approx(want.getlength(text) * TRACKING)
    want.set_variation_by_axes([14, 400])
    assert light == pytest.approx(want.getlength(text) * TRACKING)
    # a weight past the axis's range is held to it, and the optical size stays where the font has it whatever the weight is
    assert cluster_plan.width(text, px, 1000) == pytest.approx(cluster_plan.width(text, px, 900))
    assert cluster_plan.width(text, px, 50) == pytest.approx(cluster_plan.width(text, px, 100))
    cluster_plan.set_measure(static)                                      # a static font has no axis: the weight changes nothing
    assert cluster_plan.width(text, px, 800) == cluster_plan.width(text, px, 400) == cluster_plan.width(text, px, 100)


# --- no defaults: a spec without a look fails instead of rendering someone else's ---------------------------------------------

def test_build_without_an_accent_or_a_font_stops_instead_of_defaulting():
    words = seq("Hello", "there")
    accent = cap_helpers.profile("varsans")["accent"]
    with pytest.raises(EngineStop, match="accent"):
        cluster_plan.build(words, TRACK, {"font": cap_helpers.font("varsans")}, 1.0, 30)
    with pytest.raises(EngineStop, match="font"):
        cluster_plan.build(words, TRACK, {"accent": accent}, 1.0, 30)
    with pytest.raises(EngineStop, match="font"):
        cluster_plan.build(words, TRACK, {"accent": accent, "font": {"family": "X", "faces": [{"weight": "400", "src": "a.ttf"}]}}, 1.0, 30)


def test_the_plan_carries_the_looks_font_files_and_no_font_folder_or_default_family():
    plan, spec = plan_for(seq("Custom", "clothing", "fast"), "upstatic")
    assert plan["font"] == {"family": "InterStaticDyn", "faces": [{"weight": "100 900", "src": "brand-upstatic/fonts/Inter-ExtraBold.ttf"}]}
    assert plan["wordFont"]["family"] == "InterRunDyn" and plan["wordFont"]["faces"][0]["src"] == "brand-upstatic/fonts/Inter-Variable.ttf"
    assert "fontsDir" not in plan and "measure" not in json.dumps(plan)
    var, _ = plan_for(seq("Custom", "clothing", "fast"), "varsans")
    assert var["font"]["family"] == "InterDyn" and "wordFont" not in var
    # nothing of the other look, and no family or color the engine made up, is in either plan
    assert not {"InterStaticDyn", "InterRunDyn"} & set(re.findall(r"\w+Dyn", json.dumps(var)))
    assert spec["accent"].lower() not in json.dumps(var).lower() and var["accent"] == cap_helpers.profile("varsans")["accent"]


def test_display_weight_rides_into_the_plan():
    spec = cap_helpers.spec("upstatic")
    plan, _ = plan_for(seq("Custom", "clothing", "fast"), "upstatic", look={**spec["look"], "display_weight": 700})
    assert plan["displayWeight"] == 700


# --- the QC safe box --------------------------------------------------------------------------------------------------

def _row_edges(plan, spec, ev, row):
    face = "display" if row.get("hero") else "running"
    cluster_plan.set_measure(spec["font"]["measure"], (spec["look"].get("word_font") or {}).get("measure"))
    w = cluster_plan.width(row["text"], row["size"], 800, face)
    c = 540 + row["dx"]
    return c - w / 2, c + w / 2


def test_an_upper_case_looks_width_past_the_safe_box_no_longer_spills_past_it_in_the_chest_zone():
    """The G4 failure of the first sweep: a width wider than the safe box on an ALL CAPS look put the right end of a long row outside it."""
    words = seq("Where", "they", "stop", "complexity", "customization", "more", "complicated", "than", "most", "people", start=0.2)
    plan, spec = plan_for(words, "upstatic", tier="REDUCED")
    assert spec["zone"] == "chest" and spec["width"] > roomcheck.SAFE["x1"] - roomcheck.SAFE["x0"]
    assert plan["events"]
    for ev, row in rows_of(plan):
        left, right = _row_edges(plan, spec, ev, row)
        assert left >= roomcheck.SAFE["x0"] and right <= roomcheck.SAFE["x1"], (row["text"], row["size"], round(left), round(right))


def test_a_mixed_case_looks_rows_stay_inside_the_safe_box_too():
    words = seq("Ordering", "something", "that's", "gonna", "be", "a", "great", "idea", "for", "customers", start=0.2)
    plan, spec = plan_for(words, "varsans", tier="REDUCED")
    for ev, row in rows_of(plan):
        left, right = _row_edges(plan, spec, ev, row)
        assert left >= roomcheck.SAFE["x0"] and right <= roomcheck.SAFE["x1"], (row["text"], round(left), round(right))


def test_row_cap_is_the_room_to_the_nearer_edge_minus_the_margin():
    assert cluster_plan.row_cap(502, 65, 940) == 2 * 437 - 2 * cluster_plan.EDGE_MARGIN
    assert cluster_plan.row_cap(700, 65, 940) == 2 * 240 - 2 * cluster_plan.EDGE_MARGIN
    assert cluster_plan.row_cap(900, 65, 940) == 120                  # never below a readable floor
    assert cluster_plan.row_cap(5000, 65, 940) == 120


# --- the running face on non-hero rows ---------------------------------------------------------------------------------

def test_hero_rows_are_flagged_and_exactly_one_row_per_phrase_is_the_hero():
    plan, _ = plan_for(seq("send", "us", "their", "artwork", "today"), "varsans")
    for e in plan["events"]:
        if e["mode"] in ("front", "behind") and len(e["words"]) > 1:
            assert [r["hero"] for r in e["words"]].count(True) == 1
        for r in e["words"]:
            assert isinstance(r["hero"], bool)


def test_hand_specified_behind_rows_are_heroes():
    spec = cap_helpers.spec("upstatic", tier="FULL", zone="top")
    spec["behind"] = [{"match": "Big Idea", "rows": [["Big", 380, "#FFFFFF", -120], ["Idea", 340, "accent", 90]], "top": 200, "hold": 1.3}]
    plan = cluster_plan.build(seq("Big", "Idea", "makes", "sales"), TRACK, spec, 2.0, 60)
    behind = [e for e in plan["events"] if e["mode"] == "behind"]
    assert [[r["text"] for r in e["words"]] for e in behind] == [["BIG", "IDEA"]]
    assert all(r["hero"] for e in behind for r in e["words"])


# --- hero choice ------------------------------------------------------------------------------------------------------

def test_weak_words_are_not_picked_as_the_hero_when_a_real_word_is_there():
    assert cluster_plan.hero_index(seq("Ordering", "something", "that's", "gonna")) == 0          # was "something" (longest)
    assert cluster_plan.hero_index(seq("Well", "I", "mean", "you", "know")) in (2, 4)              # nothing strong: a weak word, but
    plan, _ = plan_for(seq("that's", "gonna", "be", "a"), "varsans")                               # an all-weak phrase has no hero at all
    (ev,) = plan["events"]
    assert len(ev["words"]) == 1 and ev["words"][0]["hero"] is False


def test_contractions_are_weak_as_a_class():
    assert cluster_plan.hero_index(seq("customers", "don't", "wouldn't")) == 0
    assert cluster_plan._weak("they're") and cluster_plan._weak("wouldn't") and cluster_plan._weak("it'll")
    assert not cluster_plan._weak("customers") and not cluster_plan._weak("embroidery")


def test_gate_hero_picks_the_word_and_no_hero_removes_it():
    words = seq("send", "us", "their", "artwork")
    base, _ = plan_for(words, "varsans")
    assert [r["text"] for _, r in rows_of(base) if r["hero"]] == ["artwork"]
    forced, _ = plan_for(words, "varsans", device_overrides={"hero_add": ["send"]})
    assert [r["text"] for _, r in rows_of(forced) if r["hero"]] == ["send"]
    dropped, _ = plan_for(words, "varsans", device_overrides={"hero_drop": ["artwork"]})
    assert "artwork" not in [r["text"] for _, r in rows_of(dropped) if r["hero"]]
    # a dropped word is still picked when it is the only word there is
    only, _ = plan_for(seq("artwork"), "varsans", device_overrides={"hero_drop": ["artwork"]})
    assert [r["text"] for _, r in rows_of(only)] == ["artwork"]


# --- whips ------------------------------------------------------------------------------------------------------------

def _two_clusters():
    return seq("send", "artwork", start=0.0) + seq("recreate", "design", start=2.5)


def test_whips_follow_the_looks_whip_settings():
    look = cap_helpers.spec("varsans")["look"]
    base, _ = plan_for(_two_clusters(), "varsans", look={**look, "whip_frames": 3, "whip_amount": 1.0})
    assert base["whips"] and all(w["frames"] == 3 and w["amount"] == 0.8 for w in base["whips"])
    soft, _ = plan_for(_two_clusters(), "varsans", look={**look, "whip_frames": 5, "whip_amount": 0.5})
    assert [w["frames"] for w in soft["whips"]] == [5] * len(soft["whips"]) and all(w["amount"] == 0.4 for w in soft["whips"])
    off, _ = plan_for(_two_clusters(), "varsans", look={**look, "whip_frames": 0})
    assert off["whips"] == []


# --- layout events: no cluster straddles an edge ---------------------------------------------------------------------

def _crossed(phrase_list, spans):
    """(first word, edge) of every phrase that holds words on both sides of an edge of a layout span"""
    cuts = sorted({t for s in spans for t in s})
    return [(p[0]["text"], c) for p in phrase_list for c in cuts if p[0]["start"] < c - 1e-6 and p[-1]["start"] >= c - 1e-6]


def test_phrases_around_without_spans_is_exactly_phrases():
    words = seq("We", "build", "custom", "shirts", "for", "local", "teams", "in", "Portland.")
    assert cluster_plan.phrases_around(words, None) == cluster_plan.phrases(words)
    assert cluster_plan.phrases_around(words, []) == cluster_plan.phrases(words)


def test_no_phrase_holds_words_from_both_sides_of_a_layout_event_edge():
    words = seq("Our", "Northwind", "team", "builds", "custom", "sites", "for", "local", "shops", start=1.0, step=0.4)
    spans = [[1.4, 2.2]]                                   # a page over "Northwind team", in the middle of what would be one phrase
    assert _crossed(cluster_plan.phrases(words), spans)    # planned whole, the first phrase straddles both edges
    split = cluster_plan.phrases_around(words, spans)
    assert not _crossed(split, spans)
    assert [w["text"] for p in split for w in p] == [w["text"] for w in words]          # nothing lost, nothing reordered
    assert [p[0]["text"] for p in split][:3] == ["Our", "Northwind", "builds"]            # a word that starts on an edge belongs after it


def test_no_merge_in_phrases_reaches_across_an_edge():
    """a lone stop word rides with the phrase before it, a comma continuation rides back: neither may cross an edge"""
    words = seq("Great", "shirts", "and", "prints", start=0.0, step=0.4)
    assert [[w["text"] for w in p] for p in cluster_plan.phrases(words)] == [["Great", "shirts", "and", "prints"]]
    split = cluster_plan.phrases_around(words, [[0.8, 1.2]])                             # an event over "and"
    assert [[w["text"] for w in p] for p in split] == [["Great", "shirts"], ["and"], ["prints"]]


def test_the_planner_gives_the_words_after_a_layout_event_a_cluster_of_their_own():
    words = [W("Our", 1.0, 1.15), W("Northwind", 1.2, 1.8), W("team", 1.95, 2.2), W("builds", 2.25, 2.6)]
    whole, _ = plan_for(words, "varsans", tier="FULL")
    assert [(e["start"], [r["text"] for r in e["words"]]) for e in whole["events"]] == [(1.0, ["Our", "Northwind", "team builds"])]
    plan, _ = plan_for(words, "varsans", tier="FULL", layout_spans=[[1.2, 1.9]])
    clusters = [(e["start"], [r["text"] for r in e["words"]]) for e in plan["events"] if e["mode"] in ("front", "behind")]
    assert clusters == [(1.0, ["Our"]), (1.2, ["Northwind"]), (1.95, ["team", "builds"])]


def test_the_plan_hands_the_renderer_the_looks_width_factors_only_when_the_spec_has_them():
    words = seq("send", "artwork", "today")
    for brand_id in cap_helpers.BRANDS:
        measured, measured_spec = plan_for(words, brand_id)
        assert measured["glyph"] == measured_spec["glyph"] and set(measured["glyph"]) == {"caps", "word", "hook"}
        bare = cap_helpers.spec(brand_id)
        del bare["glyph"]                                  # a face whose widths are the renderer's own has no such key
        cut = words[-1]["end"] + 0.3
        plan = cluster_plan.build(words, TRACK, bare, cut_s=cut, total_frames=int(cut * 30) + 1)
        assert "glyph" not in bare and "glyph" not in plan


# --- a face with no room for a caption block --------------------------------------------------------------------------

def _selfie(chin):
    """a face filling the top of the frame: no room above the crown, `chin` px from the top to the bottom of the face box"""
    box = {"cx": 540, "cy": chin - 550, "w": 800, "h": 1100}
    return {"samples": {str(s): dict(box) for s in range(0, 40)}, "median": dict(box)}


def _selfie_plan(chin, brand_id="varsans"):
    words = seq("We", "build", "custom", "websites", "for", "local", "shops")
    return cluster_plan.build(words, _selfie(chin), cap_helpers.spec(brand_id, tier="FULL"), cut_s=3.5, total_frames=105)


def test_a_tight_selfie_gets_one_row_under_the_chin_never_a_block_over_the_face():
    plan = _selfie_plan(1250)                                  # 170 px left under the chin, nothing above the head
    fronts = [e for e in plan["events"] if e["mode"] == "front"]
    assert fronts and not [f for f in plan["flags"] if f.startswith("no_caption_room")]
    for e in fronts:
        (row,) = e["words"]                                    # one row, carrying the whole phrase
        assert e["y"] >= 1250 and row["size"] >= cluster_plan.MIN_ROW
        assert e["y"] + row["size"] + 48 <= cluster_plan.SAFE["y1"]
    shown = " ".join(r["text"] for e in fronts for r in e["words"]).lower().split()
    assert shown == ["we", "build", "custom", "websites", "for", "local", "shops"]            # every word still has its caption


def test_a_quiet_glue_row_under_a_low_chin_stays_inside_the_safe_box_too():
    words = seq("to", "do", "so")                                  # all glue words: one quiet row, planned apart from the hero clusters
    plan = cluster_plan.build(words, _selfie(1216), cap_helpers.spec("varsans", tier="FULL"), cut_s=2.0, total_frames=60)
    (e,) = [e for e in plan["events"] if e["mode"] == "front"]
    (row,) = e["words"]
    assert e["y"] >= 1216 and row["size"] >= cluster_plan.MIN_ROW and e["y"] + row["size"] + 48 <= cluster_plan.SAFE["y1"]


def test_a_face_so_low_that_not_even_one_row_fits_is_flagged_for_the_gate():
    plan = _selfie_plan(1405)
    assert any(f.startswith("no_caption_room:") for f in plan["flags"])
    assert all(e["y"] >= 1405 for e in plan["events"] if e["mode"] == "front")                # still never above the chin line


# --- a lockup too tall for its room stays a lockup ---------------------------------------------------------------------
# A look with a big hero (250) and tall rows (112 to 132) draws a three-row lockup 484 px tall. On a reel with a face low in the frame (101 px under the chin,
# 446 above the crown) 15 of 52 phrases were taller than every room and fell back to ONE row carrying the whole phrase, which the renderer then drew at 45 px.

LOW_FACE = dict(zone="chest", width=860, hero_max=250, row_px=[112, 132], chin_gap=90)   # the layout the scenario was found with, pinned so an edit to a fixture look does not move it


def _face(cy, h):
    """a speaker who holds still: this face box in every second"""
    box = {"cx": 540, "cy": cy, "w": h, "h": h}
    return {"samples": {str(s): dict(box) for s in range(0, 40)}, "median": dict(box)}


def _fronts(track, text, brand_id="varsans", **over):
    words = seq(*text.split())
    cut = words[-1]["end"] + 0.3
    plan = cluster_plan.build(words, track, cap_helpers.spec(brand_id, tier="FULL", **{**LOW_FACE, **over}), cut_s=cut, total_frames=int(cut * 30) + 1)
    return [e for e in plan["events"] if e["mode"] == "front"]


def test_a_lockup_that_fits_nowhere_at_its_own_size_is_scaled_down_as_one_block_not_collapsed_to_one_row():
    (want,) = _fronts(_face(600, 300), "I can tell you")                  # a high face: the whole lockup fits under the chin at its own size
    (got,) = _fronts(_face(1020, 350), "I can tell you")                  # the low framing
    assert [r["text"] for r in want["words"]] == [r["text"] for r in got["words"]] == ["I can", "tell", "you"]       # the same three rows, not one row of four words
    sizes0, sizes = [r["size"] for r in want["words"]], [r["size"] for r in got["words"]]
    k = sizes[1] / sizes0[1]
    assert 0.6 <= k < 1.0                                                  # smaller, because the room is
    assert all(abs(b - a * k) <= 2 for a, b in zip(sizes0, sizes))         # the whole block, together: the hero keeps its lead over the other rows
    assert min(sizes) >= cluster_plan.MIN_ROW and [r["hero"] for r in got["words"]] == [False, True, False]
    assert [r["dx"] for r in got["words"]] != [r["dx"] for r in want["words"]] and abs(got["words"][0]["dx"] - cluster_plan.CL_DX) < abs(want["words"][0]["dx"] - cluster_plan.CL_DX)   # the stagger scales too
    crown = 1020 - 0.8 * 350
    assert got["y"] >= cluster_plan.SAFE["y0"] and got["y"] + cluster_plan.block_height(sizes) <= crown - 24 + 1    # above the head, in front, inside the safe box


def test_a_lockup_that_misses_the_room_under_the_chin_by_a_hair_stays_under_the_chin():
    """The old shrink scaled the whole block by room / height, but 48 px of it (the shadow) does not scale, so a lockup that needed 4 px more went on to the next lane and then to one row:
    this one is 8 px over the room under the chin after the old shrink, and 219 px is all there is above the head."""
    chin = 753 + 150
    (got,) = _fronts(_face(753, 300), "I can tell you")
    assert [r["text"] for r in got["words"]] == ["I can", "tell", "you"]
    sizes = [r["size"] for r in got["words"]]
    assert got["y"] >= chin and got["y"] + cluster_plan.block_height(sizes) <= cluster_plan.SAFE["y1"]
    assert sizes[1] >= 0.55 * 250                                           # the take's own lane, at the scale the planner always allowed it


def test_a_scaled_lockup_keeps_every_word_its_order_its_hero_and_the_hero_row_devices():
    """the one-row fallback built a fresh row, so the hero's glow (and any neg, count or strike tag) was dropped with the lockup; a scaled lockup is the same rows"""
    spec = cap_helpers.spec("varsans", tier="FULL", **LOW_FACE)
    (got,) = _fronts(_face(1020, 350), "before we pour more")
    assert [r["text"] for r in got["words"]] == ["before we", "pour", "more"]
    assert [r["at"] for r in got["words"]] == sorted(r["at"] for r in got["words"])
    hero = [r for r in got["words"] if r["hero"]]
    assert len(hero) == 1 and hero[0]["text"] == "pour" and hero[0]["size"] == max(r["size"] for r in got["words"])
    assert hero[0]["color"] == spec["accent"] and hero[0].get("glow") is True
    (neg,) = _fronts(_face(1020, 350), "we keep wasting money")             # a negative word keeps its chip
    assert [r["text"] for r in neg["words"]] == ["we keep", "wasting", "money"] and neg["words"][1].get("neg") is True


def test_one_row_phrases_and_a_frame_with_no_room_for_a_lockup_still_fall_back_to_one_row():
    (one,) = _fronts(_face(1020, 350), "business")
    assert len(one["words"]) == 1
    fronts = _fronts(_selfie(1250), "We build custom websites for local shops")
    assert fronts and all(len(e["words"]) == 1 for e in fronts)             # no lockup at MIN_ROW fits a face that fills the frame: the documented last resort


def test_every_phrase_of_a_real_framing_is_a_lockup_when_one_can_be_drawn():
    words = seq(*"I can tell you why your marketing is not working in 60 seconds but it's truly what happens after the click".split())
    cut = words[-1]["end"] + 0.3
    plan = cluster_plan.build(words, _face(1020, 350), cap_helpers.spec("varsans", tier="FULL", **LOW_FACE), cut_s=cut, total_frames=int(cut * 30) + 1)
    for e in plan["events"]:
        if e["mode"] == "front" and len(e["words"]) == 1 and e["words"][0].get("hero"):          # a lone hero row: a one-word phrase, never several words on one row
            assert " " not in e["words"][0]["text"], f"{e['words'][0]['text']!r} collapsed to one row"
    assert not [f for f in plan["flags"] if f.startswith("no_caption_room")]


# --- the renderer's row-size cap, read from the composition ---------------------------------------------------------------------------------

def test_the_planner_knows_the_renderers_short_hero_cap():
    """320 px on a 960 px budget and 286 on 860 at the renderer's own factor (4 letters of 0.75 em); a face measured at another caps factor has its own cap"""
    glyph = cap_helpers.spec("upstatic")["glyph"]
    narrow = {"caps": 0.6, "word": 0.5, "hook": 0.5}                                # a face narrower than the renderer's own: 960 / (4 x 0.6) and 860 / (4 x 0.6)
    assert [cluster_plan.short_hero_cap(w) for w in (960, 860)] == [320, 286]
    assert [cluster_plan.short_hero_cap(w, narrow) for w in (960, 860)] == [400, 358]
    assert [cluster_plan.short_hero_cap(w, glyph) for w in (960, 860)] == [math.floor(w / (4 * glyph["caps"])) for w in (960, 860)]
    assert cluster_plan.renderer_cap("ads", 960, hero=True) == 320                  # a short word is held to the cap
    assert cluster_plan.renderer_cap("responsibility", 960, hero=True) == 91        # a long one to its own width estimate (14 letters)
    assert cluster_plan.renderer_cap("austin", 960, glyph, hero=True) > cluster_plan.renderer_cap("austin", 960, glyph, hero=True, spaced=True)
    caps = [cluster_plan.renderer_cap("instagram", 960, hero=True, **tag) for tag in ({}, {"pin": True}, {"icon": True}, {"logo": True})]
    assert caps == sorted(caps, reverse=True) and len(set(caps)) == 4               # a mark widens the row by its own em: pin 0.9, icon 1.0, logo 1.1
    assert cluster_plan.renderer_cap("instagram", 960, hero=True, pin=True, logo=True, icon=True) == caps[1]    # a row carries one mark: pin first
    assert cluster_plan.row_drawn({"text": "ads", "size": 500, "hero": True}, 960) == 320
    assert cluster_plan.row_drawn({"text": "ads", "size": 200, "hero": True}, 960) == 200


def test_the_renderers_numbers_are_read_from_the_composition_and_never_copied_into_the_planner(tmp_path, monkeypatch):
    src = open(cluster_plan.COMPOSITION, encoding="utf-8").read()
    n = cluster_plan.renderer_numbers()
    assert set(n) == set(cluster_plan.RENDERER_NUMBERS)
    words = seq("It's", "not", "your", "ads")
    top = cap_helpers.spec("varsans", tier="FULL", zone="top", width=960, top_y0=200, hero_max=450, row_px=[76, 88], chin_gap=125)
    del top["glyph"]                                                                # no measured factors: the cap is the composition's own numbers

    def hero():
        plan = cluster_plan.build(words, _face(1000, 300), top, cut_s=2.0, total_frames=60)
        return max(r["size"] for e in plan["events"] for r in e["words"])

    assert hero() == 320                                                            # hero_max 450 is drawn at the cap, so it is planned at it
    other = tmp_path / "CaptionsDynamic.tsx"
    other.write_text(re.sub(r"(?m)^const FLOOR_LETTERS = [0-9.]+;", "const FLOOR_LETTERS = 5;", re.sub(r"(?m)^const GLYPH_CAPS = [0-9.]+;", "const GLYPH_CAPS = 0.5;", src)))
    monkeypatch.setattr(cluster_plan, "COMPOSITION", str(other))
    assert cluster_plan.short_hero_cap(960) == 384 and hero() == 384                # 960 / (5 x 0.5): the planner followed the composition, it holds no number of its own
    broken = tmp_path / "Broken.tsx"
    broken.write_text(re.sub(r"(?m)^const FLOOR_LETTERS = .*$", "", src))
    monkeypatch.setattr(cluster_plan, "COMPOSITION", str(broken))
    with pytest.raises(EngineStop, match="FLOOR_LETTERS"):
        cluster_plan.short_hero_cap(960)
    monkeypatch.setattr(cluster_plan, "COMPOSITION", str(tmp_path / "nope.tsx"))
    with pytest.raises(EngineStop, match="cannot read the composition"):
        cluster_plan.short_hero_cap(960)


def test_a_block_behind_the_head_hangs_from_the_head_by_the_height_that_renders():
    """hero_max 370 over the renderer's cap: the planner used to size the block at 370, so the smaller size that renders floated that much above where it was placed"""
    spec = cap_helpers.spec("varsans", tier="FULL", zone="top", width=960, top_y0=200, hero_max=370, row_px=[76, 88], chin_gap=125)
    cap = cluster_plan.short_hero_cap(spec["width"], spec["glyph"])
    assert cap < spec["hero_max"]
    plan = cluster_plan.build(seq("Red", "car"), _face(1000, 300), spec, cut_s=2.0, total_frames=60)
    (e,) = [e for e in plan["events"] if e["mode"] == "behind"]
    drawn = [cluster_plan.row_drawn(r, e["maxW"], plan.get("glyph")) for r in e["words"]]
    assert max(r["size"] for r in e["words"]) == cap and [r["size"] for r in e["words"]] == drawn      # the plan carries what is drawn
    anchor = (1000 - 0.8 * 300) + 0.30 * 300
    assert abs(e["y"] + cluster_plan.block_height(drawn) - anchor) <= 1


def test_a_hero_max_under_the_cap_plans_exactly_as_it_did(monkeypatch):
    """the fixture looks sit under the cap (hero 250 at 860 and 240 at 900, caps of 283 and 298), so holding rows to it moves nothing in their plans"""
    words = seq(*"Most contractors waste money on ads so call us today for a free quote".split())
    for brand_id in cap_helpers.BRANDS:
        spec = cap_helpers.spec(brand_id, tier="FULL")
        assert spec["hero_max"] <= cluster_plan.short_hero_cap(spec["width"], spec.get("glyph")), brand_id
        build = lambda: cluster_plan.build(words, _face(750, 300), spec, cut_s=words[-1]["end"] + 0.3, total_frames=int((words[-1]["end"] + 0.3) * 30) + 1)   # noqa: E731
        held = build()
        with monkeypatch.context() as m:
            m.setattr(cluster_plan, "hold_to_renderer", lambda rows, max_w, glyph=None: False)
            assert build() == held, brand_id


# --- the platform marks the renderer can draw ----------------------------------------------------------------------------

NINE_MARKS = {"instagram", "facebook", "tiktok", "youtube", "google", "meta", "x", "whatsapp", "gmail"}


def _icons_without(tmp_path, name):
    """a copy of Icons.tsx with one LOGO_PATHS entry deleted (the entry's name and the path string that follows it, on the same line or the next)"""
    src = open(cluster_plan.ICONS_TSX, encoding="utf-8").read()
    cut = re.sub(rf'(?m)^  {name}:[ \t]*\n?[ \t]*"[^"\n]*",\n', "", src)
    assert cut != src, f"{name} is not an entry of LOGO_PATHS in Icons.tsx"
    path = tmp_path / "Icons.tsx"
    path.write_text(cut, encoding="utf-8")
    return str(path)


def test_the_planner_lists_the_platform_marks_the_renderer_ships():
    assert os.path.dirname(cluster_plan.ICONS_TSX) == os.path.dirname(cluster_plan.COMPOSITION)
    assert cluster_plan.logo_names() == NINE_MARKS
    assert cluster_plan.LOGO_MAP == cluster_plan.drawn_logo_map() == cluster_plan.LOGO_MAP_ALL    # all nine ship, so every platform word is a trigger
    assert {"ig", "fb", "twitter"} <= set(cluster_plan.LOGO_MAP) and cluster_plan.LOGO_MAP["twitter"] == "x"


def test_a_platform_mark_that_is_not_shipped_is_not_a_trigger_word(tmp_path):
    icons = _icons_without(tmp_path, "instagram")
    assert cluster_plan.logo_names(icons) == NINE_MARKS - {"instagram"}
    drawn = cluster_plan.drawn_logo_map(icons)
    assert "instagram" not in drawn and "ig" not in drawn                        # the name and its short form go together
    assert drawn["facebook"] == "facebook" and drawn["fb"] == "facebook"          # the marks that are shipped stay
    assert drawn == {k: v for k, v in cluster_plan.LOGO_MAP_ALL.items() if v != "instagram"}
    assert cluster_plan.logo_names()                                              # the real file was not touched


def test_a_look_that_ships_no_platform_marks_has_no_trigger_words(tmp_path):
    bare = tmp_path / "Icons.tsx"
    bare.write_text("const LOGO_PATHS: Partial<Record<LogoName, string>> = {\n};\n", encoding="utf-8")
    assert cluster_plan.logo_names(str(bare)) == set()
    assert cluster_plan.drawn_logo_map(str(bare)) == {}


def test_a_composition_whose_icons_file_cannot_be_read_keeps_every_platform_word(tmp_path):
    nope = str(tmp_path / "nope.tsx")
    assert cluster_plan.logo_names(nope) is None
    assert cluster_plan.drawn_logo_map(nope) == cluster_plan.LOGO_MAP_ALL


def test_a_platform_word_with_no_mark_is_planned_as_an_ordinary_word(tmp_path, monkeypatch):
    words = seq("send", "it", "to", "IG")
    tagged, _ = plan_for(words, "varsans")
    assert [(r["text"], r.get("logo")) for _, r in rows_of(tagged) if r["hero"]] == [("IG", "instagram")]     # a platform name is the hero even when it is the shortest word
    monkeypatch.setattr(cluster_plan, "LOGO_MAP", cluster_plan.drawn_logo_map(_icons_without(tmp_path, "instagram")))
    plain, _ = plan_for(words, "varsans")
    assert [r["text"] for _, r in rows_of(plain) if r["hero"]] == ["send"]            # no mark to draw, so the longest word is the hero as for any phrase
    assert not [r for _, r in rows_of(plain) if r.get("logo")]                      # and no row holds room for a mark


# --- CLI -------------------------------------------------------------------------------------------------------------

def _cli(tmp_path, words_doc, spec):
    paths = {n: str(tmp_path / n) for n in ("words.json", "track.json", "spec.json", "plan.json")}
    json.dump(words_doc, open(paths["words.json"], "w"))
    json.dump(TRACK, open(paths["track.json"], "w"))
    json.dump(spec, open(paths["spec.json"], "w"))
    r = subprocess.run([sys.executable, SCRIPT, paths["words.json"], paths["track.json"], paths["plan.json"], "--spec", paths["spec.json"],
                        "--cut", "2.0", "--frames", "60"], capture_output=True, text=True)
    return r, paths


def test_cli_writes_the_plan(tmp_path):
    r, paths = _cli(tmp_path, {"words": seq("send", "artwork", "today")}, cap_helpers.spec("varsans"))
    assert r.returncode == 0 and "front clusters" in r.stdout, r.stdout + r.stderr
    assert json.load(open(paths["plan.json"]))["font"]["family"] == "InterDyn"


def test_cli_a_word_without_an_end_is_one_stop_line_not_a_traceback(tmp_path):
    words = seq("send", "artwork", "today")
    del words[1]["end"]
    r, _ = _cli(tmp_path, {"words": words}, cap_helpers.spec("varsans"))
    assert r.returncode == 2 and r.stderr.startswith("STOP: cluster_plan: word 1") and "Traceback" not in r.stdout + r.stderr


def test_cli_an_empty_transcript_and_a_spec_without_a_font_stop_plainly(tmp_path):
    r, _ = _cli(tmp_path, {"words": []}, cap_helpers.spec("varsans"))
    assert r.returncode == 2 and "no words" in r.stderr
    spec = cap_helpers.spec("varsans")
    del spec["font"]
    r, _ = _cli(tmp_path, {"words": seq("send", "artwork")}, spec)
    assert r.returncode == 2 and r.stderr.startswith("STOP: cluster_plan:") and "font" in r.stderr
