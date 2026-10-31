"""test_cap_devices.py: caption devices.

Covers: cluster_plan.py's word-device tagging (glow, neg, count, strike, pin, spaced) and its guarantee that a spec with no device fields plans exactly as it always did;
devices.py's propose() (all four layout-event triggers) and apply() (clamp or drop, cta replacement, frame and igpost facetrack fields, the soft build's thoughts); the pill
icon order (a look's icon_words, then its pill_icons rows, then the engine's own table), read from the two fixture looks; and capgate.py's Devices section: what build writes
(layout-event proposals only when the take's look has devices on) and the override forms round-tripping through apply.
"""
import copy
import json

import pytest

import cap_helpers
import cap_takes
import capgate
import cluster_plan
import devices
import lookprofile
import spec_adapter

FPS = 30
TRACK = {"samples": {str(s): {"cx": 540, "cy": 1400, "w": 200, "h": 200} for s in range(0, 30)},
         "median": {"cx": 540, "cy": 1400, "w": 200, "h": 200}}


def W(text, start, end):
    return {"text": text, "start": start, "end": end, "filler": False}


def base_spec(**over):
    """A layout-only spec: the varsans fixture's accent, font and layout numbers and none of the device switches, so every word device is off until a test turns it on."""
    fixture = cap_helpers.spec("varsans")
    s = {k: fixture[k] for k in ("accent", "zone", "width", "hero_max", "row_px", "chin_gap", "font")}
    s["case"] = "upper"
    s.update(over)
    return s


def hero_row(plan, text_substr):
    """The row (of any front/behind event) whose text contains `text_substr` (case-insensitive)."""
    for e in plan["events"]:
        if e.get("mode") not in ("front", "behind"):
            continue
        for w in e["words"]:
            if text_substr.lower() in w["text"].lower():
                return w
    raise AssertionError(f"no row containing {text_substr!r} in {[e.get('words') for e in plan['events']]}")


def all_rows(plan):
    return [w for e in plan["events"] if e.get("mode") in ("front", "behind") for w in e["words"]]


DEVICE_KEYS = ("glow", "neg", "count", "strike", "pin", "spaced")


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: neg tagging
# ---------------------------------------------------------------------------------------------

def test_neg_tags_a_builtin_word_as_the_cluster_plan_hero():
    words = [W("This", 0.0, 0.2), W("deal", 0.2, 0.4), W("is", 0.4, 0.5), W("weak.", 0.5, 0.8)]
    negative = cap_helpers.profile("upstatic")["chip_neg"]        # the look's own negative color, not the engine's fallback red
    plan = cluster_plan.build(words, TRACK, base_spec(look={"chip_neg": negative}), cut_s=1.1, total_frames=33)
    row = hero_row(plan, "weak")
    assert row.get("neg") is True
    assert row["color"] == negative and plan["chipNeg"] == negative
    assert "glow" not in row   # neg colors the row chipNeg, not accent: glow never applies to it


def test_a_neg_row_takes_the_engines_own_red_when_the_look_names_none():
    words = [W("This", 0.0, 0.2), W("deal", 0.2, 0.4), W("is", 0.4, 0.5), W("weak.", 0.5, 0.8)]
    spec = base_spec()
    plan = cluster_plan.build(words, TRACK, spec, cut_s=1.1, total_frames=33)
    assert hero_row(plan, "weak")["color"] == cluster_plan.switches(spec).chip_neg
    assert "chipNeg" not in plan


def test_neg_fires_on_a_not_or_no_plus_hero_pattern_even_off_the_builtin_list():
    # "cheap" is not in the built-in set: only the immediate "not"+hero grammar should catch it.
    words = [W("It's", 0.0, 0.2), W("not", 0.2, 0.4), W("cheap.", 0.4, 0.7)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.0, total_frames=30)
    row = hero_row(plan, "cheap")
    assert row.get("neg") is True


def test_the_looks_neg_words_extend_the_builtin_set():
    # "So" is a stop word (excluded from hero candidates), so the other word is unambiguously the hero whatever the lengths. The varsans fixture names "broken"; no
    # built-in list has it, so it is red only when the look's list is in the spec.
    neg_words = cap_helpers.spec("varsans")["neg_words"]
    assert neg_words == ["broken"]
    words = [W("So", 0.0, 0.2), W("broken.", 0.2, 0.6)]
    plan_off = cluster_plan.build(words, TRACK, base_spec(), cut_s=0.9, total_frames=27)
    assert "neg" not in hero_row(plan_off, "broken")
    plan_on = cluster_plan.build(words, TRACK, base_spec(neg_words=neg_words), cut_s=0.9, total_frames=27)
    assert hero_row(plan_on, "broken").get("neg") is True


def test_gate_neg_add_and_no_neg_overrides_extend_and_suppress_the_set():
    # A single-word phrase makes "stop" unambiguously the hero (no length-comparison ambiguity).
    words = [W("Stop.", 0.0, 0.4)]
    # "stop" IS in the built-in set by default: no-neg suppresses it for this take.
    plan_default = cluster_plan.build(words, TRACK, base_spec(), cut_s=0.7, total_frames=21)
    assert hero_row(plan_default, "stop").get("neg") is True
    plan_suppressed = cluster_plan.build(
        words, TRACK, base_spec(device_overrides={"neg_off": ["stop"]}), cut_s=0.7, total_frames=21)
    assert "neg" not in hero_row(plan_suppressed, "stop")
    # a word NOT in the built-in set can be force-added per take via neg_add.
    words2 = [W("Kind", 0.0, 0.2), W("of", 0.2, 0.35), W("mediocre.", 0.35, 0.8)]
    plan_forced = cluster_plan.build(
        words2, TRACK, base_spec(device_overrides={"neg_add": ["mediocre"]}), cut_s=1.1, total_frames=33)
    assert hero_row(plan_forced, "mediocre").get("neg") is True


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: count tagging
# ---------------------------------------------------------------------------------------------

def test_count_tags_an_eligible_integer_and_keeps_prefix_suffix_text():
    words = [W("We", 0.0, 0.15), W("have", 0.15, 0.35), W("50", 0.35, 0.6), W("locations.", 0.6, 1.0)]
    plan = cluster_plan.build(words, TRACK, base_spec(count_up=True), cut_s=1.3, total_frames=39)
    row = hero_row(plan, "50")
    assert row["count"] == 50
    assert row["text"] == "50"   # no prefix/suffix on this one: kept as-is either way


def test_count_handles_a_decorated_dollar_amount():
    words = [W("That's", 0.0, 0.2), W("$1,200", 0.2, 0.6), W("saved.", 0.6, 0.9)]
    plan = cluster_plan.build(words, TRACK, base_spec(count_up=True), cut_s=1.2, total_frames=36)
    row = hero_row(plan, "1,200")
    assert row["count"] == 1200
    assert row["text"] == "$1,200"   # prefix kept in the rendered text


def test_count_never_fires_on_a_bare_year():
    words = [W("In", 0.0, 0.15), W("2024", 0.15, 0.5), W("we", 0.5, 0.65), W("grew.", 0.65, 1.0)]
    plan = cluster_plan.build(words, TRACK, base_spec(count_up=True), cut_s=1.3, total_frames=39)
    row = hero_row(plan, "2024")
    assert "count" not in row


def test_count_never_fires_below_ten():
    words = [W("Just", 0.0, 0.2), W("$6", 0.2, 0.45), W("today.", 0.45, 0.8)]
    plan = cluster_plan.build(words, TRACK, base_spec(count_up=True), cut_s=1.1, total_frames=33)
    row = hero_row(plan, "$6")
    assert "count" not in row


def test_count_off_by_default_when_count_up_is_absent_from_spec():
    words = [W("We", 0.0, 0.15), W("have", 0.15, 0.35), W("50", 0.35, 0.6), W("locations.", 0.6, 1.0)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.3, total_frames=39)   # no count_up key at all
    assert "count" not in hero_row(plan, "50")


def test_gate_count_off_override_suppresses_count_even_when_the_look_has_it_on():
    words = [W("We", 0.0, 0.15), W("have", 0.15, 0.35), W("50", 0.35, 0.6), W("locations.", 0.6, 1.0)]
    plan = cluster_plan.build(
        words, TRACK, base_spec(count_up=True, device_overrides={"count": False}), cut_s=1.3, total_frames=39)
    assert "count" not in hero_row(plan, "50")


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: strike form
# ---------------------------------------------------------------------------------------------

def test_strike_fires_on_no_plus_noun_phrase_and_not_neg():
    words = [W("No", 0.0, 0.15), W("long", 0.15, 0.35), W("term", 0.35, 0.55), W("contracts.", 0.55, 0.9)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.2, total_frames=36)
    row = hero_row(plan, "contracts")
    assert row.get("strike") is True
    assert "neg" not in row


def test_strike_add_override_forces_a_phrase_by_exact_text():
    words = [W("Some", 0.0, 0.2), W("crazy", 0.2, 0.45), W("upcharges.", 0.45, 0.9)]
    plan = cluster_plan.build(
        words, TRACK, base_spec(device_overrides={"strike_add": ["crazy upcharges"]}),
        cut_s=1.2, total_frames=36)
    row = hero_row(plan, "upcharges")
    assert row.get("strike") is True


def test_strike_never_fires_without_a_trigger_word():
    words = [W("Great", 0.0, 0.2), W("long", 0.2, 0.4), W("term", 0.4, 0.6), W("contracts.", 0.6, 0.95)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.2, total_frames=36)
    assert "strike" not in hero_row(plan, "contracts")


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: places pin + spaced, with the one-per-6s throttle
# ---------------------------------------------------------------------------------------------

def test_places_pin_and_spaced_with_six_second_throttle():
    words = (
        [W("We're", 0.0, 0.3), W("in", 0.3, 0.45), W("Portland.", 0.45, 1.0)]
        + [W("Also", 3.0, 3.2), W("in", 3.2, 3.35), W("Austin.", 3.35, 3.9)]
        + [W("Back", 10.0, 10.2), W("in", 10.2, 10.35), W("Portland.", 10.35, 10.9)]
    )
    plan = cluster_plan.build(words, TRACK, base_spec(places=["Portland", "Austin"]),
                              cut_s=11.2, total_frames=336)
    rows = all_rows(plan)
    portland_rows = [w for w in rows if w["text"].upper() == "PORTLAND"]
    austin_rows = [w for w in rows if w["text"].upper() == "AUSTIN"]
    assert len(portland_rows) == 2 and len(austin_rows) == 1
    # both Portland heroes and the Austin hero are place-name heroes -> spaced, unthrottled
    assert portland_rows[0].get("spaced") is True
    assert portland_rows[1].get("spaced") is True
    assert austin_rows[0].get("spaced") is True
    # pin: first Portland's pre-row ("We're in") pins; Austin's pre-row ("Also in") is within 6s
    # of it and is throttled; the second Portland's pre-row ("Back in") is >=6s later and pins again.
    pinned = [w["text"] for w in rows if w.get("pin")]
    assert pinned == ["WE'RE IN", "BACK IN"], pinned


def test_places_empty_by_default_means_no_pin_or_spaced_at_all():
    words = [W("We're", 0.0, 0.3), W("in", 0.3, 0.45), W("Portland.", 0.45, 1.0)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.3, total_frames=39)
    row = hero_row(plan, "Portland")
    assert "spaced" not in row and "pin" not in row


def test_gate_pin_off_override_suppresses_the_glyph_but_not_spaced():
    words = [W("We're", 0.0, 0.3), W("in", 0.3, 0.45), W("Portland.", 0.45, 1.0)]
    plan = cluster_plan.build(
        words, TRACK, base_spec(places=["Portland"], device_overrides={"pin": False}),
        cut_s=1.3, total_frames=39)
    row = hero_row(plan, "Portland")
    assert row.get("spaced") is True
    assert not any(w.get("pin") for w in all_rows(plan))


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: glow
# ---------------------------------------------------------------------------------------------

def test_glow_applies_to_every_accent_row_when_hero_glow_is_on():
    words = [W("This", 0.0, 0.2), W("is", 0.2, 0.3), W("Sam.", 0.3, 0.6),
             W("Over", 1.0, 1.2), W("here.", 1.2, 1.5)]
    plan = cluster_plan.build(words, TRACK, base_spec(hero_glow=True), cut_s=1.8, total_frames=54)
    accent_rows = [w for w in all_rows(plan) if w["color"] == plan["accent"]]
    assert accent_rows, "expected at least one accent-colored hero row"
    assert all(w.get("glow") is True for w in accent_rows)
    non_accent_rows = [w for w in all_rows(plan) if w["color"] != plan["accent"]]
    assert not any(w.get("glow") for w in non_accent_rows)
    assert plan["heroGlow"] is True


def test_gate_glow_off_override_suppresses_glow_even_when_the_look_has_it_on():
    words = [W("This", 0.0, 0.2), W("is", 0.2, 0.3), W("Sam.", 0.3, 0.6)]
    plan = cluster_plan.build(
        words, TRACK, base_spec(hero_glow=True, device_overrides={"glow": False}),
        cut_s=1.0, total_frames=30)
    assert not any(w.get("glow") for w in all_rows(plan))


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: no device fields set means no device keys in the plan
# ---------------------------------------------------------------------------------------------

def _assert_no_device_keys(plan):
    for e in plan["events"]:
        for w in e.get("words") or []:
            for k in DEVICE_KEYS:
                assert k not in w, f"unexpected device key {k!r} on {w!r} with no device spec fields set"
    for k in ("chipNeg", "heroGlow", "heroTint", "glowPx", "ctaWash", "pageBg", "pageInk",
              "displayFont", "pillBg", "pillInk"):
        assert k not in plan, f"unexpected top-level plan device field {k!r}"


def test_plan_carries_no_device_keys_at_all_when_spec_has_no_device_fields():
    # A transcript that WOULD trip the configuration-gated devices if they were engaged (a countable number, an accent hero for glow, a place name) but the spec below never
    # sets hero_glow/places/count_up/device_overrides: glow/count/pin/spaced must never appear. (neg and strike are content-driven and unconditional by design, see
    # test_neg_fires_on_a_not_or_no_plus_hero_pattern_even_off_the_builtin_list and test_strike_fires_on_no_plus_noun_phrase_and_not_neg above; this transcript
    # uses no trigger word for either.)
    words = (
        [W("This", 0.0, 0.2), W("is", 0.2, 0.3), W("Sam.", 0.3, 0.6)]
        + [W("We've", 1.0, 1.2), W("done", 1.2, 1.4), W("this", 1.4, 1.55), W("for", 1.55, 1.7),
           W("11", 1.7, 1.9), W("years.", 1.9, 2.2)]
        + [W("Over", 3.0, 3.2), W("at", 3.2, 3.3), W("Acme", 3.3, 3.5), W("Corp.", 3.5, 3.85)]
    )
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=4.15, total_frames=125)
    _assert_no_device_keys(plan)


def test_cluster_plan_is_deterministic_on_a_spec_with_no_device_fields():
    """A spec shaped like one written before the devices existed (none of hero_glow, places, neg_words, count_up, device_overrides) plans the same twice running, and
    carries no device key."""
    words = [W("Hey.", 0.16, 0.56), W("This", 0.6, 0.76), W("is", 0.76, 0.92), W("Sam.", 0.92, 1.3)]
    upstatic = cap_helpers.spec("upstatic")
    plain_spec = {
        "accent": upstatic["accent"], "zone": "top", "width": 960, "top_y0": 330, "hero_max": 360,
        "row_px": [120, 140], "chin_gap": 125, "font": upstatic["font"],
        "no_behind": False, "case": "upper",
        "look": {"base": "#FFFFFF", "hero_base": "#FFFFFF", "chip": False,
                 "hero_shadow": "0 12px 26px rgba(0,0,0,0.45), 0 2px 6px rgba(0,0,0,0.30)",
                 "text_opacity": 0.96},
    }
    plan_a = cluster_plan.build(copy.deepcopy(words), TRACK, copy.deepcopy(plain_spec), cut_s=1.6, total_frames=48)
    plan_b = cluster_plan.build(copy.deepcopy(words), TRACK, copy.deepcopy(plain_spec), cut_s=1.6, total_frames=48)
    assert json.dumps(plan_a, sort_keys=True) == json.dumps(plan_b, sort_keys=True)
    _assert_no_device_keys(plan_a)


# ---------------------------------------------------------------------------------------------
# devices.py: propose(), all four triggers on one synthetic transcript
# ---------------------------------------------------------------------------------------------

def test_propose_hits_all_four_triggers():
    words = [
        W("Here's", 0.0, 0.3), W("the", 0.3, 0.45), W("thing", 0.45, 0.8), W("about", 0.8, 1.1), W("posting.", 1.1, 1.6),
        W("This", 3.0, 3.2), W("is", 3.2, 3.3), W("not", 3.3, 3.5), W("advertising.", 3.5, 4.1),
        W("Ads,", 8.0, 8.3), W("organic", 8.3, 8.6), W("content,", 8.6, 9.0), W("and", 9.0, 9.1),
        W("lead", 9.1, 9.3), W("tracking.", 9.3, 9.7),
        W("So", 14.0, 14.2), W("fill", 14.2, 14.4), W("out", 14.4, 14.6), W("the", 14.6, 14.7),
        W("form", 14.7, 15.0), W("below", 15.0, 15.3), W("today.", 15.3, 15.7),
    ]
    proposals = devices.propose(words)
    modes = {p["mode"] for p in proposals}
    assert modes == {"cta", "page", "frame", "igpost"}, modes

    page = next(p for p in proposals if p["mode"] == "page")
    assert page["text"] == "advertising"
    assert 3.3 <= page["start"] < 3.6

    frame = next(p for p in proposals if p["mode"] == "frame")
    item_texts = [i["text"] for i in frame["list"]]
    assert len(item_texts) >= 3
    assert "organic content" in item_texts and "lead tracking" in item_texts

    cta = next(p for p in proposals if p["mode"] == "cta")
    assert cta["rows"][0]["text"] == "FILL OUT"
    assert "form" in cta["rows"][1]["text"] and "below" in cta["rows"][1]["text"]

    igpost = next(p for p in proposals if p["mode"] == "igpost")
    assert igpost["start"] < 2.0   # the "posting." trigger near t=1.1


def test_propose_returns_empty_on_a_transcript_with_no_triggers():
    words = [W("Hello", 0.0, 0.3), W("everybody,", 0.3, 0.8), W("welcome", 0.8, 1.2), W("back.", 1.2, 1.5)]
    assert devices.propose(words) == []


# ---------------------------------------------------------------------------------------------
# devices.py: apply(): clamp, drop, cta replacement, frame/igpost facetrack fields
# ---------------------------------------------------------------------------------------------

def test_apply_clamps_an_overlapping_cluster_and_drops_one_starting_inside():
    plan = {"events": [
        {"mode": "front", "start": 7.5, "end": 8.5, "words": [{"text": "OVERLAP"}]},
        {"mode": "behind", "start": 8.5, "end": 9.5, "words": [{"text": "INSIDE"}]},
        {"mode": "cta", "start": 8.5, "end": 8.6, "rows": ["untouched"]},
    ], "matte": {"dir": "", "windows": [[0, 10]]}}
    devs = [{"mode": "frame", "start": 8.0, "end": 10.4, "list": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}]
    result = devices.apply(plan, devs, None)
    front = next(e for e in result["events"] if e["mode"] == "front")
    assert front["end"] == round(8.0 - 1 / FPS, 3)
    assert not any(e.get("mode") == "behind" and e["words"][0]["text"] == "INSIDE" for e in result["events"])
    assert any(e["mode"] == "cta" for e in result["events"])
    assert result["matte"] == plan["matte"]
    assert result["devicesApplied"] is True


def test_apply_cta_replaces_the_cluster_it_covers():
    plan = {"events": [{"mode": "front", "start": 14.0, "end": 16.5, "words": [{"text": "FILL"}]}]}
    devs = [{"mode": "cta", "start": 14.2, "end": 16.0,
             "rows": [{"text": "FILL OUT", "size": 190, "weight": 800}]}]
    result = devices.apply(plan, devs, None)
    modes = [e["mode"] for e in result["events"]]
    assert "cta" in modes
    # the original cluster is clamped to end before the cta (it does not simply vanish if it started before the cta's own onset): nothing of it survives INTO the cta's span.
    for e in result["events"]:
        if e["mode"] == "front":
            assert e["end"] <= devs[0]["start"]


def test_apply_computes_frame_card_y_and_igpost_crop_from_facetrack():
    plan = {"events": []}
    track = {"samples": {"9": {"cx": 520, "cy": 300, "w": 300, "h": 300}}, "median": {"cx": 540, "cy": 900, "w": 300, "h": 300}}
    devs = [{"mode": "frame", "start": 8.0, "end": 10.0, "list": [{"text": "a"}]},
            {"mode": "igpost", "start": 8.5, "end": 9.5}]
    result = devices.apply(plan, devs, track)
    frame = next(e for e in result["events"] if e["mode"] == "frame")
    igpost = next(e for e in result["events"] if e["mode"] == "igpost")
    # face top = 300 - 150 = 150, well above the default card y=640 -> card.y pulled up to fit it
    assert frame["card"]["y"] < 640
    assert igpost["cropCx"] == 520


def _layout(mode, start, end):
    if mode == "page":
        return {"mode": "page", "start": start, "end": end, "text": "acme"}
    if mode == "cta":
        return {"mode": "cta", "start": start, "end": end, "rows": [{"text": "BOOK A CALL"}]}
    if mode == "frame":
        return {"mode": "frame", "start": start, "end": end, "list": [{"text": "a"}, {"text": "b"}, {"text": "c"}]}
    return {"mode": "igpost", "start": start, "end": end}


@pytest.mark.parametrize("mode", ["page", "cta", "frame", "igpost"])
def test_a_layout_event_that_ends_inside_a_phrase_leaves_no_word_without_a_caption(mode):
    """The page ended mid-row: "team builds" sat in the post row of a cluster devices.apply cut off where the event started, so the words after the event had no caption and
    G5 reported them uncovered. Planned with the event's span (spec layout_spans), the words after an event are a cluster of their own."""
    import reel_qc
    words = [W("Our", 1.0, 1.15), W("Acme", 1.2, 1.8), W("team", 1.95, 2.2), W("builds", 2.25, 2.6)]
    dev = [_layout(mode, 1.2, 1.9)]
    uncovered = {}
    for label, spans in (("whole", None), ("around", [[1.2, 1.9]])):
        spec = base_spec(case="mixed", **({"layout_spans": spans} if spans else {}))
        plan = cluster_plan.build(words, TRACK, spec, cut_s=2.9, total_frames=87)
        out = devices.apply(plan, dev, TRACK, spec)
        g5 = reel_qc.g5_events(out["events"], {"words": words}, [], FPS)
        uncovered[label] = [d["text"] for d in g5["detail"]]
    assert uncovered["whole"] == ["team", "builds"]         # the defect, kept as the control: both words after the event lose their caption
    assert uncovered["around"] == []


def test_the_words_after_a_page_show_on_screen_when_they_are_spoken():
    """G5 matches a word to a caption row by its text and onset, not by whether the cluster is still up then: check the time."""
    words = [W("It's", 1.0, 1.2), W("called", 1.2, 1.4), W("marketing", 1.4, 1.9), W("automation", 2.15, 2.7), W("today.", 2.8, 3.2)]
    spec = base_spec(case="mixed", layout_spans=[[1.4, 2.1]])
    out = devices.apply(cluster_plan.build(words, TRACK, spec, cut_s=3.5, total_frames=105),
                        [{"mode": "page", "start": 1.4, "end": 2.1, "text": "marketing"}], TRACK, spec)
    for w in words[3:]:
        up = [e for e in out["events"] if e["mode"] in ("front", "behind") and e["start"] <= w["start"] < e["end"]
              and any(w["text"].strip(".") in r["text"] for r in e["words"])]
        assert up, f"{w['text']} is spoken at {w['start']} with no caption on screen: {[(e['mode'], e['start'], e['end']) for e in out['events']]}"


def test_apply_rejects_an_unknown_device_mode():
    with pytest.raises(ValueError):
        devices.apply({"events": []}, [{"mode": "banner", "start": 0, "end": 1}], None)


# ---------------------------------------------------------------------------------------------
# pill icons: the comma-list rows of a look, read through lookprofile and spec_adapter
# ---------------------------------------------------------------------------------------------

def test_a_looks_pill_icons_are_ordered_comma_rows_and_reach_the_spec_whole():
    look = cap_helpers.profile("varsans")
    assert look["pill_icons"] == ["booking,calls:calendar", "reviews:star"]
    rows = [(("booking", "calls"), "calendar"), (("reviews",), "star")]
    assert devices.pill_rows(look) == rows
    spec = cap_helpers.spec("varsans")
    assert spec["pill_icons"] == look["pill_icons"] and devices.pill_rows(spec) == rows
    assert devices.brand_icons(spec)["track"] == "search" and devices.brand_icons(spec)["ads"] == "play"


def test_a_look_that_lists_no_icon_words_and_no_pill_rows_has_neither():
    look = cap_helpers.profile("upstatic")
    assert look["pill_icons"] == [] and look["icon_words"] == []
    spec = cap_helpers.spec("upstatic")
    assert spec["pill_icons"] == [] and spec["icon_words"] == []
    assert devices.pill_rows(spec) == [] and devices.brand_icons(spec) == {}


def test_the_rows_of_an_edited_dna_keep_their_order_through_the_look_and_the_spec():
    d = cap_helpers.dna_of("varsans")
    d["captions"]["pill_icons"] = ["show,shows:star", "call,calls,calling:phone", "book,booked:calendar"]
    d["captions"]["icon_words"] = ["book:dollar"]
    look = lookprofile.build(d, cap_helpers.brand_dir("varsans"))
    assert look["pill_icons"] == ["show,shows:star", "call,calls,calling:phone", "book,booked:calendar"]
    spec = spec_adapter.build(dict(cap_helpers.TIER_DOC), look)
    assert devices.pill_rows(spec) == [(("show", "shows"), "star"), (("call", "calls", "calling"), "phone"), (("book", "booked"), "calendar")]
    assert devices._icon_for("book a show", devices.brand_icons(spec), devices.pill_rows(spec)) == "dollar"       # icon_words first
    assert devices._icon_for("booked calls", devices.brand_icons(spec), devices.pill_rows(spec)) == "phone"       # then the first row with a word in the text


# --- word-level matching: no fragments of words, no filler proposals --------------------------------------------------

@pytest.mark.parametrize("text,icon", [("head coach", "check"), ("already booked", "calendar"), ("shadow work", "check"),
                                       ("reading reviews", "star"), ("showroom", "check"), ("daily call lists", "phone"),
                                       ("$5 off", "dollar"), ("Pay", "dollar"), ("text us", "phone"), ("same day appointment", "calendar"),
                                       ("flat cost", "dollar"), ("our rating", "star"), ("find us", "search")])
def test_icon_for_matches_whole_words_never_fragments(text, icon):
    """'show' used to match inside showroom (and 'ad' inside lead, head, already, shadow, reading: see the look test below)."""
    assert devices._icon_for(text) == icon


def test_a_listed_word_matches_a_whole_word_never_a_fragment_of_one():
    look = devices.brand_icons({"icon_words": ["ad:play", "ads:play"]})
    for text in ("lead tracking", "head coach", "already here", "shadow work", "reading", "road"):
        assert devices._icon_for(text, look) == "check", text
    assert devices._icon_for("daily ads", look) == "play" and devices._icon_for("ad", look) == "play"


def test_a_page_is_not_proposed_for_a_weak_or_short_word_after_a_lead_in():
    words = [W("That's", 0.0, 0.2), W("gonna", 0.2, 0.5), W("be", 0.5, 0.6), W("great.", 0.6, 1.0),
             W("It", 1.2, 1.3), W("isn't", 1.3, 1.5), W("luck.", 1.5, 1.9),
             W("This", 2.2, 2.4), W("is", 2.4, 2.5), W("not", 2.5, 2.7), W("advertising.", 2.7, 3.4)]
    pages = [p for p in devices.propose(words) if p["mode"] == "page"]
    assert [p["text"] for p in pages] == ["luck", "advertising"]     # real content words; never "gonna" (weak) or "not" (short)


def test_that_s_not_leads_into_the_word_after_not():
    words = [W("That's", 0.0, 0.2), W("not", 0.2, 0.4), W("marketing.", 0.4, 1.0)]
    assert [p["text"] for p in devices.propose(words) if p["mode"] == "page"] == ["marketing"]


def test_a_run_of_filler_is_not_proposed_as_a_frame_list():
    words = [W("Well,", 0.0, 0.3), W("I", 0.3, 0.4), W("mean,", 0.4, 0.7), W("you", 0.7, 0.8), W("know.", 0.8, 1.2)]
    assert [p for p in devices.propose(words) if p["mode"] == "frame"] == []
    real = [W("ads,", 0.0, 0.3), W("reviews,", 0.4, 0.8), W("referrals", 0.9, 1.4)]
    assert [p["mode"] for p in devices.propose(real)] == ["frame"]


def test_apply_gives_a_page_the_looks_display_weight_unless_the_line_sets_one():
    plan = {"events": [], "displayWeight": 700}
    out = devices.apply(plan, [{"mode": "page", "start": 1.0, "end": 1.7, "text": "ads"},
                               {"mode": "page", "start": 2.0, "end": 2.7, "text": "leads", "weight": 500}], None)
    assert [e["weight"] for e in out["events"]] == [700, 500]
    assert "weight" not in devices.apply({"events": []}, [{"mode": "page", "start": 1.0, "end": 1.7, "text": "ads"}], None)["events"][0]


def test_a_pill_takes_its_icon_from_the_looks_icon_words_before_the_engines_table():
    assert devices._icon_for("daily calls") == "phone"                                # the engine's own table
    assert devices._icon_for("daily calls", devices.brand_icons({"icon_words": ["calls:star", "bogus", ":x"]})) == "star"
    assert devices.brand_icons(None) == {}
    plan = {"events": []}
    devs = [{"mode": "frame", "start": 8.0, "end": 10.4, "list": [{"text": "ads"}, {"text": "calls"}, {"text": "reviews"}]}]
    out = devices.apply(plan, devs, None, {"icon_words": ["ads:star"]})
    icons = [it["icon"] for it in out["events"][0]["list"]]
    assert icons == ["star", "phone", "star"]


AGENCY_WORDS = ("ad", "ads", "video", "videos", "content", "post", "posts", "feed", "track", "tracking", "lead", "leads", "report", "reports")


def test_the_engine_carries_no_agency_icon_for_any_look():
    """Agency words are a look's own icon_words, never the generic default every look inherits."""
    for word in AGENCY_WORDS:
        assert devices._icon_for(word) == "check", word
        assert word not in cluster_plan.ICON_BUILTIN, word
    assert not any(set(words) & set(AGENCY_WORDS) for words, _ in devices.ICON_WORDS)


def test_a_look_gets_no_agency_pill_icon_unless_it_lists_one():
    devs = [{"mode": "frame", "start": 8.0, "end": 10.4, "list": [{"text": "ads"}, {"text": "video"}, {"text": "posts"}]}]
    bare = devices.apply({"events": []}, devs, None, {"icon_words": [], "pill_icons": []})            # a look that lists none of its own
    assert [it["icon"] for it in bare["events"][0]["list"]] == ["check", "check", "check"]
    fixture = devices.apply({"events": []}, devs, None, cap_helpers.spec("upstatic"))                  # the fixture look that lists none
    assert [it["icon"] for it in fixture["events"][0]["list"]] == ["check", "check", "check"]
    listed = devices.apply({"events": []}, devs, None, {"icon_words": ["ads:play"], "pill_icons": ["video,videos:play"]})
    assert [it["icon"] for it in listed["events"][0]["list"]] == ["play", "play", "check"]           # one word by icon_words, one by a pill row


def test_a_look_that_lists_agency_words_gives_them_their_icons():
    spec = cap_helpers.spec("varsans")
    items = ["ads", "video", "content", "feed", "post it", "track leads", "booking"]
    out = devices.apply({"events": []}, [{"mode": "frame", "start": 8.0, "end": 10.4, "list": [{"text": t} for t in items]}], None, spec)
    assert [it["icon"] for it in out["events"][0]["list"]] == ["play", "play", "grid", "grid", "grid", "search", "calendar"]


def test_pill_rows_keep_their_order_and_skip_a_malformed_row():
    rows = devices.pill_rows({"pill_icons": ["ad,ads:play", "bogus", ":x", "call,calls:phone", "book,,booked:calendar"]})
    assert rows == [(("ad", "ads"), "play"), (("call", "calls"), "phone"), (("book", "booked"), "calendar")]
    assert devices.pill_rows(None) == [] and devices.pill_rows({}) == []


def test_a_pill_icon_is_chosen_by_icon_words_then_pill_rows_then_the_engines_table():
    """"$" first; then the first word of the text the look lists in icon_words (text order); then the first pill_icons ROW with a word in the text (row order, whatever the text
    order); then the engine's own plain table; then the plain check."""
    look = devices.brand_icons({"icon_words": ["book:calendar"]})
    rows = devices.pill_rows({"pill_icons": ["call,calls:phone", "show,shows:star"]})
    assert devices._icon_for("$5 book a call", look, rows) == "dollar"
    assert devices._icon_for("call to book", look, rows) == "calendar"       # icon_words: the first LISTED word of the text is book
    assert devices._icon_for("shows then calls", {}, rows) == "phone"         # pill rows: the call row is first, whatever the text order
    assert devices._icon_for("pay for shows", {}, rows) == "star"             # the look's row beats the engine's table, where show is a calendar
    assert devices._icon_for("pay up", {}, rows) == "dollar"                  # nothing of the look's: the engine's plain table
    assert devices._icon_for("nothing here", look, rows) == "check"


def _icon_of(brand_id, text):
    spec = cap_helpers.spec(brand_id)
    return devices._icon_for(text, devices.brand_icons(spec), devices.pill_rows(spec))


@pytest.mark.parametrize("text,icon", [
    ("ads and calls", "play"), ("calls and ads", "play"),                   # icon_words first: ads is listed, calls only in a row
    ("track the content", "search"), ("content to track", "grid"),          # and the first LISTED word in text order wins
    ("video posts", "play"), ("post a video", "grid"),                      # posts is not post: only the whole word counts
    ("lead tracking", "check"),                                             # tracking is not track
    ("daily calls", "calendar"), ("calls to book", "calendar"),             # the look's row (booking, calls) beats the engine's table, where a call is a phone
    ("reviews then booking", "calendar"), ("reviews", "star"),              # then the rows in order, not text order
    ("book a call", "phone"), ("pay up", "dollar"),                         # then the engine's table in its own order: call before book
    ("$5 calls", "dollar"), ("nothing here", "check")])
def test_the_variable_font_look_picks_pill_icons_in_the_priority_its_lists_set(text, icon):
    assert _icon_of("varsans", text) == icon


@pytest.mark.parametrize("text,icon", [
    ("book a call", "phone"), ("calls to book", "phone"),                   # no lists: the engine's table in its own order, call before book
    ("booked calls", "phone"), ("local shop", "dollar"), ("best right now", "check"),
    ("ads and calls", "phone"), ("lead tracking", "check"), ("show up on time", "calendar"), ("pay for shows", "calendar"),
    ("leads", "check"), ("reports weekly", "check"), ("videos", "check"), ("posts", "check"), ("respond fast", "chat"),
    ("payment plans", "dollar"), ("daily call lists", "phone"), ("$5 off", "dollar"), ("nothing here", "check")])
def test_the_static_font_look_has_no_lists_so_every_pill_icon_comes_from_the_engines_table(text, icon):
    assert _icon_of("upstatic", text) == icon


def test_a_soft_thought_cut_short_by_a_layout_event_leaves_before_its_new_end():
    """a soft build thought that runs into a layout event is shortened like a cluster, and its exitAt comes forward with its end, so it has finished its leave (the soft build's
    LEAVE_S) by the new end instead of vanishing whole on it. A thought the event does not reach keeps its exitAt"""
    words = [{"text": "a lot more", "at": 5.3, "ats": [5.3, 5.4, 5.5], "role": "pay"}]
    cut = {"mode": "front", "soft": True, "start": 5.0, "end": 7.16, "exitAt": 7.0, "y": 940, "words": words}
    kept = {"mode": "front", "soft": True, "start": 2.0, "end": 3.16, "exitAt": 3.0, "y": 940, "words": words}
    plan = {"events": [kept, cut], "matte": {"dir": "", "windows": []}}
    got = devices.apply(plan, [{"mode": "page", "start": 6.5, "end": 7.5, "text": "more"}], None)["events"]
    before, shortened = got[0], got[1]
    assert shortened["end"] == round(6.5 - 1 / FPS, 3)
    assert shortened["exitAt"] == round(shortened["end"] - cluster_plan.soft_look.LEAVE_S, 3)
    assert before == kept, "a thought the layout event does not reach is untouched"


def test_a_soft_thought_moved_past_a_layout_event_moves_its_words_with_it():
    """a soft thought that starts inside a layout event and runs on past it starts again just after the event, and every word spoken under the event arrives then, together
    and softly, instead of the line popping on whole: each row's ats are raised to the new start, and its at is still its first word's. Words spoken after the event keep their
    own times"""
    lead = {"text": "So running a shop,", "at": 6.6, "ats": [6.6, 6.8, 7.0], "role": "lead"}
    pay = {"text": "a lot more behind it", "at": 7.3, "ats": [7.3, 7.5, 7.7, 7.9, 8.1], "role": "pay"}
    e = {"mode": "front", "soft": True, "start": 6.55, "end": 9.16, "exitAt": 9.0, "y": 940, "words": [lead, pay]}
    got = devices.apply({"events": [e]}, [{"mode": "page", "start": 6.5, "end": 7.4, "text": "shop"}], None)["events"]
    (moved,) = [x for x in got if x["mode"] == "front"]
    start = round(7.4 + 1 / FPS, 3)
    assert moved["start"] == start and moved["exitAt"] == 9.0
    (lead2, pay2) = moved["words"]
    assert lead2["ats"] == [start, start, start] and lead2["at"] == start
    assert pay2["ats"] == [start, 7.5, 7.7, 7.9, 8.1] and pay2["at"] == start
    assert e["words"][0]["ats"] == [6.6, 6.8, 7.0], "the plan it was given is not changed"


def test_apply_drops_a_cluster_whose_remainder_before_the_event_is_a_flash():
    """A page that starts one frame after a cluster did leaves a one-frame caption: it is dropped, not shipped as a flicker."""
    plan = {"events": [
        {"mode": "front", "start": 6.24, "end": 7.0, "words": [{"text": "a"}, {"text": "contractor"}]},
        {"mode": "front", "start": 4.0, "end": 6.0, "words": [{"text": "kept"}, {"text": "whole"}]},
    ], "matte": {"dir": "", "windows": []}}
    devs = [{"mode": "page", "start": 6.3, "end": 7.2, "text": "contractor"}]
    result = devices.apply(plan, devs, None)
    assert [e["mode"] for e in result["events"]] == ["front", "page"]
    assert result["events"][0]["words"][0]["text"] == "kept"
    longer = {"events": [{"mode": "front", "start": 6.0, "end": 7.0, "words": [{"text": "a"}, {"text": "b"}]}]}
    shortened = devices.apply(longer, devs, None)["events"][0]
    assert shortened["end"] == round(6.3 - 1 / FPS, 3)                                # 0.27 s left: a real caption, kept


# ---------------------------------------------------------------------------------------------
# capgate.py: Devices section round-trip
# ---------------------------------------------------------------------------------------------

def _insert_into_devices_section(gate_text, extra_lines):
    """Add `extra_lines` (a single string, newline-joined) as ACTIVE lines inside the "## Devices" section: after that section's own closing `-->` and before the next "## "
    heading. The Devices section's comment always closes with the literal "-->\\n\\n## Pacing" transition (the "## Pacing" section follows Devices and precedes Overrides, see
    capgate.build()), so this is an unambiguous, single-occurrence anchor."""
    anchor = "-->\n\n## Pacing"
    assert gate_text.count(anchor) == 1
    return gate_text.replace(anchor, f"-->\n{extra_lines}\n## Pacing", 1)


def _devices_section(gate_text):
    return gate_text.split("## Devices", 1)[1].split("\n## ", 1)[0]


def _propose_lines(gate_text):
    """The `propose:` lines of a GATE.md (the comment above them names the word too, so only a line that starts with it is a proposal)."""
    return [line for line in gate_text.splitlines() if line.startswith("propose:")]


def _look(devices_switch):
    """The varsans fixture's real look as look.json holds it in a take, with the layout-event proposals set to "on" or "off" (the look makes them off unless asked)."""
    return dict(cap_helpers.profile("varsans"), devices=devices_switch)


def _seed_take(tmp_path, words, look=None):
    """A hand-written take with the four files capgate.build() reads, and a look.json: the varsans look with devices on when look is None, the dict given, or none at
    all when look is False."""
    take_dir = tmp_path / "take"
    take_dir.mkdir()
    cap_takes.write(take_dir / "words.json", {"transcript": " ".join(w["text"] for w in words), "words": words})
    cap_takes.write(take_dir / "tier.json", {"tier": "FULL", "reason": "test", "room": {}})
    cap_takes.write(take_dir / "spec.json", {"zone": "chest", "accent": cap_helpers.profile("varsans")["accent"]})
    cap_takes.write(take_dir / "plan.json", {"events": []})
    if look is not False:
        cap_takes.write(take_dir / "look.json", _look("on") if look is None else look)
    return take_dir


def test_capgate_build_renders_devices_section_with_proposal_and_generic_grammar(tmp_path):
    words = [W("That's", 0.0, 0.2), W("not", 0.2, 0.4), W("advertising.", 0.4, 0.9)]
    take_dir = _seed_take(tmp_path, words)
    path = capgate.build(str(take_dir))
    text = open(path).read()
    assert "## Devices" in text
    assert 'propose: page' in text and '"advertising"' in text
    for line in ['page: 12.27-12.97 "the point"',
                 'cta: 60.9-62.4 "LEARN MORE" "the link below"',
                 "frame: 52.5-55.0 plan, build, test, ship",
                 "igpost: 11.2-12.2 pixelate 24",
                 'neg: "weak"', 'no-neg: "stop"',
                 'front: "what\'s going on"', 'behind: "right now"',
                 'finale: "You ask" | "we answer"',
                 'strike: "the old way"',
                 "pin: off", "count: off", "glow: off"]:
        assert line in text, f"missing verbatim grammar example: {line}"


def test_capgate_build_says_so_when_there_are_no_proposals(tmp_path):
    words = [W("Hello", 0.0, 0.3), W("everybody.", 0.3, 0.8)]
    take_dir = _seed_take(tmp_path, words)
    text = open(capgate.build(str(take_dir))).read()
    assert "(no layout-event proposals for this take)" in text
    assert "proposals are off" not in text


@pytest.mark.parametrize("kind", ["no look.json", "no devices key", "devices off"])
def test_without_devices_on_in_the_look_the_gate_proposes_nothing_and_says_so(tmp_path, kind):
    """The words below would earn a page proposal with devices on. With no look.json, a look that has no devices key, or one that says off, the Devices section says the
    proposals are off, lists no `propose:` line, and still shows the grammar for a line written by hand."""
    words = [W("That's", 0.0, 0.2), W("not", 0.2, 0.4), W("advertising.", 0.4, 0.9)]
    look = {"no look.json": False, "no devices key": {k: v for k, v in _look("on").items() if k != "devices"}, "devices off": _look("off")}[kind]
    take_dir = _seed_take(tmp_path, words, look)
    text = open(capgate.build(str(take_dir))).read()
    section = _devices_section(text)
    assert "layout-event proposals are off" in section and "devices: on" in section
    assert _propose_lines(text) == []
    assert "(no layout-event proposals for this take)" not in text
    assert 'page: 12.27-12.97 "the point"' in section and 'cta: 60.9-62.4 "LEARN MORE" "the link below"' in section


def test_a_devices_propose_file_is_shown_when_devices_are_on_and_ignored_when_off(tmp_path):
    """run_captions.sh's own propose stage writes devices-propose.json before the gate is built: with devices on the gate shows what is in it (not a live reading of the
    words), with devices off the file is left alone."""
    words = [W("Hello", 0.0, 0.3), W("everybody.", 0.3, 0.8)]
    proposal = [{"mode": "page", "start": 3.0, "end": 3.7, "text": "welcome"},
                {"mode": "cta", "start": 9.5, "end": 11.0, "rows": [{"text": "FILL OUT"}, {"text": "the form"}]}]
    on = _seed_take(tmp_path, words)
    cap_takes.write(on / "devices-propose.json", proposal)
    on_text = open(capgate.build(str(on))).read()
    assert _propose_lines(on_text) == ['propose: page 3-3.7 "welcome"', 'propose: cta 9.5-11 "FILL OUT" "the form"']
    (tmp_path / "off").mkdir()
    off = _seed_take(tmp_path / "off", words, _look("off"))
    cap_takes.write(off / "devices-propose.json", proposal)
    off_text = open(capgate.build(str(off))).read()
    assert _propose_lines(off_text) == [] and "proposals are off" in off_text


def test_a_proposal_is_accepted_by_writing_its_mode_as_the_line_word(tmp_path):
    """build writes each proposal as `propose: <mode> <start>-<end> ...`: moving the line out of the comment and rewriting its leading word as the mode itself
    (`page:`) is the whole edit, and the line is accepted exactly as a hand-written one is."""
    words = [W("That's", 0.0, 0.2), W("not", 0.2, 0.4), W("advertising.", 0.4, 0.9)]
    take_dir = _seed_take(tmp_path, words)
    capgate.build(str(take_dir))
    text = open(take_dir / "GATE.md").read().replace("approved: no", "approved: yes", 1)
    (proposed,) = _propose_lines(text)
    assert proposed.startswith("propose: page ")
    (take_dir / "GATE.md").write_text(_insert_into_devices_section(text, proposed.replace("propose: page ", "page: ", 1)))
    capgate.apply_gate(str(take_dir))
    (page,) = json.load(open(take_dir / "devices.json"))
    want = devices.propose(words)[0]
    assert page == {"mode": "page", "start": want["start"], "end": want["end"], "text": "advertising"}


@pytest.mark.parametrize("devices_switch", ["on", "off", "absent"])
def test_capgate_apply_round_trips_every_devices_grammar_form(tmp_path, devices_switch):
    """Every Devices line is accepted whether the look has the proposals on, off or no look.json at all: the switch decides what build suggests, never what a hand-written
    line may do."""
    words = [W("Hello.", 0.0, 0.3)]
    take_dir = _seed_take(tmp_path, words, False if devices_switch == "absent" else _look(devices_switch))
    capgate.build(str(take_dir))
    gate_text = open(take_dir / "GATE.md").read()
    gate_text = gate_text.replace("approved: no", "approved: yes", 1)
    gate_text = _insert_into_devices_section(gate_text, (
        'page: 12.27-12.97 "advertising"\n'
        'cta: 60.9-62.4 "FILL OUT" "the form below"\n'
        "frame: 52.5-55.0 ads, organic content, lead tracking, daily call lists\n"
        "igpost: 11.2-12.2 pixelate 24\n"
        'neg: "weak"\n'
        'no-neg: "stop"\n'
        'strike: "long term contracts"\n'
        "pin: off\n"
        "count: off\n"
        "glow: off"
    ))
    (take_dir / "GATE.md").write_text(gate_text)
    capgate.apply_gate(str(take_dir))

    devices_json = json.load(open(take_dir / "devices.json"))
    by_mode = {d["mode"]: d for d in devices_json}
    assert by_mode["page"] == {"mode": "page", "start": 12.27, "end": 12.97, "text": "advertising"}
    assert by_mode["cta"]["rows"][0] == {"text": "FILL OUT", "size": 190, "weight": 800}
    assert by_mode["cta"]["rows"][1] == {"text": "the form below", "size": 74, "weight": 600}
    assert [i["text"] for i in by_mode["frame"]["list"]] == [
        "ads", "organic content", "lead tracking", "daily call lists"]
    assert by_mode["igpost"]["pixelate"] == 24

    ov = json.load(open(take_dir / "gate.json"))["overrides"]["devices"]["device_overrides"]
    assert ov["neg_add"] == ["weak"]
    assert ov["neg_off"] == ["stop"]
    assert ov["strike_add"] == ["long term contracts"]
    assert ov["pin"] is False and ov["count"] is False and ov["glow"] is False

    gate = json.load(open(take_dir / "gate.json"))
    assert len(gate["overrides"]["devices"]["layout_events"]) == 4


def test_a_layout_line_counts_under_devices_and_is_a_warning_under_overrides(tmp_path):
    """Layout events are read from the Devices section only: the same page line under Overrides is a line the gate does not understand."""
    take_dir = _seed_take(tmp_path, [W("Hello.", 0.0, 0.3)], _look("off"))
    capgate.build(str(take_dir))
    text = open(take_dir / "GATE.md").read().replace("approved: no", "approved: yes", 1)
    (take_dir / "GATE.md").write_text(text + 'page: 1.0-1.7 "hello"\n')
    capgate.apply_gate(str(take_dir))
    gate = json.load(open(take_dir / "gate.json"))
    assert not (take_dir / "devices.json").exists()
    assert gate["warnings"] == ['Overrides line not understood, ignored: page: 1.0-1.7 "hello"']


def test_capgate_apply_writes_no_devices_json_when_nothing_was_accepted(tmp_path):
    words = [W("Hello.", 0.0, 0.3)]
    take_dir = _seed_take(tmp_path, words)
    capgate.build(str(take_dir))
    text = open(take_dir / "GATE.md").read().replace("approved: no", "approved: yes", 1)
    (take_dir / "GATE.md").write_text(text)   # nothing moved out of the comment
    capgate.apply_gate(str(take_dir))
    assert not (take_dir / "devices.json").exists()
    spec = json.load(open(take_dir / "spec.json"))
    assert "device_overrides" not in spec


def test_capgate_existing_grammar_fix_tier_zone_still_works_alongside_devices(tmp_path):
    words = [W("Hey.", 0.0, 0.3), W("we", 0.3, 0.45), W("ain't", 0.45, 0.7)]
    take_dir = _seed_take(tmp_path, words)
    capgate.build(str(take_dir))
    text = open(take_dir / "GATE.md").read().replace("approved: no", "approved: yes", 1)
    text = _insert_into_devices_section(text, "glow: off")
    text += '\ntier: FULL\nfix: "we ain\'t" -> "we ink it"\n'
    (take_dir / "GATE.md").write_text(text)
    capgate.apply_gate(str(take_dir))
    gate = json.load(open(take_dir / "gate.json"))
    assert gate["overrides"]["tier"] == "FULL"
    assert json.load(open(take_dir / "tier.json"))["tier"] == "FULL"   # tier.json is the room check's own verdict, never edited
    words_after = json.load(open(take_dir / "words.json"))["words"]
    assert [w["text"] for w in words_after] == ["Hey.", "we", "ink", "it"]
    assert gate["overrides"]["devices"]["device_overrides"] == {"glow": False}
