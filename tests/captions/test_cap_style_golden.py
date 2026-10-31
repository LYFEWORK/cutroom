"""The golden: a look with no caption style laid over it plans, for both fixture looks, the spec and the plan byte for byte as the engine wrote them when the golden was made
(tests/captions/golden/<brand>.spec.json and .plan.json, made by golden_pipeline.py `write`). A take that picks no style and whose DNA names no default style gets exactly that.
Choosing a style lays layout and motion over the look and nothing else (no colour, no font, no word list, no word dropped from the screen); a style with a `look` block
moves exactly the colours and the font slot it names."""
import json
import os
import re
import shutil

import pytest

import cap_helpers
import dna
import lookprofile
import spec_adapter
import styles
import takectl
from golden_pipeline import BRAND_IDS, GOLDEN, TIER_DOC, load_profile, outputs, words
from keys import EngineStop

STYLES_DIR = os.path.join(cap_helpers.FIXTURES, "styles")
STYLES = {d["id"]: d for d in styles.load_all(STYLES_DIR)}
# the styles that set layout and motion only, and the ones that carry a look block of their own
LAYOUT_ONLY = tuple(sorted(i for i, d in STYLES.items() if not d.get("look")))
OWN_LOOK = tuple(sorted(i for i, d in STYLES.items() if d.get("look")))
HAS_RUNNING = {b: bool(cap_helpers.dna_of(b)["fonts"].get("running")) for b in BRAND_IDS}
# the plan's own colour and font fields: a layout style may not move any of them
PLAN_IDENTITY = ("font", "accent", "base", "heroBase", "heroShadow", "wordFont", "chipNeg", "heroTint", "ctaWash", "pageBg", "pageInk", "pillBg", "pillInk",
                 "displayFont", "displayWeight", "heroStroke", "glyph")


def golden(brand, kind):
    with open(os.path.join(GOLDEN, f"{brand}.{kind}.json"), encoding="utf-8") as fh:
        return fh.read()


def styled(brand, style_doc, tier=None):
    """(spec, plan) as dicts for the frozen look of a brand with the style laid over it"""
    spec, plan = outputs(styles.apply(load_profile(brand), style_doc), tier_doc=tier)
    return json.loads(spec), json.loads(plan)


def usable(brand, sid):
    """a style that asks for the running face needs a look that has one"""
    return HAS_RUNNING[brand] or (STYLES[sid].get("look") or {}).get("font") != "running"


@pytest.fixture
def put_brand(workspace):
    """install_brand(workspace, id) for one test; the workspace's Brand/ is gone again afterwards"""
    yield lambda brand_id: cap_helpers.install_brand(workspace, brand_id)
    shutil.rmtree(os.path.join(workspace, "Brand"), ignore_errors=True)


@pytest.mark.parametrize("brand", BRAND_IDS)
def test_the_looks_own_preset_is_byte_for_byte_the_golden(brand):
    """the look with no style laid over it: what a take gets when it picks none and its DNA names no default style"""
    spec, plan = outputs(load_profile(brand))
    assert spec == golden(brand, "spec"), "spec.json changed for the look's own preset"
    assert plan == golden(brand, "plan"), "plan.json changed for the look's own preset: regenerate the golden only if the planner was changed on purpose"
    assert "caption_style" not in json.loads(spec) and not any(str(f).startswith("style:") for f in json.loads(plan)["flags"])


@pytest.mark.parametrize("brand", BRAND_IDS)
def test_a_take_that_picks_no_style_is_made_in_its_looks_default_style(brand, tmp_path, put_brand):
    """a take with no pick (no GATE.md line, no caption-style.txt) asks for the DNA's captions.default_style (takectl.chosen_style, as run_captions.sh asks), so its spec and
    its plan are that style's and not the golden"""
    put_brand(brand)
    default = takectl.chosen_style(str(tmp_path))
    assert default == load_profile(brand)["default_style"] == cap_helpers.dna_of(brand)["captions"]["default_style"]
    spec, plan = styled(brand, STYLES[default])
    assert spec["caption_style"] == default and plan["flags"][-1] == f"style:{default}"
    assert plan != json.loads(golden(brand, "plan"))


@pytest.mark.parametrize("brand", BRAND_IDS)
def test_the_soft_build_plans_every_caption_as_a_thought_and_none_as_a_cluster(brand):
    spec, plan = styled(brand, STYLES["soft-build"])
    assert spec["caption_style"] == "soft-build" and spec["look"]["soft_build"] is True and plan["flags"][-1] == "style:soft-build"
    caps = [e for e in plan["events"] if e["mode"] in ("front", "behind")]
    assert caps and all(e.get("soft") for e in caps), "every caption is a thought"
    assert plan != json.loads(golden(brand, "plan"))


@pytest.mark.parametrize("brand", BRAND_IDS)
def test_the_live_look_with_no_style_is_what_the_dna_makes_plus_the_chains_own_keys(brand):
    """a look made with no style laid over it is dna.captions_profile() untouched, in its key order: no changed value, no caption_style. The only keys after it are the
    ones the chain adds (the switches the DNA does not carry, the transcriber, the font sources)"""
    d = cap_helpers.dna_of(brand)
    base = dna.captions_profile(d, cap_helpers.brand_dir(brand), dna.public_prefix(d))
    live = cap_helpers.profile(brand)
    assert "caption_style" not in live
    assert list(live)[:len(base)] == list(base), "the key order of look.json is what the DNA makes"
    assert {k: live[k] for k in base} == base
    assert set(live) - set(base) <= {"font_sources", "transcribe"} | set(lookprofile.DEFAULTS)


@pytest.mark.parametrize("brand", BRAND_IDS)
def test_a_style_that_sets_what_the_look_already_has_changes_nothing_either(brand):
    base = load_profile(brand)
    same = {"id": "same", "set": {k: base[k] for k in ("zone", "width", "hero_max", "row_px", "chin_gap", "whip_frames", "count_up")}}
    spec, plan = styled(brand, same)
    gold_plan = json.loads(golden(brand, "plan"))
    plan["flags"] = plan["flags"][:-1]
    assert plan == gold_plan and spec["caption_style"] == "same"


@pytest.mark.parametrize("brand", BRAND_IDS)
@pytest.mark.parametrize("sid", LAYOUT_ONLY)
def test_a_chosen_style_changes_layout_and_never_a_colour_or_a_font(brand, sid):
    gold = json.loads(golden(brand, "plan"))
    gold_spec = json.loads(golden(brand, "spec"))
    spec, plan = styled(brand, STYLES[sid])
    for k in PLAN_IDENTITY:
        assert plan.get(k) == gold.get(k), f"the plan's {k} moved under {sid}"
    # a soft build row has no colour of its own (its colours are the plan's "soft" block)
    row_colors = lambda p: {r["color"] for e in p["events"] if e["mode"] in ("front", "behind") for r in e["words"] if "color" in r}   # noqa: E731
    assert row_colors(plan) <= row_colors(gold) | {gold["accent"], gold["base"]}, "a caption row is drawn in a colour the look does not own"
    for k in ("accent", "places", "neg_words", "icon_words", "pill_icons", "font"):
        assert spec[k] == gold_spec[k], k
    keep = ("base", "hero_base", "hero_tint", "chip_neg", "cta_wash", "page_bg", "page_ink", "pill_bg", "pill_ink", "hero_shadow", "hero_stroke", "display_weight",
            "word_font", "display_font")
    assert {k: v for k, v in spec["look"].items() if k in keep} == {k: v for k, v in gold_spec["look"].items() if k in keep}
    assert spec["caption_style"] == sid and plan["flags"][-1] == f"style:{sid}"


@pytest.mark.parametrize("brand", [b for b in BRAND_IDS if HAS_RUNNING[b]])
@pytest.mark.parametrize("sid", OWN_LOOK)
def test_a_style_with_a_look_block_moves_the_colours_and_the_font_slot_it_names_and_no_word_list(brand, sid):
    doc = STYLES[sid]
    base = load_profile(brand)
    gold, gold_spec = json.loads(golden(brand, "plan")), json.loads(golden(brand, "spec"))
    spec, plan = styled(brand, doc)
    look = doc["look"]
    assert plan["accent"] == look["accent"] != gold["accent"]
    assert plan["base"] == look["text"] != gold["base"]
    assert plan["chipNeg"] == look["chip_neg"] and plan["heroShadow"] == look["hero_shadow"] and plan["heroTint"] == look["hero_tint"]
    assert plan["displayWeight"] == look["display_weight"]
    # look.font running: the words are drawn in the running face, and the display face stays what the page events draw in
    assert look["font"] == "running" and plan["font"]["family"] == base["word_font"]["family"] != gold["font"]["family"]
    assert plan["displayFont"]["family"] == base["font_family"] and plan["wordFont"] == gold["wordFont"]
    # what the look block does not name does not move: the hero colours and stroke, the chip, the zone, and every word list
    for k in ("heroBase", "heroStroke", "chip"):
        assert plan[k] == gold[k], k
    for k in ("places", "neg_words", "icon_words", "pill_icons", "zone"):
        assert spec[k] == gold_spec[k], k
    assert spec["caption_style"] == sid and plan["flags"][-1] == f"style:{sid}"


@pytest.mark.parametrize("brand", BRAND_IDS)
@pytest.mark.parametrize("sid", LAYOUT_ONLY)
def test_the_plan_shows_what_the_style_sets_and_a_style_that_sets_what_the_look_has_changes_nothing(brand, sid):
    """whatever a style file sets is what the plan does: a changed field changes the plan, glow and count-up off means none, whips of 0 frames means none, the chest zone
    means nothing behind the head and the top zone means a matte window"""
    d = STYLES[sid]
    gold = json.loads(golden(brand, "plan"))
    _, plan = styled(brand, d)
    body = lambda p: {k: v for k, v in p.items() if k != "flags"}   # noqa: E731
    if styles.diff(load_profile(brand), d):
        assert body(plan) != body(gold), f"{sid} sets fields this look does not have, so the plan must differ"
    else:
        assert body(plan) == body(gold), f"{sid} sets only what {brand} already has, so the plan must not move"
    rows = [r for e in plan["events"] if e["mode"] in ("front", "behind") for r in e["words"]]
    sets = d["set"]
    if sets.get("hero_glow") is False:
        assert not any(r.get("glow") for r in rows)
    if sets.get("count_up") is False:
        assert not any("count" in r for r in rows)
    if sets.get("whip_frames") == 0:
        assert plan["whips"] == []
    if sets.get("zone") == "chest":
        assert plan["matte"]["windows"] == [] and not any(e["mode"] == "behind" for e in plan["events"]) and "zone:chest" in plan["flags"]
    if sets.get("zone") == "top":
        assert plan["matte"]["windows"] and "zone:top" in plan["flags"], "a top zone style sits behind the head, so the plan needs the person matte"
    if sets.get("hero_max"):
        assert max(r["size"] for r in rows if r.get("hero")) <= sets["hero_max"]


def spoken(text):
    return re.sub(r"[^a-z0-9' ]", "", text.lower()).split()


# every style a look may use, at every room-check tier
CASES = [(b, t, sid) for b in BRAND_IDS for t in ("FULL", "REDUCED", "MINIMAL") for sid in sorted(STYLES) if usable(b, sid)]


@pytest.mark.parametrize("brand,tier,sid", CASES)
def test_every_style_plans_every_spoken_word_on_both_looks_at_every_tier(brand, tier, sid):
    """a style can change how the words look and where they sit, never which words are on the screen"""
    spec, plan = styled(brand, STYLES[sid], dict(TIER_DOC, tier=tier))
    drawn = [t for e in plan["events"] if e["mode"] in ("front", "behind") for r in e["words"] for t in spoken(r["text"])]
    said = [t for w in words() for t in spoken(w["text"])]
    assert sorted(drawn) == sorted(said), (brand, tier, sid)
    assert all(r["size"] >= 44 for e in plan["events"] if e["mode"] in ("front", "behind") for r in e["words"]), "no row is drawn below the smallest legible size"
    assert spec["tier"] == tier


def test_the_running_face_style_is_refused_for_a_look_that_has_no_running_face():
    assert not HAS_RUNNING["varsans"]
    with pytest.raises(styles.StyleError, match="no running font"):
        styles.apply(load_profile("varsans"), STYLES["rose-running"])


def test_a_style_that_lowers_the_tier_cap_reaches_the_room_check_through_look_json():
    """run_captions.sh reads caption_tier_max from the take's look.json (the styled look) before the room check, so a style's cap is the cap the room check uses"""
    assert cap_helpers.profile("varsans", "quiet-cluster")["caption_tier_max"] == "REDUCED"
    assert cap_helpers.profile("varsans")["caption_tier_max"] == "FULL"


def test_the_spec_stops_when_the_gate_asks_for_a_style_look_json_was_not_made_with():
    base = load_profile("varsans")
    gate = {"approved": True, "overrides": {"style": "keyword-slam"}}
    with pytest.raises(EngineStop, match="GATE.md asks for caption style keyword-slam but look.json was made with none"):
        spec_adapter.build(TIER_DOC, base, gate=gate)
    ok = spec_adapter.build(TIER_DOC, styles.apply(base, STYLES["keyword-slam"]), gate=gate)
    assert ok["caption_style"] == "keyword-slam"
    other = styles.apply(base, STYLES["quiet-cluster"])
    with pytest.raises(EngineStop, match="but look.json was made with quiet-cluster"):
        spec_adapter.build(TIER_DOC, other, gate=gate)
