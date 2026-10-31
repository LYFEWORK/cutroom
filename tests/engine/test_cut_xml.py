"""cut.premiere_xml: well-formed XML whatever the names hold, a real file URL, the NTSC timebase and the file's own frame size."""
import os, subprocess
import xml.etree.ElementTree as ET
import cut

PIECES = [{"c0": 1.0, "c1": 3.0, "out_in": 0.0, "out_out": 2.0}, {"c0": 5.0, "c1": 6.5, "out_in": 2.0, "out_out": 3.5}]


def video(path, size="320x240", rate=30):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:r={rate}:d=1", "-pix_fmt", "yuv420p", path], check=True)
    return path


def build(tmp_path, **plan):
    out = tmp_path / "cut.xml"
    base = {"src": str(tmp_path / "a.mp4"), "output": str(tmp_path / "Tom & Jerry <cut>.mp4")}
    cut.premiere_xml(dict(base, **plan), [dict(p) for p in PIECES], 30, str(out))
    return ET.parse(str(out)).getroot()          # raises ParseError when the document is malformed


def test_names_paths_and_markers_with_xml_metacharacters_stay_well_formed(tmp_path):
    src = video(str(tmp_path / "Tom & Jerry's <raw> #1.mp4"))
    root = build(tmp_path, src=src, markers=[{"name": "a & b", "comment": "<x> \"q\"", "out_s": 1.0}])
    assert root.find("sequence/name").text == "Tom & Jerry <cut>"
    assert root.find("sequence/marker/name").text == "a & b"
    url = root.find(".//pathurl").text
    assert url.startswith("file:///") and " " not in url and "&" not in url and "%26" in url and "%20" in url, url


def test_the_frame_size_is_read_from_the_file_not_assumed(tmp_path):
    src = video(str(tmp_path / "a.mp4"), size="320x240")
    root = build(tmp_path, src=src)
    assert root.find(".//file/media/video/samplecharacteristics/width").text == "320"
    assert root.find(".//file/media/video/samplecharacteristics/height").text == "240"
    root = build(tmp_path, src=src, src_w=1080, src_h=1920)       # an explicit size still wins
    assert root.find(".//file/media/video/samplecharacteristics/width").text == "1080"


def test_ntsc_rates_keep_their_frame_count(tmp_path):
    src = video(str(tmp_path / "a.mp4"), rate=30)
    root = build(tmp_path, src=src, xml_fps=29.97)
    rate = root.find(".//clipitem/rate")
    assert rate.find("timebase").text == "30" and rate.find("ntsc").text == "TRUE"
    clip = root.find(".//clipitem")
    assert clip.find("in").text == str(round(1.0 * 29.97)) and clip.find("duration").text == str(round(2.0 * 29.97))
    assert cut._timebase(23.976) == (24, "TRUE") and cut._timebase(59.94) == (60, "TRUE") and cut._timebase(30) == (30, "FALSE")


def test_an_unreadable_source_with_no_size_stops(tmp_path, capsys):
    import pytest
    with pytest.raises(SystemExit) as e:
        build(tmp_path, src=str(tmp_path / "missing.mp4"))
    assert e.value.code == 2 and "cannot read the frame size" in capsys.readouterr().err


def test_right_mode_defaults_to_off():
    """a speaker's "right?" is never stripped unless the plan asks: the default is "off" (the doc says so and the code does it)"""
    src = open(cut.__file__).read()
    assert 'plan.get("right_mode", "off")' in src and 'plan.get("right_mode", "most")' not in src and 'plan.get("right_mode", "pocket")' not in src


def test_a_frame_rate_that_is_not_whole_or_ntsc_stops_instead_of_writing_a_wrong_timebase(capsys):
    import pytest
    for bad in (12.5, 0, 17.3):
        with pytest.raises(SystemExit) as e:
            cut._timebase(bad)
        assert e.value.code == 2, bad
    assert "neither a whole rate nor an NTSC one" in capsys.readouterr().err
    assert cut._timebase(24) == (24, "FALSE") and cut._timebase(47.952) == (48, "TRUE") and cut._timebase(29.97) == (30, "TRUE")


def test_a_rotated_phone_file_reports_its_displayed_size(tmp_path):
    plain = video(str(tmp_path / "plain.mp4"))
    src = str(tmp_path / "rot.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", plain, "-c", "copy", src], check=True)
    assert cut._probe_dims(src) == (240, 320)


def test_the_file_is_defined_once_and_every_other_clip_points_at_it(tmp_path):
    root = build(tmp_path, src=video(str(tmp_path / "a.mp4")))
    files = root.findall(".//clipitem/file")
    assert len(files) >= 2 and len([f for f in files if len(f)]) == 1 and all(f.get("id") == "src1" for f in files)
    assert len(root.findall(".//clipitem")) == len(files)


def test_a_click_in_the_room_before_the_first_word_does_not_leave_the_cut_opening_on_dead_air(tmp_path):
    """silencedetect split the room before 'hello' in two around a mouth click, and the cutter took the FIRST fragment and opened the piece
    0.37 s before the word; the lead-in gate (0.35 s at most) rightly fails that"""
    import json
    src = str(tmp_path / "s.wav")
    expr = "0.5*sin(2*PI*300*t)*gte(t,1.0)*lt(t,2.5)+0.5*sin(2*PI*1500*t)*gte(t,0.70)*lt(t,0.75)"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc=" + expr.replace(",", "\\,") + ":s=16000:d=3", "-ac", "1", src], check=True)
    words = tmp_path / "w.wx.json"
    words.write_text(json.dumps({"segments": [{"words": [{"word": "hello", "start": 1.0, "end": 1.6}, {"word": "there", "start": 1.7, "end": 2.4}]}]}))
    plan = {"src": src, "words_json": str(words), "segments": [{"match": "hello there", "label": "01"}]}
    pieces, _, _, _ = cut.plan_pieces(plan)
    assert len(pieces) == 1 and 0.9 <= pieces[0]["c0"] <= 0.97, pieces[0]
