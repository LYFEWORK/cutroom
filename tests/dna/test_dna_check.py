"""engine/dna.py: loading, filling defaults and the check (schema walk, TODO rule, files, rules, DNA.md)."""
import json
import os
import re
from pathlib import Path

import pytest

import dna
from conftest import REPO, WOFF2, check, edit, run_cli, template


def shape(sch, tmpl, path, bad, root):
    """compare the keys of the template with the schema's properties, level by level"""
    if "$ref" in sch:
        sch = dna._deref(sch, root)
    props = sch.get("properties")
    if props and isinstance(tmpl, dict):
        if set(props) != set(tmpl):
            bad.append((path, sorted(set(props) ^ set(tmpl))))
        for k in props:
            if k in tmpl:
                shape(props[k], tmpl[k], f"{path}.{k}" if path else k, bad, root)


def test_the_template_has_every_field_of_the_schema_and_nothing_else():
    schema, bad = json.loads((REPO / "templates" / "schema.json").read_text()), []
    shape(schema, template(), "", bad, schema)
    assert bad == []


def test_the_template_carries_no_todo_and_nothing_ships_with_an_em_dash():
    assert "TODO" not in (REPO / "templates" / "dna.json").read_text()
    for f in [*(REPO / "templates").rglob("*.json"), REPO / "templates" / "DNA.md", REPO / "engine" / "dna.py"]:
        assert "—" not in f.read_text(), f


def test_the_template_with_identity_filled_is_clean(brand):
    assert check(brand) == []


def test_the_raw_template_fails_only_on_the_four_identity_fields(tmp_path):
    (tmp_path / "dna.json").write_text(json.dumps(template()))
    problems = dna.validate(dna.load(tmp_path), tmp_path, strict=False)
    assert [p.split(":")[0] for p in problems] == ["id", "name", "fonts.display.family", "fonts.display.file"]


def test_the_fields_the_task_names_are_all_in_the_schema():
    d = template()
    want = {"captions": "case zone width top_y0 hero_max row_px chin_gap chip hero_base hero_shadow hero_stroke text_opacity text_soften whip_frames whip_amount "
                        "caption_tier_max soft_build default_style hero_glow hero_tint glow_px chip_neg neg_words count_up places cta_wash page_bg page_ink "
                        "display_weight pill_bg pill_ink icon_words pill_icons feel look_note text_hook pace_gate devices master_audio".split(),
            "feed_captions": ["words_per_page", "size_px", "stroke_px", "top_px"], "hook": ["on", "look", "top_px", "size_px", "font", "hold_s"],
            "motion": ["level", "transitions_allowed", "transitions_banned", "punches", "auto_punches", "smooth", "energy"],
            "music": ["on_talking_head", "on_montage", "feel", "alone_lufs", "under_speech_db", "alone_db", "ease_s", "library", "repeat_gap_videos", "banned_md5"],
            "sound": ["sfx", "sfx_per_min", "sfx_under_speech_db", "voice_isolation_pct", "master_lufs", "true_peak_dbtp", "profanity"]}
    for sec, keys in want.items():
        assert set(d[sec]) == set(keys), sec
    assert d["captions"]["default_style"] == "clean-clusters" and d["captions"]["pace_gate"] == "report" and d["motion"]["auto_punches"] is False
    assert d["captions"]["devices"] == "off" and d["captions"]["master_audio"] == "off" and d["transcribe"] == {"engine": "local", "language": None}


def test_a_typo_is_a_failed_check(brand):
    edit(brand, lambda d: (d["captions"].update(zoen="top"), d["hook"].update(lok="card"), d.update(colorz={})))
    assert sorted(check(brand)) == ["captions.zoen: not a known field", "colorz: not a known field", "hook.lok: not a known field"]


def test_a_todo_anywhere_is_a_problem_and_the_todo_list_names_it(brand):
    edit(brand, lambda d: (d.update(notes=["TODO check the guide"]), d["captions"].update(feel="TODO", default_style="TODO"), d["fonts"]["display"].update(family="TODO Sans"),
                           d["formats"].update(home="TODO")))
    assert sorted(check(brand)) == ["captions.default_style: still says TODO", "captions.feel: still says TODO", "fonts.display.family: still says TODO",
                                    "formats.home: still says TODO", "notes[0]: still says TODO"]       # each once: the rules that compare these two stay quiet
    assert sorted(dna.todo_lines(brand)) == sorted(check(brand))


def test_a_missing_font_file_fails_and_a_file_that_is_not_a_font_fails(brand):
    font = brand / "assets" / "fonts" / "Acme-Bold.woff2"
    font.unlink()
    assert check(brand) == ["fonts.display.file: file not found: assets/fonts/Acme-Bold.woff2"]
    font.write_text("this is text")
    assert "is not a font file" in check(brand)[0]
    font.write_bytes(b"")
    assert "is empty, so it is not a font file" in check(brand)[0]
    for head in (b"wOFF", b"\x00\x01\x00\x00", b"OTTO", b"true", b"wOF2"):
        font.write_bytes(head + b"\0" * 20)
        assert check(brand) == [], head


def test_running_and_mono_fonts_and_weight_files_are_checked_too(brand):
    def faces(d):
        d["fonts"]["running"] = {"family": "Acme Condensed", "file": "fonts/Cond.woff2"}
        d["fonts"]["mono"] = {"family": "Acme Mono", "file": "fonts/Mono.woff2", "weights": {"400": "fonts/Mono-400.woff2"}}
    edit(brand, faces)
    assert sorted(p.split(":")[0] for p in check(brand)) == ["fonts.mono.file", "fonts.mono.weights.400", "fonts.running.file"]


def test_logo_files_must_exist_and_be_svg_or_png(brand):
    edit(brand, lambda d: d["logo"].update(emblem="logos/mark.svg", lockup="logos/word.jpg"))
    assert sorted(p.split(":")[0] for p in check(brand)) == ["logo.emblem", "logo.lockup"]
    (brand / "assets" / "logos").mkdir()
    (brand / "assets" / "logos" / "mark.svg").write_text("<svg/>")
    (brand / "assets" / "logos" / "word.jpg").write_text("x")
    assert check(brand) == ["logo.lockup: assets/logos/word.jpg must be an .svg or .png file"]


@pytest.mark.parametrize("top,ok", [(219, False), (220, True), (300, True), (1600, True), (1601, False), (0, False)])
def test_the_hook_never_sits_in_the_top_220_px(brand, top, ok):
    edit(brand, lambda d: d["hook"].update(top_px=top))
    problems = check(brand)
    assert (problems == []) is ok
    if top < 220:
        assert problems == [f"hook.top_px: {top} is below 220 (the platform's own header covers the top 220 px of a 1080 x 1920 frame)"]


def test_the_default_style_must_name_a_style_file_and_null_is_the_brands_own_preset(brand):
    edit(brand, lambda d: d["captions"].update(default_style="no-such-style"))
    (line,) = check(brand)
    assert line.startswith("captions.default_style: no style called 'no-such-style'") and "clean-clusters" in line
    edit(brand, lambda d: d["captions"].update(default_style="quiet"))
    assert check(brand) == []                                          # a starter answers
    edit(brand, lambda d: d["captions"].update(default_style="mine"))
    (brand / "styles").mkdir()
    (brand / "styles" / "mine.json").write_text(json.dumps({"id": "mine", "name": "Mine", "summary": "x", "use_when": "y", "never_when": "z", "set": {}}))
    assert check(brand) == []                                          # so does a file in Brand/styles/
    edit(brand, lambda d: d["captions"].update(default_style=None))
    assert check(brand) == []


def test_every_todo_line_of_dna_md_is_its_own_problem_and_a_missing_dna_md_is_one(brand):
    md = brand / "DNA.md"
    lines = md.read_text().splitlines()
    lines[8], lines[14] = "TODO: who is on camera", "also TODO here"
    md.write_text("\n".join(lines) + "\n")
    assert check(brand) == ["DNA.md line 9: TODO: who is on camera", "DNA.md line 15: also TODO here"]
    assert dna.validate(dna.load(brand), brand, strict=False) == []      # the disk checks are strict only
    md.unlink()
    assert check(brand)[0].startswith("DNA.md: not found")
    assert dna.md_todos(brand) == []


@pytest.mark.parametrize("path,value,part", [
    (("colors", "accent"), "red", "colors.accent: 'red' is not in the expected form (#RRGGBB)"),
    (("captions", "zone"), "middle", "captions.zone: 'middle' is not one of top, chest"),
    (("captions", "width"), 5000, "captions.width: 5000 is above 1080"),
    (("captions", "row_px"), [100], "captions.row_px: needs at least 2 item(s)"),
    (("captions", "whip_frames"), 3.0, "captions.whip_frames: must be integer, got float"),
    (("captions", "chip"), "yes", "captions.chip: must be boolean, got str"),
    (("output", "fps"), "30", "output.fps: must be integer, got str"),
    (("output", "crf"), 99, "output.crf: 99 is above 28"),
    (("captions", "pace_gate"), "strict", "captions.pace_gate: 'strict' is not one of off, report, enforce"),
    (("motion", "auto_punches"), 1, "motion.auto_punches: must be boolean, got int"),
    (("handle",), "acme", "handle: 'acme' is not in the expected form (an @name, or empty)"),
    (("id",), "Acme", "id: 'Acme' is not in the expected form (lowercase letters, digits and dashes)"),
    (("music", "banned_md5"), ["XYZ"], "music.banned_md5[0]: 'XYZ' is not in the expected form (32 lowercase hex characters)"),
    (("logo", "emblem"), "/abs/mark.svg", "logo.emblem: '/abs/mark.svg' is not in the expected form"),
    (("fonts", "display", "weights"), {"850": "fonts/x.ttf"}, "fonts.display.weights.850: not a known field"),
    (("version",), 2, "version: 2 is not one of 1"),
    (("feed_captions", "words_per_page"), 4, "feed_captions.words_per_page: 4 is above 3"),
    (("feed_captions", "words_per_page"), 0, "feed_captions.words_per_page: 0 is below 1"),
])
def test_values_that_break_the_schema_say_so_in_plain_words(brand, path, value, part):
    def put(d):
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
    edit(brand, put)
    assert any(p.startswith(part) for p in check(brand)), check(brand)


def test_a_hex_color_may_be_either_case_and_null_where_unset_is_allowed(brand):
    edit(brand, lambda d: (d["colors"].update(accent="#aabbcc"), d["captions"].update(hero_tint="#AABBCC", hero_stroke={"width": 2, "color": "#000000"})))
    assert check(brand) == []
    edit(brand, lambda d: d["colors"].update(accent=None))
    assert check(brand) == ["colors.accent: must be string, got null"]


@pytest.mark.parametrize("fn,part", [
    (lambda d: d["formats"].update(home="vlog"), "formats.home: 'vlog' is not in formats.list"),
    (lambda d: d["formats"].update(list=["a", "a"], home="a"), "formats.list: names a format twice"),
    (lambda d: d["hook"].update(hold_s=[6.0, 3.0]), "hook.hold_s: the shortest hold (6.0) is longer than the longest (3.0)"),
    (lambda d: d["broll"].update(hold_min_s=5, hold_max_s=2), "broll.hold_min_s: 5 is longer than hold_max_s (2)"),
    (lambda d: d["captions"].update(row_px=[200, 100]), "captions.row_px: the smallest row height is larger"),
    (lambda d: d["hook"].update(font="running"), "hook.font: is 'running' but fonts.running is null"),
    (lambda d: d["output"].update(size=[1080, 1080]), "output.size: 1080 x 1080 is not 9:16"),
    (lambda d: d["output"].update(also=["9:16"]), "output.also: names the main aspect 9:16"),
    (lambda d: d["graphics"].update(wishlist=[{"id": "x", "name": "A", "priority": "must"}, {"id": "x", "name": "B", "priority": "later"}]), "graphics.wishlist[1].id: 'x' is used twice"),
    (lambda d: d.update(studies=[{"slug": "a"}, {"slug": "a"}]), "studies[1].slug: 'a' is used twice"),
])
def test_rules_that_need_two_fields(brand, fn, part):
    edit(brand, fn)
    assert any(p.startswith(part) for p in check(brand)), check(brand)


def test_another_aspect_with_its_own_size_passes(brand):
    edit(brand, lambda d: d["output"].update(aspect="4:5", size=[1080, 1350], also=["9:16", "1:1"]))
    assert check(brand) == []


@pytest.mark.parametrize("path", [("colors", "accent"), ("fonts", "display", "file"), ("fonts", "display", "family"), ("output", "size"), ("output", "aspect"),
                                  ("id",), ("name",), ("colors",), ("fonts",), ("output",)])
def test_a_required_key_is_never_filled_and_load_names_what_is_missing(brand, path):
    def drop(d):
        for k in path[:-1]:
            d = d[k]
        del d[path[-1]]
    edit(brand, drop)
    with pytest.raises(dna.DnaError, match="dna.json is missing: .*" + re.escape(".".join(path))):
        dna.load(brand)


def test_fill_never_writes_a_required_key():
    out = dna._fill({"colors": {}, "fonts": {"display": {}}, "output": {}}, dna._template(), dna._schema())
    assert "id" not in out and "name" not in out and out["colors"] == {}
    assert out["fonts"]["display"] == {"weights": {}, "fallback": "sans-serif"} and out["output"] == {"fps": 30, "crf": 17, "also": []}
    assert out["captions"]["pace_gate"] == "report" and out["handle"] == "" and out["people"] == []


def test_omitted_optional_keys_come_from_the_template(brand):
    (brand / "dna.json").write_text(json.dumps({
        "id": "bare", "name": "Bare", "colors": template()["colors"], "fonts": {"display": {"family": "Acme", "file": "fonts/Acme-Bold.woff2"}},
        "output": {"aspect": "9:16", "size": [1080, 1920]}, "hook": {"look": "bar"}, "delivery": {"end_card": {"on": True}}}))
    d = dna.load(brand)
    assert d["captions"]["pace_gate"] == "report" and d["motion"]["auto_punches"] is False and d["hook"]["look"] == "bar" and d["hook"]["top_px"] == 300
    assert d["fonts"]["display"] == {"family": "Acme", "file": "fonts/Acme-Bold.woff2", "weights": {}, "fallback": "sans-serif"}
    assert d["fonts"]["running"] is None and d["delivery"]["end_card"] == {"on": True, "line": "", "handle": ""}
    assert d["output"]["fps"] == 30 and d["version"] == 1 and d["people"] == [] and d["words"] == {"spelling": {}, "banned": []}
    assert check(brand) == []


def test_a_person_gets_the_optional_keys_on_load(brand):
    edit(brand, lambda d: d.update(people=[{"name": "Sam"}, {"name": "Alex", "role": "Owner", "name_tag": False}]))
    assert dna.load(brand)["people"] == [{"name": "Sam", "role": "", "name_tag": True}, {"name": "Alex", "role": "Owner", "name_tag": False}]
    assert check(brand) == []
    edit(brand, lambda d: d.update(people=[{"role": "Owner"}]))
    assert check(brand) == ["people[0].name: missing"]


def test_load_reads_a_folder_or_the_file_in_it_and_the_workspace_brand_by_default(brand, tmp_path, monkeypatch):
    assert dna.load(brand)["id"] == dna.load(brand / "dna.json")["id"] == "acme"
    assert dna.brand_folder(brand / "dna.json") == brand
    ws = tmp_path / "workspace"
    (ws / "Brand").mkdir(parents=True)
    for f in brand.iterdir():
        (ws / "Brand" / f.name).symlink_to(f)
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"workspace": str(ws)}))
    monkeypatch.setattr(dna.common, "CONF", conf)
    assert dna.brand_folder() == ws / "Brand" and dna.load()["id"] == "acme"


def test_a_file_that_is_not_a_dna_is_one_plain_error(tmp_path):
    with pytest.raises(dna.DnaError, match="no dna.json at"):
        dna.load(tmp_path)
    for text, part in (("[1]", "must be a JSON object (the fields), not a list"), ("{nope", "is not valid JSON"), ('"x"', "not a string"), ("null", "not null"), ("3", "not a number")):
        (tmp_path / "dna.json").write_text(text)
        with pytest.raises(dna.DnaError, match=re.escape(part)):
            dna.load(tmp_path)


def test_load_or_exit_is_one_plain_line_and_exit_2(brand, capsys, tmp_path):
    assert dna.load_or_exit(brand)["id"] == "acme"
    with pytest.raises(SystemExit) as e:
        dna.load_or_exit(tmp_path)
    assert e.value.code == 2 and capsys.readouterr().err.startswith("dna: no dna.json at ")
    edit(brand, lambda d: d["captions"].update(zoen=1, zone="middle"))
    with pytest.raises(SystemExit) as e:
        dna.load_or_exit(brand)
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.startswith("dna: cannot be used: ") and "(and 1 more; run dna.py check)" in err and err.count("\n") == 1


def test_save_writes_a_well_formed_dna_and_refuses_a_broken_one(brand):
    d = dna.load(brand)
    d["notes"].append("a note")
    dna.save(d, brand)
    assert json.loads((brand / "dna.json").read_text())["notes"] == ["a note"] and check(brand) == []
    d["captions"]["zone"] = "middle"
    with pytest.raises(dna.DnaError, match="dna.json was not written: captions.zone"):
        dna.save(d, brand)
    assert json.loads((brand / "dna.json").read_text())["captions"]["zone"] == "chest"      # the file is as it was
    d["captions"]["zone"], d["notes"] = "chest", ["TODO later"]
    dna.save(d, brand)                                                                       # a TODO is allowed mid wizard
    assert dna.todo_lines(brand)[0] == "notes[0]: still says TODO"


def test_check_as_a_program_says_ok_or_one_line_per_problem(brand):
    r = run_cli("check", brand)
    assert (r.returncode, r.stdout, r.stderr) == (0, "ok acme\n", "")
    edit(brand, lambda d: d["hook"].update(top_px=100))
    (brand / "DNA.md").write_text("TODO: nothing yet\n")
    r = run_cli("check", brand)
    assert r.returncode == 2 and r.stdout.splitlines() == [
        "hook.top_px: 100 is below 220 (the platform's own header covers the top 220 px of a 1080 x 1920 frame)"]       # a broken value stops the checks that read the rest
    edit(brand, lambda d: d["hook"].update(top_px=300))
    r = run_cli("check", brand)
    assert r.returncode == 2 and r.stdout == "DNA.md line 1: TODO: nothing yet\n"
    r = run_cli("check", brand / "nowhere")
    assert r.returncode == 2 and r.stderr.startswith("dna: no dna.json at ") and "Traceback" not in r.stderr


def leaves(node, path=()):
    """every key path of a value, down through objects and lists (a list index is an int in the path)"""
    if isinstance(node, dict) and node:
        for k, v in node.items():
            yield from leaves(v, path + (k,))
    elif isinstance(node, list) and node:
        for i, v in enumerate(node):
            yield from leaves(v, path + (i,))
    else:
        yield path


def put(root, path, value):
    for k in path[:-1]:
        root = root[k]
    root[path[-1]] = value


ODD = [None, "x", "TODO", "", 1, -1, 1.5, True, [], [None], {}, {"a": 1}]


def full(bd):
    """a DNA with every kind of entry filled in (both other faces, weights, a stroke, people, graphics, a study), and the files it names"""
    for n in ("Cond.woff2", "Mono.woff2", "Bold.woff2"):
        (bd / "assets" / "fonts" / n).write_bytes(WOFF2)
    (bd / "assets" / "logos").mkdir(exist_ok=True)
    (bd / "assets" / "logos" / "mark.svg").write_text("<svg/>")
    def fill(d):
        d["fonts"].update(running={"family": "Cond", "file": "fonts/Cond.woff2", "fallback": "Arial"}, mono={"family": "Mono", "file": "fonts/Mono.woff2", "weights": {"500": "fonts/Mono.woff2"}})
        d["fonts"]["display"]["weights"] = {"800": "fonts/Bold.woff2"}
        d["logo"].update(emblem="logos/mark.svg")
        d["people"] = [{"name": "Sam", "role": "Owner", "name_tag": True}]
        d["captions"].update(hero_stroke={"width": 2, "color": "#000000"}, hero_base="#EEEEEE", neg_words=["never"], icon_words=["call:phone"])
        d["graphics"] = {"wishlist": [{"id": "lower-third", "name": "Lower third", "what": "A name card", "priority": "must", "from_study": "ref-one"}],
                         "built": [{"id": "lower-third", "component": "LowerThird", "since": "2030-01-02"}]}
        d["words"].update(spelling={"acne": "Acme"}, banned=["cheap"])
        d["music"]["banned_md5"] = ["0" * 32]
        d["studies"] = [{"slug": "ref-one", "url": "https://example.com/v", "date": "2030-01-02", "liked": "the pace", "adopted": ["quiet"]}]
        d["notes"] = ["a note"]
        d["formats"].update(list=["talking-head", "list"])
    edit(bd, fill)
    assert check(bd) == []


def test_no_value_of_any_type_crashes_the_check(brand):
    """a hand-edited file can hold anything: the check answers in plain lines, never with a traceback"""
    full(brand)
    base, seen = json.loads((brand / "dna.json").read_text()), 0
    for path in leaves(base):
        for odd in ODD:
            d = json.loads(json.dumps(base))
            put(d, path, odd)
            (brand / "dna.json").write_text(json.dumps(d))
            try:
                got = dna.validate(dna.load(brand), brand)
            except dna.DnaError:
                continue                                   # a required key that is gone is refused by name
            assert isinstance(got, list) and all(isinstance(x, str) for x in got), (path, odd)
            seen += 1
    assert seen > 1500


def test_odd_shapes_for_whole_sections_and_every_style_field_do_not_crash_either(brand):
    full(brand)
    base = json.loads((brand / "dna.json").read_text())
    for key in base:
        for odd in ODD:
            (brand / "dna.json").write_text(json.dumps({**base, key: odd}))
            try:
                assert isinstance(dna.validate(dna.load(brand), brand), list), (key, odd)
            except dna.DnaError:
                pass
    (brand / "dna.json").write_text(json.dumps(base))
    (brand / "styles").mkdir()
    style = {"id": "s", "name": "S", "summary": "x", "use_when": "u", "never_when": "n", "preview": None, "source": {"type": "hand", "ref": "r"}, "opt_in": False, "owner": None,
             "set": {"zone": "top", "row_px": [50, 60], "caption_tier_max": "FULL"}, "look": {"accent": "#FF0000", "hero_stroke": {"width": 1, "color": "#000000"}, "font": "running"},
             "feed": {"place": "top", "size": 1.0, "hero": {"look": "bigger", "scale": 2.0, "pick": "page"}, "words": 2, "motion": "soft"}}
    for path in leaves(style):
        for odd in ODD:
            d = json.loads(json.dumps(style))
            put(d, path, odd)
            (brand / "styles" / "s.json").write_text(json.dumps(d))
            assert isinstance(check(brand), list), (path, odd)
            try:
                dna.style(dna.load(brand), "s", brand)
            except dna.DnaError:
                pass
    for field in style:                                    # and each whole field replaced
        for odd in ODD:
            (brand / "styles" / "s.json").write_text(json.dumps({**style, field: odd}))
            assert isinstance(check(brand), list), (field, odd)
