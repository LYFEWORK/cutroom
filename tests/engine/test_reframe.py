"""reframe.py: the geometry and guards of the face-centerd 9:16 crop, then the whole crop on a synthetic landscape clip with the Apple helper stubbed."""
import json
import os
import shutil
import sys

import pytest

import face
import reframe
from helpers import sh, video
from helpers_misc import FACERECT_NO_FACE, FACERECT_ONE_FACE, run_engine, stop_lines, stub_exe


@pytest.fixture
def stub_helper(monkeypatch, tmp_path):
    def use(body):
        exe = stub_exe(tmp_path / "facerect_stub", body)
        monkeypatch.setattr(face.swiftbin, "ensure", lambda d: exe)
        return exe
    return use


def stops(capsys, fn, *args):
    """run fn(*args), which must stop; returns the STOP line it printed"""
    with pytest.raises(SystemExit) as e:
        fn(*args)
    assert e.value.code == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("STOP: ") and err.count("STOP:") == 1        # one STOP line (a tool's own multi-line stderr may follow it: see the report)
    return err


# --- the pure geometry and guards ---------------------------------------------------------------------------------------------------

def test_crop_geometry_centers_on_face():
    # 4K source, face near the right-center: crop width = round(2160*9/16) = 1215 -> even 1216, height = full source height, x centers the crop on face_cx.
    geo = reframe.crop_geometry(3840, 2160, 1900)
    assert geo == {"w": 1216, "h": 2160, "x": 1292}


def test_crop_geometry_clamps_to_left_edge():
    geo = reframe.crop_geometry(3840, 2160, 100)
    assert geo["x"] == 0
    assert geo["w"] == 1216 and geo["h"] == 2160


def test_crop_geometry_clamps_to_right_edge():
    geo = reframe.crop_geometry(3840, 2160, 3800)
    assert geo["x"] == 3840 - 1216 == 2624


def test_the_crop_width_is_always_even():
    for h in (360, 540, 720, 1080, 2160):
        assert reframe.crop_geometry(16 * h // 9, h, 100)["w"] % 2 == 0


def test_portrait_source_stops(capsys):
    assert "already portrait" in stops(capsys, reframe.ensure_landscape, 1080, 1920)


def test_square_source_counts_as_portrait(capsys):
    # height >= width: a square frame is not a landscape talking-head source.
    assert "already portrait" in stops(capsys, reframe.ensure_landscape, 1080, 1080)


def test_landscape_source_passes_guard():
    reframe.ensure_landscape(3840, 2160)  # must not stop


def test_rotated_dims_swaps_on_quarter_turn():
    # A clip stored 3840x2160 but Display-Matrix-tagged rotate=90 is DISPLAYED (and is what ffmpeg's -vf filters actually receive) as 2160x3840: a
    # portrait frame, not landscape.
    assert reframe.rotated_dims(3840, 2160, 90) == (2160, 3840)
    assert reframe.rotated_dims(3840, 2160, -90) == (2160, 3840)
    assert reframe.rotated_dims(3840, 2160, 270) == (2160, 3840)


def test_rotated_dims_leaves_upright_or_half_turn_alone():
    assert reframe.rotated_dims(3840, 2160, 0) == (3840, 2160)
    assert reframe.rotated_dims(3840, 2160, 180) == (3840, 2160)


def test_face_cx_in_source_scales_the_median_fraction():
    # face.build() reports median.cx against its own fixed 1080 px notional canvas (see reframe.py's module docstring): this is the fraction *
    # source-width conversion.
    track = {"samples": {"1": {"cx": 540, "cy": 1, "w": 1, "h": 1}}, "median": {"cx": 540}}
    assert reframe.face_cx_in_source(track, 3840) == 1920  # 540/1080 == 0.5 of source width


def test_face_cx_in_source_stops_when_no_samples(capsys):
    track = {"samples": {}, "median": {"cx": 540, "cy": 864, "w": 432, "h": 432, "fallback": True}}
    assert "no face found" in stops(capsys, reframe.face_cx_in_source, track, 3840)


# --- on real files ---------------------------------------------------------------------------------------------------------------------

def test_probe_reads_the_displayed_size_so_a_rotate_tagged_file_is_portrait(tmp_path, capsys):
    plain = video(tmp_path / "plain.mp4", size="640x360", dur=2)
    assert reframe.probe(plain) == (640, 360, "30/1", 2.0)
    rotated = str(tmp_path / "rot.mp4")
    assert sh(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", plain, "-c", "copy", rotated]).returncode == 0
    w, h, fps, dur = reframe.probe(rotated)
    assert (w, h) == (360, 640) and fps == "30/1"                          # stored 640x360, displayed 360x640
    assert "already portrait" in stops(capsys, reframe.ensure_landscape, w, h)


def test_probe_stops_on_a_file_ffprobe_cannot_read(tmp_path, capsys):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert "ffprobe is down: could not probe" in stops(capsys, reframe.probe, str(bad))


def test_the_proxy_is_small_has_no_audio_and_keeps_the_aspect_ratio(tmp_path):
    src = video(tmp_path / "land.mp4", size="640x360", dur=1)
    proxy = str(tmp_path / "proxy.mp4")
    reframe.build_proxy(src, proxy, 180)
    out = sh(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "csv=p=0", proxy]).stdout.split()
    assert out == ["video,320,180"]


def test_the_crop_is_1080_by_1920_with_the_audio_kept(tmp_path):
    src = video(tmp_path / "land.mp4", size="640x360", dur=1)
    out = str(tmp_path / "out.mp4")
    reframe.run_ffmpeg_crop(src, out, 202, 360, 89, "30/1")
    s = sh(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "csv=p=0", out]).stdout.split()
    assert s == ["video,1080,1920", "audio"]


def test_a_crop_that_ffmpeg_refuses_is_one_stop_line(tmp_path, capsys):
    src = video(tmp_path / "land.mp4", size="640x360", dur=1)
    assert "ffmpeg is down: failed cropping" in stops(capsys, reframe.run_ffmpeg_crop, src, str(tmp_path / "out.mp4"), 900, 360, 0, "30/1")      # wider than the frame


def test_reframe_crops_a_landscape_clip_around_the_face_and_says_what_it_did(stub_helper, tmp_path, capsys):
    stub_helper(FACERECT_ONE_FACE)                  # a face at x 100..220 of a 540-px-wide frame: cx 160, which is 320 of the 1080 canvas
    src = video(tmp_path / "land.mp4", size="640x360", dur=2)
    out = str(tmp_path / "out.mp4")
    side = reframe.reframe(src, out)
    assert side == {"src": src, "crop": {"w": 202, "h": 360, "x": 89}, "face_cx": 190, "proxy_h": 540, "fps": "30/1"}      # 320/1080 of 640 px = 190
    assert json.load(open(out + ".reframe.json")) == side
    s = sh(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,pix_fmt", "-of", "csv=p=0", out]).stdout.split()
    assert s == ["h264,1080,1920,yuv420p", "aac"]
    assert capsys.readouterr().out.strip() == "reframe: 640x360 -> crop 202x360 at x=89 (face cx 190) -> 1080x1920"


def test_the_crop_follows_the_face_to_the_other_side_of_the_frame(stub_helper, tmp_path):
    stub_helper("import sys\nfor f in sys.argv[1:]:\n    print(f + '\\t480\\t100\\t60\\t60')\n")      # cx 510 of 540: 1020 of 1080, 0.944 of the width
    side = reframe.reframe(video(tmp_path / "land.mp4", size="640x360", dur=1), str(tmp_path / "out.mp4"))
    assert side["face_cx"] == 604 and side["crop"]["x"] == 640 - 202           # the crop would run past the edge: clamped to it


def test_reframe_stops_on_a_portrait_source_and_on_a_face_less_one(stub_helper, tmp_path, capsys):
    stub_helper(FACERECT_ONE_FACE)
    assert "already portrait" in stops(capsys, reframe.reframe, video(tmp_path / "up.mp4", size="360x640", dur=1), str(tmp_path / "o1.mp4"))
    assert not (tmp_path / "o1.mp4").exists()
    stub_helper(FACERECT_NO_FACE)
    assert "no face found" in stops(capsys, reframe.reframe, video(tmp_path / "land.mp4", size="640x360", dur=1), str(tmp_path / "o2.mp4"))
    assert not (tmp_path / "o2.mp4").exists()


def test_the_proxy_height_flag_reaches_the_sidecar(stub_helper, tmp_path, monkeypatch):
    stub_helper(FACERECT_ONE_FACE)
    src = video(tmp_path / "land.mp4", size="640x360", dur=1)
    out = str(tmp_path / "out.mp4")
    monkeypatch.setattr(sys, "argv", ["reframe.py", src, out, "--proxy-h", "360"])
    reframe._main()
    assert json.load(open(out + ".reframe.json"))["proxy_h"] == 360


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="Apple Vision helpers need macOS and swiftc")
def test_the_command_line_stops_with_one_line_when_the_real_helper_finds_no_face(tmp_path):
    r = run_engine("reframe.py", video(tmp_path / "land.mp4", size="640x360", dur=1), tmp_path / "out.mp4")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "no face found" in r.stderr and not os.path.exists(tmp_path / "out.mp4"), r.stdout + r.stderr
