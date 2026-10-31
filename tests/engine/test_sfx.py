"""The sfx budget (sound.py) and the synthesized kit that ships with it (sfx_synth.py, engine/sfx/): every clip decoded through ffmpeg (24-bit and
float wavs included), a kit that cannot answer stops instead of mixing silence, the budget spent tier by tier and spread out, and a kit that needs no
downloaded sound at all."""
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

import sfx_synth
import sound
import support
from helpers_sound import clip, sh, write_pcm16

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")
SHIPPED = os.path.join(ENGINE, "sfx")
REPO = os.path.dirname(ENGINE)
FAMILIES = ("click", "pop", "punch", "shutter")
OLD = ("whoosh-1", "whoosh-2", "whoosh-3", "whoosh-4", "whoosh", "whoosh-out", "swish-1", "swish-2", "swish", "tick-1", "tick-2", "tick", "thud")


def first_line(text):
    return text.strip().splitlines()[0]


# --- load_clip: any wav format, mono, 48 kHz, peak 1 -----------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["pcm_s16le", "pcm_s24le", "pcm_f32le"])
def test_every_wav_format_decodes_to_the_same_clip(tmp_path, fmt):
    """a reader that takes a 24-bit file for 16-bit gives static 1.5x too long, and a float wav raises"""
    y = sound.load_clip(clip(str(tmp_path / "a.wav"), fmt))
    assert abs(len(y) - int(0.5 * sound.SR)) < 64, len(y)          # 0.5 s at 48 kHz whatever the source format and rate
    assert abs(np.abs(y).max() - 1.0) < 1e-6
    assert np.abs(np.diff(y)).max() < 0.2, "a sine at 440 Hz is smooth; static would show large steps"


def test_stereo_is_mixed_to_mono(tmp_path):
    assert sound.load_clip(clip(str(tmp_path / "s.wav"), "pcm_s24le", ch=2)).ndim == 1


def test_the_level_is_normalised_to_a_peak_of_one_whatever_the_clip_was(tmp_path):
    quiet = str(tmp_path / "quiet.wav")
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=0.5:r=44100", "-af", "volume=0.01", quiet])
    assert r.returncode == 0
    assert abs(np.abs(sound.load_clip(quiet)).max() - 1.0) < 1e-6


def test_an_unreadable_clip_stops(tmp_path, capsys):
    p = tmp_path / "bad.wav"
    p.write_bytes(b"not audio")
    with pytest.raises(SystemExit) as e:
        sound.load_clip(str(p))
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.startswith("STOP: ffmpeg cannot read the sound ") and "bad.wav" in first_line(err), err


def test_a_clip_with_no_samples_stops(tmp_path, capsys):
    p = write_pcm16(tmp_path / "empty.wav", np.zeros(0), sr=sound.SR)
    with pytest.raises(SystemExit) as e:
        sound.load_clip(p)
    err = capsys.readouterr().err
    assert e.value.code == 2 and ("is empty" in err or "cannot read the sound" in err), err


# --- kit(): a family with a clip, or a stop that says which ----------------------------------------------------------------------------

def test_kit_stops_when_a_requested_family_has_no_clip(tmp_path, capsys):
    clip(str(tmp_path / "pop-1.wav"))
    with pytest.raises(SystemExit):
        sound.kit({"pop", "nonexistent_sound"}, bank=str(tmp_path))
    err = capsys.readouterr().err
    assert "nonexistent_sound" in err and "pop" not in err.split("no sound for")[1].split(" in ")[0], err


def test_kit_stops_when_the_whole_kit_is_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sound, "PACK", str(tmp_path / "no-pack"))
    with pytest.raises(SystemExit):
        sound.kit({"pop", "click"}, bank=str(tmp_path / "empty"))
    err = capsys.readouterr().err
    assert "kit is empty" in err and "click, pop" in err and "sfx_synth.py" in err, err


def test_a_family_with_no_clip_anywhere_stops_and_names_it(tmp_path, capsys):
    clip(str(tmp_path / "pop-1.wav"))
    with pytest.raises(SystemExit):
        sound.kit({"pop", "ghost"}, bank=str(tmp_path))
    err = capsys.readouterr().err
    assert "no sound for ghost" in err and "pop" not in err.split("in ")[0], err


def test_numbered_variants_beat_a_single_file_and_a_single_file_is_enough(tmp_path):
    for name in ("pop-1.wav", "pop-2.wav", "pop.wav", "ding.wav"):
        clip(str(tmp_path / name), secs=0.3)
    K = sound.kit({"pop", "ding"}, bank=str(tmp_path))
    assert len(K["pop"]) == 2 and len(K["ding"]) == 1


def test_the_bank_wins_over_the_engines_own_kit_for_the_same_family(tmp_path):
    clip(str(tmp_path / "pop-1.wav"), secs=0.9)
    K = sound.kit({"pop"}, bank=str(tmp_path))
    assert len(K["pop"]) == 1 and abs(len(K["pop"][0]) - int(0.9 * sound.SR)) < 64, "the bank's own clip, not the engine's three pops"
    assert len(sound.kit({"pop"}, bank="")["pop"]) == 3, 'bank="" skips the bank folder: the engine\'s pop x3'


def test_the_bank_is_config_sfx_dir_when_none_is_given(tmp_path, monkeypatch):
    clip(str(tmp_path / "pop-1.wav"), secs=0.9)
    monkeypatch.setattr(support, "config", lambda: {"sfx_dir": str(tmp_path)})
    assert sound.sfx_dir() == str(tmp_path)
    assert len(sound.kit({"pop"})["pop"]) == 1
    monkeypatch.setattr(support, "config", lambda: {})
    assert sound.sfx_dir() == "" and len(sound.kit({"pop"})["pop"]) == 3


def test_a_named_sound_outside_the_four_families_comes_from_the_bank(tmp_path):
    clip(str(tmp_path / "riser-1.wav"), secs=1.2)
    K = sound.kit({"riser", "click"}, bank=str(tmp_path))
    assert len(K["riser"]) == 1 and len(K["click"]) == 3


def test_the_engines_own_kit_loads(tmp_path):
    K = sound.kit(set(sound.FAM), bank=str(tmp_path / "nothing"))     # falls through to engine/sfx/
    assert set(K) == set(sound.FAM) and all(len(v) == 3 for v in K.values())
    assert all(abs(np.abs(c).max() - 1.0) < 1e-6 and c.ndim == 1 for v in K.values() for c in v)


def test_plan_keeps_candidates_in_their_scene(tmp_path):
    clip(str(tmp_path / "pop-1.wav"))
    plan = sound.Plan(sound.kit({"pop"}, bank=str(tmp_path)))
    plan.want(1.0, 1, ("pop", 0, False))
    plan.want(5.0, 3, ("pop", 0, False), lo=2.0, hi=4.0)
    plan.want(1.96, 3, ("pop", 0, False), lo=2.0, hi=4.0)           # 50 ms before its scene is still in it
    plan.want(-1.0, 1, ("pop", 0, False))
    plan.want(9000, 1, ("pop", 0, False))
    plan.want("soon", 1, ("pop", 0, False))
    plan.want(3.0, 1)                                                # no sound named: nothing to add
    assert [c[0] for c in plan.cand] == [1.0, 1.96]


# --- the budget: tier by tier, spread out, never two sounds close together ----------------------------------------------------------------

def cands(*rows):
    """candidates (t, tier, one sound) from [(t, tier)]"""
    return [(t, tier, (("click", 0, False),)) for t, tier in rows]


def times(ev):
    return [e[0] for e in ev]


def test_the_budget_is_spent_spread_out_and_the_result_is_in_time_order():
    ev = sound.pick(cands((0, 2), (1, 2), (5, 2), (10, 2)), budget=3)
    assert times(ev) == [0, 5, 10]                                   # the first, then the one farthest from it, then the one farthest from both


def test_a_lower_tier_is_spent_first_whatever_its_time():
    ev = sound.pick(cands((1, 3), (8, 0), (4, 1)), budget=2)
    assert sorted(e[1] for e in ev) == [0, 1] and times(ev) == [4, 8]


def test_the_budget_is_a_ceiling_over_all_the_tiers():
    ev = sound.pick(cands(*[(t * 2.0, t % 3) for t in range(20)]), budget=5)
    assert len(ev) == 5
    assert sound.pick(cands((1, 0), (5, 1)), budget=0) == []


def test_no_two_sounds_are_closer_than_the_gap_even_across_tiers():
    ev = sound.pick(cands((0.0, 0), (0.5, 1), (0.9, 2), (3.0, 3)), budget=10, gap=1.0)
    assert times(ev) == [0.0, 3.0]
    ev = sound.pick(cands((0.0, 0), (0.5, 1), (0.9, 2)), budget=10, gap=0.3)
    assert times(ev) == [0.0, 0.5, 0.9]
    assert sound.pick([], budget=4) == []


# --- speaking_level: the level the sounds are scaled by -----------------------------------------------------------------------------------

def speech(path, quiet_s=1.0, loud_s=1.0, quiet=0.01, loud=0.5):
    """a mono 48 kHz wav: quiet_s of a quiet tone, then loud_s of a loud one (so a window read from the wrong place shows in the number); a little room
    noise keeps the file from being a pure repeating pattern"""
    t = lambda secs, amp: amp * np.sin(2 * np.pi * 300 * np.arange(int(secs * sound.SR)) / sound.SR)
    y = np.concatenate([t(quiet_s, quiet), t(loud_s, loud)])
    y = y + np.random.default_rng(4).standard_normal(len(y)) * 0.0003
    return write_pcm16(path, y, sr=sound.SR)


def old_level(wav, spans):
    """the level speaking_level computes for spans inside the audio: the same windows, the same percentile"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-vn", "-ac", "1", "-ar", str(sound.SR), "-f", "f32le", "-"], capture_output=True).stdout
    v = np.frombuffer(raw, dtype=np.float32)
    hop = int(0.02 * sound.SR)
    r = [np.sqrt(np.mean(v[i:i + hop] ** 2)) for a, b in spans for i in range(int(a * sound.SR), int(b * sound.SR) - hop, hop)]
    return float(np.percentile(r, 80))


def test_spans_inside_the_audio_give_the_level_they_always_gave(tmp_path):
    wav = speech(tmp_path / "a.wav")
    for spans in ([(0.1, 1.9)], [(0.0, 0.5), (1.2, 1.8)], [(1.0, 2.0)]):
        assert sound.speaking_level(wav, spans) == old_level(wav, spans), spans


def test_a_word_stamped_past_the_end_is_clamped_not_averaged_into_nan(tmp_path, capsys):
    """an aligner can give a cut's last word a stamp past the end of the audio: the windows past the end hold no samples, their mean is NaN, and the
    level and every sound the mix scales by it come out as digital silence, which punches holes in a music bed"""
    wav = speech(tmp_path / "a.wav")
    inside = sound.speaking_level(wav, [(0.1, 1.9)])
    late = sound.speaking_level(wav, [(0.1, 1.9), (1.95, 2.3)])
    assert np.isfinite(late) and abs(late - inside) / inside < 0.05, (inside, late)
    wholly_past = sound.speaking_level(wav, [(0.1, 1.9), (2.5, 3.0)])
    assert wholly_past == inside, "a span with no audio under it adds no windows"
    assert capsys.readouterr().err == "", "a level that could be measured needs no warning"


def test_a_span_before_the_start_never_reads_the_end_of_the_audio(tmp_path, capsys):
    """a negative index counts from the end of the array: a span stamped before time zero must not measure the loud tail"""
    wav = speech(tmp_path / "a.wav")
    assert sound.speaking_level(wav, [(-3.0, -1.0)]) == sound.LEVEL_DEFAULT
    err = capsys.readouterr().err
    assert err.startswith("WARN:") and "speaking_level" in err and "0 windows" in err, err
    clamped = sound.speaking_level(wav, [(-0.5, 0.9)])
    assert clamped == old_level(wav, [(0.0, 0.9)]), "a span that starts before zero is measured from zero"


def test_spans_that_are_empty_backwards_or_not_numbers_are_skipped(tmp_path):
    wav = speech(tmp_path / "a.wav")
    want = sound.speaking_level(wav, [(0.1, 1.9)])
    junk = [(None, 1.0), (float("nan"), 1.0), (1.0, float("nan")), ("x", 2.0), (1.5, 1.0), (0.5, 0.5), (float("inf"), 3.0)]
    assert sound.speaking_level(wav, junk + [(0.1, 1.9)]) == want


def test_with_nothing_to_measure_the_level_is_the_finite_default_and_one_warn_line_says_so(tmp_path, capsys):
    wav = speech(tmp_path / "a.wav", quiet_s=0.2, loud_s=0.1)                 # 0.3 s: fewer than 21 windows of 20 ms
    for spans in ([(5.0, 6.0)], [(0.0, 0.3)], None):
        capsys.readouterr()
        level = sound.speaking_level(wav, spans)
        assert level == sound.LEVEL_DEFAULT == 0.1 and np.isfinite(level), spans
        out = capsys.readouterr()
        assert out.out == "" and len(out.err.strip().splitlines()) == 1 and out.err.startswith("WARN:") and "windows of speech to measure" in out.err, (spans, out.err)
    assert sound.speaking_level(str(tmp_path / "missing.wav"), [(0.0, 1.0)]) == 0.1, "a file ffmpeg cannot read has nothing to measure either"
    assert "WARN" in capsys.readouterr().err


def test_without_spans_the_level_is_that_of_the_loud_part_of_the_audio(tmp_path):
    """no spans: only the windows within 20 dB of the loudest speech count, so a long quiet lead-in does not drag the level down"""
    wav = speech(tmp_path / "a.wav", quiet_s=1.0, loud_s=1.0, quiet=0.01, loud=0.5)
    level = sound.speaking_level(wav)
    assert 0.3 < level < 0.4, level                                           # the loud tone's own RMS is 0.5 / sqrt(2) = 0.354


def test_a_late_word_no_longer_punches_holes_in_the_mix(tmp_path):
    """the whole chain a build runs: the level, the mix of a click, a music bed added, the clip and the int16 cast"""
    wav = speech(tmp_path / "a.wav", quiet_s=0.5, loud_s=1.5)
    vref = sound.speaking_level(wav, [(0.1, 1.9), (1.95, 2.3)])
    K = {"click": [np.ones(4800, dtype=np.float64) * 0.5]}
    Y = sound.mix([(0.5, 1, (("click", 0, False),))], K, sound.SR * 2, vref)
    assert np.isfinite(Y).all() and np.abs(Y).max() > 0
    pcm = (np.clip(Y + 0.01, -0.95, 0.95) * 32767).astype(np.int16)
    assert np.abs(pcm[int(0.5 * sound.SR):int(0.6 * sound.SR)]).min() > 0, "digital silence where the click and the bed should be"


def test_spans_windows_are_whole_hops_inside_the_audio():
    hop = int(0.02 * sound.SR)
    w = list(sound.span_windows([(0.1, 0.2)], sound.SR, hop))
    assert w[0] == int(0.1 * sound.SR) and all(b - a == hop for a, b in zip(w, w[1:])) and w[-1] + hop <= int(0.2 * sound.SR)
    assert list(sound.span_windows([(0.2, 5.0)], sound.SR // 2, hop))[-1] + hop <= sound.SR // 2     # clamped to the n samples decoded
    assert list(sound.span_windows([(0.5, 5.0)], sound.SR // 2, hop)) == []                         # a span that starts at the end has no windows


# --- mix: each sound a set number of dB over the speaking level ----------------------------------------------------------------------------

def unit_kit(*names, variants=1, length=480):
    return {n: [np.ones(length) * (k + 1) / variants for k in range(variants)] for n in names}


def peak_db(Y, vref):
    return 20 * np.log10(np.abs(Y).max() / vref)


def test_a_named_sound_plays_at_its_extra_level_and_the_roles_keep_theirs():
    """EXTRA_DB lowers long or loud named sounds; a name in neither table plays 3 dB over; FAM, the four roles every kit must hold, is unchanged"""
    assert set(sound.FAM) == {"punch", "click", "shutter", "pop"} and not set(sound.FAM) & set(sound.EXTRA_DB)
    K = {n: [np.ones(480, dtype=np.float64)] for n in ("riser", "ka-ching", "pop")}
    peak = lambda n: 20 * np.log10(np.abs(sound.mix([(0.1, 0, ((n, 0, False),))], K, sound.SR, 0.1)).max() / 0.1)
    assert peak("riser") == pytest.approx(sound.EXTRA_DB["riser"], abs=0.01)
    assert peak("ka-ching") == pytest.approx(3, abs=0.01)
    assert peak("pop") == pytest.approx(sound.FAM["pop"], abs=0.01)


def test_each_family_sits_its_own_number_of_db_over_the_speaking_level():
    K = unit_kit(*sound.FAM)
    for fam, db in sound.FAM.items():
        Y = sound.mix([(0.1, 1, ((fam, 0, True),))], K, sound.SR, 0.1)
        assert peak_db(Y, 0.1) == pytest.approx(db, abs=0.01), fam


def test_a_landing_or_a_row_is_3_db_under_and_the_per_sound_and_the_overall_level_add():
    K = unit_kit("click")
    for tier, under in ((0, 0), (2, 0), (3, 3), (4, 3)):
        Y = sound.mix([(0.1, tier, (("click", 0, True),))], K, sound.SR, 0.1)
        assert peak_db(Y, 0.1) == pytest.approx(sound.FAM["click"] - under, abs=0.01), tier
    Y = sound.mix([(0.1, 2, (("click", -2.0, True),))], K, sound.SR, 0.1, level=-6.0)
    assert peak_db(Y, 0.1) == pytest.approx(sound.FAM["click"] - 2.0 - 6.0, abs=0.01)


def test_off_centre_sounds_swap_sides_and_a_centred_one_keeps_the_middle():
    K = unit_kit("click")
    ev = [(0.1, 2, (("click", 0, False),)), (0.6, 2, (("click", 0, False),)), (1.1, 2, (("click", 0, True),))]
    Y = sound.mix(ev, K, 2 * sound.SR, 0.1)
    at = lambda t: Y[int(t * sound.SR) + 10]
    l1, r1, l2, r2, l3, r3 = *at(0.1), *at(0.6), *at(1.1)
    assert l1 > r1 and r2 > l2 and l1 == pytest.approx(r2) and r1 == pytest.approx(l2)      # first one favours the left, the second the right
    assert r1 / l1 == pytest.approx(0.42, abs=1e-6) and l3 == pytest.approx(r3)              # the weaker side is 0.42, a centred one is equal


def test_the_same_clip_is_never_played_twice_in_a_row():
    K = {"click": [np.ones(480) * 1.0, np.ones(480) * 0.5]}
    ev = [(0.1 + 0.5 * k, 2, (("click", 0, True),)) for k in range(4)]
    Y = sound.mix(ev, K, 3 * sound.SR, 0.1)
    peaks = [Y[int((0.1 + 0.5 * k) * sound.SR) + 10, 0] for k in range(4)]
    assert peaks[0] != peaks[1] and peaks[0] == pytest.approx(peaks[2]) and peaks[1] == pytest.approx(peaks[3])


def test_a_sound_that_runs_past_the_end_is_cut_there_and_the_mix_is_stereo():
    K = unit_kit("click", length=4800)
    n = sound.SR // 2
    Y = sound.mix([(0.5 - 0.001, 2, (("click", 0, True),)), (5.0, 2, (("click", 0, True),))], K, n, 0.1)     # the second is wholly past the end
    assert Y.shape == (n, 2) and np.isfinite(Y).all() and np.abs(Y[-10:]).max() > 0


# --- report: what was spent, what was left silent ------------------------------------------------------------------------------------------

def test_the_report_lists_what_sounded_and_what_was_left_silent():
    cand = [(1.0, 0, (("click", 0, True),)), (1.4, 1, (("pop", 0, True),)), (9.0, 2, (("click", 0, True), ("pop", 0, False)))]
    ev = sound.pick(cand, budget=5)
    j, line = sound.report(ev, cand, 12, 0.1, 30.0, mode="test")
    assert j["budget_per_min"] == 12 and j["speech_dbfs"] == -20.0 and j["mode"] == "test"
    assert [s["t"] for s in j["sounds"]] == [1.0, 9.0] and j["sounds"][1] == {"t": 9.0, "tier": "arrivals", "kit": ["click", "pop"]}
    assert j["silent"] == [{"t": 1.4, "tier": "entrances"}]                    # 0.4 s after the first: inside the gap
    assert line == ("sfx 2 = 4 a minute (budget 12), level -20.0 dBFS speech: flash cuts + payoff 1/1  entrances 0/1  arrivals 1/1"), line


# --- the synthesized kit: no sound is downloaded, recorded or licensed ------------------------------------------------------------------------------

def samples(path):
    with wave.open(path) as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768


def centroid(y, sr):
    sp = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / sr)
    return float((f * sp).sum() / sp.sum())


@pytest.fixture(scope="module")
def synthesized(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("kit"))
    return d, sfx_synth.synth(d)


def test_the_synthesizer_writes_the_four_families_three_ways_and_the_named_sounds(synthesized):
    d, names = synthesized
    assert names == sorted(names) and len(names) == 25
    want = {f"{f}-{k}.wav" for f in FAMILIES for k in (1, 2, 3)} | {f"{n}.wav" for n in OLD}
    assert set(names) == want
    assert sorted(os.listdir(d)) == names, "nothing but the kit"


def test_every_file_is_a_48k_mono_16_bit_wav_peaking_at_minus_6_dbfs(synthesized):
    d, names = synthesized
    for n in names:
        rate, ch, width, y = samples(os.path.join(d, n))
        assert (rate, ch, width) == (48000, 1, 2), n
        assert abs(np.abs(y).max() - 0.5) < 0.002, (n, np.abs(y).max())
        assert 0.03 < len(y) / rate < 0.7 and np.sqrt((y ** 2).mean()) > 0.01, n


def test_the_variants_of_a_family_are_different_sounds(synthesized):
    d, _ = synthesized
    for fam in FAMILIES:
        ys = [samples(os.path.join(d, f"{fam}-{k}.wav"))[3] for k in (1, 2, 3)]
        for a in range(3):
            for b in range(a + 1, 3):
                assert len(ys[a]) != len(ys[b]) or not np.array_equal(ys[a], ys[b]), (fam, a, b)


def test_each_family_sounds_like_what_it_is_called(synthesized):
    """a punch is low, a pop is mid-low, a click is bright, a shutter is bright noise: the centres of their spectra keep that order"""
    d, _ = synthesized
    c = {}
    for fam in FAMILIES:
        ys = [samples(os.path.join(d, f"{fam}-{k}.wav")) for k in (1, 2, 3)]
        c[fam] = [centroid(y, rate) for rate, _, _, y in ys]
    assert max(c["punch"]) < 200 and 200 < min(c["pop"]) and max(c["pop"]) < 700
    assert min(c["click"]) > 2000 and min(c["shutter"]) > 3000
    assert max(c["punch"]) < min(c["pop"]) < max(c["pop"]) < min(c["click"])


def test_the_synthesized_kit_alone_satisfies_the_sfx_budget_with_no_other_folder(synthesized, tmp_path, monkeypatch):
    d, _ = synthesized
    monkeypatch.setattr(sound, "PACK", str(tmp_path / "no-engine-kit"))
    K = sound.kit(set(sound.FAM), bank=d)
    assert set(K) == set(sound.FAM) and all(len(v) == 3 for v in K.values())
    named = sound.kit({"whoosh", "swish", "tick", "thud"}, bank=d)
    assert [len(named[n]) for n in ("whoosh", "swish", "tick", "thud")] == [4, 2, 2, 1]


def test_the_synthesizer_is_deterministic(synthesized, tmp_path):
    d, names = synthesized
    again = str(tmp_path / "again")
    assert sfx_synth.synth(again) == names
    for n in names:
        assert open(os.path.join(d, n), "rb").read() == open(os.path.join(again, n), "rb").read(), n


def test_the_shipped_kit_is_exactly_what_the_synthesizer_writes(synthesized):
    d, names = synthesized
    shipped = sorted(f for f in os.listdir(SHIPPED) if f.endswith(".wav"))
    assert shipped == names, (set(shipped) ^ set(names))
    for n in names:
        assert open(os.path.join(SHIPPED, n), "rb").read() == open(os.path.join(d, n), "rb").read(), f"{n} is not what sfx_synth.py writes: run python3 engine/sfx_synth.py"


def test_the_shipped_folder_holds_only_the_kit_its_readme_and_the_gitignore_that_lets_the_wavs_in():
    assert sorted(f for f in os.listdir(SHIPPED) if not f.endswith(".wav")) == [".gitignore", "README.md"]
    readme = open(os.path.join(SHIPPED, "README.md"), encoding="utf-8").read()
    assert "sfx_synth.py" in readme and "generated" in readme.lower() and "licensed" in readme
    assert "!*.wav" in open(os.path.join(SHIPPED, ".gitignore")).read().splitlines()


@pytest.mark.skipif(subprocess.run(["git", "-C", REPO, "rev-parse", "--git-dir"], capture_output=True).returncode != 0, reason="not inside a git checkout")
def test_git_does_not_ignore_the_shipped_wavs_or_it_would_ship_an_empty_kit():
    for name in ("click-1.wav", "thud.wav"):
        r = subprocess.run(["git", "-C", REPO, "check-ignore", "-q", f"engine/sfx/{name}"], capture_output=True)
        assert r.returncode == 1, f"git ignores {name}: the repo-wide *.wav rule must be undone in engine/sfx/.gitignore"


# --- the command line ----------------------------------------------------------------------------------------------------------------------

def run_py(script, *args):
    return subprocess.run([sys.executable, os.path.join(ENGINE, script), *[str(a) for a in args]], capture_output=True, text=True)


def test_the_synthesizer_command_line_writes_where_it_is_told(tmp_path):
    r = run_py("sfx_synth.py", tmp_path / "out")
    assert r.returncode == 0 and r.stdout.startswith("sfx kit: 25 files -> "), r.stdout + r.stderr
    assert len(os.listdir(tmp_path / "out")) == 25


def test_the_kit_command_names_what_it_found(tmp_path):
    r = run_py("sound.py", "kit")
    assert r.returncode == 0 and r.stdout.strip() == "kit ok: click x3, pop x3, punch x3, shutter x3", r.stdout + r.stderr
    clip(str(tmp_path / "click-1.wav"))
    r = run_py("sound.py", "kit", tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "kit ok: click x1, pop x3, punch x3, shutter x3", r.stdout + r.stderr
