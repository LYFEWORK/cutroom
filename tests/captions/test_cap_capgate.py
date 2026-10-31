"""test_cap_capgate.py: the human approval gate (capgate.py): the GATE.md that build writes from a take's words, room check, spec and plan, and what apply makes of the one a
person approved (gate.json, devices.json, the fixes to words.json), then the soft build's Thoughts section, a person's edits to it and its CHECK lines.

The hand-written take comes from cap_takes.make_take. The soft build's tests plan a real take through spec_adapter and cluster_plan, on one neutral spoken window (WINDOW)
or on a short sentence, with the fixture looks' soft-build style over them.
"""
import json
import os
import subprocess
import sys

import pytest

import cap_helpers
import cap_takes
import capgate
import cluster_plan
import devices
import spec_adapter


def _gate_text(take_dir):
    return open(take_dir / "GATE.md").read()


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def test_the_gate_warns_when_the_planner_found_no_room_for_some_captions(tmp_path):
    take = cap_takes.make_take(tmp_path)
    plan = json.load(open(take / "plan.json"))
    capgate.build(str(take))
    assert "no room for" not in open(take / "GATE.md").read()
    plan["flags"] = ["cluster_plan", "zone:chest", "no_caption_room:3"]
    cap_takes.write(take / "plan.json", plan)
    capgate.build(str(take))
    text = open(take / "GATE.md").read()
    assert "WARNING: this take has no room for 3 caption(s)" in text and "## Room for captions" in text


def test_build_produces_all_sections(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    out = capgate.build(str(take_dir))
    assert out == str(take_dir / "GATE.md")
    text = _gate_text(take_dir)

    assert text.startswith("approved: no")
    assert "brand:" in text
    assert "take: sam-intro" in text
    assert "tier: FULL" in text
    assert "zone: top" in text
    assert "392px above the crown" in text
    assert "## Transcript" in text
    assert "1. Hey." in text
    assert "2. What's going on everybody?" in text
    assert "3. This is Sam over here at Acme" in text
    assert "## Hero words" in text
    # behind/front clusters show up...
    assert '"Hey"' in text
    assert '"Acme" / "Corp"' in text
    # ...but a plain "word"-mode event is not a hero cluster and must not appear as one.
    assert '"over"' not in text.split("## Overrides")[0].split("## Hero words")[1]
    assert "## Overrides" in text
    assert 'fix: "we aint" -> "we are not"' in text


def test_build_uses_look_json_when_present(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    cap_takes.write(take_dir / "look.json", cap_helpers.profile("varsans"))
    capgate.build(str(take_dir))
    assert "brand: Variable Sans" in _gate_text(take_dir)


def test_build_names_the_caption_style_the_take_was_made_with(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    capgate.build(str(take_dir))
    assert "caption style:" not in _gate_text(take_dir)                 # no look.json, no style
    cap_takes.write(take_dir / "look.json", cap_helpers.profile("upstatic"))
    capgate.build(str(take_dir))
    assert "caption style:" not in _gate_text(take_dir)                 # a look made with no style
    cap_takes.write(take_dir / "look.json", cap_helpers.profile("upstatic", "keyword-slam"))
    capgate.build(str(take_dir))
    assert "caption style: keyword-slam (a `style:` line under Overrides picks another)" in _gate_text(take_dir).splitlines()


def test_build_stops_on_missing_file(tmp_path):
    take_dir = tmp_path / "broken"
    take_dir.mkdir()
    with pytest.raises(capgate.EngineStop):
        capgate.build(str(take_dir))


# ---------------------------------------------------------------------------
# apply: approval gate
# ---------------------------------------------------------------------------

def test_apply_refuses_unapproved(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    capgate.build(str(take_dir))  # writes "approved: no"
    with pytest.raises(SystemExit) as exc:
        capgate.apply_gate(str(take_dir))
    assert exc.value.code == 3
    assert not (take_dir / "gate.json").exists()


def test_apply_refuses_when_approved_explicitly_no(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    (take_dir / "GATE.md").write_text("approved: no\n")
    with pytest.raises(SystemExit) as exc:
        capgate.apply_gate(str(take_dir))
    assert exc.value.code == 3


# ---------------------------------------------------------------------------
# apply: fix: rewrites words.json text only
# ---------------------------------------------------------------------------

def test_apply_fix_rewrites_the_word(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    gate_md = (
        "approved: yes\n\n"
        "brand: Acme\ntake: sam-intro\n\n"
        "## Overrides\n\n"
        'fix: "Hey" -> "Yo"\n'
    )
    (take_dir / "GATE.md").write_text(gate_md)

    before = json.load(open(take_dir / "words.json"))
    orig_start, orig_end = before["words"][0]["start"], before["words"][0]["end"]

    out = capgate.apply_gate(str(take_dir))
    assert out == str(take_dir / "gate.json")

    after = json.load(open(take_dir / "words.json"))
    assert after["words"][0]["text"] == "Yo"
    # timestamps untouched for a same-word-count fix
    assert after["words"][0]["start"] == orig_start
    assert after["words"][0]["end"] == orig_end

    backup = json.load(open(str(take_dir / "words.json.pre-gate")))
    assert backup["words"][0]["text"] == "Hey."

    gate = json.load(open(take_dir / "gate.json"))
    assert gate["approved"] is True
    assert gate["overrides"]["fixes"] == [{"from": "Hey", "to": "Yo"}]


def test_apply_fix_with_word_count_change_splices_without_crashing(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    gate_md = (
        "approved: yes\n\n## Overrides\n\n"
        'fix: "This is" -> "Right here is"\n'
    )
    (take_dir / "GATE.md").write_text(gate_md)
    capgate.apply_gate(str(take_dir))
    after = json.load(open(take_dir / "words.json"))
    texts = [w["text"] for w in after["words"]]
    assert "Right" in texts and "here" in texts and "is" in texts
    assert "This" not in texts


def test_apply_fix_not_found_warns_but_does_not_crash(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    gate_md = 'approved: yes\n\n## Overrides\n\nfix: "nonexistent phrase" -> "whatever"\n'
    (take_dir / "GATE.md").write_text(gate_md)
    out = capgate.apply_gate(str(take_dir))
    gate = json.load(open(out))
    assert gate["overrides"]["fixes"] == []
    assert any("not found verbatim" in w for w in gate.get("warnings", []))


# ---------------------------------------------------------------------------
# apply: zone/tier overrides land in gate.json (spec_adapter reads them; tier.json and spec.json are never edited)
# ---------------------------------------------------------------------------

def test_apply_zone_override_lands_in_gate_json_only(tmp_path):
    take_dir = cap_takes.make_take(tmp_path, zone="top")
    spec_before = open(take_dir / "spec.json").read()
    gate_md = "approved: yes\n\n## Overrides\n\nzone: chest\n"
    (take_dir / "GATE.md").write_text(gate_md)

    capgate.apply_gate(str(take_dir))

    gate = json.load(open(take_dir / "gate.json"))
    assert gate["overrides"]["zone"] == "chest"
    assert open(take_dir / "spec.json").read() == spec_before


def test_apply_tier_override_lands_in_gate_json_only(tmp_path):
    take_dir = cap_takes.make_take(tmp_path, tier="FULL")
    tier_before = open(take_dir / "tier.json").read()
    gate_md = "approved: yes\n\n## Overrides\n\ntier: REDUCED\n"
    (take_dir / "GATE.md").write_text(gate_md)

    capgate.apply_gate(str(take_dir))

    assert json.load(open(take_dir / "gate.json"))["overrides"]["tier"] == "REDUCED"
    assert open(take_dir / "tier.json").read() == tier_before


def test_apply_rejects_invalid_zone_value(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    gate_md = "approved: yes\n\n## Overrides\n\nzone: sideways\n"
    (take_dir / "GATE.md").write_text(gate_md)
    with pytest.raises(capgate.EngineStop):
        capgate.apply_gate(str(take_dir))


# ---------------------------------------------------------------------------
# apply: commented examples stay inert; hero_add/hero_drop and notes are recorded
# ---------------------------------------------------------------------------

def test_apply_leaves_commented_example_lines_inert(tmp_path):
    take_dir = cap_takes.make_take(tmp_path, zone="top")
    gate_md = capgate.build(str(take_dir)) and _gate_text(take_dir)
    # flip only the header; leave the Overrides block exactly as build() wrote it (still commented)
    gate_md = gate_md.replace("approved: no", "approved: yes", 1)
    (take_dir / "GATE.md").write_text(gate_md)

    capgate.apply_gate(str(take_dir))

    gate = json.load(open(take_dir / "gate.json"))
    assert gate["overrides"]["zone"] is None
    assert gate["overrides"]["tier"] is None
    assert gate["overrides"]["fixes"] == []
    # build()'s own explanatory prose must not leak into notes just because it sits above the
    # <!-- --> block under the Overrides heading.
    assert gate["notes"] == ""


def test_apply_records_hero_overrides_and_notes(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    gate_md = (
        "approved: yes\n\n## Overrides\n\n"
        'hero: "Corp"\n'
        "no-hero: Portland\n"
        "note: keep the pacing tight on the second sentence\n"
    )
    (take_dir / "GATE.md").write_text(gate_md)

    capgate.apply_gate(str(take_dir))

    gate = json.load(open(take_dir / "gate.json"))
    assert gate["overrides"]["hero_add"] == ["Corp"]
    assert gate["overrides"]["hero_drop"] == ["Portland"]
    # cluster_plan.py reads them from device_overrides
    dev = gate["overrides"]["devices"]["device_overrides"]
    assert dev["hero_add"] == ["Corp"] and dev["hero_drop"] == ["Portland"]
    assert "keep the pacing tight" in gate["notes"]
    assert "warnings" not in gate


def test_apply_warns_on_an_overrides_line_it_does_not_understand(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    (take_dir / "GATE.md").write_text(
        'approved: yes\n\n## Overrides\n\nfix: “Hey” -> “Yo”\nmake it louder\n')
    capgate.apply_gate(str(take_dir))
    gate = json.load(open(take_dir / "gate.json"))
    assert len(gate["warnings"]) == 2
    assert any("curly quotes" in w for w in gate["warnings"])
    assert any("make it louder" in w for w in gate["warnings"])
    assert gate["overrides"]["fixes"] == []


def test_apply_records_subject_none_and_allow(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    (take_dir / "GATE.md").write_text('approved: yes\n\n## Overrides\n\nsubject: none\nallow: "cheap"\n')
    capgate.apply_gate(str(take_dir))
    ov = json.load(open(take_dir / "gate.json"))["overrides"]
    assert ov["subject"] == "none" and ov["allow"] == ["cheap"]


def test_a_grade_line_is_a_line_the_gate_does_not_understand(tmp_path):
    """The engine has no color grade, so GATE.md offers no grade line and an approved one that carries `grade: off` is any other line the Overrides block does not know:
    ignored with the one warning such a line gets, and gate.json carries no grade."""
    take_dir = cap_takes.make_take(tmp_path)
    capgate.build(str(take_dir))
    assert "grade" not in _gate_text(take_dir).lower()
    (take_dir / "GATE.md").write_text("approved: yes\n\n## Overrides\n\ngrade: off\n")
    capgate.apply_gate(str(take_dir))
    gate = json.load(open(take_dir / "gate.json"))
    assert gate["warnings"] == ["Overrides line not understood, ignored: grade: off"]
    assert "grade" not in gate["overrides"] and "grade" not in gate


# ---------------------------------------------------------------------------
# apply: the Tier & zone section is honored when edited
# ---------------------------------------------------------------------------

def test_an_edit_in_the_tier_and_zone_section_is_an_override(tmp_path):
    take_dir = cap_takes.make_take(tmp_path, zone="top", tier="FULL")
    capgate.build(str(take_dir))
    text = _gate_text(take_dir).replace("approved: no", "approved: yes", 1)
    text = text.replace("tier: FULL\nzone: top", "tier: REDUCED\nzone: chest", 1)
    (take_dir / "GATE.md").write_text(text)
    capgate.apply_gate(str(take_dir))
    ov = json.load(open(take_dir / "gate.json"))["overrides"]
    assert ov["tier"] == "REDUCED" and ov["zone"] == "chest"


def test_an_untouched_tier_and_zone_section_overrides_nothing(tmp_path):
    take_dir = cap_takes.make_take(tmp_path, zone="top", tier="FULL")
    capgate.build(str(take_dir))
    (take_dir / "GATE.md").write_text(_gate_text(take_dir).replace("approved: no", "approved: yes", 1))
    capgate.apply_gate(str(take_dir))
    ov = json.load(open(take_dir / "gate.json"))["overrides"]
    assert ov["tier"] is None and ov["zone"] is None


def test_the_overrides_block_beats_an_edit_in_the_tier_and_zone_section(tmp_path):
    take_dir = cap_takes.make_take(tmp_path, zone="top", tier="FULL")
    capgate.build(str(take_dir))
    text = _gate_text(take_dir).replace("approved: no", "approved: yes", 1).replace("zone: top\nreason", "zone: chest\nreason", 1)
    text += "\nzone: top\n"
    (take_dir / "GATE.md").write_text(text)
    capgate.apply_gate(str(take_dir))
    assert json.load(open(take_dir / "gate.json"))["overrides"]["zone"] == "top"


def test_a_bad_tier_in_the_tier_and_zone_section_stops(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    capgate.build(str(take_dir))
    text = _gate_text(take_dir).replace("approved: no", "approved: yes", 1).replace("tier: FULL", "tier: HUGE", 1)
    (take_dir / "GATE.md").write_text(text)
    with pytest.raises(capgate.EngineStop):
        capgate.apply_gate(str(take_dir))


# ---------------------------------------------------------------------------
# apply: idempotent: fixes start from the pre-gate transcript, devices.json always follows GATE.md
# ---------------------------------------------------------------------------

def test_apply_twice_gives_the_same_words_and_a_removed_fix_is_undone(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    (take_dir / "GATE.md").write_text('approved: yes\n\n## Overrides\n\nfix: "Hey" -> "Yo"\n')
    capgate.apply_gate(str(take_dir))
    capgate.apply_gate(str(take_dir))
    gate = json.load(open(take_dir / "gate.json"))
    assert gate["overrides"]["fixes"] == [{"from": "Hey", "to": "Yo"}] and "warnings" not in gate
    assert json.load(open(take_dir / "words.json"))["words"][0]["text"] == "Yo"
    (take_dir / "GATE.md").write_text("approved: yes\n\n## Overrides\n")
    capgate.apply_gate(str(take_dir))
    assert json.load(open(take_dir / "words.json"))["words"][0]["text"] == "Hey."
    assert json.load(open(take_dir / "gate.json"))["overrides"]["fixes"] == []


def test_a_removed_page_line_removes_devices_json(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    (take_dir / "GATE.md").write_text('approved: yes\n\n## Devices\n\npage: 1.0-1.7 "ads"\n')
    capgate.apply_gate(str(take_dir))
    assert (take_dir / "devices.json").exists()
    (take_dir / "GATE.md").write_text("approved: yes\n\n## Devices\n")
    capgate.apply_gate(str(take_dir))
    assert not (take_dir / "devices.json").exists()
    assert json.load(open(take_dir / "gate.json"))["overrides"]["devices"]["device_overrides"] == {}


# ---------------------------------------------------------------------------
# build: never overwrites a GATE.md, marks heroes, warns on do-not-say words
# ---------------------------------------------------------------------------

def test_build_backs_up_an_existing_gate_md_before_rewriting(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    (take_dir / "GATE.md").write_text("approved: yes\nmy edits\n")
    capgate.build(str(take_dir))
    backups = [f for f in os.listdir(take_dir) if f.startswith("GATE.md.bak-")]
    assert len(backups) == 1
    assert (take_dir / backups[0]).read_text() == "approved: yes\nmy edits\n"
    assert _gate_text(take_dir).startswith("approved: no")


def test_build_stars_the_hero_row_of_a_cluster(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    plan = json.load(open(take_dir / "plan.json"))
    plan["events"][1]["words"] = [{"text": "Acme", "hero": False}, {"text": "Corp", "hero": True}]
    json.dump(plan, open(take_dir / "plan.json", "w"))
    capgate.build(str(take_dir))
    assert '"Acme" / *"Corp"*' in _gate_text(take_dir)


def test_build_records_the_auto_values_the_tier_and_zone_section_is_compared_against(tmp_path):
    take_dir = cap_takes.make_take(tmp_path, zone="top", tier="REDUCED")
    capgate.build(str(take_dir))
    assert "<!-- auto: tier=REDUCED zone=top -->" in _gate_text(take_dir)


def test_build_warns_when_a_caption_shows_a_do_not_say_word(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    look = cap_helpers.profile("varsans")
    assert look["do_not_say"] == ["cheap"]
    cap_takes.write(take_dir / "look.json", look)
    plan = json.load(open(take_dir / "plan.json"))
    plan["events"][1]["words"] = [{"text": "Cheap"}, {"text": "Corp"}]         # the front cluster at 2.70 s now says the look's do-not-say word
    cap_takes.write(take_dir / "plan.json", plan)
    capgate.build(str(take_dir))
    text = _gate_text(take_dir)
    assert "## Do not say" in text and 'allow: "cheap"' in text
    assert 'WARNING: "cheap" is on your do-not-say list and a caption shows it at 2.70s.' in text


def test_a_take_whose_captions_say_no_do_not_say_word_has_no_such_section(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    cap_takes.write(take_dir / "look.json", cap_helpers.profile("varsans"))
    capgate.build(str(take_dir))
    assert "## Do not say" not in _gate_text(take_dir)


def test_banned_hits_matches_whole_words_phrases_and_honors_allow():
    plan = {"events": [{"mode": "front", "start": 1.0, "words": [{"text": "Very", "at": 1.0}, {"text": "cheap", "at": 1.2}]},
                       {"mode": "page", "start": 3.0, "text": "no refunds"}]}
    assert capgate.banned_hits(["cheap"], plan) == [("cheap", 1.2)]
    assert capgate.banned_hits(["cheap"], plan, allow=["Cheap"]) == []
    assert capgate.banned_hits(["cheaper"], plan) == []              # whole word only: a longer word is no hit
    assert capgate.banned_hits(["chea"], plan) == []                 # nor is a fragment of one
    assert capgate.banned_hits(["no refunds"], plan) == [("no refunds", 3.0)]
    assert capgate.banned_hits(["very cheap"], plan)[0][0] == "very cheap"   # a phrase across two rows
    assert capgate.banned_hits(["cheap"], {"events": [{"mode": "front", "start": 5.0, "words": [{"text": "cheapest", "at": 5.0}]}]}) == []


def test_apply_missing_gate_md_stops(tmp_path):
    take_dir = cap_takes.make_take(tmp_path)
    with pytest.raises(capgate.EngineStop):
        capgate.apply_gate(str(take_dir))


# ---------------------------------------------------------------------------
# the soft build: the Thoughts section, a person's edits to it, and its CHECK lines
# ---------------------------------------------------------------------------

SOFT_FACE = {"cx": 540, "cy": 700, "w": 300, "h": 300}
SOFT_TRACK = {"samples": {str(s): SOFT_FACE for s in range(0, 120)}, "median": SOFT_FACE}

# Thirty-seven spoken words of plain English as a recognizer times them: three sentences with a pause after each and a comma pause inside two of them, and no pause
# between "it" and "than" (the second thought ends on "it" and the third starts on "than" at once).
WINDOW_TIMES = [
    ("That's", 0.50, 0.84), ("when", 0.86, 1.10), ("the", 1.12, 1.22), ("doors", 1.24, 1.52), ("open.", 1.54, 1.96),
    ("So", 2.42, 2.58), ("running", 2.60, 2.98), ("a", 3.00, 3.08), ("bakery,", 3.10, 3.62), ("there's", 3.84, 4.14),
    ("a", 4.16, 4.24), ("lot", 4.26, 4.44), ("more", 4.46, 4.70), ("behind", 4.72, 5.06), ("it", 5.08, 5.22),
    ("than", 5.24, 5.46), ("just", 5.48, 5.74), ("having", 5.76, 6.10), ("someone", 6.12, 6.50), ("bake", 6.52, 6.80),
    ("the", 6.82, 6.92), ("bread.", 6.94, 7.40),
    ("We", 7.90, 8.06), ("answer", 8.08, 8.44), ("every", 8.46, 8.74), ("call,", 8.76, 9.10), ("we", 9.30, 9.44),
    ("book", 9.46, 9.76), ("every", 9.78, 10.06), ("order,", 10.08, 10.50), ("and", 10.70, 10.84), ("we", 10.86, 11.00),
    ("follow", 11.02, 11.36), ("up", 11.38, 11.52), ("the", 11.54, 11.64), ("same", 11.66, 11.94), ("day.", 11.96, 12.40),
]
WINDOW = [{"text": t, "start": s, "end": e, "filler": False} for t, s, e in WINDOW_TIMES]


def said(text, start=1.0, step=0.3):
    return [{"text": w, "start": round(start + k * step, 3), "end": round(start + k * step + 0.25, 3), "filler": False} for k, w in enumerate(text.split())]


def _soft_take(tmp_path, words, brand="varsans"):
    """a soft build take as run_captions.sh leaves it at the gate: its words, the room check, look.json made with the soft-build style, the spec and the plan"""
    take = tmp_path / "soft-take"
    take.mkdir(parents=True)
    cap_takes.write(take / "words.json", {"source": "whisper", "transcript": " ".join(w["text"] for w in words), "words": words})
    cap_takes.write(take / "tier.json", cap_helpers.TIER_DOC)
    cap_takes.write(take / "look.json", cap_helpers.profile(brand, "soft-build"))
    cap_takes.write(take / "plan.json", _soft_plan(take))
    return take


def _soft_plan(take):
    """the spec and the plan run_captions.sh makes next: spec.json from tier.json, look.json and gate.json (when the gate was applied), then cluster_plan"""
    gate = json.load(open(take / "gate.json")) if (take / "gate.json").exists() else None
    spec = spec_adapter.build(cap_helpers.TIER_DOC, json.load(open(take / "look.json")), gate=gate)
    cap_takes.write(take / "spec.json", spec)
    words = json.load(open(take / "words.json"))["words"]
    cut = round(words[-1]["end"] + 0.3, 3)
    return cluster_plan.build(words, SOFT_TRACK, spec, cut, int(cut * 30) + 1)


def _thought_lines(take):
    """the Thoughts section's lines, as GATE.md shows them"""
    section = _gate_text(take).split("## Thoughts", 1)[1].split("\n## ", 1)[0]
    return [line for line in section.splitlines() if line.startswith("- ")]


def _edit_thoughts(take, *pairs):
    text = _gate_text(take).replace("approved: no", "approved: yes", 1)
    for old, new in pairs:
        assert old in text, old
        text = text.replace(old, new, 1)
    (take / "GATE.md").write_text(text)


def _set_thoughts(take, lines, devices=""):
    """a person replaces every thought line with these, adds `devices` lines under Devices, and approves"""
    old = "\n".join(_thought_lines(take))
    text = _gate_text(take).replace("approved: no", "approved: yes", 1).replace(old, "\n".join(f"- {line}" for line in lines), 1)
    if devices:
        text = text.replace("-->\n\n## Pacing", f"-->\n{devices}\n\n## Pacing", 1)
    (take / "GATE.md").write_text(text)


def _rows(plan):
    return [[(r["role"], r["text"], r["bar"]) for r in e["words"]] for e in plan["events"]]


WINDOW_LINES = ["- 0.50s That's when | the doors open.",
                "- 2.42s So running a bakery, there's | a lot more behind it",
                "- 5.24s than just having someone bake | the bread.",
                "- 7.90s We answer every call, | we book every order, [bar]",
                "- 10.70s and we follow up | the same day."]


def test_the_gate_shows_each_soft_thought_as_one_line_with_its_bar(tmp_path):
    """GATE.md gains a Thoughts section after the hero list: one line a thought, lead-in | payoff | tail, the words exactly as spoken (case and punctuation as the transcript
    has them, not as the payoff draws them) after the first word's spoken start, and [bar] on the one payoff on the bar. A take without the soft build has no such section"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    text = _gate_text(take)
    assert text.index("## Hero words") < text.index("## Thoughts") < text.index("## Devices")
    assert ("One caption per line: lead-in | payoff | tail. [bar] marks the one payoff on a bar. Move a | to regroup the words; the words stay "
            "as spoken.") in text
    assert _thought_lines(take) == WINDOW_LINES
    plan = json.load(open(take / "plan.json"))
    assert [e["line"] for e in plan["events"]] == [line.split(" ", 2)[2] for line in _thought_lines(take)], "each soft event carries its gate line"
    caps = _soft_take(tmp_path / "caps", said("We pack every order right here. They come out great."), brand="upstatic")
    capgate.build(str(caps))
    shown = " ".join(line.split(" ", 2)[2].replace(" [bar]", "") for line in _thought_lines(caps)).replace(" | ", " ")
    assert shown == "We pack every order right here. They come out great.", "the transcript's own case, though the upper-case look draws its payoffs in capitals"
    drawn = [r["text"] for e in json.load(open(caps / "plan.json"))["events"] for r in e["words"] if r["role"] == "pay"]
    assert drawn and all(t == t.upper() for t in drawn)
    (tmp_path / "plain").mkdir()
    plain = cap_takes.make_take(tmp_path / "plain")
    capgate.build(str(plain))
    assert "## Thoughts" not in _gate_text(plain)


def test_a_soft_take_points_its_hero_words_at_the_thoughts(tmp_path):
    """a soft build take has no hero words to change: its Hero words line points at the Thoughts section, where its payoffs are regrouped. A cluster take keeps the line it
    always had"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    text = _gate_text(take)
    assert ("A starred part (*\"words\"*) is its thought's payoff. Regroup the words and move the bar in the Thoughts section below: a soft build take "
            "reads no `hero:` or `no-hero:` line.") in text
    assert "Change it with `hero:` or `no-hero:`" not in text
    (tmp_path / "plain").mkdir()
    plain = cap_takes.make_take(tmp_path / "plain")
    capgate.build(str(plain))
    assert 'A starred word (*"word"*) is the hero of its cluster. Change it with `hero:` or `no-hero:` under Overrides.' in _gate_text(plain)


def test_a_line_the_soft_build_does_not_read_is_a_warning_on_a_soft_take(tmp_path):
    """the soft build has no hero words, draws every thought in front of the person at the chest and draws no glow, so on a soft take each hero:, no-hero:, front:, behind:,
    finale:, glow: and zone: top line is a warning in gate.json (and NOTES.md), never dropped without a word. A cluster take reads them as it always did"""
    devices = 'front: "behind it"\nbehind: "a lot more"\nfinale: "So running a bakery" | "we book every order"\nglow: on'
    overrides = 'zone: top\nhero: "bakery"\nno-hero: "lot"\n'
    soft = "a soft build take draws every thought in front of the person, under the chin"
    want = ['hero: "bakery" is ignored: a soft build take has no hero words; regroup its thoughts in the Thoughts section',
            'no-hero: "lot" is ignored: a soft build take has no hero words; regroup its thoughts in the Thoughts section',
            f'front: "behind it" is ignored: {soft}', f'behind: "a lot more" is ignored: {soft}',
            f'finale: "So running a bakery" | "we book every order" is ignored: {soft}',
            "glow: on is ignored: a soft build take has no glow", "zone: top is ignored: a soft build take draws its thoughts at the chest"]
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    _set_thoughts(take, [line.split(" ", 2)[2] for line in _thought_lines(take)], devices=devices)
    (take / "GATE.md").write_text(_gate_text(take) + overrides)
    capgate.apply_gate(str(take))
    assert json.load(open(take / "gate.json"))["warnings"] == want
    plain = cap_takes.make_take(tmp_path)
    capgate.build(str(plain))
    text = _gate_text(plain).replace("approved: no", "approved: yes", 1).replace("-->\n\n## Pacing", f"-->\n{devices}\n\n## Pacing", 1)
    (plain / "GATE.md").write_text(text + overrides)
    capgate.apply_gate(str(plain))
    gate = json.load(open(plain / "gate.json"))
    assert "warnings" not in gate and gate["overrides"]["hero_add"] == ["bakery"] and gate["overrides"]["zone"] == "top"


def test_an_untouched_thoughts_section_regroups_nothing(tmp_path):
    """approving the gate without editing a thought leaves the split to the planner (a fix: line may have changed the words since), so gate.json carries no thoughts"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    _edit_thoughts(take)
    capgate.apply_gate(str(take))
    assert "thoughts" not in json.load(open(take / "gate.json"))["overrides"]["devices"]["device_overrides"]


def test_an_emptied_or_commented_out_thoughts_section_leaves_the_split_to_the_planner(tmp_path):
    """a person who deletes every thought line, or puts them all in a comment, hands the planner no lines: it splits the words itself and the plan is made (no lines at all
    would stop it on the first word, which is in no line)"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    built, lines = _gate_text(take), "\n".join(_thought_lines(take))
    for edit in ("", f"<!--\n{lines}\n-->"):
        (take / "GATE.md").write_text(built.replace("approved: no", "approved: yes", 1).replace(lines, edit, 1))
        capgate.apply_gate(str(take))
        assert "thoughts" not in json.load(open(take / "gate.json"))["overrides"]["devices"]["device_overrides"], repr(edit)
        assert [e["line"] for e in _soft_plan(take)["events"]] == [line.split(" ", 2)[2] for line in lines.splitlines()]


def test_gate_edits_regroup_words_and_move_the_bar(tmp_path):
    """moving the | marks and words between lines regroups the thoughts and moving [bar] moves the bar: the edited lines reach the planner through gate.json and spec.json
    (device_overrides.thoughts) and the plan draws them, every word still on its own spoken time. The payoff that lost the bar gets its icon back"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    _edit_thoughts(take, ("- 2.42s So running a bakery, there's | a lot more behind it", "- 2.42s So running a bakery, | there's a lot more [bar]"),
                   ("- 5.24s than just having someone bake | the bread.", "- 4.72s behind it than just having someone | bake the bread."),
                   ("we book every order, [bar]", "we book every order,"))
    capgate.apply_gate(str(take))
    lines = ["That's when | the doors open.", "So running a bakery, | there's a lot more [bar]", "behind it than just having someone | bake the bread.",
             "We answer every call, | we book every order,", "and we follow up | the same day."]
    assert json.load(open(take / "gate.json"))["overrides"]["devices"]["device_overrides"]["thoughts"] == lines
    plan = _soft_plan(take)
    assert json.load(open(take / "spec.json"))["device_overrides"]["thoughts"] == lines
    assert _rows(plan) == [[("lead", "That's when", False), ("pay", "the doors open.", False)],
                           [("lead", "So running a bakery,", False), ("pay", "there's a lot more", True)],
                           [("lead", "behind it than just having someone", False), ("pay", "bake the bread.", False)],
                           [("lead", "We answer every call,", False), ("pay", "we book every order,", False)],
                           [("lead", "and we follow up", False), ("pay", "the same day.", False)]]
    assert [e["line"] for e in plan["events"]] == lines
    assert plan["events"][3]["words"][1]["icon"] == "calendar" and "dropped" not in plan["events"][3]["words"][1]
    assert [a for e in plan["events"] for r in e["words"] for a in r["ats"]] == [w["start"] for w in WINDOW]
    _edit_thoughts(take, ("there's a lot more [bar]", "there's a lot more"))
    capgate.apply_gate(str(take))
    assert not any(r["bar"] for e in _soft_plan(take)["events"] for r in e["words"]), "no [bar] line, no bar: a person can take it off"


def test_a_changed_gate_line_that_no_longer_matches_stops_with_one_line(tmp_path):
    """a thought line whose words no longer match the take's words (a word changed, added or dropped) stops the plan with the splitter's one line, prefixed so the person
    knows it came from GATE.md, never a traceback or a usage line"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    _edit_thoughts(take, ("- 2.42s So running a bakery, there's", "- 2.42s So running a bakeries, there's"))
    capgate.apply_gate(str(take))
    with pytest.raises(capgate.EngineStop) as stop:
        _soft_plan(take)
    assert str(stop.value) == "GATE.md thoughts: line 2 has 'bakeries,' where the words say 'bakery,' (3.10 s)"
    words = json.load(open(take / "words.json"))["words"]
    cap_takes.write(take / "track.json", SOFT_TRACK)
    script = os.path.join(cap_helpers.REPO, "engine", "captions", "cluster_plan.py")
    r = subprocess.run([sys.executable, "-B", script, str(take / "words.json"), str(take / "track.json"), str(take / "out.json"), "--spec", str(take / "spec.json"),
                        "--cut", str(round(words[-1]["end"] + 0.3, 3))], capture_output=True, text=True)
    assert r.returncode == 2 and r.stderr == "STOP: GATE.md thoughts: line 2 has 'bakeries,' where the words say 'bakery,' (3.10 s)\n" and not r.stdout
    _edit_thoughts(take, ("- 2.42s So running a bakeries, there's", "- 2.42s So running a bakery, there's"),
                   ("- 10.70s and we follow up | the same day.\n", ""))
    capgate.apply_gate(str(take))
    with pytest.raises(capgate.EngineStop, match=r"^GATE.md thoughts: 'and' at 10.70 s is in no line$"):
        _soft_plan(take)


def test_a_gate_strike_line_reaches_the_soft_path_when_its_span_is_the_payoff_the_person_kept(tmp_path):
    """a strike: line under Devices crosses out its phrase in the soft build too: the payoff the person kept is struck when it is that span, and not when the person's line
    splits the span across the lead-in and the payoff"""
    take = _soft_take(tmp_path, said("Some shops still sell flimsy plastic parts to every team."))
    capgate.build(str(take))
    _set_thoughts(take, ["Some shops still sell | flimsy plastic parts | to every team."], devices='strike: "flimsy plastic parts"')
    capgate.apply_gate(str(take))
    assert json.load(open(take / "gate.json"))["overrides"]["devices"]["device_overrides"]["strike_add"] == ["flimsy plastic parts"]
    (e,) = _soft_plan(take)["events"]
    assert [(r["role"], r["text"], bool(r.get("strike"))) for r in e["words"]] == [
        ("lead", "Some shops still sell", False), ("pay", "flimsy plastic parts", True), ("tail", "to every team.", False)]
    _set_thoughts(take, ["Some shops still sell flimsy | plastic parts | to every team."])
    capgate.apply_gate(str(take))
    (e,) = _soft_plan(take)["events"]
    assert [r["text"] for r in e["words"]] == ["Some shops still sell flimsy", "plastic parts", "to every team."]
    assert not any(r.get("strike") for r in e["words"]), "the span split across the lead-in and the payoff strikes nothing"


def test_the_gate_names_what_a_soft_payoff_lost_in_one_check_line_each(tmp_path):
    """a soft row that could not keep a device says so under the thoughts, one CHECK line with the time and the words: an icon that yielded to the bar, and a platform name
    the payoff could not hold, named by its own word so the line never reads as if a drawn logo were missing"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    assert 'CHECK 9.30s "we book every order," lost its icon: the bar took its place' in _gate_text(take).splitlines()
    caps = _soft_take(tmp_path / "caps", said("That month we got 41 leads.") + said("Read hundreds of verified customer testimonials on Google.", start=4.0),
                      brand="upstatic")
    capgate.build(str(caps))
    checks = [line for line in _gate_text(caps).splitlines() if line.startswith("CHECK ")]
    assert checks == ['CHECK 6.10s "Google." lost its logo: it did not fit'], checks
    two = _soft_take(tmp_path / "two", said("Find all of us on Instagram and Facebook."))
    (e,) = json.load(open(two / "plan.json"))["events"]
    assert [(r["text"], r.get("logo"), r.get("dropped")) for r in e["words"]] == [("Find all of us on Instagram", None, None),
                                                                              ("and Facebook.", "facebook", ["logo"])], "one name draws its logo, one cannot"
    capgate.build(str(two))
    checks = [line for line in _gate_text(two).splitlines() if line.startswith("CHECK ")]
    assert checks == ['CHECK 2.50s "Instagram" lost its logo: it did not fit'], checks


def test_a_thought_too_quick_to_read_is_a_check_line(tmp_path):
    """a thought the planner marked short (on screen under 1.0 s, or its payoff's first word fully drawn under 0.4 s before it leaves) is one CHECK line under the thoughts,
    worded as a plain sentence, at its first word's spoken start and in its words as spoken; the neutral window carries none"""
    take = _soft_take(tmp_path, said("Big news."))
    capgate.build(str(take))
    assert ('CHECK 1.00s "Big news." is too quick to read in time (on screen under 1 s, or its payoff under 0.4 s): regroup its line, or let it stand'
            in _gate_text(take).splitlines())
    window = _soft_take(tmp_path / "window", WINDOW)
    capgate.build(str(window))
    assert "too quick to read" not in _gate_text(window)


def test_a_named_red_word_drawn_in_its_lines_own_color_is_a_check_line(tmp_path):
    """a red word the look (neg_words) or a person (GATE.md neg:) named that its thought's payoff does not hold draws in its line's own color, the look's text color: one
    CHECK line under the thoughts, the word as spoken and its time. A built-in red word off the payoff says nothing: it was only ever drawn on a cluster's hero row"""
    take = _soft_take(tmp_path, said("Broken ads cost you every single lead you get."))        # the varsans look names "broken"
    capgate.build(str(take))
    assert ('CHECK 1.00s "Broken" draws in the line\'s own color: red is drawn only on a payoff, and its thought\'s payoff does not hold it; regroup its '
            "line, or let it stand") in _gate_text(take).splitlines()
    assert "draws white" not in _gate_text(take)
    built_in = _soft_take(tmp_path / "built-in", said("Scam shops cost you every single order you get."), brand="upstatic")
    capgate.build(str(built_in))
    assert "red is drawn only on a payoff" not in _gate_text(built_in)


def test_a_last_word_a_layout_event_cuts_off_is_a_check_line(tmp_path):
    """the reading time times only a thought's first word and its payoff's. In ordinary speech the neutral window carries no such line, though "a lot more behind it" leaves
    0.03 s after "it" is spoken (its thoughts crossfade). A layout event the gate accepts 0.1 s after "it" cuts "it" off: its thought is gone by the event before the word is
    drawn (thoughts.last_word_cut_off), the plan marks the thought last_unread, and checks prints one CHECK line with the word and its time, which the GATE.md built from
    the first plan does not show"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    assert not any("is not fully drawn" in x for x in _gate_text(take).splitlines()) and capgate.checks(str(take)) == [], "the neutral window carries none"
    it = next(w for w in WINDOW if w["text"] == "it")
    someone = next(w for w in WINDOW if w["text"] == "someone")
    page = {"mode": "page", "start": round(it["start"] + 0.1, 3), "end": round(someone["start"] + 0.38, 3), "text": "Results"}   # "than just having someone" sits under it
    cap_takes.write(take / "gate.json", {"overrides": {"devices": {"layout_events": [page], "device_overrides": {}}}})
    plan = _soft_plan(take)
    cap_takes.write(take / "plan.json", plan)
    (cut,) = [e for e in plan["events"] if e.get("last_unread")]
    assert cut["words"][-1]["ats"][-1] == it["start"] and cut["end"] <= page["start"]
    line = f'CHECK {it["start"]:.2f}s "it" is not fully drawn before its thought leaves: regroup its line, or let it stand'
    assert [x for x in capgate.checks(str(take)) if "is not fully drawn" in x] == [line]


def test_a_legibility_flag_is_a_check_line_under_the_thoughts(tmp_path):
    """legible.py flags a thought that stays over a bright picture even at its lowest spot (legibility:<its first arrival>), and one whose picture it could not read: GATE.md
    prints each as a CHECK line naming the words"""
    take = _soft_take(tmp_path, WINDOW)
    plan = json.load(open(take / "plan.json"))
    second, third = plan["events"][1], plan["events"][2]
    plan["flags"] += [f"legibility:{second['start']:.2f}", f"legibility:{third['start']:.2f}"]
    plan["legible"] = {"thoughts": [
        {"at": round(plan["events"][0]["start"], 2), "luma": 40.0, "remedy": "ok"},
        {"at": round(second["start"], 2), "luma": 240.0, "remedy": "move", "moved_to": 1238, "luma_moved": 236.5, "remedy_moved": "move"},
        {"at": round(third["start"], 2), "error": "ffmpeg could not read mez.mp4: broken"}]}
    cap_takes.write(take / "plan.json", plan)
    capgate.build(str(take))
    lines = _gate_text(take).splitlines()
    assert ('CHECK 2.37s "So running a bakery, there\'s a lot more behind it" may be hard to read: the picture under it is bright even at its lowest spot'
            in lines)
    assert 'CHECK 5.19s "than just having someone bake the bread." could not be checked for legibility: ffmpeg could not read mez.mp4: broken' in lines


def test_checks_prints_what_the_plan_raises_that_gate_md_does_not_show_in_the_words_as_spoken(tmp_path):
    """capgate.py checks <take>: GATE.md is built once, from the first plan, so the CHECK lines a person's edits or the plan rebuilt after the gate raise would never be
    seen; checks prints the plan's CHECK lines that GATE.md does not already show (run_captions.sh echoes them, deliver.sh files them in NOTES.md). Every CHECK line quotes
    the words as spoken, as the Thoughts lines show them, never an upper-case look's drawn capitals"""
    take = _soft_take(tmp_path, said("Just give us a call. We answer every single one."), brand="upstatic")
    capgate.build(str(take))
    assert capgate.checks(str(take)) == [], "the plan GATE.md was built from raises nothing new"
    _set_thoughts(take, ["Just give us | a call. [bar]", "We answer | every single one."])
    capgate.apply_gate(str(take))
    plan = _soft_plan(take)
    first = plan["events"][0]
    assert [r["text"] for r in first["words"]] == ["Just give us", "A CALL."]
    plan["flags"].append(f"legibility:{first['start']:.2f}")
    plan["legible"] = {"thoughts": [{"at": round(first["start"], 2), "luma": 240.0, "remedy": "move", "moved_to": 1238,
                                     "luma_moved": 238.0, "remedy_moved": "move"}]}
    cap_takes.write(take / "plan.json", plan)
    want = ['CHECK 0.95s "Just give us a call." may be hard to read: the picture under it is bright even at its lowest spot',
            # the person's lines leave "a call." fully drawn under 0.4 s before the next line starts: too quick to read
            'CHECK 1.00s "Just give us a call." is too quick to read in time (on screen under 1 s, or its payoff under 0.4 s): regroup its line, or let it stand',
            'CHECK 1.90s "a call." lost its icon: the bar took its place']
    assert capgate.checks(str(take)) == want
    script = os.path.join(cap_helpers.REPO, "engine", "captions", "capgate.py")
    r = subprocess.run([sys.executable, "-B", script, "checks", str(take)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.splitlines() == want
    (tmp_path / "bare").mkdir()
    assert capgate.checks(str(tmp_path / "bare")) == [], "no GATE.md or no plan: nothing to say"


def test_a_legibility_flag_whose_thought_the_devices_stage_took_away_raises_no_check_line(tmp_path):
    """checks reads the plan after the devices stage, as the render does: a thought a page covers is gone from it (devices.py apply drops it, its words are on the page) while
    the flag legible.py raised on it before is still in the flags. No thought is left to look at, so no CHECK line names one; a flag on a thought that stays still prints"""
    take = _soft_take(tmp_path, WINDOW)
    capgate.build(str(take))
    plan = json.load(open(take / "plan.json"))
    second, third = plan["events"][1], plan["events"][2]
    plan["flags"] += [f"legibility:{second['start']:.2f}", f"legibility:{third['start']:.2f}"]
    page = {"mode": "page", "start": 2.30, "end": 5.10, "text": "Results"}             # covers the second thought, which starts at 2.37 and is gone 0.04 s before it ends
    plan = devices.apply(plan, [page], SOFT_TRACK)
    assert [round(e["start"], 2) for e in plan["events"] if e.get("soft")] == [0.45, 5.19, 7.85, 10.65], "the page took the thought that starts at 2.37"
    cap_takes.write(take / "plan.json", plan)
    assert capgate.checks(str(take)) == ['CHECK 5.19s "than just having someone bake the bread." may be hard to read: '
                                         "the picture under it is bright even at its lowest spot"]


def test_a_thought_with_no_gate_line_is_quoted_by_the_words_it_draws(tmp_path):
    """cluster_plan writes each soft thought's GATE.md line into its event, and a CHECK line quotes the words from it; a thought with none (a plan written by hand) is quoted by
    the words its rows draw, so a one line thought is named by its words, never by an empty pair of quotes"""
    take = _soft_take(tmp_path, said("Big news."))
    capgate.build(str(take))
    plan = json.load(open(take / "plan.json"))
    (e,) = plan["events"]
    assert [r["role"] for r in e["words"]] == ["lead"], "one line"
    del e["line"]
    e["words"][0]["dropped"] = ["icon"]
    plan["flags"].append(f"legibility:{e['start']:.2f}")
    cap_takes.write(take / "plan.json", plan)
    assert capgate.checks(str(take)) == ['CHECK 0.95s "Big news." may be hard to read: the picture under it is bright even at its lowest spot',
                                         'CHECK 1.00s "Big news." lost its icon: it did not fit']
