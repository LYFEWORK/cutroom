"""The soft build's sentence ends: the `stop` (the word ends a sentence) and `comma` (a comma follows it) flags the splitter in thoughts.py
reads on every word. They come from the take's own punctuation. A take whose words carry no sentence end at all (whisper returned none on
a transcriber that left them out) takes them from Deepgram's punctuated words instead, paired with the take's words in order. The shown text never
changes: mark returns copies and adds these two keys only. Stdlib only."""
import difflib
import re
from collections.abc import Iterator

_CLOSERS = "\"')\u201d\u2019"        # what may close a quote or a bracket after a word's last mark: "Yes." and (so,)
_OPENERS = "\"'(\u201c\u2018"        # what may open one before a word: "Dr." and (etc.)
_ENDS = (".", "?", "!")
# words whose period is not a sentence end; the video editor takes this set for its sentence case, so there is one list
ABBREVIATIONS = frozenset({"dr.", "mr.", "mrs.", "ms.", "vs.", "etc.", "st.", "a.m.", "p.m.", "u.s."})


def has_stops(words: list[dict]) -> bool:
    """Whether any word ends a sentence: it carries a stop flag, or its text ends in . ? or ! (a closing quote or bracket may follow) and
    is not an abbreviation such as "Dr." or "p.m."."""
    return any(_stop(w) for w in words)


def mark(words: list[dict], dg_words: list[dict] | None = None) -> list[dict]:
    """Copies of the words with `stop` (ends a sentence) and `comma` (a comma follows) set from each word's own punctuation, and every other
    key, `text` included, as it was. The period of an abbreviation ("Dr.", "p.m.") is not a sentence end, in the take's words or in
    Deepgram's. A flag a word already carries stays. When no word ends a sentence, Deepgram's punctuated words (dg_words, each with `text`)
    give theirs to the words they pair with: the same word in the same order. A Deepgram word with no partner gives nothing, and with no
    dg_words (None, or [] after a failed call) the take stays without a stop."""
    out = [dict(w, stop=_stop(w), comma=_comma(w)) for w in words]
    if dg_words and not has_stops(out):
        for i, j in _pairs(out, dg_words):
            out[i]["stop"] = out[i]["stop"] or _stop(dg_words[j])
            out[i]["comma"] = out[i]["comma"] or _comma(dg_words[j])
    return out


def _stop(word: dict) -> bool:
    """Whether a word ends a sentence: its stop flag, or its last mark is . ? or ! and it is no abbreviation. Both are read on the same core, the word
    without the quotes and brackets around it, so "(etc.)" and Dr." are no sentence end and "(Done.)" is one."""
    if word.get("stop"):
        return True
    core = _bare(word["text"])
    return core.endswith(_ENDS) and core.lstrip().lstrip(_OPENERS).lower() not in ABBREVIATIONS


def _comma(word: dict) -> bool:
    return bool(word.get("comma")) or comma_after(word["text"])


def comma_after(text: str) -> bool:
    """Whether a comma follows a word as written: its last mark is a comma, a closing quote or bracket after it allowed ("so," and (so,)). The splitter
    reads a word with no comma flag by this rule too (thoughts.py)."""
    return _bare(text).endswith(",")


def _bare(text: str) -> str:
    """The text up to its last mark: without the space and the closing quotes and brackets after it."""
    return text.rstrip().rstrip(_CLOSERS)


def _norm(text: str) -> str:
    """A word as the matcher reads it: lower case, only letters, digits and apostrophes (the caption engine's token rule)."""
    return re.sub(r"[^a-z0-9']", "", text.lower())


def _pairs(words: list[dict], dg_words: list[dict]) -> Iterator[tuple[int, int]]:
    """(i, j) for each word i of the take paired with Deepgram's word j: the same token, in the same order, no pairs crossing. difflib's
    autojunk is off: on a list of 200 words or more it would not search on the common words ("the", "and") and could miss the pairs
    that hang on them."""
    matcher = difflib.SequenceMatcher(None, [_norm(w["text"]) for w in words], [_norm(w["text"]) for w in dg_words], autojunk=False)
    for i, j, size in matcher.get_matching_blocks():
        yield from zip(range(i, i + size), range(j, j + size))
