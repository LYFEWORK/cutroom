"""avskew.py: a mezzanine must never freeze a stalled capture into fake picture time.

A stalled variable-frame-rate phone capture has, say, 8371 video packets over a 294.762 s timestamp span (three stalls totalling 15.695 s of fake time)
against a contiguous 279.668 s audio stream. Converting it with a plain `fps=30` duplicates 473 frames to fill the stalls, so every EDL time (an
audio-domain number) lands 15.695 s late in the picture, and the closing line plays with the speaker's mouth shut. The first half of this file is the
pure halves of the gate that catches it; the second half runs it on synthetic stalled captures made with ffmpeg.
"""
import json
import os

import pytest

import avskew
from helpers import sh, video
from helpers_misc import conformed_mezzanine, naive_mezzanine, run_engine, stalled_capture, stop_lines

NOM = 30.0
FI = 1.0 / 30.0


def pts_at(fps, n, start=0.0):
    return [round(start + i / fps, 6) for i in range(n)]


# --- hole detector -----------------------------------------------------------

def test_a_clean_cfr_packet_list_has_no_holes():
    assert avskew.holes_of(pts_at(30, 300), NOM) == []


def test_frames_arriving_out_of_decode_order_are_sorted_before_measuring():
    """HEVC packets come back from ffprobe in decode order (B-frames), so 0.133 shows up before 0.067. Measuring the raw list would report a hole at
    every reorder point."""
    p = pts_at(30, 300)
    p[1], p[2] = p[2], p[1]
    p[10], p[13] = p[13], p[10]
    assert avskew.holes_of(p, NOM) == []


def test_a_stall_is_reported_at_the_packet_that_precedes_it():
    p = pts_at(30, 100) + [x + 12.5233 for x in pts_at(30, 100, 100 / 30)]
    hs = avskew.holes_of(p, NOM)
    assert len(hs) == 1
    t, gap = hs[0]
    assert abs(t - 99 / 30) < 1e-3 and abs(gap - (12.5233 + FI)) < 1e-3


def test_ordinary_vfr_jitter_under_the_factor_is_not_a_stall():
    """A 0.035 s interval is jitter, not a stall; 2.5x the nominal interval is the bar, so a single dropped frame (0.0667 s) also stays under it."""
    p = [0.0, 0.035, 0.0683, 0.135, 0.1683]
    assert avskew.holes_of(p, NOM) == []


def test_holes_total_counts_only_the_fake_time_not_the_legitimate_frame():
    """A 12.523 s gap carries one real frame interval; only 12.490 s of it is invented."""
    hs = [(132.098, 12.5233), (164.793, 2.5383)]
    assert abs(avskew.holes_total(hs, NOM) - (12.49 + 2.505)) < 1e-3


# --- clustering (message legibility) -----------------------------------------

def test_a_run_of_micro_stalls_reads_as_one_span():
    """A middle stall that is 8 packet gaps between 146.755 and 149.255: as 8 separate entries the STOP line is unreadable, and what the reader needs is
    'where do I scrub'."""
    hs = [(132.098, 12.5233), (146.755, 0.1333), (146.888, 0.1333), (147.388, 0.1333),
          (147.522, 0.1333), (148.122, 0.1), (148.222, 0.1333), (149.155, 0.1), (149.255, 0.1),
          (164.793, 2.5383)]
    cl = avskew.cluster(hs, NOM)
    assert len(cl) == 3
    assert abs(cl[0][0] - 132.098) < 1e-3 and abs(cl[0][2] - 12.49) < 1e-3 and cl[0][3] == 1
    assert abs(cl[1][0] - 146.755) < 1e-3 and abs(cl[1][1] - 149.355) < 1e-2 and cl[1][3] == 8
    assert abs(cl[2][2] - 2.505) < 1e-3
    assert avskew.render_holes(cl) == "132.098 s (12.490 s), 146.755-149.355 s (0.700 s), 164.793 s (2.505 s)"


# --- nominal rate ------------------------------------------------------------

def test_r_frame_rate_is_used_when_it_agrees_with_the_packets():
    assert avskew.nominal_fps(30.0, [FI] * 50) == 30.0
    assert abs(avskew.nominal_fps(30000 / 1001, [1001 / 30000] * 50) - 29.97) < 0.01


def test_a_container_lying_about_its_rate_falls_back_to_the_median_interval():
    """A 600/1 r_frame_rate on a 60 fps capture would renumber the frames 10x fast and hand the cut a 10x-short mezzanine. The packets are the ground
    truth."""
    assert abs(avskew.nominal_fps(600.0, [1 / 60] * 50) - 60.0) < 0.01


def test_a_missing_rate_falls_back_to_the_median_interval():
    assert abs(avskew.nominal_fps(0.0, [FI] * 50) - 30.0) < 0.01


def test_no_rate_and_no_packet_intervals_stops_with_one_plain_line(capsys):
    with pytest.raises(SystemExit) as e:
        avskew.nominal_fps(0.0, [])
    assert e.value.code == 2 and capsys.readouterr().err.startswith("STOP: cannot tell what frame rate")


def test_parse_rate_reads_a_fraction_and_survives_the_zero_fraction():
    assert abs(avskew.parse_rate("30000/1001") - 29.97) < 0.01
    assert avskew.parse_rate("0/0") == 0.0 and avskew.parse_rate("") == 0.0 and avskew.parse_rate("junk") == 0.0 and avskew.parse_rate("25") == 25.0


# --- classify ----------------------------------------------------------------

def test_clean_source_passes():
    r = avskew.classify(22.5225, 22.5225, 0.0, 29.97)
    assert r["ok"] is True
    assert "22.523" in r["reason"]


def test_skew_alone_stops_even_with_no_packet_stalls():
    """A trailing stall leaves no packet gap to find (the last frames simply never arrive), so the span-vs-audio comparison has to fail on its own."""
    r = avskew.classify(270.0, 279.668, 0.0, 30.0)
    assert r["ok"] is False
    assert "-9.668" in r["reason"] and "279.668" in r["reason"]


def test_holes_alone_stop_even_when_the_spans_happen_to_agree():
    r = avskew.classify(294.762, 294.700, 15.695, 30.0)
    assert r["ok"] is False
    assert "15.695" in r["reason"]


def test_a_capture_with_both_stalls_and_skew_stops_on_both():
    r = avskew.classify(294.7617, 279.668271, 15.695, 30.0)
    assert r["ok"] is False
    assert "15.695" in r["reason"] and "294.762" in r["reason"] and "279.668" in r["reason"]


def test_the_tolerance_is_the_only_thing_that_separates_pass_from_stop():
    assert avskew.classify(100.0, 100.09, 0.0, 30.0)["ok"] is True
    assert avskew.classify(100.0, 100.11, 0.0, 30.0)["ok"] is False
    assert avskew.classify(100.0, 100.11, 0.0, 30.0, tol_s=0.2)["ok"] is True


# --- mezzanine verification --------------------------------------------------

def test_freezedetect_output_is_parsed_into_spans():
    text = """[freezedetect @ 0x14] lavfi.freezedetect.freeze_start: 132.098
[freezedetect @ 0x14] lavfi.freezedetect.freeze_duration: 12.49
[freezedetect @ 0x14] lavfi.freezedetect.freeze_end: 144.588
[freezedetect @ 0x14] lavfi.freezedetect.freeze_start: 164.793
[freezedetect @ 0x14] lavfi.freezedetect.freeze_duration: 2.505
[freezedetect @ 0x14] lavfi.freezedetect.freeze_end: 167.298
"""
    f = avskew.parse_freezes(text)
    assert [round(x["start"], 3) for x in f] == [132.098, 164.793]
    assert [round(x["duration"], 3) for x in f] == [12.49, 2.505]


def test_a_freeze_still_running_at_end_of_file_has_no_freeze_end():
    text = ("lavfi.freezedetect.freeze_start: 10.0\n"
            "lavfi.freezedetect.freeze_duration: 0.9\n")
    assert len(avskew.parse_freezes(text)) == 1


def test_no_freezes_and_matching_durations_is_a_good_mezzanine():
    r = avskew.verify_conform(279.033, 279.033, [])
    assert r["ok"] is True


def test_a_mezzanine_whose_streams_disagree_is_refused():
    r = avskew.verify_conform(279.033, 279.668, [])
    assert r["ok"] is False and "0.635" in r["reason"]


def test_a_mezzanine_that_still_holds_a_frozen_frame_is_refused():
    """This is the whole defect: a mezzanine that plays 12.5 s of one still frame under live voice. Shipping it desyncs every cut after it."""
    r = avskew.verify_conform(279.0, 279.0, [{"start": 132.098, "duration": 12.49}])
    assert r["ok"] is False and "132.098" in r["reason"] and "12.490" in r["reason"]


def test_a_held_frame_under_half_a_second_is_a_still_moment_not_a_stall():
    r = avskew.verify_conform(279.0, 279.0, [{"start": 40.0, "duration": 0.42}])
    assert r["ok"] is True


# --- on real files: probe(), verify() and the command line --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def captures(tmp_path_factory):
    d = tmp_path_factory.mktemp("avskew")
    out = {"dir": d}
    out["clean"] = video(d / "clean.mp4", dur=2)
    out["stalled"] = stalled_capture(d / "stalled.mp4", stall_s=3.0, dur=2)
    out["conformed"] = conformed_mezzanine(out["stalled"], d / "conformed.mp4")
    out["naive"] = naive_mezzanine(out["stalled"], d / "naive.mp4")
    # no stall, but the video is shorter than the audio: the skew has to fail on its own
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(d / "short.mp4")])
    assert r.returncode == 0, r.stderr
    out["short"] = str(d / "short.mp4")
    return out


def test_a_clean_file_probes_clean(captures):
    d = avskew.probe(captures["clean"])
    assert d["ok"] is True and d["holes_total"] == 0 and d["holes"] == [] and d["hole_spans_text"] == ""
    assert d["nominal_fps"] == 30.0 and d["video_frames"] == 60 and abs(d["video_pts_span"] - 59 / 30) < 1e-3
    assert abs(d["skew"]) <= avskew.TOL_S


def test_a_stalled_capture_is_found_where_it_stalls(captures):
    d = avskew.probe(captures["stalled"])
    assert d["ok"] is False and abs(d["holes_total"] - 3.0) < 0.05
    assert len(d["holes"]) == 1 and abs(d["holes"][0]["t"] - 29 / 30) < 0.01
    assert d["hole_spans_text"].startswith("0.967 s") and "3.000 s" in d["hole_spans_text"]
    assert "3.000 s of video stalls" in d["reason"] and d["video_frames"] == 60
    assert abs(d["video_pts_span"] - (59 / 30 + 3.0)) < 0.01 and abs(d["audio_duration"] - 2.0) < 0.02


def test_a_capture_with_no_stall_but_short_video_fails_on_the_skew_alone(captures):
    d = avskew.probe(captures["short"])
    assert d["ok"] is False and d["holes_total"] == 0 and d["skew"] < -avskew.TOL_S
    assert "no packet stalls found" in d["reason"]


def test_probe_stops_with_a_plain_line_for_a_missing_file_no_audio_and_no_video(tmp_path, capsys):
    with pytest.raises(SystemExit):
        avskew.probe(str(tmp_path / "missing.mp4"))
    assert capsys.readouterr().err.startswith("STOP: source not found")
    silent = video(tmp_path / "silent.mp4", audio=False)
    with pytest.raises(SystemExit):
        avskew.probe(silent)
    assert "has no audio stream" in capsys.readouterr().err
    wav = tmp_path / "only.wav"
    assert sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)]).returncode == 0
    with pytest.raises(SystemExit):
        avskew.probe(str(wav))
    assert "has no video stream" in capsys.readouterr().err


def test_the_conform_prep_makes_passes_the_verify_and_a_naive_conversion_is_refused(captures):
    ok = avskew.verify(captures["conformed"])
    assert ok["ok"] is True and ok["freezes_over_min"] == [] and abs(ok["video_duration"] - ok["audio_duration"]) <= avskew.TOL_S
    bad = avskew.verify(captures["naive"])
    assert bad["ok"] is False and bad["freezes_over_min"], bad
    assert abs(bad["freezes_over_min"][0]["start"] - 29 / 30) < 0.05 and bad["freezes_over_min"][0]["duration"] > 2.5
    assert "still desyncs" in bad["reason"] and "held frames at" in bad["reason"]


def test_freezes_of_finds_a_held_frame_and_verify_reads_a_stream_less_file_as_an_error(captures, tmp_path, capsys):
    held = avskew.freezes_of(captures["naive"])
    assert len(held) == 1 and held[0]["duration"] > 2.5
    assert avskew.freezes_of(captures["conformed"]) == []
    wav = tmp_path / "a.wav"
    assert sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(wav)]).returncode == 0
    with pytest.raises(SystemExit):
        avskew.verify(str(wav))
    assert "is missing a video or audio stream" in capsys.readouterr().err


def test_the_cli_stops_on_a_stalled_source_but_still_writes_its_measurements(captures, tmp_path):
    j = tmp_path / "avskew.json"
    r = run_engine("avskew.py", captures["stalled"], "--json", j)
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "3.000 s of video stalls at 0.967 s (3.000 s)" in r.stderr, r.stdout + r.stderr
    d = json.load(open(j))
    assert d["ok"] is False and abs(d["holes_total"] - 3.0) < 0.05 and d["src"] == os.path.abspath(captures["stalled"])


def test_the_cli_names_a_skew_with_no_stall(captures):
    r = run_engine("avskew.py", captures["short"])
    assert r.returncode == 2 and "source video and audio timelines disagree" in r.stderr and len(stop_lines(r)) == 1, r.stdout + r.stderr


def test_the_cli_passes_a_clean_source_and_the_tolerance_flag_moves_the_bar(captures, tmp_path):
    r = run_engine("avskew.py", captures["clean"])
    assert r.returncode == 0 and r.stdout.startswith("avskew ok:") and "60 frames @ 30.000 fps" in r.stdout, r.stdout + r.stderr
    assert run_engine("avskew.py", captures["short"]).returncode == 2
    j = tmp_path / "wide.json"
    r = run_engine("avskew.py", captures["short"], "--tol", "0.2", "--json", j)
    assert r.returncode == 0 and json.load(open(j))["tol_s"] == 0.2


def test_the_verify_cli_merges_its_verdict_into_the_sidecar_and_notes_the_dropped_time(captures, tmp_path):
    j = tmp_path / "avskew.json"
    assert run_engine("avskew.py", captures["stalled"], "--json", j).returncode == 2
    r = run_engine("avskew.py", "--verify", captures["conformed"], "--json", j, "--dropped", "3.0")
    assert r.returncode == 0 and r.stdout.startswith("avskew verify ok:"), r.stdout + r.stderr
    d = json.load(open(j))
    assert d["ok"] is False and d["conform"]["ok"] is True                    # the measurement is kept, the conform is added beside it
    assert d["conform"]["dropped_s"] == 3.0 and "conformed so video time == audio time" in d["conform"]["note"]


def test_the_verify_cli_refuses_the_naive_mezzanine_and_records_why(captures, tmp_path):
    j = tmp_path / "avskew.json"
    r = run_engine("avskew.py", "--verify", captures["naive"], "--json", j)
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "the conformed mezzanine still desyncs" in r.stderr, r.stdout + r.stderr
    assert json.load(open(j))["conform"]["ok"] is False
