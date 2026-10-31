"""index.py (START HERE.html) and qc.py (the proof gate)."""
import csv
import html
import json
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote

import pytest

import index
import job as jobmod
import organize
import qc
from conftest import intake, ok, run_tool, sha_tree
from test_takes_encode import run_takes, shoot  # noqa: F401 (shoot is a pytest fixture)


# ---------------------------------------------------------------- color: both themes must read
def css_sets(page):
    """the two :root variable sets of a page: (dark, light)"""
    dark = re.search(r":root\{color-scheme:dark light;([^}]*)\}", page).group(1)
    light = re.search(r"@media \(prefers-color-scheme:light\)\{:root\{([^}]*)\}", page).group(1)
    return [dict(kv.split(":", 1) for kv in s.split(";") if kv) for s in (dark, light)]


PAIRS = [("--txt", "--ink"), ("--txt", "--ink2"), ("--txt2", "--ink2"), ("--mut", "--ink"), ("--mut", "--ink2"), ("--mut", "--ink3"), ("--accent-text", "--ink"),
         ("--accent-text", "--ink2"), ("--accent-text", "--ink3"), ("--amber-text", "--ink3"), ("--amber-text", "--ink2"), ("--on-accent", "--accent")]


@pytest.mark.parametrize("accent", ["#4F8CFF", "#777777", "#ffffff", "#000000", "#ff0000", "#00ff00", "#ffe600", "#1b3a8a", "#808080", "#abc"])
@pytest.mark.parametrize("ink", ["#0B0C10", "#1a1d29", "#222222", "#000000", "#4a4a4a"])
def test_every_text_color_reads_on_its_surface_in_both_themes(accent, ink):
    dark, light = index.tokens({"accent": accent, "ink": ink, "font_display": "system-ui"})
    for name, t in (("dark", dark), ("light", light)):
        css = {f"--{k.replace('_', '-')}": v for k, v in t.items()}
        for fg, bg in PAIRS:
            assert index.contrast(css[fg], css[bg]) >= 4.5, (name, fg, bg, css[fg], css[bg])


def test_the_page_carries_both_themes_and_asks_the_browser_which(organized):
    page = (organized / "START HERE.html").read_text(encoding="utf-8")
    dark, light = css_sets(page)
    assert dark["--ink"] == "#0B0C10" and dark["--accent"] == "#4F8CFF" and light["--accent"] == "#4F8CFF"
    assert index.lum(light["--ink"]) > 0.8 > 0.2 > index.lum(dark["--ink"])         # one is a dark page, the other a light one
    assert '<meta name="color-scheme" content="dark light">' in page and "@media (prefers-color-scheme:light)" in page
    for fg, bg in PAIRS:
        for t in (dark, light):
            assert index.contrast(t[fg], t[bg]) >= 4.5


def test_the_brand_changes_the_colors_and_the_heading_font_and_nothing_else(organized):
    j = json.loads((organized / "job.json").read_text())
    j["brand"] = {"accent": "#e4572e", "ink": "#101820", "font_display": "Georgia"}
    (organized / "job.json").write_text(json.dumps(j))
    r = run_tool("index.py", "--job", organized / "job.json")
    assert r.returncode == 0, ok(r)
    page = (organized / "START HERE.html").read_text(encoding="utf-8")
    dark, light = css_sets(page)
    assert dark["--accent"] == "#e4572e" and dark["--ink"] == "#101820" and 'font-family:"Georgia"' in page
    assert "fonts.googleapis" not in page and not re.search(r'(?:src|href)="https?://', page)          # one file, nothing fetched
    for t in (dark, light):
        for fg, bg in PAIRS:
            assert index.contrast(t[fg], t[bg]) >= 4.5


# ---------------------------------------------------------------- the page
def cards(page):
    return re.findall(r'<article class="card ([vhs])" data-search="([^"]*)" data-audio="([a-z]+)">', page)


def test_the_index_lists_every_clip_once_in_its_own_shape_with_a_thumbnail(organized):
    page = (organized / "START HERE.html").read_text(encoding="utf-8")
    plan = json.loads((organized / ".work" / "plan.json").read_text())
    hrefs = [unquote(html.unescape(h)) for h in re.findall(r'class="thumb" href="([^"]+)"', page)]
    assert sorted(hrefs) == sorted(m["rel"] for m in plan["moves"])
    assert all((organized / h).exists() for h in hrefs)
    assert len(re.findall(r'<img src="data:image/jpeg;base64,[A-Za-z0-9+/=]{100,}"', page)) == 7
    shapes = {m.group(2).split(" - ")[0]: m.group(1) for m in re.finditer(r'<article class="card ([vhs])".*?aria-label="Open ([^"]+)"', page, re.S)}
    assert shapes == {"C0001": "v", "C0002": "v", "C0003": "h", "C0004": "v", "C0005": "h", "C0006": "s", "C0007": "v"}
    assert page.count('data-audio="silent"') == 2 and page.count('data-audio="candid"') == 1 and page.count('data-audio="audio"') == 4


def test_the_header_the_coverage_and_the_footer_say_what_the_shoot_is(organized):
    page = (organized / "START HERE.html").read_text(encoding="utf-8")
    assert "<title>Candle Workshop Start Here · Shoot 2026-10-31</title>" in page and "<h1>Candle Workshop <span>Start Here</span></h1>" in page
    assert "<b>7</b>clips" in page and "<b>3</b>interview answers" in page and "<b>4</b>b-roll clips" in page and "<b>2</b>silent clips" in page
    assert re.search(r'<div class="cov hit"><b>Q01</b><span>Why Our Process Takes Three Days</span><em>2 takes</em>', page)
    assert re.search(r'<div class="cov hit"><b>Q02</b><span>Welcome To The Workshop</span><em>1 take</em>', page)
    assert "Candle Workshop, shoot 2026-10-31. Organized " in page and "moved (not copied) out of the source folder" in page
    assert "Built by" not in page


def test_a_section_with_nothing_in_it_is_left_out_and_a_question_never_filmed_is_marked(organized):
    page = (organized / "START HERE.html").read_text(encoding="utf-8")
    assert "Everything else" not in page and "<h2>Interview: on-camera answers</h2>" in page and "<h2>B-Roll: by what is on screen</h2>" in page
    j = jobmod.load_job(organized / "job.json")
    j["questions"].append({"id": "Q03", "title": "Never Filmed", "question": "x"})
    out = index.build(j, str(organized / "wide.html"))
    assert re.search(r'<div class="cov miss"><b>Q03</b><span>Never Filmed</span><em>0 takes</em>', Path(out).read_text(encoding="utf-8"))


def test_what_a_classifier_wrote_is_escaped_never_run(organized):
    res = organized / ".work" / "plan.json"
    plan = json.loads(res.read_text())
    plan["records"][0]["answer_summary"] = '<script>alert("x")</script> & "quotes" <img src=x onerror=alert(1)>'
    plan["records"][0]["flags"] = ['"><script>bad()</script>']
    res.write_text(json.dumps(plan))
    out = index.build(jobmod.load_job(organized / "job.json"), str(organized / "x.html"))
    page = Path(out).read_text(encoding="utf-8")
    assert "<script>alert" not in page and "<script>bad" not in page and "onerror=alert(1)>" not in page.replace("&lt;img", "")
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in page


def test_the_search_and_filters_script_is_present_and_hooks_every_card(organized):
    page = (organized / "START HERE.html").read_text(encoding="utf-8")
    assert page.count("<script>") == 1 and "querySelectorAll('.card')" in page and page.count('<button type="button" data-f=') == 4
    assert 'aria-label="Search the footage"' in page and 'placeholder="Search anything said or seen"' in page
    # every card's search text holds its file name and what it is about
    texts = {m[1] for m in cards(page)}
    assert any("welcome to the workshop" in t for t in texts) and any("process takes three days" in t for t in texts)


def test_a_copied_shoot_says_it_was_copied(tmp_path, organized):
    plan = json.loads((organized / ".work" / "plan.json").read_text())
    plan["mode"] = "copy"
    (organized / ".work" / "plan.json").write_text(json.dumps(plan))
    out = index.build(jobmod.load_job(organized / "job.json"), str(tmp_path / "c.html"))
    assert "copied from the source folder, which was left as it was" in Path(out).read_text(encoding="utf-8")


def test_after_takes_the_index_has_a_card_per_cut_with_its_words_and_counts_takes_per_line(shoot):
    assert run_takes(shoot).returncode == 0
    r = run_tool("index.py", "--job", shoot / "job.json")
    assert r.returncode == 0, ok(r)
    page = (shoot / "START HERE.html").read_text(encoding="utf-8")
    assert "<b>3</b>takes cut" in page and "<b>1</b>bloopers cut" in page
    assert re.search(r'<div class="cov hit"><b>Q01</b><span>Why Our Process Takes Three Days</span><em>3 takes</em>', page)      # the take of C0001 and the flub and the clean take of C0007 (the raw clips are not takes, the blooper is B-roll)
    assert "TAKE 3 · CLEAN" in page and "BLOOPER" in page and "TAKE 2 · FLUB" in page
    assert "Raw Clips" in page and "Bloopers" in page


def test_a_clip_name_with_spaces_gets_its_cut_thumbnails(shoot, tmp_path):
    """the cut's id is read by length, not by the first word, so a clip called 'take one' finds its own thumbnail"""
    rows = [{"file": "Interview/Q01 X/0025 - take one-02 - Q01 - Take 1 - Clean.mov", "source_clip": "0025 - take one"}]
    assert jobmod.cut_id(rows[0]) == "0025 - take one-02"


# ---------------------------------------------------------------- qc
def qc_out(root, *args):
    return run_tool("qc.py", "--job", root / "job.json", *args)


def test_qc_prints_qc_ok_and_nothing_else_when_everything_holds(organized):
    r = qc_out(organized)
    assert r.returncode == 0 and r.stdout == "QC OK\n" and r.stderr == ""


def test_verbose_qc_lists_every_check(organized):
    r = qc_out(organized, "-v")
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK"
    lines = r.stdout.splitlines()
    assert all(l.startswith("  ok   ") for l in lines[:-1]) and len(lines) >= 10
    assert any("all 7 planned files exist" in l for l in lines) and any("ffprobe plays all 7 clips" in l for l in lines) and any("index thumbnails embedded: 7/7" in l for l in lines)


def one_problem(r, text):
    assert r.returncode == 1, ok(r)
    lines = r.stdout.strip().splitlines()
    assert all(l.startswith("QC: ") for l in lines) and "QC OK" not in r.stdout       # one line per problem, nothing else
    assert any(text in l for l in lines), lines
    return lines


def test_qc_names_a_missing_clip(organized):
    next((organized / "B-Roll").rglob("C0003*.MP4")).unlink()
    lines = one_problem(qc_out(organized), "all 7 planned files exist: missing")
    assert any("byte total" in l for l in lines)


def test_qc_names_a_clip_that_will_not_play(organized):
    p = next((organized / "B-Roll").rglob("C0003*.MP4"))
    size = p.stat().st_size
    p.write_bytes(b"\0" * size)
    one_problem(qc_out(organized), "ffprobe plays all 7 clips: unplayable")


def test_qc_names_a_clip_whose_size_changed(organized):
    p = next((organized / "B-Roll").rglob("C0003*.MP4"))
    with open(p, "ab") as fh:
        fh.write(b"extra")
    one_problem(qc_out(organized), "byte total")


def test_qc_fails_a_plan_that_lost_its_pre_move_total(organized):
    for p in (organized / ".work" / "plan.json", organized / "Edit" / "plan.json"):
        d = json.loads(p.read_text())
        d.pop("pre_move_bytes")
        p.write_text(json.dumps(d))
    one_problem(qc_out(organized), "plan.json has no pre_move_bytes")


def test_qc_names_a_video_left_in_the_source_folder(organized):
    (organized / "RAW").mkdir()
    shutil.copy(next((organized / "B-Roll").rglob("C0003*.MP4")), organized / "RAW" / "C0099.MP4")
    one_problem(qc_out(organized), "no video left in the source folder: still there ['C0099.MP4']")


def test_a_manifest_with_a_row_missing_fails(organized):
    mf = organized / "Footage Manifest.csv"
    rows = list(csv.DictReader(open(mf, encoding="utf-8")))[:-1]
    with open(mf, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    one_problem(qc_out(organized), "manifest rows 6 == clips 7")


def test_an_index_that_lost_a_card_or_a_thumbnail_fails(organized):
    p = organized / "START HERE.html"
    page = p.read_text(encoding="utf-8")
    p.write_text(re.sub(r'<article class="card [vhs]".*?</article>', "", page, count=1, flags=re.S), encoding="utf-8")
    lines = one_problem(qc_out(organized), "index has 6 links for 7 clips and cuts")
    assert any("index lists every clip and every cut exactly once: not listed" in l for l in lines) and any("index thumbnails embedded: 6/7" in l for l in lines)
    p.unlink()
    one_problem(qc_out(organized), "START HERE.html exists")


def test_a_missing_readme_and_an_unclassified_clip_are_named(organized):
    (organized / "README.txt").unlink()
    one_problem(qc_out(organized), "README.txt exists")


def test_no_plan_anywhere_is_one_line(organized):
    for p in (organized / ".work" / "plan.json", organized / "Edit" / "plan.json"):
        p.unlink()
    r = qc_out(organized)
    assert r.returncode == 1 and r.stdout.strip() == "QC: no plan.json in the scratch folder or in Edit/ (organize the shoot first: intake.py apply)"


def test_qc_survives_the_scratch_folder_being_deleted(organized):
    shutil.rmtree(organized / ".work")
    r = qc_out(organized)
    assert r.returncode == 0 and r.stdout == "QC OK\n"        # the plan outlives it in Edit/, and nothing else is read from it


def test_qc_checks_the_cuts_too(shoot):
    assert run_takes(shoot).returncode == 0 and run_tool("index.py", "--job", shoot / "job.json").returncode == 0
    assert qc_out(shoot).stdout == "QC OK\n"
    rows = list(csv.DictReader(open(shoot / "Takes Manifest.csv", encoding="utf-8")))
    victim = shoot / rows[0]["file"]
    victim.rename(str(victim) + ".gone")
    one_problem(qc_out(shoot), "all 4 cut files in Takes Manifest.csv exist: missing")
    victim = Path(str(victim) + ".gone")
    victim.rename(str(victim)[:-5])
    (shoot / "Interview" / "Q01 Why Our Process Takes Three Days" / "unlisted.mov").write_bytes(b"x")
    one_problem(qc_out(shoot), "every cut file on disk is listed in Takes Manifest.csv: unlisted")
    (shoot / "Interview" / "Q01 Why Our Process Takes Three Days" / "unlisted.mov").unlink()
    (shoot / "Interview" / "Q01 Why Our Process Takes Three Days" / "half.part.mov").write_bytes(b"x")
    one_problem(qc_out(shoot), "no half-written .part.mov files")
    (shoot / "Interview" / "Q01 Why Our Process Takes Three Days" / "half.part.mov").unlink()
    assert qc_out(shoot).stdout == "QC OK\n"


def test_qc_checks_each_cuts_duration_against_its_in_and_out(shoot):
    assert run_takes(shoot).returncode == 0 and run_tool("index.py", "--job", shoot / "job.json").returncode == 0
    mf = shoot / "Takes Manifest.csv"
    rows = list(csv.DictReader(open(mf, encoding="utf-8")))
    rows[0]["out_s"] = str(float(rows[0]["out_s"]) + 30)
    with open(mf, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    one_problem(qc_out(shoot), "every cut's duration matches its in/out")


def test_qc_checks_every_cuts_raw_clip_is_in_raw_clips(shoot):
    assert run_takes(shoot).returncode == 0 and run_tool("index.py", "--job", shoot / "job.json").returncode == 0
    mf = shoot / "Takes Manifest.csv"
    rows = list(csv.DictReader(open(mf, encoding="utf-8")))
    rows[0]["raw_path"] = "Raw Clips/not there.mp4"
    with open(mf, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    one_problem(qc_out(shoot), "every cut's raw clip is in Raw Clips/")


@pytest.mark.parametrize("brand", [{"ink": "#e8e8e8"}, {"ink": "#ffffff"}, {"ink": "#888"}, {"ink": "#666666"}])
def test_an_ink_that_is_not_dark_stops_the_run_because_it_is_the_dark_themes_background(tmp_path, capsys, brand):
    (tmp_path / "job.json").write_text(json.dumps({"shoot_type": "interview", "brand": brand}))
    with pytest.raises(SystemExit):
        jobmod.load_job(tmp_path / "job.json")
    assert "job.brand.ink must be a dark color" in capsys.readouterr().err
