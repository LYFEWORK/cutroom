"""matte.py: person mattes. `frames` (windows -> RGBA webp), `video` (a matte png a frame) and `fix` (clean a matte for compositing). The Apple helpers are
replaced by stub executables, except where a test says it runs the real one. `fix` needs scipy, which lives in the word-timing environment: its tests
run under a python that has it and skip when there is none."""
import os
import shutil
import subprocess
import sys
import textwrap

import numpy as np
import pytest
from PIL import Image

import matte
from helpers import sh, video
from helpers_misc import ENGINE, VENV_PYTHON, run_engine, stop_lines, stub_exe

MATTE_PY = os.path.join(ENGINE, "matte.py")


def stops(capsys, fn, *args):
    with pytest.raises(SystemExit) as e:
        fn(*args)
    assert e.value.code == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("STOP: ") and err.count("STOP:") == 1
    return err


# --- frames: the windows -> RGBA webp ----------------------------------------------------------------------------------------------

def test_frame_names_merge_windows():
    assert matte.frame_names([[3, 5], [4, 6], [10, 10]]) == [3, 4, 5, 6, 10]


def test_compose_writes_rgba_webp(tmp_path):
    rgb = tmp_path / "f.png"
    Image.new("RGB", (40, 60), (200, 50, 50)).save(rgb)
    m = tmp_path / "f.mask.png"
    mk = Image.new("L", (40, 60), 0)
    mk.paste(255, (10, 10, 30, 50))
    mk.save(m)
    out = tmp_path / "f00001.webp"
    matte.compose(str(rgb), str(m), str(out))
    im = Image.open(out)
    assert im.mode == "RGBA" and im.getchannel("A").getpixel((20, 30)) > 200 and im.getchannel("A").getpixel((2, 2)) < 20


# --- every requested frame is verified -----------------------------------------------------------------------------------------------

def _fake_chain(monkeypatch, tmp_path, short=0):
    """A personmask/ffmpeg stand-in: extracts `short` fewer frames than asked for."""
    binp = tmp_path / "personmask"
    binp.write_text("#!/bin/sh\n")
    monkeypatch.setattr(matte.swiftbin, "ensure", lambda d: str(binp))

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, service):
        if cmd[0] == "ffmpeg":
            pat = cmd[-1]
            f0 = int(cmd[cmd.index("-start_number") + 1])
            n = int(cmd[cmd.index("-frames:v") + 1])
            for i in range(f0, f0 + n - short):
                Image.new("RGB", (8, 8), (9, 9, 9)).save(pat.replace("%05d", f"{i:05d}"))
        else:
            for f in cmd[1:]:
                Image.new("L", (8, 8), 255).save(f.replace(".png", ".mask.png"))
        return R()
    monkeypatch.setattr(matte, "_run", fake_run)


def test_build_stops_when_a_requested_frame_was_not_produced(tmp_path, monkeypatch, capsys):
    """build() once composed whatever listdir happened to find and never compared it against the windows, so a short extraction shipped a video with no
    matte over those frames, silently."""
    _fake_chain(monkeypatch, tmp_path, short=1)
    err = stops(capsys, matte.build, "video.mp4", [[10, 14]], str(tmp_path / "out"))
    assert "4 of 5" in err and "first 14, last 14" in err and "no matte over those frames" in err


def test_build_returns_the_count_when_every_frame_is_there(tmp_path, monkeypatch):
    _fake_chain(monkeypatch, tmp_path, short=0)
    assert matte.build("video.mp4", [[10, 14]], str(tmp_path / "out")) == 5
    assert sorted(os.listdir(tmp_path / "out")) == [f"f{i:05d}.webp" for i in range(10, 15)]


def test_build_stops_when_the_helper_fails_or_leaves_a_mask_out(tmp_path, monkeypatch, capsys):
    binp = tmp_path / "personmask"
    binp.write_text("#!/bin/sh\n")
    monkeypatch.setattr(matte.swiftbin, "ensure", lambda d: str(binp))

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def run_ffmpeg_ok_then(code, masks):
        def fake_run(cmd, service):
            r = R()
            if cmd[0] == "ffmpeg":
                f0 = int(cmd[cmd.index("-start_number") + 1])
                for i in range(f0, f0 + int(cmd[cmd.index("-frames:v") + 1])):
                    Image.new("RGB", (8, 8), (9, 9, 9)).save(cmd[-1].replace("%05d", f"{i:05d}"))
            else:
                r.returncode = code
                r.stderr = "kaput"
                for f in cmd[1:][:masks]:
                    Image.new("L", (8, 8), 255).save(f.replace(".png", ".mask.png"))
            return r
        return fake_run
    monkeypatch.setattr(matte, "_run", run_ffmpeg_ok_then(4, 0))
    assert "personmask is down: exit 4: kaput" in stops(capsys, matte.build, "v.mp4", [[1, 3]], str(tmp_path / "o1"))
    monkeypatch.setattr(matte, "_run", run_ffmpeg_ok_then(0, 2))
    assert "personmask is down: no mask produced for f00003.png" in stops(capsys, matte.build, "v.mp4", [[1, 3]], str(tmp_path / "o2"))


# --- every matte frame is the source frame it is named for ------------------------------------------------------------------------------

def numbered_clip(path, frames=96, fps=30):
    """a test clip where frame k wears its own number as eight black and white blocks (bit b of k in block b), encoded the way the mezzanines are
    (libx264 fast, crf 16), so a frame can be read back from the matte exactly"""
    d = os.path.join(os.path.dirname(str(path)), "numbers")
    os.makedirs(d, exist_ok=True)
    for k in range(frames):
        a = np.zeros((16, 64), dtype=np.uint8)
        for b in range(8):
            if (k >> b) & 1:
                a[:, b * 8:(b + 1) * 8] = 255
        Image.fromarray(a, "L").convert("RGB").save(os.path.join(d, f"{k:03d}.png"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-i", os.path.join(d, "%03d.png"), "-c:v", "libx264", "-preset", "fast", "-crf", "16",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


def frame_number(webp):
    a = np.asarray(Image.open(webp).convert("L"))
    return sum(1 << b for b in range(8) if a[:, b * 8:(b + 1) * 8].mean() > 128)


def real_extraction(monkeypatch, tmp_path):
    """real ffmpeg, a personmask stand-in that marks every pixel as person (Apple Vision is not what is under test)"""
    binp = stub_exe(tmp_path / "personmask",
                    "import sys\nfrom PIL import Image\nfor f in sys.argv[1:]:\n    Image.new('L', Image.open(f).size, 255).save(f.replace('.png', '.mask.png'))\n")
    monkeypatch.setattr(matte.swiftbin, "ensure", lambda d: binp)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
@pytest.mark.parametrize("fps", [30, 24])
def test_every_matte_frame_is_the_source_frame_it_is_named_for_at_every_window_phase(tmp_path, monkeypatch, fps):
    """a window that started at a frame f0 with f0 = 2 (mod 3) at 30 fps was seeked to a time rounded up past the frame, which the accurate seek then
    dropped: f00026 held source frame 27 and a person cutout came out one frame late. 24 fps rounds up for f0 = 1, 5, 7 and more"""
    real_extraction(monkeypatch, tmp_path)
    clip = numbered_clip(tmp_path / "n.mp4", fps=fps)
    for f0 in (0, 1, 2, 3, 4, 5, 7, 26, 27, 28, 29, 44, 45):
        out = tmp_path / f"out-{fps}-{f0}"
        assert matte.build(clip, [[f0, f0 + 5]], str(out), fps=fps) == 6
        assert [frame_number(out / f"f{f:05d}.webp") for f in range(f0, f0 + 6)] == list(range(f0, f0 + 6)), (fps, f0)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
def test_two_windows_in_one_call_are_each_aligned(tmp_path, monkeypatch):
    real_extraction(monkeypatch, tmp_path)
    clip = numbered_clip(tmp_path / "n.mp4")
    out = tmp_path / "out"
    assert matte.build(clip, [[2, 7], [26, 31]], str(out)) == 12
    assert {f: frame_number(out / f"f{f:05d}.webp") for f in [*range(2, 8), *range(26, 32)]} == {f: f for f in [*range(2, 8), *range(26, 32)]}


def test_the_seek_is_a_hair_before_the_frame_and_never_before_zero():
    assert matte._seek(0, 30) == "0.0000" and matte._seek(1, 30) == "0.0323" and matte._seek(26, 30) == "0.8657"
    for fps in (24, 25, 30, 60):
        for f0 in range(0, 200):
            t = float(matte._seek(f0, fps))
            assert f0 / fps - 0.0011 <= t <= f0 / fps and t > (f0 - 1) / fps, (fps, f0, t)


def test_the_frames_command_writes_the_webp_frames_and_counts_them(tmp_path, monkeypatch, capsys):
    real_extraction(monkeypatch, tmp_path)
    clip = numbered_clip(tmp_path / "n.mp4", frames=40)
    win = tmp_path / "windows.json"
    win.write_text("[[3, 5], [10, 12]]")
    monkeypatch.setattr(sys, "argv", ["matte.py", "frames", clip, str(win), str(tmp_path / "out")])
    matte._main()
    assert capsys.readouterr().out.strip() == f"6 matte frames -> {tmp_path / 'out'}"
    assert sorted(os.listdir(tmp_path / "out")) == [f"f{i:05d}.webp" for i in (3, 4, 5, 10, 11, 12)]


# --- video: a matte png a frame ----------------------------------------------------------------------------------------------------------

def test_video_mattes_reports_the_helpers_last_line_and_refuses_what_it_cannot_use(tmp_path, monkeypatch, capsys):
    exe = stub_exe(tmp_path / "personmatte", "import sys\nprint('frames: 60')\nprint('done: 90 frames, 1080x1920')\n")
    monkeypatch.setattr(matte.swiftbin, "ensure", lambda d: exe)
    assert matte.video_mattes("v.mp4", str(tmp_path / "m"), "fast") == "done: 90 frames, 1080x1920"
    assert os.path.isdir(tmp_path / "m")
    assert "quality 'ultra' is not one of fast, balanced, accurate" in stops(capsys, matte.video_mattes, "v.mp4", str(tmp_path / "m"), "ultra")
    bad = stub_exe(tmp_path / "personmatte2", "import sys\nprint('no video track')\nsys.exit(1)\n")
    monkeypatch.setattr(matte.swiftbin, "ensure", lambda d: bad)
    assert "personmatte failed on v.mp4: no video track" in stops(capsys, matte.video_mattes, "v.mp4", str(tmp_path / "m2"))


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="Apple Vision helpers need macOS and swiftc")
def test_the_real_helper_writes_one_gray_looking_matte_a_frame_at_the_videos_size(tmp_path):
    clip = video(tmp_path / "v.mp4", size="160x120", dur=1, audio=False)
    r = run_engine("matte.py", "video", clip, tmp_path / "masks", "fast")
    assert r.returncode == 0 and r.stdout.strip().endswith("done: 30 frames, 160x120"), r.stdout + r.stderr
    files = sorted(os.listdir(tmp_path / "masks"))
    assert files == [f"m{i:05d}.png" for i in range(30)]
    im = Image.open(tmp_path / "masks" / files[0])
    assert im.size == (160, 120)
    a = np.asarray(im)
    assert a.dtype == np.uint8 and (a.ndim == 2 or (a[..., 0] == a[..., 1]).all() and (a[..., 0] == a[..., 2]).all())      # gray: one channel, or three equal ones


# --- fix: temporal median, held white objects, dilation and feather -------------------------------------------------------------------------

def _has_scipy(py):
    try:
        return subprocess.run([py, "-c", "import scipy.ndimage, numpy, PIL"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.fixture(scope="module")
def scipy_python():
    """a python that has scipy: the one running the tests when it does, else the word-timing environment's"""
    for py in (sys.executable, VENV_PYTHON):
        if py and _has_scipy(py):
            return py
    pytest.skip("needs a python with scipy (the word-timing environment has it): set CUTROOM_TEST_VENV_PYTHON")


def in_python(py, code):
    """run `code` under `py` with the engine importable; the test fails with the child's own words when it raises"""
    r = subprocess.run([py, "-c", f"import sys; sys.path.insert(0, {ENGINE!r}); import numpy as np, matte\n" + textwrap.dedent(code)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_a_single_flipped_frame_is_restored_by_the_temporal_median(scipy_python):
    in_python(scipy_python, """
        person = np.zeros((120, 80), np.uint8); person[30:100, 25:55] = 255
        masks = [person.copy() for _ in range(7)]; masks[3] = np.zeros_like(person)
        assert matte.temporal_median(masks, 3, 2)[60, 40] == 255
        assert matte.temporal_median(masks, 0, 2)[60, 40] == 255        # the edges repeat their first and last frame
    """)


def test_a_bright_object_touching_the_person_joins_the_matte_and_a_far_one_does_not(scipy_python):
    in_python(scipy_python, """
        H, W = 240, 160
        m = np.zeros((H, W), np.uint8); m[60:200, 50:100] = 255                       # the person
        frame = np.full((H, W, 3), 60, np.uint8)
        frame[100:140, 100:140] = 235                                                   # a white sheet held against the side (40x40 = 1600 px: too small)
        frame[100:160, 100:160] = 235                                                   # 60x60 = 3600 px, touching the matte
        frame[10:70, 5:45] = 235                                                        # 60x40 = 2400 px far from the person
        add, n = matte.white_objects(frame, m)
        assert add[130, 130] and not add[40, 20] and n == 1
        none, n0 = matte.white_objects(np.full((H, W, 3), 60, np.uint8), m)
        assert n0 == 0 and not none.any()
    """)


def test_finish_dilates_and_feathers_but_keeps_the_core_solid(scipy_python):
    in_python(scipy_python, """
        person = np.zeros((120, 80), np.uint8); person[30:100, 25:55] = 255
        out = matte.finish(person)
        assert out[65, 40] == 255 and out[28, 40] > 0 and out[0, 0] == 0 and out.dtype == np.uint8
    """)


def person_masks(folder, n, shape=(120, 80), box=(30, 100, 25, 55), flip=()):
    os.makedirs(folder, exist_ok=True)
    for i in range(n):
        m = np.zeros(shape, np.uint8)
        if i not in flip:
            m[box[0]:box[1], box[2]:box[3]] = 255
        Image.fromarray(m).save(os.path.join(folder, f"m{i:05d}.png"))


def test_the_fix_command_repairs_a_flipped_frame_through_the_word_timing_python(scipy_python, tmp_path):
    """the command is run by the python the tests run under: when that one has no scipy it hands itself to the word-timing python from config.json"""
    person_masks(tmp_path / "in", 7, flip=(3,))
    clip = video(tmp_path / "v.mp4", size="80x120", dur=1)
    r = run_engine("matte.py", "fix", clip, tmp_path / "in", tmp_path / "out", "--median", "5")
    assert r.returncode == 0 and r.stdout.strip() == f"done 7 masks, 0 white-object unions -> {tmp_path / 'out'}", r.stdout + r.stderr
    assert sorted(os.listdir(tmp_path / "out")) == [f"m{i:05d}.png" for i in range(7)]
    assert np.asarray(Image.open(tmp_path / "out" / "m00003.png"))[60, 40] == 255          # the flipped frame is back
    assert np.asarray(Image.open(tmp_path / "out" / "m00000.png"))[0, 0] == 0


def test_the_fix_command_joins_a_held_white_object_only_inside_the_white_window(scipy_python, tmp_path):
    H, W = 240, 160
    person_masks(tmp_path / "in", 12, shape=(H, W), box=(60, 200, 50, 100))
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c=0x3c3c3c:s={W}x{H}:r=30:d=0.4,drawbox=x=100:y=100:w=60:h=60:c=0xebebeb:t=fill,drawbox=x=5:y=10:w=40:h=60:c=0xebebeb:t=fill",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "10", str(tmp_path / "v.mp4")])
    assert r.returncode == 0, r.stderr
    r = run_engine("matte.py", "fix", tmp_path / "v.mp4", tmp_path / "in", tmp_path / "out", "--white-until", "0.2", "--median", "3")
    assert r.returncode == 0 and "done 12 masks, 6 white-object unions" in r.stdout, r.stdout + r.stderr          # one blob a frame, six frames in 0.2 s
    first = np.asarray(Image.open(tmp_path / "out" / "m00000.png"))
    later = np.asarray(Image.open(tmp_path / "out" / "m00008.png"))
    assert first[130, 130] == 255 and first[40, 20] == 0           # the sheet against the person joined; the one across the frame did not
    assert later[130, 130] == 0                                    # past the window the white facade does not leak in


def test_the_fix_command_stops_with_one_line_when_there_is_nothing_to_clean(scipy_python, tmp_path):
    (tmp_path / "empty").mkdir()
    r = run_engine("matte.py", "fix", tmp_path / "v.mp4", tmp_path / "empty", tmp_path / "out")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "no .png masks in" in r.stderr, r.stdout + r.stderr


def run_without_scipy(*args, env=None):
    """`matte.py <args>` in a python that has no scipy however the host is set up: the module import is blocked the way a missing install would be"""
    code = ("import sys, runpy; sys.path.insert(0, %r); sys.modules['scipy'] = None; sys.modules['scipy.ndimage'] = None; sys.argv = ['matte.py'] + %r; "
            "runpy.run_path(%r, run_name='__main__')" % (ENGINE, [str(a) for a in args], MATTE_PY))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ, **(env or {})))


def test_without_scipy_the_fix_command_hands_itself_to_the_word_timing_python(scipy_python, tmp_path):
    person_masks(tmp_path / "in", 5, flip=(2,))
    clip = video(tmp_path / "v.mp4", size="80x120", dur=1)
    r = run_without_scipy("fix", clip, tmp_path / "in", tmp_path / "out", "--median", "3")
    assert r.returncode == 0 and r.stdout.strip() == f"done 5 masks, 0 white-object unions -> {tmp_path / 'out'}", r.stdout + r.stderr
    assert np.asarray(Image.open(tmp_path / "out" / "m00002.png"))[60, 40] == 255


def test_without_scipy_anywhere_the_fix_command_stops_naming_the_python_it_tried_and_does_not_loop(tmp_path):
    r = run_without_scipy("fix", tmp_path / "v.mp4", tmp_path / "in", tmp_path / "out", env={"CUTROOM_MATTE_REEXEC": "1"})
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "scipy is not installed under" in r.stderr and "install.sh" in r.stderr, r.stdout + r.stderr
    assert "Traceback" not in r.stderr and not (tmp_path / "out").exists()


def test_without_scipy_a_config_that_names_no_python_is_one_plain_line(tmp_path):
    r = run_without_scipy("fix", tmp_path / "v.mp4", tmp_path / "in", tmp_path / "out", env={"CUTROOM_VENV_PYTHON": str(tmp_path / "no-such-python")})
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "the word-timing python is not at" in r.stderr and "no-such-python" in r.stderr, r.stdout + r.stderr


# --- the command line ------------------------------------------------------------------------------------------------------------------

def test_an_unknown_command_and_a_bad_quality_are_one_stop_line_each(tmp_path):
    r = run_engine("matte.py", "paint")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "unknown command 'paint'" in r.stderr
    r = run_engine("matte.py", "video", "v.mp4", tmp_path / "o", "ultra")
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "quality 'ultra' is not one of" in r.stderr
