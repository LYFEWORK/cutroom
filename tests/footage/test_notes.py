"""notes.py: a plain-text notes file beside every video."""
import json
import os
import shutil
from pathlib import Path

import pytest

import job as jobmod
import notes
from conftest import intake, ok, run_tool, sha_tree
from test_takes_encode import run_takes, shoot  # noqa: F401 (shoot is a pytest fixture)


def write_notes(root, *args):
    return run_tool("notes.py", "--job", root / "job.json", *args)


def notes_of(root):
    """{relative video path: notes text} for every video that has a notes file beside it"""
    out = {}
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".work", "Edit")]
        for f in fs:
            if f.endswith(".txt") and (Path(d) / f).read_text(encoding="utf-8").startswith(jobmod.MARK):
                out[os.path.relpath(os.path.join(d, f), root)] = (Path(d) / f).read_text(encoding="utf-8")
    return out


def find(root, startswith):
    return next(p for p in root.rglob("*") if p.name.startswith(startswith) and p.suffix.lower() in (".mp4", ".mov"))


def test_every_video_gets_a_notes_file_beside_it_with_the_same_name(organized):
    r = write_notes(organized)
    assert r.returncode == 0 and r.stdout.strip() == "wrote 7 notes files (a .txt beside every video)", ok(r)
    got = notes_of(organized)
    videos = [p for p in organized.rglob("*") if p.suffix.lower() in (".mp4", ".mov") and ".work" not in p.parts]
    assert len(videos) == 7 and sorted(got) == sorted(os.path.relpath(p.with_suffix(".txt"), organized) for p in videos)
    assert all(t.splitlines()[0] == jobmod.MARK + os.path.basename(k)[:-4] + os.path.splitext([v for v in videos if v.with_suffix(".txt").name == os.path.basename(k)][0].name)[1] for k, t in got.items())


def test_an_answer_says_its_question_what_is_on_screen_and_what_is_said_with_times(organized):
    write_notes(organized)
    t = notes_of(organized)[os.path.relpath(find(organized, "C0001 - Q01").with_suffix(".txt"), organized)]
    lines = t.splitlines()
    assert lines[1].startswith("Candle Workshop · 2026-10-31 · runs 0:0")
    assert "QUESTION  Q01 · Why does your process take three days?" in lines
    assert any(l.startswith("ON SCREEN  A title card in blue") for l in lines) and "FLAGS  scripted_take" in lines
    assert "WHAT IS SAID  ·  times are m:ss in this file" in lines
    said = " ".join(l for l in lines[lines.index("WHAT IS SAID  ·  times are m:ss in this file") + 1:]).lower()
    assert "process" in said and "overnight" in said and lines[lines.index("WHAT IS SAID  ·  times are m:ss in this file") + 1].startswith("0:00  ")


def test_a_clip_with_no_speech_says_why(organized):
    write_notes(organized)
    got = notes_of(organized)
    silent = got[os.path.relpath(find(organized, "C0004").with_suffix(".txt"), organized)]
    noaudio = got[os.path.relpath(find(organized, "C0005").with_suffix(".txt"), organized)]
    ambient = got[os.path.relpath(find(organized, "C0003").with_suffix(".txt"), organized)]
    assert "NO SPEECH. The camera recorded no sound on this clip." in silent and "WHAT IS SAID" not in silent
    assert "NO SPEECH. The camera recorded no sound on this clip." in noaudio
    assert "NO SPEECH. Natural sound only." in ambient


def test_a_candid_chat_says_to_check_it_before_using_the_sound(organized):
    write_notes(organized)
    t = notes_of(organized)[os.path.relpath(find(organized, "C0006").with_suffix(".txt"), organized)]
    assert "WHAT IS SAID (candid talk, check it before using the sound)  ·  times are m:ss in this file" in t and "FLAGS  interviewer_audible" in t


def test_a_cut_take_is_retimed_to_the_cut_and_says_which_part_is_speech_and_which_is_handle(shoot):
    assert run_takes(shoot).returncode == 0
    assert write_notes(shoot).returncode == 0
    got = notes_of(shoot)
    clean = next(v for k, v in got.items() if "Take 3 - Clean" in k)
    assert "QUESTION  Q01 · Why does your process take three days?" in clean and "TAKE  3 · Clean" in clean
    assert " · cut from C0007 " in clean.splitlines()[1] and "SPOKEN  " in clean and "in this file. The rest is handle." in clean
    flub = next(v for k, v in got.items() if "Flub" in k)
    assert "TAKE  2 · Flub · stops after three words" in flub
    blooper = next(v for k, v in got.items() if "Blooper" in k)
    assert "BLOOPER  false start" in blooper
    # the words are in the cut's own time: the first word of the clean take is near 0:00, not near the 4 s it sits at inside the raw clip
    first = clean.split("WHAT IS SAID", 1)[1].splitlines()[1]
    assert first.startswith("0:0") and "process" in clean.lower()


def test_an_ad_shoot_calls_it_a_script_line(shoot):
    j = json.loads((shoot / "job.json").read_text())
    j["shoot_type"] = "ad"
    (shoot / "job.json").write_text(json.dumps(j))
    assert run_takes(shoot).returncode == 0 and write_notes(shoot).returncode == 0
    assert any("SCRIPT LINE  Q01 · " in v for v in notes_of(shoot).values()) and not any("QUESTION  Q01" in v for v in notes_of(shoot).values())


def test_a_rerun_changes_nothing_and_a_file_of_your_own_is_never_touched(organized):
    write_notes(organized)
    mine = organized / "Interview" / "Q01 Why Our Process Takes Three Days" / "my own notes.txt"
    mine.write_text("these are my words")
    before = notes_of(organized)
    assert write_notes(organized).returncode == 0
    assert notes_of(organized) == before and mine.read_text() == "these are my words"


def test_a_notes_file_whose_video_is_gone_is_removed_and_one_that_is_not_ours_stays(organized):
    write_notes(organized)
    video = find(organized, "C0003")
    txt = video.with_suffix(".txt")
    video.unlink()
    orphan_mine = organized / "B-Roll" / "Wax Station" / "someone's list.txt"
    orphan_mine.write_text("keep")
    assert write_notes(organized).returncode == 0
    assert not txt.exists() and orphan_mine.read_text() == "keep"


def test_it_refuses_to_rewrite_notes_when_the_scratch_folder_cannot_rebuild_them(organized):
    write_notes(organized)
    before = notes_of(organized)
    shutil.rmtree(organized / ".work")
    r = write_notes(organized)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: REFUSED, nothing was touched: the .work folder is gone") and "Traceback" not in r.stderr
    assert notes_of(organized) == before                        # not one file was rewritten as NO SPEECH
    (organized / ".work").mkdir()
    r = write_notes(organized)
    assert r.returncode == 2 and "has no evidence.json" in r.stderr


def test_a_video_neither_manifest_lists_gets_no_notes_and_is_named(organized):
    stray = organized / "Interview" / "unlisted video.mp4"
    shutil.copy(find(organized, "C0003"), stray)
    r = write_notes(organized)
    assert r.returncode == 0 and "[warn] no notes for 1 video(s) that neither manifest lists: Interview/unlisted video.mp4" in r.stdout
    assert not stray.with_suffix(".txt").exists()


def test_notes_are_not_written_for_the_work_folder_or_edit(organized):
    (organized / ".work" / "frames" / "x.mp4").parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(find(organized, "C0003"), organized / ".work" / "frames" / "x.mp4")
    assert write_notes(organized).returncode == 0
    assert not (organized / ".work" / "frames" / "x.txt").exists() and not list((organized / "Edit").glob("*.txt"))


def test_notes_come_before_the_shoot_is_organized_is_one_plain_line(classified):
    r = intake("notes", classified)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: notes come after the shoot is organized") and "Traceback" not in r.stderr


def test_srt_and_words_reading(tmp_path):
    scr = tmp_path
    (scr / "transcripts").mkdir()
    (scr / "words").mkdir()
    (scr / "transcripts" / "c.srt").write_text("1\n00:00:01,500 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:05,000\nThank you.\n\n3\n00:01:02,250 --> 00:01:03,000\n[MUSIC]\n\n4\n00:01:05,000 --> 00:01:06,000\nLast words here\n")
    assert notes.srt(str(scr), "c") == [(1.5, "Hello there."), (65.0, "Last words here")]       # filler and a sound label are not speech
    (scr / "words" / "c.txt").write_text("t=1.00 one\nt=1.30 two.\nt=1.40 three\nt=3.00 four\nt=3.10 five\n")
    got = notes.words(str(scr), "c", 0.5, 4.0)
    assert got == [(1.0, "one two."), (1.4, "three"), (3.0, "four five")]                          # a new line at a sentence end and at a pause
    assert notes.mss(65.9) == "1:05" and notes.mss(-3) == "0:00" and notes.secs("00:01:02,500") == 62.5
