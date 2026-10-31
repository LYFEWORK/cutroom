"""organize.py: the plan, the move, the rollback, the copy from another volume, and the reverse that puts every camera file back byte for byte."""
import csv
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import common
import job as jobmod
import organize
from conftest import HOME, ok, run_tool, sha_tree, mini_shoot, intake, need_media


def rec(clip, **kw):
    base = dict(clip=clip, category="broll_with_sound", reel="NONE", reel_title="", name="Some Shot", subject_folder="Other", subject_other="", flags=[], usable=True,
                silent=False, ext=".MP4", duration_s=3.0, mean_db=-30.0, max_db=-10.0, transcript="", orientation="vertical", verified=True)
    base.update(kw)
    return base


JOB = {"root": "/shoot", "name": "Test", "questions": [{"id": "Q01", "title": "Why Our Process Takes Three Days"}, {"id": "Q02", "title": "Welcome To The Workshop"}],
       "subject_overrides": {}, "subjects": ["Wax Station", "Other"]}


def rel_of(recs, job=JOB):
    return {m["clip"]: m["rel"] for m in organize.plan(job, recs)}


# ---------------------------------------------------------------- what the plan names
def test_an_answer_is_filed_by_its_question_with_a_take_number_in_shoot_order():
    r = rel_of([rec("C0001", category="interview", reel="Q01", name="Why Three Days First"), rec("C0002", category="interview", reel="Q01", name="why three days"),
                rec("C0003", category="interview", reel="Q02", name="welcome intro", ext=".mov")])     # load_records hands them over in clip order
    assert r["C0001"] == "Interview/Q01 Why Our Process Takes Three Days/C0001 - Q01 - Why Three Days First - Take 1.MP4"
    assert r["C0002"] == "Interview/Q01 Why Our Process Takes Three Days/C0002 - Q01 - Why Three Days - Take 2.MP4"
    assert r["C0003"] == "Interview/Q02 Welcome To The Workshop/C0003 - Q02 - Welcome Intro - Take 1.mov"


def test_an_answer_to_no_listed_question_is_a_bonus_answer():
    assert rel_of([rec("C0001", category="interview", reel="BONUS", name="candle history")])["C0001"] == "Interview/Bonus Answers/C0001 - Bonus - Candle History.MP4"
    assert rel_of([rec("C0001", category="interview", reel="Q99", name="x")])["C0001"] == "Interview/Bonus Answers/C0001 - Bonus - X.MP4"


def test_with_no_question_list_answers_are_grouped_by_the_topic_the_classifier_named():
    j = {**JOB, "questions": []}
    r = rel_of([rec("C0001", category="interview", reel="BONUS", reel_title="candle history", name="first story"),
                rec("C0002", category="interview", reel="BONUS", reel_title="Candle History", name="second story")], j)
    assert r["C0001"] == "Interview/Candle History/C0001 - First Story.MP4" and r["C0002"] == "Interview/Candle History/C0002 - Second Story.MP4"


def test_b_roll_goes_by_what_is_on_screen_and_says_when_it_has_no_sound_or_is_a_chat():
    r = rel_of([rec("C0001", subject_folder="Wax Station", name="pouring wax"), rec("C0002", subject_folder="Wax Station", name="empty shelf", silent=True),
                rec("C0003", category="candid_talk", subject_folder="Wax Station", name="chat"), rec("C0004", subject_folder="Other", subject_other="the back yard", name="fence")])
    assert r["C0001"] == "B-Roll/Wax Station/C0001 - Pouring Wax.MP4"
    assert r["C0002"] == "B-Roll/Wax Station/C0002 - Empty Shelf - No Audio.MP4"
    assert r["C0003"] == "B-Roll/Wax Station/C0003 - Chat - Candid Talk.MP4"
    assert r["C0004"] == "B-Roll/The Back Yard/C0004 - Fence.MP4"                       # a folder that is not on the list files under its own name


def test_a_subject_override_beats_the_record():
    j = {**JOB, "subject_overrides": {"C0001": "Hands Close Up"}}
    assert rel_of([rec("C0001", subject_folder="Wax Station", name="pour")], j)["C0001"] == "B-Roll/Hands Close Up/C0001 - Pour.MP4"


def test_off_topic_unusable_and_unclassified_clips_have_their_own_folders():
    r = rel_of([rec("C0001", category="off_topic", name="someone else's shoot"), rec("C0002", category="unusable", name="lens cap"),
                rec("C0003", usable=False, name=""), rec("C0004", category="MISSING", reel="NONE", name="")])
    assert r["C0001"] == "Off Topic/C0001 - Someone Else's Shoot.MP4"
    assert r["C0002"] == "Unusable/C0002 - Lens Cap.MP4"
    assert r["C0003"] == "Unusable/C0003 - Unusable.MP4"
    assert r["C0004"] == "Unusable/C0004 - UNCLASSIFIED.MP4"


def test_a_name_is_cleaned_of_what_a_path_or_a_link_cannot_hold():
    r = rel_of([rec("C0001", name='a/b: c? "d" #5 100% | x, y', subject_folder="Wax/Station")])["C0001"]
    assert "/" not in r.split("/", 2)[2] and not any(ch in r.split("/", 2)[2] for ch in '\\:*?"<>|#%,')
    assert r.startswith("B-Roll/Wax Station/")


def test_a_long_name_gives_way_but_never_the_take_number_or_the_extension():
    name = "word " * 60
    rel = rel_of([rec("C0001", category="interview", reel="Q01", name=name)])["C0001"]
    leaf = rel.split("/")[-1]
    assert len(leaf) <= organize.NAME_CAP and leaf.startswith("C0001 - Q01 - Word") and leaf.endswith(" - Take 1.MP4")
    broll = rel_of([rec("C0002", name=name, silent=True)])["C0002"].split("/")[-1]
    assert len(broll) <= organize.NAME_CAP and broll.endswith(" - No Audio.MP4")


def test_two_clips_that_would_land_on_one_path_stop_the_plan(capsys):
    with pytest.raises(SystemExit):
        organize.plan(JOB, [rec("C0001"), rec("C0001")])
    assert "COLLISION" in capsys.readouterr().err


# ---------------------------------------------------------------- the files on disk
def test_a_sidecar_belongs_to_the_clip_whose_name_it_starts_with_and_the_longest_owner_wins(tmp_path):
    for n in ("C0001.MP4", "C00010.MP4", "C0001M01.XML", "C0001.LRF", "C00010M01.XML", "C0001-2.THM", "notes.txt", ".hidden", ".DS_Store"):
        (tmp_path / n).write_bytes(b"x")
    s = organize.list_sources(str(tmp_path))
    assert sorted(s) == ["C0001", "C00010"]
    assert sorted(os.path.basename(p) for p in s["C0001"]["sidecars"]) == ["C0001-2.THM", "C0001.LRF", "C0001M01.XML"]
    assert [os.path.basename(p) for p in s["C00010"]["sidecars"]] == ["C00010M01.XML"]     # C0001 never claims C00010.XML


def test_a_clip_name_with_spaces_owns_its_sidecars(tmp_path):
    for n in ("0025 - take one.mp4", "0025 - take one.xml", "0025 - take two.mp4"):
        (tmp_path / n).write_bytes(b"x")
    s = organize.list_sources(str(tmp_path))
    assert [os.path.basename(p) for p in s["0025 - take one"]["sidecars"]] == ["0025 - take one.xml"] and s["0025 - take two"]["sidecars"] == []


def test_two_files_with_one_name_stop_the_run(tmp_path, capsys):
    (tmp_path / "C0001.MP4").write_bytes(b"x")
    (tmp_path / "C0001.MOV").write_bytes(b"y")
    with pytest.raises(SystemExit):
        organize.list_sources(str(tmp_path))
    assert "DUPLICATE NAME C0001" in capsys.readouterr().err


# ---------------------------------------------------------------- the checked copy
def test_a_copy_is_checked_against_the_original_and_the_original_is_never_touched(tmp_path):
    src = tmp_path / "card.mp4"
    src.write_bytes(os.urandom(3 << 20))
    os.utime(src, (1_700_000_000, 1_700_000_000))
    dst = tmp_path / "shoot" / "copy.mp4"
    dst.parent.mkdir()
    before = src.read_bytes()
    organize.verified_copy(str(src), str(dst))
    assert dst.read_bytes() == before and src.read_bytes() == before
    assert int(dst.stat().st_mtime) == 1_700_000_000                                   # a camera file keeps the time it was recorded
    assert not os.path.exists(str(dst) + ".part")


@pytest.mark.parametrize("damage", ["truncate", "head", "tail"])
def test_a_copy_that_does_not_match_is_deleted_and_says_so(tmp_path, monkeypatch, damage):
    src = tmp_path / "card.mp4"
    data = bytearray(os.urandom(3 << 20))
    src.write_bytes(bytes(data))
    real = shutil.copyfile

    def bad_copy(a, b, **kw):
        real(a, b)
        d = bytearray(open(b, "rb").read())
        if damage == "truncate":
            d = d[:-5]
        elif damage == "head":
            d[100] ^= 0xFF
        else:
            d[-100] ^= 0xFF
        open(b, "wb").write(bytes(d))
    monkeypatch.setattr(shutil, "copyfile", bad_copy)
    dst = tmp_path / "copy.mp4"
    with pytest.raises(organize.CopyFailed, match="does not match the original"):
        organize.verified_copy(str(src), str(dst))
    assert not dst.exists() and not os.path.exists(str(dst) + ".part") and src.read_bytes() == bytes(data)


def test_a_copy_never_overwrites_a_file_that_is_already_there(tmp_path):
    src, dst = tmp_path / "card.mp4", tmp_path / "shoot.mp4"
    src.write_bytes(b"from the card")
    dst.write_bytes(b"already here")
    with pytest.raises(FileExistsError, match="a copy never overwrites"):
        organize.verified_copy(str(src), str(dst))
    assert dst.read_bytes() == b"already here" and src.read_bytes() == b"from the card" and not (tmp_path / "shoot.mp4.part").exists()


def test_a_small_file_is_checked_whole(tmp_path, monkeypatch):
    src = tmp_path / "s.xml"
    src.write_bytes(b"a" * 1000)
    real = shutil.copyfile
    monkeypatch.setattr(shutil, "copyfile", lambda a, b, **kw: (real(a, b), open(b, "r+b").write(b"b" + b"a" * 998 + b"c"))[0])
    with pytest.raises(organize.CopyFailed):
        organize.verified_copy(str(src), str(tmp_path / "d.xml"))


# ---------------------------------------------------------------- records
def test_a_subject_folder_is_the_jobs_fixed_list_whatever_the_case(tmp_path):
    shoot = mini_shoot(tmp_path, {"C0001.MP4": ("vertical", "noise")}, subjects=["Wax Station", "Other"])
    scr = shoot / ".work" / "results"
    scr.mkdir(exist_ok=True)
    for sf, want in (("wax station", ("Wax Station", "")), ("Garden", ("Other", "Garden")), ("", ("", ""))):
        (scr / "C0001.final.json").write_text(json.dumps({"clip": "C0001", "category": "broll_with_sound", "subject_folder": sf, "name": "x"}))
        r = organize.load_records(jobmod.load_job(shoot / "job.json"))[0]
        assert (r["subject_folder"], r.get("subject_other", "")) == want


def test_what_a_classifier_writes_loosely_is_tidied_on_the_way():
    r = rec("C0001", name=42, people=["a", "b"], usable="false", reel=None, notes=None)
    assert organize.record_problems([r]) == [] and r["name"] == "42" and r["people"] == "['a', 'b']" and r["usable"] is False and r["reel"] == "NONE" and r["notes"] is None
    assert organize.record_problems([rec("C0002", usable="yes")]) == []
    r2 = rec("C0001", name=42, usable="no")
    organize.record_problems([r2])
    assert rel_of([r2])["C0001"] == "Unusable/C0001 - 42.MP4"
    assert jobmod.clean(42) == "42" and jobmod.clean(None) == "" and jobmod.clean(0) == ""


def test_a_record_with_a_category_that_is_not_one_of_the_six_is_named():
    probs = organize.record_problems([rec("C0001", category="banana"), rec("C0002", flags="oops"), rec("C0003", category="MISSING"), rec("C0004")])
    assert len(probs) == 2 and "C0001: category 'banana' is not one of" in probs[0] and "C0002: flags must be a list" in probs[1]


# ---------------------------------------------------------------- a whole shoot
def test_apply_files_every_clip_and_its_sidecars_and_changes_no_byte(organized, started_master):
    root = organized
    plan = json.loads((root / ".work" / "plan.json").read_text())
    assert plan["applied"] is True and plan["mode"] == "move" and plan["pre_move_bytes"] == sum(os.path.getsize(root / m["rel"]) for m in plan["moves"])
    assert json.loads((root / "Edit" / "plan.json").read_text()) == plan                       # the copy that outlives the scratch folder
    assert not (root / "RAW").exists()                                                         # emptied, so removed
    after = sha_tree(root)
    originals = started_master["hashes"]                                                       # hashes of the card as it was dropped
    for m in plan["moves"]:
        orig = [k for k in originals if k.startswith(m["clip"] + ".")][0]
        assert after[m["rel"]] == originals[orig], m["rel"]                                    # every clip is its original, byte for byte
    assert after["Camera Sidecars/C0001M01.XML"] == originals["C0001M01.XML"] and after["Camera Sidecars/C0006.LRF"] == originals["C0006.LRF"]
    assert plan["sidecars"]["C0001"] == ["C0001M01.XML"] and plan["sidecars"]["C0006"] == ["C0006.LRF"]


def test_the_tree_and_the_manifest_say_where_everything_went(organized):
    root = organized
    tree = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".work" not in p.parts)
    assert "Interview/Q01 Why Our Process Takes Three Days/C0001 - Q01 - Why Our Process Takes Three Days Clean Read - Take 1.MP4" in tree
    assert "Interview/Q01 Why Our Process Takes Three Days/C0007 - Q01 - Why Our Process Takes Three Days Two Attempts - Take 2.MP4" in tree
    assert "Interview/Q02 Welcome To The Workshop/C0002 - Q02 - Welcome To The Workshop Intro - Take 1.MP4" in tree
    assert "B-Roll/Wax Station/C0003 - Ambient Hum Of The Workshop Wide.MP4" in tree
    assert "B-Roll/Shelves/C0004 - Silent Shelf Of Candle Jars - No Audio.MP4" in tree
    assert "B-Roll/Wax Station/C0005 - Pouring Wax Into A Jar Wide - No Audio.MP4" in tree
    assert "B-Roll/Wax Station/C0006 - Chat About Wax Pouring - Candid Talk.MOV" in tree
    assert {"README.txt", "START HERE.html", "Footage Manifest.csv", "Edit/plan.json", "job.json"} <= set(tree)
    rows = list(csv.DictReader(open(root / "Footage Manifest.csv", encoding="utf-8")))
    assert len(rows) == 7 and list(rows[0]) == ["clip", "new_path", "original", "category", "reel", "reel_title", "subject_folder", "duration_s", "audio", "name", "question_asked",
                                                "answer_summary", "key_quote", "on_screen", "people", "shot", "camera", "flags", "usable", "confidence", "visual_agree", "content_agree", "source", "notes"]
    by = {r["clip"]: r for r in rows}
    assert by["C0001"]["original"] == "RAW/C0001.MP4" and by["C0001"]["category"] == "interview" and by["C0004"]["audio"] == "none (silent)"
    assert all((root / r["new_path"]).exists() for r in rows)


def test_a_record_with_no_verifier_stops_the_plan_and_names_the_clip_and_nothing_moves(classified):
    (classified / ".work" / "results" / "C0003.final.json").unlink()
    r = intake("apply", classified)
    assert r.returncode == 2 and "UNVERIFIED (1)" in r.stdout and "C0003" in r.stdout and r.stderr.strip().startswith("STOP: 1 clip(s) have no verified record")
    assert (classified / "RAW" / "C0003.MP4").exists() and not (classified / "Footage Manifest.csv").exists() and not (classified / "Interview").exists()


def test_a_classify_record_alone_is_not_verified_but_with_both_verdicts_it_is(classified):
    res = classified / ".work" / "results"
    final = json.loads((res / "C0003.final.json").read_text())
    (res / "C0003.final.json").unlink()
    (res / "C0003.classify.json").write_text(json.dumps(final))
    j = jobmod.load_job(classified / "job.json")
    assert not [r for r in organize.load_records(j) if r["clip"] == "C0003"][0]["verified"]
    for lens in ("visual", "content"):
        (res / f"C0003.{lens}.json").write_text(json.dumps({"clip": "C0003", "agree": True, "issues": [], "corrections": {}, "confidence": 0.9}))
    r3 = [r for r in organize.load_records(j) if r["clip"] == "C0003"][0]
    assert r3["verified"] and r3["source"] == "C0003.classify.json" and r3["visual_agree"] is True and r3["content_agree"] is True
    r = intake("apply", classified)                                            # the workflow route's agreed record files the clip
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)


def test_allow_unverified_files_a_clip_nobody_verified_as_unclassified(classified):
    (classified / ".work" / "results" / "C0003.final.json").unlink()
    r = intake("apply", classified, "--allow-unverified")
    assert (classified / "Unusable" / "C0003 - UNCLASSIFIED.MP4").exists()      # filed, so nothing is lost ...
    assert r.returncode == 1 and "QC: no UNCLASSIFIED clips" in r.stdout          # ... but the QC does not pass a shoot with a clip nobody looked at


def test_a_destination_that_already_exists_refuses_the_whole_run_before_anything_moves(classified):
    d = classified / "Interview" / "Q02 Welcome To The Workshop"
    d.mkdir(parents=True)
    (d / "C0002 - Q02 - Welcome To The Workshop Intro - Take 1.MP4").write_bytes(b"someone's file")
    before = sha_tree(classified)
    r = intake("apply", classified)
    assert r.returncode == 2 and "REFUSED, nothing moved" in r.stderr and "destination exists" in r.stderr
    assert sha_tree(classified) == before and (classified / "RAW" / "C0002.MP4").exists()      # not one file touched, and the other file is still the other file
    assert not (classified / "Footage Manifest.csv").exists() and not (classified / "README.txt").exists()


def test_apply_twice_is_refused_until_reverse(organized):
    r = intake("apply", organized)
    assert r.returncode == 2 and "already organized" in r.stderr


def test_plan_only_writes_a_preview_and_moves_nothing(classified):
    j = jobmod.load_job(classified / "job.json")
    before = sha_tree(classified)
    moves = organize.run(j, "plan", say=lambda s: None)
    assert len(moves) == 7 and sha_tree(classified) == before                      # the preview goes to .work; nothing in the shoot changes
    assert (classified / ".work" / "Footage Manifest.preview.csv").exists() and (classified / "RAW" / "C0001.MP4").exists() and not (classified / "Interview").exists()
    assert json.loads((classified / ".work" / "plan.json").read_text())["applied"] is False


# ---------------------------------------------------------------- a move that fails half way
def failing_rename(monkeypatch, root, nth, exc=OSError("disk went away")):
    real, count = os.rename, [0]

    def rename(a, b, *args, **kw):
        if str(a).startswith(str(root / "RAW")):
            count[0] += 1
            if count[0] == nth:
                raise exc
        return real(a, b, *args, **kw)
    monkeypatch.setattr(os, "rename", rename)
    return count


def test_a_move_that_fails_half_way_is_rolled_back_and_the_generated_files_are_removed(classified, monkeypatch, capsys):
    before = sha_tree(classified)
    failing_rename(monkeypatch, classified, 4)
    j = jobmod.load_job(classified / "job.json")
    with pytest.raises(SystemExit) as e:
        organize.run(j, "apply", say=lambda s: None)
    monkeypatch.undo()
    err = capsys.readouterr().err
    assert e.value.code == 2 and "MOVE FAILED (disk went away); 3 renames rolled back, camera files are where they were" in err
    assert sha_tree(classified) == before                                                           # every file is where it was and nothing generated is left
    assert not any((classified / d).exists() for d in ("Interview", "B-Roll", "Camera Sidecars", "Edit", "README.txt", "Footage Manifest.csv"))
    assert json.loads((classified / ".work" / "plan.json").read_text())["applied"] is False         # and a plan can be made again


def test_ctrl_c_in_the_middle_of_a_move_is_the_same(classified, monkeypatch, capsys):
    before = sha_tree(classified)
    failing_rename(monkeypatch, classified, 5, KeyboardInterrupt())
    j = jobmod.load_job(classified / "job.json")
    with pytest.raises(SystemExit):
        organize.run(j, "apply", say=lambda s: None)
    monkeypatch.undo()
    assert "STOPPED by you; 4 renames rolled back" in capsys.readouterr().err and sha_tree(classified) == before


def test_after_a_rolled_back_move_apply_works(classified, monkeypatch):
    failing_rename(monkeypatch, classified, 2)
    j = jobmod.load_job(classified / "job.json")
    with pytest.raises(SystemExit):
        organize.run(j, "apply", say=lambda s: None)
    monkeypatch.undo()
    r = intake("apply", classified)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)


# ---------------------------------------------------------------- reverse
def test_reverse_restores_every_camera_file_byte_for_byte_and_removes_what_was_generated(organized, started_master):
    r = intake("reverse", organized)
    assert r.returncode == 0 and "reversed 7 clips" in r.stdout, ok(r)
    left = sha_tree(organized)
    originals = started_master["hashes"]
    assert {k.replace("RAW/", "", 1): v for k, v in left.items() if k.startswith("RAW/")} == originals
    assert sorted(k for k in left if not k.startswith("RAW/")) == ["job.json"]
    assert not any((organized / d).exists() for d in ("Interview", "B-Roll", "Camera Sidecars", "Edit", "README.txt", "START HERE.html", "Footage Manifest.csv"))
    assert json.loads((organized / ".work" / "plan.json").read_text())["applied"] is False


def test_organize_after_a_reverse_gives_the_same_tree(organized):
    first = sorted(sha_tree(organized))
    assert intake("reverse", organized).returncode == 0
    r = intake("apply", organized)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)
    assert sorted(sha_tree(organized)) == first


def test_reverse_leaves_a_file_it_did_not_make_and_says_where(organized):
    note = organized / "Interview" / "Q01 Why Our Process Takes Three Days" / "editor note.txt"
    note.write_text("do not lose this")
    r = intake("reverse", organized)
    assert r.returncode == 0 and "left in place (not made by this tool" in r.stdout and "Interview/Q01 Why Our Process Takes Three Days" in r.stdout
    assert note.read_text() == "do not lose this" and (organized / "RAW" / "C0001.MP4").exists()


def test_reverse_refuses_when_the_source_folder_already_holds_a_file_of_the_same_name(organized):
    (organized / "RAW").mkdir()
    (organized / "RAW" / "C0001.MP4").write_bytes(b"a different file")
    before = sha_tree(organized)
    r = intake("reverse", organized)
    assert r.returncode == 2 and "REFUSED" in r.stderr and "already holds" in r.stderr
    assert sha_tree(organized) == before


def test_reverse_names_a_file_that_is_gone_and_still_puts_the_rest_back(organized):
    victim = next((organized / "B-Roll").rglob("C0003*.MP4"))
    victim.unlink()
    r = intake("reverse", organized)
    assert r.returncode == 0 and "not found, so not moved back (1)" in r.stdout and "C0003" in r.stdout
    assert len(list((organized / "RAW").glob("*.MP4"))) == 5


def test_a_manifest_that_points_outside_the_shoot_cannot_move_or_delete_anything_there(organized, tmp_path):
    outside = tmp_path / "precious.txt"
    outside.write_text("keep me")
    mf = organized / "Footage Manifest.csv"
    rows = list(csv.DictReader(open(mf, encoding="utf-8")))
    rows[0]["new_path"] = "../../" + os.path.relpath(outside, organized.parent.parent)
    with open(mf, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    before = sha_tree(organized)
    r = intake("reverse", organized)
    assert r.returncode == 2 and "REFUSED: the manifest names a path outside this shoot" in r.stderr
    assert outside.read_text() == "keep me" and sha_tree(organized) == before


def test_reverse_of_a_shoot_that_was_never_organized_is_one_plain_line(classified):
    r = intake("reverse", classified)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: nothing to reverse") and "Traceback" not in r.stderr


# ---------------------------------------------------------------- a card on another volume: copy, check, leave the card alone
def organize_from(tmp_path, card, monkeypatch=None, force_copy=False):
    """a mini shoot whose camera files live in `card` (outside the shoot folder), with records written; -> (shoot, job)"""
    shoot = mini_shoot(tmp_path, {"C0001.MP4": ("vertical", "noise"), "C0002.MP4": ("horizontal", "silent")}, sidecars={"C0001M01.XML": b"<xml/>"})
    shutil.copytree(shoot / "RAW", card)
    shutil.rmtree(shoot / "RAW")
    raw = json.loads((shoot / "job.json").read_text())
    raw["src"] = str(card)
    (shoot / "job.json").write_text(json.dumps(raw))
    scr = shoot / ".work" / "results"
    for clip, kw in (("C0001", dict(name="Pour One", subject_folder="Other")), ("C0002", dict(name="Shelf Two", subject_folder="Other", category="broll_silent"))):
        scr.mkdir(exist_ok=True)
        (scr / f"{clip}.final.json").write_text(json.dumps({"clip": clip, "category": "broll_with_sound", "reel": "NONE", "name": "x", "flags": [], "usable": True, **kw}))
    if force_copy:
        monkeypatch.setattr(organize, "same_volume", lambda a, b: False)
    return shoot, jobmod.load_job(shoot / "job.json")


def test_apply_from_another_volume_copies_checks_and_leaves_the_card_untouched(tmp_path, monkeypatch):
    card = tmp_path / "card"
    shoot, j = organize_from(tmp_path, card, monkeypatch, force_copy=True)
    before = sha_tree(card)
    lines = []
    organize.run(j, "apply", say=lines.append)
    assert sha_tree(card) == before                                                                 # the card is exactly as it was
    assert any(l.startswith("copied 2 clips") and "was not changed: the card can be ejected" in l for l in lines)
    plan = json.loads((shoot / ".work" / "plan.json").read_text())
    assert plan["mode"] == "copy" and plan["applied"] and plan["src"] == str(card)
    for m in plan["moves"]:
        orig = card / f"{m['clip']}.MP4"
        assert sha_tree(shoot)[m["rel"]] == sha_tree(card)[orig.name]
    assert (shoot / "Camera Sidecars" / "C0001M01.XML").read_bytes() == b"<xml/>"
    assert not [f for d, _, fs in os.walk(shoot) for f in fs if f.endswith(".part")]
    assert "copied (the source folder was left as it was)" in (shoot / "README.txt").read_text()


def test_a_copy_that_fails_deletes_the_copies_and_the_card_is_still_untouched(tmp_path, monkeypatch, capsys):
    card = tmp_path / "card"
    shoot, j = organize_from(tmp_path, card, monkeypatch, force_copy=True)
    before = sha_tree(card)
    real, n = organize.verified_copy, [0]

    def flaky(a, b):
        n[0] += 1
        if n[0] == 2:
            raise organize.CopyFailed("the copy of x does not match the original")
        return real(a, b)
    monkeypatch.setattr(organize, "verified_copy", flaky)
    with pytest.raises(SystemExit):
        organize.run(j, "apply", say=lambda s: None)
    err = capsys.readouterr().err
    assert "MOVE FAILED" in err and "1 copies deleted, the source was not touched" in err
    assert sha_tree(card) == before
    assert not any((shoot / d).exists() for d in ("B-Roll", "Camera Sidecars", "README.txt", "Footage Manifest.csv"))


def test_a_copy_that_would_not_fit_is_refused_before_anything_is_written(tmp_path, monkeypatch, capsys):
    card = tmp_path / "card"
    shoot, j = organize_from(tmp_path, card, monkeypatch, force_copy=True)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    with pytest.raises(SystemExit):
        organize.run(j, "apply", say=lambda s: None)
    err = capsys.readouterr().err
    assert "not enough room for the copy" in err and not (shoot / "B-Roll").exists() and not (shoot / "Footage Manifest.csv").exists()


def test_reverse_after_a_copy_files_the_copies_in_raw_inside_the_shoot_and_the_card_stays_as_it_was(tmp_path, monkeypatch):
    card = tmp_path / "card"
    shoot, j = organize_from(tmp_path, card, monkeypatch, force_copy=True)
    before_card = sha_tree(card)
    organize.run(j, "apply", say=lambda s: None)
    organize.reverse(j)
    assert sha_tree(card) == before_card
    assert sha_tree(shoot / "RAW") == before_card                                                    # the copies, back in the shoot's own RAW
    assert not (shoot / "B-Roll").exists() and not (shoot / "README.txt").exists()


def make_volume(tmp_path):
    """a real second volume: a small disk image mounted under /Volumes. -> (mount point, detach function), or skips"""
    if not shutil.which("hdiutil"):
        pytest.skip("needs hdiutil to make a second volume")
    img = tmp_path / "cardvol"
    r = subprocess.run(["hdiutil", "create", "-size", "64m", "-fs", "Journaled HFS+", "-volname", f"CUTROOMTEST{os.getpid()}", "-type", "SPARSE", "-attach", "-plist", str(img)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"hdiutil could not make a volume here: {r.stderr.strip()[-200:]}")
    import plistlib
    info = plistlib.loads(r.stdout.encode())
    mount = next(e["mount-point"] for e in info["system-entities"] if e.get("mount-point"))
    dev = next(e["dev-entry"] for e in info["system-entities"] if e.get("dev-entry"))

    def detach():
        subprocess.run(["hdiutil", "detach", mount, "-force", "-quiet"], capture_output=True)
    return Path(mount), detach


def test_on_a_real_second_volume_apply_copies_and_leaves_the_card_alone(tmp_path):
    need_media()
    mount, detach = make_volume(tmp_path)
    try:
        assert os.stat(mount).st_dev != os.stat(tmp_path).st_dev
        card = mount / "DCIM"
        shoot, j = organize_from(tmp_path, card)
        before = sha_tree(card)
        lines = []
        organize.run(j, "apply", say=lines.append)
        assert sha_tree(card) == before and json.loads((shoot / ".work" / "plan.json").read_text())["mode"] == "copy"
        assert any("the card can be ejected" in l for l in lines)
        assert all((shoot / m["rel"]).stat().st_size == (card / f"{m['clip']}.MP4").stat().st_size for m in json.loads((shoot / ".work" / "plan.json").read_text())["moves"])
        organize.reverse(j)
        assert sha_tree(card) == before and sha_tree(shoot / "RAW") == before
    finally:
        detach()


# ---------------------------------------------------------------- a file ffprobe could not read stays where it is
def test_a_video_prep_could_not_read_stays_in_raw_and_the_qc_still_passes(tmp_path):
    need_media()
    shoot = mini_shoot(tmp_path, {"C0001.MP4": ("vertical", "noise")})
    (shoot / "RAW" / "C0002.MP4").write_bytes(b"this is not a video" * 50)
    r = run_tool("prep.py", "--job", shoot / "job.json", "--skip-whisper")
    assert r.returncode == 0 and "ffprobe cannot read C0002.MP4" in r.stdout
    scr = shoot / ".work" / "results"
    scr.mkdir(exist_ok=True)
    (scr / "C0001.final.json").write_text(json.dumps({"clip": "C0001", "category": "broll_with_sound", "reel": "NONE", "name": "Pour", "flags": [], "usable": True, "subject_folder": "Other"}))
    (shoot / ".work" / "cheat-sheet.txt").write_text("x")
    r = intake("apply", shoot)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)
    assert (shoot / "RAW" / "C0002.MP4").read_bytes() == b"this is not a video" * 50 and not (shoot / "RAW" / "C0001.MP4").exists()
    assert json.loads((shoot / ".work" / "plan.json").read_text())["left_alone"] == ["C0002.MP4"]
    assert intake("reverse", shoot).returncode == 0 and (shoot / "RAW" / "C0002.MP4").exists() and (shoot / "RAW" / "C0001.MP4").exists()


def test_files_that_are_not_clips_stay_in_raw_and_are_named(tmp_path):
    need_media()
    shoot = mini_shoot(tmp_path, {"C0001.MP4": ("vertical", "noise")}, sidecars={"photo.jpg": b"jpg", "voice memo.m4a": b"audio", "C0001M01.XML": b"<x/>"})
    scr = shoot / ".work" / "results"
    scr.mkdir(exist_ok=True)
    (scr / "C0001.final.json").write_text(json.dumps({"clip": "C0001", "category": "broll_with_sound", "reel": "NONE", "name": "Pour", "flags": [], "usable": True, "subject_folder": "Other"}))
    r = intake("apply", shoot)
    assert r.returncode == 0 and "source leftovers: ['photo.jpg', 'voice memo.m4a']" in r.stdout and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)
    assert sorted(os.listdir(shoot / "RAW")) == ["photo.jpg", "voice memo.m4a"] and (shoot / "Camera Sidecars" / "C0001M01.XML").exists()
