"""takes.py's encode: a take is aimed at 1.3x its camera clip's video bitrate, measured from its packets against the same span of the camera clip, and
climbs a ladder (VideoToolbox 1.3x, VideoToolbox 1.6x, libx264 crf; off a Mac libx264 1.3x, 1.6x, crf) until it is not under it. A take that no rung gets there fails by name; a rerun cuts an
existing take again when it is under its camera clip and keeps it when it is not; two takes are cut at once.

The ladder and the rerun are tested with ffmpeg and the measure stubbed, so the numbers are exact. The measure is tested against canned ffprobe output that
behaves like ffprobe (key-frame seek, B-frames shown out of file order, a clock that does not start at 0) and against real clips made with ffmpeg."""
import csv
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import pytest

import takes
from conftest import FOOTAGE, intake, ok, run_tool, sha_tree

M = 1_000_000
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")


# ---------------------------------------------------------------- the measure
FRAME = 0.033333   # the frame duration the canned ffprobe prints


def stream(n_frames, size, start=0.0, durations=True):
    """the video packets of a 30 fps IBBP file in FILE order: [(pts, size, key, duration)]. Every 30th frame is a key frame; each P frame comes before the two
    B frames shown ahead of it, so a packet shown early arrives after one shown late. size(n) is the size of the frame shown n-th"""
    order = [0]
    for k in range(0, (n_frames - 1) // 3):
        order += [3 * k + 3, 3 * k + 1, 3 * k + 2]
    order += list(range(len(order), n_frames))
    return [(start + n / 30, size(n), n % 30 == 0, f"{FRAME:.6f}" if durations else "N/A") for n in order]


def fake_ffprobe(monkeypatch, packets, start=0.0, late=0.0):
    """ffprobe over a file with those packets. -read_intervals 'a%b' acts the way ffprobe does: it seeks to the last key frame shown at or before a, then prints
    packets in file order until the first one shown at or after b. late = how far past a a sloppy demuxer's seek may land (MPEG-TS guesses a byte position).
    Returns the list of commands it was asked"""
    asked = []

    def run(cmd, **kw):
        asked.append(cmd)
        out = packets
        if "-read_intervals" in cmd:
            a, b = (float(x) for x in cmd[cmd.index("-read_intervals") + 1].split("%"))
            first = max((i for i, p in enumerate(packets) if p[2] and p[0] <= a + late), default=0)
            out = []
            for p in packets[first:]:
                if p[0] >= b: break
                out.append(p)
        doc = {"format": {"start_time": f"{start:.6f}"},
               "packets": [{"pts_time": f"{p[0]:.6f}", "dts_time": f"{p[0]:.6f}", "duration_time": p[3], "size": str(p[1])} for p in out]}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(doc), "")
    monkeypatch.setattr(takes.subprocess, "run", run)
    return asked


def sized(first, last):
    """frame n is 4000 + 7n bytes inside first..last-1 and huge outside, so a frame counted that should not be, or left out that should be, moves the answer"""
    return lambda n: 4000 + 7 * n if first <= n < last else 900_000


def want_bps(first, last, secs=None):
    """the bitrate of frames first..last-1 of `sized`: their bytes over their frame durations (or over `secs`)"""
    return sum(4000 + 7 * n for n in range(first, last)) * 8 / (secs if secs else (last - first) * FRAME)


def test_the_whole_file_is_its_packet_sizes_over_the_time_they_cover(monkeypatch):
    fake_ffprobe(monkeypatch, stream(300, lambda n: 1000 + n))
    assert takes.video_bps("any.mov") == pytest.approx(sum(1000 + n for n in range(300)) * 8 / (300 * FRAME), rel=1e-9)


def test_a_span_counts_only_the_packets_shown_inside_it(monkeypatch):
    asked = fake_ffprobe(monkeypatch, stream(300, sized(90, 180)))   # frames 90..179 are 3.0 s to just under 6.0 s; every other frame is huge
    got = takes.video_bps("camera.mp4", 3.0, 6.0)
    assert got == pytest.approx(want_bps(90, 180), rel=1e-9)   # the key frame before the span, the B frames shown ahead of the P frames, the frames past the end: none counted
    assert len(asked) == 1 and "-read_intervals" in asked[0] and asked[0][asked[0].index("-select_streams") + 1] == "v:0"


def test_a_span_that_ends_on_a_p_frame_still_gets_the_b_frames_shown_before_it(monkeypatch):
    # B frames 88 and 89 come after P frame 90 in the file: ffprobe stops at the first packet shown at or after its end, so the read has to run past the span
    fake_ffprobe(monkeypatch, stream(300, sized(60, 90)))
    assert takes.video_bps("camera.mp4", 2.0, 3.0) == pytest.approx(want_bps(60, 90), rel=1e-9)


def test_a_span_that_starts_on_a_key_frame_with_b_frames_shown_ahead_of_it_leaves_those_out(monkeypatch):
    # key frame 60 comes in the file before B frames 58 and 59, which are shown ahead of it: they are before the span
    fake_ffprobe(monkeypatch, stream(300, sized(60, 120)))
    assert takes.video_bps("camera.mp4", 2.0, 4.0) == pytest.approx(want_bps(60, 120), rel=1e-9)


def test_a_demuxer_that_seeks_a_little_late_still_gets_the_first_frames_of_the_span(monkeypatch):
    # a span that starts half way through a second of key-frame spacing: the read starts a second ahead of it, so a seek that lands up to 0.9 s late is still before it
    fake_ffprobe(monkeypatch, stream(300, sized(105, 180)), late=0.9)
    assert takes.video_bps("camera.ts", 3.5, 6.0) == pytest.approx(want_bps(105, 180), rel=1e-9)


def test_a_file_whose_clock_does_not_start_at_zero_is_read_where_ffmpeg_reads_it(monkeypatch):
    asked = fake_ffprobe(monkeypatch, stream(300, sized(90, 180), start=10.0), start=10.0)   # MPEG-TS and MXF: ffmpeg's -ss counts from the first frame, ffprobe's intervals do not
    assert takes.video_bps("camera.mts", 3.0, 6.0) == pytest.approx(want_bps(90, 180), rel=1e-9)
    assert len(asked) == 2 and asked[1][asked[1].index("-read_intervals") + 1] == "12.000%17.000"


def test_without_frame_durations_the_time_is_first_to_last_shown_plus_one_frame(monkeypatch):
    fake_ffprobe(monkeypatch, stream(300, sized(90, 180), durations=False))
    assert takes.video_bps("camera.mp4", 3.0, 6.0) == pytest.approx(want_bps(90, 180, secs=3.0), rel=1e-6)


def test_a_file_ffprobe_cannot_read_measures_zero(monkeypatch):
    monkeypatch.setattr(takes.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "No such file"))
    assert takes.video_bps("gone.mov") == 0.0 and takes.video_bps("gone.mov", 1.0, 2.0) == 0.0


@needs_ffmpeg
def test_a_real_clip_is_measured_per_span_from_its_own_packets(tmp_path):
    """one second of noise-like test pattern, then one second of black: the span before the cut is far above the span after it, and each is the sum of the
    packet sizes ffprobe itself prints for the frames shown in it"""
    clip = str(tmp_path / "two-halves.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=30:d=1", "-f", "lavfi", "-i", "color=c=black:s=160x120:r=30:d=1",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-crf", "10", "-g", "25", "-bf", "2", "-pix_fmt", "yuv420p", clip],
                   check=True)
    raw = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,size", "-of", "csv=p=0", clip],
                         capture_output=True, text=True, check=True).stdout.split()
    pk = [(float(a), int(b)) for a, b in (r.split(",") for r in raw)]
    assert len(pk) == 60
    busy, quiet = takes.video_bps(clip, 0.0, 1.0), takes.video_bps(clip, 1.0, 2.0)
    # ffprobe prints a frame's duration to the microsecond (0.033333 s), so thirty of them add up to a hair under a second: the sums agree to 1 part in 10,000
    assert busy == pytest.approx(sum(s for t, s in pk if t < 1.0 - 1e-6) * 8 / 1.0, rel=1e-4)
    assert quiet == pytest.approx(sum(s for t, s in pk if t >= 1.0 - 1e-6) * 8 / 1.0, rel=1e-4)
    assert busy > 10 * quiet
    assert takes.video_bps(clip) == pytest.approx(sum(s for _, s in pk) * 8 / 2.0, rel=1e-4)


def test_a_bitrate_in_a_line_of_output_has_enough_digits_to_tell_thin_ones_apart():
    assert [takes.mbps(x) for x in (96_749_052, 9_969_056, 4_200_000, 30_439)] == ["96.7", "9.97", "4.20", "0.030"]


# ---------------------------------------------------------------- the ladder
class Rig:
    """stands in for ffmpeg and for the measure. Every cut writes its output file; the measure reads off the next value of `got` for each cut that ran.
    fail = the 1-based numbers of the ffmpeg runs that exit non-zero"""

    def __init__(self, monkeypatch, got, fail=(), vt=True):
        self.got, self.fail, self.cmds = list(got), set(fail), []
        monkeypatch.setattr(takes, "run_ffmpeg", self.run)
        monkeypatch.setattr(takes, "video_bps", self.bps)
        monkeypatch.setattr(takes, "have_videotoolbox", lambda: vt)

    def run(self, cmd):
        self.cmds.append(cmd)
        if len(self.cmds) in self.fail: return subprocess.CompletedProcess(cmd, 1, "", "[h264_videotoolbox] Error: cannot create the session\n")
        with open(cmd[-1], "wb") as f: f.write(b"cut %d" % len(self.cmds))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def bps(self, path, t_in=None, t_out=None):
        return self.got.pop(0)

    def arg(self, n, flag):
        return self.cmds[n][self.cmds[n].index(flag) + 1]


def cut(tmp_path, src_bps=100 * M, name="C0001-02"):
    dst = str(tmp_path / "Interview" / "L01 Test" / f"{name}.mov")
    return dst, lambda: takes.encode_take(name, "camera.mp4", dst, 10.0, 30.0, src_bps)


def test_the_first_rung_is_videotoolbox_at_1_3x_with_no_maxrate_and_a_take_not_under_its_source_is_kept(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [110 * M])
    dst, go = cut(tmp_path)
    r = go()
    assert (r["rung"], r["take_bps"], r["src_bps"]) == ("VideoToolbox 1.3x", 110 * M, 100 * M)
    assert len(rig.cmds) == 1 and rig.arg(0, "-c:v") == "h264_videotoolbox" and rig.arg(0, "-b:v") == str(130 * M)
    assert "-maxrate" not in rig.cmds[0] and "-bufsize" not in rig.cmds[0]
    assert (rig.arg(0, "-ss"), rig.arg(0, "-to")) == ("10.000", "30.000")
    assert open(dst, "rb").read() == b"cut 1" and not os.path.exists(dst + ".part.mov")


def test_what_is_not_the_bitrate_stays_as_it_was(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [110 * M])
    cut(tmp_path)[1]()
    c = rig.cmds[0]
    assert "-map 0:v:0 -map 0:a:0?" in " ".join(c)
    assert (rig.arg(0, "-c:a"), rig.arg(0, "-pix_fmt"), rig.arg(0, "-profile:v"), rig.arg(0, "-movflags")) == ("pcm_s16le", "yuv420p", "high", "+faststart")
    assert c[c.index("-i") + 1] == "camera.mp4" and c[-1].endswith(".part.mov")


def test_nothing_caps_a_camera_clip_above_90_mbit(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [330 * M])
    cut(tmp_path, src_bps=250 * M)[1]()
    assert rig.arg(0, "-b:v") == str(325 * M)


def test_a_camera_clip_thinner_than_8_mbit_is_still_cut_at_8(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [7 * M])
    cut(tmp_path, src_bps=4 * M)[1]()
    assert rig.arg(0, "-b:v") == str(8 * M)


def test_a_take_exactly_at_its_source_is_not_under_it(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [100 * M])
    assert cut(tmp_path)[1]()["rung"] == "VideoToolbox 1.3x" and len(rig.cmds) == 1


def test_a_take_under_its_source_is_cut_again_at_1_6x(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [90 * M, 105 * M])
    dst, go = cut(tmp_path)
    r = go()
    assert r["rung"] == "VideoToolbox 1.6x" and r["take_bps"] == 105 * M and len(rig.cmds) == 2
    assert (rig.arg(0, "-b:v"), rig.arg(1, "-b:v")) == (str(130 * M), str(160 * M)) and "-maxrate" not in rig.cmds[1]
    assert open(dst, "rb").read() == b"cut 2"   # the second cut, not the first


def test_still_under_at_1_6x_it_is_encoded_with_libx264_on_a_quality_setting(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [80 * M, 95 * M, 101 * M])
    dst, go = cut(tmp_path)
    r = go()
    assert r["rung"] == f"libx264 crf {takes.CRF}" and r["take_bps"] == 101 * M and len(rig.cmds) == 3
    assert rig.arg(2, "-c:v") == "libx264" and rig.arg(2, "-crf") == str(takes.CRF) and rig.arg(2, "-preset") == "medium"
    assert "-b:v" not in rig.cmds[2] and "-maxrate" not in rig.cmds[2]
    assert open(dst, "rb").read() == b"cut 3"


def test_a_take_no_rung_gets_to_its_source_fails_in_one_line_naming_it_and_both_bitrates(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [60 * M, 80 * M, 93 * M])
    dst, go = cut(tmp_path, name="C0574-02")
    with pytest.raises(takes.TakeFailed) as e:
        go()
    line = str(e.value)
    assert "\n" not in line and line.startswith("C0574-02:") and "93.0 Mbit/s" in line and "100.0 Mbit/s" in line
    assert "VideoToolbox 1.3x" in line and "VideoToolbox 1.6x" in line and f"libx264 crf {takes.CRF}" in line
    assert len(rig.cmds) == 3 and not os.path.exists(dst) and not os.path.exists(dst + ".part.mov")


def test_a_failed_take_leaves_the_take_that_was_already_there_alone(tmp_path, monkeypatch):
    Rig(monkeypatch, [60 * M, 80 * M, 93 * M])
    dst, go = cut(tmp_path)
    os.makedirs(os.path.dirname(dst)); open(dst, "wb").write(b"the old take")
    with pytest.raises(takes.TakeFailed):
        go()
    assert open(dst, "rb").read() == b"the old take" and not os.path.exists(dst + ".part.mov")


def test_a_rung_that_does_not_run_moves_on_to_the_next(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [120 * M], fail={1, 2})   # VideoToolbox will not open, twice; libx264 gets there
    r = cut(tmp_path)[1]()
    assert r["rung"] == f"libx264 crf {takes.CRF}" and len(rig.cmds) == 3


def test_when_no_encode_runs_the_line_still_names_the_take_and_the_camera_clip_bitrate(tmp_path, monkeypatch):
    Rig(monkeypatch, [], fail={1, 2, 3})
    dst, go = cut(tmp_path, name="C0574-02")
    with pytest.raises(takes.TakeFailed) as e:
        go()
    line = str(e.value)
    assert "\n" not in line and line.startswith("C0574-02:") and "100.0 Mbit/s" in line and "cannot create the session" in line and not os.path.exists(dst)


def test_a_machine_without_videotoolbox_climbs_libx264_at_1_3x_then_1_6x_then_crf(tmp_path, monkeypatch):
    """Linux and WSL2 (and a Mac without VideoToolbox): the same two bitrate targets on libx264, then the fixed-quality rung"""
    rig = Rig(monkeypatch, [120 * M], vt=False)
    r = cut(tmp_path)[1]()
    assert r["rung"] == "libx264 1.3x" and len(rig.cmds) == 1 and rig.arg(0, "-c:v") == "libx264" and rig.arg(0, "-b:v") == str(130 * M) and "-crf" not in rig.cmds[0]
    rig = Rig(monkeypatch, [90 * M, 95 * M, 101 * M], vt=False)
    r = cut(tmp_path)[1]()
    assert r["rung"] == f"libx264 crf {takes.CRF}" and len(rig.cmds) == 3
    assert rig.arg(1, "-b:v") == str(160 * M) and rig.arg(2, "-crf") == str(takes.CRF) and "-b:v" not in rig.cmds[2]
    assert [takes.rung_name(x) for x in takes.RUNGS_X264] == ["libx264 1.3x", "libx264 1.6x", f"libx264 crf {takes.CRF}"]


def test_a_stopping_run_starts_no_new_encode(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [90 * M, 120 * M])
    takes.STOP.set()
    try:
        with pytest.raises(takes.Stopped):
            cut(tmp_path)[1]()
    finally:
        takes.STOP.clear()
    assert rig.cmds == []


def test_a_cut_that_fails_because_the_run_is_stopping_is_the_stop_and_not_a_failed_rung(tmp_path, monkeypatch):
    rig = Rig(monkeypatch, [120 * M], fail={1})
    failing_cut = rig.run

    def run(cmd):
        takes.STOP.set()   # Ctrl-C: the main thread sets the stop at the moment the cut under way dies
        return failing_cut(cmd)
    monkeypatch.setattr(takes, "run_ffmpeg", run)
    dst, lines = str(tmp_path / "Interview" / "L01 Test" / "C0001-02.mov"), []
    try:
        with pytest.raises(takes.Stopped):
            takes.encode_take("C0001-02", "camera.mp4", dst, 10.0, 30.0, 100 * M, lines.append)
    finally:
        takes.STOP.clear()
    assert len(rig.cmds) == 1 and lines == [] and not os.path.exists(dst + ".part.mov")   # no "did not run", no second rung


# ---------------------------------------------------------------- one cut of the run: keep or cut again
def clip_job(tmp_path):
    scr = str(tmp_path / "scratch")
    c = {"id": "C0001-02", "in": 3.32, "out": 13.0, "word_in": 4.32, "word_out": 12.0}
    return c, str(tmp_path / "Interview" / "Q02 Test" / "C0001-02 - Q02 - Take 1 - Clean.mov"), scr


class CutRig(Rig):
    """the measure answers by what is asked: the camera clip over a span, the take on disk, or the cut just made"""

    def __init__(self, monkeypatch, camera, on_disk, made=()):
        super().__init__(monkeypatch, made)
        self.camera, self.on_disk, self.asked = camera, on_disk, []
        monkeypatch.setattr(takes, "sh", lambda cmd: self.asked.append(("thumb", cmd[cmd.index("-ss") + 1], cmd[-1])))

    def bps(self, path, t_in=None, t_out=None):
        self.asked.append((os.path.basename(path), t_in, t_out))
        if t_in is not None: return self.camera
        return self.on_disk if not path.endswith(".part.mov") else self.got.pop(0)


def lay(dst, content=b"on disk"):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f: f.write(content)


def test_an_existing_take_not_under_its_camera_clip_is_kept_and_not_cut_again(tmp_path, monkeypatch):
    c, dst, scr = clip_job(tmp_path)
    rig = CutRig(monkeypatch, camera=97 * M, on_disk=120 * M)
    lay(dst); lay(str(tmp_path / "camera.mp4"))
    r = takes.cut_take(c, str(tmp_path / "camera.mp4"), dst, scr)
    assert r == {"kept": True, "take_bps": 120 * M, "src_bps": 97 * M}
    assert rig.cmds == [] and open(dst, "rb").read() == b"on disk" and not os.path.exists(f"{scr}/thumbs")
    assert ("camera.mp4", 3.32, 13.0) in rig.asked   # the camera clip is measured over the span the take was cut from


def test_an_existing_take_under_its_camera_clip_is_cut_again(tmp_path, monkeypatch):
    c, dst, scr = clip_job(tmp_path)
    rig = CutRig(monkeypatch, camera=97 * M, on_disk=49 * M, made=[110 * M])
    lay(dst); lay(str(tmp_path / "camera.mp4"))
    lines = []
    r = takes.cut_take(c, str(tmp_path / "camera.mp4"), dst, scr, lines.append)
    assert r["rung"] == "VideoToolbox 1.3x" and r["take_bps"] == 110 * M and len(rig.cmds) == 1
    assert open(dst, "rb").read() == b"cut 1"                           # the new cut replaced the thin one
    assert any("49.0" in x and "97.0" in x and "cutting it again" in x for x in lines)
    assert [a for a in rig.asked if a[0] == "thumb"][0][2].endswith("C0001-02.jpg")


def test_an_existing_take_that_will_not_play_is_cut_again(tmp_path, monkeypatch):
    c, dst, scr = clip_job(tmp_path)
    rig = CutRig(monkeypatch, camera=97 * M, on_disk=0.0, made=[110 * M])
    lay(dst); lay(str(tmp_path / "camera.mp4"))
    assert takes.cut_take(c, str(tmp_path / "camera.mp4"), dst, scr)["rung"] == "VideoToolbox 1.3x" and open(dst, "rb").read() == b"cut 1"


def test_a_take_that_is_not_there_is_cut_and_gets_its_thumbnail(tmp_path, monkeypatch):
    c, dst, scr = clip_job(tmp_path)
    rig = CutRig(monkeypatch, camera=97 * M, on_disk=0.0, made=[110 * M])
    lay(str(tmp_path / "camera.mp4"))
    takes.cut_take(c, str(tmp_path / "camera.mp4"), dst, scr)
    assert open(dst, "rb").read() == b"cut 1" and [a[1:] for a in rig.asked if a[0] == "thumb"] == [("8.16", f"{scr}/thumbs/C0001-02.jpg")]


def test_a_camera_clip_that_cannot_be_measured_stops_the_take_by_name(tmp_path, monkeypatch):
    c, dst, scr = clip_job(tmp_path)
    CutRig(monkeypatch, camera=0.0, on_disk=0.0)
    lay(str(tmp_path / "camera.mp4"))
    with pytest.raises(takes.TakeFailed, match="C0001-02: cannot read the video bitrate of its camera clip"):
        takes.cut_take(c, str(tmp_path / "camera.mp4"), dst, scr)


def test_no_camera_clip_and_no_take_fails_by_name_and_no_camera_clip_with_a_take_keeps_it(tmp_path, monkeypatch):
    c, dst, scr = clip_job(tmp_path)
    CutRig(monkeypatch, camera=97 * M, on_disk=120 * M)
    with pytest.raises(takes.TakeFailed, match="C0001-02: its camera clip is not there"):
        takes.cut_take(c, str(tmp_path / "gone.mp4"), dst, scr)
    lay(dst)
    r = takes.cut_take(c, str(tmp_path / "gone.mp4"), dst, scr)
    assert r["kept"] and r["unchecked"] and open(dst, "rb").read() == b"on disk"


# ---------------------------------------------------------------- two lanes
def test_two_takes_are_cut_at_once_and_never_more():
    live, peak, done, lock = [0], [0], [], threading.Lock()
    both = threading.Barrier(2, timeout=20)   # returns only when a second take is running at the same moment: one lane would time out here

    def work(i):
        with lock:
            live[0] += 1; peak[0] = max(peak[0], live[0])
        if i < 2: both.wait()
        with lock:
            live[0] -= 1; done.append(i)
    assert takes.run_lanes(8, work) == []
    assert peak[0] == 2 and sorted(done) == list(range(8)) and takes.LANES == 2


def test_a_failed_take_does_not_stop_the_others_and_comes_back_in_cut_order():
    done = []

    def work(i):
        if i in (1, 4): raise takes.TakeFailed(f"C000{i}-01: video 40.0 Mbit/s is under its camera clip's 97.0 Mbit/s")
        done.append(i)
    got = takes.run_lanes(6, work)
    assert got == ["C0001-01: video 40.0 Mbit/s is under its camera clip's 97.0 Mbit/s", "C0004-01: video 40.0 Mbit/s is under its camera clip's 97.0 Mbit/s"]
    assert sorted(done) == [0, 2, 3, 5]


def test_an_error_that_is_not_a_failed_take_stops_the_run_and_is_raised():
    started = []

    def work(i):
        if i == 0: raise RuntimeError("disk full")
        started.append(i); threading.Event().wait(0.1)
    with pytest.raises(RuntimeError, match="disk full"):
        takes.run_lanes(40, work)
    assert len(started) < 39 and not takes.STOP.is_set()


# ---------------------------------------------------------------- the whole script, on a real shoot
# The shoot is the synthetic card, started with real whisper, classified and organized (conftest), then given the segment maps the segmenting step would have written.
def segments(root, attempts, dur):
    """C0001 is one clean read. C0007 holds a false start, a pause and the whole line: a flub, a clean take, and the blooper that overlaps the flub"""
    (a1s, a1e), (a2s, a2e) = attempts
    res = root / ".work" / "results"
    (res / "C0001.segments.json").write_text(json.dumps({"clip": "C0001", "segments": [
        {"start": 0.0, "end": round(dur["C0001"] - 0.3, 2), "kind": "take", "line": "Q01", "quality": "clean", "note": "full answer"}]}))
    (res / "C0007.segments.json").write_text(json.dumps({"clip": "C0007", "segments": [
        {"start": a1s, "end": round(a1e + 0.3, 2), "kind": "take", "line": "Q01", "quality": "flub", "note": "stops after three words"},
        {"start": round(a2s - 0.1, 2), "end": round(min(a2e + 0.3, dur["C0007"]), 2), "kind": "take", "line": "Q01", "quality": "clean", "note": ""},
        {"start": a1s, "end": round(a1e + 0.5, 2), "kind": "blooper", "line": "Q01", "note": "false start"}]}))


@pytest.fixture
def shoot(organized, started_master):
    ev = {e["clip"]: e["duration_s"] for e in json.loads((organized / ".work" / "evidence.json").read_text())}
    segments(organized, started_master["attempts"], ev)
    return organized


def run_takes(root, *args):
    return run_tool("takes.py", "--job", root / "job.json", "--handle", "1", *args)


def read_manifest(root):
    return list(csv.DictReader(open(root / "Takes Manifest.csv", encoding="utf-8")))


def camera_span(root, row):
    return takes.video_bps(str(root / row["raw_path"]), float(row["in_s"]), float(row["out_s"]))


def camera_files(root):
    return sorted(root / r["new_path"] for r in csv.DictReader(open(root / "Footage Manifest.csv", encoding="utf-8")) if r["clip"] in ("C0001", "C0007"))


def cuts_on_disk(root):
    return sorted(f for d, _, fs in os.walk(root) for f in fs if f.endswith(".mov"))


def test_every_take_the_script_cuts_is_not_under_its_camera_clip(shoot):
    r = run_takes(shoot)
    assert r.returncode == 0, ok(r)
    rows = read_manifest(shoot)
    assert len(rows) == 4
    for row in rows:
        got, want = takes.video_bps(str(shoot / row["file"])), camera_span(shoot, row)
        assert want > 0 and got >= want, (row["file"], got, want)
    assert sum(bool(re.match(r"  C000\d-\d\d: (VideoToolbox [\d.]+x|libx264 ([\d.]+x|crf \d+)): ", line)) for line in r.stdout.splitlines()) == 4   # each cut says which rung got there


def test_the_takes_are_named_numbered_filed_and_the_camera_files_move_to_raw_clips(shoot):
    before = sha_tree(shoot)
    cameras = {p.name: before[os.path.relpath(p, shoot)] for p in camera_files(shoot)}
    assert run_takes(shoot).returncode == 0
    rows = {os.path.basename(r["file"]): r for r in read_manifest(shoot)}
    folder = "Interview/Q01 Why Our Process Takes Three Days/"
    assert set(rows) == {"C0001-01 - Q01 - Take 1 - Clean - Full Answer.mov", "C0007-01 - Q01 - Take 2 - Flub - Stops After Three Words.mov",   # numbered in time order, a blooper too
                         "C0007-02 - Blooper - Q01 - False Start.mov", "C0007-03 - Q01 - Take 3 - Clean.mov"}
    assert rows["C0001-01 - Q01 - Take 1 - Clean - Full Answer.mov"]["file"] == folder + "C0001-01 - Q01 - Take 1 - Clean - Full Answer.mov"
    assert rows["C0007-02 - Blooper - Q01 - False Start.mov"]["file"] == "B-Roll/Bloopers/C0007-02 - Blooper - Q01 - False Start.mov"
    after = sha_tree(shoot)
    for name, h in cameras.items():                                                          # the originals are untouched, only moved
        assert after["Raw Clips/" + [k for k in after if k.startswith("Raw Clips/") and k.split("/")[1].startswith(name.split(" - ")[0])][0].split("/", 1)[1]] == h
    assert all(not (shoot / "Interview" / "Q01 Why Our Process Takes Three Days" / n).exists() for n in os.listdir(shoot / "Raw Clips"))
    m = list(csv.DictReader(open(shoot / "Footage Manifest.csv", encoding="utf-8")))
    assert all(r["new_path"].startswith("Raw Clips/") for r in m if r["clip"] in ("C0001", "C0007"))


def test_a_rerun_cuts_again_the_take_that_is_under_its_camera_clip_and_keeps_the_rest(shoot):
    root = shoot
    assert run_takes(root).returncode == 0
    rows = read_manifest(root)
    thin = next(r for r in rows if r["file"].split("/")[-1].startswith("C0001-01"))
    thin_path = str(root / thin["file"])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", thin_path, "-vf", "scale=32:56", "-c:v", "libx264", "-crf", "51", "-preset", "ultrafast", "-g", "600",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", thin_path + ".thin.mov"], check=True)
    os.replace(thin_path + ".thin.mov", thin_path)
    want = camera_span(root, thin)
    assert 0 < takes.video_bps(thin_path) < want                                              # the premise: this take is under its camera clip now
    stamp = {r["file"]: os.stat(root / r["file"]).st_mtime_ns for r in rows if r is not thin}
    before = (root / "Takes Manifest.csv").read_text()
    r = run_takes(root)
    assert r.returncode == 0, ok(r)
    assert takes.video_bps(thin_path) >= want
    assert {f: os.stat(root / f).st_mtime_ns for f in stamp} == stamp                         # the takes that were fine were not touched
    assert "C0001-01: the take on disk is" in r.stdout and "cutting it again" in r.stdout
    assert sum(" kept, " in line for line in r.stdout.splitlines()) == 3
    assert (root / "Takes Manifest.csv").read_text() == before                                # the manifest is the same
    assert not [f for _, _, fs in os.walk(root) for f in fs if f.endswith(".part.mov")]


def test_a_rerun_with_other_notes_leaves_no_stale_cut_behind(shoot):
    root = shoot
    assert run_takes(root).returncode == 0
    old = {r["file"] for r in read_manifest(root)}
    seg = root / ".work" / "results" / "C0001.segments.json"
    d = json.loads(seg.read_text())
    d["segments"][0]["note"] = "better note"
    seg.write_text(json.dumps(d))
    r = run_takes(root)
    assert r.returncode == 0, ok(r)
    new = {r_["file"] for r_ in read_manifest(root)}
    assert len(new - old) == 1 and len(old - new) == 1
    assert "removed 2 files from an earlier run (changed or no longer a cut)" in r.stdout     # the old cut and its thumbnail
    assert not any(os.path.basename(f).startswith("C0001-01 - Q01 - Take 1 - Clean - Full Answer") for f in cuts_on_disk(root))
    on_disk = sorted(os.path.relpath(os.path.join(d_, f), root) for d_, _, fs in os.walk(root) for f in fs if f.endswith(".mov") and "Raw Clips" not in d_)
    assert on_disk == sorted(new)


def test_a_failed_take_stops_the_run_before_the_manifest_and_before_the_camera_files_move(shoot, monkeypatch, capsys):
    root = shoot
    cameras = camera_files(root)
    assert len(cameras) == 2 and all(p.exists() for p in cameras)
    real = takes.cut_take

    def cut_take(c, src, dst, scr, say=lambda s: None):
        if c["id"] == "C0001-01":
            raise takes.TakeFailed("C0001-01: video 40.0 Mbit/s is under its camera clip's 97.0 Mbit/s after VideoToolbox 1.3x, VideoToolbox 1.6x, libx264 crf 9")
        return real(c, src, dst, scr, say)
    monkeypatch.setattr(takes, "cut_take", cut_take)
    with pytest.raises(SystemExit) as e:
        takes.main(["--job", str(root / "job.json"), "--handle", "1"])
    err = capsys.readouterr().err.strip().splitlines()
    assert e.value.code == 2
    assert err[0] == "FAILED C0001-01: video 40.0 Mbit/s is under its camera clip's 97.0 Mbit/s after VideoToolbox 1.3x, VideoToolbox 1.6x, libx264 crf 9"
    assert err[1].startswith("STOP: 1 of 4 takes stayed under their camera clip's bitrate") and len(err) == 2
    assert not (root / "Takes Manifest.csv").exists() and not (root / "Raw Clips").exists()
    assert all(p.exists() for p in cameras)                                                    # the camera files are where they were
    assert len(cuts_on_disk(root)) == 3 and not any("C0001-01" in f for f in cuts_on_disk(root))   # the other three were cut; the failed one left no file


def test_the_dry_run_says_how_the_takes_will_be_encoded_and_cuts_nothing(shoot):
    r = run_takes(shoot, "--dry-run")
    assert r.returncode == 0, ok(r)
    line = next(x for x in r.stdout.splitlines() if x.startswith("encode:"))
    assert line.startswith("encode: " + (" then ".join(takes.rung_name(x) for x in takes.ladder()))) and f"{takes.LANES} at a time" in line
    assert cuts_on_disk(shoot) == [] and not (shoot / "Takes Manifest.csv").exists()


def part_files(root):
    return sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.endswith(".part.mov"))


def test_ctrl_c_stops_the_run_with_no_part_file_no_manifest_and_the_camera_files_where_they_were(shoot, tmp_path):
    root = shoot
    cameras = camera_files(root)
    shim = tmp_path / "bin" / "ffmpeg"   # the real ffmpeg for everything but a cut, which starts its .part.mov and then hangs, so both lanes are mid-cut
    shim.parent.mkdir()
    shim.write_text(f'#!/bin/sh\nfor last; do :; done\ncase "$last" in *.part.mov) : > "$last"; sleep 120 ;; *) exec {shutil.which("ffmpeg")} "$@" ;; esac\n')
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim.parent}{os.pathsep}{os.environ['PATH']}", "CUTROOM_HOME": os.environ["CUTROOM_HOME"], "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.Popen([sys.executable, str(FOOTAGE / "takes.py"), "--job", str(root / "job.json"), "--handle", "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env=env, start_new_session=True)
    try:
        t0 = time.time()
        while time.time() - t0 < 60 and len(part_files(root)) < 2 and p.poll() is None:
            time.sleep(0.1)
        assert len(part_files(root)) == 2, "both lanes should be mid-cut"
        os.killpg(p.pid, signal.SIGINT)   # what Ctrl-C sends, to the whole group
        out, err = p.communicate(timeout=30)
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
    assert p.returncode == 130 and err.strip().splitlines()[-1].startswith("STOP: stopped by you") and "Traceback" not in err
    assert "did not run" not in out and "trying" not in out
    assert part_files(root) == [] and cuts_on_disk(root) == []
    assert not (root / "Takes Manifest.csv").exists() and not (root / "Raw Clips").exists() and all(p.exists() for p in cameras)


@needs_ffmpeg
def test_the_libx264_rung_cuts_a_real_clip_to_a_take_not_under_it(tmp_path, monkeypatch):
    monkeypatch.setattr(takes, "have_videotoolbox", lambda: False)
    src = str(tmp_path / "camera.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:v", "libx264",
                    "-b:v", "300k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src], check=True)
    c = {"id": "C0001-01", "in": 0.5, "out": 2.5, "word_in": 1.0, "word_out": 2.0}
    dst = str(tmp_path / "Interview" / "L01 Test" / "C0001-01.mov")
    r = takes.cut_take(c, src, dst, str(tmp_path / "scratch"))
    assert r["rung"].startswith("libx264") and r["take_bps"] >= r["src_bps"] > 0
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration", "-of", "json", dst],
                                     capture_output=True, text=True, check=True).stdout)
    assert [(s["codec_name"], s.get("width")) for s in info["streams"]] == [("h264", 320), ("pcm_s16le", None)]
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.1)
    assert os.path.exists(str(tmp_path / "scratch" / "thumbs" / "C0001-01.jpg")) and not os.path.exists(dst + ".part.mov")


@needs_ffmpeg
def test_a_source_with_no_audio_stream_is_cut_with_none_instead_of_failing(tmp_path, monkeypatch):
    """-map 0:a:0 fails on a clip that has no sound; the optional map cuts the picture alone"""
    monkeypatch.setattr(takes, "have_videotoolbox", lambda: False)
    src = str(tmp_path / "silent-camera.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=30:d=3", "-c:v", "libx264", "-b:v", "200k", "-pix_fmt", "yuv420p", src], check=True)
    dst = str(tmp_path / "Interview" / "L01 Test" / "C0001-01.mov")
    r = takes.cut_take({"id": "C0001-01", "in": 0.2, "out": 2.2, "word_in": 0.5, "word_out": 1.5}, src, dst, str(tmp_path / "scratch"))
    assert r["take_bps"] >= r["src_bps"] > 0
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", dst], capture_output=True, text=True, check=True).stdout)["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]


@needs_ffmpeg
def test_only_the_first_audio_stream_is_taken_so_a_second_stream_ffmpeg_cannot_decode_does_not_stop_the_cut(tmp_path, monkeypatch):
    """a phone that records spatial audio next to the normal track: all-streams mapping died on the stream with no decoder, the first-stream mapping cuts"""
    monkeypatch.setattr(takes, "have_videotoolbox", lambda: False)
    src = str(tmp_path / "two-audio.mov")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=30:d=3", "-f", "lavfi", "-i", "sine=f=440:d=3", "-f", "lavfi", "-i", "sine=f=880:d=3",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-b:v", "200k", "-pix_fmt", "yuv420p", "-c:a", "aac", src], check=True)
    dst = str(tmp_path / "Interview" / "L01 Test" / "C0001-01.mov")
    takes.cut_take({"id": "C0001-01", "in": 0.2, "out": 2.2, "word_in": 0.5, "word_out": 1.5}, src, dst, str(tmp_path / "scratch"))
    streams = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", dst], capture_output=True, text=True, check=True).stdout)["streams"]
    assert [s["codec_type"] for s in streams] == ["video", "audio"]


# ---------------------------------------------------------------- the segment files an agent writes are checked
def seg_file(tmp_path, body):
    p = tmp_path / "C0001.segments.json"
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return str(p)


@pytest.mark.parametrize("body, why", [
    ("{broken", "is not valid JSON"),
    ({"clip": "C0001"}, 'needs a "segments" list'),
    ({"segments": ["x"]}, "segment 1 must be an object"),
    ({"segments": [{"start": 1, "end": 2, "kind": "scene", "line": "Q01", "quality": "clean"}]}, 'kind must be "take" or "blooper"'),
    ({"segments": [{"start": "a", "end": 2, "kind": "take", "line": "Q01", "quality": "clean"}]}, "start and end must be numbers"),
    ({"segments": [{"end": 2, "kind": "take", "line": "Q01", "quality": "clean"}]}, "start and end must be numbers"),
    ({"segments": [{"start": 5, "end": 3, "kind": "take", "line": "Q01", "quality": "clean"}]}, "end must be after start"),
    ({"segments": [{"start": 20, "end": 30, "kind": "take", "line": "Q01", "quality": "clean"}]}, "end must be after start and inside the clip"),
    ({"segments": [{"start": 1, "end": 2, "kind": "take", "line": "Q01", "quality": "good"}]}, 'a take needs quality "clean", "usable" or "flub"'),
    ({"segments": [{"start": 1, "end": 2, "kind": "take", "line": "Q01"}]}, 'a take needs quality "clean", "usable" or "flub"'),
])
def test_a_mistake_in_a_segments_file_stops_with_one_line_naming_the_file_and_segment(tmp_path, capsys, body, why):
    with pytest.raises(SystemExit) as e:
        takes.load_segments(seg_file(tmp_path, body), 10.0)
    err = capsys.readouterr().err.strip()
    assert e.value.code == 2 and err.startswith("STOP: C0001.segments.json") and why in err and "\n" not in err


def test_segments_come_back_sorted_clamped_to_the_clip_and_cleaned(tmp_path):
    body = {"segments": [{"start": 5, "end": 99, "kind": "take", "line": " Q01 ", "quality": "Clean", "note": " ok "},
                         {"start": -2, "end": 3, "kind": "blooper", "line": "", "quality": "flub", "note": None}]}
    got = takes.load_segments(seg_file(tmp_path, body), 10.0)
    assert got == [{"start": 0.0, "end": 3.0, "kind": "blooper", "line": "", "quality": "", "note": ""},
                   {"start": 5.0, "end": 10.0, "kind": "take", "line": "Q01", "quality": "clean", "note": "ok"}]


def test_a_take_that_names_a_line_the_job_does_not_have_is_refused_by_name_and_nothing_is_cut(shoot, capsys):
    seg = shoot / ".work" / "results" / "C0001.segments.json"
    d = json.loads(seg.read_text())
    d["segments"][0]["line"] = "Q99"
    seg.write_text(json.dumps(d))
    r = run_takes(shoot)
    assert r.returncode == 2 and "REFUSED: these takes name a line that is not in job.questions" in r.stderr and "C0001-01 (line 'Q99')" in r.stderr
    assert cuts_on_disk(shoot) == [] and not (shoot / "Raw Clips").exists()


def test_with_no_segments_at_all_takes_stops_with_one_line(organized):
    r = run_takes(organized)
    assert r.returncode == 2 and r.stderr.strip().startswith("STOP: no segments.json files") and "Traceback" not in r.stderr


def test_an_interview_clip_with_no_segments_file_is_named_and_left_where_it_is(shoot):
    r = run_takes(shoot)
    assert r.returncode == 0 and "interview clips with no segments.json stay uncut where they are: C0002" in r.stdout
    assert any(p.name.startswith("C0002 - Q02") for p in (shoot / "Interview" / "Q02 Welcome To The Workshop").iterdir())


# ---------------------------------------------------------------- names and thumbnails
def test_a_note_in_a_file_name_loses_its_apostrophes_and_punctuation_and_falls_on_a_word():
    assert takes.note_text("complete read, ends on a stray i don't") == "Complete Read Ends On A Stray I Dont"
    assert takes.note_text("says three-time instead of two-time!?") == "Says Three-time Instead Of Two-time"
    long = takes.note_text("word " * 40)
    assert len(long) <= 45 and long.startswith("Word Word") and long.endswith("Word")               # cut on a word, never in one
    assert takes.note_text("") == "" and takes.note_text(None) == ""


def test_a_thumbnail_is_as_wide_as_the_clips_own_shape_calls_for(tmp_path, monkeypatch):
    seen = []
    c = {"id": "C0001-02", "in": 3.32, "out": 13.0, "word_in": 4.32, "word_out": 12.0, "thumb_w": 426}
    monkeypatch.setattr(takes, "run_ffmpeg", lambda cmd: (open(cmd[-1], "wb").write(b"cut"), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    monkeypatch.setattr(takes, "video_bps", lambda path, t_in=None, t_out=None: 100 * M)
    monkeypatch.setattr(takes, "have_videotoolbox", lambda: False)
    monkeypatch.setattr(takes, "sh", lambda cmd: seen.append(cmd))
    camera = tmp_path / "camera.mp4"
    camera.write_bytes(b"x")
    takes.cut_take(c, str(camera), str(tmp_path / "Interview" / "x" / "C0001-02.mov"), str(tmp_path / "scratch"))
    assert seen[0][seen[0].index("-vf") + 1] == "scale=426:-2"
    c2 = {k: v for k, v in c.items() if k != "thumb_w"}
    takes.cut_take(c2, str(camera), str(tmp_path / "Interview" / "x" / "C0001-03.mov"), str(tmp_path / "scratch"))
    assert seen[1][seen[1].index("-vf") + 1] == "scale=240:-2"


def test_a_cut_that_played_is_kept_even_when_its_thumbnail_cannot_be_made(tmp_path, monkeypatch):
    c = {"id": "C0001-02", "in": 3.32, "out": 13.0, "word_in": 4.32, "word_out": 12.0}
    monkeypatch.setattr(takes, "run_ffmpeg", lambda cmd: (open(cmd[-1], "wb").write(b"cut"), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    monkeypatch.setattr(takes, "video_bps", lambda path, t_in=None, t_out=None: 100 * M)
    monkeypatch.setattr(takes, "have_videotoolbox", lambda: False)

    def no_thumb(cmd):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(takes, "sh", no_thumb)
    camera = tmp_path / "camera.mp4"
    camera.write_bytes(b"x")
    lines = []
    dst = str(tmp_path / "Interview" / "x" / "C0001-02.mov")
    r = takes.cut_take(c, str(camera), dst, str(tmp_path / "scratch"), lines.append)
    assert r["rung"].startswith("libx264") and open(dst, "rb").read() == b"cut"
    assert lines == ["  C0001-02: no thumbnail could be made, the camera clip's own is used"]


def test_the_thumbnail_of_a_stale_cut_of_a_clip_with_spaces_in_its_name_is_removed_with_it(tmp_path):
    root, scr = tmp_path / "shoot", tmp_path / "scratch"
    clip = "0025 - answer take one"
    rel = f"Interview/Q01 X/{clip}-01 - Q01 - Take 1 - Clean.mov"
    (root / "Interview" / "Q01 X").mkdir(parents=True)
    (scr / "thumbs").mkdir(parents=True)
    (root / rel).write_bytes(b"cut")
    (root / rel).with_suffix(".txt").write_text("notes")
    (scr / "thumbs" / f"{clip}-01.jpg").write_bytes(b"thumb")
    (scr / "thumbs" / f"{clip}.jpg").write_bytes(b"the clip's own thumbnail")
    with open(root / "Takes Manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "source_clip", "raw_path", "kind", "line", "take", "quality", "in_s", "out_s", "spoken_from_s", "spoken_to_s", "note"])
        w.writerow([rel, clip, f"Raw Clips/{clip}.mp4", "take", "Q01", 1, "clean", "0.00", "5.00", "1.00", "4.00", ""])
    takes.drop_stale_cuts(str(root), str(scr), [])
    assert not (root / rel).exists() and not (root / rel).with_suffix(".txt").exists()
    assert not (scr / "thumbs" / f"{clip}-01.jpg").exists() and (scr / "thumbs" / f"{clip}.jpg").exists()     # the cut's thumbnail goes, the clip's own stays
