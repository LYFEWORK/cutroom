"""colortag_check.py is the G9 color gate. It guards a trap that has shipped before: a chain of ffmpeg passes produced yuvj420p + bt470bg and a platform's
uploader silently rejected the file. Every fixture here is a real 1-frame ffmpeg render with the tags under test explicitly stamped, not a mocked ffprobe response:
the thing this gate protects against is exactly ffmpeg and ffprobe disagreeing with what a person expects those tags to mean."""
import os
import subprocess
import sys

import pytest

import cap_helpers
import colortag_check

SCRIPT = os.path.join(cap_helpers.REPO, "engine", "captions", "colortag_check.py")


def _render(path, extra, vf=None):
    """A 1-frame clip. The encoder copies primaries and transfer from the FRAMES, so a fully tagged file needs setparams
    (a bare -color_primaries leaves them "unknown"); vf=None leaves the frames as lavfi made them."""
    pre = ["-vf", vf] if vf else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=1"] + pre
                   + extra + [str(path)], check=True)


BT709_FRAMES = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,format=yuv420p"


@pytest.fixture
def good(tmp_path):
    p = tmp_path / "good.mp4"
    _render(p, ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"], vf=BT709_FRAMES)
    return str(p)


@pytest.fixture
def bad_pixfmt(tmp_path):
    """The exact drift this gate was written for: yuvj420p (JPEG/full-range pixel format) instead of
    yuv420p, which also drags color_range to "pc" as a side effect."""
    p = tmp_path / "bad-pixfmt.mp4"
    _render(p, ["-pix_fmt", "yuvj420p"])
    return str(p)


@pytest.fixture
def bad_range(tmp_path):
    p = tmp_path / "bad-range.mp4"
    _render(p, ["-pix_fmt", "yuv420p", "-color_range", "pc", "-colorspace", "bt709",
                "-color_primaries", "bt709", "-color_trc", "bt709"])
    return str(p)


@pytest.fixture
def bad_space(tmp_path):
    """The other half of the trap: bt470bg instead of bt709."""
    p = tmp_path / "bad-space.mp4"
    _render(p, ["-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt470bg",
                "-color_primaries", "bt470bg"])
    return str(p)


# --- probe_color / check ---------------------------------------------------------------------------

def test_probe_color_reads_the_stream_tags(good):
    tags = colortag_check.probe_color(good)
    assert tags["pix_fmt"] == "yuv420p" and tags["color_range"] == "tv" and tags["color_space"] == "bt709"


def test_check_passes_a_correctly_tagged_render(good):
    r = colortag_check.check(good)
    assert r["pass"] is True and r["detail"] == []
    assert "yuv420p" in r["numbers"] and "bt709" in r["numbers"]


def test_check_fails_the_pixfmt_drift(bad_pixfmt):
    """yuvj420p is the exact defect that made a platform's uploader reject a file."""
    r = colortag_check.check(bad_pixfmt)
    assert r["pass"] is False
    assert any(d["why"] == "pix_fmt" and d["got"] == "yuvj420p" for d in r["detail"])
    assert "re-export the source" in r["detail"][0]["plain"]


def test_check_fails_full_range_color(bad_range):
    r = colortag_check.check(bad_range)
    assert r["pass"] is False
    assert any(d["why"] == "color_range" and d["got"] == "pc" for d in r["detail"])


def test_check_fails_the_wrong_colorspace(bad_space):
    """bt470bg is the other half of the trap."""
    r = colortag_check.check(bad_space)
    assert r["pass"] is False
    assert any(d["why"] == "color_space" and d["got"] == "bt470bg" for d in r["detail"])


def test_check_reports_all_bad_tags_together(bad_range):
    """bad_range is wrong on two fronts at once (pix_fmt AND color_range): both must be reported,
    not just the first one found."""
    r = colortag_check.check(bad_range)
    whys = {d["why"] for d in r["detail"]}
    assert {"pix_fmt", "color_range"} <= whys


def test_check_fails_when_primaries_or_transfer_are_not_bt709(tmp_path):
    """A renderer used to write bt709 on the matrix alone; a file whose primaries or transfer are unlabeled or
    something else is not a bt709 file, whatever its range and matrix say."""
    p = tmp_path / "unlabeled.mp4"
    _render(p, ["-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709"])   # frames carry no primaries / transfer
    r = colortag_check.check(str(p))
    assert r["pass"] is False
    assert {d["why"] for d in r["detail"]} == {"color_primaries", "color_transfer"}
    assert all("re-render" in d["plain"] for d in r["detail"])
    wrong = tmp_path / "wrong.mp4"
    _render(wrong, ["-color_range", "tv", "-colorspace", "bt709"],
            vf="setparams=colorspace=bt709:color_primaries=bt2020:color_trc=arib-std-b67:range=tv,format=yuv420p")
    got = {d["why"]: d["got"] for d in colortag_check.check(str(wrong))["detail"]}
    assert got == {"color_primaries": "bt2020", "color_transfer": "arib-std-b67"}


def test_check_raises_enginestop_on_an_unreadable_file(tmp_path):
    with pytest.raises(colortag_check.EngineStop):
        colortag_check.check(str(tmp_path / "no-such-file.mp4"))


# --- CLI -------------------------------------------------------------------------------------------

def _cli(*args):
    return subprocess.run([sys.executable, "-B", SCRIPT, *args], capture_output=True, text=True)


def test_cli_exits_zero_and_prints_pass_for_a_good_render(good):
    r = _cli(good)
    assert r.returncode == 0
    assert r.stdout.startswith("PASS")


def test_cli_exits_one_and_prints_a_plain_sentence_for_a_bad_render(bad_pixfmt):
    r = _cli(bad_pixfmt)
    assert r.returncode == 1
    assert r.stdout.startswith("FAIL")
    assert "re-export the source" in r.stdout
    assert "Traceback" not in r.stdout + r.stderr


def test_cli_stops_with_one_line_on_a_missing_file():
    r = _cli("/no/such/render.mp4")
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.strip().startswith("STOP:") and "\n" not in r.stderr.strip() and "Traceback" not in r.stderr


def test_cli_stops_with_one_line_with_no_arguments():
    r = _cli()
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.strip().startswith("STOP:") and "\n" not in r.stderr.strip() and "Traceback" not in r.stderr
