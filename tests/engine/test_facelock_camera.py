"""Face lock-on paid from the camera file (facelock.lock(sources=...)): a cut made from a bigger camera file has its locked picture read out of that
file, so the punch-in costs spare pixels and not sharpness. Real ffmpeg on synthetic footage through a mezzanine line like the prep stage's and the
real cutter (frame for frame, a clip joined from two camera files, a cut with a color correction on it). The tracker is stood in for: the frames,
the encode and the check are the engine's own."""
import json
import os
import shutil
import subprocess
import types

import numpy as np
import pytest

import cut as cutter
import facelock as fl
from helpers_facelock import TAGS, ff

W, H = 270, 480          # the cut in these tests: the mezzanine's shape at a quarter of the size
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg and ffprobe")


def camera_file(path, seconds, size="540x960", rate="30000/1001", tint=""):
    """a camera file: twice the cut's size, 29.97 frames a second (so the 30 fps conversion repeats a frame at 16.7 s), fine grain on it the way a sensor
    leaves grain (detail the half-size cut cannot hold; the grain does not change from frame to frame, which keeps the file small), with sound"""
    ff("-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d={seconds},noise=alls=24:allf=u{tint}", "-f", "lavfi", "-i", f"sine=f=330:d={seconds}", "-c:v", "libx264", "-crf", "8",
       "-pix_fmt", "yuv420p", *TAGS, "-c:a", "aac", "-shortest", path)
    return path


def mezzanine(raw, out):
    """the prep stage's mezzanine line at this size (cover-scale, center crop, 30 fps), without the gain staging"""
    ff("-i", raw, "-map", "0:v:0", "-map", "0:a:0", "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,format=yuv420p",
       "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-g", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", out)
    return out


def joined(parts, out):
    """the parts' mezzanines back to back through the concat demuxer, stream copied"""
    lst = out + ".txt"
    with open(lst, "w") as f:
        f.write("".join(f"file '{p}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out)
    return out


def cut_of(mez, spans, out):
    """the real cutter's render pass -> the EDL a locked picture is made from"""
    pieces = [{"c0": a, "c1": b} for a, b in spans]
    cutter.render({"src": mez, "output": out, "audio_clean": False}, pieces, 30)
    return {"src": mez, "fps": 30, "pieces": pieces}


def frames_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames", "-of", "json", path], capture_output=True, text=True, check=True)
    return int(json.loads(r.stdout)["streams"][0]["nb_frames"])


def numbers_of(path):
    """the cutter's frame number of every frame of a file, in file order: its presentation time on the 30 fps grid. A stream-copied join starts a part of
    a frame late (behind the sound's lead-in), so its numbers start at 1 and a file's frame i is the cutter's frame i + 1"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return [int(round(t * 30)) for t in sorted(float(x.strip().rstrip(",")) for x in r.stdout.split() if x.strip() and x.strip() != "N/A")]


def plain_sources(edl, camera, vf=""):
    """where each piece's picture is in the camera file its working clip was made from: the piece's first frame on the 30 fps grid"""
    return [{"file": camera, "frame": int(round(p["c0"] * 30)), "vf": vf} for p in edl["pieces"]]


def picture(path, size=(W, H)):
    """every frame of a video as RGB, read the accurate way: (n, h, w, 3)"""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0", "-fps_mode", "passthrough", "-vf",
                        "scale=in_color_matrix=bt709:in_range=tv:flags=bicubic+accurate_rnd+full_chroma_int,format=rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, size[1], size[0], 3).astype(np.float32)


def detail(frames):
    """how much fine detail a picture holds: the mean size of its second difference (a Laplacian), on luma"""
    y = frames.mean(axis=3)
    return float(np.abs(4 * y[:, 1:-1, 1:-1] - y[:, :-2, 1:-1] - y[:, 2:, 1:-1] - y[:, 1:-1, :-2] - y[:, 1:-1, 2:]).mean())


def sway(n, amp=(14, 8), at=(135, 190), period=(47, 31)):
    t = np.arange(n)
    return np.stack([at[0] + amp[0] * np.sin(2 * np.pi * t / period[0]), at[1] + amp[1] * np.cos(2 * np.pi * t / period[1])], axis=1)


def plan_for(cut_p, edl, strength=1.0):
    n = frames_of(cut_p)
    spans = fl.spans_of(edl["pieces"], 30, n)
    return fl.plan(sway(n), spans, W, H, strength), spans


def read_all(cut_p, edl, sources):
    """every frame of every piece read from the camera files and checked against the cut (the engine's _camera): what the check measured, a piece"""
    pl, spans = plan_for(cut_p, edl)
    big, src = fl.bigger(sources, edl["pieces"], spans, 30.0, W, H, W, pl["zoom"])
    assert big, src
    n = sum(1 for _ in fl._camera(cut_p, pl, W, H, "30/1", 30.0, *big["size"], big["feeds"], big["seen"]))
    assert n == frames_of(cut_p)
    return big["seen"]


def one_picture(seen):
    """every piece's frames are the cut's own picture and a frame off in either direction is not"""
    for apart, early, late in seen:
        assert apart < fl.SAME and apart < early and apart < late, seen


@pytest.fixture(scope="module")
def shoot(tmp_path_factory):
    """one small shoot: two camera files, their mezzanines, the two joined, and a folder to cut into"""
    d = tmp_path_factory.mktemp("shoot")
    os.makedirs(d / "mez")
    os.makedirs(d / "cuts")
    a = camera_file(str(d / "camera-a.mp4"), 20)
    b = camera_file(str(d / "camera-b.mp4"), 6, tint=",hue=h=90")
    for raw, name in ((a, "a"), (b, "b")):
        mezzanine(raw, str(d / "mez" / f"{name}.mp4"))
    joined([str(d / "mez" / "a.mp4"), str(d / "mez" / "b.mp4")], str(d / "mez" / "ab.mp4"))
    return types.SimpleNamespace(dir=d, a=a, b=b, mez=lambda n: str(d / "mez" / f"{n}.mp4"), cut=lambda n: str(d / "cuts" / f"{n}-cut.mp4"))


# --- the shape of the bigger picture -----------------------------------------------------------------------------------------------------

def test_the_biggest_picture_a_camera_file_fills_keeps_the_cuts_shape():
    assert fl.fits(2160, 3840, 1080, 1920) == (2160, 3840)                 # 4K shot upright: twice the cut
    assert fl.fits(3840, 2160, 1080, 1920) == (1206, 2144)                 # 4K shot on its side: the 9:16 middle of it, a little over the cut
    assert fl.fits(1080, 1920, 1080, 1920) == (1080, 1920)                 # the cut's own size: nothing spare
    assert fl.fits(2160, 3840, 1080, 1920, most=1080 * 1.15) == (1242, 2208)   # read no bigger than a 15% punch-in needs
    assert fl.fits(2160, 3840, 1080, 1920, most=1080 * 1.127) == (1224, 2176)  # rounded up to a width that keeps 9:16 exact
    for w, h in (fl.fits(1728, 3072, 1080, 1920), fl.fits(2160, 3840, 1080, 1920, most=1300)):
        assert w * 16 == h * 9 and w % 2 == 0 and h % 2 == 0


def test_a_camera_file_no_bigger_than_the_cut_is_not_read(monkeypatch):
    pieces = [{"out_in": 0.0, "out_out": 1.0}, {"out_in": 1.0, "out_out": 2.0}]
    spans = [(0, 30), (30, 60)]
    monkeypatch.setattr(fl.os.path, "exists", lambda p: True)
    monkeypatch.setattr(fl, "shown", lambda p: (1080, 1920))
    big, why = fl.bigger([{"file": "/x/clip.mp4", "frame": 278}, {"file": "/x/clip.mp4", "frame": 317}], pieces, spans, 30.0, 1080, 1920, 1080, 1.15)
    assert big is None and why == "the camera file is no bigger than the cut (clip.mp4 fills 1080 x 1920, the cut is 1080 x 1920)"
    monkeypatch.setattr(fl, "shown", lambda p: (2160, 3840) if "big" in p else (1080, 1920))
    big, why = fl.bigger([{"file": "/x/big.mp4", "frame": 0}, {"file": "/x/small.mp4", "frame": 0}], pieces, spans, 30.0, 1080, 1920, 1080, 1.15)
    assert big is None and "small.mp4 fills 1080 x 1920" in why        # one small part and the whole video stays on the cut: one sharpness for every piece
    monkeypatch.setattr(fl, "shown", lambda p: (2160, 3840))
    big, src = fl.bigger([{"file": "/x/a.mp4", "frame": 9, "vf": "lut3d=x"}, {"file": "/x/a.mp4", "frame": 40}], pieces, spans, 30.0, 1080, 1920, 1080, 1.15)
    assert big["size"] == (1242, 2208) and big["feeds"] == [("/x/a.mp4", 9, "lut3d=x"), ("/x/a.mp4", 40, "")]
    assert src == {"mode": "camera", "width": 1242, "height": 2208, "files": [{"file": "/x/a.mp4", "fills": [2160, 3840]}]}
    big, why = fl.bigger([{"file": "/x/a.mp4", "frame": 9}], pieces, spans, 30.0, 1080, 1920, 1080, 1.15)
    assert big is None and why == "its pieces do not each have a camera file"


# --- frame for frame, for real -----------------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_every_piece_is_read_from_its_own_frames_of_the_camera_file(shoot):
    """pieces out of order, one from the first frame, one across the frame the 30 fps conversion repeats: each is the cut's own picture, and the same
    pieces read one frame off are not"""
    cut_p = shoot.cut("one")
    edl = cut_of(shoot.mez("a"), [(18.0, 19.0), (0.0, 0.8), (16.5, 17.2), (5.0333, 6.0)], cut_p)
    sources = plain_sources(edl, shoot.a)
    assert [s["frame"] for s in sources] == [540, 0, 495, 151]
    seen = read_all(cut_p, edl, sources)
    assert len(seen) == 4
    one_picture(seen)
    for step in (1, -1):                                        # the wrong frame, by one
        off = [{**s, "frame": max(0, s["frame"] + step)} for s in sources]
        with pytest.raises(fl.NotTheCut, match=rf"is a frame out of step with the cut \(read one frame {'earlier' if step > 0 else 'later'} it fits"):
            read_all(cut_p, edl, off)


@needs_ffmpeg
def test_a_clip_joined_from_two_camera_files_is_read_from_each_of_them(shoot):
    """two mezzanines joined by stream copy: the joined clip's picture starts a part of a frame late, so the cutter numbers its frames one up, and the
    second part starts where the first one's frames end. Each piece is read from the camera file its frames came from"""
    nums = numbers_of(shoot.mez("ab"))
    na = frames_of(shoot.mez("a"))
    assert nums[0] == 1 and len(nums) == na + frames_of(shoot.mez("b"))     # the offset the mapping has to follow: frame 0 of the file is the cutter's frame 1
    at_b = (nums[na] + 15) / 30                                              # half a second into the second part, on the cutter's clock
    cut_p = shoot.cut("two")
    edl = cut_of(shoot.mez("ab"), [(at_b, at_b + 1.0), (3.0, 4.0), (at_b + 2.0, at_b + 3.0)], cut_p)
    sources = []
    for p in edl["pieces"]:
        i = nums.index(int(round(p["c0"] * 30)))
        sources.append({"file": shoot.a, "frame": i, "vf": ""} if i < na else {"file": shoot.b, "frame": i - na, "vf": ""})
    assert [(os.path.basename(s["file"]), s["frame"]) for s in sources] == [("camera-b.mp4", 15), ("camera-a.mp4", 89), ("camera-b.mp4", 75)]
    one_picture(read_all(cut_p, edl, sources))
    plan, spans = plan_for(cut_p, edl)
    big, info = fl.bigger(sources, edl["pieces"], spans, 30.0, W, H, W, plan["zoom"])
    assert big and info["mode"] == "camera" and sorted(os.path.basename(f["file"]) for f in info["files"]) == ["camera-a.mp4", "camera-b.mp4"]   # one reading, two files


@needs_ffmpeg
def test_another_picture_is_refused(shoot):
    cut_p = shoot.cut("one")
    edl = cut_of(shoot.mez("a"), [(2.0, 3.0), (7.0, 8.0)], cut_p)
    sources = plain_sources(edl, shoot.a)
    wrong = [{**s, "file": shoot.b, "frame": 10 + 20 * i} for i, s in enumerate(sources)]
    with pytest.raises(fl.NotTheCut, match=r"piece 1 \(0.00-1.00 s\) of camera-b.mp4 is not the picture in the cut \(they differ by [\d.]+ levels"):
        read_all(cut_p, edl, wrong)
    past = [{**sources[0], "frame": frames_of(shoot.mez("a")) + 100}, sources[1]]            # past the end of the camera file
    with pytest.raises(fl.NotTheCut, match="frames? short of the cut"):
        read_all(cut_p, edl, past)


# --- the locked picture, for real (the tracker stood in for: the frames, the encode and the check are the engine's own) ---------------

@pytest.fixture
def tracked(monkeypatch):
    """lock() with the tracker's answer given: a face that sways, found on every frame"""
    def go(cut_p, extra=0):
        n = frames_of(cut_p) + extra
        monkeypatch.setattr(fl, "track", lambda video, w: (sway(n), np.full(n, 60.0)))
    return go


@needs_ffmpeg
def test_the_camera_file_pays_for_the_punch_in(shoot, tracked, tmp_path):
    cut_p = shoot.cut("one")
    edl = cut_of(shoot.mez("a"), [(18.0, 19.0), (0.0, 0.8), (16.5, 17.2)], cut_p)
    sources = plain_sources(edl, shoot.a)
    tracked(cut_p)
    from_cut, from_camera = str(tmp_path / "cut.lock.mp4"), str(tmp_path / "camera.lock.mp4")
    r0 = fl.lock(cut_p, from_cut, 1.0, edl["pieces"])
    r1 = fl.lock(cut_p, from_camera, 1.0, edl["pieces"], sources=sources)
    assert r0["source"] == {"mode": "cut"} and not any("camera file" in line for line in fl.summary(r0))      # nothing offered, nothing said
    src = r1["source"]
    wide = fl.fits(540, 960, W, H, most=W * r1["zoom"])                       # read no wider than the punch-in needs
    assert W < wide[0] <= 324 and src["mode"] == "camera" and (src["width"], src["height"]) == wide and src["files"] == [{"file": shoot.a, "fills": [540, 960]}]
    assert src["check"]["levels_apart"] < fl.SAME and src["check"]["a_frame_off_is_worse_by"] > 1
    assert {k: v for k, v in r1.items() if k != "source"} == {k: v for k, v in r0.items() if k != "source"}   # the same plan: the tracker and the maths never saw the camera file
    line = [x for x in fl.summary(r1) if "camera file" in x]
    assert line == [f"the punch-in is paid from the camera file: every frame is read {wide[0]} px wide from camera-a.mp4 (540 x 960 at the cut's shape) and checked against the cut, "
                    f"not enlarged from the {W} px cut"]
    a, b = picture(from_cut), picture(from_camera)
    assert a.shape == b.shape == (frames_of(cut_p), H, W, 3)                  # the cut's size and frame count, either way
    assert np.abs(a - b).mean() < 6                                           # one picture, moved the same way
    assert detail(b) > 1.15 * detail(a)                                       # and more of its fine detail is left
    assert json.load(open(from_camera + ".json")) == r1


@needs_ffmpeg
def test_a_punch_is_read_out_of_the_camera_file_and_dropped_without_one(shoot, tracked, tmp_path):
    """the camera moves with the lock off: a 1.4x punch on the middle piece is a true crop of the camera file about the eye line, and with only the cut
    to read from the punch is dropped and the push still made"""
    from PIL import Image
    cut_p = shoot.cut("moves")
    edl = cut_of(shoot.mez("a"), [(2.0, 3.0), (8.0, 9.5), (12.0, 13.0)], cut_p)
    sources = plain_sources(edl, shoot.a)
    tracked(cut_p)
    moves = [{"kind": "push", "start_s": 0.1, "end_s": 1.0, "ramp": [0.1, 0.9], "z": 1.12}, {"kind": "punch", "start_s": 1.0, "end_s": 2.5, "ramp": [1.0, 1.0], "z": 1.4}]
    from_camera, from_cut = str(tmp_path / "camera.mp4"), str(tmp_path / "cut.mp4")
    r1 = fl.lock(cut_p, from_camera, 0.0, edl["pieces"], sources=sources, moves=moves)
    r0 = fl.lock(cut_p, from_cut, 0.0, edl["pieces"], moves=moves)
    assert [(m["kind"], m["on"], m["z"]) for m in r1["moves"]] == [("push", True, 1.12), ("punch", True, 1.4)]
    assert r1["source"]["mode"] == "camera" and r1["source"]["width"] == fl.fits(540, 960, W, H, most=W * 1.4)[0]   # read as wide as the punch needs
    assert [(m["kind"], m["on"]) for m in r0["moves"]] == [("push", True), ("punch", False)] and "reads soft" in r0["moves"][1]["why"]
    assert any(line.startswith("punch 1.00-2.50 s dropped") for line in fl.summary(r0))
    a, b, c = picture(cut_p), picture(from_camera), picture(from_cut)
    assert np.abs(a[45:] - c[45:]).mean() < 2                                  # past the push the cut plays as cut: no punch was made from it
    i = 52                                                                     # a frame inside the punch (1.73 s)
    pin = r1["pieces"][1]["pin"]
    x0, y0 = pin[0] - pin[0] / 1.4, pin[1] - pin[1] / 1.4
    want = np.asarray(Image.fromarray(a[i].astype(np.uint8)).resize((W, H), Image.LANCZOS, box=(x0, y0, x0 + W / 1.4, y0 + H / 1.4)), np.float32)
    assert np.abs(b[i] - want).mean() < 8 < np.abs(b[i] - a[i]).mean()        # the punch: the cut's own crop about the eye line, not the frame as cut
    assert np.abs(b[80] - a[80]).mean() < 6                                    # back to the full frame on the next joint
    assert detail(b[i:i + 1]) > 1.15 * detail(want[None])                      # and sharper than that crop enlarged from the cut


@needs_ffmpeg
def test_a_camera_file_that_is_not_the_cut_falls_back_to_the_cut_and_says_why(shoot, tracked, tmp_path):
    cut_p = shoot.cut("one")
    edl = cut_of(shoot.mez("a"), [(2.0, 3.0), (7.0, 8.0)], cut_p)
    sources = plain_sources(edl, shoot.a)
    tracked(cut_p)
    out = str(tmp_path / "lock.mp4")
    off = [{**s, "frame": s["frame"] + 1} for s in sources]
    rep = fl.lock(cut_p, out, 1.0, edl["pieces"], sources=off)
    assert rep["source"]["mode"] == "cut" and rep["source"]["why"].startswith("piece 1 (0.00-1.00 s) of camera-a.mp4 is a frame out of step with the cut (read one frame earlier it fits")
    assert rep["frames"] == frames_of(out) == frames_of(cut_p)                # a whole locked picture all the same, from the cut
    said = [x for x in fl.summary(rep) if "camera file" in x]
    assert len(said) == 1 and said[0].startswith(f"the punch-in is enlarged from the {W} px cut, not paid from the camera file: piece 1 (0.00-1.00 s) of camera-a.mp4")
    same = [{**s, "file": cut_p, "frame": int(round(p["out_in"] * 30))} for s, p in zip(sources, edl["pieces"])]   # a "camera file" the cut's own size
    rep = fl.lock(cut_p, out, 1.0, edl["pieces"], sources=same)
    assert rep["source"] == {"mode": "cut", "why": f"the camera file is no bigger than the cut (one-cut.mp4 fills {W} x {H}, the cut is {W} x {H})"}
    assert "again" not in rep["source"]                                       # facts about the file's picture: the same answer next time
    gone = [{**s, "file": str(tmp_path / "unplugged.mp4")} for s in sources]
    rep = fl.lock(cut_p, out, 1.0, edl["pieces"], sources=gone)
    assert rep["source"]["mode"] == "cut" and rep["source"]["why"].startswith("the camera file would not read") and rep["source"]["again"] is True
    stub = str(tmp_path / "half-copied.mp4")
    with open(stub, "wb") as f:
        f.write(open(shoot.a, "rb").read(4096))                               # a copy that stopped: ffprobe reads no picture in it
    rep = fl.lock(cut_p, out, 1.0, edl["pieces"], sources=[{**s, "file": stub} for s in sources])
    assert rep["source"]["again"] is True and rep["frames"] == frames_of(out)  # worth another try once it reads, and a whole picture from the cut meanwhile


@needs_ffmpeg
def test_a_tracker_that_read_more_frames_than_the_cut_has_is_still_one_plain_stop(shoot, tracked, tmp_path, capsys):
    cut_p = shoot.cut("one")
    edl = cut_of(shoot.mez("a"), [(2.0, 3.0)], cut_p)
    sources = plain_sources(edl, shoot.a)
    tracked(cut_p, extra=4)
    out = str(tmp_path / "lock.mp4")
    with pytest.raises(SystemExit) as e:
        fl.lock(cut_p, out, 1.0, edl["pieces"], sources=sources)
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.count("STOP:") == 1 and "the tracker read 34 frames" in err and "and the renderer read 30" in err, err
    assert not os.path.exists(out) and not os.path.exists(out + ".json")


@needs_ffmpeg
def test_the_locked_picture_keeps_the_cuts_colors_and_its_comment_tag(tmp_path):
    """the frames go through RGB and back: they come out the cut's own levels (ffmpeg's quick 8-bit path read about 2 of 255 dark), and the comment tag
    a file carries (some tools leave a mark there) is copied to the locked picture as it is"""
    src, out = str(tmp_path / "bars.mp4"), str(tmp_path / "bars.lock.mp4")
    mark = "a tool's mark: version 1.2 mode=footage shots=1"
    ff("-f", "lavfi", "-i", f"smptebars=s={W}x{H}:r=30:d=1", "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-crf", "6", "-pix_fmt", "yuv420p", *TAGS, "-c:a", "aac",
       "-metadata", f"comment={mark}", src)
    pl = fl.plan(sway(30), [(0, 30)], W, H, 0.0)                              # strength 0: every frame is the whole source frame
    assert fl.render(src, out, pl, W, H, "30/1", W) == 30
    a, b = picture(src), picture(out)
    assert np.abs((b - a).mean(axis=(0, 1, 2))).max() < 0.5                   # no channel drifts
    assert fl.comment_of(out) == fl.comment_of(src) == mark
    plain = str(tmp_path / "plain.mp4")
    ff("-i", src, "-map_metadata", "-1", "-c", "copy", plain)
    fl.render(plain, out, pl, W, H, "30/1", W)
    assert fl.comment_of(out) == ""                                           # and a cut with no mark gets none


@needs_ffmpeg
def test_a_color_correction_on_the_cut_is_put_on_its_camera_frames_too(shoot, tracked, tmp_path):
    """a cut that was color corrected after it was made is no longer the camera file's own picture: each piece's camera frames get the same correction
    put on (the source's `vf`), and the check passes only with it"""
    fix = "eq=brightness=0.08:contrast=1.15"
    plain_cut, graded = shoot.cut("plain"), str(tmp_path / "graded-cut.mp4")
    edl = cut_of(shoot.mez("a"), [(2.0, 3.0), (7.0, 8.0)], plain_cut)
    ff("-i", plain_cut, "-vf", fix, "-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv420p", *TAGS, "-c:a", "copy", graded)
    assert np.abs(picture(graded)[:30] - picture(plain_cut)[:30]).mean() > 2 * fl.SAME       # the correction really is another picture now
    with_fix = plain_sources(edl, shoot.a, fix)
    one_picture(read_all(graded, edl, with_fix))
    with pytest.raises(fl.NotTheCut, match=r"piece 1 .* is not the picture in the cut"):
        read_all(graded, edl, plain_sources(edl, shoot.a))
    tracked(graded)
    out = str(tmp_path / "lock.mp4")
    assert fl.lock(graded, out, 1.0, edl["pieces"], sources=with_fix)["source"]["mode"] == "camera"
    rep = fl.lock(graded, out, 1.0, edl["pieces"], sources=plain_sources(edl, shoot.a))      # without it the engine falls back to the cut's own frames, and says why
    assert rep["source"]["mode"] == "cut" and "is not the picture in the cut" in rep["source"]["why"]
