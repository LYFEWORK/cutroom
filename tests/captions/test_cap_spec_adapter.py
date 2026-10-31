"""spec_adapter.py turns the room check's tier.json, the take's look.json and the operator's gate.json into the spec.json the planner reads. These tests run it against the
two fixture looks: the tier and zone rules, what rides into the spec, the width factors measured on the look's own font files, the gate's overrides, the command line, and
the planner honoring the two flags the adapter emits."""
import json
import os
import subprocess
import sys

import pytest

import cap_helpers
import glyph
import spec_adapter
from cap_helpers import profile
from keys import EngineStop

SCRIPT = os.path.join(cap_helpers.REPO, "engine", "captions", "spec_adapter.py")


def tier_doc(tier, **extra):
    d = {"tier": tier, "reason": "test", "room": {"above": 0, "below": 0, "left": 0, "right": 0},
         "safe": {"x0": 65, "x1": 940, "y0": 270, "y1": 1420}}
    d.update(extra)
    return d


def brand(base="upstatic", **over):
    """A real look from a fixture DNA (what look.json holds in a take), with `over` applied."""
    b = profile(base)
    b.update(over)
    return b


def run_script(*args):
    return subprocess.run([sys.executable, "-B", SCRIPT, *map(str, args)], capture_output=True, text=True)


# --- tier_to_zone: pure function, all three tiers x two zones ----------------------------------

def test_tier_to_zone_minimal_forces_chest_regardless_of_the_looks_zone():
    assert spec_adapter.tier_to_zone("MINIMAL", "chest") == "chest"
    assert spec_adapter.tier_to_zone("MINIMAL", "top") == "chest"


def test_tier_to_zone_reduced_forces_chest_regardless_of_the_looks_zone():
    assert spec_adapter.tier_to_zone("REDUCED", "chest") == "chest"
    assert spec_adapter.tier_to_zone("REDUCED", "top") == "chest"


def test_tier_to_zone_full_defers_to_the_looks_zone():
    assert spec_adapter.tier_to_zone("FULL", "chest") == "chest"
    assert spec_adapter.tier_to_zone("FULL", "top") == "top"


def test_tier_to_zone_is_case_insensitive_on_tier():
    assert spec_adapter.tier_to_zone("full", "top") == "top"
    assert spec_adapter.tier_to_zone("minimal", "top") == "chest"


def test_tier_to_zone_rejects_unknown_tier():
    with pytest.raises(EngineStop):
        spec_adapter.tier_to_zone("HUGE", "chest")


# --- build(): zone + no_behind + hero_max wiring ------------------------------------------------

def test_full_tier_uses_the_looks_zone_top_unchanged():
    spec = spec_adapter.build(tier_doc("FULL"), brand(zone="top", hero_max=360))
    assert spec["zone"] == "top" and spec["no_behind"] is False and spec["hero_max"] == 360


def test_full_tier_uses_the_looks_zone_chest_unchanged():
    spec = spec_adapter.build(tier_doc("FULL"), brand(zone="chest", hero_max=250))
    assert spec["zone"] == "chest" and spec["no_behind"] is False and spec["hero_max"] == 250


def test_minimal_forces_chest_no_behind_and_caps_hero_max():
    spec = spec_adapter.build(tier_doc("MINIMAL"), brand(zone="top", hero_max=360))
    assert spec["zone"] == "chest"
    assert spec["no_behind"] is True
    assert spec["hero_max"] == 252   # 360 * 0.7, rounded


def test_reduced_forces_chest_but_does_not_set_no_behind_or_cap_hero_max():
    spec = spec_adapter.build(tier_doc("REDUCED"), brand(zone="top", hero_max=360))
    assert spec["zone"] == "chest"
    assert spec["no_behind"] is False
    assert spec["hero_max"] == 360


def test_minimal_caps_hero_max_even_when_the_looks_zone_is_already_chest():
    spec = spec_adapter.build(tier_doc("MINIMAL"), brand(zone="chest", hero_max=200))
    assert spec["zone"] == "chest" and spec["no_behind"] is True and spec["hero_max"] == 140


# --- build(): the rest of cluster_plan.py's --spec shape ----------------------------------------

def test_spec_carries_accent_width_top_y0_row_px_chin_gap_and_font():
    spec = spec_adapter.build(tier_doc("FULL"), brand(width=900, top_y0=210, row_px=[120, 140], chin_gap=100))
    assert spec["accent"] == cap_helpers.dna_of("upstatic")["colors"]["accent"] == "#38E1B0"
    assert spec["width"] == 900
    assert spec["top_y0"] == 210
    assert spec["row_px"] == [120, 140]
    assert spec["chin_gap"] == 100
    f = spec["font"]
    assert f["family"] == "InterStaticDyn"
    assert f["faces"] == [{"weight": "100 900", "src": "brand-upstatic/fonts/Inter-ExtraBold.ttf"}]
    assert os.path.isfile(f["measure"]) and f["measure"].endswith("Inter-ExtraBold.ttf")


def test_each_look_gets_its_own_font_and_running_face():
    up = spec_adapter.build(tier_doc("FULL"), profile("upstatic"))
    vs = spec_adapter.build(tier_doc("FULL"), profile("varsans"))
    assert up["font"]["family"] != vs["font"]["family"]
    assert vs["font"]["faces"] == [{"weight": "100 900", "src": "brand-varsans/fonts/Inter-Variable.ttf"}]
    assert "word_font" not in vs["look"]                                         # varsans has no running face
    assert up["look"]["word_font"]["family"] == "InterRunDyn"                     # upstatic's running face
    assert up["look"]["word_font"]["faces"] == [{"weight": "100 900", "src": "brand-upstatic/fonts/Inter-Variable.ttf"}]
    assert up["look"]["word_font"]["measure"].endswith("Inter-Variable.ttf")
    assert "display_font" not in up["look"] and "display_font" not in vs["look"]   # the display face again adds nothing


def test_a_style_that_swaps_the_slot_to_the_running_face_carries_the_display_face_as_display_font():
    spec = cap_helpers.spec("upstatic", style="rose-running")
    assert spec["font"]["family"] == "InterRunDyn" and spec["font"]["measure"].endswith("Inter-Variable.ttf")
    assert spec["look"]["display_font"] == {"family": "InterStaticDyn", "measure": None,
                                            "faces": [{"weight": "100 900", "src": "brand-upstatic/fonts/Inter-ExtraBold.ttf"}]}
    assert spec["caption_style"] == "rose-running"


def test_spec_carries_case_through_from_the_look():
    assert spec_adapter.build(tier_doc("FULL"), brand(case="upper"))["case"] == "upper"
    assert spec_adapter.build(tier_doc("FULL"), brand(case="title"))["case"] == "title"


def test_build_rejects_invalid_case():
    with pytest.raises(EngineStop):
        spec_adapter.build(tier_doc("FULL"), brand(case="loud"))


def test_build_rejects_missing_or_unknown_tier():
    with pytest.raises(EngineStop):
        spec_adapter.build(tier_doc(""), brand())
    with pytest.raises(EngineStop):
        spec_adapter.build(tier_doc("HUGE"), brand())


def test_a_look_json_that_lacks_a_layout_key_stops_naming_it():
    # no defaults live in this file: a sparse look.json is an error, not a silent default look
    with pytest.raises(EngineStop, match="accent"):
        spec_adapter.build(tier_doc("FULL"), {})
    broken = brand()
    del broken["font_faces"]
    with pytest.raises(EngineStop, match="font_faces"):
        spec_adapter.build(tier_doc("FULL"), broken)


def test_device_switches_the_look_leaves_out_are_off():
    p = brand()
    for k in ("hero_glow", "count_up", "places", "neg_words", "icon_words"):
        p.pop(k, None)
    spec = spec_adapter.build(tier_doc("FULL"), p)
    assert spec["hero_glow"] is False and spec["count_up"] is False
    assert spec["places"] == [] and spec["neg_words"] == [] and spec["icon_words"] == []


def test_the_looks_word_lists_ride_into_the_spec_from_its_dna():
    spec = spec_adapter.build(tier_doc("FULL"), profile("varsans"))
    assert spec["places"] == ["Portland", "Portland Oregon"] and spec["neg_words"] == ["broken"]
    assert spec["icon_words"] == ["ad:play", "ads:play", "video:play", "post:grid", "content:grid", "feed:grid", "track:search"]
    assert spec["pill_icons"] == ["booking,calls:calendar", "reviews:star"]


def test_the_looks_ordered_pill_rows_ride_into_the_spec_beside_its_icon_words():
    spec = spec_adapter.build(tier_doc("FULL"), brand(pill_icons=["ad,ads:play", "call,calls:phone"], icon_words=["ads:play"]))
    assert spec["pill_icons"] == ["ad,ads:play", "call,calls:phone"] and spec["icon_words"] == ["ads:play"]
    plain = spec_adapter.build(tier_doc("FULL"), {k: v for k, v in brand().items() if k not in ("pill_icons", "icon_words")})
    assert plain["pill_icons"] == [] and plain["icon_words"] == []


# --- build(): the width factors measured on the look's own font files ----------------------------

@pytest.mark.parametrize("brand_id", cap_helpers.BRANDS)
def test_a_look_carries_the_width_factors_glyph_measures_on_its_own_faces(brand_id):
    look = profile(brand_id)
    got = spec_adapter.build(tier_doc("FULL"), look)["glyph"]
    assert got == glyph.glyph_factors(look) and set(got) == {"caps", "word", "hook"}
    assert all(v > 0 for v in got.values())


def test_the_factors_of_a_mixed_case_look_put_capitals_wider_than_running_words():
    got = spec_adapter.build(tier_doc("FULL"), profile("varsans"))["glyph"]
    assert 0 < got["word"] < got["caps"] and 0 < got["hook"] < got["caps"]


def test_the_factors_follow_the_font_files_the_look_names():
    """the caps factor is measured on the display files alone, so a look pointed at another face gets that face's number"""
    static = profile("upstatic")["font_abs"]["800"]
    varsans = profile("varsans")
    own = spec_adapter.build(tier_doc("FULL"), varsans)["glyph"]
    swapped = spec_adapter.build(tier_doc("FULL"), dict(varsans, font_abs={w: static for w in varsans["font_abs"]}))["glyph"]
    assert swapped["caps"] == spec_adapter.build(tier_doc("FULL"), profile("upstatic"))["glyph"]["caps"] != own["caps"]


def test_a_look_written_in_capitals_measures_all_three_kinds_as_capitals_and_title_case_measures_like_mixed():
    look = profile("varsans")          # one face and no running face: the running words are the display face
    mixed = spec_adapter.build(tier_doc("FULL"), dict(look, case="mixed"))["glyph"]
    upper = spec_adapter.build(tier_doc("FULL"), dict(look, case="upper"))["glyph"]
    assert upper["caps"] == upper["word"] == upper["hook"] == mixed["caps"]
    assert mixed["word"] < upper["word"] and mixed["hook"] < upper["hook"]
    assert spec_adapter.build(tier_doc("FULL"), dict(look, case="title"))["glyph"] == mixed


def test_a_look_whose_faces_match_the_renderers_own_numbers_carries_no_glyph(monkeypatch):
    """glyph is written only when it differs from what the renderer falls back to, so such a look's spec has no such key"""
    same = {"caps": glyph.GLYPH_CAPS, "word": glyph.GLYPH_RUN, "hook": glyph.GLYPH_RUN}
    monkeypatch.setattr(spec_adapter.glyph, "glyph_factors", lambda look: dict(same))
    assert spec_adapter.glyph_factors(profile("varsans")) is None
    assert "glyph" not in spec_adapter.build(tier_doc("FULL"), profile("varsans"))
    monkeypatch.setattr(spec_adapter.glyph, "glyph_factors", lambda look: dict(same, caps=0.7))
    assert spec_adapter.glyph_factors(profile("varsans")) == dict(same, caps=0.7)
    assert spec_adapter.build(tier_doc("FULL"), profile("varsans"))["glyph"] == dict(same, caps=0.7)


def test_a_font_that_cannot_be_measured_stops_the_spec_with_one_plain_line(tmp_path):
    ghost = str(tmp_path / "gone.ttf")
    with pytest.raises(EngineStop, match="gone.ttf") as e:
        spec_adapter.build(tier_doc("FULL"), brand("upstatic", font_abs={w: ghost for w in brand("upstatic")["font_abs"]}))
    assert "\n" not in str(e.value)


def test_a_file_that_is_not_a_font_stops_the_spec_naming_it(tmp_path):
    notfont = tmp_path / "words.ttf"
    notfont.write_text("this is not a font")
    with pytest.raises(EngineStop, match="words.ttf"):
        spec_adapter.build(tier_doc("FULL"), brand("upstatic", font_abs={w: str(notfont) for w in brand("upstatic")["font_abs"]}))


def test_look_carries_whip_and_display_weight_for_the_planner_and_plan():
    spec = spec_adapter.build(tier_doc("FULL"), brand(whip_frames=5, whip_amount=0.5, display_weight=700))
    assert spec["look"]["whip_frames"] == 5 and spec["look"]["whip_amount"] == 0.5 and spec["look"]["display_weight"] == 700


# --- build(): the soft build asks the display file whether it is one variable font ---------------

def test_font_variable_asks_the_display_file_whether_it_is_one_variable_font():
    assert spec_adapter.font_variable(profile("varsans")) is True        # the shipped Inter: one variable file
    assert spec_adapter.font_variable(profile("upstatic")) is False      # a static ExtraBold cut


def test_font_variable_reads_the_weight_800_file_and_otherwise_the_first():
    variable, static = profile("varsans")["font_abs"]["800"], profile("upstatic")["font_abs"]["800"]
    assert spec_adapter.font_variable({"font_abs": {"400": variable, "800": static}}) is False
    assert spec_adapter.font_variable({"font_abs": {"400": variable, "900": static}}) is True
    assert spec_adapter.font_variable({"font_abs": {"400": static, "900": variable}}) is False


def test_a_look_with_no_display_font_file_on_disk_stops_with_one_plain_line(tmp_path):
    for files in ({"800": str(tmp_path / "gone.ttf")}, {}):
        with pytest.raises(EngineStop, match="no display font file on disk"):
            spec_adapter.font_variable({"font_abs": files})


@pytest.mark.parametrize("brand_id,variable", [("varsans", True), ("upstatic", False)])
def test_only_a_soft_spec_carries_on_accent_and_whether_the_display_font_is_variable(brand_id, variable):
    soft = cap_helpers.spec(brand_id, style="soft-build")["look"]
    on_accent = cap_helpers.dna_of(brand_id)["colors"]["on_accent"]
    assert soft["soft_build"] is True and soft["on_accent"] == on_accent and soft["font_variable"] is variable
    plain = cap_helpers.spec(brand_id)["look"]
    assert not plain.get("soft_build") and not {"on_accent", "font_variable"} & set(plain)


# --- build(): the operator's gate.json -----------------------------------------------------------

def test_gate_tier_override_replaces_the_room_check_tier():
    gate = {"overrides": {"tier": "MINIMAL"}}
    spec = spec_adapter.build(tier_doc("FULL"), brand(zone="top", hero_max=360), gate=gate)
    assert spec["tier"] == "MINIMAL" and spec["no_behind"] is True and spec["zone"] == "chest" and spec["hero_max"] == 252
    assert spec["zone_reason"] == "operator override via GATE.md"


def test_gate_zone_override_beats_the_tier_and_the_look():
    gate = {"overrides": {"zone": "top"}}
    assert spec_adapter.build(tier_doc("REDUCED"), brand(zone="chest"), gate=gate)["zone"] == "top"
    assert spec_adapter.build(tier_doc("FULL"), brand(zone="top"), gate={"overrides": {"zone": "chest"}})["zone"] == "chest"


def test_gate_device_overrides_and_finale_ride_into_the_spec():
    gate = {"overrides": {"devices": {"device_overrides": {"neg_add": ["weak"], "hero_drop": ["gonna"]}, "layout_events": []},
                          "finale": {"top": "You ask", "bottom": "we answer"}}}
    spec = spec_adapter.build(tier_doc("FULL"), brand(), gate=gate)
    assert spec["device_overrides"] == {"neg_add": ["weak"], "hero_drop": ["gonna"]}
    assert spec["finale"] == {"top": "You ask", "bottom": "we answer"}


def test_accepted_layout_events_ride_into_the_spec_as_spans_for_the_planner():
    events = [{"mode": "page", "start": 1.2, "end": 1.9, "text": "x"}, {"mode": "cta", "start": 5.0, "end": 6.5, "rows": []}]
    gate = {"overrides": {"devices": {"layout_events": events, "device_overrides": {}}}}
    assert spec_adapter.build(tier_doc("FULL"), brand(), gate=gate)["layout_spans"] == [[1.2, 1.9], [5.0, 6.5]]
    assert "layout_spans" not in spec_adapter.build(tier_doc("FULL"), brand())
    assert "layout_spans" not in spec_adapter.build(tier_doc("FULL"), brand(), gate={"overrides": {"devices": {"layout_events": []}}})


def test_no_gate_means_no_overrides_in_the_spec():
    spec = spec_adapter.build(tier_doc("FULL"), brand())
    assert "device_overrides" not in spec and "finale" not in spec and "zone_reason" not in spec


# --- build(): --behind pass-through --------------------------------------------------------------

def test_behind_is_passed_through_untouched_when_given():
    behind = [{"match": "Acme Corp", "rows": [["Acme", 380, "#FFFFFF", -120], ["Corp", 340, "accent", 90]],
               "top": 200, "hold": 1.3, "maxW": 960}]
    spec = spec_adapter.build(tier_doc("FULL"), brand(zone="top"), behind=behind)
    assert spec["behind"] == behind
    assert spec["behind"][0]["match"] == "Acme Corp"   # unmodified, not re-cased or re-shaped


def test_behind_is_omitted_when_not_given():
    spec = spec_adapter.build(tier_doc("FULL"), brand())
    assert "behind" not in spec


def test_behind_still_passed_through_on_minimal_tier_since_cluster_plan_honors_no_behind():
    # spec_adapter's job is to emit the flag; cluster_plan.py (see test_cluster_plan_honors_
    # no_behind_and_case below) is the one that actually skips the list when no_behind is set.
    behind = [{"match": "Hi", "rows": [["Hi", 300, "#FFFFFF", 0]], "top": 200, "hold": 1.0}]
    spec = spec_adapter.build(tier_doc("MINIMAL"), brand(zone="top"), behind=behind)
    assert spec["no_behind"] is True
    assert spec["behind"] == behind


# --- CLI: file-in/file-out plumbing (mirrors the other scripts' _main() shape) -------------------

def test_main_writes_spec_json(tmp_path):
    tier_path, look_path, out_path = (tmp_path / n for n in ("tier.json", "look.json", "spec.json"))
    json.dump(tier_doc("FULL"), open(tier_path, "w"))
    json.dump(brand(zone="top"), open(look_path, "w"))
    r = run_script(tier_path, look_path, out_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "top zone, behind ok, upper case"
    out = json.load(open(out_path))
    assert out["zone"] == "top" and out["no_behind"] is False


def test_main_with_gate_flag(tmp_path):
    tier_path, look_path, gate_path, out_path = (tmp_path / n for n in ("tier.json", "look.json", "gate.json", "spec.json"))
    json.dump(tier_doc("FULL"), open(tier_path, "w"))
    json.dump(brand(zone="top"), open(look_path, "w"))
    json.dump({"overrides": {"zone": "chest"}}, open(gate_path, "w"))
    r = run_script(tier_path, look_path, out_path, "--gate", gate_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.load(open(out_path))["zone"] == "chest"


def test_main_with_behind_flag(tmp_path):
    tier_path, look_path, behind_path, out_path = (tmp_path / n for n in ("tier.json", "look.json", "behind.json", "spec.json"))
    json.dump(tier_doc("FULL"), open(tier_path, "w"))
    json.dump(brand(zone="top"), open(look_path, "w"))
    behind = [{"match": "Acme Corp", "rows": [["Acme", 380, "#FFFFFF", -120]], "top": 200, "hold": 1.3}]
    json.dump(behind, open(behind_path, "w"))
    r = run_script(tier_path, look_path, out_path, "--behind", behind_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.load(open(out_path))["behind"] == behind


def test_main_with_both_flags_in_either_order(tmp_path):
    tier_path, look_path, gate_path, behind_path = (tmp_path / n for n in ("tier.json", "look.json", "gate.json", "behind.json"))
    json.dump(tier_doc("FULL"), open(tier_path, "w"))
    json.dump(brand(zone="top"), open(look_path, "w"))
    json.dump({"overrides": {"zone": "chest"}}, open(gate_path, "w"))
    behind = [{"match": "Hi", "rows": [["Hi", 300, "#FFFFFF", 0]], "top": 200, "hold": 1.0}]
    json.dump(behind, open(behind_path, "w"))
    for n, flags in enumerate((["--gate", gate_path, "--behind", behind_path], ["--behind", behind_path, "--gate", gate_path])):
        out_path = tmp_path / f"spec{n}.json"
        r = run_script(tier_path, look_path, out_path, *flags)
        assert r.returncode == 0, r.stdout + r.stderr
        out = json.load(open(out_path))
        assert out["zone"] == "chest" and out["behind"] == behind


def test_main_stops_cleanly_on_bad_tier(tmp_path):
    tier_path, look_path, out_path = (tmp_path / n for n in ("tier.json", "look.json", "spec.json"))
    json.dump(tier_doc("NOT_A_TIER"), open(tier_path, "w"))
    json.dump(brand(), open(look_path, "w"))
    r = run_script(tier_path, look_path, out_path)
    assert r.returncode == 2
    assert r.stderr.startswith("STOP:") and r.stdout == "" and "Traceback" not in r.stderr
    assert not out_path.exists()


def test_main_stops_with_the_usage_line_when_an_argument_is_missing(tmp_path):
    r = run_script(tmp_path / "tier.json")
    assert r.returncode == 2 and r.stderr.startswith("STOP: usage: spec_adapter.py <tier.json> <look.json>") and "Traceback" not in r.stderr


# --- integration: cluster_plan.py actually honors the two flags spec_adapter.py emits -----------

def _words(*texts, start=0.0, step=0.5, dur=0.4):
    out = []
    t = start
    for txt in texts:
        out.append({"text": txt, "start": round(t, 3), "end": round(t + dur, 3), "filler": False})
        t += step
    return out


def _track(cx=540, cy=1050, w=400, h=400):
    m = {"cx": cx, "cy": cy, "w": w, "h": h}
    return {"W": 1080, "H": 1920, "samples": {"0": m}, "median": m}


def test_cluster_plan_honors_no_behind_and_case():
    import cluster_plan
    words = _words("Acme", "Corp", "makes", "signs", "fast")
    behind = [{"match": "Acme Corp", "rows": [["Acme", 380, "#FFFFFF", -120], ["Corp", 340, "accent", 90]],
               "top": 200, "hold": 1.3, "maxW": 960}]

    # FULL + zone top: the hand-specified "Acme Corp" behind cluster renders, in mixed case.
    spec_full = spec_adapter.build(tier_doc("FULL"), brand(zone="top", case="mixed"), behind=behind)
    plan_full = cluster_plan.build(words, _track(), spec_full, cut_s=5.0, total_frames=150)
    behind_events = [e for e in plan_full["events"] if e["mode"] == "behind"]
    assert any(r["text"] == "Acme" for e in behind_events for r in e["words"])

    # MINIMAL: no_behind is set, so the SAME hand-specified behind cluster must not appear at all,
    # and every rendered word is upper-cased per the look's case.
    spec_min = spec_adapter.build(tier_doc("MINIMAL"), brand(zone="top", case="upper"), behind=behind)
    plan_min = cluster_plan.build(words, _track(), spec_min, cut_s=5.0, total_frames=150)
    assert not [e for e in plan_min["events"] if e["mode"] == "behind"]
    rendered = [r["text"] for e in plan_min["events"] for r in e["words"]]
    assert rendered and all(t == t.upper() for t in rendered)


def test_cluster_plan_title_case_capitalizes_first_letter_only():
    import cluster_plan
    words = _words("we", "build", "fast", "signs", "here")
    spec = spec_adapter.build(tier_doc("FULL"), brand(zone="chest", case="title"))
    plan = cluster_plan.build(words, _track(), spec, cut_s=5.0, total_frames=150)
    rendered = [r["text"] for e in plan["events"] for r in e["words"] if r["text"]]
    assert rendered   # non-empty
    for t in rendered:
        assert t[:1] == t[:1].upper()          # first letter capitalized
        assert t[1:] == t[1:].lower()          # rest of the string untouched (lowercase as transcribed)
