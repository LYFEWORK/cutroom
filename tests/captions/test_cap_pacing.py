"""Pacing: how the caption chain keeps something changing on screen.

Covers cluster_plan.py's logo and icon tagging (the 4 s icon throttle, pin, logo and red-word precedence, the numeral-hero exclusion, the icon words a look adds)
and its byte-identical guarantee on a spec and words with no trigger word; cadence.py's change timeline and gap filling (a quiet stretch gets punches at real
cluster onsets where one exists, a fallback offset where none does, and nothing within 8 frames of a cut), its quiet-stretch report, its device_overrides
(punch, punch_add, punch_off) and the on/off switch for the automatic punch-ins (`auto`, `--auto on|off`); capgate.py's "## Pacing" section (build's report lines
and the override forms round-tripping through apply); and reel_qc.py's G10 pace gate on synthetic spike lists, in each of its three modes (off, report, enforce),
and its punch-aware G4 origin warning.

The planner tests use the fixture look `varsans` for the font and for the icon words a look can carry; the byte-identical test uses `upstatic`.
"""
import copy
import functools
import inspect
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import cadence
import cap_helpers
import cap_takes
import capgate
import cluster_plan
import reel_qc

FPS = 30
CADENCE = os.path.join(cap_helpers.REPO, "engine", "captions", "cadence.py")
TRACK = {"samples": {str(s): {"cx": 540, "cy": 1400, "w": 200, "h": 200} for s in range(0, 30)},
         "median": {"cx": 540, "cy": 1400, "w": 200, "h": 200}}


def W(text, start, end):
    return {"text": text, "start": start, "end": end, "filler": False}


@functools.lru_cache(maxsize=None)
def _fixture_spec(brand_id):
    return cap_helpers.spec(brand_id)


def fixture_spec(brand_id):
    return copy.deepcopy(_fixture_spec(brand_id))


def base_spec(**over):
    """A bare planner spec: the fixture look's font and accent, nothing that triggers a device (no places, red words or icon words of its own)."""
    fx = _fixture_spec("varsans")
    s = {"accent": fx["accent"], "zone": "chest", "case": "upper", "width": 860, "hero_max": 250,
         "row_px": [112, 132], "chin_gap": 90, "font": copy.deepcopy(fx["font"])}
    s.update(over)
    return s


def needs_mark(name):
    """The platform marks in remotion/captions/Icons.tsx are optional (a project may delete any of them), and the planner only tags a platform word whose mark is
    shipped: a test that needs a mark is skipped, with that reason, in a project that does not ship it."""
    return pytest.mark.skipif(name not in (cluster_plan.logo_names() or set(cluster_plan.LOGO_MAP_ALL.values())),
                              reason=f"remotion/captions/Icons.tsx does not ship the {name} mark")


def hero_row(plan, text_substr):
    for e in plan["events"]:
        if e.get("mode") not in ("front", "behind"):
            continue
        for w in e["words"]:
            if text_substr.lower() in w["text"].lower():
                return w
    raise AssertionError(f"no row containing {text_substr!r} in {[e.get('words') for e in plan['events']]}")


def all_rows(plan):
    return [w for e in plan["events"] if e.get("mode") in ("front", "behind") for w in e["words"]]


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: logo trigger
# ---------------------------------------------------------------------------------------------

@needs_mark("instagram")
def test_logo_tags_a_platform_name_and_it_becomes_the_hero():
    # "IG" (2 chars) would never win hero_index()'s longest-word contest against "today" (5 chars)
    # on its own: logo is the one device that OVERRIDES the hero choice.
    words = [W("DM", 0.0, 0.3), W("us", 0.3, 0.45), W("on", 0.45, 0.55), W("IG", 0.55, 0.8), W("today.", 0.8, 1.2)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.5, total_frames=45)
    row = hero_row(plan, "IG")
    assert row.get("logo") == "instagram"
    # "today" is a separate (post) row of the same cluster, not the hero: only ITS row is untagged
    today_row = hero_row(plan, "today")
    assert today_row is not row and today_row.get("logo") is None


@pytest.mark.parametrize("word,expected", [
    pytest.param(w, e, marks=needs_mark(e)) for w, e in
    [("Instagram", "instagram"), ("Facebook", "facebook"), ("FB", "facebook"), ("TikTok", "tiktok"),
     ("YouTube", "youtube"), ("Google", "google"), ("Meta", "meta"), ("WhatsApp", "whatsapp"),
     ("Gmail", "gmail"), ("Twitter", "x")]
])
def test_logo_map_covers_every_platform_name_the_renderer_draws(word, expected):
    # the renderer ships all nine platform marks (remotion/captions/Icons.tsx), so every one of these words is tagged
    words = [W("Check", 0.0, 0.25), W("us", 0.25, 0.4), W("out", 0.4, 0.55), W("on", 0.55, 0.65), W(f"{word}.", 0.65, 1.0)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.3, total_frames=39)
    row = hero_row(plan, word.rstrip("."))
    assert row.get("logo") == expected


def test_logo_off_via_device_override_disables_the_trigger():
    words = [W("DM", 0.0, 0.3), W("us", 0.3, 0.45), W("on", 0.45, 0.55), W("IG", 0.55, 0.8), W("today.", 0.8, 1.2)]
    plan = cluster_plan.build(words, TRACK, base_spec(device_overrides={"logo": False}), cut_s=1.5, total_frames=45)
    assert not any(w.get("logo") for w in all_rows(plan))
    assert hero_row(plan, "today").get("logo") is None


def _icons_file(tmp_path, names):
    """An Icons.tsx stand-in whose LOGO_PATHS block has one entry per name, in the shape the real file has."""
    body = "".join(f'  {n}:\n    "M0 0h1v1z",\n' for n in names)
    path = tmp_path / "Icons.tsx"
    path.write_text(f"const LOGO_PATHS: Partial<Record<LogoName, string>> = {{\n{body}}};\n")
    return str(path)


def test_the_logo_map_is_cut_to_the_marks_the_renderer_ships(tmp_path):
    shipped = cluster_plan.logo_names()
    assert cluster_plan.LOGO_MAP == cluster_plan.drawn_logo_map()
    assert set(cluster_plan.LOGO_MAP.values()) <= (shipped if shipped is not None else set(cluster_plan.LOGO_MAP_ALL.values()))   # no word is tagged without its mark
    path = _icons_file(tmp_path, ["instagram", "tiktok"])
    assert cluster_plan.logo_names(path) == {"instagram", "tiktok"}
    cut = cluster_plan.drawn_logo_map(path)
    assert cut == {"instagram": "instagram", "ig": "instagram", "tiktok": "tiktok"}
    assert cluster_plan.logo_names(_icons_file(tmp_path, [])) == set() and cluster_plan.drawn_logo_map(_icons_file(tmp_path, [])) == {}
    # a file that cannot be read says nothing about the marks, so nothing is cut
    assert cluster_plan.logo_names(str(tmp_path / "no-such.tsx")) is None
    assert cluster_plan.drawn_logo_map(str(tmp_path / "no-such.tsx")) == cluster_plan.LOGO_MAP_ALL


def test_a_platform_word_with_no_shipped_mark_is_never_tagged_and_the_rest_still_are(tmp_path, monkeypatch):
    monkeypatch.setattr(cluster_plan, "LOGO_MAP", cluster_plan.drawn_logo_map(_icons_file(tmp_path, ["instagram"])))
    words = [W("Check", 0.0, 0.25), W("us", 0.25, 0.4), W("out", 0.4, 0.55), W("on", 0.55, 0.65), W("YouTube.", 0.65, 1.0)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.3, total_frames=39)
    assert not any(w.get("logo") for w in all_rows(plan))
    words = [W("DM", 0.0, 0.3), W("us", 0.3, 0.45), W("on", 0.45, 0.55), W("IG", 0.55, 0.8), W("today.", 0.8, 1.2)]
    assert hero_row(cluster_plan.build(words, TRACK, base_spec(), cut_s=1.5, total_frames=45), "IG").get("logo") == "instagram"


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: icon trigger
# ---------------------------------------------------------------------------------------------

def test_icon_tags_the_hero_row_when_the_trigger_word_is_already_the_hero():
    words = [W("Just", 0.0, 0.2), W("book", 0.2, 0.5), W("now.", 0.5, 0.8)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.1, total_frames=33)
    assert hero_row(plan, "book").get("icon") == "calendar"


def test_icon_default_map_covers_every_plain_bucket():
    cases = [("phone", "phone"), ("call", "phone"), ("text", "phone"), ("dm", "chat"), ("message", "chat"), ("chat", "chat"),
             ("book", "calendar"), ("schedule", "calendar"), ("appointment", "calendar"), ("calendar", "calendar"),
             ("price", "dollar"), ("cost", "dollar"), ("money", "dollar"), ("pay", "dollar"),
             ("review", "star"), ("rating", "star"), ("star", "star"), ("search", "search"), ("find", "search")]
    assert {word for word, _ in cases} == set(cluster_plan.ICON_BUILTIN)      # the table names every built-in word
    for word, icon in cases:
        words = [W("Just", 0.0, 0.2), W(f"{word}.", 0.2, 0.6)]
        plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.0, total_frames=30)
        assert hero_row(plan, word).get("icon") == icon, f"{word} -> expected {icon}"


def test_icon_dollar_sign_triggers_when_not_a_numeral_hero():
    # "just" is a stop word (excluded from hero_index candidates), leaving "$$$" (containing a
    # literal "$" but no digit) as the unambiguous hero of this short phrase.
    words = [W("Just", 0.0, 0.2), W("$$$.", 0.2, 0.5)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=0.8, total_frames=24)
    row = hero_row(plan, "$")
    assert row.get("icon") == "dollar"
    assert row.get("count") is None   # no digit at all: never a numeral hero


def test_icon_skipped_on_a_numeral_hero():
    # "17" is in the count device's own 10..99999 range (a bare "7" is not: _count_value()
    # requires it, and hero_index() prefers ANY digit-bearing word regardless of size).
    words = [W("Call", 0.0, 0.3), W("us", 0.3, 0.45), W("17", 0.45, 0.65), W("times.", 0.65, 0.95)]
    plan = cluster_plan.build(words, TRACK, base_spec(count_up=True), cut_s=1.2, total_frames=36)
    row = hero_row(plan, "17")
    assert row.get("count") == 17
    assert row.get("icon") is None
    assert not any(w.get("icon") for w in all_rows(plan))   # "call" never becomes hero here either


def test_icon_excluded_on_a_row_that_already_carries_pin():
    words = [W("Come", 0.0, 0.3), W("visit", 0.3, 0.6), W("our", 0.6, 0.75), W("Portland", 0.75, 1.2), W("store.", 1.2, 1.5)]
    plan = cluster_plan.build(words, TRACK, base_spec(places=["Portland"]), cut_s=1.8, total_frames=54)
    row = hero_row(plan, "Portland")
    assert row.get("spaced") is True
    assert row.get("icon") is None


def test_icon_excluded_on_a_neg_row():
    # "weak" is a built-in neg word and, at 4 chars, the unambiguous hero of this short phrase.
    words = [W("This", 0.0, 0.2), W("deal", 0.2, 0.4), W("is", 0.4, 0.5), W("weak.", 0.5, 0.8)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.1, total_frames=33)
    row = hero_row(plan, "weak")
    assert row.get("neg") is True
    assert row.get("icon") is None


@needs_mark("instagram")
def test_icon_excluded_on_a_logo_row():
    words = [W("DM", 0.0, 0.3), W("us", 0.3, 0.45), W("on", 0.45, 0.55), W("IG", 0.55, 0.8), W("today.", 0.8, 1.2)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.5, total_frames=45)
    row = hero_row(plan, "IG")
    assert row.get("logo") == "instagram"
    assert row.get("icon") is None


def test_icon_throttled_to_one_per_four_seconds():
    # "Call" (0.0s) and "DM" (1.7s) are both icon triggers within 4s of each other: only the
    # first should keep its glyph.
    words = [W("Call", 0.0, 0.3), W("us", 0.3, 0.5), W("now.", 0.5, 0.8),
             W("Or", 1.5, 1.7), W("DM", 1.7, 2.0), W("us.", 2.0, 2.2)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=2.5, total_frames=75)
    icons = [(w["text"], w.get("icon")) for w in all_rows(plan) if w.get("icon")]
    assert icons == [("CALL", "phone")]


def test_icon_throttle_releases_after_four_seconds():
    # "DM." is its own (punctuation-terminated) one-word phrase, so it is unambiguously its own
    # hero: "Or DM us." would instead hand hero_index() a tie it breaks toward "us".
    words = [W("Call", 0.0, 0.3), W("us", 0.3, 0.5), W("now.", 0.5, 0.8), W("DM.", 5.2, 5.5)]
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=6.0, total_frames=180)
    icons = sorted((w["text"], w.get("icon")) for w in all_rows(plan) if w.get("icon"))
    assert icons == [("CALL", "phone"), ("DM", "chat")]


def test_icon_words_field_adds_a_custom_word_without_losing_the_defaults():
    words = [W("Book", 0.0, 0.3), W("a", 0.3, 0.4), W("consult.", 0.4, 0.8)]
    plan_off = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.1, total_frames=33)
    assert hero_row(plan_off, "consult").get("icon") is None   # "consult" isn't a built-in trigger
    plan_on = cluster_plan.build(words, TRACK, base_spec(icon_words=["consult:calendar"]), cut_s=1.1, total_frames=33)
    assert hero_row(plan_on, "consult").get("icon") == "calendar"
    # the built-in defaults are still active alongside the look's custom word
    words2 = [W("Just", 0.0, 0.2), W("book.", 0.2, 0.5)]
    plan_builtin_still_on = cluster_plan.build(words2, TRACK, base_spec(icon_words=["consult:calendar"]),
                                               cut_s=0.8, total_frames=24)
    assert hero_row(plan_builtin_still_on, "book").get("icon") == "calendar"


AGENCY_HEROES = ("ads", "video", "content", "post", "feed", "track")


def test_a_look_gets_no_agency_hero_icon_unless_it_lists_one():
    """ad, video, content, post, feed and track are an agency's own icon words: the engine's own defaults are plain."""
    for text in AGENCY_HEROES:
        words = [W("Our", 0.0, 0.2), W(text + ".", 0.2, 0.6)]
        bare = cluster_plan.build(words, TRACK, base_spec(), cut_s=1.0, total_frames=30)
        assert hero_row(bare, text).get("icon") is None, text
        listed = cluster_plan.build(words, TRACK, base_spec(icon_words=[f"{text}:play"]), cut_s=1.0, total_frames=30)
        assert hero_row(listed, text).get("icon") == "play", text


def test_a_look_that_lists_the_agency_words_gets_their_hero_icons():
    """varsans lists ad, ads, video, post, content, feed and track in its own icon_words: the look's list is what gives them their icons."""
    listed = dict(item.split(":") for item in _fixture_spec("varsans")["icon_words"])
    expected = (("ads", "play"), ("video", "play"), ("content", "grid"), ("post", "grid"), ("feed", "grid"), ("track", "search"))
    for text, icon in expected:
        assert listed[text] == icon
        words = [W("Our", 0.0, 0.2), W(text + ".", 0.2, 0.6)]
        plan = cluster_plan.build(words, TRACK, fixture_spec("varsans"), cut_s=1.0, total_frames=30)
        assert hero_row(plan, text).get("icon") == icon, text


def test_a_look_without_icon_words_gets_no_agency_icon_from_its_fixture_either():
    """upstatic lists no icon words, so the same six words stay plain on it."""
    assert _fixture_spec("upstatic")["icon_words"] == []
    for text in AGENCY_HEROES:
        words = [W("Our", 0.0, 0.2), W(text + ".", 0.2, 0.6)]
        plan = cluster_plan.build(words, TRACK, fixture_spec("upstatic"), cut_s=1.0, total_frames=30)
        assert hero_row(plan, text).get("icon") is None, text


def _pill_words(spec):
    """every word a look's pill_icons list names ("booking,calls:calendar" names booking and calls)"""
    return [w.strip() for item in spec["pill_icons"] for w in item.partition(":")[0].split(",") if w.strip()]


def test_a_pill_only_word_is_never_a_hero_row_icon():
    """pill_icons feeds frame pills alone: right, best, show, fit, local, shop, payment, booked, leads and the like give a pill
    an icon but never a hero row, which is where icon_words (the shorter list) is read. The words a fixture look names in its own pill_icons count too."""
    generic = ("right", "best", "show", "fit", "local", "shop", "payment", "booked", "leads", "reports", "respond")
    for brand_id in cap_helpers.BRANDS:
        spec = fixture_spec(brand_id)
        listed = {item.split(":")[0] for item in spec["icon_words"]}
        for text in generic + tuple(_pill_words(spec)):
            if text in listed or text in cluster_plan.ICON_BUILTIN:
                continue
            words = [W("Our", 0.0, 0.2), W(text + ".", 0.2, 0.6)]
            plan = cluster_plan.build(words, TRACK, fixture_spec(brand_id), cut_s=1.0, total_frames=30)
            assert hero_row(plan, text).get("icon") is None, (brand_id, text)


def test_the_fixture_look_with_pill_icons_names_some_pill_words():
    # guards the test above: varsans really does name pill words, so the loop is not running on nothing
    assert _pill_words(_fixture_spec("varsans"))


def test_icon_off_via_device_override_disables_the_trigger():
    words = [W("Just", 0.0, 0.2), W("book.", 0.2, 0.5)]
    plan = cluster_plan.build(words, TRACK, base_spec(device_overrides={"icon": False}), cut_s=0.8, total_frames=24)
    assert hero_row(plan, "book").get("icon") is None


# ---------------------------------------------------------------------------------------------
# cluster_plan.py: byte-identical guarantee (no trigger words present)
# ---------------------------------------------------------------------------------------------

PACING_DEVICE_KEYS = ("logo", "icon")


def _assert_no_pacing_keys(plan):
    for w in all_rows(plan):
        for k in PACING_DEVICE_KEYS:
            assert k not in w, f"unexpected pacing key {k!r} on {w!r} with no trigger word present"


def test_plan_carries_no_logo_or_icon_keys_when_the_transcript_has_no_trigger_words():
    words = (
        [W("This", 0.0, 0.2), W("is", 0.2, 0.3), W("Sam.", 0.3, 0.6)]
        + [W("We've", 1.0, 1.2), W("done", 1.2, 1.4), W("this", 1.4, 1.55), W("for", 1.55, 1.7),
           W("11", 1.7, 1.9), W("years.", 1.9, 2.2)]
        + [W("Over", 3.0, 3.2), W("at", 3.2, 3.3), W("Acme", 3.3, 3.5), W("Corp.", 3.5, 3.85)]
    )
    plan = cluster_plan.build(words, TRACK, base_spec(), cut_s=4.15, total_frames=125)
    _assert_no_pacing_keys(plan)


def test_the_planner_is_deterministic_on_a_spec_with_no_trigger_words():
    """The same regression pattern the devices tests use for their own feature: freeze a words and spec pair with no logo or icon trigger
    words and confirm build() round-trips through JSON identically. LOGO_MAP and ICON_BUILTIN are always-on constants, so they must never
    perturb a take that does not say any of those words."""
    words = [W("Hey.", 0.16, 0.56), W("This", 0.6, 0.76), W("is", 0.76, 0.92), W("Sam.", 0.92, 1.3)]
    spec = {
        "accent": _fixture_spec("upstatic")["accent"], "zone": "top", "width": 960, "top_y0": 330, "hero_max": 360,
        "row_px": [120, 140], "chin_gap": 125, "font": copy.deepcopy(_fixture_spec("upstatic")["font"]),
        "no_behind": False, "case": "upper",
        "look": {"base": "#FFFFFF", "hero_base": "#FFFFFF", "chip": False,
                 "hero_shadow": "0 12px 26px rgba(0,0,0,0.45), 0 2px 6px rgba(0,0,0,0.30)",
                 "text_opacity": 0.96},
    }
    plan_a = cluster_plan.build(copy.deepcopy(words), TRACK, copy.deepcopy(spec), cut_s=1.6, total_frames=48)
    plan_b = cluster_plan.build(copy.deepcopy(words), TRACK, copy.deepcopy(spec), cut_s=1.6, total_frames=48)
    assert json.dumps(plan_a, sort_keys=True) == json.dumps(plan_b, sort_keys=True)
    _assert_no_pacing_keys(plan_a)


# ---------------------------------------------------------------------------------------------
# cadence.py: change_timeline / _structural_timeline (pure)
# ---------------------------------------------------------------------------------------------

def test_change_timeline_includes_cuts_cluster_bounds_device_onsets_layout_and_whips():
    plan = {
        "events": [
            {"mode": "front", "start": 1.0, "end": 1.5, "words": [{"text": "hi", "at": 1.0}]},
            {"mode": "behind", "start": 3.0, "end": 3.6,
             "words": [{"text": "50", "at": 3.2, "count": 50}]},
            {"mode": "cta", "start": 8.0, "end": 9.0},
        ],
        "whips": [{"at": 60, "frames": 3}],   # frame 60 -> 2.0s
    }
    t = cadence.change_timeline(plan, cuts=[0.0, 5.0])
    assert t == [0.0, 1.0, 1.5, 2.0, 3.0, 3.2, 3.6, 5.0, 8.0, 9.0]


def test_structural_timeline_excludes_plain_cluster_bounds_but_keeps_device_onsets():
    plan = {
        "events": [
            {"mode": "front", "start": 1.0, "end": 1.5, "words": [{"text": "hi", "at": 1.0}]},
            {"mode": "behind", "start": 3.0, "end": 3.6,
             "words": [{"text": "50", "at": 3.2, "count": 50}]},
        ],
        "whips": [],
    }
    t = cadence._structural_timeline(plan, cuts=[0.0])
    assert 1.0 not in t and 1.5 not in t          # plain cluster bounds are not structural
    assert 3.2 in t                                # the count device's own onset is structural
    assert 3.0 not in t and 3.6 not in t           # that cluster's OWN start/end are still plain


# ---------------------------------------------------------------------------------------------
# cadence.py: insert_punches (pure gap-filling)
# ---------------------------------------------------------------------------------------------

def test_nine_second_quiet_stretch_gets_punches_at_cluster_onsets_inside_it():
    """The concrete scenario: cuts bound a 9s span with nothing structural
    happening inside it, but two PLAIN caption clusters land at 4.0s and 7.0s. Because plain
    cluster bounds are not part of the structural (gap-detection) timeline, they are legitimately
    "inside" the 9s gap and get preferred as landing spots over the arbitrary +1.5s fallback."""
    plan = {
        "durationInFrames": 9 * FPS,
        "events": [
            {"mode": "front", "start": 4.0, "end": 4.3, "words": [{"text": "ok", "at": 4.0}]},
            {"mode": "front", "start": 7.0, "end": 7.3, "words": [{"text": "ok2", "at": 7.0}]},
        ],
        "whips": [],
    }
    cuts = [0.0, 9.0]
    punches, quiet = cadence.insert_punches(plan, cuts, TRACK, max_gap=3.0)
    ats = sorted(round(p["at"] / FPS, 2) for p in punches)
    assert 4.0 in ats and 7.0 in ats, f"expected punches at the real cluster onsets, got {ats}"
    assert quiet == []
    for p in punches:
        for c in cuts:
            assert abs(p["at"] / FPS - c) * FPS >= 8 - 1e-6, f"punch at {p['at']} too close to cut {c}"


def test_punches_never_land_within_eight_frames_of_a_cut():
    plan = {"durationInFrames": 12 * FPS,
            "events": [{"mode": "front", "start": 2.15, "end": 2.4, "words": [{"text": "x", "at": 2.15}]}],
            "whips": []}
    cuts = [0.0, 2.2, 12.0]   # a cluster onset sits inside 8 frames (0.267s) of the cut at 2.2
    punches, quiet = cadence.insert_punches(plan, cuts, TRACK, max_gap=3.0)
    for p in punches:
        for c in cuts:
            assert abs(p["at"] / FPS - c) * FPS >= 8 - 1e-6


def test_fallback_offset_used_when_no_cluster_lands_in_the_gap():
    plan = {"durationInFrames": 20 * FPS, "events": [], "whips": []}
    punches, quiet = cadence.insert_punches(plan, [0.0, 20.0], TRACK, max_gap=3.0)
    assert punches, "a totally dead 20s span must still get automatic punches"
    assert quiet == []
    pn = cadence.pace_numbers(plan, [0.0, 20.0], punches)
    assert pn["max_gap"] <= 3.0 + 1e-6


def test_quiet_stretch_reported_when_punches_off():
    """device_overrides.punch = False: no automatic punches, but the quiet report still runs."""
    plan = {"durationInFrames": 20 * FPS, "events": [], "whips": []}
    end_t = 20.0
    base = cadence._structural_timeline(plan, [0.0, 20.0])
    pts = sorted(set([0.0] + base + [end_t]))
    quiet = [[t0, t1] for t0, t1 in zip(pts, pts[1:]) if t1 - t0 > 3.0]
    assert quiet == [[0.0, 20.0]]


def _cadence_files(folder, overrides=None, plan=None, cuts=(0.0, 20.0)):
    """plan.json, facetrack.json, cuts.json and (when `overrides` is given) a spec.json holding device_overrides, written into `folder`;
    the paths cadence.run takes. The plan is a 20 s take with nothing on screen unless `plan` says otherwise."""
    folder.mkdir(parents=True, exist_ok=True)
    plan = plan or {"durationInFrames": 20 * FPS, "events": [], "whips": []}
    plan_path, cuts_path, track_path = folder / "plan.json", folder / "cuts.json", folder / "facetrack.json"
    plan_path.write_text(json.dumps(plan))
    track_path.write_text(json.dumps(TRACK))
    cuts_path.write_text(json.dumps({"cuts": list(cuts)}))
    spec_path = folder / "spec.json"
    if overrides is not None:
        spec_path.write_text(json.dumps({"device_overrides": overrides}))
    elif spec_path.exists():
        spec_path.unlink()
    return str(folder / "mez.mp4"), str(plan_path), str(track_path), str(cuts_path)


def _cadence_run(folder, overrides=None, plan=None, **kw):
    mez, plan_path, track_path, cuts_path = _cadence_files(folder, overrides, plan)
    return cadence.run(mez, plan_path, track_path, plan_path, cuts_path=cuts_path, **kw)


def test_run_honors_punch_off_device_override(tmp_path):
    out = _cadence_run(tmp_path, {"punch": False})
    assert out["punches"] == []
    assert out["pace"]["quiet"] == [[0.0, 20.0]]


def test_run_honors_punch_add_and_punch_off_overrides(tmp_path):
    out = _cadence_run(tmp_path / "add", {"punch_add": [10.0]})
    assert any(p["at"] == 300 for p in out["punches"])   # 10.0s * 30fps

    # now cancel it with punch_off at the same time
    out2 = _cadence_run(tmp_path / "cancel", {"punch_add": [10.0], "punch_off": [10.0]})
    assert not any(abs(p["at"] / FPS - 10.0) <= 1.0 for p in out2["punches"])


def test_cuts_json_is_reused_not_recomputed(tmp_path):
    cuts_path = tmp_path / "cuts.json"
    json.dump({"cuts": [1.23, 4.56]}, open(cuts_path, "w"))
    got = cadence.cuts_for("/does/not/exist.mp4", str(cuts_path))
    assert got == [1.23, 4.56]   # never touched ffmpeg: the file existed and was reused


# ---------------------------------------------------------------------------------------------
# cadence.py: the switch for the automatic punch-ins (run(auto=...), --auto on|off)
# ---------------------------------------------------------------------------------------------

def test_the_automatic_punch_ins_are_on_by_default_in_the_function():
    assert inspect.signature(cadence.run).parameters["auto"].default is True


def test_auto_on_punches_a_long_quiet_plan(tmp_path):
    out = _cadence_run(tmp_path, auto=True)
    assert out["punches"], "a dead 20 s plan gets automatic punches while auto is on"
    assert out["pace"]["punches_added"] == len(out["punches"]) and out["pace"]["quiet"] == []
    assert all(p["scale"] == cadence.PUNCH_SCALE for p in out["punches"])


def test_auto_off_adds_no_punch_to_the_plan_auto_on_punches(tmp_path):
    on = _cadence_run(tmp_path / "on", auto=True)
    off = _cadence_run(tmp_path / "off", auto=False)
    assert on["punches"] and off["punches"] == []
    assert off["pace"]["punches_added"] == 0


def test_auto_off_still_makes_the_pace_numbers_and_the_quiet_report(tmp_path):
    """The cuts, the pace numbers and the quiet report are made either way: a person still sees where the gaps are."""
    off = _cadence_run(tmp_path, auto=False)
    assert off["pace"]["quiet"] == [[0.0, 20.0]]
    assert off["pace"]["max_gap"] == 20.0 and off["pace"]["punches_added"] == 0
    assert {"median_gap", "p90_gap", "max_gap", "punches_added", "quiet"} <= set(off["pace"])
    assert json.load(open(tmp_path / "plan.json"))["pace"] == off["pace"]       # and plan.json carries them for the gate


def test_an_explicit_punch_add_is_honored_with_auto_off(tmp_path):
    out = _cadence_run(tmp_path, {"punch_add": [10.0]}, auto=False)
    assert [p["at"] for p in out["punches"]] == [300]                            # exactly the one asked for, no automatic filling around it
    assert out["punches"][0]["scale"] == cadence.PUNCH_SCALE
    assert out["pace"]["punches_added"] == 1
    assert out["pace"]["quiet"] == [[0.0, 10.0], [10.0, 20.0]]                   # the rest of the gaps are still reported, not filled
    with_auto = _cadence_run(tmp_path / "auto", {"punch_add": [10.0]}, auto=True)
    assert any(p["at"] == 300 for p in with_auto["punches"]) and len(with_auto["punches"]) > 1


def test_an_explicit_punch_add_is_honored_even_next_to_a_cut_with_auto_off(tmp_path):
    """An explicit ask is exempt from the 8-frame guard, with auto on or off: it is never silently dropped."""
    mez, plan_path, track_path, cuts_path = _cadence_files(tmp_path, {"punch_add": [5.05]}, cuts=(0.0, 5.0, 20.0))
    out = cadence.run(mez, plan_path, track_path, plan_path, cuts_path=cuts_path, auto=False)
    assert [p["at"] for p in out["punches"]] == [round(5.05 * FPS)]


def test_punch_off_still_removes_an_explicit_punch_with_auto_off(tmp_path):
    out = _cadence_run(tmp_path, {"punch_add": [10.0], "punch_off": [10.0]}, auto=False)
    assert out["punches"] == []


def test_a_gate_punch_false_wins_over_auto_on(tmp_path):
    """GATE.md `punch: off` lands in device_overrides as punch False: it stops the automatic pass even when the chain asked for it."""
    out = _cadence_run(tmp_path, {"punch": False}, auto=True)
    assert out["punches"] == [] and out["pace"]["quiet"] == [[0.0, 20.0]]
    assert _cadence_run(tmp_path / "plain", auto=True)["punches"], "the same plan is punched when the gate does not say punch: off"


def test_a_gate_punch_false_still_lets_an_explicit_punch_add_through_with_auto_on(tmp_path):
    out = _cadence_run(tmp_path, {"punch": False, "punch_add": [10.0]}, auto=True)
    assert [p["at"] for p in out["punches"]] == [300]


def _real_clip(path):
    """A 20 s clip with two real hard cuts (white 7 s, black 7 s, gray 6 s: colors of very different brightness, since the detector reads gray
    frame differences) at 96x170, so cadence.py's own cut detector has something to find."""
    parts = [("white", 7), ("black", 7), ("gray", 6)]
    inputs = [a for c, d in parts for a in ("-f", "lavfi", "-i", f"color=c={c}:s=96x170:r=30:d={d}")]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", "concat=n=3:v=1:a=0", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


def _cadence_cli(folder, clip, *extra, overrides=None):
    """Run cadence.py as a program on `clip` with a fresh quiet plan beside it (cuts.json is not pre-written, so the program detects the cuts itself)."""
    folder.mkdir(parents=True, exist_ok=True)
    plan = folder / "plan.json"
    plan.write_text(json.dumps({"durationInFrames": 20 * FPS, "events": [], "whips": []}))
    (folder / "facetrack.json").write_text(json.dumps(TRACK))
    spec = folder / "spec.json"
    if overrides is not None:
        spec.write_text(json.dumps({"device_overrides": overrides}))
    r = subprocess.run([sys.executable, "-B", CADENCE, clip, str(plan), str(folder / "facetrack.json"), str(plan), *extra],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.load(open(plan)), r.stdout


def test_cli_punches_a_quiet_clip_by_default_and_with_auto_on(tmp_path):
    clip = _real_clip(tmp_path / "clip.mp4")
    default, said = _cadence_cli(tmp_path / "default", clip)
    assert default["punches"] and f"{len(default['punches'])} punch(es) added" in said
    cuts = json.load(open(tmp_path / "default" / "cuts.json"))["cuts"]
    assert len(cuts) == 2 and abs(cuts[0] - 7.0) < 0.1 and abs(cuts[1] - 14.0) < 0.1       # the program found the two real cuts itself
    for p in default["punches"]:
        assert all(abs(p["at"] / FPS - c) * FPS >= 8 - 1e-6 for c in cuts)
    on, said_on = _cadence_cli(tmp_path / "on", clip, "--auto", "on")
    assert on["punches"] == default["punches"] and said_on == said


def test_cli_auto_off_adds_no_punch_but_still_reports_the_quiet_stretches(tmp_path):
    clip = _real_clip(tmp_path / "clip.mp4")
    off, said = _cadence_cli(tmp_path / "off", clip, "--auto", "off")
    assert off["punches"] == [] and "0 punch(es) added" in said and "3 quiet stretch(es)" in said
    assert [q[0] for q in off["pace"]["quiet"]][0] == 0.0 and len(off["pace"]["quiet"]) == 3
    assert off["pace"]["punches_added"] == 0 and off["pace"]["max_gap"] >= 6.0
    assert len(json.load(open(tmp_path / "off" / "cuts.json"))["cuts"]) == 2             # the cuts are made either way


def test_cli_gate_punch_off_wins_over_auto_on_and_punch_add_survives_auto_off(tmp_path):
    clip = _real_clip(tmp_path / "clip.mp4")
    gated, _ = _cadence_cli(tmp_path / "gated", clip, "--auto", "on", overrides={"punch": False})
    assert gated["punches"] == [] and len(gated["pace"]["quiet"]) == 3
    asked, _ = _cadence_cli(tmp_path / "asked", clip, "--auto", "off", overrides={"punch_add": [10.0]})
    assert [p["at"] for p in asked["punches"]] == [300]


def test_cli_auto_with_no_value_is_one_plain_stop_line(tmp_path):
    clip = _real_clip(tmp_path / "clip.mp4")
    (tmp_path / "plan.json").write_text(json.dumps({"durationInFrames": 600, "events": [], "whips": []}))
    r = subprocess.run([sys.executable, "-B", CADENCE, clip, str(tmp_path / "plan.json"), str(tmp_path / "none.json"), str(tmp_path / "out.json"), "--auto"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == "" and r.stderr.startswith("STOP:") and "Traceback" not in r.stderr


# ---------------------------------------------------------------------------------------------
# capgate.py: "## Pacing" section: build() report lines + apply() override round-trip
# ---------------------------------------------------------------------------------------------

def _seed_pacing_take(tmp_path, pace=None):
    """cap_takes' hand-written take with a plan that carries `pace` (or no pace data at all when None), as cadence.py would have left it."""
    take_dir = cap_takes.make_take(tmp_path, zone="chest")
    plan = json.load(open(take_dir / "plan.json"))
    plan.update({"events": [], "whips": []})
    if pace is not None:
        plan["pace"] = pace
    cap_takes.write(take_dir / "plan.json", plan)
    return take_dir


def test_build_renders_pace_numbers_and_quiet_lines(tmp_path):
    take_dir = _seed_pacing_take(tmp_path, pace={
        "median_gap": 1.6, "p90_gap": 2.6, "max_gap": 5.9, "punches_added": 3,
        "quiet": [[15.0, 21.0]],
    })
    capgate.build(str(take_dir))
    text = (take_dir / "GATE.md").read_text()
    assert "## Pacing" in text
    assert "pace: median 1.6s" in text
    assert "punches +3" in text
    assert "pace: 15-21 quiet" in text
    assert "(pace gate limits: median <= 1.8s, p90 <= 3.5s, max <= 5.0s)" in text


def test_build_reports_missing_pace_data_plainly_instead_of_crashing(tmp_path):
    take_dir = _seed_pacing_take(tmp_path, pace=None)
    capgate.build(str(take_dir))
    text = (take_dir / "GATE.md").read_text()
    assert "no pacing data" in text


def test_gate_pacing_grammar_round_trips_through_apply(tmp_path):
    take_dir = _seed_pacing_take(tmp_path, pace={"median_gap": 1.0, "p90_gap": 1.0, "max_gap": 1.0,
                                                 "punches_added": 0, "quiet": []})
    capgate.build(str(take_dir))
    text = (take_dir / "GATE.md").read_text().replace("approved: no", "approved: yes", 1)
    anchor = "-->\n\n## Overrides"
    assert text.count(anchor) == 1
    text = text.replace(anchor, "-->\npunch: 12.4\nno-punch: 8.0\nlogo: off\nicon: off\n## Overrides", 1)
    (take_dir / "GATE.md").write_text(text)
    capgate.apply_gate(str(take_dir))
    ov = json.load(open(take_dir / "gate.json"))["overrides"]["devices"]["device_overrides"]
    assert ov["punch_add"] == [12.4]
    assert ov["punch_off"] == [8.0]
    assert ov["logo"] is False
    assert ov["icon"] is False


def test_gate_punch_off_grammar_round_trips_through_apply(tmp_path):
    take_dir = _seed_pacing_take(tmp_path, pace={"median_gap": 1.0, "p90_gap": 1.0, "max_gap": 1.0,
                                                 "punches_added": 0, "quiet": []})
    capgate.build(str(take_dir))
    text = (take_dir / "GATE.md").read_text().replace("approved: no", "approved: yes", 1)
    anchor = "-->\n\n## Overrides"
    text = text.replace(anchor, "-->\npunch: off\n## Overrides", 1)
    (take_dir / "GATE.md").write_text(text)
    capgate.apply_gate(str(take_dir))
    ov = json.load(open(take_dir / "gate.json"))["overrides"]["devices"]["device_overrides"]
    assert ov["punch"] is False


def test_pacing_overrides_merge_with_devices_overrides_not_replace_them(tmp_path):
    """A gate that activates BOTH a Devices override and a Pacing override must land both in the
    same spec.json device_overrides dict: one must not clobber the other."""
    take_dir = _seed_pacing_take(tmp_path, pace={"median_gap": 1.0, "p90_gap": 1.0, "max_gap": 1.0,
                                                 "punches_added": 0, "quiet": []})
    capgate.build(str(take_dir))
    text = (take_dir / "GATE.md").read_text().replace("approved: no", "approved: yes", 1)
    text = text.replace("-->\n\n## Pacing", '-->\nglow: off\n## Pacing', 1)
    text = text.replace("-->\n\n## Overrides", "-->\npunch: off\n## Overrides", 1)
    (take_dir / "GATE.md").write_text(text)
    capgate.apply_gate(str(take_dir))
    ov = json.load(open(take_dir / "gate.json"))["overrides"]["devices"]["device_overrides"]
    assert ov == {"glow": False, "punch": False}


# ---------------------------------------------------------------------------------------------
# reel_qc.py: G10 pace on synthetic spike lists, in each of its three modes
# ---------------------------------------------------------------------------------------------

# the spike lists the G10 tests share, each over the spoken span 0 to 20 s: one that passes and one for each limit
SPIKES_OK = list(np.arange(0.5, 19.5, 1.5))                              # a change roughly every 1.5s: comfortably inside 1.8/3.5/5.0
SPIKES_MEDIAN = list(np.arange(2.0, 20.0, 2.0))                          # every 2.0s: median and p90 both exactly 2.0s (over 1.8, under 3.5)
SPIKES_P90 = list(np.arange(0.5, 12.0, 1.0)) + [19.5]                    # frequent early changes, one long stretch near the end
SPIKES_MAX = [1.0, 2.0, 3.0, 3.5, 4.0, 19.0]                             # one 15s dead stretch in the middle
# ten quick changes, then four 4.2s gaps: median 0.3s and max 4.2s are inside their limits, and only the p90 gap (4.2s over 3.5) is not
SPIKES_P90_ONLY = [round(0.3 * i, 2) for i in range(1, 11)] + [7.2, 11.4, 15.6, 19.8]


def test_g10_passes_when_spikes_are_well_within_thresholds():
    g = reel_qc.g10_pace(SPIKES_OK, 0.0, 20.0)
    assert g["pass"] is True
    assert g["skipped"] is False


def test_g10_fails_on_median_gap():
    g = reel_qc.g10_pace(SPIKES_MEDIAN, 0.0, 20.0)
    assert g["pass"] is False
    assert any(d["why"] == "median" for d in g["detail"])
    assert not any(d["why"] == "max" for d in g["detail"])


def test_g10_fails_on_p90_gap():
    g = reel_qc.g10_pace(SPIKES_P90, 0.0, 20.0)
    assert g["pass"] is False
    assert any(d["why"] in ("p90", "max") for d in g["detail"])


def test_g10_fails_on_max_gap():
    g = reel_qc.g10_pace(SPIKES_MAX, 0.0, 20.0)
    assert g["pass"] is False
    assert any(d["why"] == "max" for d in g["detail"])


def test_g10_fails_on_the_p90_gap_alone():
    g = reel_qc.g10_pace(SPIKES_P90_ONLY, 0.0, 20.0)
    assert g["pass"] is False
    assert [d["why"] for d in g["detail"]] == ["p90"] and g["detail"][0]["p90"] == 4.2


def test_g10_is_report_only_under_eight_seconds():
    # this WOULD fail on max (6s gap) if it were a hard gate, but the spoken span is only 6.5s
    spikes = [0.2]
    g = reel_qc.g10_pace(spikes, 0.0, 6.5)
    assert g["pass"] is True
    assert g.get("report_only") is True
    assert any(d["why"] == "max" for d in g["detail"])   # still recorded, just not failed
    assert "spoken span under 8s" in g["numbers"]


def test_g10_skipped_with_no_spoken_span():
    g = reel_qc.g10_pace([], None, None)
    assert g["skipped"] is True
    assert g["pass"] is True


def test_g10_thresholds_match_the_documented_constants():
    assert reel_qc.PACE_MEDIAN_MAX == 1.8
    assert reel_qc.PACE_P90_MAX == 3.5
    assert reel_qc.PACE_MAX_MAX == 5.0
    assert reel_qc.PACE_REPORT_ONLY_UNDER_S == 8.0
    assert "G10" in reel_qc.MACHINE_GATES


FAILING = {"median": SPIKES_MEDIAN, "p90": SPIKES_P90_ONLY, "max": SPIKES_MAX}
EVERY_LIST = [SPIKES_OK, SPIKES_MEDIAN, SPIKES_P90, SPIKES_P90_ONLY, SPIKES_MAX, [], [0.2]]


def test_the_pace_modes_are_off_report_and_enforce():
    assert reel_qc.PACE_MODES == ("off", "report", "enforce")


def test_g10_with_no_mode_is_the_enforce_mode():
    for spikes in EVERY_LIST:
        assert reel_qc.g10_pace(spikes, 0.0, 20.0) == reel_qc.g10_pace(spikes, 0.0, 20.0, mode="enforce")


@pytest.mark.parametrize("spikes", EVERY_LIST, ids=["fine", "median", "p90-tail", "p90-alone", "max", "none", "one"])
def test_g10_mode_off_is_a_skipped_passing_gate_whatever_the_spikes_are(spikes):
    g = reel_qc.g10_pace(spikes, 0.0, 20.0, mode="off")
    assert g["skipped"] is True and g["pass"] is True and g["detail"] == []
    assert "pace gate off" in g["numbers"] and "report_only" not in g
    assert reel_qc.line("G10", g).startswith("G10 SKIP ")
    assert reel_qc.verdict_of({"G10": g}) == "PASS"


def test_g10_mode_off_skips_without_a_spoken_span_too():
    g = reel_qc.g10_pace([], None, None, mode="off")
    assert g["skipped"] is True and g["pass"] is True and "pace gate off" in g["numbers"]


@pytest.mark.parametrize("why,spikes", sorted(FAILING.items()))
def test_g10_mode_report_never_fails_but_still_lists_what_enforce_would_fail(why, spikes):
    enforced = reel_qc.g10_pace(spikes, 0.0, 20.0, mode="enforce")
    g = reel_qc.g10_pace(spikes, 0.0, 20.0, mode="report")
    assert enforced["pass"] is False
    assert g["pass"] is True and g["report_only"] is True and g["skipped"] is False
    assert g["detail"] == enforced["detail"] and any(d["why"] == why for d in g["detail"])
    assert g["numbers"].endswith("[report-only, pace gate is report]")
    assert reel_qc.line("G10", g).startswith("G10 AUDIT ")
    assert reel_qc.verdict_of({"G10": g}) == "PASS"


def test_g10_mode_report_on_a_take_that_passes_is_still_an_audit_with_nothing_to_list():
    g = reel_qc.g10_pace(SPIKES_OK, 0.0, 20.0, mode="report")
    assert g["pass"] is True and g["report_only"] is True and g["detail"] == [] and "[report-only" not in g["numbers"]
    assert reel_qc.line("G10", g).startswith("G10 AUDIT ")


def test_g10_mode_report_names_the_gate_not_the_length_when_both_would_apply():
    short = reel_qc.g10_pace([0.2], 0.0, 6.5, mode="report")
    assert short["pass"] is True and short["report_only"] is True and short["numbers"].endswith("[report-only, pace gate is report]")
    assert reel_qc.g10_pace([0.2], 0.0, 6.5, mode="enforce")["numbers"].endswith("[report-only, spoken span under 8s]")


def test_g10_mode_report_with_no_spoken_span_is_a_skip():
    g = reel_qc.g10_pace([], None, None, mode="report")
    assert g["skipped"] is True and g["pass"] is True


@pytest.mark.parametrize("why,spikes", sorted(FAILING.items()))
def test_g10_mode_enforce_fails_exactly_like_the_unmoded_gate(why, spikes):
    g = reel_qc.g10_pace(spikes, 0.0, 20.0, mode="enforce")
    assert g["pass"] is False and g["skipped"] is False and "report_only" not in g
    assert any(d["why"] == why for d in g["detail"])
    assert g == reel_qc.g10_pace(spikes, 0.0, 20.0)
    assert reel_qc.line("G10", g).startswith("G10 FAIL ")
    assert reel_qc.verdict_of({"G10": g}) == "FAIL"


def test_g10_mode_enforce_passes_a_paced_take_and_is_report_only_under_eight_seconds():
    g = reel_qc.g10_pace(SPIKES_OK, 0.0, 20.0, mode="enforce")
    assert g["pass"] is True and g["skipped"] is False and "report_only" not in g
    short = reel_qc.g10_pace([0.2], 0.0, 6.5, mode="enforce")
    assert short["pass"] is True and short["report_only"] is True and any(d["why"] == "max" for d in short["detail"])


def test_the_plain_sentences_for_a_pace_failure_read_the_same_in_every_mode():
    g = reel_qc.g10_pace(SPIKES_MAX, 0.0, 20.0, mode="report")
    said = reel_qc.plain("G10", [d for d in g["detail"] if d["why"] == "max"][0])
    assert "15" in said and "nothing changing on screen" in said
    med = reel_qc.plain("G10", [d for d in reel_qc.g10_pace(SPIKES_MEDIAN, 0.0, 20.0)["detail"] if d["why"] == "median"][0])
    assert "median gap 2" in med and "1.8" in med


# ---------------------------------------------------------------------------------------------
# reel_qc.py: punch origin warning (report-only, never fails G4)
# ---------------------------------------------------------------------------------------------

def test_punch_origin_warning_fires_when_origin_drifts_off_the_face():
    track = {"samples": {"5": {"cx": 540, "cy": 900, "w": 200, "h": 200}}, "median": {"cx": 540, "cy": 900, "w": 200, "h": 200}}
    punches = [{"at": 5 * FPS, "scale": 1.08, "hold": 24, "cx": 900, "cy": 900}]   # cx far off the 200px-wide face box
    warnings = reel_qc.punch_origin_warnings(punches, track, FPS)
    assert len(warnings) == 1
    assert warnings[0]["why"] == "punch_origin"


def test_punch_origin_warning_silent_when_origin_is_on_the_face():
    track = {"samples": {"5": {"cx": 540, "cy": 900, "w": 200, "h": 200}}, "median": {"cx": 540, "cy": 900, "w": 200, "h": 200}}
    punches = [{"at": 5 * FPS, "scale": 1.08, "hold": 24, "cx": 540, "cy": 900}]
    assert reel_qc.punch_origin_warnings(punches, track, FPS) == []


def test_g4_never_fails_from_a_punch_origin_warning_alone():
    """A punch origin warning rides in G4's own detail and numbers but must never flip `pass`: the
    ink sweep (frames=[]) is what decides that."""
    warnings = [{"why": "punch_origin", "at": 5.0, "cx": 900, "cy": 900, "face": {"x0": 440, "x1": 640, "y0": 800, "y1": 1000}}]
    g = reel_qc.g4_captions(frames=[], events=[], punch_warnings=warnings)
    assert g["pass"] is True   # skipped (no frames): report_only=False here, but never a fail
    assert g["punch_warnings"] == warnings
    assert "punch origin warning" in g["numbers"]
