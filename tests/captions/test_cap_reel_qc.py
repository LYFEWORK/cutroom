"""reel_qc.py: the never-see gates on a finished caption reel.

The caption chain takes an already-cut, already-mixed video and burns in captions: there is no EDL, no pieces, no splice joints, and output time equals source time.
So G-FACE is a whole-reel face-presence check over the spoken span, G2 samples the one continuous reel, and G8 and G9 check the audio origin and the colour tags.
G-ACCENT checks that the look's accent colour came through the conform unchanged, and G10 (the pace gate) runs as the look says: off, report or enforce.

The heavy paths (decode, freezedetect, loudnorm, cross-correlation, ffprobe colour tags) are exercised once end to end on short lavfi takes in the CLI section; the
sections above it are pure and instant.
"""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import cap_helpers
import reel_qc
from keys import EngineStop

REEL_QC = os.path.join(cap_helpers.REPO, "engine", "captions", "reel_qc.py")

SRC_WORDS = [{"text": "why", "start": 10.10, "end": 10.30}, {"text": "your", "start": 10.35, "end": 10.55},
             {"text": "calendar", "start": 10.60, "end": 11.10}, {"text": "stays", "start": 20.05, "end": 20.35},
             {"text": "empty", "start": 20.40, "end": 20.90}]
FACE = {"cx": 540, "cy": 900, "w": 400, "h": 400}


def track_for(seconds, missing=()):
    return {"W": 1080, "H": 1920, "seconds": seconds,
            "samples": {str(s): dict(FACE) for s in range(seconds) if s not in missing},
            "median": dict(FACE)}


# --- G-FACE: whole-reel face presence over the spoken span ---------------------------------------

def test_g_face_passes_full_coverage_over_the_spoken_span():
    g = reel_qc.g_face(SRC_WORDS, track_for(22))
    assert g["pass"] is True
    assert "face 100%" in g["numbers"] and "10.10-20.90" in g["numbers"]


def test_g_face_fails_below_the_ninety_percent_threshold():
    """words span seconds 10-21 (11 sampled seconds); missing 2 of them is 82%, under 0.90."""
    track = track_for(22, missing=(11, 15))
    g = reel_qc.g_face(SRC_WORDS, track)
    assert g["pass"] is False
    assert any(d["why"] == "face" for d in g["detail"])
    assert g["detail"][0]["threshold"] == 0.90


def test_g_face_ignores_missing_seconds_outside_the_spoken_span():
    """A face absent long before the first word or long after the last word (an intro card, a
    product shot, dead air) must never fail the gate: only the spoken span counts toward pass.
    The spoken span (10.10-20.90) samples seconds 10-20 (11 of them); the other 19 of this 30s
    track's seconds are outside it, and 8 of those 19 have no face."""
    track = track_for(30, missing=(0, 1, 2, 25, 26, 27, 28, 29))
    g = reel_qc.g_face(SRC_WORDS, track)
    assert g["pass"] is True
    assert "face 100%" in g["numbers"]
    assert "outside the spoken span: 8/19" in g["numbers"]


def test_g_face_holds_the_spoken_span_to_the_reels_own_length():
    """A clip cut off mid-sentence ends with a word whose transcribed end runs past the last frame (6.06 s of picture, the last word "ending" at 7.36 s):
    the seconds that were never there have no face and used to fail a take whose subject was on camera the whole time."""
    words = [{"text": "so", "start": 0.0, "end": 0.3}, {"text": "have", "start": 5.92, "end": 7.36}]
    track = track_for(7, missing=(6,))                                    # a face in every second of the 6.17 s of picture
    assert reel_qc.g_face(words, track)["pass"] is False                  # measured to 7.36 s: second 6 has no face, 6 of 7 seconds is 86%
    g = reel_qc.g_face(words, track, duration=6.17)
    assert g["pass"] is True and "face 100% over the spoken span 0.00-6.17s" in g["numbers"]
    assert reel_qc.spoken_span(words, 6.17) == (0.0, 6.17) and reel_qc.spoken_span(words) == (0.0, 7.36) and reel_qc.spoken_span(words, 9.0) == (0.0, 7.36)


def test_g_face_reports_but_never_fails_on_the_outside_span_alone():
    track = track_for(25, missing=(0, 1))       # both missing seconds are before word 1 starts at 10.10
    g = reel_qc.g_face(SRC_WORDS, track)
    assert g["pass"] is True and "outside the spoken span" in g["numbers"]


def test_g_face_skips_without_any_words():
    g = reel_qc.g_face([], track_for(5))
    assert g["pass"] is True and g["skipped"] is True and "nothing spoken" in g["numbers"]


def test_g_face_skips_without_a_facetrack():
    g = reel_qc.g_face(SRC_WORDS, None)
    assert g["pass"] is True and g["skipped"] is True and "no facetrack" in g["numbers"]


def test_g_face_skips_a_spoken_span_too_short_to_sample():
    tiny = [{"text": "hi", "start": 10.10, "end": 10.20}]
    g = reel_qc.g_face(tiny, track_for(2))
    assert g["pass"] is True and g["skipped"] is True and "too short to sample" in g["numbers"]


# --- G2 picture == sound -------------------------------------------------------------------------

CLEAN_MATCH = [{"t": 0.5, "corr": 0.99}, {"t": 30.0, "corr": 0.97}, {"t": 59.0, "corr": 0.98}]
CLEAN_LAG = [{"at": 0.5, "lag_s": 0.004}, {"at": 30.0, "lag_s": 0.006}, {"at": 59.0, "lag_s": 0.005}]


def test_g2_passes_a_clean_take():
    g = reel_qc.g2_picture_sound([], CLEAN_MATCH, CLEAN_LAG)
    assert g["pass"] is True and "freezes 0" in g["numbers"]


def test_g2_fails_a_frozen_mezzanine():
    g = reel_qc.g2_picture_sound([{"start": 133.3, "duration": 11.3}], CLEAN_MATCH, CLEAN_LAG)
    assert g["pass"] is False and "freezes 1" in g["numbers"]


def test_g2_fails_a_stale_picture():
    """The reel's own frame no longer matches the mezzanine's at the same timestamp."""
    stale = [{"t": 0.5, "corr": 0.99}, {"t": 30.0, "corr": 0.41}, {"t": 59.0, "corr": 0.38}]
    g = reel_qc.g2_picture_sound([], stale, CLEAN_LAG)
    assert g["pass"] is False and any(d["why"] == "frame_match" for d in g["detail"])


def test_g2_fails_a_lag_over_sixty_milliseconds():
    late = [{"at": 0.5, "lag_s": 0.004}, {"at": 30.0, "lag_s": 0.081}, {"at": 59.0, "lag_s": 0.070}]
    g = reel_qc.g2_picture_sound([], CLEAN_MATCH, late)
    assert g["pass"] is False and any(d["why"] == "lag" for d in g["detail"])


def test_g2_fails_a_growing_lag_even_inside_the_budget():
    drift = [{"at": 0.5, "lag_s": 0.002}, {"at": 30.0, "lag_s": 0.030}, {"at": 59.0, "lag_s": 0.057}]
    g = reel_qc.g2_picture_sound([], CLEAN_MATCH, drift)
    assert g["pass"] is False and any(d["why"] == "lag_growth" for d in g["detail"])


def test_g2_reports_what_it_could_not_measure():
    g = reel_qc.g2_picture_sound(None, [], [])
    assert g["pass"] is True and g["skipped"] is True and "no mezzanine" in g["numbers"]


def test_sample_times_spreads_across_a_long_reel():
    t = reel_qc.sample_times(60.0)
    assert len(t) == 3 and t[0] < 5 and 25 < t[1] < 35 and t[2] > 55


def test_sample_times_degrades_gracefully_on_a_very_short_reel():
    t = reel_qc.sample_times(0.4)
    assert len(t) == 1 and 0 <= t[0] <= 0.4


def test_sample_times_handles_zero_duration():
    assert reel_qc.sample_times(0.0) == []


# --- G4 captions in the box and off the head -----------------------------------------------------

def blank(h=426, w=240):
    return np.zeros((h, w), dtype=np.int16)


def test_ink_counts_finds_ink_outside_the_safe_box():
    d = blank(); d[10:14, 4:8] = 255                       # canvas y ~45, x ~18: above the box
    c = reel_qc.ink_counts(d, FACE, reel_qc.crown_y(FACE))
    assert c["ink"] == 16 and c["outside"] == 16


def test_ink_counts_ignores_a_few_stray_pixels_through_the_gate():
    d = blank(); d[10, 4:7] = 255                          # 3 px outside the box
    frames = [{"n": 0, "t": 0.0, **reel_qc.ink_counts(d, FACE, reel_qc.crown_y(FACE))}]
    assert reel_qc.g4_captions(frames, [], 30)["pass"] is True


def test_g4_fails_one_frame_of_ink_outside_the_safe_box():
    d = blank(); d[10, 0:9] = 255                          # 9 px outside
    frames = [{"n": 0, "t": 0.0, **reel_qc.ink_counts(d, FACE, reel_qc.crown_y(FACE))}]
    g = reel_qc.g4_captions(frames, [], 30)
    assert g["pass"] is False and "outside 1" in g["numbers"]


def test_g4_fails_ink_across_the_face():
    d = blank(); d[195:205, 110:130] = 255                 # canvas ~ (540, 900): dead centre of the face
    c = reel_qc.ink_counts(d, FACE, reel_qc.crown_y(FACE))
    assert c["on_face"] > 0
    g = reel_qc.g4_captions([{"n": 0, "t": 0.0, **c}], [], 30)
    assert g["pass"] is False and any(x["why"] == "face" for x in g["detail"])


def test_g4_allows_ink_above_the_crown_only_while_a_hook_or_lockup_is_live():
    d = blank(); d[75:85, 40:120] = 255                    # canvas y ~ 338-383, above the crown (crown y ~ 580)
    c = reel_qc.ink_counts(d, FACE, reel_qc.crown_y(FACE))
    assert c["above_crown"] > 0 and c["outside"] == 0
    frames = [{"n": 0, "t": 1.0, **c}]
    assert reel_qc.g4_captions(frames, [], 30)["pass"] is False
    lockup = [{"mode": "front", "words": [{"text": "DROP", "at": 0.9}], "start": 0.9, "end": 2.0}]
    assert reel_qc.g4_captions(frames, lockup, 30)["pass"] is True
    earlier = [{"mode": "front", "words": [{"text": "DROP", "at": 0.1}], "start": 0.1, "end": 0.5}]
    assert reel_qc.g4_captions(frames, earlier, 30)["pass"] is False      # the lockup is gone by the time the ink is up there


def test_g4_says_so_when_there_was_nothing_to_sweep():
    g = reel_qc.g4_captions([], [], 30)
    assert g["skipped"] is True and g["pass"] is True


def test_g4_skips_whip_cut_frames():
    """A whip cut blurs and pushes the whole frame on purpose; that ink must not fail the gate."""
    d = blank(); d[:, :] = 255                              # ink everywhere: what a whip frame looks like
    c = reel_qc.ink_counts(d, FACE, reel_qc.crown_y(FACE))
    frames = [{"n": 30, "t": 1.0, **c}]
    assert reel_qc.g4_captions(frames, [], 30, whips=[{"at": 30, "frames": 4}])["pass"] is True
    assert "1 whip frames skipped" in reel_qc.g4_captions(frames, [], 30, whips=[{"at": 30, "frames": 4}])["numbers"]


# --- G5 caption events ---------------------------------------------------------------------------

WORDS_DOC = {"words": [{"text": "why", "start": 0.00, "end": 0.20, "filler": False},
                       {"text": "your", "start": 0.25, "end": 0.45, "filler": False},
                       {"text": "calendar", "start": 0.50, "end": 1.00, "filler": False}]}


def evs(*spans):
    return [{"mode": "front", "words": [{"text": t, "at": a}], "start": a, "end": b} for t, a, b in spans]


GOOD_EVENTS = evs(("why", 0.00, 0.24), ("your", 0.25, 0.49), ("calendar", 0.50, 1.10))


def test_g5_passes_a_clean_plan():
    g = reel_qc.g5_events(GOOD_EVENTS, WORDS_DOC, [], 30)
    assert g["pass"] is True and "uncovered words 0" in g["numbers"]


def test_g5_catches_a_sub_three_frame_event_and_names_it_by_its_words():
    bad = evs(("why", 0.00, 0.24), ("your", 0.25, 0.30), ("calendar", 0.50, 1.10))
    g = reel_qc.g5_events(bad, WORDS_DOC, [], 30)
    assert g["pass"] is False
    short = [d for d in g["detail"] if d["why"] == "short"]
    assert short and short[0]["text"] == "your"
    assert '"your"' in reel_qc.plain("G5", short[0])


def test_g5_wants_every_spoken_word_on_screen_or_named_in_a_flag():
    g = reel_qc.g5_events(GOOD_EVENTS[:2], WORDS_DOC, [], 30)
    assert g["pass"] is False and any(d["why"] == "uncovered" for d in g["detail"])
    ok = reel_qc.g5_events(GOOD_EVENTS[:2], WORDS_DOC, ["word_dropped_no_time:calendar"], 30)
    assert ok["pass"] is True


def test_g5_covers_a_hyphen_joined_word_on_both_halves():
    """cluster_plan.py glues ASR splits like 'go'+'to' into 'go-to'; the hyphen split must let
    EITHER half of the spoken pair count as covered by that one lockup event."""
    words = {"words": [{"text": "go", "start": 0.00, "end": 0.20, "filler": False},
                       {"text": "to", "start": 0.20, "end": 0.35, "filler": False}]}
    lockup = [{"mode": "front", "words": [{"text": "go-to", "at": 0.00}], "start": 0.0, "end": 0.9}]
    g = reel_qc.g5_events(lockup, words, [], 30)
    assert g["pass"] is True and "uncovered words 0" in g["numbers"]


def test_g5_wants_the_word_up_on_screen_when_it_is_spoken_not_just_a_row_with_its_text_somewhere():
    """A cluster cut off before a word came still holds that word's row in the plan: matched by text and onset alone it counted
    as covered while the viewer never saw it (a page that ended mid-phrase)."""
    cut_off = [{"mode": "front", "start": 0.0, "end": 0.40, "words": [{"text": "why", "at": 0.0}, {"text": "your", "at": 0.25}, {"text": "calendar", "at": 0.5}]}]
    g = reel_qc.g5_events(cut_off, WORDS_DOC, [], 30)
    assert g["pass"] is False and [d["text"] for d in g["detail"]] == ["calendar"]
    page = {"mode": "page", "start": 0.45, "end": 1.1, "text": "calendar"}
    assert reel_qc.g5_events(cut_off + [page], WORDS_DOC, [], 30)["pass"] is True              # a page over it shows it
    multi = [{"mode": "front", "start": 0.0, "end": 0.40, "words": [{"text": "why your calendar", "at": 0.0}]}]
    assert [d["text"] for d in reel_qc.g5_events(multi, WORDS_DOC, [], 30)["detail"]] == ["calendar"]   # one row of several words: the same rule
    assert reel_qc.g5_events([dict(multi[0], end=1.1)], WORDS_DOC, [], 30)["pass"] is True


def test_g5_skips_the_transcript_checks_without_words():
    g = reel_qc.g5_events(GOOD_EVENTS, None, [], 30)
    assert g["pass"] is True and "no words.json" in g["numbers"]


def test_g5_says_so_without_a_plan():
    g = reel_qc.g5_events(None, WORDS_DOC, [], 30)
    assert g["skipped"] is True and g["pass"] is True


# --- G6 / G7 / G8 / verdict ------------------------------------------------------------------------

def test_g6_is_recorded_as_a_human_read_not_a_machine_verdict():
    g = reel_qc.g6_visual("/w/evidence/strip.png")
    assert g["pass"] is None and g["human"] is True and "strip.png" in g["numbers"]


def test_g7_is_report_only_by_default_even_when_out_of_band():
    """The clip arrives already mixed: G7 never blocks delivery unless the look asks for strict enforcement."""
    g = reel_qc.g7_loudness(-21.4, -6.0)
    assert g["pass"] is True and g["report_only"] is True
    assert any(d["why"] == "integrated" for d in g["detail"])       # measured and recorded
    assert "audit only" in g["numbers"]


def test_g7_enforces_the_band_under_strict_loudness():
    assert reel_qc.g7_loudness(-16.2, -1.9, strict=True)["pass"] is True
    assert reel_qc.g7_loudness(-21.4, -6.0, strict=True)["pass"] is False
    assert reel_qc.g7_loudness(-15.9, -0.8, strict=True)["pass"] is False       # true peak too hot


def test_g7_skips_when_nothing_was_measured():
    assert reel_qc.g7_loudness(None, None)["skipped"] is True


def test_an_audit_only_mix_off_the_target_carries_one_plain_check_sentence_for_the_editor():
    """The engine leaves the mix alone, so a person has to be told: a mix at -20 LUFS plays quieter on social platforms than most videos."""
    g = reel_qc.g7_loudness(-20.0, -0.5)
    assert g["pass"] is True and g["mode"] == "audit" and g["lufs"] == -20.0 and g["true_peak"] == -0.5
    assert g["check"] == ("this cut is mixed at -20.0 LUFS; social platforms play it quieter than most videos. "
                          "It also peaks at -0.5 dBTP, which can crackle when a platform re-encodes it.")
    assert "\n" not in g["check"]                                                   # the peak rides in the same one line
    assert reel_qc.g7_loudness(-16.0, -0.5)["check"] == "this cut peaks at -0.5 dBTP; it can crackle when a platform re-encodes it."
    flat = reel_qc.g7_loudness(-9.0, -3.0)["check"]
    assert flat == "this cut is mixed at -9.0 LUFS; social platforms turn it down, so it plays flatter than most videos." and "dBTP" not in flat


def test_a_mix_inside_the_target_has_no_check_and_the_band_edges_are_inside_it():
    for i, tp in ((-16.0, -2.0), (-15.0, -1.5), (-17.0, -1.5), (-16.5, -3.0)):
        g = reel_qc.g7_loudness(i, tp)
        assert "check" not in g and g["pass"] is True, (i, tp)
    assert "check" in reel_qc.g7_loudness(-17.1, -3.0) and "check" in reel_qc.g7_loudness(-14.9, -3.0)
    assert "check" in reel_qc.g7_loudness(-16.0, -1.4)


def test_an_enforced_g7_blocks_instead_of_advising():
    """master_audio: loudnorm: G7 is a gate (no check sentence, the failure is the plain G7 line), and its record says it was enforced."""
    g = reel_qc.g7_loudness(-20.0, -0.5, strict=True)
    assert g["pass"] is False and g["mode"] == "enforced" and "check" not in g
    others = {k: {"pass": True} for k in reel_qc.MACHINE_GATES if k != "G7"}
    assert reel_qc.verdict_of(others | {"G7": g}) == "FAIL"
    assert reel_qc.verdict_of(others | {"G7": reel_qc.g7_loudness(-20.0, -0.5)}) == "PASS"      # the same mix as an audit never blocks
    assert reel_qc.g7_loudness(-16.0, -2.0, strict=True)["mode"] == "enforced"
    assert reel_qc.g7_loudness(-16.0, -2.0, strict=True)["pass"] is True


def test_the_loudness_check_never_speaks_without_a_finite_measurement():
    assert reel_qc.loudness_check(None, None) is None
    assert reel_qc.loudness_check(float("-inf"), -3.0) is None


def test_g8_fails_a_reel_with_no_audio_stream(tmp_path):
    """The direct defense against the renderer's own audio never being remuxed in."""
    p = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                    "-pix_fmt", "yuv420p", "-an", str(p)], check=True)
    g = reel_qc.g8_audio_origin(str(p), None)
    assert g["pass"] is False and any(d["why"] == "no_audio_stream" for d in g["detail"])


def test_g8_skips_when_the_reel_itself_is_missing():
    g = reel_qc.g8_audio_origin("/no/such/reel.mp4", None)
    assert g["pass"] is True and g["skipped"] is True


def test_verdict_fails_on_any_machine_gate_and_ignores_the_human_one():
    ok = {k: {"pass": True} for k in reel_qc.MACHINE_GATES}
    ok["G6"] = {"pass": None, "human": True}
    assert reel_qc.verdict_of(ok) == "PASS"
    for k in reel_qc.MACHINE_GATES:
        bad = dict(ok, **{k: {"pass": False}})
        assert reel_qc.verdict_of(bad) == "FAIL", k


def test_verdict_passes_when_a_gate_was_skipped():
    g = {k: {"pass": True} for k in ("G-FACE", "G2", "G5", "G7", "G8", "G9")}
    g["G4"] = {"pass": True, "skipped": True}
    g["G6"] = {"pass": None, "human": True}
    assert reel_qc.verdict_of(g) == "PASS"


def test_every_gate_prints_one_line_with_its_numbers():
    g = reel_qc.g_face(SRC_WORDS, track_for(22))
    line = reel_qc.line("G-FACE", g)
    assert line.startswith("G-FACE PASS ") and "\n" not in line and "face" in line
    assert reel_qc.line("G6", reel_qc.g6_visual("/w/strip.png")).startswith("G6 HUMAN ")
    assert reel_qc.line("G4", reel_qc.g4_captions([], [], 30)).startswith("G4 SKIP ")
    assert reel_qc.line("G7", reel_qc.g7_loudness(-21.4, -6.0)).startswith("G7 AUDIT ")


# --- plain-language failure lines ------------------------------------------------------------------

def test_plain_renders_a_one_sentence_explanation_for_every_gate():
    """Every FAIL line goes through plain() so a non-engineer reading the CLI output (not
    reel-qc.json) gets a sentence, not a {"why": ...} dict."""
    cases = [
        ("G-FACE", {"why": "face", "fraction": 0.5, "threshold": 0.9}),
        ("G-WORDS", {"why": "banned", "word": "cheap", "t": 2.4}),
        ("G-ACCENT", {"why": "accent_colour", "colour": "#FFD60A", "source": [255.0, 214.0, 10.0], "mezzanine": [234.0, 228.0, 11.0], "shift": 21.0}),
        ("G2", {"why": "freeze", "at": 12.0, "s": 1.1}),
        ("G2", {"why": "lag", "at": 5.0, "lag_s": 0.09}),
        ("G4", {"why": "face", "t": 3.0}),
        ("G5", {"why": "uncovered", "text": "empty", "t": 10.4}),
        ("G7", {"why": "true_peak", "dbtp": -0.2}),
        ("G8", {"why": "no_audio_stream"}),
        ("G9", {"why": "pix_fmt", "got": "yuvj420p", "want": "yuv420p", "plain": "re-export the source."}),
        ("G10", {"why": "median", "median": 2.4, "max": 1.8}),
        ("G10", {"why": "p90", "p90": 4.1, "max": 3.5}),
        ("G10", {"why": "max", "gap": 7.2, "max": 5.0, "at": 3.0}),
    ]
    for name, d in cases:
        s = reel_qc.plain(name, d)
        assert isinstance(s, str) and len(s) > 0 and "{" not in s and "why" not in s, (name, d, s)


# --- the numeric helpers behind G2 / G8 -------------------------------------------------------------

def test_ncc_is_one_for_identical_frames_and_low_for_unrelated_ones():
    rng = np.random.default_rng(7)
    a = rng.integers(0, 255, (60, 40)).astype(np.int16)
    b = rng.integers(0, 255, (60, 40)).astype(np.int16)
    assert reel_qc.ncc(a, a) > 0.999
    assert reel_qc.ncc(a, b) < 0.5
    assert reel_qc.ncc(a, np.full_like(a, 7)) == 0.0        # a flat frame correlates with nothing


def test_face_fraction_ignores_seconds_the_track_never_had_a_frame_for():
    """The words run to 23.57 s but the video stream stops at 23.43 s, so second 23 was never extracted: 'face 67%' for a face seen in every frame there was"""
    track = {"samples": {"21": dict(FACE), "22": dict(FACE)}, "extracted": [0, 21, 22]}
    assert reel_qc.face_fraction(track, 20.93, 23.57) == (1.0, 2)
    assert reel_qc.face_fraction({"samples": {"21": dict(FACE), "22": dict(FACE)}}, 20.93, 23.57) == (2 / 3, 3)    # a track written before "extracted" existed reads as before
    assert reel_qc.face_fraction({"samples": {"21": dict(FACE)}, "extracted": [21, 22]}, 20.93, 23.57) == (0.5, 2)   # a second that had a frame and no face still counts against it


def test_lag_of_recovers_a_known_offset():
    sr = 16000
    rng = np.random.default_rng(3)
    base = rng.standard_normal(sr * 2).astype(np.float32)
    shifted = np.concatenate([np.zeros(480, np.float32), base])[:sr * 2]   # b is 30 ms late
    assert abs(reel_qc.lag_of(base, shifted, sr) - 0.030) < 0.002
    assert abs(reel_qc.lag_of(base, base, sr)) < 0.001


def test_lag_and_strength_says_how_well_the_two_sounds_match():
    sr = 16000
    rng = np.random.default_rng(5)
    base = rng.standard_normal(sr * 2).astype(np.float32)
    lag, strength = reel_qc.lag_and_strength(base, np.concatenate([np.zeros(480, np.float32), base])[:sr * 2], sr)
    assert abs(lag - 0.030) < 0.002 and strength > 0.9                                   # the same sound, 30 ms late
    other = rng.standard_normal(sr * 2).astype(np.float32)
    assert reel_qc.lag_and_strength(base, other, sr)[1] < reel_qc.LAG_MATCH_MIN          # a different sound
    assert reel_qc.lag_and_strength(base, np.zeros(sr * 2, np.float32), sr) == (0.0, 0.0)   # silence has no lag, not a lag of zero
    assert reel_qc.lag_and_strength(base[:10], base[:10], sr) == (0.0, 0.0)              # too short to say


def test_g2_fails_audio_that_does_not_match_the_take_s_audio_at_all():
    nonsense = [{"at": 0.0, "lag_s": 0.0, "corr": 0.04}]
    g = reel_qc.g2_picture_sound([], CLEAN_MATCH, nonsense)
    assert g["pass"] is False and [d["why"] for d in g["detail"]] == ["no_match"]
    assert "does not match" in reel_qc.plain("G2", g["detail"][0])
    ok = reel_qc.g2_picture_sound([], CLEAN_MATCH, [{"at": 0.0, "lag_s": 0.004, "corr": 0.99}])
    assert ok["pass"] is True and "audio lag vs audio.wav max 4ms" in ok["numbers"]


def test_g2_says_so_when_there_was_no_audio_to_measure():
    g = reel_qc.g2_picture_sound([], CLEAN_MATCH, [])
    assert g["pass"] is True and "audio lag n/a" in g["numbers"]


def _wav_and_reel(tmp, name, seconds, delay_ms=0, sound="pink"):
    """audio.wav (48 kHz pink noise, or silence) and a reel mp4 carrying the same sound `delay_ms` late."""
    wav, reel = str(tmp / f"{name}.wav"), str(tmp / f"{name}-reel.mp4")
    src = (["-f", "lavfi", "-i", f"anoisesrc=color=pink:duration={seconds}:sample_rate=48000:seed=11"] if sound == "pink"
           else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(seconds)])
    subprocess.run(["ffmpeg", "-v", "error", "-y", *src, wav], check=True)
    af = ["-af", f"adelay={delay_ms}:all=1"] if delay_ms else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:size=64x112:rate=30:duration={seconds}", "-i", wav,
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", *af, "-c:a", "aac", "-shortest", reel], check=True)
    return wav, reel


def test_audio_lag_measures_the_reels_own_audio_against_audio_wav(tmp_path):
    """The mezzanine has no audio track, so G2's lag used to compare against nothing and read "n/a" on every take."""
    wav, reel = _wav_and_reel(tmp_path, "aligned", 6)
    lags = reel_qc.audio_lag(reel, wav)
    assert len(lags) == 1 and lags[0]["at"] == 0.0 and abs(lags[0]["lag_s"]) < 0.005 and lags[0]["corr"] > 0.9
    assert reel_qc.g2_picture_sound([], [], lags)["pass"] is True
    wav, late = _wav_and_reel(tmp_path, "late", 6, delay_ms=80)
    lags = reel_qc.audio_lag(late, wav)
    assert 0.070 < lags[0]["lag_s"] < 0.090 and lags[0]["corr"] > 0.9                    # 80 ms late: the sign is "the reel lags"
    g = reel_qc.g2_picture_sound([], [], lags)
    assert g["pass"] is False and [d["why"] for d in g["detail"]] == ["lag"]
    assert "out of sync by about 8" in reel_qc.plain("G2", g["detail"][0])
    wav, ok = _wav_and_reel(tmp_path, "inside", 6, delay_ms=40)
    assert reel_qc.g2_picture_sound([], [], reel_qc.audio_lag(ok, wav))["pass"] is True      # 40 ms is inside the 60 ms budget


def test_audio_lag_reads_the_first_ten_seconds_and_the_last_ten_of_a_long_reel(tmp_path):
    wav, reel = _wav_and_reel(tmp_path, "long", 26)
    lags = reel_qc.audio_lag(reel, wav)
    assert [l["at"] for l in lags] == [0.0, 16.0] and all(l["win_s"] == 10.0 for l in lags)
    short_wav, short = _wav_and_reel(tmp_path, "short", 12)
    assert [l["at"] for l in reel_qc.audio_lag(short, short_wav)] == [0.0]                  # too short for a second window


def test_audio_lag_leaves_out_a_silent_window_instead_of_reading_it_as_zero(tmp_path):
    wav, reel = _wav_and_reel(tmp_path, "quiet", 3, sound="silence")
    assert reel_qc.audio_lag(reel, wav) == []
    assert reel_qc.audio_lag(reel, str(tmp_path / "nope.wav")) == [] and reel_qc.audio_lag(str(tmp_path / "nope.mp4"), wav) == []


def test_audio_lag_sees_audio_that_is_not_the_takes_audio(tmp_path):
    wav, _ = _wav_and_reel(tmp_path, "mine", 6)
    other_wav, other_reel = _wav_and_reel(tmp_path, "theirs", 6)                              # same recipe, so build a different sound
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", other_reel, "-f", "lavfi", "-i",
                    "anoisesrc=color=white:duration=6:sample_rate=48000:seed=99", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                    "-shortest", str(tmp_path / "swapped.mp4")], check=True)
    lags = reel_qc.audio_lag(str(tmp_path / "swapped.mp4"), wav)
    assert lags and lags[0]["corr"] < reel_qc.LAG_MATCH_MIN
    assert reel_qc.g2_picture_sound([], [], lags)["pass"] is False


# --- the gate list: G-ACCENT is in it, the colour grade is not ----------------------------------------------

def test_the_gate_list_holds_g_accent_and_no_grade_gate():
    assert reel_qc.GATE_ORDER == ("G-FACE", "G2", "G4", "G5", "G-WORDS", "G-ACCENT", "G6", "G7", "G8", "G9", "G10")
    assert "G-ACCENT" in reel_qc.MACHINE_GATES and "G-ACCENT" in reel_qc.GATE_ORDER
    assert "G-GRADE" not in reel_qc.MACHINE_GATES and "G-GRADE" not in reel_qc.GATE_ORDER
    assert reel_qc.MACHINE_GATES == tuple(g for g in reel_qc.GATE_ORDER if g != "G6")      # G6 is the human read: it has no verdict
    assert not any(hasattr(reel_qc, n) for n in ("g_grade", "_grade_report", "brand_drift", "BRAND_DRIFT_MAX"))


# --- the CLI, end to end on a short lavfi take -------------------------------------------------------

CAPTION_BOX = (40, 290, 120, 35)     # inside the safe box (canvas x 65-940 / y 270-1420 == 270x480 x 16-235 / y 68-355), below the face box and the crown line
OUTSIDE_BOX = (2, 8, 60, 30)         # leaves the safe box at the top left
BT709_TAGS = ["-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def _flat_vf(box):
    """a flat box drawn on the picture, with the colour conversion to BT.709 limited range made explicitly, so the colour is coded the way a player decodes it"""
    x, y, w, h, colour = box
    return (f"drawbox=x={x}:y={y}:w={w}:h={h}:color={colour}:t=fill,"
            "scale=out_color_matrix=bt709:out_range=limited:flags=accurate_rnd+full_chroma_int,format=yuv420p")


def _take(tmp, name, box, no_audio_on_reel=False, audio_delay_ms=0, seconds=2, word="why", mez_box=None, source_box=None):
    """A minimal take-dir the CLI can read: a `seconds` long 270x480 mezzanine (real bt709/tv/yuv420p tags, a moving picture over pink noise so freezedetect
    and cross-correlation both have something real to measure) and a 'reel' that is the SAME picture with one white box drawn on it (the box IS the caption ink
    the sweep has to find), remuxed with the SAME audio.wav this take extracted (output time == source time: no cutting, no retiming). The one caption says
    `word` the whole time. `mez_box` and `source_box` (x, y, w, h, 0xRRGGBB) draw a flat colour into the mezzanine and into a source.mp4 beside it, for the
    accent gate."""
    t = os.path.join(tmp, name); os.makedirs(t, exist_ok=True)
    mez = os.path.join(t, "mez.mp4")
    base = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=270x480:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:duration={seconds}:sample_rate=48000:seed=42"]
    tail = ["-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", *BT709_TAGS,
            "-c:a", "aac", "-ar", "48000", "-shortest"]
    subprocess.run(base + (["-vf", _flat_vf(mez_box)] if mez_box else []) + tail + [mez], check=True)
    if source_box:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=270x480:rate=30:duration={seconds}", "-vf", _flat_vf(source_box),
                        "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", *BT709_TAGS, "-an", os.path.join(t, "source.mp4")], check=True)
    audio_wav = os.path.join(t, "audio.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mez, "-vn", "-ar", "48000", audio_wav], check=True)
    render = os.path.join(t, "render.mp4")   # the un-remuxed render: video only, box drawn on
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", mez, "-vf",
                    "drawbox=x=%d:y=%d:w=%d:h=%d:color=white:t=fill,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" % box,
                    "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", *BT709_TAGS,
                    "-an", render], check=True)
    reel = os.path.join(t, f"{name}-reel.mp4")
    if no_audio_on_reel:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", render, "-c:v", "copy", "-an", reel], check=True)
    else:
        late = ["-af", f"adelay={audio_delay_ms}:all=1"] if audio_delay_ms else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", render, "-i", audio_wav,
                        "-map", "0:v", "-map", "1:a", "-c:v", "copy", *late, "-c:a", "aac", "-shortest", reel], check=True)
    end = round(seconds - 0.2, 2)
    json.dump({"source": "deepgram", "transcript": word,
               "words": [{"text": word, "start": 0.02, "end": end, "filler": False}]},
              open(os.path.join(t, "words.json"), "w"))
    json.dump({"W": 1080, "H": 1920, "seconds": seconds, "samples": {str(s): dict(FACE) for s in range(seconds)},
               "median": dict(FACE)}, open(os.path.join(t, "facetrack.json"), "w"))
    json.dump({"src": "mez.mp4", "flags": [],
               "events": [{"mode": "front", "words": [{"text": word, "at": 0.02}], "start": 0.02, "end": end}]},
              open(os.path.join(t, "plan.json"), "w"))
    return t


def _look(t, brand="varsans", **over):
    """<take>/look.json as the chain writes it: a fixture look's whole profile, `over` replacing keys."""
    with open(os.path.join(t, "look.json"), "w") as f:
        json.dump(dict(cap_helpers.profile(brand), **over), f)


def _cli(t, name, *extra):
    return subprocess.run([sys.executable, "-B", REEL_QC, t, name, *extra], capture_output=True, text=True)


def _card(t):
    return json.load(open(os.path.join(t, "reel-qc.json")))


def test_cli_sweeps_a_real_reel_and_writes_the_evidence(tmp_path):
    """The box sits inside the safe box, below the face box and well below the crown line. A take with no look.json has no accent to compare and
    no pace gate to run, and both say so."""
    t = _take(str(tmp_path), "t1", CAPTION_BOX)
    r = _cli(t, "t1")
    assert r.returncode in (0, 1), r.stdout + r.stderr
    card = _card(t)
    assert set(card["gates"]) == {"G-FACE", "G2", "G4", "G5", "G-WORDS", "G-ACCENT", "G6", "G7", "G8", "G9", "G10"}
    assert card["gates"]["G-ACCENT"]["skipped"] is True and "not measured" in card["gates"]["G-ACCENT"]["numbers"]      # no look.json, so no accent to look for
    assert card["gates"]["G10"]["skipped"] is True and "pace gate off" in card["gates"]["G10"]["numbers"]               # no look.json, so the pace gate is off
    for k in ("G-FACE", "G4", "G5", "G8", "G9"):
        assert card["gates"][k]["pass"] is True, (k, card["gates"][k])
    assert card["gates"]["G4"]["skipped"] is False and card["gates"]["G4"]["frames"] > 0
    for p in (card["strip"], card["sheet"]):
        assert os.path.getsize(p) > 0 and open(p, "rb").read(4) == b"\x89PNG"
    assert "G-FACE " in r.stdout and "G9 " in r.stdout and "G-ACCENT SKIP " in r.stdout and "G10 SKIP " in r.stdout
    assert "G-GRADE" not in r.stdout


def test_cli_fails_and_exits_one_when_caption_ink_leaves_the_safe_box(tmp_path):
    t = _take(str(tmp_path), "t2", OUTSIDE_BOX)
    r = _cli(t, "t2")
    assert r.returncode == 1, r.stdout + r.stderr
    card = _card(t)
    assert card["verdict"] == "FAIL" and card["gates"]["G4"]["pass"] is False
    assert "G4 FAIL" in r.stdout
    assert any("safe zone" in line for line in r.stdout.splitlines())   # plain-language failure line


def test_cli_fails_g8_when_the_reel_has_no_audio(tmp_path):
    """The named defense against the renderer's own audio shipping instead of the remuxed source."""
    t = _take(str(tmp_path), "t3", CAPTION_BOX, no_audio_on_reel=True)
    r = _cli(t, "t3")
    assert r.returncode == 1, r.stdout + r.stderr
    card = _card(t)
    assert card["gates"]["G8"]["pass"] is False
    assert any(d["why"] == "no_audio_stream" for d in card["gates"]["G8"]["detail"])
    assert any("never remuxed in" in line for line in r.stdout.splitlines())


def test_cli_g2_measures_the_audio_lag_against_audio_wav_and_fails_a_late_remux(tmp_path):
    """G2 used to read "lag n/a" on every take (it compared the reel with the mezzanine, which has no audio). Now the same reel passes
    with its own audio and fails with the audio 80 ms late."""
    t = _take(str(tmp_path), "t7", CAPTION_BOX)
    r = _cli(t, "t7")
    g2 = _card(t)["gates"]["G2"]
    assert g2["pass"] is True and "audio lag vs audio.wav max" in g2["numbers"] and "n/a" not in g2["numbers"].split("audio lag")[1], g2
    t = _take(str(tmp_path), "t8", CAPTION_BOX, audio_delay_ms=80)
    r = _cli(t, "t8")
    card = _card(t)
    assert r.returncode == 1 and card["gates"]["G2"]["pass"] is False
    assert any(d["why"] == "lag" and 0.07 < d["lag_s"] < 0.09 for d in card["gates"]["G2"]["detail"])
    assert any(line.strip().startswith("G2: the audio is out of sync by about 8") for line in r.stdout.splitlines()), r.stdout


def test_cli_g7_never_blocks_the_verdict_without_strict_loudness(tmp_path):
    t = _take(str(tmp_path), "t4", CAPTION_BOX)
    r = _cli(t, "t4")
    card = _card(t)
    assert card["gates"]["G7"]["pass"] is True and card["gates"]["G7"].get("report_only") is True
    assert card["gates"]["G7"]["mode"] == "audit" and "check" in card["gates"]["G7"]      # this clip's own mix is off the target
    assert "social platforms" in card["gates"]["G7"]["check"]


def test_cli_strict_loudness_makes_g7_a_gate(tmp_path):
    t = _take(str(tmp_path), "t9", CAPTION_BOX)
    r = _cli(t, "t9", "--strict-loudness")
    card = _card(t)
    assert r.returncode == 1 and card["gates"]["G7"]["pass"] is False and card["gates"]["G7"]["mode"] == "enforced"
    assert any(line.strip().startswith("G7: the audio loudness is") for line in r.stdout.splitlines()), r.stdout


def test_cli_stops_with_one_line_when_the_reel_is_missing(tmp_path):
    t = os.path.join(str(tmp_path), "t5"); os.makedirs(t)
    json.dump({"words": []}, open(os.path.join(t, "words.json"), "w"))
    r = _cli(t, "t5")
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.strip().startswith("STOP:") and "no reel to check" in r.stderr and "Traceback" not in r.stderr


def test_cli_stops_with_one_line_when_words_json_is_missing(tmp_path):
    t = os.path.join(str(tmp_path), "t6"); os.makedirs(t)
    open(os.path.join(t, "t6-reel.mp4"), "w").close()
    r = _cli(t, "t6")
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.strip().startswith("STOP:") and "words.json" in r.stderr and "Traceback" not in r.stderr


# --- G-ACCENT: the look's accent colour through the picture path ----------------------------------------------

ACCENT = cap_helpers.profile("varsans")["accent"]       # the fixture look's accent: #FFD60A
ACCENT_RGB = tuple(int(ACCENT.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def _flat_clip(path, rgb, matrix="bt709", rng="tv", space="bt709"):
    """a 2 s flat-colour clip coded with the given matrix and range, tagged to match"""
    pix = "yuvj420p" if rng == "pc" else "yuv420p"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x%02X%02X%02X:s=216x384:r=30:d=2,format=rgb24" % rgb,
                    "-vf", f"scale=out_color_matrix={matrix}:out_range={'full' if rng == 'pc' else 'limited'}:flags=accurate_rnd+full_chroma_int,format={pix}",
                    "-c:v", "libx264", "-crf", "8", "-pix_fmt", pix, "-color_range", rng, "-colorspace", space, path], check=True)


def _shifted(rgb, delta):
    return tuple(max(0, min(255, c + d)) for c, d in zip(rgb, delta))


def _drift(delta, accent=ACCENT):
    """an accent_drift record: the accent's own pixels in the source, and the same pixels `delta` levels away in the mezzanine"""
    src = [float(c) for c in ACCENT_RGB]
    mez = [round(c + d, 1) for c, d in zip(src, delta)]
    return {"colour": accent, "pixels": 5000, "source": src, "mezzanine": mez, "shift": round(max(abs(d) for d in delta), 1)}


def test_the_accent_check_reads_the_pictures_and_ignores_how_each_file_is_coded(tmp_path):
    src, same, moved = (str(tmp_path / n) for n in ("src.mp4", "same.mp4", "moved.mp4"))
    _flat_clip(src, ACCENT_RGB, "bt601", "pc", "bt470bg")              # the source: a BT.601 full-range export
    _flat_clip(same, ACCENT_RGB)                                       # the mezzanine: the same colour, BT.709 limited
    _flat_clip(moved, _shifted(ACCENT_RGB, (-21, 14, 1)))              # a white balance cast on an accent-coloured end card: -21 red, +14 green
    ok = reel_qc.accent_drift(src, same, ACCENT)
    assert ok and ok["shift"] <= 1.5 and ok["colour"] == ACCENT.upper() and ok["pixels"] > reel_qc.ACCENT_MIN_PX
    bad = reel_qc.accent_drift(src, moved, ACCENT)
    assert bad and bad["shift"] > 15
    grey = str(tmp_path / "grey.mp4")
    _flat_clip(grey, (120, 120, 120))
    assert reel_qc.accent_drift(grey, grey, ACCENT) is None            # the accent is not on screen: nothing to compare


def test_the_accent_check_works_on_the_other_fixture_look_too(tmp_path):
    accent = cap_helpers.profile("upstatic")["accent"]
    rgb = tuple(int(accent.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    src, same = str(tmp_path / "src.mp4"), str(tmp_path / "same.mp4")
    _flat_clip(src, rgb, "bt601", "pc", "bt470bg")
    _flat_clip(same, rgb)
    ok = reel_qc.accent_drift(src, same, accent)
    assert ok and ok["shift"] <= 1.5 and ok["colour"] == accent.upper()
    assert reel_qc.accent_drift(src, same, ACCENT) is None             # the first look's yellow is not on this look's screen


def test_the_drift_limit_is_two_levels():
    assert reel_qc.ACCENT_DRIFT_MAX == 2.0
    assert reel_qc.ACCENT_MIN_PX == 300 and reel_qc.ACCENT_NEAR == 30 and reel_qc.ACCENT_FRAMES == 10


def test_a_moved_accent_fails_g_accent_and_a_kept_one_passes():
    fine = _drift((-0.5, 0.2, 0.5))
    g = reel_qc.g_accent(fine)
    assert g["pass"] is True and g["skipped"] is False and g["detail"] == [] and g["accent_colour"] == fine
    assert f"accent colour {ACCENT} within 0.5 levels of the source" in g["numbers"]
    moved = _drift((-20.8, 13.8, 1.4))
    g = reel_qc.g_accent(moved)
    assert g["pass"] is False and g["skipped"] is False and g["accent_colour"] == moved
    assert g["detail"] == [{"why": "accent_colour", "colour": ACCENT, "source": moved["source"], "mezzanine": moved["mezzanine"], "shift": 20.8}]
    assert f"accent colour {ACCENT} moved 20.8 levels (limit 2)" in g["numbers"]
    assert reel_qc.line("G-ACCENT", g).startswith("G-ACCENT FAIL ") and reel_qc.line("G-ACCENT", reel_qc.g_accent(fine)).startswith("G-ACCENT PASS ")


def test_the_failure_says_in_plain_words_what_moved_and_what_to_do():
    moved = _drift((-20.8, 13.8, 1.4))
    said = reel_qc.plain("G-ACCENT", reel_qc.g_accent(moved)["detail"][0])
    assert ACCENT in said and "(255.0, 214.0, 10.0)" in said and "(234.2, 227.8, 11.4)" in said
    assert "20.8 levels off (limit 2)" in said and "re-export the source as plain Rec.709" in said
    assert "{" not in said and "why" not in said and "grade" not in said


def test_the_limit_is_inclusive_at_two_levels_and_fails_beyond_it():
    assert reel_qc.g_accent(_drift((2.0, 0.0, 0.0)))["pass"] is True
    assert reel_qc.g_accent(_drift((0.0, -2.0, 0.0)))["pass"] is True
    assert reel_qc.g_accent(_drift((2.1, 0.0, 0.0)))["pass"] is False
    assert reel_qc.g_accent(_drift((0.0, 0.0, -2.1)))["pass"] is False


def test_an_accent_that_is_not_on_screen_is_a_visible_skip_never_a_silent_pass():
    for nothing in (None, {}):
        g = reel_qc.g_accent(nothing)
        assert g["pass"] is True and g["skipped"] is True and g["detail"] == [] and "not measured" in g["numbers"]
        assert reel_qc.line("G-ACCENT", g).startswith("G-ACCENT SKIP ")
    assert reel_qc.g_accent()["skipped"] is True


def test_a_moved_accent_fails_the_verdict_and_a_skipped_one_does_not():
    others = {k: {"pass": True} for k in reel_qc.MACHINE_GATES if k != "G-ACCENT"}
    assert reel_qc.verdict_of(others | {"G-ACCENT": reel_qc.g_accent(_drift((-20.8, 13.8, 1.4)))}) == "FAIL"
    assert reel_qc.verdict_of(others | {"G-ACCENT": reel_qc.g_accent(None)}) == "PASS"
    assert reel_qc.verdict_of(others | {"G-ACCENT": reel_qc.g_accent(_drift((0.5, 0.0, 0.0)))}) == "PASS"


def test_cli_g_accent_reads_the_looks_accent_against_the_source_and_fails_a_moved_colour(tmp_path):
    """look.json's accent is compared where the source shows it as flat colour: a mezzanine that kept it passes, one that moved it fails with one plain line."""
    box = (10, 20, 120, 120)
    t = _take(str(tmp_path), "a1", CAPTION_BOX, mez_box=(*box, "0x%02X%02X%02X" % ACCENT_RGB), source_box=(*box, "0x%02X%02X%02X" % ACCENT_RGB))
    _look(t)
    r = _cli(t, "a1")
    g = _card(t)["gates"]["G-ACCENT"]
    assert r.returncode == 0, r.stdout + r.stderr
    assert g["pass"] is True and g["skipped"] is False and g["accent_colour"]["colour"] == ACCENT.upper() and g["accent_colour"]["shift"] <= reel_qc.ACCENT_DRIFT_MAX
    assert g["accent_colour"]["pixels"] > reel_qc.ACCENT_MIN_PX and "G-ACCENT PASS " in r.stdout

    moved = "0x%02X%02X%02X" % _shifted(ACCENT_RGB, (-21, 14, 1))
    t = _take(str(tmp_path), "a2", CAPTION_BOX, mez_box=(*box, moved), source_box=(*box, "0x%02X%02X%02X" % ACCENT_RGB))
    _look(t)
    r = _cli(t, "a2")
    card = _card(t)
    assert r.returncode == 1 and card["verdict"] == "FAIL" and card["gates"]["G-ACCENT"]["pass"] is False
    assert [k for k, v in card["gates"].items() if v["pass"] is False] == ["G-ACCENT"]          # nothing else in this take is wrong
    assert card["gates"]["G-ACCENT"]["accent_colour"]["shift"] > 15
    assert any(line.startswith("G-ACCENT FAIL") for line in r.stdout.splitlines())
    assert any(line.strip().startswith(f"G-ACCENT: your accent colour {ACCENT.upper()} came back different from the source") for line in r.stdout.splitlines()), r.stdout


def test_cli_g_accent_skips_without_a_look_accent_a_source_or_with_an_hdr_source(tmp_path):
    """The same moved take that fails above is a visible SKIP when the look has no accent, when there is no source to compare, and when the source was HDR
    (the conversion moves colours on purpose)."""
    box = (10, 20, 120, 120)
    t = _take(str(tmp_path), "a3", CAPTION_BOX, mez_box=(*box, "0x%02X%02X%02X" % _shifted(ACCENT_RGB, (-21, 14, 1))),
              source_box=(*box, "0x%02X%02X%02X" % ACCENT_RGB))
    _look(t)
    assert _cli(t, "a3").returncode == 1 and _card(t)["gates"]["G-ACCENT"]["pass"] is False                 # the baseline: it fails
    _look(t, accent=None)
    g = (_cli(t, "a3"), _card(t)["gates"]["G-ACCENT"])[1]
    assert g["skipped"] is True and g["pass"] is True                                                         # a look with no accent has nothing to compare
    _look(t)
    os.rename(os.path.join(t, "source.mp4"), os.path.join(t, "source-gone.mp4"))
    r = _cli(t, "a3")
    assert r.returncode == 0 and _card(t)["gates"]["G-ACCENT"]["skipped"] is True                            # no source beside the take: nothing to compare
    os.rename(os.path.join(t, "source-gone.mp4"), os.path.join(t, "source.mp4"))
    json.dump({"hdr": "hlg"}, open(os.path.join(t, "intake.json"), "w"))
    r = _cli(t, "a3")
    g = _card(t)["gates"]["G-ACCENT"]
    assert r.returncode == 0 and g["skipped"] is True and g["pass"] is True and "HDR" in g["numbers"]


# --- G10 pace, run on a real take: off, report and enforce -----------------------------------------------------

def _long_take(tmp_path, name):
    """A 10 s take: the spoken span is 9.78 s, long enough for the pace gate to count, and nothing on screen ever changes, so the gate has something to fail."""
    return _take(str(tmp_path), name, CAPTION_BOX, seconds=10)


def test_run_with_the_pace_gate_on_report_measures_it_and_never_fails(tmp_path):
    t = _long_take(tmp_path, "p1")
    card = reel_qc.run(t, "p1", pace_gate="report")
    g = card["gates"]["G10"]
    assert g["pass"] is True and g["report_only"] is True and g["skipped"] is False
    assert {d["why"] for d in g["detail"]} == {"median", "p90", "max"} and "0 change(s)" in g["numbers"]     # it measured, and this take is not paced
    assert card["verdict"] == "PASS" and not [k for k, v in card["gates"].items() if v["pass"] is False]
    assert _card(t)["gates"]["G10"] == g                                                                      # and the card on disk says the same


def test_run_with_the_pace_gate_on_enforce_fails_a_take_that_is_not_paced(tmp_path):
    t = _long_take(tmp_path, "p2")
    card = reel_qc.run(t, "p2", pace_gate="enforce")
    g = card["gates"]["G10"]
    assert g["pass"] is False and g["skipped"] is False and "report_only" not in g
    assert {d["why"] for d in g["detail"]} == {"median", "p90", "max"}
    assert card["verdict"] == "FAIL" and [k for k, v in card["gates"].items() if v["pass"] is False] == ["G10"]


def test_run_with_the_pace_gate_off_skips_g10_and_the_take_passes(tmp_path):
    t = _long_take(tmp_path, "p3")
    card = reel_qc.run(t, "p3", pace_gate="off")
    g = card["gates"]["G10"]
    assert g["skipped"] is True and g["pass"] is True and g["detail"] == [] and "pace gate off" in g["numbers"]
    assert card["verdict"] == "PASS"


def test_run_reads_the_pace_gate_from_look_json_and_the_argument_wins(tmp_path):
    """The pace mode is look.json's pace_gate unless the argument is given. The two fixture looks carry the two modes the DNA can ask for: varsans is
    report, upstatic is enforce."""
    assert cap_helpers.profile("varsans")["pace_gate"] == "report" and cap_helpers.profile("upstatic")["pace_gate"] == "enforce"
    t = _long_take(tmp_path, "p4")
    _look(t, "varsans")
    g = reel_qc.run(t, "p4")["gates"]["G10"]
    assert g["pass"] is True and g["report_only"] is True and g["detail"]                                      # report: measured, never fails
    _look(t, "upstatic")
    card = reel_qc.run(t, "p4")
    assert card["gates"]["G10"]["pass"] is False and card["verdict"] == "FAIL"                                # enforce: the same take fails
    _look(t, "upstatic", pace_gate="off")
    g = reel_qc.run(t, "p4")["gates"]["G10"]
    assert g["skipped"] is True and g["pass"] is True                                                         # a look.json carrying "pace_gate": "off" skips G10
    _look(t, "upstatic")                                                                                      # the argument beats the look, both ways
    g = reel_qc.run(t, "p4", pace_gate="report")["gates"]["G10"]
    assert g["pass"] is True and g["report_only"] is True and g["skipped"] is False
    _look(t, "varsans", pace_gate="off")
    assert reel_qc.run(t, "p4", pace_gate="enforce")["gates"]["G10"]["pass"] is False


def test_a_short_take_is_report_only_under_enforce_and_skipped_under_off(tmp_path):
    t = _take(str(tmp_path), "p5", CAPTION_BOX, seconds=6)                                                    # a 5.78 s spoken span: under the 8 s the gate needs to mean anything
    card = reel_qc.run(t, "p5", pace_gate="enforce")
    g = card["gates"]["G10"]
    assert g["pass"] is True and g["report_only"] is True and {d["why"] for d in g["detail"]} == {"median", "p90", "max"}      # measured, listed, not failed
    assert g["numbers"].endswith("[report-only, spoken span under 8s]") and card["verdict"] == "PASS"
    assert reel_qc.run(t, "p5", pace_gate="off")["gates"]["G10"]["skipped"] is True


def test_cli_pace_gate_flag_sets_the_mode_and_shows_in_the_lines_and_the_exit_code(tmp_path):
    t = _long_take(tmp_path, "p6")
    r = _cli(t, "p6", "--pace-gate", "enforce")
    assert r.returncode == 1, r.stdout + r.stderr
    assert any(line.startswith("G10 FAIL ") for line in r.stdout.splitlines()) and any(line.startswith("FAIL (G10).") for line in r.stdout.splitlines())
    assert any(line.strip().startswith("G10: ") and "nothing changing on screen" in line for line in r.stdout.splitlines()), r.stdout
    r = _cli(t, "p6", "--pace-gate", "report")
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(line.startswith("G10 AUDIT ") and "report-only" in line for line in r.stdout.splitlines()) and any(line.startswith("PASS.") for line in r.stdout.splitlines())
    r = _cli(t, "p6", "--pace-gate", "off")
    assert r.returncode == 0 and any(line.startswith("G10 SKIP ") for line in r.stdout.splitlines())


def test_cli_pace_gate_flag_may_come_before_the_take_and_beats_look_json(tmp_path):
    t = _take(str(tmp_path), "p7", CAPTION_BOX)
    _look(t, "upstatic")                                                                                      # enforce in the look
    r = subprocess.run([sys.executable, "-B", REEL_QC, "--pace-gate", "off", t, "p7"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert any(line.startswith("G10 SKIP ") for line in r.stdout.splitlines())
    assert _card(t)["gates"]["G10"]["skipped"] is True
    r = _cli(t, "p7")                                                                                         # and without the flag the look's own mode applies
    assert any(line.startswith("G10 AUDIT ") for line in r.stdout.splitlines())                              # (2 s: enforce is report-only on so short a take)


def test_an_unknown_pace_gate_stops_the_run_with_one_plain_line(tmp_path):
    t = os.path.join(str(tmp_path), "bad"); os.makedirs(t)
    open(os.path.join(t, "bad-reel.mp4"), "w").close()
    json.dump({"words": []}, open(os.path.join(t, "words.json"), "w"))
    with pytest.raises(EngineStop, match=r"the pace gate is 'sometimes', which is not one of off, report, enforce"):
        reel_qc.run(t, "bad", pace_gate="sometimes")
    json.dump({"pace_gate": "sometimes"}, open(os.path.join(t, "look.json"), "w"))
    with pytest.raises(EngineStop, match="pace gate is 'sometimes'"):
        reel_qc.run(t, "bad")
    assert not os.path.exists(os.path.join(t, "reel-qc.json"))
    for mode in reel_qc.PACE_MODES:                                                                           # every mode it names is accepted
        json.dump({"pace_gate": mode}, open(os.path.join(t, "look.json"), "w"))
        with pytest.raises(Exception) as e:
            reel_qc.run(t, "bad")                                                                             # (it goes on to read the empty reel file, which is not this test's business)
        assert "pace gate" not in str(e.value)


def test_cli_stops_with_one_line_for_an_unknown_pace_gate_or_a_flag_with_no_value(tmp_path):
    t = os.path.join(str(tmp_path), "bad"); os.makedirs(t)
    open(os.path.join(t, "bad-reel.mp4"), "w").close()
    json.dump({"words": []}, open(os.path.join(t, "words.json"), "w"))
    r = _cli(t, "bad", "--pace-gate", "sometimes")
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.strip() == "STOP: the pace gate is 'sometimes', which is not one of off, report, enforce"
    r = subprocess.run([sys.executable, "-B", REEL_QC, t, "bad", "--pace-gate"], capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == "" and r.stderr.startswith("STOP: usage: reel_qc.py") and "Traceback" not in r.stderr


# --- G-FACE audit-only (subject: none), G-WORDS (do_not_say) -------------------------------------------

def test_g_face_audit_only_never_fails_but_still_reports_the_numbers():
    track = track_for(22)
    track["samples"] = {}
    g = reel_qc.g_face(SRC_WORDS, track, audit_only=True)
    assert g["pass"] is True and g["report_only"] is True and "audit only" in g["numbers"]
    assert reel_qc.line("G-FACE", g).startswith("G-FACE AUDIT")
    assert reel_qc.g_face(SRC_WORDS, track)["pass"] is False                    # the same take, not audit-only


BANNED = cap_helpers.profile("varsans")["do_not_say"][0]      # the fixture look's do-not-say word: "cheap"


def test_g_words_fails_on_a_do_not_say_word_in_a_caption_and_allow_waives_it():
    plan = {"events": [{"mode": "front", "start": 2.0, "words": [{"text": "100%", "at": 2.0}, {"text": BANNED.capitalize(), "at": 2.4}]}]}
    g = reel_qc.g_words(plan, [BANNED])
    assert g["pass"] is False and g["detail"] == [{"why": "banned", "word": BANNED, "t": 2.4}]
    said = reel_qc.plain("G-WORDS", g["detail"][0])
    assert "do-not-say" in said and "allow:" in said and "on your do-not-say list" in said
    assert reel_qc.g_words(plan, [BANNED], allow=[BANNED.capitalize()])["pass"] is True
    assert reel_qc.g_words(plan, [])["skipped"] is True and reel_qc.g_words(None, ["x"])["skipped"] is True
    assert "no do-not-say list" in reel_qc.g_words(plan, [])["numbers"]
    assert "G-WORDS" in reel_qc.MACHINE_GATES and "G-WORDS" in reel_qc.GATE_ORDER


def test_cli_fails_g_words_for_a_banned_caption_and_passes_it_when_the_gate_allows(tmp_path):
    t = _take(str(tmp_path), "t3", CAPTION_BOX, word=BANNED)
    _look(t)                                       # the look's own do-not-say list rides in look.json
    r = _cli(t, "t3")
    card = _card(t)
    assert r.returncode == 1 and card["gates"]["G-WORDS"]["pass"] is False
    assert "G-WORDS:" in r.stdout and "do-not-say" in r.stdout
    json.dump({"approved": True, "overrides": {"allow": [BANNED]}}, open(os.path.join(t, "gate.json"), "w"))
    assert _cli(t, "t3").returncode == 0
    assert _card(t)["gates"]["G-WORDS"]["pass"] is True


def test_cli_subject_none_makes_g_face_audit_only(tmp_path):
    t = _take(str(tmp_path), "t4", CAPTION_BOX)
    json.dump({"W": 1080, "H": 1920, "seconds": 2, "samples": {}, "median": {"cx": 540, "cy": 864, "w": 432, "h": 432, "fallback": True}},
              open(os.path.join(t, "facetrack.json"), "w"))
    assert _cli(t, "t4").returncode == 1                     # no face anywhere in a spoken span: G-FACE fails
    json.dump({"approved": True, "overrides": {"subject": "none"}}, open(os.path.join(t, "gate.json"), "w"))
    r = _cli(t, "t4")
    card = _card(t)
    assert card["gates"]["G-FACE"]["report_only"] is True and card["gates"]["G-FACE"]["pass"] is True
    assert "G-FACE AUDIT" in r.stdout and r.returncode == 0, r.stdout


# --- contact sheet: frames where a punch or whip moves the picture are not evidence ----------------------

def test_sheet_times_prefers_calm_frames():
    frames = [{"n": n, "t": n / 30, "ink": 100 if n == 5 else 10} for n in range(0, 60)]
    assert reel_qc.sheet_times(frames, k=1) == [5 / 30]                       # the inkiest frame, no punch
    busy = reel_qc.busy_frames({"punches": [{"at": 4, "frames": 3, "hold": 10}], "whips": []})
    assert 5 in busy and 40 not in busy
    pick = reel_qc.sheet_times(frames, k=1, busy=busy)[0]
    assert round(pick * 30) not in busy                                      # skipped the busy frame for a calm one


def test_busy_frames_covers_punch_ramps_and_whips():
    busy = reel_qc.busy_frames({"punches": [{"at": 100, "frames": 3, "hold": 24}], "whips": [{"at": 10, "frames": 3}]})
    assert {99, 100, 126, 132, 7, 10, 13} <= busy and 60 not in busy
    assert reel_qc.busy_frames(None) == set()


def test_a_punch_with_no_scale_uses_the_cadence_scale():
    import cadence
    pu = reel_qc._punch_at([{"at": 0, "frames": 3, "hold": 24}], 10 / 30)
    assert abs(pu["scale"] - cadence.PUNCH_SCALE) < 1e-9 and cadence.PUNCH_SCALE == 1.12


# --- G9 compares pixels, not just labels --------------------------------------------------------------

def _encode(src_filter, out, retag=False):
    """A 1 s colour-rich clip. src_filter shapes the pixels; retag stamps tv/bt709 labels over whatever the stream holds
    (what an old render step used to do)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=270x480:rate=30:duration=1",
           "-vf", src_filter, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12"]
    if retag:
        cmd += ["-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0"]
    subprocess.run(cmd + [out], check=True)
    return out


BT709 = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,format=yuv420p"


def test_g9_passes_a_reel_whose_pixels_and_labels_are_both_bt709(tmp_path):
    mez = _encode(BT709, str(tmp_path / "mez.mp4"))
    reel = _encode(BT709, str(tmp_path / "reel.mp4"))
    g = reel_qc.g9_color(reel, mez, [0.2, 0.5, 0.8])
    assert g["pass"] is True and "pixels vs mezzanine" in g["numbers"]


def test_g9_fails_full_range_bt601_pixels_relabelled_as_limited_bt709(tmp_path):
    """The bug an old render step used to hide: the renderer's default output is full-range BT.601, and a bitstream retag made every
    tag read bt709/tv. The labels pass colortag_check; only the pixel comparison sees the shifted colours."""
    mez = _encode(BT709, str(tmp_path / "mez.mp4"))
    reel = _encode("scale=out_range=pc:out_color_matrix=bt601,format=yuvj420p", str(tmp_path / "reel.mp4"), retag=True)
    import colortag_check
    assert colortag_check.check(reel)["pass"] is True          # the labels are perfect
    g = reel_qc.g9_color(reel, mez, [0.2, 0.5, 0.8])
    assert g["pass"] is False
    d = [x for x in g["detail"] if x["why"] == "pixels"][0]
    assert max(d["drift"]) > reel_qc.COLOR_PIXEL_TOL and "colour conversion is wrong" in d["plain"]


def test_pixel_color_drift_is_none_without_a_comparable_frame(tmp_path):
    reel = _encode(BT709, str(tmp_path / "reel.mp4"))
    assert reel_qc.pixel_color_drift(reel, str(tmp_path / "missing.mp4"), [0.5]) is None
    assert reel_qc.g9_color(reel)["pass"] is True              # tags only, no mezzanine


def test_calm_times_avoid_punches_and_whips():
    plan = {"punches": [{"at": 60, "frames": 3, "hold": 24}], "whips": []}
    ts = reel_qc.calm_times(6.0, plan)
    assert len(ts) == 3 and all(not (59 <= round(t * 30) < 95) for t in ts)
    assert reel_qc.calm_times(1.0, None) != []


# --- layout events (page/cta/frame/igpost) replace the picture on purpose --------------------------------

PAGE_PLAN = {"events": [{"mode": "page", "text": "dry fit", "start": 7.52, "end": 9.0}], "punches": [], "whips": []}


def test_busy_frames_cover_a_layout_event_so_the_colour_check_never_samples_inside_it():
    busy = reel_qc.busy_frames(PAGE_PLAN)
    assert 226 in busy and 270 in busy and 200 not in busy
    for t in reel_qc.calm_times(14.0, PAGE_PLAN):
        assert not (7.52 <= t <= 9.0), t


REAL_PUNCHES = [{"at": 226, "hold": 24, "scale": 1.12}, {"at": 420, "hold": 24, "scale": 1.12}, {"at": 473, "hold": 24, "scale": 1.12}]


def test_picture_busy_spans_are_the_ramps_whips_and_layout_events_never_the_held_punch():
    plan = {"events": [{"mode": "page", "start": 7.52, "end": 9.0}, {"mode": "front", "start": 1.0, "end": 2.0}],
            "punches": [{"at": 420, "hold": 24}], "whips": [{"at": 100, "frames": 3}]}
    spans = reel_qc.picture_busy_spans(plan)
    assert (7.52, 9.0) in spans                                           # a layout event
    assert any(a <= 419 / 30 and b >= 424 / 30 for a, b in spans)          # the snap-in
    assert any(a <= 447 / 30 and b >= 452 / 30 for a, b in spans)          # the ease back
    assert any(a <= 97 / 30 and b >= 103 / 30 for a, b in spans)           # the whip
    inside = lambda t: any(a <= t <= b for a, b in spans)
    assert not inside(435 / 30) and not inside(1.5)                        # the held punch frames, and a plain caption, are comparable
    assert reel_qc.picture_busy_spans(None) == []


def test_a_caption_chip_is_busy_picture_so_the_match_never_samples_under_it():
    """A correct reel failed G2 at 0.8959 because a sample fell under the red NEVER chip, a solid box the mezzanine does not have."""
    neg = {"mode": "front", "start": 7.0, "end": 8.2, "words": [{"text": "never", "neg": True}, {"text": "quit"}]}
    plain = {"mode": "front", "start": 1.0, "end": 2.0, "words": [{"text": "keep"}]}
    plan = {"events": [neg, plain]}
    assert reel_qc.chip_spans(plan) == [(7.0, 8.2)] and (7.0, 8.2) in reel_qc.picture_busy_spans(plan)
    accent = {"mode": "front", "start": 3.0, "end": 4.0, "words": [{"text": "send", "accent": True}]}
    assert reel_qc.chip_spans({"events": [accent]}) == [] and reel_qc.chip_spans({"events": [accent], "chip": True}) == [(3.0, 4.0)]
    times = reel_qc.clear_of_spans([7.1, 7.6], reel_qc.picture_busy_spans(plan), 20.0)
    assert not any(7.0 - 0.1 <= t <= 8.2 + 0.1 for t in times) and times                  # moved to just before or after the chip, not dropped


def test_a_soft_bar_is_busy_picture_so_the_match_never_samples_under_it():
    """the soft build's bar is an opaque block of the accent behind its payoff, a box the mezzanine does not have, so a thought with a bar row is
    busy picture like a chip; a soft thought with no bar, and a cluster, are not"""
    bar = {"mode": "front", "soft": True, "start": 4.2, "end": 6.4, "exitAt": 6.24, "y": 940,
           "words": [{"text": "than just having someone", "role": "lead", "bar": False}, {"text": "book from the account.", "role": "pay", "bar": True}]}
    plain = {"mode": "front", "soft": True, "start": 1.0, "end": 4.36, "exitAt": 4.2, "y": 940,
             "words": [{"text": "So running ads,", "role": "lead", "bar": False}, {"text": "a lot more", "role": "pay", "bar": False}]}
    cluster = {"mode": "front", "start": 7.0, "end": 8.0, "words": [{"text": "keep"}]}
    plan = {"events": [plain, bar, cluster]}
    assert reel_qc.bar_spans(plan) == [(4.2, 6.4)] and (4.2, 6.4) in reel_qc.picture_busy_spans(plan)
    assert (1.0, 4.36) not in reel_qc.picture_busy_spans(plan) and reel_qc.bar_spans(None) == []
    times = reel_qc.clear_of_spans([5.0], reel_qc.picture_busy_spans(plan), 20.0)
    assert times and not any(4.1 <= t <= 6.5 for t in times)


def test_the_picture_check_never_samples_mid_ease():
    """A good reel failed G2 with 0.896 against 0.9 because the middle sample (15.0 s) fell in the ease back of the punch
    at frame 420: the linear stand-in for the render's eased curve is a different zoom there. Held frames of the same punch read 0.99."""
    plan = {"events": [], "punches": REAL_PUNCHES, "whips": []}
    assert 15.0 in reel_qc.sample_times(30.0)
    times = reel_qc.clear_of_spans(reel_qc.sample_times(30.0), reel_qc.picture_busy_spans(plan), 30.0)
    assert 15.0 not in times and len(times) == 3
    busy = reel_qc.picture_busy_spans(plan)
    assert not any(a - 0.1 <= t <= b + 0.1 for t in times for a, b in busy)


def test_clear_of_spans_moves_a_sample_out_of_a_page_and_drops_one_that_cannot_move():
    spans = reel_qc.layout_spans({"events": [{"mode": "page", "start": 6.3, "end": 7.2}]})
    moved = reel_qc.clear_of_spans([0.5, 7.0, 13.5], spans, 14.0)
    assert moved[0] == 0.5 and moved[-1] == 13.5 and len(moved) == 3
    assert not any(6.2 <= t <= 7.3 for t in moved)
    assert reel_qc.clear_of_spans([1.0], [(0.0, 2.0)], 2.0) == []
    assert reel_qc.clear_of_spans([1.0, 5.0], [], 6.0) == [1.0, 5.0]
