"""The cutter's edges on the voice (cut.snap_edges, cut.Voice) and its two edge gates (cut.edges_in_voice, cut.joint_words),
on synthetic audio: tones for voiced sound, band-limited noise for a fricative, low-passed noise for an outdoor room.

The cases are the ones that clipped real words: an aligned end 0.36 s early, a drawn-out "I" the cutter took for a gap, an "um" the aligner
labelled "that" that a span's end check pulled back in, and a row pinned by ear that turned off the joint listen on the row beside it, so "holes"
ended on its l and read "hole".
"""
import json, wave
import numpy as np
import pytest
import cut

SR = 16000


def room(duration, rms=0.0005, low=False, seed=5):
    """room noise: white, or (low) mostly under 800 Hz like wind on a range, with a little hiss on top"""
    rng = np.random.default_rng(seed)
    n = int(duration * SR)
    if not low:
        return rng.normal(0, rms, n)
    x = rng.normal(0, 1, n)
    f = np.fft.rfftfreq(n, 1 / SR)
    lp = np.fft.irfft(np.fft.rfft(x) * (f < 800), n)
    return lp / (np.std(lp) + 1e-12) * rms + rng.normal(0, rms * 0.1, n)


def tone(y, t0, t1, amp, hz=220, ramp=0.005):
    """a voiced stretch: a sine with 5 ms fades (a hard edge would splash a click into the 2.5 to 7 kHz band)"""
    t = np.arange(len(y)) / SR
    m = (t >= t0) & (t < t1)
    env = np.clip(np.minimum(t[m] - t0, t1 - t[m]) / ramp, 0, 1)
    y[m] += amp * env * np.sin(2 * np.pi * hz * t[m])
    return y


def hiss(y, t0, t1, rms, band=(3000, 6000), seed=9):
    """band-limited noise: a fricative (the z of "holes")"""
    a, b = int(t0 * SR), int(t1 * SR)
    x = np.random.default_rng(seed).normal(0, 1, b - a)
    f = np.fft.rfftfreq(b - a, 1 / SR)
    x = np.fft.irfft(np.fft.rfft(x) * ((f >= band[0]) & (f < band[1])), b - a)
    y[a:b] += x / (np.std(x) + 1e-12) * rms
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
    return dict({"src": src, "words_json": str(wj), "output": str(tmp_path / "cut.mp4"), "segments": segments, "refine_joints": False,
                 "sil_noise_db": -35}, **extra)


def shaped(plan):
    pieces, intent, review, fps = cut.plan_pieces(plan)
    return cut.shape_pieces(plan, pieces, review), review


def holes_take(duration=2.6):
    """'holes.' on a noisy range: its vowel 0.30 to 0.78 s, 60 ms of room, then its z 0.84 to 0.93 s, heard only in the 2.5 to 7 kHz band (the
    broadband level stays inside the wind). Aligned 0.30 to 0.60 s. Then 'and' at 1.70 s"""
    y = room(duration, rms=0.0015, low=True)
    tone(y, 0.30, 0.78, 0.04)
    hiss(y, 0.84, 0.93, 0.0012)
    tone(y, 1.70, 2.00, 0.04)
    return y, [("holes.", 0.30, 0.60), ("and", 1.70, 2.00)]


# --- the measure ------------------------------------------------------------------------------------------------------------------------

def test_the_band_hears_a_fricative_the_broadband_level_does_not():
    y, _ = holes_take()
    v = cut.Voice(y, SR)
    z, quiet = v._i(0.88), v._i(1.20)
    assert v.db[z] - v.db[quiet] < cut.EDGE_RISE_DB           # the z is inside the wind on the broadband level
    assert v.hi[z] - v.hi[quiet] > cut.EDGE_HI_RISE_DB + 3    # and plain in the band


def test_a_word_s_end_carries_over_one_short_dip_into_its_z():
    y, _ = holes_take()
    v = cut.Voice(y, SR)
    end, how = v.end_of(0.65, (0.30, 0.60), 1.66, ref=0.60, fric=True)
    assert how == "word" and 0.92 <= end <= 0.95, (end, how)
    end, how = v.end_of(0.65, (0.30, 0.60), 1.66, ref=0.60, fric=False)   # a word that ends on a vowel never ends on a band-only sound
    assert how == "word" and 0.77 <= end <= 0.80, (end, how)


def test_the_answer_does_not_depend_on_where_the_cut_sits():
    y, _ = holes_take()
    v = cut.Voice(y, SR)
    ends = {round(v.end_of(t, (0.30, 0.60), 1.66, ref=0.60)[0], 3) for t in (0.50, 0.70, 0.83, 0.90)}
    assert len(ends) == 1, ends


def test_a_breath_too_long_for_a_word_s_ending_is_not_its_ending():
    y = room(2.0, rms=0.0015, low=True)
    tone(y, 0.30, 0.78, 0.04)
    hiss(y, 0.84, 1.10, 0.0012)                       # 0.26 s of band-only sound after a dip: a breath, not a z
    v = cut.Voice(y, SR)
    end, how = v.end_of(0.65, (0.30, 0.60), 1.5, ref=0.60)
    assert how == "word" and end < 0.80, (end, how)


def test_a_start_never_walks_into_a_sound_far_louder_than_the_word():
    y = room(2.0)
    tone(y, 0.62, 0.66, 0.9)                          # a shot on the range, right before the word
    tone(y, 0.66, 1.20, 0.03)
    v = cut.Voice(y, SR)
    start, how = v.start_of(0.75, (0.66, 1.20), 0.30, ref=0.70)
    assert how == "runs on" and start is None


def test_a_start_in_running_speech_does_not_walk_back_over_another_sound():
    """'Here's a | nice, a- a | mean': the span opens inside running voice; the sound before it is a filler the row leaves out"""
    y = room(2.0)
    tone(y, 0.30, 0.55, 0.2); tone(y, 0.58, 1.20, 0.2)   # one run of voice with a 30 ms dip at 0.55
    v = cut.Voice(y, SR)
    start, how = v.start_of(0.80, (0.78, 1.20), 0.20, ref=0.78)
    assert how == "runs on"


def test_which_words_may_start_or_end_on_a_band_only_sound():
    assert cut.fricative_end("holes.") and cut.fricative_end("others") and cut.fricative_end("have")
    assert not cut.fricative_end("responsibility") and not cut.fricative_end("the") and not cut.fricative_end("I")
    assert cut.fricative_start("Some") and cut.fricative_start("help")
    assert not cut.fricative_start("And") and not cut.fricative_start("We've")


# --- the cutter -------------------------------------------------------------------------------------------------------------------------

def test_an_early_aligned_end_goes_on_to_where_the_voice_decays(tmp_path):
    """'responsibility': aligned to end at 0.85 s, sounding until 1.20 s; the cut used to end 0.07 s after the aligned end"""
    y = room(2.4)
    tone(y, 0.30, 1.20, 0.2)
    tone(y, 1.80, 2.10, 0.2)
    plan = plan_for(tmp_path, y, [("responsibility", 0.30, 0.85), ("to", 1.80, 2.10)], [{"match": "responsibility", "label": "01"}])
    out, review = shaped(plan)
    assert 1.20 <= out[0]["c1"] <= 1.30, out[0]
    assert abs(out[0]["c1"] * 30 - round(out[0]["c1"] * 30)) < 1e-6                         # on render's frame grid
    assert any("runs to 1.20" in r["why"] for r in review), review


def test_a_drawn_out_vowel_is_not_a_gap(tmp_path):
    """'And I reflect': the I is aligned 20 ms long and sounds half a second into 'reflect' with no quiet between. The planner splits at that
    'gap' (a non-silent pause); the voice runs straight through it, so the two pieces join and the I plays whole"""
    y = room(2.0)
    tone(y, 0.30, 0.50, 0.3)
    tone(y, 0.58, 1.10, 0.2); tone(y, 1.10, 1.50, 0.3)
    plan = plan_for(tmp_path, y, [("and", 0.30, 0.50), ("i", 0.55, 0.57), ("reflect", 1.12, 1.50)], [{"match": "and i reflect", "label": "01"}])
    out, review = shaped(plan)
    cuts = [(a["c1"], b["c0"]) for a, b in zip(out, out[1:]) if abs(b["c0"] - a["c1"]) > 0.02]
    assert not any(0.60 < c1 < 1.08 or 0.60 < c0 < 1.08 for c1, c0 in cuts), (out, cuts)
    assert any("runs straight on" in r["why"] for r in review), review


def test_a_span_end_never_stretches_over_a_filler_the_aligner_called_a_word(tmp_path):
    """A span ends at 1.30 s in the pause after 'that' (1.00 to 1.12 s). The aligner labelled 'that' over the pause and the 'um' after it
    (1.00 to 1.90 s), and the old end check stretched the span to the label's end, um and all"""
    y = room(2.6)
    tone(y, 0.40, 0.95, 0.2); tone(y, 1.00, 1.12, 0.1)
    tone(y, 1.60, 1.85, 0.25)                                                                # the um
    tone(y, 1.88, 2.20, 0.25)
    words = [("help", 0.40, 0.70), ("do", 0.72, 0.95), ("that.", 1.00, 1.90), ("if", 1.92, 2.20)]
    plan = plan_for(tmp_path, y, words, [{"match": "help do that.", "span": [0.35, 1.30], "label": "01"}])
    out, _ = shaped(plan)
    assert out[0]["c1"] <= 1.31, out[0]


def test_a_matched_row_ending_on_that_label_leaves_the_um_out(tmp_path):
    y = room(2.6)
    tone(y, 0.40, 0.95, 0.2); tone(y, 1.00, 1.12, 0.1)
    tone(y, 1.60, 1.85, 0.25)
    tone(y, 1.88, 2.20, 0.25)
    words = [("help", 0.40, 0.70), ("do", 0.72, 0.95), ("that.", 1.00, 1.90), ("if", 1.92, 2.20)]
    plan = plan_for(tmp_path, y, words, [{"match": "help do that.", "label": "01"}])
    out, _ = shaped(plan)
    assert all(p["c0"] < p["c1"] for p in out), out
    assert max(p["c1"] for p in out) < 1.55 and 1.12 <= max(p["c1"] for p in out), out


def test_a_struck_filler_after_a_short_dip_stays_out(tmp_path):
    """a short weak 'uh' 60 ms after the word looks like a word's ending to the bridge; the planner's measured bound keeps it out"""
    y = room(1.8)
    tone(y, 0.30, 0.80, 0.3); tone(y, 0.86, 0.96, 0.02)
    tone(y, 1.30, 1.60, 0.3)
    plan = plan_for(tmp_path, y, [("every", 0.30, 0.80), ("uh,", 0.86, 0.96), ("time", 1.30, 1.60)], [{"match": "every uh, time", "label": "01"}])
    out, _ = shaped(plan)
    assert [p["text"] for p in out] == ["every", "time"]
    assert 0.80 <= out[0]["c1"] <= 0.86 and out[1]["c0"] >= 0.96, out


def test_a_pinned_neighbour_still_gets_its_end_on_the_voice(tmp_path):
    """Row 2 matched, row 3 a span set by ear right after it. 'holes.' aligned to end at 0.60 s; its z sounds to 0.93 s"""
    y, words = holes_take()
    plan = plan_for(tmp_path, y, words, [{"match": "holes.", "label": "r2"}, {"match": "and", "span": [1.60, 2.10], "label": "r3"}])
    out, _ = shaped(plan)
    assert 0.93 <= out[0]["c1"] <= 1.0, out[0]
    assert out[1]["c0"] == pytest.approx(1.60, abs=0.04) and out[1]["explicit"]


def test_an_exact_span_keeps_both_edges(tmp_path):
    y, words = holes_take()
    plan = plan_for(tmp_path, y, words, [{"match": "holes.", "span": [0.25, 0.70], "edges_exact": True, "label": "r2"}])
    out, _ = shaped(plan)
    assert (out[0]["c0"], out[0]["c1"]) == (pytest.approx(0.25, abs=1 / 60), pytest.approx(0.70, abs=1 / 60))


def test_a_second_look_moves_nothing(tmp_path):
    y, words = holes_take()
    plan = plan_for(tmp_path, y, words, [{"match": "holes.", "label": "r2"}, {"match": "and", "label": "r3"}])
    pieces, _, review, _ = cut.plan_pieces(plan)
    v, w = cut.Voice(cut.load_pcm(plan["src"]), SR), cut.load_words(plan["words_json"])
    cut.snap_edges(pieces, v, w, review, fps=30, final=True)
    before = [(p["c0"], p["c1"]) for p in pieces]
    assert cut.snap_edges(pieces, v, w, review, fps=30, final=True) == 0
    assert [(p["c0"], p["c1"]) for p in pieces] == before


def test_the_joint_listen_refines_the_matched_side_beside_a_span(tmp_path, monkeypatch):
    """with row 3 pinned, refine_joints used to skip the joint altogether; now row 2's end is listened to and bisected, row 3's start is left as set"""
    y, words = holes_take()
    plan = plan_for(tmp_path, y, words, [{"match": "holes.", "label": "r2"}, {"match": "and", "span": [1.60, 2.10], "label": "r3"}])
    pieces, _, review, _ = cut.plan_pieces(plan)
    pieces[0]["c1"] = 0.62                                       # the aligner's early end, as it was before snap_edges
    heard = []
    def fake_heard(pcm, a0, a1, b0, b1, tmp, sr=16000):
        heard.append((a1, b0))
        return (["holes"] if a1 >= 0.93 else []) + ["and"]
    monkeypatch.setattr(cut, "_heard", fake_heard)
    cut.refine_joints(pieces, cut.load_pcm(plan["src"]), review, words=cut.load_words(plan["words_json"]), log=lambda *a: None)
    assert heard, "the joint beside the span was never listened to"
    assert pieces[0]["c1"] >= 0.93 and pieces[1]["c0"] == pytest.approx(1.60) and "refine_c0" not in pieces[1]


def test_a_span_start_never_walks_back_over_a_repeat_its_row_also_says(tmp_path):
    """'the interesting thing is, is once you become': the row says 'is' once; its second span opens on 'once', right after the repeated
    'is' (the two run together). That 'is' lines up with the row's text, but it lies before the span, so it is not the span's to walk back over"""
    y = room(2.0)
    tone(y, 0.30, 0.70, 0.2)
    tone(y, 0.92, 1.40, 0.2)                                              # the repeated "is" runs straight into "once"
    words = [("thing", 0.30, 0.50), ("is,", 0.55, 0.70), ("is", 0.95, 1.05), ("once", 1.12, 1.40)]
    plan = plan_for(tmp_path, y, words, [{"match": "thing is, once", "span": [0.25, 0.75], "label": "r6b"},
                                         {"match": "thing is, once", "span": [1.07, 1.45], "label": "r6c"}])
    out, review = shaped(plan)
    assert out[1]["c0"] >= 1.06, out[1]
    assert any("inside running voice before 'once'" in r["why"] for r in review), review


def test_a_joint_between_two_spans_is_not_listened_to(tmp_path, monkeypatch):
    y, words = holes_take()
    plan = plan_for(tmp_path, y, words, [{"match": "holes.", "span": [0.25, 1.0], "label": "r2"}, {"match": "and", "span": [1.60, 2.10], "label": "r3"}])
    pieces, _, review, _ = cut.plan_pieces(plan)
    heard = []
    monkeypatch.setattr(cut, "_heard", lambda *a, **k: heard.append(a) or [])
    cut.refine_joints(pieces, cut.load_pcm(plan["src"]), review, words=cut.load_words(plan["words_json"]), log=lambda *a: None)
    assert heard == []


def test_frames_take_a_span_written_to_three_decimals():
    assert cut._on_frame(33.667, 33.667, 33.71, 30) == pytest.approx(1010 / 30)
    assert cut._on_frame(1.0, 1.21, 1.30, 30) in (37 / 30, 38 / 30, 39 / 30)
    assert cut._on_frame(1.0, 1.21, 1.22, 30) == 1.0                                   # no frame fits: as it was


# --- the gates --------------------------------------------------------------------------------------------------------------------------

def test_the_edge_gate_lists_an_end_inside_the_voice_and_sets_an_exact_one_apart():
    y = room(2.4)
    tone(y, 0.30, 1.20, 0.2); tone(y, 1.80, 2.10, 0.2)
    v = cut.Voice(y, SR)
    words = [{"text": "responsibility", "start": 0.30, "end": 0.85}, {"text": "to", "start": 1.80, "end": 2.10}]
    pieces = [{"label": "a", "c0": 0.25, "c1": 0.90, "out_in": 0.0, "out_out": 0.65}, {"label": "b", "c0": 1.75, "c1": 2.20, "out_in": 0.65, "out_out": 1.10}]
    got = cut.edges_in_voice(pieces, v, words)
    assert [(e["label"], e["edge"]) for e in got] == [("a", "end")] and got[0]["voice_s"] == pytest.approx(1.20, abs=0.02), got
    pieces[0]["exact"] = True
    assert cut.edges_in_voice(pieces, v, words)[0]["exact"] is True


def test_the_joint_transcript_check_hears_hole_for_holes():
    words = [{"text": w, "start": s, "end": e} for w, s, e in
             [("any", 30.0, 30.3), ("new", 30.4, 30.6), ("holes.", 30.7, 31.0), ("And", 32.0, 32.2), ("my", 32.3, 32.5), ("wife's", 32.6, 33.0)]]
    pieces = [{"label": "r2", "c0": 29.9, "c1": 31.05, "out_in": 0.0, "out_out": 1.15}, {"label": "r3", "c0": 31.95, "c1": 33.1, "out_in": 1.15, "out_out": 2.30}]
    heard = [{"text": w, "start": s} for w, s in [("any", 0.1), ("new", 0.5), ("hole.", 0.8), ("And", 1.2), ("my", 1.5), ("wife's", 1.7)]]
    got = cut.joint_words(pieces, words, heard)
    assert len(got) == 1 and got[0]["expected"] == "holes." and got[0]["heard"] == "hole." and "joint r2" in got[0]["where"], got
    heard[2]["text"] = "holes"
    assert cut.joint_words(pieces, words, heard) == []
    spelled = [dict(h) for h in heard]; spelled[3:5] = [{"text": "Andmy", "start": 1.2}]     # spelling alone is not a difference
    assert cut.joint_words(pieces, words, spelled) == []
