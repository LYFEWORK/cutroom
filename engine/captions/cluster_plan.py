#!/usr/bin/env python3
"""cluster_plan.py <words.json> <facetrack.json> <out.plan.json> [--spec spec.json] [--cut S] [--frames N]
Cluster captions. Every phrase becomes ONE lockup: rows
overlap and stagger, one hero word 2-3x the others, mixed case, white + accent on the hero. Emits
CaptionsDynamic events (mode front/behind with cluster fields) + matte windows + whip frames.
spec.json (built by spec_adapter.py from your look): {"behind":[{"match":"Big Idea","rows":[["Big",300,"#FFFFFF",-80],["Idea",250,"accent",60]],
  "top":270,"hold":1.3}], "accent":"#96FF00", "font":{"family":"InterDyn","faces":[{"weight":"100 900","src":"brand-demo/fonts/Inter-Variable.ttf"}],
  "measure":"/abs/path/Inter-Variable.ttf"}, "chin_gap":90, "hero_max":220, "row_px":[96,120], "no_behind":false, "case":"mixed"}
accent and font are required (no defaults). Every row is measured with the brand's own font file (spec.font.measure; the running
face's file, look.word_font.measure, for the non-hero rows) and held inside the QC safe box.
no_behind (spec_adapter.py, tier MINIMAL): the room check found no space above the crown for any
hero text — every hand-specified spec["behind"] entry is skipped and zone is forced to "chest"
even if the spec asked for "top", so nothing tries to render above the head.
case (upper|mixed|title, default "mixed"): applied to every word cluster_plan.py renders — upper
uppercases, title capitalizes the first letter only, mixed renders the words as transcribed.
caption_style (spec_adapter.py, only when the take has a named style, its pick or your default): the id of the style whose fields were laid
over your look when look.json was made. The planner reads the styled values like any look value and only records the id, as a "style:<id>" entry in the plan's
flags; a spec without it plans (and flags) byte for byte as before.
glyph (spec_adapter.py, only when your face's measured width differs from the renderer's own numbers): the width factors the renderer's row-size cap reads. The cap itself is the
composition's (CaptionsDynamic.tsx): the planner reads its numbers out of that file (renderer_cap, short_hero_cap), never from a copy, so a hero_max above the cap is planned at
the cap and a block is placed by the height that renders.

Soft build (README, "The soft build"): when spec.look.soft_build is on, build() plans thoughts instead of clusters
(soft_plan, soft_events). The words go to the shared splitter (softbuild/), steered by the keys the planner puts on them (neg, struck,
logo, glue) so each device's words land on a payoff, and each thought is one front event with "soft": true and a lead-in row, a payoff row and an
optional tail row, laid out once under the chin and centerd on the safe box: no color, no glow, no stagger. The payoff row is the hero row
and carries the devices, refitted to what the renderer draws; one payoff of the plan that is neither red nor struck and carries no pin, logo or
spread takes the bar (an icon gives way to it). A person regroups the thoughts and moves the bar in GATE.md's Thoughts section: edited lines
reach soft_events as device_overrides.thoughts and replace the split, their [bar] the bar, whatever its payoff carries. Each event carries its
line as GATE.md shows it. A soft
plan is chest only (spec behind and finale are skipped, so there is no matte window and no whip) and adds "soft", the colors its words
settle through. The device switches are read once, for both planners, by switches(), and both write plan.json through plan_doc(). A spec
without soft_build plans byte for byte as before.

Room: a lockup taller than every room it could have is never collapsed to one row. scale_lockup scales the whole block down (every row together, the hero's lead
and the stagger kept) until it fits under the chin, above the head, or under the chin at the least gap, with every row at MIN_ROW or more; ONE row remains for a one-word phrase,
an all-weak phrase, and a frame with no room for a lockup at MIN_ROW (a face filling the top of it: the gate warns).

Caption DEVICES (the README, "Devices"): glow / neg /
count / strike are tags on the HERO row of an automatic phrase cluster (hand-specified spec["behind"]
rows are author-written and are only ever considered for glow, since that one is a pure function of
"is this row already colored accent"); pin + spaced are tags the hero row (or the row above it, for
pin) gets when the phrase contains one of the brand's spec["places"] entries. Every device defaults
OFF when its spec key is simply absent from spec.json (spec.get(key, False)/spec.get("places", [])
etc. -- never spec.get(key, True)) -- the brand-level "on by default" (count_up) is realised by
spec_adapter.py always writing the resolved bool into a FRESH spec.json; this function never invents
that default from a missing key. That is what keeps a pre-existing spec.json (produced before this
section existed) planning BYTE-IDENTICAL to before: no hero_glow/places/neg_words/count_up/
device_overrides keys -> GLOW_ON=COUNT_ON=False, PLACES_TOK=[], and NEG_SET/STRIKE_TRIGGERS are
data-driven (matched against the built-in word list only, unconditionally -- but a transcript with
none of those words plans exactly as it did before this feature existed).

Pacing devices (the README, "Pacing"): logo / icon are
two more hero-row tags, same unconditional-built-in-set convention as neg/strike above -- LOGO_MAP
and ICON_BUILTIN are always active (a bare spec.json with no icon_words key still tags "call"/
"DM us"/etc, exactly like NEG_BUILTIN always tags "weak"/"never"). ICON_BUILTIN holds only plain
words: the agency ones (ad, video, post, content, feed, track) belong to your own icon_words,
so a look never gets them unless it lists them. logo is the one device that
OVERRIDES hero_index()'s own choice rather than just tagging whatever row it already picked --
"Instagram"/"IG"/"FB" must become the hero even when a longer word in the same phrase would
otherwise win, so the words are literal ("that row is also the hero of its
phrase"). icon never moves the hero; it is skipped outright on a numeral hero (count already owns
that row) and on a row that already carries pin/logo/neg ("a row carries at most one of pin /
logo / icon"), then throttled to one per 4s the same way pin is throttled to one per 6s. Neither
device fires without a trigger word in the transcript, so a spec/words pair with none of the
LOGO_MAP/ICON_BUILTIN/icon_words tokens in it plans BYTE-IDENTICAL to before this section existed."""
import json, math, re, sys, os
from collections import namedtuple
from PIL import ImageFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import facetrack
import roomcheck
from keys import cli, EngineStop
# the soft build's shared core, the softbuild/ package next to this file
SOFTBUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "softbuild")
if not all(os.path.isfile(os.path.join(SOFTBUILD, f)) for f in ("__init__.py", "look.py", "sentences.py", "thoughts.py")):
    raise SystemExit(f"SETUP: the soft build's shared core is missing: {SOFTBUILD}")
from softbuild import look as soft_look, sentences, thoughts  # noqa: E402
FPS = 30; SAFE = dict(roomcheck.SAFE); MAXW = 800
CL_W = 860; CL_DX = -38   # chest budget + shift so the lockup is centerd in the safe box (x 502), not on the canvas
EDGE_MARGIN = 12          # px kept between a row's edge and the safe box (glow and shadow reach past the glyphs)
MIN_ROW = 44              # the smallest caption row worth drawing, px
MIN_GAP = 24              # the least air a row keeps under the chin when nothing else fits
SHRINK_FLOOR = 0.55       # the least a lockup is scaled to stay in the lane under the chin (below it the lane above the head is tried)
COMPOSITION = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "remotion", "captions", "CaptionsDynamic.tsx")   # the renderer: where its row-size cap is read from
STOP = set("a an the of to and or is are was were be it this that so we you i my me for on in at what they them can do did if but as by with from your our its just like here over we've i've you've been".split())
# Words that are never the hero of a phrase while a stronger word is in it (they are not STOP words: they still
# take part in phrase splitting). Contractions are weak as a class.
WEAK_HERO = set("gonna gotta wanna going something anything everything have has had their theirs there than most more know really very "
                "actually literally would could should because about many much some other then when where which while well okay yeah "
                "thing things stuff kind sort lot lots also even still every each those these into onto through before after "
                "mean once go goes get gets got let say said see make makes take takes come comes want wants need needs".split())
_CONTRACTION = re.compile(r"('s|'re|'ve|'ll|'d|n't|'m)$")
# the fonts the rows are measured with: set from spec["font"]["measure"] (and look.word_font.measure) at the top of build()
MEASURE = {"display": None, "running": None}
_fonts = {}
def set_measure(display, running=None):
    if not display:
        raise EngineStop("cluster_plan: the spec has no font to measure text with (spec.font.measure); build spec.json with spec_adapter.py")
    MEASURE["display"], MEASURE["running"] = display, running or display
def _face(path, px, weight):
    f = _fonts.get((path, px, weight))
    if f is None:
        try:
            f = ImageFont.truetype(path, px)
        except OSError as e:
            raise EngineStop(f"cluster_plan: cannot read the brand font {path}: {e}")
        try:
            # a variable font: the weight goes on the weight axis (clamped to its range) and every other axis keeps the font's own default;
            # an optical-size or width axis in first place must never be set to a weight
            f.set_variation_by_axes([soft_look._axis_value(a, weight) for a in f.get_variation_axes()])
        except (OSError, AttributeError, IndexError):
            pass                               # a static font: it is what it is
        _fonts[(path, px, weight)] = f
    return f
def width(text, px, weight=800, face="display"):
    path = MEASURE.get(face)
    if not path:
        raise EngineStop("cluster_plan: no measuring font set (set_measure / spec.font.measure)")
    return _face(path, px, weight).getlength(text) * 0.96   # -0.04em tracking
def fit(text, px, maxw, weight=800, floor=60, face="display"):
    while px > floor and width(text, px, weight, face) > maxw: px -= 4
    return px
def row_cap(center, x0, x1, margin=EDGE_MARGIN):
    """The widest a row centerd at x=center may be and still sit inside [x0, x1] with `margin` px to spare."""
    return max(120, 2 * min(x1 - center, center - x0) - 2 * margin)
# --- the renderer's own row-size cap ---------------------------------------------------------------------------
# CaptionsDynamic draws a cluster row no bigger than floor(maxW / max(FLOOR_LETTERS x caps, letters x factor + tracking + mark)): a row of FLOOR_LETTERS letters or fewer is held
# to maxW / (FLOOR_LETTERS x caps) however big the plan asks for it (320 px on a 960 px budget and 286 on 860 at the renderer's own factor, 370 and 331 in a face measured at caps 0.6481), and a
# longer row is held to its own width estimate. The numbers are READ from the composition (one `const NAME = <number>;` line each), never copied here, so what the planner fits
# and places is the block that renders. The plan's own `size` stays what the planner asked for unless the planner itself scales or holds a row (below): the renderer applies the same
# cap to it either way.
RENDERER_NUMBERS = ("GLYPH_CAPS", "FLOOR_LETTERS", "SPACED_EM", "MARK_PIN_EM", "MARK_LOGO_EM", "MARK_ICON_EM")
_renderer_numbers = {}
def renderer_numbers(path=None, names=RENDERER_NUMBERS):
    """The renderer's own numbers, read out of CaptionsDynamic.tsx (comments stripped, one `const NAME = <number>;` line each): by default the ones it caps a
    cluster row's size with (legible.py asks for its punch-in timings). EngineStop with one plain line when the file cannot be read or has lost one of them, so
    nothing in the engine falls back to a number of its own."""
    path = path or COMPOSITION
    got = _renderer_numbers.get((path, names))
    if got is None:
        try:
            with open(path, encoding="utf-8") as fh: text = fh.read()
        except OSError as e:
            raise EngineStop(f"cluster_plan: cannot read the composition {path} to learn the renderer's numbers: {e}")
        text = re.sub(r"(?m)(?<!:)//.*$", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
        got = {}
        for name in names:
            m = re.search(rf"(?m)^const {name} = (\d+\.?\d*|\.\d+);", text)
            if not m:
                raise EngineStop(f"cluster_plan: {os.path.basename(path)} has no `const {name} = <number>;` line, which the engine reads that number of the renderer from")
            got[name] = float(m.group(1))
        _renderer_numbers[(path, names)] = got
    return got
def renderer_cap(text, max_w, glyph=None, hero=False, spaced=False, pin=False, logo=False, icon=False, numbers=None):
    """The biggest size in px the composition draws a cluster row of this text at under a width budget of max_w (the event's maxW): CaptionsDynamic's own formula. glyph is the
    plan's {caps, word, hook} (absent: the renderer's own factor for every row); hero picks caps or word; spaced, pin, logo and icon are the row's own tags, a mark counting once (pin, else
    logo, else icon)."""
    n = numbers or renderer_numbers()
    base = (glyph["caps"] if hero else glyph["word"]) if glyph else n["GLYPH_CAPS"]
    per = base + (n["SPACED_EM"] if spaced else 0)
    mark = n["MARK_PIN_EM"] if pin else n["MARK_LOGO_EM"] if logo else n["MARK_ICON_EM"] if icon else 0
    floor_em = n["FLOOR_LETTERS"] * (glyph["caps"] if glyph else n["GLYPH_CAPS"])
    return math.floor(max_w / max(floor_em, len(text) * per + mark))
def short_hero_cap(max_w, glyph=None, numbers=None):
    """The most any cluster row is ever drawn at under a width budget of max_w: the cap of a row of FLOOR_LETTERS letters or fewer. A hero_max above it is drawn at it."""
    return renderer_cap("", max_w, glyph, hero=True, numbers=numbers)
def row_drawn(row, max_w, glyph=None, numbers=None):
    """The size the composition draws a planned row at: its own size, held to the renderer's cap for its text, tracking and mark."""
    return min(row["size"], renderer_cap(row["text"], max_w, glyph, bool(row.get("hero")), bool(row.get("spaced")), bool(row.get("pin")), bool(row.get("logo")), bool(row.get("icon")), numbers))
def hold_to_renderer(rows, max_w, glyph=None):
    """Hold every row to the most the renderer draws under max_w (short_hero_cap), so the plan carries the size that is drawn and the block the planner fits and places is the one that
    renders. True when a row changed; a row already under the cap, and so any plan whose hero_max is under it, is untouched."""
    cap, changed = short_hero_cap(max_w, glyph), False
    for r in rows:
        if r["size"] > cap: r["size"] = cap; changed = True
    return changed
def block_height(sizes):
    """The height in px of a lockup of rows this big, top row first: every row but the last overlaps the next at the planner's 0.84 leading, plus 48 px for the drop shadow."""
    return sum(sizes[:-1]) * 0.84 + sizes[-1] + 48
def scale_to_room(sizes, room):
    """(k, scaled): the rows' sizes scaled together by the largest k <= 1 whose block is at most `room` px high. The 48 px shadow allowance does not scale, and sizes are whole px
    rounded down, so the scaled block never runs over."""
    raw = block_height(sizes)
    if raw <= room: return 1.0, list(sizes)
    k = max(0.0, (room - 48) / (raw - 48)) * (1 - 1e-9)
    return k, [int(s * k) for s in sizes]
def scale_lockup(rows, max_w, glyph, chin, crown, chin_gap, glow_room):
    """A lockup whose block fits nowhere at its own size stays a lockup (every phrase is ONE tight cluster): its rows, as the renderer draws them, are scaled together
    (sizes, and the stagger of each row off the lockup's center) until the block fits, in the first of these places that can hold it with every row still at MIN_ROW:
      1. under the chin, in the take's own lane, at the usual gap, down to SHRINK_FLOOR (the exact version of the shrink the planner already tried);
      2. above the head, in front, the lane every lifted cluster uses;
      3. under the chin at the least gap (MIN_GAP), down to MIN_ROW.
    -> (top, above_head) with the rows changed in place, or None (nothing changed: not even a MIN_ROW lockup fits, and the caller falls back to one row)."""
    drawn = [row_drawn(r, max_w, glyph) for r in rows]
    under = int(chin + chin_gap + glow_room); tight = int(chin + MIN_GAP + glow_room)
    # (room for the block in px, the least scale that is still the lane's own look, where its top goes: None above the head, where it hangs from the crown)
    for room, floor_k, top in ((SAFE["y1"] - under, SHRINK_FLOOR, under), (crown - 24 - SAFE["y0"], 0.0, None), (SAFE["y1"] - tight, 0.0, tight)):
        if room <= 48: continue
        k, sizes = scale_to_room(drawn, room)
        if k < floor_k or min(sizes) < MIN_ROW: continue
        for r, size in zip(rows, sizes):
            r["size"] = size; r["dx"] = int((r["dx"] - CL_DX) * k) + CL_DX
        return (int(crown - 24 - block_height(sizes)) if top is None else top), top is None
    return None
def tok(t): return re.sub(r"[^a-z0-9']", "", t.lower())
def clean(t):
    t = t.strip(); return t[:-1] if t and t[-1] in ".,!" else t   # keep "?"
def casef(t, case):
    # spec["case"] (spec_adapter.py, from your look's case): one case per
    # look. "title" here means sentence case (capitalize the first letter only), not Python's
    # str.title() (which would capitalize every word).
    if case == "upper": return t.upper()
    if case == "title": return t[:1].upper() + t[1:] if t else t
    return t
JOIN = {("go", "to"): "go-to"}   # ASR splits that must read as one word
def joined(words):
    out = []; i = 0
    while i < len(words):
        w = words[i]
        if i + 1 < len(words) and (tok(w["text"]), tok(words[i + 1]["text"])) in JOIN:
            n = words[i + 1]; out.append(dict(w, text=JOIN[(tok(w["text"]), tok(n["text"]))] + re.sub(r"[^.,?!]", "", n["text"]), end=n["end"])); i += 2
        else: out.append(w); i += 1
    return out
NUM = {"zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,
       "thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,"thirty":30,
       "forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90}
def numerals(words):
    """Spoken numbers become numerals ("eleven" -> "11", "twenty five" -> "25", "two hundred" -> "200"): the hero
    rule keys on digits and a numeral is what the reference reels put on screen. Punctuation on the last token stays."""
    out = []; i = 0
    while i < len(words):
        w = words[i]; t = tok(w["text"]); trail = re.sub(r"[^.,?!]", "", w["text"])
        # "one" is a word far more often than a number ("to get one client"): it only becomes "1"
        # when it heads a larger number ("one hundred", "one million") or a "$one"-style unit form.
        nxt = tok(words[i + 1]["text"]) if i + 1 < len(words) else ""
        if t == "one" and nxt not in ("hundred", "thousand", "million", "billion", "percent"):
            out.append(w); i += 1; continue
        if t in NUM:
            val = NUM[t]; j = i + 1
            if val >= 20 and val % 10 == 0 and j < len(words) and tok(words[j]["text"]) in NUM and NUM[tok(words[j]["text"])] < 10:
                val += NUM[tok(words[j]["text"])]; trail = re.sub(r"[^.,?!]", "", words[j]["text"]); j += 1
            if j < len(words) and tok(words[j]["text"]) in ("hundred", "thousand"):
                val *= 100 if tok(words[j]["text"]) == "hundred" else 1000; trail = re.sub(r"[^.,?!]", "", words[j]["text"]); j += 1
            elif j < len(words) and tok(words[j]["text"]) in ("million", "billion"):
                # "$1 million" / "3 million" reads as the reference does: numeral + the word, one token
                out.append(dict(w, text=str(val) + " " + words[j]["text"], end=words[j]["end"])); i = j + 1; continue
            out.append(dict(w, text=str(val) + trail, end=words[j - 1]["end"])); i = j
        else:
            out.append(w); i += 1
    return out
def phrases(words, max_len=4, gap=0.28):
    words = joined(numerals(words))
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_p = bool(re.search(r"[.?!,]$", w["text"]))
        big_gap = nxt is not None and nxt["start"] - w["end"] >= gap
        # a phrase carrying a strike trigger ("we don't do long term contracts") keeps its tail: the
        # struck row is the whole rejected thing, so the cap stretches to 6 words for that phrase
        limit = 6 if any(tok(x["text"]) in STRIKE_TRIGGERS for x in cur) else max_len
        if end_p or big_gap or len(cur) >= limit or nxt is None:
            out.append(cur); cur = []
    # a lone stop-word phrase rides with its neighbour
    merged = []
    for p in out:
        if merged and len(p) == 1 and tok(p[0]["text"]) in STOP and len(merged[-1]) < max_len: merged[-1].extend(p)
        else: merged.append(p)
    # A comma is a WEAK boundary: a 1-2 word continuation after one ("Portland, / Oregon.")
    # is the same thought and must read as ONE cluster. Merge it back when the
    # combined phrase still fits the row budget.
    i = 1
    while i < len(merged):
        prev, cur = merged[i - 1], merged[i]
        if (len(cur) <= 2 and len(prev) + len(cur) <= max_len + 1
                and re.search(r",$", prev[-1]["text"]) and cur[0]["start"] - prev[-1]["end"] < 0.5):
            prev.extend(merged.pop(i)); continue
        i += 1
    # a stop word stranded at the END of a phrase (the length cap fell mid-thought: "This is Sam over") rides
    # forward into the next phrase when that one opens with stop words too ("over here at")
    for i in range(len(merged) - 1):
        while len(merged[i]) > 1 and tok(merged[i][-1]["text"]) in STOP and tok(merged[i + 1][0]["text"]) in STOP \
              and len(merged[i + 1]) < max_len and not re.search(r"[.?!,]$", merged[i][-1]["text"]):
            merged[i + 1].insert(0, merged[i].pop())
    return merged
def phrases_around(words, spans, max_len=4, gap=0.28):
    """phrases() run separately on each stretch of the transcript between layout-event edges (spec["layout_spans"]: the [start, end]
    of every cta / page / frame / igpost the gate accepted). A cluster that straddled the start of one is cut off there by
    devices.py apply, and the words spoken after the event ended, in the same row or the next, never got a caption. Planned this
    way the words after an event are a cluster of their own and no merge in phrases() reaches across an edge. No spans: phrases()."""
    cuts = sorted({float(t) for s in spans or [] for t in (s[0], s[1])})
    if not cuts: return phrases(words, max_len, gap)
    out, stretch, seen = [], [], None
    for w in words:
        k = sum(1 for c in cuts if c <= w["start"] + 1e-6)     # a word starting on an edge belongs to the stretch after it
        if stretch and k != seen:
            out += phrases(stretch, max_len, gap); stretch = []
        stretch.append(w); seen = k
    return out + (phrases(stretch, max_len, gap) if stretch else [])
def _weak(t):
    return t in WEAK_HERO or bool(_CONTRACTION.search(t))
def hero_index(p, add=frozenset(), drop=frozenset()):
    """The hero word of a phrase. A GATE.md `hero:` word wins when the phrase holds it; a `no-hero:` word is never picked
    (unless nothing else is left). Otherwise a numeral, else the longest word that is not a stop word and not a weak one
    (gonna, something, that's, have ...), falling back to weak words only when nothing stronger is there."""
    forced = [i for i, w in enumerate(p) if tok(w["text"]) in add]
    if forced: return max(forced, key=lambda i: len(tok(p[i]["text"])))
    nums = [i for i, w in enumerate(p) if re.search(r"\d", w["text"]) and tok(w["text"]) not in drop]
    if nums: return nums[0]
    for keep in (lambda t: t not in STOP and not _weak(t) and t not in drop,
                 lambda t: t not in STOP and t not in drop,
                 lambda t: t not in drop,
                 lambda t: True):
        cands = [(len(tok(w["text"]).replace("'", "")), i) for i, w in enumerate(p) if keep(tok(w["text"]))]
        if cands: return max(cands)[1]

# --- caption devices -----------------------------------------
# Built-in negative/weak-word set, unconditionally ORed with your look's spec["neg_words"] (and
# a take's GATE.md `neg:`/`no-neg:` overrides) -- the README, "Devices".
NEG_BUILTIN = {"weak", "no", "never", "nothing", "wrong", "stop", "worst", "fail", "waste",
               "wasting", "broke", "losing", "stuck", "bad", "scam", "fake"}
# strike's grammar trigger set: "(no|don't|never|without|not) ... noun-phrase-hero".
STRIKE_TRIGGERS = {"no", "dont", "don't", "never", "without", "not"}
STRIKE_VERBS = {"do", "have", "need", "want", "sign", "use", "charge", "require", "offer", "take", "get", "run", "pay", "make", "any"}
VERB_HEROES = {"understand", "know", "think", "see", "want", "need", "like", "mean", "care", "believe", "work", "get", "go",
               "have", "do", "say", "tell", "matter", "worry", "forget", "remember"}
# --- pacing devices (the README, "Pacing"): logo + icon --------------------
# logo: tok()'d trigger -> the LWord.logo enum value the reference lists (row-pops section).
# Twitter/X both collapse to "x". Always active, same unconditional convention as NEG_BUILTIN.
ICONS_TSX = os.path.join(os.path.dirname(COMPOSITION), "Icons.tsx")   # where the platform marks the renderer can draw are listed (LOGO_PATHS)
def logo_names(path=None):
    """The platform marks the renderer can draw: the names in Icons.tsx's LOGO_PATHS block, one `name:` a line (the marks are optional, so a project may
    list fewer than nine, or none). None when the file cannot be read, which the renderer's own checks report when it matters."""
    try:
        with open(path or ICONS_TSX, encoding="utf-8") as fh: text = fh.read()
    except OSError:
        return None
    m = re.search(r"const LOGO_PATHS[^=]*=\s*\{(.*?)\n\};", text, re.S)
    return set(re.findall(r"^  (\w+):", m.group(1), re.M)) if m else set()
LOGO_MAP_ALL = {"instagram": "instagram", "ig": "instagram", "facebook": "facebook", "fb": "facebook",
                "tiktok": "tiktok", "youtube": "youtube", "google": "google", "meta": "meta",
                "whatsapp": "whatsapp", "gmail": "gmail", "twitter": "x", "x": "x"}
def drawn_logo_map(path=None):
    """LOGO_MAP_ALL cut to the marks Icons.tsx has an entry for: a word whose mark is not shipped is never tagged, so no room is held for it"""
    have = logo_names(path)
    return dict(LOGO_MAP_ALL) if have is None else {k: v for k, v in LOGO_MAP_ALL.items() if v in have}
LOGO_MAP = drawn_logo_map()
# icon: the plain default trigger map, always active. Your captions.icon_words adds to it (an agency's words
# live there): even a look that REPLACED icon_words with a short custom list still gets these,
# because _icon_map() below unions onto this, not onto whatever spec["icon_words"] happens to contain. A look that lists
# nothing gets nothing else: no ad, video, post, content, feed or track icon ever comes from the engine itself.
ICON_BUILTIN = {"phone": "phone", "call": "phone", "text": "phone",
                "dm": "chat", "message": "chat", "chat": "chat",
                "book": "calendar", "schedule": "calendar", "appointment": "calendar", "calendar": "calendar",
                "money": "dollar", "pay": "dollar", "price": "dollar", "cost": "dollar",
                "review": "star", "rating": "star", "star": "star",
                "search": "search", "find": "search"}
def _icon_map(icon_words):
    """ICON_BUILTIN unioned with your look's icon_words (list of "word:icon" strings: README, Devices).
    A malformed entry (no colon, or an empty side) is skipped rather
    than raising: this is a planner lookup table, not a validated profile field."""
    out = dict(ICON_BUILTIN)
    for item in icon_words or []:
        word, sep, icon = str(item).partition(":")
        word, icon = tok(word), icon.strip()
        if sep and word and icon:
            out[word] = icon
    return out
def _logo_index(p):
    """First index in phrase `p` whose tok()'d text is a LOGO_MAP trigger, or (None, None)."""
    for i, w in enumerate(p):
        name = LOGO_MAP.get(tok(clean(w["text"])))
        if name is not None:
            return i, name
    return None, None
def _count_value(text):
    """Digits-only value of a numeral hero for the `count` device, or None when it is not
    count-eligible. Only integers 10..99999 ("a '$6' doesn't count") -- and a BARE
    (undecorated) 4-digit token shaped like 1900-2099 reads as a year, never a count. A decorated
    one ("$1,200", "1900 people" -- the ASR/normalize_words numeral would carry a suffix word
    separately, but a currency/percent glyph on the token itself counts as decoration) still
    counts: the decoration is what tells a dollar amount or a quantity apart from a calendar
    year."""
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    value = int(digits)
    decorated = bool(re.search(r"[^\d]", text))
    if not decorated and re.fullmatch(r"(19|20)\d{2}", text):
        return None
    return value if 10 <= value <= 99999 else None
def _span_of(p, target_toks):
    """First contiguous index span [i, i+n) in phrase `p` whose tok()'d words equal the tuple
    `target_toks` (itself already tok()'d), or None. Shared by the places (pin/spaced) and the
    strike_add (GATE.md `strike:` override) matchers -- both are "does this multi-word phrase
    appear literally, in order, somewhere in this cluster" lookups."""
    if not target_toks:
        return None
    toks = [tok(w["text"]) for w in p]
    n = len(target_toks)
    if n > len(toks):
        return None
    for i in range(len(toks) - n + 1):
        if tuple(toks[i:i + n]) == target_toks:
            return i, i + n
    return None
# Every caption device's switch, resolved once from the spec for both planners (build()'s clusters and soft_events' thoughts). See the module
# docstring's byte-identical note: each MUST default to off on a missing key, never to the brand-level "on by default" (that lives in spec_adapter.py).
Switches = namedtuple("Switches", "neg_set strike_add places count_on pin_on logo_on icon_on icon_map chip_neg")
def switches(spec):
    """The spec's device switches: the red words (NEG_BUILTIN, the brand's neg_words, GATE.md neg: and no-neg:), the GATE.md strike: phrases and the
    brand's places as token tuples (places longest first), count-up, pin, logo and icon on or off (logo and icon have the same on-by-default,
    GATE.md-overridable shape as count and pin, the README, "Pacing"), the icon word map and the brand's negative red."""
    ov = spec.get("device_overrides") or {}
    return Switches(
        neg_set=((NEG_BUILTIN | {tok(x) for x in (spec.get("neg_words") or [])}
                  | {tok(x) for x in (ov.get("neg_add") or [])})
                 - {tok(x) for x in (ov.get("neg_off") or [])}),
        strike_add=[tuple(tok(x) for x in str(s).split()) for s in (ov.get("strike_add") or [])],
        places=sorted((tuple(tok(x) for x in str(pl).split()) for pl in (spec.get("places") or []) if str(pl).strip()), key=len, reverse=True),
        count_on=bool(spec.get("count_up", False)) and bool(ov.get("count", True)),
        pin_on=bool(ov.get("pin", True)),
        logo_on=bool(ov.get("logo", True)),
        icon_on=bool(ov.get("icon", True)),
        icon_map=_icon_map(spec.get("icon_words")),
        chip_neg=(spec.get("look") or {}).get("chip_neg") or "#E3161B")
def last_matte_frame(end_s, cut_s, total_frames):
    """Last frame index (inclusive) of a matte window that ends at end_s: two frames of margin, never past the cut and never past
    the picture's last frame (index total_frames - 1: a window that asks for frame total_frames asks for a frame that is not there)."""
    return min(int(end_s * FPS) + 2, int(cut_s * FPS), total_frames - 1)


# --- the soft build (the README, "The soft build"): thoughts instead of clusters ---------------------------------------
SOFT_DX = soft_look.CENTER_X["engine"] - 540   # -38: every line of a thought is centerd on x 502, the middle of the QC safe box; no stagger
SOFT_SHADOW = 24                               # px the soft shadow reaches under a thought's last line: 6 px down and an 18 px blur
def _ends(w):
    """Whether a word ends its clause for a strike: it carries a stop, a comma or a hard end."""
    return bool(w["stop"] or w["comma"] or w.get("hard_end"))
def _strike_from(W, k):
    """Where the strike word k triggers starts: after a strike trigger and its doing verb, or right after a bare "no" or "without"; None when word k
    triggers none (it is no trigger, or the trigger or its verb ends its clause)."""
    t = tok(W[k]["text"])
    if t not in STRIKE_TRIGGERS or _ends(W[k]):
        return None
    if k + 1 < len(W) and tok(W[k + 1]["text"]) in STRIKE_VERBS:
        return None if _ends(W[k + 1]) else k + 2
    return k + 1 if t in ("no", "without") else None
def _verb_hero_first(W, idx):
    """Whether the first content word of these words (the first the splitter does not read as a function word) is a verb hero: a strike never
    crosses one out ("You don't need | to go in here and")."""
    first = next((W[k] for k in idx if not thoughts.is_function(W[k]["text"])), None)
    return first is not None and tok(first["text"]) in VERB_HEROES
def _soft_words(words, spec, sw):
    """The words the splitter reads: every word but the fillers, copied (sentences.mark sets stop and comma from each word's own punctuation), with
    the keys it steers by. hard_end, the edge's own time, on the last word before each edge of a layout event (spec layout_spans; a word starting on
    an edge belongs to the stretch after it, as in phrases_around), so the thought that ends there is gone by the edge. neg on a red word: one in sw.neg_set, a strike trigger only when it carries its own stop or comma
    ("The answer is no.", never a promise like "we never miss"), or the word closing a "not X" or "no X" at a stop or a comma. struck, the run's
    length, on the first word of each run a strike would cross out: from the word after a trigger and its doing verb (or a bare "no" or
    "without") to the stop, comma or hard end that ends it within a payoff's most ("no setup fees at all.", never the idiom "no idea how many
    leads we were missing"), and a GATE.md strike: phrase as written; a run that holds a red word, opens on a verb hero or is longer than a
    payoff can never strike and is left unmarked. glue on a trigger before its doing verb, so the two end the lead-in together. place, the
    place's length, on the first word of each of the brand's places (spec places, the longer of two that overlap), so its thought takes the
    payoff that is exactly the place when it can and the place ends its phrase (never "a Florida print shop"). logo on a platform name when
    logos are on."""
    W = sentences.mark([w for w in words if not w.get("filler")])
    thoughts.end_at(W, {float(t) for s in spec.get("layout_spans") or [] for t in (s[0], s[1])})
    toks = [tok(w["text"]) for w in W]
    for k, w in enumerate(W):
        clause = w["stop"] or w["comma"]
        if (toks[k] in sw.neg_set and (toks[k] not in STRIKE_TRIGGERS or clause)) or (k > 0 and toks[k - 1] in ("not", "no") and clause):
            w["neg"] = True
    runs = []                                   # (first word, past the last, the trigger glued to its verb or None)
    for k in range(len(W)):
        start = _strike_from(W, k)
        if start is None:
            continue
        end = next((e + 1 for e in range(start, min(len(W), start + thoughts.PAY_WORDS)) if _ends(W[e])), None)
        if end is not None and not _verb_hero_first(W, range(start, end)):
            runs.append((start, end, k if start == k + 2 else None))
    for phrase in sw.strike_add:
        runs += [(i, i + len(phrase), None) for i in range(len(W) - len(phrase) + 1) if phrase and tuple(toks[i:i + len(phrase)]) == phrase]
    for start, end, glued in runs:
        if end - start <= thoughts.PAY_WORDS and not any(x.get("neg") for x in W[start:end]):
            W[start]["struck"] = max(W[start].get("struck", 0), end - start)
            if glued is not None:
                W[glued]["glue"] = True
    held = set()                                # the words of the places already marked: sw.places runs longest first, so a longer one wins
    for place in sw.places:
        for i in range(len(W) - len(place) + 1):
            if tuple(toks[i:i + len(place)]) == place and not held & set(range(i, i + len(place))):
                W[i]["place"] = len(place)
                held |= set(range(i, i + len(place)))
    for k, w in enumerate(W):
        if sw.logo_on and toks[k] in LOGO_MAP:
            w["logo"] = True
    return W
def _named_red(spec):
    """The red words a person or the brand named, as tokens: GATE.md neg: lines and the brand's neg_words, less GATE.md no-neg: lines. One of these that its
    thought's payoff does not hold draws in its line's own color, and the soft build says so in a CHECK line. The built-in red words (NEG_BUILTIN) were
    only ever drawn on a cluster's hero row, so one of those off the payoff is not said."""
    ov = spec.get("device_overrides") or {}
    return ({tok(x) for x in (spec.get("neg_words") or [])} | {tok(x) for x in (ov.get("neg_add") or [])}) - {tok(x) for x in (ov.get("neg_off") or [])}
def _in_place(toks, a, b, places):
    """Whether words a to b (b past the last) lie inside one occurrence of a place (places: token tuples, toks: every word's token)."""
    return any(tuple(toks[i:i + len(p)]) == p for p in places for i in range(max(0, b - len(p)), a + 1))
def _payoff_devices(W, pay, sw, toks):
    """(devices, place, icon) of a thought's payoff row, the part a cluster's hero row plays today. neg when it holds a red word; else strike when it
    starts at a struck run's first word and holds the whole run (_soft_words marks the runs: a trigger's, or a GATE.md strike: phrase), so a run
    the path passed over shows no strike at all. logo for a platform name in it; count when it holds one number and that one counts (the
    renderer's countText swaps one digit run); spaced when it lies inside one of the brand's places (place: it is a pin candidate). icon is the
    first icon word in it, never on a payoff with a number, a place, a logo or red; pin and icon are throttled by the caller."""
    words = [W[k] for k in pay]
    dev = {}
    if any(w.get("neg") for w in words):
        dev["neg"] = True
    elif W[pay[0]].get("struck") and len(pay) >= W[pay[0]]["struck"]:
        dev["strike"] = True
    logo = _logo_index(words)[1] if sw.logo_on else None
    if logo:
        dev["logo"] = logo
    numbers = [clean(w["text"]) for w in words if re.search(r"\d", w["text"])]
    count = _count_value(numbers[0]) if sw.count_on and "neg" not in dev and len(numbers) == 1 else None
    if count is not None:
        dev["count"] = count
    place = _in_place(toks, pay[0], pay[-1] + 1, sw.places)
    if place:
        dev["spaced"] = True
    icon = None
    if sw.icon_on and not (numbers or place or logo or "neg" in dev):
        icon = next(filter(None, (sw.icon_map.get(tok(clean(w["text"]))) or ("dollar" if "$" in w["text"] else None) for w in words)), None)
    return dev, place, icon
def soft_measure(spec):
    """measure(text, px, role): the width in px of a soft line in the brand's display font (softbuild look.font_measure at the spec's display weight,
    variable or static, and case), as the splitter and the renderer lay it out. A font that cannot be read is one EngineStop line."""
    look = spec.get("look") or {}
    font = soft_look.font_measure(spec["font"]["measure"], int(look.get("display_weight") or 800), bool(look.get("font_variable")), spec.get("case", "mixed"))
    def measure(text, px, role):
        try:
            return font(text, px, role)
        except OSError as e:
            raise EngineStop(f"cluster_plan: {e}")
    return measure
def _soft_row(W, idx, role, size, weight, case, bar=False):
    """One line of a thought: its words exactly as spoken, punctuation kept, in the line's case; the spoken start of its first word (at: QC G5 reads
    it) and of every word it draws (ats: a token with a space in it, your "Big Idea", is two words arriving together); thoughts.line_of."""
    text, ats = thoughts.line_of(W, idx, role, case)
    return {"text": text, "at": W[idx[0]]["start"], "ats": ats, "role": role, "size": size, "weight": weight, "dx": SOFT_DX, "hero": role == "pay", "bar": bar}
def soft_drawn(measure, row, px):
    """The width a soft row draws at px, as the renderer lays it out (the composition's own numbers, renderer_numbers(), as row_drawn reads them for
    a cluster row): its text at its role's tracking (measure, the bare font measure, soft_measure), SPACED_EM of px a character when it is spaced,
    and its one mark (pin, else logo, else icon)."""
    n = renderer_numbers()
    mark = next((n[f"MARK_{k.upper()}_EM"] for k in ("pin", "logo", "icon") if row.get(k)), 0)
    return measure(row["text"], px, row["role"]) + px * (mark + (n["SPACED_EM"] * len(row["text"]) if row.get("spaced") else 0))
def _dropped(row, names):
    """Notes on a row the devices it could not draw (dropped, each name once), which GATE.md prints as a CHECK line."""
    if names:
        row["dropped"] = row.get("dropped", []) + [n for n in names if n not in row.get("dropped", [])]
def _soft_refit(row, measure, width):
    """A payoff that carries a spread or a mark, sized for what it draws (soft_drawn): from its size down in 2 px steps until that fits width,
    never under PAY_FLOOR. When the floor still does not fit, the spread goes, and then the marks (the splitter fitted the payoff with room for a
    platform name's mark, so this is rare); a row that loses any says so in dropped."""
    for gone in ((), ("spaced",), ("pin", "logo", "icon")):
        _dropped(row, [key for key in gone if row.pop(key, None) is not None])
        px = soft_look.fit(row["text"], row["size"], width, soft_look.PAY_FLOOR, lambda text, size: soft_drawn(measure, row, size))
        if soft_drawn(measure, row, px) <= width:
            row["size"] = px
            return
def soft_block(sizes):
    """The height in px of a thought whose lines are this big: the lines, LINE_GAP between two, and the shadow under the last one."""
    return sum(sizes) + soft_look.LINE_GAP * (len(sizes) - 1) + SOFT_SHADOW
def _soft_shrink(sizes, floors, room):
    """The sizes scaled down together by the largest factor k <= 1 at which the thought is at most `room` px high, a line that k would take under its
    floor held at the floor; None when even the floors are too tall. Whole px, rounded down, so the block never runs over."""
    space = room - soft_block([0] * len(sizes))
    if sum(floors) > space:
        return None
    held = set()
    while True:
        free = [i for i in range(len(sizes)) if i not in held]
        k = min(1.0, (space - sum(floors[i] for i in held)) / sum(sizes[i] for i in free)) if free else 1.0
        low = {i for i in free if sizes[i] * k < floors[i]}
        if not low:
            return [floors[i] if i in held else int(sizes[i] * k) for i in range(len(sizes))]
        held |= low
def _soft_place(rows, track, start, end, chin_gap):
    """The top of a thought, its rows shrunk in place when they must be. It sits chin_gap under the lowest chin of its life (its start, its middle
    and 1.5 s in, as a cluster samples it); when the block would pass the safe box every line shrinks by one factor, none under its floor (lead-in
    and tail 44, payoff 60); still too tall, the least gap (MIN_GAP); still too tall, the floors there, passing the safe box (the plan's
    no_caption_room flag counts it). Never above the head."""
    chin = max(f["cy"] + f["h"] / 2 for f in (facetrack.at(track, t) for t in (start, (start + end) / 2, min(end, start + 1.5))))
    sizes = [r["size"] for r in rows]
    floors = [soft_look.PAY_FLOOR if r["role"] == "pay" else soft_look.LEAD_FLOOR["engine"] for r in rows]
    for gap in (chin_gap, MIN_GAP):
        fitted = _soft_shrink(sizes, floors, SAFE["y1"] - int(chin + gap))
        if fitted is not None:
            for r, size in zip(rows, fitted): r["size"] = size
            return int(chin + gap)
    for r, size in zip(rows, floors): r["size"] = size
    return int(chin + MIN_GAP)
def _throttled(marks, every):
    """The (row, value) of each (at, row, value) mark that may show when one may show every `every` seconds, the first winning: today's pin (6 s)
    and icon (4 s) throttles."""
    kept, last = [], None
    for at, row, value in sorted(marks, key=lambda m: m[0]):
        if last is None or at - last >= every:
            kept.append((row, value)); last = at
    return kept
BAR_YIELDS = ("neg", "strike", "pin", "spaced", "logo", "icon")   # what a payoff on the bar gives up to it, in the order its dropped note names them
def _given_or_split(W, measure, spec, next_start):
    """(the thoughts, whether a person wrote them): the lines of GATE.md's Thoughts section when a person edited them (device_overrides.thoughts, read
    by thoughts.parse_lines, their [bar] the bar), else the splitter's own (thoughts.split). Lines that no longer match the words stop the take with
    the splitter's one line, which names GATE.md, and so does a line that runs across a layout event: an edge is a hard end, as in the split."""
    given = (spec.get("device_overrides") or {}).get("thoughts")
    if given is None:
        return thoughts.split(W, measure, "engine", next_start), False
    try:
        got = thoughts.parse_lines(given, W, measure, "engine", next_start)
    except thoughts.ThoughtError as e:
        raise EngineStop(f"GATE.md {e}")
    across = thoughts.crossing(given, got, W)   # a layout event's edge is a hard end for a person's lines too
    if across:
        row, k = across
        raise EngineStop(f"GATE.md thoughts: line {row} runs across a layout event after {W[k]['text']!r} ({W[k]['start']:.2f} s); end the line there")
    return got, True
def soft_events(words, track, spec, cut_s, sw=None):
    """The soft build's captions: the take's words split into thoughts (softbuild/thoughts.py, at the engine's widths and floors, measured with the
    brand's display font), or the thoughts a person wrote in GATE.md, one front event {mode, soft, start, end, exitAt, y, words, line} a thought (and
    short when it cannot be read in time, as the splitter or parse_lines marks it; red_off, [{text, at}], the red words a person or the brand named
    (_named_red) that its payoff does not hold, which draw in their line's own color, since red is drawn only on a payoff (thoughts.red_left_off);
    and last_unread when a layout event's edge cuts off its last word, which it leaves before that word is drawn: thoughts.last_word_cut_off),
    its rows the lead-in, the payoff and the tail, and line the thought as GATE.md shows it (thoughts.format_line over the words as spoken). It
    starts to arrive ARRIVE_EARLY before its first word; it starts to leave (exitAt) LEAVE_LEAD before the next thought's first word, or LEAVE_S
    before a layout event's edge when its last word is the last before one, so it is gone by the edge (thoughts.hard_leave: the splitter's own leave,
    so what it checked for reading time is what draws), and never later than LINGER_S (1.0 s) after its last word ends, so it never holds over the
    silence after its words, save that the last one keeps to the cut when that comes within LINGER_S + LEAVE_S of its last word; it is gone LEAVE_S
    later (thoughts.timing), and a silence longer than LINGER_S + LEAVE_S with content words on both sides ends a
    thought in the split. The payoff row is the hero row (GATE.md and NOTES.md list
    it) and carries the devices, decided first; then the
    bar: the payoff a person marked [bar], or else the one thoughts.pick_bar picks among those that are neither red nor struck and carry no logo or
    spread (an icon is the lightest device and may take it). The bar's box is the payoff's text alone and the bar is the reel's one emphasis, so
    whatever else its payoff carried, its pin or icon included, gives way to it and is noted in dropped; then the throttles settle pin and icon among
    the other payoffs. A payoff with a spread or a mark is refitted to what draws; then each thought is placed. The splitter fits a payoff that holds a platform name with room for its mark,
    and a thought whose payoff could not hold its platform name says so on that payoff row (its lead-in row when it has none): dropped gains "logo".
    sw: the spec's switches when the caller has them (switches(spec) otherwise)."""
    sw = sw or switches(spec)
    look = spec.get("look") or {}
    weight, variable, case = int(look.get("display_weight") or 800), bool(look.get("font_variable")), spec.get("case", "mixed")
    W = _soft_words(words, spec, sw)
    bare = soft_measure(spec)
    logo_em = renderer_numbers()["MARK_LOGO_EM"] if sw.logo_on else 0
    def measure(text, px, role):   # what the splitter and the bar fit with: a payoff holding a platform name keeps room for its mark
        mark = logo_em * px if role == "pay" and logo_em and any(tok(x) in LOGO_MAP for x in text.split()) else 0
        return bare(text, px, role) + mark
    got, given = _given_or_split(W, measure, spec, cut_s + soft_look.LEAVE_LEAD)
    toks = [tok(w["text"]) for w in W]
    lead_w, pay_w = soft_look.weights(weight, variable)
    planned, pins, icons = [], [], []
    for t, when in zip(got, thoughts.timing(got, W, cut_s)):
        start, end, exit_at = when["start"], when["end"], when["exitAt"]
        rows, pay = [_soft_row(W, t["lead"], "lead", t["lead_px"], lead_w, case)], None
        if t["pay"]:
            dev, place, icon = _payoff_devices(W, t["pay"], sw, toks)
            pay = dict(_soft_row(W, t["pay"], "pay", t["pay_px"], pay_w, case), **dev)
            rows.append(pay)
            if place and sw.pin_on: pins.append((pay["at"], pay, True))
            if icon: icons.append((pay["at"], pay, icon))
        if t["tail"]:
            rows.append(_soft_row(W, t["tail"], "tail", t["lead_px"], lead_w, case))
        planned.append((t, pay, start, end, exit_at, rows))
    if not given:   # the planner's bar: never on a red or struck payoff, nor on one whose spread (a place, a pin's too) or logo carries what the line means
        thoughts.pick_bar([t for t, pay, *_ in planned if pay and not {"neg", "strike", "logo", "spaced"} & set(pay)], W, measure, "engine")
    for t, pay, *_ in planned:   # the bar's payoff gives up what it carries before the throttles, so a pin or an icon near it may still show
        if pay and t["bar"]:
            marks = {key for key, cands in (("pin", pins), ("icon", icons)) if any(row is pay for _, row, _ in cands)}
            _dropped(pay, [k for k in BAR_YIELDS if pay.pop(k, None) is not None or k in marks])
            pins[:], icons[:] = [m for m in pins if m[1] is not pay], [m for m in icons if m[1] is not pay]
    for key, marks, every in (("pin", pins, 6.0), ("icon", icons, 4.0)):
        for row, value in _throttled(marks, every):
            row[key] = value
    widths = soft_look.widths("engine")
    for t, pay, *_, rows in planned:
        if pay:
            pay["size"], pay["bar"] = t["pay_px"], t["bar"]
            if not t["bar"] and any(k in pay for k in ("spaced", "pin", "logo", "icon")):
                _soft_refit(pay, bare, widths["pay"])
        if sw.logo_on and any(W[k].get("logo") for k in t["lead"] + t["tail"]):   # a platform name the payoff could not hold
            _dropped(pay or rows[0], ["logo"])
    chin_gap, named = spec.get("chin_gap", 90), _named_red(spec)

    def red_off(t):   # the red words a person or the brand named that the payoff does not hold: they draw in their line's own color
        return [{"text": W[k]["text"], "at": W[k]["start"]} for k in thoughts.red_left_off(t, W) if toks[k] in named]
    return [{"mode": "front", "soft": True, "start": start, "end": end, "exitAt": exit_at, "y": _soft_place(rows, track, start, end, chin_gap),
             "words": rows, "line": thoughts.format_line(t, W), **({"short": True} if t["short"] else {}),
             **({"red_off": off} if (off := red_off(t)) else {}),
             **({"last_unread": True} if thoughts.last_word_cut_off(W[(t["tail"] or t["pay"] or t["lead"])[-1]], exit_at) else {})}
            for t, _, start, end, exit_at, rows in planned]
def soft_plan(words, track, spec, cut_s, total_frames):
    """plan.json for a soft build take: the thoughts (soft_events) as its events, chest only (spec behind and finale are skipped, as no_behind does,
    so there is no matte window and no whip), the flags as today (no_caption_room counts the thoughts that pass the safe box even at their floors),
    and "soft", the colors a thought settles through: the accent's deep and light tints, text (the brand's colors.white, which every spec carries as
    look.base: what every line that is not red and not on the bar settles to), barInk (the brand's on_accent, what the payoff settles to on the bar)
    and neg (the brand's negative red)."""
    sw = switches(spec)
    events = soft_events(words, track, spec, cut_s, sw)
    no_room = sum(1 for e in events if e["y"] + soft_block([r["size"] for r in e["words"]]) > SAFE["y1"])
    plan = plan_doc(spec, total_frames, events, [], [], "chest", no_room)
    deep, light = soft_look.tints(spec["accent"])
    look = spec.get("look") or {}
    plan["soft"] = {"deep": deep, "light": light, "text": look.get("base") or "#FFFFFF", "barInk": look.get("on_accent") or "#FFFFFF", "neg": sw.chip_neg}
    return plan


def build(words, track, spec, cut_s, total_frames):
    if not spec.get("accent"):
        raise EngineStop("cluster_plan: the spec has no accent color; build spec.json with spec_adapter.py")
    font_spec = spec.get("font") or {}
    set_measure(font_spec.get("measure"), ((spec.get("look") or {}).get("word_font") or {}).get("measure"))
    if not font_spec.get("family") or not font_spec.get("faces"):
        raise EngineStop("cluster_plan: the spec has no font (family and files); build spec.json with spec_adapter.py")
    if (spec.get("look") or {}).get("soft_build"):
        return soft_plan(words, track, spec, cut_s, total_frames)
    accent = spec["accent"]
    # The non-accent row color is your look's base (spec.look.base), not a hardcoded white: a look
    # on a light page captions in ink, not white.
    WHITE = (spec.get("look") or {}).get("base") or "#FFFFFF"
    chin_gap = spec.get("chin_gap", 90); hero_max = spec.get("hero_max", 220); row_px = spec.get("row_px", [96, 120])
    look0 = spec.get("look") or {}
    whip_frames = int(look0.get("whip_frames", 3)); whip_amount = float(look0.get("whip_amount", 1.0))
    def whip(at, k=1.0):
        # whip_frames 0 turns the whip cuts off; whip_amount scales the blur and push (the planner's 1.0 / 0.8 pair keeps its ratio)
        return {"at": at, "frames": whip_frames, "amount": round(whip_amount * k, 3)} if whip_frames > 0 and whip_amount > 0 else None
    case = spec.get("case", "mixed"); no_behind = bool(spec.get("no_behind", False))
    glyph = spec.get("glyph")   # your look's measured width factors (spec_adapter.py): what the renderer's row-size cap reads, absent when they are the renderer's own
    # zone "top": heroes 300-380 px, rows fitted to
    # `width` (960 = 60 px margins, the platforms draw no controls in the top third), block anchored to the HEAD
    # (bottom sits a little into the crown) and rendered BEHIND the person, so the matte, not the layout, keeps
    # text off the face. The plan carries its own `safe` box for the QC sweep.
    zone = spec.get("zone", "chest")
    if no_behind: zone = "chest"   # spec_adapter.py (tier MINIMAL): no room above the crown — everything renders chest/front
    cl_w = spec.get("width", CL_W); cl_dx = 0 if zone == "top" else CL_DX
    # In the top zone the placement ALTERNATES — the big moments (a numeral hero, or a hand-spec'd
    # behind cluster) stay behind the head; the phrases between them come forward to the chest band
    # in front of him. `mix: false` in the spec restores the old all-behind behavior.
    mix = bool(spec.get("mix", True)) and zone == "top"
    # spec.finale = {"top": "...", "bottom": "..."} pins the closing pair (the top line stays on screen the
    # entire time and the bottom one is drawn under it): the top phrase
    # renders behind the head, the bottom one in front on the chest, and neither is clamped away by the
    # cluster that follows -- both hold to the cut.
    finale = spec.get("finale") or {}
    def _is(phrase_words, name):
        want = " ".join(tok(w) for w in str(finale.get(name, "")).split() if tok(w))
        got = " ".join(tok(w["text"]) for w in phrase_words)
        return bool(want) and want == got
    top_zone = {"x0": int((1080 - cl_w) / 2) - 8, "x1": int((1080 + cl_w) / 2) + 8, "y0": spec.get("top_y0", 200), "y1": 1000}
    # the QC safe box (G4) a row must stay inside: the room-check box, widened by the plan's own top-zone box when the
    # zone is "top" (reel_qc counts ink inside ANY of the declared boxes)
    SAFE_BOX_X = (SAFE["x0"], SAFE["x1"])
    TOP_BOX_X = (min(SAFE["x0"], top_zone["x0"]), max(SAFE["x1"], top_zone["x1"]))
    # --- caption devices: every gate resolved ONCE, off unless the spec key says otherwise (switches(), shared with the soft build).
    ov = spec.get("device_overrides") or {}
    NEG_SET, STRIKE_ADD, PLACES_TOK, COUNT_ON, PIN_ON, LOGO_ON, ICON_ON, ICON_MAP, CHIP_NEG = switches(spec)
    # GATE.md `front: "phrase"` / `behind: "phrase"` (a prop behind the speaker can show through a letter up top; in front of him his body covers it)
    # -- the cluster holding the phrase takes that lane.
    FRONT_ADD = [tuple(tok(x) for x in str(s).split()) for s in (ov.get("front_add") or [])]
    BEHIND_ADD = [tuple(tok(x) for x in str(s).split()) for s in (ov.get("behind_add") or [])]
    GLOW_ON = bool(spec.get("hero_glow", False)) and bool(ov.get("glow", True))
    GLOW_PX = int((spec.get("look") or {}).get("glow_px", 14) or 14)   # near-glow radius; the far halo is 40/14 of it
    HERO_ADD = frozenset(t for x in (ov.get("hero_add") or []) for t in map(tok, str(x).split()) if t)
    HERO_DROP = frozenset(t for x in (ov.get("hero_drop") or []) for t in map(tok, str(x).split()) if t)
    pin_candidates = []   # [(at, row_dict)], throttled to one per 6s AFTER every phrase is tagged
    icon_candidates = []  # [(at, row_dict, icon_name)], throttled to one per 4s the same way
    W = [w for w in words if not w.get("filler")]
    used = set(); events = []; windows = []; whips = []
    # --- hand-specified behind clusters (hero moments above/behind the head) ---
    for b in ([] if no_behind else spec.get("behind", [])):
        toks = [tok(x) for x in b["match"].split()]
        for i in range(len(W) - len(toks) + 1):
            if [tok(w["text"]) for w in W[i:i + len(toks)]] == toks:
                seg = W[i:i + len(toks)]; used.update(id(w) for w in seg)
                start = seg[0]["start"]; end = min(cut_s, start + b.get("hold", 1.2))
                rows = []
                for r in b["rows"]:
                    text, px, col, dx = r; text = casef(text, case); col = accent if col == "accent" else col
                    wi = next((w for w in seg if tok(w["text"]) == tok(text.split()[0])), seg[0])
                    row = {"text": text, "at": wi["start"], "size": fit(text, px, MAXW), "color": col, "dx": dx, "weight": 800, "hero": True}
                    # glow is a pure function of "is this row colored accent" -- it
                    # applies here too, a hand-specified hero moment is still a hero moment.
                    if GLOW_ON and col == accent:
                        row["glow"] = True
                    rows.append(row)
                ev = {"mode": "behind", "cluster": True, "words": rows, "start": start, "end": round(end, 3),
                      "y": b.get("top", SAFE["y0"]), "stack": "column", "leading": 0.84, "exit": "blur", "maxW": b.get("maxW", MAXW)}
                events.append(ev)
                windows.append([max(0, int(start * FPS) - 2), last_matte_frame(end, cut_s, total_frames)])
                wh = whip(int(round(start * FPS)))
                if wh: whips.append(wh)
                break
    # --- automatic front clusters for everything else ---
    rest = [w for w in W if id(w) not in used]
    ps = phrases_around(rest, spec.get("layout_spans"))
    fronts = []; lifted = []   # indices of chest clusters that had to go above the head (no room under the chin)
    no_room = []               # onsets of clusters with no legal place at all: the gate warns, nothing is drawn onto the face
    for k, p in enumerate(ps):
        if all(tok(w["text"]) in STOP or _weak(tok(w["text"])) for w in p):   # glue-only phrase ("over here at", "that's gonna be a"): one quiet row, no fake hero
            text = casef(" ".join(clean(w["text"]) for w in p), case)
            gcl = SAFE_BOX_X if zone != "top" else TOP_BOX_X
            px = fit(text, row_px[1], min(cl_w, row_cap(540 + cl_dx, *gcl)), face="running")
            # the face at the phrase's ONSET is not the face for the whole phrase (the same lesson the hero clusters learned below): a speaker who
            # settles or leans drops his chin 40 px inside a second, and a quiet row placed from the first frame sat 14 px over his beard by the
            # phrase's middle (G4, 8 px of ink). Place against the LOWEST chin, and up top against the HIGHEST crown, across the row's own life.
            g_start, g_end = p[0]["start"], p[-1]["end"] + 0.30
            faces = [facetrack.at(track, tt) for tt in (g_start, (g_start + g_end) / 2, min(g_end, g_start + 1.5))]
            if zone == "top" and not (mix and k % 3 == 2):
                top = int(max(top_zone["y0"], min(c["cy"] - c["h"] * 0.8 for c in faces) - px - 20))
                fronts.append({"mode": "behind", "cluster": True, "words": [{"text": text, "at": p[0]["start"], "size": px, "color": WHITE, "dx": 0, "weight": 800, "hero": False}],
                               "start": round(p[0]["start"], 3), "end": p[-1]["end"] + 0.30, "y": top, "stack": "column", "leading": 0.84, "exit": "blur", "maxW": cl_w, "safe": top_zone})
            else:
                chin = max(c["cy"] + c["h"] / 2 for c in faces)
                top = int(chin + chin_gap + 40)
                if top + px + 48 > SAFE["y1"]:         # no room for the usual gap: the largest row that fits directly under the chin, never past the safe box
                    top = int(chin + MIN_GAP); px = int(min(px, SAFE["y1"] - top - 48))
                    if px < MIN_ROW: px = MIN_ROW; no_room.append(round(p[0]["start"], 2))
                fronts.append({"mode": "front", "cluster": True, "words": [{"text": text, "at": p[0]["start"], "size": px, "color": WHITE, "dx": 0, "weight": 800, "hero": False}],
                               "start": round(p[0]["start"], 3), "end": p[-1]["end"] + 0.30, "y": top, "leading": 0.84, "exit": "blur", "maxW": cl_w})
            continue
        h = hero_index(p, HERO_ADD, HERO_DROP)
        # logo (the README, "Pacing"): a platform name anywhere in the phrase
        # FORCES that word to be the hero -- the one device that overrides hero_index()'s own
        # choice instead of only tagging whatever row it already picked (a short trigger like
        # "IG"/"FB"/"X" would otherwise never win the longest-word contest). Checked before the
        # pre/hero/post split below so everything downstream (is_neg, strike, count, spaced) reads
        # off the FINAL hero.
        logo_i, logo_name = _logo_index(p) if LOGO_ON else (None, None)
        if logo_i is not None:
            h = logo_i
        pre = [clean(w["text"]) for w in p[:h]]; hero = clean(p[h]["text"]); post = [clean(w["text"]) for w in p[h + 1:]]

        # --- caption devices: decide the hero row's tags from the RAW phrase tokens,
        # before casef()/fit() ever touch the text. neg beats strike (a row is one or the other,
        # never both); count is skipped on a neg row; spaced/pin are independent of both.
        hero_tok = tok(hero)
        prev_tok = tok(clean(p[h - 1]["text"])) if h > 0 else ""
        # "not X" / "no X" chips X only when X closes the phrase ("that's not luck"); a longer tail
        # ("no hidden fees ever") is a rejected THING and reads as a strike instead (below).
        is_neg = hero_tok in NEG_SET or (prev_tok in ("not", "no") and h == len(p) - 1)
        # icon: eligibility decided here, off the single-word hero, same timing as
        # is_neg above -- a numeral hero is never icon-eligible (count already owns that row); the
        # pin/logo/neg exclusion ("a row carries at most one of pin / logo / icon") is enforced
        # below, off place_hits_hero/logo_i/is_neg directly rather than re-reading the row's dict,
        # so it is correct regardless of pin's own later 6s-throttle survival.
        icon_name = None
        if ICON_ON and not re.search(r"\d", hero):
            icon_name = ICON_MAP.get(hero_tok) or ("dollar" if "$" in hero else None)
        # PLACES_TOK is sorted longest-entry-first, but the FIRST entry that matches ANYWHERE in
        # the phrase is not necessarily the one that covers the hero -- "West Palm Beach, Florida"
        # matches BOTH "West Palm Beach" (pre, doesn't touch the hero "Florida") and "Florida"
        # (IS the hero); only a span that actually covers h counts as "the row holding the place
        # is the hero". Among spans that do cover h, the longest-first order still
        # picks the most specific one first.
        place_spans = [sp for sp in (_span_of(p, e) for e in PLACES_TOK) if sp is not None]
        place_span = next((sp for sp in place_spans if sp[0] <= h < sp[1]), None)
        place_hits_hero = place_span is not None
        strike_spans = [sp for sp in (_span_of(p, e) for e in STRIKE_ADD) if sp is not None]
        forced_strike = any(sp[0] <= h < sp[1] for sp in strike_spans)
        # A strike crosses out a THING the speaker rejects ("we don't do long term contracts", "no
        # hidden fees"), never a verb ("don't understand"): the trigger must be followed by a doing
        # verb (or be a bare "no"/"without"), the hero must not itself be a verb, and the struck row
        # is the WHOLE tail after the trigger(+verb), as one row -- the reference strikes the phrase.
        strike_from = None
        for i, w in enumerate(p[:h]):
            if tok(clean(w["text"])) in STRIKE_TRIGGERS:
                j = i + 1
                if j < len(p) and tok(clean(p[j]["text"])) in STRIKE_VERBS: j += 1
                elif tok(clean(w["text"])) not in ("no", "without"): continue
                if j <= h and hero_tok not in VERB_HEROES: strike_from = j
                break
        if forced_strike and strike_from is None:
            strike_from = next(sp[0] for sp in strike_spans if sp[0] <= h < sp[1])
        auto_strike = strike_from is not None
        hero_dev = {}
        if is_neg:
            hero_dev["neg"] = True
        elif auto_strike:
            hero_dev["strike"] = True
            pre = [clean(w["text"]) for w in p[:strike_from]]
            hero = " ".join(clean(w["text"]) for w in p[strike_from:]); post = []
            h = strike_from
        if logo_i is not None:
            hero_dev["logo"] = logo_name
        if COUNT_ON and "neg" not in hero_dev:
            cval = _count_value(hero)
            if cval is not None:
                hero_dev["count"] = cval
        if place_hits_hero:
            hero_dev["spaced"] = True

        rows = []
        if pre: rows.append((casef(" ".join(pre), case), row_px[0], WHITE, -110, p[0]["start"], "running", False))
        is_num = bool(re.search(r"\d", hero))
        col = CHIP_NEG if is_neg else (accent if (is_num or k % 2 == 0) else WHITE)
        rows.append((casef(hero, case), hero_max, col, 0 if not (pre and post) else 20, p[h]["start"], "display", True))
        if post: rows.append((casef(" ".join(post), case), row_px[1], WHITE, 110, p[h + 1]["start"], "running", False))
        # fit every row, then size the block. A row is measured with its own face (the brand's running face for the
        # non-hero rows) and held inside the QC safe box: centerd at 540 + its dx, with EDGE_MARGIN px to spare.
        box_x = TOP_BOX_X if zone == "top" else SAFE_BOX_X
        fitted = []
        for text, px, c, dx, at, face, is_hero in rows:
            cap = row_cap(540 + dx + cl_dx, *box_x)
            px2 = fit(text, px, min(cl_w - abs(dx) * 1.6, cap), face=face)
            if width(text, px2, 800, face) > cap:   # still past the safe box at the usual 60 px floor: go smaller rather than spill
                px2 = fit(text, px2, cap, face=face, floor=44)
            fitted.append({"text": text, "at": at, "size": px2, "color": c, "dx": dx + cl_dx, "weight": 800, "hero": is_hero})
        hero_row = fitted[1] if pre else fitted[0]
        hero_row.update(hero_dev)
        if GLOW_ON and hero_row["color"] == accent:
            hero_row["glow"] = True
        if place_hits_hero and PIN_ON:
            pin_row = fitted[0] if pre else hero_row
            pin_candidates.append((pin_row["at"], pin_row))
        # icon: skip a numeral hero (handled above via icon_name being None already) and a row
        # that already carries pin/logo/neg -- checked off the same early booleans is_neg/logo_i/
        # place_hits_hero used everywhere else in this function, not off hero_row's own keys (pin
        # itself is only decided later, in the throttle pass below).
        if icon_name and not is_neg and logo_i is None and not place_hits_hero:
            icon_candidates.append((hero_row["at"], hero_row, icon_name))
        # glyph box: overlapping rows + the last row at full height, plus the drop shadow (12 px down, 26 px blur)
        block_h = sum(r["size"] for r in fitted[:-1]) * 0.84 + fitted[-1]["size"] + 48
        f = facetrack.at(track, p[0]["start"]); chin = f["cy"] + f["h"] / 2; crown = f["cy"] - f["h"] * 0.8
        start = p[0]["start"]; end = p[-1]["end"] + 0.30
        is_num_hero = bool(re.search(r"\d", fitted[1 if pre else 0]["text"]))   # pre/hero/post are ROWS: the hero row is 1 when a pre row exists
        # alternate lanes: even clusters (and any numeral hero) stay BEHIND up top, odd ones come
        # FORWARD to the chest band in front of him. A front cluster is sized down — the chest
        # budget is the narrow centerd one (CL_W), not the top zone's full width.
        # the front lane is every THIRD cluster, so the
        # eye stays up top for two beats before it drops.
        go_behind = (zone == "top") and (not mix or k % 3 != 2 or is_num_hero)
        fin_top, fin_bot = _is(p, "top"), _is(p, "bottom")
        if fin_top: go_behind = True
        elif fin_bot: go_behind = False
        lane_front = any(_span_of(p, e) is not None for e in FRONT_ADD)
        lane_behind = any(_span_of(p, e) is not None for e in BEHIND_ADD)
        if lane_front: go_behind = False        # sized like a mix front cluster below; it does NOT hold to the cut
        elif lane_behind: go_behind = True
        front_w = CL_W if (mix or fin_bot or lane_front) else cl_w   # the width budget the renderer holds a front cluster's rows to (the event's maxW)
        if (mix or fin_bot or lane_front) and zone == "top" and not go_behind:
            for r in fitted:
                r["size"] = int(r["size"] * 0.62); r["dx"] = int((r["dx"] - cl_dx) * 0.7) + CL_DX
            block_h = sum(r["size"] for r in fitted[:-1]) * 0.84 + fitted[-1]["size"] + 48
        # The renderer never draws a row bigger than its own cap for this lane's width (short_hero_cap: 320 px behind the head at the renderer's own factor, 370 in a narrower face): plan the block at the size that
        # is drawn, so a hero_max above the cap no longer leaves a block floating above the head or shrinks it for room it does not need. Under the cap nothing moves.
        if hold_to_renderer(fitted, cl_w if go_behind else front_w, glyph):
            block_h = sum(r["size"] for r in fitted[:-1]) * 0.84 + fitted[-1]["size"] + 48
        if go_behind:
            # A top-zone cluster is anchored once, at the phrase's own onset -- but a long phrase over
            # real footage (a six-word line with its onset at 3.44s) can run 1.5-2s, long enough for a subject who moves during it to rise
            # well above where the onset sample put his crown. G4 caught the result directly: 9
            # swept frames of caption ink sitting on his face at 4.53-5.07s, the matte only ever
            # hiding what actually is his silhouette, never a block that has outgrown its room.
            # Fix: sample the crown at the START, MIDPOINT and END of the cluster's own span (not
            # just onset) and design the block against the SMALLEST of those (the highest the head
            # gets, i.e. the least clearance) -- and if the block, sized purely from text-fit,
            # cannot fit inside that worst-case room without dipping past the intended h*0.30 margin,
            # SHRINK every row proportionally (floor 0.6x, so a lockup never collapses to something
            # unreadable) rather than silently letting it hang lower into a face that has moved up.
            crown_at = [facetrack.at(track, t) for t in (start, (start + end) / 2, min(end, start + 1.5))]
            worst_crown = min(c["cy"] - c["h"] * 0.8 for c in crown_at)
            worst_h = next(c["h"] for c in crown_at if c["cy"] - c["h"] * 0.8 == worst_crown)
            room = worst_crown + worst_h * 0.30 - top_zone["y0"]   # px available between top_y0 and the tightest crown
            if block_h > room > 0:
                shrink = max(0.6, room / block_h)
                for r in fitted: r["size"] = int(r["size"] * shrink)
                block_h = sum(r["size"] for r in fitted[:-1]) * 0.84 + fitted[-1]["size"] + 48
            # placement uses the SAME worst-case crown the sizing above just fit against -- using
            # the (looser) onset crown here would place a correctly-shrunk block too low again,
            # right back into the room the shrink was just computed to stay clear of.
            top = int(max(top_zone["y0"], worst_crown + worst_h * 0.30 - block_h))   # bottom row dips into the crown; the matte hides it
            fronts.append({"mode": "behind", "cluster": True, "words": fitted, "start": round(start, 3), "end": end, "y": top,
                           "stack": "column", "leading": 0.84, "exit": "blur", "maxW": cl_w, "safe": top_zone,
                           **({"_hold": True} if fin_top else {})})
        else:
            # a glow hero throws a soft halo ~40 px (x glowPx/14) past its glyphs; G4 counts that halo
            # as ink, so a front cluster with glow sits that much lower under the chin (and shrinks
            # below if the box runs out) rather than letting the halo climb onto the face.
            glow_room = int(GLOW_PX * 40 / 14) if any(r.get("glow") for r in fitted) else 0
            # the chin at the phrase's ONSET is not the chin for the whole phrase: a speaker who leans
            # in (MCA sampler, "And that's where this", 14.6 s) drops his chin ~110 px onto a row placed
            # from the first frame. Place against the LOWEST chin across the cluster's own span.
            chin_at = [facetrack.at(track, t) for t in (start, (start + end) / 2, min(end, start + 1.5))]
            chin = max(c["cy"] + c["h"] / 2 for c in chin_at)
            top = int(chin + chin_gap + glow_room)
            if top + block_h > SAFE["y1"]:
                over = top + block_h - SAFE["y1"]
                if over <= 90: top = int(top - over)             # a hair too tall: nudge it up
                else:                                            # genuinely too tall: shrink, keep it below the chin
                    k2 = max(SHRINK_FLOOR, (SAFE["y1"] - top) / block_h)
                    sizes0 = [r["size"] for r in fitted]
                    for r in fitted: r["size"] = int(r["size"] * k2)
                    block_h = sum(r["size"] for r in fitted[:-1]) * 0.84 + fitted[-1]["size"] + 48
                    if top + block_h > SAFE["y1"]:
                        # Even at the floor the block does not fit under the chin (a head low in the frame:
                        # MCA sampler, chin at y 1162 of 1920). The old code then slid it UP onto the face --
                        # G4 caught 65 frames of ink on the chin. The reference puts these ABOVE the head, in
                        # front, when the room is up there: use the highest crown across the span and, if the
                        # unshrunk block fits between the safe top and that crown, restore the sizes and go up.
                        crown = min(c["cy"] - c["h"] * 0.8 for c in chin_at)
                        for r, s0 in zip(fitted, sizes0): r["size"] = s0
                        block_h = sum(r["size"] for r in fitted[:-1]) * 0.84 + fitted[-1]["size"] + 48
                        if crown - 24 - block_h >= SAFE["y0"]:
                            top = int(crown - 24 - block_h); lifted.append(len(fronts))
                        else:
                            # The block fits nowhere at its own size. A phrase of two or more rows stays a lockup, scaled down as one block until it fits (scale_lockup: under the chin
                            # in this take's own lane, above the head, or under the chin at the least gap, every row at MIN_ROW or more); the shape and the hero's lead over the
                            # other rows are the lockup's, so it reads as one of the phrases around it, not as a line of small type. Only a lockup that cannot be drawn at
                            # MIN_ROW anywhere on this frame (a face filling the top of it) falls back to one row.
                            placed = scale_lockup(fitted, front_w, glyph, chin, crown, chin_gap, glow_room) if len(fitted) > 1 else None
                            if placed is not None:
                                top = placed[0]   # not counted in `lifted`: the one-lane-per-take rule below keeps firing exactly when it did, so no other phrase of the take moves
                            else:
                                # no room anywhere: ONE row carrying the whole phrase, as big as the room under the chin allows (the block adds 48 px
                                # for the shadow), never above the chin line. Not even MIN_ROW fits: the row still goes under the chin and the gate warns.
                                hero = next(r for r in fitted if r.get("hero"))
                                size = min(hero["size"], SAFE["y1"] - (chin + MIN_GAP) - 48)
                                if size < MIN_ROW: size = MIN_ROW; no_room.append(round(start, 2))
                                fitted[:] = [{"text": casef(" ".join(clean(w["text"]) for w in p), case), "at": fitted[0]["at"], "size": int(size), "color": hero["color"],
                                              "dx": hero["dx"], "weight": 800, "hero": True}]
                                top = int(chin + MIN_GAP)
                    else:
                        top = int(min(chin + chin_gap, SAFE["y1"] - block_h))
            fronts.append({"mode": "front", "cluster": True, "words": fitted, "start": round(start, 3), "end": end, "y": top,
                           "leading": 0.84, "exit": "blur", "maxW": front_w,
                           **({"_hold": True} if fin_bot else {})})
    # One lane per take: when a third or more of the chest clusters had to go above the head, the take's
    # chest band is simply too tight for this style (a head low in the frame) -- lift EVERY automatic
    # front cluster up there rather than bouncing the eye between lanes. A cluster that does not fit above stays where it was.
    if zone != "top" and fronts and len(lifted) >= max(2, len(fronts) // 3):
        for i, e in enumerate(fronts):
            if i in lifted or e.get("_hold"): continue
            cs = [facetrack.at(track, t) for t in (e["start"], (e["start"] + e["end"]) / 2, min(e["end"], e["start"] + 1.5))]
            crown = min(c["cy"] - c["h"] * 0.8 for c in cs)
            block_h = sum(r["size"] for r in e["words"][:-1]) * 0.84 + e["words"][-1]["size"] + 48
            if crown - 24 - block_h >= SAFE["y0"]: e["y"] = int(crown - 24 - block_h)
    # pin: one per 6 seconds, first wins. spaced itself is never throttled -- only the
    # glyph is, so a second place name within the window still gets its accent->base letter-spread,
    # it just doesn't also drop a second pin icon on screen seconds after the first.
    last_pin_at = None
    for at, row in sorted(pin_candidates, key=lambda pr: pr[0]):
        if last_pin_at is None or at - last_pin_at >= 6.0:
            row["pin"] = True
            last_pin_at = at
    # icon: one per 4 seconds, first wins (the README, "Pacing") -- the same
    # throttle shape as pin above, just a tighter window (icon triggers are commoner than places).
    last_icon_at = None
    for at, row, name in sorted(icon_candidates, key=lambda ic: ic[0]):
        if last_icon_at is None or at - last_icon_at >= 4.0:
            row["icon"] = name
            last_icon_at = at
    # clamp each front cluster to the next one's onset (one front cluster on screen at a time)
    for i, e in enumerate(fronts):
        nxt = fronts[i + 1]["start"] if i + 1 < len(fronts) else cut_s
        e["end"] = round(min(e["end"], nxt - 1 / FPS, cut_s), 3)
        if i + 1 < len(fronts) and fronts[i + 1]["start"] - e["end"] >= 0.20 - 1 / FPS:   # a real pause -> whip into the next cluster
            wh = whip(int(round(fronts[i + 1]["start"] * FPS)), 0.8)
            if wh: whips.append(wh)
    if fronts: fronts[-1]["end"] = round(cut_s, 3)
    for e in fronts:                      # the finale pair holds to the cut and leaves on it, together
        if e.pop("_hold", False): e["end"] = round(cut_s, 3); e["fade"] = 0
    if zone == "top":
        # a phrase spoken while a hand-specified hero is still holding up top drops to the chest band in FRONT
        # (the reference stacks exactly like this: big word behind the head, the next phrase lower and in front)
        heroes = [e for e in events if e["mode"] == "behind"]
        for e in fronts:   # a cluster already up when a hero lands hands the top zone over: it ends at the hero's onset
            later = [h["start"] for h in heroes if e["start"] < h["start"] < e["end"]]
            if later: e["end"] = round(min(later) - 1 / FPS, 3)
        for e in fronts:
            if any(h["start"] - 1e-6 <= e["start"] < h["end"] for h in heroes):
                f = facetrack.at(track, e["start"]); chin = f["cy"] + f["h"] / 2
                block_h = sum(r["size"] for r in e["words"][:-1]) * 0.84 + e["words"][-1]["size"] + 48
                for r in e["words"]: r["size"] = int(r["size"] * 0.72)          # chest band is narrower than the top zone
                block_h *= 0.72
                e["mode"] = "front"; e.pop("stack", None); e.pop("safe", None); e["maxW"] = CL_W
                for r in e["words"]: r["dx"] = int(r["dx"] * 0.7) + CL_DX
                e["y"] = int(min(chin + chin_gap, SAFE["y1"] - block_h))
    events += fronts
    events.sort(key=lambda e: e["start"])
    if zone == "top":
        # only the BEHIND clusters need the person drawn over them; with mixing that is no longer the
        # whole section, but one contiguous window is cheaper to reason about than many small ones.
        beh = [e for e in events if e["mode"] == "behind"]
        windows = [[max(0, int(min(e["start"] for e in beh) * FPS) - 2),
                    last_matte_frame(max(e["end"] for e in beh), cut_s, total_frames)]] if beh else []
    whips = sorted({w["at"]: w for w in whips}.values(), key=lambda w: w["at"])[:10]
    return plan_doc(spec, total_frames, events, windows, whips, zone, len(no_room), top_zone if zone == "top" else None)
def plan_doc(spec, total_frames, events, windows, whips, zone, no_room, safe=None):
    """plan.json as both planners write it: the brand's fonts and colors as the composition reads them (camelCase, each only when the brand sets
    it), the person matte's windows, the whip cuts, the events and the flags (cluster_plan, the zone, no_caption_room:<n> when n lockups found no
    room, style:<id> when the take chose a style), and the top zone's own safe box when there is one."""
    look = spec.get("look") or {}
    WHITE = look.get("base") or "#FFFFFF"
    def face(v):   # a spec font ({family, faces, measure}) -> the composition's font prop (family + the files to load)
        return {"family": v["family"], "faces": v["faces"]}
    return {"src": "", "font": face(spec["font"]),
            "durationInFrames": total_frames, "accent": spec["accent"],
            "base": look.get("base", WHITE), "heroBase": look.get("hero_base", look.get("base", WHITE)), "chip": bool(look.get("chip", False)),
            "heroShadow": look.get("hero_shadow", "0 12px 26px rgba(0,0,0,0.45), 0 2px 6px rgba(0,0,0,0.30)"),
            "textOpacity": float(look.get("text_opacity", 0.96)),
            **({"textSoften": float(look["text_soften"])} if look.get("text_soften") else {}),
            **({"heroStroke": look["hero_stroke"]} if look.get("hero_stroke") else {}),
            **({"wordFont": face(look["word_font"])} if look.get("word_font") else {}),
            # caption-device plan fields: camelCase, only when set -- an absent brand
            # field adds nothing here at all.
            **({"chipNeg": look["chip_neg"]} if look.get("chip_neg") else {}),
            **({"heroGlow": True} if spec.get("hero_glow") else {}),
            **({"heroTint": look["hero_tint"]} if look.get("hero_tint") else {}),
            **({"glowPx": int(look["glow_px"])} if look.get("glow_px") else {}),
            **({"ctaWash": look["cta_wash"]} if look.get("cta_wash") else {}),
            **({"pageBg": look["page_bg"]} if look.get("page_bg") else {}),
            **({"pageInk": look["page_ink"]} if look.get("page_ink") else {}),
            **({"displayFont": face(look["display_font"])} if look.get("display_font") else {}),
            **({"displayWeight": int(look["display_weight"])} if look.get("display_weight") else {}),
            **({"pillBg": look["pill_bg"]} if look.get("pill_bg") else {}),
            **({"pillInk": look["pill_ink"]} if look.get("pill_ink") else {}),
            # your look's measured width factors for the renderer's cap (spec_adapter.py): only a look whose face differs from the renderer's own numbers has them
            **({"glyph": spec["glyph"]} if spec.get("glyph") else {}),
            "matte": {"dir": "", "windows": windows}, "whips": whips, "events": events,
            "flags": ["cluster_plan", f"zone:{zone}"] + ([f"no_caption_room:{no_room}"] if no_room else []) + ([f"style:{spec['caption_style']}"] if spec.get("caption_style") else []),
            **({"safe": safe} if safe else {})}
def _main():
    a = sys.argv[1:]; words, track, out = a[0], a[1], a[2]
    spec = json.load(open(a[a.index("--spec") + 1])) if "--spec" in a else {}
    cut = float(a[a.index("--cut") + 1]) if "--cut" in a else None
    frames = int(a[a.index("--frames") + 1]) if "--frames" in a else None
    with open(words) as f: W = json.load(f)
    with open(track) as f: T = json.load(f)
    if not W.get("words"): raise EngineStop("cluster_plan: words.json has no words to caption")
    for i, w in enumerate(W["words"]):
        if not all(k in w for k in ("text", "start", "end")):
            raise EngineStop(f"cluster_plan: word {i} in words.json lacks text, start or end; re-run the hear stage")
    cut = cut or W["words"][-1]["end"] + 0.3; frames = frames or int(cut * FPS)
    plan = build(W["words"], T, spec, cut, frames)
    with open(out, "w") as f: json.dump(plan, f, indent=1)
    print(len([e for e in plan["events"] if e["mode"] == "front"]), "front clusters,", len([e for e in plan["events"] if e["mode"] == "behind"]), "behind,", len(plan["whips"]), "whips, windows", plan["matte"]["windows"])
if __name__ == "__main__":
    cli(_main, "cluster_plan.py <words.json> <facetrack.json> <out.plan.json> [--spec spec.json] [--cut S] [--frames N]")
