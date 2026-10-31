"""sidecars.py: every finished video ships with "<stem> - Transcript.md" and "<stem> - Caption.txt". The --check gate must look at something, and the
transcript must always have words."""
import json
import os

import pytest

import sidecars
from helpers import sh, speak, video
from helpers_misc import run_engine, speech, stop_lines


def touch(p, text="x"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
    return p


def sidecar_pair(vid, words=5, caption="a real caption"):
    stem = os.path.splitext(vid)[0]
    touch(stem + " - Transcript.md", f"# t: Transcript\n\n0:30 · {words} words · v.mp4\n")
    touch(stem + " - Caption.txt", caption)


def run_check(folder, cwd=None):
    return run_engine("sidecars.py", "--check", folder, cwd=cwd)


# --- --check ---------------------------------------------------------------------------------------------------------------------------

def test_check_stops_on_a_missing_folder(tmp_path):
    r = run_check(tmp_path / "nope")
    assert r.returncode == 2 and r.stderr.startswith("STOP: sidecars check: no such folder") and len(stop_lines(r)) == 1, r.stdout + r.stderr


def test_check_stops_when_the_folder_holds_no_finished_video(tmp_path):
    """a vacuous PASS (exit 0 over nothing) is what lets an empty or wrong folder look delivered"""
    touch(str(tmp_path / "notes.txt"))
    r = run_check(tmp_path)
    assert r.returncode == 2 and "holds no finished video" in r.stderr, r.stdout + r.stderr


def test_check_stops_when_every_video_it_finds_is_one_it_skips(tmp_path):
    touch(str(tmp_path / "B-Roll" / "clip.mp4"))
    touch(str(tmp_path / "take-cut.mp4"))
    r = run_check(tmp_path)
    assert r.returncode == 2 and "holds no finished video" in r.stderr, r.stdout + r.stderr


def test_check_skips_working_copies_broll_qc_evidence_build_and_symlinks_case_insensitively(tmp_path):
    reel = touch(str(tmp_path / "take-cut-master.mp4"))
    sidecar_pair(reel)
    for rel in ("take-cut.mp4", "take-cut.rawaudio.mov", "qc/take.mp4", "B-ROLL/clip.mp4", "b-roll/clip2.mp4", "BRoll/x.mov", "build/preview.mp4", "Qc/x.mp4",
                "Evidence/strip.mp4", "EVIDENCE/deep/er/clip.mov"):
        touch(str(tmp_path / rel))
    os.symlink(reel, tmp_path / "link.mp4")
    assert sidecars.deliverables(str(tmp_path)) == [reel]                    # the finished master counts; the cutter's own -cut.mp4 does not
    r = run_check(tmp_path)
    assert r.returncode == 0 and "PASS" in r.stdout and "(1 videos)" in r.stdout, r.stdout + r.stderr


def test_deliverables_are_sorted_and_only_mp4_and_mov(tmp_path):
    for rel in ("b.mp4", "a.MOV", "sub/c.mp4", "d.mkv", "e.txt"):
        touch(str(tmp_path / rel))
    assert sidecars.deliverables(str(tmp_path)) == [str(tmp_path / "a.MOV"), str(tmp_path / "b.mp4"), str(tmp_path / "sub" / "c.mp4")]


def test_check_uses_the_path_relative_to_the_folder_not_the_absolute_path(tmp_path):
    """a finished video under a parent folder that happens to be called qc or build must not be skipped just because of where the folder lives"""
    folder = tmp_path / "build" / "Finals"
    v = touch(str(folder / "reel.mp4"))
    assert sidecars.deliverables(str(folder)) == [v]
    r = run_check(folder)
    assert r.returncode == 1 and "NO-TRANSCRIPT" in r.stdout and "NO-CAPTION" in r.stdout, r.stdout


def test_check_survives_glob_metacharacters_in_the_folder_name(tmp_path):
    folder = tmp_path / "Clips [takes] *final*"
    v = touch(str(folder / "reel.mp4"))
    sidecar_pair(v)
    r = run_check(folder)
    assert r.returncode == 0 and "(1 videos)" in r.stdout, r.stdout + r.stderr


def test_check_fails_an_empty_transcript_and_a_pending_caption(tmp_path):
    a = touch(str(tmp_path / "a.mp4"))
    sidecar_pair(a, words=0)
    b = touch(str(tmp_path / "b.mp4"))
    sidecar_pair(b, caption="CAPTION NEEDED for b")
    r = run_check(tmp_path)
    assert r.returncode == 1
    assert f"EMPTY-TRANSCRIPT {tmp_path / 'a - Transcript.md'}" in r.stdout and f"CAPTION-NEEDED {tmp_path / 'b - Caption.txt'}" in r.stdout, r.stdout
    assert r.stdout.strip().splitlines()[-1] == f"sidecars check {tmp_path}: 2 missing (2 videos)"


def test_every_finished_video_needs_both_sidecars_wherever_its_folder_is_called(tmp_path):
    """there is no folder whose videos need only a caption: a video with one sidecar is reported for the other"""
    only_caption = touch(str(tmp_path / "Ready To Post" / "Title.mp4"))
    touch(str(tmp_path / "Ready To Post" / "Title - Caption.txt"), "caption")
    only_transcript = touch(str(tmp_path / "Other" / "Clip.mp4"))
    touch(str(tmp_path / "Other" / "Clip - Transcript.md"), "# c\n\n0:05 · 4 words · Clip.mp4\n")
    r = run_check(tmp_path)
    assert r.returncode == 1
    assert f"NO-TRANSCRIPT {only_caption}" in r.stdout and f"NO-CAPTION {only_transcript}" in r.stdout and "CAPTION-NEEDED" not in r.stdout, r.stdout


def test_check_treats_the_folder_the_same_given_absolute_relative_or_as_an_ancestor(tmp_path):
    folder = tmp_path / "Finals"
    v = touch(str(folder / "Title.mp4"))
    sidecar_pair(v)
    for arg, cwd in ((str(folder), tmp_path), ("Finals", tmp_path), (".", folder), (str(tmp_path), tmp_path)):
        r = run_check(arg, cwd=cwd)
        assert r.returncode == 0 and "(1 videos)" in r.stdout, (arg, r.stdout + r.stderr)


def test_check_in_process_returns_the_exit_code_and_prints_one_line_a_problem(tmp_path, capsys):
    a = touch(str(tmp_path / "a.mp4"))
    sidecar_pair(a)
    assert sidecars.check(str(tmp_path)) == 0
    assert capsys.readouterr().out == f"sidecars check {tmp_path}: PASS (1 videos)\n"
    touch(str(tmp_path / "b.mp4"))
    assert sidecars.check(str(tmp_path)) == 1
    assert capsys.readouterr().out.splitlines() == [f"NO-TRANSCRIPT {tmp_path / 'b.mp4'}", f"NO-CAPTION {tmp_path / 'b.mp4'}", f"sidecars check {tmp_path}: 2 missing (2 videos)"]


# --- write -------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def clip(tmp_path):
    return video(tmp_path / "clip.mp4", dur=2)


def test_a_words_file_that_parses_to_nothing_falls_back_to_the_audio(tmp_path, monkeypatch):
    """an empty words file used to produce an empty Transcript.md and a green --check"""
    v = touch(str(tmp_path / "v.mp4"))
    w = touch(str(tmp_path / "words.json"), '{"words": []}')
    seen = []
    monkeypatch.setattr(sidecars, "whisper_segments", lambda vid: seen.append(vid) or [("Hello there friends.", 0.0, 2.0)])
    monkeypatch.setattr(sidecars, "duration", lambda vid: 2.0)
    sidecars.write(v, words=w, caption_text="caption")
    assert seen == [os.path.abspath(v)]
    assert "3 words" in open(str(tmp_path / "v - Transcript.md")).read()


def test_the_transcript_is_the_given_words_in_timestamped_paragraphs(clip, tmp_path, capsys):
    w = touch(str(tmp_path / "words.json"), json.dumps({"words": [
        {"text": "hello", "start": 0.0, "end": 0.4}, {"text": "there.", "start": 0.5, "end": 0.9},
        {"text": "after", "start": 3.0, "end": 3.3}, {"text": "a", "start": 3.4, "end": 3.5}, {"text": "pause.", "start": 3.6, "end": 4.0}]}))
    state = sidecars.write(clip, words=w, caption_text="the caption")
    assert state == "written"
    tx = open(tmp_path / "clip - Transcript.md", encoding="utf-8").read().splitlines()
    assert tx[0] == "# clip: Transcript" and tx[1] == ""
    assert tx[2] == "0:02 · 5 words · clip.mp4"
    assert tx[4:] == ["**[0:00]** Hello there.", "", "**[0:03]** After a pause."]
    assert open(tmp_path / "clip - Caption.txt").read() == "the caption\n"
    assert capsys.readouterr().out == "sidecars clip.mp4: transcript 5 words, caption written\n"


def test_the_title_names_the_transcript(clip, tmp_path):
    w = touch(str(tmp_path / "w.json"), json.dumps([{"word": "one", "start_s": 0.0, "end_s": 0.2}]))
    sidecars.write(clip, words=w, caption_text="c", title="My Title")
    assert open(tmp_path / "clip - Transcript.md").read().startswith("# My Title: Transcript\n")


def test_no_caption_writes_a_placeholder_and_says_so_and_a_later_run_replaces_it(clip, tmp_path, capsys):
    w = touch(str(tmp_path / "w.json"), json.dumps({"words": [{"text": "hi", "start": 0.0, "end": 0.2}]}))
    assert sidecars.write(clip, words=w) == "pending"
    out = capsys.readouterr().out.splitlines()
    cap = str(tmp_path / "clip - Caption.txt")
    assert out[0] == f"CAPTION-NEEDED {cap}" and out[1].endswith("caption pending")
    text = open(cap).read()
    assert text.startswith("CAPTION NEEDED for clip: ") and "Transcript: clip - Transcript.md" in text
    assert sidecars.write(clip, words=w) == "pending"                    # a placeholder is never "kept"
    assert sidecars.write(clip, words=w, caption_text="  the real one  ") == "written"
    assert open(cap).read() == "the real one\n"


def test_a_caption_already_written_is_kept(clip, tmp_path):
    w = touch(str(tmp_path / "w.json"), json.dumps({"words": [{"text": "hi", "start": 0.0, "end": 0.2}]}))
    sidecars.write(clip, words=w, caption_text="mine, with an emoji \U0001F600")
    assert sidecars.write(clip, words=w) == "kept"
    assert open(tmp_path / "clip - Caption.txt", encoding="utf-8").read() == "mine, with an emoji \U0001F600\n"


def test_a_caption_file_and_a_blank_caption_text(clip, tmp_path):
    w = touch(str(tmp_path / "w.json"), json.dumps({"words": [{"text": "hi", "start": 0.0, "end": 0.2}]}))
    c = touch(str(tmp_path / "caption.md"), "\nfrom a file\n\n")
    assert sidecars.write(clip, words=w, caption=c) == "written"
    assert open(tmp_path / "clip - Caption.txt").read() == "from a file\n"
    assert sidecars.write(clip, words=w, caption_text="   ") == "kept"             # blank text is no caption: the one on disk stays


def test_write_stops_on_a_missing_video(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        sidecars.write(str(tmp_path / "gone.mp4"), words=None)
    assert e.value.code == 2 and capsys.readouterr().err.startswith("STOP: sidecars: no video")


def test_nothing_the_engine_writes_here_has_an_em_dash(clip, tmp_path):
    w = touch(str(tmp_path / "w.json"), json.dumps({"words": [{"text": "hi", "start": 0.0, "end": 0.2}]}))
    sidecars.write(clip, words=w)
    for f in ("clip - Transcript.md", "clip - Caption.txt"):
        assert chr(0x2014) not in open(tmp_path / f, encoding="utf-8").read()          # no em dash


# --- the pieces ------------------------------------------------------------------------------------------------------------------------

def test_load_words_reads_every_shape_the_engine_writes(tmp_path):
    shapes = {
        "dict": {"words": [{"text": "a", "start": 1, "end": 2}]},
        "list": [{"text": "a", "start": 1, "end": 2}],
        "word_keys": [{"word": "a", "start": 1, "end": 2}],
        "seconds_keys": [{"text": "a", "start_s": 1, "end_s": 2}],
    }
    for name, doc in shapes.items():
        assert sidecars.load_words(touch(str(tmp_path / f"{name}.json"), json.dumps(doc))) == [("a", 1.0, 2.0)], name
    odd = touch(str(tmp_path / "odd.json"), json.dumps([{"text": "  b  ", "start": 3}, {"text": "", "start": 4}, {"text": "c"}, {"word": "d", "start": 5, "end": 6}]))
    assert sidecars.load_words(odd) == [("b", 3.0, 3.0), ("d", 5.0, 6.0)]        # no end: the start; no text or no start: skipped


def test_stamp_is_minutes_and_seconds():
    assert sidecars.stamp(0) == "0:00" and sidecars.stamp(75.9) == "1:15" and sidecars.stamp(600) == "10:00"


def test_a_pause_over_1_2_seconds_starts_a_new_paragraph():
    units = [("one", 0.0, 0.5), ("two", 0.6, 1.0), ("three", 2.3, 2.6), ("four", 2.7, 3.0)]
    assert sidecars.paragraphs(units) == [(0.0, "One two"), (2.3, "Three four")]
    assert sidecars.paragraphs([("a", 0.0, 0.5), ("b", 1.7, 2.0)]) == [(0.0, "A b")]            # exactly 1.2 s of gap is not over 1.2 s


def test_a_sentence_end_closes_the_paragraph_once_twelve_seconds_have_run():
    # one word a second with no pause worth the name; sentences end at w5 (5 s in) and w12 (12 s in)
    units = [(f"w{i}" + ("." if i in (5, 12) else ""), float(i), i + 0.9) for i in range(14)]
    paras = sidecars.paragraphs(units)
    assert [p[0] for p in paras] == [0.0, 13.0]                                  # "w5." did not close it: only 5 s had run. "w12." did
    assert paras[0][1] == "W0 w1 w2 w3 w4 w5. W6 w7 w8 w9 w10 w11 w12." and paras[1][1] == "W13"


def test_paragraphs_capitalize_the_start_of_every_sentence_and_squeeze_spaces():
    out = sidecars.paragraphs([("so  it", 0.0, 0.5), ("works.", 0.6, 1.0), ("then", 1.1, 1.4), ("again?", 1.5, 2.0), ("yes.", 2.1, 2.4)])
    assert out == [(0.0, "So it works. Then again? Yes.")]


def test_transcript_words_reads_the_count_from_the_header(tmp_path):
    p = touch(str(tmp_path / "t.md"), "# t: Transcript\n\n0:10 · 42 words · v.mp4\n")
    assert sidecars.transcript_words(p) == 42
    assert sidecars.transcript_words(touch(str(tmp_path / "u.md"), "# nothing here\n")) == 0


# --- the command line ------------------------------------------------------------------------------------------------------------------

def test_the_command_line_writes_both_files_then_check_passes(clip, tmp_path):
    w = touch(str(tmp_path / "w.json"), json.dumps({"words": [{"text": "hello", "start": 0.0, "end": 0.4}, {"text": "world.", "start": 0.5, "end": 0.9}]}))
    r = run_engine("sidecars.py", clip, "--words", w, "--caption-text", "A caption.", "--title", "Hello")
    assert r.returncode == 0 and r.stdout.strip() == "sidecars clip.mp4: transcript 2 words, caption written", r.stdout + r.stderr
    assert open(tmp_path / "clip - Transcript.md").read().startswith("# Hello: Transcript\n")
    assert run_check(tmp_path).returncode == 0


def test_the_command_line_leaves_a_pending_caption_for_the_check_to_find(clip, tmp_path):
    w = touch(str(tmp_path / "w.json"), json.dumps({"words": [{"text": "hello", "start": 0.0, "end": 0.4}]}))
    r = run_engine("sidecars.py", clip, "--words", w)
    assert r.returncode == 0 and r.stdout.splitlines()[0] == f"CAPTION-NEEDED {tmp_path / 'clip - Caption.txt'}", r.stdout + r.stderr
    c = run_check(tmp_path)
    assert c.returncode == 1 and "CAPTION-NEEDED" in c.stdout


def test_the_command_line_stops_on_a_missing_video_and_on_a_missing_option_value(tmp_path):
    r = run_engine("sidecars.py", tmp_path / "gone.mp4", "--caption-text", "x")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "sidecars: no video" in r.stderr
    r = run_engine("sidecars.py", tmp_path / "gone.mp4", "--words")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and r.stderr.startswith("STOP: usage: sidecars.py")


def test_a_words_file_that_is_not_json_is_one_stop_line(clip, tmp_path):
    bad = touch(str(tmp_path / "bad.json"), "not json")
    r = run_engine("sidecars.py", clip, "--words", bad)
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "bad input" in r.stderr and "Traceback" not in r.stderr


# --- no words file: the file's own audio ---------------------------------------------------------------------------------------------

@speech
def test_without_a_words_file_the_files_own_audio_is_transcribed(tmp_path):
    wav = speak(tmp_path / "say.wav", "Hello there. This is a short test of the sidecars.")
    v = tmp_path / "spoken.mp4"
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=6", "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(v)])
    assert r.returncode == 0, r.stderr
    assert sidecars.write(str(v), caption_text="c") == "written"
    tx = open(tmp_path / "spoken - Transcript.md", encoding="utf-8").read().lower()
    assert tx.startswith("# spoken: transcript") and "hello there" in tx and "sidecars" in tx
    n = sidecars.transcript_words(str(tmp_path / "spoken - Transcript.md"))
    assert n >= 8
