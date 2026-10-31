"""Face lock-on (facelock.py): the plan maths with no video in it, the camera moves on top of the lock, and the tracker and the renderer for real.

The real tracker is Apple Vision, which finds real faces only. Wherever a test needs a face it controls, a stub stands in for the helper
(helpers_facelock.py): it finds a red dot and a green dot, the "eyes", and answers in the helper's own format, so the engine's frame reading,
tracking maths, smoothing, plan, resampling and encode are all its own code on a synthetic picture. The real helper is run once, on a picture
with nobody in it."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import facelock as fl
from helpers_facelock import eyes_video, faceless_video, ff, make_stub, swiftc_works, use_stub

ENGINE = os.path.dirname(os.path.abspath(fl.__file__))
W, H = 1080, 1920


def sway(n, amp=(60, 30), at=(540, 700), period=(47, 31)):
    """a head that sways smoothly around `at`: (n, 2) eye midpoints"""
    t = np.arange(n)
    return np.stack([at[0] + amp[0] * np.sin(2 * np.pi * t / period[0]), at[1] + amp[1] * np.cos(2 * np.pi * t / period[1])], axis=1)


def boxes(pl):
    return np.array([fl.box(pl, i, W, H) for i in range(len(pl["follow"]))])


def on_screen(anchor, pl):
    """where each tracked eye midpoint lands in the output"""
    return pl["pin"] + pl["zoom"] * (anchor - pl["follow"])


# --- the plan maths ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("strength", [0.3, 0.6, 1.0])
def test_no_frame_edge_ever_shows(strength):
    """every output frame is cut from inside the source, on every frame of every piece, at any strength: a small sway, a walk across the frame
    that runs past the cap, a piece near the frame's corner and a piece with holes in its track"""
    rng = np.random.default_rng(7)
    a = np.concatenate([sway(90), sway(240, amp=(260, 140)), sway(60, amp=(40, 40), at=(180, 260)), sway(150) + rng.normal(0, 3, (150, 2))])
    a[400:430] = np.nan
    a[470:474] = np.nan
    spans = [(0, 90), (90, 330), (330, 390), (390, 540)]
    pl = fl.plan(a, spans, W, H, strength)
    b = boxes(pl)
    assert (b[:, 0] >= 0).all() and (b[:, 1] >= 0).all() and (b[:, 2] <= W).all() and (b[:, 3] <= H).all()
    assert 1.0 < pl["zoom"] <= 1 + fl.CAP + 1e-9
    assert np.allclose(b[:, 2] - b[:, 0], W / pl["zoom"]) and np.allclose(b[:, 3] - b[:, 1], H / pl["zoom"])   # one punch-in for the whole video


def test_strength_zero_is_a_no_op():
    a = np.concatenate([sway(120, amp=(200, 90)), sway(80, at=(300, 900))])
    pl = fl.plan(a, [(0, 120), (120, 200)], W, H, 0.0)
    assert pl["zoom"] == 1.0
    assert np.allclose(boxes(pl), [0, 0, W, H])                 # every output frame is the whole source frame, untouched
    assert all(p["strength"] == 0 for p in pl["pieces"])


def test_a_long_gap_eases_back_to_rest_and_a_short_one_is_ridden_through():
    a = sway(300, amp=(80, 0), period=(600, 31))               # a slow lean to one side: well off rest by the middle of the take
    gap = a.copy()
    gap[100:200] = np.nan                                      # 100 frames with no face (the speaker turned away): longer than the hold
    pl = fl.plan(gap, [(0, 300)], W, H, 1.0)
    rest = pl["pieces"][0]["rest"]
    assert np.abs(pl["follow"][135:165] - rest).max() < 0.5    # the middle of the gap sits at rest: no guessing where the speaker went
    assert np.abs(pl["follow"][60] - rest).max() > 5 and np.abs(pl["follow"][240] - rest).max() > 5   # and it follows again either side
    assert np.abs(np.diff(pl["follow"], axis=0)).max() < 4     # eased there and back: never a snap from one frame to the next
    b = boxes(pl)
    assert (b[:, 0] >= 0).all() and (b[:, 2] <= W).all()

    short = a.copy()
    short[140:150] = np.nan                                    # 10 frames (a blink of the detector): inside the hold
    ps = fl.plan(short, [(0, 300)], W, H, 1.0)
    full = fl.plan(a, [(0, 300)], W, H, 1.0)
    assert np.abs(ps["follow"] - full["follow"]).max() < 1.0   # bridged, not eased: the frame keeps following


def test_every_piece_of_the_cut_gets_its_own_rest_point():
    """two pieces of a take with the head in different places: one rest point for the whole cut would have to span both, a rest point a piece does not"""
    a = np.concatenate([sway(120, amp=(30, 15), at=(380, 640)), sway(120, amp=(30, 15), at=(700, 820))])
    one = fl.plan(a, [(0, 240)], W, H, 1.0)
    two = fl.plan(a, [(0, 120), (120, 240)], W, H, 1.0)
    r0, r1 = (p["rest"] for p in two["pieces"])
    assert abs(r0[0] - 380) < 8 and abs(r1[0] - 700) < 8 and abs(r0[1] - 640) < 8 and abs(r1[1] - 820) < 8
    assert two["zoom"] < 1.08 < one["zoom"]                    # the joint is another moment of the take: its jump is not paid for in punch-in
    assert all(abs(p["strength"] - 1.0) < 1e-6 for p in two["pieces"])


def test_past_the_cap_one_piece_follows_less_and_the_rest_keep_their_strength():
    a = np.concatenate([sway(150, amp=(40, 20)), sway(240, amp=(300, 60), period=(240, 31))])
    pl = fl.plan(a, [(0, 150), (150, 390)], W, H, 1.0, cap=0.15)
    calm, wide = pl["pieces"]
    assert abs(pl["zoom"] - 1.15) < 1e-9                       # never past the cap
    assert abs(calm["strength"] - 1.0) < 1e-6 and calm["want"] < 1.15
    assert wide["want"] > 1.5 and 0.1 < wide["strength"] < 0.5  # it wanted far more than the cap, so it follows less
    b = boxes(pl)
    assert (b[:, 0] >= 0).all() and (b[:, 2] <= W).all() and (b[:, 1] >= 0).all() and (b[:, 3] <= H).all()
    rep = {"strength": 1.0, "cap": 0.15, "zoom": pl["zoom"], "frames": 390, "frames_without_face": 0, "travel": fl.travel(a, pl),
           "pieces": [{"out_in": p["f0"] / 30, "out_out": p["f1"] / 30, "frames_with_face": p["found"], "strength": p["strength"], "want": p["want"]} for p in pl["pieces"]]}
    lines = fl.summary(rep)
    assert len(lines) == 2 and "punch-in 15.0%" in lines[0]
    assert "piece 2 (5.00-13.00 s)" in lines[1] and "past the 15% cap" in lines[1] and f"follows at {wide['strength']:.2f}, not 1" in lines[1]   # the report says which piece and how much


def test_strength_one_pins_the_face_and_a_lower_strength_leaves_its_share_of_the_movement():
    a = sway(240, amp=(50, 25), period=(120, 90))              # slow enough that the smoothing takes nothing off it
    full = fl.plan(a, [(0, 240)], W, H, 1.0)
    assert np.ptp(on_screen(a, full), axis=0).max() < 2.0      # the eye line holds still on screen
    assert np.abs(on_screen(a, full) - full["pieces"][0]["pin"]).max() < 2.0
    part = fl.plan(a, [(0, 240)], W, H, 0.6)
    left = np.ptp(on_screen(a, part), axis=0) / (np.ptp(a, axis=0) * part["zoom"])
    assert np.allclose(left, 0.4, atol=0.03)                   # 0.6 follows 60% of the movement and leaves 40%
    assert part["zoom"] < full["zoom"]                         # and costs less punch-in


def test_a_piece_with_no_face_plays_as_shot_at_the_same_punch_in():
    a = np.concatenate([sway(120), np.full((60, 2), np.nan)])
    pl = fl.plan(a, [(0, 120), (120, 180)], W, H, 1.0)
    assert pl["pieces"][1]["found"] == 0
    b = boxes(pl)[120:]
    assert np.allclose(b, b[0]) and np.allclose((b[0][0] + b[0][2]) / 2, W / 2) and np.allclose((b[0][1] + b[0][3]) / 2, H / 2)   # still, centred


def test_a_read_that_jumps_to_another_face_is_thrown_out():
    a = sway(120, amp=(20, 10))
    size = np.full(120, 380.0)
    a[60] = (900, 300)                                         # one frame on a poster behind the speaker
    clean = fl.reject(a, size, [(0, 120)])
    assert np.isnan(clean[60]).all() and np.isfinite(np.delete(clean, 60, axis=0)).all()
    assert np.abs(np.diff(fl.plan(a, [(0, 120)], W, H, 1.0, size=size)["follow"], axis=0)).max() < 6   # the frame does not lurch after it


def test_the_pieces_cover_every_frame_and_no_usable_pieces_is_one_piece():
    pieces = [{"out_in": 0.0, "out_out": 1.2}, {"out_in": 1.2, "out_out": 4.7667}, {"out_in": 4.7667, "out_out": 12.7}]
    assert fl.spans_of(pieces, 30, 381) == [(0, 36), (36, 143), (143, 381)]
    assert fl.spans_of(pieces, 30, 384) == [(0, 36), (36, 143), (143, 384)]   # a few frames past the last out_out belong to the last piece
    assert fl.spans_of([{"out_in": 0.0}], 30, 90) == [(0, 90)] and fl.spans_of(None, 30, 90) == [(0, 90)]


def test_facepts_lines_become_eye_midpoints_and_a_frame_with_no_face_is_a_hole():
    anchor, size = fl.parse_facepts(["/t/f000001.jpg\t100\t80\t60\t60\t120\t100\t150\t104", "/t/f000002.jpg\tnone"], 2.0)
    assert anchor[0].tolist() == [270.0, 204.0] and size[0] == 120.0
    assert np.isnan(anchor[1]).all() and np.isnan(size[1])


# --- the camera moves on top of the lock (a push, a pull-out and a punch) --------------------------------------------------------------

def test_each_move_is_one_size_a_frame_and_the_full_frame_outside_it():
    moves = [{"kind": "push", "start_s": 1.0, "end_s": 3.0, "ramp": [1.0, 2.0], "z": 1.2}, {"kind": "pull", "start_s": 4.0, "end_s": 6.0, "ramp": [5.0, 6.0], "z": 1.2},
             {"kind": "punch", "start_s": 7.0, "end_s": 8.0, "ramp": [7.0, 7.0], "z": 1.4}]
    s = fl.scales(moves, 300, 30.0)
    assert s[:30].tolist() == [1.0] * 30 and s[30] == 1.0 and s[45] == pytest.approx(1.1) and s[60:90].tolist() == pytest.approx([1.2] * 30)
    assert s[90] == 1.0                                                 # back to the full frame on the joint at 3.0 s, never eased back on screen
    assert s[120:150].tolist() == pytest.approx([1.2] * 30) and s[165] == pytest.approx(1.1) and s[180] == 1.0   # the pull-out opens tight on its cut
    assert s[210:240].tolist() == pytest.approx([1.4] * 30) and s[240] == 1.0
    assert (np.diff(s[30:60]) >= 0).all() and (np.diff(s[150:180]) <= 0).all()   # one way each, no snap in the middle


@pytest.mark.parametrize("strength", [0.0, 1.0])
def test_a_move_never_shows_a_frame_edge(strength):
    a = np.concatenate([sway(120, amp=(200, 90)), sway(120, amp=(40, 20), at=(160, 300))])
    pl = fl.plan(a, [(0, 120), (120, 240)], W, H, strength)
    pl["scale"] = fl.scales([{"kind": "punch", "start_s": 0.5, "end_s": 7.5, "ramp": [0.5, 0.5], "z": 1.4}], 240, 30.0)
    b = boxes(pl)
    assert (b[:, 0] >= -1e-6).all() and (b[:, 1] >= -1e-6).all() and (b[:, 2] <= W + 1e-6).all() and (b[:, 3] <= H + 1e-6).all()
    assert np.allclose(b[:, 2] - b[:, 0], W / fl.zooms(pl)) and fl.zooms(pl)[100] == pytest.approx(1.4 * pl["zoom"])   # the punch is in every frame's rectangle


def test_a_punch_is_kept_only_where_the_camera_file_pays_for_it():
    p = {"kind": "punch", "start_s": 1, "end_s": 3, "ramp": [1, 1], "z": 1.4}
    push = {"kind": "push", "start_s": 4, "end_s": 6, "ramp": [4, 5], "z": 1.12}
    full, short, none = fl.paid([p, push], 2.0), fl.paid([p, push], 1.3), fl.paid([p, push], None)
    assert full[0]["on"] and full[0]["z"] == 1.4 and short[0]["on"] and short[0]["z"] == 1.3 and short[0]["asked"] == 1.4
    assert not none[0]["on"] and "soft" in none[0]["why"] and not fl.paid([p], 1.2)[0]["on"]
    assert all(m["on"] and m["z"] == 1.12 for m in (full[1], short[1], none[1]))   # a push enlarges from the cut like an emphasis zoom
    lines = fl.summary({"strength": 0, "pieces": [], "travel": [[0, 0], [0, 0]], "W": 1080, "source": {"mode": "cut"}, "moves": short + none[:1]})
    assert lines[0].startswith("punch 1.00-3.00 s at 1.30x, down from 1.40x") and lines[2].startswith("punch 1.00-3.00 s dropped:")


# --- the tracker and the renderer, on a face the stub finds -------------------------------------------------------------------------

def test_a_swaying_face_comes_out_pinned(tmp_path, monkeypatch):
    """a face that sways 48 px across and 28 px up and down over a moving picture, tracked and locked, then tracked again on the result"""
    use_stub(monkeypatch, fl, tmp_path)
    src, out = eyes_video(tmp_path / "sway.mp4"), str(tmp_path / "sway.lock.mp4")
    rep = fl.lock(src, out, 1.0)
    assert rep["frames"] == 90 and rep["frames_without_face"] == 0 and len(rep["pieces"]) == 1
    assert 1.05 < rep["zoom"] <= 1.15 and rep["pieces"][0]["strength"] == 1.0
    before, _ = fl.track(src, 540)
    after, _ = fl.track(out, 540)
    assert len(after) == 90 and np.isfinite(after).all()
    raw, locked = np.ptp(before, axis=0), np.ptp(after, axis=0)
    assert raw[0] > 40 and raw[1] > 22                          # it really swayed
    assert locked[0] < 8 and locked[1] < 8                      # and now the eye line holds within a few pixels
    assert np.abs(after.mean(axis=0) - rep["pieces"][0]["pin"]).max() < 4      # where the report says it is pinned
    assert json.load(open(out + ".json")) == rep
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,nb_frames,color_space,color_transfer,color_primaries", "-of", "json", out],
                           capture_output=True, text=True, check=True)
    st = {s["codec_type"]: s for s in json.loads(probe.stdout)["streams"]}
    assert (st["video"]["width"], st["video"]["height"], st["video"]["nb_frames"]) == (540, 960, "90") and "audio" in st      # same size, same frames, the sound kept
    assert [st["video"].get(k) for k in ("color_space", "color_transfer", "color_primaries")] == ["bt709"] * 3


def test_every_piece_gets_its_own_rest_point_in_the_rendered_picture(tmp_path, monkeypatch):
    """the same face in two places, cut in two pieces: each piece is pinned where its own head sat, and the one punch-in is the same on both sides"""
    use_stub(monkeypatch, fl, tmp_path)
    a = eyes_video(tmp_path / "a.mp4", secs=2, at=(120, 260), amp=(10, 6))
    b = eyes_video(tmp_path / "b.mp4", secs=2, at=(330, 520), amp=(10, 6))
    lst = tmp_path / "list.txt"
    lst.write_text(f"file '{a}'\nfile '{b}'\n")
    joined = str(tmp_path / "ab.mp4")
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", joined)
    pieces = [{"out_in": 0.0, "out_out": 2.0}, {"out_in": 2.0, "out_out": 4.0}]
    out = str(tmp_path / "ab.lock.mp4")
    rep = fl.lock(joined, out, 1.0, pieces)
    one = fl.lock(joined, str(tmp_path / "one.lock.mp4"), 1.0)            # the same video with no pieces: one rest point for both heads
    assert len(rep["pieces"]) == 2 and rep["zoom"] < 1.08 < one["zoom"]   # the jump between the two heads is not paid for in punch-in
    r0, r1 = rep["pieces"][0]["rest"], rep["pieces"][1]["rest"]
    assert r1[0] - r0[0] > 150 and r1[1] - r0[1] > 150                    # each piece has the rest point of its own head
    after, _ = fl.track(out, 540)
    first, second = after[5:55], after[65:115]
    assert np.ptp(first, axis=0).max() < 6 and np.ptp(second, axis=0).max() < 6


def test_the_command_line_locks_a_picture_and_says_what_it_did(tmp_path, monkeypatch, capsys):
    use_stub(monkeypatch, fl, tmp_path)
    src, out = eyes_video(tmp_path / "sway.mp4"), str(tmp_path / "locked.mp4")
    edl = tmp_path / "cut.edl.json"
    edl.write_text(json.dumps({"pieces": [{"out_in": 0.0, "out_out": 1.5}, {"out_in": 1.5, "out_out": 3.0}]}))
    monkeypatch.setattr(sys, "argv", ["facelock.py", src, out, "--strength", "1", "--edl", str(edl), "--cap", "0.2", "--width", "270"])
    fl._main()
    said = capsys.readouterr().out
    assert said.startswith("strength 1: punch-in ") and "2 pieces each with its own rest point" in said and "a face in 90 of 90 frames" in said
    rep = json.load(open(out + ".json"))
    assert rep["cap"] == 0.2 and len(rep["pieces"]) == 2
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", out], capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "270,480"                             # --width: a smaller picture cut from the same frames


def test_a_video_with_no_face_and_a_strength_that_locks_nothing_are_plain_stops(tmp_path, monkeypatch, capsys):
    use_stub(monkeypatch, fl, tmp_path)
    src, out = faceless_video(tmp_path / "empty.mp4"), str(tmp_path / "out.mp4")
    with pytest.raises(SystemExit) as e:
        fl.lock(src, out, 0.6)
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.startswith("STOP: no face in any frame") and err.count("STOP:") == 1, err
    with pytest.raises(SystemExit) as e:
        fl.lock(src, out, 0.0)
    assert e.value.code == 2 and "locks nothing" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        fl.lock(src, out, 0.6, cap=1.5)
    assert "is not a punch-in" in capsys.readouterr().err
    assert not os.path.exists(out)
    r = subprocess.run([sys.executable, os.path.join(ENGINE, "facelock.py"), src, out], capture_output=True, text=True)   # no --strength: there is no default
    assert r.returncode == 2 and r.stderr.startswith("STOP: usage: facelock.py") and r.stderr.count("STOP:") == 1 and "Traceback" not in r.stdout + r.stderr


def test_a_helper_that_fails_or_answers_short_is_one_plain_stop(tmp_path, monkeypatch, capsys):
    src = faceless_video(tmp_path / "empty.mp4")
    for mode, line in (("fail", "STOP: facepts is down: exit 3: the stub was told to fail"), ("short", "STOP: facepts is down: 29 lines for 30 frames")):
        use_stub(monkeypatch, fl, tmp_path, mode)
        with pytest.raises(SystemExit) as e:
            fl.track(src, 540)
        assert e.value.code == 2 and capsys.readouterr().err.strip() == line
    monkeypatch.setattr(fl.swiftbin, "ensure", lambda helper_dir: make_stub(tmp_path, "dots"))
    anchor, size = fl.track(src, 540)                                    # and the same picture through a helper that answers: a hole for every frame
    assert anchor.shape == (30, 2) and np.isnan(anchor).all() and np.isnan(size).all()


def test_an_hdr_picture_is_refused_before_anything_is_tracked(tmp_path, monkeypatch, capsys):
    use_stub(monkeypatch, fl, tmp_path)
    hlg = str(tmp_path / "hlg.mov")
    ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=1", "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error",
       "-color_trc", "arib-std-b67", "-color_primaries", "bt2020", "-colorspace", "bt2020nc", "-tag:v", "hvc1", hlg)
    with pytest.raises(SystemExit) as e:
        fl.lock(hlg, str(tmp_path / "out.mp4"), 1.0)
    assert e.value.code == 2 and "is HDR (arib-std-b67): lock the SDR cut, not the camera file" in capsys.readouterr().err


# --- the real helper, once ------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(not swiftc_works(), reason="the tracker is Apple Vision: it needs macOS and swiftc to build engine/swift/facepts")
def test_the_real_helper_runs_and_finds_no_face_in_a_picture_with_nobody_in_it(tmp_path, capsys):
    """no stub here: swiftbin builds (or confirms) the real facepts binary, and the engine tracks a grey moving picture through it"""
    src = faceless_video(tmp_path / "empty.mp4")
    binary = fl.swiftbin.ensure(os.path.join(ENGINE, "swift", "facepts"))
    assert os.path.isfile(binary) and os.access(binary, os.X_OK)
    anchor, size = fl.track(src, 540)
    assert anchor.shape == (30, 2) and np.isnan(anchor).all() and np.isnan(size).all()
    with pytest.raises(SystemExit) as e:
        fl.lock(src, str(tmp_path / "out.mp4"), 0.6)
    assert e.value.code == 2 and capsys.readouterr().err.startswith("STOP: no face in any frame")
