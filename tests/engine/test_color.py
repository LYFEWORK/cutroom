"""The color gate (qc.color_check, qc.g_color, `qc.py color`): tags AND pixels.

A finished file has to be yuv420p, limited range, with BT.709 as its matrix, primaries and transfer (a platform's uploader rejects anything else, and
a picture left untagged shifts every color against the BT.709 mezzanine the cut was made from). Tags alone are not enough: a stream relabeled
bt709 over pictures converted with the wrong matrix or range has perfect tags and shifted colors, so the picture's own colors are compared with
the cut it was made from."""
import os
import shutil
import subprocess
import sys

import pytest

import qc
from helpers_qc import TAGS, sh

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")
QC_PY = os.path.join(ENGINE, "qc.py")
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")


def clip(path, extra=(), src="testsrc2=size=216x384:rate=30", dur=2, vf=None):
    """a lavfi clip encoded with libx264 and the given output options (no audio)"""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", str(dur)] + (["-vf", vf] if vf else []) + ["-c:v", "libx264", *extra, str(path)]
    r = sh(cmd)
    assert r.returncode == 0, r.stderr
    return str(path)


BT709 = ["-pix_fmt", "yuv420p", *TAGS]


# --- the tags ---------------------------------------------------------------------------------------------------------------------

def test_a_bt709_limited_range_clip_passes(tmp_path):
    a = clip(tmp_path / "a.mp4", BT709)
    r = qc.color_check(a, a)
    assert r["pass"] and r["bad"] == [], r
    assert r["numbers"].startswith("tags pix_fmt yuv420p, range tv, space bt709, primaries bt709, transfer bt709"), r["numbers"]
    assert "median drift R0.0 G0.0 B0.0" in r["numbers"]


def test_a_renderers_default_tags_fail_with_one_plain_sentence_each(tmp_path):
    """yuvj420p / pc / bt470bg: what a renderer writes when it is not told to make BT.709"""
    a = clip(tmp_path / "old.mp4", ["-pix_fmt", "yuvj420p", "-color_range", "pc", "-colorspace", "bt470bg"])
    r = qc.color_check(a)
    assert not r["pass"]
    joined = " | ".join(r["bad"])
    assert "pix_fmt is yuvj420p, not yuv420p" in joined and "color_range is pc, not tv" in joined and "color_space is bt470bg, not bt709" in joined, joined


def test_an_untagged_clip_fails_and_says_which_fields_the_file_leaves_open(tmp_path):
    a = clip(tmp_path / "plain.mp4", ["-pix_fmt", "yuv420p"])
    r = qc.color_check(a)
    assert not r["pass"]
    assert any(b.startswith("color_primaries is unknown") for b in r["bad"]) and any(b.startswith("color_transfer is unknown") for b in r["bad"]), r["bad"]


def test_a_ten_bit_picture_fails_on_its_pixel_format(tmp_path):
    a = clip(tmp_path / "ten.mp4", ["-pix_fmt", "yuv420p10le", *TAGS])
    r = qc.color_check(a)
    assert not r["pass"] and any("pix_fmt is yuv420p10le, not yuv420p" in b for b in r["bad"]), r["bad"]


def test_a_file_with_no_video_stream_stops_with_one_line(tmp_path, capsys):
    a = str(tmp_path / "sound.m4a")
    assert sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", "-c:a", "aac", a]).returncode == 0
    with pytest.raises(SystemExit) as e:
        qc.color_check(a)
    err = capsys.readouterr().err.strip()
    assert e.value.code == 2 and err.startswith("STOP: ffprobe could not read a video stream") and "\n" not in err


# --- the pixels -------------------------------------------------------------------------------------------------------------------

def test_a_relabeled_picture_fails_on_pixels_even_with_perfect_tags(tmp_path):
    """tags say bt709 / tv, but the pixels went through the wrong levels: only the pixel comparison sees it"""
    good = clip(tmp_path / "good.mp4", BT709)
    shifted = clip(tmp_path / "shifted.mp4", BT709, vf="format=yuv420p,eq=brightness=0.15:contrast=1.3")
    assert qc.color_check(shifted)["pass"], "the tags alone are perfect"
    r = qc.color_check(shifted, good)
    assert not r["pass"] and any("colors differ from the cut" in b for b in r["bad"]), r


def test_a_reencode_of_the_same_picture_stays_inside_the_pixel_tolerance(tmp_path):
    lossless = clip(tmp_path / "a.mp4", ["-qp", "0", *BT709])
    lossy = clip(tmp_path / "b.mp4", ["-crf", "26", *BT709])
    r = qc.color_check(lossy, lossless)
    assert r["pass"], r
    assert "limit 5" in r["numbers"]


def test_pixels_are_compared_over_the_shorter_of_the_two_durations(tmp_path):
    long_ = clip(tmp_path / "long.mp4", BT709, dur=4)
    short = clip(tmp_path / "short.mp4", BT709, dur=1)
    drift = qc.pixel_drift(long_, short)
    assert drift is not None and len(drift) == 3 and max(drift) <= qc.PIXEL_TOL


def test_a_picture_that_cannot_be_decoded_to_compare_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    a = clip(tmp_path / "a.mp4", BT709)
    monkeypatch.setattr(qc, "frame_medians", lambda path, t: None)
    r = qc.color_check(a, a)
    assert not r["pass"] and r["bad"] == ["no frame could be decoded to compare the picture's colors with the cut"]


def test_the_gate_record_carries_the_verdict_the_numbers_and_each_problem(tmp_path):
    good = clip(tmp_path / "good.mp4", BT709)
    old = clip(tmp_path / "old.mp4", ["-pix_fmt", "yuvj420p", "-color_range", "pc", "-colorspace", "bt470bg"])
    ok, bad = qc.g_color(good, good), qc.g_color(old, good)
    assert ok["pass"] is True and ok["skipped"] is False and ok["detail"] == []
    assert bad["pass"] is False and len(bad["detail"]) >= 3 and "pix_fmt yuvj420p" in bad["numbers"]
    assert qc.line("COLOR", ok).startswith("COLOR PASS ") and qc.line("COLOR", bad).startswith("COLOR FAIL ")
    assert qc.verdict_of({"COLOR": bad}) == "FAIL" and qc.verdict_of({"COLOR": ok}) == "PASS"


# --- the engine's own encoders ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["cut", "prep", "hdr"])
def test_the_encoder_flags_the_engine_uses_write_all_four_tags(module, tmp_path):
    """this ffmpeg build writes an x264 stream's matrix from -colorspace but leaves the primaries and transfer unknown unless x264 is told too: every
    module that encodes a picture carries the flag set that tells it, and the file it makes passes the gate"""
    flags = list(getattr(pytest.importorskip(module), "BT709"))
    assert "-x264-params" in flags and "colorprim=bt709:transfer=bt709:colormatrix=bt709" in flags, flags
    a = clip(tmp_path / "a.mp4", ["-pix_fmt", "yuv420p", *flags])
    r = qc.color_check(a)
    assert r["pass"], r


# --- the command line -------------------------------------------------------------------------------------------------------------

def test_cli_exit_codes(tmp_path):
    good = clip(tmp_path / "good.mp4", BT709)
    old = clip(tmp_path / "old.mp4", ["-pix_fmt", "yuvj420p", "-color_range", "pc", "-colorspace", "bt470bg"])
    shifted = clip(tmp_path / "shifted.mp4", BT709, vf="format=yuv420p,eq=brightness=0.15:contrast=1.3")

    def run(*args):
        return subprocess.run([sys.executable, QC_PY, "color", *args], capture_output=True, text=True)
    ok, bad, drift, gone, none = run(good, good), run(old, good), run(shifted, good), run(str(tmp_path / "nope.mp4")), run()
    assert ok.returncode == 0 and ok.stdout.startswith("COLOR PASS"), ok.stdout + ok.stderr
    assert bad.returncode == 1 and bad.stdout.startswith("COLOR FAIL") and "  pix_fmt is yuvj420p, not yuv420p" in bad.stdout, bad.stdout + bad.stderr
    assert drift.returncode == 1 and "  the picture's colors differ from the cut" in drift.stdout, drift.stdout + drift.stderr
    for r in (gone, none):
        assert r.returncode == 2 and r.stderr.startswith("STOP:") and r.stderr.count("STOP:") == 1 and "Traceback" not in r.stderr, r.stdout + r.stderr
    assert "ffprobe could not read a video stream" in gone.stderr and "usage:" in none.stderr
