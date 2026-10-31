#!/usr/bin/env python3
"""capgate.py — the human approval gate.

CLI:
    capgate.py build <take-dir>     # words.json + tier.json + spec.json + plan.json -> GATE.md
    capgate.py apply <take-dir>     # GATE.md (approved: yes) -> gate.json (+ devices.json, words.json fixes)
    capgate.py checks <take-dir>    # the plan's CHECK lines GATE.md does not show (run_captions.sh prints them, deliver.sh files them in NOTES.md)

`build` reads the take's transcript (words.json, {"transcript", "words":[...]}),
the room-check verdict (tier.json), the resolved zone (spec.json), and the caption plan
(plan.json, CaptionsDynamic props, events[].mode in behind/front/cta/page/frame/igpost) and writes a
single flat GATE.md an operator edits like a script: transcript as numbered sentences, every
behind/front hero cluster with its words and timestamp (the row picked as the hero is starred), and
commented Overrides, Devices and Pacing blocks showing every override form. An existing GATE.md is replaced
only after it is copied to GATE.md.bak-<timestamp>; run_captions.sh never calls build when a GATE.md exists.

`apply` re-reads GATE.md. If the header line does not read "approved: yes" it refuses and exits 3
with a plain sentence, never a traceback, never a silent pass-through. On approval it writes gate.json,
the single record of what was approved: spec_adapter.py reads it (--gate) to build spec.json, so tier.json
and spec.json are never edited in place. Everything apply does is a pure function of GATE.md plus
words.json.pre-gate (the transcript as it was before any `fix:`), so running it twice gives the same result
and removing a line from GATE.md really removes its effect.

Active lines (a line inside a <!-- --> comment is inert; move it out, or add a new one):
  ## Tier & zone   `tier: X` / `zone: Y` edited away from the values GATE.md was built with (recorded in the
                   `auto:` comment) are overrides, same as the lines below.
  ## Overrides     zone: top|chest     tier: MINIMAL|REDUCED|FULL     fix: "from" -> "to"
                   hero: "word"        the word is the hero of the cluster that holds it
                   no-hero: "word"     the word is never picked as a hero (unless nothing else is left)
                   allow: "word"       a do_not_say word the operator accepts in the captions (QC gate G-WORDS)
                   subject: none       nobody is on camera (voiceover, screen recording): G-FACE becomes audit-only
                   style: <id>         a named caption style (Brand/styles/<id>.json, styles.py) laid over your look
                                       for this take, in place of the one in caption-style.txt. run_captions.sh reads it straight from the file
                                       (`capgate.py style <take-dir>`) when it makes look.json, so after editing it run the same
                                       command again and the spec, plan and render are made again
                   note: free text     recorded in gate.json
                   Any other line in this block is reported as a warning and ignored.
  ## Devices       page:/cta:/frame:/igpost: become devices.json (always rewritten, or removed when none);
                   neg:/no-neg:/strike:/pin:/count:/glow:/front:/behind:/finale: become device_overrides.
  ## Pacing        punch:/no-punch:/punch: off/logo: off/icon: off join device_overrides.
`hero:`/`no-hero:` ride in device_overrides too (hero_add, hero_drop), where cluster_plan.py reads them.

Devices (the README, "Devices"): `build` also renders a
"## Devices" section, this take's own auto-detected layout-event PROPOSALS (devices.py propose,
read from <take-dir>/devices-propose.json when run_captions.sh already wrote one, else computed live so
this works standalone too; both only when your look has `devices: on`) as commented-out `propose: <mode> <start>-<end> ...` lines, plus the generic
word-device/layout-event override forms as inert examples. `apply` parses the section's ACTIVE lines.

Pacing (the README, "Pacing"): `build` also renders a
"## Pacing" section, cadence.py's own plan["pace"] numbers (median/p90/max gap, punches added) as
a plain `pace: median ... p90 ... max ... punches +N` line, one `pace: <t0>-<t1> quiet` report line
per unresolved quiet stretch (both report-only, never parsed back as directives), then the override
forms as inert examples. `apply` parses the section's ACTIVE lines.

Thoughts (the soft build, the README, "The soft build"): for a soft build plan `build`
renders a "## Thoughts" section after the hero list, one `- <first word's start>s lead-in | payoff | tail`
line a thought (the soft event's own `line`: the words exactly as spoken, ` [bar]` on the bar's line),
then a CHECK line for a thought too quick to read in time (the event's short), for each device a soft row
could not keep (its `dropped`), for each red word a person or your look named that its thought draws in
its line's own colour (the event's red_off), for a last word a layout event's edge cuts off before it is
drawn (the event's last_unread) and for each thought legible.py flagged, then an `auto:` comment that
records the lines as built. `apply` hands the lines on (device_overrides.thoughts, for cluster_plan.py's
soft_events) only when a person edited them: an untouched section leaves the split to the planner, so a
`fix:` line never has to be copied into it. The time on a line is read back as nothing but a label: the
words decide. Every CHECK line quotes the words as spoken, as the Thoughts lines do. GATE.md is built
once, from the first plan, so `checks` prints the CHECK lines a later plan raises that GATE.md does not
show. A soft build take has no hero words, so its Hero words line points at the Thoughts section, and
`apply` records each `hero:`, `no-hero:`, `front:`, `behind:`, `finale:`, `glow:` and `zone: top` line
as a warning: the soft build reads none of them.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import sys

import devices
from cluster_plan import LOGO_MAP, tok
from keys import cli, EngineStop

VALID_ZONES = ("top", "chest")
VALID_TIERS = ("MINIMAL", "REDUCED", "FULL")
HERO_MODES = ("behind", "front")

APPROVED_RE = re.compile(r"^approved:\s*(yes|no)\s*$", re.I | re.M)
OVERRIDES_HEADING_RE = re.compile(r"^##\s*Overrides\s*$", re.I | re.M)
DEVICES_HEADING_RE = re.compile(r"^##\s*Devices\s*$", re.I | re.M)
PACING_HEADING_RE = re.compile(r"^##\s*Pacing\s*$", re.I | re.M)
NEXT_HEADING_RE = re.compile(r"^##\s", re.M)
ZONE_RE = re.compile(r'^\s*zone\s*:\s*(\S+)\s*$', re.I)
TIER_RE = re.compile(r'^\s*tier\s*:\s*(\S+)\s*$', re.I)
FIX_RE = re.compile(r'^\s*fix\s*:\s*"(.*?)"\s*->\s*"(.*?)"\s*$', re.I)
NOHERO_RE = re.compile(r'^\s*no-hero\s*:\s*(.+?)\s*$', re.I)
HERO_RE = re.compile(r'^\s*hero\s*:\s*(.+?)\s*$', re.I)
ALLOW_RE = re.compile(r'^\s*allow\s*:\s*(.+?)\s*$', re.I)
SUBJECT_RE = re.compile(r'^\s*subject\s*:\s*(none|face)\s*$', re.I)
STYLE_RE = re.compile(r'^\s*style\s*:\s*(\S+)\s*$', re.I)
STYLE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")   # styles.py's id shape; whether the style exists is the take's `takectl.py style` check
NOTE_RE = re.compile(r'^\s*note\s*:\s*(.+?)\s*$', re.I)
TIERZONE_HEADING_RE = re.compile(r"^##\s*Tier\s*&\s*zone\s*$", re.I | re.M)
AUTO_RE = re.compile(r"<!--\s*auto:\s*tier=(\w+)\s+zone=(\w+)\s*-->")
# --- devices section grammar ---
PAGE_LINE_RE = re.compile(r'^\s*page\s*:\s*([\d.]+)\s*-\s*([\d.]+)\s+"(.*?)"\s*$', re.I)
CTA_LINE_RE = re.compile(r'^\s*cta\s*:\s*([\d.]+)\s*-\s*([\d.]+)\s+"(.*?)"(?:\s+"(.*?)")?\s*$', re.I)
FRAME_LINE_RE = re.compile(r'^\s*frame\s*:\s*([\d.]+)\s*-\s*([\d.]+)\s+(.+?)\s*$', re.I)
IGPOST_LINE_RE = re.compile(r'^\s*igpost\s*:\s*([\d.]+)\s*-\s*([\d.]+)(?:\s+pixelate\s+(\d+))?\s*$', re.I)
NEG_LINE_RE = re.compile(r'^\s*neg\s*:\s*"(.*?)"\s*$', re.I)
NONEG_LINE_RE = re.compile(r'^\s*no-neg\s*:\s*"(.*?)"\s*$', re.I)
FRONT_LINE_RE = re.compile(r'^\s*front\s*:\s*"(.*?)"\s*$', re.I)     # bring the cluster holding this phrase in front of the person
BEHIND_LINE_RE = re.compile(r'^\s*behind\s*:\s*"(.*?)"\s*$', re.I)   # ...or keep it behind the head
# finale: "You think it" | "we ink it"  -> the closing pair pinned top/behind + bottom/front, both
# held to the cut. Take-specific, so it lives at the gate, not in your look.
FINALE_RE = re.compile(r'^\s*finale\s*:\s*"(.*?)"\s*\|\s*"(.*?)"\s*$', re.I)
STRIKE_LINE_RE = re.compile(r'^\s*strike\s*:\s*"(.*?)"\s*$', re.I)
PIN_LINE_RE = re.compile(r'^\s*pin\s*:\s*(on|off)\s*$', re.I)
COUNT_LINE_RE = re.compile(r'^\s*count\s*:\s*(on|off)\s*$', re.I)
GLOW_LINE_RE = re.compile(r'^\s*glow\s*:\s*(on|off)\s*$', re.I)
CTA_ROW0_SIZE, CTA_ROW0_WEIGHT = 190, 800   # measured constants -- not per-instance
CTA_ROW1_SIZE, CTA_ROW1_WEIGHT = 74, 600
# --- pacing section grammar (the README, "Pacing") ---
PUNCH_LINE_RE = re.compile(r'^\s*punch\s*:\s*(off|[\d.]+)\s*$', re.I)     # punch: 12.4  OR  punch: off
NOPUNCH_LINE_RE = re.compile(r'^\s*no-punch\s*:\s*([\d.]+)\s*$', re.I)
PACE_LOGO_LINE_RE = re.compile(r'^\s*logo\s*:\s*(on|off)\s*$', re.I)
PACE_ICON_LINE_RE = re.compile(r'^\s*icon\s*:\s*(on|off)\s*$', re.I)
# --- thoughts section grammar (the soft build) ---
THOUGHTS_HEADING_RE = re.compile(r"^##\s*Thoughts\s*$", re.I | re.M)
THOUGHT_LINE_RE = re.compile(r"^\s*-\s+(?:\d+\.\d+s\s+)?(.*?)\s*$")     # - 72.09s lead-in | payoff | tail [bar]
THOUGHTS_AUTO_RE = re.compile(r"<!--\s*auto:\s*thoughts=(\w+)\s*-->")
THOUGHTS_NOTE = ("One caption per line: lead-in | payoff | tail. [bar] marks the one payoff on a bar. Move a | to regroup the words; the words "
                 "stay as spoken.")
DEVICE_NAMES = {"neg": "red", "strike": "strike", "pin": "pin", "spaced": "spread", "logo": "logo", "icon": "icon"}   # a dropped device, as a CHECK line says it
SOFT_HERO_NOTE = ("A starred part (*\"words\"*) is its thought's payoff. Regroup the words and move the bar in the Thoughts section below: a soft build take "
                  "reads no `hero:` or `no-hero:` line.")
# what a soft build take does not read, and why: each such line of an approved GATE.md is a warning in gate.json (and NOTES.md), never dropped without a word
SOFT_NO_HERO = "a soft build take has no hero words; regroup its thoughts in the Thoughts section"
SOFT_IN_FRONT = "a soft build take draws every thought in front of the person, under the chin"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _need(take_dir, name):
    path = os.path.join(take_dir, name)
    if not os.path.exists(path):
        raise EngineStop(
            f"missing {name} in {take_dir}: run run_captions.sh's earlier stages first "
            f"(it chains intake -> ... -> spec_adapter -> cluster_plan before this gate)"
        )
    try:
        return json.load(open(path))
    except json.JSONDecodeError as e:
        raise EngineStop(f"{name} in {take_dir} is not valid JSON: {e}")


def sentences(transcript):
    """Split a transcript into displayed sentences. Simple end-punctuation split — good enough
    for a spoken-word transcript, no abbreviation handling needed here."""
    transcript = (transcript or "").strip()
    if not transcript:
        return []
    parts = re.findall(r"[^.!?]+[.!?]*", transcript)
    return [p.strip() for p in parts if p.strip()]


def hero_words(plan):
    """Every behind/front cluster in plan.json's events, in order, with its words and start time.
    Layout events (cta, page, frame, igpost) are left out on purpose: the operator reviews hero placement here, and
    layout events are listed under Devices."""
    out = []
    for ev in plan.get("events", []) or []:
        mode = ev.get("mode")
        if mode not in HERO_MODES:
            continue
        words = ev.get("words") or []
        start = ev.get("start")
        if start is None and words:
            start = words[0].get("at")
        texts = [w.get("text", "") for w in words if w.get("text")]
        heroes = [bool(w.get("hero")) for w in words if w.get("text")]
        out.append({"mode": mode, "start": start, "texts": texts, "heroes": heroes})
    out.sort(key=lambda h: (h["start"] is None, h["start"]))
    return out


def _brand_name(take_dir, spec, plan, tier):
    brand_path = os.path.join(take_dir, "look.json")
    if os.path.exists(brand_path):
        try:
            name = json.load(open(brand_path)).get("brand")
            if name:
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return spec.get("brand") or plan.get("brand") or tier.get("brand") or "Unknown"


def _devices_on(take_dir):
    """Whether the take's look.json turns the layout-event proposals on (devices: on). Off when there is no look.json: they are off unless asked for."""
    try:
        with open(os.path.join(take_dir, "look.json")) as f:
            return json.load(f).get("devices") == "on"
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _device_proposals(take_dir, words_doc):
    """<take-dir>/devices-propose.json, written by run_captions.sh's own `devices.py propose` stage
    before the gate is built -- or, so this function (and a direct unit test) works without that
    pipeline stage having run at all, computed live from the same inputs devices.propose() wants.
    Nothing is proposed unless the take's look has devices on."""
    if not _devices_on(take_dir):
        return []
    path = os.path.join(take_dir, "devices-propose.json")
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except (OSError, json.JSONDecodeError):
            pass
    return devices.propose(words_doc.get("words", []))


def _format_propose_line(d):
    """A `propose: <mode> <start>-<end> ...` line in the SAME grammar as the accepted form (see
    the module docstring) -- the reviewer accepts one by deleting "propose: " and nothing else."""
    mode, span = d["mode"], f'{d["start"]:g}-{d["end"]:g}'
    if mode == "page":
        return f'propose: page {span} "{d["text"]}"'
    if mode == "cta":
        parts = " ".join(f'"{r["text"]}"' for r in d.get("rows", []))
        return f'propose: cta {span} {parts}'
    if mode == "frame":
        items = ", ".join(item["text"] for item in d.get("list", []))
        return f'propose: frame {span} {items}'
    if mode == "igpost":
        line = f'propose: igpost {span}'
        if d.get("pixelate"):
            line += f' pixelate {d["pixelate"]}'
        return line
    return f'propose: {mode} {span}'   # forward-compatible fallback, not expected to hit today


def _format_pace_lines(plan):
    """The "## Pacing" section's report lines (plain, never inside the comment -- report-only,
    the README, "Pacing"): cadence.py's own plan["pace"] numbers, then one
    `pace: <t0>-<t1> quiet` line per stretch cadence.py could not resolve with a punch. Missing
    plan["pace"] (cadence.py has not run yet -- capgate.py build() is being used standalone, or
    out of the normal run_captions.sh order) reports that plainly instead of crashing."""
    pace = plan.get("pace")
    if not pace:
        return ["(no pacing data -- run cadence.py before building this gate)"]
    lines = [f'pace: median {pace.get("median_gap", 0):g}s, p90 {pace.get("p90_gap", 0):g}s, '
             f'max {pace.get("max_gap", 0):g}s, punches +{pace.get("punches_added", 0)} '
             f'(pace gate limits: median <= 1.8s, p90 <= 3.5s, max <= 5.0s)']
    for t0, t1 in pace.get("quiet") or []:
        lines.append(f"pace: {t0:g}-{t1:g} quiet")
    return lines


def _plan_texts(plan):
    """(text, start) for every string the plan draws, case as rendered."""
    out = []
    for ev in plan.get("events") or []:
        t0 = ev.get("start") or 0
        for key in ("text",):
            if isinstance(ev.get(key), str):
                out.append((ev[key], t0))
        for coll in ("words", "rows", "list"):
            items = [it for it in ev.get(coll) or [] if isinstance(it, dict) and isinstance(it.get("text"), str)]
            for it in items:
                out.append((it["text"], it.get("at", t0)))
            if len(items) > 1:
                out.append((" ".join(it["text"] for it in items), t0))
    return out


def banned_hits(do_not_say, plan, allow=()):
    """[(word, seconds)] for every do_not_say entry (a word or a phrase) that a caption in the plan shows, except
    the ones the operator allowed. Whole-word, case-insensitive match."""
    allowed = {a.lower().strip() for a in allow}
    hits = []
    for bad in do_not_say or []:
        if bad.lower().strip() in allowed:
            continue
        rx = re.compile(r"(?<![\w'])" + r"\s+".join(re.escape(w) for w in bad.split()) + r"(?![\w'])", re.I)
        for text, t in _plan_texts(plan):
            if rx.search(text):
                hits.append((bad, float(t)))
                break
    return hits


def _banned_hits(take_dir, plan):
    path = os.path.join(take_dir, "look.json")
    try:
        with open(path) as f:
            return banned_hits(json.load(f).get("do_not_say") or [], plan)
    except (OSError, json.JSONDecodeError):
        return []


def _no_room_warning(plan):
    """The WARNING block for a take whose face leaves no legal place for some caption (cluster_plan.py flags it): under the chin there is
    less than one minimum row of room and there is none above the head, so the caption would sit on the face and G4 would fail it."""
    flag = next((str(f) for f in (plan.get("flags") or []) if str(f).startswith("no_caption_room:")), None)
    if not flag:
        return []
    n = flag.split(":", 1)[1]
    return ["## Room for captions", "",
            f"WARNING: this take has no room for {n} caption(s): the face leaves less than a minimum row of space under the chin and none above the head, "
            "so they would sit on the face and QC would fail them. Reframe the cut (more room under the chin), or `subject: none` under Overrides if nobody is on camera.",
            ""]


def _soft_events(plan):
    return [e for e in plan.get("events") or [] if e.get("soft")]


def _thoughts_key(lines):
    """A short hash of thought lines, their spacing evened out: how apply tells a Thoughts section a person edited from the one build wrote."""
    return hashlib.sha256("\n".join(" ".join(line.split()) for line in lines).encode()).hexdigest()[:16]


def _lost(names):
    """'its icon', 'its pin and its spread', 'its red, its strike and its pin'"""
    said = [f"its {DEVICE_NAMES.get(n, n)}" for n in names]
    return said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]


def _spoken(e):
    """The words each row of a soft thought says, as spoken (its Thoughts line's parts: the drawn text puts a caps look's payoff in capitals), or the
    words its rows draw when it has no line that fits them (a plan written by hand)."""
    rows = e.get("words") or []
    parts = e["line"].removesuffix(" [bar]").split(" | ") if e.get("line") else []
    return parts if len(parts) == len(rows) else [r["text"] for r in rows]


def _dropped_checks(e):
    """[(seconds, CHECK line)] for what the rows of one soft thought could not keep (their dropped): the bar took its place on the bar's row, anywhere
    else it did not fit. A device is named with its row's words and time; a lost logo with its platform name's own word and time, every platform name in
    the thought that draws none, so the line never reads as if a logo that is drawn were missing (the first one on a payoff that keeps its logo draws).
    The words are quoted as spoken."""
    rows, out = e.get("words") or [], []
    for row, said in zip(rows, _spoken(e)):
        rest = [n for n in row.get("dropped") or [] if n != "logo"]
        if rest:
            out.append((row["at"], f'CHECK {row["at"]:.2f}s "{said}" lost {_lost(rest)}: {"the bar took its place" if row.get("bar") else "it did not fit"}'))
    if any("logo" in (row.get("dropped") or []) for row in rows):
        for row, said in zip(rows, _spoken(e)):
            names = [(word, at) for word, at in zip(said.split(), row.get("ats") or []) if tok(word) in LOGO_MAP]
            why = "the bar took its place" if row.get("bar") else "it did not fit"
            out += [(at, f'CHECK {at:.2f}s "{word}" lost its logo: {why}') for word, at in names[1 if row.get("logo") else 0:]]
    return out


def _legibility_checks(plan):
    """[(seconds, CHECK line)] for each thought legible.py flagged (legibility:<its first arrival>): one over a picture still bright at its lowest spot, or
    one whose picture could not be read, as its record in plan["legible"] says. The words are quoted as spoken. A flag whose thought is gone from the
    plan (the devices stage drops a thought a page or a cta covers: its words are on the page) names nothing a person could look at, so it says nothing."""
    events = {round(e["start"], 2): e for e in _soft_events(plan)}
    records = {r.get("at"): r for r in (plan.get("legible") or {}).get("thoughts") or []}
    out = []
    for flag in plan.get("flags") or []:
        name, _, value = str(flag).partition(":")
        if name != "legibility":
            continue
        at = round(float(value), 2)
        e = events.get(at)
        if e is None:
            continue
        what = f'"{" ".join(_spoken(e))}"'
        error = (records.get(at) or {}).get("error")
        out.append((at, f"CHECK {at:.2f}s {what} could not be checked for legibility: {error}" if error else
                        f"CHECK {at:.2f}s {what} may be hard to read: the picture under it is bright even at its lowest spot"))
    return out


def _last_word_checks(e):
    """[(seconds, CHECK line)] for a soft thought the planner marked last_unread: a layout event's edge cuts off its last word, which it leaves before that
    word is drawn (thoughts.last_word_cut_off; the reading time times only its first word and its payoff's). It names that word as spoken and its time;
    a name said as one word and drawn as two is quoted whole."""
    rows = e.get("words") or []
    if not e.get("last_unread") or not rows or not rows[-1].get("ats"):
        return []
    ats = rows[-1]["ats"]
    at = ats[-1]
    n = len(ats) - next(k for k in range(len(ats)) if ats[k] == at)
    word = " ".join(_spoken(e)[-1].split()[-n:])
    return [(at, f'CHECK {at:.2f}s "{word}" is not fully drawn before its thought leaves: regroup its line, or let it stand')]


def _short_checks(e):
    """[(seconds, CHECK line)] for a soft thought the planner marked short: it cannot be read in time (on screen 1.0 s, its payoff's first word fully drawn
    0.4 s before it leaves). Worded as a plain sentence, at its first word's spoken start, as its Thoughts line
    shows it, and in its words as spoken."""
    rows = e.get("words") or []
    if not e.get("short") or not rows:
        return []
    at = rows[0]["at"]
    return [(at, f'CHECK {at:.2f}s "{" ".join(_spoken(e))}" is too quick to read in time (on screen under 1 s, or its payoff under 0.4 s): regroup its '
                 "line, or let it stand")]


def _red_checks(e):
    """[(seconds, CHECK line)] for each red word a person or your look named that a soft thought draws in its line's own colour, your look's text colour (the
    event's red_off: its payoff does not hold the word, and red is drawn only on a payoff), the word as spoken at its own time. The planner leaves the
    built-in red words out: they were only ever drawn on a cluster's hero row."""
    return [(r["at"], f'CHECK {r["at"]:.2f}s "{r["text"]}" draws in the line\'s own colour: red is drawn only on a payoff, and its thought\'s payoff does '
                      "not hold it; regroup its line, or let it stand") for r in e.get("red_off") or ()]


def _soft_checks(plan):
    """Every CHECK line a soft build plan raises, in time order: a thought too quick to read, what its rows could not keep, a named red word drawn in its
    line's own colour, a last word its thought leaves before it is drawn, and the thoughts legible.py flagged."""
    events = _soft_events(plan)
    return [line for _, line in sorted([c for e in events for c in _short_checks(e) + _dropped_checks(e) + _red_checks(e) + _last_word_checks(e)]
                                      + _legibility_checks(plan), key=lambda c: c[0])]


def _thoughts_section(plan):
    """GATE.md's "## Thoughts" section for a soft build plan (module notes): one line a thought, the CHECK lines, the auto: record. [] for any other
    plan, whose GATE.md is what it always was."""
    events = _soft_events(plan)
    if not events:
        return []
    checks = _soft_checks(plan)
    return (["", "## Thoughts", "", THOUGHTS_NOTE, ""] + [f"- {e['words'][0]['at']:.2f}s {e['line']}" for e in events]
            + ([""] + checks if checks else []) + [f"<!-- auto: thoughts={_thoughts_key([e['line'] for e in events])} -->"])


def checks(take_dir):
    """The CHECK lines of <take_dir>/plan.json that its GATE.md does not already show. GATE.md is built once, from the first plan, so what a person's
    edits or the plan made again after the gate raise (a device their [bar] displaced, a thought the legibility pass flagged) would never be seen:
    run_captions.sh prints these lines after the devices stage, from the plan the render draws, and deliver.sh files the same lines in NOTES.md. [] when
    the take has no GATE.md or no plan."""
    gate_path, plan_path = os.path.join(take_dir, "GATE.md"), os.path.join(take_dir, "plan.json")
    if not (os.path.exists(gate_path) and os.path.exists(plan_path)):
        return []
    with open(gate_path) as f:
        shown = set(f.read().splitlines())
    with open(plan_path) as f:
        plan = json.load(f)
    return [line for line in _soft_checks(plan) if line not in shown]


def _brand_style(take_dir):
    """the caption style the take's look.json was made with, or None (no style: your own look)"""
    try:
        with open(os.path.join(take_dir, "look.json")) as f:
            style = json.load(f).get("caption_style")
    except (OSError, json.JSONDecodeError):
        return None
    return style if isinstance(style, str) and style else None


def _made_soft(take_dir):
    """Whether this run makes the take in the soft build: the take's look.json says soft_build. The look stage makes it (the take's caption style laid over
    your look) before the gate is applied, so a style: line changed in the same edit is already in it."""
    try:
        with open(os.path.join(take_dir, "look.json")) as f:
            return json.load(f).get("soft_build") is True
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _soft_ignored(o, zone, ov, finale):
    """The warnings for the lines of an approved GATE.md that a soft build take does not read, one a line: hero: and no-hero: (its thoughts' payoffs play the
    hero, regrouped in the Thoughts section), front:, behind: and finale: (every thought is drawn in front of the person, under the chin), glow: (nothing
    glows) and zone: top (its thoughts sit at the chest). o is _parse_overrides' dict, zone the zone override, ov the device overrides."""
    out = [f'hero: "{w}" is ignored: {SOFT_NO_HERO}' for w in o["hero_add"]] + [f'no-hero: "{w}" is ignored: {SOFT_NO_HERO}' for w in o["hero_drop"]]
    out += [f'{key}: "{w}" is ignored: {SOFT_IN_FRONT}' for key in ("front", "behind") for w in ov.get(f"{key}_add") or ()]
    if finale:
        out.append(f'finale: "{finale["top"]}" | "{finale["bottom"]}" is ignored: {SOFT_IN_FRONT}')
    if "glow" in ov:
        out.append(f'glow: {"on" if ov["glow"] else "off"} is ignored: a soft build take has no glow')
    if zone == "top":
        out.append("zone: top is ignored: a soft build take draws its thoughts at the chest")
    return out


def build(take_dir):
    """Write <take_dir>/GATE.md. Returns the path written."""
    words_doc = _need(take_dir, "words.json")
    tier = _need(take_dir, "tier.json")
    spec = _need(take_dir, "spec.json")
    plan = _need(take_dir, "plan.json")

    brand_name = _brand_name(take_dir, spec, plan, tier)
    take_name = os.path.basename(os.path.normpath(take_dir))
    banned = _banned_hits(take_dir, plan)
    style = _brand_style(take_dir)

    tier_name = tier.get("tier", "UNKNOWN")
    tier_reason = tier.get("reason", "")
    room = tier.get("room") or {}
    zone = spec.get("zone", "unknown")
    zone_reason = spec.get("zone_reason") or tier_reason

    room_bits = [f"{k} {room[k]}px" for k in ("above", "below", "left", "right") if k in room]
    room_line = " · ".join(room_bits) if room_bits else "no room data"

    sents = sentences(words_doc.get("transcript", ""))
    heroes = hero_words(plan)

    lines = [
        "approved: no",
        "",
        f"brand: {brand_name}",
        f"take: {take_name}",
        *([f"caption style: {style} (a `style:` line under Overrides picks another)"] if style else []),
        "",
        "## Tier & zone",
        "",
        f"tier: {tier_name}",
        f"zone: {zone}",
        f"reason: {tier_reason or zone_reason or '(no reason recorded)'}",
        f"room: {room_line}",
        f"<!-- auto: tier={tier_name} zone={zone} -->",
        "",
        *_no_room_warning(plan),
        "## Transcript",
        "",
    ]
    if sents:
        lines += [f"{i}. {s}" for i, s in enumerate(sents, 1)]
    else:
        lines.append("(no transcript found in words.json)")
    lines += ["", "## Hero words", "", (SOFT_HERO_NOTE if _soft_events(plan) else
                                        "A starred word (*\"word\"*) is the hero of its cluster. Change it with `hero:` or `no-hero:` under Overrides."), ""]
    if heroes:
        for h in heroes:
            ts = h["start"]
            ts_txt = f"{ts:.2f}s" if isinstance(ts, (int, float)) else "?s"
            marks = h.get("heroes") or []
            words_txt = " / ".join((f'*"{t}"*' if (i < len(marks) and marks[i] and len(h["texts"]) > 1) else f'"{t}"')
                                   for i, t in enumerate(h["texts"]))
            lines.append(f'- {ts_txt} [{h["mode"]}] {words_txt}')
    else:
        lines.append("(no behind/front hero clusters in plan.json)")
    lines += _thoughts_section(plan)
    if banned:
        lines += ["", "## Do not say", ""]
        for word, t in banned:
            lines.append(f'WARNING: "{word}" is on your do-not-say list and a caption shows it at {t:.2f}s. '
                         f'Change the words with `fix:`, or accept it with `allow: "{word}"` under Overrides.')
    proposals = _device_proposals(take_dir, words_doc)
    lines += [
        "",
        "## Devices",
        "",
        "<!--",
        "Same convention as Overrides below: move a line out of this comment (or add a new one)",
        "to activate it, save, set `approved: yes`, run the same run_captions.sh command again. A",
        "`propose:` line is this take's own auto-detected suggestion -- accept ONE by dropping",
        '"propose: " and nothing else (editing the rest of the line first is fine). The generic',
        "examples below show the full grammar for every device, proposed or not.",
        "",
    ]
    if proposals:
        lines += [_format_propose_line(d) for d in proposals]
        lines.append("")
    else:
        lines += (["(no layout-event proposals for this take)", ""] if _devices_on(take_dir) else
                  ["(layout-event proposals are off: set devices: on in your look to get them; a line you write below still works)", ""])
    lines += [
        'page: 12.27-12.97 "the point"',
        'cta: 60.9-62.4 "LEARN MORE" "the link below"',
        "frame: 52.5-55.0 plan, build, test, ship",
        "igpost: 11.2-12.2 pixelate 24",
        'neg: "weak"',
        'no-neg: "stop"',
        'front: "what\'s going on"',
        'behind: "right now"',
        'finale: "You ask" | "we answer"',
        'strike: "the old way"',
        "pin: off",
        "count: off",
        "glow: off",
        "-->",
    ]
    lines += [
        "",
        "## Pacing",
        "",
    ]
    lines += _format_pace_lines(plan)
    lines += [
        "",
        "<!--",
        "Same convention as Devices/Overrides above: move a line out of this comment (or add a new",
        "one) to activate it, save, set `approved: yes`, run the same run_captions.sh command again.",
        "The `pace:` lines above this comment are cadence.py's own report (numbers + any stretch",
        "still over the limit after auto-punching) -- report-only, never parsed as a directive.",
        "",
        "punch: 12.4",
        "no-punch: 12.4",
        "punch: off",
        "logo: off",
        "icon: off",
        "-->",
    ]
    lines += [
        "",
        "## Overrides",
        "",
        "<!--",
        "Edit this take like a script. Everything in this comment is inert. To change something,",
        "move that line OUT of the comment (or add a new line after it), save this file, set",
        "`approved: yes` at the top, then run the same run_captions.sh command again.",
        "",
        "zone: chest",
        "tier: FULL",
        'fix: "we aint" -> "we are not"',
        'hero: "event"',
        'no-hero: "gonna"',
        'allow: "<a do-not-say word>"',
        "subject: none",
        "style: keyword-slam",
        "note: reshot the intro, keep the long pause",
        "-->",
        "",
    ]
    out_path = os.path.join(take_dir, "GATE.md")
    if os.path.exists(out_path):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(out_path, f"{out_path}.bak-{stamp}")
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"GATE.md written: {out_path}")
    print("Open it, edit like a script, set 'approved: yes', save, then run apply.")
    return out_path


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _active_lines(section_text):
    """Yield the text of `section_text` with any <!-- ... --> comment spans removed, so an
    unedited example stays inert and only a line the operator actually moved out of the comment
    (or added below it) is ever parsed as a directive."""
    in_comment = False
    active = []
    for raw in section_text.splitlines():
        line = raw
        while True:
            if not in_comment:
                idx = line.find("<!--")
                if idx == -1:
                    if line.strip():
                        active.append(line)
                    break
                before = line[:idx]
                if before.strip():
                    active.append(before)
                line = line[idx + 4:]
                in_comment = True
            else:
                idx = line.find("-->")
                if idx == -1:
                    break
                line = line[idx + 3:]
                in_comment = False
    return active


def _section(text, heading_re):
    """The text of one "## ..." section, up to the next heading ("" when the heading is missing)."""
    m = heading_re.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    return rest[:nxt.start()] if nxt else rest


def _parse_overrides(text):
    """Return a dict: zone, tier, fixes[(from,to)], hero_add[], hero_drop[], allow[], subject, style, notes, warnings[].
    Every active line of the Overrides block is either a directive or a warning: nothing is silently ignored."""
    m = OVERRIDES_HEADING_RE.search(text)
    section = text[m.end():] if m else ""
    o = {"zone": None, "tier": None, "fixes": [], "hero_add": [], "hero_drop": [], "allow": [], "subject": None,
         "style": None, "notes": "", "warnings": []}
    note_lines = []
    for line in _active_lines(section):
        fm = FIX_RE.match(line)
        if fm:
            o["fixes"].append((fm.group(1), fm.group(2)))
        elif NOHERO_RE.match(line):
            o["hero_drop"].append(NOHERO_RE.match(line).group(1).strip().strip('"'))
        elif HERO_RE.match(line):
            o["hero_add"].append(HERO_RE.match(line).group(1).strip().strip('"'))
        elif ALLOW_RE.match(line):
            o["allow"].append(ALLOW_RE.match(line).group(1).strip().strip('"'))
        elif SUBJECT_RE.match(line):
            o["subject"] = SUBJECT_RE.match(line).group(1).lower()
        elif STYLE_RE.match(line):
            val = STYLE_RE.match(line).group(1)
            if not STYLE_ID_RE.match(val):
                raise EngineStop(f'style override "{val}" is not a caption style id: lower case words joined by hyphens, like keyword-slam (python3 styles.py list)')
            o["style"] = val
        elif ZONE_RE.match(line):
            val = ZONE_RE.match(line).group(1).strip().lower()
            if val not in VALID_ZONES:
                raise EngineStop(f'zone override "{val}" is not valid: use "top" or "chest"')
            o["zone"] = val
        elif TIER_RE.match(line):
            val = TIER_RE.match(line).group(1).strip().upper()
            if val not in VALID_TIERS:
                raise EngineStop(f'tier override "{val}" is not valid: use MINIMAL, REDUCED, or FULL')
            o["tier"] = val
        elif NOTE_RE.match(line):
            note_lines.append(NOTE_RE.match(line).group(1).strip())
        else:
            o["warnings"].append(f'Overrides line not understood, ignored: {line.strip()}'
                                 + (' (curly quotes? fix lines need straight quotes: fix: "from" -> "to")' if re.match(r'^\s*fix\s*:', line, re.I) else ''))
    o["notes"] = "\n".join(note_lines).strip()
    return o


def _parse_tier_zone(text):
    """(tier, zone) the operator left in the Tier & zone section when they differ from the values GATE.md was built
    with (the `auto:` comment); None for a value that was not edited, or when the file has no auto record."""
    am = AUTO_RE.search(text)
    if not am:
        return None, None
    auto_tier, auto_zone = am.group(1).upper(), am.group(2).lower()
    tier = zone = None
    for line in _active_lines(_section(text, TIERZONE_HEADING_RE)):
        tm, zm = TIER_RE.match(line), ZONE_RE.match(line)
        if tm:
            val = tm.group(1).strip().upper()
            if val not in VALID_TIERS:
                raise EngineStop(f'tier "{val}" in the Tier & zone section is not valid: use MINIMAL, REDUCED, or FULL')
            tier = val if val != auto_tier else None
        if zm:
            val = zm.group(1).strip().lower()
            if val not in VALID_ZONES:
                raise EngineStop(f'zone "{val}" in the Tier & zone section is not valid: use "top" or "chest"')
            zone = val if val != auto_zone else None
    return tier, zone


def _parse_devices(text):
    """Return (layout_events[], device_overrides{}) from the "## Devices" section's ACTIVE lines
    (the same <!-- --> inert-comment convention _active_lines()/_parse_overrides() already use).
    layout_events are already shaped for devices.py apply()'s `devices` argument; device_overrides
    is written into gate.json (spec_adapter.py puts it in spec.json) for cluster_plan.py's own device_overrides gate (the
    neg_add/neg_off/strike_add lists, pin/count/glow booleans)."""
    m = DEVICES_HEADING_RE.search(text)
    if not m:
        return [], {}
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    section = rest[:nxt.start()] if nxt else rest
    layout = []
    ov = {}
    for line in _active_lines(section):
        pm = PAGE_LINE_RE.match(line)
        if pm:
            layout.append({"mode": "page", "start": float(pm.group(1)), "end": float(pm.group(2)),
                           "text": pm.group(3)})
            continue
        cm = CTA_LINE_RE.match(line)
        if cm:
            rows = [{"text": cm.group(3), "size": CTA_ROW0_SIZE, "weight": CTA_ROW0_WEIGHT}]
            if cm.group(4):
                rows.append({"text": cm.group(4), "size": CTA_ROW1_SIZE, "weight": CTA_ROW1_WEIGHT})
            layout.append({"mode": "cta", "start": float(cm.group(1)), "end": float(cm.group(2)),
                           "rows": rows})
            continue
        im = IGPOST_LINE_RE.match(line)
        if im:
            d = {"mode": "igpost", "start": float(im.group(1)), "end": float(im.group(2))}
            if im.group(3):
                d["pixelate"] = int(im.group(3))
            layout.append(d)
            continue
        fm = FRAME_LINE_RE.match(line)
        if fm:
            items = [x.strip() for x in fm.group(3).split(",") if x.strip()]
            if items:
                start, end = float(fm.group(1)), float(fm.group(2))
                step = (end - start) / len(items)
                lst = [{"text": it, "at": round(start + i * step, 3)} for i, it in enumerate(items)]
                layout.append({"mode": "frame", "start": start, "end": end, "list": lst})
            continue
        nm = NEG_LINE_RE.match(line)
        if nm:
            ov.setdefault("neg_add", []).append(nm.group(1))
            continue
        nnm = NONEG_LINE_RE.match(line)
        if nnm:
            ov.setdefault("neg_off", []).append(nnm.group(1))
            continue
        fm2 = FRONT_LINE_RE.match(line)
        if fm2:
            ov.setdefault("front_add", []).append(fm2.group(1))
            continue
        bm2 = BEHIND_LINE_RE.match(line)
        if bm2:
            ov.setdefault("behind_add", []).append(bm2.group(1))
            continue
        sm = STRIKE_LINE_RE.match(line)
        if sm:
            ov.setdefault("strike_add", []).append(sm.group(1))
            continue
        pinm = PIN_LINE_RE.match(line)
        if pinm:
            ov["pin"] = pinm.group(1).lower() == "on"
            continue
        cntm = COUNT_LINE_RE.match(line)
        if cntm:
            ov["count"] = cntm.group(1).lower() == "on"
            continue
        glm = GLOW_LINE_RE.match(line)
        if glm:
            ov["glow"] = glm.group(1).lower() == "on"
            continue
        # an unrecognized line here is just noise -- the Devices section has no free-text notes
        # field the way Overrides does, so (unlike _parse_overrides) nothing collects it.
    return layout, ov


def _parse_pacing(text):
    """Return a device_overrides{} dict from the "## Pacing" section's ACTIVE lines (same
    <!-- --> inert-comment convention as _parse_devices). Lands in the SAME
    device_overrides dict _parse_devices() writes to -- cadence.py owns `punch`/`punch_add`/
    `punch_off`, cluster_plan.py owns `logo`/`icon`. The report-only `pace: ...` lines this
    section also carries never match any of these regexes, so they are silently skipped here
    exactly like an unrecognized Devices line is."""
    m = PACING_HEADING_RE.search(text)
    if not m:
        return {}
    rest = text[m.end():]
    nxt = NEXT_HEADING_RE.search(rest)
    section = rest[:nxt.start()] if nxt else rest
    ov = {}
    for line in _active_lines(section):
        pm = PUNCH_LINE_RE.match(line)
        if pm:
            val = pm.group(1)
            if val.lower() == "off":
                ov["punch"] = False
            else:
                ov.setdefault("punch_add", []).append(float(val))
            continue
        nm = NOPUNCH_LINE_RE.match(line)
        if nm:
            ov.setdefault("punch_off", []).append(float(nm.group(1)))
            continue
        lm = PACE_LOGO_LINE_RE.match(line)
        if lm:
            ov["logo"] = lm.group(1).lower() == "on"
            continue
        im = PACE_ICON_LINE_RE.match(line)
        if im:
            ov["icon"] = im.group(1).lower() == "on"
            continue
        # a pace: ... report line (numbers or a quiet stretch), or unrecognized -- ignored.
    return ov


def _parse_thoughts(text):
    """The "## Thoughts" section's thought lines without their "- " and their time, when a person edited them; None when GATE.md has no such section,
    when it is as build wrote it (its auto: record says so), or when it has no active line, emptied or commented out: the planner then splits the words
    itself, as the feed does with an empty reel.json "thoughts"."""
    if not THOUGHTS_HEADING_RE.search(text):
        return None
    section = _section(text, THOUGHTS_HEADING_RE)
    lines = [m.group(1) for m in (THOUGHT_LINE_RE.match(line) for line in _active_lines(section)) if m]
    auto = THOUGHTS_AUTO_RE.search(section)
    return None if not lines or auto and auto.group(1) == _thoughts_key(lines) else lines


def _normalize(text):
    return re.sub(r"[^\w\s']", "", text or "").lower().strip()


def apply_fix(words, from_phrase, to_phrase):
    """Rewrite the first matching contiguous run of `words[i]["text"]` for a verbatim (punctuation-
    and case-insensitive) match of from_phrase, with to_phrase. Equal word counts are a pure text
    swap — every timestamp untouched. A different word count splices in new word entries spanning
    the original run's [start, end] evenly, so a phrase-length fix never desyncs the rest of the
    take. Returns True if a match was found and applied, False otherwise (caller warns, never
    crashes — a missed fix is a plain warning, not a fatal error)."""
    from_words = from_phrase.split()
    to_words = to_phrase.split()
    n = len(from_words)
    if n == 0:
        return False
    target = [_normalize(w) for w in from_words]
    for i in range(len(words) - n + 1):
        window = words[i:i + n]
        if [_normalize(w.get("text", "")) for w in window] == target:
            if len(to_words) == n:
                for w, new_text in zip(window, to_words):
                    w["text"] = new_text
            else:
                start = window[0].get("start", 0.0)
                end = window[-1].get("end", start)
                span = max(end - start, 0.0)
                count = len(to_words)
                step = span / count if count else 0.0
                spliced = [
                    {
                        "text": nt,
                        "start": round(start + step * j, 3),
                        "end": round(start + step * (j + 1), 3),
                        "filler": False,
                    }
                    for j, nt in enumerate(to_words)
                ]
                words[i:i + n] = spliced
            return True
    return False


def apply_gate(take_dir):
    """Parse and act on <take_dir>/GATE.md. Writes gate.json (and devices.json, and words.json when there are fixes).
    Exits 3 (via sys.exit, which propagates cleanly through keys.cli's except clauses) with a plain sentence if the
    take is not approved: never a traceback for that, the single most common operator mistake (forgetting to flip
    the header line before re-running).

    Idempotent: fixes are applied to words.json.pre-gate's text (the transcript before any fix), and devices.json is
    rewritten (or removed) from the Devices section every time, so a line deleted from GATE.md stops having an effect."""
    gate_md_path = os.path.join(take_dir, "GATE.md")
    if not os.path.exists(gate_md_path):
        raise EngineStop(f"no GATE.md in {take_dir}: run `capgate.py build {take_dir}` first")
    with open(gate_md_path) as f:
        text = f.read()

    m = APPROVED_RE.search(text)
    if not m or m.group(1).lower() != "yes":
        print(
            f"{gate_md_path} is not approved yet. Open it, review the take, set "
            f"'approved: yes' at the very top, save, then run the same run_captions.sh command again (or `capgate.py apply {take_dir}`)."
        )
        sys.exit(3)

    o = _parse_overrides(text)
    sec_tier, sec_zone = _parse_tier_zone(text)
    zone = o["zone"] or sec_zone
    tier = o["tier"] or sec_tier
    layout_events, device_overrides = _parse_devices(text)
    pacing_overrides = _parse_pacing(text)
    if pacing_overrides:
        device_overrides = {**device_overrides, **pacing_overrides}
    if o["hero_add"]:
        device_overrides["hero_add"] = o["hero_add"]
    if o["hero_drop"]:
        device_overrides["hero_drop"] = o["hero_drop"]
    thought_lines = _parse_thoughts(text)
    if thought_lines is not None:
        device_overrides["thoughts"] = thought_lines
    finale = None
    for line in _active_lines(text):
        fm = FINALE_RE.match(line)
        if fm:
            finale = {"top": fm.group(1), "bottom": fm.group(2)}

    applied_fixes = []
    warnings = list(o["warnings"]) + (_soft_ignored(o, zone, device_overrides, finale) if _made_soft(take_dir) else [])
    words_path = os.path.join(take_dir, "words.json")
    backup_path = words_path + ".pre-gate"
    if o["fixes"] or os.path.exists(backup_path):
        words_doc = _need(take_dir, "words.json")
        if os.path.exists(backup_path):   # start again from the transcript as it was before any fix
            with open(backup_path) as f:
                words_doc = json.load(f)
        for frm, to in o["fixes"]:
            if apply_fix(words_doc.get("words", []), frm, to):
                applied_fixes.append({"from": frm, "to": to})
            else:
                warnings.append(f'fix: "{frm}" -> "{to}" not found verbatim, skipped')
        if applied_fixes and not os.path.exists(backup_path):
            shutil.copyfile(words_path, backup_path)
        if applied_fixes or os.path.exists(backup_path):
            with open(words_path, "w") as f:
                json.dump(words_doc, f, indent=1)

    devices_json_path = os.path.join(take_dir, "devices.json")
    if layout_events:
        with open(devices_json_path, "w") as f:
            json.dump(layout_events, f, indent=1)
    elif os.path.exists(devices_json_path):
        os.remove(devices_json_path)

    gate = {
        "approved": True,
        "overrides": {
            "zone": zone,
            "tier": tier,
            "hero_add": o["hero_add"],
            "hero_drop": o["hero_drop"],
            "fixes": applied_fixes,
            "allow": o["allow"],
            "subject": o["subject"],
            "devices": {"layout_events": layout_events, "device_overrides": device_overrides},
            "finale": finale,
        },
        "notes": o["notes"],
    }
    if o["style"]:
        gate["overrides"]["style"] = o["style"]     # only when asked for: a gate with no style line writes the gate.json it always did
    if warnings:
        gate["warnings"] = warnings
    gate_json_path = os.path.join(take_dir, "gate.json")
    with open(gate_json_path, "w") as f:
        json.dump(gate, f, indent=1)

    for w in warnings:
        print(f"warning: {w}")
    summary = [f"gate.json written: {gate_json_path}"]
    if zone:
        summary.append(f"zone -> {zone}")
    if tier:
        summary.append(f"tier -> {tier}")
    if applied_fixes:
        summary.append(f"{len(applied_fixes)} fix(es) applied")
    if layout_events:
        summary.append(f"{len(layout_events)} device(s) accepted -> {devices_json_path}")
    if device_overrides:
        summary.append(f"device_overrides -> {sorted(device_overrides)}")
    if o["subject"]:
        summary.append(f"subject -> {o['subject']}")
    if o["style"]:
        summary.append(f"style -> {o['style']}")
    print("; ".join(summary))
    return gate_json_path


def style_of(take_dir):
    """The caption style the take's GATE.md asks for with an active `style:` line (an id, never checked against the library here), or None when there is no GATE.md or no
    such line. Read straight from the file, so it works before the gate is approved: run_captions.sh makes look.json before it applies the gate."""
    path = os.path.join(take_dir, "GATE.md")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return _parse_overrides(f.read())["style"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main():
    if len(sys.argv) < 3:
        raise ValueError("need a subcommand and a take-dir")
    sub, take_dir = sys.argv[1], sys.argv[2]
    if sub == "build":
        build(take_dir)
    elif sub == "apply":
        apply_gate(take_dir)
    elif sub == "style":
        print(style_of(take_dir) or "")
    elif sub == "checks":
        for line in checks(take_dir):
            print(line)
    else:
        raise ValueError(f"unknown subcommand {sub!r}")


if __name__ == "__main__":
    cli(_main, "capgate.py build|apply|style|checks <take-dir>")
