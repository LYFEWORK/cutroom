"""The cutter's planner on synthetic audio (numpy tones and silences written as a wav): where the cut starts and ends next to a struck word, and
what the retry ladder's lead_extra / tail_extra do to the edges.

The numbers come from a real take: a struck "uh" with sound from 1.01 to 1.14 s, a 40 ms gap, "every" from 1.18 s, and
WhisperX stamps that put the uh's end at 1.20 s and every at 1.245 s. The quietest moment of a window read off the stamps can reach back before
the struck word (a stamp that starts early holds the quiet in front of it) or forward past it, so the planner looks for the quiet BETWEEN the two
sounds, measured on the level scale.
"""
import json, os, wave
import numpy as np
import pytest
import cut

SR = 16000


def synth(duration, tones=(), quiet=(), noise=0.002, seed=3):
    """float signal: room noise (RMS `noise`, `quiet` stretches [(t0, t1, rms)] are quieter) plus tones [(t0, t1, amp)] at 220 Hz"""
    n = int(duration * SR)
    t = np.arange(n) / SR
    level = np.full(n, noise)
    for t0, t1, rms in quiet:
        level[(t >= t0) & (t < t1)] = rms
    y = np.random.default_rng(seed).normal(0, 1, n) * level
    for t0, t1, amp in tones:
        m = (t >= t0) & (t < t1)
        y[m] += amp * np.sin(2 * np.pi * 220 * t[m])
    return y


def write_wav(path, y):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return str(path)


def plan_for(tmp_path, y, words, segments, **extra):
    src = write_wav(tmp_path / "a.wav", y)
    wj = tmp_path / "w.wx.json"
    wj.write_text(json.dumps({"segments": [{"words": [{"word": w, "start": s, "end": e} for w, s, e in words]}]}))
    return dict({"src": src, "words_json": str(wj), "output": str(tmp_path / "cut.mp4"), "segments": segments, "refine_joints": False}, **extra)


# --- the voice: the one definition of speech ------------------------------------------------------------------------------

def test_the_threshold_is_twelve_db_over_the_room_and_clamped():
    quiet = np.full(400, -70.0)
    assert cut.threshold(quiet, 1.0, 2.0) == cut.FLOOR_DB                    # a very quiet room never goes under the floor
    noisy = np.full(400, -20.0)
    assert cut.threshold(noisy, 1.0, 2.0) == cut.CEIL_DB                     # running speech never raises it above the ceiling
    room = np.full(400, -55.0)
    assert cut.threshold(room, 1.0, 2.0) == pytest.approx(-43.0)
    assert cut.threshold(room, 1.0, 1.0) == pytest.approx(-43.0)


def test_sound_runs_close_a_dip_shorter_than_three_hops_and_keep_a_longer_gap():
    db = np.full(100, -60.0)
    db[10:30] = -20.0; db[32:50] = -20.0           # a 20 ms dip: one sound
    db[53:70] = -20.0                              # a 30 ms gap: two sounds
    runs = cut.sound_runs(db, -43.0, 0.0, 1.0)
    assert [(round(a, 3), round(b, 3)) for a, b in runs] == [(0.10, 0.50), (0.53, 0.70)]


def test_pause_run_is_the_quiet_stretch_that_holds_the_time():
    db = np.full(100, -20.0)
    db[40:60] = -70.0
    assert cut.pause_run(db, -43.0, 0.50) == (pytest.approx(0.40), pytest.approx(0.60))
    assert cut.pause_run(db, -43.0, 0.30) is None
    assert cut.pause_run(db, -43.0, 99.0) is None


def test_levels_db_matches_what_the_joint_check_measures(tmp_path):
    import cut
    y = synth(1.0, tones=[(0.3, 0.7, 0.3)])
    a = cut.Audio(write_wav(tmp_path / "x.wav", y))
    np.testing.assert_allclose(a.db[:len(a.db) - 1], cut.levels_db(a.y, a.SR)[:len(a.db) - 1], atol=1e-4)


# --- the valley between a struck word and the kept word is bounded by measured sound ---------------------------------------

def test_a_cut_start_never_lands_before_or_inside_the_struck_words_sound(tmp_path):
    """The struck "uh" sounds from 1.00 to 1.12 s, 70 ms of room, then "every" from 1.19 s. Both stamps are early (uh from 0.85 s, every from
    1.15 s), so the stamp window starts at 0.93 s, inside the very quiet stretch before the uh: the quietest moment of that window is in front
    of the uh and the piece would play the whole filler. The quiet between the two sounds is 1.12 to 1.19 s."""
    y = synth(2.2, tones=[(0.30, 0.60, 0.3), (1.00, 1.12, 0.2), (1.19, 1.70, 0.3)], quiet=[(0.60, 1.00, 0.0004)])
    plan = plan_for(tmp_path, y, [("hand", 0.30, 0.60), ("uh,", 0.85, 1.12), ("every", 1.15, 1.70)],
                    [{"match": "hand uh, every", "label": "01"}])
    pieces, _, _, _ = cut.plan_pieces(plan)
    assert [p["text"] for p in pieces] == ["hand", "every"]
    c0 = pieces[1]["c0"]
    assert 1.12 <= c0 <= 1.19, c0                                         # in the gap, after the uh and before every
    assert pieces[1]["lo"] == pytest.approx(1.12, abs=0.011)             # and the ladder may widen into the gap, no further
    assert plan["_dropped"] == [{"text": "uh,", "start": 0.85, "end": 1.12}]


def test_a_cut_end_never_lands_inside_or_after_the_struck_words_sound(tmp_path):
    """The mirror: "every" ends at 0.80 s, 60 ms of room, then a struck "uh" from 0.86 to 1.00 s, then a very quiet stretch. WhisperX stamps the uh
    after its sound (1.00 to 1.15 s), so the stamp window runs into that quiet and its quietest moment is behind the uh: the piece would end
    after the filler."""
    y = synth(1.8, tones=[(0.30, 0.80, 0.3), (0.86, 1.00, 0.2)], quiet=[(1.00, 1.60, 0.0004)])
    plan = plan_for(tmp_path, y, [("every", 0.30, 0.80), ("uh,", 1.00, 1.15)], [{"match": "every uh,", "label": "01"}])
    pieces, _, _, _ = cut.plan_pieces(plan)
    assert [p["text"] for p in pieces] == ["every"]
    c1 = pieces[0]["c1"]
    assert 0.80 <= c1 <= 0.86, c1
    assert pieces[0]["hi"] == pytest.approx(0.85, abs=0.011)


def test_the_quiet_is_still_found_when_the_stamps_are_where_they_always_were(tmp_path):
    """stamps right on the sound: the cut still starts in the gap between the two sounds"""
    y = synth(2.2, tones=[(0.30, 0.60, 0.3), (0.85, 1.12, 0.2), (1.19, 1.70, 0.3)])
    plan = plan_for(tmp_path, y, [("hand", 0.30, 0.60), ("uh,", 0.85, 1.12), ("every", 1.19, 1.70)],
                    [{"match": "hand uh, every", "label": "01"}])
    pieces, _, _, _ = cut.plan_pieces(plan)
    assert 1.12 <= pieces[1]["c0"] <= 1.19


def test_running_speech_with_no_gap_keeps_the_stamp_window(tmp_path):
    """a struck word with no quiet before the next one (one run of sound): no measured gap, so the stamp window decides and nothing may widen"""
    y = synth(2.0, tones=[(0.30, 0.60, 0.3), (0.85, 1.70, 0.25)])
    plan = plan_for(tmp_path, y, [("hand", 0.30, 0.60), ("uh,", 0.85, 1.10), ("every", 1.12, 1.70)],
                    [{"match": "hand uh, every", "label": "01"}])
    pieces, _, _, _ = cut.plan_pieces(plan)
    p = pieces[1]
    assert p["lo"] == p["c0"]


def test_every_struck_word_is_listed_for_the_checker(tmp_path):
    y = synth(2.0, tones=[(0.30, 0.60, 0.3), (0.85, 1.05, 0.2), (1.19, 1.70, 0.3)])
    plan = plan_for(tmp_path, y, [("hand", 0.30, 0.60), ("uh,", 0.85, 1.05), ("every", 1.19, 1.70)],
                    [{"match": "hand uh, every", "label": "01"}])
    cut.plan_pieces(plan)
    assert [d["text"] for d in plan["_dropped"]] == ["uh,"]
    assert all(set(d) == {"text", "start", "end"} for d in plan["_dropped"])


# --- the retry ladder's knob ------------------------------------------------------------------------------------------------

def _two_words(tmp_path, **extra):
    y = synth(2.4, tones=[(0.30, 0.60, 0.3), (1.20, 1.60, 0.3)])
    return plan_for(tmp_path, y, [("alpha", 0.30, 0.60), ("bravo", 1.20, 1.60)],
                    [{"match": "alpha", "label": "01"}, {"match": "bravo", "label": "02"}], **extra)


def _edges(pieces): return [(round(p["c0"], 3), round(p["c1"], 3)) for p in pieces]


def test_lead_and_tail_extra_move_every_edge_into_the_quiet_beside_it(tmp_path):
    base_plan = _two_words(tmp_path)
    pieces, _, review, _ = cut.plan_pieces(base_plan)
    base = _edges(cut.shape_pieces(base_plan, pieces, review))
    wide_plan = _two_words(tmp_path, lead_extra=0.04, tail_extra=0.04)
    pieces, _, review, _ = cut.plan_pieces(wide_plan)
    wide = _edges(cut.shape_pieces(wide_plan, pieces, review))
    for (b0, b1), (w0, w1) in zip(base, wide):
        assert w0 == pytest.approx(b0 - 0.04, abs=0.002) and w1 == pytest.approx(b1 + 0.04, abs=0.002), (base, wide)


def test_the_default_changes_nothing(tmp_path):
    a, b = _two_words(tmp_path), _two_words(tmp_path, lead_extra=0.0, tail_extra=0.0)
    pa, _, ra, _ = cut.plan_pieces(a); pb, _, rb, _ = cut.plan_pieces(b)
    assert _edges(cut.shape_pieces(a, pa, ra)) == _edges(cut.shape_pieces(b, pb, rb))


def test_the_extra_stops_at_the_speech_next_to_the_edge(tmp_path):
    """a huge extra cannot pull the start of "bravo" back into "alpha" or push the end of "alpha" into "bravo": the edges stop at the quiet's limits
    and never cross each other"""
    plan = _two_words(tmp_path, lead_extra=5.0, tail_extra=5.0)
    pieces, _, review, _ = cut.plan_pieces(plan)
    (a0, a1), (b0, b1) = _edges(cut.shape_pieces(plan, pieces, review))
    assert a1 <= b0 + 1e-6 and a1 <= 1.19 and b0 >= 0.60 and a0 >= 0.0


def test_an_explicit_span_is_never_widened(tmp_path):
    y = synth(2.0, tones=[(0.5, 1.5, 0.3)])
    plan = plan_for(tmp_path, y, [("alpha", 0.5, 1.5)], [{"match": "alpha", "span": [0.45, 1.55], "label": "01"}], lead_extra=0.1, tail_extra=0.1)
    pieces, _, review, _ = cut.plan_pieces(plan)
    out = cut.shape_pieces(plan, pieces, review)
    assert out[0]["explicit"] is True and out[0]["c0"] == pytest.approx(0.45, abs=0.05) and out[0]["c1"] >= 1.5


def test_the_edl_pieces_carry_no_planner_bounds(tmp_path):
    plan = _two_words(tmp_path, lead_extra=0.04)
    pieces, _, review, _ = cut.plan_pieces(plan)
    out = cut.shape_pieces(plan, pieces, review)
    assert all("lo" not in p and "hi" not in p for p in out)
