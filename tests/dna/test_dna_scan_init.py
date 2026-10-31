"""engine/dna.py: the color maths, the folder scan, the logo palette and init (the wizard's first step)."""
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

import pytest

import dna
from conftest import REPO, WOFF2, check, edit, run_cli

DARK_INKS = ["#0B1F3A", "#000000", "#111111", "#2B2B2B", "#0F3D2E", "#5A0F1B", "#3A1F6B", "#1B2A41"]
PAPER_INKS = ["#5B4B3A", "#3D3D5C", "#4A4A4A", "#6B4F3A", "#2F6B4F"]      # luma over 0.2: not a dark ink
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'


def lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lum(h):
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def wcag(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def guide(tmp_path, css=True, fonts=("Acme-ExtraBold.woff2", "Acme-Regular.woff2"), logos=("acme-mark.svg", "acme-wordmark.svg")):
    g = tmp_path / "Acme Guide"
    (g / "fonts").mkdir(parents=True)
    for f in fonts:
        (g / "fonts" / f).write_bytes(WOFF2)
    for lg in logos:
        (g / lg).write_text(SVG)
    if css:
        (g / "style.css").write_text(":root { --brand: #FF5418; --dark: #0B1F3A; --paper: #FFFFFF; }\n.a { color: #FF5418 } .b { background: #FF5418 }\n"
                                     ".c { color: #0B1F3A } .d { color: #0B1F3A } .e { color: #777777 }\n")
    return g


def png(path, blocks, size=(60, 60)):
    """a logo PNG from (color, share of the width) blocks side by side; the rest transparent"""
    from PIL import Image
    im, x = Image.new("RGBA", size, (0, 0, 0, 0)), 0
    for color, share in blocks:
        w = round(size[0] * share)
        im.paste(color + (255,), (x, 0, x + w, size[1]))
        x += w
    im.save(path)
    return path


# ------------------------------------------------------------------ the color maths

def test_luma_sat_mix_and_contrast_have_known_values():
    assert dna.luma("#000000") == 0 and dna.luma("#FFFFFF") == pytest.approx(1.0)
    assert dna.luma("#FF0000") == pytest.approx(0.2126) and dna.sat("#FF0000") == 1 and dna.sat("#777777") == 0
    assert dna.mix("#000000", "#FFFFFF", 0.5) == "#808080" and dna.mix("#102030", "#FFFFFF", 0) == "#102030" and dna.mix("#102030", "#FFFFFF", 1) == "#FFFFFF"
    assert dna.contrast("#FFFFFF", "#000000") == pytest.approx(21) and dna.contrast("#000000", "#FFFFFF") == pytest.approx(21)
    assert dna.rel_lum("#000000") == 0 and dna.rel_lum("#FFFFFF") == pytest.approx(1)
    for a, b in (("#0B1F3A", "#FFB703"), ("#2F80ED", "#0B1F3A"), ("#C2410C", "#FFFFFF")):
        assert dna.contrast(a, b) == pytest.approx(wcag(a, b))


@pytest.mark.parametrize("ink", PAPER_INKS + ["#0B1F3A"])
def test_text_on_a_light_accent_is_the_inks_own_color_when_it_reads_there_else_black(ink):
    want = ink if wcag(ink, "#FFB703") >= 4.5 else "#000000"
    assert dna.on_accent("#FFB703", ink) == want


def test_text_on_a_deep_accent_is_white_and_a_light_accent_with_no_ink_gets_black():
    assert dna.on_accent("#C2410C", None) == "#FFFFFF" and dna.on_accent("#C2410C", "#101820") == "#FFFFFF"
    assert dna.on_accent("#FFB703", None) == "#000000"


@pytest.mark.parametrize("ink", DARK_INKS)
def test_tone_at_finds_the_color_that_reads_the_ratio_on_the_floor(ink):
    hexv, share = dna.tone_at(ink, "#FFFFFF", 5.2)
    assert 5.2 <= wcag(hexv, ink) < 5.3 and 0 < share < 1


def test_tone_at_returns_the_far_end_when_even_it_falls_short_and_can_start_elsewhere():
    assert dna.tone_at("#0B1F3A", "#102040", 5.2) == ("#102040", 1.0)
    lighter, share = dna.tone_at("#0B1F3A", "#FFFFFF", 4.5, start="#2A2A55")        # a dim accent made lighter until it reads on the floor
    assert 4.5 <= wcag(lighter, "#0B1F3A") < 4.6 and 0 < share < 1
    assert dna.mix("#2A2A55", "#FFFFFF", share) == lighter


def test_norm_hex_takes_six_digit_hex_only():
    assert dna.norm_hex("#abcdef", "x") == "#ABCDEF"
    for bad in ("abcdef", "#abc", "red", "", None, "#GGGGGG", "#1234567"):
        with pytest.raises(dna.DnaError, match="x must be #RRGGBB"):
            dna.norm_hex(bad, "x")


# ------------------------------------------------------------------ scan

def test_scan_proposes_accent_ink_base_on_accent_font_and_logos(tmp_path):
    g = guide(tmp_path)
    before = sorted(str(p.relative_to(g)) for p in g.rglob("*"))
    p, notes = dna.scan(g)
    assert (p["accent"], p["ink"], p["base"]) == ("#FF5418", "#0B1F3A", "#FFFFFF")      # the most frequent strong color, dark color and white
    assert p["on_accent"] == dna.on_accent("#FF5418", "#0B1F3A") == "#FFFFFF"
    assert os.path.basename(p["font"]) == "Acme-ExtraBold.woff2" and [os.path.basename(x) for x in p["fonts"]] == ["Acme-Regular.woff2"]   # the heaviest wins
    assert os.path.basename(p["emblem"]) == "acme-mark.svg" and os.path.basename(p["lockup"]) == "acme-wordmark.svg" and len(p["logos"]) == 2
    assert any(n.startswith("colors.accent proposed #FF5418") for n in notes) and any(n.startswith("colors.ink proposed #0B1F3A") for n in notes)
    assert "other colors found (count): #777777 x1" in notes and not [n for n in notes if n.startswith("heads up")]
    assert sorted(str(p.relative_to(g)) for p in g.rglob("*")) == before                # read only


def test_scan_picks_the_strong_color_over_a_more_frequent_gray_and_skips_what_it_cannot_read(tmp_path):
    g = tmp_path / "g"
    g.mkdir()
    (g / "a.css").write_text("#777777 " * 9 + "#10B981 " * 3 + "#0A0A0A " * 2)
    (g / "big.html").write_text("#FF0000 " + "x" * 2_100_000)          # over 2 MB: not read
    (g / ".hidden").mkdir()
    (g / ".hidden" / "x.css").write_text("#0000FF " * 50)
    (g / "node_modules").mkdir()
    (g / "node_modules" / "y.css").write_text("#00FFFF " * 50)
    p, _ = dna.scan(g)
    assert (p["accent"], p["ink"]) == ("#10B981", "#0A0A0A") and "base" not in p and "font" not in p and "logos" not in p


def test_scan_names_font_files_it_cannot_use_and_a_missing_folder_is_a_plain_error(tmp_path):
    g = tmp_path / "g"
    g.mkdir()
    for n in ("Old.eot", "Old.ttc"):
        (g / n).write_bytes(b"x" * 16)
    p, notes = dna.scan(g)
    (line,) = [n for n in notes if n.startswith("heads up")]
    assert "Old.eot" in line and "Old.ttc" in line and ".woff2" in line and "font" not in p
    with pytest.raises(dna.DnaError, match="folder not found"):
        dna.scan(tmp_path / "nope")


def test_a_wordmark_is_a_lockup_and_not_the_emblem(tmp_path):
    p, _ = dna.scan(guide(tmp_path, logos=("wordmark.svg",)))
    assert "emblem" not in p and os.path.basename(p["lockup"]) == "wordmark.svg"
    p, _ = dna.scan(guide(tmp_path / "two", logos=("AcmeWordmark.svg", "acme-mark.svg", "logo-full.svg")))
    assert os.path.basename(p["emblem"]) == "acme-mark.svg" and os.path.basename(p["lockup"]) == "AcmeWordmark.svg"


def test_scan_reads_a_logo_png_when_no_color_is_written_anywhere(tmp_path):
    pytest.importorskip("PIL")
    g = guide(tmp_path, css=False, fonts=(), logos=())
    png(g / "logo.png", [((255, 84, 24), 0.5), ((11, 31, 58), 0.4)])
    p, notes = dna.scan(g)
    assert "colors read from the logo logo.png" in notes
    assert dna.luma(p["accent"]) > 0.12 and dna.sat(p["accent"]) > 0.45 and abs(int(p["accent"][1:3], 16) - 255) < 8 and p["ink"] == "#0B1F3A"


def test_palette_from_image_gives_the_biggest_color_first_and_leaves_transparency_out(tmp_path):
    pytest.importorskip("PIL")
    pal, note = dna.palette_from_image(png(tmp_path / "l.png", [((255, 84, 24), 0.5), ((11, 31, 58), 0.3), ((240, 240, 240), 0.1)]))
    assert note == "" and [c["hex"] for c in pal][:3] == ["#FF5418", "#0B1F3A", "#F0F0F0"]
    assert pal[0]["share"] > pal[1]["share"] > pal[2]["share"] and sum(c["share"] for c in pal) == pytest.approx(1.0, abs=0.02)   # shares of the opaque part
    assert pal[0]["share"] == pytest.approx(0.5 / 0.9, abs=0.03) and len(pal) == 3                                                   # the clear tenth is no color
    assert dna.palette_from_image(png(tmp_path / "l.png", [((255, 84, 24), 0.5), ((11, 31, 58), 0.3), ((240, 240, 240), 0.1)])) == (pal, "")       # a run repeats


def test_palette_from_image_says_why_it_skipped(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(tmp_path / "clear.png")
    (tmp_path / "bad.png").write_text("not an image")
    assert dna.palette_from_image(tmp_path / "clear.png") == ([], "clear.png has no opaque pixels")
    pal, note = dna.palette_from_image(tmp_path / "bad.png")
    assert pal == [] and note.startswith("bad.png could not be read as an image")
    monkeypatch.setitem(sys.modules, "PIL", None)                                       # as if Pillow were not installed
    assert dna.palette_from_image(tmp_path / "clear.png") == ([], "Pillow is not installed, so the colors of the logo were not read (pip install pillow)")


# ------------------------------------------------------------------ init

def test_init_writes_a_dna_whose_only_open_items_are_the_placeholder_lines_of_dna_md(tmp_path, capsys):
    g, bd = guide(tmp_path), tmp_path / "Brand"
    p, notes = dna.scan(g)
    todo = dna.init(bd, "Acme Roofing", notes=notes, **p)
    d = json.loads((bd / "dna.json").read_text())
    assert (d["id"], d["name"], d["handle"]) == ("acme-roofing", "Acme Roofing", "")
    assert (d["colors"]["accent"], d["colors"]["ink"], d["colors"]["bg"], d["colors"]["on_accent"]) == ("#FF5418", "#0B1F3A", "#0B1F3A", "#FFFFFF")
    assert d["colors"]["card"] == dna.mix("#0B1F3A", "#FFFFFF", 0.08) and d["surface"]["background"] == "rgba(31,49,74,0.94)"
    assert d["fonts"]["display"] == {"family": "Acme", "file": "fonts/Acme-ExtraBold.woff2", "weights": {}, "fallback": "sans-serif"}
    assert (d["logo"]["emblem"], d["logo"]["lockup"]) == ("logos/acme-mark.svg", "logos/acme-wordmark.svg")
    assert (bd / "assets" / "fonts" / "Acme-Regular.woff2").read_bytes() == WOFF2 and (bd / "assets" / "logos" / "acme-mark.svg").read_text() == SVG
    assert sorted(f.name for f in (bd / "styles").glob("*.json")) == sorted(f.name for f in (REPO / "templates" / "styles").glob("*.json"))
    md = (bd / "DNA.md").read_text()
    assert md.startswith("# Acme Roofing: how this brand is edited") and md.count("TODO") == 10
    assert any(n.startswith("font family 'Acme' guessed") for n in d["notes"]) and any(n.startswith("colors.accent proposed") for n in d["notes"])
    assert todo == [f"DNA.md line {i}: {ln}" for i, ln in enumerate((bd / "DNA.md").read_text().splitlines(), 1) if "TODO" in ln] and len(todo) == 10
    assert check(bd) == todo and dna.todo_lines(bd) == todo
    out = capsys.readouterr().out
    assert out.startswith(f"created {bd} (dna.json, DNA.md, styles/, assets/); the id is 'acme-roofing'") and "still to do before `dna.py check` passes:" in out
    for line in todo:
        assert f"  {line}\n" in out


def test_once_the_placeholder_lines_are_written_the_check_is_clean(tmp_path):
    bd = tmp_path / "Brand"
    p, _ = dna.scan(guide(tmp_path))
    dna.init(bd, "Acme Roofing", **p)
    (bd / "DNA.md").write_text((bd / "DNA.md").read_text().replace("TODO:", "Done:"))
    assert check(bd) == [] and dna.todo_lines(bd) == []
    r = run_cli("check", bd)
    assert (r.returncode, r.stdout) == (0, "ok acme-roofing\n")


def test_init_with_no_font_leaves_a_todo_in_the_font_for_the_wizard(tmp_path):
    bd = tmp_path / "Brand"
    todo = dna.init(bd, "No Font Co")
    assert todo[:2] == ["fonts.display.family: still says TODO", "fonts.display.file: still says TODO"] and len(todo) == 12
    assert dna.todo_lines(bd) == todo
    r = run_cli("todo", bd)
    assert r.returncode == 0 and r.stdout.splitlines() == todo


def test_todo_as_a_program_prints_nothing_when_nothing_is_left(brand):
    r = run_cli("todo", brand)
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_a_first_init_never_overwrites_a_dna_md_or_a_style_that_is_already_there(tmp_path):
    bd = tmp_path / "Brand"
    (bd / "styles").mkdir(parents=True)
    (bd / "DNA.md").write_text("# My own notes\n")
    (bd / "styles" / "quiet.json").write_text('{"mine": true}')
    dna.init(bd, "Keep Co")
    assert (bd / "DNA.md").read_text() == "# My own notes\n" and (bd / "styles" / "quiet.json").read_text() == '{"mine": true}'
    assert (bd / "styles" / "soft-build.json").is_file()


def test_init_run_again_changes_only_what_it_is_given(tmp_path, capsys):
    g, bd = guide(tmp_path), tmp_path / "Brand"
    dna.init(bd, "Keep Co", handle="keepco", ink="#8A8A8A")                   # a light ink: the first note names it
    edit(bd, lambda d: (d.update(people=[{"name": "Sam", "role": "Owner", "name_tag": True}]), d["hook"].update(look="bar", top_px=400), d["music"].update(feel="calm")))
    (bd / "DNA.md").write_text("# My notes\nWritten by hand.\n")
    (bd / "styles" / "quiet.json").write_text((bd / "styles" / "quiet.json").read_text().replace("Quiet", "My quiet"))
    before = json.loads((bd / "dna.json").read_text())
    assert before["fonts"]["display"]["family"] == "TODO" and before["colors"]["accent"] == "#2F6FED" and len(before["notes"]) == 1
    capsys.readouterr()
    todo = dna.init(bd, "Keep Co", accent="#FF5418", ink="#0B1F3A", font=str(g / "fonts" / "Acme-ExtraBold.woff2"), emblem=str(g / "acme-mark.svg"))
    after = json.loads((bd / "dna.json").read_text())
    assert {k for k in after if after[k] != before[k]} == {"colors", "surface", "fonts", "logo", "notes"}          # nothing else moved: people, hook, music, captions, handle...
    c = after["colors"]
    assert (c["accent"], c["ink"], c["bg"], c["on_accent"], c["card"]) == ("#FF5418", "#0B1F3A", "#0B1F3A", "#FFFFFF", dna.mix("#0B1F3A", "#FFFFFF", 0.08))
    assert after["surface"]["background"] == "rgba(31,49,74,0.94)" and c["negative"] == before["colors"]["negative"] and c["base"] == before["colors"]["base"]
    assert after["fonts"]["display"]["family"] == "Acme" and after["fonts"]["display"]["file"] == "fonts/Acme-ExtraBold.woff2" and after["logo"]["emblem"] == "logos/acme-mark.svg"
    assert (bd / "assets" / "fonts" / "Acme-ExtraBold.woff2").is_file() and (bd / "assets" / "logos" / "acme-mark.svg").is_file()
    assert after["people"] == before["people"] and after["hook"] == before["hook"] and after["music"]["feel"] == "calm" and after["handle"] == "@keepco"
    assert after["notes"][:len(before["notes"])] == before["notes"] and len(after["notes"]) > len(before["notes"])        # earlier notes stay, new ones follow
    assert (bd / "DNA.md").read_text() == "# My notes\nWritten by hand.\n" and "My quiet" in (bd / "styles" / "quiet.json").read_text()
    assert capsys.readouterr().out.startswith(f"updated {bd / 'dna.json'}") and todo == [] == check(bd)


def test_init_run_again_works_out_on_accent_from_a_new_ink_alone_and_does_not_bring_back_a_deleted_starter(tmp_path):
    bd = tmp_path / "Brand"
    dna.init(bd, "Light Co", accent="#FFB703", ink="#101820")
    assert json.loads((bd / "dna.json").read_text())["colors"]["on_accent"] == "#101820"        # the ink reads on this light accent
    (bd / "styles" / "hero-lockup.json").unlink()
    dna.init(bd, ink="#000000")
    c = json.loads((bd / "dna.json").read_text())["colors"]
    assert (c["ink"], c["accent"], c["on_accent"], c["bg"]) == ("#000000", "#FFB703", "#000000", "#000000")
    dna.init(bd, ink="#2B2B2B", accent="#C2410C")
    assert json.loads((bd / "dna.json").read_text())["colors"]["on_accent"] == "#FFFFFF"        # a deep accent takes white
    assert not (bd / "styles" / "hero-lockup.json").exists()                                    # the user's deletion stands


def test_init_run_again_with_nothing_to_change_says_so_and_touches_nothing(tmp_path, capsys):
    bd = tmp_path / "Brand"
    dna.init(bd, "Same Co")
    before = (bd / "dna.json").read_bytes()
    capsys.readouterr()
    todo = dna.init(bd)
    assert (bd / "dna.json").read_bytes() == before and "nothing changed" in capsys.readouterr().out
    assert todo == dna.todo_lines(bd) == check(bd)
    r = run_cli("init", bd, "--name", "Same Co")                       # the same name is a change of nothing, not a refusal
    assert r.returncode == 0 and (bd / "dna.json").read_text().count('"name": "Same Co"') == 1


def test_init_run_again_can_rename_the_brand_but_never_changes_its_id(tmp_path):
    bd = tmp_path / "Brand"
    dna.init(bd, "Old Name")
    dna.init(bd, "New Name", handle="@newname", site="https://new.example")
    d = json.loads((bd / "dna.json").read_text())
    assert (d["id"], d["name"], d["handle"], d["site"]) == ("old-name", "New Name", "@newname", "https://new.example")
    with pytest.raises(dna.DnaError, match="the id is 'old-name' and init does not change it"):
        dna.init(bd, "New Name", brand_id="new-name")
    with pytest.raises(dna.DnaError, match="init needs a name"):
        dna.init(tmp_path / "Fresh")


def test_init_run_again_leaves_a_broken_dna_alone_and_says_why(tmp_path):
    bd = tmp_path / "Brand"
    bd.mkdir()
    (bd / "dna.json").write_text("{nope")
    with pytest.raises(dna.DnaError, match="is not valid JSON"):
        dna.init(bd, "X", accent="#112233")
    assert (bd / "dna.json").read_text() == "{nope"


def test_a_failed_run_again_leaves_the_dna_as_it_was_and_removes_the_files_it_copied(tmp_path, monkeypatch):
    g, bd = guide(tmp_path), tmp_path / "Brand"
    dna.init(bd, "Safe Co")
    before = (bd / "dna.json").read_bytes()
    real, calls = shutil.copyfile, []
    def flaky(src, dst, **kw):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError(13, "Permission denied")
        return real(src, dst, **kw)
    monkeypatch.setattr(shutil, "copyfile", flaky)
    with pytest.raises(dna.DnaError, match="cannot copy"):
        dna.init(bd, font=str(g / "fonts" / "Acme-ExtraBold.woff2"), fonts=[str(g / "fonts" / "Acme-Regular.woff2")], accent="#FF5418")
    assert (bd / "dna.json").read_bytes() == before and not (bd / "assets" / "fonts").exists()


def test_init_takes_an_id_a_handle_and_caps(tmp_path):
    bd = tmp_path / "Brand"
    dna.init(bd, "Acme & Sons, Inc.", brand_id="acme-sons", handle="acme.sons", site="https://acme.example", caps=True)
    d = json.loads((bd / "dna.json").read_text())
    assert (d["id"], d["handle"], d["site"], d["type"]["caps"], d["captions"]["case"]) == ("acme-sons", "@acme.sons", "https://acme.example", True, "upper")
    dna.init(tmp_path / "B2", "Acme & Sons, Inc.")
    assert json.loads((tmp_path / "B2" / "dna.json").read_text())["id"] == "acme-sons-inc"
    dna.init(tmp_path / "B4", "Caf\u00e9 D\u00fcsseldorf")
    assert json.loads((tmp_path / "B4" / "dna.json").read_text())["id"] == "cafe-dusseldorf"
    for bad, part in ((dict(name="   "), "init needs a name"), (dict(name="Fine", brand_id="Bad_Id"), "id 'Bad_Id' must be lowercase letters, digits and dashes"),
                      (dict(name="!!!"), re.escape("must be lowercase letters, digits and dashes (pass --id)"))):
        with pytest.raises(dna.DnaError, match=part):
            dna.init(tmp_path / "B3", bad.pop("name"), **bad)
    assert not (tmp_path / "B3").exists()


def test_a_dark_ink_makes_the_floor_and_the_card_and_a_lighter_ink_leaves_the_neutral_dark_ones(tmp_path):
    neutral = json.loads((REPO / "templates" / "dna.json").read_text())["colors"]
    for i, ink in enumerate(DARK_INKS):
        dna.init(tmp_path / f"d{i}", "Dark", ink=ink, accent="#FFB703")
        c = json.loads((tmp_path / f"d{i}" / "dna.json").read_text())["colors"]
        assert c["ink"] == c["bg"] == ink and c["card"] == dna.mix(ink, "#FFFFFF", 0.08) and c["on_accent"] == dna.on_accent("#FFB703", ink)
    for i, ink in enumerate(PAPER_INKS):
        dna.init(tmp_path / f"p{i}", "Paper", ink=ink, accent="#C2410C")
        d = json.loads((tmp_path / f"p{i}" / "dna.json").read_text())
        assert d["colors"]["ink"] == ink and d["colors"]["bg"] == neutral["bg"] and d["colors"]["card"] == neutral["card"] and d["colors"]["on_accent"] == "#FFFFFF"
        assert any("is not dark" in n for n in d["notes"])


def test_an_accent_that_cannot_be_read_on_the_floor_is_flagged_with_a_lighter_one(tmp_path, capsys):
    dna.init(tmp_path / "weak", "Weak", ink="#0B1F3A", accent="#2A2A55")
    out = capsys.readouterr().out
    (line,) = [ln for ln in out.splitlines() if ln.startswith("heads up")]
    assert f"the accent #2A2A55 reads {wcag('#2A2A55', '#0B1F3A'):.2f}:1 on the floor #0B1F3A, under 3:1" in line
    lighter = dna.tone_at("#0B1F3A", "#FFFFFF", 4.5, start="#2A2A55")[0]
    assert f"A lighter {lighter} reads 4.5:1" in line
    dna.init(tmp_path / "fine", "Fine", ink="#0B1F3A", accent="#6EA8FF")
    assert "heads up" not in capsys.readouterr().out


def test_init_checks_every_file_before_it_writes(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    for kw, part in ((dict(font=str(notes)), "font must be a .ttf or .otf or .woff or .woff2 file, got notes.txt"), (dict(emblem=str(notes)), "emblem must be a .svg or .png file"),
                     (dict(lockup=str(notes)), "lockup must be a"), (dict(lockup_dark=str(notes)), "lockup_dark must be a"), (dict(font="/no/such.ttf"), "font file not found: /no/such.ttf"),
                     (dict(accent="red"), "accent must be #RRGGBB"), (dict(ink="#12"), "ink must be #RRGGBB")):
        with pytest.raises(dna.DnaError, match=part):
            dna.init(tmp_path / "Brand", "Bad", **kw)
        assert not (tmp_path / "Brand").exists()


def test_a_failed_init_leaves_nothing_behind(tmp_path, monkeypatch):
    g = guide(tmp_path)
    real, calls = shutil.copyfile, []
    def flaky(src, dst, **kw):
        calls.append(dst)
        if len(calls) == 3:
            raise OSError(13, "Permission denied")
        return real(src, dst, **kw)
    monkeypatch.setattr(shutil, "copyfile", flaky)
    bd = tmp_path / "Brand"
    with pytest.raises(dna.DnaError, match="cannot copy .* into the brand: Permission denied"):
        dna.init(bd, "Half", **dna.scan(g)[0])
    assert len(calls) == 3 and not bd.exists()
    bd.mkdir()
    (bd / "keep.txt").write_text("mine")
    calls.clear()
    with pytest.raises(dna.DnaError):
        dna.init(bd, "Half", **dna.scan(g)[0])
    assert sorted(p.name for p in bd.iterdir()) == ["keep.txt"] and (bd / "keep.txt").read_text() == "mine"       # only what init made is removed


def test_a_system_font_is_copied_by_its_bytes(tmp_path):
    system = "/System/Library/Fonts/Supplemental/Arial.ttf"
    if not os.path.isfile(system):
        pytest.skip("no system Arial on this machine")
    dna.init(tmp_path / "Brand", "Sys", font=system)
    assert (tmp_path / "Brand" / "assets" / "fonts" / "Arial.ttf").read_bytes() == Path(system).read_bytes()


def test_a_font_that_cannot_be_read_stops_with_one_plain_line(tmp_path):
    locked = tmp_path / "Locked.ttf"
    locked.write_bytes(b"x" * 64)
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this user can read a mode 0 file")
        r = run_cli("init", tmp_path / "Brand", "--name", "Locked", "--font", locked)
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert r.returncode == 2 and "Traceback" not in r.stderr and r.stderr.startswith("dna: cannot copy ") and r.stderr.count("\n") == 1
    assert not (tmp_path / "Brand").exists()


def test_a_font_with_the_same_name_is_copied_beside_the_first_not_over_it(tmp_path):
    a, b = tmp_path / "a" / "Same.woff2", tmp_path / "b" / "Same.woff2"
    for f, extra in ((a, b"A"), (b, b"B")):
        f.parent.mkdir()
        f.write_bytes(WOFF2 + extra)
    dna.init(tmp_path / "Brand", "Twin", font=str(a), fonts=[str(b)])
    names = sorted(p.name for p in (tmp_path / "Brand" / "assets" / "fonts").iterdir())
    assert names == ["Same-2.woff2", "Same.woff2"] and (tmp_path / "Brand" / "assets" / "fonts" / "Same.woff2").read_bytes().endswith(b"A")


# ------------------------------------------------------------------ the programs

def test_init_scan_and_check_as_programs(tmp_path):
    g, bd = guide(tmp_path), tmp_path / "Brand"
    r = run_cli("scan", g)
    assert r.returncode == 0 and json.loads(r.stdout[:r.stdout.rindex("}") + 1])["accent"] == "#FF5418" and "colors.accent proposed #FF5418" in r.stdout
    r = run_cli("init", bd, "--name", "Acme Roofing", "--from", g, "--accent", "#00AA88", "--handle", "acmeroofing", "--caps")
    assert r.returncode == 0, r.stderr
    d = json.loads((bd / "dna.json").read_text())
    assert (d["colors"]["accent"], d["colors"]["ink"], d["handle"], d["type"]["caps"]) == ("#00AA88", "#0B1F3A", "@acmeroofing", True)
    assert d["colors"]["on_accent"] == dna.on_accent("#00AA88", "#0B1F3A")           # worked out again from the accent given
    assert d["fonts"]["display"]["file"] == "fonts/Acme-ExtraBold.woff2"
    r = run_cli("init", tmp_path / "B2", "--name", "Flags", "--font", g / "fonts" / "Acme-Regular.woff2", "--emblem", g / "acme-mark.svg", "--lockup", g / "acme-wordmark.svg",
                "--ink", "#101820", "--from", g)
    assert r.returncode == 0, r.stderr
    d = json.loads((tmp_path / "B2" / "dna.json").read_text())
    assert d["fonts"]["display"]["file"] == "fonts/Acme-Regular.woff2" and d["logo"]["emblem"] == "logos/acme-mark.svg" and d["colors"]["ink"] == "#101820"
    assert (tmp_path / "B2" / "assets" / "fonts" / "Acme-ExtraBold.woff2").is_file()          # the font the scan picked is copied along
    r = run_cli("init", tmp_path / "B3", "--name", "Bad", "--accent", "nope")
    assert r.returncode == 2 and r.stderr == "dna: accent must be #RRGGBB, got 'nope'\n"
    assert run_cli("init", tmp_path / "B3").returncode == 2                           # --name is required
    assert run_cli("nonsense").returncode == 2 and run_cli().returncode == 2


def test_the_doctor_calls_check_with_no_folder_and_the_workspace_decides(tmp_path):
    ws = tmp_path / "ws"
    (ws / "Brand").mkdir(parents=True)
    p, _ = dna.scan(guide(tmp_path))
    dna.init(ws / "Brand", "Acme Roofing", **p)
    (ws / "Brand" / "DNA.md").write_text((ws / "Brand" / "DNA.md").read_text().replace("TODO:", "Done:"))
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"workspace": str(ws)}))
    r = run_cli("check", env={**os.environ, "CUTROOM_HOME": str(home)})
    assert (r.returncode, r.stdout) == (0, "ok acme-roofing\n")
    r = run_cli("check", env={**os.environ, "CUTROOM_HOME": str(tmp_path / "empty")})
    assert r.returncode == 2 and r.stderr.startswith("STOP: no workspace is set")


def test_scan_says_so_when_a_png_logo_cannot_be_read_for_want_of_pillow(tmp_path, monkeypatch):
    g = guide(tmp_path, css=False, fonts=(), logos=())
    (g / "logo.png").write_bytes(b"\x89PNG not read")
    monkeypatch.setitem(sys.modules, "PIL", None)
    p, notes = dna.scan(g)
    assert "accent" not in p and "Pillow is not installed, so the colors of the logo were not read (pip install pillow)" in notes
    assert p["logos"] == [str(g / "logo.png")]


def test_the_wizards_two_calls_as_programs_start_with_a_name_then_add_the_look(tmp_path):
    g, bd = guide(tmp_path), tmp_path / "Brand"
    r = run_cli("init", bd, "--name", "Acme Roofing")
    assert r.returncode == 0 and "fonts.display.family: still says TODO" in r.stdout and "created " in r.stdout
    r = run_cli("init", bd, "--name", "Acme Roofing", "--accent", "#00AA88", "--ink", "#101820", "--font", g / "fonts" / "Acme-ExtraBold.woff2", "--emblem", g / "acme-mark.svg")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(f"updated {bd / 'dna.json'}") and "fonts.display" not in r.stdout and "still to do before" in r.stdout
    d = json.loads((bd / "dna.json").read_text())
    assert (d["name"], d["colors"]["accent"], d["colors"]["bg"], d["fonts"]["display"]["family"], d["logo"]["emblem"]) == ("Acme Roofing", "#00AA88", "#101820", "Acme", "logos/acme-mark.svg")
    assert run_cli("todo", bd).stdout.count("DNA.md line") == 10 and run_cli("check", bd).stdout.count("DNA.md line") == 10
