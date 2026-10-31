"""engine/dna.py: caption styles (set, look, feed), the rules that keep a style from touching the brand's identity, and the starter styles."""
import json
import os
from pathlib import Path

import pytest

import dna
from conftest import REPO, WOFF2, add_style, check, copy_starters, edit, run_cli

STARTERS = {"clean-clusters", "top-clusters", "hero-lockup", "keyword-slam", "quiet", "soft-build"}
LOOK_TO_PROFILE = {"accent": "accent", "text": "base", "on_accent": "on_accent", "hero_base": "hero_base", "hero_tint": "hero_tint", "hero_shadow": "hero_shadow",
                   "hero_stroke": "hero_stroke", "chip_neg": "chip_neg", "display_weight": "display_weight"}


def starter(sid):
    return json.loads((REPO / "templates" / "styles" / f"{sid}.json").read_text())


def refused(bd, sid, part):
    D = dna.load(bd)
    with pytest.raises(dna.DnaError) as e:
        dna.style(D, sid, bd)
    assert part in str(e.value), str(e.value)


def test_the_six_starter_styles_are_valid_and_neutral():
    files = sorted((REPO / "templates" / "styles").glob("*.json"))
    assert {f.stem for f in files} == STARTERS
    for f in files:
        st = json.loads(f.read_text())
        assert dna._style_file_problems(f) == [], f
        assert st["id"] == f.stem and st["opt_in"] is False and st["owner"] is None and st["preview"] is None
        assert "look" not in st and set(st["set"]) <= set(dna.STYLE_SET) and st["summary"] and st["use_when"] and st["never_when"]
        assert st["source"] == {"type": "starter"}


def test_a_style_lays_its_set_over_the_captions_section_and_carries_its_feed_block(brand):
    D = dna.load(brand)
    base, prof = dna.captions_profile(D, brand), dna.style(D, "quiet", brand)
    assert (prof["zone"], prof["hero_max"], prof["row_px"], prof["hero_glow"], prof["count_up"], prof["whip_frames"]) == ("chest", 170, [84, 100], False, False, 0)
    assert prof["caption_tier_max"] == "REDUCED" and prof["style"] == "quiet"
    assert prof["feed"] == {"place": "lower", "size": 0.85, "hero": {"look": "color"}, "motion": "quiet"}
    assert prof["width"] == base["width"] == 860 and dna.style(D, "soft-build", brand)["soft_build"] is True
    assert dna.style(D, "clean-clusters", brand, "brand-acme")["font_faces"] == dna.captions_profile(D, brand, "brand-acme")["font_faces"]


def test_a_style_changes_nothing_that_is_the_brands(brand):
    edit(brand, lambda d: d["captions"].update(neg_words=["never"], hero_base="#EEEEEE", places=["Springfield"], feel="Calm.", pace_gate="enforce"))
    D = dna.load(brand)
    base = dna.captions_profile(D, brand)
    mine = set(dna.STYLE_SET) | set(LOOK_TO_PROFILE.values())
    for sid in STARTERS:
        prof = dna.style(D, sid, brand)
        assert {k: v for k, v in prof.items() if k not in mine | {"style", "feed"}} == {k: v for k, v in base.items() if k not in mine}, sid


def test_a_style_only_lowers_the_tier(brand):
    D = dna.load(brand)
    assert dna.style(D, "quiet", brand)["caption_tier_max"] == "REDUCED"
    D["captions"]["caption_tier_max"] = "MINIMAL"
    assert dna.style(D, "quiet", brand)["caption_tier_max"] == "MINIMAL"          # a cap the brand set lower stays
    add_style(brand, "loud", set={"caption_tier_max": "FULL"})
    D["captions"]["caption_tier_max"] = "REDUCED"
    assert dna.style(D, "loud", brand)["caption_tier_max"] == "REDUCED"           # and a style never raises it


def test_a_color_or_font_or_word_list_in_set_is_refused(brand):
    for key, value, part in (("hero_base", "#FFFFFF", "set.hero_base: a color, the display weight and the font go in look, not in set"),
                             ("hero_tint", "#FFFFFF", "go in look"), ("chip_neg", "#E3161B", "go in look"), ("display_weight", 700, "go in look"),
                             ("hero_shadow", "none", "go in look"), ("hero_stroke", None, "go in look"),
                             ("neg_words", ["x"], "set.neg_words: that field is the brand's: a style may not set it"), ("places", ["x"], "the brand's"),
                             ("icon_words", ["a:b"], "the brand's"), ("pill_icons", [], "the brand's"), ("cta_wash", "#FFFFFF", "the brand's"),
                             ("page_bg", "#FFFFFF", "the brand's"), ("feel", "x", "the brand's"), ("default_style", "quiet", "the brand's"),
                             ("text_hook", "off", "the brand's"), ("pace_gate", "off", "the brand's"), ("font", "x", "not a known field"), ("fonts", {}, "not a known field")):
        add_style(brand, "bad", set={"zone": "chest", key: value})
        refused(brand, "bad", f"set.{key}: ")
        refused(brand, "bad", part)


def test_a_style_cannot_carry_colors_fonts_or_words_anywhere_else_either(brand):
    for field in ({"colors": {"accent": "#FF0000"}}, {"fonts": {}}, {"words": {"banned": []}}, {"captions": {}}, {"spelling": {}}, {"accent": "#FF0000"}):
        add_style(brand, "bad", **field)
        refused(brand, "bad", f"{next(iter(field))}: not a known field")


def test_a_style_set_field_takes_the_type_and_range_of_the_captions_field(brand):
    for key, value, part in (("zone", "middle", "set.zone: 'middle' is not one of top, chest"), ("width", 5000, "set.width: 5000 is above 1080"),
                             ("whip_frames", 3.5, "set.whip_frames: must be integer"), ("row_px", [10, 20, 30], "set.row_px: at most 2 item(s)"),
                             ("caption_tier_max", "HUGE", "set.caption_tier_max"), ("count_up", "yes", "set.count_up: must be boolean")):
        add_style(brand, "bad", set={key: value})
        refused(brand, "bad", part)


def test_look_lays_colors_over_the_profile_and_text_is_base(brand):
    look = {"accent": "#FF8800", "text": "#F0F0F0", "on_accent": "#101010", "hero_base": "#FFFFFF", "hero_tint": "#FFE0B0", "hero_shadow": "0 2px 4px #000",
            "hero_stroke": {"width": 3, "color": "#000000"}, "chip_neg": "#CC0000", "display_weight": 700}
    add_style(brand, "warm", look=look)
    D = dna.load(brand)
    base, prof = dna.captions_profile(D, brand), dna.style(D, "warm", brand)
    for k, v in look.items():
        assert prof[LOOK_TO_PROFILE[k]] == v, k
    assert prof["base"] == "#F0F0F0" and base["base"] == "#FFFFFF"
    assert {k for k in prof if prof[k] != base.get(k)} == set(LOOK_TO_PROFILE.values()) | {"style", "feed"}       # nothing else moved
    assert prof["font"] == base["font"] and prof["font_files"] == base["font_files"] and prof["word_font"] == base["word_font"]


def test_a_missing_look_key_is_the_brands_value_and_null_is_not_a_value(brand):
    edit(brand, lambda d: d["captions"].update(hero_base="#EEEEEE", hero_tint="#AAAAAA", hero_stroke={"width": 2, "color": "#000000"}))
    add_style(brand, "plain", look={"accent": "#FF8800"})
    prof = dna.style(dna.load(brand), "plain", brand)
    assert (prof["hero_base"], prof["hero_tint"], prof["hero_stroke"]) == ("#EEEEEE", "#AAAAAA", {"width": 2, "color": "#000000"}) and prof["accent"] == "#FF8800"
    for key in ("hero_base", "hero_tint", "hero_stroke", "accent", "chip_neg"):
        add_style(brand, "nul", look={key: None})
        refused(brand, "nul", f"look.{key}: must be ")


def test_look_font_running_points_the_font_keys_at_the_running_face_and_leaves_the_other_two_where_they_are(brand):
    (brand / "assets" / "fonts" / "Cond.woff2").write_bytes(WOFF2)
    edit(brand, lambda d: d["fonts"].update(running={"family": "Acme Condensed", "file": "fonts/Cond.woff2", "fallback": "Arial"}))
    add_style(brand, "cond", look={"font": "running"})
    D = dna.load(brand)
    base, prof = dna.captions_profile(D, brand, "p"), dna.style(D, "cond", brand, "p")
    assert (prof["font"], prof["font_family"], prof["font_prefix"], prof["font_fallback"]) == ("Acme Condensed", "AcmeCondensedDyn", "AcmeCondensed", "Arial")
    assert prof["font_files"]["800"] == "p/fonts/Cond.woff2" and prof["font_faces"] == [{"weight": "100 900", "src": "p/fonts/Cond.woff2"}] and prof["fonts_dir"] == "p/fonts"
    assert prof["font_abs"]["800"] == str(brand / "assets" / "fonts" / "Cond.woff2") and prof["font_variable"] is False
    assert prof["display_font"] == base["display_font"] and prof["display_font"]["family"] == "AcmeDyn"            # page and pill text stay in the display face
    assert prof["word_font"] == base["word_font"] and prof["word_font"]["family"] == "AcmeCondensedDyn"             # the other rows were already in the running face
    assert base["font_family"] == "AcmeDyn"
    add_style(brand, "disp", look={"font": "display"})
    assert dna.style(D, "disp", brand, "p")["font_family"] == "AcmeDyn"


def test_look_font_running_needs_a_running_face(brand):
    add_style(brand, "cond", look={"font": "running"})
    refused(brand, "cond", "look.font: is 'running' but fonts.running is null")
    assert any(p == "styles/cond.json: look.font: is 'running' but fonts.running is null" for p in check(brand))


def test_a_key_in_look_that_is_not_on_the_list_is_refused(brand):
    for key in ("cta_wash", "neg_words", "page_bg", "pill_bg", "feel", "font_family", "case", "base", "colors"):
        add_style(brand, "bad", look={key: "#FFFFFF"})
        refused(brand, "bad", f"look.{key}: not a known field")
    for look, part in (({"accent": "red"}, "look.accent: 'red' is not in the expected form (#RRGGBB)"), ({"font": "mono"}, "look.font: 'mono' is not one of display, running"),
                       ({"display_weight": 50}, "look.display_weight: 50 is below 100"), ({"hero_stroke": {"width": 2}}, "look.hero_stroke.color: missing")):
        add_style(brand, "bad", look=look)
        refused(brand, "bad", part)


def test_feed_block_rules(brand):
    add_style(brand, "feedy", feed={"place": "top", "size": 1.2, "hero": {"look": "bigger", "scale": 2.0, "pick": "accent"}, "words": 3, "motion": "reveal"})
    D = dna.load(brand)
    assert dna.style(D, "feedy", brand)["feed"] == {"place": "top", "size": 1.2, "hero": {"look": "bigger", "scale": 2.0, "pick": "accent"}, "words": 3, "motion": "reveal"}
    D["feed_captions"]["words_per_page"] = 2
    assert dna.style(D, "feedy", brand)["feed"]["words"] == 2                      # a style only lowers the words a page
    add_style(brand, "none")
    assert dna.style(D, "none", brand)["feed"] == {}
    for feed, part in (({"place": "middle"}, "feed.place: 'middle' is not one of chest, lower, top"), ({"size": 3}, "feed.size: 3 is above 1.5"), ({"words": 4}, "feed.words: 4 is above 3"),
                       ({"hero": {"scale": 9}}, "feed.hero.scale: 9 is above 2.8"), ({"hero": {"look": "huge"}}, "feed.hero.look"), ({"color": "#FFF"}, "feed.color: not a known field"),
                       ({"hero": {"color": "#FFFFFF"}}, "feed.hero.color: not a known field"), ({"motion": "spin"}, "feed.motion")):
        add_style(brand, "bad", feed=feed)
        refused(brand, "bad", part)


def test_unknown_styles_wrong_case_and_odd_ids_are_plain_errors(brand):
    refused(brand, "no-such", "unknown style 'no-such' (known: ")
    refused(brand, "Quiet", "style id 'Quiet' is written in the wrong case: use 'quiet'")
    refused(brand, "../quiet", "must be lowercase letters, digits and dashes")
    add_style(brand, "mine")
    refused(brand, "MINE", "wrong case: use 'mine'")


def test_a_style_file_must_say_its_own_name_and_have_the_required_fields(brand):
    add_style(brand, "mine")
    st = json.loads((brand / "styles" / "mine.json").read_text())
    (brand / "styles" / "mine.json").write_text(json.dumps({**st, "id": "other"}))
    refused(brand, "mine", "id: 'other' must match the file name 'mine'")
    (brand / "styles" / "mine.json").write_text(json.dumps({"id": "mine", "name": "Mine"}))
    refused(brand, "mine", "summary: missing (and 3 more)")
    assert sorted(check(brand)) == [f"styles/mine.json: {k}: missing" for k in ("never_when", "set", "summary", "use_when")]
    (brand / "styles" / "mine.json").write_text("{nope")
    refused(brand, "mine", "is not valid JSON")


def test_the_users_style_wins_over_a_starter_of_the_same_name_and_the_list_says_where_each_is(brand):
    copy_starters(brand)
    add_style(brand, "quiet", name="My quiet", set={"zone": "top", "hero_max": 99})
    add_style(brand, "extra")
    got = {s["id"]: s for s in dna.styles_list(brand)}
    assert set(got) == STARTERS | {"extra"} and got["quiet"]["where"] == "brand" and got["quiet"]["name"] == "My quiet" and got["clean-clusters"]["where"] == "brand"
    assert {s["where"] for s in dna.styles_list()} == {"starter"} and len(dna.styles_list()) == 6        # with no brand folder, only the starters
    prof = dna.style(dna.load(brand), "quiet", brand)
    assert (prof["zone"], prof["hero_max"]) == ("top", 99)
    assert {"id", "name", "summary", "use_when", "never_when", "where", "path"} == set(got["quiet"])


def test_check_names_a_broken_style_file_by_its_file_name(brand):
    add_style(brand, "bad", set={"hero_base": "#FFFFFF"}, look={"cta_wash": "#FFFFFF"})
    assert sorted(check(brand)) == ["styles/bad.json: look.cta_wash: not a known field",
                                    "styles/bad.json: set.hero_base: a color, the display weight and the font go in look, not in set"]
    (brand / "styles" / "bad.json").write_text("[]")
    assert check(brand)[0].startswith("styles/bad.json: ") and "must be a JSON object" in check(brand)[0]
    (brand / "styles" / "bad.json").unlink()
    copy_starters(brand)
    assert check(brand) == []


def test_styles_as_a_program(brand):
    copy_starters(brand)
    r = run_cli("styles", brand)
    assert r.returncode == 0 and {ln.split("\t")[0] for ln in r.stdout.splitlines()} == STARTERS and all(ln.endswith("\tbrand") for ln in r.stdout.splitlines())
    r = run_cli("captions", brand, "--style", "quiet", "--prefix", "brand-acme")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["style"] == "quiet" and out["fonts_dir"] == "brand-acme/fonts" and out["caption_tier_max"] == "REDUCED"
    r = run_cli("captions", brand)
    assert json.loads(r.stdout)["fonts_dir"] == str(brand / "assets" / "fonts") and "style" not in json.loads(r.stdout)       # absolute paths unless a prefix is given
    r = run_cli("captions", brand, "--style", "nope")
    assert r.returncode == 2 and r.stderr.startswith("dna: unknown style 'nope'") and "Traceback" not in r.stderr
    t = json.loads(run_cli("theme", brand).stdout)
    assert t["font_faces"][0]["src"] == str(brand / "assets" / "fonts" / "Acme-Bold.woff2") and t["id"] == "acme"
    assert json.loads(run_cli("theme", brand, "--prefix", "brand-acme").stdout)["font_faces"][0]["src"] == "brand-acme/fonts/Acme-Bold.woff2"
    assert json.loads(run_cli("show", brand).stdout)["captions"]["pace_gate"] == "report"


def test_a_style_file_takes_the_keys_the_caption_chains_own_files_carry(brand):
    add_style(brand, "full", added="2030-01-02", added_for="acme", owner="acme", status="built", opt_in=True, preview="full.jpg", source={"type": "study", "ref": "x", "note": 1})
    D = dna.load(brand)
    assert dna.style(D, "full", brand)["style"] == "full" and check(brand) == []
    for fields, part in (({"status": "later"}, "status: 'later' is not one of built, to build"), ({"added": "01/02/2030"}, "added: '01/02/2030' is not in the expected form (YYYY-MM-DD)"),
                         ({"added_for": "Acme Co"}, "added_for: 'Acme Co' is not in the expected form"), ({"preview": "full.txt"}, "preview: 'full.txt' is not in the expected form"),
                         ({"preview": "../x.jpg"}, "preview: '../x.jpg' is not in the expected form"), ({"preview": "/abs/x.jpg"}, "preview: '/abs/x.jpg' is not in the expected form"),
                         ({"source": "study"}, "source: must be object or null, got str"), ({"opt_in": "yes"}, "opt_in: must be boolean, got str"),
                         ({"name": "two\nlines"}, "name: 'two\\nlines' is not in the expected form (one line)"), ({"summary": ""}, "summary: must not be empty")):
        add_style(brand, "bad", **fields)
        refused(brand, "bad", part)
    add_style(brand, "ok-null", source=None, preview=None, owner=None, added_for=None)
    assert dna.style(D, "ok-null", brand)["style"] == "ok-null"


def test_style_ids_are_lower_case_words_joined_by_hyphens_starting_with_a_letter(brand):
    for bad in ("2-up", "-lead", "trail-", "two--dashes", "UPPER", "under_score"):
        (brand / "styles").mkdir(exist_ok=True)
        (brand / "styles" / f"{bad}.json").write_text(json.dumps({"id": bad, "name": "x", "summary": "x", "use_when": "x", "never_when": "x", "set": {}}))
        assert any(p.startswith(f"styles/{bad}.json: id: ") and "lower case words joined by hyphens" in p for p in check(brand)), bad
        (brand / "styles" / f"{bad}.json").unlink()
    edit(brand, lambda d: d["captions"].update(default_style="2-up"))
    assert any(p.startswith("captions.default_style: '2-up' is not in the expected form") for p in check(brand))


def test_a_style_that_is_to_build_is_in_no_menu_and_a_private_style_is_refused_to_other_brands(brand):
    add_style(brand, "later", status="to build")
    add_style(brand, "mine-only", owner="someone-else")
    assert "later" not in {s["id"] for s in dna.styles_list(brand)} and "mine-only" in {s["id"] for s in dna.styles_list(brand)}
    refused(brand, "mine-only", "style 'mine-only' is private to someone-else: acme cannot use it")
    add_style(brand, "mine-only", owner="acme")
    assert dna.style(dna.load(brand), "mine-only", brand)["style"] == "mine-only"


def test_set_row_px_runs_small_to_large(brand):
    add_style(brand, "rows", set={"row_px": [140, 120]})
    refused(brand, "rows", "set.row_px: the first number is the smaller row (minimum, then maximum)")


def test_the_style_menu_needs_no_workspace_to_show_the_starters(tmp_path):
    r = run_cli("styles", env={**os.environ, "CUTROOM_HOME": str(tmp_path / "empty")})
    assert r.returncode == 0 and {ln.split("\t")[0] for ln in r.stdout.splitlines()} == STARTERS and all(ln.endswith("\tstarter") for ln in r.stdout.splitlines())
    assert r.stderr == ""
