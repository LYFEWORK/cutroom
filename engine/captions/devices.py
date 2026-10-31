#!/usr/bin/env python3
"""devices.py propose|apply — layout-event (cta/page/frame/igpost) detection and merging.
the README, "Devices" and 4. Word-DEVICE tagging (glow/neg/count/
strike/pin/spaced) lives in cluster_plan.py — this file is only the four LAYOUT EVENTS, which take
over the whole frame for a beat and are never auto-applied.

    devices.py propose <words.json> <out.json>
        Scans the transcript for the four trigger patterns and writes the list of
        PROPOSED layout events to <out.json>. Nothing here is ever applied automatically —
        capgate.py renders each proposal into GATE.md as a commented-out `propose: <mode>
        <start>-<end> ...` line; a person accepts one by moving it out of the comment and
        rewriting the leading word as the mode itself (`page:` / `cta:` / `frame:` / `igpost:`),
        editing the rest of the line freely first if they want to. capgate.py's `apply` then
        collects every accepted line into devices.json.

    devices.py apply <plan.json> <devices.json> <facetrack.json> <out.plan.json> [<spec.json>]
        Merges the ACCEPTED layout events in devices.json into plan.json's events list: clamps or
        drops any front/behind cluster the layout event covers (a "clamp":
        a cta REPLACES the cluster it covers exactly the same way page/frame/igpost drop one, the
        distinction is about narrative role, not mechanics), computes the frame
        card's `y` (lowered when the default 640 would clip the face) and the igpost crop center
        from facetrack, and writes the result — with plan["devicesApplied"] = True, run_captions.sh's
        own resume marker for this stage — to <out.plan.json>. <plan.json> and <out.plan.json> may
        be the same path (read-then-write-same-file, the pattern normalize_words.py already uses). With <spec.json>, the
        brand's icon_words (first word of the pill's text that is listed) and then its pill_icons (rows in order) choose the
        icon of a frame pill before the engine's own brand-neutral word table does.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster_plan import tok, clean, STOP, WEAK_HERO, soft_look
import facetrack
from keys import cli

FPS = 30
MIN_CLUSTER_FRAMES = 3     # reel_qc.MIN_EVENT_FRAMES: a caption shorter than this is a flash

# ---------------------------------------------------------------------------------------------
# propose() — the four trigger patterns
# ---------------------------------------------------------------------------------------------

# (trigger phrase, tok()'d) -> how many of ITS OWN words become the cta's caps row 0; everything
# after, up to the next real pause/sentence end, becomes row 1 (lowercase, as spoken).
CTA_TRIGGERS = [
    (("fill", "out", "the", "form"), 2),   # FILL OUT / the form ...
    (("link", "in", "bio"), 1),             # LINK / in bio
    (("link", "below"), 1),                 # LINK / below
    (("dm", "us"), 2),                      # DM US / ...
    (("dm", "me"), 2),                      # DM ME / ...
    (("book", "a", "call"), 3),             # BOOK A CALL / ...
    (("click", "below"), 1),                # CLICK / below
]
IGPOST_PHRASES = [("a", "video"), ("my", "video")]
IGPOST_WORDS = {"post", "posting", "posted", "content"}
PAGE_LEADINS = [("is", "not"), ("isn't",), ("that's", "not"), ("that's",), ("it's", "called")]
# spoken filler: a list made of these ("well, I mean, you know") is not a spoken list, and none of them is a page word
FILLER_TOKENS = {"well", "i", "mean", "you", "know", "so", "like", "okay", "yeah", "right", "um", "uh", "just", "actually", "basically", "literally"}


def _page_word_ok(token):
    """A word worth a full-page slam: a content word (not a stop, weak or filler word, longer than three letters)."""
    return len(token) > 3 and token not in STOP and token not in WEAK_HERO and token not in FILLER_TOKENS and "'" not in token
FRAME_MAX_SPAN = 4.0     # "3+ short items ... within 4s"
FRAME_MAX_ITEM_WORDS = 3


def _find(tokens, phrase, start=0):
    n = len(phrase)
    for i in range(start, len(tokens) - n + 1):
        if tuple(tokens[i:i + n]) == phrase:
            return i
    return -1


def _extend_clause(words, i0, i1, max_extra=6, gap=0.28):
    """Grow the captured span [i0, i1) forward, word by word, stopping at sentence-ending
    punctuation, a real pause, or after `max_extra` more words — a trigger match alone
    ("fill out the form") doesn't include the imperative phrase's tail ("... below")."""
    j = i1
    added = 0
    while j < len(words) and added < max_extra:
        prev = words[j - 1]
        if re.search(r"[.?!]$", prev["text"]):
            break
        if words[j]["start"] - prev["end"] >= gap:
            break
        j += 1
        added += 1
    return j


def _propose_cta(words, tokens, used):
    out = []
    for phrase, split_n in CTA_TRIGGERS:
        start_search = 0
        while True:
            i = _find(tokens, phrase, start_search)
            if i == -1:
                break
            start_search = i + 1
            if any(k in used for k in range(i, i + len(phrase))):
                continue
            j = _extend_clause(words, i, i + len(phrase))
            row0 = " ".join(clean(w["text"]) for w in words[i:i + split_n]).upper()
            row1_words = words[i + split_n:j]
            row1 = " ".join(clean(w["text"]) for w in row1_words).lower()
            used.update(range(i, j))
            rows = [{"text": row0}]
            if row1:
                rows.append({"text": row1})
            out.append({"mode": "cta", "start": round(words[i]["start"], 3),
                        "end": round(words[j - 1]["end"] + 0.3, 3), "rows": rows})
    return out


def _propose_page(words, tokens, used):
    out = []
    for phrase in PAGE_LEADINS:
        start_search = 0
        while True:
            i = _find(tokens, phrase, start_search)
            if i == -1:
                break
            start_search = i + 1
            wi = i + len(phrase)
            if wi >= len(words) or wi in used or any(k in used for k in range(i, wi)):
                continue
            text = clean(words[wi]["text"]).lower()
            if not _page_word_ok(tok(text)):
                continue
            start = round(words[wi]["start"], 3)
            out.append({"mode": "page", "start": start, "end": round(start + 0.7, 3), "text": text})
            used.add(wi)
    return out


def _propose_igpost(words, tokens, used):
    out = []
    i = 0
    while i < len(words):
        if i in used:
            i += 1
            continue
        hit = tokens[i] in IGPOST_WORDS or any(
            i + len(ph) <= len(tokens) and tuple(tokens[i:i + len(ph)]) == ph for ph in IGPOST_PHRASES
        )
        if hit:
            start = round(words[i]["start"], 3)
            out.append({"mode": "igpost", "start": start, "end": round(start + 1.0, 3)})
            used.add(i)
        i += 1
    return out


def _split_list_items(words, start_i, max_span=FRAME_MAX_SPAN, max_item_words=FRAME_MAX_ITEM_WORDS):
    """Try to parse a spoken list starting at `start_i`: items separated by a trailing comma,
    a standalone 'and'/'or', or a trailing '?'/'.'/'!' (which also ends the scan). Returns
    (items, end_index) — items is a list of word-lists (each one "item"); end_index is one past
    the last word consumed. An item over `max_item_words` is not "short" and ends the scan without
    being counted (the caller then sees fewer than 3 items and moves on)."""
    items, cur, j = [], [], start_i
    while j < len(words):
        w = words[j]
        if w["start"] - words[start_i]["start"] > max_span:
            break
        raw = w["text"]
        if tok(clean(raw)) in ("and", "or"):
            if cur:
                items.append(cur)
                cur = []
            j += 1
            continue
        cur.append(w)
        j += 1
        if len(cur) > max_item_words:
            cur = []   # too long to be a "short" list item -- discard, do not count it
            break
        if re.search(r"[,.!?]$", raw):
            items.append(cur)
            cur = []
            if re.search(r"[.!]$", raw):
                break
    if cur:
        items.append(cur)
    return items, j


def _propose_frame(words, tokens, used):
    out = []
    i, n = 0, len(words)
    while i < n:
        if i in used:
            i += 1
            continue
        items, j = _split_list_items(words, i)
        items = [it for it in items if it]
        real = [it for it in items if not all(tok(clean(w["text"])) in FILLER_TOKENS or tok(clean(w["text"])) in STOP for w in it)]
        if len(items) >= 3 and len(real) >= 3 and (items[-1][-1]["end"] - items[0][0]["start"]) <= FRAME_MAX_SPAN:
            start = round(items[0][0]["start"], 3)
            end = round(items[-1][-1]["end"] + 0.3, 3)
            texts = [" ".join(clean(w["text"]) for w in it).lower() for it in items]
            out.append({"mode": "frame", "start": start, "end": end,
                        "list": [{"text": t} for t in texts]})
            used.update(range(i, j))
            i = j
        else:
            i += 1
    return out


def propose(words):
    """words: the take's words.json["words"] list (raw, un-normalized is fine — only tok()'d
    comparisons and trailing punctuation matter here)."""
    words = [w for w in (words or []) if not w.get("filler")]
    tokens = [tok(w["text"]) for w in words]
    used = set()
    out = []
    out += _propose_cta(words, tokens, used)
    out += _propose_page(words, tokens, used)
    out += _propose_igpost(words, tokens, used)
    out += _propose_frame(words, tokens, used)
    out.sort(key=lambda e: e["start"])
    return out


# ---------------------------------------------------------------------------------------------
# apply() — merge accepted layout events into plan.json
# ---------------------------------------------------------------------------------------------

DEVICE_MODES = ("cta", "page", "frame", "igpost")


# the engine's own word table for a frame pill's icon, the last thing asked: it applies when neither the brand's icon_words nor its
# pill_icons name any word of the item. The first row whose whole word (never a fragment of one) appears in the item's text wins.
# Plain words only: the agency words (ad, video, content, post, feed, track, lead, report) are a look's own vocabulary (its
# pill_icons), so a pill for "ads" is the plain check unless your look lists it.
ICON_WORDS = [(("call", "calls", "calling"), "phone"), (("phone", "phones"), "phone"), (("text",), "phone"),
              (("dm", "dms"), "chat"), (("message", "messages", "messaging"), "chat"),
              (("respond", "responds", "response", "responses", "responding"), "chat"),
              (("calendar",), "calendar"), (("book", "books", "booked", "booking", "bookings"), "calendar"),
              (("schedule", "schedules", "scheduled", "scheduling"), "calendar"), (("appointment",), "calendar"),
              (("show", "shows"), "calendar"),
              (("pay", "pays", "paid", "payment", "payments"), "dollar"), (("price", "prices", "pricing"), "dollar"),
              (("cost",), "dollar"), (("shop", "shops"), "dollar"), (("money",), "dollar"),
              (("search",), "search"), (("find",), "search"),
              (("fit", "fits"), "check"), (("right",), "check"),
              (("place", "places"), "pin"), (("local",), "pin"),
              (("best",), "star"), (("review", "reviews"), "star"), (("rating",), "star")]
def brand_icons(spec):
    """{token: icon} from a spec's icon_words ("word:icon" strings); a malformed entry is skipped."""
    out = {}
    for item in (spec or {}).get("icon_words") or []:
        word, sep, icon = str(item).partition(":")
        if sep and tok(word) and icon.strip():
            out[tok(word)] = icon.strip()
    return out


def pill_rows(spec):
    """[(words, icon)] in order from a spec's pill_icons ("word,word,word:icon" strings, one row each, the same shape as ICON_WORDS); a
    malformed entry is skipped. Unlike icon_words (matched by the first listed word of the pill's text), the first ROW with any word in
    the text wins, so the order of the rows is the priority."""
    out = []
    for item in (spec or {}).get("pill_icons") or []:
        words, sep, icon = str(item).rpartition(":")
        row = tuple(t for t in (tok(w) for w in words.split(",")) if t)
        if sep and row and icon.strip():
            out.append((row, icon.strip()))
    return out


def _icon_for(text, brand=None, rows=()):
    """The pill's icon: a "$" is a dollar; else the first word of the text the brand lists in icon_words; else the first row of the brand's
    pill_icons, then of the engine's own table, that has a word in the text; else the plain check."""
    t = text.lower()
    if "$" in t:
        return "dollar"
    ordered = re.findall(r"[a-z']+", t)
    for w in ordered:
        if brand and w in brand:
            return brand[w]
    toks = set(ordered)
    for words, ic in list(rows) + ICON_WORDS:
        if toks & set(words): return ic
    return "check"

def _dropped_words(events, dstart, dend):
    """The running words a layout event swallows (rows of clusters that start inside its span),
    flattened to {text, at} in onset order — what an igpost draws inside its image, and what a
    frame's list items are timed against."""
    out = []
    for e in events:
        if e.get("mode") in ("front", "behind") and dstart <= e["start"] < dend:
            for r in e.get("words") or []:
                out.append({"text": r.get("text", ""), "at": r.get("at", e["start"])})   # test fixtures carry bare rows
    return sorted(out, key=lambda w: w["at"])

def _from(row, start):
    """A row of a cluster that starts again at `start`: its at no earlier than that, and a soft line's every word time (ats) as well, so the words
    spoken under a layout event arrive together, softly, at the new start, and at is still its first word's."""
    if "ats" not in row:
        return dict(row, at=max(row.get("at", start), start))
    ats = [max(a, start) for a in row["ats"]]
    return dict(row, ats=ats, at=ats[0])


def _clamp_events(events, dstart, dend, fps=FPS):
    """Clamp: a front/behind cluster overlapping a layout event ends 1 frame before
    it; one starting inside the event's span is dropped outright: its words are in the event.
    A soft build thought that is shortened leaves by its new end: its exitAt comes forward to the
    end less the soft build's leave (LEAVE_S), so it fades out instead of vanishing on the cut."""
    out = []
    for e in events:
        if e.get("mode") not in ("front", "behind"):
            out.append(e)
            continue
        if dstart <= e["start"] < dend:
            # a cluster that begins inside the event but keeps talking well past it is not "in the
            # event" -- it resumes the moment the event ends (rows already spoken land together);
            # only a cluster wholly inside the span is dropped.
            if e["end"] > dend + 0.25:
                e = dict(e); e["start"] = round(dend + 1.0 / fps, 3)
                e["words"] = [_from(w, e["start"]) for w in (e.get("words") or [])]
                out.append(e)
            continue
        if e["start"] < dstart < e["end"]:
            e = dict(e)
            e["end"] = round(dstart - 1.0 / fps, 3)
            if e["end"] - e["start"] < MIN_CLUSTER_FRAMES / fps - 1e-6:
                continue            # what is left is a flash, not a caption: QC would fail it as too short
            if e.get("soft"):
                e["exitAt"] = round(min(e["exitAt"], e["end"] - soft_look.LEAVE_S), 3)
        out.append(e)
    return out


def _frame_card(d, track):
    """card.y defaults to 640; the planner may LOWER it (a smaller y = higher up the
    frame = more of the video kept above the card) just enough to keep the face's own top edge
    inside the card, never below the default."""
    card = dict(d.get("card") or {})
    card.setdefault("x", 0)
    card.setdefault("w", 1080)
    card.setdefault("h", 1280)
    card.setdefault("radius", 40)
    y = card.get("y", 640)
    face = facetrack.at(track, (d["start"] + d["end"]) / 2) if track else None
    if face:
        face_top = face["cy"] - face["h"] / 2
        if face_top < y:
            y = max(0, int(face_top - 40))
    card["y"] = int(y)
    return card


def _cta_fit(d, track, safe_y0=270, safe_y1=1420):
    """(y, scale) for the CTA block so its rows never sit on the face (the reference's talking head is
    small and low-third; ours are not always). Block = row0 190 + row1 74 (x1.05 line-height) +
    chevrons 36 + 80. Below the chin if that fits inside the safe box, else above the crown; in either
    lane the rows scale DOWN (floor 0.6) to fit the room. Contract default (700, 1.0) with no track."""
    ROWS, CHEV = 190 * 1.05 + 74 * 1.05, 36 + 80
    if not track:
        return 700, 1.0
    cs = [facetrack.at(track, t) for t in (d["start"], (d["start"] + d["end"]) / 2, d["end"])]
    chin = max(c["cy"] + c["h"] / 2 for c in cs)
    crown = min(c["cy"] - c["h"] * 0.8 for c in cs)
    below = safe_y1 - (chin + 60)
    above = crown - 24 - safe_y0
    for room, y_of in ((below, lambda blk: chin + 60), (above, lambda blk: crown - 24 - blk)):
        k = min(1.0, (room - CHEV) / ROWS)
        if k >= 0.6:
            return int(y_of(ROWS * k + CHEV)), round(k, 3)
    return 700, 1.0


def _igpost_crop_cx(d, track):
    face = facetrack.at(track, (d["start"] + d["end"]) / 2) if track else None
    return int(face["cx"]) if face else 540


def _build_device_event(d, track):
    mode = d.get("mode")
    if mode == "page":
        ev = {"mode": "page", "start": d["start"], "end": d["end"], "text": d.get("text", "")}
        for k in ("color", "ink", "font", "size", "slide", "weight"):
            if d.get(k) is not None:
                ev[k] = d[k]
        return ev
    if mode == "cta":
        ev = {"mode": "cta", "start": d["start"], "end": d["end"], "rows": d.get("rows", [])}
        for k in ("chevrons", "wash", "y"):
            if d.get(k) is not None:
                ev[k] = d[k]
        if ev.get("y") is None:
            y, k = _cta_fit(d, track)
            ev["y"] = y
            if k < 1.0:
                ev["rows"] = [dict(r, size=int(r.get("size", 190 if i == 0 else 74) * k)) for i, r in enumerate(ev["rows"])]
        return ev
    if mode == "frame":
        ev = {"mode": "frame", "start": d["start"], "end": d["end"],
              "card": _frame_card(d, track), "list": d.get("list", [])}
        if d.get("pill") is not None:
            ev["pill"] = d["pill"]
        return ev
    if mode == "igpost":
        ev = {"mode": "igpost", "start": d["start"], "end": d["end"],
              "pixelate": d.get("pixelate", 0), "cropCx": _igpost_crop_cx(d, track)}
        if d.get("rows") is not None:
            ev["rows"] = d["rows"]
        return ev
    raise ValueError(f"devices.py apply: unknown device mode {mode!r} (want one of {DEVICE_MODES})")


def apply(plan, devices, track, spec=None):
    """Returns a NEW plan dict (does not mutate `plan` in place) with the accepted layout events
    merged in and plan["matte"] left untouched (the matte windows do not cover them)."""
    events = list(plan.get("events") or [])
    icons, rows = brand_icons(spec), pill_rows(spec)
    device_events = []
    for d in devices or []:
        if d.get("mode") not in DEVICE_MODES:
            raise ValueError(f"devices.py apply: unknown device mode {d.get('mode')!r} (want one of {DEVICE_MODES})")
        swallowed = _dropped_words(events, d["start"], d["end"])
        events = _clamp_events(events, d["start"], d["end"])
        d = dict(d)
        if d["mode"] == "igpost" and d.get("rows") is None and swallowed:
            d["rows"] = swallowed                      # the words spoken over the mock stay on screen, inside the post
        if d["mode"] == "frame":
            # a GATE.md frame line carries no times/icons: time each pill to the first swallowed row
            # whose text contains the item's first word, fall back to the even spacing capgate wrote;
            # pick an icon from the item's own words.
            lst = []
            for it in d.get("list") or []:
                it = dict(it); head = it["text"].split()[0].lower().strip("?,.!")
                hit = next((w for w in swallowed if head and head in w["text"].lower()), None)
                if hit: it["at"] = hit["at"]
                it.setdefault("icon", _icon_for(it["text"], icons, rows))
                lst.append(it)
            d["list"] = lst
        device_events.append(_build_device_event(d, track))
    events += device_events
    events.sort(key=lambda e: e["start"])
    out = dict(plan)
    if out.get("displayWeight"):   # the brand's display weight is the page word's weight unless the line set its own
        for ev in device_events:
            if ev.get("mode") == "page":
                ev.setdefault("weight", out["displayWeight"])
    out["events"] = events
    out["devicesApplied"] = True
    return out


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def _main():
    a = sys.argv[1:]
    if len(a) < 1:
        raise ValueError("need a subcommand")
    sub = a[0]
    if sub == "propose":
        words_p, out_p = a[1], a[2]
        words = json.load(open(words_p)).get("words", [])
        result = propose(words)
        json.dump(result, open(out_p, "w"), indent=1)
        print(f"{len(result)} device proposal(s)")
    elif sub == "apply":
        plan_p, devices_p, track_p, out_p = a[1], a[2], a[3], a[4]
        plan = json.load(open(plan_p))
        devices = json.load(open(devices_p))
        track = json.load(open(track_p)) if os.path.exists(track_p) else None
        spec = json.load(open(a[5])) if len(a) > 5 else None
        result = apply(plan, devices, track, spec)
        json.dump(result, open(out_p, "w"), indent=1)
        print(f"{len(devices)} device(s) applied, {len(result['events'])} total events")
    else:
        raise ValueError(f"unknown subcommand {sub!r}")


if __name__ == "__main__":
    cli(_main, "devices.py propose <words.json> <out.json> | "
               "devices.py apply <plan.json> <devices.json> <facetrack.json> <out.plan.json> [<spec.json>]")
