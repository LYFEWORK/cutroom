"""intake.py is the door every take walks through first: iCloud-dataless refusal, decode verification, a physical (never symlinked) copy, and the intake.json shape conform.sh
and every later stage read output_size/channels/source from. Pure logic is unit-tested directly; the CLI entry points are exercised end to end against tiny
ffmpeg-generated fixtures so a real STOP: exit-2 and a real intake.json are both proven, not just the functions that produce their pieces.

An HDR clip (HLG or PQ) is accepted only when the converter, engine/hdr.py, is in the install; without it the clip stops with the SDR instruction."""
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

import cap_helpers
import intake
import keys

CAPTIONS = os.path.join(cap_helpers.REPO, "engine", "captions")
SCRIPT = os.path.join(CAPTIONS, "intake.py")


def make_video(path, width=216, height=384, duration=0.5, audio=True, channels=2, vf=None, rotate=None):
    """A tiny real H.264/AAC mp4 (9:16 by default): fast to build, enough for ffprobe/ffmpeg to treat as a real
    take. `audio=False` produces a video-only file (the missing-audio-track failure path); `vf` filters the picture
    (setparams stamps colour tags); `rotate` writes a display-rotation tag (degrees) on the video stream."""
    args = ["ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=30:duration={duration}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
                 "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-ac", str(channels)]
    else:
        args += ["-map", "0:v", "-an"]
    if vf:
        args += ["-vf", vf]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest"]
    target = str(path) + ".coded.mp4" if rotate is not None else str(path)
    args += [target]
    r = subprocess.run(args, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    if rotate is not None:     # a phone clip: the picture is coded landscape, a display-rotation tag stands it up
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", str(rotate), "-i", target, "-c", "copy", str(path)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    return path


def make_garbage(path):
    """Not a video at all: the file exists and ffprobe/ffmpeg both refuse it, exactly like a
    truncated iCloud download or a half-written export."""
    with open(path, "wb") as f:
        f.write(os.urandom(4096))
    return path


def run_cli(*args, script=SCRIPT, env=None):
    return subprocess.run([sys.executable, "-B", script, *[str(a) for a in args]],
                          capture_output=True, text=True, env=dict(os.environ, **(env or {})))


@pytest.fixture
def install_copy(tmp_path):
    """install_copy(converter) -> the path of an intake.py in a tree of its own, with the two files it imports. The tree's engine/hdr.py is where the converter would be:
    a stand-in file when `converter` is true, nothing when it is false."""
    def make(converter):
        tree = tmp_path / ("with-converter" if converter else "no-converter") / "engine"
        (tree / "captions").mkdir(parents=True)
        for name in ("intake.py", "avskew.py", "keys.py"):
            shutil.copy(os.path.join(CAPTIONS, name), tree / "captions" / name)
        if converter:
            (tree / "hdr.py").write_text("# stands in for the converter: intake only asks whether the file is there\n")
        return str(tree / "captions" / "intake.py")
    return make


# --- pure -----------------------------------------------------------------------------------

def test_is_dataless_true_for_the_icloud_eviction_flag():
    assert intake.is_dataless("dataless") is True
    assert intake.is_dataless("compressed dataless") is True


def test_is_dataless_false_for_ordinary_flags():
    assert intake.is_dataless("") is False
    assert intake.is_dataless("archived") is False
    assert intake.is_dataless(None) is False


@pytest.mark.skipif(sys.platform != "darwin", reason="the iCloud eviction flag is a macOS file flag (stat -f %Sf)")
def test_stat_flags_reads_the_real_file_flags_and_a_missing_file_is_one_plain_stop(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    assert intake.is_dataless(intake.stat_flags(str(f))) is False
    with pytest.raises(keys.EngineStop, match="could not stat"):
        intake.stat_flags(str(tmp_path / "gone.mp4"))


def test_output_size_below_4k_stays_native_never_upscaled():
    assert intake.output_size_for(1080, 1920) == [1080, 1920]
    assert intake.output_size_for(1920, 1080) == [1080, 1920]
    assert intake.output_size_for(2159, 3840) == [1080, 1920]


def test_output_size_at_or_above_4k_targets_2160():
    assert intake.output_size_for(2160, 3840) == [2160, 3840]
    assert intake.output_size_for(3840, 2160) == [2160, 3840]


def test_output_size_is_decided_from_the_displayed_short_side():
    # a phone clip coded 2560x1440 with a 90 degree rotation tag is 1440x2560 on screen: not 4K, never upscaled to it
    dw, dh = intake.display_size(2560, 1440, rotation=90)
    assert (dw, dh) == (1440, 2560)
    assert intake.output_size_for(dw, dh) == [1080, 1920]
    assert intake.display_size(3840, 2160, rotation=-90) == (2160, 3840)
    assert intake.display_size(1080, 1920, rotation=0) == (1080, 1920)


def test_display_size_applies_the_pixel_aspect_ratio_before_the_rotation():
    assert intake.display_size(540, 1920, sar="2:1") == (1080, 1920)
    assert intake.display_size(1080, 1920, sar="0:1") == (1080, 1920)      # a nonsense SAR is ignored


def test_is_9x16_within_one_percent():
    assert intake.is_9x16(1080, 1920) and intake.is_9x16(2160, 3840) and intake.is_9x16(1079, 1920)
    assert not intake.is_9x16(1080, 1350)          # 4:5
    assert not intake.is_9x16(1920, 1080)          # landscape
    assert not intake.is_9x16(1080, 1080)          # square
    assert not intake.is_9x16(1000, 1920)


@pytest.mark.parametrize("dw,dh,shape", [(1920, 1080, "landscape"), (1080, 1080, "square-ish"), (1080, 1350, "not 9:16")])
def test_a_picture_that_is_not_9x16_stops_with_its_size_its_shape_and_the_reframe_instruction(dw, dh, shape):
    with pytest.raises(keys.EngineStop) as e:
        intake.check_picture("x.mp4", dw, dh, "bt709")
    assert f"{dw}x{dh}" in str(e.value) and shape in str(e.value) and "Reframe" in str(e.value) and "9:16" in str(e.value)
    assert intake.check_picture("x.mp4", 1080, 1920, "bt709") is None


def test_build_intake_shape_has_every_documented_field():
    d = intake.build_intake(
        source_copy="/t/source.mp4", source_original="/orig/clip.MOV",
        sha256_64mb="abc123", size_bytes=999,
        width=3840, height=2160, fps=29.9701, duration=32.36666,
        vcodec="h264", acodec="aac", channels=2,
        display=(2160, 3840), rotation=90.0, color_transfer="bt709",
    )
    assert d == {
        "source": "/t/source.mp4", "source_original": "/orig/clip.MOV",
        "sha256_64mb": "abc123", "size_bytes": 999,
        "width": 3840, "height": 2160, "display_width": 2160, "display_height": 3840,
        "rotation": 90.0, "color_transfer": "bt709", "fps": 29.97, "duration": 32.367,
        "vcodec": "h264", "acodec": "aac", "channels": 2,
        "output_size": [2160, 3840], "hdr": None,
    }
    assert intake.build_intake("/t/s.mp4", "/o.mov", "x", 1, 1080, 1920, 30, 1, "hevc", "aac", 2, color_transfer="arib-std-b67", hdr="hlg")["hdr"] == "hlg"


def test_sha256_first_64mb_hashes_only_the_leading_window(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"\x01" * (intake.HASH_BYTES + 5000))
    expect = hashlib.sha256(b"\x01" * intake.HASH_BYTES).hexdigest()
    assert intake.sha256_first_64mb(str(p)) == expect


def test_copy_physical_refuses_a_symlink_result(tmp_path, monkeypatch):
    """os.symlink swapped in for shutil.copy2 would leave a symlink at dest: copy_physical must
    catch that rather than silently shipping a link into the render's public folder, where the bundler does not follow links."""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"hello")
    dest = tmp_path / "out" / "source.mp4"
    monkeypatch.setattr(intake.shutil, "copy2", lambda s, d: os.symlink(s, d))
    with pytest.raises(keys.EngineStop, match="symlink"):
        intake.copy_physical(str(src), str(dest))


def test_copy_physical_refuses_a_copy_that_came_out_a_different_size(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"hello world")
    dest = tmp_path / "out" / "source.mp4"
    monkeypatch.setattr(intake.shutil, "copy2", lambda s, d: open(d, "wb").write(b"hello"))
    with pytest.raises(keys.EngineStop, match="different size"):
        intake.copy_physical(str(src), str(dest))


# --- the HDR converter -----------------------------------------------------------------------

def test_hdr_installed_follows_the_converter_file(tmp_path, monkeypatch):
    here = tmp_path / "hdr.py"
    monkeypatch.setattr(intake, "HDR_MODULE", str(here))
    assert intake.hdr_installed() is False
    here.write_text("# the converter\n")
    assert intake.hdr_installed() is True


def test_this_install_ships_the_converter_where_intake_looks_for_it():
    assert intake.HDR_MODULE == os.path.join(cap_helpers.REPO, "engine", "hdr.py") and intake.hdr_installed()


@pytest.mark.parametrize("transfer,kind", [("arib-std-b67", "HLG"), ("smpte2084", "PQ")])
def test_an_hdr_transfer_stops_with_the_sdr_instruction_unless_the_converter_is_there(transfer, kind):
    with pytest.raises(keys.EngineStop) as e:
        intake.check_picture("x.mp4", 1080, 1920, transfer)
    msg = str(e.value)
    assert f"HDR ({kind})" in msg and "cannot turn HDR into SDR" in msg and "SDR" in msg and "Rec.709" in msg and "HDR Video" in msg
    assert intake.check_picture("x.mp4", 1080, 1920, transfer, hdr_ok=True) is None


# --- dataless / decode refusal, exercised directly on _main --------------------------------

def test_dataless_source_stops_with_a_brctl_hint(tmp_path, monkeypatch):
    video = make_garbage(tmp_path / "evicted.mp4")
    take = tmp_path / "take"
    monkeypatch.setattr(intake, "stat_flags", lambda p: "dataless")
    monkeypatch.setattr(sys, "argv", ["intake.py", str(video), str(take)])
    with pytest.raises(keys.EngineStop) as e:
        intake._main()
    assert "brctl download" in str(e.value)
    assert not take.exists() or not any(take.iterdir())


def test_undecodable_file_stops_before_any_copy(tmp_path, monkeypatch):
    video = make_garbage(tmp_path / "corrupt.mp4")
    take = tmp_path / "take"
    monkeypatch.setattr(intake, "stat_flags", lambda p: "")
    monkeypatch.setattr(sys, "argv", ["intake.py", str(video), str(take)])
    with pytest.raises(keys.EngineStop, match="decode"):
        intake._main()
    assert not (take / "intake.json").exists()


def test_missing_audio_track_stops_with_a_plain_reason(tmp_path, monkeypatch):
    video = make_video(tmp_path / "silent.mp4", audio=False)
    take = tmp_path / "take"
    monkeypatch.setattr(intake, "stat_flags", lambda p: "")
    monkeypatch.setattr(sys, "argv", ["intake.py", str(video), str(take)])
    with pytest.raises(keys.EngineStop, match="audio"):
        intake._main()


# --- end-to-end CLI ---------------------------------------------------------------------------

def test_cli_missing_args_stops_clean_no_traceback():
    r = run_cli()
    assert r.returncode == 2
    assert r.stderr.startswith("STOP: usage: intake.py <video> <take-dir>") and r.stdout == ""
    assert "Traceback" not in r.stdout + r.stderr


def test_cli_source_not_found_stops(tmp_path):
    r = run_cli("/no/such/file.mp4", tmp_path / "take")
    assert r.returncode == 2
    assert r.stderr.startswith("STOP: source file not found") and "Traceback" not in r.stderr
    assert not (tmp_path / "take").exists()


def test_cli_a_file_that_will_not_decode_stops_before_anything_is_copied(tmp_path):
    r = run_cli(make_garbage(tmp_path / "corrupt.mp4"), tmp_path / "take")
    assert r.returncode == 2 and r.stderr.startswith("STOP: corrupt.mp4 will not decode") and r.stdout == "" and "Traceback" not in r.stderr
    assert not (tmp_path / "take").exists()


def test_cli_happy_path_native_output_size(tmp_path):
    video = make_video(tmp_path / "clip.mp4", width=216, height=384, duration=0.5, channels=2)
    take = tmp_path / "take"
    r = run_cli(video, take)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "intake ok" in r.stdout

    copy = take / "source.mp4"
    assert copy.exists() and not copy.is_symlink()
    assert copy.stat().st_size == video.stat().st_size

    d = json.loads((take / "intake.json").read_text())
    assert d["source"] == str(copy.resolve()) or os.path.abspath(d["source"]) == str(copy.resolve())
    assert d["source_original"] == str(video.resolve())
    assert d["width"] == 216 and d["height"] == 384
    assert d["display_width"] == 216 and d["display_height"] == 384 and d["rotation"] == 0
    assert abs(d["fps"] - 30.0) < 0.5
    assert abs(d["duration"] - 0.5) < 0.3
    assert d["vcodec"] == "h264"
    assert d["acodec"] == "aac"
    assert d["channels"] == 2
    assert d["output_size"] == [1080, 1920]
    assert d["size_bytes"] == copy.stat().st_size
    assert d["sha256_64mb"] == hashlib.sha256(copy.read_bytes()[:intake.HASH_BYTES]).hexdigest()


def test_cli_a_symlinked_source_lands_as_a_physical_copy(tmp_path):
    video = make_video(tmp_path / "clip.mp4", duration=0.3)
    link = tmp_path / "link.mp4"
    os.symlink(video, link)
    take = tmp_path / "take"
    r = run_cli(link, take)
    assert r.returncode == 0, r.stdout + r.stderr
    copy = take / "source.mp4"
    assert copy.exists() and not copy.is_symlink() and copy.read_bytes() == video.read_bytes()


def test_cli_4k_source_targets_4k_output_size(tmp_path):
    # a real 2160x3840 frame, tiny duration so the encode stays fast
    video = make_video(tmp_path / "uhd.mp4", width=2160, height=3840, duration=0.1, channels=1)
    take = tmp_path / "take"
    r = run_cli(video, take)
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads((take / "intake.json").read_text())
    assert d["width"] == 2160 and d["output_size"] == [2160, 3840] and d["channels"] == 1


def test_cli_a_rotated_phone_clip_is_judged_by_its_displayed_size(tmp_path):
    """Coded 1440x810 with a 90 degree tag shows as 810x1440 (9:16): accepted, native output, not upscaled."""
    video = make_video(tmp_path / "rot.mp4", width=1440, height=810, duration=0.2, rotate=90)
    take = tmp_path / "take"
    r = run_cli(video, take)
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads((take / "intake.json").read_text())
    assert (d["width"], d["height"]) == (1440, 810)
    assert (d["display_width"], d["display_height"]) == (810, 1440) and abs(d["rotation"]) == 90
    assert d["output_size"] == [1080, 1920]


def test_cli_landscape_source_stops_with_the_reframe_instruction(tmp_path):
    video = make_video(tmp_path / "land.mp4", width=384, height=216, duration=0.2)
    take = tmp_path / "take"
    r = run_cli(video, take)
    assert r.returncode == 2 and r.stderr.startswith("STOP:") and r.stdout == ""
    assert "384x216" in r.stderr and "9:16" in r.stderr and "Reframe" in r.stderr
    assert not (take / "intake.json").exists() and not (take / "source.mp4").exists()    # nothing copied


HLG = "setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=arib-std-b67,format=yuv420p"
PQ = "setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=smpte2084,format=yuv420p"


def test_cli_an_hdr_source_is_accepted_when_the_converter_is_installed_and_recorded(tmp_path):
    """engine/hdr.py turns HLG and PQ into SDR, so the engine takes the iPhone's HDR Video instead of asking for a re-export."""
    assert intake.hdr_installed()
    for name, vf, kind in (("hlg.mp4", HLG, "hlg"), ("pq.mp4", PQ, "pq")):
        video = make_video(tmp_path / name, duration=0.2, vf=vf)
        r = run_cli(video, tmp_path / f"take-{kind}")
        assert r.returncode == 0, r.stdout + r.stderr
        d = json.load(open(tmp_path / f"take-{kind}" / "intake.json"))
        assert d["hdr"] == kind and d["output_size"] == [1080, 1920]


@pytest.mark.parametrize("name,vf,kind", [("hlg.mp4", HLG, "HLG"), ("pq.mp4", PQ, "PQ")])
def test_cli_an_hdr_source_stops_with_the_sdr_instruction_without_the_converter(tmp_path, install_copy, name, vf, kind):
    video = make_video(tmp_path / name, duration=0.2, vf=vf)
    r = run_cli(video, tmp_path / "take", script=install_copy(converter=False))
    assert r.returncode == 2 and r.stderr.startswith("STOP:") and r.stdout == "" and "Traceback" not in r.stderr
    assert f"HDR ({kind})" in r.stderr and "cannot turn HDR into SDR" in r.stderr
    assert "SDR" in r.stderr and "Rec.709" in r.stderr and "HDR Video" in r.stderr
    assert not (tmp_path / "take" / "intake.json").exists() and not (tmp_path / "take" / "source.mp4").exists()


def test_cli_an_hdr_source_is_accepted_in_a_tree_that_has_the_converter_file(tmp_path, install_copy):
    """the one thing intake asks of the converter is that its file is installed beside the engine"""
    video = make_video(tmp_path / "hdr.mp4", duration=0.2, vf=HLG)
    r = run_cli(video, tmp_path / "take", script=install_copy(converter=True))
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.load(open(tmp_path / "take" / "intake.json"))["hdr"] == "hlg"


def test_cli_an_sdr_clip_records_no_hdr(tmp_path):
    r = run_cli(make_video(tmp_path / "sdr.mp4", duration=0.2), tmp_path / "take")
    assert r.returncode == 0 and json.load(open(tmp_path / "take" / "intake.json"))["hdr"] is None


def test_cli_an_sdr_clip_needs_no_converter(tmp_path, install_copy):
    r = run_cli(make_video(tmp_path / "sdr.mp4", duration=0.2), tmp_path / "take", script=install_copy(converter=False))
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.load(open(tmp_path / "take" / "intake.json"))["hdr"] is None


def test_cli_a_bt2020_clip_with_no_transfer_tag_stops_too(tmp_path):
    video = make_video(tmp_path / "wide.mp4", duration=0.2,
                       vf="setparams=colorspace=bt2020nc:color_primaries=bt2020,format=yuv420p")
    r = run_cli(video, tmp_path / "take")
    assert r.returncode == 2 and r.stderr.startswith("STOP:") and "BT.2020" in r.stderr and "SDR" in r.stderr
    assert not (tmp_path / "take" / "intake.json").exists()
    assert intake.check_picture("x.mp4", 1080, 1920, "arib-std-b67", hdr_ok=True) is None         # but an HDR transfer the converter handles is let through
    with pytest.raises(keys.EngineStop, match="BT.2020"):
        intake.check_picture("x.mp4", 1080, 1920, "unknown", "bt2020", "bt2020nc", hdr_ok=True)


def test_cli_an_sdr_clip_tagged_bt709_is_accepted(tmp_path):
    video = make_video(tmp_path / "sdr.mp4", duration=0.2,
                       vf="setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709,format=yuv420p")
    r = run_cli(video, tmp_path / "take")
    assert r.returncode == 0, r.stdout
    assert json.loads((tmp_path / "take" / "intake.json").read_text())["color_transfer"] == "bt709"


def test_cli_rerun_overwrites_cleanly(tmp_path):
    """Idempotent re-runs are the whole point of the source hash: running intake.py twice on the
    same clip into the same take dir must not error or leave stale state."""
    video = make_video(tmp_path / "clip.mp4", width=216, height=384, duration=0.4)
    take = tmp_path / "take"
    assert run_cli(video, take).returncode == 0
    r2 = run_cli(video, take)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    d = json.loads((take / "intake.json").read_text())
    assert d["width"] == 216
