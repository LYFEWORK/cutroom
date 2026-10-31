"""qc.py: the quality gates on a finished video, as code.

Each gate is a plain function that takes measurements and returns {"pass", "numbers", "detail", "skipped"}; every test above the line "the gates on a
take" pins one gate's decision on numbers written into the test, so nothing here needs a video and the whole top half runs instantly. The heavy
paths (decode, freezedetect, loudnorm, cross-correlation, the Apple text reader) are exercised end to end on one-second synthetic takes in the
bottom half, through qc.run and through the command line.

The gates: G1 speech-only pieces (and a face, unless the take is faceless), G2 picture == sound, G3 no clipped onsets, G4 captions in the box,
G5 caption events, G6 the human read, G7 loudness, COLOR the tags and pixels (test_color.py goes deeper), WORDS the finished file still says what
the cut says. A gate with nothing to measure says SKIP: it is never a silent pass."""
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

import qc
from helpers_qc import FACE, draw_box, make_take, still_video, text_frame

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")
QC_PY = os.path.join(ENGINE, "qc.py")
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
needs_swiftc = pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="the Apple helpers need macOS and swiftc")

# A take that is fine: two pieces, tight lead-ins, a face on every sampled second.
SRC_WORDS = [{"text": "why", "start": 10.10, "end": 10.30}, {"text": "your", "start": 10.35, "end": 10.55},
             {"text": "calendar", "start": 10.60, "end": 11.10}, {"text": "stays", "start": 20.05, "end": 20.35},
             {"text": "empty", "start": 20.40, "end": 20.90}]
PIECES = [{"seg": 0, "c0": 10.03, "c1": 11.31, "out_in": 0.0, "out_out": 1.28},
          {"seg": 1, "c0": 19.98, "c1": 21.11, "out_in": 1.28, "out_out": 2.41}]


def track_for(seconds, missing=()):
    return {"W": 1080, "H": 1920, "seconds": seconds,
            "samples": {str(s): dict(FACE) for s in range(seconds) if s not in missing},
            "median": dict(FACE)}


# --- G1 speech-only pieces -----------------------------------------------------------------------

def test_g1_passes_a_tight_speech_only_take():
    g = qc.g1_speech_only(PIECES, SRC_WORDS, track_for(3))
    assert g["pass"] is True
    assert "lead-in max 0.07" in g["numbers"] and "tail max 0.21" in g["numbers"]


def test_g1_fails_a_long_lead_in():
    """a piece whose c0 sits well before its first word plays dead footage (a stray clip of the speaker checking the camera); the geometry gates only
    ever checked NEGATIVE heads"""
    pieces = [dict(PIECES[0], c0=9.40), PIECES[1]]          # 0.70 s of lead-in
    g = qc.g1_speech_only(pieces, SRC_WORDS, track_for(3))
    assert g["pass"] is False
    assert "0.7" in g["numbers"]
    assert any(d["piece"] == 0 and d["why"] == "lead_in" for d in g["detail"])


def test_g1_fails_a_long_tail():
    pieces = [dict(PIECES[0], c1=11.90), PIECES[1]]          # 0.80 s past the last word
    g = qc.g1_speech_only(pieces, SRC_WORDS, track_for(3))
    assert g["pass"] is False
    assert any(d["why"] == "tail" for d in g["detail"])


def test_g1_fails_a_piece_with_no_words_at_all():
    """pure dead footage: nothing is spoken inside the piece. cut.geometry deliberately calls this 'not clipped' (there is no word for the cut to
    land in); G1 is where it fails"""
    pieces = PIECES + [{"seg": 2, "c0": 40.0, "c1": 42.0, "out_in": 2.41, "out_out": 4.41}]
    g = qc.g1_speech_only(pieces, SRC_WORDS, track_for(5))
    assert g["pass"] is False
    assert any(d["piece"] == 2 and d["why"] == "no_words" for d in g["detail"])


def test_g1_fails_when_the_face_is_absent_from_a_piece():
    """the face track samples once a second; a second with no face box is the speaker out of frame"""
    long_piece = [{"seg": 0, "c0": 10.0, "c1": 30.0, "out_in": 0.0, "out_out": 20.0}]
    src = [{"text": "why", "start": 10.05, "end": 29.9}]
    g = qc.g1_speech_only(long_piece, src, track_for(21, missing=(3, 4, 5)))
    assert g["pass"] is False
    assert any(d["why"] == "face" for d in g["detail"])
    assert "face 85%" in g["numbers"]


def test_g1_face_check_is_skipped_when_a_piece_has_no_sampled_second():
    """a sub-second piece never lands on a face-track sample; that is not evidence of an empty frame, so it must not fail: it is reported as unmeasured"""
    tiny = [{"seg": 0, "c0": 10.10, "c1": 10.55, "out_in": 0.60, "out_out": 1.05}]
    g = qc.g1_speech_only(tiny, SRC_WORDS, track_for(2))
    assert g["pass"] is True and "face n/a" in g["numbers"] and "too short to sample a face" in g["numbers"]


def test_g1_face_check_is_skipped_without_a_face_track():
    g = qc.g1_speech_only(PIECES, SRC_WORDS, None)
    assert g["pass"] is True and "no face track" in g["numbers"]


EMPTY_TRACK = {"W": 1080, "H": 1920, "seconds": 3, "samples": {}, "median": {"cx": 540, "cy": 864, "w": 432, "h": 432, "fallback": True}, "extracted": [0, 1, 2]}


def test_g1_face_rule_is_switchable_for_a_take_with_nobody_on_camera():
    """a voice-over or a screen take has no face in any second: with the rule on that is a FAIL, with it off the face is simply not asked for"""
    on = qc.g1_speech_only(PIECES, SRC_WORDS, EMPTY_TRACK)
    assert on["pass"] is False and "face 0%" in on["numbers"] and any(d["why"] == "face" for d in on["detail"])
    off = qc.g1_speech_only(PIECES, SRC_WORDS, EMPTY_TRACK, check_face=False)
    assert off["pass"] is True and "face rule off (faceless)" in off["numbers"] and off["detail"] == []
    assert qc.g1_speech_only(PIECES, SRC_WORDS, None, check_face=False)["pass"] is True


def test_g1_with_the_face_rule_off_still_judges_the_speech():
    """only the face is left out: dead footage and empty pieces still fail a faceless take"""
    long_lead = [dict(PIECES[0], c0=9.40), PIECES[1]]
    g = qc.g1_speech_only(long_lead, SRC_WORDS, None, check_face=False)
    assert g["pass"] is False and any(d["why"] == "lead_in" for d in g["detail"])
    empty = PIECES + [{"seg": 2, "c0": 40.0, "c1": 42.0, "out_in": 2.41, "out_out": 4.41}]
    assert qc.g1_speech_only(empty, SRC_WORDS, None, check_face=False)["pass"] is False


def test_g1_says_so_when_the_edl_has_no_pieces():
    g = qc.g1_speech_only([], SRC_WORDS, track_for(3))
    assert g["skipped"] is True and g["pass"] is True


# --- G2 picture == sound -------------------------------------------------------------------------

CLEAN_MATCH = [{"piece": 0, "corr": 0.99}, {"piece": 1, "corr": 0.97}, {"piece": 2, "corr": 0.98}]
CLEAN_LAG = [{"at": 0.5, "lag_s": 0.004}, {"at": 30.0, "lag_s": 0.006}, {"at": 59.0, "lag_s": 0.005}]


def test_g2_passes_a_clean_take():
    g = qc.g2_picture_sound({"ok": True, "reason": "no stalls"}, [], CLEAN_MATCH, CLEAN_LAG)
    assert g["pass"] is True and "freezes 0" in g["numbers"]


def test_g2_accepts_a_source_that_conformed_cleanly():
    skew = {"ok": False, "reason": "15.695 s of video stalls",
            "conform": {"ok": True, "reason": "video == audio", "freezes_over_min": []}}
    g = qc.g2_picture_sound(skew, [], CLEAN_MATCH, CLEAN_LAG)
    assert g["pass"] is True and "conformed" in g["numbers"]


def test_g2_fails_an_unconformed_skew():
    skew = {"ok": False, "reason": "15.695 s of video stalls",
            "conform": {"ok": False, "reason": "the conformed mezzanine still desyncs"}}
    g = qc.g2_picture_sound(skew, [], CLEAN_MATCH, CLEAN_LAG)
    assert g["pass"] is False and any(d["why"] == "skew" for d in g["detail"])


def test_g2_fails_a_frozen_mezzanine():
    """15 s of frozen picture in the mezzanine is what makes a closing line play with the speaker's mouth shut"""
    g = qc.g2_picture_sound({"ok": True}, [{"start": 133.3, "duration": 11.3}], CLEAN_MATCH, CLEAN_LAG)
    assert g["pass"] is False and "freezes 1" in g["numbers"]


def test_g2_ignores_a_held_frame_under_half_a_second():
    g = qc.g2_picture_sound({"ok": True}, [{"start": 3.0, "duration": 0.4}], CLEAN_MATCH, CLEAN_LAG)
    assert g["pass"] is True and "freezes 0" in g["numbers"]


def test_g2_fails_a_stale_picture():
    """the frame-match half: the cut's own frame no longer matches the mezzanine it was cut from"""
    stale = [{"piece": 0, "corr": 0.99}, {"piece": 11, "corr": 0.41}, {"piece": 17, "corr": 0.38}]
    g = qc.g2_picture_sound({"ok": True}, [], stale, CLEAN_LAG)
    assert g["pass"] is False and any(d["why"] == "frame_match" for d in g["detail"])


def test_g2_fails_a_lag_over_sixty_milliseconds():
    late = [{"at": 0.5, "lag_s": 0.004}, {"at": 30.0, "lag_s": 0.081}, {"at": 59.0, "lag_s": 0.070}]
    g = qc.g2_picture_sound({"ok": True}, [], CLEAN_MATCH, late)
    assert g["pass"] is False and any(d["why"] == "lag" for d in g["detail"])


def test_g2_fails_a_growing_lag_even_inside_the_budget():
    """drift that accumulates is a different failure from a constant offset. Each sample is under 60 ms here; the video still drifts 55 ms across
    its length"""
    drift = [{"at": 0.5, "lag_s": 0.002}, {"at": 30.0, "lag_s": 0.030}, {"at": 59.0, "lag_s": 0.057}]
    g = qc.g2_picture_sound({"ok": True}, [], CLEAN_MATCH, drift)
    assert g["pass"] is False and any(d["why"] == "lag_growth" for d in g["detail"])


def test_g2_reports_what_it_could_not_measure():
    g = qc.g2_picture_sound(None, None, [], [])
    assert g["pass"] is True and g["skipped"] is True and "no mezzanine" in g["numbers"]


# --- G3 no clipped onsets ------------------------------------------------------------------------

def test_g3_passes_clean_geometry():
    g = qc.g3_onsets(PIECES, SRC_WORDS, "whisperx (words.wx.json)")
    assert g["pass"] is True and "whisperx" in g["numbers"]


def test_g3_fails_a_head_clip_past_the_stamp_tolerance():
    """piece c0 14.567 against 'because' starting at 14.480 gives head -0.087, past the 0.08 s tolerance"""
    src = [{"text": "because", "start": 14.480, "end": 15.065}]
    pieces = [{"seg": 0, "c0": 14.567, "c1": 15.40, "out_in": 0.0, "out_out": 0.833}]
    g = qc.g3_onsets(pieces, src, "whisperx")
    assert g["pass"] is False and any(d["why"] == "head" for d in g["detail"])


def test_g3_credits_the_stamp_tolerance():
    """a 0.05 s negative head is aligner onset jitter, not a cut inside a word: takes were stopped on exactly this, and every one was disproven by
    measuring the waveform"""
    src = [{"text": "because", "start": 14.517, "end": 15.065}]
    pieces = [{"seg": 0, "c0": 14.567, "c1": 15.40, "out_in": 0.0, "out_out": 0.833}]
    assert qc.g3_onsets(pieces, src, "whisperx")["pass"] is True


def test_g3_fails_a_tail_clip():
    src = [{"text": "empty", "start": 20.40, "end": 21.00}]
    pieces = [{"seg": 0, "c0": 20.30, "c1": 20.85, "out_in": 0.0, "out_out": 0.55}]
    g = qc.g3_onsets(pieces, src, "whisperx")
    assert g["pass"] is False and any(d["why"] == "tail" for d in g["detail"])


def test_g3_says_so_when_the_edl_has_no_pieces():
    assert qc.g3_onsets([], SRC_WORDS, "whisperx")["skipped"] is True


# --- G4 captions in the box and off the head -----------------------------------------------------

def blank(h=426, w=240):
    return np.zeros((h, w), dtype=np.int16)


def test_crown_y_is_a_fraction_of_the_face_height_above_the_face_box_center():
    """the crown sits 0.30 of the face height above the top of Vision's face box, which starts at the BROW"""
    assert qc.crown_y(FACE) == 900 - 400 * (0.5 + qc.CROWN_H) == 580
    assert qc.crown_y(None) is None


def test_sweep_size_is_pinned_so_the_two_files_diff_frame_for_frame():
    assert qc.sweep_size({"w": 1080, "h": 1920}) == (240, 426)
    assert qc.sweep_size({"w": 270, "h": 480}) == (240, 426)


def test_ink_counts_finds_ink_outside_the_safe_box():
    """the safe box is x 65-940 / y 270-1420 on the 1080x1920 canvas; the sweep works at 240 px wide"""
    d = blank(); d[10:14, 4:8] = 255                       # canvas y ~45, x ~18: above the box
    c = qc.ink_counts(d, FACE, qc.crown_y(FACE))
    assert c["ink"] == 16 and c["outside"] == 16


def test_ink_counts_accepts_several_safe_boxes_and_ink_is_inside_if_it_sits_in_any():
    d = blank(); d[10:14, 4:8] = 255                       # outside the default box...
    top_zone = {"x0": 0, "x1": 1080, "y0": 0, "y1": 200}   # ...but inside a plan-declared zone at the top
    assert qc.ink_counts(d, FACE, qc.crown_y(FACE), safe=[qc.SAFE, top_zone])["outside"] == 0


def test_ink_counts_ignores_a_few_stray_pixels_through_the_gate():
    d = blank(); d[10, 4:7] = 255                          # 3 px outside the box
    frames = [{"n": 0, "t": 0.0, **qc.ink_counts(d, FACE, qc.crown_y(FACE))}]
    assert qc.g4_captions(frames, [], 30)["pass"] is True


def test_g4_fails_one_frame_of_ink_outside_the_safe_box():
    d = blank(); d[10, 0:9] = 255                          # 9 px outside
    frames = [{"n": 0, "t": 0.0, **qc.ink_counts(d, FACE, qc.crown_y(FACE))}]
    g = qc.g4_captions(frames, [], 30)
    assert g["pass"] is False and "outside 1" in g["numbers"]


def test_g4_fails_ink_across_the_face():
    """the gate that must never trip: a caption drawn over the speaker's mouth"""
    d = blank(); d[195:205, 110:130] = 255                 # canvas ~ (540, 900): dead center of the face
    c = qc.ink_counts(d, FACE, qc.crown_y(FACE))
    assert c["on_face"] > 0
    g = qc.g4_captions([{"n": 0, "t": 0.0, **c}], [], 30)
    assert g["pass"] is False and any(x["why"] == "face" for x in g["detail"])


def test_g4_allows_ink_above_the_crown_only_while_a_hook_or_lockup_is_live():
    """the crown line is the face top minus 0.30 of its height (the face box starts at the BROW, so scalp above it read as free room). Only the text
    hook, the behind layer and the front lockup live up there."""
    d = blank(); d[75:85, 40:120] = 255                    # canvas y ~ 338-383, above the crown (crown y ~ 580)
    c = qc.ink_counts(d, FACE, qc.crown_y(FACE))
    assert c["above_crown"] > 0 and c["outside"] == 0
    frames = [{"n": 0, "t": 1.0, **c}]
    assert qc.g4_captions(frames, [], 30)["pass"] is False
    hook = [{"mode": "texthook", "text": "Why your calendar stays empty.", "start": 0.0, "end": 2.2}]
    assert qc.g4_captions(frames, hook, 30)["pass"] is True
    lockup = [{"mode": "front", "words": [{"text": "DROP", "at": 0.9}], "start": 0.9, "end": 2.0}]
    assert qc.g4_captions(frames, lockup, 30)["pass"] is True
    slam = [{"mode": "keyword", "text": "ALRIGHT", "start": 0.9, "end": 2.0}]
    assert qc.g4_captions(frames, slam, 30)["pass"] is False


def test_g4_allows_a_running_word_above_the_crown_only_when_its_planned_position_is_there():
    frames = [{"n": 30, "t": 1.0, "ink": 90, "outside": 0, "on_face": 0, "above_crown": 90, "crown": 580}]
    up = [{"mode": "word", "text": "stays", "start": 0.9, "end": 1.4, "y": 300}]
    drifted = [{"mode": "word", "text": "stays", "start": 0.9, "end": 1.4, "y": 700}]
    assert qc.g4_captions(frames, up, 30)["pass"] is True
    assert qc.g4_captions(frames, drifted, 30)["pass"] is False
    assert qc.g4_captions(frames, [{"mode": "word", "text": "stays", "start": 5.0, "end": 5.4, "y": 300}], 30)["pass"] is False    # not live at that moment


def test_g4_skips_the_frames_of_a_whip_transition_and_says_how_many():
    """a whip blurs and pushes the WHOLE picture on purpose: the diff reads it as ink everywhere, and those frames are not caption ink"""
    frames = [{"n": n, "t": n / 30, "ink": 5000, "outside": 5000, "on_face": 800, "above_crown": 900, "crown": 580} for n in range(20, 32)]
    assert qc.g4_captions(frames, [], 30)["pass"] is False
    g = qc.g4_captions(frames, [], 30, whips=[{"at": 26, "frames": 6}])
    assert g["pass"] is True and "12 whip frames skipped" in g["numbers"]


def test_g4_tolerates_a_corner_graze_of_the_face_box_but_not_a_word_across_it():
    frames = [{"n": 1, "t": 0.1, "ink": 50, "outside": 0, "on_face": 7, "above_crown": 0, "crown": 400}]
    assert qc.g4_captions(frames, [])["pass"] is True
    frames[0]["on_face"] = 160
    assert qc.g4_captions(frames, [])["pass"] is False


def test_g4_says_so_when_there_was_nothing_to_sweep():
    g = qc.g4_captions([], [], 30)
    assert g["skipped"] is True and g["pass"] is True


def test_face_zone_without_a_track_is_no_zone_so_a_faceless_sweep_cannot_crash():
    assert qc.face_zone(None, 1.0) is None and qc.face_at(None, 1.0) is None


# --- G5 caption events ---------------------------------------------------------------------------

CUT_WORDS = {"words": [{"text": "why", "start": 0.00, "end": 0.20, "filler": False},
                       {"text": "your", "start": 0.25, "end": 0.45, "filler": False},
                       {"text": "calendar", "start": 0.50, "end": 1.00, "filler": False}]}


def evs(*spans):
    return [{"mode": "word", "text": t, "start": a, "end": b} for t, a, b in spans]


GOOD_EVENTS = evs(("why", 0.00, 0.24), ("your", 0.25, 0.49), ("calendar", 0.50, 1.10))


def test_g5_passes_a_clean_plan():
    g = qc.g5_events(GOOD_EVENTS, CUT_WORDS, [], 30)
    assert g["pass"] is True and "overlaps 0" in g["numbers"]


def test_g5_catches_overlapping_word_events():
    """two running captions on screen at once: one monotonic cursor is supposed to make this impossible, and the gate is what proves it on the
    shipped file"""
    bad = evs(("why", 0.00, 0.40), ("your", 0.25, 0.49), ("calendar", 0.50, 1.10))
    g = qc.g5_events(bad, CUT_WORDS, [], 30)
    assert g["pass"] is False and any(d["why"] == "overlap" for d in g["detail"])


def test_g5_catches_a_sub_three_frame_event():
    """a caption that lasts one or two frames is a flash, not a caption"""
    bad = evs(("why", 0.00, 0.24), ("your", 0.25, 0.30), ("calendar", 0.50, 1.10))
    g = qc.g5_events(bad, CUT_WORDS, [], 30)
    assert g["pass"] is False and any(d["why"] == "short" for d in g["detail"])


def test_g5_catches_events_out_of_spoken_order():
    bad = evs(("your", 0.00, 0.24), ("why", 0.25, 0.49), ("calendar", 0.50, 1.10))
    g = qc.g5_events(bad, CUT_WORDS, [], 30)
    assert g["pass"] is False and any(d["why"] == "order" for d in g["detail"])


def test_g5_wants_every_spoken_word_on_screen_or_named_in_a_flag():
    g = qc.g5_events(GOOD_EVENTS[:2], CUT_WORDS, [], 30)
    assert g["pass"] is False and any(d["why"] == "uncovered" for d in g["detail"])
    ok = qc.g5_events(GOOD_EVENTS[:2], CUT_WORDS, ["word_dropped_no_time:calendar"], 30)
    assert ok["pass"] is True


def test_g5_samples_event_timing_against_the_cut_transcript():
    """three sampled events must land within one frame of the word they caption"""
    late = evs(("why", 0.00, 0.24), ("your", 0.42, 0.60), ("calendar", 0.62, 1.10))
    g = qc.g5_events(late, CUT_WORDS, [], 30)
    assert g["pass"] is False and any(d["why"] == "timing" for d in g["detail"])


def test_g5_skips_the_transcript_checks_without_cut_words():
    g = qc.g5_events(GOOD_EVENTS, None, [], 30)
    assert g["pass"] is True and "no cut.words.json" in g["numbers"]


def test_g5_says_so_without_a_plan():
    g = qc.g5_events(None, CUT_WORDS, [], 30)
    assert g["skipped"] is True and g["pass"] is True and "no captions-plan.json" in g["numbers"]


def test_g5_does_not_match_a_merged_event_to_the_same_word_glued_elsewhere_in_the_take():
    """a planner merged "set" and "up" into one event, 11 s after the take says "setup" as one word; matching the glued text to that other word read
    the event as out of order"""
    words = [{"text": "a", "start": 13.0, "end": 13.1}, {"text": "setup", "start": 14.0, "end": 14.4},
             {"text": "items,", "start": 24.575, "end": 24.815}, {"text": "set", "start": 25.055, "end": 25.3}, {"text": "up", "start": 25.324, "end": 25.404}]
    events = [{"mode": "word", "text": "items,", "start": 24.575, "end": 25.055, "y": 1100, "size": 100, "weight": 400},
              {"mode": "word", "text": "set up", "start": 25.055, "end": 25.404, "y": 1100, "size": 100, "weight": 400}]
    g = qc.g5_events(events, {"words": words}, [], 30)
    assert not [d for d in g["detail"] if d["why"] == "order"], g["detail"]
    assert qc._word_index(words, events[1]) in (3, 4)


def test_g5_counts_a_word_spoken_twice_once_each_by_text_and_start():
    """matched by (text, start), never by text alone: the same word spoken twice must not let the second one's event mask the first one's drop"""
    words = {"words": [{"text": "go", "start": 0.0, "end": 0.2}, {"text": "go", "start": 1.0, "end": 1.2}]}
    only_second = evs(("go", 1.0, 1.24))
    g = qc.g5_events(only_second, words, [], 30)
    assert g["pass"] is False and [d["t"] for d in g["detail"] if d["why"] == "uncovered"] == [0.0]
    assert qc.g5_events(evs(("go", 0.0, 0.24), ("go", 1.0, 1.24)), words, [], 30)["pass"] is True


# --- G6 / G7 / verdict ---------------------------------------------------------------------------

def test_g6_is_recorded_as_a_human_read_not_a_machine_verdict():
    g = qc.g6_visual("/w/evidence/strip.png")
    assert g["pass"] is None and g["human"] is True and "strip.png" in g["numbers"]


def test_g7_loudness_band():
    assert qc.g7_loudness(-16.2, -1.9)["pass"] is True
    assert qc.g7_loudness(-21.4, -6.0)["pass"] is False       # a cut's raw room sound
    assert qc.g7_loudness(-15.9, -0.8)["pass"] is False       # true peak too hot
    assert qc.g7_loudness(None, None)["skipped"] is True


def test_g7_edges_of_the_band_are_inside_it():
    assert qc.g7_loudness(-15.0, -1.5)["pass"] is True and qc.g7_loudness(-17.0, -3.0)["pass"] is True
    assert qc.g7_loudness(-14.9, -3.0)["pass"] is False and qc.g7_loudness(-17.1, -3.0)["pass"] is False
    assert qc.g7_loudness(-16.0, -1.4)["pass"] is False


def test_parse_loudnorm_reads_the_last_json_block_of_ffmpegs_log():
    log = "[Parsed_loudnorm_0 @ 0x1] \n{\n\t\"input_i\" : \"-16.28\",\n\t\"input_tp\" : \"-7.39\",\n\t\"input_lra\" : \"0.00\"\n}\n"
    assert qc.parse_loudnorm(log) == (-16.3, -7.4)
    assert qc.parse_loudnorm("no json here") == (None, None)
    assert qc.parse_loudnorm("{ \"input_i\" : \"not a number\" }") == (None, None)


MACHINE = ("G1", "G2", "G3", "G4", "G5", "G7", "COLOR", "WORDS")


def test_verdict_fails_on_any_machine_gate_and_ignores_the_human_one():
    ok = {k: {"pass": True} for k in MACHINE}
    ok["G6"] = {"pass": None, "human": True}
    assert qc.verdict_of(ok) == "PASS"
    for k in MACHINE:
        bad = dict(ok, **{k: {"pass": False}})
        assert qc.verdict_of(bad) == "FAIL", k
    assert qc.MACHINE_GATES == MACHINE and "G6" not in qc.MACHINE_GATES


def test_verdict_passes_when_a_gate_was_skipped():
    g = {k: {"pass": True} for k in MACHINE}
    g["G4"] = {"pass": True, "skipped": True}
    g["WORDS"] = {"pass": True, "skipped": True}
    g["G6"] = {"pass": None, "human": True}
    assert qc.verdict_of(g) == "PASS"


def test_every_gate_prints_one_line_with_its_numbers():
    g = qc.g1_speech_only(PIECES, SRC_WORDS, track_for(3))
    line = qc.line("G1", g)
    assert line.startswith("G1 PASS ") and "\n" not in line and "lead-in max" in line
    assert qc.line("G6", qc.g6_visual("/w/strip.png")).startswith("G6 HUMAN ")
    assert qc.line("G4", qc.g4_captions([], [], 30)).startswith("G4 SKIP ")
    assert qc.line("WORDS", qc.g_words(None, "x")).startswith("WORDS SKIP ")
    assert qc.line("G1", qc.g1_speech_only(PIECES, SRC_WORDS, EMPTY_TRACK)).startswith("G1 FAIL ")


# --- WORDS: the finished file still says what the cut says ----------------------------------------

LONG_TEXT = "why your calendar stays empty and how a small change fixes it by the end of this week"      # 17 words
SHORT_TEXT = "why your calendar stays empty"                                                              # 5 words


def test_words_passes_identical_transcripts_and_close_durations():
    g = qc.g_words(LONG_TEXT, LONG_TEXT, 5.02, 5.0)
    assert g["pass"] is True and g["skipped"] is False and g["detail"] == []
    assert "words match 1.00" in g["numbers"] and "duration delta 0.02s" in g["numbers"]


def test_words_ignores_case_and_punctuation():
    assert qc.g_words("Why your calendar, stays empty!", SHORT_TEXT)["pass"] is True


def test_a_word_buried_in_a_short_take_fails_and_is_named():
    """the mastered file lost 'empty': 4 of 5 words match, a ratio of 0.89, under the 0.9 bar"""
    g = qc.g_words("why your calendar stays", SHORT_TEXT)
    assert g["pass"] is False and "words match 0.89" in g["numbers"]
    assert {"why": "transcript", "ratio": 0.8889, "min": 0.9} in g["detail"]
    assert {"why": "word", "cut": "empty", "file": ""} in g["detail"]


def test_a_changed_word_fails_a_short_take_and_shows_both_spellings():
    g = qc.g_words("why your calendars stay empty", SHORT_TEXT)
    assert g["pass"] is False
    assert {"why": "word", "cut": "calendar stays", "file": "calendars stay"} in g["detail"]


def test_a_single_difference_in_a_long_take_is_listed_on_the_card_even_when_the_ratio_holds():
    """the bar is a ratio, so one missing word of seventeen is a ratio of 0.97 and the gate passes: the difference rides on the card's detail so a
    person reading qc.json can still see it"""
    g = qc.g_words(LONG_TEXT.replace(" fixes it", ""), LONG_TEXT)
    assert g["pass"] is True and float(g["numbers"].split("words match ")[1].split(" ")[0]) > 0.9
    assert {"why": "word", "cut": "fixes it", "file": ""} in g["detail"]


def test_a_duration_mismatch_fails_even_when_every_word_matches():
    g = qc.g_words(LONG_TEXT, LONG_TEXT, 5.30, 5.0)
    assert g["pass"] is False and {"why": "duration", "delta_s": 0.3, "max": 0.2} in g["detail"]
    assert qc.g_words(LONG_TEXT, LONG_TEXT, 5.19, 5.0)["pass"] is True          # under the 0.2 s bar
    assert qc.g_words(LONG_TEXT, LONG_TEXT, 5.20, 5.0)["pass"] is False         # the bar itself is a fail


def test_without_durations_only_the_words_are_compared():
    g = qc.g_words(LONG_TEXT, LONG_TEXT)
    assert g["pass"] is True and "duration" not in g["numbers"]


@pytest.mark.parametrize("heard,expected", [(None, SHORT_TEXT), (SHORT_TEXT, None), ("", SHORT_TEXT), (SHORT_TEXT, "")])
def test_words_with_either_transcript_missing_is_a_skip_never_a_silent_pass(heard, expected):
    g = qc.g_words(heard, expected, 5.0, 5.0)
    assert g["skipped"] is True and g["pass"] is True and "not compared" in g["numbers"]


# --- the numeric helpers behind G2 ----------------------------------------------------------------

def test_ncc_is_one_for_identical_frames_and_low_for_unrelated_ones():
    rng = np.random.default_rng(7)
    a = rng.integers(0, 255, (60, 40)).astype(np.int16)
    b = rng.integers(0, 255, (60, 40)).astype(np.int16)
    assert qc.ncc(a, a) > 0.999
    assert qc.ncc(a, b) < 0.5
    assert qc.ncc(a, np.full_like(a, 7)) == 0.0        # a flat frame correlates with nothing


def test_lag_of_recovers_a_known_offset():
    sr = 16000
    rng = np.random.default_rng(3)
    base = rng.standard_normal(sr * 2).astype(np.float32)
    shifted = np.concatenate([np.zeros(480, np.float32), base])[:sr * 2]   # b is 30 ms late
    assert abs(qc.lag_of(base, shifted, sr) - 0.030) < 0.002
    assert abs(qc.lag_of(base, base, sr)) < 0.001


def test_lag_and_strength_says_how_well_the_two_sounds_match():
    sr = 16000
    rng = np.random.default_rng(5)
    base = rng.standard_normal(sr * 2).astype(np.float32)
    lag, strength = qc.lag_and_strength(base, np.concatenate([np.zeros(480, np.float32), base])[:sr * 2], sr)
    assert abs(lag - 0.030) < 0.002 and strength > 0.9                                   # the same sound, 30 ms late
    other = rng.standard_normal(sr * 2).astype(np.float32)
    assert qc.lag_and_strength(base, other, sr)[1] < 0.3                                 # a different sound
    assert qc.lag_and_strength(base, np.zeros(sr * 2, np.float32), sr) == (0.0, 0.0)     # silence has no lag, not a lag of zero
    assert qc.lag_and_strength(base[:10], base[:10], sr) == (0.0, 0.0)                   # too short to say


def test_sample_pieces_prefers_pieces_long_enough_to_hold_the_window():
    """a 2 s cross-correlation window opened in a 0.6 s piece runs over the next splice, and the correlation then aligns on content the mezzanine
    plays earlier: 124 ms of 'drift' that is really the cut doing its job"""
    pieces = [{"c0": 0, "c1": 0.6}, {"c0": 10, "c1": 16}, {"c0": 20, "c1": 22.2},
              {"c0": 30, "c1": 36}, {"c0": 40, "c1": 40.7}]
    assert qc.sample_pieces(pieces) == [0, 2, 4]
    assert qc.sample_pieces(pieces, min_dur=2.5) == [1, 3]
    # nothing is long enough: measure what there is rather than reporting nothing
    assert qc.sample_pieces([{"c0": 0, "c1": 0.6}], min_dur=2.5) == [0]
    assert qc.sample_pieces([]) == []


def test_audio_lag_leaves_out_a_window_with_no_sound_instead_of_reading_it_as_zero(monkeypatch):
    """a silent window answered 0.0, so a stretch nobody could measure went on the card as perfectly in sync (and a silent last window hid a growing
    lag: 10 ms then 40 ms then "0 ms" reads as no drift)"""
    sr = 16000
    voice = np.random.default_rng(7).standard_normal(sr * 12).astype(np.float32)
    dropped = voice.copy(); dropped[sr * 4:sr * 8] = 0.0                                  # the video's sound drops out for the middle piece
    monkeypatch.setattr(qc, "pcm16", lambda path, sr=16000: dropped if path == "reel.mp4" else voice)
    pieces = [{"c0": t, "c1": t + 4.0, "out_in": t, "out_out": t + 4.0} for t in (0.0, 4.0, 8.0)]
    lags = qc.audio_lag("reel.mp4", "mez.mp4", pieces)
    assert [l["piece"] for l in lags] == [0, 2], lags                                     # the silent middle window is not a reading
    assert all(abs(l["lag_s"]) < 0.001 and l["corr"] > 0.9 for l in lags)
    monkeypatch.setattr(qc, "pcm16", lambda path, sr=16000: np.zeros(sr * 12, np.float32) if path == "reel.mp4" else voice)
    assert qc.audio_lag("reel.mp4", "mez.mp4", pieces) == []                              # a video with no sound at all: nothing measured
    assert "lag n/a" in qc.g2_picture_sound(None, [], [], [])["numbers"]


def test_frame_match_and_audio_lag_need_all_their_inputs():
    pieces = [{"c0": 0.0, "c1": 1.0, "out_in": 0.0, "out_out": 1.0}]
    assert qc.frame_match(None, "mez.mp4", pieces) == [] and qc.frame_match("cut.mp4", None, pieces) == [] and qc.frame_match("cut.mp4", "mez.mp4", []) == []
    assert qc.audio_lag(None, "mez.mp4", pieces) == [] and qc.audio_lag("reel.mp4", "mez.mp4", []) == []


# --- face fractions and splice edges ---------------------------------------------------------------

def test_face_fraction_ignores_a_sample_frame_at_the_very_end_of_the_file():
    """the last second of a cut has no frame at s + 0.5 when the file ends at s + 0.5: 'face 67%' for a face that was never missing"""
    track = {"samples": {"27": dict(FACE), "28": dict(FACE)}}              # second 29 would be the frame at 29.5 s, past the end
    frac, n = qc.face_fraction(track, 27.5, 29.5)
    assert (frac, n) == (1.0, 2)


def test_face_fraction_ignores_seconds_the_track_never_had_a_frame_for():
    """a cut whose EDL ends at 23.57 s but whose video stream stops at 23.43 s: second 23 was never extracted, and 'face 67%' for a face seen in every
    frame there was is wrong"""
    track = {"samples": {"21": dict(FACE), "22": dict(FACE)}, "extracted": [0, 21, 22]}
    assert qc.face_fraction(track, 20.93, 23.57) == (1.0, 2)
    assert qc.face_fraction({"samples": {"21": dict(FACE), "22": dict(FACE)}}, 20.93, 23.57) == (2 / 3, 3)    # a track written before "extracted" existed reads as before
    assert qc.face_fraction({"samples": {"21": dict(FACE)}, "extracted": [21, 22]}, 20.93, 23.57) == (0.5, 2)   # a second that had a frame and no face still counts against it
    assert qc.face_fraction(None, 0.0, 5.0) == (None, 0)


def test_splice_edges_count_only_boundaries_that_drop_source():
    pieces = [{"c0": 1.0, "c1": 2.0}, {"c0": 2.0, "c1": 3.0}, {"c0": 3.5, "c1": 4.0}]
    assert qc.splice_edges(pieces) == [(True, False), (False, True), (True, True)]    # the first head and the last tail always count


# --- G3 on a splice that is not there (contiguous pieces) and on struck words -------------------------

# the cutter splits a long segment at a silence and leaves the halves adjacent in the source; one word straddles each boundary
CONTIG = [{"seg": 3, "c0": 51.500, "c1": 54.933, "out_in": 8.167, "out_out": 11.600},
          {"seg": 4, "c0": 54.933, "c1": 57.767, "out_in": 11.600, "out_out": 14.433},
          {"seg": 5, "c0": 57.900, "c1": 63.233, "out_in": 14.433, "out_out": 19.767}]
STRADDLE = [{"text": "once", "start": 51.55, "end": 52.0},
            {"text": "check", "start": 54.70, "end": 55.387},          # spans the contiguous joint
            {"text": "counts", "start": 57.60, "end": 58.165},         # spans the real splice
            {"text": "every", "start": 58.30, "end": 63.10}]


def test_g3_ignores_a_word_running_across_a_contiguous_piece_boundary():
    """the source plays straight through a boundary where c1 == the next c0, so no word can be clipped there, but the tail reads -0.454 s and the head
    of the piece after it reads -0.16 s, and the joint check reports both"""
    g = qc.g3_onsets(CONTIG, STRADDLE, "whisperx")
    assert not any(d["piece"] == 0 and d["why"] == "tail" for d in g["detail"]), g["detail"]
    assert not any(d["piece"] == 1 and d["why"] == "head" for d in g["detail"]), g["detail"]
    assert "1 non-splice edge" in g["numbers"]


def test_g3_still_fails_the_real_splice_in_the_same_take():
    """piece 2 ends at 57.767 and piece 3 opens at 57.900: 0.133 s of the source is dropped in the middle of a word, which is audible and must still
    fail, from both sides"""
    g = qc.g3_onsets(CONTIG, STRADDLE, "whisperx")
    assert g["pass"] is False
    assert any(d["why"] == "tail" and d["piece"] == 1 for d in g["detail"])
    assert any(d["why"] == "head" and d["piece"] == 2 for d in g["detail"])


def test_g3_always_checks_the_videos_own_first_and_last_edge():
    """the opening head and the closing tail have no neighbour to be contiguous with: a clip there is the first and last thing anyone hears"""
    src = [{"text": "why", "start": 10.20, "end": 10.90}]
    one = [{"seg": 0, "c0": 10.30, "c1": 10.80, "out_in": 0.0, "out_out": 0.5}]
    g = qc.g3_onsets(one, src, "whisperx")
    assert g["pass"] is False
    assert {d["why"] for d in g["detail"]} == {"head", "tail"}


def test_g3_waiver_turns_a_fail_into_a_noted_pass():
    pieces = [{"c0": 1.0, "c1": 2.0, "out_in": 0.0, "out_out": 1.0}]
    words = [{"text": "decent", "start": 0.7, "end": 1.4}]               # the piece starts 0.3 s inside the word: a real clip by the stamps
    plain = qc.g3_onsets(pieces, words, "t")
    assert plain["pass"] is False
    waived = qc.g3_onsets(pieces, words, "t", waived="JOINTS=warn")
    assert waived["pass"] is True and waived["waived"] is True and "WAIVED (JOINTS=warn)" in waived["numbers"] and waived["detail"]


def test_g3_does_not_count_a_struck_word_as_a_word_a_piece_plays():
    """a piece starts in the gap after a struck "uh" that the aligner stamped to 1.20 s. By the stamps the cut is 0.2 s inside the uh, which the plan
    removed; the EDL's "dropped" list says so and G3 stops reading it"""
    words = [{"text": "hand,", "start": 0.362, "end": 0.703}, {"text": "uh,", "start": 0.96, "end": 1.20},
             {"text": "every", "start": 1.245, "end": 1.446}, {"text": "single", "start": 1.486, "end": 1.727}]
    pieces = [{"seg": 0, "c0": 0.033, "c1": 0.8, "out_in": 0.0, "out_out": 0.767}, {"seg": 0, "c0": 1.1667, "c1": 1.9, "out_in": 0.767, "out_out": 1.5}]
    assert qc.g3_onsets(pieces, words, "whisperx")["pass"] is False
    g = qc.g3_onsets(pieces, words, "whisperx", dropped=[{"text": "uh,", "start": 0.96, "end": 1.2}])
    assert g["pass"] is True, g


# --- which inputs the run uses ---------------------------------------------------------------------

def test_first_takes_the_first_file_that_exists_and_has_content(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    assert qc._first(str(a), str(b), None) is None
    a.write_text("")
    b.write_text("x")
    assert qc._first(str(a), str(b)).endswith("b.mp4")             # an empty file is not a deliverable
    a.write_text("y")
    assert qc._first(None, str(a), str(b)).endswith("a.mp4")


def test_freezes_recorded_by_prep_are_reused_instead_of_remeasured():
    """avskew measured THIS mezzanine at prep time; freezedetect over a 4-minute source to learn the same thing is seconds of the budget for nothing"""
    assert qc.recorded_freezes(None) is None
    assert qc.recorded_freezes({"ok": True}) is None
    assert qc.recorded_freezes({"conform": {"ok": True, "freezes_over_min": []}}) == []
    held = [{"start": 133.3, "duration": 11.3}]
    assert qc.recorded_freezes({"conform": {"ok": True, "freezes_over_min": held}}) == held


@needs_ffmpeg
def test_probe_of_a_file_that_is_not_a_video_stops_instead_of_raising(tmp_path, capsys):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(SystemExit) as e:
        qc.probe(str(bad))
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.startswith("STOP: ffprobe could not read bad.mp4") and "Traceback" not in err


@needs_ffmpeg
def test_probe_of_a_file_that_is_not_a_video_stops_with_exactly_one_line(tmp_path, capsys):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(SystemExit):
        qc.probe(str(bad))
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


# =================================================================================================================================
# The gates on a take: qc.run and the command line, end to end on one-second synthetic takes.
# =================================================================================================================================

@pytest.fixture(scope="module")
def base_take(tmp_path_factory):
    """a clean take: tagged BT.709, -16 LUFS, a face in every second, no captions"""
    return make_take(tmp_path_factory.mktemp("base"))


@pytest.fixture
def take(base_take, tmp_path):
    """a private copy of the clean take, free to change"""
    return base_take.clone(tmp_path)


def cli(t, *args):
    return subprocess.run([sys.executable, QC_PY, t.T, t.name, *args], capture_output=True, text=True)


def card_of(t):
    return json.load(open(t.path("qc.json")))


def caption_plan(events=None, flags=()):
    return {"src": "s", "durationInFrames": 30, "flags": list(flags), "events": events if events is not None else [{"mode": "word", "text": "why", "start": 0.02, "end": 0.90}]}


@needs_ffmpeg
def test_a_clean_take_passes_every_gate_and_writes_the_card_and_the_evidence(take):
    card = qc.run(take.T, take.name)
    assert card["verdict"] == "PASS" and card["faceless"] is False and card["pieces"] == 1
    assert set(card["gates"]) == {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "COLOR", "WORDS"}
    g = card["gates"]
    for k in ("G1", "G2", "G3", "G7", "COLOR", "WORDS"):
        assert g[k]["pass"] is True and g[k]["skipped"] is False, (k, g[k])
    assert "face 100%" in g["G1"]["numbers"] and "freezes 0" in g["G2"]["numbers"] and "frame-match min 1.00" in g["G2"]["numbers"]
    assert g["G4"]["skipped"] is True and g["G5"]["skipped"] is True                  # no captions plan: both are visible SKIPs
    assert g["G6"]["pass"] is None and g["G6"]["human"] is True
    assert g["COLOR"]["numbers"].startswith("tags pix_fmt yuv420p, range tv, space bt709, primaries bt709, transfer bt709")
    assert card["video"] == take.master and card["mezzanine"] == take.mez and card["render_source"] is None
    for p in (card["strip"], card["sheet"]):
        assert p.startswith(take.T) and os.path.getsize(p) > 0 and open(p, "rb").read(4) == b"\x89PNG"
    assert card == card_of(take)                                                     # the card on disk is the card returned


@needs_ffmpeg
def test_the_cli_prints_one_line_per_gate_and_exits_zero_on_a_pass(take):
    r = cli(take)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert [l.split(" ")[0] for l in lines[:9]] == ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "COLOR", "WORDS"], r.stdout
    assert lines[0].startswith("G1 PASS ") and lines[3].startswith("G4 SKIP ") and lines[5].startswith("G6 HUMAN ") and lines[8].startswith("WORDS PASS ")
    assert lines[9].startswith("PASS: strip ") and "contact-sheet.png" in lines[9] and r.stderr == ""
    assert card_of(take)["verdict"] == "PASS"


@needs_ffmpeg
def test_the_contact_sheet_is_six_frames_with_the_safe_box_the_face_box_and_the_crown_line_drawn_on_them(take):
    from PIL import Image
    qc.run(take.T, take.name)
    im = Image.open(take.path("evidence", "contact-sheet.png")).convert("RGB")
    assert im.size == (3 * qc.SHEET_W, 2 * 640)                                     # three tiles across, two down, no captions sweep needed
    px = {tuple(p) for p in np.asarray(im).reshape(-1, 3)}
    assert (0, 255, 0) in px and (0, 120, 255) in px and (0, 255, 255) in px         # safe box, face box, crown line
    assert (255, 0, 255) not in px                                                   # no caption ink without captions


@needs_ffmpeg
def test_a_faceless_take_is_judged_without_the_face_rule_and_its_sheet_has_no_face_box(tmp_path):
    from PIL import Image
    t = make_take(tmp_path, face="none")
    card = qc.run(t.T, t.name, faceless=True)
    assert card["verdict"] == "PASS" and card["faceless"] is True and "face rule off (faceless)" in card["gates"]["G1"]["numbers"]
    assert not os.path.exists(t.path("cut.facetrack.json")), "a faceless take never asks Apple Vision for a face"
    px = {tuple(p) for p in np.asarray(Image.open(card["sheet"]).convert("RGB")).reshape(-1, 3)}
    assert (0, 255, 0) in px and (0, 120, 255) not in px


@needs_ffmpeg
def test_a_plan_that_says_no_speaker_switches_the_face_rule_off_like_the_flag(tmp_path):
    t = make_take(tmp_path, face="none")
    t.write("plan.json", {"name": t.name, "speaker": False})
    card = qc.run(t.T, t.name)
    assert card["faceless"] is True and card["verdict"] == "PASS"
    t.write("plan.json", {"name": t.name, "speaker": True})
    assert qc.run(t.T, t.name, faceless=True)["faceless"] is True                   # the flag alone is enough


@needs_ffmpeg
def test_the_cli_flag_makes_a_faceless_take_pass(tmp_path):
    t = make_take(tmp_path, face="none")
    r = cli(t, "--faceless")
    assert r.returncode == 0 and "G1 PASS" in r.stdout and "face rule off (faceless)" in r.stdout, r.stdout + r.stderr
    assert card_of(t)["faceless"] is True


@needs_ffmpeg
def test_a_track_that_never_found_a_face_fails_g1_unless_the_take_is_faceless(tmp_path):
    t = make_take(tmp_path, face="empty")
    card = qc.run(t.T, t.name)
    assert card["verdict"] == "FAIL" and card["gates"]["G1"]["pass"] is False and "face 0%" in card["gates"]["G1"]["numbers"]
    assert [k for k, g in card["gates"].items() if g["pass"] is False] == ["G1"]
    r = cli(t)
    assert r.returncode == 1 and "G1 FAIL" in r.stdout and "  G1 {'piece': 0, 'why': 'face'" in r.stdout and r.stdout.count("\nFAIL: G1") == 1, r.stdout + r.stderr
    off = qc.run(t.T, t.name, faceless=True)
    assert off["verdict"] == "PASS" and off["gates"]["G1"]["pass"] is True


@needs_ffmpeg
@needs_swiftc
def test_with_no_track_file_the_face_is_looked_for_on_the_video_itself(tmp_path):
    """no cut.facetrack.json (the chain's face-on-the-master stage did not run): qc.py runs the Apple face finder on the finished video and keeps the
    track. A test picture has no face, so G1 fails on it, which proves the finder really ran"""
    t = make_take(tmp_path, face="none")
    card = qc.run(t.T, t.name)
    track = json.load(open(t.path("cut.facetrack.json")))
    assert track["samples"] == {} and track["median"].get("fallback") is True and track["H"] == 1920
    assert card["gates"]["G1"]["pass"] is False and "face 0%" in card["gates"]["G1"]["numbers"]


# --- G4 and G5 on a take with captions -----------------------------------------------------------

@needs_ffmpeg
def test_a_captions_plan_without_a_render_source_is_a_visible_skip_for_g4_and_a_real_check_for_g5(take):
    take.write("captions-plan.json", caption_plan())
    g = qc.run(take.T, take.name)["gates"]
    assert g["G4"]["skipped"] is True and "no render source" in g["G4"]["numbers"] and "--render-source" in g["G4"]["numbers"]
    assert g["G5"]["skipped"] is False and g["G5"]["pass"] is True and "1 events" in g["G5"]["numbers"]


@needs_ffmpeg
def test_a_render_source_that_is_the_video_itself_cannot_isolate_captions_so_g4_skips(take):
    take.write("captions-plan.json", caption_plan())
    g = qc.run(take.T, take.name, render_from=take.master)["gates"]
    assert g["G4"]["skipped"] is True and "no render source" in g["G4"]["numbers"]


@needs_ffmpeg
def test_a_render_source_without_a_captions_plan_is_not_swept(take):
    card = qc.run(take.T, take.name, render_from=take.cut)
    assert card["gates"]["G4"]["skipped"] is True and "no sweep" in card["gates"]["G4"]["numbers"] and card["gates"]["G5"]["skipped"] is True
    assert card["render_source"] == take.cut and card["verdict"] == "PASS"


@needs_ffmpeg
def test_cli_sweeps_a_real_captioned_video_and_writes_the_evidence(take):
    """the box sits where a running caption sits: inside the safe box (canvas x 65-940 / y 270-1420 is 270x480 x 16-235 / y 68-355), below the face
    box (y 175-275) and well below the crown line (canvas 580 is y 145). The box IS the caption ink the sweep has to find, so the contact sheet draws it"""
    from PIL import Image
    captioned = draw_box(take.master, take.path("captioned.mp4"), (40, 290, 120, 35))
    take.write("captions-plan.json", caption_plan())
    r = cli(take, "--video", captioned, "--render-source", take.master)
    assert r.returncode == 0, r.stdout + r.stderr
    card = card_of(take)
    for k in ("G1", "G2", "G3", "G4", "G5", "G7", "COLOR", "WORDS"):
        assert card["gates"][k]["pass"] is True, (k, card["gates"][k])
    g4 = card["gates"]["G4"]
    assert g4["skipped"] is False and g4["frames"] > 0 and "outside 0, on face 0, above crown 0" in g4["numbers"] and "max ink 0 px" not in g4["numbers"], g4
    assert card["video"] == captioned and card["render_source"] == take.master
    px = {tuple(p) for p in np.asarray(Image.open(card["sheet"]).convert("RGB")).reshape(-1, 3)}
    assert (255, 0, 255) in px, "the caption ink is drawn on the contact sheet where the sweep found it"
    assert "G4 PASS" in r.stdout and "G5 PASS" in r.stdout


@needs_ffmpeg
def test_cli_fails_and_exits_one_when_caption_ink_leaves_the_safe_box(take):
    """captions leaking past the safe box, here above its top edge"""
    captioned = draw_box(take.master, take.path("captioned.mp4"), (2, 8, 60, 30))
    take.write("captions-plan.json", caption_plan())
    r = cli(take, "--video", captioned, "--render-source", take.master)
    assert r.returncode == 1, r.stdout + r.stderr
    card = card_of(take)
    assert card["verdict"] == "FAIL" and card["gates"]["G4"]["pass"] is False and [k for k, g in card["gates"].items() if g["pass"] is False] == ["G4"]
    assert "G4 FAIL" in r.stdout and "outside" in r.stdout and r.stdout.count("\nFAIL: G4") == 1


@needs_ffmpeg
def test_a_faceless_take_can_still_be_swept_for_captions(take):
    """the sweep reads the face box only to keep captions off it: with no track there is no box, and nothing may crash"""
    captioned = draw_box(take.master, take.path("captioned.mp4"), (40, 290, 120, 35))
    take.write("captions-plan.json", caption_plan())
    card = qc.run(take.T, take.name, faceless=True, video=captioned, render_from=take.master)
    assert card["gates"]["G4"]["skipped"] is False and card["gates"]["G4"]["pass"] is True and card["verdict"] == "PASS"


@needs_ffmpeg
def test_a_captions_plan_that_misses_a_spoken_word_fails_g5_and_the_run(take):
    take.write("captions-plan.json", caption_plan(events=[]))
    card = qc.run(take.T, take.name)
    assert card["gates"]["G5"]["pass"] is False and any(d["why"] == "uncovered" for d in card["gates"]["G5"]["detail"]) and card["verdict"] == "FAIL"
    take.write("captions-plan.json", caption_plan(events=[], flags=["word_dropped_no_time:why"]))
    assert qc.run(take.T, take.name)["gates"]["G5"]["pass"] is True


# --- COLOR, WORDS and G7 inside the run ---------------------------------------------------------

@needs_ffmpeg
def test_a_master_with_the_wrong_tags_fails_the_color_gate_and_the_run(tmp_path):
    t = make_take(tmp_path, tagged_master=False)
    r = cli(t)
    assert r.returncode == 1, r.stdout + r.stderr
    card = card_of(t)
    assert card["verdict"] == "FAIL" and [k for k, g in card["gates"].items() if g["pass"] is False] == ["COLOR"]
    assert "COLOR FAIL" in r.stdout and "  COLOR color_primaries is unknown, not bt709" in r.stdout


@needs_ffmpeg
def test_the_color_gate_compares_with_the_reference_it_is_given_and_defaults_to_the_cut(take):
    default = qc.run(take.T, take.name)["gates"]["COLOR"]
    assert "pixels vs the cut: median drift R0.0 G0.0 B0.0" in default["numbers"]
    other = take.path("other.mp4")
    shutil.copy(take.cut, other)
    assert qc.run(take.T, take.name, reference=other)["gates"]["COLOR"]["pass"] is True


@needs_ffmpeg
def test_a_cut_transcript_that_the_finished_file_no_longer_matches_fails_the_words_gate(tmp_path):
    t = make_take(tmp_path)
    t.write(f"{t.name}-cut.mp4.qc.json", {"verdict": "PASS", "transcript": "something else entirely"})
    r = cli(t)
    assert r.returncode == 1 and "WORDS FAIL" in r.stdout and "  WORDS {'why': 'transcript'" in r.stdout, r.stdout + r.stderr
    g = card_of(t)["gates"]["WORDS"]
    assert g["pass"] is False and "words match 0.00" in g["numbers"]


@needs_ffmpeg
def test_without_the_finished_files_words_the_words_gate_is_a_visible_skip(tmp_path):
    t = make_take(tmp_path, cut_words=None)
    card = qc.run(t.T, t.name)
    assert card["gates"]["WORDS"]["skipped"] is True and "not compared" in card["gates"]["WORDS"]["numbers"] and card["verdict"] == "PASS"


@needs_ffmpeg
def test_the_words_gate_falls_back_to_the_finished_files_word_list_when_it_has_no_transcript_text(take):
    t = json.load(open(take.path("cut.words.json")))
    del t["transcript"]
    take.write("cut.words.json", t)
    assert qc.run(take.T, take.name)["gates"]["WORDS"]["pass"] is True


@needs_ffmpeg
@pytest.mark.parametrize("lufs,why", [(-12.0, "integrated"), (None, "integrated")])
def test_a_master_that_is_too_loud_or_left_at_the_rooms_level_fails_g7(tmp_path, lufs, why):
    t = make_take(tmp_path, master_lufs=lufs)
    r = cli(t)
    assert r.returncode == 1, r.stdout + r.stderr
    g7 = card_of(t)["gates"]["G7"]
    assert g7["pass"] is False and any(d["why"] == why for d in g7["detail"]) and "G7 FAIL" in r.stdout


@needs_ffmpeg
def test_a_silent_finished_file_fails_g7_and_the_run(tmp_path):
    t = make_take(tmp_path, silent_master=True)
    r = cli(t)
    assert r.returncode == 1 and "G7 FAIL I -inf LUFS" in r.stdout, r.stdout + r.stderr
    g7 = card_of(t)["gates"]["G7"]
    assert g7["pass"] is False and g7["detail"][0]["why"] == "integrated"


def _no_non_finite_numbers(text):
    def refuse(constant):
        raise ValueError(f"{constant} is not JSON")
    json.loads(text, parse_constant=refuse)


@needs_ffmpeg
def test_the_card_of_a_silent_file_is_still_valid_json(tmp_path):
    t = make_take(tmp_path, silent_master=True)
    qc.run(t.T, t.name)
    _no_non_finite_numbers(open(t.path("qc.json")).read())


@needs_ffmpeg
def test_the_card_of_an_ordinary_take_is_strict_json(take):
    qc.run(take.T, take.name)
    _no_non_finite_numbers(open(take.path("qc.json")).read())


# --- G2 and G3 inside the run --------------------------------------------------------------------

@needs_ffmpeg
def test_a_mezzanine_the_cut_does_not_match_fails_the_stale_picture_check(tmp_path):
    t = make_take(tmp_path, mez_pattern="mandelbrot")
    g2 = qc.run(t.T, t.name)["gates"]["G2"]
    assert g2["pass"] is False and any(d["why"] == "frame_match" for d in g2["detail"]), g2


@needs_ffmpeg
def test_a_voice_that_runs_late_against_the_source_fails_the_lag_check(tmp_path):
    t = make_take(tmp_path, dur=2.0, master_delay_ms=150)           # two seconds: the measurement window needs a second and a half of sound
    g2 = qc.run(t.T, t.name)["gates"]["G2"]
    assert g2["pass"] is False and any(d["why"] == "lag" and 0.1 < d["lag_s"] < 0.2 for d in g2["detail"]), g2


@needs_ffmpeg
def test_freezes_prep_recorded_are_used_and_freezedetect_is_not_run_again(take, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("freezedetect ran although prep had recorded the freezes")
    monkeypatch.setattr(qc, "freezes_of", boom)
    os.makedirs(os.path.dirname(take.avskew), exist_ok=True)
    with open(take.avskew, "w") as f:
        json.dump({"ok": False, "reason": "stalls", "conform": {"ok": True, "reason": "video == audio", "freezes_over_min": []}}, f)
    g2 = qc.run(take.T, take.name)["gates"]["G2"]
    assert g2["pass"] is True and "source conformed cleanly" in g2["numbers"] and "freezes 0" in g2["numbers"]
    with open(take.avskew, "w") as f:
        json.dump({"ok": False, "reason": "stalls", "conform": {"ok": True, "reason": "video == audio", "freezes_over_min": [{"start": 0.2, "duration": 0.7}]}}, f)
    g2 = qc.run(take.T, take.name)["gates"]["G2"]
    assert g2["pass"] is False and "freezes 1" in g2["numbers"] and any(d["why"] == "freeze" for d in g2["detail"])


@needs_ffmpeg
def test_a_source_whose_conform_failed_fails_g2(take):
    os.makedirs(os.path.dirname(take.avskew), exist_ok=True)
    with open(take.avskew, "w") as f:
        json.dump({"ok": False, "reason": "stalls", "conform": {"ok": False, "reason": "the conformed mezzanine still desyncs"}}, f)
    g2 = qc.run(take.T, take.name)["gates"]["G2"]
    assert g2["pass"] is False and any(d["why"] == "skew" for d in g2["detail"]) and "conform FAILED" in g2["numbers"]


@needs_ffmpeg
def test_a_clip_in_the_edl_fails_g3_in_the_run_and_the_waiver_in_joints_json_turns_it_into_a_noted_pass(tmp_path):
    words = [{"text": "decent", "start": 0.2, "end": 0.9, "filler": False}]
    pieces = [{"seg": 0, "c0": 0.5, "c1": 1.0, "out_in": 0.0, "out_out": 0.5}]               # the piece starts 0.3 s inside the word
    t = make_take(tmp_path, words=words, pieces=pieces)
    g3 = qc.run(t.T, t.name)["gates"]["G3"]
    assert g3["pass"] is False and any(d["why"] == "head" and d["word"] == "decent" for d in g3["detail"]) and "edges read off the waveform" in g3["numbers"]
    t.write("joints.json", {"verdict": "FAIL", "waived": "JOINTS=warn"})
    g3 = qc.run(t.T, t.name)["gates"]["G3"]
    assert g3["pass"] is True and g3["waived"] is True and "WAIVED (JOINTS=warn)" in g3["numbers"]


STRUCK_WORDS = [{"text": "hand,", "start": 0.10, "end": 0.35, "filler": False}, {"text": "uh,", "start": 0.40, "end": 0.60, "filler": True},
                {"text": "every", "start": 0.64, "end": 0.90, "filler": False}]
STRUCK = [{"text": "uh,", "start": 0.40, "end": 0.60}]


@needs_ffmpeg
def test_words_the_plan_struck_are_never_counted_as_played_by_g3_in_the_run(tmp_path):
    """the piece opens at 0.55 s, in the gap after a struck "uh," that the aligner stamped to 0.60 s: by the stamps the cut is 0.15 s inside a word the
    plan removed. Without the EDL's list of struck words that reads as a clipped head; with it, G3 reads "every" as the first word played"""
    pieces = [{"seg": 0, "c0": 0.55, "c1": 1.0, "out_in": 0.0, "out_out": 0.45}]
    t = make_take(tmp_path, words=STRUCK_WORDS, pieces=pieces)
    g3 = qc.run(t.T, t.name)["gates"]["G3"]
    assert g3["pass"] is False and any(d["why"] == "head" and d["word"] == "uh," for d in g3["detail"]), g3
    edl = json.load(open(t.edl))
    edl["dropped"] = STRUCK
    t.write(f"{t.name}-cut.edl.json", edl)
    assert qc.run(t.T, t.name)["gates"]["G3"]["pass"] is True


@needs_ffmpeg
def test_a_piece_that_plays_only_a_struck_word_is_dead_footage_for_g1_in_the_run(tmp_path):
    pieces = [{"seg": 0, "c0": 0.38, "c1": 0.62, "out_in": 0.0, "out_out": 0.24}]            # nothing but the "uh," the plan struck
    t = make_take(tmp_path, words=STRUCK_WORDS, pieces=pieces)
    assert qc.run(t.T, t.name)["gates"]["G1"]["pass"] is True                                  # an unstruck "uh," is speech
    edl = json.load(open(t.edl))
    edl["dropped"] = STRUCK
    t.write(f"{t.name}-cut.edl.json", edl)
    g1 = qc.run(t.T, t.name)["gates"]["G1"]
    assert g1["pass"] is False and any(d["why"] == "no_words" for d in g1["detail"]), g1


# --- which video is checked ------------------------------------------------------------------------

@needs_ffmpeg
def test_the_final_file_wins_over_the_cut_master_and_an_explicit_video_wins_over_both(take):
    final = take.path(f"{take.name}-final.mp4")
    shutil.copy(take.master, final)
    assert qc.run(take.T, take.name)["video"] == final
    explicit = take.path("elsewhere.mp4")
    shutil.copy(take.master, explicit)
    assert qc.run(take.T, take.name, video=explicit)["video"] == explicit
    os.remove(final)
    assert qc.run(take.T, take.name)["video"] == take.master


# --- stops --------------------------------------------------------------------------------------------

def one_stop(r, *words, one_line=True):
    out = (r.stdout + r.stderr).strip()
    assert r.returncode == 2 and r.stderr.startswith("STOP:") and r.stderr.count("STOP:") == 1 and "Traceback" not in out, out
    assert not one_line or len(r.stderr.strip().splitlines()) == 1, r.stderr
    for w in words:
        assert w in r.stderr, (w, r.stderr)


@needs_ffmpeg
def test_cli_stops_with_one_line_when_the_edl_is_missing(take):
    os.remove(take.edl)
    one_stop(cli(take), "no EDL", "-cut.edl.json")


@needs_ffmpeg
def test_cli_stops_with_one_line_when_the_source_transcript_is_missing(take):
    os.remove(take.path("words.json"))
    one_stop(cli(take), "no source transcript")


@needs_ffmpeg
def test_cli_stops_with_one_line_when_there_is_no_video_to_check(take):
    os.remove(take.master)
    one_stop(cli(take), "no video to check", "--video")


@needs_ffmpeg
def test_cli_stops_with_one_line_on_an_unreadable_json_file(take):
    take.path("x")
    with open(take.edl, "w") as f:
        f.write("{nope")
    one_stop(cli(take), "could not read", "JSONDecodeError")


@needs_ffmpeg
def test_cli_stops_with_one_line_when_the_video_is_not_a_video(take):
    with open(take.master, "wb") as f:
        f.write(b"not a video")
    one_stop(cli(take), "ffprobe could not read")


def test_cli_with_no_arguments_stops_with_the_usage_line():
    r = subprocess.run([sys.executable, QC_PY], capture_output=True, text=True)
    one_stop(r, "usage: qc.py")


def test_cli_stops_with_one_line_when_the_take_folder_does_not_exist(tmp_path):
    r = subprocess.run([sys.executable, QC_PY, str(tmp_path / "nowhere" / "t9"), "t9"], capture_output=True, text=True)
    one_stop(r, "no video to check")


# --- zones: no overlay text under a platform's interface ----------------------------------------------

@pytest.fixture(scope="module")
def zone_videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("zones")

    def clip(name, text, y):
        return still_video(text_frame(str(d / f"{name}.png"), text, y), str(d / f"{name}.mp4"))
    return {"blank": clip("blank", None, 0), "top": clip("top", "HANDLE BAND", 60), "mid": clip("mid", "MIDDLE OF IT", 900), "low": clip("low", "LOW TEXT", 1450)}


@needs_ffmpeg
@needs_swiftc
def test_overlay_text_in_the_handle_band_is_a_hit_on_two_samples_in_a_row(zone_videos):
    hits = qc.zones_check(zone_videos["top"], zone_videos["blank"], "organic", every=1.0)
    assert [(t, zone) for t, _, zone in hits] == [(0.0, "the handle band at the top"), (1.0, "the handle band at the top")]
    assert all(text.upper().startswith("HANDLE") for _, text, _ in hits)


@needs_ffmpeg
@needs_swiftc
def test_overlay_text_in_the_middle_of_the_picture_is_no_hit(zone_videos):
    assert qc.zones_check(zone_videos["mid"], zone_videos["blank"], "organic", every=1.0) == []


@needs_ffmpeg
@needs_swiftc
def test_the_two_profiles_draw_the_zones_differently(zone_videos):
    """text at y 1450 is above the organic caption band (from y 1650) and inside the ads bottom 35% (from y 1248); the top band is a zone in both"""
    assert qc.zones_check(zone_videos["low"], zone_videos["blank"], "organic", every=1.0) == []
    ads = qc.zones_check(zone_videos["low"], zone_videos["blank"], "ads", every=1.0)
    assert len(ads) == 2 and {zone for _, _, zone in ads} == {"the bottom 35%"}
    assert {zone for _, _, zone in qc.zones_check(zone_videos["top"], zone_videos["blank"], "ads", every=1.0)} == {"the top 14%"}


@needs_ffmpeg
@needs_swiftc
def test_text_that_sits_in_a_zone_for_one_sample_only_is_an_entrance_passing_through(zone_videos, tmp_path):
    """a line has to sit in a zone on two samples in a row: text on screen for the first of three samples is an animation passing through"""
    passing = str(tmp_path / "passing.mp4")
    blank3 = str(tmp_path / "blank3.mp4")
    top1 = still_video(text_frame(str(tmp_path / "t.png"), "HANDLE BAND", 60), str(tmp_path / "top1.mp4"), dur=1)
    blank2 = still_video(text_frame(str(tmp_path / "b.png"), None, 0), str(tmp_path / "blank2.mp4"), dur=2)
    for out, parts in ((passing, [top1, blank2]), (blank3, [blank2, blank2])):
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", *sum((["-i", p] for p in parts), []), "-filter_complex", f"concat=n={len(parts)}:v=1:a=0", "-t", "3",
                            "-pix_fmt", "yuv420p", "-c:v", "libx264", out], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    assert qc.zones_check(passing, blank3, "organic", every=1.0) == []
    assert len(qc.zones_check(zone_videos["top"], zone_videos["blank"], "organic", every=1.0)) == 2          # the same text on two samples is a hit


@needs_ffmpeg
@needs_swiftc
def test_text_that_is_also_in_the_raw_cut_is_footage_not_an_overlay(zone_videos):
    assert qc.zones_check(zone_videos["top"], zone_videos["top"], "organic", every=1.0) == []


@needs_ffmpeg
@needs_swiftc
def test_only_text_the_caller_drew_can_fail_when_the_caller_names_it(zone_videos):
    assert qc.zones_check(zone_videos["top"], zone_videos["blank"], "organic", every=1.0, ours={"somethingelse"}) == []
    assert len(qc.zones_check(zone_videos["top"], zone_videos["blank"], "organic", every=1.0, ours={"handleband"})) == 2
    assert len(qc.zones_check(zone_videos["top"], zone_videos["blank"], "organic", every=1.0, ours=set())) == 2      # nothing drawn found: judge every new line


def test_an_unknown_profile_stops_with_one_line(capsys):
    with pytest.raises(SystemExit) as e:
        qc.zones_check("a.mp4", "b.mp4", "stories")
    err = capsys.readouterr().err.strip()
    assert e.value.code == 2 and err == "STOP: zones: profile 'stories' is not one of organic, ads"


@needs_ffmpeg
@needs_swiftc
def test_the_zones_command_exits_one_on_a_hit_and_zero_on_none(zone_videos):
    def run(*args):
        return subprocess.run([sys.executable, QC_PY, "zones", *args], capture_output=True, text=True)
    hit = run(zone_videos["top"], zone_videos["blank"], "--every", "1.0")
    assert hit.returncode == 1 and "sits under the handle band at the top" in hit.stdout and hit.stdout.rstrip().endswith("zones (organic): 2 hit(s)"), hit.stdout + hit.stderr
    clean = run(zone_videos["mid"], zone_videos["blank"], "--every", "1.0")
    assert clean.returncode == 0 and clean.stdout.strip() == "zones (organic): 0 hit(s)", clean.stdout + clean.stderr
    ads = run(zone_videos["low"], zone_videos["blank"], "--profile", "ads", "--every", "1.0")
    assert ads.returncode == 1 and "zones (ads): 2 hit(s)" in ads.stdout
    one_stop(run(zone_videos["low"], zone_videos["blank"], "--profile", "stories"), "profile 'stories'")
