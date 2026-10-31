#!/usr/bin/env python3
"""styles.py: the named caption styles, one JSON file each. Stdlib only, Python 3.12+. Importing it needs nothing else of the engine; only the command
line below imports the look module.

Your look (the captions section of your DNA, made into a flat profile by lookprofile.py) is the base of every take. A style is a PARTIAL look laid over it
for one take. It has two blocks:

  "set"   the layout and motion fields: where the words sit, how big they are, how they move (ALLOWED below, 17 fields, each held to the type and
          range of the captions schema)
  "look"  the colors and the font slot this style draws in (LOOK below): accent, text, on_accent, hero_base, hero_tint, hero_shadow, hero_stroke,
          chip_neg, display_weight, and font ("display" or "running": which of your two faces draws the words)

A color or a font in "set" is refused (it belongs in "look"), and so is any key that is not in one of the two lists, so a typo never slips through.
Whatever a style leaves out is your look's own value. The things that name you (id, brand name, handle) and the words you taught the engine
(spelling, do_not_say) never move, and the font files only move when look.font swaps the slot.

  {"id": "keyword-slam", "name": "Keyword slam", "summary": "...",
   "use_when": "one short sentence", "never_when": "one short sentence",
   "preview": null,                      a path under the styles folder to a picture of the look, or null
   "source": {"type": "..."},            where the look came from (an object, or null)
   "opt_in": true, "owner": null,        kept for files that carry them; the engine reads owner only to keep a style private to one DNA id
   "added": "YYYY-MM-DD", "added_for": null, "status": "built",
   "set":  {"zone": "chest", "hero_max": 320},
   "look": {"accent": "#FFD60A", "hero_shadow": "0 3px 18px rgba(0,0,0,0.45)", "font": "running"},
   "feed": {}}

`feed` is an object this chain never reads: the video editor's own captions take their hints from it. It is accepted and left alone.

Adding a style is adding one file: copy one, change `set` and `look` and the two sentences, run `python3 styles.py check`, then `python3 preview_style.py <id>` to
see it, then render a take with it. use_when and never_when are the menu text; the engine never reads them.

Where the library is, first match wins: $CAPTION_STYLES_DIR alone when it is set (the tests use it), else <workspace>/Brand/styles (yours), then
templates/styles (the starters). Your file beats a starter of the same id.

The fields' types and ranges come from templates/schema.json (its captions properties) when that file is there and defines them, else from FIELDS below,
which holds the same numbers.

  styles.check(dir=None)             -> (docs, problems): every valid style, in id order, and one plain line per problem in the library
  styles.load(style_id, dir=None)    the one style, checked (StyleError for an unknown id, naming the known ones)
  styles.usable(doc, owner)          StyleError when the style is private to another DNA id
  styles.apply(profile, doc)         -> a copy of the look's profile with the style laid over it, "caption_style" set. Names and word lists are asserted unchanged
  styles.diff(profile, doc)          -> [(field, before, after)] for the fields the style really changes
  styles.describe(doc)               one line of what the style sets

  python3 styles.py list
  python3 styles.py show <id> [--diff]     the style, and with --diff what it changes in your look
  python3 styles.py check                  every file in the library (exit 1 when any is wrong)

Exit 0 ok, 1 a style problem, 2 usage.
"""
import copy
import datetime
import functools
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ENGINE = os.path.dirname(HERE)
REPO = os.path.dirname(ENGINE)
STARTER_DIR = os.path.join(REPO, "templates", "styles")
SCHEMA = os.path.join(REPO, "templates", "schema.json")
ENV_DIR = "CAPTION_STYLES_DIR"
ENV_SCHEMA = "CAPTION_SCHEMA"

# what a style may set: where the words sit and how big they are, the treatment of the text, the motion between phrases, the highest room-check tier, and
# whether the captions are drawn as the soft build
LAYOUT = ("case", "zone", "width", "top_y0", "hero_max", "row_px", "chin_gap")
TREATMENT = ("chip", "text_opacity", "text_soften", "hero_glow", "glow_px")
MOTION = ("whip_frames", "whip_amount", "count_up")
TIER = ("caption_tier_max",)
SOFT = ("soft_build",)
ALLOWED = LAYOUT + TREATMENT + MOTION + TIER + SOFT
# the look block: colors, the shadow and stroke, the page weight, and which face draws the words. Each maps to one key of the profile.
LOOK = ("accent", "text", "on_accent", "hero_base", "hero_tint", "hero_shadow", "hero_stroke", "chip_neg", "display_weight", "font")
LOOK_PROFILE_KEY = {"accent": "accent", "text": "base", "on_accent": "on_accent", "hero_base": "hero_base", "hero_tint": "hero_tint",
                    "hero_shadow": "hero_shadow", "hero_stroke": "hero_stroke", "chip_neg": "chip_neg", "display_weight": "display_weight"}
FONT_SLOTS = ("display", "running")
TIER_ORDER = ("MINIMAL", "REDUCED", "FULL")           # lowest first: a style can only lower a look's cap, never raise it
KEYS = ("id", "name", "summary", "use_when", "never_when", "preview", "source", "opt_in", "owner", "added", "added_for", "status", "set", "look", "feed")
REQUIRED = ("id", "name", "summary", "use_when", "never_when", "set")
STATUSES = ("built", "to build")                      # "to build": the style waits for the engine to learn a field it needs, and is in no menu
ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
RECORD_KEY = "caption_style"                          # the key a styled profile carries, and what spec.json, plan flags, gate.json and delivery.json record
# what a style may never move: the names and the taught words. The font keys join them unless look.font swaps the slot.
IDENTITY = ("id", "brand", "handle", "spelling", "do_not_say")
FONT_KEYS = ("font", "font_family", "font_files", "font_abs", "font_faces", "fonts_dir", "font_prefix", "word_font", "display_font")

_HEX = {"type": "string", "pattern": r"^#[0-9A-Fa-f]{6}$"}
FIELDS = {
    "case": {"type": "string", "enum": ["upper", "mixed", "title"]},
    "zone": {"type": "string", "enum": ["top", "chest"]},
    "width": {"type": "integer", "minimum": 300, "maximum": 1080},
    "top_y0": {"type": "integer", "minimum": 0, "maximum": 1920},
    "hero_max": {"type": "integer", "minimum": 50, "maximum": 800},
    "row_px": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer", "minimum": 20, "maximum": 400}},
    "chin_gap": {"type": "integer", "minimum": 0, "maximum": 600},
    "chip": {"type": "boolean"},
    "text_opacity": {"type": "number", "minimum": 0, "maximum": 1},
    "text_soften": {"type": "number", "minimum": 0, "maximum": 20},
    "hero_glow": {"type": "boolean"},
    "glow_px": {"type": "integer", "minimum": 0, "maximum": 80},
    "whip_frames": {"type": "integer", "minimum": 0, "maximum": 30},
    "whip_amount": {"type": "number", "minimum": 0, "maximum": 1},
    "count_up": {"type": "boolean"},
    "caption_tier_max": {"type": "string", "enum": ["MINIMAL", "REDUCED", "FULL"]},
    "soft_build": {"type": "boolean"},
    "accent": _HEX, "text": _HEX, "on_accent": _HEX, "hero_base": _HEX, "hero_tint": _HEX, "chip_neg": _HEX,
    "hero_shadow": {"type": "string"},
    "hero_stroke": {"type": "object", "required": ["width", "color"], "additionalProperties": False,
                    "properties": {"width": {"type": "integer", "minimum": 0, "maximum": 40}, "color": _HEX}},
    "display_weight": {"type": "integer", "minimum": 100, "maximum": 900},
    "font": {"type": "string", "enum": list(FONT_SLOTS)},
}


class StyleError(ValueError):
    """a style that cannot be read, found or used: one plain line"""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


# ------------------------------------------------------------------ where the library is

def user_dir():
    """<workspace>/Brand/styles when a workspace is set up, else None. Never stops: a missing setup just means there are no styles of yours."""
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    try:
        import common
        ws = common.load_config(require_workspace=False).get("workspace")
    except (ImportError, SystemExit, OSError, ValueError):
        return None
    return os.path.join(os.path.abspath(os.path.expanduser(ws)), "Brand", "styles") if ws else None


def styles_dirs(path=None):
    """the folders styles are looked for in, best first: the one asked for, else $CAPTION_STYLES_DIR alone, else yours and then the starters"""
    one = path or os.environ.get(ENV_DIR)
    if one:
        return [os.path.abspath(os.path.expanduser(one))]
    return [d for d in (user_dir(), STARTER_DIR) if d and os.path.isdir(d)]


def where(doc):
    """yours, starter or folder: which library a loaded style came from"""
    d = doc.get("_dir")
    return "yours" if d == user_dir() else "starter" if d == STARTER_DIR else "folder"


def _schema_props(path=None):
    """The fields' schemas: FIELDS, each replaced by the one templates/schema.json (or $CAPTION_SCHEMA) defines under captions.properties when it
    defines it with a type."""
    p = path or os.environ.get(ENV_SCHEMA) or SCHEMA
    try:
        stamp = os.path.getmtime(p)
    except OSError:
        stamp = None
    return copy.deepcopy(_read_props(p, stamp))


@functools.lru_cache(maxsize=8)
def _read_props(p, stamp):
    props = copy.deepcopy(FIELDS)
    if stamp is None:
        return props
    try:
        with open(p, encoding="utf-8") as fh:
            theirs = json.load(fh)["properties"]["captions"]["properties"]
    except (OSError, ValueError, KeyError, TypeError):
        return props
    for k in props:
        if isinstance(theirs.get(k), dict) and (theirs[k].get("type") or theirs[k].get("enum")):
            props[k] = theirs[k]
    return props


def _type_ok(v, t):
    if t == "string":
        return isinstance(v, str)
    if t == "integer":
        return not isinstance(v, bool) and (isinstance(v, int) or (isinstance(v, float) and v.is_integer()))
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return True


def _whole(v, sch):
    """v with a whole-number float turned into an int where the schema says integer (JSON 320.0 is the integer 320), so no float reaches a plan's sizes"""
    if isinstance(v, float) and v.is_integer() and sch.get("type") == "integer":
        return int(v)
    if isinstance(v, list) and isinstance(sch.get("items"), dict):
        return [_whole(x, sch["items"]) for x in v]
    if isinstance(v, dict) and isinstance(sch.get("properties"), dict):
        return {k: _whole(x, sch["properties"].get(k, {})) for k, x in v.items()}
    return v


def _check_value(v, sch, path, out):
    """the schema's own rule for one field (type, enum, pattern, range, array of numbers, object), as one plain line when it is broken"""
    t = sch.get("type")
    if t is not None and not _type_ok(v, t):
        out.append(f"{path}: must be {t}, got {type(v).__name__ if v is not None else 'null'}")
        return
    if "enum" in sch and v not in sch["enum"]:
        out.append(f"{path}: {v!r} is not one of {', '.join(map(str, sch['enum']))}")
    if "pattern" in sch and isinstance(v, str) and not re.match(sch["pattern"], v):
        out.append(f"{path}: {v!r} is not in the form {'#RRGGBB' if sch['pattern'] == _HEX['pattern'] else sch['pattern']}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in sch and v < sch["minimum"]:
            out.append(f"{path}: {v} is below {sch['minimum']}")
        if "maximum" in sch and v > sch["maximum"]:
            out.append(f"{path}: {v} is above {sch['maximum']}")
    if isinstance(v, list):
        if len(v) < sch.get("minItems", 0) or ("maxItems" in sch and len(v) > sch["maxItems"]):
            out.append(f"{path}: needs {sch['minItems']} items" if sch.get("minItems") == sch.get("maxItems") else f"{path}: wrong number of items")
        for i, x in enumerate(v):
            if "items" in sch:
                _check_value(x, sch["items"], f"{path}[{i}]", out)
    if isinstance(v, dict):
        for k in sch.get("required", []):
            if k not in v:
                out.append(f"{path}: {k} is missing")
        for k, x in v.items():
            if k in (sch.get("properties") or {}):
                _check_value(x, sch["properties"][k], f"{path}.{k}", out)
            elif sch.get("additionalProperties") is False:
                out.append(f"{path}: {k} is not a known key")


# ------------------------------------------------------------------ one style file

def _line_problem(where_, key, v, what):
    if not isinstance(v, str) or not v.strip():
        return f"{where_}: {key} must be {what}"
    if "\n" in v:
        return f"{where_}: {key} must be one line"
    return None


def problems(doc, filename=None, props=None):
    """[] when a style is sound, else one plain line per problem: unknown keys, a missing line, an id that is not its file name, a field in the wrong block (a
    color or the font in `set`), a value outside the range the schema allows. props: the fields' schemas (read when left out)"""
    where_ = filename or (doc.get("id") if isinstance(doc, dict) and isinstance(doc.get("id"), str) else "style")
    if not isinstance(doc, dict):
        return [f"{where_}: a style must be a JSON object"]
    out = [f"{where_}: {k} is not a known key (known: {', '.join(KEYS)})" for k in doc if k not in KEYS]
    out += [f"{where_}: {k} is missing" for k in REQUIRED if k not in doc]
    sid = doc.get("id")
    if "id" in doc:
        if not isinstance(sid, str) or not ID_RE.match(sid):
            out.append(f"{where_}: id must be lower case words joined by hyphens (keyword-slam)")
        elif filename and sid != os.path.splitext(os.path.basename(filename))[0]:
            out.append(f"{where_}: id {sid} must be the file name (a file called {sid}.json)")
    for k, what in (("name", "a short line"), ("summary", "a short line"), ("use_when", "one short sentence"), ("never_when", "one short sentence")):
        if k in doc:
            bad = _line_problem(where_, k, doc[k], what)
            if bad:
                out.append(bad)
    pv = doc.get("preview")
    if pv is not None and (not isinstance(pv, str) or not pv.strip() or os.path.isabs(pv) or ".." in pv.split("/") or not pv.lower().endswith(IMAGE_EXT)):
        out.append(f"{where_}: preview must be null or an image path inside the styles folder ({', '.join(IMAGE_EXT)})")
    if doc.get("source") is not None and not isinstance(doc.get("source"), dict):
        out.append(f"{where_}: source must be an object or null")
    if "opt_in" in doc and not isinstance(doc["opt_in"], bool):
        out.append(f"{where_}: opt_in must be true or false")
    if "feed" in doc and not isinstance(doc["feed"], dict):
        out.append(f"{where_}: feed must be an object (the video editor reads it, this chain does not)")
    for k in ("owner", "added_for"):
        v = doc.get(k)
        if v is not None and (not isinstance(v, str) or not ID_RE.match(v)):
            out.append(f"{where_}: {k} must be an id or null")
    if "added" in doc and (not isinstance(doc["added"], str) or not DATE_RE.match(doc["added"]) or not _is_date(doc["added"])):
        out.append(f"{where_}: added must be a YYYY-MM-DD date")
    if "status" in doc and doc["status"] not in STATUSES:
        out.append(f"{where_}: status must be one of {', '.join(STATUSES)} (or left out)")
    props = props or _schema_props()
    sets = doc.get("set")
    if "set" in doc:
        if not isinstance(sets, dict):
            out.append(f"{where_}: set must be an object of fields to values")
        else:
            for k, v in sets.items():
                if k not in ALLOWED:
                    hint = "colors, the shadow, the stroke and the font slot go in the `look` block" if k in LOOK else \
                        f"a style's `set` lays layout and motion over your look ({', '.join(ALLOWED)})"
                    out.append(f"{where_}: set.{k} is not a field a style may set: {hint}")
                else:
                    before = len(out)
                    _check_value(v, props[k], f"{where_}: set.{k}", out)
                    if len(out) == before and k == "row_px" and v[0] > v[1]:
                        out.append(f"{where_}: set.row_px: the first number is the smaller row (minimum, then maximum)")
    if "look" in doc:
        look = doc["look"]
        if not isinstance(look, dict):
            out.append(f"{where_}: look must be an object of colors and the font slot")
        else:
            for k, v in look.items():
                if k not in LOOK:
                    hint = "layout and motion go in the `set` block" if k in ALLOWED else f"a style's `look` holds {', '.join(LOOK)}"
                    out.append(f"{where_}: look.{k} is not a field a style may set: {hint}")
                else:
                    _check_value(v, props[k], f"{where_}: look.{k}", out)
    return out


def _is_date(s):
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def read(path):
    """the JSON object in one style file; StyleError names the file for a file that cannot be read or is not JSON"""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as e:
        raise StyleError(f"{os.path.basename(path)}: cannot be read ({e.strerror or e})")
    except ValueError as e:
        raise StyleError(f"{os.path.basename(path)}: is not valid JSON ({e})")


# ------------------------------------------------------------------ the library

def _files(d):
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".json") and not f.startswith("."))


def ids(dir=None):
    """every style id in the library, sorted, a style of yours and a built-in with one id counted once"""
    return sorted({os.path.splitext(f)[0] for d in styles_dirs(dir) for f in _files(d)})


def check(dir=None):
    """-> (docs, problems). docs: every style file that is sound, in id order, each with its folder under "_dir" (not a file key); the first folder in the
    search order that holds an id wins it. problems: one plain line for every file that is not sound, in every folder. A library with no folder is one
    problem. Nothing is raised for a bad file: the library is read whole so one bad style never hides the others"""
    dirs = styles_dirs(dir)
    if not dirs:
        return [], ["there is no caption style library: no folder of styles was found"]
    props, docs, found, seen = _schema_props(), {}, [], set()
    for d in dirs:
        for name in _files(d):
            sid = os.path.splitext(name)[0]
            try:
                doc = read(os.path.join(d, name))
            except StyleError as e:
                found.append(str(e))
                continue
            bad = problems(doc, name, props)
            if bad:
                found += bad
            elif sid not in seen:
                seen.add(sid)
                docs[sid] = {**doc, "_dir": d}
    return [docs[k] for k in sorted(docs)], found


def load_all(dir=None):
    docs, bad = check(dir)
    if bad:
        raise StyleError(bad)
    return docs


def load(style_id, dir=None):
    """the checked style with this id (only its own file is read: a bad file elsewhere in the library never stops a take). StyleError for an id that is not a
    style, naming the known ones"""
    if not isinstance(style_id, str) or not ID_RE.match(style_id):
        raise StyleError(f"{style_id!r} is not a caption style id (lower case words joined by hyphens, like keyword-slam)")
    dirs = styles_dirs(dir)
    for d in dirs:
        path = os.path.join(d, style_id + ".json")
        if os.path.isfile(path):
            doc = read(path)
            bad = problems(doc, style_id + ".json")
            if bad:
                raise StyleError(bad[0] + (f" (and {len(bad) - 1} more; python3 styles.py check)" if len(bad) > 1 else ""))
            return {**doc, "_dir": d}
    raise StyleError(f"unknown caption style '{style_id}' (known: {', '.join(ids(dir)) or 'none'}; python3 styles.py list)")


def usable(doc, owner=None):
    """the style, when this DNA may use it; StyleError when it is private to another id"""
    who = doc.get("owner")
    if who not in (None, owner):
        raise StyleError(f"caption style {doc['id']} is private to {who}: {owner} cannot use it")
    return doc


# ------------------------------------------------------------------ laying a style over a look

def _swap_to_running(profile, out, sid):
    """look.font "running": the words are drawn in the look's running face. The display face stays what the page events draw in (display_font)."""
    wf = profile.get("word_font")
    if not wf or not wf.get("faces"):
        raise StyleError(f"caption style {sid}: look.font is running, but your look has no running font (add one to your DNA, or leave look.font out)")
    out.setdefault("display_font", {"fonts_dir": profile.get("fonts_dir"), "prefix": profile.get("font_prefix"),
                                    "family": profile.get("font_family"), "faces": copy.deepcopy(profile.get("font_faces"))})
    out["font"] = wf.get("prefix") or profile.get("font")
    for dst, src in (("font_family", "family"), ("font_files", "files"), ("font_abs", "abs"), ("font_faces", "faces"), ("fonts_dir", "fonts_dir"),
                     ("font_prefix", "prefix")):
        out[dst] = copy.deepcopy(wf.get(src))


def apply(profile, doc):
    """a copy of the look's profile (lookprofile.load, the DNA's captions profile) with the style's fields laid over it: your look's base, then the style's `set`,
    then its `look`. The profile comes back with "caption_style" set to the style's id, so every later stage can record it. A style only lowers caption_tier_max
    (a look's cap is never raised). The names and the taught words are asserted unchanged on the way out, and so are the font files unless look.font swapped them."""
    out, props = copy.deepcopy(profile), _schema_props()
    sid = doc["id"]
    for k, v in (doc.get("set") or {}).items():
        if k not in ALLOWED:
            raise StyleError(f"caption style {sid}: set.{k} is not a field a style may set")
        v = _whole(v, props[k])
        if k == "caption_tier_max" and profile.get(k) in TIER_ORDER and v in TIER_ORDER:
            v = min(profile[k], v, key=TIER_ORDER.index)
        out[k] = copy.deepcopy(v)
    look = doc.get("look") or {}
    for k, v in look.items():
        if k not in LOOK:
            raise StyleError(f"caption style {sid}: look.{k} is not a field a style may set")
        if k == "font":
            if v == "running":
                _swap_to_running(profile, out, sid)
            continue
        out[LOOK_PROFILE_KEY[k]] = copy.deepcopy(_whole(v, props[k]))
    out[RECORD_KEY] = sid
    swapped = look.get("font") == "running"
    moved = [k for k in IDENTITY + (() if swapped else FONT_KEYS) if profile.get(k) != out.get(k)]
    if moved:
        raise StyleError(f"caption style {sid} would change {', '.join(moved)}: names, taught words and font files never move")
    return out


def diff(profile, doc):
    """[(field, before, after)] for the fields the style really changes in this profile, `set` fields first, in the order a style file lists them (a field it
    sets to the look's own value is not one)"""
    after = apply(profile, doc)
    keys = list((doc.get("set") or {})) + [LOOK_PROFILE_KEY[k] for k in (doc.get("look") or {}) if k in LOOK_PROFILE_KEY]
    if (doc.get("look") or {}).get("font") == "running":
        keys.append("font_family")
    return [(k, profile.get(k), after.get(k)) for k in keys if profile.get(k) != after.get(k)]


def describe(doc):
    """one line of what the style sets: 'zone top, width 960, row_px 120-140, look accent #FFD60A' ('your own look, nothing laid over it' for a style that sets nothing)"""
    def show(v):
        return "-".join(map(str, v)) if isinstance(v, list) else str(v).lower() if isinstance(v, bool) else (v if not isinstance(v, dict) else json.dumps(v))
    bits = [f"{k} {show(v)}" for k, v in (doc.get("set") or {}).items()] + [f"look {k} {show(v)}" for k, v in (doc.get("look") or {}).items()]
    return ", ".join(bits) if bits else "your own look, nothing laid over it"


def text_of(doc):
    """the file's text: known keys in their fixed order, two-space indent, a final newline"""
    return json.dumps({k: doc[k] for k in KEYS if k in doc}, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ the command line

def _row(doc):
    return f"{doc['id']:<18} {doc['name']:<26} [{where(doc)}]  {describe(doc)}"


def _main():
    a = sys.argv[1:]
    cmd, rest = a[0], a[1:]
    show_diff = "--diff" in rest
    rest = [x for x in rest if x != "--diff"]
    if cmd == "check":
        docs, bad = check()
        for line in bad:
            print("FIX  " + line)
        print(f"styles check: {len(docs)} sound, {len(bad)} problem" + ("" if len(bad) == 1 else "s"))
        sys.exit(1 if bad else 0)
    if cmd == "list":
        for doc in load_all():
            print(_row(doc))
        return
    if cmd == "show":
        doc = load(rest[0])
        print(f"{doc['id']}  {doc['name']}  ({doc.get('status', 'built')}, {where(doc)})")
        for k in ("summary", "use_when", "never_when", "added", "preview"):
            print(f"  {k}: {doc.get(k) if doc.get(k) is not None else '(none)'}")
        print(f"  sets: {describe(doc)}")
        if show_diff:
            import lookprofile
            changes = diff(lookprofile.load(), doc)
            print("  in your look: " + ("no field changes (it sets what your look already has)" if not changes else "it changes"))
            for k, before, after in changes:
                print(f"    {k}: {before} -> {after}")
        return
    raise ValueError(f"unknown command {cmd!r}")


def main():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from keys import cli, EngineStop

    def run():
        try:
            _main()
        except StyleError as e:
            raise EngineStop("; ".join(e.problems))
    cli(run, "styles.py list | show <id> [--diff] | check")


if __name__ == "__main__":
    main()
