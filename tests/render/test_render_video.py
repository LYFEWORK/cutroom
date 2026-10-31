"""End to end: render.py turns an edit plan into a finished video through Remotion. Synthetic cuts (a busy test pattern and a voice), so every check
is a measurement of the file that comes out: size, rate, tags, length, loudness, and what is on the frames."""
import json
import re
import shutil
import subprocess

import pytest

import render_helpers as H
from render_helpers import needs_remotion

pytestmark = needs_remotion

SIZES = {"portrait": (1080, 1920), "landscape": (1920, 1080), "feed": (1080, 1350)}


def frame_pair(final, cut, t, size, gw, gh):
    return H.gray(final, t, gw, gh), H.gray(cut, t, gw, gh)


@pytest.mark.parametrize("name", ["portrait", "landscape", "feed"])
def test_every_layer_renders_at_every_size(tmp_path, request, clip, name):
    W, Hh = SIZES[name]
    cut = request.getfixturevalue(f"cut_{name}")
    plan = H.full_plan(cut, clip, size=(W, Hh))
    out = tmp_path / "final.mp4"
    proc = H.run_render(H.write_plan(tmp_path / "edit.json", plan), out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [ln for ln in proc.stderr.splitlines() if ln.strip()], proc.stderr  # nothing on stderr but a STOP line would be

    info = H.probe(out)
    assert (info["w"], info["h"]) == (W, Hh) and info["fps"] == 30.0
    # the end card at 4.8 s holds 2.5 s past the 6 s cut, so the final runs 7.3 s, its sound as long as its picture
    assert abs(info["dur"] - max(H.probe(cut)["dur"], 4.8 + 2.5)) <= 0.1 and abs(info["a_dur"] - info["dur"]) <= 0.1, info
    assert (info["codec"], info["tag"], info["pix"], info["range"]) == ("h264", "avc1", "yuv420p", "tv")
    assert (info["space"], info["primaries"], info["transfer"]) == ("bt709", "bt709", "bt709")
    assert info["audio"] and info["a_rate"] == 48000
    lufs, tp = H.loudness(out)
    assert lufs == pytest.approx(-16, abs=1.0) and tp <= -1.5

    gw, gh = W // 4, Hh // 4
    f0, c0 = frame_pair(out, cut, 0.0, (W, Hh), gw, gh)
    # the hook card is on the first frame, fully formed: the band it sits in is far from the cut's own picture, the rest of the frame is the cut
    y0 = int(0.156 * gh)
    hook_band = H.mean_abs_diff(H.rows(f0, gw, y0, y0 + int(0.2 * gh)), H.rows(c0, gw, y0, y0 + int(0.2 * gh)))
    low = (int(0.72 * gh), int(0.9 * gh))
    rest = H.mean_abs_diff(H.rows(f0, gw, *low), H.rows(c0, gw, *low))
    assert hook_band > 12 and rest < 6, (hook_band, rest)
    # the picture the edit did not touch matches the cut: median difference of 4 levels or less
    assert H.median_abs_diff(H.rows(f0, gw, *low), H.rows(c0, gw, *low)) <= 4
    # the hook leaves when it ends (2.0 s)
    f_after, c_after = frame_pair(out, cut, 2.2, (W, Hh), gw, gh)
    assert H.mean_abs_diff(H.rows(f_after, gw, y0, y0 + int(0.2 * gh)), H.rows(c_after, gw, y0, y0 + int(0.2 * gh))) < hook_band / 2
    # the insert fills the frame at 3.3 s: nothing of the cut is left
    f_in, c_in = frame_pair(out, cut, 3.3, (W, Hh), gw, gh)
    assert H.mean_abs_diff(f_in, c_in) > 25
    # the lower third is up at 1.5 s, in the band just above the platform's bottom edge
    f_lt, c_lt = frame_pair(out, cut, 1.5, (W, Hh), gw, gh)
    band = (int(0.80 * gh), int(0.93 * gh))
    assert H.mean_abs_diff(H.rows(f_lt, gw, *band), H.rows(c_lt, gw, *band)) > 6
    # the end card covers the picture from 4.8 s: dark, nothing like the cut
    f_end, c_end = frame_pair(out, cut, 5.7, (W, Hh), gw, gh)
    assert H.mean(f_end) < 60 < H.mean(c_end) and H.mean_abs_diff(f_end, c_end) > 40
    # and past the cut's end (6 s) the card is still up, over the held picture: dark
    assert H.mean(H.gray(out, 7.0, gw, gh)) < 60


def test_every_look_and_move_renders(tmp_path, cut_portrait, clip, media):
    """the paths a quiet plan never takes: captions with a style, the mark, the mono hook, a pip and a window insert, dynamic whips, a band wipe"""
    lock = media / "lockup.png"
    if not lock.exists():
        H.ffmpeg("-f", "lavfi", "-i", "color=c=0xFF5A1F:s=420x100", "-frames:v", "1", lock)
    theme = {**H.THEME, "lockup_src": str(lock), "lockup_dark_src": str(lock), "lockup_on": True, "caps": True, "skew": 8, "cap_words": 2,
             "caption_stroke": 3, "broll_style": "dynamic", "smooth_motion": False, "hook_look": "mono"}
    plan = H.full_plan(
        cut_portrait, clip, theme=theme,
        words=H.words("So here is the real cost nobody ever talks about when they sell you one", start=0.3), feed={"motion": "soft", "hero": {"look": "bigger_bold", "scale": 1.5, "pick": "page"}, "words": 2, "place": "chest"},
        hook={"lines": ["It costs *more*", "than you think"], "end_s": 2.2, "turn_s": 1.0},
        inserts=[
            {"src": str(clip), "start_s": 1.0, "end_s": 2.0, "mode": "window", "side": "left"},
            {"src": str(clip), "start_s": 2.4, "end_s": 3.4, "mode": "pip"},
            {"src": str(clip), "start_s": 3.4, "end_s": 4.2, "mode": "full", "transition": "whip-l"},
            {"src": str(clip), "start_s": 4.3, "end_s": 4.8, "mode": "full", "transition": "band"},
        ],
        graphics=[{"component": "TitleCard", "start_s": 0.4, "end_s": 1.8, "props": {"text": "The *real* cost"}, "captions": "hide"},
                  {"component": "LowerThird", "start_s": 4.0, "end_s": 5.4, "props": {"name": "Alex Rivera", "role": "Founder"}}],
        zooms=[{"start_s": 2.0, "end_s": 3.0, "z": 1.18, "cx": 500, "cy": 900}],
        sfx="auto", end_card={"at_s": 5.2, "line": "Book a *call*"},
    )
    out = tmp_path / "final.mp4"
    proc = H.run_render(H.write_plan(tmp_path / "edit.json", plan), out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    info = H.probe(out)
    assert (info["w"], info["h"], info["fps"]) == (1080, 1920, 30.0) and abs(info["dur"] - 7.7) <= 0.1  # the end card at 5.2 s holds 2.5 s
    # the mono hook drops the picture to black and white from the turn: a patch of the picture that nothing is drawn over is in color at
    # 0.5 s and without color at 1.6 s
    def color(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-vf", "crop=230:400:850:700,scale=46:80:flags=area,format=rgb24", "-f", "rawvideo", "-"],
                             capture_output=True).stdout
        px = [raw[i : i + 3] for i in range(0, len(raw), 3)]
        return sum(max(p) - min(p) for p in px) / len(px)  # how far apart the channels are: 0 in black and white
    assert color(0.5) > 12 and color(1.6) < 3 and color(2.8) > 12


def test_a_montage_renders_with_voice_music_and_effects(tmp_path, clip, music, click):
    import sound

    vo1 = H.make_voice(tmp_path / "vo1.wav", 3.0)
    plan = {
        "theme": H.THEME, "output": {"size": [1080, 1920], "fps": 30, "crf": 20},
        "montage": {"clips": [{"src": str(clip), "at": 0, "dur": 3.0, "z": [1.0, 1.06]}, {"src": str(clip), "at": 3.0, "dur": 3.0, "in": "push", "inDur": 0.4},
                              {"src": str(clip), "at": 6.0, "dur": 3.0, "in": "dissolve"}, {"src": str(clip), "at": 9.0, "dur": 3.0}],
                    "vo": [{"src": str(vo1), "at": 0.6}]},
        "words": H.words("Most owners lose their best leads before the first call", start=0.8),
        "hook": {"lines": ["Most owners lose", "their *best* leads"], "end_s": 3.0, "look": "bar"},
        "music": {"file": str(music)}, "sfx": [{"file": str(click), "at_s": 3.35, "db": -14}],
        "end_card": {"at_s": 10.2},
    }
    out = tmp_path / "montage.mp4"
    proc = H.run_render(H.write_plan(tmp_path / "montage.json", plan), out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    info = H.probe(out)
    assert (info["w"], info["h"], info["fps"]) == (1080, 1920, 30.0) and abs(info["dur"] - 12.7) <= 0.1  # the end card at 10.2 s holds 2.5 s past the last clip
    lufs, tp = H.loudness(out)
    assert lufs == pytest.approx(-16, abs=1.0) and tp <= -1.5
    assert re.search(r"music: music.wav, [\d.]+ dB under the talk", proc.stdout) and "sfx: 1 effect placed" in proc.stdout
    # the bed sits about 14 dB under the voice where the voice is absent (after the line, 3.6 s to the end), measured on the final
    log = H.ebur_log(out)
    talk = sound.loudness(log, [(1.4, 2.4)])
    alone = sound.loudness(log, [(8.0, 10.0)])
    assert talk is not None and alone is not None
    assert talk - alone == pytest.approx(14, abs=3.0), (talk, alone)


def still_of(tmp_path, plan, frame, name):
    """one frame of a plan through Remotion (no video, no sound): the png's path"""
    pp = H.write_plan(tmp_path / f"{name}.json", plan)
    work = tmp_path / f"work-{name}"
    prep = H.run_render(pp, tmp_path / f"{name}.mp4", "--work", work, "--prep-only")
    assert prep.returncode == 0, prep.stdout + prep.stderr
    png = tmp_path / f"{name}.png"
    r = subprocess.run(["npx", "--no-install", "remotion", "still", "index.ts", "Edit", str(png), f"--props={work / 'props.json'}", f"--public-dir={work / 'public'}", f"--frame={frame}", "--log=error"],
                       cwd=H.REMOTION, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return png


def rgb_at(png, x, y, w=6, h=4):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(png), "-vf", f"crop={w}:{h}:{x}:{y},format=rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
    px = [raw[i : i + 3] for i in range(0, len(raw), 3)]
    return tuple(sum(p[c] for p in px) / len(px) for c in range(3))


def test_an_edit_can_turn_the_mark_on_and_the_theme_can_leave_it_off(tmp_path, small_cut, clip, media):
    lock = media / "mark-lockup.png"
    H.ffmpeg("-f", "lavfi", "-i", "color=c=0xFF5A1F:s=420x100", "-frames:v", "1", lock)
    theme = {**H.THEME, "lockup_src": str(lock), "lockup_dark_src": str(lock), "lockup_on": False}
    base = H.full_plan(small_cut, clip, size=(360, 640), theme=theme, hook=None, graphics=[], zooms=[], inserts=[], end_card=False)
    # at 360 x 640 the mark sits at x 22, y 78, 42 x 10: its middle is at about (43, 83)
    orange = rgb_at(still_of(tmp_path, {**base, "mark": True}, 5, "on"), 40, 81)
    assert orange[0] > 220 and 60 < orange[1] < 120 and orange[2] < 80, orange
    plain = rgb_at(still_of(tmp_path, base, 5, "off"), 40, 81)
    assert not (plain[0] > 220 and 60 < plain[1] < 120 and plain[2] < 80), plain
    # the theme's own switch draws it with nothing said in the edit
    on = rgb_at(still_of(tmp_path, {**base, "theme": {**theme, "lockup_on": True}}, 5, "theme"), 40, 81)
    assert on[0] > 220 and on[2] < 80, on


def test_the_dnas_own_theme_renders(tmp_path, small_cut, clip):
    """the theme exactly as engine/dna.py writes it, from a Brand folder made by dna.py init"""
    brand = tmp_path / "Brand"
    init = subprocess.run(["python3", str(H.ENGINE / "dna.py"), "init", str(brand), "--name", "Demo Co", "--id", "demo", "--handle", "@democo", "--accent", "#ff5a1f",
                           "--ink", "#101114", "--font", str(H.FONT)], capture_output=True, text=True, cwd=H.REPO)
    theme = subprocess.run(["python3", str(H.ENGINE / "dna.py"), "theme", str(brand)], capture_output=True, text=True, cwd=H.REPO)
    if init.returncode != 0 or theme.returncode != 0:
        pytest.skip("engine/dna.py could not make a Brand folder here: " + (init.stderr or theme.stderr).strip()[-200:])
    th = json.loads(theme.stdout)
    assert {"id", "name", "colors", "surface", "font_family", "font_faces", "hook_look", "hook_top", "end_card", "smooth_motion", "music_level"} <= set(th)
    plan = H.full_plan(small_cut, clip, size=(360, 640), theme=th)
    out = tmp_path / "final.mp4"
    proc = H.run_render(H.write_plan(tmp_path / "edit.json", plan), out, "--brand", brand)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (H.probe(out)["w"], H.probe(out)["h"]) == (360, 640)


def test_the_picture_holds_the_cuts_last_frame_under_the_end_card(tmp_path, small_cut, clip):
    """the mechanics behind the card: with holdFrom set, every frame past it is the frame named, which is the cut's own picture there. The card
    is taken out of the props so the held picture can be seen; before the hold the picture plays as it is."""
    plan = H.full_plan(small_cut, clip, size=(360, 640), hook=None, graphics=[], zooms=[], inserts=[], end_card={})
    work = tmp_path / "work-hold"
    prep = H.run_render(H.write_plan(tmp_path / "hold.json", plan), tmp_path / "hold.mp4", "--work", work, "--prep-only")
    assert prep.returncode == 0, prep.stdout + prep.stderr
    props = json.loads((work / "props.json").read_text())
    assert props["holdFrom"] == 178 and props["durationInFrames"] == 255
    props["endCard"] = None
    (work / "props.json").write_text(json.dumps(props))

    def still(frame):
        png = tmp_path / f"hold-{frame}.png"
        r = subprocess.run(["npx", "--no-install", "remotion", "still", "index.ts", "Edit", str(png), f"--props={work / 'props.json'}", f"--public-dir={work / 'public'}", f"--frame={frame}", "--log=error"],
                           cwd=H.REMOTION, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return H.gray(png, 0, 90, 160)

    held, later, live = still(200), still(240), still(150)
    cut = {f: H.gray(small_cut, f / 30, 90, 160) for f in (150, 177, 178, 179)}
    d = H.mean_abs_diff
    assert d(held, later) < 1.0, d(held, later)  # past the cut every frame is the same picture
    # and it is the cut's own frame 178, to the frame: a still sits within 2 levels of its source frame at this scale, and about 4 from the frames beside it
    assert d(held, cut[178]) < 2.5 < min(d(held, cut[177]), d(held, cut[179])), [round(d(held, cut[f]), 2) for f in (177, 178, 179)]
    assert d(live, cut[150]) < 2.5 < d(live, cut[178]), (d(live, cut[150]), d(live, cut[178]))  # before the hold the picture plays as it is


def test_the_bed_plays_through_the_end_card_and_fades_out_with_it(tmp_path, small_cut, clip, music):
    """an Edit with music and an end card: the final runs the card's 2.5 s past the cut, the bed is heard under the card, and it is on its way
    out over the last stretch (the bed fades over its last 1.5 s)"""
    import sound

    plan = H.full_plan(small_cut, clip, size=(360, 640), hook=None, graphics=[], zooms=[], inserts=[], end_card={}, music={"file": str(music)})
    out = tmp_path / "card.mp4"
    proc = H.run_render(H.write_plan(tmp_path / "card.json", plan), out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "end card: from 6.00 s for 2.5 s, the cut's last frame held under it, so the video runs to 8.50 s" in proc.stdout
    info = H.probe(out)
    assert abs(info["dur"] - 8.5) <= 0.1 and abs(info["a_dur"] - 8.5) <= 0.15, info
    log = H.ebur_log(out)
    under_card = sound.loudness(log, [(6.6, 7.6)])
    tail = sound.loudness(log, [(7.9, 8.5)])  # the meter reads 0.4 s blocks every 0.1 s: the last three readings
    assert under_card is not None and under_card > -45, under_card                # the bed is there under the card
    assert tail is not None and tail < under_card - 8, (under_card, tail)         # and on its way out at the very end


def test_remotion_refuses_a_theme_with_no_accent(tmp_path):
    """the renderer's own check, not render.py's: applyTheme throws on a theme with no id or no colors.accent"""
    for theme in ({"id": "x", "colors": {"ink": "#000000"}}, {"colors": {"accent": "#ff0000"}}):
        props = tmp_path / "props.json"
        props.write_text(json.dumps({"src": "cut.mp4", "theme": theme, "durationInFrames": 30, "fps": 30, "width": 360, "height": 640}))
        (tmp_path / "public").mkdir(exist_ok=True)
        r = subprocess.run(["npx", "--no-install", "remotion", "still", "index.ts", "Edit", str(tmp_path / "x.png"), f"--props={props}", f"--public-dir={tmp_path / 'public'}", "--frame=0", "--log=error"],
                           cwd=H.REMOTION, capture_output=True, text=True)
        assert r.returncode != 0 and "applyTheme: the theme needs an id and colors.accent" in r.stdout + r.stderr


def test_a_render_that_cannot_start_stops_plainly(tmp_path, cut_portrait, clip, monkeypatch):
    """no Remotion install: one line that says what to run"""
    import render

    monkeypatch.setattr(render, "REMOTION", tmp_path / "no-remotion")
    with pytest.raises(SystemExit):
        render.run_remotion("Edit", tmp_path / "p.json", tmp_path, tmp_path / "o.mp4", 20, 2, 5.0)
