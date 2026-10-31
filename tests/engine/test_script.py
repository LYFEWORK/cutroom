"""The script gate (script.py): SCRIPT.md is built from a plan, the person edits it, and apply() turns the edits back into the plan."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import cut
import script
from helpers import write_wav

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")

TEXT = "If you are a business owner, um listen up. Most people never call back right? Comment WELCOME and I will send it."
WORDS = {"words": [{"text": t, "start": i * .4, "end": i * .4 + .3, "filler": t == "um", "double": False, "cap": t[:1].isupper()}
                   for i, t in enumerate(TEXT.split())]}
PLAN = {"segments": [{"match": "If you are a business owner, um listen up.", "occurrence": 1, "label": "hook", "drop": ["um"]},
                     {"match": "Most people never call back right?", "occurrence": 1, "label": "02", "drop": []},
                     {"match": "Comment WELCOME and I will send it.", "occurrence": 1, "label": "03", "drop": []}]}


def test_build_marks_fillers_and_drops_struck():
    md = script.build(WORDS, PLAN)
    assert md.startswith("# ") and "approved: no" in md
    assert "~~um~~" in md and "## Hook" in md and "## 02" in md and "## 03" in md
    assert "text_hook" not in md and "music" not in md


def test_the_notes_in_the_file_never_read_as_the_approval_line():
    md = script.build(WORDS, PLAN)
    assert "approved: yes" in md and script.parse(md)["approved"] is False         # the note mentions it; only a line that starts with it counts
    assert script.parse(md.replace("approved: no", "approved: yes"))["approved"] is True
    assert not any(l.startswith("approved: yes") for l in md.splitlines())


def test_parse_reads_struck_and_flags():
    md = "# t\napproved: yes\n\n## Hook\nMost people ~~never~~ call back.\n"
    p = script.parse(md)
    assert p["approved"] is True
    assert p["lines"][0]["struck"] == ["never"] and p["lines"][0]["kept"] == ["Most", "people", "call", "back."]
    assert p["lines"][0]["tokens"] == ["Most", "people", "never", "call", "back."] and p["lines"][0]["groups"] == [{"text": "never", "at": 2, "n": 1}]


def test_a_struck_phrase_is_one_group_with_its_first_words_position():
    p = script.parse("# t\napproved: no\n\n## 01\nSo ~~you know what~~ the point is.\n")
    assert p["lines"][0]["groups"] == [{"text": "you know what", "at": 1, "n": 3}] and p["lines"][0]["tokens"][1:4] == ["you", "know", "what"]


def test_apply_turns_strikes_into_drops_and_deleted_sections_into_removed_segments():
    md = script.build(WORDS, PLAN)
    md = md.replace("call back right?", "call back ~~right?~~")
    md = md.replace("approved: no", "approved: yes")
    md = md[: md.index("## 03")]                                           # the third section is deleted
    new = script.apply(md, PLAN)
    assert new["gate"] == {"approved": True}
    assert len(new["segments"]) == 2 and "right?" in new["segments"][1]["drop"]
    assert new["segments"][0]["drop"] == ["um"]


def test_build_filler_strike_is_positional_not_a_text_scan():
    # "Well," is flagged filler in segment 1's occurrence; the unrelated word "well" recurs as legitimate content in segment 2 and must NOT be
    # struck just because the text matches.
    words = {"words": [
        {"text": "Well,", "filler": True}, {"text": "listen", "filler": False}, {"text": "up.", "filler": False},
        {"text": "We", "filler": False}, {"text": "did", "filler": False}, {"text": "our", "filler": False},
        {"text": "homework", "filler": False}, {"text": "well", "filler": False},
    ]}
    plan = {"segments": [
        {"match": "Well, listen up.", "occurrence": 1, "label": "hook", "drop": []},
        {"match": "We did our homework well", "occurrence": 1, "label": "02", "drop": []},
    ]}
    md = script.build(words, plan)
    assert "~~Well,~~" in md
    assert "~~well~~" not in md


def test_build_shows_a_planned_strike_on_the_occurrence_the_plan_means_not_on_every_equal_word():
    words = {"words": [{"text": t, "start": i * .4, "end": i * .4 + .3, "filler": False} for i, t in enumerate("It is really good and really fast.".split())]}
    plan = {"segments": [{"match": "It is really good and really fast.", "occurrence": 1, "label": "01", "drop": [{"text": "really", "start": 5 * .4}]}]}
    md = script.build(words, plan)
    assert "It is really good and ~~really~~ fast." in md


def test_apply_disambiguates_duplicate_labels_by_position_not_first_match():
    # Two segments share label "02" -> two "## 02" sections. An edit made only in the SECOND section must land on the SECOND segment.
    words = {"words": [{"text": t, "filler": False} for t in "First clip here Second clip here".split()]}
    plan = {"segments": [
        {"match": "First clip here", "occurrence": 1, "label": "02", "drop": []},
        {"match": "Second clip here", "occurrence": 1, "label": "02", "drop": []},
    ]}
    md = script.build(words, plan)
    assert md.count("## 02") == 2
    before, mid, after = md.split("## 02")
    after = after.replace("here", "~~here~~", 1)                          # strike only in the 2nd section
    md = before + "## 02" + mid + "## 02" + after
    new = script.apply(md, plan)
    assert new["segments"][0]["drop"] == []
    assert new["segments"][1]["drop"] == ["here"]


def test_apply_mid_run_deletion_with_duplicate_labels_does_not_misbind_or_drop():
    # Three segments share label "02". The MIDDLE section is deleted: the surviving two keep their OWN content, and none vanishes silently.
    words = {"words": [{"text": t, "filler": False} for t in "First clip here Second clip here Third clip here".split()]}
    plan = {"segments": [
        {"match": "First clip here", "occurrence": 1, "label": "02", "drop": []},
        {"match": "Second clip here", "occurrence": 1, "label": "02", "drop": []},
        {"match": "Third clip here", "occurrence": 1, "label": "02", "drop": []},
    ]}
    md = script.build(words, plan)
    assert md.count("## 02") == 3
    middle_section = "## 02\nSecond clip here\n\n"
    assert middle_section in md
    new = script.apply(md.replace(middle_section, "", 1), plan)
    assert len(new["segments"]) == 2
    assert new["segments"][0]["match"] == "First clip here" and new["segments"][1]["match"] == "Third clip here"
    assert new["segments"][0]["drop"] == [] and new["segments"][1]["drop"] == []


def test_a_word_un_struck_after_a_run_comes_back():
    """SCRIPT.md is the source of truth for the whole drop list: it is applied again on every resume, against the plan the last apply wrote, so
    taking the strike off a word has to bring it back"""
    md = script.build(WORDS, PLAN).replace("approved: no", "approved: yes")
    struck = md.replace("call back right?", "call back ~~right?~~")
    once = script.apply(struck, PLAN)
    assert once["segments"][1]["drop"] == ["right?"]
    twice = script.apply(md, once)
    assert twice["segments"][1]["drop"] == []


def test_a_filler_left_unstruck_is_kept_in_the_cut():
    """build() pre-strikes fillers; un-striking one has to survive the cut, and the cutter only honours that through seg["keep_fillers"]"""
    md = script.build(WORDS, PLAN).replace("approved: no", "approved: yes")
    kept = script.apply(md.replace("~~um~~", "um"), PLAN)
    assert kept["segments"][0]["drop"] == [] and kept["segments"][0]["keep_fillers"] is True
    cut_ = script.apply(md, PLAN)
    assert cut_["segments"][0]["drop"] == ["um"] and not cut_["segments"][0].get("keep_fillers")


def test_apply_rewrites_plan_json_only_when_the_content_changes(tmp_path):
    """the chain skips a re-cut while the cut is NEWER than plan.json, and `apply` runs on every resume: a no-change apply must leave the file
    untouched, while a real SCRIPT.md edit (and the widened lead_extra the retry ladder writes) still rewrites it"""
    md = script.build(WORDS, PLAN).replace("approved: no", "approved: yes")
    sc = tmp_path / "SCRIPT.md"; sc.write_text(md)
    plan = tmp_path / "plan.json"; json.dump(PLAN, open(plan, "w"), indent=1)

    def run():
        r = subprocess.run([sys.executable, os.path.join(ENGINE, "script.py"), "apply", str(sc), str(plan), str(plan)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return r
    run()
    back = os.stat(plan).st_mtime - 1000
    os.utime(plan, (back, back))
    run()
    assert os.stat(plan).st_mtime == back, "plan.json was rewritten though nothing changed"
    sc.write_text(md.replace("call back right?", "call back ~~right?~~"))
    run()
    assert os.stat(plan).st_mtime > back, "a changed SCRIPT.md did not rewrite plan.json"


def test_the_cli_builds_the_file_and_applies_it_with_the_words(tmp_path):
    (tmp_path / "words.json").write_text(json.dumps(WORDS))
    (tmp_path / "plan.json").write_text(json.dumps(PLAN))
    py = os.path.join(ENGINE, "script.py")
    r = subprocess.run([sys.executable, py, "build", str(tmp_path / "words.json"), str(tmp_path / "plan.json"), str(tmp_path / "SCRIPT.md")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    md = (tmp_path / "SCRIPT.md").read_text().replace("approved: no", "approved: yes")
    (tmp_path / "SCRIPT.md").write_text(md)
    r = subprocess.run([sys.executable, py, "apply", str(tmp_path / "SCRIPT.md"), str(tmp_path / "plan.json"), str(tmp_path / "out.json"), "--words", str(tmp_path / "words.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "approved: True" in r.stdout, r.stdout + r.stderr
    out = json.loads((tmp_path / "out.json").read_text())
    assert out["segments"][0]["drop"] == [{"text": "um", "start": WORDS["words"][6]["start"]}]


def test_a_build_that_cannot_find_a_segment_leaves_no_half_written_script(tmp_path):
    (tmp_path / "words.json").write_text(json.dumps(WORDS))
    bad = {"segments": [{"match": "never said", "occurrence": 1, "label": "01"}]}
    (tmp_path / "plan.json").write_text(json.dumps(bad))
    r = subprocess.run([sys.executable, os.path.join(ENGINE, "script.py"), "build", str(tmp_path / "words.json"), str(tmp_path / "plan.json"), str(tmp_path / "SCRIPT.md")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and r.stderr.startswith("STOP:") and not (tmp_path / "SCRIPT.md").exists()


# --- strikes are whole words: "um," never takes the middle of "number," ----------------------------------------------------------------

SR = 16000


def _take(tmp_path, spoken, struck, filler_flags=()):
    """a take whose words are `spoken` [(text, start, end)] with a 220 Hz tone under each: returns (plan after the gate, plan_pieces result)"""
    t = np.arange(int(3.2 * SR)) / SR
    y = np.random.default_rng(3).normal(0, 0.002, len(t))
    for _, a, b in spoken:
        m = (t >= a) & (t < b)
        y[m] += 0.3 * np.sin(2 * np.pi * 220 * t[m])
    src = write_wav(tmp_path / "a.wav", y)
    words = {"words": [{"text": w, "start": a, "end": b, "filler": w in filler_flags, "double": False, "cap": w[:1].isupper()} for w, a, b in spoken]}
    text = " ".join(w for w, _, _ in spoken)
    plan = {"src": src, "words_json": str(tmp_path / "w.wx.json"), "output": str(tmp_path / "cut.mp4"), "refine_joints": False,
            "segments": [{"match": text, "occurrence": 1, "label": "01"}]}
    (tmp_path / "w.wx.json").write_text(json.dumps(cut.wx_sidecar(words["words"])))
    md = script.build(words, plan).replace("approved: no", "approved: yes")
    for w in struck:
        md = md.replace(f" {w} ", f" ~~{w}~~ ", 1)
    return words, plan, md


def test_striking_um_does_not_remove_number(tmp_path):
    spoken = [("The", 0.30, 0.50), ("number,", 0.55, 1.05), ("um,", 1.45, 1.75), ("three.", 2.15, 2.65)]
    words, plan, md = _take(tmp_path, spoken, ["um,"])
    assert "~~um,~~" in md and "~~number,~~" not in md
    for new in (script.apply(md, plan, words), script.apply(md, plan)):                # the exact form, and the bare-string form
        pieces, _, _, _ = cut.plan_pieces(new)
        assert [d["text"] for d in new["_dropped"]] == ["um,"], new["_dropped"]
        assert "number," in " ".join(p["text"] for p in pieces) and "um," not in " ".join(p["text"] for p in pieces)


def test_striking_ah_does_not_remove_yeah(tmp_path):
    spoken = [("Yeah,", 0.30, 0.70), ("ah,", 1.10, 1.35), ("that", 1.70, 1.95), ("works.", 2.05, 2.60)]
    words, plan, md = _take(tmp_path, spoken, ["ah,"])
    new = script.apply(md, plan, words)
    cut.plan_pieces(new)
    assert [d["text"] for d in new["_dropped"]] == ["ah,"]


def test_a_strike_with_its_start_time_picks_the_right_word_when_the_text_repeats(tmp_path):
    spoken = [("It", 0.30, 0.45), ("was", 0.50, 0.70), ("really", 0.75, 1.15), ("good", 1.20, 1.60), ("and", 1.65, 1.85), ("really", 1.90, 2.30), ("fast.", 2.35, 2.80)]
    words, plan, md = _take(tmp_path, spoken, [])
    md = md.replace("and really fast.", "and ~~really~~ fast.")
    exact = script.apply(md, plan, words)
    assert exact["segments"][0]["drop"] == [{"text": "really", "start": 1.90}]
    cut.plan_pieces(exact)
    assert [(d["text"], d["start"]) for d in exact["_dropped"]] == [("really", 1.9)]


def test_resolve_drops_strikes_whole_words_only():
    ws = [{"text": t, "start": i * .5, "end": i * .5 + .4} for i, t in enumerate("The number, um, volume, assume, Yeah, ah, umbrella, ahead".split())]
    assert cut.resolve_drops(ws, ["um,"]) == {2}
    assert cut.resolve_drops(ws, ["ah,"]) == {6}
    assert cut.resolve_drops(ws, ["um", "ah"]) == {2, 6}
    assert cut.resolve_drops(ws, ["umbrella"]) == {7}
    assert cut.resolve_drops(ws, [{"text": "number, um,", "keep_last_words": 1}]) == {1}
    with pytest.raises(SystemExit):
        cut.resolve_drops(ws, ["mber"])                                    # a piece of a word is never a word
    with pytest.raises(SystemExit):
        cut.resolve_drops(ws, ["um", "um"])                                # struck twice, said once


def test_two_equal_strikes_take_two_occurrences_and_a_timed_one_takes_its_own():
    ws = [{"text": t, "start": i * .5, "end": i * .5 + .4} for i, t in enumerate("so really, really good".split())]
    assert cut.resolve_drops(ws, ["really,", "really,"][:1]) == {1}
    assert cut.resolve_drops(ws, [{"text": "really,", "start": 1.0}]) == {2}
    assert cut.resolve_drops(ws, [{"text": "really,", "start": 9.0}]) == {1}          # a time that matches nothing falls back to the first free one
    assert cut.resolve_drops([{"text": "a", "start": 0}, {"text": "a", "start": 1}], ["a", "a"]) == {0, 1}
