"""face.py: the per-second face track (parse, nearest sample, interpolated zone), the face anchor and the room tier. The Apple helper is replaced by a
stub executable except in the one test that runs the real thing on a picture with no face in it."""
import json
import shutil
import subprocess
import sys

import pytest

import face
from helpers import sh, video
from helpers_misc import FACERECT_NO_FACE, FACERECT_ONE_FACE, run_engine, stop_lines, stub_exe

LINES = ["/tmp/s000.png\t100\t200\t120\t120", "/tmp/s001.png\t110\t210\t120\t120", "/tmp/s003.png\t0\t0\t0\t0"]


@pytest.fixture
def stub_helper(monkeypatch, tmp_path):
    """face.py asks swiftbin for the facerect binary: hand it a stub that prints `body`'s lines instead of calling Apple Vision"""
    def use(body):
        exe = stub_exe(tmp_path / "facerect_stub", body)
        monkeypatch.setattr(face.swiftbin, "ensure", lambda d: exe)
        return exe
    return use


# --- parse, median, nearest sample ---------------------------------------------------------------------------------------------------

def test_parse_scales_and_skips_empty():
    s = face.parse_facerect(LINES, 2.0)
    assert s[0] == {"cx": 320, "cy": 520, "w": 240, "h": 240}
    assert 3 not in s and 1 in s


def test_parse_skips_malformed_lines_and_the_no_face_line():
    s = face.parse_facerect(["/tmp/s000.png\tnone", "garbage", "/tmp/s002.png\t1\t2\t3\t4"], 1.0)
    assert list(s) == [2]


def test_median_and_nearest():
    s = face.parse_facerect(LINES, 2.0)
    med = face.median_box(s)
    assert med["cx"] == 330
    track = {"W": 1080, "H": 1920, "samples": {str(k): v for k, v in s.items()}, "median": med}
    assert face.at(track, 1.4)["cx"] == 340      # nearest = second 1 (its frame is at 1.5 s)
    assert face.at(track, 9.0) == med            # nothing near: the median


def test_at_reads_each_sample_at_the_moment_its_frame_was_taken():
    # Sample "s" is the frame extracted at s + 0.5. t=1.9 is 0.4 s from sample 1's frame (1.5 s) and 0.6 s from sample 2's (2.5 s): sample 1 wins. Read
    # at s instead of s + 0.5, sample 2 won (0.1 s away), half a second early: that put every box half a second early on a moving subject.
    track = {"W": 1080, "H": 1920,
             "samples": {"1": {"cx": 1, "cy": 1, "w": 1, "h": 1}, "2": {"cx": 2, "cy": 2, "w": 2, "h": 2}},
             "median": {"cx": 0, "cy": 0, "w": 0, "h": 0}}
    assert face.at(track, 1.9) == track["samples"]["1"]
    assert face.at(track, 2.1) == track["samples"]["2"]      # 0.4 s from 2.5, 0.6 s from 1.5
    assert face.at(track, 1.53) == track["samples"]["1"]


def test_at_falls_back_to_median_beyond_true_2s_window():
    # Only sample "0" exists (its frame at 0.5 s); t=2.99 is a 2.49 s gap, outside the documented 2 s window, even though floor-anchoring
    # (int(2.99) = 2, offsets -3..3) would have coincidentally landed on second 0.
    track = {"W": 1080, "H": 1920, "samples": {"0": {"cx": 9, "cy": 9, "w": 9, "h": 9}},
             "median": {"cx": 0, "cy": 0, "w": 0, "h": 0}}
    assert face.at(track, 2.99) == track["median"]


def test_median_box_with_no_face_is_a_marked_fallback_that_follows_the_canvas():
    assert face.median_box({}) == {"cx": 540, "cy": 864, "w": 432, "h": 432, "fallback": True}
    assert face.median_box({}, 608)["cy"] == 273


def test_piece_of_finds_the_piece_that_holds_an_output_time():
    pieces = [{"out_in": 0.0, "out_out": 1.0}, {"out_in": 1.0, "out_out": 3.0}]
    assert face.piece_of(pieces, 0.5) == 0 and face.piece_of(pieces, 1.001) == 1 and face.piece_of(pieces, 2.9) == 1 and face.piece_of(pieces, 9.0) is None
    assert face.piece_of(pieces, 1.0) == 0                   # a time exactly on a joint still belongs to the piece that ends there (a millionth of a second of slack)
    assert face.piece_of(None, 1.0) is None


# --- the canvas follows the picture's shape ------------------------------------------------------------------------------------------

def test_canvas_height_keeps_the_pictures_proportion_at_a_1080_wide_canvas():
    assert face.canvas_h(1080, 1920) == 1920 and face.canvas_h(2160, 3840) == 1920
    assert face.canvas_h(1920, 1080) == 608 and face.canvas_h(320, 180) == 608
    assert face.canvas_h(1080, 1440) == 1440


@pytest.mark.parametrize("size, H", [("180x320", 1920), ("320x180", 608), ("240x240", 1080)])
def test_the_track_records_a_canvas_as_tall_as_the_picture_is(stub_helper, tmp_path, size, H):
    stub_helper(FACERECT_ONE_FACE)
    t = face.build(video(tmp_path / "v.mp4", size=size, dur=2), str(tmp_path / "out.json"))
    assert t["W"] == 1080 and t["H"] == H
    assert json.load(open(tmp_path / "out.json"))["H"] == H


def test_build_writes_one_sample_a_second_in_the_notional_canvas(stub_helper, tmp_path):
    stub_helper(FACERECT_ONE_FACE)
    t = face.build(video(tmp_path / "v.mp4", size="180x320", dur=3), str(tmp_path / "out.json"))
    assert t["seconds"] == 4 and sorted(t["samples"]) == ["0", "1", "2"]
    assert t["samples"]["0"] == {"cx": 320, "cy": 520, "w": 240, "h": 240}         # (100 + 60) * 2 and (200 + 60) * 2: boxes are doubled
    assert t["median"] == {"cx": 320, "cy": 520, "w": 240, "h": 240}


# --- build() stops instead of looking like "no face" ---------------------------------------------------------------------------------

def test_build_stops_on_a_missing_video(stub_helper, tmp_path, capsys):
    # ffprobe exits 1 with empty stdout on a nonexistent file; build() must stop with a line, not raise a bare ValueError from float('').
    stub_helper(FACERECT_ONE_FACE)
    with pytest.raises(SystemExit) as e:
        face.build(str(tmp_path / "missing.mp4"), str(tmp_path / "out.json"))
    assert e.value.code == 2 and capsys.readouterr().err.startswith("STOP: ffprobe is down: could not read duration")


def test_build_stops_when_facerect_produces_no_output(stub_helper, tmp_path, capsys):
    # A total tracking failure (the binary crashed, every frame unreadable) must stop, not fall through to a silent median_box({}) default that looks
    # identical to an ordinary "no face visible" result.
    stub_helper("import sys\nsys.exit(0)")
    with pytest.raises(SystemExit):
        face.build(video(tmp_path / "clip.mp4", size="64x64", dur=1), str(tmp_path / "out.json"))
    assert capsys.readouterr().err.startswith("STOP: facerect is down: produced no output for any frame")


def test_build_stops_when_facerect_exits_with_an_error(stub_helper, tmp_path, capsys):
    stub_helper("import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)")
    with pytest.raises(SystemExit):
        face.build(video(tmp_path / "clip.mp4", size="64x64", dur=1), str(tmp_path / "out.json"))
    assert "facerect is down: exit 3: boom" in capsys.readouterr().err


def test_build_records_the_seconds_that_had_a_frame_when_the_audio_outlasts_the_picture(stub_helper, tmp_path):
    """23.43 s of picture in a 23.54 s file: the last second has no frame at its half-second, and the speech gate must not count it as a face miss"""
    clip = tmp_path / "clip.mp4"
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30:d=3.43", "-f", "lavfi", "-i", "sine=d=3.54",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)])
    assert r.returncode == 0, r.stderr
    stub_helper(FACERECT_ONE_FACE)
    t = face.build(str(clip), str(tmp_path / "out.json"))
    assert t["seconds"] == 4 and t["extracted"] == [0, 1, 2] and sorted(t["samples"]) == ["0", "1", "2"]


# --- zone() / over(): the face ESTIMATED between 1 Hz samples ----------------------------------------------------------------------------
A = {"cx": 400, "cy": 500, "w": 200, "h": 200}
B = {"cx": 600, "cy": 700, "w": 200, "h": 200}
TRACK = {"W": 1080, "H": 1920, "samples": {"0": A, "1": B, "2": B}, "median": B}


def test_zone_interpolates_between_the_two_bracketing_samples():
    assert face.zone(TRACK, 0.5) == A                                   # on sample 0's frame (0.5 s)
    assert face.zone(TRACK, 1.5) == B
    mid = face.zone(TRACK, 1.0)                                         # half way between the frames at 0.5 and 1.5
    assert (mid["cx"], mid["cy"]) == (500, 600)


def test_zone_grows_the_box_by_the_pad():
    z = face.zone(TRACK, 0.5, pad=0.1)
    assert (z["w"], z["h"]) == (240, 240) and (z["cx"], z["cy"]) == (400, 500)


def test_zone_does_not_interpolate_across_a_joint():
    """a joint is another moment of the take: the neighbour on the far side of it says nothing about this frame"""
    pieces = [{"out_in": 0.0, "out_out": 1.0}, {"out_in": 1.0, "out_out": 3.0}]
    assert face.zone(TRACK, 0.9, pieces) == A                           # nearest sample alone
    assert face.zone(TRACK, 1.9, pieces) == B


def test_zone_is_none_for_a_take_with_no_face():
    assert face.zone({"W": 1080, "H": 1920, "samples": {}, "median": {"cx": 540, "cy": 860, "w": 400, "h": 400, "fallback": True}}, 1.0) is None


def test_over_holds_the_face_for_a_whole_window():
    z = face.over(TRACK, 0.5, 1.5)
    assert z["cy"] == 600 and z["h"] == 400        # both ends: the box spans y 400..800
    assert face.over(TRACK, 0.6, 0.6)["cy"] == pytest.approx(520)


def test_over_is_none_when_the_take_has_no_face():
    assert face.over({"W": 1080, "H": 1920, "samples": {}, "median": {"cx": 1, "cy": 1, "w": 1, "h": 1, "fallback": True}}, 0.0, 2.0) is None


def test_over_clears_the_face_on_both_sides_of_a_joint_inside_the_window():
    """anything that stays up across a cut sees two different moments of the take, and has to clear the face in both"""
    hi = {"cx": 540, "cy": 600, "w": 200, "h": 200}
    lo = {"cx": 540, "cy": 900, "w": 200, "h": 200}
    track = {"W": 1080, "H": 1920, "samples": {"0": hi, "1": lo}, "median": hi}
    pieces = [{"out_in": 0.0, "out_out": 1.0}, {"out_in": 1.0, "out_out": 2.0}]
    z = face.over(track, 0.8, 1.2, pieces)
    assert z["cy"] + z["h"] / 2 >= 1000, z          # reaches down to the second piece's chin
    assert face.over(track, 0.5, 0.9, pieces)["cy"] + face.over(track, 0.5, 0.9, pieces)["h"] / 2 <= 700


# --- the face anchor ---------------------------------------------------------------------------------------------------------------------

def test_anchor_is_the_median_face_in_the_pictures_own_pixels(stub_helper, tmp_path):
    # every other frame has a face: 3 of 6 is enough
    stub_helper("import re, sys\nfor f in sys.argv[1:]:\n    i = int(re.search(r'f(\\d\\d)\\.png$', f).group(1))\n    print(f + ('\\t100\\t200\\t120\\t120' if i % 2 == 0 else '\\tnone'))\n")
    clip = video(tmp_path / "v.mp4", size="1080x1920", dur=2)
    a = face.anchor(clip, str(tmp_path / "anchor.json"), 6)
    assert a == {"cx": 320, "cy": 520, "w": 240, "h": 240, "hits": 3, "frames": 6, "fallback": False, "W": 1080, "H": 1920}
    assert json.load(open(tmp_path / "anchor.json")) == a


def test_anchor_scales_boxes_by_the_pictures_width(stub_helper, tmp_path):
    stub_helper(FACERECT_ONE_FACE)
    a = face.anchor(video(tmp_path / "v.mp4", size="540x960", dur=1), str(tmp_path / "anchor.json"), 4)
    assert (a["cx"], a["cy"], a["w"], a["h"]) == (160, 260, 120, 120) and (a["W"], a["H"]) == (540, 960)      # a 540-px picture is read at its own size


def test_anchor_with_fewer_than_three_hits_falls_back_to_a_marked_center(stub_helper, tmp_path):
    stub_helper("import re, sys\nfor f in sys.argv[1:]:\n    i = int(re.search(r'f(\\d\\d)\\.png$', f).group(1))\n    print(f + ('\\t100\\t200\\t120\\t120' if i == 0 else '\\tnone'))\n")
    a = face.anchor(video(tmp_path / "v.mp4", size="1080x1920", dur=1), str(tmp_path / "anchor.json"), 4)
    assert a["fallback"] is True and a["hits"] == 1 and a["frames"] == 4
    assert (a["cx"], a["cy"]) == (540, 768) and a["w"] == a["h"] == 324                  # (0.5 W, 0.40 H), a face a third of the width


def test_anchor_with_no_face_at_all_still_writes_the_fallback(stub_helper, tmp_path):
    stub_helper(FACERECT_NO_FACE)
    a = face.anchor(video(tmp_path / "v.mp4", size="180x320", dur=1), str(tmp_path / "anchor.json"))
    assert a["fallback"] is True and a["hits"] == 0 and a["frames"] == 24


def test_anchor_stops_when_there_is_no_picture(stub_helper, tmp_path, capsys):
    stub_helper(FACERECT_NO_FACE)
    wav = tmp_path / "a.wav"
    assert sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)]).returncode == 0
    with pytest.raises(SystemExit):
        face.anchor(str(wav), str(tmp_path / "anchor.json"))
    assert capsys.readouterr().err.startswith("STOP: no picture to find a face in")


# --- the room tier -----------------------------------------------------------------------------------------------------------------------


def track(cx, cy, w, h, fallback=False, H=1920):
    m = {"cx": cx, "cy": cy, "w": w, "h": h}
    if fallback:
        m["fallback"] = True
    return {"W": 1080, "H": H, "samples": {"0": m} if not fallback else {}, "median": m}


def test_full_when_room_above_and_below():
    t = face.tier_for(track(540, 1050, 400, 400))     # crown 730 (CROWN_H=0.30), chin 1250: above 460, below 170
    assert t["tier"] == "FULL" and t["room"]["above"] == 460 and t["room"]["below"] == 170


def test_room_above_is_measured_from_the_crown_not_the_brow():
    """Vision's face box starts at the brow, so the scalp counted as free room. A face with cy 1146 and h 552 reads 600 px above from the brow; from the
    crown (CROWN_H=0.30) it is 434, which clears the 420 px room a hero word needs, so the tier is FULL."""
    t = face.tier_for(track(523, 1146, 552, 552))
    assert t["room"]["above"] == 434 and t["room"]["below"] == -2
    assert t["tier"] == "FULL"


def test_a_hero_word_gates_on_room_above_alone():
    """the hero word lives ABOVE the head, so room below the chin must not veto it: 621 px above the crown and no room below is still FULL"""
    t = face.tier_for(track(540, 1218, 408, 408))     # crown 891.6 (CROWN_H=0.30), chin 1422
    assert t["room"]["above"] == 621 and t["room"]["below"] == -2
    assert t["tier"] == "FULL" and "621px above the crown" in t["reason"]


def test_reduced_when_only_room_below():
    t = face.tier_for(track(540, 520, 400, 400))      # head 320-720: above 50, below 700
    assert t["tier"] == "REDUCED"


def test_minimal_when_tight_or_no_face():
    assert face.tier_for(track(540, 1100, 900, 1100))["tier"] == "MINIMAL"   # head 550-1650
    assert face.tier_for(track(0, 0, 0, 0, fallback=True))["tier"] == "MINIMAL"


def test_tier_max_caps():
    assert face.tier_for(track(540, 900, 400, 400), tier_max="REDUCED")["tier"] == "REDUCED"      # this framing is REDUCED already: nothing to cap
    t = face.tier_for(track(540, 1050, 400, 400), tier_max="REDUCED")                             # a FULL framing, held down to REDUCED
    assert t["tier"] == "REDUCED" and "capped at REDUCED" in t["reason"]
    assert face.tier_for(track(540, 1050, 400, 400), tier_max="MINIMAL")["tier"] == "MINIMAL"


def test_the_tier_reports_the_safe_box_it_judged_by():
    assert face.tier_for(track(540, 1050, 400, 400))["safe"] == face.SAFE == {"x0": 65, "x1": 940, "y0": 270, "y1": 1420}


def test_the_safe_box_keeps_its_proportions_on_a_shorter_canvas():
    assert face.safe_box(1920) == face.SAFE
    s = face.safe_box(608)
    assert (s["y0"], s["y1"]) == (86, 450) and (s["x0"], s["x1"]) == (65, 940)


def test_a_landscape_track_is_judged_against_the_scaled_box():
    t = face.tier_for(track(540, 304, 100, 100, H=608))      # crown 224, chin 354: above 224 - 86, below 450 - 354
    assert t["room"]["above"] == 138 and t["room"]["below"] == 96 and t["safe"] == face.safe_box(608)
    assert t["tier"] == "MINIMAL"


# --- the command line ----------------------------------------------------------------------------------------------------------------------

def test_the_room_command_writes_the_tier_and_says_it_in_one_line(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps(track(540, 1050, 400, 400)))
    r = run_engine("face.py", "room", tmp_path / "t.json", tmp_path / "tier.json")
    assert r.returncode == 0 and r.stdout.startswith("FULL - 460px above the crown"), r.stdout + r.stderr
    assert json.load(open(tmp_path / "tier.json"))["tier"] == "FULL"
    r = run_engine("face.py", "room", tmp_path / "t.json", tmp_path / "tier2.json", "reduced")
    assert r.returncode == 0 and r.stdout.startswith("REDUCED - ") and "capped at REDUCED" in r.stdout


def test_the_room_command_refuses_a_tier_it_does_not_know(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps(track(540, 1050, 400, 400)))
    r = run_engine("face.py", "room", tmp_path / "t.json", tmp_path / "tier.json", "HUGE")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "unknown tier_max 'HUGE'" in r.stderr and not (tmp_path / "tier.json").exists()


def test_an_unknown_command_is_one_stop_line():
    r = run_engine("face.py", "wander")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "unknown command 'wander'" in r.stderr


# --- the real helper -------------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="Apple Vision helpers need macOS and swiftc")
def test_the_real_helper_runs_and_a_picture_with_no_face_gives_the_marked_fallback(tmp_path):
    """facerect is built on first use and run on real frames: a test pattern has no face, so every second is empty and the median is the fallback"""
    clip = video(tmp_path / "v.mp4", size="180x320", dur=2)
    r = run_engine("face.py", "track", clip, tmp_path / "t.json")
    assert r.returncode == 0 and r.stdout.startswith("face samples: 0/3 median"), r.stdout + r.stderr
    t = json.load(open(tmp_path / "t.json"))
    assert t["samples"] == {} and t["median"]["fallback"] is True and t["H"] == 1920 and t["extracted"] == [0, 1]
    r = run_engine("face.py", "anchor", clip, tmp_path / "a.json", "6")
    assert r.returncode == 0 and r.stdout.endswith(f"FALLBACK -> {tmp_path / 'a.json'}\n") and json.load(open(tmp_path / "a.json"))["hits"] == 0, r.stdout + r.stderr
