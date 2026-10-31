"""The sound half of render.py, without Remotion: the music bed under the voice (22 dB under the talk, 14 dB under the voice alone, measured on
the final), effects at their times, and the final normalised to -16 LUFS at -1.5 dBTP or lower. The picture here is a plain test pattern."""
import re
import shutil

import pytest

import render_helpers as H
from render_helpers import needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.fixture(scope="module")
def long_cut(media):
    """14 seconds: a voice over the first six or so, then silence, so there is a stretch where music plays alone"""
    return H.make_cut(media / "long.mp4", 360, 640, 14.0)


def job_for(tmp_path, cut, **over):
    """the job render.py builds from a plan (no Remotion): returns (job, work folder)"""
    import render

    plan = {"cut": str(cut), "theme": H.THEME, "output": {"size": [360, 640]}, "sfx": False, **over}
    work = tmp_path / "work"
    (work / "public").mkdir(parents=True)
    return render.build(plan, tmp_path, H.THEME, work / "public"), work


def finish(tmp_path, job, work):
    """mix, normalise and check, the way main() does after the picture: returns the final file"""
    import render

    video = tmp_path / "video-only.mp4"
    H.ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=360x640:rate=30:duration={job['dur']}", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", video)
    audio = render.mix_sound(job, work)
    out = tmp_path / "final.mp4"
    render.mux(video, audio, job["dur"], out)
    render.check(out, job, audio is not None)
    return out, audio


def test_the_bed_sits_under_the_talk_and_under_the_voice_alone(tmp_path, long_cut, music, capsys):
    import sound

    job, work = job_for(tmp_path, long_cut, music={"file": str(music)})
    out, audio = finish(tmp_path, job, work)
    said = capsys.readouterr().out
    m = re.search(r"music: music.wav, ([\d.]+) dB under the talk and ([\d.]+) dB under the voice alone \(asked 22 and 14\)", said)
    assert m, said
    assert float(m.group(1)) == pytest.approx(22, abs=3) and float(m.group(2)) == pytest.approx(14, abs=3)
    # measured on the final, the way the QC gate does: the talk against the music alone
    rep = sound.bed(str(long_cut), str(music), str(tmp_path / "ref.wav"), 22.0, 14.0, 1.5, None)
    log = H.ebur_log(out)
    talk = sound.loudness(log, [(a + 0.6, b - 0.6) for a, b in rep["spans"] if b - a > 2.0])
    alone = sound.loudness(log, [w for w in rep["alone_windows"] if w[1] - w[0] >= 1.5])
    assert talk is not None and alone is not None, (rep["spans"], rep["alone_windows"])
    assert talk - alone == pytest.approx(14, abs=2.5)
    i, tp = H.loudness(out)
    assert i == pytest.approx(-16, abs=1) and tp <= -1.5


def test_the_bed_is_never_in_the_pauses_between_lines_only_around_the_talk(tmp_path, long_cut, music):
    """one stretch of talk holds the bed steady: the level between the first and the last word does not climb back up"""
    import numpy as np
    import sound

    job, work = job_for(tmp_path, long_cut, music={"file": str(music)})
    import render

    render.mix_sound(job, work)
    rep = sound.bed(str(long_cut), str(music), str(tmp_path / "ref.wav"), 22.0, 14.0, 1.5, None)
    talk = [s for s in rep["spans"] if s[1] - s[0] > 2]
    assert len(talk) == 1
    a, b = talk[0]
    Y = render.read_audio(work / "bed.wav")
    V = render.read_audio(long_cut)
    n = min(len(Y), len(V))
    bed = (Y[:n] - V[:n]).mean(axis=1)  # what the mix added to the voice: the bed alone
    h = 4800  # 100 ms
    env = [20 * np.log10(np.sqrt((bed[i : i + h] ** 2).mean()) + 1e-9) for i in range(int((a + 0.5) * 48000), int((b - 0.5) * 48000), h)]
    assert max(env) - min(env) < 5.0  # steady through the talk: the bed climbing back up in a pause would be 8 dB, the track's own tremolo is 2.5


def test_a_hot_voice_is_limited_to_the_true_peak_and_still_lands_on_the_loudness(tmp_path):
    """steady noise with a loud 5 ms burst every two seconds: the gain to -16 LUFS would put the bursts over the peak target, so they are limited"""
    import render

    wav = tmp_path / "hot.wav"
    H.ffmpeg("-f", "lavfi", "-i", "anoisesrc=a=0.15:d=8:r=48000", "-f", "lavfi", "-i", "aevalsrc='if(lt(mod(t,2),0.005),0.95*sin(2*PI*1000*t),0)':d=8:s=48000",
             "-filter_complex", "[0][1]amix=inputs=2:normalize=0,aformat=channel_layouts=stereo", wav)
    cut = tmp_path / "hot.mp4"
    H.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=8", "-i", wav, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", cut)
    i_in, tp_in = render.loudness_of(cut)
    assert tp_in + (-16 - i_in) > -2  # the plain gain to -16 LUFS would pass the peak target
    job, work = job_for(tmp_path, cut)
    out, _ = finish(tmp_path, job, work)
    i, tp = H.loudness(out)
    assert i == pytest.approx(-16, abs=0.5) and tp <= -1.5


def test_a_sound_the_peaks_keep_from_the_target_stops_plainly(tmp_path):
    """loudness made of a few loud clicks cannot be raised without passing the peak target: the render says so instead of shipping it quiet"""
    import render

    wav = tmp_path / "clicks.wav"
    H.ffmpeg("-f", "lavfi", "-i", "aevalsrc='if(lt(mod(t,0.5),0.001),0.95,0)':d=6:s=48000", "-af", "aformat=channel_layouts=stereo", wav)
    cut = tmp_path / "clicks.mp4"
    H.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=6", "-i", wav, "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", cut)
    job, work = job_for(tmp_path, cut)
    with pytest.raises(SystemExit):
        finish(tmp_path, job, work)


def test_a_cut_with_no_sound_gives_a_final_with_none(tmp_path, capsys):
    cut = tmp_path / "mute.mp4"
    H.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=3", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", cut)
    job, work = job_for(tmp_path, cut)
    out, audio = finish(tmp_path, job, work)
    assert audio is None and not H.probe(out)["audio"] and "no sound to mix" in capsys.readouterr().out


def test_effects_are_laid_at_their_times_and_the_family_names_come_from_the_kit(tmp_path, long_cut, click, capsys):
    import render

    job, work = job_for(tmp_path, long_cut, sfx=[{"file": str(click), "at_s": 9.0, "db": -14}, {"family": "pop", "at_s": 11.0}])
    audio = render.mix_sound(job, work)
    assert audio[:4] == ["-f", "f32le", "-ar", "48000"]
    assert (work / "mix.f32").stat().st_size / 8 / 48000 == pytest.approx(14.0, abs=0.1)
    assert "sfx: 2 effects placed" in capsys.readouterr().out
    import numpy as np

    mix = np.fromfile(work / "mix.f32", dtype=np.float32).reshape(-1, 2)
    voice = render.read_audio(long_cut)[: len(mix)]
    added = np.abs(mix - voice[: len(mix)]).max(axis=1)
    assert added[int(9.0 * 48000) : int(9.2 * 48000)].max() > 1e-3 and added[int(10.0 * 48000) : int(10.8 * 48000)].max() < 1e-6  # lands where asked, nowhere else
    assert added[int(11.0 * 48000) : int(11.3 * 48000)].max() > 1e-3


def test_a_family_that_is_not_in_the_kit_stops_plainly(tmp_path, long_cut):
    import render

    job, work = job_for(tmp_path, long_cut, sfx=[{"family": "kazoo", "at_s": 1.0}])
    with pytest.raises(SystemExit):
        render.mix_sound(job, work)


def test_automatic_effects_are_on_unless_the_plan_says_otherwise(tmp_path, long_cut, capsys):
    import render

    gfx = [{"component": "LowerThird", "start_s": 1.0, "end_s": 3.0, "props": {"name": "A"}}, {"component": "TitleCard", "start_s": 9.0, "end_s": 12.0, "props": {"text": "x"}}]
    job, work = job_for(tmp_path, long_cut, graphics=gfx, sfx="auto")
    assert job["sfx"]["mode"] == "auto"
    render.mix_sound(job, work)
    assert "sfx: " in capsys.readouterr().out and (work / "mix.f32").exists()
    other = tmp_path / "b"
    other.mkdir()
    job_off, _ = job_for(other, long_cut, graphics=gfx, sfx=False)
    assert job_off["sfx"]["mode"] == "off"


def test_a_piece_with_music_and_no_voice_loops_the_track_and_fades(tmp_path, music, clip):
    """a montage with no voiceover: the track alone, looped to the length, faded in and out, normalised by the final step"""
    import numpy as np
    import render

    plan = {"montage": {"clips": [{"src": str(clip), "at": 0, "dur": 2.5}, {"src": str(clip), "at": 2.5, "dur": 2.5}]}, "theme": H.THEME, "output": {"size": [360, 640]},
            "music": {"file": str(music)}, "sfx": False}
    work = tmp_path / "work"
    (work / "public").mkdir(parents=True)
    job = render.build(plan, tmp_path, H.THEME, work / "public")
    assert job["comp"] == "Montage" and not job["has_voice"] and job["dur"] == 5.0
    audio = render.mix_sound(job, work)
    Y = render.read_audio(work / "bed.wav")
    assert len(Y) == 5 * 48000 and np.abs(Y[:48]).max() < 0.01 and np.abs(Y[-48:]).max() < 0.01 and np.abs(Y[48000:96000]).max() > 0.01
    assert audio == ["-i", str(work / "bed.wav")]


def test_a_montages_voiceover_lines_are_laid_at_their_times(tmp_path, clip):
    import numpy as np
    import render

    vo = H.make_voice(tmp_path / "vo.wav", 2.0)
    plan = {"montage": {"clips": [{"src": str(clip), "at": 0, "dur": 3.0}], "vo": [{"src": str(vo), "at": 1.0}]}, "theme": H.THEME, "output": {"size": [360, 640]}, "sfx": False}
    work = tmp_path / "work"
    (work / "public").mkdir(parents=True)
    job = render.build(plan, tmp_path, H.THEME, work / "public")
    assert job["has_voice"]
    voice = render.voice_track(job, work)
    Y = render.read_audio(voice)
    assert len(Y) == 3 * 48000 and np.abs(Y[: 47000]).max() < 1e-4 and np.abs(Y[50000:100000]).max() > 0.01  # silence until 1 s, then the line
