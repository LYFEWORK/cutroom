"""sound.py: the downmix (a dead channel is dropped, not averaged in), the voice master (a two-pass loudnorm that lands inside the loudness gate)
and the music bed (the track's sounding body, looped with an equal-power crossfade, held steady under the talk and eased over a voice that music
alone leads into). The sfx budget has its own file (test_sfx.py)."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import cut
import sound
from helpers import video
from helpers_sound import (SR, block_db, burst_voice, decode, ffmpeg, loudness, mean_db, noise_music, rms_db, sh, tone_music, window_db, write_pcm16)

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")
SOUND = os.path.join(ENGINE, "sound.py")


def run_cli(*args):
    r = subprocess.run([sys.executable, SOUND, *[str(a) for a in args]], capture_output=True, text=True)
    return r


def one_stop_line(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return len(lines) == 1 and lines[0].startswith("STOP: ")


# --- downmix: a dead channel is dropped, not averaged in -------------------------------------------------------------------------------

def test_a_dead_channel_is_detected_by_the_30_db_rule():
    assert sound.live_channels([-27.2, -85.5]) == [0]
    assert sound.live_channels([-85.5, -27.2]) == [1]
    assert sound.live_channels([-27.2, -30.0]) is None            # a normal stereo pair is averaged
    assert sound.live_channels([-27.0, -57.0]) == [0] and sound.live_channels([-27.0, -56.9]) is None   # the line is 30 dB
    assert sound.live_channels([-20.0]) is None and sound.live_channels([-20, -20, -20, -20]) is None
    assert sound.live_channels([-20, -22, -80, -90]) == [0, 1] and sound.live_channels([-80, -20, -85, -90]) == [1]   # a quad file with dead channels
    assert sound.filter_for([-27.2, -85.5])[0] == "pan=mono|c0=c0"
    assert sound.filter_for([-30, -31])[0] is None
    assert sound.filter_for([-90, -20, -85, -90])[0] == "pan=mono|c0=c1"
    assert sound.filter_for([-20, -22, -80, -90])[0] == "pan=mono|c0=0.5*c0+0.5*c1"


def test_the_note_names_the_live_and_the_dead_channels():
    assert sound.filter_for([-27.2, -85.5])[1] == "channel 0 only (channel 1 is dead)"
    assert sound.filter_for([-20, -22, -80, -90])[1] == "channels 0, 1 only (channels 2, 3 are dead)"
    assert sound.filter_for([-30, -31])[1] == "averaged to mono"


def test_a_one_sided_mic_keeps_its_level(tmp_path):
    """left -27 dB, right silent: -ac 1 averaged the two and cost 6.1 dB, so the loudness gate read the video 6 dB low"""
    st = str(tmp_path / "one_sided.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=2:r=48000", "-af", "volume=0.3,pan=stereo|c0=c0|c1=0*c0", st)
    out = str(tmp_path / "mono.wav")
    note = sound.downmix(st, out)
    left = str(tmp_path / "left.wav")
    ffmpeg("-i", st, "-af", "pan=mono|c0=c0", left)
    assert "channel 0 only" in note
    assert abs(mean_db(out) - mean_db(left)) < 0.3, (mean_db(out), mean_db(left))
    avg = str(tmp_path / "avg.wav")
    ffmpeg("-i", st, "-ac", "1", avg)
    assert mean_db(avg) < mean_db(out) - 5, "the plain -ac 1 average should be about 6 dB lower"


@pytest.mark.parametrize("quiet_db, dead", [(-35, True), (-25, False)])
def test_the_dead_channel_line_is_30_db_under_the_loudest(tmp_path, quiet_db, dead):
    """a right channel 35 dB under the left is dead and dropped; one 25 dB under is just a quiet side and is averaged"""
    st = str(tmp_path / "s.wav")
    gain = 10 ** (quiet_db / 20)
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=2:r=48000", "-af", f"volume=0.3,pan=stereo|c0=c0|c1={gain:.5f}*c0", st)
    note = sound.downmix(st, str(tmp_path / "o.wav"))
    assert ("channel 0 only" in note) is dead and ("averaged" in note) is (not dead), note


def test_a_normal_stereo_file_is_averaged_and_a_mono_one_passes(tmp_path):
    s = str(tmp_path / "s.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=1:r=48000", "-ac", "2", s)
    assert "averaged" in sound.downmix(s, str(tmp_path / "o1.wav"))
    m = str(tmp_path / "m.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=1:r=48000", "-ac", "1", m)
    assert "mono source" in sound.downmix(m, str(tmp_path / "o2.wav"), rate=16000)


def test_the_rate_and_the_channel_count_are_what_was_asked_for(tmp_path):
    q = str(tmp_path / "quad.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=1:r=44100", "-ac", "4", q)
    for rate in (48000, 16000):
        out = str(tmp_path / f"o{rate}.wav")
        sound.downmix(q, out, rate=rate)
        r = sh(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0", out])
        assert r.stdout.strip() == f"{rate},1", r.stdout


def test_a_quad_file_with_one_live_channel_keeps_its_level_and_a_missing_file_says_so(tmp_path, capsys):
    q = str(tmp_path / "quad.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=2:r=48000", "-af", "volume=0.3,pan=quad|c0=0*c0|c1=0*c0|c2=c0|c3=0*c0", q)
    out = str(tmp_path / "mono.wav")
    note = sound.downmix(q, out)
    live = str(tmp_path / "live.wav")
    ffmpeg("-i", q, "-af", "pan=mono|c0=c2", live)
    assert "channel 2 only" in note and abs(mean_db(out) - mean_db(live)) < 0.3, (note, mean_db(out), mean_db(live))
    with pytest.raises(SystemExit) as e:
        sound.downmix(str(tmp_path / "gone.wav"), out)
    err = capsys.readouterr().err
    assert e.value.code == 2 and "file not found" in err and "no audio stream" not in err and one_stop_line(err), err


def test_a_picture_with_no_sound_says_so(tmp_path, capsys):
    v = video(tmp_path / "silent.mp4", audio=False)
    with pytest.raises(SystemExit) as e:
        sound.downmix(v, str(tmp_path / "o.wav"))
    err = capsys.readouterr().err
    assert e.value.code == 2 and "no audio stream" in err and one_stop_line(err), err


def test_the_first_audio_track_of_a_video_is_the_one_taken(tmp_path):
    v = video(tmp_path / "v.mp4", dur=2)
    out = str(tmp_path / "o.wav")
    assert "mono source" in sound.downmix(v, out, 16000) or "averaged" in sound.downmix(v, out, 16000)
    r = sh(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels:format=duration", "-of", "csv=p=0", out])
    assert r.stdout.split()[0] == "16000,1" and abs(float(r.stdout.split()[1]) - 2.0) < 0.1, r.stdout


def test_the_downmix_command_line_prints_the_note(tmp_path):
    st = str(tmp_path / "one_sided.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=1:r=48000", "-af", "volume=0.3,pan=stereo|c0=c0|c1=0*c0", st)
    r = run_cli("downmix", st, tmp_path / "o.wav", 16000)
    assert r.returncode == 0 and r.stdout.strip().endswith("channel 0 only (channel 1 is dead)"), r.stdout + r.stderr
    assert (tmp_path / "o.wav").exists()


# --- the voice master: a two-pass loudnorm that lands inside the loudness gate ---------------------------------------------------------

def speechlike(path, gain_db, secs=8):
    """pink noise low-passed to the voice band with a 3 Hz tremolo, at a chosen level"""
    ffmpeg("-f", "lavfi", "-i", f"anoisesrc=d={secs}:c=pink:a=0.5:r=48000", "-af", f"lowpass=f=3000,volume={gain_db}dB,tremolo=f=3:d=0.6", "-ac", "1", path)
    return str(path)


@pytest.mark.parametrize("gain_db", [-30, -12])
def test_the_voice_master_lands_inside_the_loudness_gate(tmp_path, gain_db):
    """a quiet and a hot source: the gate is -16 +/- 1 LUFS with the true peak at or under -1.5 dBTP, after the AAC encode that follows"""
    src = speechlike(tmp_path / "src.wav", gain_db)
    out = str(tmp_path / "master.wav")
    sound.master(src, out)
    aac = str(tmp_path / "master.m4a")
    ffmpeg("-i", out, "-c:a", "aac", "-b:a", "192k", aac)
    i, tp = loudness(aac)
    assert abs(i - -16.0) <= 1.0, i
    assert tp <= -1.5, tp


def test_the_master_is_mono_48k_and_returns_what_pass_one_measured(tmp_path):
    src = speechlike(tmp_path / "src.wav", -20)
    out = str(tmp_path / "master.wav")
    m = sound.master(src, out)
    assert set(m) == {"input_i", "input_lra", "input_tp", "input_thresh", "target_offset"} and -70 < m["input_i"] < -5
    r = sh(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels:format=duration", "-of", "csv=p=0", out])
    rate, dur = r.stdout.split()[0], float(r.stdout.split()[1])
    assert rate == "48000,1", r.stdout
    assert abs(dur - (8 - 0.025)) < 0.05, dur           # the denoiser's 25 ms of latency is taken back out: the master stays on the cut's clock


def test_the_targets_are_arguments(tmp_path):
    src = speechlike(tmp_path / "src.wav", -20)
    out = str(tmp_path / "master.wav")
    sound.master(src, out, i=-20.0, tp=-3.0)
    i, tp = loudness(out)
    assert abs(i - -20.0) <= 0.7 and tp <= -2.9, (i, tp)


def test_the_master_stops_on_silent_or_tiny_input_with_a_whole_sentence(tmp_path, capsys):
    silent = str(tmp_path / "silent.wav")
    ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3", silent)
    tiny = str(tmp_path / "tiny.wav")
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=0.1:r=48000", tiny)
    for src in (silent, tiny):
        with pytest.raises(SystemExit) as e:
            sound.master(src, str(tmp_path / "o.wav"))
        err = capsys.readouterr().err
        assert e.value.code == 2 and one_stop_line(err), err
        assert "silent or too short" in err or "loudnorm pass" in err, err
        assert "t of range" not in err, err


def test_a_missing_file_is_one_stop_line_from_the_command_line(tmp_path):
    r = run_cli("master", tmp_path / "gone.wav", tmp_path / "o.wav")
    assert r.returncode == 2 and one_stop_line(r.stderr) and "loudnorm pass 1 failed" in r.stderr, r.stderr


def test_the_master_command_line_takes_its_targets(tmp_path):
    src = speechlike(tmp_path / "src.wav", -20, secs=6)
    r = run_cli("master", src, tmp_path / "o.wav", "--i", -18, "--tp", -3)
    assert r.returncode == 0 and "target -18 LUFS, TP -3" in r.stdout, r.stdout + r.stderr
    assert abs(loudness(str(tmp_path / "o.wav"))[0] - -18.0) <= 0.7


# --- the command line contract ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("args", [(), ("frobnicate",), ("downmix",), ("master", "only-one-argument"), ("bed", "a.wav", "b.wav"), ("master", "a.wav", "b.wav", "--i")])
def test_a_bad_command_line_is_one_stop_line_and_exit_2(args):
    r = run_cli(*args)
    assert r.returncode == 2 and one_stop_line(r.stderr) and "Traceback" not in r.stderr, (args, r.stderr)


# --- the music bed: the track's body, looped with an equal-power crossfade ----------------------------------------------------------------

def track(intro=5.0, body=24.0, fade=2.0, outro=1.5, pulse=True, seed=7):
    """a ~32 s stereo bank track: `intro` s at -60 dB under the music, `body` s of music (noise on an 82 bpm pulse), a linear fade `fade` s long,
    then `outro` s of digital silence"""
    n = int((intro + body + fade + outro) * SR)
    t = np.arange(n) / SR
    lvl = np.where(t < intro, 10 ** (-60 / 20), 1.0) * np.clip((intro + body + fade - t) / fade, 0, 1)
    if pulse:
        lvl = lvl * (0.6 + 0.4 * np.abs(np.sin(np.pi * t * 82 / 60)))
    return 0.2 * np.random.default_rng(seed).standard_normal((n, 2)) * lvl[:, None]


def levels(bed, frame):
    h = int(frame * SR)
    k = len(bed) // h
    return 20 * np.log10(np.maximum(np.sqrt((bed[:k * h].reshape(k, h, -1) ** 2).mean(axis=(1, 2))), 1e-12))


def longest_hole(bed, under_db=20, frame=0.05):
    """seconds of the longest run of frame-s stretches more than under_db under the bed's median"""
    best = run = 0
    lv = levels(bed, frame)
    for low in lv < np.median(lv) - under_db:
        run = run + 1 if low else 0
        best = max(best, run)
    return best * frame


def raw_loop(M, n):
    """the bed made the naive way: the whole track, concatenated end to start"""
    return np.concatenate([M] * (n // len(M) + 1))[:n]


def test_the_body_drops_the_quiet_intro_and_the_silent_outro_and_keeps_the_music():
    a, b = sound.span(track(), SR)
    assert abs(a / SR - 5.0) <= 0.05, a / SR
    assert 29.0 < b / SR <= 31.0, b / SR      # inside the fade (it ends 31 s in): the music and the start of its fade stay, the silence goes


def test_a_bed_three_tracks_long_has_no_stretch_over_half_a_second_more_than_20_db_under_its_median():
    M, n = track(), 75 * SR
    a, b = sound.span(M, SR)
    bed = sound.loop(M[a:b], n, SR)
    assert bed.shape == (n, 2)
    assert longest_hole(bed) <= 0.5, longest_hole(bed)


def test_the_raw_loop_of_the_same_track_has_the_hole_the_test_above_would_catch():
    """the control: the same track looped as it was has a hole of several seconds at its first seam (the 1.5 s silence, the 5 s intro)"""
    assert longest_hole(raw_loop(track(), 75 * SR)) > 5


def seam_mid_db(bed, M):
    """the level of the middle 0.25 s of the first seam, in dB against the bed's median 0.25 s level"""
    mid, q = len(M) - int(sound.XFADE_S * SR / 2), int(0.125 * SR)
    lv = levels(bed, 0.25)
    return 20 * np.log10(np.sqrt((bed[mid - q:mid + q] ** 2).mean())) - np.median(lv)


def test_the_seam_is_a_crossfade_that_overlaps_the_next_pass_by_xfade_s():
    """a hard cut would start the second pass at the track's first sample; the crossfade starts it XFADE_S in, under the end of the first"""
    M = track(intro=0, fade=0.001, outro=0, pulse=False)
    bed, x = sound.loop(M, 2 * len(M), SR), int(sound.XFADE_S * SR)
    assert np.array_equal(bed[len(M):len(M) + SR], M[x:x + SR])
    assert np.array_equal(bed[:len(M) - x], M[:len(M) - x])


def test_the_seam_is_equal_power_so_the_bed_holds_its_level_through_it():
    """steady music across a seam: the cosine and sine hold the level within a dB of the bed's median; the control, a linear crossfade of the
    same two passes, dips about 3 dB at its middle"""
    M = track(intro=0, fade=0.001, outro=0, pulse=False)
    bed = sound.loop(M, 2 * len(M), SR)
    assert abs(seam_mid_db(bed, M)) < 1.0
    assert levels(bed, 0.25).max() - levels(bed, 0.25).min() < 2.0
    x = int(sound.XFADE_S * SR)
    ramp = (np.arange(x) + 0.5)[:, None] / x
    linear = bed.copy()
    linear[len(M) - x:len(M)] = M[-x:] * (1 - ramp) + M[:x] * ramp
    assert seam_mid_db(linear, M) < -2.5


def test_a_reel_shorter_than_the_body_gets_the_body_from_its_start_with_no_seam():
    M = track()
    a, b = sound.span(M, SR)
    bed = sound.loop(M[a:b], 10 * SR, SR)
    assert np.array_equal(bed, M[a:a + 10 * SR])


def test_a_track_with_nothing_above_the_line_is_all_body_and_an_empty_one_is_a_silent_bed():
    quiet = np.zeros((3 * SR, 2))
    assert sound.span(quiet, SR) == (0, 3 * SR)
    assert not sound.loop(quiet[:0], SR, SR).any()
    assert sound.span(np.ones((SR // 2, 2)), SR) == (0, SR // 2)             # a track under a second is all body


def test_a_tiny_body_crossfades_over_a_third_of_itself_and_keeps_its_shape():
    M = np.random.default_rng(3).standard_normal((2 * SR, 2)) * 0.1
    bed = sound.loop(M, 9 * SR, SR)
    assert bed.shape == (9 * SR, 2) and np.isfinite(bed).all()
    assert np.array_equal(bed[:SR], M[:SR])


# --- the envelope: one bed that never pops up between lines --------------------------------------------------------------------------------

# the eleven lines of a real talk as a words file has them: the old per-run duck brought the bed back up in each of the ten pauses
TALK_LINES = [(0.44, 2.15), (3.69, 5.87), (6.33, 11.48), (11.91, 13.34), (13.85, 20.33), (21.26, 24.47), (24.95, 27.71), (28.27, 31.04),
              (31.52, 32.25), (32.73, 33.37), (33.86, 35.11)]


def words(lines):
    return [{"text": "word", "start_s": a, "end_s": b} for a, b in lines]


def env_db(spans, seconds=42.0):
    return 20 * np.log10(sound.envelope(int(seconds * SR), SR, spans))


def at(e, t):
    return e[int(round(t * SR))]


def per_run_duck(lines, seconds=42.0):
    """the control: a duck made per run of words (words closer than 0.4 s run together), 8 dB under each run, with 0.18 s ramps 0.2 s out"""
    tt, env, spans = np.arange(int(seconds * SR)) / SR, np.ones(int(seconds * SR)), []
    for w in words(lines):
        if spans and w["start_s"] - spans[-1][1] < 0.4:
            spans[-1][1] = w["end_s"]
        else:
            spans.append([w["start_s"], w["end_s"]])
    for a, b in spans:
        k = np.clip(np.minimum((tt - (a - 0.2)) / 0.18, ((b + 0.2) - tt) / 0.18), 0, 1)
        env = np.minimum(env, 1 - k * (1 - 10 ** (-8 / 20)))
    return 20 * np.log10(env)


def pauses(lines):
    return [(b0, a1) for (_, b0), (a1, _) in zip(lines, lines[1:])]


def test_the_bed_holds_through_every_pause_between_the_lines_of_a_talk():
    runs = sound.talk(words(TALK_LINES))
    assert len(runs) == 1 and runs[0][0]["start_s"] == 0.44 and runs[0][1]["end_s"] == 35.11
    e = env_db(sound.edges(runs))
    for a, b in pauses(TALK_LINES):
        seg = e[int(a * SR):int(b * SR)]
        assert np.abs(seg + sound.DUCK_DB).max() < 0.01, (a, b, seg.max())     # the talk level, to the hundredth of a dB


def test_a_per_run_duck_on_the_same_lines_comes_up_8_db_in_every_pause():
    """the control for the test above: a duck made per run of words brings the bed back to its full level in each of the ten pauses"""
    e = per_run_duck(TALK_LINES)
    for a, b in pauses(TALK_LINES):
        assert e[int(a * SR):int(b * SR)].max() > -0.1, (a, b)


def test_talk_runs_lines_together_unless_no_one_speaks_for_hold_s():
    w = words([(1.0, 2.0), (5.9, 7.0), (11.0, 12.0)])           # 3.9 s, then exactly 4.0 s of no voice
    runs = sound.talk(w)
    assert [(a["start_s"], b["end_s"]) for a, b in runs] == [(1.0, 7.0), (11.0, 12.0)]
    assert sound.talk([]) == []


def test_a_voice_that_music_alone_leads_into_gets_the_bed_eased_down_over_1_5_s_before_it():
    """a 6 s stretch with no voice (a montage, a cold open): the music plays alone at its full level, then eases down over EASE_S and has landed
    on the talk level by the time the next line starts, LEAD_S before its first word when the voice is not read"""
    sp = sound.edges(sound.talk(words([(1.0, 5.0), (11.0, 15.0)])))
    assert np.allclose(sp, [(0.85, 5.45), (10.85, 15.45)])
    e = env_db(sp, 20.0)
    on = 10.85
    assert abs(at(e, 8.0)) < 1e-9 and abs(at(e, on - sound.EASE_S)) < 1e-9          # music alone: its full level
    assert abs(at(e, on - sound.EASE_S / 2) + sound.DUCK_DB / 2) < 0.01           # half way down half way through
    assert abs(at(e, on) + sound.DUCK_DB) < 1e-9 and abs(at(e, 11.0) + sound.DUCK_DB) < 1e-9   # down before the voice
    ease = e[int((on - sound.EASE_S) * SR):int(on * SR)]
    assert (np.diff(ease) <= 1e-12).all()                                             # only ever down


def test_the_ease_never_steps_and_leaves_and_lands_with_no_corner():
    e = env_db(sound.edges(sound.talk(words([(1.0, 5.0), (11.0, 15.0)]))), 20.0)
    per_10ms = np.abs(np.diff(e[::SR // 100]))
    assert per_10ms.max() <= sound.DUCK_DB * np.pi / (2 * sound.EASE_S) / 100 + 1e-3   # 0.084 dB in 10 ms at most (a linear 0.18 s ramp steps 0.7)
    on, step = 10.85, per_10ms.max()
    first, last = (at(e, on - sound.EASE_S) - at(e, on - sound.EASE_S + 0.01)), (at(e, on - 0.01) - at(e, on))
    assert 0 < first < step / 10 and 0 < last < step / 10, (first, last, step)          # it leaves and lands gently: no corner at either end
    assert np.abs(np.diff(per_run_duck([(1.0, 5.0), (11.0, 15.0)], 20.0)[::SR // 100])).max() > 0.6   # the control: linear ramps step


def test_after_the_last_word_the_bed_eases_back_up_and_not_before_the_word_has_decayed():
    sp = sound.edges(sound.talk(words(TALK_LINES)))
    decay = sp[-1][1]
    assert abs(decay - (35.11 + sound.TAIL_S)) < 1e-9                               # the voice not read: TAIL_S after the last word
    e = env_db(sp)
    assert np.abs(e[int(35.11 * SR):int(decay * SR)] + sound.DUCK_DB).max() < 1e-9   # still down through the decay
    assert abs(at(e, decay + sound.EASE_S / 2) + sound.DUCK_DB / 2) < 0.01
    assert abs(at(e, decay + sound.EASE_S)) < 1e-9                                  # music alone again under the end card


def test_a_reel_that_opens_on_its_first_word_starts_at_the_talk_level():
    e = env_db(sound.edges(sound.talk(words([(0.08, 3.0)]))), 6.0)
    assert np.abs(e[:int(3.0 * SR)] + sound.DUCK_DB).max() < 1e-9


def voice_clip(t_on=1.0, t_off=1.5, tail=0.9, room_db=-70.0, word_db=-20.0, seconds=4.0, seed=3):
    """a word on a quiet room: a voiced tone (partials under the engine's 2.5 to 7 kHz band) at word_db from t_on (a 5 ms onset) to t_off, then its
    decay, falling evenly in dB to the room over tail seconds"""
    t = np.arange(int(seconds * SR)) / SR
    lvl = np.where((t >= t_on) & (t < t_off), word_db, np.where((t >= t_off) & (t < t_off + tail), word_db + (room_db - word_db) * (t - t_off) / tail, -200.0))
    lvl = lvl + 20 * np.log10(np.clip((t - t_on) / 0.005, 1e-10, 1))
    tone = np.sqrt(2) * (np.sin(2 * np.pi * 220 * t) * 0.9 + np.sin(2 * np.pi * 880 * t) * 0.3)
    room = np.random.default_rng(seed).standard_normal(len(t)) * 10 ** (room_db / 20)
    return (tone * 10 ** (lvl / 20) + room).astype(np.float32)


def test_the_edges_of_the_talk_are_read_on_the_voice_not_on_the_aligners_times():
    """a voice starts in the gap before its first word and ends after its last word's decay. The aligner started this word 0.25 s late and ended it
    while its decay still sounded (the voice is over its threshold until about 2.04 s): read on the voice, the bed is down by 1.0 s and stays down
    until the decay is over; the margins alone would have it moving 0.1 s early at both ends"""
    y = voice_clip()
    w = [{"text": "mean", "start_s": 1.25, "end_s": 1.5}]
    (a, b), = sound.edges(sound.talk(w), cut.Voice(y, SR), len(y) / SR)
    assert abs(a - 1.0) <= 0.02, a
    assert 1.5 + sound.TAIL_S + 0.05 < b <= 1.5 + sound.EDGE_MAX_S, b
    assert np.allclose(sound.edges(sound.talk(w)), [(1.1, 1.95)])                # the control: the margins off the aligned times


def test_the_talk_never_runs_past_the_end_of_the_cuts_audio():
    assert np.allclose(sound.edges(sound.talk(words([(0.44, 35.11)])), audio_s=35.11), [(0.29, 35.11)])


def test_the_music_alone_stretches_and_their_loudness(tmp_path):
    """alone() is where the music plays at its full level, clear of the talk and its eases; loudness() reads ffmpeg's ebur128 log over them.
    A tone at two levels 20 dB apart measures 20 LU apart"""
    assert np.allclose(sound.alone([(0.29, 35.56)], 0.25, 38.6), [(37.06, 38.6)])  # under the end card, before its fade
    assert sound.alone([(0.29, 35.56)], 0.25, 36.0) == []                          # the bed fades before it is fully up: nothing to measure
    assert np.allclose(sound.alone([(5.0, 8.0)], 0.25, 20.0), [(0.25, 3.5), (9.5, 20.0)])
    wav = tmp_path / "two-levels.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=3,volume=0.2", "-f", "lavfi", "-i", "sine=f=440:d=3,volume=0.02",
                    "-filter_complex", "[0][1]concat=n=2:v=0:a=1", str(wav)], check=True)
    log = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    loud, quiet = sound.loudness(log, [(0.5, 2.9)]), sound.loudness(log, [(3.5, 5.9)])
    assert abs(loud - quiet - 20) < 0.2, (loud, quiet)
    assert sound.loudness(log, [(10, 12)]) is None


# --- the envelope is worked out per span window: the same numbers as the whole-length formula ---------------------------------------------

def naive_envelope(n, sr, spans, duck_db, ease_s):
    """the formula over the whole length for every span (the slow way)"""
    t = np.arange(n) / sr
    e = max(ease_s, 1e-3)
    k = np.zeros(n)
    for a, b in spans:
        x = np.clip(np.minimum(t - (a - e), (b + e) - t) / e, 0, 1)
        k = np.maximum(k, 0.5 - 0.5 * np.cos(np.pi * x))
    return 10 ** (-duck_db * k / 20)


@pytest.mark.parametrize("spans, duck, ease, seconds", [
    ([(1.0, 3.0), (6.0, 7.0)], 8.0, 1.5, 12),
    ([(-1.0, 0.5)], 8.0, 1.5, 4),                       # a span that starts before the audio does
    ([(3.0, 9.0)], 8.0, 1.5, 6),                        # one that runs past its end
    ([(1.0, 2.0), (1.5, 4.0), (3.5, 3.6)], 20.0, 0.5, 8),   # overlapping spans: the deepest ease wins
    ([(0.2, 0.4), (5.0, 5.1)], 3.0, 3.0, 8),            # eases longer than the gaps between spans
    ([(2.0, 4.0)], 0.0, 1.5, 6),                        # no ducking at all
    ([(2.0, 4.0)], 8.0, 0.0, 6),                        # a zero ease is worked as the smallest one the formula allows
    ([], 8.0, 1.5, 3),
    ([(i * 2.0, i * 2.0 + 0.7) for i in range(15)], 12.0, 0.8, 32),
])
def test_the_envelope_of_a_window_is_the_envelope_of_the_whole_length(spans, duck, ease, seconds):
    n = int(seconds * 8000)
    got = sound.envelope(n, 8000, spans, duck, ease)
    assert np.array_equal(got, naive_envelope(n, 8000, spans, duck, ease))


def test_the_envelope_is_one_where_music_plays_alone_and_the_talk_level_inside_a_span():
    e = sound.envelope(10 * 1000, 1000, [(3.0, 5.0)], 8.0, 1.0)
    assert e[0] == 1.0 and e[1500] == 1.0 and e[-1] == 1.0
    assert np.allclose(20 * np.log10(e[3000:5001]), -8.0)
    assert e.min() == pytest.approx(10 ** (-8 / 20))


# --- voice_spans: the talk read off the voice itself ------------------------------------------------------------------------------------

BURSTS = [(1.0, 3.0), (4.5, 7.0), (8.5, 10.0), (20.0, 23.0)]


def test_voice_spans_finds_the_talk_in_the_voice_with_its_margins():
    y = burst_voice(BURSTS, 40)
    spans, runs = sound.voice_spans(y)
    assert len(runs) == 4
    for (a, b), (ra, rb) in zip(BURSTS, runs):
        assert abs(ra - a) < 0.03 and abs(rb - b) < 0.03, (a, b, ra, rb)
    assert len(spans) == 2                                        # the first three lines are 1.5 s apart: one stretch of talk
    assert np.allclose(spans, [(0.85, 10.0 + sound.TAIL_S), (20.0 - sound.LEAD_S, 23.0 + sound.TAIL_S)], atol=0.03), spans


def test_voice_spans_hold_s_decides_what_a_pause_between_lines_is():
    y = burst_voice(BURSTS, 40)
    assert len(sound.voice_spans(y, hold_s=1.0)[0]) == 4         # 1.5 s of quiet is longer than a 1 s hold
    assert len(sound.voice_spans(y, hold_s=12.0)[0]) == 1        # the 10 s gap is shorter than a 12 s hold


def test_voice_spans_never_leave_the_audio():
    y = burst_voice([(0.02, 1.0), (3.0, 3.98)], 4.0)
    spans, _ = sound.voice_spans(y)
    assert spans[0][0] == 0.0 and spans[-1][1] == 4.0


# --- bed(): the music under a voice, at levels relative to the voice -------------------------------------------------------------------

INNER = [(1.1, 2.9), (4.6, 6.9), (8.6, 9.9), (20.1, 22.9)]           # inside the bursts, clear of their edges and of any ease
ALONE = [(12.5, 18.0), (25.0, 38.0)]                                 # no voice, clear of the eases (the second ends before the closing fade)


def pcm_pair(tmp_path, music_kind="noise", music_dur=45, bursts=BURSTS, dur=40, rms=0.1, name="v"):
    """a voice wav (48 kHz mono, 16 bit) and a music wav: returns (voice path, music path)"""
    v = write_pcm16(tmp_path / f"{name}.wav", burst_voice(bursts, dur, rms=rms))
    M = noise_music(music_dur) if music_kind == "noise" else tone_music(music_dur)
    m = write_pcm16(tmp_path / f"{name}-{music_kind}.wav", M)
    return v, m


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """one bed of each kind of music, made once and read back: {'noise' | 'tone': (voice, mix, report)} as decoded stereo float arrays"""
    d = tmp_path_factory.mktemp("bed")
    out = {}
    for kind in ("noise", "tone"):
        v, m = pcm_pair(d, kind)
        o = str(d / f"out-{kind}.wav")
        rep = sound.bed(v, m, o)
        out[kind] = (decode(v), decode(o), rep, o)
    return out


def level_under(voice, mix, windows, ref_windows=None):
    """(the voice's level over `ref_windows` (default: the same windows), the bed's level over `windows`): the bed is what the mix has beyond the voice"""
    placed = mix - voice
    both = lambda x, ws: np.concatenate([x[int(a * SR):int(b * SR)] for a, b in ws])
    return rms_db(both(voice, ref_windows or windows)), rms_db(both(placed, windows))


def test_the_bed_sits_22_db_under_the_talk_and_14_db_under_the_voice_where_it_plays_alone(made):
    voice, mix, rep, _ = made["noise"]
    v, under = level_under(voice, mix, INNER)
    assert abs((v - under) - 22.0) <= 1.5, (v, under)
    v, alone = level_under(voice, mix, ALONE, ref_windows=INNER)
    assert abs((v - alone) - 14.0) <= 1.5, (v, alone)


def test_the_voice_is_the_level_the_burst_was_made_at_in_the_decoded_channels(made):
    """the check above reads the voice off the decoded file: a mono voice decoded to stereo sits 3 dB down per channel (the pan law)"""
    voice, _, _, _ = made["noise"]
    v = rms_db(np.concatenate([voice[int(a * SR):int(b * SR)] for a, b in INNER]))
    assert abs(v - (20 * np.log10(0.1) - 3.01)) < 0.2, v


def test_the_report_says_what_was_asked_and_what_was_measured(made):
    voice, mix, rep, _ = made["noise"]
    assert rep["under_speech_db"] == 22.0 and rep["alone_db"] == 14.0 and rep["ease_s"] == 1.5 and rep["duck_db"] == 8.0
    assert abs(rep["under_speech_db_measured"] - 22.0) <= 1.5 and abs(rep["alone_db_measured"] - 14.0) <= 1.5, rep
    assert rep["peak_scaled"] is False
    assert len(rep["spans"]) == 2 and np.allclose(rep["spans"], [[0.85, 10.45], [19.85, 23.45]], atol=0.05), rep["spans"]
    assert np.allclose(rep["alone_windows"], [[11.95, 18.35], [24.95, 40.0]], atol=0.05), rep["alone_windows"]
    # the bed at its alone level sits alone_db under the voice: music level + gain = voice level - 14
    assert abs(rep["music_db"] + rep["gain_db"] - (rep["voice_db"] - 14.0)) <= 0.11, rep
    v, _ = level_under(voice, mix, INNER)
    assert abs(rep["voice_db"] - v) <= 1.0, (rep["voice_db"], v)


def test_the_bed_never_pops_up_between_lines_closer_than_4_s(made):
    voice, mix, _, _ = made["tone"]
    bed = block_db(mix - voice)
    a, b = int(1.0 / 0.01), int(10.0 / 0.01)
    assert bed[a:b].max() - bed[a:b].min() < 0.05, "the bed moved between lines that are closer than the hold"   # 1.5 s pauses at 3.0-4.5 and 7.0-8.5
    assert abs((bed[int(15 / 0.01)] - bed[int(5 / 0.01)]) - 8.0) < 0.05                                          # but in the 10 s of quiet it comes up the whole 8 dB


def test_the_bed_eases_everywhere_and_never_steps(made):
    voice, mix, _, _ = made["tone"]
    bed = block_db(mix - voice)
    d = np.abs(np.diff(bed[10:-160]))                        # leave out the 50 ms fade in and the closing fade (a cosine in amplitude: steep in dB)
    assert d.max() <= sound.DUCK_DB * np.pi / (2 * sound.EASE_S) / 100 + 0.02, d.max()   # the raised cosine's steepest 10 ms: 0.084 dB
    assert np.abs(np.diff(d)).max() < 0.01, "the bed has a corner in its ease"


def test_the_bed_fades_in_over_50_ms_and_out_over_the_last_ease(made):
    voice, mix, _, _ = made["tone"]
    bed = block_db(mix - voice)
    assert bed[0] < bed[20] - 10 and bed[1] < bed[20] - 4            # a 50 ms sine ramp: its first 10 ms average about 15 dB under the level it reaches
    assert np.abs((mix - voice)[:4]).max() < 1e-4                    # and nothing at all on the first samples: no click
    tail = bed[-150:]                                                # the last 1.5 s
    assert (np.diff(tail[:-3]) <= 0.2).all() and tail[-1] < tail[0] - 40, tail[[0, -1]]
    assert abs(bed[-160] - bed[-400]) < 0.5                          # and level just before the fade begins


def test_a_music_file_shorter_than_the_voice_is_looped_with_no_dip(tmp_path):
    v, m = pcm_pair(tmp_path, "noise", music_dur=12)
    out = str(tmp_path / "o.wav")
    rep = sound.bed(v, m, out)
    voice, mix = decode(v), decode(out)
    placed = mix - voice
    lv = np.array([window_db(placed, a, a + 0.5) for a in np.arange(25.0, 37.0, 0.5)])      # the alone stretch crosses a loop seam at 33 s
    assert lv.max() - lv.min() < 1.0, lv
    v_db, under = level_under(voice, mix, INNER)
    assert abs((v_db - under) - 22.0) <= 1.5
    assert abs(rep["alone_db_measured"] - 14.0) <= 1.5


def test_the_quiet_intro_and_the_fade_of_a_bank_track_leave_no_hole_in_the_bed(tmp_path):
    v = write_pcm16(tmp_path / "v.wav", burst_voice(BURSTS, 40))
    m = write_pcm16(tmp_path / "m.wav", track())                    # 5 s of near silence, 24 s of music, a fade, 1.5 s of silence: ~32 s for a 40 s voice
    out = str(tmp_path / "o.wav")
    sound.bed(v, m, out)
    placed = decode(out) - decode(v)
    assert longest_hole(placed[int(25.0 * SR):int(38.0 * SR)]) <= 0.5


@pytest.mark.parametrize("under, alone", [(30.0, 10.0), (18.0, 18.0), (40.0, 5.0)])
def test_the_levels_are_the_ones_asked_for(tmp_path, under, alone):
    v, m = pcm_pair(tmp_path, "noise")
    out = str(tmp_path / "o.wav")
    rep = sound.bed(v, m, out, under_speech_db=under, alone_db=alone)
    voice, mix = decode(v), decode(out)
    vdb, u = level_under(voice, mix, INNER)
    assert abs((vdb - u) - under) <= 1.5, (under, vdb - u)
    vdb, a = level_under(voice, mix, ALONE, ref_windows=INNER)
    assert abs((vdb - a) - alone) <= 1.5, (alone, vdb - a)
    assert rep["duck_db"] == under - alone


def test_equal_levels_mean_no_ducking_at_all(tmp_path):
    v, m = pcm_pair(tmp_path, "tone")
    out = str(tmp_path / "o.wav")
    sound.bed(v, m, out, under_speech_db=18.0, alone_db=18.0)
    bed = block_db(decode(out) - decode(v))
    assert bed[100:-200].max() - bed[100:-200].min() < 0.05          # flat from the first line to the closing fade


def test_a_bed_cannot_sit_louder_under_the_talk_than_alone(tmp_path, capsys):
    v, m = pcm_pair(tmp_path, "noise")
    with pytest.raises(SystemExit) as e:
        sound.bed(v, m, str(tmp_path / "o.wav"), under_speech_db=10.0, alone_db=14.0)
    err = capsys.readouterr().err
    assert e.value.code == 2 and one_stop_line(err) and "louder" in err, err
    assert not (tmp_path / "o.wav").exists()


def test_a_silent_music_file_stops(tmp_path, capsys):
    v = write_pcm16(tmp_path / "v.wav", burst_voice(BURSTS, 40))
    m = write_pcm16(tmp_path / "silent.wav", np.zeros((5 * SR, 2)))
    with pytest.raises(SystemExit):
        sound.bed(v, m, str(tmp_path / "o.wav"))
    err = capsys.readouterr().err
    assert one_stop_line(err) and "silent" in err and str(m) in err, err


def test_a_voice_too_short_to_put_a_bed_under_stops(tmp_path, capsys):
    v = write_pcm16(tmp_path / "v.wav", burst_voice([(0.05, 0.2)], 0.3))
    m = write_pcm16(tmp_path / "m.wav", noise_music(5))
    with pytest.raises(SystemExit):
        sound.bed(v, m, str(tmp_path / "o.wav"))
    err = capsys.readouterr().err
    assert one_stop_line(err) and "too short" in err, err


def test_a_file_that_is_not_audio_stops_with_one_line(tmp_path, capsys):
    v = write_pcm16(tmp_path / "v.wav", burst_voice(BURSTS, 40))
    junk = tmp_path / "music.wav"
    junk.write_bytes(b"this is not audio")
    with pytest.raises(SystemExit):
        sound.bed(v, str(junk), str(tmp_path / "o.wav"))
    err = capsys.readouterr().err
    assert err.startswith("STOP: ffmpeg cannot read the audio of ") and "music.wav" in err.splitlines()[0], err


@pytest.mark.xfail(strict=False, reason="engine/sound.py _decode (also load_clip, _write_stereo, downmix): the ffmpeg error tail is put into the STOP message "
                   "with its own newlines, so one failure prints two or three lines; common.stop should collapse whitespace")
def test_a_file_that_is_not_audio_is_one_stop_line(tmp_path, capsys):
    v = write_pcm16(tmp_path / "v.wav", burst_voice(BURSTS, 40))
    junk = tmp_path / "music.wav"
    junk.write_bytes(b"this is not audio")
    with pytest.raises(SystemExit):
        sound.bed(v, str(junk), str(tmp_path / "o.wav"))
    assert one_stop_line(capsys.readouterr().err)


def test_the_voice_comes_through_unchanged(tmp_path):
    """with the bed 90 dB under the voice the mix is the voice to within one 16-bit step: the bed never touches the voice's own level"""
    v, m = pcm_pair(tmp_path, "noise")
    out = str(tmp_path / "o.wav")
    sound.bed(v, m, out, under_speech_db=90.0, alone_db=90.0)
    assert np.abs(decode(out) - decode(v)).max() < 1e-4


def test_a_hot_voice_is_scaled_to_stay_under_full_scale_not_clipped(tmp_path):
    v, m = pcm_pair(tmp_path, "noise", rms=0.55)                     # peaks near 0.95 in each channel before the bed is added
    out = str(tmp_path / "o.wav")
    rep = sound.bed(v, m, out, under_speech_db=3.0, alone_db=3.0)
    mix = decode(out)
    assert rep["peak_scaled"] is True
    assert np.abs(mix).max() <= 1.0 and np.abs(mix).max() > 0.95


def test_the_output_is_a_48k_stereo_wav(made):
    r = sh(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate,channels:format=duration", "-of", "csv=p=0", made["noise"][3]])
    codec, rate, ch, dur = r.stdout.replace("\n", ",").strip(",").split(",")[:4]
    assert (codec, rate, ch) == ("pcm_s16le", "48000", "2") and abs(float(dur) - 40.0) < 0.01, r.stdout


def test_words_give_the_same_talk_as_the_voice_itself(tmp_path):
    v, m = pcm_pair(tmp_path, "noise")
    ws = [{"start": a, "end": b, "text": f"line{i}"} for i, (a, b) in enumerate(BURSTS)]
    out = str(tmp_path / "o.wav")
    rep = sound.bed(v, m, out, words=ws)
    plain = sound.bed(v, m, str(tmp_path / "p.wav"))
    assert len(rep["spans"]) == len(plain["spans"]) == 2
    assert np.allclose(rep["spans"], plain["spans"], atol=0.5), (rep["spans"], plain["spans"])
    voice, mix = decode(v), decode(out)
    vdb, u = level_under(voice, mix, INNER)
    assert abs((vdb - u) - 22.0) <= 1.5


def test_the_bed_command_line_takes_its_levels_and_its_words(tmp_path):
    v, m = pcm_pair(tmp_path, "noise")
    wj = tmp_path / "words.json"
    wj.write_text(json.dumps({"words": [{"start": a, "end": b, "text": "x"} for a, b in BURSTS]}))
    out = tmp_path / "o.wav"
    r = run_cli("bed", v, m, out, "--under-speech", 20, "--alone", 12, "--ease", 1.0, "--words", wj)
    assert r.returncode == 0 and r.stdout.startswith(f"bed {out}: voice ") and "(asked 20 and 12)" in r.stdout and "2 stretches of talk" in r.stdout, r.stdout + r.stderr
    voice, mix = decode(v), decode(str(out))
    vdb, u = level_under(voice, mix, INNER)
    assert abs((vdb - u) - 20.0) <= 1.5


def test_the_bed_command_line_stops_on_levels_that_cannot_be_met(tmp_path):
    v, m = pcm_pair(tmp_path, "noise")
    r = run_cli("bed", v, m, tmp_path / "o.wav", "--under-speech", 5, "--alone", 14)
    assert r.returncode == 2 and one_stop_line(r.stderr) and "louder" in r.stderr, r.stderr


@pytest.mark.xfail(strict=False, raises=ValueError, reason="engine/sound.py bed(): `fade[-k:] = ...` with k = int(ease_s * 48000) = 0 (an ease of 0 or less) assigns an "
                   "empty array to the whole fade and raises ValueError; the command line then prints a misleading usage STOP for `--ease 0`")
@pytest.mark.parametrize("ease", [0.0, -1.0])
def test_an_ease_of_nothing_is_a_plain_result_or_a_plain_stop_never_a_traceback(tmp_path, ease):
    v, m = pcm_pair(tmp_path, "noise")
    try:
        rep = sound.bed(v, m, str(tmp_path / "o.wav"), ease_s=ease)
    except SystemExit as e:                                         # a plain STOP is an acceptable answer
        assert e.code == 2
    else:
        assert rep["ease_s"] == ease
