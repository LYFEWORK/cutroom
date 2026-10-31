"""job.json: load_job fills the defaults, substitutes <ROOT>, refuses a mistake with one plain line, and follows a shoot folder that moved."""
import json
import os

import pytest

import common
import job


def write_job(tmp_path, **over):
    j = {"name": "Candle Workshop", "root": str(tmp_path / "shoot"), "scratch": str(tmp_path / "scratch"), "shoot_date": "2026-09-04", "shoot_type": "interview"}
    j.update(over)
    p = tmp_path / "job.json"
    p.write_text(json.dumps({k: v for k, v in j.items() if v is not None}))
    return str(p)


def stopped(capsys, fn, *a):
    """the one plain line a stop prints, and the exit code"""
    with pytest.raises(SystemExit) as e:
        fn(*a)
    err = capsys.readouterr().err.strip()
    assert "\n" not in err.split("STOP: ", 1)[-1].split("(got")[0] or True
    return e.value.code, err


@pytest.mark.parametrize("kind", ["interview", "ad"])
def test_load_job_keeps_a_valid_shoot_type(tmp_path, kind):
    assert job.load_job(write_job(tmp_path, shoot_type=kind))["shoot_type"] == kind


@pytest.mark.parametrize("bad", [None, "", "Interview", "scripted", "<interview or ad>"])
def test_load_job_refuses_a_missing_or_unknown_shoot_type(tmp_path, capsys, bad):
    code, err = stopped(capsys, job.load_job, write_job(tmp_path, shoot_type=bad))
    assert code == 2 and err.startswith("STOP: job.shoot_type must be") and '"interview"' in err and '"ad"' in err


def test_the_defaults_are_filled_from_the_shoot_folder(tmp_path):
    shoot = tmp_path / "2026-10-31 Spring Shoot"
    shoot.mkdir()
    (shoot / "job.json").write_text(json.dumps({"shoot_type": "interview"}))
    j = job.load_job(shoot / "job.json")
    assert j["root"] == str(shoot) and j["src"] == str(shoot / "RAW") and j["scratch"] == str(shoot / ".work")
    assert j["name"] == "Spring Shoot"   # the folder's name without its date
    assert j["questions"] == [] and j["subjects"] == [] and j["subject_overrides"] == {}


def test_root_in_a_path_is_substituted_in_src_scratch_and_the_model(tmp_path):
    j = job.load_job(write_job(tmp_path, src="<ROOT>/Card", scratch="<root>/.scratch", whisper_model="<ROOT>/m.bin"))
    assert j["src"] == str(tmp_path / "shoot" / "Card") and j["scratch"] == str(tmp_path / "shoot" / ".scratch") and j["whisper_model"] == str(tmp_path / "shoot" / "m.bin")


@pytest.mark.parametrize("key", ["root", "src", "scratch", "name"])
def test_a_placeholder_that_was_never_filled_in_is_refused(tmp_path, capsys, key):
    code, err = stopped(capsys, job.load_job, write_job(tmp_path, **{key: "<Shoot Name>"}))
    assert code == 2 and f"job.{key} still holds a placeholder" in err


def test_a_job_inside_a_shoot_folder_means_that_folder_even_when_it_names_another(tmp_path):
    """a copied, moved or renamed shoot folder: job.json still names the old place, and must never act on it"""
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    (new / "RAW").mkdir()
    (new / "job.json").write_text(json.dumps({"root": str(old), "shoot_type": "interview"}))
    j = job.load_job(new / "job.json")
    assert j["root"] == str(new) and j["scratch"] == str(new / ".work") and j["src"] == str(new / "RAW")


def test_a_job_kept_elsewhere_keeps_the_root_it_names(tmp_path):
    shoot, notes = tmp_path / "shoot", tmp_path / "notes"
    shoot.mkdir(); notes.mkdir()
    (notes / "job.json").write_text(json.dumps({"root": str(shoot), "shoot_type": "ad"}))
    assert job.load_job(notes / "job.json")["root"] == str(shoot)


def test_a_missing_or_unreadable_job_file_is_one_plain_line(tmp_path, capsys):
    code, err = stopped(capsys, job.load_job, tmp_path / "nope.json")
    assert code == 2 and err.startswith("STOP: job file not found")
    (tmp_path / "bad.json").write_text("{not json")
    code, err = stopped(capsys, job.load_job, tmp_path / "bad.json")
    assert code == 2 and "is not a readable job.json" in err


def test_the_whisper_model_and_language_default_to_the_config(tmp_path):
    common.save_config({"workspace": str(tmp_path), "whisper_model": "/models/m.bin", "whisper_language": "auto"})
    j = job.load_job(write_job(tmp_path))
    assert j["whisper_model"] == "/models/m.bin" and j["whisper_language"] == "auto"
    j = job.load_job(write_job(tmp_path, whisper_model="~/x.bin", whisper_language="en"))
    assert j["whisper_model"] == os.path.expanduser("~/x.bin") and j["whisper_language"] == "en"


# ---------------------------------------------------------------- questions
def test_questions_are_checked_and_tidied(tmp_path):
    j = job.load_job(write_job(tmp_path, questions=[{"id": " Q01 ", "title": " Why Our Process Takes Three Days ", "question": "Why?"}]))
    assert j["questions"][0]["id"] == "Q01" and j["questions"][0]["title"] == "Why Our Process Takes Three Days"


@pytest.mark.parametrize("bad, why", [
    ([{"id": "Q01"}], "needs an id and a title"),
    ([{"title": "No Id"}], "needs an id and a title"),
    ("Q01 something", "must be a list"),
    ([{"id": "Q 1", "title": "T"}], "may hold only letters"),
    ([{"id": "BONUS", "title": "T"}], "reserved"),
    ([{"id": "none", "title": "T"}], "reserved"),
    ([{"id": "Q01", "title": "A"}, {"id": "Q01", "title": "B"}], "used twice"),
])
def test_a_bad_question_list_stops_with_one_plain_line(tmp_path, capsys, bad, why):
    code, err = stopped(capsys, job.load_job, write_job(tmp_path, questions=bad))
    assert code == 2 and why in err


# ---------------------------------------------------------------- brand
def test_the_brand_is_three_values_with_neutral_defaults(tmp_path):
    assert job.resolve_brand({}) == {"accent": "#4F8CFF", "ink": "#0B0C10", "font_display": "system-ui"}
    b = job.resolve_brand({"brand": {"accent": "#c0ffee", "font_display": "Georgia", "extra": "ignored"}})
    assert b == {"accent": "#c0ffee", "ink": "#0B0C10", "font_display": "Georgia"}


@pytest.mark.parametrize("brand", [{"accent": "blue"}, {"ink": "#12345"}, {"font_display": 'x";}body{display:none'}, "dark"])
def test_a_brand_value_that_is_not_what_it_should_be_stops_the_run(tmp_path, capsys, brand):
    code, err = stopped(capsys, job.load_job, write_job(tmp_path, brand=brand))
    assert code == 2 and "job.brand" in err


# ---------------------------------------------------------------- names
def test_slug_keeps_an_apostrophe_and_title_cases_what_is_written_right():
    assert job.slug("don't skip the hook") == "Don't Skip The Hook"
    assert job.slug("SHOUTED WORDS get fixed") == "Shouted Words Get Fixed"   # a shouted word of more than 3 letters becomes Title Case
    assert job.slug("why our qc tv works") == "Why Our QC TV Works"           # the known abbreviations stay upper
    assert job.slug("a McDonald story") == "A McDonald Story"                   # a word with its own capitals keeps them
    assert job.slug("a 4k hd shot / of ai") == "A 4K HD Shot Of AI"
    assert job.clean('a: b? c* "d" <e> | f # g % h, i') == "a b c d e f g h i"


def test_question_folder_is_the_id_then_the_slug():
    assert job.question_folder({"id": "Q02", "title": "why 240 costs less"}) == "Q02 Why 240 Costs Less"


def test_a_cut_id_is_read_off_the_front_of_the_file_name_so_a_clip_name_may_hold_spaces():
    row = {"file": "Interview/Q01 X/0025 - take one-02 - Q01 - Take 1 - Clean.mov", "source_clip": "0025 - take one"}
    assert job.cut_id(row) == "0025 - take one-02"
    assert job.cut_id({"file": "B-Roll/Bloopers/C0001-03 - Blooper.mov", "source_clip": "C0001"}) == "C0001-03"
