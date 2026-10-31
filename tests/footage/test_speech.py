"""Whisper labels a sound it cannot transcribe in brackets, parentheses or stars. A clip that holds only such a label has no speech, the same as "." and "Thank you."."""
import pytest

import job


@pytest.mark.parametrize("text", ["", None, ".", "Thank you.", "[MUSIC]", "(humming)", "*sad music*", "*Painful music*", "*music* [BLANK_AUDIO] .",
                                  "Transcription by CastingWords", "Subtitles by the Amara.org community", "Okay.", "um", "  you  "])
def test_a_sound_label_or_filler_is_not_speech(text):
    assert job.speech_text(text) == ""


@pytest.mark.parametrize("text", ["Candle wax.", "Yeah, we can do that.", "*laughs* All you gotta do, read below.", "[MUSIC] Pour it all day."])
def test_speech_comes_back_as_heard(text):
    assert job.speech_text(text) == text


def test_spaces_are_squeezed_but_the_words_are_not_changed():
    assert job.speech_text("  every   batch\n has to cure  ") == "every batch has to cure"
