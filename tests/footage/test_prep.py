"""prep.py: probe, audio levels, whisper, frames, contact sheets, thumbnails and the evidence every later step reads."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import common
import prep
from conftest import HAVE_MEDIA, MODEL, intake, make_clip, need_media, ok, run_tool


# ---------------------------------------------------------------- the shape of a clip
@pytest.mark.parametrize("w, h, rot, shape, box", [
    (1080, 1920, 0, "vertical", (240, 426)),
    (1920, 1080, 0, "horizontal", (426, 240)),
    (1080, 1080, 0, "square", (320, 320)),
    (1000, 1040, 0, "square", (320, 320)),            # within 5 percent of square
    (1080, 1350, 0, "vertical", (240, 426)),          # 4:5 is vertical
    (3840, 2160, 90, "vertical", (240, 426)),         # coded landscape, displayed rotated: vertical
    (3840, 2160, -90, "vertical", (240, 426)),
    (3840, 2160, 270, "vertical", (240, 426)),
    (3840, 2160, 180, "horizontal", (426, 240)),      # upside down is still wide
    (2160, 3840, 90, "horizontal", (426, 240)),       # coded portrait, displayed rotated: horizontal
    (0, 0, 0, "vertical", (240, 426)),                # an unknown size counts as the shape phones shoot
])
def test_the_thumbnail_box_comes_from_the_size_and_the_rotation(w, h, rot, shape, box):
    assert prep.orientation(w, h, rot) == shape
    assert prep.thumb_box(w, h, rot) == box


def test_display_size_swaps_only_a_quarter_turn():
    assert prep.display_size(3840, 2160, 90) == (2160, 3840)
    assert prep.display_size(3840, 2160, 180) == (3840, 2160)
    assert prep.display_size(3840, 2160) == (3840, 2160)


# ---------------------------------------------------------------- contact sheets
def make_frames(d, n, size=(480, 853)):
    d.mkdir(parents=True)
    for i in range(n):
        Image.new("RGB", size, (i * 5 % 255, 80, 120)).save(d / f"{i:02d}_{i * 2 + 1:06.2f}s.jpg")
    return sorted(f for f in os.listdir(d) if f.endswith(".jpg"))


def test_a_sheet_of_tall_frames_stays_short_enough_to_be_read_and_keeps_every_frame_on_disk(tmp_path):
    scr = tmp_path / "scr"
    for sub in ("sheets", "thumbs"):
        (scr / sub).mkdir(parents=True)
    fr = make_frames(scr / "frames" / "clip", 40)
    m = {"width": 1080, "height": 1920, "rotation": 0}
    assert prep.make_sheet_and_thumb("clip", m, str(scr), fr, prep.font(22))
    w, h = Image.open(scr / "sheets" / "clip.jpg").size
    assert w == 4 * 480 and h <= prep.SHEET_MAX_H and h > 3000     # sampled, not shrunk
    assert len(os.listdir(scr / "frames" / "clip")) == 40
    assert Image.open(scr / "thumbs" / "clip.jpg").size[0] <= 240


def test_a_short_clip_gets_every_frame_on_its_sheet(tmp_path):
    scr = tmp_path / "scr"
    for sub in ("sheets", "thumbs"):
        (scr / sub).mkdir(parents=True)
    fr = make_frames(scr / "frames" / "clip", 6, (480, 270))
    prep.make_sheet_and_thumb("clip", {"width": 1920, "height": 1080, "rotation": 0}, str(scr), fr, prep.font(22))
    assert Image.open(scr / "sheets" / "clip.jpg").size == (4 * 480, 2 * 300)      # 6 tiles, 4 across
    t = Image.open(scr / "thumbs" / "clip.jpg").size
    assert t[0] > t[1] and t[0] <= 426 and t[1] <= 240                              # a wide clip gets a wide thumbnail


def test_a_long_clip_name_is_cut_short_on_the_tile_never_the_time():
    im = Image.new("RGB", (480, 30))
    d = ImageDraw.Draw(im)
    f = prep.font(22)
    label = prep.fit_label(d, "0025 - an extremely long clip name that cannot fit on one tile", "t=012.30s", f, 468)
    assert label.endswith("t=012.30s") and "~" in label and d.textlength(label, font=f) <= 468
    assert prep.fit_label(d, "C0001", "t=001.00s", f, 468) == "C0001  t=001.00s"


# ---------------------------------------------------------------- the run on a real card
def read_ev(shoot):
    return {e["clip"]: e for e in json.loads((shoot / ".work" / "evidence.json").read_text())}


def test_prep_measures_every_clip_and_writes_what_the_next_steps_read(started):
    scr = started / ".work"
    ev = read_ev(started)
    assert sorted(ev) == [f"C000{i}" for i in range(1, 8)]
    rows = [l.split("\t") for l in (scr / "probe.tsv").read_text().splitlines()]
    assert [r[0] for r in rows] == sorted(ev) and all(len(r) == 7 for r in rows)
    assert {r[0]: r[5] for r in rows}["C0006"] == ".MOV"
    for c, e in ev.items():
        assert (scr / "sheets" / f"{c}.jpg").exists() and (scr / "thumbs" / f"{c}.jpg").exists() and (scr / "ev" / f"{c}.json").exists()
        assert os.listdir(scr / "frames" / c) and e["duration_s"] > 1


def test_silent_and_no_audio_clips_are_flagged_and_never_transcribed(started):
    ev = read_ev(started)
    assert ev["C0004"]["digitally_silent"] and ev["C0004"]["max_db"] < -60 and ev["C0004"]["no_speech"] and ev["C0004"]["transcript"] == ""
    assert ev["C0005"]["digitally_silent"] and ev["C0005"]["no_speech"]                    # no audio stream at all
    assert not (started / ".work" / "transcripts" / "C0004.srt").exists() and not (started / ".work" / "transcripts" / "C0005.srt").exists()
    assert not (started / ".work" / "audio" / "C0005.wav").exists()                          # nothing to extract
    assert not ev["C0003"]["digitally_silent"] and ev["C0003"]["max_db"] > -60              # ambient sound is sound


def test_spoken_clips_come_back_with_their_words(started):
    ev = read_ev(started)
    assert "process" in ev["C0001"]["transcript"].lower() and "cure" in ev["C0001"]["transcript"].lower()
    assert "workshop" in ev["C0002"]["transcript"].lower()
    assert "wax" in ev["C0006"]["transcript"].lower()
    assert not ev["C0001"]["no_speech"]


@pytest.mark.parametrize("clip, shape, box", [("C0001", "vertical", (240, 426)), ("C0003", "horizontal", (426, 240)), ("C0006", "square", (320, 320))])
def test_every_thumbnail_is_fitted_to_the_clips_own_shape(started, clip, shape, box):
    assert read_ev(started)[clip]["orientation"] == shape
    w, h = Image.open(started / ".work" / "thumbs" / f"{clip}.jpg").size
    assert w <= box[0] and h <= box[1] and (w == box[0] or h == box[1])
    assert (w > h) == (shape == "horizontal") and (w == h) == (shape == "square")


def test_a_rerun_trusts_what_is_already_there_and_runs_no_whisper(started, capsys):
    srt = started / ".work" / "transcripts" / "C0001.srt"
    before = srt.stat().st_mtime_ns

    def facts(ev):   # what was measured; the sheet and frames paths follow the folder, which is a copy of the one prep first ran in
        return {c: {k: v for k, v in e.items() if k not in ("sheet", "frames_dir")} for c, e in ev.items()}
    ev1 = facts(read_ev(started))
    prep.main(["--job", str(started / "job.json")])
    assert srt.stat().st_mtime_ns == before and facts(read_ev(started)) == ev1
    assert read_ev(started)["C0001"]["sheet"] == str(started / ".work" / "sheets" / "C0001.jpg")
    out = capsys.readouterr().out
    assert "[whisper] C0001" not in out and "[whisper] done" in out


def test_no_half_written_file_is_left_by_a_run(started):
    leftovers = [f for d, _, fs in os.walk(started / ".work") for f in fs if ".part." in f]
    assert leftovers == []


# ---------------------------------------------------------------- what is wrong with a card
def raw_shoot(tmp_path, files, **job_over):
    shoot = tmp_path / "shoot"
    (shoot / "RAW").mkdir(parents=True)
    (shoot / "job.json").write_text(json.dumps({"name": "Test", "shoot_type": "interview", **job_over}))
    for name, fn in files.items():
        fn(shoot / "RAW" / name)
    return shoot


def run_prep(shoot, *args):
    return run_tool("prep.py", "--job", shoot / "job.json", "--skip-whisper", *args)


def test_a_video_file_ffprobe_cannot_read_is_named_and_left_alone(tmp_path):
    need_media()
    shoot = raw_shoot(tmp_path, {"good.mp4": lambda p: make_clip(p, audio="none"), "bad.mp4": lambda p: p.write_bytes(b"not a video at all" * 100)})
    r = run_prep(shoot)
    assert r.returncode == 0, ok(r)
    assert "ffprobe cannot read bad.mp4" in r.stdout and "[probe] 1 clips" in r.stdout
    assert (shoot / "RAW" / "bad.mp4").read_bytes() == b"not a video at all" * 100
    assert [l.split("\t")[0] for l in (shoot / ".work" / "probe.tsv").read_text().splitlines()] == ["good"]


def test_a_nested_folder_and_a_duplicate_name_are_warned_about(tmp_path):
    need_media()
    shoot = raw_shoot(tmp_path, {"A.mp4": lambda p: make_clip(p, audio="none"), "A.mov": lambda p: make_clip(p, audio="none"),
                                 "card/B.mp4": lambda p: (p.parent.mkdir(), make_clip(p, audio="none"))})
    r = run_prep(shoot)
    assert r.returncode == 0, ok(r)
    assert "1 video file(s) in the nested folder card" in r.stdout and "duplicate name A: A.mov and A.mp4" in r.stdout


def test_a_folder_with_no_readable_video_stops_with_one_line(tmp_path):
    need_media()
    shoot = raw_shoot(tmp_path, {"junk.mp4": lambda p: p.write_bytes(b"x" * 50)})
    r = run_prep(shoot)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: no video files ffprobe can read in") and "Traceback" not in r.stderr


def test_a_missing_source_folder_stops_with_one_line(tmp_path):
    need_media()
    shoot = raw_shoot(tmp_path, {})
    shutil.rmtree(shoot / "RAW")
    r = run_prep(shoot)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: source folder missing") and "Traceback" not in r.stderr


@pytest.mark.skipif(not shutil.which("whisper-cli"), reason="needs whisper-cli")
def test_a_missing_whisper_model_stops_before_any_clip_is_filed_as_silent(tmp_path):
    need_media()
    shoot = raw_shoot(tmp_path, {"a.mp4": lambda p: make_clip(p, audio="noise", seconds=2)}, whisper_model=str(tmp_path / "nope.bin"))
    r = run_tool("prep.py", "--job", shoot / "job.json")
    assert r.returncode == 2 and "STOP: whisper model missing" in r.stderr and "Traceback" not in r.stderr


def test_a_clip_no_frame_can_be_read_from_still_gets_a_sheet_and_a_thumbnail_and_a_warning(tmp_path, capsys):
    scr = tmp_path / "scr"
    for sub in ("sheets", "thumbs"):
        (scr / sub).mkdir(parents=True)
    m = {"width": 3840, "height": 2160, "rotation": 90}                                 # displayed vertical
    assert prep.make_sheet_and_thumb("clip", m, str(scr), [], prep.font(22)) is False
    assert Image.open(scr / "sheets" / "clip.jpg").size == (1920, 270) and Image.open(scr / "thumbs" / "clip.jpg").size == (240, 426)
    assert "[warn] no frame could be read from clip: it has a placeholder sheet and thumbnail" in capsys.readouterr().out


def test_sound_ffmpeg_cannot_decode_is_flagged_and_never_silently_filed_as_silent(tmp_path):
    need_media()
    scr = tmp_path / "scr"
    (scr / "audio").mkdir(parents=True)
    (scr / "audiostats").mkdir()
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"not media")
    m = {"path": str(bad), "has_audio": True}
    prep.audio_stats("x", m, str(scr))
    assert m["audio_unreadable"] is True and m["has_audio_stream"] is False and m["max_db"] == -91.0
    m2 = {"path": str(bad), "has_audio": False}
    prep.audio_stats("y", m2, str(scr))
    assert m2["audio_unreadable"] is False            # no stream at all is silence, not a decoding problem


def test_the_evidence_of_every_clip_says_whether_its_sound_could_be_read(started):
    ev = read_ev(started)
    assert all(e["audio_unreadable"] is False for e in ev.values())
