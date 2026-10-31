"""cap_helpers.py: shared test helpers: real looks and specs built through the DNA module (engine/dna.py) and lookprofile.py from two neutral fixture brands, never hand-written
font or color dicts.

The fixture brands (tests/captions/fixtures/brands/):

    varsans    mixed case, one variable font (the shipped Inter), no running face, accent yellow, a few places, neg words, icon words, pill icons, a spelling map
               and a do-not-say word; default style soft-build, pace gate report
    upstatic   upper case, a static Inter ExtraBold display face and the variable Inter as its running face, base color a warm white, a chip and a hero stroke,
               a place; default style keyword-slam, pace gate enforce

materialize() makes a real Brand/ folder from one (dna.json, DNA.md, assets/fonts/ with the font files, styles/ with the fixture styles; a style that asks for
the running face is left out of a brand that has none); brand_dir() makes it once per test run under ROOT.
"""
import copy
import json
import os
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FIXTURES = os.path.join(HERE, "fixtures")
BRANDS = ("varsans", "upstatic")
ROOT = tempfile.mkdtemp(prefix="captions-brands-")

TIER_DOC = {"tier": "FULL", "reason": "test", "room": {"above": 500, "below": 500, "left": 100, "right": 100},
            "safe": {"x0": 65, "x1": 940, "y0": 270, "y1": 1420}}

FONT_FILES = {"Inter-Variable.ttf": os.path.join(REPO, "templates", "fonts", "Inter-Variable.ttf"),
              "Inter-ExtraBold.ttf": os.path.join(FIXTURES, "fonts", "Inter-ExtraBold.ttf")}


def materialize(brand_id, dest):
    """A real Brand/ folder for a fixture brand at `dest`: dna.json, DNA.md, assets/fonts/ (both font files), styles/ (every fixture style). Returns dest."""
    src = os.path.join(FIXTURES, "brands", brand_id)
    os.makedirs(os.path.join(dest, "assets", "fonts"), exist_ok=True)
    for name in ("dna.json", "DNA.md"):
        shutil.copyfile(os.path.join(src, name), os.path.join(dest, name))
    for name, path in FONT_FILES.items():
        shutil.copyfile(path, os.path.join(dest, "assets", "fonts", name))
    has_running = bool(json.load(open(os.path.join(src, "dna.json")))["fonts"].get("running"))
    os.makedirs(os.path.join(dest, "styles"), exist_ok=True)
    for name in sorted(os.listdir(os.path.join(FIXTURES, "styles"))):
        doc = json.load(open(os.path.join(FIXTURES, "styles", name)))
        if (doc.get("look") or {}).get("font") == "running" and not has_running:
            continue   # a style for the running face is a DNA problem (dna.py check says so) in a look that has none
        shutil.copyfile(os.path.join(FIXTURES, "styles", name), os.path.join(dest, "styles", name))
    return dest


def brand_dir(brand_id="varsans"):
    """The materialized Brand/ folder of a fixture brand, made once per run (never written to by a test: copy it first)."""
    dest = os.path.join(ROOT, brand_id, "Brand")
    if not os.path.isdir(dest):
        materialize(brand_id, dest)
    return dest


def dna_of(brand_id="varsans"):
    """The brand's DNA dict as engine/dna.py loads it (the template fills what dna.json leaves out)."""
    import dna
    return dna.load(brand_dir(brand_id))


def profile(brand_id="varsans", style=None):
    """lookprofile.build() of a fixture brand: what look.json holds in a take (the DNA's captions profile, a style over it, the chain's extra keys)."""
    import lookprofile
    return lookprofile.build(dna_of(brand_id), brand_dir(brand_id), style)


def spec(brand_id="varsans", tier="FULL", gate=None, style=None, **over):
    """The spec.json spec_adapter builds for a fixture brand; `over` replaces top-level keys."""
    import spec_adapter
    s = spec_adapter.build(dict(TIER_DOC, tier=tier), profile(brand_id, style), gate=gate)
    s.update(over)
    return copy.deepcopy(s)


def font(brand_id="varsans"):
    """spec["font"] for a fixture brand: {family, faces, measure}."""
    return spec(brand_id)["font"]


def install_brand(workspace, brand_id="varsans", edit=None):
    """Put a fixture brand's Brand/ folder into a workspace (replacing any there); edit(dna_dict) may change its dna.json first. Returns the Brand/ path."""
    dest = os.path.join(workspace, "Brand")
    shutil.rmtree(dest, ignore_errors=True)
    materialize(brand_id, dest)
    if edit:
        p = os.path.join(dest, "dna.json")
        d = json.load(open(p))
        edit(d)
        with open(p, "w") as f:
            json.dump(d, f, indent=2)
    return dest
