"""styles.py: the named caption styles. A style is a PARTIAL look laid over your look (the captions section of your DNA) for one take, in two blocks: `set`, the layout and
motion fields (17 of them, each held to the type and range of the captions schema), and `look`, the colors and the font slot (accent, text, on_accent, hero_base, hero_tint,
hero_shadow, hero_stroke, chip_neg, display_weight, font). A color or a font in `set` is refused, so is a layout field in `look`, the names and the taught words of the look never
move, and a library with one bad file still serves the others.

The fixture styles (tests/captions/fixtures/styles) stand in for a user's own library and are only read. Everything that writes goes to a scratch folder, which
CAPTION_STYLES_DIR points the whole library at. The base look is the captions profile the DNA module makes of a fixture brand: varsans (one variable face, no running face)
or upstatic (a static display face and a variable running face)."""
import copy
import functools
import json
import os
import shutil
import subprocess
import sys

import pytest

import cap_helpers
import common
import dna
import lookprofile
import styles
from keys import EngineStop

SCRIPT = os.path.join(cap_helpers.REPO, "engine", "captions", "styles.py")
FIXTURE_STYLES = os.path.join(cap_helpers.FIXTURES, "styles")
LAYOUT_ONLY = ("cluster-chest", "cluster-top", "hero-lockup", "keyword-slam", "quiet-cluster", "soft-build")
FIXTURE_IDS = tuple(sorted(LAYOUT_ONLY + ("rose-running",)))

# what a style may never move: the names, the taught words and the word lists of the look, and its face files unless look.font swaps the slot
NAMES = ("id", "brand", "handle", "dna_file")
WORDS = ("spelling", "do_not_say", "places", "neg_words", "icon_words", "pill_icons")
FACE = ("font", "font_fallback", "fonts_dir", "font_prefix", "font_family", "font_files", "font_faces", "font_abs", "font_variable", "display_font", "word_font")
# the profile key each look.<name> lays itself over, written out here so a change to styles.LOOK_PROFILE_KEY is caught (look.text is the profile's `base`)
LOOK_LANDS_ON = {"accent": "accent", "text": "base", "on_accent": "on_accent", "hero_base": "hero_base", "hero_tint": "hero_tint", "hero_shadow": "hero_shadow",
                 "hero_stroke": "hero_stroke", "chip_neg": "chip_neg", "display_weight": "display_weight"}
# a look block that moves every color key and the weight, each to something neither fixture brand has
LOOK_ALL = {"accent": "#E8456B", "text": "#F7F2EE", "on_accent": "#1A1014", "hero_base": "#FEFEFE", "hero_tint": "#FFB3C4", "hero_shadow": "0 2px 10px rgba(0,0,0,0.5)",
            "hero_stroke": {"width": 3, "color": "#101418"}, "chip_neg": "#B3122B", "display_weight": 700}
# every profile key the DNA module's own style layering and styles.apply set the same way for a style without a font swap (the rest is what lookprofile adds)
PARITY_SKIP = {"caption_style", "style", "feed", "font_sources", "transcribe", "devices", "master_audio"}


@functools.lru_cache(maxsize=None)
def _dna_profile(brand_id):
    d = cap_helpers.dna_of(brand_id)
    return dna.captions_profile(d, cap_helpers.brand_dir(brand_id), dna.public_prefix(d))


def base_look(brand_id="varsans"):
    """the look a style is laid over: the captions profile the DNA module makes of a fixture brand (a fresh copy every time)"""
    return copy.deepcopy(_dna_profile(brand_id))


def fx(style_id):
    """one fixture style, loaded and checked from the fixture folder"""
    return styles.load(style_id, FIXTURE_STYLES)


def doc(**over):
    base = {"id": "demo", "name": "Demo", "summary": "A demo.", "use_when": "a demo", "never_when": "not a demo", "preview": None, "source": None, "opt_in": True,
            "owner": None, "added_for": None, "set": {"hero_max": 300}}
    base.update(over)
    return base


def write(folder, d):
    """a style written the way the engine writes one: known keys in their fixed order"""
    with open(os.path.join(str(folder), d["id"] + ".json"), "w", encoding="utf-8") as fh:
        fh.write(styles.text_of(d))


def write_raw(path, obj):
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def changed_keys(before, after):
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """a scratch copy of the fixture library, which styles.py reads through CAPTION_STYLES_DIR"""
    d = tmp_path / "styles"
    shutil.copytree(FIXTURE_STYLES, d)
    monkeypatch.setenv(styles.ENV_DIR, str(d))
    return d


@pytest.fixture
def config(tmp_path, monkeypatch):
    """a config.json of this test's own, which common reads in place of the shared one (nothing is written there yet)"""
    path = tmp_path / "config.json"
    monkeypatch.setattr(common, "CONF", path)
    return path


# ------------------------------------------------------------------ the libraries: the fixtures and the starters

def test_the_fixture_library_is_sound_and_holds_exactly_the_fixture_styles():
    docs, bad = styles.check(FIXTURE_STYLES)
    assert bad == [], bad
    ids = [d["id"] for d in docs]
    assert ids == sorted(ids) == styles.ids(FIXTURE_STYLES) and tuple(ids) == FIXTURE_IDS
    for d in docs:
        assert styles.where(d) == "folder"
        for k in ("summary", "use_when", "never_when"):
            assert d[k].strip() and "\n" not in d[k], f"{d['id']}: {k} is one plain line"


def test_every_starter_is_sound_ownerless_and_carries_no_color_or_face():
    """the starters carry a feed object (the video editor reads it, this chain does not) and no added date: check takes both"""
    docs, bad = styles.check(styles.STARTER_DIR)
    on_disk = sorted(f[:-len(".json")] for f in os.listdir(styles.STARTER_DIR) if f.endswith(".json"))
    assert bad == [], bad
    assert on_disk and [d["id"] for d in docs] == on_disk
    for d in docs:
        assert d.get("owner") is None and d["set"], f"{d['id']}: ownerless, and it sets something"
        assert "look" not in d, f"{d['id']}: a starter carries no color and no face, those are the user's"
        assert styles.where(d) == "starter" and styles.usable(d, "anyone") is d
        for k in ("summary", "use_when", "never_when"):
            assert d[k].strip() and "\n" not in d[k], f"{d['id']}: {k} is one plain line"


def test_a_shared_style_is_ownerless_so_every_dna_may_use_it_and_a_private_one_names_its_owner(lib):
    for sid in FIXTURE_IDS:
        d = fx(sid)
        assert d.get("owner") is None and d.get("status", "built") == "built" and d["set"], sid
        assert styles.usable(d) is d and styles.usable(d, "varsans") is d and styles.usable(d, "upstatic") is d
    write(lib, doc(id="acme-only", owner="upstatic"))
    private = styles.load("acme-only")
    assert styles.usable(private, "upstatic") is private
    with pytest.raises(styles.StyleError, match="caption style acme-only is private to upstatic: varsans cannot use it"):
        styles.usable(private, "varsans")


def test_soft_build_sets_its_five_fields_and_nothing_else():
    d = fx("soft-build")
    assert d["name"] == "Soft build" and d.get("owner") is None and d.get("status", "built") == "built" and d["preview"] is None and "look" not in d
    assert d["set"] == {"soft_build": True, "zone": "chest", "hero_glow": False, "whip_frames": 0, "count_up": True}
    assert styles.describe(d) == "soft_build true, zone chest, hero_glow false, whip_frames 0, count_up true"
    for brand_id in cap_helpers.BRANDS:
        base = base_look(brand_id)
        got = styles.apply(base, d)
        assert got["caption_style"] == "soft-build" and got["soft_build"] is True
        assert changed_keys(base, got) <= set(d["set"]) | {"caption_style"}, brand_id


def test_a_style_may_switch_the_soft_build_on_or_off_but_never_name_the_default_style():
    assert styles.SOFT == ("soft_build",) and set(styles.SOFT) <= set(styles.ALLOWED) and "default_style" not in styles.ALLOWED
    for v in (True, False):
        assert styles.problems(doc(set={"soft_build": v}), "demo.json") == []
    switched_on = {**base_look("varsans"), "soft_build": True}
    assert styles.apply(switched_on, doc(set={"soft_build": False}))["soft_build"] is False, "the style's value goes over the look's, as every field does"
    assert styles.apply(switched_on, doc(set={"hero_max": 300}))["soft_build"] is True, "a style that leaves it out leaves the look's"
    never_wrote = {k: v for k, v in base_look("varsans").items() if k != "soft_build"}
    assert "soft_build" not in styles.apply(never_wrote, doc(set={"hero_max": 300})), "and a profile that never carried it still has none"


def test_every_field_a_style_may_set_is_a_caption_field_and_none_is_a_color_a_font_or_a_name():
    with open(styles.SCHEMA, encoding="utf-8") as fh:
        props = json.load(fh)["properties"]["captions"]["properties"]
    assert set(styles.ALLOWED) <= set(props)
    for k in styles.ALLOWED:
        assert "$ref" not in props[k], f"{k} is a color or a stroke: those go in the look block"
    assert len(styles.ALLOWED) == 17 and sorted(styles.ALLOWED) == sorted(styles.LAYOUT + styles.TREATMENT + styles.MOTION + styles.TIER + styles.SOFT)
    assert not set(styles.ALLOWED) & set(styles.LOOK), "a field is in one block or the other"
    assert not set(styles.ALLOWED) & (set(NAMES) | set(WORDS) | set(FACE))


def test_each_look_key_lands_on_one_profile_key_and_only_text_is_renamed():
    assert styles.LOOK_PROFILE_KEY == LOOK_LANDS_ON
    assert set(styles.LOOK) == set(LOOK_LANDS_ON) | {"font"} and styles.FONT_SLOTS == ("display", "running")
    for brand_id in cap_helpers.BRANDS:
        assert set(LOOK_LANDS_ON.values()) <= set(base_look(brand_id)), brand_id


def test_the_built_in_field_table_holds_the_same_numbers_as_the_schema_file():
    with open(styles.SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    props = schema["properties"]["captions"]["properties"]
    for k in styles.ALLOWED + ("display_weight", "hero_shadow"):
        for attr in ("type", "enum", "minimum", "maximum", "minItems", "maxItems", "items"):
            assert styles.FIELDS[k].get(attr) == props[k].get(attr), f"{k}.{attr}"
    stroke = schema["definitions"]["stroke"]["properties"]
    assert styles.FIELDS["hero_stroke"]["properties"]["width"] == stroke["width"]


# ------------------------------------------------------------------ what a style file may hold

@pytest.mark.parametrize("change,fragment", [
    ({"set": {"hero_max": 5000}}, "set.hero_max: 5000 is above 800"),
    ({"set": {"hero_max": 49}}, "set.hero_max: 49 is below 50"),
    ({"set": {"hero_max": "big"}}, "set.hero_max: must be integer, got str"),
    ({"set": {"hero_max": 320.5}}, "set.hero_max: must be integer, got float"),
    ({"set": {"hero_max": True}}, "set.hero_max: must be integer, got bool"),
    ({"set": {"zone": "bottom"}}, "set.zone: 'bottom' is not one of top, chest"),
    ({"set": {"case": "lower"}}, "set.case: 'lower' is not one of upper, mixed, title"),
    ({"set": {"row_px": [140, 100]}}, "set.row_px: the first number is the smaller row"),
    ({"set": {"row_px": [100]}}, "set.row_px: needs 2 items"),
    ({"set": {"row_px": [19, 100]}}, "set.row_px[0]: 19 is below 20"),
    ({"set": {"row_px": [100, 401]}}, "set.row_px[1]: 401 is above 400"),
    ({"set": {"row_px": [72.5, 88]}}, "set.row_px[0]: must be integer, got float"),
    ({"set": {"row_px": 90}}, "set.row_px: must be array, got int"),
    ({"set": {"caption_tier_max": "HUGE"}}, "set.caption_tier_max: 'HUGE' is not one of MINIMAL, REDUCED, FULL"),
    ({"set": {"soft_build": "yes"}}, "set.soft_build: must be boolean, got str"),
    ({"set": {"soft_build": 1}}, "set.soft_build: must be boolean, got int"),
    ({"set": {"count_up": None}}, "set.count_up: must be boolean, got null"),
    ({"set": {"text_opacity": True}}, "set.text_opacity: must be number, got bool"),
    ({"set": {"font_family": "Sans"}}, "set.font_family is not a field a style may set"),
    ({"set": {"places": ["Portland"]}}, "set.places is not a field a style may set"),
    ({"set": {"default_style": "soft-build"}}, "set.default_style is not a field a style may set"),
    ({"set": {"caption_style": "other"}}, "set.caption_style is not a field a style may set"),
    ({"set": "zone top"}, "set must be an object"),
    ({"set": None}, "set must be an object"),
    ({"set": []}, "set must be an object"),
    ({"name": "  "}, "name must be a short line"),
    ({"summary": 5}, "summary must be a short line"),
    ({"use_when": ""}, "use_when must be one short sentence"),
    ({"never_when": None}, "never_when must be one short sentence"),
    ({"use_when": "two\nlines"}, "use_when must be one line"),
    ({"id": 5}, "id must be lower case words joined by hyphens"),
    ({"id": "Demo Style"}, "id must be lower case words joined by hyphens"),
    ({"id": "demo-"}, "id must be lower case words joined by hyphens"),
    ({"id": "1demo"}, "id must be lower case words joined by hyphens"),
    ({"id": "other"}, "id other must be the file name (a file called other.json)"),
    ({"mode": "cluster"}, "mode is not a known key"),
    ({"preview": "/abs/p.png"}, "preview must be null or an image path inside the styles folder"),
    ({"preview": "../p.png"}, "preview must be null or an image path inside the styles folder"),
    ({"preview": "a/../p.png"}, "preview must be null or an image path inside the styles folder"),
    ({"preview": "notes.txt"}, "preview must be null or an image path"),
    ({"preview": ""}, "preview must be null or an image path"),
    ({"source": "somewhere"}, "source must be an object or null"),
    ({"source": ["a"]}, "source must be an object or null"),
    ({"opt_in": "yes"}, "opt_in must be true or false"),
    ({"opt_in": 0}, "opt_in must be true or false"),
    ({"owner": "Acme Co"}, "owner must be an id or null"),
    ({"owner": 7}, "owner must be an id or null"),
    ({"added_for": "Bad Id"}, "added_for must be an id or null"),
    ({"status": "maybe"}, "status must be one of built, to build (or left out)"),
    ({"status": None}, "status must be one of built, to build (or left out)"),
])
def test_a_style_file_that_breaks_a_rule_is_named_in_one_plain_line(change, fragment):
    bad = styles.problems(doc(**change), "demo.json")
    assert any(fragment in line for line in bad), bad
    assert all(line.startswith("demo.json: ") for line in bad), bad


@pytest.mark.parametrize("change", [
    {"preview": "previews/keyword-slam.png"}, {"preview": "p.JPG"}, {"preview": "a/b/c.webp"}, {"source": {"type": "hand"}}, {"source": None}, {"opt_in": False},
    {"owner": "acme"}, {"added_for": "acme"}, {"status": "built"}, {"status": "to build"}, {"set": {}}, {"set": {"row_px": [100, 100]}},
    {"set": {"hero_max": 320.0, "row_px": [72.0, 88.0]}}, {"never_when": "a dash " + chr(0x2014) + " here", "summary": "an en dash " + chr(0x2013) + " here"},
])
def test_a_style_file_that_keeps_the_rules_is_sound(change):
    assert styles.problems(doc(**change), "demo.json") == []


def test_a_missing_required_key_is_named():
    d = doc()
    for k in styles.REQUIRED:
        broken = {x: v for x, v in d.items() if x != k}
        assert any(f"{k} is missing" in line for line in styles.problems(broken, "demo.json")), k
    assert styles.REQUIRED == ("id", "name", "summary", "use_when", "never_when", "set")
    assert styles.problems("a list", "demo.json") == ["demo.json: a style must be a JSON object"]
    assert styles.problems({}, "demo.json") == [f"demo.json: {k} is missing" for k in styles.REQUIRED]


def test_without_a_file_name_a_problem_is_named_by_the_style_id_and_the_id_is_not_compared_to_a_file():
    assert styles.problems(doc(id="other")) == []
    assert styles.problems(doc(set={"hero_max": 5000})) == ["demo: set.hero_max: 5000 is above 800"]
    assert styles.problems(doc(id=5, set={"hero_max": 5000}))[0].startswith("style: id must be")


def test_unknown_keys_are_all_listed_with_the_keys_a_style_file_may_hold():
    bad = styles.problems(doc(mode="cluster", mood="calm"), "demo.json")
    assert len(bad) == 2 and "mode is not a known key" in bad[0] and "mood is not a known key" in bad[1]
    assert {"set", "look", "feed"} <= set(styles.KEYS)
    assert all("(known: " in line and line.endswith("set, look, feed)") for line in bad)


def test_the_feed_block_is_left_alone_but_must_be_an_object_and_added_is_optional():
    for feed in ({}, {"place": "chest"}, {"anything": [1, 2], "nested": {"at": "any depth"}}):
        assert styles.problems(doc(feed=feed), "demo.json") == [], feed
    for feed in ("chest", [], None, 5):
        bad = styles.problems(doc(feed=feed), "demo.json")
        assert len(bad) == 1 and "feed must be an object" in bad[0], feed
    assert "added" not in doc() and "feed" not in doc() and styles.problems(doc(), "demo.json") == []


@pytest.mark.parametrize("added,ok", [("2031-04-09", True), ("2031-02-28", True), ("2031-13-40", False), ("2031-02-30", False), ("2031-4-9", False),
                                      ("yesterday", False), (20310409, False), (None, False), ("", False)])
def test_added_is_optional_but_a_real_date_when_it_is_there(added, ok):
    bad = styles.problems(doc(added=added), "demo.json")
    assert (bad == []) is ok, bad
    assert ok or bad == ["demo.json: added must be a YYYY-MM-DD date"]


@pytest.mark.parametrize("key", styles.LOOK)
def test_a_color_the_weight_or_the_font_slot_in_set_is_sent_to_the_look_block(key):
    bad = styles.problems(doc(set={key: "#FFFFFF"}), "demo.json")
    assert len(bad) == 1, bad
    assert f"set.{key} is not a field a style may set" in bad[0] and "`look` block" in bad[0], bad


@pytest.mark.parametrize("key", styles.ALLOWED)
def test_a_layout_or_motion_field_in_look_is_sent_to_the_set_block(key):
    bad = styles.problems(doc(set={}, look={key: 1}), "demo.json")
    assert len(bad) == 1, bad
    assert f"look.{key} is not a field a style may set" in bad[0] and "`set` block" in bad[0], bad


@pytest.mark.parametrize("key", ["font_family", "places", "spelling", "handle", "caption_style"])
def test_a_key_that_is_in_neither_block_is_refused_in_look_with_the_list_of_what_look_holds(key):
    bad = styles.problems(doc(set={}, look={key: "x"}), "demo.json")
    assert len(bad) == 1 and f"look.{key} is not a field a style may set: a style's `look` holds " in bad[0], bad
    assert all(k in bad[0] for k in styles.LOOK)


@pytest.mark.parametrize("look,fragment", [
    ({"accent": "#FFF"}, "look.accent: '#FFF' is not in the form #RRGGBB"),
    ({"accent": "FFD60A"}, "look.accent: 'FFD60A' is not in the form #RRGGBB"),
    ({"accent": "red"}, "look.accent: 'red' is not in the form #RRGGBB"),
    ({"accent": "#GGGGGG"}, "look.accent: '#GGGGGG' is not in the form #RRGGBB"),
    ({"accent": "#FFD60AFF"}, "look.accent: '#FFD60AFF' is not in the form #RRGGBB"),
    ({"accent": 123}, "look.accent: must be string, got int"),
    ({"accent": None}, "look.accent: must be string, got null"),
    ({"text": "#12345"}, "look.text: '#12345' is not in the form #RRGGBB"),
    ({"on_accent": "black"}, "look.on_accent: 'black' is not in the form #RRGGBB"),
    ({"hero_base": "#FFFFFFF"}, "look.hero_base: '#FFFFFFF' is not in the form #RRGGBB"),
    ({"hero_tint": "pink"}, "look.hero_tint: 'pink' is not in the form #RRGGBB"),
    ({"chip_neg": "#E3161"}, "look.chip_neg: '#E3161' is not in the form #RRGGBB"),
    ({"hero_shadow": 5}, "look.hero_shadow: must be string, got int"),
    ({"hero_stroke": {"width": 2}}, "look.hero_stroke: color is missing"),
    ({"hero_stroke": {"color": "#000000"}}, "look.hero_stroke: width is missing"),
    ({"hero_stroke": {"width": 41, "color": "#000000"}}, "look.hero_stroke.width: 41 is above 40"),
    ({"hero_stroke": {"width": -1, "color": "#000000"}}, "look.hero_stroke.width: -1 is below 0"),
    ({"hero_stroke": {"width": 2.5, "color": "#000000"}}, "look.hero_stroke.width: must be integer, got float"),
    ({"hero_stroke": {"width": 2, "color": "black"}}, "look.hero_stroke.color: 'black' is not in the form #RRGGBB"),
    ({"hero_stroke": {"width": 2, "color": "#000000", "blur": 3}}, "look.hero_stroke: blur is not a known key"),
    ({"hero_stroke": "2px"}, "look.hero_stroke: must be object, got str"),
    ({"hero_stroke": None}, "look.hero_stroke: must be object, got null"),
    ({"display_weight": 99}, "look.display_weight: 99 is below 100"),
    ({"display_weight": 901}, "look.display_weight: 901 is above 900"),
    ({"display_weight": 450.5}, "look.display_weight: must be integer, got float"),
    ({"display_weight": "bold"}, "look.display_weight: must be integer, got str"),
    ({"font": "bold"}, "look.font: 'bold' is not one of display, running"),
    ({"font": None}, "look.font: must be string, got null"),
])
def test_a_look_value_that_breaks_a_rule_is_named_in_one_plain_line(look, fragment):
    bad = styles.problems(doc(set={}, look=look), "demo.json")
    assert any(fragment in line for line in bad), bad
    assert all(line.startswith("demo.json: ") for line in bad), bad


@pytest.mark.parametrize("look", [
    {}, {"accent": "#ffd60a"}, {"accent": "#FFD60A", "text": "#ffffff", "on_accent": "#101418"}, {"hero_stroke": {"width": 0, "color": "#000000"}},
    {"hero_stroke": {"width": 40, "color": "#000000"}}, {"hero_stroke": {"width": 2.0, "color": "#000000"}}, {"display_weight": 100}, {"display_weight": 900},
    {"display_weight": 700.0}, {"font": "display"}, {"font": "running"}, LOOK_ALL,
])
def test_a_look_block_that_keeps_the_rules_is_sound(look):
    assert styles.problems(doc(set={}, look=look), "demo.json") == []


@pytest.mark.parametrize("look", ["red", None, [], 5])
def test_a_look_that_is_not_an_object_is_one_plain_line(look):
    bad = styles.problems(doc(look=look), "demo.json")
    assert bad == ["demo.json: look must be an object of colors and the font slot"]


@pytest.mark.parametrize("field,low,high", [
    ("width", 300, 1080), ("top_y0", 0, 1920), ("hero_max", 50, 800), ("chin_gap", 0, 600), ("glow_px", 0, 80), ("text_opacity", 0, 1), ("text_soften", 0, 20),
    ("whip_frames", 0, 30), ("whip_amount", 0, 1),
])
def test_each_number_is_held_to_its_range_and_both_ends_are_allowed(field, low, high):
    assert styles.problems(doc(set={field: low}), "demo.json") == [] and styles.problems(doc(set={field: high}), "demo.json") == []
    assert styles.problems(doc(set={field: low - 1}), "demo.json") == [f"demo.json: set.{field}: {low - 1} is below {low}"]
    assert styles.problems(doc(set={field: high + 1}), "demo.json") == [f"demo.json: set.{field}: {high + 1} is above {high}"]


def test_row_px_is_two_whole_rows_from_20_to_400_and_the_first_is_the_smaller():
    for ok in ([20, 20], [20, 400], [400, 400], [88, 104], [88.0, 104.0]):
        assert styles.problems(doc(set={"row_px": ok}), "demo.json") == [], ok
    assert styles.problems(doc(set={"row_px": [104, 88]}), "demo.json") == ["demo.json: set.row_px: the first number is the smaller row (minimum, then maximum)"]
    assert styles.problems(doc(set={"row_px": [88, 104, 120]}), "demo.json")[0] == "demo.json: set.row_px: needs 2 items"
    assert styles.problems(doc(set={"row_px": []}), "demo.json")[0] == "demo.json: set.row_px: needs 2 items"
    assert styles.problems(doc(set={"row_px": [10, 500]}), "demo.json") == ["demo.json: set.row_px[0]: 10 is below 20", "demo.json: set.row_px[1]: 500 is above 400"]


def test_the_numbers_are_held_to_the_captions_schemas_own_ranges():
    with open(styles.SCHEMA, encoding="utf-8") as fh:
        props = json.load(fh)["properties"]["captions"]["properties"]
    held = 0
    for k in styles.ALLOWED:
        sch = props[k]
        if "maximum" in sch and not sch.get("items"):
            assert any("above" in line for line in styles.problems(doc(set={k: sch["maximum"] + 1}), "demo.json")), k
            held += 1
        if "minimum" in sch and not sch.get("items"):
            assert any("below" in line for line in styles.problems(doc(set={k: sch["minimum"] - 1}), "demo.json")), k
    assert held >= 9


def test_the_text_of_a_style_has_no_house_punctuation_rule_but_stays_one_line():
    """a neutral engine takes any punctuation: only a missing, empty or multi line sentence is a problem"""
    dashes = chr(0x2014) + chr(0x2013)
    assert styles.problems(doc(use_when="fast lists " + dashes + " and numbers"), "demo.json") == []
    assert any("one line" in line for line in styles.problems(doc(summary="line one\nline two"), "demo.json"))


# ------------------------------------------------------------------ the schema file hook

def schema_file(tmp_path, name, captions):
    path = tmp_path / name
    write_raw(path, {"properties": {"captions": {"properties": captions}}})
    return str(path)


def test_a_schema_file_named_by_caption_schema_replaces_only_the_fields_it_defines(tmp_path, monkeypatch):
    narrow = schema_file(tmp_path, "narrow.json", {"width": {"type": "integer", "minimum": 400, "maximum": 500}})
    assert styles.problems(doc(set={"width": 600}), "demo.json") == [], "the schema that ships allows 600"
    monkeypatch.setenv(styles.ENV_SCHEMA, narrow)
    assert styles.problems(doc(set={"width": 600}), "demo.json") == ["demo.json: set.width: 600 is above 500"]
    assert styles.problems(doc(set={"width": 399}), "demo.json") == ["demo.json: set.width: 399 is below 400"]
    assert styles.problems(doc(set={"width": 450}), "demo.json") == []
    assert styles.problems(doc(set={"hero_max": 800, "whip_frames": 30, "row_px": [20, 400]}), "demo.json") == [], "every other field keeps the built-in range"
    assert styles.problems(doc(set={"hero_max": 801}), "demo.json") == ["demo.json: set.hero_max: 801 is above 800"]
    assert styles.problems(doc(set={"zone": "bottom"}), "demo.json") == ["demo.json: set.zone: 'bottom' is not one of top, chest"]
    assert styles.problems(doc(set={}, look={"display_weight": 901}), "demo.json") == ["demo.json: look.display_weight: 901 is above 900"]


def test_the_schema_file_decides_the_check_the_load_and_the_whole_number_rule(tmp_path, monkeypatch):
    lib = tmp_path / "styles"
    lib.mkdir()
    write(lib, doc(id="wide", set={"width": 600}))
    assert styles.check(str(lib))[1] == [] and styles.load("wide", str(lib))["set"] == {"width": 600}
    monkeypatch.setenv(styles.ENV_SCHEMA, schema_file(tmp_path, "narrow.json", {"width": {"type": "integer", "minimum": 400, "maximum": 500}}))
    assert styles.check(str(lib))[1] == ["wide.json: set.width: 600 is above 500"]
    with pytest.raises(styles.StyleError, match=r"wide\.json: set\.width: 600 is above 500"):
        styles.load("wide", str(lib))
    base = base_look("varsans")
    assert type(styles.apply(base, doc(set={"width": 450.0}))["width"]) is int, "the schema says integer, so a whole number written with a point is an int"
    monkeypatch.setenv(styles.ENV_SCHEMA, schema_file(tmp_path, "fractional.json", {"width": {"type": "number", "minimum": 400, "maximum": 500}}))
    assert styles.problems(doc(set={"width": 450.5}), "demo.json") == []
    got = styles.apply(base, doc(set={"width": 450.0}))["width"]
    assert got == 450.0 and type(got) is float, "the schema says number, so the point stays"


def test_a_schema_entry_with_a_type_or_an_enum_replaces_the_built_in_and_one_without_does_not(tmp_path, monkeypatch):
    path = schema_file(tmp_path, "mixed.json", {"zone": {"enum": ["top", "chest", "bottom"]}, "hero_max": {"minimum": 400}, "width": {"$ref": "#/definitions/x"},
                                                "default_style": {"type": "string"}})
    monkeypatch.setenv(styles.ENV_SCHEMA, path)
    assert styles.problems(doc(set={"zone": "bottom"}), "demo.json") == [], "an enum alone is a definition"
    assert styles.problems(doc(set={"zone": "middle"}), "demo.json") == ["demo.json: set.zone: 'middle' is not one of top, chest, bottom"]
    assert styles.problems(doc(set={"hero_max": 100}), "demo.json") == [], "a bare minimum defines nothing, the built-in 50 stays"
    assert styles.problems(doc(set={"width": 299}), "demo.json") == ["demo.json: set.width: 299 is below 300"], "a $ref defines nothing either"
    assert any("set.default_style is not a field a style may set" in line for line in styles.problems(doc(set={"default_style": "x"}), "demo.json")), \
        "a schema cannot widen what a style may set"


@pytest.mark.parametrize("content", ["{not json", "[]", '{"properties": {}}', '{"properties": {"captions": []}}', '{"properties": []}', "null", ""])
def test_a_schema_file_that_cannot_be_read_falls_back_to_the_built_in_table(tmp_path, monkeypatch, content):
    path = tmp_path / "schema.json"
    path.write_text(content)
    monkeypatch.setenv(styles.ENV_SCHEMA, str(path))
    assert styles.problems(doc(set={"width": 1081}), "demo.json") == ["demo.json: set.width: 1081 is above 1080"]
    assert styles.problems(doc(set={"width": 1080, "hero_max": 800}), "demo.json") == []


def test_a_missing_schema_file_or_one_that_is_a_folder_falls_back_to_the_built_in_table(tmp_path, monkeypatch):
    monkeypatch.setenv(styles.ENV_SCHEMA, str(tmp_path / "no-such-schema.json"))
    assert styles.problems(doc(set={"width": 299}), "demo.json") == ["demo.json: set.width: 299 is below 300"]
    monkeypatch.setenv(styles.ENV_SCHEMA, str(tmp_path))
    assert styles.problems(doc(set={"width": 299}), "demo.json") == ["demo.json: set.width: 299 is below 300"]
    monkeypatch.delenv(styles.ENV_SCHEMA)
    monkeypatch.setattr(styles, "SCHEMA", str(tmp_path / "gone" / "schema.json"))
    assert styles.problems(doc(set={"width": 299}), "demo.json") == ["demo.json: set.width: 299 is below 300"], "the schema file that ships can be missing too"
    assert styles.problems(doc(set={"width": 1080, "row_px": [20, 400]}), "demo.json") == []


# ------------------------------------------------------------------ a library with a bad file in it

def test_a_bad_file_is_reported_and_never_hides_or_breaks_the_good_ones(lib):
    write(lib, doc(id="tinted", set={"hero_tint": "#FFFFFF"}))
    (lib / "broken.json").write_text("{nope")
    (lib / "README.txt").write_text("not a style")
    docs, bad = styles.check()
    assert {d["id"] for d in docs} == set(FIXTURE_IDS) and "tinted" not in {d["id"] for d in docs}
    assert any("tinted.json: set.hero_tint is not a field a style may set" in line for line in bad)
    assert any(line.startswith("broken.json: is not valid JSON") for line in bad) and len(bad) == 2
    with pytest.raises(styles.StyleError) as e:
        styles.load_all()
    assert len(e.value.problems) == 2
    assert styles.load("keyword-slam")["id"] == "keyword-slam", "a take that asks for a good style is never stopped by a bad file elsewhere"
    with pytest.raises(styles.StyleError, match="tinted.json: set.hero_tint"):
        styles.load("tinted")


def test_a_hidden_file_and_a_file_that_is_a_folder_are_handled_without_stopping(lib):
    (lib / ".hidden.json").write_text("{nope")
    (lib / "._keyword-slam.json").write_bytes(b"\x00\x05\x16\x07")
    (lib / "folder.json").mkdir()
    docs, bad = styles.check()
    assert {d["id"] for d in docs} == set(FIXTURE_IDS), "a hidden file, such as a copy a file manager leaves, is not a style"
    assert bad == ["folder.json: cannot be read (Is a directory)"]
    assert styles.ids() == sorted(FIXTURE_IDS + ("folder",))


def test_a_file_that_cannot_be_read_names_the_file(tmp_path):
    for name, content in (("nope.json", None), ("broken.json", "{nope"), ("empty.json", "")):
        if content is not None:
            (tmp_path / name).write_text(content)
        with pytest.raises(styles.StyleError) as e:
            styles.read(str(tmp_path / name))
        assert len(e.value.problems) == 1 and e.value.problems[0].startswith(f"{name}: "), (name, e.value.problems)
    assert styles.read(os.path.join(FIXTURE_STYLES, "soft-build.json"))["id"] == "soft-build"


def test_a_style_file_whose_id_is_not_its_name_is_named_by_its_file(lib):
    write_raw(lib / "other.json", {k: v for k, v in doc().items() if k != "preview"})
    docs, bad = styles.check()
    assert bad == ["other.json: id demo must be the file name (a file called demo.json)"]
    assert "demo" not in {d["id"] for d in docs}
    with pytest.raises(styles.StyleError, match="other.json: id demo must be the file name"):
        styles.load("other")


def test_a_style_that_is_broken_is_never_swapped_for_the_starter_of_the_same_id(workspace):
    """load reads the first file in the search order that holds the id: a broken file of yours stops the take with its own problem, never a quiet fallback"""
    brand = cap_helpers.install_brand(workspace, "varsans")
    starter = [d for d in styles.check(styles.STARTER_DIR)[0] if not os.path.isfile(os.path.join(brand, "styles", d["id"] + ".json"))][0]["id"]
    (open(os.path.join(brand, "styles", starter + ".json"), "w")).write("{nope")
    with pytest.raises(styles.StyleError, match=rf"{starter}\.json: is not valid JSON"):
        styles.load(starter)
    assert any(line.startswith(f"{starter}.json: is not valid JSON") for line in styles.check()[1])


def test_load_names_the_first_problem_and_counts_the_rest_in_one_line(lib):
    write_raw(lib / "many.json", doc(id="many", set={"hero_max": 1, "width": 1, "zone": "x"}, look={"accent": "x"}))
    with pytest.raises(styles.StyleError) as e:
        styles.load("many")
    assert e.value.problems == ["many.json: set.hero_max: 1 is below 50 (and 3 more; python3 styles.py check)"]
    assert len(styles.problems(doc(id="many", set={"hero_max": 1, "width": 1, "zone": "x"}, look={"accent": "x"}), "many.json")) == 4


def test_a_style_error_is_one_line_or_a_list_of_them():
    assert styles.StyleError("one").problems == ["one"] and str(styles.StyleError("one")) == "one"
    assert styles.StyleError(["a", "b"]).problems == ["a", "b"] and str(styles.StyleError(["a", "b"])) == "a; b"
    assert isinstance(styles.StyleError("x"), ValueError)


def test_a_library_with_no_folder_at_all_is_one_problem(tmp_path, monkeypatch, config):
    monkeypatch.setattr(styles, "STARTER_DIR", str(tmp_path / "no-starters"))
    assert styles.styles_dirs() == []
    docs, bad = styles.check()
    assert docs == [] and bad == ["there is no caption style library: no folder of styles was found"]
    with pytest.raises(styles.StyleError, match=r"unknown caption style 'keyword-slam' \(known: none; python3 styles.py list\)"):
        styles.load("keyword-slam")


def test_a_folder_named_for_the_library_that_is_missing_is_an_empty_library(tmp_path):
    missing = str(tmp_path / "nope")
    assert styles.ids(missing) == []
    with pytest.raises(styles.StyleError, match=r"unknown caption style 'soft-build' \(known: none; python3 styles.py list\)"):
        styles.load("soft-build", missing)


def test_an_unknown_style_names_the_known_ones(lib):
    with pytest.raises(styles.StyleError) as e:
        styles.load("hero-lock-up")
    assert "unknown caption style 'hero-lock-up'" in str(e.value) and "hero-lockup" in str(e.value) and "keyword-slam" in str(e.value)
    assert str(e.value).endswith("; python3 styles.py list)")
    for bad in ("Keyword Slam", "Keyword-Slam", "", None, 5, "../x", "-slam", "slam-"):
        with pytest.raises(styles.StyleError, match="is not a caption style id"):
            styles.load(bad)


# ------------------------------------------------------------------ where the library is

def test_the_library_is_yours_then_the_starters_and_a_style_of_yours_beats_a_starter_of_the_same_id(workspace):
    brand = cap_helpers.install_brand(workspace, "varsans")
    mine = os.path.join(brand, "styles")
    assert styles.user_dir() == mine and styles.styles_dirs() == [mine, styles.STARTER_DIR]
    starters = sorted(f[:-len(".json")] for f in os.listdir(styles.STARTER_DIR) if f.endswith(".json"))
    yours = sorted(f[:-len(".json")] for f in os.listdir(mine) if f.endswith(".json"))
    starter_only, yours_only = sorted(set(starters) - set(yours)), sorted(set(yours) - set(starters))
    assert starter_only and yours_only
    assert styles.ids() == sorted(set(starters) | set(yours))
    for sid in yours_only:
        assert styles.where(styles.load(sid)) == "yours", sid
    for sid in starter_only:
        assert styles.where(styles.load(sid)) == "starter", sid
    target = starter_only[0]
    write(mine, doc(id=target, summary="My own take on a starter.", set={"hero_max": 321}))
    d = styles.load(target)
    assert styles.where(d) == "yours" and d["summary"] == "My own take on a starter." and d["set"] == {"hero_max": 321}
    assert d["_dir"] == mine and styles.ids().count(target) == 1
    docs, bad = styles.check()
    assert bad == [] and [x["id"] for x in docs].count(target) == 1 and next(x for x in docs if x["id"] == target)["summary"] == "My own take on a starter."
    assert [x["id"] for x in docs] == sorted(x["id"] for x in docs) == styles.ids()


def test_caption_styles_dir_is_the_whole_library_and_a_folder_asked_for_beats_it(workspace, tmp_path, monkeypatch):
    cap_helpers.install_brand(workspace, "varsans")
    one, other = tmp_path / "one", tmp_path / "other"
    one.mkdir()
    other.mkdir()
    write(one, doc(id="only-here"))
    write(other, doc(id="over-there"))
    monkeypatch.setenv(styles.ENV_DIR, str(one))
    assert styles.styles_dirs() == [str(one)] and styles.ids() == ["only-here"], "neither yours nor the starters are searched"
    assert styles.where(styles.load("only-here")) == "folder"
    with pytest.raises(styles.StyleError, match=r"unknown caption style 'quiet' \(known: only-here; python3 styles.py list\)"):
        styles.load("quiet")
    assert styles.ids(str(other)) == ["over-there"] and styles.check(str(other))[1] == [] and styles.load("over-there", str(other))["id"] == "over-there"
    monkeypatch.delenv(styles.ENV_DIR)
    assert styles.ids() != ["only-here"] and "only-here" not in styles.ids()


def test_the_library_of_yours_is_the_brand_styles_folder_of_the_configured_workspace(config, tmp_path):
    ws = tmp_path / "ws"
    config.write_text(json.dumps({"workspace": str(ws)}))
    mine = os.path.join(str(ws), "Brand", "styles")
    assert styles.user_dir() == mine
    assert styles.styles_dirs() == [styles.STARTER_DIR], "the folder is not there yet, so only the starters are searched"
    os.makedirs(mine)
    assert styles.styles_dirs() == [mine, styles.STARTER_DIR]
    config.write_text(json.dumps({"workspace": "~/acme-workspace"}))
    assert styles.user_dir() == os.path.join(os.path.expanduser("~/acme-workspace"), "Brand", "styles"), "a ~ in the workspace is the home folder"


def test_no_workspace_or_a_config_that_will_not_read_means_no_styles_of_yours_and_never_stops(config, capsys):
    assert styles.user_dir() is None, "no config.json at all"
    config.write_text(json.dumps({"workspace": ""}))
    assert styles.user_dir() is None and styles.styles_dirs() == [styles.STARTER_DIR]
    config.write_text("{not json")
    assert styles.user_dir() is None and styles.styles_dirs() == [styles.STARTER_DIR]
    capsys.readouterr()   # common says why on stderr, styles.py carries on with the starters
    assert styles.ids() == sorted(f[:-len(".json")] for f in os.listdir(styles.STARTER_DIR) if f.endswith(".json"))


def test_where_says_yours_starter_or_folder(workspace, tmp_path):
    cap_helpers.install_brand(workspace, "varsans")
    assert styles.where({"_dir": styles.user_dir()}) == "yours"
    assert styles.where({"_dir": styles.STARTER_DIR}) == "starter"
    assert styles.where({"_dir": str(tmp_path)}) == "folder"
    assert styles.where({}) == "folder", "a style that was not read from a library is in no library"


def test_text_of_writes_the_known_keys_in_their_fixed_order_and_reads_back_the_same(lib):
    d = doc(id="round-trip", summary="A café style.", added="2031-04-09", look={"accent": "#E8456B", "font": "display"}, feed={"place": "chest"}, set={"zone": "top", "hero_max": 300})
    write(lib, d)
    text = (lib / "round-trip.json").read_text(encoding="utf-8")
    assert text.endswith("}\n") and '\n  "id": "round-trip",' in text and "café" in text
    keys = list(json.loads(text))
    assert keys == [k for k in styles.KEYS if k in d] and keys[-3:] == ["set", "look", "feed"]
    loaded = styles.load("round-trip")
    assert styles.text_of(loaded) == text, "read and written back the same, and the folder it came from is not a file key"
    assert {k: v for k, v in loaded.items() if k != "_dir"} == d
    assert styles.text_of({**d, "unknown": 1, "_dir": "x"}) == text


# ------------------------------------------------------------------ laying a style over a look

@pytest.mark.parametrize("brand_id", cap_helpers.BRANDS)
@pytest.mark.parametrize("sid", LAYOUT_ONLY)
def test_a_style_with_no_look_block_moves_layout_and_motion_fields_and_nothing_else(brand_id, sid):
    base = base_look(brand_id)
    before = copy.deepcopy(base)
    d = fx(sid)
    got = styles.apply(base, d)
    assert got["caption_style"] == sid and "caption_style" not in base and base == before, "the look's own profile is never changed in place"
    assert changed_keys(base, got) - {"caption_style"} <= set(d["set"]) <= set(styles.ALLOWED)
    for k, v in d["set"].items():
        if k != "caption_tier_max":
            assert got[k] == v, f"{k}: the style's value lands"
    for k in NAMES + WORDS + FACE + tuple(LOOK_LANDS_ON.values()):
        assert got.get(k) == base.get(k), f"{k} moved"


def test_every_starter_laid_over_either_look_moves_only_the_fields_it_sets():
    docs, bad = styles.check(styles.STARTER_DIR)
    assert bad == [] and docs
    for brand_id in cap_helpers.BRANDS:
        base = base_look(brand_id)
        for d in docs:
            got = styles.apply(base, d)
            assert got["caption_style"] == d["id"]
            assert changed_keys(base, got) - {"caption_style"} <= set(d["set"]), (brand_id, d["id"])
            for k in NAMES + WORDS + FACE + tuple(LOOK_LANDS_ON.values()):
                assert got.get(k) == base.get(k), (brand_id, d["id"], k)


def test_the_style_goes_over_the_look_so_a_field_it_leaves_out_stays_the_looks():
    base = base_look("upstatic")
    wider = base["hero_max"] + 60
    got = styles.apply(base, doc(set={"hero_max": wider}))
    assert got["hero_max"] == wider and got["zone"] == base["zone"] and got["width"] == base["width"] and got["row_px"] == base["row_px"] and got["case"] == base["case"]
    assert styles.diff(base, doc(set={"hero_max": wider})) == [("hero_max", base["hero_max"], wider)]
    assert styles.diff(base, doc(set={"hero_max": base["hero_max"]})) == [], "a style that sets what the look already has changes nothing"


def test_diff_lists_set_fields_first_in_the_order_the_file_lists_them_then_look_fields_under_their_profile_names():
    base = base_look("varsans")
    d = doc(set={"zone": "top", "hero_max": base["hero_max"] + 10, "width": base["width"]}, look={"text": "#F7F2EE", "accent": base["accent"], "display_weight": 700})
    assert styles.diff(base, d) == [("zone", "chest", "top"), ("hero_max", base["hero_max"], base["hero_max"] + 10), ("base", base["base"], "#F7F2EE"),
                                    ("display_weight", base["display_weight"], 700)], "width and accent are the look's own values, so they are not changes"
    assert styles.diff(base, doc(set={})) == []


@pytest.mark.parametrize("look_cap,style_cap,want", [("FULL", "REDUCED", "REDUCED"), ("FULL", "MINIMAL", "MINIMAL"), ("REDUCED", "FULL", "REDUCED"),
                                                     ("REDUCED", "MINIMAL", "MINIMAL"), ("MINIMAL", "FULL", "MINIMAL"), ("MINIMAL", "REDUCED", "MINIMAL"),
                                                     ("REDUCED", "REDUCED", "REDUCED"), ("FULL", "FULL", "FULL")])
def test_a_style_can_only_lower_the_tier_cap_of_the_look(look_cap, style_cap, want):
    base = {**base_look("varsans"), "caption_tier_max": look_cap}
    assert styles.apply(base, doc(set={"caption_tier_max": style_cap}))["caption_tier_max"] == want
    assert styles.TIER_ORDER == ("MINIMAL", "REDUCED", "FULL")


def test_a_look_with_no_tier_cap_takes_the_styles():
    base = {k: v for k, v in base_look("varsans").items() if k != "caption_tier_max"}
    assert styles.apply(base, doc(set={"caption_tier_max": "REDUCED"}))["caption_tier_max"] == "REDUCED"
    assert base_look("varsans")["caption_tier_max"] == "FULL", "and the look the DNA makes caps at the top"


@pytest.mark.parametrize("key", ["accent", "hero_tint", "font", "font_family", "places", "master_audio", "caption_style", "id", "handle", "spelling"])
def test_apply_refuses_a_style_built_in_code_that_reaches_for_a_field_it_may_not_set(key):
    """the file check already refuses these; apply says it again, so a style that never went through a file cannot move a name or a color in set either"""
    with pytest.raises(styles.StyleError, match=rf"caption style demo: set\.{key} is not a field a style may set"):
        styles.apply(base_look("varsans"), doc(set={key: "x"}))


@pytest.mark.parametrize("key", ["hero_max", "zone", "soft_build", "places", "font_family", "caption_style"])
def test_apply_refuses_a_look_key_that_is_not_a_look_key(key):
    with pytest.raises(styles.StyleError, match=rf"caption style demo: look\.{key} is not a field a style may set"):
        styles.apply(base_look("varsans"), doc(set={}, look={key: "x"}))


@pytest.mark.parametrize("target", ["id", "brand", "handle", "spelling", "do_not_say", "font_family", "font_files"])
def test_apply_asserts_the_names_the_taught_words_and_the_face_files_unchanged_on_the_way_out(target, monkeypatch):
    monkeypatch.setitem(styles.LOOK_PROFILE_KEY, "accent", target)
    with pytest.raises(styles.StyleError, match=rf"caption style demo would change {target}: names, taught words and font files never move"):
        styles.apply(base_look("varsans"), doc(set={}, look={"accent": "#E8456B"}))


def test_the_profile_is_copied_and_the_styles_own_lists_and_dicts_are_not_shared_with_it():
    base = base_look("varsans")
    d = fx("keyword-slam")
    style_rows, brand_rows = list(d["set"]["row_px"]), list(base["row_px"])
    got = styles.apply(base, d)
    got["row_px"].append(1)
    assert d["set"]["row_px"] == style_rows and base["row_px"] == brand_rows
    own = doc(set={}, look={"hero_stroke": {"width": 3, "color": "#101418"}})
    got = styles.apply(base, own)
    got["hero_stroke"]["width"] = 9
    assert own["look"]["hero_stroke"] == {"width": 3, "color": "#101418"} and base["hero_stroke"] is None


def test_a_whole_number_written_with_a_point_is_an_integer_in_the_profile_as_it_is_in_a_style_file():
    got = styles.apply(base_look("varsans"), doc(set={"hero_max": 320.0, "row_px": [72.0, 88.0], "whip_amount": 1.0, "text_opacity": 0.9}))
    assert got["hero_max"] == 320 and type(got["hero_max"]) is int and [type(x) for x in got["row_px"]] == [int, int]
    assert type(got["whip_amount"]) is float and got["text_opacity"] == 0.9, "a number field keeps its point"


def test_describe_names_what_a_style_sets():
    assert styles.describe(doc(set={"zone": "chest", "row_px": [72, 88], "hero_glow": False, "hero_max": 320})) == "zone chest, row_px 72-88, hero_glow false, hero_max 320"
    assert styles.describe(doc(set={})) == "your own look, nothing laid over it"
    assert styles.describe(doc(set={"zone": "top"}, look={"accent": "#FFD60A", "hero_stroke": {"width": 2, "color": "#000000"}, "font": "running"})) == \
        'zone top, look accent #FFD60A, look hero_stroke {"width": 2, "color": "#000000"}, look font running'
    assert styles.describe(fx("rose-running")).endswith("look display_weight 700, look font running")


def test_a_feed_block_never_changes_what_the_engine_lays_over_a_look():
    feed = {"place": "chest", "size": 1.0, "hero": {"look": "bigger", "scale": 1.9, "pick": "page"}, "words": 3}
    for brand_id in cap_helpers.BRANDS:
        base = base_look(brand_id)
        with_feed, without = doc(feed=feed), doc()
        assert styles.apply(base, with_feed) == styles.apply(base, without), "the chain reads `set` and `look` only"
        assert styles.diff(base, with_feed) == styles.diff(base, without)
        assert styles.describe(with_feed) == styles.describe(without)
    assert "feed" not in styles.apply(base_look("varsans"), doc(feed=feed))


# ------------------------------------------------------------------ the look block

def test_the_look_block_lays_colors_the_shadow_the_stroke_and_the_weight_over_the_look():
    base = base_look("varsans")
    for k, v in LOOK_ALL.items():
        assert base[LOOK_LANDS_ON[k]] != v, f"{k}: the fixture look must not already have it"
    before = copy.deepcopy(base)
    d = doc(set={"hero_max": 300}, look=copy.deepcopy(LOOK_ALL))
    assert styles.problems(d, "demo.json") == []
    got = styles.apply(base, d)
    for k, v in LOOK_ALL.items():
        assert got[styles.LOOK_PROFILE_KEY[k]] == v, k
    assert got["base"] == "#F7F2EE" and got["accent"] == "#E8456B" and got["hero_stroke"] == {"width": 3, "color": "#101418"} and got["display_weight"] == 700
    assert got["caption_style"] == "demo" and got["hero_max"] == 300
    assert changed_keys(base, got) == {"caption_style", "hero_max", *LOOK_LANDS_ON.values()}
    for k in NAMES + WORDS + FACE:
        assert got[k] == base[k], f"{k} moved"
    assert base == before, "the look's own profile is never changed in place"


def test_a_look_block_with_one_key_moves_only_that_key():
    base = base_look("upstatic")
    got = styles.apply(base, doc(set={}, look={"accent": "#E8456B"}))
    assert got["accent"] == "#E8456B" and changed_keys(base, got) == {"accent", "caption_style"}
    got = styles.apply(base, doc(set={}, look={"text": "#F7F2EE"}))
    assert got["base"] == "#F7F2EE" and got["accent"] == base["accent"] and changed_keys(base, got) == {"base", "caption_style"}
    assert styles.apply(base, doc(set={}, look={})) == {**base, "caption_style": "demo"}
    assert styles.apply(base, doc(set={"hero_max": base["hero_max"]}, look={"accent": base["accent"]})) == {**base, "caption_style": "demo"}


def test_a_stroke_the_look_never_had_and_a_stroke_it_did_both_take_the_styles():
    plain, stroked = base_look("varsans"), base_look("upstatic")
    assert plain["hero_stroke"] is None and stroked["hero_stroke"]["width"] == 2
    mine = {"width": 5, "color": "#101418"}
    assert styles.apply(plain, doc(set={}, look={"hero_stroke": mine}))["hero_stroke"] == mine
    assert styles.apply(stroked, doc(set={}, look={"hero_stroke": mine}))["hero_stroke"] == mine, "the style's stroke replaces the look's whole, never a merge"
    assert styles.apply(stroked, doc(set={}, look={"hero_stroke": {"width": 0, "color": stroked["hero_stroke"]["color"]}}))["hero_stroke"]["width"] == 0


def test_a_whole_number_float_in_the_look_block_is_an_integer_in_the_profile():
    got = styles.apply(base_look("varsans"), doc(set={}, look={"display_weight": 700.0, "hero_stroke": {"width": 2.0, "color": "#101418"}}))
    assert got["display_weight"] == 700 and type(got["display_weight"]) is int
    assert got["hero_stroke"] == {"width": 2, "color": "#101418"} and type(got["hero_stroke"]["width"]) is int


def test_look_font_display_is_the_display_face_and_changes_nothing():
    for brand_id in cap_helpers.BRANDS:
        base = base_look(brand_id)
        assert styles.apply(base, doc(set={}, look={"font": "display"})) == {**base, "caption_style": "demo"}
        assert styles.diff(base, doc(set={}, look={"font": "display"})) == []


def test_look_font_running_draws_the_words_in_the_running_face_and_keeps_the_display_face_for_page_text():
    base = base_look("upstatic")
    running = base["word_font"]
    got = styles.apply(base, doc(set={}, look={"font": "running"}))
    for dst, src in (("font_family", "family"), ("font_files", "files"), ("font_abs", "abs"), ("font_faces", "faces"), ("fonts_dir", "fonts_dir"), ("font_prefix", "prefix")):
        assert got[dst] == running[src], dst
        if dst != "fonts_dir":
            assert got[dst] != base[dst], f"{dst}: the two faces differ"
    assert got["display_font"] == base["display_font"] and got["display_font"]["family"] == base["font_family"], "page and pill text stay in the display face"
    assert got["word_font"] == running
    assert all(os.path.isfile(p) for p in got["font_abs"].values()) and {os.path.basename(p) for p in got["font_abs"].values()} == {"Inter-Variable.ttf"}
    for k in NAMES + WORDS + ("accent", "base", "hero_max", "zone"):
        assert got[k] == base[k], k
    assert changed_keys(base, got) <= {"caption_style", "font", "font_family", "font_files", "font_abs", "font_faces", "fonts_dir", "font_prefix"}
    assert styles.diff(base, doc(set={}, look={"font": "running"})) == [("font_family", base["font_family"], running["family"])]


def test_look_font_running_records_the_display_face_when_the_profile_does_not_carry_it_yet():
    base = {k: v for k, v in base_look("upstatic").items() if k != "display_font"}
    got = styles.apply(base, doc(set={}, look={"font": "running"}))
    assert got["display_font"] == {"fonts_dir": base["fonts_dir"], "prefix": base["font_prefix"], "family": base["font_family"], "faces": base["font_faces"]}
    assert got["display_font"]["faces"] is not base["font_faces"], "a copy, not the profile's own list"


def test_look_font_running_on_a_look_without_a_running_face_is_refused_in_one_plain_line():
    expected = ["caption style rose-running: look.font is running, but your look has no running font (add one to your DNA, or leave look.font out)"]
    base = base_look("varsans")
    assert base["word_font"] is None
    with pytest.raises(styles.StyleError) as e:
        styles.apply(base, fx("rose-running"))
    assert e.value.problems == expected
    with pytest.raises(styles.StyleError) as e:
        styles.diff(base, fx("rose-running"))
    assert e.value.problems == expected
    no_faces = {**base_look("upstatic"), "word_font": {**base_look("upstatic")["word_font"], "faces": []}}
    with pytest.raises(styles.StyleError, match="look.font is running, but your look has no running font"):
        styles.apply(no_faces, fx("rose-running"))
    assert styles.apply(base_look("upstatic"), fx("rose-running"))["caption_style"] == "rose-running", "and a look that has one takes it"


def test_the_rose_running_style_lays_its_colors_its_weight_and_the_running_face_over_upstatic():
    base = base_look("upstatic")
    d = fx("rose-running")
    got = styles.apply(base, d)
    for k, v in d["look"].items():
        if k != "font":
            assert got[LOOK_LANDS_ON[k]] == v, k
    assert got["zone"] == d["set"]["zone"] and got["hero_max"] == d["set"]["hero_max"] and got["caption_style"] == "rose-running"
    assert got["font_family"] == base["word_font"]["family"] != base["font_family"]
    assert [k for k, _, _ in styles.diff(base, d)] == [k for k in ("hero_max", "accent", "base", "on_accent", "hero_tint", "hero_shadow", "chip_neg", "display_weight",
                                                                  "font_family")], "zone is the look's own value already"
    assert base["accent"] == "#38E1B0", "the colors of the DNA itself did not move"


# ------------------------------------------------------------------ the same answer as the DNA module

@pytest.mark.parametrize("brand_id", cap_helpers.BRANDS)
@pytest.mark.parametrize("sid", LAYOUT_ONLY)
def test_styles_apply_and_the_dna_module_lay_a_style_over_a_look_the_same_way(brand_id, sid):
    got = styles.apply(base_look(brand_id), fx(sid))
    want = cap_helpers.profile(brand_id, sid)
    assert got["caption_style"] == want["caption_style"] == sid
    assert {k: v for k, v in got.items() if k not in PARITY_SKIP} == {k: v for k, v in want.items() if k not in PARITY_SKIP}


def test_a_style_with_a_look_block_and_no_font_swap_agrees_with_the_dna_module(tmp_path):
    bd = cap_helpers.materialize("varsans", str(tmp_path / "Brand"))
    write(os.path.join(bd, "styles"), doc(id="rose", set={"zone": "chest", "hero_max": 230}, look=LOOK_ALL))
    d = dna.load(bd)
    base = dna.captions_profile(d, bd, dna.public_prefix(d))
    got = styles.apply(base, styles.load("rose", os.path.join(bd, "styles")))
    want = lookprofile.build(d, bd, "rose")
    assert want["accent"] == "#E8456B" and want["base"] == "#F7F2EE"
    assert {k: v for k, v in got.items() if k not in PARITY_SKIP} == {k: v for k, v in want.items() if k not in PARITY_SKIP}


def test_a_look_block_with_the_running_face_agrees_with_the_dna_module_on_colors_weight_and_face():
    """the DNA module also moves font, font_fallback and font_variable, its own bookkeeping for the slot; styles.apply leaves those three on the display face"""
    d = fx("rose-running")
    got = styles.apply(base_look("upstatic"), d)
    want = cap_helpers.profile("upstatic", "rose-running")
    for k in ("accent", "base", "on_accent", "hero_base", "hero_tint", "hero_shadow", "hero_stroke", "chip_neg", "display_weight"):
        assert got[k] == want[k], k
    assert got["display_weight"] == 700
    assert got["font_family"] == want["font_family"] == base_look("upstatic")["word_font"]["family"]
    for k in ("font_files", "font_abs", "font_faces", "fonts_dir", "font_prefix", "display_font", "word_font", "zone", "hero_max"):
        assert got[k] == want[k], k
    assert want["caption_style"] == "rose-running"


def test_both_refuse_a_style_that_asks_for_the_running_face_of_a_look_that_has_none(tmp_path):
    bd = cap_helpers.materialize("varsans", str(tmp_path / "Brand"))
    shutil.copyfile(os.path.join(FIXTURE_STYLES, "rose-running.json"), os.path.join(bd, "styles", "rose-running.json"))
    with pytest.raises(EngineStop, match="running"):
        lookprofile.build(dna.load(bd), bd, "rose-running")
    with pytest.raises(styles.StyleError, match="running"):
        styles.apply(base_look("varsans"), styles.load("rose-running", os.path.join(bd, "styles")))


def test_lookprofile_lays_the_style_over_the_look_and_names_a_bad_one(tmp_path):
    d, bd = cap_helpers.dna_of("upstatic"), cap_helpers.brand_dir("upstatic")
    plain = cap_helpers.profile("upstatic")
    assert "caption_style" not in plain and {k: v for k, v in plain.items() if k in _dna_profile("upstatic")} == _dna_profile("upstatic"), "no style: exactly what the DNA makes"
    styled = cap_helpers.profile("upstatic", "keyword-slam")
    assert styled["caption_style"] == "keyword-slam" and styled["zone"] == fx("keyword-slam")["set"]["zone"] and styled["accent"] == plain["accent"]
    with pytest.raises(EngineStop, match="style: unknown style 'nope'"):
        cap_helpers.profile("upstatic", "nope")
    mine = cap_helpers.materialize("upstatic", str(tmp_path / "Brand"))
    write(os.path.join(mine, "styles"), doc(id="acme-only", owner="varsans"))
    with pytest.raises(EngineStop, match="style: style 'acme-only' is private to varsans: upstatic cannot use it"):
        lookprofile.build(d, mine, "acme-only")
    write(os.path.join(mine, "styles"), doc(id="acme-only", owner="upstatic"))
    assert lookprofile.build(d, mine, "acme-only")["caption_style"] == "acme-only"
    assert bd and os.path.isdir(bd)


# ------------------------------------------------------------------ the command line

def run(*args, env=None):
    r = subprocess.run([sys.executable, "-B", SCRIPT, *args], capture_output=True, text=True, env=dict(os.environ, **(env or {})))
    return r.returncode, r.stdout, r.stderr


def test_check_and_list_on_the_fixture_library(lib):
    env = {styles.ENV_DIR: str(lib)}
    code, out, err = run("check", env=env)
    assert (code, err) == (0, "") and out.strip() == f"styles check: {len(FIXTURE_IDS)} sound, 0 problems"
    code, out, err = run("list", env=env)
    rows = out.splitlines()
    assert (code, err) == (0, "") and [r.split()[0] for r in rows] == list(FIXTURE_IDS)
    for row, sid in zip(rows, FIXTURE_IDS):
        d = fx(sid)
        assert "[folder]" in row and d["name"] in row and row.endswith(styles.describe(d)), row


def test_list_and_check_without_the_library_folder_set_mark_each_style_yours_or_a_starter(workspace):
    brand = cap_helpers.install_brand(workspace, "varsans")
    code, out, err = run("list")
    rows = {r.split()[0]: r for r in out.splitlines()}
    assert (code, err) == (0, "") and sorted(rows) == styles.ids()
    for sid, row in rows.items():
        want = "yours" if os.path.isfile(os.path.join(brand, "styles", sid + ".json")) else "starter"
        assert f"[{want}]" in row, sid
    code, out, err = run("check")
    assert (code, err) == (0, "") and out.strip() == f"styles check: {len(styles.ids())} sound, 0 problems"


def test_show_prints_the_card_and_with_diff_what_it_changes_in_your_look(workspace):
    cap_helpers.install_brand(workspace, "varsans")
    d = styles.load("keyword-slam")
    code, out, err = run("show", "keyword-slam")
    assert (code, err) == (0, "") and out.startswith("keyword-slam  Keyword slam  (built, yours)\n")
    assert f"use_when: {d['use_when']}" in out and f"never_when: {d['never_when']}" in out and f"sets: {styles.describe(d)}" in out
    assert "added: (none)" in out and "preview: (none)" in out and "in your look" not in out, "without --diff the card says nothing about your look"
    code, out, err = run("show", "keyword-slam", "--diff")
    changes = styles.diff(lookprofile.load(), d)
    assert changes, "keyword-slam is not the varsans look"
    assert (code, err) == (0, "") and "  in your look: it changes\n" in out
    for k, before, after in changes:
        assert f"    {k}: {before} -> {after}" in out
    assert "    accent:" not in out and "    font_family:" not in out, "a layout style changes no color and no face"
    code, out, err = run("show", "--diff", "keyword-slam")
    assert code == 0 and "  in your look: it changes\n" in out, "the flag may come first"


def test_show_diff_says_so_when_a_style_sets_what_the_look_already_has(workspace):
    brand = cap_helpers.install_brand(workspace, "varsans")
    write(os.path.join(brand, "styles"), doc(id="same-as-look", set={"zone": base_look("varsans")["zone"]}))
    code, out, err = run("show", "same-as-look", "--diff")
    assert (code, err) == (0, "") and "  in your look: no field changes (it sets what your look already has)" in out and "    zone:" not in out


def test_show_diff_of_a_style_with_a_look_block_lists_the_colors_and_the_face_it_changes(workspace):
    cap_helpers.install_brand(workspace, "upstatic")
    d = styles.load("rose-running")
    code, out, err = run("show", "rose-running", "--diff")
    changes = styles.diff(lookprofile.load(), d)
    assert (code, err) == (0, "") and {"accent", "base", "display_weight", "font_family"} <= {k for k, _, _ in changes}
    for k, before, after in changes:
        assert f"    {k}: {before} -> {after}" in out
    assert "    accent: #38E1B0 -> #E8456B\n" in out and "    font_family: InterStaticDyn -> InterRunDyn\n" in out
    assert f"sets: {styles.describe(d)}" in out and "look font running" in out


def test_show_diff_of_a_running_face_style_in_a_look_without_one_is_one_plain_stop(workspace, lib):
    cap_helpers.install_brand(workspace, "varsans")
    code, out, err = run("show", "rose-running", "--diff", env={styles.ENV_DIR: str(lib)})
    assert code == 2 and err.count("\n") == 1 and "Traceback" not in err
    assert err.startswith("STOP: caption style rose-running: look.font is running, but your look has no running font")
    assert "  in your look" not in out


def test_check_exits_1_and_names_every_problem_in_a_bad_library(lib):
    env = {styles.ENV_DIR: str(lib)}
    write(lib, doc(id="tinted", set={"hero_tint": "#FFFFFF"}))
    code, out, err = run("check", env=env)
    assert (code, err) == (1, "") and "FIX  tinted.json: set.hero_tint is not a field a style may set" in out
    assert out.strip().endswith(f"styles check: {len(FIXTURE_IDS)} sound, 1 problem"), out
    (lib / "broken.json").write_text("{nope")
    code, out, err = run("check", env=env)
    assert (code, err) == (1, "") and "FIX  broken.json: is not valid JSON" in out and out.strip().endswith("2 problems"), out


def test_a_style_asked_for_by_name_is_served_from_a_library_with_a_bad_file_in_it(lib):
    write(lib, doc(id="tinted", set={"hero_tint": "#FFFFFF"}))
    code, out, err = run("show", "keyword-slam", env={styles.ENV_DIR: str(lib)})
    assert (code, err) == (0, "") and out.startswith("keyword-slam  Keyword slam")
    code, out, err = run("show", "tinted", env={styles.ENV_DIR: str(lib)})
    assert code == 2 and out == "" and err.startswith("STOP: tinted.json: set.hero_tint is not a field a style may set") and err.count("\n") == 1


def test_a_bad_invocation_or_an_unknown_style_is_one_stop_line_on_stderr(lib):
    env = {styles.ENV_DIR: str(lib)}
    for args in (("show", "no-such-style"), ("show", "Keyword Slam"), ("show",), ("frobnicate",), ()):
        code, out, err = run(*args, env=env)
        assert code == 2 and out == "" and err.startswith("STOP: ") and err.count("\n") == 1 and "Traceback" not in err, (args, out, err)
    code, out, err = run("show", "no-such-style", env=env)
    assert err.startswith("STOP: unknown caption style 'no-such-style' (known: cluster-chest, cluster-top, hero-lockup, keyword-slam, quiet-cluster, rose-running, soft-build;")
    code, out, err = run("frobnicate", env=env)
    assert err == "STOP: usage: styles.py list | show <id> [--diff] | check\n"
