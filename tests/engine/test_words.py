"""words.py, the local word engine: every spoken word of a take with its own start and end, on this Mac.

Three kinds of test. The pure ones (flagging, reading the aligned json, the prompt rule, the transcript pack) need no tool at all. The plumbing ones
run `hear` against FAKE tools (a shell script standing in for whisper-cli and another for the word-timing python) to see exactly what the engine
asks of them and what it makes of their answers. The real ones (marker `speech`) run the real Whisper model and the real aligner on a macOS `say`
voice and skip with one plain reason when this machine has neither."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

import numpy as np
import pytest

import cut
import support
import words
from conftest import HAVE_SPEECH, VENV_PYTHON, speech
from helpers import speak, talking_video
from helpers_sound import write_pcm16

ENGINE = words.HERE
WORDS_PY = os.path.join(ENGINE, "words.py")
WX_ALIGN = os.path.join(ENGINE, "wx_align.py")

# an aligned json as the aligner writes it: one word it could not stamp ("3", a number) has no start or end
WX_SMALL = {"segments": [{"start": 0.0, "end": 1.1, "text": "So the first thing",
                          "words": [{"word": "So", "start": 0.02, "end": 0.40, "score": 0.9},
                                    {"word": "the", "start": 0.48, "end": 0.64, "score": 0.9},
                                    {"word": "first", "start": 0.64, "end": 0.88, "score": 0.9},
                                    {"word": "thing", "start": 0.88, "end": 1.04, "score": 0.9}]}]}


def one_stop_line(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return len(lines) == 1 and lines[0].startswith("STOP: ")


def run_py(script, *args, env=None):
    return subprocess.run([sys.executable, script, *[str(a) for a in args]], capture_output=True, text=True, env=env)


def W(*rows):
    return [{"text": t, "start": s, "end": e} for t, s, e in rows]


# --- one filler list, and what flag() makes of it --------------------------------------------------------------------------------------

def test_the_word_engine_uses_the_cutters_one_filler_list():
    assert words.FILLERS is cut.FILLERS
    assert words.load_words is cut.load_words and words.DEFAULT_PROMPT is cut.DEFAULT_PROMPT and words.prompt_for is cut.prompt_for


@pytest.mark.parametrize("filler", ["um", "uh", "uhm", "erm", "uhh", "er", "umm", "hmm", "mm", "ah"])
def test_every_filler_the_cutter_strikes_is_flagged_whatever_its_case_or_punctuation(filler):
    for text in (filler, filler.capitalize() + ",", filler.upper() + ".", filler + "?"):
        assert words.flag(W((text, 0.0, 0.2)))[0]["filler"] is True, text


@pytest.mark.parametrize("word", ["number", "umbrella", "errand", "earth", "ahead", "Yeah,", "humble", "terminal", "I"])
def test_a_word_that_only_contains_a_filler_is_not_one(word):
    assert words.flag(W((word, 0.0, 0.3)))[0]["filler"] is False


def test_flag_marks_fillers_doubles_caps():
    ws = W(("So", 0, .4), ("um", .4, .5), ("the", .5, .6), ("the", .6, .7), ("Claude", .8, 1.1))
    f = words.flag(ws)
    assert f[1]["filler"] is True and f[0]["filler"] is False
    assert f[3]["double"] is True and f[2]["double"] is False
    assert f[4]["cap"] is True and f[2]["cap"] is False


def test_a_double_is_the_same_word_again_within_half_a_second():
    near = words.flag(W(("is", 0.0, 0.2), ("is,", 0.4, 0.6)))             # 0.2 s of room between them
    far = words.flag(W(("is", 0.0, 0.2), ("is", 0.9, 1.1)))               # 0.7 s: well past the half second
    other = words.flag(W(("is", 0.0, 0.2), ("it", 0.25, 0.4)))
    assert near[1]["double"] is True and far[1]["double"] is False and other[1]["double"] is False
    assert words.flag(W(("um", 0.0, 0.2), ("um", 0.3, 0.5)))[1]["double"] is True and words.flag(W(("...", 0.0, 0.2), ("...", 0.3, 0.5)))[1]["double"] is False


def test_a_capital_that_opens_a_sentence_is_not_a_cap_but_a_name_or_an_I_is():
    f = words.flag(W(("Hello", 0, .3), ("there.", .3, .6), ("Then", .8, 1.0), ("I", 1.0, 1.1), ("met", 1.1, 1.3), ("Ana?", 1.3, 1.6), ("Right!", 1.7, 1.9),
                     ("Sam", 2.0, 2.3), ("and", 2.3, 2.4), ("Lee", 2.4, 2.7)))
    assert [w["cap"] for w in f] == [False, False, False, True, False, True, False, False, False, True]


def test_flag_keeps_every_other_key_and_changes_none_of_its_input():
    ws = [{"text": "So", "start": 0, "end": .4}, {"text": "3", "start": .4, "end": .5, "unaligned": True}]
    before = json.loads(json.dumps(ws))
    f = words.flag(ws)
    assert f[1]["unaligned"] is True and "unaligned" not in f[0]
    assert ws == before and all(a is not b for a, b in zip(f, ws))


# --- reading the aligned json ------------------------------------------------------------------------------------------------------------

def test_wx_words_reads_the_aligner_output_in_the_words_json_shape():
    assert words.wx_words(WX_SMALL) == [{"text": "So", "start": 0.02, "end": 0.40}, {"text": "the", "start": 0.48, "end": 0.64},
                                        {"text": "first", "start": 0.64, "end": 0.88}, {"text": "thing", "start": 0.88, "end": 1.04}]


def test_wx_words_strips_the_text_rounds_the_stamps_and_skips_what_is_empty():
    doc = {"segments": [{"words": [{"word": " Hello, ", "start": 0.123456, "end": 0.7}, {"word": "   ", "start": 0.8, "end": 0.9}, {"text": "there", "start": 1, "end": 2}]},
                        {"words": [{"word": "again", "start": 2.5, "end": 3.0004}]}, {}]}
    got = words.wx_words(doc)
    assert [w["text"] for w in got] == ["Hello,", "there", "again"]
    assert got[0]["start"] == 0.123 and got[1] == {"text": "there", "start": 1.0, "end": 2.0} and got[2]["end"] == 3.0


def test_an_unaligned_word_is_kept_between_its_neighbours_and_marked():
    """a number or a symbol has no characters the aligner can place: it keeps its place in the transcript, between the words either side of it"""
    doc = {"segments": [{"words": [{"word": "I", "start": 0.1, "end": 0.3}, {"word": "have", "start": 0.4, "end": 0.7}, {"word": "3"},
                                   {"word": "kids", "start": 1.2, "end": 1.6}, {"word": "$5"}]}]}
    got = words.wx_words(doc)
    assert [w["text"] for w in got] == ["I", "have", "3", "kids", "$5"]
    assert [w.get("unaligned") for w in got] == [None, None, True, None, True]
    assert got[2]["start"] == pytest.approx(0.72) and got[2]["end"] == pytest.approx(1.18)         # from 20 ms after "have" to 20 ms before "kids"
    assert got[4]["start"] == pytest.approx(1.62) and got[4]["end"] == pytest.approx(1.98)           # nothing after it: 0.4 s of room, less 20 ms at each end
    assert all(w["start"] < w["end"] for w in got)
    assert [w["start"] for w in got] == sorted(w["start"] for w in got)


def test_an_unaligned_first_word_starts_just_after_zero_and_a_take_of_numbers_alone_is_kept():
    first = words.wx_words({"segments": [{"words": [{"word": "7"}, {"word": "up", "start": 0.5, "end": 0.8}]}]})
    assert first[0]["text"] == "7" and first[0]["start"] == pytest.approx(0.02) and first[0]["end"] == pytest.approx(0.48) and first[0]["unaligned"] is True
    alone = words.wx_words({"segments": [{"words": [{"word": "1"}, {"word": "2"}, {"word": "3"}]}]})
    assert [w["text"] for w in alone] == ["1", "2", "3"] and all(w["unaligned"] and w["start"] < w["end"] for w in alone)
    assert [w["start"] for w in alone] == sorted(w["start"] for w in alone)


def test_a_word_with_a_start_but_no_end_is_placed_as_unaligned():
    got = words.wx_words({"segments": [{"words": [{"word": "a", "start": 0.0, "end": 0.2}, {"word": "b", "start": 0.5}, {"word": "c", "start": 1.0, "end": 1.2}]}]})
    assert got[1]["unaligned"] is True and 0.2 <= got[1]["start"] < got[1]["end"] <= 1.0


def test_wx_words_is_fast_on_a_long_take():
    rows = [{"word": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.2} if i % 50 else {"word": str(i)} for i in range(20000)]
    began = time.perf_counter()
    got = words.wx_words({"segments": [{"words": rows}]})
    assert time.perf_counter() - began < 2 and len(got) == 20000 and sum(1 for w in got if w.get("unaligned")) == 400


# --- the prompt rule ---------------------------------------------------------------------------------------------------------------------

def test_english_gets_the_default_prompt_and_other_languages_get_none():
    assert words.prompt_for("en", {}) == words.DEFAULT_PROMPT != ""
    for lang in ("es", "fr", "de", "auto", "ja"):
        assert words.prompt_for(lang, {}) == "", lang


def test_a_config_prompt_overrides_for_every_language_and_an_empty_one_turns_it_off():
    assert words.prompt_for("en", {"whisper_prompt": "Custom words, here."}) == "Custom words, here."
    assert words.prompt_for("es", {"whisper_prompt": "Hola, amigos."}) == "Hola, amigos."
    assert words.prompt_for("en", {"whisper_prompt": ""}) == ""
    assert words.prompt_for("en", {"whisper_prompt": None}) == words.DEFAULT_PROMPT, "null in the config means unset"
    assert words.prompt_for("en", {"other": 1}) == words.DEFAULT_PROMPT


def test_the_prompt_comes_from_the_config_file_when_none_is_passed(monkeypatch):
    monkeypatch.setattr(support, "config", lambda: {"whisper_prompt": "From the config."})
    assert words.prompt_for("en") == "From the config."
    monkeypatch.setattr(support, "config", lambda: {})
    assert words.prompt_for("en") == words.DEFAULT_PROMPT and words.prompt_for("es") == ""


def test_the_default_prompt_is_what_makes_whisper_punctuate_and_keep_false_starts():
    """a short punctuated sentence with a filler and a repeated word in it: the model carries that style into the take"""
    p = words.DEFAULT_PROMPT
    assert p.rstrip().endswith((".", "?", "!")) and p.count(".") >= 2
    assert {"um", "uh"} <= {t.strip(",.").lower() for t in p.split()}
    toks = [t.strip(",.").lower() for t in p.split()]
    assert any(a == b for a, b in zip(toks, toks[1:])), "a doubled word"


# --- the plumbing, against fake tools ----------------------------------------------------------------------------------------------------

@pytest.fixture
def fake(tmp_path, monkeypatch):
    """a shell script for whisper-cli and another for the word-timing python, first on PATH and in the config, that record the arguments they get and
    answer with canned files. Returns a namespace to set the canned answers and read what was asked."""
    class Fake:
        pass
    f = Fake()
    f.dir = tmp_path / "fake bin"
    f.dir.mkdir()
    f.whisper_args, f.align_args, f.json = tmp_path / "whisper.args", tmp_path / "align.args", tmp_path / "canned-whisper.json"
    f.wx = tmp_path / "canned-aligned.json"
    f.json.write_text(json.dumps({"result": {"language": "en"}, "transcription": [{"offsets": {"from": 0, "to": 3000}, "text": " Hello there."}]}))
    f.wx.write_text(json.dumps(WX_SMALL))
    f.model = tmp_path / "model.bin"
    f.model.write_text("not a model")
    f.whisper_rc, f.align_rc = 0, 0
    # whisper-cli: record the arguments, write <of>.json and <of>.srt from the canned file, exit with FAKE_WHISPER_RC
    (f.dir / "whisper-cli").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_WHISPER_ARGS"\nwhile [ $# -gt 0 ]; do case "$1" in -of) OF="$2"; shift ;; esac; shift; done\n'
                                      '[ "$FAKE_WHISPER_RC" = 0 ] && [ -n "$OF" ] && cp "$FAKE_WHISPER_JSON" "$OF.json" && : > "$OF.srt"\nexit "$FAKE_WHISPER_RC"\n')
    # the python: record the arguments, write the aligned json to the fourth argument (the script's own path is the first), exit with FAKE_ALIGN_RC
    (f.dir / "fake-python").write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_ALIGN_ARGS"\n[ "$FAKE_ALIGN_RC" = 0 ] && cp "$FAKE_ALIGN_JSON" "$4"\nexit "$FAKE_ALIGN_RC"\n')
    for n in ("whisper-cli", "fake-python"):
        (f.dir / n).chmod((f.dir / n).stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(f.dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("WHISPER_MODEL", str(f.model))
    monkeypatch.setenv("CUTROOM_VENV_PYTHON", str(f.dir / "fake-python"))
    monkeypatch.setenv("FAKE_WHISPER_ARGS", str(f.whisper_args))
    monkeypatch.setenv("FAKE_ALIGN_ARGS", str(f.align_args))
    monkeypatch.setenv("FAKE_WHISPER_JSON", str(f.json))
    monkeypatch.setenv("FAKE_ALIGN_JSON", str(f.wx))
    monkeypatch.setenv("FAKE_WHISPER_RC", "0")
    monkeypatch.setenv("FAKE_ALIGN_RC", "0")
    f.setenv = monkeypatch.setenv
    return f


def argv(path):
    """the arguments a fake tool was called with, in order (it writes one per line)"""
    return path.read_text().split("\n")[:-1]


def tone_wav(path, rate=16000, ch=1, secs=1.0):
    t = np.arange(int(rate * secs)) / rate
    y = 0.1 * np.sin(2 * np.pi * 220 * t) + np.random.default_rng(1).standard_normal(len(t)) * 0.001
    return write_pcm16(path, y if ch == 1 else np.stack([y, y], axis=1), sr=rate)


def test_hear_runs_whisper_then_the_aligner_and_writes_the_words_contract(fake, tmp_path):
    wav = tone_wav(tmp_path / "take.wav")
    out = tmp_path / "words.json"
    doc = words.hear(wav, out=str(out), work=str(tmp_path / "work dir"), logs=str(tmp_path / "logs"))
    assert doc["source"] == "whisper" and doc["language"] == "en" and doc["transcript"] == "So the first thing"
    assert [w["text"] for w in doc["words"]] == ["So", "the", "first", "thing"] and doc["words"][0] == {"text": "So", "start": 0.02, "end": 0.4, "filler": False, "double": False, "cap": False}
    assert json.loads(out.read_text()) == doc and out.read_text().endswith("\n")
    work = tmp_path / "work dir"
    assert sorted(os.listdir(work)) == ["take.json", "take.srt", "take.wx.json"]                  # a 16 kHz mono wav is used where it is: nothing copied
    assert sorted(os.listdir(tmp_path / "logs")) == ["take.align.log", "take.whisper.log"]


def test_whisper_is_asked_for_english_with_the_prompt_and_the_aligner_for_the_same_language(fake, tmp_path):
    wav = tone_wav(tmp_path / "take.wav")
    work = tmp_path / "w"
    words.hear(wav, work=str(work))
    assert argv(fake.whisper_args) == ["-m", str(fake.model), "-f", wav, "-l", "en", "--prompt", words.DEFAULT_PROMPT, "-osrt", "-oj", "-of", str(work / "take")]
    assert argv(fake.align_args) == [os.path.join(words.HERE, "wx_align.py"), wav, str(work / "take.json"), str(work / "take.wx.json"), "--language", "en", "--device", "cpu"]


@pytest.mark.parametrize("language, prompted", [("en", True), ("es", False), ("auto", False)])
def test_the_prompt_goes_to_whisper_in_english_only(fake, tmp_path, language, prompted):
    wav = tone_wav(tmp_path / "take.wav")
    words.hear(wav, language=language, work=str(tmp_path / "w"))
    a = argv(fake.whisper_args)
    assert a[a.index("-l") + 1] == language and ("--prompt" in a) is prompted, a


def test_a_config_prompt_and_the_config_language_and_device_are_what_whisper_and_the_aligner_get(fake, tmp_path, monkeypatch):
    cfg = {"whisper_language": "es", "whisper_prompt": "Hola, amigos.", "align_device": "mps"}
    monkeypatch.setattr(support, "config", lambda: dict(cfg))
    wav = tone_wav(tmp_path / "take.wav")
    words.hear(wav, work=str(tmp_path / "w"))
    a = argv(fake.whisper_args)
    assert a[a.index("-l") + 1] == "es" and a[a.index("--prompt") + 1] == "Hola, amigos." and a[a.index("--prompt") + 2] == "-osrt", a
    assert argv(fake.align_args)[-4:] == ["--language", "es", "--device", "mps"]


def test_auto_aligns_in_the_language_whisper_heard(fake, tmp_path):
    fake.json.write_text(json.dumps({"result": {"language": "fr"}, "transcription": [{"offsets": {"from": 0, "to": 1000}, "text": " Bonjour."}]}))
    doc = words.hear(tone_wav(tmp_path / "take.wav"), language="auto", work=str(tmp_path / "w"))
    assert doc["language"] == "fr" and argv(fake.align_args)[-4:] == ["--language", "fr", "--device", "cpu"]
    fake.json.write_text(json.dumps({"transcription": []}))                       # a whisper json that does not say: English
    doc = words.hear(tone_wav(tmp_path / "other.wav"), language="auto", work=str(tmp_path / "w2"))
    assert doc["language"] == "en"


def test_a_16k_mono_wav_is_heard_where_it_is_and_anything_else_is_converted_first(fake, tmp_path):
    mono16 = tone_wav(tmp_path / "ok.wav")
    words.hear(mono16, work=str(tmp_path / "w1"))
    a = argv(fake.whisper_args)
    assert a[a.index("-f") + 1] == mono16
    for name, kw in (("stereo.wav", {"ch": 2}), ("rate48.wav", {"rate": 48000})):
        src = tone_wav(tmp_path / name, **kw)
        work = tmp_path / ("w-" + name)
        words.hear(src, work=str(work))
        heard = argv(fake.whisper_args)
        assert heard[heard.index("-f") + 1] == str(work / name) and src not in heard, (name, heard)
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0", str(work / name)], capture_output=True, text=True)
        assert r.stdout.strip() == "16000,1", r.stdout


def test_a_video_is_converted_once_and_its_wav_is_reused(fake, tmp_path):
    v = talking_video(str(tmp_path / "clip.mp4"), "Hello there.", size="320x240", pad_s=0.2) if shutil.which("say") else None
    if v is None:
        pytest.skip("needs macOS `say` to make a talking clip")
    work = tmp_path / "w"
    words.hear(v, work=str(work))
    wav = work / "clip.wav"
    a = argv(fake.whisper_args)
    assert wav.exists() and a[a.index("-f") + 1] == str(wav)
    first = wav.stat().st_mtime_ns
    words.hear(v, work=str(work))
    assert wav.stat().st_mtime_ns == first, "the converted wav was made again"


def test_a_second_call_over_the_same_work_folder_does_not_run_whisper_or_the_aligner_again(fake, tmp_path):
    wav = tone_wav(tmp_path / "take.wav")
    work = tmp_path / "w"
    first = words.hear(wav, out=str(tmp_path / "a.json"), work=str(work))
    fake.whisper_args.unlink()
    fake.align_args.unlink()
    second = words.hear(wav, out=str(tmp_path / "b.json"), work=str(work))
    assert second == first and not fake.whisper_args.exists() and not fake.align_args.exists()
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_audio_newer_than_the_files_made_from_it_is_heard_again(fake, tmp_path):
    wav = tone_wav(tmp_path / "take.wav")
    work = tmp_path / "w"
    words.hear(wav, work=str(work))
    fake.whisper_args.unlink()
    later = time.time() + 5
    os.utime(wav, (later, later))
    words.hear(wav, work=str(work))
    assert fake.whisper_args.exists(), "the audio changed and nothing was heard again"


def test_without_a_work_folder_nothing_is_left_behind(fake, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    doc = words.hear(tone_wav(tmp_path / "take.wav"))
    assert doc["words"] and os.listdir(scratch) == []
    fake.setenv("FAKE_WHISPER_RC", "1")
    with pytest.raises(SystemExit):
        words.hear(tone_wav(tmp_path / "again.wav"))
    assert os.listdir(scratch) == [], "a failed run leaves its temporary folder behind"


def test_transcribe_gives_the_flagged_words_and_nothing_else(fake, tmp_path):
    fake.wx.write_text(json.dumps({"segments": [{"words": [{"word": "Um,", "start": 0.1, "end": 0.3}, {"word": "yes", "start": 0.4, "end": 0.7}]}]}))
    got = words.transcribe(tone_wav(tmp_path / "x.wav"))
    assert [(w["text"], w["filler"]) for w in got] == [("Um,", True), ("yes", False)]


def test_a_number_the_aligner_could_not_stamp_stays_in_the_words_json(fake, tmp_path):
    fake.wx.write_text(json.dumps({"segments": [{"words": [{"word": "I", "start": 0.1, "end": 0.2}, {"word": "have", "start": 0.3, "end": 0.6}, {"word": "3"},
                                                           {"word": "kids.", "start": 1.0, "end": 1.4}]}]}))
    doc = words.hear(tone_wav(tmp_path / "x.wav"), work=str(tmp_path / "w"))
    assert doc["transcript"] == "I have 3 kids." and [bool(w.get("unaligned")) for w in doc["words"]] == [False, False, True, False]
    assert len(cut.sentence_spans(doc["words"])) == 1 and "3" in cut.sentence_spans(doc["words"])[0]["match"]


# --- failures are one plain STOP line ---------------------------------------------------------------------------------------------------

def test_whisper_failing_is_one_stop_line_that_names_the_file(fake, tmp_path, capsys):
    fake.setenv("FAKE_WHISPER_RC", "1")
    with pytest.raises(SystemExit) as e:
        words.hear(tone_wav(tmp_path / "take.wav"), work=str(tmp_path / "w"))
    err = capsys.readouterr().err
    assert e.value.code == 2 and one_stop_line(err) and "whisper-cli failed on take.wav" in err, err


def test_the_aligner_failing_is_one_stop_line_with_its_log(fake, tmp_path, capsys):
    fake.setenv("FAKE_ALIGN_RC", "1")
    with pytest.raises(SystemExit) as e:
        words.hear(tone_wav(tmp_path / "take.wav"), work=str(tmp_path / "w"), logs=str(tmp_path / "logs"))
    err = capsys.readouterr().err
    assert e.value.code == 2 and one_stop_line(err) and "the word aligner failed on take.wav" in err and str(tmp_path / "logs" / "take.align.log") in err, err


def test_a_take_the_aligner_found_no_words_in_stops(fake, tmp_path, capsys):
    fake.wx.write_text(json.dumps({"segments": [{"words": []}]}))
    with pytest.raises(SystemExit):
        words.hear(tone_wav(tmp_path / "take.wav"), work=str(tmp_path / "w"))
    err = capsys.readouterr().err
    assert one_stop_line(err) and "no words were found in take.wav" in err, err


def test_a_missing_file_stops_before_anything_is_run(fake, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        words.hear(str(tmp_path / "gone.wav"))
    err = capsys.readouterr().err
    assert e.value.code == 2 and one_stop_line(err) and "no such file" in err and "gone.wav" in err and not fake.whisper_args.exists(), err
    with pytest.raises(SystemExit):
        words.transcribe(str(tmp_path / "gone.wav"))
    assert one_stop_line(capsys.readouterr().err)


def test_a_picture_with_no_sound_stops_with_the_downmix_line(fake, tmp_path, capsys):
    from helpers import video
    v = video(tmp_path / "silent.mp4", audio=False)
    with pytest.raises(SystemExit):
        words.hear(v, work=str(tmp_path / "w"))
    err = capsys.readouterr().err
    assert one_stop_line(err) and "no audio stream" in err and not fake.whisper_args.exists(), err


def test_a_missing_whisper_cli_says_how_to_get_it(fake, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(SystemExit):
        words.hear(tone_wav(tmp_path / "take.wav"))
    err = capsys.readouterr().err
    assert one_stop_line(err) and "whisper-cli is not installed" in err and "install.sh" in err, err


# --- the command line contract ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("args", [(), ("only-one.wav",), ("--out",), ("--pack",), ("--pack", "name", "dir")])
def test_a_bad_command_line_is_one_stop_line_and_exit_2(args):
    r = run_py(WORDS_PY, *args)
    assert r.returncode == 2 and one_stop_line(r.stderr) and "Traceback" not in r.stderr, (args, r.stderr)


def test_the_aligner_script_with_no_arguments_is_one_stop_line_and_exit_2():
    r = run_py(WX_ALIGN)
    assert r.returncode == 2 and one_stop_line(r.stderr) and "usage: wx_align.py" in r.stderr, r.stderr


def test_a_missing_model_is_one_stop_line_that_names_it(tmp_path):
    env = dict(os.environ, WHISPER_MODEL=str(tmp_path / "no-such-model.bin"))
    r = run_py(WORDS_PY, tmp_path / "x.wav", "--out", tmp_path / "o.json", env=env)
    assert r.returncode == 2 and one_stop_line(r.stderr), r.stderr
    assert "the Whisper model is not at " + str(tmp_path / "no-such-model.bin") in r.stderr and "install.sh" in r.stderr, r.stderr


def test_a_missing_word_timing_python_is_one_stop_line_that_names_it(fake, tmp_path):
    env = dict(os.environ, CUTROOM_VENV_PYTHON=str(tmp_path / "no-venv" / "bin" / "python"))
    r = run_py(WORDS_PY, tmp_path / "x.wav", "--out", tmp_path / "o.json", env=env)
    assert r.returncode == 2 and one_stop_line(r.stderr), r.stderr
    assert "the word-timing python is not at " + str(tmp_path / "no-venv" / "bin" / "python") in r.stderr, r.stderr


def test_a_missing_input_file_from_the_command_line(fake, tmp_path):
    r = run_py(WORDS_PY, tmp_path / "gone.wav", "--out", tmp_path / "o.json")
    assert r.returncode == 2 and one_stop_line(r.stderr) and "no such file" in r.stderr, r.stderr


def test_the_command_line_with_fake_tools_prints_the_summary_and_writes_the_file(fake, tmp_path):
    fake.wx.write_text(json.dumps({"segments": [{"words": [{"word": "So", "start": 0.1, "end": 0.2}, {"word": "um,", "start": 0.3, "end": 0.4},
                                                           {"word": "is", "start": 0.5, "end": 0.6}, {"word": "is", "start": 0.7, "end": 0.8}]}]}))
    out = tmp_path / "words.json"
    r = run_py(WORDS_PY, tone_wav(tmp_path / "take.wav"), "--out", out, "--work", tmp_path / "work", "--logs", tmp_path / "logs", "--device", "cpu")
    assert r.returncode == 0 and r.stdout.strip() == f"4 words, 1 fillers, 1 doubles (en) -> {out}" and r.stderr == "", r.stdout + r.stderr
    assert json.loads(out.read_text())["words"][1]["filler"] is True


def test_the_aligner_script_without_whisperx_is_one_stop_line_that_names_its_python(tmp_path):
    """wx_align.py under a python with no whisperx: one STOP line that names the python and says what to do"""
    import importlib.util
    if importlib.util.find_spec("whisperx") is not None:
        pytest.skip("this python has whisperx")
    r = run_py(WX_ALIGN, tmp_path / "a.wav", tmp_path / "b.json", tmp_path / "o.json")
    assert r.returncode == 2 and one_stop_line(r.stderr) and "whisperx is not installed under " + sys.executable in r.stderr and "install.sh" in r.stderr, r.stderr


# --- the transcript pack ------------------------------------------------------------------------------------------------------------------

def pack_files(d, segs, wx_words_, name="take", secs=4.0):
    """the three files a pack reads for a prepared take: whisper's json, the aligned json and a wav with two pauses of 0.4 s or more in it"""
    d.mkdir(exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({"transcription": [{"offsets": {"from": int(a * 1000), "to": int(b * 1000)}, "text": t} for a, b, t in segs]}))
    (d / f"{name}.wx.json").write_text(json.dumps({"segments": [{"words": wx_words_}]}))
    t = np.arange(int(16000 * secs)) / 16000
    y = 0.2 * np.sin(2 * np.pi * 220 * t) + np.random.default_rng(2).standard_normal(len(t)) * 0.0003
    y[(t >= 1.0) & (t < 1.6)] = np.random.default_rng(3).standard_normal(int(((t >= 1.0) & (t < 1.6)).sum())) * 0.0003      # 0.6 s of room
    y[(t >= 2.6) & (t < 3.2)] = np.random.default_rng(4).standard_normal(int(((t >= 2.6) & (t < 3.2)).sum())) * 0.0003      # and another
    write_pcm16(d / f"{name}.wav", y, sr=16000)


def test_the_pack_lists_the_segments_the_word_stream_and_the_counts(tmp_path):
    ws = [{"word": "So", "start": 0.1, "end": 0.3}, {"word": "um,", "start": 0.4, "end": 0.5}, {"word": "the", "start": 1.7, "end": 1.9},
          {"word": "uh", "start": 2.0, "end": 2.1}, {"word": "end.", "start": 3.3, "end": 3.9}]
    pack_files(tmp_path / "wx", [(0.0, 1.2, " So, um,"), (1.6, 3.9, " the, uh, end."), (3.9, 4.0, "  ")], ws)
    line = words.transcript_pack("take", str(tmp_path / "wx"), str(tmp_path / "take.txt"))
    assert line == f"take: 5 words, 2 fillers, 2 pauses -> {tmp_path / 'take.txt'}"
    text = (tmp_path / "take.txt").read_text()
    assert text.startswith("# take: 5 words, 3.9s\n")
    assert "[  0.00-  1.20] So, um," in text and "[  1.60-  3.90] the, uh, end." in text and text.count("] ") == 2, "an empty segment is not listed"
    assert "[0.1]So um, the uh end." in text
    assert text.index("## SEGMENTS") < text.index("## WORD STREAM") < text.index("## COUNTS")
    counts = text.split("## COUNTS\n")[1].splitlines()
    assert counts[0] == "fillers: 2 um@0.4 uh@2.0"
    assert counts[1].startswith("measured pauses >=0.40s: 2 starting at ")
    starts = [float(x) for x in counts[1].split("starting at ")[1].split(", ")]
    assert starts[0] == pytest.approx(1.0, abs=0.05) and starts[1] == pytest.approx(2.6, abs=0.05), "the two 0.6 s gaps in the audio"


def test_the_word_stream_marks_every_tenth_word_with_its_start(tmp_path):
    ws = [{"word": f"w{i}", "start": i * 0.25, "end": i * 0.25 + 0.2} for i in range(25)]
    pack_files(tmp_path / "wx", [(0.0, 6.0, " w0 ...")], ws, secs=7.0)
    words.transcript_pack("take", str(tmp_path / "wx"), str(tmp_path / "t.txt"))
    stream = [l for l in (tmp_path / "t.txt").read_text().splitlines() if l.startswith("[0.0]")][0].split()
    assert stream[0] == "[0.0]w0" and stream[10] == "[2.5]w10" and stream[20] == "[5.0]w20" and stream[11] == "w11" and len(stream) == 25


def test_a_pack_with_no_aligned_words_stops(tmp_path, capsys):
    pack_files(tmp_path / "wx", [(0.0, 1.0, " x")], [])
    with pytest.raises(SystemExit):
        words.transcript_pack("take", str(tmp_path / "wx"), str(tmp_path / "t.txt"))
    err = capsys.readouterr().err
    assert one_stop_line(err) and "nothing to pack" in err and not (tmp_path / "t.txt").exists(), err


@pytest.mark.xfail(strict=False, reason="engine/words.py transcript_pack: the word stream and the count keep only words that have a start, so a number the aligner could not stamp "
                   "is missing from the pack ('I have kids') while the cutter's own loader and words.json keep it ('I have 3 kids'): a match string "
                   "written from the pack never resolves")
def test_the_pack_keeps_a_number_the_aligner_could_not_stamp(tmp_path):
    ws = [{"word": "I", "start": 0.1, "end": 0.3}, {"word": "have", "start": 0.4, "end": 0.7}, {"word": "3"}, {"word": "kids", "start": 1.7, "end": 2.0}]
    pack_files(tmp_path / "wx", [(0.0, 2.0, " I have 3 kids")], ws)
    words.transcript_pack("take", str(tmp_path / "wx"), str(tmp_path / "t.txt"))
    text = (tmp_path / "t.txt").read_text()
    assert "[0.1]I have 3 kids" in text and text.startswith("# take: 4 words")


def test_the_pack_command_line_writes_the_pack_and_prints_the_summary(tmp_path):
    ws = [{"word": "So", "start": 0.1, "end": 0.3}, {"word": "yes.", "start": 0.4, "end": 0.7}]
    pack_files(tmp_path / "wx", [(0.0, 1.0, " So yes.")], ws)
    r = run_py(WORDS_PY, "--pack", "take", tmp_path / "wx", tmp_path / "t.txt")
    assert r.returncode == 0 and r.stdout.startswith("take: 2 words, 0 fillers, ") and (tmp_path / "t.txt").exists(), r.stdout + r.stderr


def test_main_hands_a_run_to_hear_and_prints_one_summary_line(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_hear(src, out, language, work, logs, device):
        seen.update(src=src, out=out, language=language, work=work, logs=logs, device=device)
        return {"language": "en", "words": words.flag(W(("Um,", 0, .2), ("is", .3, .4), ("is", .5, .6), ("ok.", .7, .9)))}
    monkeypatch.setattr(words, "hear", fake_hear)
    monkeypatch.setattr(sys, "argv", ["words.py", "clip.mov", "--out", "o.json", "--language", "auto", "--work", "w", "--logs", "l", "--device", "mps"])
    words._main()
    assert seen == {"src": "clip.mov", "out": "o.json", "language": "auto", "work": "w", "logs": "l", "device": "mps"}
    assert capsys.readouterr().out == "4 words, 1 fillers, 1 doubles (en) -> o.json\n"


# --- the real thing: Whisper and the aligner on a voice ----------------------------------------------------------------------------------

SENTENCE = "So today I want to talk about, um, the number three. It is the most important thing, uh, that matters."
EXPECTED = ["today", "want", "talk", "about", "number", "three", "most", "important", "thing", "matters"]


def norm(t):
    return "".join(c for c in t.lower() if c.isalnum())


class Heard:
    pass


@pytest.fixture(scope="module")
def heard(tmp_path_factory):
    """the sentence spoken once, in a work folder with a space in its name, and heard once: every real test below reads this one run"""
    if not HAVE_SPEECH:
        pytest.skip("needs the Whisper model, the word-timing python and macOS `say`")
    h = Heard()
    h.dir = tmp_path_factory.mktemp("spoken")
    h.work = h.dir / "work folder"
    h.work.mkdir()
    h.wav = speak(str(h.work / "take.wav"), SENTENCE)       # in the work folder, as prep lays it out
    h.out, h.logs = h.dir / "words.json", h.dir / "logs"
    began = time.perf_counter()
    h.doc = words.hear(h.wav, out=str(h.out), work=str(h.work), logs=str(h.logs))
    h.seconds = time.perf_counter() - began
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", h.wav], capture_output=True, text=True)
    h.dur = float(r.stdout)
    return h


@speech
def test_a_spoken_sentence_comes_back_as_its_words_with_times_and_flags(heard):
    d = heard.doc
    assert d["source"] == "whisper" and d["language"] == "en" and set(d) == {"source", "language", "transcript", "words"}
    ws = d["words"]
    assert d["transcript"] == " ".join(w["text"] for w in ws)
    assert all({"text", "start", "end", "filler", "double", "cap"} <= set(w) for w in ws)
    toks = [norm(w["text"]) for w in ws]
    pos = -1
    for want in EXPECTED:                                    # the content words, in order
        assert want in toks[pos + 1:], (want, toks)
        pos = toks.index(want, pos + 1)
    assert 17 <= len(ws) <= 24, len(ws)


@speech
def test_the_fillers_that_were_spoken_are_flagged_and_only_fillers_are(heard):
    ws = heard.doc["words"]
    assert any(w["filler"] for w in ws), "neither the um nor the uh was kept"
    assert all(w["filler"] == (norm(w["text"]) in cut.FILLERS) for w in ws)
    um = [w for w in ws if norm(w["text"]) in ("um", "uh")]
    assert um and all(w["end"] - w["start"] < 0.8 for w in um)


@speech
def test_the_times_are_plausible_for_a_voice_that_speaks_at_a_normal_rate(heard):
    ws, dur = heard.doc["words"], heard.dur
    assert all(0 <= w["start"] < w["end"] <= dur + 0.3 for w in ws)
    assert [w["start"] for w in ws] == sorted(w["start"] for w in ws)
    assert max(w["end"] - w["start"] for w in ws) < 1.5 and ws[0]["start"] < 1.0 and ws[-1]["end"] > 0.6 * dur
    assert all(b["start"] >= a["end"] - 0.15 for a, b in zip(ws, ws[1:])), "two words overlap"


@speech
def test_the_text_comes_back_punctuated_so_the_planner_finds_its_sentences(heard):
    spans = cut.sentence_spans(heard.doc["words"])
    assert len(spans) >= 2 and spans[0]["start"] < spans[-1]["start"]
    assert "number" in spans[0]["match"].lower() and "matters" in spans[-1]["match"].lower()


@speech
def test_the_words_json_is_written_and_the_work_and_log_files_are_where_they_should_be(heard):
    assert json.loads(heard.out.read_text()) == heard.doc
    assert sorted(os.listdir(heard.work)) == ["take.json", "take.srt", "take.wav", "take.wx.json"]
    assert sorted(os.listdir(heard.logs)) == ["take.align.log", "take.whisper.log"]
    assert "aligned words:" in (heard.logs / "take.align.log").read_text()


@speech
def test_a_second_call_over_the_same_work_folder_reuses_the_files_and_is_fast(heard, monkeypatch):
    before = {n: (heard.work / n).stat().st_mtime_ns for n in os.listdir(heard.work)}
    real = subprocess.run

    def guard(cmd, *a, **k):
        assert os.path.basename(str(cmd[0])) in ("ffprobe", "ffmpeg"), f"the second call ran {cmd[0]}"
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", guard)
    began = time.perf_counter()
    again = words.hear(heard.wav, out=str(heard.dir / "again.json"), work=str(heard.work))
    took = time.perf_counter() - began
    assert again == heard.doc and (heard.dir / "again.json").read_bytes() == heard.out.read_bytes()
    assert {n: (heard.work / n).stat().st_mtime_ns for n in os.listdir(heard.work)} == before
    assert took < 2.0 and took < heard.seconds / 2, (took, heard.seconds)


@speech
def test_audio_that_changed_is_heard_again(heard, tmp_path):
    work = tmp_path / "work folder"
    shutil.copytree(heard.work, work)
    time.sleep(0.05)
    speak(str(work / "take.wav"), "Goodbye for now, my friend.")        # newer than the files made from the old audio
    doc = words.hear(str(work / "take.wav"), work=str(work))
    toks = [norm(w["text"]) for w in doc["words"]]
    assert "goodbye" in toks and "friend" in toks and "number" not in toks, toks


@speech
def test_a_video_is_converted_heard_and_reused(tmp_path):
    v = talking_video(str(tmp_path / "clip.mp4"), "Hello there, this is a short test.", size="320x240")
    work = tmp_path / "w"
    doc = words.hear(v, out=str(tmp_path / "o.json"), work=str(work), logs=str(tmp_path / "logs"))
    toks = [norm(w["text"]) for w in doc["words"]]
    assert "hello" in toks and "test" in toks, toks
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels", "-of", "csv=p=0", str(work / "clip.wav")], capture_output=True, text=True)
    assert r.stdout.strip() == "16000,1", r.stdout
    first = (work / "clip.wav").stat().st_mtime_ns, (work / "clip.wx.json").stat().st_mtime_ns
    assert words.hear(v, work=str(work)) == doc
    assert ((work / "clip.wav").stat().st_mtime_ns, (work / "clip.wx.json").stat().st_mtime_ns) == first


@speech
def test_language_auto_hears_english_as_english_and_leaves_no_temporary_folder(heard, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    doc = words.hear(heard.wav, language="auto")                          # no work folder: everything in a temporary one
    toks = [norm(w["text"]) for w in doc["words"]]
    assert doc["language"] == "en" and "number" in toks and "matters" in toks and len(doc["words"]) >= 15
    assert os.listdir(scratch) == []


@speech
def test_the_command_line_runs_the_whole_engine_and_prints_one_line(heard, tmp_path):
    out = tmp_path / "out dir" / "words.json"
    out.parent.mkdir()
    r = run_py(WORDS_PY, heard.wav, "--out", out, "--work", tmp_path / "a work folder", "--logs", tmp_path / "logs")
    assert r.returncode == 0 and r.stderr == "", r.stdout + r.stderr
    n = len(json.loads(out.read_text())["words"])
    assert r.stdout.strip().startswith(f"{n} words, ") and r.stdout.strip().endswith(f"(en) -> {out}"), r.stdout
    assert (tmp_path / "logs" / "take.whisper.log").exists()


@speech
def test_the_transcript_pack_of_a_real_run(heard, tmp_path):
    out = tmp_path / "take.txt"
    line = words.transcript_pack("take", str(heard.work), str(out))
    fillers = sum(w["filler"] for w in heard.doc["words"])
    assert line.startswith(f"take: {len(heard.doc['words'])} words, {fillers} fillers, ") and line.endswith(f"-> {out}")
    text = out.read_text()
    assert text.startswith(f"# take: {len(heard.doc['words'])} words, ") and f"fillers: {fillers} " in text
    segs = [l for l in text.splitlines() if l.startswith("[") and "-" in l.split("]")[0]]
    assert segs and all(l[1:7].strip().replace(".", "").isdigit() for l in segs), segs
    r = run_py(WORDS_PY, "--pack", "take", heard.work, tmp_path / "again.txt")
    assert r.returncode == 0 and r.stdout.strip().startswith(f"take: {len(heard.doc['words'])} words"), r.stdout + r.stderr


@speech
def test_the_aligner_stops_with_one_line_on_a_language_it_has_no_model_for(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps({"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": " hello"}]}))
    wav = tone_wav(tmp_path / "w.wav")
    r = subprocess.run([VENV_PYTHON, WX_ALIGN, wav, str(tmp_path / "w.json"), str(tmp_path / "o.json"), "--language", "xx"], capture_output=True, text=True)
    assert r.returncode == 2 and one_stop_line(r.stderr) and "no alignment model for language 'xx'" in r.stderr, r.stderr
    assert not (tmp_path / "o.json").exists()


@speech
def test_the_aligner_stops_when_whisper_heard_no_speech(tmp_path):
    (tmp_path / "w.json").write_text(json.dumps({"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": "  "}]}))
    wav = tone_wav(tmp_path / "w.wav")
    r = subprocess.run([VENV_PYTHON, WX_ALIGN, wav, str(tmp_path / "w.json"), str(tmp_path / "o.json")], capture_output=True, text=True)
    assert r.returncode == 2 and one_stop_line(r.stderr) and "whisper heard no speech in " + wav in r.stderr, r.stderr


@speech
def test_a_device_the_aligner_cannot_use_is_one_stop_line_with_the_log(heard, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        words.hear(heard.wav, work=str(tmp_path / "w"), logs=str(tmp_path / "logs"), device="no-such-device")
    err = capsys.readouterr().err
    assert e.value.code == 2 and one_stop_line(err) and "the word aligner failed on take.wav" in err and "take.align.log" in err, err


@speech
@pytest.mark.xfail(strict=False, reason="engine/words.py hear()/_tail: for a language whisper does not know, whisper-cli prints \"error: unknown language 'xx'\" and then its whole usage text; "
                   "the STOP line carries the last three lines of that log, which are usage lines about VAD, and never names the language")
def test_a_language_whisper_does_not_know_is_named_in_the_stop_line(heard, tmp_path, capsys):
    with pytest.raises(SystemExit):
        words.hear(heard.wav, language="xx", work=str(tmp_path / "w"))
    err = capsys.readouterr().err
    assert one_stop_line(err) and "xx" in err
