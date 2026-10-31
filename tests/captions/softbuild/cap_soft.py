"""cap_soft.py: what the soft build's tests share: the two reference windows (fixtures/approved_windows.json), the font measure a window is split with, and
the plain measure the random-stream tests use. Import it as `cap_soft` from any test in tests/captions/ (the root conftest puts this folder's
parent on sys.path; the soft build tests sit beside this file).

A window is a short stretch of plain speech written for these tests, with the splits the splitter (softbuild/thoughts.py) records for it. Both are measured
with a font the repo ships: "varsans" is the variable Inter (mixed case), "upstatic" the static Inter ExtraBold (capitals), the two fixture looks."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
FIXTURES = os.path.join(HERE, "fixtures")


def even(text, px, role):
    """a measure that makes every character 0.56 of the size wide, whatever the role"""
    return len(text) * px * 0.56


def _load():
    with open(os.path.join(FIXTURES, "approved_windows.json"), encoding="utf-8") as fh:
        return json.load(fh)["windows"]


WINDOW_LIST = _load()
WINDOWS = {w["name"].split(",")[0].lower(): w for w in WINDOW_LIST}      # "varsans", "upstatic": the fixture looks' own ids


def real_measure(window):
    """measure(text, px, role) in the window's own font file (softbuild look.font_measure): the repo's Inter, resolved from the repo root"""
    from softbuild import look
    return look.font_measure(os.path.join(REPO, window["font"]), window["display_weight"], window["variable"], window["case"])


def spans(window):
    """[(first word, past the last word)] of each thought the window records, in order: the words of each `expect` entry counted off its text"""
    out, at = [], 0
    for want in window["expect"]:
        text = want["group"] if "group" in want else want["line"].replace(" | ", " ")
        out.append((at, at + len(text.split())))
        at += len(text.split())
    assert at == len(window["words"]), "the recorded thoughts do not cover the window's words"
    return out


def part(window, first, last=None):
    """(the words of recorded thoughts `first` to `last` inclusive, when the word after them starts): the next word of the window, or its next_start"""
    last = first if last is None else last
    lo, hi = spans(window)[first][0], spans(window)[last][1]
    words = window["words"]
    return words[lo:hi], (words[hi]["start"] if hi < len(words) else window["next_start"])
