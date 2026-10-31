"""glyph.py: how wide a font draws caption text, as the three width factors the planner and the renderer share, plus the one font question the
planner asks of a file (is it a variable font).

The renderer (CaptionsDynamic.tsx) caps every caption row to its width budget with a per-character width factor, and the planner sizes and places
rows with the same factor, so the two always agree. The renderer's own numbers (GLYPH_CAPS, GLYPH_RUN) are calibrated on a reference face. Any other
face gets its own factors: the reference constant times (this face's measured mean advance / the reference face's measured mean advance for the same
kind of text), so a narrower or wider face is shrunk to fit and never overflows.

The reference face's six mean advances are measured once and written down below (REFERENCE), so no reference font has to ship. They are px per pt per
character over GLYPH_CORPUS at MEASURE_PX, to six places 0.686748 (caps), 0.53271 (running words) and 0.588323 (hook) for mixed case, and 0.686748 for
all three when the writer's case is upper. glyph_factors measures only the writer's own font, so nothing falls back to a number of its own: a font that
cannot be read stops the plan, and says which file.

  glyph_factors(profile)   {"caps", "word", "hook"}: px per pt per char for the profile's own faces, rounded to 4 places
  font_is_variable(path)   True when the file is one variable font (it has an fvar table), so a line can be drawn at any weight
  font_tags(path)          the table tags of a ttf, otf or woff2 file, read from its table directory
"""
import functools
import os

from PIL import ImageFont

from keys import EngineStop

GLYPH_CAPS = 0.75   # px per pt per char of heavy uppercase (heroes, lockup rows, keyword slams) on the reference face, measured at weights 800 and 900 with the W/M-heavy
                    # words highest. Every other face scales it by its own measure (glyph_factors). Keep it equal to GLYPH_CAPS in CaptionsDynamic.tsx.
GLYPH_RUN = 0.58    # the same for running words in lowercase on the reference face, measured at weights 400, 500 and 700. Equal to GLYPH_RUN in CaptionsDynamic.tsx.
GLYPH_HOOK = GLYPH_RUN  # and for a hook line in mixed case at weight 800 on the reference face

# The reference face's mean advances: what the same measurement gives on the face the constants above were calibrated on, for each case a writer may use.
# upper: the running words and the hook are drawn in capitals too, so all three kinds are the heavy-uppercase measure.
REFERENCE = {
    "mixed": {"caps": 0.6867477409638554, "word": 0.5327103413654618, "hook": 0.5883226656626506},
    "upper": {"caps": 0.6867477409638554, "word": 0.6867477409638554, "hook": 0.6867477409638554},
}

# A face's own factors come from measuring its font files with PIL on this one fixed list of everyday caption words. Two in five of them carry a W or an M,
# the widest letters, so the words that run wide are in the sample. The list is part of the calibration: REFERENCE was measured on exactly these words.
GLYPH_CORPUS = tuple("""people never call back today listen comment welcome calendar empty money sleeps here business owner
customers mistake secret truth biggest reason nobody exactly free fast stop wrong leads booked appointments reviews system
growth revenue profit strategy content video social brand local quality service client
women wow web wasting window world memory month market marketing movement management
responsibility accountability understanding professional conversations automation""".split())
MEASURE_PX = 100   # every face is measured at this size; the advance is then per pt per char
CAPS_WEIGHTS, RUN_WEIGHTS, HOOK_WEIGHTS = (800, 900), (400, 500, 700), (800,)   # the weights CaptionsDynamic draws each kind of text at
SAMPLES = {"upper": str.upper, "lower": lambda w: w, "mixed": str.capitalize}    # how a corpus word is written for each kind of text


@functools.lru_cache(maxsize=None)
def _face(path):
    """One font file opened by PIL, once per file: (font, axes). `axes` is PIL's list of variation axes for a variable font and None for a static one, where
    every weight is the one file. A path that is not a file, or that PIL cannot read, stops the plan naming it."""
    if not os.path.isfile(path):
        raise EngineStop(f"glyph: the font {path} is not a file, so its width cannot be measured")
    try:
        font = ImageFont.truetype(path, MEASURE_PX)
    except OSError as e:
        raise EngineStop(f"glyph: the font {path} is not a font PIL can read ({e}), so its width cannot be measured") from e
    try:
        return font, font.get_variation_axes()
    except OSError:
        return font, None


def axis_value(axis, weight):
    """What a variable font's axis is set to: the weight, kept inside the axis's range, on the weight axis, and the font's own default on any other
    (an optical-size or width axis must not be set to a weight)."""
    name = axis["name"].decode("latin-1") if isinstance(axis["name"], bytes) else str(axis["name"])
    if name.lower() in ("weight", "wght"):
        return max(axis["minimum"], min(axis["maximum"], weight))
    return axis["default"]


@functools.lru_cache(maxsize=None)
def _advance(path, weight, kind):
    """Mean advance per pt per char of GLYPH_CORPUS written as `kind` (SAMPLES) in one font file at one weight: the total width of the words over their
    total characters. Cached per (real file path, weight, kind), so a face is measured once however often it is asked."""
    font, axes = _face(path)
    if axes:
        font.set_variation_by_axes([axis_value(a, weight) for a in axes])
    words = [SAMPLES[kind](w) for w in GLYPH_CORPUS]
    return sum(font.getlength(w) for w in words) / (MEASURE_PX * sum(len(w) for w in words))


def _mean_advance(files, weights, kind):
    """The mean of _advance over `weights`; `files` maps a weight to a font file, and a weight it lacks takes the nearest one."""
    got = [_advance(files[min(files, key=lambda k: abs(int(k) - w))], w, kind) for w in weights]
    return sum(got) / len(got)


def _face_files(profile, running):
    """{weight: real font path} of the face that draws one kind of text: the running face when `running` and the profile has one, else the display face."""
    wf = profile.get("word_font") if running else None
    found = (wf or {}).get("abs") if wf else profile.get("font_abs")
    if not found:
        raise EngineStop("glyph: the look has no font files on disk (font_abs), so the width of its text cannot be measured")
    return {w: os.path.realpath(p) for w, p in found.items()}


def _measured(profile, upper):
    """Mean advance per pt per char of one profile's faces for the three kinds of text. `upper` (the writer uses capitals) puts the running words and the
    hook in the heavy-uppercase kind; otherwise the running words are lowercase and the hook is mixed case."""
    display, running = _face_files(profile, False), _face_files(profile, True)
    caps = _mean_advance(display, CAPS_WEIGHTS, "upper")
    word = _mean_advance(running, CAPS_WEIGHTS, "upper") if upper else _mean_advance(running, RUN_WEIGHTS, "lower")
    hook = caps if upper else _mean_advance(display, HOOK_WEIGHTS, "mixed")
    return {"caps": caps, "word": word, "hook": hook}


def _reference_factors(upper):
    """The factors the reference face gets, and what CaptionsDynamic falls back to when the plan carries no `glyph` prop."""
    return {"caps": GLYPH_CAPS, "word": GLYPH_CAPS if upper else GLYPH_RUN, "hook": GLYPH_CAPS if upper else GLYPH_HOOK}


def glyph_factors(profile):
    """{"caps", "word", "hook"}: px per pt per char for this profile's own faces. caps sizes the hero, the lockup rows and the keyword slam (the display face
    in capitals); word sizes the running words (the running face when the look has one, capitals when it writes capitals); hook sizes a hook line (the capitals
    factor for a capitals look, else the display face in mixed case). Each is the reference constant times (this look's measured mean advance / the reference
    face's recorded mean for the SAME kind of text). Rounded to 4 places so the planner, the plan file and the renderer hold the same number."""
    upper = profile.get("case", "mixed") == "upper"
    mine, ref = _measured(profile, upper), REFERENCE["upper" if upper else "mixed"]
    return {k: round(v * (mine[k] / ref[k]), 4) for k, v in _reference_factors(upper).items()}


# ------------------------------------------------------------------ the one structural question about a font file

_WOFF2_TAGS = ("cmap head hhea hmtx maxp name OS/2 post cvt fpgm glyf loca prep CFF VORG EBDT EBLC gasp hdmx kern LTSH PCLT VDMX vhea vmtx "
               "BASE GDEF GPOS GSUB EBSC JSTF MATH CBDT CBLC COLR CPAL SVG sbix acnt avar bdat bloc bsln cvar fdsc feat fmtx fvar gvar "
               "hsty just lcar mort morx opbd prop trak Zapf Silf Glat Gloc Feat Sill").split()


def font_tags(path):
    """The table tags of a ttf, otf or woff2 file (read from the table directory, nothing is decompressed); empty if unreadable."""
    try:
        with open(path, "rb") as fh:
            d = fh.read(65536)
    except OSError:
        return set()
    if d[:4] == b"wOF2":
        n, pos, tags = int.from_bytes(d[12:14], "big"), 48, set()

        def base128(i):
            v = 0
            while True:
                b = d[i]
                i += 1
                v = (v << 7) | (b & 0x7F)
                if not b & 0x80:
                    return v, i
        try:
            for _ in range(n):
                flags = d[pos]
                pos += 1
                idx = flags & 0x3F
                if idx == 63:
                    tag = d[pos:pos + 4].decode("latin1")
                    pos += 4
                else:
                    tag = _WOFF2_TAGS[idx]
                _, pos = base128(pos)
                if (tag in ("glyf", "loca")) == ((flags >> 6) == 0):
                    _, pos = base128(pos)
                tags.add(tag)
        except IndexError:
            pass
        return tags
    if d[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        n = int.from_bytes(d[4:6], "big")
        return {d[12 + 16 * i:16 + 16 * i].decode("latin1") for i in range(n) if 16 + 16 * i <= len(d)}
    return set()


def font_is_variable(path):
    """True when the file is a variable font: one file with an fvar table that serves every weight, so a line can be drawn at any weight from 100 to 900.
    False for a static file, which has its one weight, and for a file that cannot be read (the look check names that one)."""
    return "fvar" in font_tags(path)
