"""engine/dna.py: the two views (captions_profile, theme), the path helpers and the font reader."""
import json
import os
from pathlib import Path

import pytest

import dna
from conftest import WOFF2, check, edit, template, variable_ttf

PROFILE_KEYS = ("id brand handle accent base on_accent font font_family font_files font_abs font_faces fonts_dir font_prefix word_font case chip hero_shadow "
                "hero_stroke text_opacity text_soften hero_base hero_tint hero_glow glow_px neg_words chip_neg count_up text_hook caption_tier_max spelling "
                "do_not_say pace_gate auto_punches").split()
PROFILE_EXTRAS = {"dna_file", "font_fallback", "font_variable", "display_font"}
THEME_EXTRAS = {"font_fallback", "hook_on", "running_family", "running_faces", "mono_family", "mono_faces"}
THEME_KEYS = ("id name colors surface font_family font_faces caps skew cap_words caption_size caption_top caption_stroke hook_look hook_top hook_size hook_font "
              "lockup_src lockup_dark_src emblem_src lockup_on broll_style smooth_motion music_level end_card").split()


def with_running(bd):
    """a running face next to the display face (and a mono one), both as real font files"""
    for n in ("Cond.woff2", "Mono.woff2"):
        (bd / "assets" / "fonts" / n).write_bytes(WOFF2)
    edit(bd, lambda d: d["fonts"].update(running={"family": "Acme Condensed", "file": "fonts/Cond.woff2", "fallback": "Arial"}, mono={"family": "Acme Mono", "file": "fonts/Mono.woff2"}))


def test_captions_profile_has_every_key_the_engine_contract_lists(brand):
    D = dna.load(brand)
    prof = dna.captions_profile(D, brand, prefix="brand-acme")
    assert set(PROFILE_KEYS) <= set(prof)
    assert set(D["captions"]) <= set(prof)                       # every other captions field is copied through under its own name
    assert set(prof) - set(PROFILE_KEYS) - set(D["captions"]) == PROFILE_EXTRAS
    assert prof["pace_gate"] == "report" and prof["auto_punches"] is False


def test_captions_profile_values(brand):
    D = dna.load(brand)
    prof = dna.captions_profile(D, brand, prefix="brand-acme")
    assert (prof["id"], prof["brand"], prof["handle"]) == ("acme", "Acme", "@acme")
    assert (prof["accent"], prof["base"], prof["on_accent"]) == (D["colors"]["accent"], D["colors"]["base"], D["colors"]["on_accent"])
    assert (prof["font"], prof["font_prefix"], prof["font_family"], prof["fonts_dir"]) == ("Acme", "Acme", "AcmeDyn", "brand-acme/fonts")
    assert prof["font_files"] == {w: "brand-acme/fonts/Acme-Bold.woff2" for w in ("400", "500", "700", "800", "900")}
    assert prof["font_faces"] == [{"weight": "100 900", "src": "brand-acme/fonts/Acme-Bold.woff2"}]
    assert prof["font_abs"]["800"] == str(brand / "assets" / "fonts" / "Acme-Bold.woff2") and os.path.isabs(prof["font_abs"]["800"])
    assert prof["display_font"] == {"fonts_dir": "brand-acme/fonts", "prefix": "Acme", "family": "AcmeDyn", "faces": prof["font_faces"]}
    assert prof["word_font"] is None and prof["font_variable"] is False and prof["font_fallback"] == "sans-serif"
    assert prof["dna_file"] == str(brand / "dna.json")
    assert prof["spelling"] == {} and prof["do_not_say"] == []


def test_paths_start_with_the_prefix_when_given_and_are_absolute_when_not(brand):
    D, font = dna.load(brand), str(brand / "assets" / "fonts" / "Acme-Bold.woff2")
    plain = dna.captions_profile(D, brand)
    assert plain["fonts_dir"] == str(brand / "assets" / "fonts") and plain["font_files"]["400"] == font and plain["font_faces"] == [{"weight": "100 900", "src": font}]
    assert plain["display_font"]["faces"] == plain["font_faces"] and plain["display_font"]["fonts_dir"] == plain["fonts_dir"]
    assert dna.theme(D, brand)["font_faces"] == [{"weight": "100 900", "src": font}]
    assert dna.captions_profile(D, brand, "")["font_files"]["400"] == font                  # an empty prefix is no prefix
    assert dna.public_prefix(D) == "brand-acme"
    edit(brand, lambda d: d["fonts"]["display"].update(file="Acme-Bold.woff2"))      # a font at the root of assets/
    (brand / "assets" / "Acme-Bold.woff2").write_bytes(WOFF2)
    D = dna.load(brand)
    assert dna.captions_profile(D, brand)["fonts_dir"] == str(brand / "assets") and dna.captions_profile(D, brand, "brand-acme")["fonts_dir"] == "brand-acme"


def test_chip_neg_follows_the_negative_color_until_set_and_text_hook_follows_hook_on(brand):
    D = dna.load(brand)
    assert dna.captions_profile(D, brand)["chip_neg"] == D["colors"]["negative"] and dna.captions_profile(D, brand)["text_hook"] == "on"
    D["captions"]["chip_neg"], D["colors"]["negative"] = "#112233", "#AA0000"
    assert dna.captions_profile(D, brand)["chip_neg"] == "#112233"
    D["captions"]["chip_neg"] = None
    assert dna.captions_profile(D, brand)["chip_neg"] == "#AA0000"
    D["hook"]["on"] = False
    assert dna.captions_profile(D, brand)["text_hook"] == "off"
    D["hook"]["on"], D["captions"]["text_hook"] = True, "off"
    assert dna.captions_profile(D, brand)["text_hook"] == "off"


def test_the_gates_and_word_lists_ride_in_the_profile(brand):
    edit(brand, lambda d: (d["captions"].update(pace_gate="enforce", neg_words=["never"]), d["motion"].update(auto_punches=True),
                           d["words"].update(spelling={"acne": "Acme"}, banned=["cheap"])))
    prof = dna.captions_profile(dna.load(brand), brand)
    assert (prof["pace_gate"], prof["auto_punches"], prof["neg_words"], prof["spelling"], prof["do_not_say"]) == ("enforce", True, ["never"], {"acne": "Acme"}, ["cheap"])
    assert check(brand) == []


def test_a_running_face_becomes_word_font_and_the_mono_and_running_faces_ride_in_the_theme(brand):
    with_running(brand)
    D = dna.load(brand)
    wf = dna.captions_profile(D, brand, "brand-acme")["word_font"]
    assert (wf["family"], wf["prefix"], wf["fonts_dir"]) == ("AcmeCondensedDyn", "AcmeCondensed", "brand-acme/fonts")
    assert wf["files"]["800"] == "brand-acme/fonts/Cond.woff2" and wf["faces"] == [{"weight": "100 900", "src": "brand-acme/fonts/Cond.woff2"}]
    assert wf["abs"]["800"] == str(brand / "assets" / "fonts" / "Cond.woff2")
    t = dna.theme(D, brand, "brand-acme")
    assert (t["running_family"], t["mono_family"], t["hook_font"]) == ("AcmeCondensedDyn", "AcmeMonoDyn", "display")
    assert t["running_faces"] == [{"weight": "100 900", "src": "brand-acme/fonts/Cond.woff2"}] and t["mono_faces"][0]["src"] == "brand-acme/fonts/Mono.woff2"
    assert dna.theme(D, brand)["running_faces"][0]["src"] == str(brand / "assets" / "fonts" / "Cond.woff2")
    edit(brand, lambda d: d["hook"].update(font="running"))
    assert dna.theme(dna.load(brand), brand)["hook_font"] == "running" and check(brand) == []


def test_a_family_shipped_one_file_per_weight(brand):
    for n in ("A-400.ttf", "A-700.ttf", "A-900.ttf"):
        (brand / "assets" / "fonts" / n).write_bytes(WOFF2)
    edit(brand, lambda d: d["fonts"]["display"].update(file="fonts/A-700.ttf", weights={"400": "fonts/A-400.ttf", "700": "fonts/A-700.ttf", "900": "fonts/A-900.ttf"}))
    D = dna.load(brand)
    assert check(brand) == []
    prof = dna.captions_profile(D, brand, "p")
    assert prof["font_files"] == {"400": "p/fonts/A-400.ttf", "500": "p/fonts/A-400.ttf", "700": "p/fonts/A-700.ttf", "800": "p/fonts/A-700.ttf", "900": "p/fonts/A-900.ttf"}
    assert prof["font_faces"] == [{"weight": "400", "src": "p/fonts/A-400.ttf"}, {"weight": "700", "src": "p/fonts/A-700.ttf"}, {"weight": "900", "src": "p/fonts/A-900.ttf"}]
    assert dna.theme(D, brand, "p")["font_faces"] == prof["font_faces"]


def test_a_variable_font_is_found_by_its_fvar_table(brand):
    assert dna.font_is_variable(dna.load(brand), brand) is False
    (brand / "assets" / "fonts" / "Acme-Bold.woff2").write_bytes(variable_ttf())
    D = dna.load(brand)
    assert dna.font_is_variable(D, brand) is True and dna.captions_profile(D, brand)["font_variable"] is True
    (brand / "assets" / "fonts" / "Acme-Bold.woff2").unlink()
    assert dna.font_is_variable(D, brand) is False                      # unreadable is not variable; check names the file


def test_a_variable_woff2_is_found_by_its_table_directory(brand):
    blob = b"wOF2" + b"\0" * 8 + (1).to_bytes(2, "big") + b"\0" * 34 + bytes([dna._WOFF2_TAGS.index("fvar"), 10])      # one directory entry: a known tag, fvar
    (brand / "assets" / "fonts" / "Acme-Bold.woff2").write_bytes(blob)
    assert dna._font_tags(brand / "assets" / "fonts" / "Acme-Bold.woff2") == {"fvar"} and dna.font_is_variable(dna.load(brand), brand) is True
    (brand / "assets" / "fonts" / "Acme-Bold.woff2").write_bytes(blob[:49])                                               # a cut-off directory reads as no tables, not a crash
    assert dna.font_is_variable(dna.load(brand), brand) is False


def test_a_family_name_is_always_a_css_identifier(brand):
    edit(brand, lambda d: d["fonts"]["display"].update(family="3270 Narrow!"))
    prof = dna.captions_profile(dna.load(brand), brand)
    assert prof["font_prefix"] == "F3270Narrow" and prof["font_family"] == "F3270NarrowDyn" and prof["font"] == "3270 Narrow!"


def test_theme_has_every_key_the_renderer_contract_lists(brand):
    D = dna.load(brand)
    t = dna.theme(D, brand, "brand-acme")
    assert set(THEME_KEYS) <= set(t) and set(t) - set(THEME_KEYS) == THEME_EXTRAS
    assert list(t["colors"]) == ["accent", "ink", "base", "on_accent", "negative", "bg", "card"] and t["colors"] == {k: D["colors"][k] for k in t["colors"]}
    assert t["surface"] == D["surface"] and (t["id"], t["name"]) == ("acme", "Acme") and (t["caps"], t["skew"]) == (False, 0)
    assert (t["cap_words"], t["caption_size"], t["caption_top"], t["caption_stroke"]) == (3, 84, 1190, 0)
    assert (t["hook_on"], t["hook_look"], t["hook_top"], t["hook_size"], t["hook_font"]) == (True, "card", 300, None, "display")
    assert (t["broll_style"], t["smooth_motion"], t["music_level"]) == ("cut", True, -30)
    assert (t["lockup_src"], t["lockup_dark_src"], t["emblem_src"], t["lockup_on"]) == (None, None, None, False)
    assert t["running_family"] is None and t["running_faces"] == [] and t["mono_family"] is None and t["font_fallback"] == "sans-serif"
    assert t["font_family"] == "AcmeDyn" and set(t["end_card"]) == {"on", "line", "handle"} and json.dumps(t)         # plain data all through


def test_theme_logos_get_the_prefix_or_an_absolute_path_and_turn_the_mark_on(brand):
    edit(brand, lambda d: d["logo"].update(emblem="logos/mark.svg"))
    t = dna.theme(dna.load(brand), brand, "brand-acme")
    assert (t["emblem_src"], t["lockup_src"], t["lockup_on"]) == ("brand-acme/logos/mark.svg", None, True)
    assert dna.theme(dna.load(brand), brand)["emblem_src"] == str(brand / "assets" / "logos" / "mark.svg")
    edit(brand, lambda d: d["logo"].update(emblem=None, lockup_dark="logos/word-dark.svg"))
    t = dna.theme(dna.load(brand), brand, "brand-acme")
    assert (t["lockup_dark_src"], t["lockup_on"]) == ("brand-acme/logos/word-dark.svg", True)


def test_the_end_card_takes_the_brands_handle_until_it_has_its_own(brand):
    edit(brand, lambda d: d["delivery"]["end_card"].update(on=True, line="Follow for more"))
    assert dna.theme(dna.load(brand), brand)["end_card"] == {"on": True, "line": "Follow for more", "handle": "@acme"}
    edit(brand, lambda d: d["delivery"]["end_card"].update(handle="@acme.shop"))
    assert dna.theme(dna.load(brand), brand)["end_card"]["handle"] == "@acme.shop"


def test_paths_resolve_under_assets_from_any_spelling_of_the_folder(brand, monkeypatch):
    D = dna.load(brand)
    assert dna.resolve(D, "fonts/Acme-Bold.woff2", brand) == str(brand / "assets" / "fonts" / "Acme-Bold.woff2")
    assert dna.resolve(D, "", brand) == str(brand / "assets")
    monkeypatch.chdir(brand.parent)
    assert dna.resolve(D, "x.svg", "Brand") == str(brand / "assets" / "x.svg")
    assert dna.music_dir(D, brand) == brand / "music"
    assert dna.grade_profile(D, brand) == {"enabled": False, "lut": None}
    D["grade"].update(enabled=True, lut="luts/look.cube")
    assert dna.grade_profile(D, brand) == {"enabled": True, "lut": str(brand / "assets" / "luts" / "look.cube")}


def test_folders_are_the_workspace_folders_and_archive_follows_the_dna(brand, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"workspace": str(ws)}))
    monkeypatch.setattr(dna.common, "CONF", conf)
    D = dna.load(brand)
    got = dna.folders(D)
    assert list(got) == list(dna.common.WORKSPACE_FOLDERS) and got["Footage"] == ws / "Footage" and got["Archive"] == ws / "Archive"
    D["storage"]["archive"] = str(tmp_path / "ssd")
    assert dna.folders(D)["Archive"] == tmp_path / "ssd" and dna.folders(D)["Finished"] == ws / "Finished"


def test_the_renderers_theme_linker_takes_what_theme_gives(brand, tmp_path):
    """engine/render.py copies the files a theme names into its own public folder and points the theme at the copies: it needs absolute paths"""
    render = pytest.importorskip("render", reason="engine/render.py is not in this checkout")
    if not hasattr(render, "link_theme_assets"):
        pytest.skip("render.py has no link_theme_assets")
    with_running(brand)
    (brand / "assets" / "logos").mkdir()
    (brand / "assets" / "logos" / "mark.svg").write_text("<svg/>")
    edit(brand, lambda d: (d["hook"].update(font="running"), d["logo"].update(emblem="logos/mark.svg")))
    out = render.link_theme_assets(dna.theme(dna.load(brand), brand), tmp_path / "public", tmp_path)
    assert out["font_faces"] == [{"weight": "100 900", "src": "brand-acme/Acme-Bold.woff2"}] and out["emblem_src"] == "brand-acme/mark.svg"
    assert out["running_faces"] == [{"weight": "100 900", "src": "brand-acme/Cond.woff2"}] and out["mono_faces"] == [{"weight": "100 900", "src": "brand-acme/Mono.woff2"}]
    assert sorted(p.name for p in (tmp_path / "public" / "brand-acme").iterdir()) == ["Acme-Bold.woff2", "Cond.woff2", "Mono.woff2", "mark.svg"]
