"""render.py reads an edit plan: every plan that cannot render stops with ONE plain line before Remotion starts, and a good plan is turned
into the right props, conformed clips and linked theme files. Nothing here starts a render (--prep-only), so it is quick."""
import json
import shutil
import subprocess
import sys

import pytest

import render_helpers as H
from render_helpers import needs_ffmpeg

pytestmark = needs_ffmpeg


def prep(tmp_path, plan, *extra):
    """run render.py --prep-only on a plan; returns (process, work folder)"""
    pp = H.write_plan(tmp_path / "edit.json", plan)
    work = tmp_path / "work"
    return H.run_render(pp, tmp_path / "out.mp4", "--work", work, "--prep-only", *extra), work


def props_of(work):
    return json.loads((work / "props.json").read_text())


# ---------------------------------------------------------------- the plan must be sound: one STOP line each
def broken(small_cut, clip, **changes):
    plan = H.full_plan(small_cut, clip, size=(540, 960))
    for k, v in changes.items():
        if v is None:
            plan.pop(k, None)
        else:
            plan[k] = v
    return plan


CASES = [
    ("no cut", dict(cut=None), "needs a cut"),
    ("cut file missing", dict(cut="/nowhere/cut.mp4"), "cut not found"),
    ("no theme", dict(theme=None), "needs a theme"),
    ("theme without accent", dict(theme={"id": "x", "colors": {"ink": "#000"}}), "colors.accent"),
    ("theme without id", dict(theme={"colors": {"accent": "#fff"}}), "an id and colors.accent"),
    ("theme with a look nobody draws", dict(theme={**H.THEME, "hook_look": "neon"}), "hook_look must be one of"),
    ("hook look nobody draws", dict(hook={"lines": ["a"], "look": "neon"}), "hook.look must be one of"),
    ("hook with no words", dict(hook={"end_s": 3}), "hook needs lines"),
    ("hook lines that are one string", dict(hook={"lines": "a whole sentence"}), "hook.lines must be a list of strings"),
    ("graphic nobody registered", dict(graphics=[{"component": "Confetti", "start_s": 1, "end_s": 2}]), "is not registered in remotion/graphics/index.ts (registered: LowerThird, TitleCard)"),
    ("odd output size", dict(output={"size": [541, 960]}), "must be even"),
    ("size nobody can read", dict(output={"size": "big"}), "output.size must look like"),
    ("insert after the end", dict(inserts=[{"src": "x.mp4", "start_s": 99, "end_s": 100}]), "after the end of the video"),
    ("insert that ends before it starts", dict(inserts=[{"src": "x.mp4", "start_s": 3, "end_s": 2}]), "ends (2) before it starts (3)"),
    ("insert mode nobody has", dict(inserts=[{"src": "x.mp4", "start_s": 0.1, "end_s": 0.9, "mode": "tile"}]), "mode must be one of"),
    ("transition nobody has", dict(inserts=[{"src": "x.mp4", "start_s": 0.1, "end_s": 0.9, "transition": "spin"}]), "transition must be one of"),
    ("insert clip missing", dict(inserts=[{"src": "/nowhere/b.mp4", "start_s": 0.1, "end_s": 0.9}]), "inserts[0].src not found"),
    ("zoom with no scale", dict(zooms=[{"start_s": 0.1, "end_s": 0.9}]), "zooms[0].z must be a number"),
    ("end card past the end", dict(end_card={"at_s": 50}), "past the end of the video"),
    ("captions mode nobody has", dict(captions_mode="burned"), "captions_mode must be one of"),
    ("words with no text", dict(words=[{"start_s": 0, "end_s": 1}]), "words[0] needs text"),
    ("music file missing", dict(music={"file": "/nowhere/m.wav"}), "music file not found"),
    ("sfx with nothing to play", dict(sfx=[{"at_s": 1}]), "needs a file or a family"),
    ("sfx nobody understands", dict(sfx="loud"), "sfx must be a list"),
    ("feed that is not an object", dict(feed="quiet"), "feed must be an object"),
]


@pytest.mark.parametrize("name,changes,expect", CASES, ids=[c[0] for c in CASES])
def test_a_plan_that_cannot_render_stops_with_one_plain_line(tmp_path, small_cut, clip, name, changes, expect):
    proc, work = prep(tmp_path, broken(small_cut, clip, **changes))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    stops = [ln for ln in proc.stderr.splitlines() if ln.startswith("STOP:")]
    assert len(stops) == 1, proc.stderr
    assert expect in stops[0], stops[0]
    assert "Traceback" not in proc.stderr
    assert not (work / "props.json").exists()


def test_a_bad_invocation_is_one_usage_line_like_every_engine_script():
    for args in ([], ["plan.json"], ["plan.json", "--out"], ["plan.json", "--out", "x.mp4", "--nope"]):
        r = subprocess.run([sys.executable, str(H.RENDER), *args], capture_output=True, text=True)
        assert r.returncode == 2 and r.stderr.startswith("STOP: usage: render.py EDIT.json --out FINAL.mp4") and len(r.stderr.strip().splitlines()) == 1 and not r.stdout, (args, r.stderr)
    assert subprocess.run([sys.executable, str(H.RENDER), "--help"], capture_output=True, text=True).returncode == 0


def test_an_old_python_stops_with_one_line_before_anything_is_read():
    code = f"import sys; sys.version_info = (3, 11, 9, 'final', 0); sys.path.insert(0, {str(H.ENGINE)!r}); import render"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 2 and r.stderr.count("STOP:") == 1 and "Python 3.12 or newer" in r.stderr and "Traceback" not in r.stderr


def test_a_missing_plan_file_stops_plainly(tmp_path):
    proc = H.run_render(tmp_path / "nope.json", tmp_path / "o.mp4")
    assert proc.returncode == 2 and "edit plan not found" in H.stop_line(proc)


def test_a_plan_that_is_not_json_stops_plainly(tmp_path):
    (tmp_path / "edit.json").write_text("{not json")
    proc = H.run_render(tmp_path / "edit.json", tmp_path / "o.mp4")
    assert proc.returncode == 2 and "is not valid JSON" in H.stop_line(proc)


def test_an_hdr_cut_stops_plainly(tmp_path, clip):
    hdr = tmp_path / "hdr.mp4"
    H.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-bsf:v", "h264_metadata=colour_primaries=9:transfer_characteristics=18:matrix_coefficients=9", hdr)  # BT.2020, HLG
    proc, _ = prep(tmp_path, H.full_plan(hdr, clip, size=(360, 640)))
    assert proc.returncode == 2 and "is HDR (arib-std-b67)" in H.stop_line(proc)


def test_a_file_that_is_not_a_font_stops_plainly(tmp_path, small_cut, clip):
    fake = tmp_path / "font.ttf"
    fake.write_text("this is not a font")
    theme = {**H.THEME, "font_faces": [{"weight": "400", "src": str(fake)}]}
    proc, _ = prep(tmp_path, broken(small_cut, clip, theme=theme))
    assert proc.returncode == 2 and "is not a font file" in H.stop_line(proc)


def test_a_theme_file_that_is_missing_stops_plainly(tmp_path, small_cut, clip):
    theme = {**H.THEME, "lockup_src": "logos/none.png", "lockup_on": True}
    proc, _ = prep(tmp_path, broken(small_cut, clip, theme=theme))
    assert proc.returncode == 2 and "theme.lockup_src not found" in H.stop_line(proc)


# ---------------------------------------------------------------- a good plan becomes props, conformed clips and linked files
def test_props_carry_the_plan_and_the_theme_files_are_linked(tmp_path, small_cut, clip):
    plan = H.full_plan(small_cut, clip, size=(540, 960), words=H.words("one two three four five six"))
    proc, work = prep(tmp_path, plan)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    p = props_of(work)
    assert (p["width"], p["height"], p["fps"], p["durationInFrames"]) == (540, 960, 30, 180)
    assert p["src"] == "cut.mp4" and (work / "public" / "cut.mp4").stat().st_size == small_cut.stat().st_size
    assert p["captions_mode"] == "feed" and len(p["words"]) == 6
    assert p["hook"]["end_s"] == 2.0 and p["hook"]["lines"][1] == "nobody *tells* you"
    assert p["graphics"][0]["component"] == "LowerThird" and p["graphics"][0]["props"]["name"] == "Alex Rivera"
    assert p["zooms"][0]["z"] == 1.12
    # the theme's end card line and handle fill in what the plan's end card leaves out
    assert p["endCard"] == {"at_s": 4.8, "line": "Follow for *more*", "handle": "@testco"}
    # the font is a file in the public folder and the theme points at it
    src = p["theme"]["font_faces"][0]["src"]
    assert src == "brand-testbrand/Inter-Variable.ttf" and (work / "public" / src).stat().st_size == H.FONT.stat().st_size
    assert p["theme"]["colors"]["accent"] == "#FF5A1F"
    assert p["feed"] is None and p["mark"] is None and p["safe"] is None


def test_no_words_means_no_caption_pages_and_the_plan_can_turn_them_off(tmp_path, small_cut, clip):
    proc, work = prep(tmp_path, H.full_plan(small_cut, clip, size=(540, 960)))
    assert props_of(work)["captions_mode"] == "none"
    proc, work = prep(tmp_path, H.full_plan(small_cut, clip, size=(540, 960), words=H.words("a b c"), captions_mode="external"))
    assert props_of(work)["captions_mode"] == "external"


def test_an_insert_is_conformed_to_the_frame_silent_with_a_second_of_handle(tmp_path, small_cut, clip):
    plan = H.full_plan(small_cut, clip, size=(540, 960))
    proc, work = prep(tmp_path, plan)
    assert proc.returncode == 0, proc.stderr
    ins = props_of(work)["inserts"][0]
    assert ins["src"] == "broll/insert-00.mp4" and ins["from_s"] == 0 and ins["mode"] == "full" and ins["transition"] == "soft"
    out = work / "public" / ins["src"]
    info = H.probe(out)
    assert (info["w"], info["h"], info["fps"]) == (540, 960, 30.0) and not info["audio"]
    assert info["dur"] == pytest.approx(1.5 + 1.0, abs=0.1)  # the insert's 1.5 s and a second of handle
    # dense keyframes (-g 15) so a seek is exact
    frames = H.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pict_type", "-of", "csv=p=0", out]).stdout.split()
    keys = [i for i, t in enumerate(frames) if t.startswith("I")]
    assert keys and keys[0] == 0 and max(b - a for a, b in zip(keys, keys[1:] + [len(frames)])) <= 15


def test_the_conform_is_kept_until_something_changes(tmp_path, small_cut, clip):
    plan = H.full_plan(small_cut, clip, size=(540, 960))
    _, work = prep(tmp_path, plan)
    first = (work / "public" / "broll" / "insert-00.mp4").stat().st_mtime_ns
    prep(tmp_path, plan)
    assert (work / "public" / "broll" / "insert-00.mp4").stat().st_mtime_ns == first
    plan["inserts"][0]["end_s"] = 3.5  # a shorter window needs a different clip
    prep(tmp_path, plan)
    assert (work / "public" / "broll" / "insert-00.mp4").stat().st_mtime_ns != first


def test_a_still_picture_can_be_an_insert(tmp_path, small_cut, clip):
    still = tmp_path / "still.png"
    H.ffmpeg("-f", "lavfi", "-i", "mandelbrot=size=300x200", "-frames:v", "1", still)
    plan = H.full_plan(small_cut, still, size=(540, 960))
    proc, work = prep(tmp_path, plan)
    assert proc.returncode == 0, proc.stderr
    info = H.probe(work / "public" / "broll" / "insert-00.mp4")
    assert (info["w"], info["h"]) == (540, 960) and info["dur"] == pytest.approx(2.5, abs=0.15)


def test_a_clip_can_say_where_the_crop_sits(tmp_path, small_cut, clip):
    import render

    cmd = render.conform_cmd("a.mp4", "b.mp4", 1080, 1920, 30, 2.0, 3.5, pos="20% 80%")
    vf = cmd[cmd.index("-vf") + 1]
    assert "crop=1080:1920:x='(iw-ow)*0.2':y='(ih-oh)*0.8'" in vf and "fps=30" in vf and "-an" in cmd and ["-g", "15"] == cmd[cmd.index("-g") : cmd.index("-g") + 2]
    assert cmd[cmd.index("-ss") + 1] == "2" and cmd[cmd.index("-t") + 1] == "3.50"
    with pytest.raises(SystemExit):
        render.conform_cmd("a.mp4", "b.mp4", 1080, 1920, 30, 0, 1, pos="left")


def test_the_registered_graphics_are_read_from_the_registry():
    import render

    assert {"LowerThird", "TitleCard"} <= render.registered_graphics()


def test_a_graphic_can_be_asked_for_by_the_id_the_dna_gave_it(tmp_path, small_cut, clip):
    brand = tmp_path / "Brand"
    brand.mkdir()
    (brand / "dna.json").write_text(json.dumps({"graphics": {"built": [{"id": "name-tag", "component": "LowerThird"}]}}))
    plan = broken(small_cut, clip, graphics=[{"id": "name-tag", "start_s": 0.2, "end_s": 0.9, "props": {"name": "A"}}])
    proc, work = prep(tmp_path, plan, "--brand", brand)
    assert proc.returncode == 0, proc.stderr
    g = props_of(work)["graphics"][0]
    assert g["component"] == "LowerThird" and "id" not in g


def test_a_file_a_graphic_shows_is_linked_into_the_public_folder(tmp_path, small_cut, clip):
    img = tmp_path / "my logo.png"
    H.ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", img)
    plan = broken(small_cut, clip, graphics=[{"component": "LowerThird", "start_s": 0.2, "end_s": 0.9, "props": {"name": "A", "logo": str(img), "fine": "/not/a/file"}}])
    proc, work = prep(tmp_path, plan)
    assert proc.returncode == 0, proc.stderr
    pr = props_of(work)["graphics"][0]["props"]
    assert pr["logo"].startswith("assets/") and pr["logo"].endswith("-my-logo.png") and (work / "public" / pr["logo"]).is_file()
    assert pr["fine"] == "/not/a/file" and pr["name"] == "A"


def test_the_dnas_prefixed_theme_paths_are_found_in_the_brand_folder(tmp_path, small_cut, clip):
    brand = tmp_path / "Brand"
    (brand / "assets" / "fonts").mkdir(parents=True)
    shutil.copy(H.FONT, brand / "assets" / "fonts" / "Inter-Variable.ttf")
    (brand / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n")
    theme = {**H.THEME, "font_faces": [{"weight": "100 900", "src": "brand-testbrand/assets/fonts/Inter-Variable.ttf"}],
             "lockup_src": "assets/logo.png", "lockup_dark_src": "brand-testbrand/assets/logo.png", "lockup_on": True}
    proc, work = prep(tmp_path, broken(small_cut, clip, theme=theme), "--brand", brand)
    assert proc.returncode == 0, proc.stderr
    t = props_of(work)["theme"]
    assert t["font_faces"][0]["src"] == "brand-testbrand/Inter-Variable.ttf"
    assert t["lockup_src"] == "brand-testbrand/logo.png" and t["lockup_dark_src"] == "brand-testbrand/logo.png"
    assert (work / "public" / "brand-testbrand" / "logo.png").is_file()


def test_the_theme_can_be_a_file(tmp_path, small_cut, clip):
    (tmp_path / "theme.json").write_text(json.dumps(H.THEME))
    proc, work = prep(tmp_path, broken(small_cut, clip, theme="theme.json"))
    assert proc.returncode == 0, proc.stderr
    assert props_of(work)["theme"]["id"] == "testbrand"


def test_the_theme_can_turn_the_hook_off_and_the_end_card_needs_a_place(tmp_path, small_cut, clip):
    theme = {**H.THEME, "hook_on": False}
    proc, work = prep(tmp_path, broken(small_cut, clip, theme=theme, end_card=None))
    assert "text hooks off" in proc.stdout and "no end_card.at_s" in proc.stdout
    p = props_of(work)
    assert p["hook"] is None and p["endCard"] is None


def test_a_plan_can_switch_its_own_end_card_off(tmp_path, small_cut, clip):
    proc, work = prep(tmp_path, broken(small_cut, clip, end_card={"on": False}))
    assert props_of(work)["endCard"] is None


def test_the_word_engines_file_is_a_plan_too(tmp_path, small_cut, clip):
    engine_words = {"words": [{"text": "hello", "start": 0.2, "end": 0.5}, {"text": "there", "start": 0.5, "end": 0.9, "filler": False}]}
    proc, work = prep(tmp_path, broken(small_cut, clip, words=engine_words))
    assert proc.returncode == 0, proc.stderr
    assert props_of(work)["words"] == [{"text": "hello", "start_s": 0.2, "end_s": 0.5}, {"text": "there", "start_s": 0.5, "end_s": 0.9}]


# ---------------------------------------------------------------- where the face is
def test_a_face_box_follows_the_cut_into_the_output_frame():
    import render

    same = render.scale_face({"cx": 540, "cy": 960, "h": 400}, (1080, 1920), (1080, 1920))
    assert same == {"cx": 540.0, "cy": 960.0, "h": 400.0}
    assert render.scale_face({"cx": 0.5, "cy": 0.25, "h": 0.2}, (1080, 1920), (1080, 1920)) == {"cx": 540.0, "cy": 480.0, "h": 384.0}  # fractions of the cut
    # a vertical cut cover-fitted into a 1920 x 1080 frame is scaled 1.78 and cropped top and bottom
    wide = render.scale_face({"cx": 540, "cy": 960, "h": 400}, (1080, 1920), (1920, 1080))
    assert wide["cx"] == pytest.approx(960) and wide["cy"] == pytest.approx(540) and wide["h"] == pytest.approx(711.1, abs=0.2)
    # a 4K cut into a 1080 frame: pixels of the cut, halved
    assert render.scale_face({"cx": 1080, "cy": 1920, "h": 800}, (2160, 3840), (1080, 1920)) == {"cx": 540.0, "cy": 960.0, "h": 400.0}
    # a box measured on a notional canvas (engine/face.py track: 1080 wide, 608 tall for a 16:9 picture)
    assert render.scale_face({"cx": 540, "cy": 304, "h": 100, "W": 1080, "H": 608}, (3840, 2160), (1920, 1080)) == {"cx": 960.0, "cy": 540.0, "h": pytest.approx(177.6, abs=0.1)}


def test_the_face_engines_files_are_a_plan_too(tmp_path, small_cut, clip):
    track = {"W": 1080, "H": 1920, "samples": {"0": {"cx": 540, "cy": 900, "w": 300, "h": 380}, "1": {"cx": 560, "cy": 910, "w": 300, "h": 380}},
             "median": {"cx": 550, "cy": 905, "w": 300, "h": 380}}
    proc, work = prep(tmp_path, broken(small_cut, clip, face=None, track=track))
    p = props_of(work)
    assert proc.returncode == 0, proc.stderr
    # the cut is 360 x 640 and the output 540 x 960: a notional canvas is read as fractions of the cut
    assert p["face"]["cx"] == pytest.approx(550 / 1080 * 540) and p["track"]["1"]["cy"] == pytest.approx(910 / 1920 * 960)
    # an anchor that found no face is a guess, and is not used
    proc, work = prep(tmp_path, broken(small_cut, clip, face={"cx": 100, "cy": 100, "h": 50, "fallback": True}, track=None))
    assert props_of(work)["face"] is None and "fallback guess" in proc.stdout


def test_a_plan_with_a_hook_and_no_face_says_so(tmp_path, small_cut, clip):
    proc, _ = prep(tmp_path, broken(small_cut, clip, face=None))
    assert "no face box" in proc.stdout


# ---------------------------------------------------------------- sound: the pure parts
def test_a_normalise_filter_is_a_gain_and_limits_only_when_the_peaks_would_pass():
    import render

    f = render.normalise_filter(-20.0, -8.0)
    assert f == "volume=4.000dB"  # -8 dBTP + 4 dB stays under -2
    f = render.normalise_filter(-17.0, -1.0)
    assert f.startswith("volume=1.000dB,aresample=192000,alimiter=limit=") and f.endswith("aresample=48000")
    assert "volume=2.000dB" in render.normalise_filter(-17.0, -9.0, bias=1.0)


def test_an_effect_is_moved_into_a_pause_or_left_out():
    import render

    ws = [{"start_s": 1.0, "end_s": 1.4}, {"start_s": 1.5, "end_s": 1.9}, {"start_s": 3.0, "end_s": 3.4}]
    assert render.into_pause(0.5, ws) == 0.5  # already in a pause
    t = render.into_pause(1.2, ws)  # on a word: the nearest free time, clear of the 60 ms after a word and of the next word
    assert t is not None and not any(w["start_s"] - 0.02 <= x <= w["end_s"] + 0.06 for w in ws for x in (t, t + 0.12))
    assert render.into_pause(1.2, [{"start_s": 0.0, "end_s": 3.0}]) is None  # no pause within half a second
    assert render.into_pause(1.2, []) == 1.2


def test_effects_budget_gaps_and_edges(tmp_path):
    import render
    import sound

    job = {"sfx": {"mode": "auto", "per_min": 12, "under_db": 14}, "dur": 10.0,
           "words": [{"text": "w", "start_s": 0.5 + 0.5 * i, "end_s": 0.7 + 0.5 * i} for i in range(12)],
           "graphics": [{"start_s": 0.2, "end_s": 2}, {"start_s": 3.0, "end_s": 5}, {"start_s": 9.9, "end_s": 10}],
           "inserts": [{"mode": "full", "start_s": 5.0, "end_s": 6.5}, {"mode": "full", "start_s": 6.5, "end_s": 8.0}, {"mode": "window", "start_s": 8.2, "end_s": 9.0}],
           "clips": [], "end_card": {"at_s": 9.0}}
    ev = render.auto_sfx(job, sound)
    assert len(ev) == 3  # a video under 15 s keeps a floor of three
    ts = [e["at_s"] for e in ev]
    assert ts == sorted(ts) and all(b - a >= 1.0 for a, b in zip(ts, ts[1:]))  # never two within a second
    assert all(0.5 <= t <= 9.7 for t in ts)  # none in the first 0.5 s or the last 0.3 s
    assert all(e["db"] == -14 for e in ev) and {e["family"] for e in ev} <= {"pop", "click", "shutter"}
    # not on a word, and not in the 60 ms after one
    assert not any(w["start_s"] - 0.02 <= t <= w["end_s"] + 0.06 for t in ts for w in job["words"])


def test_an_effect_lands_at_the_level_asked_against_the_speaker(tmp_path):
    import numpy as np
    import render
    import sound

    sr = render.SR
    rng = np.random.default_rng(1)
    voice = rng.normal(0, 0.05, (sr * 4, 2))  # speech-ish noise at about -26 dBFS rms
    vref_db = 20 * np.log10(0.05)
    click = np.zeros(sr // 5)
    click[:2000] = rng.normal(0, 0.5, 2000) * np.hanning(4000)[:2000]
    Y = np.zeros_like(voice)
    ev = [{"clip": click, "at_s": 0.5, "db": -14.0, "centred": False, "family": "click"},
          {"clip": click, "at_s": 2.0, "db": -14.0, "centred": False, "family": "click"},
          {"clip": click, "at_s": 3.0, "db": -14.0, "centred": True, "family": "punch"}]
    assert render.place_sfx(Y, ev, vref_db) == 3
    for at in (0.5, 2.0, 3.0):
        a0 = int(at * sr)
        part = np.abs(Y[a0 : a0 + sr // 5]).max(axis=1)  # the louder side
        k = len(part) // 960
        lv = np.sqrt((part[: k * 960].reshape(k, 960) ** 2).mean(axis=1))
        lv = lv[lv > 1e-6]
        assert 20 * np.log10(np.percentile(lv, 90)) == pytest.approx(vref_db - 14, abs=0.5)
    left, right = np.abs(Y[int(0.5 * sr) : int(0.7 * sr), 0]).max(), np.abs(Y[int(0.5 * sr) : int(0.7 * sr), 1]).max()
    left2, right2 = np.abs(Y[int(2.0 * sr) : int(2.2 * sr), 0]).max(), np.abs(Y[int(2.0 * sr) : int(2.2 * sr), 1]).max()
    assert (left < right) != (left2 < right2)  # off-centre, and the sides swap
    c = Y[int(3.0 * sr) : int(3.2 * sr)]
    assert np.allclose(c[:, 0], c[:, 1])  # a centred sound is the same on both sides


def test_an_effect_on_a_word_is_called_out(capsys):
    import render

    render.check_notes([{"at_s": 1.1, "family": "pop"}, {"at_s": 2.5, "family": "click"}], [{"text": "hello", "start_s": 1.0, "end_s": 1.3}])
    out = capsys.readouterr().out
    assert "pop effect at 1.10 s lands on the word 'hello'" in out and "click" not in out
