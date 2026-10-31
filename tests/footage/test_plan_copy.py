"""The organizer keeps plan.json in <ROOT>/Edit once the shoot is applied, so qc.py passes after the scratch folder is gone."""
import json
import os

import job


def a_job(tmp_path):
    (tmp_path / "scratch").mkdir()
    (tmp_path / "shoot").mkdir()
    return {"root": str(tmp_path / "shoot"), "scratch": str(tmp_path / "scratch")}


def test_only_an_applied_plan_is_copied_into_edit(tmp_path):
    j = a_job(tmp_path)
    job.write_plan(j, {"moves": [], "applied": False})
    assert (tmp_path / "scratch" / "plan.json").exists() and not (tmp_path / "shoot" / "Edit" / "plan.json").exists()
    job.write_plan(j, {"moves": [{"rel": "a"}], "applied": True, "pre_move_bytes": 5})
    assert json.loads((tmp_path / "shoot" / "Edit" / "plan.json").read_text()) == json.loads((tmp_path / "scratch" / "plan.json").read_text())


def test_read_plan_prefers_scratch_then_edit_then_none(tmp_path):
    j = a_job(tmp_path)
    assert job.read_plan(j) is None
    job.write_plan(j, {"moves": [], "applied": True, "n": 1})
    (tmp_path / "scratch" / "plan.json").write_text(json.dumps({"moves": [], "applied": True, "n": 2}))
    assert job.read_plan(j)["n"] == 2
    os.remove(tmp_path / "scratch" / "plan.json")
    assert job.read_plan(j)["n"] == 1


def test_a_plan_is_written_whole_or_not_at_all(tmp_path, monkeypatch):
    """the plan is what reverse recovers from: a write that dies half way must leave the plan that was there"""
    j = a_job(tmp_path)
    job.write_plan(j, {"moves": [], "applied": False, "n": 1})

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    try:
        job.write_plan(j, {"moves": [], "applied": False, "n": 2})
    except OSError:
        pass
    monkeypatch.undo()
    assert json.loads((tmp_path / "scratch" / "plan.json").read_text())["n"] == 1
