"""intake.py: the one command the module is driven by. start, subjects, apply, takes, notes, status, reverse, and what each stops on."""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import common
import intake as intake_mod
import job as jobmod
import organize
from conftest import HOME, Workspace, classify_card, intake, make_clip, need_media, need_speech, ok, run_tool, sha_tree, write_config, write_final
from test_organize import make_volume


# ---------------------------------------------------------------- the question sheet
def q(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_a_question_sheet_in_the_usual_shape_is_read(tmp_path):
    p = q(tmp_path, "q.md", '# Questions\n\nQ01 Welcome To The Workshop \u2014 "I\'m Dana, welcome to the workshop." \n'
                            'Q02 Why Three Days: "Why does it take three days?" Follow-ups: what cures; what is rushed.\n'
                            '- Q03: Can You Rush It? \u2014 "When somebody needs it fast, what do you check?"\n')
    got = intake_mod.parse_questions(p)
    assert [x["id"] for x in got] == ["Q01", "Q02", "Q03"]
    assert got[0] == {"id": "Q01", "title": "Welcome To The Workshop", "question": "I'm Dana, welcome to the workshop."}
    assert got[1] == {"id": "Q02", "title": "Why Three Days", "question": "Why does it take three days?", "followups": "what cures; what is rushed."}
    assert got[2] == {"id": "Q03", "title": "Can You Rush It?", "question": "When somebody needs it fast, what do you check?"}


def test_a_plain_list_of_questions_gets_ids_and_short_titles(tmp_path):
    p = q(tmp_path, "q.txt", "1. Why does your process take three days?\n2) What happens to a batch that is rushed?\n- Who taught you to pour wax?\n\nLast one without a number\n")
    got = intake_mod.parse_questions(p)
    assert [x["id"] for x in got] == ["Q01", "Q02", "Q03", "Q04"]
    assert got[0]["title"] == "Why Does Your Process Take Three" and got[0]["question"] == "Why does your process take three days?"
    assert got[3]["question"] == "Last one without a number"


def test_ids_that_are_given_are_kept_and_the_rest_do_not_clash_with_them(tmp_path):
    p = q(tmp_path, "q.txt", 'L02 Second Line: "The second line."\nWhat is this one?\nL01 First Line: "The first line."\n')
    got = intake_mod.parse_questions(p)
    assert [x["id"] for x in got] == ["L02", "Q02", "L01"]


def test_script_lines_for_an_ad_shoot_are_read_the_same_way(tmp_path):
    p = q(tmp_path, "lines.md", 'L01 Most Ads Fail Early: "Most ads fail before anyone sees them."\nL02 Fix The Hook: "Fix the first three seconds."\n')
    got = intake_mod.parse_questions(p)
    assert [(x["id"], x["title"], x["question"]) for x in got] == [("L01", "Most Ads Fail Early", "Most ads fail before anyone sees them."), ("L02", "Fix The Hook", "Fix the first three seconds.")]


@pytest.mark.parametrize("body", [
    [{"id": "Q01", "title": "A", "question": "Why?"}, {"id": "Q02", "title": "B", "question": "How?", "followups": "x"}],
    {"questions": [{"id": "Q01", "title": "A", "question": "Why?"}]},
    ["Why does it take three days?", "What is rushed?"],
])
def test_a_json_question_sheet_is_a_list_or_an_object_with_one(tmp_path, body):
    got = intake_mod.parse_questions(q(tmp_path, "q.json", json.dumps(body)))
    assert all(x["id"] and x["title"] and x["question"] for x in got) and len(got) == len(body["questions"] if isinstance(body, dict) else body)
    assert got[0]["id"] == "Q01"


@pytest.mark.parametrize("name, text, why", [("none.md", None, "does not exist"), ("empty.md", "# nothing here\n\n", "holds no questions"), ("bad.json", "{x", "is not valid JSON"),
                                             ("num.json", "[1, 2]", "must be an object or a string"), ("obj.json", '{"a": 1}', "must hold a list")])
def test_a_question_sheet_that_cannot_be_read_stops_with_one_line(tmp_path, capsys, name, text, why):
    p = tmp_path / name
    if text is not None:
        p.write_text(text)
    with pytest.raises(SystemExit):
        intake_mod.parse_questions(p)
    err = capsys.readouterr().err.strip()
    assert err.startswith("STOP: ") and why in err and "\n" not in err


# ---------------------------------------------------------------- taking the files in, unit level
def touch(root, rel, data=b"x"):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_a_card_is_gathered_without_its_junk_and_without_links(tmp_path):
    for rel in ("DCIM/100MEDIA/C0001.MP4", "DCIM/100MEDIA/C0001M01.XML", "PRIVATE/M4ROOT/CLIP/C0002.MP4", ".DS_Store", "DCIM/._C0001.MP4", "DCIM/Thumbs.db",
                ".Spotlight-V100/Store/x", ".Trashes/501/y.MP4", ".fseventsd/z", "notes.txt"):
        touch(tmp_path, rel)
    os.symlink(tmp_path / "notes.txt", tmp_path / "link.txt")
    files, links = intake_mod.gather(tmp_path)
    assert sorted(rel for _, rel in files) == ["DCIM/100MEDIA/C0001.MP4", "DCIM/100MEDIA/C0001M01.XML", "PRIVATE/M4ROOT/CLIP/C0002.MP4", "notes.txt"] and links == 1


def test_a_single_file_is_gathered_as_itself(tmp_path):
    p = touch(tmp_path, "clip.mp4")
    assert intake_mod.gather(p) == ([(str(p), "clip.mp4")], 0)


def test_a_card_is_flattened_and_names_that_do_not_clash_are_kept():
    files = [("/a/DCIM/C0001.MP4", "DCIM/C0001.MP4"), ("/a/DCIM/C0001M01.XML", "DCIM/C0001M01.XML"), ("/a/other/C0002.MP4", "other/C0002.MP4"), ("/a/top.mp4", "top.mp4")]
    assert [n for _, n in intake_mod.flat_names(files)] == ["C0001.MP4", "C0001M01.XML", "C0002.MP4", "top.mp4"]


def test_two_cards_with_one_name_get_their_folder_in_front_of_every_file_so_sidecars_stay_with_their_clip():
    files = [("/a/CARD_A/C0001.MP4", "CARD_A/C0001.MP4"), ("/a/CARD_A/C0001M01.XML", "CARD_A/C0001M01.XML"), ("/a/CARD_B/C0001.MP4", "CARD_B/C0001.MP4"),
             ("/a/CARD_B/C0001M01.XML", "CARD_B/C0001M01.XML"), ("/a/solo.MP4", "solo.MP4")]
    got = [n for _, n in intake_mod.flat_names(files)]
    assert got == ["CARD_A - C0001.MP4", "CARD_A - C0001M01.XML", "CARD_B - C0001.MP4", "CARD_B - C0001M01.XML", "solo.MP4"]


def test_a_file_at_the_top_level_keeps_its_name_and_the_folder_that_clashes_is_the_one_renamed():
    files = [("/a/C0001.MP4", "C0001.MP4"), ("/a/card/C0001.MP4", "card/C0001.MP4")]
    assert [n for _, n in intake_mod.flat_names(files)] == ["C0001.MP4", "card - C0001.MP4"]


def test_names_that_differ_only_by_case_clash_on_a_mac_and_a_video_clashes_by_its_name_without_the_extension():
    assert [n for _, n in intake_mod.flat_names([("/a/x/C1.mp4", "x/C1.mp4"), ("/a/y/c1.MP4", "y/c1.MP4")])] == ["x - C1.mp4", "y - c1.MP4"]
    assert [n for _, n in intake_mod.flat_names([("/a/x/C1.mp4", "x/C1.mp4"), ("/a/y/C1.mov", "y/C1.mov")])] == ["x - C1.mp4", "y - C1.mov"]


def test_a_name_that_is_still_taken_gets_a_number_so_nothing_is_ever_overwritten():
    files = [("/a/a_b/C1.mp4", "a_b/C1.mp4"), ("/a/a/b/C1.mp4", "a/b/C1.mp4")]            # both folders tag as a_b
    got = [n for _, n in intake_mod.flat_names(files)]
    assert len({g.lower() for g in got}) == 2


def test_the_day_is_the_day_the_first_clip_was_recorded(tmp_path):
    a, b = touch(tmp_path, "a.mp4"), touch(tmp_path, "b.mp4")
    os.utime(a, (1_761_000_000, 1_761_000_000))
    os.utime(b, (1_762_000_000, 1_762_000_000))
    day = time.strftime("%Y-%m-%d", time.localtime(1_761_000_000))
    assert intake_mod.guess_date([(str(a), "a.mp4"), (str(b), "b.mp4")]) == day
    assert intake_mod.guess_date([(str(touch(tmp_path, "n.txt")), "n.txt")]) == time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- start
def drop(ws, name, spec):
    """<workspace>/Drop/<name>/ with the files of spec: {relative path: "clip" (a tiny clip with no sound) or bytes}"""
    need_media()
    root = ws.drop / name if name else ws.drop
    for rel, what in spec.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if what == "clip":
            make_clip(p, "vertical", "none", seconds=2, label=p.stem)
        elif what == "hclip":
            make_clip(p, "horizontal", "silent", seconds=2, label=p.stem)
        else:
            p.write_bytes(what)
    return root


def shoot_of(ws, folder):
    return ws.footage / folder


def test_start_makes_the_shoot_folder_takes_the_files_in_and_says_what_comes_next(ws):
    need_speech()
    card = drop(ws, "Spring Card", {"DCIM/100MEDIA/C0001.MP4": "clip", "DCIM/100MEDIA/C0001M01.XML": b"<xml/>", "PRIVATE/M4ROOT/CLIP/C0002.MP4": "hclip", "DCIM/.DS_Store": b"junk",
                                    "DCIM/._C0001.MP4": b"apple double", "DCIM/Thumbs.db": b"junk", "photo.jpg": b"jpg bytes"})
    originals = {rel: h for rel, h in sha_tree(card).items() if rel in ("DCIM/100MEDIA/C0001.MP4", "DCIM/100MEDIA/C0001M01.XML", "PRIVATE/M4ROOT/CLIP/C0002.MP4", "photo.jpg")}
    r = intake("start", card, "--name", "Spring Shoot", "--date", "2026-04-02", "--type", "ad", "--context", "A spring shoot.", "--people", "Dana on camera.")
    assert r.returncode == 0 and "Traceback" not in r.stderr, ok(r)
    shoot = shoot_of(ws, "2026-04-02 Spring Shoot")
    assert sorted(os.listdir(shoot / "RAW")) == ["C0001.MP4", "C0001M01.XML", "C0002.MP4", "photo.jpg"]              # flattened, junk left behind
    assert {k: v for k, v in sha_tree(shoot / "RAW").items()} == {os.path.basename(k): v for k, v in originals.items()}   # every byte as it was dropped
    assert not card.exists()                                                                                          # the emptied folder in Drop is gone ...
    assert ws.drop.is_dir()                                                                                           # ... and Drop itself never is
    job = json.loads((shoot / "job.json").read_text())
    assert job["name"] == "Spring Shoot" and job["shoot_type"] == "ad" and job["shoot_date"] == "2026-04-02" and job["context"] == "A spring shoot." and job["people"] == "Dana on camera."
    assert job["root"] == str(shoot) and job["src"] == "<ROOT>/RAW" and job["scratch"] == "<ROOT>/.work" and job["questions"] == [] and job["subjects"] == []
    journal = json.loads((shoot / ".work" / "start.json").read_text())
    assert journal["mode"] == "move" and len(journal["files"]) == 4 and journal["source"] == str(card)
    for d in ("sheets", "thumbs", "ev", "results", "audio", "transcripts"):
        assert (shoot / ".work" / d).is_dir()
    assert (shoot / ".work" / "cheat-sheet.txt").exists() and (shoot / ".work" / "evidence.json").exists()
    out = r.stdout
    assert "[probe] 2 clips" in out and "SHOOT STARTED in" in out and "NEXT: two steps for Claude, no script does them" in out
    assert "intake.py\" subjects" in out and "classify-batch.md" in out and "C0001\n" in out and "C0002\n" in out and ".work/results/<clip>.final.json" in out
    assert "\u2014" not in out


def test_a_file_that_only_looks_like_junk_is_never_deleted_it_stays_in_the_dropped_folder_and_is_named(ws):
    need_speech()
    card = drop(ws, "Careful", {"C0001.MP4": "clip", "interview.part.1.mov": b"someone's real file", ".DS_Store": b"junk"})
    r = intake("start", card, "--name", "Careful", "--date", "2026-05-09")
    assert r.returncode == 0, ok(r)
    assert os.listdir(ws.footage / "2026-05-09 Careful" / "RAW") == ["C0001.MP4"]
    assert (card / "interview.part.1.mov").read_bytes() == b"someone's real file" and not (card / ".DS_Store").exists()   # the OS junk went, the file stayed
    assert "[warn] 1 item(s) stay in" in r.stdout and "interview.part.1.mov" in r.stdout


def test_start_names_the_folder_from_the_dropped_folder_and_the_date_from_the_clips(ws):
    need_speech()
    card = drop(ws, "Back Yard Day", {"C0001.MP4": "clip"})
    os.utime(card / "C0001.MP4", (1_761_000_000, 1_761_000_000))
    r = intake("start", card)
    assert r.returncode == 0, ok(r)
    day = time.strftime("%Y-%m-%d", time.localtime(1_761_000_000))
    assert (ws.footage / f"{day} Back Yard Day" / "job.json").exists()
    assert json.loads((ws.footage / f"{day} Back Yard Day" / "job.json").read_text())["shoot_type"] == "interview"


def test_loose_files_dropped_straight_into_drop_are_taken_and_drop_stays(ws):
    need_speech()
    drop(ws, "", {"C0001.MP4": "clip", "C0002.MP4": "clip"})
    r = intake("start", ws.drop, "--name", "Loose Clips", "--date", "2026-05-01")
    assert r.returncode == 0, ok(r)
    assert ws.drop.is_dir() and os.listdir(ws.drop) == []
    assert sorted(os.listdir(ws.footage / "2026-05-01 Loose Clips" / "RAW")) == ["C0001.MP4", "C0002.MP4"]


def test_a_single_file_can_be_dropped(ws):
    need_speech()
    drop(ws, "", {"one.mp4": "clip"})
    r = intake("start", ws.drop / "one.mp4", "--name", "One Clip", "--date", "2026-05-02")
    assert r.returncode == 0, ok(r)
    assert os.listdir(ws.footage / "2026-05-02 One Clip" / "RAW") == ["one.mp4"] and not (ws.drop / "one.mp4").exists()


def test_two_cards_that_both_hold_c0001_are_both_kept_with_their_sidecars(ws):
    need_speech()
    drop(ws, "Both Cards", {"CARD_A/C0001.MP4": "clip", "CARD_A/C0001M01.XML": b"a-side", "CARD_B/C0001.MP4": "hclip", "CARD_B/C0001M01.XML": b"b-side"})
    r = intake("start", ws.drop / "Both Cards", "--date", "2026-05-03")
    assert r.returncode == 0, ok(r)
    raw = ws.footage / "2026-05-03 Both Cards" / "RAW"
    assert sorted(os.listdir(raw)) == ["CARD_A - C0001.MP4", "CARD_A - C0001M01.XML", "CARD_B - C0001.MP4", "CARD_B - C0001M01.XML"]
    assert (raw / "CARD_A - C0001M01.XML").read_bytes() == b"a-side" and (raw / "CARD_B - C0001M01.XML").read_bytes() == b"b-side"
    j = jobmod.load_job(ws.footage / "2026-05-03 Both Cards" / "job.json")
    src = organize.list_sources(j["src"])
    assert [os.path.basename(p) for p in src["CARD_A - C0001"]["sidecars"]] == ["CARD_A - C0001M01.XML"]          # a clip still owns its own sidecar


def test_a_clip_name_with_spaces_goes_through_start_apply_and_reverse(ws):
    need_speech()
    drop(ws, "Spaces", {"0025 - answer take one.mp4": "clip", "0026 - answer take two.mp4": "hclip"})
    assert intake("start", ws.drop / "Spaces", "--date", "2026-05-04").returncode == 0
    shoot = ws.footage / "2026-05-04 Spaces"
    for clip in ("0025 - answer take one", "0026 - answer take two"):
        write_final(shoot / ".work", clip, category="broll_silent", name="Plain Shot", subject_folder="Other", people="nobody")
    r = intake("apply", shoot)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)
    assert (shoot / "B-Roll" / "Other" / "0025 - answer take one - Plain Shot - No Audio.mp4").exists()
    assert intake("reverse", shoot).returncode == 0 and sorted(os.listdir(shoot / "RAW")) == ["0025 - answer take one.mp4", "0026 - answer take two.mp4"]


def test_awkward_file_names_survive_the_whole_way_and_come_back_exactly(ws):
    """accents, #, %, &, parentheses and an apostrophe in a clip name: the filed name is cleaned, the original is kept in the manifest, the sidecar follows, reverse restores the name"""
    need_speech()
    names = ["Caf\u00e9 take #1 100% (final).mp4", "Br\u00fbl\u00e9e & Co's reel.mp4"]
    drop(ws, "Awkward", {names[0]: "clip", names[1]: "hclip", "Caf\u00e9 take #1 100% (final).xml": b"<x/>"})
    originals = sha_tree(ws.drop / "Awkward")
    assert intake("start", ws.drop / "Awkward", "--date", "2026-05-10").returncode == 0
    shoot = ws.footage / "2026-05-10 Awkward"
    for clip in (names[0][:-4], names[1][:-4]):
        write_final(shoot / ".work", clip, category="broll_silent", name="Plain Shot", subject_folder="Other", people="nobody")
    r = intake("apply", shoot)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)
    filed = sorted(os.listdir(shoot / "B-Roll" / "Other"))
    assert filed == ["Br\u00fbl\u00e9e & Co's reel - Plain Shot - No Audio.mp4", "Caf\u00e9 take 1 100 (final) - Plain Shot - No Audio.mp4"]
    rows = {r_["clip"]: r_ for r_ in csv.DictReader(open(shoot / "Footage Manifest.csv", encoding="utf-8"))}
    assert rows[names[0][:-4]]["original"] == "RAW/" + names[0] and os.listdir(shoot / "Camera Sidecars") == ["Caf\u00e9 take #1 100% (final).xml"]
    assert intake("notes", shoot).returncode == 0 and (shoot / "B-Roll" / "Other" / "Caf\u00e9 take 1 100 (final) - Plain Shot - No Audio.txt").exists()
    assert intake("reverse", shoot).returncode == 0
    assert sha_tree(shoot / "RAW") == originals                                        # the names and every byte, as dropped


def test_start_on_a_real_second_volume_copies_checks_and_leaves_the_card_alone(ws, tmp_path):
    need_speech()
    mount, detach = make_volume(tmp_path)
    try:
        card = mount / "DCIM"
        for name, shape in (("C0001.MP4", "vertical"), ("C0002.MP4", "horizontal")):
            card.mkdir(exist_ok=True)
            make_clip(card / name, shape, "none", seconds=2, label=name)
        (card / "C0001M01.XML").write_bytes(b"<x/>")
        before = sha_tree(card)
        r = intake("start", card, "--name", "From The Card", "--date", "2026-05-05")
        assert r.returncode == 0, ok(r)
        shoot = ws.footage / "2026-05-05 From The Card"
        assert "Copying 3 files" in r.stdout and "each checked against the original" in r.stdout and "the card can be ejected" in r.stdout
        assert sha_tree(card) == before                                                                  # not a byte of the card changed, nothing deleted from it
        assert sha_tree(shoot / "RAW") == before and json.loads((shoot / ".work" / "start.json").read_text())["mode"] == "copy"
        assert not [f for d, _, fs in os.walk(shoot) for f in fs if f.endswith(".part")]
    finally:
        detach()


def test_a_failure_in_the_middle_of_taking_the_files_in_puts_every_file_back_and_removes_the_shoot_folder(ws, monkeypatch, capsys):
    need_speech()
    card = drop(ws, "Fails", {"C0001.MP4": "clip", "C0002.MP4": "clip", "C0003.MP4": "clip"})
    before = sha_tree(card)
    real, n = os.rename, [0]

    def rename(a, b, *args, **kw):
        if str(a).startswith(str(card)):
            n[0] += 1
            if n[0] == 3:
                raise OSError("the volume went away")
        return real(a, b, *args, **kw)
    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(SystemExit) as e:
        intake_mod.main(["start", str(card), "--name", "Fails", "--date", "2026-05-06"])
    monkeypatch.undo()
    err = capsys.readouterr().err.strip()
    assert e.value.code == 2 and err.endswith("could not take the files in (the volume went away); the files are back where they were") and "Traceback" not in err
    assert sha_tree(card) == before and not (ws.footage / "2026-05-06 Fails").exists()


def test_ctrl_c_while_taking_the_files_in_is_the_same(ws, monkeypatch, capsys):
    need_speech()
    card = drop(ws, "Stopped", {"C0001.MP4": "clip", "C0002.MP4": "clip"})
    before = sha_tree(card)
    real, n = os.rename, [0]

    def rename(a, b, *args, **kw):
        if str(a).startswith(str(card)):
            n[0] += 1
            if n[0] == 2:
                raise KeyboardInterrupt()
        return real(a, b, *args, **kw)
    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(SystemExit):
        intake_mod.main(["start", str(card), "--date", "2026-05-07"])
    monkeypatch.undo()
    assert "STOPPED by you; the files are back where they were" in capsys.readouterr().err and sha_tree(card) == before and not list(ws.footage.iterdir())


def test_a_copy_that_would_not_fit_is_refused_before_anything_is_written(ws, tmp_path, monkeypatch, capsys):
    need_speech()
    card = drop(ws, "Big", {"C0001.MP4": "clip"})
    monkeypatch.setattr(organize, "same_volume", lambda a, b: False)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    with pytest.raises(SystemExit):
        intake_mod.main(["start", str(card), "--date", "2026-05-08"])
    assert "not enough room to copy" in capsys.readouterr().err and not list(ws.footage.iterdir()) and (card / "C0001.MP4").exists()


@pytest.mark.parametrize("make, args, why", [
    (lambda ws: ws.root / "nowhere", [], "does not exist"),
    (lambda ws: drop(ws, "Empty", {}) if (ws.drop / "Empty").mkdir() is None else None, [], "holds no files"),
    (lambda ws: drop(ws, "Photos", {"a.jpg": b"x", "b.wav": b"y"}), [], "no video files in"),
    (lambda ws: drop(ws, "Clips", {"C0001.MP4": "clip"}), ["--date", "yesterday"], "--date must look like"),
    (lambda ws: drop(ws, "Clips", {"C0001.MP4": "clip"}), ["--questions", "/nowhere/q.md"], "does not exist"),
])
def test_start_stops_with_one_plain_line_and_changes_nothing(ws, make, args, why):
    need_speech()
    path = make(ws)
    before = sha_tree(ws.drop)
    r = intake("start", path, *args)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: ") and why in r.stderr and "Traceback" not in r.stderr, ok(r)
    assert sha_tree(ws.drop) == before and not list(ws.footage.iterdir())


def test_a_question_the_job_would_refuse_stops_start_before_a_file_moves(ws, tmp_path):
    need_speech()
    card = drop(ws, "Bad Ids", {"C0001.MP4": "clip"})
    sheet = q(tmp_path, "q.json", json.dumps([{"id": "Q 1", "title": "Has A Space", "question": "Why?"}]))
    before = sha_tree(card)
    r = intake("start", card, "--questions", sheet)
    assert r.returncode == 2 and "job.questions item 1: the id 'Q 1' may hold only letters" in r.stderr and "Traceback" not in r.stderr
    assert sha_tree(card) == before and not list(ws.footage.iterdir())


def test_start_on_a_name_already_used_says_so_and_leaves_both_alone(ws):
    need_speech()
    drop(ws, "Same", {"C0001.MP4": "clip"})
    assert intake("start", ws.drop / "Same", "--name", "Repeat", "--date", "2026-06-01").returncode == 0
    drop(ws, "Same", {"C0009.MP4": "clip"})
    r = intake("start", ws.drop / "Same", "--name", "Repeat", "--date", "2026-06-01")
    assert r.returncode == 2 and "already exists: choose another --name" in r.stderr
    assert (ws.drop / "Same" / "C0009.MP4").exists() and os.listdir(ws.footage / "2026-06-01 Repeat" / "RAW") == ["C0001.MP4"]


def test_a_folder_already_inside_footage_is_refused(ws):
    need_speech()
    inside = ws.footage / "2026-01-01 Old" / "RAW"
    inside.mkdir(parents=True)
    r = intake("start", inside)
    assert r.returncode == 2 and "is already inside the Footage folder" in r.stderr


def test_start_without_a_workspace_says_to_run_the_setup(tmp_path):
    need_media()
    cfg = HOME / "config.json"
    cfg.write_text("{}")
    r = intake("start", tmp_path)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: no workspace is set") and "Traceback" not in r.stderr


# ---------------------------------------------------------------- subjects
def test_subjects_are_tidied_deduped_and_other_is_always_last(started):
    r = intake("subjects", started, "--set", " desk work ,Wax Station,\nDESK WORK,front  counter ")
    assert r.returncode == 0, ok(r)
    j = json.loads((started / "job.json").read_text())
    assert j["subjects"] == ["Desk Work", "Wax Station", "Front Counter", "Other"]
    assert "B-roll folders set (4): Desk Work, Wax Station, Front Counter, Other" in r.stdout and "classify 7 clip(s)" in r.stdout
    args = json.loads((started / ".work" / "workflow-args.json").read_text())
    assert args["subjects"] == j["subjects"] and len(args["clips"]) == 7 and args["has_questions"] is True
    assert "Q01  Why Our Process Takes Three Days: Why does your process take three days? Follow-ups: what happens overnight." in (started / ".work" / "cheat-sheet.txt").read_text()


def test_subjects_that_already_name_other_do_not_get_a_second_one(started):
    assert intake("subjects", started, "--set", "Bench,other").returncode == 0
    assert json.loads((started / "job.json").read_text())["subjects"] == ["Bench", "Other"]


@pytest.mark.parametrize("value, why", [("", "--set needs at least one folder name"), (" , ,", "--set needs at least one folder name"), (",".join(f"Folder {i}" for i in range(30)), "too many to be useful")])
def test_bad_subjects_stop_with_one_line(started, value, why):
    r = intake("subjects", started, "--set", value)
    assert r.returncode == 2 and why in r.stderr and "Traceback" not in r.stderr
    assert json.loads((started / "job.json").read_text())["subjects"] == []


# ---------------------------------------------------------------- the whole way through
def test_start_then_classify_then_apply_then_reverse_restores_the_card_byte_for_byte(card, ws):
    need_speech()
    originals = sha_tree(card.dir)
    r = intake("start", card.dir, "--name", "Candle Workshop", "--date", "2026-10-31", "--questions", card.sheet, "--people", "One speaker.")
    assert r.returncode == 0, ok(r)
    shoot = ws.footage / "2026-10-31 Candle Workshop"
    assert not card.dir.exists() and sorted(os.listdir(shoot / "RAW")) == sorted(originals)
    r = intake("status", shoot)
    assert "Stage: prepped, B-roll folders not set" in r.stdout and "records: 0 of 7 ready" in r.stdout
    # apply too soon: nothing classified
    r = intake("apply", shoot)
    assert r.returncode == 2 and "UNVERIFIED (7)" in r.stdout and (shoot / "RAW" / "C0001.MP4").exists()
    assert intake("subjects", shoot, "--set", "Workshop Bench,Wax Station,Shelves,Other").returncode == 0
    classify_card(shoot / ".work")
    r = intake("status", shoot)
    assert "Stage: ready to organize" in r.stdout and "records: 7 of 7 ready" in r.stdout
    r = intake("apply", shoot)
    assert r.returncode == 0, ok(r)
    lines = r.stdout.strip().splitlines()
    assert lines[-1] == "QC OK" and re.fullmatch(r"Index: .*START HERE\.html \(\d+ KB\)", lines[-2]) and r.stdout.count("START HERE.html") == 1      # the path once, not twice
    index = shoot / "START HERE.html"
    assert index.exists()
    page = index.read_text(encoding="utf-8")
    plan = json.loads((shoot / ".work" / "plan.json").read_text())
    for m in plan["moves"]:
        assert os.path.basename(m["rel"]).replace("&", "&amp;") in page or os.path.basename(m["rel"]) in page, m["rel"]
    assert page.count('class="thumb"') == 7
    assert intake("notes", shoot).returncode == 0
    st = json.loads(intake("status", shoot, "--json").stdout)
    assert st["stage"] == "organized" and st["organized"] and st["index"] and st["notes_files"] == 7 and st["records_ready"] == 7
    r = intake("reverse", shoot)
    assert r.returncode == 0 and "reversed 7 clips" in r.stdout, ok(r)
    assert {k.replace("RAW/", "", 1): v for k, v in sha_tree(shoot).items() if k.startswith("RAW/")} == originals                   # byte for byte
    assert sorted(k for k in sha_tree(shoot) if not k.startswith("RAW/")) == ["job.json"]


def test_the_takes_stage_stops_for_the_segments_then_cuts(organized, started_master):
    r = intake("takes", organized)
    assert r.returncode == 0, ok(r)
    assert "[words] done, 3 clips" in r.stdout and "NEXT: for each clip below, Claude writes" in r.stdout and "segment.md" in r.stdout
    assert "  C0001 (" in r.stdout and "  C0002 (" in r.stdout and "  C0007 (" in r.stdout
    assert all((organized / ".work" / "words" / f"{c}.txt").exists() for c in ("C0001", "C0002", "C0007")) and not (organized / "Takes Manifest.csv").exists()
    r2 = intake("takes", organized, "--cut")
    assert r2.returncode == 2 and r2.stderr.strip().startswith("STOP: no segments yet")
    (att1, att2) = started_master["attempts"]
    dur = {e["clip"]: e["duration_s"] for e in json.loads((organized / ".work" / "evidence.json").read_text())}
    res = organized / ".work" / "results"
    (res / "C0001.segments.json").write_text(json.dumps({"clip": "C0001", "segments": [{"start": 0.0, "end": dur["C0001"] - 0.3, "kind": "take", "line": "Q01", "quality": "clean", "note": ""}]}))
    (res / "C0007.segments.json").write_text(json.dumps({"clip": "C0007", "segments": [
        {"start": att1[0], "end": att1[1] + 0.3, "kind": "take", "line": "Q01", "quality": "flub", "note": "stops short"},
        {"start": att2[0] - 0.1, "end": min(att2[1] + 0.3, dur["C0007"]), "kind": "take", "line": "Q01", "quality": "clean", "note": ""}]}))
    dry = intake("takes", organized, "--cut", "--handle", "1", "--dry-run")
    assert dry.returncode == 0 and "3 cuts from 2 raw clips" in dry.stdout and not (organized / "Takes Manifest.csv").exists()
    r3 = intake("takes", organized, "--cut", "--handle", "1")
    assert r3.returncode == 0, ok(r3)
    lines = r3.stdout.strip().splitlines()
    assert lines[-1] == "QC OK" and any(l.startswith("Index: ") for l in lines) and "[takes] done" in r3.stdout
    assert len(list(csv.DictReader(open(organized / "Takes Manifest.csv", encoding="utf-8")))) == 3
    r4 = intake("status", organized)
    assert "Stage: organized, takes cut" in r4.stdout and "segments for 2 of the 3 that answer a listed line" in r4.stdout and "3 cuts" in r4.stdout


def test_a_clip_that_answers_no_listed_line_is_optional_in_the_takes_stage(organized):
    res = organized / ".work" / "results"
    rec = json.loads((res / "C0002.final.json").read_text())
    rec["reel"] = "BONUS"
    (res / "C0002.final.json").write_text(json.dumps(rec))
    r = intake("takes", organized)
    assert r.returncode == 0, ok(r)
    out = r.stdout
    assert out.index("  C0001 (") < out.index("Optional: these answer no listed line") < out.index("  C0002 (")


def test_takes_need_a_list_of_lines_to_file_under(organized):
    j = json.loads((organized / "job.json").read_text())
    j["questions"] = []
    (organized / "job.json").write_text(json.dumps(j))
    r = intake("takes", organized)
    assert r.returncode == 2 and "this shoot has none" in r.stderr and "Traceback" not in r.stderr


def test_takes_before_apply_is_one_plain_line(classified):
    r = intake("takes", classified)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: takes come after the shoot is organized")


# ---------------------------------------------------------------- the stages the workflow route uses
def test_the_workflow_routes_three_files_are_enough_and_a_dispute_without_a_judge_is_merged_by_apply(classified):
    res = classified / ".work" / "results"
    for p in res.glob("*.final.json"):
        d = json.loads(p.read_text())
        clip = d["clip"]
        p.unlink()
        (res / f"{clip}.classify.json").write_text(json.dumps(d))
        for lens in ("visual", "content"):
            body = {"clip": clip, "agree": True, "issues": [], "corrections": {}, "confidence": 0.9}
            if clip == "C0003" and lens == "visual":
                body.update(agree=False, issues=["name is vague"], corrections={"name": "Ambient Hum In The Workshop Wide Shot", "subject_folder": "Not On The List"})
            (res / f"{clip}.{lens}.json").write_text(json.dumps(body))
    r = intake("apply", classified)
    assert r.returncode == 0 and "C0003 -> ['visual:name', 'visual:subject_folder']" in r.stdout and "merged 1 fallback finals" in r.stdout, ok(r)
    assert r.stdout.strip().splitlines()[-1] == "QC OK"
    assert (classified / "B-Roll" / "Other" / "C0003 - Ambient Hum In The Workshop Wide Shot.MP4").exists()


# ---------------------------------------------------------------- finding the shoot
def test_a_shoot_is_found_by_path_by_name_by_part_of_a_name_and_not_when_it_is_ambiguous(ws, started):
    a = intake("status", started)
    assert a.returncode == 0 and "Stage: prepped, B-roll folders not set" in a.stdout
    for ref in ("2026-10-31 Candle Workshop", "Candle Workshop", "candle", "2026-10-31"):
        assert intake("status", ref).returncode == 0, ref
    twin = ws.footage / "2026-11-02 Candle Workshop Two"
    shutil.copytree(started, twin)
    r = intake("status", "Candle Workshop")
    assert r.returncode == 2 and "matches 2 shoots" in r.stderr
    assert intake("status", "2026-10-31 Candle Workshop").returncode == 0                      # the whole name is exact, so it is not ambiguous
    r = intake("status", "nothing like it")
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: no shoot called 'nothing like it'")
    r = intake("status", ws.root)
    assert r.returncode == 2 and "is not a shoot folder: it has no job.json" in r.stderr


def test_a_shoot_folder_that_was_renamed_still_works_and_its_job_follows_it(ws, organized):
    moved = organized.parent / "2026-10-31 Renamed By Hand"
    organized.rename(moved)
    r = intake("status", moved)
    assert r.returncode == 0 and "Stage: organized" in r.stdout, ok(r)
    assert json.loads((moved / "job.json").read_text())["root"] == str(moved)               # job.json now names the folder it is in
    ev = json.loads((moved / ".work" / "evidence.json").read_text())
    assert all(e["sheet"].startswith(str(moved)) and os.path.exists(e["sheet"]) for e in ev)      # and so do the contact sheet paths the classifiers read
    assert intake("notes", moved).returncode == 0 and (moved / "START HERE.html").exists()
    assert intake("reverse", moved).returncode == 0 and (moved / "RAW" / "C0001.MP4").exists()


def test_working_on_a_copy_of_a_shoot_never_touches_the_original(ws, organized):
    twin = ws.footage / "2026-10-31 Candle Workshop Copy"
    shutil.copytree(organized, twin)
    before = sha_tree(organized)
    assert intake("reverse", twin).returncode == 0
    assert sha_tree(organized) == before and (organized / "Interview").exists()


# ---------------------------------------------------------------- status
def test_status_follows_the_shoot_from_start_to_the_end(classified):
    r = intake("status", classified)
    assert r.returncode == 0 and "Stage: ready to organize" in r.stdout and "Next: python3" in r.stdout and "apply" in r.stdout
    res = classified / ".work" / "results"
    (res / "C0003.final.json").rename(res / "C0003.classify.json")
    (res / "C0004.final.json").unlink()
    st = json.loads(intake("status", classified, "--json").stdout)
    assert st["stage"] == "classifying" and st["records_ready"] == 5 and st["records_unverified"] == ["C0003"] and st["records_missing"] == ["C0004"]
    assert "1 clip(s) have no record and 1 are unverified" in st["next"]
    plain = intake("status", classified).stdout
    assert "records: 5 of 7 ready; no record: C0004; unverified: C0003" in plain


def test_status_before_the_subjects_are_set(started):
    st = json.loads(intake("status", started, "--json").stdout)
    assert st["stage"] == "prepped, B-roll folders not set" and st["clips"] == 7 and st["prepped"] and st["subjects"] == [] and st["questions"] == 2 and st["raw_files"] == 9


def test_status_says_when_prep_never_finished(started):
    (started / ".work" / "evidence.json").unlink()
    st = json.loads(intake("status", started, "--json").stdout)
    assert st["stage"] == "started" and "prep did not finish" in st["next"] and "intake.py\" prep" in st["next"]


def test_prep_can_be_run_again_and_says_what_comes_next(started):
    (started / ".work" / "evidence.json").unlink()
    r = intake("prep", started)
    assert r.returncode == 0 and "[prep] done" in r.stdout and "NEXT: two steps for Claude" in r.stdout and (started / ".work" / "evidence.json").exists()


# ---------------------------------------------------------------- every stop is one plain line
@pytest.mark.parametrize("args, why", [
    (["apply", "no such shoot"], "no shoot called"),
    (["notes", "no such shoot"], "no shoot called"),
    (["reverse", "no such shoot"], "no shoot called"),
    (["status", "no such shoot"], "no shoot called"),
])
def test_an_unknown_shoot_is_one_plain_line(ws, args, why):
    r = intake(*args)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: ") and why in r.stderr and "Traceback" not in r.stderr and "\n" not in r.stderr.strip()


def test_apply_before_prep_is_one_plain_line(started):
    (started / ".work" / "evidence.json").unlink()
    r = intake("apply", started)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: prep has not run for this shoot") and "Traceback" not in r.stderr


def test_a_final_record_with_a_wrong_category_is_named_and_nothing_moves(classified):
    p = classified / ".work" / "results" / "C0001.final.json"
    d = json.loads(p.read_text())
    d["category"] = "interview_answer"
    p.write_text(json.dumps(d))
    r = intake("apply", classified)
    assert r.returncode == 2 and "RECORDS THAT CANNOT BE FILED" in r.stdout and "C0001: category 'interview_answer' is not one of" in r.stdout and "1 record(s) need a fix" in r.stderr
    assert (classified / "RAW" / "C0001.MP4").exists() and not (classified / "Footage Manifest.csv").exists()


def test_a_final_record_that_is_not_json_is_named_and_nothing_moves(classified):
    (classified / ".work" / "results" / "C0001.final.json").write_text("{this is not json")
    r = intake("apply", classified)
    assert r.returncode == 2 and "C0001.final.json is not valid JSON" in r.stderr and "Traceback" not in r.stderr
    assert (classified / "RAW" / "C0001.MP4").exists()


def test_an_unexpected_error_is_one_plain_line_and_the_debug_switch_shows_where(started, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("something odd")
    monkeypatch.setattr(intake_mod, "shoot_state", boom)
    with pytest.raises(SystemExit) as e:
        intake_mod.main(["status", str(started)])
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.strip().startswith("STOP: RuntimeError: something odd") and "Traceback" not in err
    monkeypatch.setenv("CUTROOM_DEBUG", "1")
    with pytest.raises(SystemExit):
        intake_mod.main(["status", str(started)])
    assert "Traceback" in capsys.readouterr().err


def test_ctrl_c_is_one_plain_line_and_exit_130(started, monkeypatch, capsys):
    monkeypatch.setattr(intake_mod, "shoot_state", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(SystemExit) as e:
        intake_mod.main(["status", str(started)])
    assert e.value.code == 130 and capsys.readouterr().err.strip().startswith("STOP: stopped by you")


def test_the_help_lists_every_command_and_needs_no_workspace():
    HOME.joinpath("config.json").write_text("{}")
    r = intake("--help")
    assert r.returncode == 0 and all(c in r.stdout for c in ("start", "prep", "subjects", "apply", "takes", "notes", "status", "reverse"))
    r = intake("start", "-h")
    assert r.returncode == 0 and "--questions" in r.stdout and "--type {interview,ad}" in r.stdout
    r = intake()
    assert r.returncode == 2 and "usage:" in r.stderr and "Traceback" not in r.stderr


def test_nothing_this_module_prints_has_an_em_or_en_dash(started, organized):
    for args in (["status", organized], ["prep", organized], ["subjects", organized, "--set", "A,B"]):
        r = intake(*args)
        assert "\u2014" not in r.stdout + r.stderr and "\u2013" not in r.stdout + r.stderr, args
