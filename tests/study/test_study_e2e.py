"""End to end: the whole study on a synthetic reel with the transcript off (fast), then on a voiced clip with it on."""
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from studykit import TEXTS, WATERMARK, needs_vision, needs_whisper

FILES = ["board.jpg", "keyframes.jpg", "metrics.json", "transcript.md", "transcript.json", "moments.json", "palette.json", "source.json", "source.mp4", "study.md"]
EMPTY_SECTIONS = ["Captions", "Hook", "Cuts and pacing", "B-roll and graphics", "Sound", "Steal list", "Leave out"]


@pytest.fixture(scope="session")
def main_run(cli, synthetic_reel, tmp_path_factory):
    out = tmp_path_factory.mktemp("studies") / "synthetic"
    r = cli([synthetic_reel, "--out", out, "--name", "Synthetic reel", "--liked", "the cards", "--no-transcript"])
    return r, out


def load(out, name):
    return json.loads((out / name).read_text())


@needs_vision
def test_exit_zero_and_every_output_file(main_run):
    r, out = main_run
    assert r.returncode == 0, r.stderr
    assert "study ok: Synthetic reel" in r.stdout
    for name in FILES:
        assert (out / name).is_file(), name
    for folder in ("frames/shots", "frames/dense", "strips"):
        assert any((out / folder).glob("*.jpg")), folder
    assert not [p for p in out.iterdir() if p.name.startswith(".")], "no staging or download scratch left behind"


@needs_vision
def test_metrics_shape_and_values(main_run):
    _, out = main_run
    m = load(out, "metrics.json")
    assert (m["width"], m["height"], m["has_audio"]) == (1080, 1920, True)
    assert 19.5 <= m["duration_s"] <= 20.5
    assert 3 <= m["shots"] <= 6                      # four sources cut together
    assert 6 <= m["cuts_per_min"] <= 18              # three hard cuts in 20 s is 9 a minute
    assert 0.5 <= m["median_shot_s"] <= 5.5
    assert 35 <= m["text_on_screen_pct"] <= 80       # the cards show 12 s of 20, the watermark is ignored
    assert m["median_text_px"] > 20
    assert m["words_per_minute"] is None and m["transcript_language"] == ""
    assert isinstance(m["mix_lufs"], float) and m["mix_lufs"] < 0
    assert m["portrait_9x16"] is True and m["ui_band_ignored"] is False
    assert m["hook"]["window_s"] == 3.0 and m["hook"]["text_present"] is True and m["hook"]["cuts"] <= 1 and m["hook"]["first_words"] == ""
    assert m["moments_from"] == "text" and m["moments"] == len(load(out, "moments.json"))
    assert {"cuts_per_min", "sound_hits_per_min", "text_on_screen_pct", "words_per_minute", "mix_lufs"} <= set(m["notes"])
    assert all("approximate" in m["notes"][k] for k in ("cuts_per_min", "sound_hits_per_min", "text_on_screen_pct", "mix_lufs"))
    assert set(m["timing_s"]) >= {"source", "numbers", "text scan", "boards"}


@needs_vision
def test_watermark_is_ignored_and_cards_become_moments(main_run):
    _, out = main_run
    m = load(out, "metrics.json")
    assert len(m["persistent_text_ignored"]) == 1 and "WATERMARK" in m["persistent_text_ignored"][0].upper().replace(" ", "")
    moments = load(out, "moments.json")
    assert 3 <= len(moments) <= 6
    assert all(x["kind"] == "text" for x in moments)
    seen = " ".join(" ".join(x["ocr_text"]) for x in moments).upper()
    assert "WATERMARK" not in seen
    assert sum(any(w in seen for w in card.split()[:2]) for card, _, _ in TEXTS) >= 3, seen
    for (_, a, b) in TEXTS[:1]:   # the first card shows 0.4 to 3.4 s: the first moment sits on it
        assert moments[0]["t0"] <= a + 0.6 and moments[0]["t1"] >= b - 0.6


@needs_vision
def test_moments_json_shapes(main_run):
    _, out = main_run
    moments = load(out, "moments.json")
    assert [x["index"] for x in moments] == list(range(1, len(moments) + 1))
    for x in moments:
        assert set(x) == {"index", "t0", "t1", "kind", "shot_index", "frames", "strip", "ocr_text", "said", "palette"}
        assert 0 <= x["t0"] < x["t1"] <= 20.5 and x["shot_index"] >= 1
        assert x["strip"] == f"strips/m{x['index']:02d}.jpg" and (out / x["strip"]).is_file()
        assert x["frames"] and all((out / f).is_file() for f in x["frames"])
        assert all(re.fullmatch(r"frames/dense/t\d{4}\.\d{2}\.jpg", f) for f in x["frames"])
        times = [float(re.search(r"t(\d{4}\.\d{2})\.jpg", f).group(1)) for f in x["frames"]]
        assert all(t * 5 == pytest.approx(round(t * 5), abs=1e-6) for t in times)   # on the 0.2 s grid
        assert times == sorted(times) and x["t0"] - 0.45 <= times[0] and times[-1] <= x["t1"] + 0.45
        assert x["palette"] and all(set(c) == {"hex", "share", "kind"} and re.fullmatch(r"#[0-9A-F]{6}", c["hex"]) for c in x["palette"])
    # neighbouring moments share the dense frames they overlap, by the global grid
    names = [f for x in moments for f in x["frames"]]
    assert len(set(names)) == len(list((out / "frames/dense").glob("*.jpg")))


@needs_vision
def test_images_have_the_promised_layout(main_run):
    _, out = main_run
    with Image.open(out / "board.jpg") as im:
        assert im.width == 6 * 270
    with Image.open(out / "keyframes.jpg") as im:
        assert im.width == 4 * 360                      # tiles at least 360 px wide
    moments = load(out, "moments.json")
    with Image.open(out / moments[0]["strip"]) as im:
        cols = min(len(moments[0]["frames"]), 8)
        assert im.width == cols * min(240, 1600 // cols) and im.height > 300
    shots = sorted((out / "frames/shots").glob("s*.jpg"))
    assert len(shots) == load(out, "metrics.json")["shots"]
    with Image.open(shots[0]) as im:
        assert im.width == 540


@needs_vision
def test_palette_json_and_source_json(main_run):
    _, out = main_run
    pal = load(out, "palette.json")
    assert pal["overall"] and {c["kind"] for c in pal["overall"]} <= {"dominant", "accent"}
    assert [x["moment"] for x in pal["moments"]] == [x["index"] for x in load(out, "moments.json")]
    s = load(out, "source.json")
    assert {"name", "url", "page_url", "uploader", "title", "description", "duration"} <= set(s)
    assert s["name"] == "Synthetic reel" and s["url"] == "" and s["liked"] == "the cards" and s["slug"] == "reel"
    assert 19.5 <= s["duration"] <= 20.5


@needs_vision
def test_study_md_is_prefilled_with_empty_sections(main_run):
    _, out = main_run
    md = (out / "study.md").read_text()
    for head in ("## Source", "## Numbers", "## Colours", "## Transcript", "## Moments"):
        assert head in md
    for title in EMPTY_SECTIONS:
        sec = md.split(f"## {title}\n")[1].split("\n## ")[0].strip()
        assert sec.startswith("<!--") and sec.endswith("-->"), title
    assert md.count("approximate") >= 4
    assert "(No speech: the transcript was switched off" in md
    assert "What was liked: the cards" in md
    assert re.search(r"\| 1 \| .* \| text \| .* \| strips/m01\.jpg \|", md)
    assert "\u2014" not in md and "\u2013" not in md      # plain punctuation only
    assert (out / "transcript.md").read_text().count("No speech") == 1 and load(out, "transcript.json") == []


def test_no_ocr_makes_every_shot_a_moment(cli, synthetic_reel, tmp_path):
    out = tmp_path / "noocr"
    r = cli([synthetic_reel, "--out", out, "--no-ocr", "--no-transcript"])
    assert r.returncode == 0, r.stderr
    m = load(out, "metrics.json")
    assert m["moments_from"] == "shot" and m["text_on_screen_pct"] is None and m["median_text_px"] is None and m["hook"]["text_present"] is None
    moments = load(out, "moments.json")
    assert len(moments) >= m["shots"] and all(x["kind"] == "shot" and x["ocr_text"] == [] for x in moments)
    assert all(x["t1"] - x["t0"] <= 6.0 + 1e-6 for x in moments)
    assert "not measured" in (out / "study.md").read_text()


def test_max_moments_keeps_the_first_and_the_last(cli, synthetic_reel, tmp_path):
    out = tmp_path / "capped"
    r = cli([synthetic_reel, "--out", out, "--no-ocr", "--no-transcript"])
    n = len(load(out, "moments.json"))
    assert n >= 4
    out2 = tmp_path / "capped2"
    r = cli([synthetic_reel, "--out", out2, "--no-ocr", "--no-transcript", "--max-moments", "3"])
    assert r.returncode == 0, r.stderr
    capped, full = load(out2, "moments.json"), load(out, "moments.json")
    assert [x["index"] for x in capped] == [1, 2, 3] and len(list((out2 / "strips").glob("*.jpg"))) == 3
    assert capped[0]["t0"] == full[0]["t0"] and capped[-1]["t1"] == full[-1]["t1"]
    assert load(out2, "metrics.json")["moments"] == 3


def test_rerun_refreshes_and_leaves_a_filled_in_study_alone(cli, synthetic_reel, tmp_path):
    out = tmp_path / "again"
    args = [synthetic_reel, "--out", out, "--no-ocr", "--no-transcript"]
    assert cli(args).returncode == 0
    first = (out / "metrics.json").stat().st_mtime_ns
    (out / "frames/dense/stale.jpg").write_bytes(b"x")          # a leftover of an older run
    r = cli(args)
    assert r.returncode == 0 and "left alone" not in r.stdout
    assert (out / "metrics.json").stat().st_mtime_ns > first and not (out / "frames/dense/stale.jpg").exists()
    md = out / "study.md"
    filled = md.read_text().replace("<!-- Where they sit, how big, case, colour roles (hex plus role), how the hero word is treated, how they enter and leave. Cite frames. -->", "Centre, big, upper case.")
    assert filled != md.read_text()
    md.write_text(filled)
    r = cli(args)
    assert r.returncode == 0 and "study.md has been filled in" in r.stdout
    assert md.read_text() == filled and not [p for p in out.iterdir() if p.name.startswith(".")]


@needs_whisper
def test_transcript_on_a_voiced_clip(cli, spoken_clip, tmp_path):
    out = tmp_path / "voiced"
    r = cli([spoken_clip, "--out", out, "--no-ocr"])
    assert r.returncode == 0, r.stderr
    segs = load(out, "transcript.json")
    assert segs and all(set(s) == {"t0", "t1", "text"} and s["t1"] > s["t0"] for s in segs)
    said = " ".join(s["text"] for s in segs).lower()
    assert "fox" in said and "transcript" in said
    m = load(out, "metrics.json")
    assert m["transcript_language"] == "en" and 60 <= m["words_per_minute"] <= 400
    assert m["hook"]["first_words"].lower().startswith("hello") and m["has_audio"]
    assert m["mix_lufs"] < -10
    md = (out / "transcript.md").read_text()
    assert "[00:00.0" in md and "fox" in md.lower()
    assert "fox" in (out / "study.md").read_text().lower()
    assert any("fox" in x["said"].lower() for x in load(out, "moments.json"))


# ---------------------------------------------------------------- other kinds of input

def make_clip(path, *args):
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(path)], check=True)
    return path


def test_a_clip_with_no_audio_track(cli, synthetic_reel, tmp_path):
    clip = make_clip(tmp_path / "mute.mp4", "-i", synthetic_reel, "-t", "6", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p")
    out = tmp_path / "s"
    r = cli([clip, "--out", out, "--no-ocr"])
    assert r.returncode == 0, r.stderr
    m = load(out, "metrics.json")
    assert m["has_audio"] is False and m["sound_hits_per_min"] is None and m["mix_lufs"] is None and m["words_per_minute"] is None
    assert "(No speech: the file has no audio track.)" in (out / "transcript.md").read_text()
    assert "no audio" in (out / "study.md").read_text()


def test_a_silent_track_is_not_transcribed_or_given_a_loudness(cli, synthetic_reel, tmp_path):
    clip = make_clip(tmp_path / "silent.mp4", "-i", synthetic_reel, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "6", "-map", "0:v", "-map", "1:a",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    out = tmp_path / "s"
    r = cli([clip, "--out", out, "--no-ocr"])
    assert r.returncode == 0, r.stderr
    m = load(out, "metrics.json")
    assert m["has_audio"] is True and m["mix_lufs"] is None and m["words_per_minute"] is None
    assert "(No speech: the audio track is silent.)" in (out / "transcript.md").read_text()


def test_a_landscape_file_warns_and_still_studies(cli, synthetic_reel, tmp_path):
    clip = make_clip(tmp_path / "wide.mp4", "-i", synthetic_reel, "-t", "8", "-vf", "scale=1280:720,setsar=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac")
    out = tmp_path / "s"
    r = cli([clip, "--out", out, "--no-ocr", "--no-transcript"])
    assert r.returncode == 0, r.stderr
    assert "not 9:16" in r.stdout
    m = load(out, "metrics.json")
    assert (m["width"], m["height"], m["portrait_9x16"]) == (1280, 720, False)
    with Image.open(out / load(out, "moments.json")[0]["strip"]) as im:
        assert im.width <= 1600
    with Image.open(out / "board.jpg") as im:
        assert im.width == 6 * 270


def test_a_file_that_is_not_an_mp4_is_remuxed(cli, synthetic_reel, tmp_path):
    clip = make_clip(tmp_path / "clip.mkv", "-i", synthetic_reel, "-t", "6", "-c", "copy")
    out = tmp_path / "s"
    r = cli([clip, "--out", out, "--no-ocr", "--no-transcript"])
    assert r.returncode == 0, r.stderr
    assert (out / "source.mp4").is_file() and load(out, "source.json")["origin_file"].endswith("clip.mkv")
    assert 5.5 <= load(out, "metrics.json")["duration_s"] <= 6.5
    assert not (out / "source.part.mp4").exists()


def test_a_file_that_is_not_a_video_is_one_stop_line(cli, tmp_path):
    bad = tmp_path / "notes.mp4"
    bad.write_text("this is not a video")
    r = cli([bad, "--out", tmp_path / "s", "--no-ocr", "--no-transcript"])
    lines = [l for l in r.stderr.splitlines() if l.strip()]
    assert r.returncode == 2 and len(lines) == 1 and lines[0].startswith("STOP: ")
    assert not (tmp_path / "s").exists()


@needs_vision
def test_ui_band_ignores_text_under_the_interface_and_keep_static_keeps_the_mark(cli, synthetic_reel, tmp_path):
    out = tmp_path / "band"
    r = cli([synthetic_reel, "--out", out, "--no-transcript", "--ui-band"])
    assert r.returncode == 0, r.stderr
    m = load(out, "metrics.json")
    assert m["ui_band_ignored"] is True and m["persistent_text_ignored"] == []     # the mark sits in the bottom band, so it was never read
    assert "WATERMARK" not in " ".join(" ".join(x["ocr_text"]) for x in load(out, "moments.json")).upper()
    assert 35 <= m["text_on_screen_pct"] <= 80
    out2 = tmp_path / "static"
    r = cli([synthetic_reel, "--out", out2, "--no-transcript", "--keep-ui-text"])        # the alias of --keep-static-text
    assert r.returncode == 0, r.stderr
    m2 = load(out2, "metrics.json")
    assert m2["persistent_text_ignored"] == [] and m2["text_on_screen_pct"] >= 90
    assert "WATERMARK" in " ".join(" ".join(x["ocr_text"]) for x in load(out2, "moments.json")).upper()
