"""The soft build's splitter: a take's timed words become thoughts, each a lead-in line, a payoff line and an optional tail line, so a
caption reads as one thought and not as a run of auto captions. The split is a shortest path over the word boundaries. Stdlib only: the
font measure comes in as a callable, measure(text, px, role) -> width in px for role "lead", "pay" or "tail" (look.font_measure makes one).

A word is {"text", "start", "end", "stop"} plus, when it has them, "comma", "neg" (a red word), "mark" ("accent" or "tier2", a designer
mark), "struck" (on the first word of a run a strike crosses out: the run's length in words), "place" (on the first word of a place name
the brand lists: its length in words), "logo" (a platform name, drawn with its mark), "glue" (no boundary right after it: a strike's
trigger before its doing verb) and "hard_end" (an interruption starts right after it: the second it starts, as end_at sets it, or True
when only the word's own end is known). A missing comma is read from the text by sentences.py's rule (comma_after). A thought is
{"lead", "pay", "tail", "bar", "short", "lead_px", "pay_px"}: the lists are word indices in order and together hold every word once. A
single line is a lead-in with no payoff (pay_px 0), and the tail draws at lead_px.

The hard rules: a lead-in is 2 to 6 words, a payoff 1 to 5, a tail 2 to 5, a single line up to 7 and a thought up to 14; each line fits
its width at its floor; a thought is never only function words and a payoff never is; no boundary falls right after a glued word, between
a number and its unit, inside a run of capitalized words (a name) or right after a word that needs the next one (an article, a possessive
or a preposition that takes an object) unless that word carries a comma or a stop, save that a lead-in may end on such a word right
before a struck run whose whole run is its payoff ("We did it all without | shortcuts.") or, when the word is a preposition, before a
place name that is exactly its payoff and ends its phrase ("Everything we make ships from | Florida."), and nowhere else; no thought runs
past a hard end, nor across a silence longer than SILENCE_S that has a content word on each side of it as far as the next such end (a
thought lingers at most LINGER_S after its last word and is gone LEAVE_S later, so the one before such a silence has left before the next
word, which arrives in a thought of its own). Small words alone on one side of such a silence ("So", "and") join the words across it
instead of standing alone, and the small words after the last stop before one cross it with the words after it ("We booked them in. |
And [pause] then the phone"), never across a hard end. A thought leaves for the next one's first word, for what a hard end after it allows,
or LINGER_S after its last word ends, whichever comes first, save that the last one keeps to a cut that comes within SILENCE_S of its last
word (timing), and its reading time is read to that same leave. A thought that cannot be read in time (on screen 1.0 s, its payoff's
first word opaque 0.4 s) is marked short instead, and one that ends at a hard end gets a payoff only when it can be read in time: before
an interruption a thought that cannot is a single line. The words between two of these ends, a stretch, split on their own, and words
that cannot make a thought of their own are held by a neighbouring thought of their stretch: the one before them ("we booked it. and the"
before an interruption) or the one after ("of the leads | we got that week." right after one). When no split of a stretch keeps the rules
(a lone "of" between two interruptions, a word too wide for any line), the words that break one stand as single lines marked short.

The pulls, hard but under reading time: of a thought's ways that keep the rules, those read in time come first, and among them a way whose
payoff holds a red word when the thought holds one, then one whose payoff starts at a struck run's first word and holds the run, then
one whose payoff is exactly a place name the thought holds whole and that ends its phrase there (no word after it, a hard end or a stop
or a comma on its last word, or a function word next, whatever thought that word falls in), then one whose payoff holds a platform
name, then one whose payoff holds a designer mark; the cost only chooses among what is left. When two devices share one thought this
order decides: red, then strike, then place, then logo, then mark. A pull no way can keep is passed over. A strike right at a sentence's
start ("No hidden fees") cannot be kept: its lead-in would be the one word "No"; nor can a place whose lead-in would run past six words
("We are a print shop based in West Palm Beach.") or would end on an article or a possessive ("This is the Florida we grew up in."). A
place that runs on into the noun after it names that noun and is not pulled at all ("We are a Florida print shop.", "We get orders from
Florida companies every day."), even where a pause ends its thought right after it, and neither is one before a content word ("made in
Florida every week."). A pull acts inside one thought only: the path picks the thought boundaries by cost, so a neighbouring split that
would keep a pull is not sought, and in this build no path-level weight counts a missed pull.

The cost, lower is better (the weights are the constants below): THOUGHT for each thought; LENGTH for each word a thought is away from
LENGTH_AIM; STOP_INSIDE for each sentence end inside a thought before its last word; AFTER for the boundary after a thought, at a sentence
end, at a comma or a pause of PAUSE_S or more, before a clause opener, or anywhere else; TAIL for a tail line. Taken off for the payoff:
ENDS when it ends its thought, NUMBER when it holds a number, MARKED when it holds a designer mark or a red word, OPENS when it opens
with an article, a determiner or a subject pronoun, and HERO when it is one word, the thought's longest content word, with a tail after
it. Of the splits that keep the rules the one with the fewest short thoughts wins, then the cheapest, then the one with fewer thoughts,
then the one whose boundaries come first."""
import re
from collections.abc import Callable

from . import look, sentences

Measure = Callable[[str, int, str], float]

_ARTICLES = "a an the"
_PREPOSITIONS = ("about above across after against along among around at before behind below beneath beside besides between beyond by "
                 "despite down during except for from in inside into like near of off on onto out outside over past per since than through "
                 "throughout till to toward towards under underneath unlike until up upon via with within without")
_CONJUNCTIONS = "and but or nor so yet because cause if unless while whereas although though whether when whenever where wherever that then"
_PRONOUNS = ("i me my mine myself you your yours yourself yourselves he him his himself she her hers herself it its itself we us our ours "
             "ourselves they them their theirs themselves this these those who whom whose which what whatever whoever someone somebody "
             "something anyone anybody anything there")
_AUXILIARIES = ("am is are was were be been have has had do does did can could will would shall should may might must gonna gotta wanna "
                "i'm you're we're they're he's she's it's that's there's here's what's who's where's how's i've you've we've they've i'll "
                "you'll we'll they'll it'll that'll i'd you'd we'd they'd it'd isn't aren't wasn't weren't don't doesn't didn't can't "
                "couldn't won't wouldn't shouldn't haven't hasn't hadn't let's")
# The narrow function word list: a thought is never only these, and a payoff never only these. Not the hero planner's STOP or WEAK_HERO
# lists, which would reject the approved "that goes into it": "goes", "spend" and "time" carry meaning here.
FUNCTION = frozenset(" ".join((_ARTICLES, _PREPOSITIONS, _CONJUNCTIONS, _PRONOUNS, _AUXILIARIES)).split())
# Words that need the next one: an article, a possessive and a preposition that takes an object. The prepositions most often a verb's
# particle ("show up", "come out", "look over") are left out. "about", "behind", "in" and "on" stay, though each can be a particle too,
# so a line runs on past one where it is ("they walk in and").
_LEANS = frozenset(("a an the my your his its our their about against among at behind beside besides between beyond despite during "
                    "except for from in into of on onto per than throughout to toward towards unlike upon via with within without").split())
_PREPOSITION_WORDS = frozenset(_PREPOSITIONS.split())   # a place's lead-in may end on one of these right before it, never on an article or a possessive
# Words that open a noun phrase or a clause, an article, a determiner or a subject pronoun (a contraction counts by its first part): a
# payoff that opens with one stands as a phrase of its own.
_OPENS = frozenset("a an the this that these those my your his her its our their i you he she we they it".split())
_CLAUSE = frozenset("and but so because then which when if".split())       # a thought may end before one of these
_NUMBER_WORDS = frozenset("zero two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
                          "eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion".split())
_I = frozenset("i i'm i've i'll i'd".split())                               # capitalized everywhere, so never a sign of a name
_CONTRACTION = re.compile(r"'(s|re|ve|ll|d|m)$")

# The cost weights, named in the module notes. At these values the reference windows (tests/captions/softbuild/fixtures/approved_windows.json) split as intended.
THOUGHT = 1.0
LENGTH, LENGTH_AIM = 0.15, 7
STOP_INSIDE = 4.0
AFTER = {"stop": 0.0, "comma": 0.6, "clause": 1.2, "other": 2.0}
PAUSE_S = 0.25
TAIL = 0.8
ENDS, NUMBER, MARKED, OPENS, HERO = 0.6, 0.8, 1.0, 0.6, 1.5

READ_S = 1.04        # on screen 1.0 s: a thought arrives 0.05 s before its first word and starts to leave 0.09 s before the next one's
PAY_READ_S = 0.627   # the payoff's first word opaque 0.4 s before the leave: it is opaque 0.187 s into an arrival that starts 0.05 s early
SILENCE_S = look.LINGER_S + look.LEAVE_S   # a gap longer than this, with content words on both sides, ends a thought: the one before has left
MOST_WORDS, ONE_LINE = 14, 7                                    # the most words in a thought, and in a thought drawn as one line
LEAD_LEAST, LEAD_WORDS, PAY_WORDS, TAIL_LEAST, TAIL_WORDS = 2, 6, 5, 2, 5
_EPS = 1e-9


class ThoughtError(ValueError):
    """Lines a person wrote that cannot stand as thoughts (parse_lines): words that do not match, two bars, a line of two words or more too
    wide at its floor. The message is one plain line naming the word or the line."""


def split(words: list[dict], measure: Measure, pipeline: str, next_start: float | None = None) -> list[dict]:
    """The thoughts of a stream of timed words: the best split under the module's rules and cost. pipeline is "feed" or "engine" (its
    widths and floors); next_start is when the word after these starts (None: nothing follows, so the last thought lingers its LINGER_S
    and is read in time)."""
    return _Words(words, measure, pipeline).split(next_start) if words else []


def pick_bar(thoughts: list[dict], words: list[dict], measure: Measure, pipeline: str) -> int | None:
    """Puts the one bar on the strongest payoff and returns that thought's index, or None when no payoff can take it. A payoff holding a
    designer mark comes first, then one holding a number, then the one with the most letters in its content words, the earlier on a tie.
    A payoff with a red word never takes it (red and the accent never share a line), nor one too wide for the bar at the payoff floor.
    The pick's payoff is refitted to the bar's width; every other thought loses its bar and gets its payoff's own size back."""
    known = _Words(words, measure, pipeline)
    best = None
    for n, t in enumerate(thoughts):
        pay = t["pay"]
        if t["bar"] and pay:
            t["pay_px"] = known.fit("pay", pay[0], pay[-1] + 1)
        t["bar"] = False
        if not pay or any(words[k].get("neg") for k in pay):
            continue
        px = known.fit("pay", pay[0], pay[-1] + 1, known.widths["bar"])
        if px is None:
            continue
        rank = (any(words[k].get("mark") in ("accent", "tier2") for k in pay), any(known.number[k] for k in pay),
                sum(_letters(words[k]["text"]) for k in pay if not known.function[k]))
        if best is None or rank > best[0]:
            best = (rank, n, px)
    if best is None:
        return None
    _, n, px = best
    thoughts[n]["bar"], thoughts[n]["pay_px"] = True, px
    return n


def red_left_off(thought: dict, words: list[dict]) -> list[int]:
    """The indices of a thought's red words (neg) that its payoff does not hold, in order. Red is drawn only on a payoff, so these draw in their line's own
    color, the brand's text color, as the words around them do, and both planners name each in a CHECK line (the caption chain only those a person or
    your look named). A single line has no payoff, so every red word in it is one."""
    pay = set(thought["pay"])
    return [k for k in thought["lead"] + thought["pay"] + thought["tail"] if words[k].get("neg") and k not in pay]


def hard_leave(word: dict) -> float | None:
    """The latest a thought that ends on this word may leave for, or None when the word is no hard end. A thought starts to leave LEAVE_LEAD
    before the time it leaves for and is gone LEAVE_S after that, so a hard end that carries its interruption's time (end_at) leaves for that
    time less LEAVE_S - LEAVE_LEAD: the thought has left by the interruption itself. A hard end of True, with no time, leaves for the word's own
    end."""
    hard = word.get("hard_end")
    if hard is True:
        return word["end"]
    if isinstance(hard, bool) or not isinstance(hard, (int, float)):
        return None
    return hard - (look.LEAVE_S - look.LEAVE_LEAD)


def last_word_cut_off(word: dict, exit_at: float) -> bool:
    """Whether an interruption cuts off a thought's last word: the word is a hard end, and the thought starts to leave at exit_at before the word is
    fully opaque (its arrival, which begins ARRIVE_EARLY before it is spoken, reaches its opaque point OPAQUE_AT of ARRIVE_S in only then or later).
    The reading time (_Words.reads) times only a thought's first word and its payoff's, so such a word is never read: both planners mark the thought
    and say so in a CHECK line. A last word the next thought follows in ordinary speech is never cut off: the two thoughts crossfade, the approved
    look."""
    return bool(word.get("hard_end")) and word["start"] - look.ARRIVE_EARLY + look.OPAQUE_AT * look.ARRIVE_S >= exit_at


def timing(thoughts: list[dict], words: list[dict], until: float) -> list[dict]:
    """When each thought is on screen, {"first", "start", "exitAt", "end"} in seconds, both planners' one timing. first is the spoken start of its
    first word, and it starts to arrive ARRIVE_EARLY before that, never before 0. It starts to leave (exitAt) at the first of: LEAVE_LEAD
    before the next thought's first word (the last one: on until, the cut or the reel's own end), LEAVE_LEAD before what its last word's hard
    end allows when that word is one (hard_leave: gone by the interruption), and LINGER_S after its last word ends, so it never holds over
    the silence after its words. The last one keeps to until when that comes within SILENCE_S (LINGER_S + LEAVE_S) of its last word, so a
    cut right after the words never catches it mid-fade. It is gone (end) LEAVE_S after it starts to leave, never past until. This is the
    leave the splitter reads a thought's reading time by (_Words.reads), so what it checked is what draws. The times are rounded to the
    millisecond."""
    out = []
    for n, t in enumerate(thoughts):
        last = (t["tail"] or t["pay"] or t["lead"])[-1]
        leave = words[thoughts[n + 1]["lead"][0]]["start"] if n + 1 < len(thoughts) else until + look.LEAVE_LEAD
        if (hard := hard_leave(words[last])) is not None:
            leave = min(leave, hard)
        first, exit_at = words[t["lead"][0]]["start"], leave - look.LEAVE_LEAD
        if n + 1 < len(thoughts) or until - words[last]["end"] > SILENCE_S + _EPS:
            exit_at = min(exit_at, words[last]["end"] + look.LINGER_S)
        exit_at = round(exit_at, 3)
        out.append({"first": first, "start": round(max(0.0, first - look.ARRIVE_EARLY), 3), "exitAt": exit_at,
                    "end": round(min(exit_at + look.LEAVE_S, until), 3)})
    return out


def format_line(thought: dict, words: list[dict]) -> str:
    """One thought as a line a person can edit: `lead-in | payoff | tail`, ` [bar]` at the end of the bar's line, no `|` for a single
    line. The words' own text, case untouched."""
    parts = [thought["lead"]] + ([thought["pay"]] if thought["pay"] else []) + ([thought["tail"]] if thought["tail"] else [])
    line = " | ".join(" ".join(words[k]["text"] for k in part) for part in parts)
    return line + " [bar]" if thought["bar"] else line


def parse_lines(lines: list[str], words: list[dict], measure: Measure | None = None, pipeline: str | None = None,
                next_start: float | None = None) -> list[dict]:
    """The thoughts a person wrote, one line each in format_line's grammar. Together the lines hold every word once, in order, exactly as
    spoken; a ThoughtError names the first word that does not match. With a measure and its pipeline each line is sized as split sizes
    it and a part too wide at its floor is refused, save a line of one word, which is drawn at the floor as split's rescue draws it;
    without one the sizes are 0. short is the reading time, read as split reads it. Blank lines are skipped, and counted in the line
    numbers the errors give."""
    if measure is not None and pipeline is None:
        raise ValueError("thoughts: parse_lines needs the pipeline to size the lines")
    known = _Words(words, measure, pipeline)
    parsed, at, bar_row = [], 0, None
    for row, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        bar = line.endswith("[bar]")
        parts = line[:-len("[bar]")].split("|") if bar else line.split("|")
        if len(parts) > 3:
            raise ThoughtError(f"thoughts: line {row} has {len(parts)} parts; a thought is lead-in | payoff | tail")
        groups = []
        for part in parts:
            got, at = _claim(part, words, at, row)
            groups.append(got)
        if bar and len(groups) == 1:
            raise ThoughtError(f"thoughts: line {row} puts the bar on a line with no payoff")
        if bar and bar_row:
            raise ThoughtError(f"thoughts: lines {bar_row} and {row} both carry [bar]; the bar marks one payoff")
        bar_row = row if bar else bar_row
        parsed.append((row, groups + [[]] * (3 - len(groups)), bar))
    if at < len(words):
        raise ThoughtError(f"thoughts: {words[at]['text']!r} at {words[at]['start']:.2f} s is in no line")
    out = []
    for row, (lead, pay, tail), bar in parsed:
        i, j = lead[0], (tail or pay or lead)[-1] + 1
        short = not known.reads(i, pay[0] if pay else None, j, next_start)
        lead_px = pay_px = 0
        if measure is not None:
            lead_px, pay_px = known.size(row, lead, pay, tail, bar)
        out.append(_thought(lead, pay, tail, short, lead_px, pay_px, bar))
    return out


def end_at(words: list[dict], edges) -> None:
    """Sets hard_end on the last word that starts before each edge (in seconds), where an interruption starts, so no thought runs across it: the
    engine's layout events, the feed's scene windows. hard_end is the edge's own time, so the thought that ends there is gone by it (hard_leave);
    two edges between the same two words leave the earlier. A word that starts on an edge, within a microsecond, belongs to the stretch after it,
    and an edge before the first word ends nothing."""
    for edge in edges:
        before = [k for k, w in enumerate(words) if w["start"] + 1e-6 < edge]
        if before:
            word = words[before[-1]]
            had = word.get("hard_end")
            timed = isinstance(had, (int, float)) and not isinstance(had, bool)
            word["hard_end"] = min(had, float(edge)) if timed else float(edge)


def line_of(words: list[dict], idx: list[int], role: str, case: str) -> tuple[str, list[float]]:
    """(text, ats) of one line of a thought as both pipelines draw it: the text of words idx joined by spaces, punctuation kept, cased for its
    role (look.line_case), and the spoken start of each word it draws; a token with a space in it (a name like "Big Idea") is two words
    arriving together."""
    return look.line_case(" ".join(words[k]["text"] for k in idx), role, case), [words[k]["start"] for k in idx for _ in words[k]["text"].split()]


def crossing(lines: list[str], thoughts: list[dict], words: list[dict]) -> tuple[int, int] | None:
    """(the line number, the word) of the first of a person's lines whose thought (parse_lines' thoughts of those lines) runs on past a word
    that carries a hard end, or None. An interruption ends a thought in the split, so a planner refuses such a line in its own words. Blank
    lines count in the line numbers, as parse_lines counts them."""
    rows = (n for n, line in enumerate(lines, 1) if line.strip())
    for row, t in zip(rows, thoughts):
        k = next((k for k in (t["lead"] + t["pay"] + t["tail"])[:-1] if words[k].get("hard_end")), None)
        if k is not None:
            return row, k
    return None


def _claim(part, words, at, row):
    """The indices of the words one part of a line holds, from word `at` on, matched by their exact text. Returns them and where the
    next part starts."""
    got, rest = [], part.strip()
    if not rest:
        raise ThoughtError(f"thoughts: line {row} has an empty part")
    while rest:
        said = rest.split()[0]
        if at >= len(words):
            raise ThoughtError(f"thoughts: line {row} has {said!r} after the last word")
        text = words[at]["text"]
        if not (rest == text or rest.startswith(text) and rest[len(text)].isspace()):
            raise ThoughtError(f"thoughts: line {row} has {said!r} where the words say {text!r} ({words[at]['start']:.2f} s)")
        got.append(at)
        at, rest = at + 1, rest[len(text):].lstrip()
    return got, at


def is_function(text: str) -> bool:
    """Whether a word is one of the narrow function words (FUNCTION) as the splitter reads it, or holds no letter or digit at all."""
    k = _key(text)
    return k in FUNCTION or not re.search(r"[a-z0-9]", k)


def _thought(lead, pay, tail, short, lead_px, pay_px, bar=False):
    return {"lead": list(lead), "pay": list(pay), "tail": list(tail), "bar": bar, "short": short, "lead_px": lead_px, "pay_px": pay_px}


def _key(text):
    """A word as the word lists know it: lower case, curly apostrophes straightened, only letters, digits and apostrophes kept."""
    return re.sub(r"[^a-z0-9']", "", text.lower().replace("\u2019", "'")).strip("'")


def _letters(text):
    return sum(c.isalpha() for c in text)


class _Words:
    """One stream of words, read once: each word's class, where a boundary may and must fall, and each line's fitted size. Boundary k
    falls between word k - 1 and word k."""

    def __init__(self, words, measure, pipeline):
        self.words, self.measure, self.n = words, measure, len(words)
        self.widths = look.widths(pipeline) if pipeline else None
        self.lead_floor = look.LEAD_FLOOR[pipeline] if pipeline else None
        self.key = [_key(w["text"]) for w in words]
        self.function = [is_function(w["text"]) for w in words]
        self.number = [bool(re.search(r"\d", w["text"])) or k in _NUMBER_WORDS for w, k in zip(words, self.key)]
        self.stop = [bool(w["stop"]) for w in words]
        self.comma = [bool(w["comma"]) if "comma" in w else sentences.comma_after(w["text"]) for w in words]
        self.hard = [bool(w.get("hard_end")) for w in words]
        self.red = [bool(w.get("neg")) for w in words]
        self.struck = [min(int(w.get("struck") or 0), PAY_WORDS) for w in words]   # a struck run's length, on its first word
        self.place = [int(w.get("place") or 0) for w in words]                     # a place name's length, on its first word
        self.logo = [bool(w.get("logo")) for w in words]
        self.glue = [bool(w.get("glue")) for w in words]
        self.designer = [w.get("mark") in ("accent", "tier2") for w in words]
        self.marked = [d or r for d, r in zip(self.designer, self.red)]
        self.capital = [bool(re.match(r"[^A-Za-z]*[A-Z]", w["text"])) and k not in _I for w, k in zip(words, self.key)]
        self.ends = self._stretch_ends()
        self.forced = [False] + self.ends[:-1] + [True]
        self.open = [True] + [self._may_split(k) for k in range(1, self.n)] + [True]
        # a lead-in may also end right before a struck run's first word, or a place name's first word after a preposition, where only the lean
        # closes that boundary ("without | shortcuts.", "ships from | Florida."), and only when its payoff holds the whole run or is exactly a place
        # that ends its phrase (layout); every other boundary there stays as open() has it, so no line ends on the "a" of "a Florida print shop."
        self.run_open = [False] + [bool(self.struck[k] or self.place[k] and self.key[k - 1] in _PREPOSITION_WORDS) and self._may_split(k, lean=False)
                                   for k in range(1, self.n)] + [False]
        self.fits = {}

    def _stretch_ends(self):
        """Whether a thought always ends after each word, so the words after it start a stretch of their own: after a hard end, always; at a
        silence longer than SILENCE_S, only when the words on each side of it, as far as the next such end, hold a content word (one that is not
        a function word), so a lone small word beside a pause ("So", "and") joins the words across it instead of standing alone, and never across
        a hard end. Small words after the last stop before such a silence cross it with the words after it, so the stretch ends at the stop:
        "We booked them in. | And [pause] then the phone". The sides are read from the start of the words, a silence crossed joining its side to
        the one before it."""
        quiet = [k for k in range(self.n - 1) if not self.hard[k] and self.words[k + 1]["start"] - self.words[k]["end"] > SILENCE_S + _EPS]
        cuts = sorted(set(quiet) | {k for k in range(self.n) if self.hard[k]} | ({self.n - 1} if self.n else set()))
        ends, start = list(self.hard), 0
        for at, k in enumerate(cuts):
            if self.hard[k] or k == self.n - 1:
                start = k + 1
                continue
            right = range(k + 1, cuts[at + 1] + 1)
            if not (any(not self.function[i] for i in range(start, k + 1)) and any(not self.function[i] for i in right)):
                continue                                          # the small words on one side join the words across the silence
            stop = next((i for i in range(k, start - 1, -1) if self.stop[i]), None)
            if stop is not None and stop < k and all(self.function[i] for i in range(stop + 1, k + 1)):
                k = stop                                          # the small words after the last stop go on with the words after the silence
            ends[k], start = True, k + 1
        return ends

    def _may_split(self, k, lean=True):
        """Whether a boundary may fall before word k: always where a stretch ends (ends: a hard end, or a long silence with content words on
        both sides) or at a comma or a stop; never right after a glued word, between a number and its unit, inside a run of capitalized words
        (a name) or, with lean, right after a word that needs the next one."""
        if self.ends[k - 1] or self.stop[k - 1] or self.comma[k - 1]:
            return True
        if self.glue[k - 1]:
            return False
        if self.number[k - 1] and not self.function[k]:
            return False
        return not (self.capital[k - 1] and self.capital[k]) and not (lean and self.key[k - 1] in _LEANS)

    def fit(self, role, lo, hi, maxw=None):
        """The size words lo to hi draw at as a line of this role (a payoff against maxw when given), or None when they are too wide
        even at the role's floor."""
        key = (role, lo, hi, maxw)
        if key not in self.fits:
            text = " ".join(self.words[k]["text"] for k in range(lo, hi))
            if role == "pay":
                top, floor, maxw = (look.HERO_PX if hi - lo == 1 else look.PAY_PX), look.PAY_FLOOR, maxw or self.widths["pay"]
            else:
                top, floor, maxw = look.LEAD_PX, self.lead_floor, self.widths[role]

            def measure(t, px):
                return self.measure(t, px, role)

            px = look.fit(text, top, maxw, floor, measure)
            self.fits[key] = None if px == floor and measure(text, floor) > maxw else px
        return self.fits[key]

    def size(self, row, lead, pay, tail, bar):
        """(lead_px, pay_px) of a line a person wrote, or a ThoughtError naming the part too wide at its floor. A line of one word too wide for
        any line is drawn at the floor, as the split's rescue draws it: there is nothing to split it into."""
        lead_px = self.fit("lead", lead[0], lead[-1] + 1)
        if lead_px is None and len(lead) == 1 and not pay:
            lead_px = self.lead_floor
        if lead_px is None:
            raise ThoughtError(f"thoughts: line {row}'s lead-in is too wide at {self.lead_floor} px; split it")
        pay_px = 0
        if pay:
            pay_px = self.fit("pay", pay[0], pay[-1] + 1, self.widths["bar"] if bar else None)
            if pay_px is None:
                raise ThoughtError(f"thoughts: line {row}'s payoff is too wide{' for the bar' if bar else ''} at {look.PAY_FLOOR} px; split it")
        if tail:
            tail_px = self.fit("tail", tail[0], tail[-1] + 1)
            if tail_px is None:
                raise ThoughtError(f"thoughts: line {row}'s tail is too wide at {self.lead_floor} px; split it")
            lead_px = min(lead_px, tail_px)
        return lead_px, pay_px

    def reads(self, i, a, j, next_start):
        """Whether the thought of words i to j, its payoff from word a (None: no payoff), is read in time, to the leave timing gives it. It
        leaves for the first of: the next thought's first word (next_start after the last word; None, nothing follows), what a hard end right
        after it allows (hard_leave), and LINGER_S + LEAVE_LEAD after its last word ends (it starts to leave LINGER_S after that word at the
        latest), save that the last thought keeps to next_start when the cut it stands for comes within SILENCE_S of its last word."""
        then = self.words[j]["start"] if j < self.n else next_start
        leave = self.words[j - 1]["end"] + look.LINGER_S + look.LEAVE_LEAD
        if then is not None and j == self.n and then - look.LEAVE_LEAD - self.words[j - 1]["end"] <= SILENCE_S + _EPS:
            leave = then
        for limit in (then, hard_leave(self.words[j - 1])):
            if limit is not None:
                leave = min(leave, limit)
        return leave - self.words[i]["start"] >= READ_S - _EPS and (a is None or leave - self.words[a]["start"] >= PAY_READ_S - _EPS)

    def ends_phrase(self, b):
        """Whether the words before word b end their phrase, read from the word after them whatever thought it falls in: no word after them, a hard end
        or a stop or a comma on word b - 1, or a function word next. A place that runs on into the noun after it ("a Florida print shop") names that
        noun and ends none, even where a pause ends a thought between them."""
        return b == self.n or self.hard[b - 1] or self.stop[b - 1] or self.comma[b - 1] or self.function[b]

    def after(self, j):
        """The cost of a thought ending before word j (nothing at the end of the words)."""
        if j == self.n:
            return 0.0
        if self.stop[j - 1]:
            return AFTER["stop"]
        if self.comma[j - 1] or self.words[j]["start"] - self.words[j - 1]["end"] >= PAUSE_S:
            return AFTER["comma"]
        return AFTER["clause"] if self.key[j] in _CLAUSE else AFTER["other"]

    def base(self, i, j):
        """The cost of words i to j as a thought, before its payoff."""
        return THOUGHT + LENGTH * abs(j - i - LENGTH_AIM) + STOP_INSIDE * sum(self.stop[i:j - 1]) + self.after(j)

    def layout(self, i, j, next_start):
        """The best way to draw words i to j as one thought, (short, cost, a, b, lead_px, pay_px) with the payoff a to b and the tail b to
        j (a == b == j for a single line), or None when no way keeps the rules. Before a hard end a payoff is offered only when it can be
        read in time, so a thought there that cannot is a single line. Of the ways that keep the rules, one read in time comes first, then
        the pulls in their order: a payoff holding a red word when the thought holds one, one that starts a struck run and holds it when
        the thought holds the run's first word, one that is exactly a place name when the thought holds a whole one that ends its phrase
        (ends_phrase), one holding a platform name when the thought holds one, one holding a designer mark when the thought holds one (a
        single line keeps none of them); then the cheapest. Cost is in millionths, so ties are exact."""
        content = [k for k in range(i, j) if not self.function[k]]
        if not content:
            return None
        longest = max(_letters(self.words[k]["text"]) for k in content)
        base = self.base(i, j)
        red, logo, designer = any(self.red[i:j]), any(self.logo[i:j]), any(self.designer[i:j])
        runs = [(s, self.struck[s]) for s in range(i, j) if self.struck[s]]
        places = [(s, self.place[s]) for s in range(i, j) if self.place[s] and s + self.place[s] <= j and self.ends_phrase(s + self.place[s])]
        best = rank = None

        def offer(got):
            nonlocal best, rank
            a, b = got[2], got[3]
            misses = (red and not any(self.red[a:b]), bool(runs) and not any(a == s and b >= s + n for s, n in runs),
                      bool(places) and not any(a == s and b == s + n for s, n in places),
                      logo and not any(self.logo[a:b]), designer and not any(self.designer[a:b]))
            key = (got[0],) + misses + (got[1],)
            if rank is None or key < rank:
                best, rank = got, key

        if j - i <= ONE_LINE and (px := self.fit("lead", i, j)) is not None:
            offer((not self.reads(i, None, j, next_start), round(base * 1e6), j, j, px, 0))
        for a in range(i + LEAD_LEAST, min(i + LEAD_WORDS, j - 1) + 1):
            run_only = not self.open[a] and self.run_open[a]      # a lead-in ending on a lean word: only before a whole struck run or place
            if not (self.open[a] or run_only) or (lead_px := self.fit("lead", i, a)) is None:
                continue
            for b in range(a + 1, min(a + PAY_WORDS, j) + 1):
                if 0 < j - b < TAIL_LEAST or j - b > TAIL_WORDS or b < j and not self.open[b] or all(self.function[k] for k in range(a, b)):
                    continue
                if run_only and not (self.struck[a] and b >= a + self.struck[a] or (a, b - a) in places and self.key[a - 1] in _PREPOSITION_WORDS):
                    continue
                if (short := not self.reads(i, a, j, next_start)) and self.hard[j - 1]:
                    continue
                if (pay_px := self.fit("pay", a, b)) is None or b < j and (tail_px := self.fit("tail", b, j)) is None:
                    continue
                cost = base + (TAIL if b < j else -ENDS)
                cost -= NUMBER * any(self.number[a:b]) + MARKED * any(self.marked[a:b])
                cost -= OPENS * (_CONTRACTION.sub("", self.key[a]) in _OPENS)
                if b < j and b - a == 1 and not self.function[a] and _letters(self.words[a]["text"]) >= longest:
                    cost -= HERO
                offer((short, round(cost * 1e6), a, b, min(lead_px, tail_px) if b < j else lead_px, pay_px))
        return best

    def split(self, next_start):
        """The thoughts, stretch by stretch: a hard end always ends a thought, and so does a silence longer than SILENCE_S with content words on
        both sides (ends), so the words between two of them split on their own. In a stretch that cannot split under the rules the words that
        break one stand as single lines marked short."""
        out, start = [], 0
        for end in [k for k in range(1, self.n + 1) if self.forced[k]]:
            out += self.path(start, end, next_start) or self.path(start, end, next_start, rescue=True)
            start = end
        return out

    def path(self, s, e, next_start, rescue=False):
        """The best split of words s to e, which hold no hard end and no silence that ends a stretch, or None when none keeps the rules. Per
        boundary it keeps the best (words in lines that break a rule, thoughts missing the reading time, cost, thoughts, boundaries) of the words
        before it. With rescue any one to seven words may also stand as a single line that breaks a rule, marked short, so a split always
        exists."""
        best, back = {s: (0, 0, 0, 0, ())}, {}
        for j in range(s + 1, e + 1):
            for i in range(max(s, j - MOST_WORDS), j):
                if i not in best:
                    continue
                ways = []
                if self.open[i] and self.open[j] and (got := self.layout(i, j, next_start)) is not None:
                    ways.append((0, got))
                if rescue and j - i <= ONE_LINE:
                    ways.append((j - i, (True, round(self.base(i, j) * 1e6), j, j, self.fit("lead", i, j) or self.lead_floor, 0)))
                stray, shorts, cost, count, ends = best[i]
                for loose, got in ways:
                    key = (stray + loose, shorts + got[0], cost + got[1], count + 1, ends + (j,))
                    if j not in best or key < best[j]:
                        best[j], back[j] = key, (i, got)
        if e not in best:
            return None
        out, j = [], e
        while j > s:
            i, (short, _, a, b, lead_px, pay_px) = back[j]
            out.append(_thought(range(i, a), range(a, b), range(b, j), short, lead_px, pay_px))
            j = i
        return out[::-1]
