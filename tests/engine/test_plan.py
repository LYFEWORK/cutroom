"""The plan builder: the take's sentences, the segments and plan the cutter is asked for, and the shapes both consumers (the cutter's matcher and
the script gate) read."""
import json
import os
import subprocess
import sys

import pytest

import cut
import script

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")

# One take with (a) a filler INSIDE a sentence and (b) two textually identical sentences: the two shapes that break a naive plan builder.
TOKENS = ["This", "is", "the", "thing", "um", "that", "matters", "most.",
          "So", "book", "a", "call", "today.",
          "So", "book", "a", "call", "today."]


def words_json():
    return {"source": "whisper", "transcript": " ".join(TOKENS),
            "words": [{"text": t, "start": round(i * 0.4, 3), "end": round(i * 0.4 + 0.3, 3),
                       "filler": t == "um", "double": False, "cap": False}
                      for i, t in enumerate(TOKENS)]}


def W(text, t0=0.0):
    out, t = [], t0
    for tok in text.split():
        out.append({"text": tok, "start": round(t, 2), "end": round(t + 0.3, 2), "filler": tok.lower().strip(".,?!") in ("um", "uh"), "double": False, "cap": tok[:1].isupper()})
        t += 0.35
    return out


def spans(words=None):
    return cut.sentence_spans((words or words_json()["words"]))


# --- sentences ------------------------------------------------------------------------------------------------------------------------

def test_sentences_split_on_punctuation():
    s = cut.sentences(W("If you are a business owner, listen up. Most people never call back. Comment WELCOME and I will send it."))
    assert len(s) == 3 and s[0]["text"].startswith("If you are") and s[2]["text"].startswith("Comment")
    assert s[0]["start"] == 0.0 and s[1]["words"][0]["text"] == "Most"


def test_a_last_run_with_no_ending_mark_is_still_a_sentence():
    s = cut.sentences(W("One thing. And then a trailing thought with no mark"))
    assert len(s) == 2 and s[1]["text"].endswith("no mark")


def test_a_filler_never_ends_or_opens_a_sentence_but_stays_inside_the_match():
    words = W("Well um. That works. Fine.")
    sp = cut.sentence_spans(words)
    assert [s["match"] for s in sp] == ["Well um. That works.", "Fine."]       # "um." is a filler: it closes nothing, and it stays in the run


# --- segments -------------------------------------------------------------------------------------------------------------------------

def test_match_strings_keep_interior_fillers():
    segs = cut.segments(spans())
    assert segs[0]["label"] == "01" and segs[0]["match"] == "This is the thing um that matters most."


def test_repeated_sentences_get_distinct_occurrences():
    segs = cut.segments(spans())
    repeats = [s for s in segs if s["match"] == "So book a call today."]
    assert [s["occurrence"] for s in repeats] == [1, 2]
    assert [s["label"] for s in repeats] == ["02", "03"]


def test_the_order_is_the_transcript_order_unless_hook_first_is_asked_for():
    plain = cut.segments(spans())
    assert [s["label"] for s in plain] == ["01", "02", "03"]
    first = cut.segments(spans(), hook_first=3)
    assert [s["label"] for s in first] == ["hook", "01", "02"] and first[0]["occurrence"] == 2
    with pytest.raises(SystemExit):
        cut.segments(spans(), hook_first=9)


def test_both_consumers_resolve_every_segment_against_the_sidecar(tmp_path):
    w = words_json()
    plan = cut.build_plan(spans(), "t1", str(tmp_path), str(tmp_path))
    wx = tmp_path / "words.wx.json"
    wx.write_text(json.dumps(cut.wx_sidecar(w["words"])))
    assert plan["words_json"] == str(wx)
    # the cutter's side: every segment resolves, and the two identical sentences land on DIFFERENT source spans instead of both cutting the first
    loaded = cut.load_words(str(wx))
    found = [cut.find_match(loaded, s["match"], s["occurrence"]) for s in plan["segments"]]
    assert len(set(found)) == len(found)
    # the gate's side: build finds every segment and strikes the aligned filler
    md = script.build(w, plan)
    assert "~~um~~" in md and "## 01" in md and "## 03" in md


def test_the_filler_stripped_match_is_what_used_to_fail(tmp_path):
    """the filler-stripped sentence is not a contiguous run of the transcript either consumer searches"""
    w = words_json()
    wx = tmp_path / "words.wx.json"
    wx.write_text(json.dumps(cut.wx_sidecar(w["words"])))
    stripped = "This is the thing that matters most."
    with pytest.raises(SystemExit):
        cut.find_match(cut.load_words(str(wx)), stripped, 1)
    bad = {"segments": [{"match": stripped, "occurrence": 1, "label": "01", "drop": []}]}
    with pytest.raises(SystemExit):
        script.build(w, bad)


def test_wx_sidecar_round_trips_word_for_word(tmp_path):
    w = words_json()
    wx = tmp_path / "words.wx.json"
    wx.write_text(json.dumps(cut.wx_sidecar(w["words"])))
    loaded = cut.load_words(str(wx))
    assert [x["text"] for x in loaded] == TOKENS
    assert loaded[0]["start"] == 0.0 and round(loaded[-1]["end"], 3) == round(17 * 0.4 + 0.3, 3)


# --- the plan -------------------------------------------------------------------------------------------------------------------------

def test_the_plan_carries_the_talking_head_noise_floor():
    """-30 dB reads quiet word onsets as silence and cuts inside them; -35 is clean, and the joint listener stays off because the voice decides"""
    plan = cut.build_plan(spans(), "t1", "/root", "/work")
    assert plan["sil_noise_db"] == -35 and plan["qc_noise_db"] == -35 and plan["refine_joints"] is False
    assert plan["src"] == "/root/mez/t1.mp4" and plan["output"] == "/work/t1-cut.mp4" and plan["fps"] == 30


def test_the_planners_defaults_never_edit_the_speakers_words():
    plan = cut.build_plan(spans(), "t1", "/root", "/work")
    assert plan["right_mode"] == "off" and plan["profanity"] == "off" and plan["drop_fillers"] is True
    assert plan["gate"] == {"approved": False} and "hook_first" not in plan
    assert "grade" not in plan and "ring" not in json.dumps(plan)


def test_hook_first_is_recorded_in_the_plan_when_it_is_used():
    plan = cut.build_plan(spans(), "t1", "/root", "/work", hook_first=2)
    assert plan["hook_first"] == 2 and plan["segments"][0]["label"] == "hook"


def test_the_plan_cli_writes_sentences_plan_and_sidecar(tmp_path):
    w = tmp_path / "words.json"
    w.write_text(json.dumps(words_json()))
    py = os.path.join(ENGINE, "cut.py")
    r = subprocess.run([sys.executable, py, "sentences", str(w), str(tmp_path / "sentences.json")], capture_output=True, text=True)
    assert r.returncode == 0 and "3 sentences" in r.stdout, r.stdout + r.stderr
    r = subprocess.run([sys.executable, py, "plan", str(w), str(tmp_path / "sentences.json"), "t1", str(tmp_path), str(tmp_path), "--hook-first", "2"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["words_json"] == str(tmp_path / "words.wx.json") and (tmp_path / "words.wx.json").exists()
    assert plan["segments"][0]["label"] == "hook" and plan["hook_first"] == 2


def test_check_plan_lists_the_kept_text_and_flags_what_does_not_resolve(tmp_path):
    wx = tmp_path / "words.wx.json"
    wx.write_text(json.dumps(cut.wx_sidecar(words_json()["words"])))
    plan = {"words_json": str(wx), "segments": [{"match": "This is the thing um that matters most.", "occurrence": 1, "label": "01", "drop": ["um"]},
                                                 {"match": "So book a call today.", "occurrence": 2, "label": "02", "drop": []}]}
    lines, fails = cut.check_plan(plan)
    assert not fails and "This is the thing that matters most." in "\n".join(lines)
    plan["segments"].append({"match": "Never said.", "occurrence": 1, "label": "03"})
    plan["segments"][1]["drop"] = ["book book"]
    lines, fails = cut.check_plan(plan)
    assert len(fails) == 2 and any("not in the transcript" in f for f in fails) and any("whole word" in f for f in fails), fails
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    r = subprocess.run([sys.executable, os.path.join(ENGINE, "cut.py"), "check-plan", str(p)], capture_output=True, text=True)
    assert r.returncode == 1 and "FAIL" in r.stdout
