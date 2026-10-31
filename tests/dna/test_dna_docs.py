"""engine/dna_README.md says what the schema, the template and the two views say."""
import json

import dna
from conftest import REPO, WOFF2, edit, template

README = REPO / "engine" / "dna_README.md"
LEAVES = {"fonts.running", "fonts.mono", "captions.hero_stroke"}      # same shape as another row; documented as one row


def schema():
    return json.loads((REPO / "templates" / "schema.json").read_text())


def fields(sch, root, path=""):
    """every documented field path: leaves, array fields (people[].name) and the arrays themselves"""
    sch = dna._deref(sch, root) if "$ref" in sch else sch
    if path in LEAVES:
        yield path
    elif "properties" in sch:
        for k, v in sch["properties"].items():
            yield from fields(v, root, f"{path}.{k}" if path else k)
    elif sch.get("type") == "array" and path:
        yield path
        items = dna._deref(sch["items"], root) if "$ref" in sch.get("items", {}) else sch.get("items", {})
        for k, v in items.get("properties", {}).items():
            yield from fields(v, root, f"{path}[].{k}")
    elif path:
        yield path


def test_the_readme_has_a_row_for_every_field_of_the_schema():
    s, text = schema(), README.read_text()
    paths = list(fields(s, s))
    assert len(paths) > 100
    assert [p for p in paths if f"| `{p}` |" not in text] == []
    assert "`captions.pace_gate`" in text and "`motion.auto_punches`" in text


def test_the_readme_names_every_key_of_both_views_and_of_a_styled_profile(brand):
    for n in ("Cond.woff2", "Mono.woff2"):
        (brand / "assets" / "fonts" / n).write_bytes(WOFF2)
    edit(brand, lambda d: d["fonts"].update(running={"family": "Acme Condensed", "file": "fonts/Cond.woff2"}, mono={"family": "Acme Mono", "file": "fonts/Mono.woff2"}))
    D, text = dna.load(brand), README.read_text()
    keys = set(dna.captions_profile(D, brand)) | set(dna.style(D, "quiet", brand)) | set(dna.theme(D, brand))
    assert [k for k in sorted(keys) if f"`{k}`" not in text] == []


def test_the_readme_says_how_it_ships_and_carries_no_house_text():
    text = README.read_text()
    assert "—" not in text and "one source of truth" in text and "no separate defaults file" in text
    assert "python3 engine/dna.py check" in text and "python3 engine/dna.py init" in text
