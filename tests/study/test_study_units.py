"""Unit tests: the selftest cases as real tests, plus the edges the selftest leaves out. Nothing here needs a network or a model."""
import re
import subprocess
import sys

import pytest
from PIL import Image

from studykit import STUDY, study


@pytest.mark.parametrize("url,want", [
    ("https://www.instagram.com/reel/Cx1Ab2Cd3Ef/?stkn=MTlxcnpmb216aWpqdA==", "ig-cx1ab2cd3ef"),
    ("https://www.instagram.com/reels/Cx1Ab2Cd3Ef/", "ig-cx1ab2cd3ef"),
    ("https://instagram.com/someone/reel/ABC123/", "ig-abc123"),
    ("https://www.instagram.com/p/XyZ_9-1/", "ig-xyz_9-1"),
    ("https://www.instagram.com/share/reel/BAbCdEf/", "ig-share-babcdef"),
    ("https://www.tiktok.com/@dev/video/7301234567890", "tt-7301234567890"),
    ("https://vm.tiktok.com/ZMabc123/", "tt-zmabc123"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5", "yt-dqw4w9wgxcq"),
    ("https://m.youtube.com/watch?feature=share&v=AbC_123-xyz", "yt-abc_123-xyz"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "yt-dqw4w9wgxcq"),
    ("https://www.youtube.com/shorts/AbCdEf12345", "yt-abcdef12345"),
    ("https://www.youtube.com/live/AbCdEf12345?si=1", "yt-abcdef12345"),
    ("https://x.com/someone/status/1790000000000000000", "x-1790000000000000000"),
    ("https://twitter.com/u/status/123", "x-123"),
])
def test_url_slug(url, want):
    assert study.url_slug(url) == want


def test_url_slug_fallback_is_stable_and_shaped():
    a, b = study.url_slug("https://example.com/some/video?utm=1"), study.url_slug("https://example.com/some/video?utm=2")
    assert a == b and re.fullmatch(r"example-com-some-video-[0-9a-f]{6}", a)
    assert re.fullmatch(r"example-com-[0-9a-f]{6}", study.url_slug("https://example.com"))
    assert study.url_slug("https://example.com/a") != study.url_slug("https://example.com/b")


def test_clean_url_and_is_url():
    assert study.clean_url("https://www.instagram.com/reel/AB/?stkn=1&igsh=2#x") == "https://www.instagram.com/reel/AB/"
    assert study.clean_url("https://www.youtube.com/watch?v=abc&utm=1") == "https://www.youtube.com/watch?v=abc"
    assert study.clean_url(" https://youtu.be/abc?si=1 ") == "https://youtu.be/abc"
    assert [study.is_url(x) for x in ("https://a.b/c", "HTTP://a.b", "/tmp/x.mp4", "ftp://x", "reel.mp4")] == [True, True, False, False, False]


SAMPLES = [(0.0, ["HOOK"]), (0.25, ["HOOK"]), (0.5, ["HOOK TEXT"]), (1.5, ["NEXT"]), (1.75, ["NEXT"]), (3.0, ["CUT A"]), (3.25, ["CUT A"]),
           (3.5, ["CUT A"]), (3.75, ["CUT B"]), (4.0, ["CUT B"]), (4.25, ["CUT B"])]


def test_cluster_gap_beat_and_typewriter():
    got = study.cluster_moments(SAMPLES, [3.6], 0.25, 10.0)
    assert [(m["t0"], m["t1"]) for m in got] == [(0.0, 0.75), (1.5, 2.0), (3.0, 3.75), (3.75, 4.5)]
    assert got[0]["lines"] == ["HOOK TEXT"]          # typewriter partials fold into the longest


def test_cluster_tail_fragment_joins_the_moment_before():
    got = study.cluster_moments([(0.0, ["A WORD"]), (0.25, ["A WORD"]), (0.5, ["A WORD"]), (0.75, ["A WORD"]), (1.0, ["TAIL"])], [0.95], 0.25, 5)
    assert [(m["t0"], m["t1"], m["lines"]) for m in got] == [(0.0, 1.25, ["A WORD", "TAIL"])]


def test_cluster_edges():
    assert study.cluster_moments([], [], 0.25, 10) == []
    assert len(study.cluster_moments([(0, ["a b"]), (0.5, ["a b"])], [], 0.25, 5)) == 1          # one missing sample does not split
    assert len(study.cluster_moments([(0, ["a b"]), (0.8, ["a b"])], [], 0.25, 5)) == 2          # a gap over 0.6 s does
    long = study.cluster_moments([(i * 0.25, ["WORD"]) for i in range(40)], [], 0.25, 20)
    assert [round(m["t1"] - m["t0"], 2) for m in long] == [6.0, 4.0]                             # never over 6 s
    assert study.cluster_moments([(9.9, ["LATE"])], [], 0.25, 10.0)[0]["t1"] == 10.0             # never past the end
    many = study.cluster_moments([(0.0, [f"line number {i} here" for i in range(15)])], [], 0.25, 10)
    assert len(many[0]["lines"]) == 12


def test_persistent_text_is_dropped_by_share_of_frames():
    ps = [(i * 0.25, ["Brandname", "word %d" % i] if i % 2 else ["K Brandname"]) for i in range(30)] + [(8.0, ["Brandnane"])]
    kept, names = study.drop_persistent(ps)
    assert len(names) == 1 and "brandn" in names[0].lower()
    assert all("brandna" not in l.lower() for _, ls in kept for l in ls) and len(kept) == 15
    assert study.drop_persistent(ps[:10])[1] == []                                              # under 6 s: left alone
    title = [(i * 0.25, ["BIG TITLE"]) for i in range(30)]
    assert study.drop_persistent(title, total=150)[1] == []                                      # a fifth of the frames: a title, kept
    kept, names = study.drop_persistent(title, total=32)
    assert names == ["BIG TITLE"] and kept == []                                                 # nine tenths of the frames: a mark, dropped


def test_persistent_text_in_other_scripts_and_the_edge_of_the_share():
    words = ["привет", "дом", "кот", "лес", "море"]
    cyr = [(i * 0.25, ["ЛОГОТИП", words[i % 5]]) for i in range(30)]
    kept, names = study.drop_persistent(cyr, total=30)
    assert len(names) == 1 and all("ЛОГОТИП" not in l for _, ls in kept for l in ls)
    edge = [(i * 0.25, ["CAPTION ONE"]) for i in range(18)]
    assert study.drop_persistent(edge, total=30)[1] == ["CAPTION ONE"]                           # 18 of 30 is exactly 60%
    assert study.drop_persistent(edge, total=31)[1] == []                                        # 18 of 31 is under it


def test_a_long_marks_garbled_misreadings_go_with_it_but_real_captions_stay():
    mark = "Presented by"
    ps = [(i * 0.25, [mark]) for i in range(40)]
    ps += [(10.0 + i * 0.25, [v]) for i, v in enumerate(["Presenel", "Presenied og", "cented byy", "Preseniaas"])]       # OCR misreadings of the mark
    ps += [(12.0, ["Presented by the best cook in town, tonight"]), (12.25, ["BIG HEADLINE"]), (12.5, ["Pressed bay"]), (12.75, ["Butter"])]
    kept, names = study.drop_persistent(ps, total=52)
    left = [l for _, ls in kept for l in ls]
    assert names == [mark] and "Presenel" not in left and "cented byy" not in left and "Preseniaas" not in left
    assert "Presented by the best cook in town, tonight" in left and "BIG HEADLINE" in left and "Butter" in left      # a longer sentence and other words are not the mark
    short = [(i * 0.25, ["Butter"]) for i in range(40)] + [(10.0, ["Button"])]
    assert study.drop_persistent(short, total=41)[0] == [(10.0, ["Button"])]      # a short mark gets no fuzzy match: too easy to mistake another word for it


def test_noise_rule_counts_letters_in_any_script():
    kept, gone = study.drop_noise([{"t0": 1.0, "t1": 1.25, "lines": ["/ Brn"]}, {"t0": 2.0, "t1": 2.5, "lines": ["work; vork"]},
                                   {"t0": 3.0, "t1": 3.5, "lines": ["BIG REAL HEADLINE"]}, {"t0": 5.0, "t1": 6.0, "lines": ["Go"]},
                                   {"t0": 7.0, "t1": 7.25, "lines": ["这是一个很长的标题文字内容"]}, {"t0": 8.0, "t1": 8.25, "lines": ["标题"]}])
    assert [m["t0"] for m in kept] == [3.0, 5.0, 7.0] and len(gone) == 3


@pytest.mark.parametrize("a,b,want", [(0.0, 10.0, [(0.0, 5.0), (5.0, 10.0)]), (2.0, 4.5, [(2.0, 4.5)]), (0.0, 6.0, [(0.0, 6.0)]), (1.0, 7.5, [(1.0, 4.25), (4.25, 7.5)]), (1.0, 7.0, [(1.0, 7.0)])])
def test_split_span(a, b, want):
    assert study.split_span(a, b) == want


def test_split_span_long_shot_is_thirteen_parts_none_over_six_seconds():
    parts = study.split_span(0.0, 76.0)
    assert len(parts) == 13 and all(y - x <= 6.0 + 1e-6 for x, y in parts) and parts[0][0] == 0.0 and parts[-1][1] == 76.0


@pytest.mark.parametrize("t,want", [(12.39, 12.2), (12.4, 12.4), (0.1, 0.0), (0.0, 0.0), (59.99, 59.8), (-0.1, -0.2)])
def test_grid_time(t, want):
    assert study.grid_time(t) == want


def test_pick_even_keeps_first_and_last_and_never_repeats():
    assert study.pick_even(list(range(10)), 4) == [0, 3, 6, 9]
    assert study.pick_even(list(range(3)), 8) == [0, 1, 2] and study.pick_even(list(range(9)), 1) == [0] and study.pick_even([], 5) == []
    got = study.pick_even(list(range(100)), 48)
    assert got[0] == 0 and got[-1] == 99 and len(got) == len(set(got)) <= 48


def test_shots_from_cuts():
    assert study.shots_from([5, 10], 20) == [(0.0, 5), (5, 10), (10, 20)]
    assert study.shots_from([0.1, 5], 20) == [(0.1, 5), (5, 20)]            # a first shot of 0.1 s is a flicker, not a shot
    assert study.shots_from([], 7.5) == [(0.0, 7.5)]


def test_source_conflict(tmp_path):
    assert study.source_conflict({}, "https://a.b/c") is None
    assert study.source_conflict({"url": "https://a.b/c"}, "https://a.b/c?utm=1") is None
    assert study.source_conflict({"url": "https://a.b/c"}, "https://a.b/d") == "https://a.b/c"
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    assert study.source_conflict({"origin_file": str(f.resolve())}, str(f)) is None
    assert study.source_conflict({"origin_file": str(f.resolve())}, str(tmp_path / "b.mp4")) == str(f.resolve())
    assert study.source_conflict({"url": "https://a.b/c"}, str(f)) == "https://a.b/c"
    assert study.source_conflict({"origin_file": str(f.resolve())}, "https://a.b/c") == str(f.resolve())


@pytest.mark.parametrize("text,wall", [
    ("ERROR: [Instagram] x: Requested content is not available, rate-limit reached or login required.", True),
    ("Sign in to confirm you're not a bot", True), ("Use --cookies-from-browser or --cookies for the authentication", True),
    ("This video is private", True), ("HTTP Error 404: Not Found", False), ("Unsupported URL: https://x.y", False)])
def test_login_wall_wording(text, wall):
    assert bool(study.LOGIN_WALL.search(text)) is wall


def test_speech_stats():
    segs = [{"t0": 1.0, "t1": 3.0, "text": "one two three four"}, {"t0": 3.0, "t1": 7.0, "text": "five six seven eight"}]
    assert study.speech_stats(segs) == (80, "one two three four")
    assert study.speech_stats([]) == (None, "")
    late = study.speech_stats([{"t0": 4.0, "t1": 6.0, "text": "starts after the hook window"}])
    assert late == (150, "")                                                                    # no first words when speech starts after 3 s
    assert study.speech_stats([{"t0": 0.0, "t1": 0.2, "text": "hi"}])[0] == 60                  # a span under a second counts as a second
    assert study.speech_stats([{"t0": 0.0, "t1": 5.0, "text": " ".join(f"w{i}" for i in range(20))}])[1].count(" ") == 11   # first 12 words


def test_whisper_lines_become_segments_in_any_script():
    out = ("\n[00:00:00.000 --> 00:00:02.140]   Cuando hagas un video hablado, entra caminando a cámara.\n"
           "[00:00:02.280 --> 00:00:03.860]   [MUSIC]\n[00:00:03.900 --> 00:00:04.500]   (applause)\n[00:00:04.500 --> 00:00:05.000]   ♪ ♪\n"
           "[00:00:05.000 --> 00:00:06.000]   Привет, мир\n[00:00:06.000 --> 00:00:07.000]   你好，世界\n[00:00:07.000 --> 00:00:07.500]   ...\n"
           "[00:00:07.500 --> 00:00:08.000]   Music\n[01:02:03.500 --> 01:02:04.250]   late line\n[00:00:09.000 --> 00:00:10.000]   [BLANK_AUDIO]\nnot a segment line\n")
    segs = study.whisper_segments(out)
    assert [s["text"] for s in segs] == ["Cuando hagas un video hablado, entra caminando a cámara.", "Привет, мир", "你好，世界", "late line"]
    assert segs[0] == {"t0": 0.0, "t1": 2.14, "text": segs[0]["text"]} and segs[-1]["t0"] == 3723.5 and segs[-1]["t1"] == 3724.25
    assert study.whisper_segments("") == []


def test_transcript_md_marks_no_speech():
    assert "(No speech: the audio track is silent.)" in study.transcript_md("X", [], "the audio track is silent")
    md = study.transcript_md("X", [{"t0": 65.5, "t1": 70.0, "text": "late line"}], "")
    assert "[01:05.5 - 01:10.0] late line" in md


def test_text_helpers():
    assert study.ascii_label("Café ✨ 日本") == "Cafe"
    assert study.cell("a | b\n  c") == "a / b c" and study.cell("x" * 50, 10) == "x" * 10 + "..."
    assert study.tail_of("one\n\nlast line  with   spaces\n") == "last line with spaces" and study.tail_of("") == "no output"
    assert study.fmt_t(65.5) == "01:05.5" and study.fmt_t(0) == "00:00.0"
    assert study.slugify("My Reel: Part 2!") == "my-reel-part-2"


def test_font_falls_back_to_the_pillow_default(monkeypatch):
    monkeypatch.setattr(study, "FONTS", ("/no/such/font.ttf",))
    f = study.font(20)
    assert f.getlength("abc") > 0


def solid(path, rgb, size=(90, 160)):
    Image.new("RGB", size, rgb).save(path, quality=95)
    return path


def test_palette_finds_ink_and_a_small_accent(tmp_path):
    assert study.palette_of([]) == []
    im = Image.new("RGB", (90, 160), (255, 84, 24))
    im.paste((10, 10, 10), (0, 0, 90, 100))
    im.save(tmp_path / "a.jpg", quality=95)
    pal = study.palette_of([tmp_path / "a.jpg"])
    assert pal[0]["hex"][1:3] in ("0A", "0B", "09") and pal[0]["kind"] == "dominant"
    assert any(c["hex"].startswith(("#FE", "#FF")) for c in pal)
    im2 = Image.new("RGB", (90, 160), (10, 10, 10))
    im2.paste((255, 84, 24), (0, 0, 90, 4))                                                      # about 2.5% of the frame: too small to win a k-means
    im2.save(tmp_path / "b.jpg", quality=95)
    orange = [c for c in study.palette_of([tmp_path / "b.jpg"]) if int(c["hex"][1:3], 16) > 0xE0 and int(c["hex"][3:5], 16) < 0x70]
    assert len(orange) == 1 and orange[0]["share"] < 0.05                                       # kept, once, whichever pass found it


def test_palette_accent_pass_adds_a_color_the_means_missed(tmp_path, monkeypatch):
    import numpy as np
    real, calls = study.kmeans, []

    def fake(px, k=5, **kw):
        calls.append(k)
        if len(calls) == 1:   # the dominant pass finds only the two grays
            return np.array([[10.0, 10, 10], [200, 200, 200]]), np.array([0.5, 0.5])
        return real(px, k, **kw)

    monkeypatch.setattr(study, "kmeans", fake)
    im = Image.new("RGB", (90, 160), (10, 10, 10))
    im.paste((200, 200, 200), (0, 0, 90, 80))
    im.paste((255, 84, 24), (0, 80, 90, 85))                                                     # 3% of the frame
    im.save(tmp_path / "d.jpg", quality=95)
    pal = study.palette_of([tmp_path / "d.jpg"])
    assert [c["kind"] for c in pal][:3] == ["dominant", "dominant", "accent"] and int(pal[2]["hex"][1:3], 16) > 0xD0 and int(pal[2]["hex"][3:5], 16) < 0x90 and 0.005 <= pal[2]["share"] <= 0.05


def test_palette_is_repeatable_and_shares_add_up(tmp_path):
    im = Image.new("RGB", (90, 160), (200, 30, 30))
    im.paste((30, 30, 200), (0, 0, 90, 80))
    im.save(tmp_path / "c.jpg", quality=95)
    a, b = study.palette_of([tmp_path / "c.jpg"]), study.palette_of([tmp_path / "c.jpg"])
    assert a == b and 0.9 <= sum(c["share"] for c in a if c["kind"] == "dominant") <= 1.01


def test_strip_has_one_tile_per_frame_with_room_for_the_time(tmp_path):
    names = []
    for i in range(11):
        names.append(solid(tmp_path / f"t{i * 0.2 + 12:07.2f}.jpg", (20 * i, 40, 90)))
    out = tmp_path / "m01.jpg"
    study.make_strip(names, "M01  12.0-14.0 s  hello", out)
    with Image.open(out) as im:
        cols, rows = 8, 2
        tw = min(240, 1600 // cols)
        th = round(160 * tw / 90)
        assert im.size == (cols * tw, 34 + rows * (th + 24))
    study.make_strip(names[:3], "short", tmp_path / "m02.jpg")
    with Image.open(tmp_path / "m02.jpg") as im:
        assert im.width == 3 * 240
    wide = [solid(tmp_path / f"t{i + 30:07.2f}.jpg", (9, 9, 9), (160, 90)) for i in range(4)]
    study.make_strip(wide, "landscape", tmp_path / "m03.jpg", portrait=False)
    with Image.open(tmp_path / "m03.jpg") as im:
        assert im.width == 4 * 320


def test_sheet_tiles_and_labels(tmp_path):
    tiles = [(solid(tmp_path / f"s{i}.jpg", (i * 20, 10, 10)), f"{i}  {i}.0s") for i in range(7)]
    study.make_sheet(tiles, "A title", tmp_path / "board.jpg", 6, 270)
    with Image.open(tmp_path / "board.jpg") as im:
        th = round(160 * 270 / 90)
        assert im.size == (6 * 270, 2 * th + 46)


def test_selftest_runs_offline_and_passes():
    r = subprocess.run([sys.executable, "-I", str(STUDY), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0 and "selftest: all passed" in r.stdout and "FAIL" not in r.stdout
