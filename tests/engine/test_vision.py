"""The Linux stand-ins for the Apple Vision helpers (engine/vision/) and the locator switch that hands them out off a Mac.

The stand-ins' Python packages live in the word-timing environment; a test that needs one skips when that environment is not there or
lacks the package. `--check` and the output contracts (one line per image, `none` for no face, a mask file per frame) run here on any
platform, because the stand-ins are plain Python with the same command lines as the Swift helpers."""
import importlib.util
import os
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine", "swift"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine"))
import support  # noqa: E402
import swiftbin  # noqa: E402

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")
VISION = os.path.join(ENGINE, "vision")
SWIFT = os.path.join(ENGINE, "swift")
NAMES = ("facerect", "facepts", "personmask", "personmatte", "textboxes", "hdr2sdr")


def venv_has(*modules):
    py = support.venv_python(must_exist=False)
    if not (py and os.access(py, os.X_OK)):
        return False
    r = subprocess.run([py, "-c", "import " + ", ".join(modules)], capture_output=True, text=True)
    return r.returncode == 0


def run(name, *args, timeout=240):
    env = dict(os.environ)
    env.pop("CUTROOM_SHIM_REEXEC", None)
    return subprocess.run([os.path.join(VISION, name), *args], capture_output=True, text=True, timeout=timeout, env=env)


def test_every_helper_has_a_stand_in_that_is_executable_and_answers_usage():
    for n in NAMES:
        p = os.path.join(VISION, n)
        assert os.path.isfile(p) and os.access(p, os.X_OK), n
        head = open(p).read(400)
        assert head.startswith("#!/usr/bin/env python3") and "usage:" in head, n


def test_off_a_mac_the_locator_hands_out_the_stand_in(monkeypatch):
    monkeypatch.setattr(swiftbin, "IS_MAC", False)
    for n in NAMES:
        assert swiftbin.ensure(os.path.join(SWIFT, n)) == os.path.join(VISION, n)
    with pytest.raises(SystemExit):
        swiftbin.ensure(os.path.join(SWIFT, "nosuchhelper"))


def test_on_a_mac_the_locator_still_builds_the_apple_helper(monkeypatch):
    monkeypatch.setattr(swiftbin, "IS_MAC", True)
    assert swiftbin.ensure(os.path.join(SWIFT, "facerect")).endswith(os.path.join("facerect", "bin", "facerect")) if sys.platform == "darwin" else True


@pytest.mark.skipif(not venv_has("cv2", "numpy"), reason="the word-timing environment has no opencv")
def test_facerect_and_facepts_print_one_line_per_image_and_none_for_no_face(tmp_path):
    blank = tmp_path / "s000.png"
    Image.new("RGB", (540, 960), (40, 40, 40)).save(blank)
    r = run("facerect", "--check")
    assert r.returncode == 0 and r.stdout.startswith("ok facerect:"), r.stdout + r.stderr
    r = run("facerect", str(blank), str(tmp_path / "missing.png"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [f"{blank}\tnone", f"{tmp_path / 'missing.png'}\tnone"]
    r = run("facepts", str(blank))
    assert r.returncode == 0 and r.stdout.splitlines() == [f"{blank}\tnone"], r.stdout + r.stderr


@pytest.mark.skipif(not venv_has("cv2", "numpy"), reason="the word-timing environment has no opencv")
def test_facerect_finds_a_real_face_when_a_frame_with_one_is_at_hand(tmp_path):
    """a frame of a fixture clip, when this Mac has one from today's runs; the box has to sit inside the image and be a plausible head"""
    frame = os.environ.get("CUTROOM_TEST_FACE_FRAME")
    if not frame or not os.path.isfile(frame):
        pytest.skip("no face frame given (CUTROOM_TEST_FACE_FRAME)")
    r = run("facerect", frame)
    p = r.stdout.strip().split("\t")
    assert r.returncode == 0 and len(p) == 5, r.stdout + r.stderr
    x, y, w, h = map(int, p[1:])
    W, H = Image.open(frame).size
    assert 0 <= x < x + w <= W and 0 <= y < y + h <= H and w > W * 0.08 and h > H * 0.05


@pytest.mark.skipif(not venv_has("onnxruntime", "numpy", "PIL"), reason="the word-timing environment has no onnxruntime")
def test_personmask_writes_a_gray_mask_per_frame_of_the_image_size(tmp_path):
    r = run("personmask", "--check")
    assert r.returncode == 0 and r.stdout.startswith("ok personmask:"), r.stdout + r.stderr
    frames = []
    for i in range(2):
        p = tmp_path / f"f{i:05d}.png"
        im = Image.new("RGB", (160, 96), (20, 80, 160))
        ImageDraw.Draw(im).ellipse([60, 10, 100, 60], fill=(220, 180, 150))
        im.save(p)
        frames.append(str(p))
    r = run("personmask", *frames)
    assert r.returncode == 0, r.stdout + r.stderr
    for f in frames:
        m = f[:-4] + ".mask.png"
        assert os.path.isfile(m) and f"OK {m}" in r.stdout
        mi = Image.open(m)
        assert mi.mode == "L" and mi.size == (160, 96)


@pytest.mark.skipif(not venv_has("onnxruntime", "numpy", "PIL"), reason="the word-timing environment has no onnxruntime")
def test_personmatte_writes_a_mask_for_every_frame_and_ends_with_the_count(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=10:duration=1", "-pix_fmt", "yuv420p", str(clip)], check=True)
    out = tmp_path / "m"
    r = run("personmatte", str(clip), str(out), "fast")
    assert r.returncode == 0 and r.stdout.strip().endswith("done: 10 frames, 160x96"), r.stdout + r.stderr
    assert sorted(os.listdir(out)) == [f"m{i:05d}.png" for i in range(10)]
    assert Image.open(out / "m00000.png").mode == "L"


def test_hdr2sdr_check_and_a_conversion_when_ffmpeg_can_tone_map(tmp_path):
    r = run("hdr2sdr", "--check")
    if r.returncode != 0:
        assert r.stderr.startswith("STOP: hdr2sdr:") and "zscale" in r.stderr or "tonemap" in r.stderr, r.stderr
        pytest.skip("this ffmpeg cannot tone map")
    assert r.stdout.startswith("ok hdr2sdr:")
    src = tmp_path / "hlg.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1", "-pix_fmt", "yuv420p10le",
                    "-c:v", "libx265", "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", "-tag:v", "hvc1", str(src)], check=True)
    out = tmp_path / "sdr.mov"
    r = run("hdr2sdr", str(src), str(out), "--codec", "h264", "--max-short", "90")
    assert r.returncode == 0 and r.stdout.startswith("hdr2sdr: frames=30 first_pts=") and "size=160x90 codec=h264" in r.stdout, r.stdout + r.stderr
    tags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer,color_primaries,pix_fmt,width",
                           "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert tags == "160,yuv420p,bt709,bt709" or tags == "160,yuv420p,bt709,bt709\n".strip(), tags


def test_textboxes_check_names_tesseract_when_it_is_missing_or_reads_a_line_when_it_is_there(tmp_path):
    if not venv_has("pytesseract", "PIL"):
        pytest.skip("the word-timing environment has no pytesseract")
    r = run("textboxes", "--check")
    if r.returncode != 0:
        assert "tesseract" in r.stderr and r.stderr.startswith("STOP: textboxes:"), r.stderr
        pytest.skip("tesseract is not installed here")
    img = tmp_path / "t.png"
    im = Image.new("RGB", (600, 200), "white")
    ImageDraw.Draw(im).text((40, 80), "CLICK THE LINK BELOW", fill="black")
    im.save(img)
    r = run("textboxes", str(img))
    assert r.returncode == 0, r.stderr
    for line in r.stdout.splitlines():
        p = line.split("\t")
        assert len(p) == 6 and p[0] == str(img) and all(v.lstrip("-").isdigit() for v in p[1:5])
