"""normalize_words.py: spoken numbers become numerals in words.json, once, with a backup; with the take's look.json the DNA's spelling map fixes names the transcriber
gets wrong. It runs inside the take's plan stage."""
import json
import os
import subprocess
import sys

import cap_helpers
import normalize_words

SCRIPT = os.path.join(cap_helpers.REPO, "engine", "captions", "normalize_words.py")


def W(text, start, end):
    return {"text": text, "start": start, "end": end, "filler": False}


def run(path, *extra):
    return subprocess.run([sys.executable, "-B", SCRIPT, str(path), *map(str, extra)], capture_output=True, text=True)


def texts(path):
    return [w["text"] for w in json.load(open(path))["words"]]


def test_numbers_become_numerals_with_merged_timing_and_a_backup(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"transcript": "we booked twenty five jobs", "words": [W("we", 0, .2), W("booked", .2, .6), W("twenty", .6, .9),
                                                                      W("five", .9, 1.2), W("jobs", 1.2, 1.5)]}, open(p, "w"))
    r = run(p)
    assert r.returncode == 0 and "normalize:" in r.stdout
    d = json.load(open(p))
    assert [w["text"] for w in d["words"]] == ["we", "booked", "25", "jobs"]
    assert (d["words"][2]["start"], d["words"][2]["end"]) == (0.6, 1.2) and d["transcript"] == "we booked 25 jobs"
    assert len(json.load(open(str(p) + ".pre-normalize"))["words"]) == 5


def test_a_hundred_and_the_punctuation_after_a_number_survive(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("got", 0, .2), W("two", .2, .5), W("hundred", .5, .9), W("leads", .9, 1.3), W("in", 1.3, 1.4), W("thirty", 1.4, 1.8), W("days.", 1.8, 2.2)]}, open(p, "w"))
    assert run(p).returncode == 0
    d = json.load(open(p))
    assert [w["text"] for w in d["words"]] == ["got", "200", "leads", "in", "30", "days."]
    assert (d["words"][1]["start"], d["words"][1]["end"]) == (0.2, 0.9)


def test_a_second_run_rewrites_nothing(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("eleven", 0, .4), W("leads", .4, .8)]}, open(p, "w"))
    run(p)
    first = open(p).read()
    r = run(p)
    assert r.stdout.strip() == "normalize: 0 token(s) rewritten" and open(p).read() == first


def test_a_transcript_with_no_numbers_is_left_alone_and_gets_no_backup(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("hello", 0, .4), W("there", .4, .8)]}, open(p, "w"))
    before = open(p).read()
    run(p)
    assert open(p).read() == before and not os.path.exists(str(p) + ".pre-normalize")


def test_bad_inputs_are_one_stop_line(tmp_path):
    missing = run(tmp_path / "none.json")
    assert missing.returncode == 2 and missing.stderr.startswith("STOP: bad input") and missing.stdout == ""
    p = tmp_path / "words.json"
    p.write_text("{not json")
    assert run(p).returncode == 2
    p.write_text(json.dumps({"words": "nope"}))
    r = run(p)
    assert r.returncode == 2 and r.stderr.startswith("STOP:") and "no \"words\" list" in r.stderr and "Traceback" not in r.stdout + r.stderr
    assert r.stderr.count("\n") == 1


def test_a_look_json_that_cannot_be_read_is_one_stop_line_and_the_words_stay_as_they_were(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("eleven", 0, .4), W("leads", .4, .8)]}, open(p, "w"))
    before = open(p).read()
    r = run(p, tmp_path / "no-look.json")
    assert r.returncode == 2 and r.stderr.startswith("STOP: bad input") and "Traceback" not in r.stderr
    assert open(p).read() == before and not os.path.exists(str(p) + ".pre-normalize")


# --- the DNA's spelling map, through the take's look.json ---------------------------------------

def write_look(tmp_path, **spelling):
    """<tmp>/look.json: the varsans look, its spelling map (acme corp -> ACME Corp) plus any extra keys the test adds"""
    look = cap_helpers.profile("varsans")
    look["spelling"] = {**look["spelling"], **spelling}
    path = tmp_path / "look.json"
    json.dump(look, open(path, "w"))
    return path


def test_the_fixture_look_carries_the_spelling_map_the_tests_rely_on():
    assert cap_helpers.profile("varsans")["spelling"]["acme corp"] == "ACME Corp"
    assert cap_helpers.profile("upstatic")["spelling"] == {}


def test_a_multi_word_key_merges_its_tokens_keeps_the_trailing_punctuation_and_matches_in_any_case(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("Hi", 0, .2), W("at", .2, .4), W("Acme", .4, .8), W("Corp.", .8, 1.2), W("and", 1.2, 1.4), W("acme", 1.4, 1.8), W("corp", 1.8, 2.2)]}, open(p, "w"))
    r = run(p, write_look(tmp_path))
    # the count is the new tokens (two) plus the tokens merged away (two)
    assert r.returncode == 0 and r.stdout.strip() == "normalize: 4 token(s) rewritten", r.stdout + r.stderr
    d = json.load(open(p))
    assert [w["text"] for w in d["words"]] == ["Hi", "at", "ACME Corp.", "and", "ACME Corp"]
    assert (d["words"][2]["start"], d["words"][2]["end"]) == (0.4, 1.2) and (d["words"][4]["start"], d["words"][4]["end"]) == (1.4, 2.2)
    assert d["transcript"] == "Hi at ACME Corp. and ACME Corp"
    assert len(json.load(open(str(p) + ".pre-normalize"))["words"]) == 7


def test_the_punctuation_around_a_multi_word_key_stays_on_its_first_and_last_token(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("(Acme", 0, .4), W("corp),", .4, .9), W("said", .9, 1.2)]}, open(p, "w"))
    run(p, write_look(tmp_path))
    assert texts(p) == ["(ACME Corp),", "said"]


def test_a_one_word_key_fixes_the_name_and_keeps_the_words_own_punctuation(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("Hi", 0, .2), W("Jon,", .2, .6), W("at", .6, .8), W("acme's", .8, 1.2)]}, open(p, "w"))
    r = run(p, write_look(tmp_path, jon="John", **{"acme's": "ACME's"}))
    assert r.returncode == 0 and "normalize: 2 token(s)" in r.stdout, r.stdout + r.stderr
    assert texts(p) == ["Hi", "John,", "at", "ACME's"]
    assert json.load(open(p))["transcript"] == "Hi John, at ACME's"


def test_a_curly_apostrophe_in_the_transcript_matches_a_straight_one_in_the_key(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("ACME’S", 0, .5), W("ads", .5, .9)]}, open(p, "w"))
    run(p, write_look(tmp_path, **{"acme's": "ACME's"}))
    assert texts(p) == ["ACME's", "ads"]


def test_the_longest_key_wins_where_two_keys_start_on_the_same_word(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("acme", 0, .3), W("corp", .3, .7), W("and", .7, .9), W("acme", .9, 1.3)]}, open(p, "w"))
    run(p, write_look(tmp_path, acme="Acme"))
    assert texts(p) == ["ACME Corp", "and", "Acme"]


def test_spelling_is_idempotent_and_a_word_that_merely_contains_a_key_is_left_alone(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("jon", 0, .3), W("jonas", .3, .8)]}, open(p, "w"))
    look = write_look(tmp_path, jon="John")
    run(p, look)
    first = open(p).read()
    assert texts(p) == ["John", "jonas"]
    r = run(p, look)
    assert "normalize: 0 token(s)" in r.stdout and open(p).read() == first


def test_numbers_and_spelling_are_fixed_in_one_pass_with_one_backup(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("we", 0, .2), W("booked", .2, .6), W("twenty", .6, .9), W("five", .9, 1.2), W("jobs", 1.2, 1.5), W("for", 1.5, 1.7),
                         W("acme", 1.7, 2.0), W("corp", 2.0, 2.4)]}, open(p, "w"))
    r = run(p, write_look(tmp_path))
    assert r.returncode == 0 and r.stdout.strip() == "normalize: 4 token(s) rewritten", r.stdout + r.stderr   # two new tokens, two merged away
    d = json.load(open(p))
    assert [w["text"] for w in d["words"]] == ["we", "booked", "25", "jobs", "for", "ACME Corp"] and d["transcript"] == "we booked 25 jobs for ACME Corp"
    assert len(json.load(open(str(p) + ".pre-normalize"))["words"]) == 8


def test_without_a_look_json_only_the_numbers_are_fixed(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("acme", 0, .3), W("corp", .3, .7), W("booked", .7, 1.0), W("eleven", 1.0, 1.4)]}, open(p, "w"))
    run(p)
    assert texts(p) == ["acme", "corp", "booked", "11"]


def test_a_look_with_no_spelling_map_changes_nothing_but_the_numbers(tmp_path):
    p = tmp_path / "words.json"
    json.dump({"words": [W("acme", 0, .3), W("corp", .3, .7)]}, open(p, "w"))
    before = open(p).read()
    look = cap_helpers.profile("upstatic")
    assert look["spelling"] == {}
    path = tmp_path / "look.json"
    json.dump(look, open(path, "w"))
    assert run(p, path).stdout.strip() == "normalize: 0 token(s) rewritten" and open(p).read() == before


def test_apply_spelling_is_a_pure_function_of_the_words_and_the_map():
    words = [W("Acme", 0, .3), W("Corp.", .3, .7)]
    out = normalize_words.apply_spelling(words, {"acme corp": "ACME Corp"})
    assert [(w["text"], w["start"], w["end"]) for w in out] == [("ACME Corp.", 0, .7)]
    assert [w["text"] for w in words] == ["Acme", "Corp."], "the input list is not changed"
    assert normalize_words.apply_spelling(words, {}) is words and normalize_words.apply_spelling(words, None) is words
    assert normalize_words.apply_spelling(words, {"  ": "x"}) is words, "a blank key is ignored"
