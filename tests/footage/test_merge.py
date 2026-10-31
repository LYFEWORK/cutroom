"""merge.py builds a final record from the verifiers' corrections when no judge's final landed, and leaves everything else alone."""
import json
import os

import pytest

import job as jobmod
import merge
import organize


def make(tmp_path, **files):
    shoot = tmp_path / "shoot"
    (shoot / ".work" / "results").mkdir(parents=True)
    (shoot / "job.json").write_text(json.dumps({"name": "T", "shoot_type": "interview", "subjects": ["Wax Station", "Shelves"]}))
    for name, body in files.items():
        (shoot / ".work" / "results" / name).write_text(json.dumps(body))
    return shoot


RECORD = {"clip": "C0004", "category": "broll_with_sound", "reel": "NONE", "reel_title": "", "name": "Dryer", "subject_folder": "Wax Station", "shot": "wide", "camera": "static",
          "flags": [], "usable": True, "confidence": 0.8, "notes": "first pass"}


def run(shoot):
    merge.main(["--job", str(shoot / "job.json")])
    return shoot / ".work" / "results"


def final(res, clip="C0004"):
    return json.loads((res / f"{clip}.final.json").read_text())


def test_the_visual_corrections_go_on_first_then_the_content_ones(tmp_path):
    shoot = make(tmp_path, **{"C0004.classify.json": RECORD,
                              "C0004.visual.json": {"agree": False, "issues": ["x"], "corrections": {"shot": "close_up", "name": "Visual Name"}},
                              "C0004.content.json": {"agree": False, "issues": ["y"], "corrections": {"name": "Content Name", "flags": ["shaky"]}}})
    f = final(run(shoot))
    assert f["name"] == "Content Name" and f["shot"] == "close_up" and f["flags"] == ["shaky"]                # content lens owns what the transcript decides
    assert f["notes"].endswith("FINAL BY MERGE (no judge record): applied visual:shot, visual:name, content:name, content:flags")


def test_a_correction_that_is_not_allowed_is_ignored_and_a_folder_off_the_list_becomes_other(tmp_path):
    shoot = make(tmp_path, **{"C0004.classify.json": RECORD,
                              "C0004.visual.json": {"agree": False, "issues": [], "corrections": {"subject_folder": "Dryer Room", "shot": "close_up", "camera": "zoom", "category": "banana", "usable": "yes",
                                                                                              "flags": "oops", "clip": "C9999", "nonsense": 1}},
                              "C0004.content.json": {"agree": True, "issues": [], "corrections": {}}})
    f = final(run(shoot))
    assert f["subject_folder"] == "Other" and f["shot"] == "close_up"        # the folder falls back to Other, the legal correction is kept
    assert f["camera"] == "static" and f["category"] == "broll_with_sound" and f["usable"] is True and f["flags"] == [] and f["clip"] == "C0004" and "nonsense" not in f


def test_a_folder_on_the_list_is_kept_and_a_legal_category_is_taken(tmp_path):
    shoot = make(tmp_path, **{"C0004.classify.json": RECORD,
                              "C0004.visual.json": {"agree": False, "corrections": {"subject_folder": "Shelves", "category": "off_topic"}},
                              "C0004.content.json": {"agree": True}})
    f = final(run(shoot))
    assert f["subject_folder"] == "Shelves" and f["category"] == "off_topic"


def test_an_agreed_record_or_a_final_that_is_already_there_is_left_alone(tmp_path):
    shoot = make(tmp_path, **{"C0001.classify.json": {**RECORD, "clip": "C0001"}, "C0001.visual.json": {"agree": True}, "C0001.content.json": {"agree": True},
                              "C0002.classify.json": {**RECORD, "clip": "C0002"}, "C0002.visual.json": {"agree": False, "corrections": {"name": "X"}},
                              "C0002.final.json": {**RECORD, "clip": "C0002", "name": "The Judge's Version"},
                              "C0003.classify.json": {**RECORD, "clip": "C0003"}})                     # one pass, no verdicts at all
    res = run(shoot)
    assert not (res / "C0001.final.json").exists() and not (res / "C0003.final.json").exists()
    assert final(res, "C0002")["name"] == "The Judge's Version"


def test_one_verdict_missing_counts_as_agreeing(tmp_path):
    shoot = make(tmp_path, **{"C0004.classify.json": RECORD, "C0004.content.json": {"agree": False, "corrections": {"name": "Only Content"}}})
    assert final(run(shoot))["name"] == "Only Content"


def test_a_file_that_is_not_json_stops_with_one_plain_line(tmp_path, capsys):
    shoot = make(tmp_path, **{"C0004.visual.json": {"agree": False, "corrections": {"name": "X"}}})
    (shoot / ".work" / "results" / "C0004.classify.json").write_text("{not json")
    with pytest.raises(SystemExit) as e:
        merge.main(["--job", str(shoot / "job.json")])
    assert e.value.code == 2 and "C0004.classify.json is not valid JSON" in capsys.readouterr().err


def test_the_merged_record_is_what_organize_reads_and_counts_as_verified(tmp_path):
    shoot = make(tmp_path, **{"C0004.classify.json": RECORD, "C0004.visual.json": {"agree": False, "corrections": {"name": "Better"}}, "C0004.content.json": {"agree": True}})
    (shoot / ".work" / "probe.tsv").write_text("C0004\t3.0\t270\t480\t15\t.MP4\t0\n")
    (shoot / ".work" / "evidence.json").write_text(json.dumps([{"clip": "C0004", "digitally_silent": False, "mean_db": -30, "max_db": -10, "transcript": "", "ext": ".MP4"}]))
    run(shoot)
    r = organize.load_records(jobmod.load_job(shoot / "job.json"))[0]
    assert r["verified"] and r["source"] == "C0004.final.json" and r["name"] == "Better"
