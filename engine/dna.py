#!/usr/bin/env python3
"""dna.py: the one reader of the user's DNA: Brand/dna.json (the values) and Brand/DNA.md (the judgment). Stdlib only (Pillow is optional, for the colors of a logo).

    python3 engine/dna.py check [folder]     schema, files, TODO lines: one plain line per problem and exit 2, or "ok <id>"
    python3 engine/dna.py show|theme|styles|todo [folder]      captions [folder] [--style id] [--prefix p]
    python3 engine/dna.py scan <folder>      what a brand folder proposes (colors, font, logos); read only
    python3 engine/dna.py init <folder> [--name "<Name>"] [--from <folder>] [--accent #hex] [--ink #hex] [--font f] [--emblem f] [--lockup f]
                                             a new DNA; run again, it changes only the options given

In code:  sys.path.insert(0, "<repo>/engine"); import dna; D = dna.load_or_exit(); bd = dna.brand_folder()
  load / load_or_exit / validate / save      read (omitted optional keys filled from templates/dna.json), read and check (exit 2 with one plain line), the problem list, write back
  captions_profile / theme / style           the flat dict the caption chain reads, the one the renderer takes as its theme prop, and a profile with a caption style laid over it
  scan / palette_from_image / init           the wizard's tools: what a brand folder proposes, the colors of a logo, a fresh DNA
  resolve / folders / public_prefix ...      paths: assets, the workspace folders, the renderer's public folder name (public_prefix(dna) is "brand-<id>")
engine/dna_README.md has every function, every field (type, default, which module reads it), both views' keys and the caption style rules.
"""
import argparse
import colorsys
import copy
import functools
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

TEMPLATES = common.REPO / "templates"
FONT_EXT = (".ttf", ".otf", ".woff", ".woff2")
FONT_UNUSABLE = (".eot", ".ttc", ".otc")   # font files that cannot be used (no browser takes them as a web font): named in a heads up, never skipped in silence
LOGO_EXT = (".svg", ".png")
TEXT_EXT = (".html", ".css", ".md", ".json", ".txt")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", "dist"}
LOGO_HINT = re.compile(r"logo|mark|emblem|lockup|wordmark|icon", re.I)
EMBLEM_HINT = re.compile(r"emblem|icon|(?<!word)mark(?!eting)|symbol", re.I)   # a wordmark is a lockup, not the emblem
LOCKUP_HINT = re.compile(r"lockup|wordmark|logo", re.I)
WEIGHT_PREF = ["extrabold", "800", "black", "bold", "700", "semibold", "600", "medium", "500", "regular", "400"]
HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
DARK_INK = 0.2   # an ink under this luma() is a dark brand: its cards can sit on it
# what a style may set: layout, treatment, motion, tier and soft build in `set`; color and font keys in `look` (look key -> the profile key it lays over)
STYLE_SET = ("case", "zone", "width", "top_y0", "hero_max", "row_px", "chin_gap", "chip", "text_opacity", "text_soften", "hero_glow", "glow_px",
             "whip_frames", "whip_amount", "count_up", "caption_tier_max", "soft_build")
STYLE_LOOK = {"accent": "accent", "text": "base", "on_accent": "on_accent", "hero_base": "hero_base", "hero_tint": "hero_tint", "hero_shadow": "hero_shadow",
              "hero_stroke": "hero_stroke", "chip_neg": "chip_neg", "display_weight": "display_weight"}
FONT_KEYS = ("font", "font_fallback", "fonts_dir", "font_prefix", "font_family", "font_files", "font_faces", "font_abs", "font_variable", "display_font", "word_font")
TIERS = ("MINIMAL", "REDUCED", "FULL")   # a style can only lower the cap
STYLE_ID = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"   # lower case words joined by hyphens, starting with a letter


class DnaError(Exception):
    pass


# ------------------------------------------------------------------ reading and the schema walker

def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise DnaError(f"no {Path(path).name} at {path}")
    except OSError as e:
        raise DnaError(f"{path} cannot be read: {e.strerror or e}")
    except ValueError as e:
        raise DnaError(f"{path} is not valid JSON: {e}")
    if not isinstance(raw, dict):   # a list, a string, a number or null parses as JSON but is not a DNA
        shape = "a list" if isinstance(raw, list) else "a string" if isinstance(raw, str) else "null" if raw is None else "true or false" if isinstance(raw, bool) else "a number"
        raise DnaError(f"{path} must be a JSON object (the fields), not {shape}")
    return raw


@functools.lru_cache(maxsize=4)
def _shipped(name):
    return _read(TEMPLATES / name)


def _schema():
    return _shipped("schema.json")   # shared and read-only


def _template():
    return copy.deepcopy(_shipped("dna.json"))   # the neutral DNA, and the defaults _fill uses: one source of truth


def _spelled_differently(s, folder, ext=".json"):
    """the file stem in `folder` that is `s` apart from its case, else None. A disk that ignores case (macOS) finds a style under any spelling of its id, but the code
    after it builds keys from the id as written, so an id must be written exactly as its file is"""
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    if s + ext in names:
        return None
    return next((n[:-len(ext)] for n in sorted(names) if n.lower() == (s + ext).lower()), None)


def _deref(sch, root):
    """sch with its $ref followed (the keys written next to the $ref win)"""
    ref = root
    for part in sch["$ref"].lstrip("#/").split("/"):
        ref = ref[part]
    return {**ref, **{k: x for k, x in sch.items() if k != "$ref"}}


def _type_ok(v, t):
    if t == "string": return isinstance(v, str)
    if t == "integer": return isinstance(v, int) and not isinstance(v, bool)   # JSON 3.0 is a float here: a count written 3.0 is a typo, not an integer
    if t == "number": return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean": return isinstance(v, bool)
    if t == "array": return isinstance(v, list)
    if t == "object": return isinstance(v, dict)
    if t == "null": return v is None
    return True


def _walk(v, sch, path, root, out):
    if "$ref" in sch:
        return _walk(v, _deref(sch, root), path, root, out)
    t = sch.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(v, x) for x in ts):
            out.append(f"{path}: must be {' or '.join(ts)}, got {type(v).__name__ if v is not None else 'null'}")
            return
    if isinstance(v, str) and "TODO" in v:   # after the type check: a TODO where a number or a list belongs is a wrong type, and the checks that follow read the value
        out.append(f"{path}: still says TODO")
        return
    why = f" ({sch['why']})" if sch.get("why") else ""
    if "enum" in sch and v not in sch["enum"]:
        out.append(f"{path}: {v!r} is not one of {', '.join(map(str, sch['enum']))}{why}")
    if isinstance(v, str):
        if "pattern" in sch and not re.search(sch["pattern"], v):
            out.append(f"{path}: {v!r} is not in the expected form" + (why or (" (#RRGGBB)" if "[0-9A-Fa-f]{6}" in sch["pattern"] else "")))
        if len(v) < sch.get("minLength", 0):
            out.append(f"{path}: must not be empty")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in sch and v < sch["minimum"]: out.append(f"{path}: {v} is below {sch['minimum']}{why}")
        if "maximum" in sch and v > sch["maximum"]: out.append(f"{path}: {v} is above {sch['maximum']}{why}")
    if isinstance(v, list):
        if len(v) < sch.get("minItems", 0): out.append(f"{path}: needs at least {sch['minItems']} item(s)")
        if "maxItems" in sch and len(v) > sch["maxItems"]: out.append(f"{path}: at most {sch['maxItems']} item(s)")
        if "items" in sch:
            for i, x in enumerate(v):
                _walk(x, sch["items"], f"{path}[{i}]", root, out)
    if isinstance(v, dict):
        props, pats = sch.get("properties", {}), sch.get("patternProperties", {})
        for k in sch.get("required", []):
            if k not in v: out.append(f"{path}.{k}: missing" if path else f"{k}: missing")
        for k, x in v.items():
            sub = f"{path}.{k}" if path else k
            if k in props:
                _walk(x, props[k], sub, root, out)
                continue
            hit = next((p for p in pats if re.search(p, k)), None)
            if hit:
                _walk(x, pats[hit], sub, root, out)
            elif sch.get("additionalProperties") is False:
                out.append(f"{sub}: not a known field")
            elif isinstance(sch.get("additionalProperties"), dict):
                _walk(x, sch["additionalProperties"], sub, root, out)


def _missing(raw, schema):
    """the required keys the file does not write: every `required` list of the schema, followed down through the sections the file has"""
    out = []
    def need(obj, sch, where):
        sch = _deref(sch, schema) if "$ref" in sch else sch
        if not isinstance(obj, dict):
            return
        out.extend(f"{where}{k}" for k in sch.get("required", []) if k not in obj)
        for k, sub in sch.get("properties", {}).items():
            if k in obj:
                need(obj[k], sub, f"{where}{k}.")
    need(raw, schema, "")
    return out


def _fill(raw, defaults, schema, sch=None):
    """the omitted optional keys, level by level, from the neutral template; a key the schema requires is never filled (the file must write it)"""
    sch = schema if sch is None else (_deref(sch, schema) if "$ref" in sch else sch)
    out, required, props = copy.deepcopy(raw), set(sch.get("required", [])), sch.get("properties", {})
    for k, dv in defaults.items():
        if k not in out:
            if k not in required:
                out[k] = copy.deepcopy(dv)
        elif isinstance(out[k], dict) and isinstance(dv, dict) and k in props:
            out[k] = _fill(out[k], dv, schema, props[k])
    return out


# ------------------------------------------------------------------ loading and validating

def brand_folder(path=None):
    """the folder that holds dna.json: an explicit folder (or the dna.json file in it), else the workspace's Brand/"""
    if path is None:
        return Path(common.brand_dir())
    p = Path(path).expanduser()
    return p.parent if p.suffix == ".json" else p


def load(path=None):
    bd = brand_folder(path)
    raw = _read(bd / "dna.json")
    gone = _missing(raw, _schema())
    if gone:
        raise DnaError(f"dna.json is missing: {', '.join(gone)}")
    d = _fill(raw, _template(), _schema())
    for who in d["people"] if isinstance(d["people"], list) else []:   # a person's optional keys, so a reader never asks whether they are there
        if isinstance(who, dict):
            who.setdefault("role", "")
            who.setdefault("name_tag", True)
    return d


def load_or_exit(path=None, strict=True):
    """load + validate for a script: one plain line on stderr and exit 2 when the DNA cannot be used"""
    try:
        d = load(path)
    except DnaError as e:
        print(f"dna: {e}", file=sys.stderr)
        sys.exit(2)
    problems = validate(d, brand_folder(path), strict=strict)
    if problems:
        print(f"dna: cannot be used: {problems[0]}" + (f" (and {len(problems) - 1} more; run dna.py check)" if len(problems) > 1 else ""), file=sys.stderr)
        sys.exit(2)
    return d


def save(dna, brand_dir):
    """write dna.json back (atomic). Refuses a DNA that is not well formed; a TODO still in it is fine, the wizard is part way through"""
    plain = {k: v for k, v in dna.items() if not k.startswith("_")}
    bad = [p for p in validate(plain, brand_dir, strict=False) if not p.endswith("still says TODO")]
    if bad:
        raise DnaError(f"dna.json was not written: {bad[0]}")
    common.atomic_write_json(Path(brand_dir) / "dna.json", plain)


def md_todos(brand_dir):
    """[(line number, the line)] for every line of DNA.md that still says TODO ([] when there are none or there is no DNA.md)"""
    try:
        with open(Path(brand_dir) / "DNA.md", encoding="utf-8", errors="replace") as fh:
            return [(i, ln.rstrip("\r\n")) for i, ln in enumerate(fh, 1) if "TODO" in ln]
    except OSError:
        return []


def todo_lines(brand_dir):
    """every TODO still in the DNA, one plain line each: the dna.json values first, then the DNA.md lines (the list the wizard works through)"""
    out = []
    _walk(_read(Path(brand_dir) / "dna.json"), _schema(), "", _schema(), out)
    return [p for p in out if p.endswith("still says TODO")] + [f"DNA.md line {n}: {ln.strip()[:100]}" for n, ln in md_todos(brand_dir)]


FONT_MAGIC = (b"wOF2", b"wOFF", b"\x00\x01\x00\x00", b"OTTO", b"true")   # the first four bytes of a WOFF2, WOFF, TTF, OTF or old Mac TTF file


def _font_problem(path):
    """None when the file starts with the signature of a font container, else why it is not a font: it is empty (a failed download or copy), or it is not a font file"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return "is not a font file"
    if head in FONT_MAGIC:
        return None
    return "is not a font file" if head else "is empty, so it is not a font file"


def _asset_paths(d):
    """(where, relpath) for every file the DNA points at under Brand/assets/"""
    for role in ("display", "running", "mono"):
        face = d["fonts"].get(role)
        if isinstance(face, dict):
            if face.get("file"): yield f"fonts.{role}.file", face["file"]
            for w, p in (face.get("weights") or {}).items():
                yield f"fonts.{role}.weights.{w}", p
    for k, p in d["logo"].items():
        if p: yield f"logo.{k}", p
    if d["grade"].get("lut"): yield "grade.lut", d["grade"]["lut"]


def _file_problems(d, bd):
    out = []
    for where, rel in _asset_paths(d):
        if "TODO" in rel:
            continue   # already reported as a TODO
        path = bd / "assets" / rel
        if not path.is_file():
            out.append(f"{where}: file not found: assets/{rel}")
        elif where.startswith("fonts.") and _font_problem(path):
            out.append(f"{where}: assets/{rel} {_font_problem(path)} (a TTF, OTF, WOFF or WOFF2 starts with its own signature)")
        elif where.startswith("logo.") and not rel.lower().endswith(LOGO_EXT):
            out.append(f"{where}: assets/{rel} must be an .svg or .png file")
    return out


def _rules(d):
    """what one field cannot say alone"""
    out, f, h, b, o = [], d["formats"], d["hook"], d["broll"], d["output"]
    if "TODO" not in f["home"] and f["home"] not in f["list"]: out.append(f"formats.home: '{f['home']}' is not in formats.list")
    if len(set(f["list"])) != len(f["list"]): out.append("formats.list: names a format twice")
    if h["hold_s"][0] > h["hold_s"][1]: out.append(f"hook.hold_s: the shortest hold ({h['hold_s'][0]}) is longer than the longest ({h['hold_s'][1]})")
    if b["hold_min_s"] > b["hold_max_s"]: out.append(f"broll.hold_min_s: {b['hold_min_s']} is longer than hold_max_s ({b['hold_max_s']})")
    if d["captions"]["row_px"][0] > d["captions"]["row_px"][1]: out.append("captions.row_px: the smallest row height is larger than the largest")
    if h["font"] == "running" and not d["fonts"]["running"]: out.append("hook.font: is 'running' but fonts.running is null: add a running font, or use 'display'")
    (w, ht), shape = o["size"], re.fullmatch(r"(\d+):(\d+)", o["aspect"])   # a TODO in the aspect is reported by the walk; the shape rule waits for a real one
    if shape and abs(w / ht - int(shape[1]) / int(shape[2])) > 0.01: out.append(f"output.size: {w} x {ht} is not {o['aspect']}")
    if o["aspect"] in o["also"]: out.append(f"output.also: names the main aspect {o['aspect']}")
    for where, items, key in (("graphics.wishlist", d["graphics"]["wishlist"], "id"), ("graphics.built", d["graphics"]["built"], "id"), ("studies", d["studies"], "slug")):
        names = [x[key] for x in items]
        out.extend(f"{where}[{i}].{key}: '{n}' is used twice" for i, n in enumerate(names) if n in names[:i])
    return out


def _md_problems(bd):
    if not (bd / "DNA.md").is_file():
        return ["DNA.md: not found (write who is on camera, the voice, the look, motion, captions, b-roll, music and sound, formats, what is never allowed and where things are)"]
    return [f"DNA.md line {n}: {ln.strip()[:100]}" for n, ln in md_todos(bd)]


def _style_problems(d, bd):
    out, ds = [], d["captions"]["default_style"]
    if ds and "TODO" not in ds and not any(s["id"] == ds for s in styles_list(bd)):
        out.append(f"captions.default_style: no style called '{ds}' (a file Brand/styles/{ds}.json, or a starter: {', '.join(s['id'] for s in styles_list())})")
    folder = bd / "styles"
    for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if p.name.startswith("."):
            continue   # a hidden record beside the styles (the preview's .stills.json), not a style
        out.extend(f"styles/{p.name}: {x}" for x in _style_file_problems(p, d))
    return out


def validate(dna, brand_dir, strict=True):
    """the plain-language problems of a DNA ([] = fine; a leftover TODO always counts). strict adds the checks that read the disk: every font, logo and LUT file exists and a
    font starts like a font, every style file reads, DNA.md exists with no TODO line"""
    bd, schema, out = Path(brand_dir), _schema(), []
    plain = {k: v for k, v in dna.items() if not k.startswith("_")}
    _walk(plain, schema, "", schema, out)
    if any(not p.endswith("still says TODO") for p in out):
        return out   # the checks below read well-formed values
    d = _fill(plain, _template(), schema)
    out += _rules(d)
    if strict:
        out += _file_problems(d, bd) + _style_problems(d, bd) + _md_problems(bd)
    return out


# ------------------------------------------------------------------ paths and fonts

def _abs(brand_dir, rel):
    return os.path.normpath(os.path.join(os.path.abspath(os.path.expanduser(str(brand_dir))), "assets", rel))


def resolve(dna, rel, brand_dir):
    """absolute path of an asset under Brand/assets/ (rel "" gives the folder)"""
    return _abs(brand_dir, rel) if rel else _abs(brand_dir, "")


def public_prefix(dna):
    return f"brand-{dna['id']}"


def folders(dna):
    """{name: Path} for every workspace folder (common.WORKSPACE_FOLDERS); Archive is storage.archive when the DNA names one"""
    out = {n: common.ws(n) for n in common.WORKSPACE_FOLDERS}
    if dna["storage"]["archive"]:
        out["Archive"] = Path(dna["storage"]["archive"]).expanduser()
    return out


def music_dir(dna, brand_dir):
    return Path(brand_dir) / dna["music"]["library"]


def grade_profile(dna, brand_dir):
    """{enabled, lut}: the brand's color grade; lut is an absolute path or None (None grades by balance only)"""
    g = dna["grade"]
    return {"enabled": bool(g["enabled"]), "lut": resolve(dna, g["lut"], brand_dir) if g["lut"] else None}


def _faces(face):
    """[{"weight": "100 900", "src": rel}]: one face per distinct file. A face with no weights map serves every weight."""
    ws = face.get("weights")
    if not ws:
        return [{"weight": "100 900", "src": face["file"]}]
    by = {}
    for w, p in sorted(ws.items()):
        by.setdefault(p, []).append(int(w))
    return [{"weight": str(min(x)) if min(x) == max(x) else f"{min(x)} {max(x)}", "src": p} for p, x in by.items()]


def _files_by_weight(face):
    ws = face.get("weights")
    return {str(w): (_nearest(ws, w) if ws else face["file"]) for w in (400, 500, 700, 800, 900)}


def _nearest(ws, w):
    k = min(ws, key=lambda x: abs(int(x) - w))
    return ws[k]


def _slug(s):
    s = re.sub(r"[^A-Za-z0-9]", "", s)
    return s if s[:1].isalpha() else "F" + s   # a CSS family name starts with a letter


_WOFF2_TAGS = ("cmap head hhea hmtx maxp name OS/2 post cvt fpgm glyf loca prep CFF VORG EBDT EBLC gasp hdmx kern LTSH PCLT VDMX vhea vmtx "
               "BASE GDEF GPOS GSUB EBSC JSTF MATH CBDT CBLC COLR CPAL SVG sbix acnt avar bdat bloc bsln cvar fdsc feat fmtx fvar gvar "
               "hsty just lcar mort morx opbd prop trak Zapf Silf Glat Gloc Feat Sill").split()


def _font_tags(path):
    """the table tags of a ttf, otf or woff2 file (read from the table directory, nothing is decompressed); empty if unreadable"""
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
                b = d[i]; i += 1; v = (v << 7) | (b & 0x7F)
                if not b & 0x80: return v, i
        try:
            for _ in range(n):
                flags = d[pos]; pos += 1; idx = flags & 0x3F
                if idx == 63: tag = d[pos:pos + 4].decode("latin1"); pos += 4
                else: tag = _WOFF2_TAGS[idx]
                _, pos = base128(pos)
                if (tag in ("glyf", "loca")) == ((flags >> 6) == 0): _, pos = base128(pos)
                tags.add(tag)
        except IndexError:
            pass
        return tags
    if d[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        n = int.from_bytes(d[4:6], "big")
        return {d[12 + 16 * i:16 + 16 * i].decode("latin1") for i in range(n) if 16 + 16 * i <= len(d)}
    return set()


def font_is_variable(dna, brand_dir, role="display"):
    """True when the face in slot `role` (display, running or mono) is a variable font: one file with an fvar table that serves every weight. False for a static file, and
    for a file that cannot be read (check names that one)"""
    return "fvar" in _font_tags(_abs(brand_dir, dna["fonts"][role]["file"]))


# ------------------------------------------------------------------ the two views

def _pre(prefix, brand_dir):
    """a file under assets/ as a view gives it: behind `prefix` (the renderer's public folder) when there is one, else absolute. None stays None"""
    return lambda p: (f"{prefix}/{p}" if prefix else _abs(brand_dir, p)) if p else p


def _view(face, brand_dir, prefix):
    """one font face as the engines read it: its folder, a CSS family name, the file for each weight, the faces and the absolute paths"""
    pre, slug, by, folder = _pre(prefix, brand_dir), _slug(face["family"]), _files_by_weight(face), os.path.dirname(face["file"])
    return {"fonts_dir": f"{prefix}/{folder}".rstrip("/") if prefix else _abs(brand_dir, folder), "prefix": slug, "family": slug + "Dyn",
            "files": {w: pre(p) for w, p in by.items()}, "faces": [{"weight": x["weight"], "src": pre(x["src"])} for x in _faces(face)],
            "abs": {w: _abs(brand_dir, p) for w, p in by.items()}}


def _font_keys(dna, role, brand_dir, prefix):
    """the profile's display-font keys, read from the face in slot `role`"""
    face = dna["fonts"][role]
    v = _view(face, brand_dir, prefix)
    return {"font": face["family"], "font_fallback": face.get("fallback", ""), "fonts_dir": v["fonts_dir"], "font_prefix": v["prefix"], "font_family": v["family"],
            "font_files": v["files"], "font_faces": v["faces"], "font_abs": v["abs"],
            "font_variable": font_is_variable(dna, brand_dir, role),
            "display_font": {k: v[k] for k in ("fonts_dir", "prefix", "family", "faces")}}


def captions_profile(dna, brand_dir, prefix=None):
    """the flat dict the caption chain reads. Every field of the captions section keeps its name; the color, font, word and switch keys that live elsewhere in the DNA are
    added (accent, base, on_accent, the font keys, word_font, spelling, do_not_say, auto_punches); chip_neg falls back to colors.negative and text_hook is off when hook.on is"""
    c, col, f = dna["captions"], dna["colors"], dna["fonts"]
    out = {"id": dna["id"], "dna_file": os.path.abspath(Path(brand_dir) / "dna.json"), "brand": dna["name"], "handle": dna["handle"],
           "accent": col["accent"], "base": col["base"], "on_accent": col["on_accent"], **_font_keys(dna, "display", brand_dir, prefix),
           "word_font": _view(f["running"], brand_dir, prefix) if f["running"] else None}
    out.update(copy.deepcopy(c))
    out["chip_neg"] = c["chip_neg"] or col["negative"]
    out["text_hook"] = "on" if dna["hook"]["on"] and c["text_hook"] == "on" else "off"
    out["auto_punches"] = dna["motion"]["auto_punches"]
    out["spelling"], out["do_not_say"] = dict(dna["words"]["spelling"]), list(dna["words"]["banned"])
    return out


def theme(dna, brand_dir, prefix=None):
    """the flat dict the Edit renderer takes as its theme prop. The running and mono faces ride along (hook.font "running" and the mono hook look draw in them)"""
    pre, c, lg, h, fc, f = _pre(prefix, brand_dir), dna["colors"], dna["logo"], dna["hook"], dna["feed_captions"], dna["fonts"]
    d, ec = _view(f["display"], brand_dir, prefix), dict(dna["delivery"]["end_card"])
    ec["handle"] = ec["handle"] or dna["handle"]
    out = {"id": dna["id"], "name": dna["name"], "colors": {k: c[k] for k in ("accent", "ink", "base", "on_accent", "negative", "bg", "card")},
           "surface": {k: dna["surface"][k] for k in ("background", "border")}, "font_family": d["family"], "font_faces": d["faces"],
           "font_fallback": f["display"].get("fallback", ""), "caps": dna["type"]["caps"], "skew": dna["type"]["skew"],
           "cap_words": fc["words_per_page"], "caption_size": fc["size_px"], "caption_top": fc["top_px"], "caption_stroke": fc["stroke_px"],
           "hook_on": h["on"], "hook_look": h["look"], "hook_top": h["top_px"], "hook_size": h["size_px"], "hook_font": h["font"],
           "lockup_src": pre(lg["lockup"]), "lockup_dark_src": pre(lg["lockup_dark"]), "emblem_src": pre(lg["emblem"]),
           "lockup_on": bool(lg["lockup"] or lg["lockup_dark"] or lg["emblem"]), "broll_style": dna["broll"]["style"],
           "smooth_motion": dna["motion"]["smooth"], "music_level": dna["music"]["alone_lufs"], "end_card": ec}
    for role in ("running", "mono"):
        v = _view(f[role], brand_dir, prefix) if f[role] else None
        out[f"{role}_family"], out[f"{role}_faces"] = (v["family"], v["faces"]) if v else (None, [])
    return out


# ------------------------------------------------------------------ caption styles

def _style_schema():
    s = _schema()["properties"]["captions"]["properties"]
    ref = lambda n: {"$ref": f"#/definitions/{n}"}
    num = lambda lo, hi: {"type": "number", "minimum": lo, "maximum": hi}
    enum = lambda *v: {"type": "string", "enum": list(v)}
    line = {"type": "string", "minLength": 1, "pattern": r"^[^\n]*$", "why": "one line"}
    sid = {"type": ["string", "null"], "pattern": STYLE_ID, "why": "an id: lower case words joined by hyphens, or null"}
    return {"type": "object", "additionalProperties": False, "required": ["id", "name", "summary", "use_when", "never_when", "set"], "properties": {
        "id": {"type": "string", "pattern": STYLE_ID, "why": "lower case words joined by hyphens, like keyword-slam"}, "name": line, "summary": line, "use_when": line, "never_when": line,
        "preview": {"type": ["string", "null"], "pattern": r"^(?!/)(?!.*\.\.)[^\s].*\.(png|jpe?g|webp)$", "why": "null, or an image path inside the styles folder"},
        "source": {"type": ["object", "null"]}, "opt_in": {"type": "boolean"}, "owner": sid, "added_for": sid, "added": ref("date"), "status": enum("built", "to build"),
        "set": {"type": "object", "additionalProperties": False, "properties": {k: s[k] for k in STYLE_SET}},
        "look": {"type": "object", "additionalProperties": False, "properties": {
            "accent": ref("hex"), "text": ref("hex"), "on_accent": ref("hex"), "hero_base": ref("hex"), "hero_tint": ref("hex"), "hero_shadow": {"type": "string"},
            "hero_stroke": {"type": "object", "additionalProperties": False, "required": ["width", "color"], "properties": {"width": {"type": "integer", "minimum": 0, "maximum": 40}, "color": ref("hex")}},
            "chip_neg": ref("hex"), "display_weight": s["display_weight"], "font": enum("display", "running")}},
        "feed": {"type": "object", "additionalProperties": False, "properties": {
            "place": enum("chest", "lower", "top"), "size": num(0.7, 1.5), "motion": enum("reveal", "quiet", "soft"), "words": {"type": "integer", "minimum": 1, "maximum": 3},
            "hero": {"type": "object", "additionalProperties": False, "properties": {"look": enum("color", "bigger", "bigger_bold"), "scale": num(1.3, 2.8), "pick": enum("accent", "page")}}}}}}


def _style_file_problems(path, d=None):
    """every plain-language problem of one style file ([] = fine). `set` takes layout, treatment, motion, tier and soft_build fields; colors, the display weight and the font
    slot go in `look`; a word list or anything else is the brand's. d (the DNA) adds the rules that need it"""
    try:
        st = _read(path)
    except DnaError as e:
        return [str(e)]
    out, cap = [], _schema()["properties"]["captions"]["properties"]
    for k in [k for k in st["set"] if k not in STYLE_SET] if isinstance(st.get("set"), dict) else []:
        out.append(f"set.{k}: " + ("a color, the display weight and the font go in look, not in set" if k in STYLE_LOOK
                                   else "that field is the brand's: a style may not set it" if k in cap else "not a known field"))
        del st["set"][k]
    _walk(st, _style_schema(), "", _schema(), out)
    row = st["set"].get("row_px") if isinstance(st.get("set"), dict) else None
    if isinstance(row, list) and len(row) == 2 and all(isinstance(x, int) for x in row) and row[0] > row[1]:
        out.append("set.row_px: the first number is the smaller row (minimum, then maximum)")
    if isinstance(st.get("id"), str) and st["id"] != Path(path).stem:
        out.append(f"id: '{st['id']}' must match the file name '{Path(path).stem}'")
    if d is not None and isinstance(st.get("look"), dict) and st["look"].get("font") == "running" and not d["fonts"]["running"]:
        out.append("look.font: is 'running' but fonts.running is null")
    return out


def styles_list(brand_dir=None):
    """every style that can be picked, [{id, name, summary, use_when, never_when, where, path}]: the user's Brand/styles/ first, then the starters they have not replaced"""
    out, seen = [], set()
    for where, folder in (("brand", Path(brand_dir) / "styles" if brand_dir else None), ("starter", TEMPLATES / "styles")):
        for p in sorted(folder.glob("*.json")) if folder and folder.is_dir() else []:
            if p.name.startswith("."):
                continue   # hidden records are not styles
            try:
                st = _read(p)
            except DnaError:
                continue   # check names a file that does not read
            if p.stem not in seen:
                seen.add(p.stem)
                if st.get("status") == "to build":
                    continue   # waiting for the engine to learn a field it needs: in no menu
                out.append({"id": p.stem, "where": where, "path": str(p), **{k: st.get(k, "") for k in ("name", "summary", "use_when", "never_when")}})
    return out


def _load_style(sid, brand_dir, d=None):
    if not re.fullmatch(r"[A-Za-z0-9-]+", str(sid)):
        raise DnaError(f"style id '{sid}' must be lowercase letters, digits and dashes")
    path = None
    for folder in (Path(brand_dir) / "styles", TEMPLATES / "styles"):
        real = _spelled_differently(sid, folder)
        if real:
            raise DnaError(f"style id '{sid}' is written in the wrong case: use '{real}' (ids are lowercase and must match the file name exactly)")
        if path is None and (folder / f"{sid}.json").is_file():
            path = folder / f"{sid}.json"
    if path is None:
        raise DnaError(f"unknown style '{sid}' (known: {', '.join(s['id'] for s in styles_list(brand_dir)) or 'none'}). A style is one file: Brand/styles/{sid}.json")
    problems = _style_file_problems(path, d)
    if problems:
        raise DnaError(f"style '{sid}': {problems[0]}" + (f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""))
    return _read(path)


def style(dna, style_id, brand_dir, prefix=None):
    """captions_profile with a style laid over it: its `set` over the captions section (the tier only ever lowers), its `look` over the profile's color and font keys
    (look.text is `base`; look.font "running" points the font keys at the running face). Its `feed` block rides along as "feed" (words capped at feed_captions.words_per_page)"""
    st = _load_style(style_id, brand_dir, dna)
    if st.get("owner") not in (None, dna["id"]):
        raise DnaError(f"style '{style_id}' is private to {st['owner']}: {dna['id']} cannot use it")
    d, s = copy.deepcopy(dna), st["set"]
    if "caption_tier_max" in s:
        s["caption_tier_max"] = min(s["caption_tier_max"], d["captions"]["caption_tier_max"], key=TIERS.index)
    d["captions"].update(s)
    base, prof, look = captions_profile(dna, brand_dir, prefix), captions_profile(d, brand_dir, prefix), st.get("look") or {}
    for k, v in look.items():
        if k != "font":
            prof[STYLE_LOOK[k]] = copy.deepcopy(v)
    if look.get("font") == "running":   # the words draw in the running face; display_font (page and pill text) stays the display face, word_font stays the running one
        prof.update({k: v for k, v in _font_keys(d, "running", brand_dir, prefix).items() if k != "display_font"})
    mine = set(STYLE_SET) | set(STYLE_LOOK.values()) | set(FONT_KEYS)
    kept = [k for k in base if k not in mine and prof.get(k) != base[k]]
    if kept:
        raise DnaError(f"style '{style_id}' changed {', '.join(kept)}, which are the brand's")
    feed = copy.deepcopy(st.get("feed") or {})
    if "words" in feed:
        feed["words"] = min(feed["words"], dna["feed_captions"]["words_per_page"])
    return {**prof, "style": style_id, "feed": feed}


# ------------------------------------------------------------------ color maths and the folder scan

def _rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def luma(h):
    r, g, b = (x / 255 for x in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def sat(h):
    return colorsys.rgb_to_hsv(*(x / 255 for x in _rgb(h)))[1]


def mix(h, toward, amt):
    return "#" + "".join(f"{round(x + (y - x) * amt):02X}" for x, y in zip(_rgb(h), _rgb(toward)))


def rel_lum(h):
    """WCAG relative luminance of #RRGGBB"""
    lin = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(x / 255) for x in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((rel_lum(a), rel_lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tone_at(floor, toward, ratio, start=None):
    """the color on the line from `start` (default: the floor) to `toward` that reads `ratio`:1 on the floor (`toward` itself when even it falls short);
    returns (hex, share of the way to `toward`)"""
    start = start or floor
    if contrast(toward, floor) <= ratio:
        return toward, 1.0
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2
        if contrast(mix(start, toward, mid), floor) < ratio:
            lo = mid
        else:
            hi = mid
    return mix(start, toward, hi), hi


def on_accent(accent, ink):
    """the color of text on the accent: white on a deep accent, else the ink when it reads there (4.5:1), else black"""
    if luma(accent) <= 0.5:
        return "#FFFFFF"
    return ink if ink and contrast(ink, accent) >= 4.5 else "#000000"


_text_on_accent = on_accent   # init() has a parameter of the same name


def norm_hex(v, what):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", v or ""):
        raise DnaError(f"{what} must be #RRGGBB, got {v!r}")
    return v.upper()


def _discover(folder):
    """read-only walk: fonts, logo candidates, hex color counts, and the font files that cannot be used"""
    fonts, logos, colors, unusable = [], [], Counter(), []
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(fns):
            p, ext = os.path.join(dp, fn), os.path.splitext(fn)[1].lower()
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if ext in FONT_EXT:
                fonts.append(p)
            elif ext in FONT_UNUSABLE:
                unusable.append(p)
            elif ext in LOGO_EXT and LOGO_HINT.search(fn) and size < 5_000_000:
                logos.append(p)
            elif ext in TEXT_EXT and size < 2_000_000:
                try:
                    with open(p, errors="ignore") as f:
                        colors.update("#" + x.upper() for x in HEX.findall(f.read()))
                except OSError:
                    pass
    return fonts, logos, colors, unusable


def pick_font(fonts):
    def score(p):
        n = os.path.basename(p).lower()
        for i, w in enumerate(WEIGHT_PREF):
            if w in n:
                return i
        return len(WEIGHT_PREF)
    return sorted(fonts, key=lambda p: (score(p), p))[0]


def copy_into(src, dst_dir):
    """copy the bytes only: copy2 also copies file flags, which a system font refuses. Returns the name the copy has"""
    os.makedirs(dst_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(src))
    dst, n = os.path.join(dst_dir, stem + ext), 2
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f"{stem}-{n}{ext}")
        n += 1
    try:
        shutil.copyfile(src, dst)
    except OSError as e:
        raise DnaError(f"cannot copy {src} into the brand: {e.strerror or e}")
    return os.path.basename(dst)


def palette_from_image(path, k=5):
    """([{hex, share}], note): k-means over a small copy of a logo (transparent pixels left out), the biggest color first. Pillow is optional: without it, ([], why)"""
    try:
        from PIL import Image
    except ImportError:
        return [], "Pillow is not installed, so the colors of the logo were not read (pip install pillow)"
    name = os.path.basename(path)
    try:
        im = Image.open(path).convert("RGBA")
    except (OSError, ValueError) as e:
        return [], f"{name} could not be read as an image: {e}"
    im.thumbnail((64, 64))
    raw = im.tobytes()
    px = sorted(((raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 4) if raw[i + 3] >= 128), key=sum)
    if not px:
        return [], f"{name} has no opaque pixels"
    cents = [px[(2 * i + 1) * len(px) // (2 * min(k, len(px)))] for i in range(min(k, len(px)))]   # start spread across the dark-to-light order, so a run is repeatable
    for _ in range(12):
        groups = [[] for _ in cents]
        for p in px:
            groups[min(range(len(cents)), key=lambda i: sum((a - b) ** 2 for a, b in zip(p, cents[i])))].append(p)
        new = [tuple(sum(c) / len(g) for c in zip(*g)) if g else cents[i] for i, g in enumerate(groups)]
        if new == cents:
            break
        cents = new
    rows = [("#%02X%02X%02X" % tuple(round(x) for x in c), len(g) / len(px)) for c, g in zip(cents, groups)]
    return [{"hex": h, "share": round(s, 3)} for h, s in sorted(rows, key=lambda r: -r[1]) if s >= 0.01], ""


def scan(folder):
    """read a brand folder (never writes to it) -> (proposal, notes). The proposal is what init() takes: accent, ink, base, on_accent, font (the best weight), fonts (the others to
    copy), emblem, lockup and logos (every logo found). Notes say what was guessed and what was found but not used; a 'heads up' note needs a person"""
    folder = os.path.abspath(os.path.expanduser(str(folder)))
    if not os.path.isdir(folder):
        raise DnaError(f"folder not found: {folder}")
    fonts, logos, counts, unusable = _discover(folder)
    notes, p = [], {}
    if unusable:
        notes.append(f"heads up: the folder has font files that cannot be used ({', '.join(sorted(os.path.basename(x) for x in unusable))}): a font is a .ttf, .otf, .woff or .woff2 file. Convert them, or pass one with --font")
    png = next((x for x in logos if x.lower().endswith(".png")), None)
    if not counts and png:   # no hex colors written anywhere: read the logo's own
        pal, why = palette_from_image(png)
        counts.update({c["hex"]: max(1, round(c["share"] * 1000)) for c in pal})
        notes.append(f"colors read from the logo {os.path.basename(png)}" if pal else why)
    ranked = [h for h, _ in counts.most_common()]
    accent = next((h for h in ranked if sat(h) > 0.45 and 0.12 < luma(h) < 0.92), None)
    ink = next((h for h in ranked if luma(h) < 0.12), None)
    base = next((h for h in ranked if luma(h) > 0.97), None)
    for k, v in (("accent", accent), ("ink", ink), ("base", base)):
        if v:
            p[k] = v
    if accent:
        p["on_accent"] = on_accent(accent, ink)
        notes.append(f"colors.accent proposed {accent}: the most frequent strong color in the folder; confirm against the guide")
    if ink:
        notes.append(f"colors.ink proposed {ink}: the most frequent dark color in the folder; confirm")
    rest = [(h, n) for h, n in counts.most_common(14) if h not in {accent, ink, base}]
    if rest:
        notes.append("other colors found (count): " + ", ".join(f"{h} x{n}" for h, n in rest))
    if fonts:
        p["font"] = pick_font(fonts)
        p["fonts"] = [x for x in fonts if x != p["font"]]
    if logos:
        p["logos"] = logos
        p["emblem"] = next((x for x in logos if EMBLEM_HINT.search(os.path.basename(x))), None)
        p["lockup"] = next((x for x in logos if LOCKUP_HINT.search(os.path.basename(x)) and x != p["emblem"]), None)
        notes.append("logos found: " + ", ".join(sorted(os.path.basename(x) for x in logos)))
    return {k: v for k, v in p.items() if v}, notes


# ------------------------------------------------------------------ init (the wizard's first step)

def _status(bd, notes=()):
    """print the heads-up notes and what is still to do before check passes; return those lines"""
    for n in notes:
        if n.startswith("heads up"):
            print(n)
    todo = validate(load(bd), bd)
    if todo:
        print("still to do before `dna.py check` passes:")
        for line in todo:
            print(f"  {line}")
    else:
        print(f"check passed for {load(bd)['id']}")
    return todo


def init(brand_dir, name=None, *, brand_id=None, handle="", site="", accent=None, ink=None, base=None, on_accent=None, font=None, fonts=(), emblem=None, lockup=None,
         lockup_dark=None, logos=(), caps=False, notes=()):
    """write a new DNA into brand_dir: dna.json from the template with the proposal applied, the fonts and logos copied into assets/, the starter styles in styles/, DNA.md from
    the template. Prints what is still TODO and returns those lines. Run again on a folder that has a dna.json, it changes only what it is given (colors, font, logos, handle,
    site, caps, name): bg, card and on_accent are worked out again from a new ink or accent, and people, styles and DNA.md are left alone. A failed step leaves nothing behind"""
    bd, name = Path(brand_dir).expanduser().resolve(), (name or "").strip()
    existing = (bd / "dna.json").exists()
    bid = brand_id or re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).strip("-")   # Café Düsseldorf: cafe-dusseldorf
    if not existing and not name:
        raise DnaError("init needs a name (--name)")
    if not existing and not re.fullmatch(r"[a-z0-9-]+", bid):
        raise DnaError(f"id '{bid}' must be lowercase letters, digits and dashes (pass --id)")
    for what, p, ext in [("font", font, FONT_EXT)] + [(w, x, LOGO_EXT) for w, x in (("emblem", emblem), ("lockup", lockup), ("lockup_dark", lockup_dark))]:
        if p and not os.path.isfile(os.path.expanduser(p)):
            raise DnaError(f"{what} file not found: {p}")
        if p and not str(p).lower().endswith(ext):
            raise DnaError(f"{what} must be a {' or '.join(ext)} file, got {os.path.basename(p)}")
    accent, ink, base, on_acc = (norm_hex(x, w) if x else None for w, x in (("accent", accent), ("ink", ink), ("base", base), ("on_accent", on_accent)))
    d, notes = load(bd) if existing else _template(), list(notes)
    if existing and brand_id and brand_id != d["id"]:
        raise DnaError(f"the id is '{d['id']}' and init does not change it (rename it in dna.json)")
    if existing and not any((name, handle, site, accent, ink, base, on_acc, font, fonts, emblem, lockup, lockup_dark, logos, caps)):
        print(f"{bd / 'dna.json'} is already there and no option was given, so nothing changed (--accent --ink --font --emblem --lockup --handle --site --caps change one)")
        return _status(bd)
    if not existing:
        d.update(id=bid)
    if name:
        d["name"] = name
    if handle:
        d["handle"] = "@" + handle.lstrip("@")
    if site:
        d["site"] = site
    c = d["colors"]
    c.update({k: v for k, v in (("accent", accent), ("ink", ink), ("base", base)) if v})
    if ink and luma(ink) < DARK_INK:   # a dark ink is the floor and the card is the ink with 8% white; a lighter ink leaves the neutral dark floor
        c["bg"], c["card"] = ink, mix(ink, "#FFFFFF", 0.08)
        d["surface"]["background"] = "rgba({},{},{},0.94)".format(*_rgb(c["card"]))
        notes.append("colors.bg is the ink and colors.card is the ink with 8% white; confirm against the guide")
    elif ink:
        notes.append(f"the ink {ink} is not dark (luma {luma(ink):.2f}, a dark ink is under {DARK_INK}), so colors.bg and colors.card keep their neutral dark values; set them from the guide")
    if accent or ink or on_acc or not existing:
        c["on_accent"] = on_acc or _text_on_accent(c["accent"], c["ink"])
        if contrast(c["accent"], c["bg"]) < 3:
            lighter, _ = tone_at(c["bg"], "#FFFFFF", 4.5, start=c["accent"])
            notes.append(f"heads up: the accent {c['accent']} reads {contrast(c['accent'], c['bg']):.2f}:1 on the floor {c['bg']}, under 3:1: captions and cards draw it there. A lighter {lighter} reads 4.5:1")
    if caps:
        d["type"]["caps"], d["captions"]["case"] = True, "upper"
    made, existed = [], {p for p in (bd, bd / "assets", bd / "assets" / "fonts", bd / "assets" / "logos", bd / "styles") if p.exists()}
    try:
        use = font or (fonts[0] if fonts else None)
        if use:
            got = copy_into(os.path.expanduser(use), bd / "assets" / "fonts")
            made.append(bd / "assets" / "fonts" / got)
            for x in [x for x in fonts if x != use]:
                made.append(bd / "assets" / "fonts" / copy_into(x, bd / "assets" / "fonts"))
            stem = re.split(r"[-_ ]", os.path.splitext(os.path.basename(use))[0])[0]
            fam = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
            d["fonts"]["display"].update(family=fam, file=f"fonts/{got}")
            notes.append(f"font family '{fam}' guessed from the file name {os.path.basename(use)}; confirm fonts.display.family and add a fallback")
        elif not existing:
            d["fonts"]["display"].update(family="TODO", file="TODO")
        placed = {}
        for src in [x for x in (emblem, lockup, lockup_dark, *logos) if x]:
            if src not in placed:
                placed[src] = copy_into(os.path.expanduser(src), bd / "assets" / "logos")
                made.append(bd / "assets" / "logos" / placed[src])
        for key, src in (("emblem", emblem), ("lockup", lockup), ("lockup_dark", lockup_dark)):
            if src:
                d["logo"][key] = "logos/" + placed[src]
        d["notes"] = d["notes"] + notes
        if not existing:
            (bd / "styles").mkdir(parents=True, exist_ok=True)
            for p in sorted((TEMPLATES / "styles").glob("*.json")):
                if not (bd / "styles" / p.name).exists():
                    shutil.copyfile(p, bd / "styles" / p.name)
                    made.append(bd / "styles" / p.name)
            if not (bd / "DNA.md").exists():
                (bd / "DNA.md").write_text((TEMPLATES / "DNA.md").read_text(encoding="utf-8").replace("# TODO Brand", f"# {name}", 1), encoding="utf-8")
                made.append(bd / "DNA.md")
        common.atomic_write_json(bd / "dna.json", d)   # last: a folder with a dna.json is a finished init
    except BaseException:
        for p in made:
            p.unlink(missing_ok=True)
        for p in sorted({bd / "assets" / "fonts", bd / "assets" / "logos", bd / "assets", bd / "styles", bd} - existed, key=lambda q: -len(q.parts)):
            try:
                p.rmdir()
            except OSError:
                pass
        raise
    print(f"updated {bd / 'dna.json'}" if existing else f"created {bd} (dna.json, DNA.md, styles/, assets/); the id is '{d['id']}'")
    todo = _status(bd, notes)
    print("also set in dna.json: handle, people (the speaker's name tag), formats, hook, logo, and the music and sound rules")
    return todo


# ------------------------------------------------------------------ CLI

def _run(a):
    if a.cmd == "scan":
        proposal, notes = scan(a.folder)
        print(json.dumps(proposal, indent=2, ensure_ascii=False))
        if notes:
            print("\n".join(notes))
        return 0
    if a.cmd == "init":
        prop, notes = scan(a.src) if a.src else ({}, [])
        if a.font:   # an explicit font is the display font; the one the scan picked is copied along
            prop["fonts"] = [x for x in [prop.get("font")] + prop.get("fonts", []) if x]
            prop["font"] = a.font
        for k in ("accent", "ink", "emblem", "lockup"):
            if getattr(a, k):
                prop[k] = getattr(a, k)
        if a.accent or a.ink:
            prop.pop("on_accent", None)   # worked out again from the accent and ink given
        init(a.folder, a.name, brand_id=a.id, handle=a.handle or "", site=a.site or "", caps=a.caps, notes=notes, **prop)
        return 0
    if a.cmd == "styles" and not a.folder and not common.load_config(require_workspace=False).get("workspace"):   # no workspace yet: the starters are the menu
        print("\n".join(f"{s['id']}\t{s['name']}\t{s['where']}" for s in styles_list()))
        return 0
    bd = brand_folder(a.folder)
    if a.cmd == "check":
        d = load(a.folder)
        problems = validate(d, bd)
        print("\n".join(problems) if problems else f"ok {d['id']}")
        return 2 if problems else 0
    if a.cmd == "todo":
        lines = todo_lines(bd)
        if lines:
            print("\n".join(lines))
        return 0
    if a.cmd == "styles":
        print("\n".join(f"{s['id']}\t{s['name']}\t{s['where']}" for s in styles_list(bd)))
        return 0
    d = load_or_exit(a.folder, strict=False)   # these print what the engines would read; check is the gate for files and DNA.md
    prefix = getattr(a, "prefix", None) or None   # no --prefix, or an empty one: absolute paths
    out = d if a.cmd == "show" else theme(d, bd, prefix) if a.cmd == "theme" else style(d, a.style, bd, prefix) if a.style else captions_profile(d, bd, prefix)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dna.py", description="Read, check and set up the DNA (Brand/dna.json and Brand/DNA.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("check", "show", "captions", "theme", "styles", "todo"):
        p = sub.add_parser(cmd)
        p.add_argument("folder", nargs="?", help="the folder with dna.json (default: the workspace's Brand/)")
        if cmd in ("captions", "theme"):
            p.add_argument("--prefix", help="put this in front of every asset path, e.g. brand-<id> for the renderer's public folder (default: absolute paths)")
        if cmd == "captions":
            p.add_argument("--style", help="lay this caption style over the profile")
    sub.add_parser("scan").add_argument("folder")
    p = sub.add_parser("init")
    p.add_argument("folder")
    p.add_argument("--name", help="the brand's name (required the first time; later it renames)")
    p.add_argument("--from", dest="src")
    for flag in ("id", "handle", "site", "accent", "ink", "font", "emblem", "lockup"):
        p.add_argument("--" + flag)
    p.add_argument("--caps", action="store_true")
    try:
        return _run(ap.parse_args(argv))
    except DnaError as e:
        print(f"dna: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
