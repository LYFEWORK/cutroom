"""workflow_args.py: the cheat sheet the classifying agents read, and the args object classify.js takes."""
import json

import pytest

import job as jobmod
import workflow_args


def a_job(tmp_path, **over):
    j = {"name": "Candle Workshop", "shoot_type": "interview", "shoot_date": "2026-10-31", "camera_label": "phone, 1080p", "context": "A candle workshop.",
         "questions": [{"id": "Q01", "title": "Why Three Days", "question": "Why does it take three days?", "followups": "overnight", "hints": "cure"},
                       {"id": "Q02", "title": "Welcome", "question": "Say hello."}],
         "subjects": ["Wax Station", "Other"]}
    j.update(over)
    shoot = tmp_path / "shoot"
    (shoot / ".work").mkdir(parents=True, exist_ok=True)
    (shoot / ".work" / "probe.tsv").write_text("C0001\t3.0\t270\t480\t15\t.MP4\t0\nC0002 two\t4.0\t270\t480\t15\t.MP4\t0\n")
    (shoot / "job.json").write_text(json.dumps(j))
    return jobmod.load_job(shoot / "job.json"), shoot


def test_the_cheat_sheet_lists_every_question_with_its_id_followups_and_hints(tmp_path):
    j, _ = a_job(tmp_path, people={"interviewer": "Sam", "subject": "Dana, the owner", "extras": "A dog wanders through."}, shot_list="wax, jars", notes="Q02 was filmed first.")
    t = workflow_args.cheat_sheet_text(j)
    lines = t.splitlines()
    assert lines[0] == "CANDLE WORKSHOP: QUESTION LIST FOR THIS SHOOT. Off-camera interviewer: Sam. On-camera subject: Dana, the owner. A dog wanders through."
    assert "Q01  Why Three Days: Why does it take three days? Follow-ups: overnight (cure)" in lines and "Q02  Welcome: Say hello." in lines
    assert any(l.startswith("ANYTHING ELSE answered on camera that is not one of the listed questions = BONUS") for l in lines)
    assert "SHOT LIST / B-ROLL VOCAB: wax, jars" in lines and "NOTES: Q02 was filmed first." in lines
    assert "\u2014" not in t and "\u2013" not in t                                    # no em or en dash in anything it writes


def test_people_may_be_one_sentence(tmp_path):
    j, _ = a_job(tmp_path, people="Dana on camera, Sam asking.")
    assert workflow_args.cheat_sheet_text(j).splitlines()[0].endswith("Dana on camera, Sam asking.")
    assert workflow_args.people_text({"people": ""}) == "" and workflow_args.people_text({}) == ""


def test_an_ad_shoots_questions_are_script_lines(tmp_path):
    j, _ = a_job(tmp_path, shoot_type="ad")
    t = workflow_args.cheat_sheet_text(j)
    assert "SCRIPT LINES FOR THIS SHOOT. The person on camera reads each line, several attempts each." in t.splitlines()[0]
    assert "not one of the listed lines = BONUS" in t


def test_with_no_question_list_every_answer_is_a_bonus_with_a_topic(tmp_path):
    j, _ = a_job(tmp_path, questions=[])
    t = workflow_args.cheat_sheet_text(j)
    assert "No question list was supplied. Every on-camera answer gets reel=BONUS" in t and "Q01" not in t


def test_the_args_carry_every_clip_the_subjects_and_the_context(tmp_path):
    j, shoot = a_job(tmp_path, people="Dana on camera.")
    a = workflow_args.build_args(j)
    assert a == {"scratch": str(shoot / ".work"), "clips": ["C0001", "C0002 two"], "subjects": ["Wax Station", "Other"], "has_questions": True,
                 "context": "Raw footage from a shoot called Candle Workshop on 2026-10-31 (phone, 1080p). A candle workshop. Dana on camera."}
    j2, _ = a_job(tmp_path / "b", questions=[])
    assert workflow_args.build_args(j2)["has_questions"] is False


def test_the_command_writes_both_files_and_prints_the_args(tmp_path, capsys):
    j, shoot = a_job(tmp_path)
    args = workflow_args.main(["--job", str(shoot / "job.json")])
    assert json.loads(capsys.readouterr().out) == args == json.loads((shoot / ".work" / "workflow-args.json").read_text())
    assert (shoot / ".work" / "cheat-sheet.txt").read_text() == workflow_args.cheat_sheet_text(j)


def test_it_refuses_empty_subjects_with_one_line(tmp_path, capsys):
    j, shoot = a_job(tmp_path, subjects=[])
    with pytest.raises(SystemExit) as e:
        workflow_args.main(["--job", str(shoot / "job.json")])
    assert e.value.code == 2 and capsys.readouterr().err.startswith("STOP: job.subjects is empty")
