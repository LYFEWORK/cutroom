from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import common
from review import approved, gate, inventory, store
from conftest import ENGINE, SHOOT, clip_by_name, decide, ffmpeg, make_clip


@pytest.fixture()
def reviewed(shoot):
    """The four-clip shoot, ingested, with one of each decision: desk_v kept whole, desk_h kept in part, silent cut, take1 not reviewed."""
    inventory.build(say=lambda *_: None)
    decide(clip_by_name("desk_v.mp4")["id"], "keep")
    decide(clip_by_name("desk_h.mp4")["id"], "keep", [[1.0, 3.0]])
    decide(clip_by_name("silent.mp4")["id"], "cut")
    return shoot


def path_of(shoot, name):
    return str(next(p for p in shoot.rglob(name)))


# ---------------------------------------------------------------- approved

def test_list_keep_is_what_an_edit_may_use(reviewed):
    rows = approved.listing()
    assert {r["path"].split("/")[-1] for r in rows} == {"desk_v.mp4", "desk_h.mp4"}
    v = next(r for r in rows if r["path"].endswith("desk_v.mp4"))
    h = next(r for r in rows if r["path"].endswith("desk_h.mp4"))
    assert v["whole"] is True and v["ranges"] == [[0.0, v["dur"]]]
    assert h["whole"] is False and h["ranges"] == [[1.0, 3.0]]
    assert os.path.isabs(v["path"]) and os.path.exists(v["path"])
    assert {"id", "shoot", "kind", "group", "dur", "vertical", "desc", "status", "paths"} <= set(v)
    assert (v["vertical"], h["vertical"], v["kind"], v["group"], v["shoot"]) == (True, False, "broll", "Desk", SHOOT)


def test_list_filters_and_statuses(reviewed):
    assert [r["path"].split("/")[-1] for r in approved.listing(status="cut")] == ["silent.mp4"]
    assert [r["path"].split("/")[-1] for r in approved.listing(status="todo")] == ["take1.mp4"]
    assert [r["path"].split("/")[-1] for r in approved.listing(kind="take", status="todo")] == ["take1.mp4"]
    assert approved.listing(kind="take") == []                                        # nothing kept in takes
    assert len(approved.listing(shoot=SHOOT.lower())) == 2 and approved.listing(shoot="Another Shoot") == []


def test_a_draft_trim_is_not_a_decision(reviewed):
    decide(clip_by_name("take1.mp4")["id"], "none", [[1.0, 2.0]])
    assert [r["path"].split("/")[-1] for r in approved.listing(status="todo")] == ["take1.mp4"]
    assert approved.check(path_of(reviewed, "take1.mp4"))[0] == 1


def test_a_clip_whose_file_is_gone_is_not_listed(reviewed):
    Path(path_of(reviewed, "desk_v.mp4")).unlink()
    assert [r["path"].split("/")[-1] for r in approved.listing()] == ["desk_h.mp4"]


def test_list_output_text_and_json(reviewed, capsys):
    assert approved.main(["list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert any(ln.endswith("desk_v.mp4") for ln in out) and any(ln.endswith("desk_h.mp4  keep 1.00-3.00s") for ln in out)
    assert approved.main(["list", "--json", "--status", "cut"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["status"] == "cut" and rows[0]["ranges"] is None


def test_check_exit_codes(reviewed, tmp_path, capsys):
    assert approved.check(path_of(reviewed, "desk_v.mp4"))[0] == 0
    code, res = approved.check(path_of(reviewed, "desk_h.mp4"))
    assert code == 0 and res["ranges"] == [[1.0, 3.0]] and res["whole"] is False
    assert approved.check(path_of(reviewed, "silent.mp4"))[0] == 1
    assert approved.check(path_of(reviewed, "take1.mp4"))[0] == 1                      # never reviewed is off limits too
    assert approved.check(str(tmp_path / "nothing.mp4")) == (2, {"status": "unknown", "path": str(tmp_path / "nothing.mp4"), "reason": "not on disk"})
    for name, want, text in (("desk_v.mp4", 0, "whole clip"), ("desk_h.mp4", 0, "only 1.00-3.00s"), ("silent.mp4", 1, "CUT (off limits)"), ("take1.mp4", 1, "NOT REVIEWED YET")):
        assert approved.main(["check", path_of(reviewed, name)]) == want
        assert text in capsys.readouterr().out


def test_check_a_file_the_review_never_saw(reviewed, tmp_path, capsys):
    stranger = make_clip(tmp_path / "stranger.mp4", "216x384", 3, freq=1234)
    assert approved.main(["check", str(stranger)]) == 2
    assert "NOT IN THE REVIEW LIST (off limits)" in capsys.readouterr().out


def test_a_copy_resolves_by_its_content(reviewed, tmp_path):
    for name, code in (("desk_v.mp4", 0), ("silent.mp4", 1)):
        elsewhere = tmp_path / "somewhere else" / "renamed copy.mp4"
        elsewhere.parent.mkdir(exist_ok=True)
        shutil.copy(path_of(reviewed, name), elsewhere)
        got, res = approved.check(str(elsewhere))
        assert got == code and res["how"] == "content" and res["id"] == clip_by_name(name)["id"]
        elsewhere.unlink()


def test_a_file_changed_since_it_was_reviewed_is_unknown(reviewed):
    target = path_of(reviewed, "desk_v.mp4")
    make_clip(Path(target), "216x384", 3, freq=2222)                                  # re-exported over the same path
    code, res = approved.check(target)
    assert code == 2 and "changed since the last ingest" in res["reason"]


def test_a_relative_path_is_taken_from_where_you_stand(reviewed, monkeypatch):
    monkeypatch.chdir(reviewed / "B-Roll" / "Desk")
    assert approved.check("desk_v.mp4")[0] == 0


def test_status_counts(reviewed, capsys):
    res = approved.status_counts()
    assert res["shoots"][SHOOT] == {"todo": 1, "keep": 2, "cut": 1, "total": 4} and res["all"]["todo"] == 1
    assert approved.main(["status"]) == 0
    out = capsys.readouterr().out
    assert f"{SHOOT}: 1 to review of 4 (2 kept, 1 cut)" in out and "All shoots: 1 clip to review" in out
    approved.main(["status", "--json"])
    assert json.loads(capsys.readouterr().out)["all"]["total"] == 4


def test_status_when_there_is_nothing(ws, capsys):
    inventory.build(say=lambda *_: None)
    approved.main(["status"])
    assert "No clips yet" in capsys.readouterr().out


def test_the_command_line_wiring_and_exit_codes(reviewed):
    env = {**os.environ}
    script = str(ENGINE / "review" / "approved.py")
    run = lambda *a: subprocess.run([sys.executable, script, *a], capture_output=True, text=True, env=env)     # noqa: E731
    assert run("check", path_of(reviewed, "desk_v.mp4")).returncode == 0
    assert run("check", path_of(reviewed, "silent.mp4")).returncode == 1
    assert run("check", "/definitely/not/there.mp4").returncode == 2
    r = run("list", "--json")
    assert r.returncode == 0 and len(json.loads(r.stdout)) == 2


# ---------------------------------------------------------------- gate

def write_plan(tmp_path, obj, name="plan.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return p


def problems(plan, **kw):
    return {(p["status"], os.path.basename(p["ref"])) for p in gate.run_gate(plan, **kw)["problems"]}


def test_a_plan_naming_a_cut_clip_fails(reviewed, tmp_path):
    plan = write_plan(tmp_path, {"scenes": [{"src": path_of(reviewed, "silent.mp4")}]})
    assert problems(plan) == {("CUT", "silent.mp4")}
    assert gate.main([str(plan)]) == 1


def test_a_kept_clip_inside_its_kept_part_passes(reviewed, tmp_path, capsys):
    plan = write_plan(tmp_path, {"scenes": [{"src": path_of(reviewed, "desk_h.mp4"), "in": 1.2, "out": 2.8},
                                            {"src": path_of(reviewed, "desk_v.mp4")}, {"src": path_of(reviewed, "desk_v.mp4"), "in": 0, "out": 5.9}]})
    assert problems(plan) == set()
    assert gate.main([str(plan)]) == 0
    assert "gate: clean (3 references to 2 clips" in capsys.readouterr().out


@pytest.mark.parametrize("t_in,t_out", [(0.0, 2.0), (2.0, 4.0), (0.5, 3.5), (1.0, 3.5), (3.5, 4.5), (4.0, 5.0)])
def test_a_kept_clip_used_outside_its_kept_part_fails(reviewed, tmp_path, t_in, t_out):
    plan = write_plan(tmp_path, {"clip": {"file": path_of(reviewed, "desk_h.mp4"), "in": t_in, "out": t_out}})
    assert problems(plan) == {("OUTSIDE", "desk_h.mp4")}


def test_the_edges_of_a_kept_part_pass_within_a_hair(reviewed, tmp_path):
    ok = write_plan(tmp_path, {"clip": {"file": path_of(reviewed, "desk_h.mp4"), "in": 1.0, "out": 3.0}})
    assert problems(ok) == set()
    ok = write_plan(tmp_path, {"clip": {"file": path_of(reviewed, "desk_h.mp4"), "in": 0.95, "out": 3.05}}, "edge.json")
    assert problems(ok) == set()


def test_use_that_spans_a_gap_between_two_kept_parts_fails(reviewed, tmp_path):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[0.5, 1.5], [3.0, 4.0]])
    inside = write_plan(tmp_path, {"c": [{"src": path_of(reviewed, "desk_v.mp4"), "in": 0.6, "out": 1.4}, {"src": path_of(reviewed, "desk_v.mp4"), "in": 3.2, "out": 3.9}]})
    assert problems(inside) == set()
    across = write_plan(tmp_path, {"c": {"src": path_of(reviewed, "desk_v.mp4"), "in": 1.0, "out": 3.5}}, "across.json")
    assert problems(across) == {("OUTSIDE", "desk_v.mp4")}


def test_a_trimmed_clip_named_without_times_would_use_all_of_it(reviewed, tmp_path):
    plan = write_plan(tmp_path, {"layers": [path_of(reviewed, "desk_h.mp4")]})
    res = gate.run_gate(plan)["problems"]
    assert [(p["status"], "1.00-3.00s" in p["detail"]) for p in res] == [("NO TIMES", True)]


def test_a_time_past_the_end_of_the_clip_fails(reviewed, tmp_path):
    plan = write_plan(tmp_path, {"c": {"src": path_of(reviewed, "desk_v.mp4"), "in": 2, "out": 99}})
    assert problems(plan) == {("OUTSIDE", "desk_v.mp4")}
    backwards = write_plan(tmp_path, {"c": {"src": path_of(reviewed, "desk_v.mp4"), "in": 3, "out": 2}}, "b.json")
    assert problems(backwards) == {("OUTSIDE", "desk_v.mp4")}


def test_an_unreviewed_clip_fails(reviewed, tmp_path):
    assert problems(write_plan(tmp_path, {"x": path_of(reviewed, "take1.mp4")})) == {("UNREVIEWED", "take1.mp4")}


def test_a_file_in_the_footage_folder_that_was_never_ingested_is_unreviewed(reviewed, tmp_path):
    new = make_clip(reviewed / "B-Roll" / "Desk" / "brand_new.mp4", "216x384", 3, freq=3131)
    assert problems(write_plan(tmp_path, {"x": str(new)})) == {("UNREVIEWED", "brand_new.mp4")}


def test_a_file_outside_every_review_folder(reviewed, tmp_path):
    sting = make_clip(tmp_path / "logo sting.mp4", "216x384", 2, freq=4141)
    plan = write_plan(tmp_path, {"x": str(sting)})
    assert problems(plan) == {("UNKNOWN", "logo sting.mp4")}
    assert problems(plan, allow_outside=True) == set()
    assert gate.main([str(plan), "--allow-outside"]) == 0
    missing = write_plan(tmp_path, {"x": str(tmp_path / "no such file.mp4")}, "missing.json")
    assert problems(missing, allow_outside=True) == {("UNKNOWN", "no such file.mp4")}          # cannot be judged, so never let through


def test_a_bare_file_name_resolves_through_the_review_list(reviewed, tmp_path):
    plan = write_plan(tmp_path, {"a": "silent.mp4", "b": "Desk/desk_v.mp4", "c": "B-Roll/Desk/desk_h.mp4"})
    assert problems(plan) == {("CUT", "silent.mp4"), ("NO TIMES", "desk_h.mp4")}


def test_an_ambiguous_name_is_refused(reviewed, tmp_path):
    (reviewed / "Interview" / "Other Line").mkdir()
    make_clip(reviewed / "Interview" / "Other Line" / "silent.mp4", "216x384", 3, freq=5151)       # a different clip with the same name
    inventory.build(say=lambda *_: None)
    res = gate.run_gate(write_plan(tmp_path, {"a": "silent.mp4"}))["problems"]
    assert [(p["status"], "2 clips" in p["detail"]) for p in res] == [("AMBIGUOUS", True)]
    assert problems(write_plan(tmp_path, {"a": "Other Line/silent.mp4"}, "full.json")) == {("UNREVIEWED", "silent.mp4")}     # a longer path is not ambiguous


def test_relative_paths_file_urls_and_queries_are_followed(reviewed, tmp_path):
    proj = tmp_path / "Projects" / "demo"
    proj.mkdir(parents=True)
    shutil.copy(path_of(reviewed, "silent.mp4"), proj / "footage.mp4")                             # a copy placed in a project
    plan = write_plan(proj, {"a": "footage.mp4", "b": f"file://{path_of(reviewed, 'silent.mp4')}", "c": "footage.mp4?t=3", "d": "https://example.com/x.mp4"}, "edit.json")
    res = gate.run_gate(plan)["problems"]
    assert len(res) == 3 and {p["status"] for p in res} == {"CUT"}                                  # the remote link is not a local file: ignored


def test_a_copy_in_a_project_folder_is_judged_by_its_content(reviewed, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    shutil.copy(path_of(reviewed, "desk_v.mp4"), proj / "copy of v.mp4")
    assert problems(write_plan(proj, {"clip": {"src": "copy of v.mp4"}})) == set()


def test_time_keys_and_where_they_are_read_from(reviewed, tmp_path):
    h = path_of(reviewed, "desk_h.mp4")
    assert problems(write_plan(tmp_path, {"c": {"src": h, "source_in": 1.1, "source_out": 2.9}})) == set()
    assert problems(write_plan(tmp_path, {"c": {"src": h, "source_in": 0.0, "source_out": 2.9}}, "b.json")) == {("OUTSIDE", "desk_h.mp4")}
    assert problems(write_plan(tmp_path, {"c": {"src": h, "in": "1.1", "out": "2.9"}}, "c.json")) == {("NO TIMES", "desk_h.mp4")}      # text is not a time
    assert problems(write_plan(tmp_path, {"in": 1.1, "out": 2.9, "layers": [{"src": h}]}, "d.json")) == {("NO TIMES", "desk_h.mp4")}   # times belong next to the path
    assert problems(write_plan(tmp_path, {"c": {"src": h, "in": 1.5}}, "e.json")) == {("OUTSIDE", "desk_h.mp4")}                        # from 1.5 to the end


def test_a_renderer_insert_is_read_by_its_from_s_and_its_span_on_the_edit(reviewed, tmp_path):
    """engine/render.py's insert shape: the clip plays from from_s for (end_s - start_s) seconds of the edit, at rate. The gate reads it without
    extra keys, so an edit plan is checked as written."""
    h = path_of(reviewed, "desk_h.mp4")
    assert problems(write_plan(tmp_path, {"inserts": [{"src": h, "from_s": 1.1, "start_s": 4.0, "end_s": 5.8}]})) == set()
    assert problems(write_plan(tmp_path, {"inserts": [{"src": h, "from_s": 0.0, "start_s": 4.0, "end_s": 5.8}]}, "b.json")) == {("OUTSIDE", "desk_h.mp4")}
    assert problems(write_plan(tmp_path, {"inserts": [{"src": h, "from_s": 1.1, "start_s": 4.0, "end_s": 4.9, "rate": 2.0}]}, "c.json")) == set()   # 0.9 s of the edit at 2x is 1.8 s of the clip
    assert problems(write_plan(tmp_path, {"inserts": [{"src": h, "from_s": 1.1, "start_s": 4.0, "end_s": 5.0, "rate": 3.0}]}, "d.json")) == {("OUTSIDE", "desk_h.mp4")}
    assert problems(write_plan(tmp_path, {"inserts": [{"src": h, "from_s": 1.1}]}, "e.json")) == {("OUTSIDE", "desk_h.mp4")}   # no span: from 1.1 to the end
    assert problems(write_plan(tmp_path, {"inserts": [{"src": h, "in": 1.1, "out": 2.9, "from_s": 0.0}]}, "f.json")) == set()  # explicit in/out win


def test_a_folder_is_scanned_and_housekeeping_files_are_skipped(reviewed, ws, tmp_path):
    proj = ws / "Projects" / "demo"
    (proj / "node_modules").mkdir(parents=True)
    write_plan(proj, {"a": path_of(reviewed, "silent.mp4")}, "one.json")
    write_plan(proj, {"a": path_of(reviewed, "take1.mp4")}, "two.json")
    write_plan(proj / "node_modules", {"a": path_of(reviewed, "silent.mp4")}, "skipped.json")
    res = gate.run_gate(proj)
    assert res["files"] == 2 and {p["status"] for p in res["problems"]} == {"CUT", "UNREVIEWED"}
    whole = gate.run_gate(ws)                                                                       # the workspace: Review/'s own json is never read as a plan
    assert not any("inventory.json" in p["file"] or "decisions.json" in p["file"] for p in whole["problems"])


def test_a_plan_with_no_media_says_so(reviewed, tmp_path, capsys):
    assert gate.main([str(write_plan(tmp_path, {"title": "hello", "n": 3}))]) == 0
    assert "no media references found" in capsys.readouterr().out


def test_gate_output_and_json(reviewed, tmp_path, capsys):
    plan = write_plan(tmp_path, {"a": path_of(reviewed, "silent.mp4"), "b": {"src": path_of(reviewed, "desk_h.mp4"), "in": 0, "out": 1}})
    assert gate.main([str(plan)]) == 1
    out = capsys.readouterr().out
    assert "CUT" in out and "OUTSIDE" in out and "gate: 2 problems" in out
    assert gate.main([str(plan), "--json"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 2


def test_gate_stops_on_a_missing_or_broken_plan(reviewed, tmp_path):
    with pytest.raises(SystemExit) as e:
        gate.main([str(tmp_path / "nope.json")])
    assert e.value.code == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    with pytest.raises(SystemExit) as e:
        gate.main([str(bad)])
    assert e.value.code == 2


def test_gate_command_line_exit_codes(reviewed, tmp_path):
    script = str(ENGINE / "review" / "gate.py")
    good = write_plan(tmp_path, {"a": path_of(reviewed, "desk_v.mp4")})
    badp = write_plan(tmp_path, {"a": path_of(reviewed, "silent.mp4")}, "bad.json")
    assert subprocess.run([sys.executable, script, str(good)], capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, script, str(badp)], capture_output=True).returncode == 1
