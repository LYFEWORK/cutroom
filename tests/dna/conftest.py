import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engine"))
import dna  # noqa: E402

WOFF2 = b"wOF2" + b"\0" * 60   # starts like a font: enough for the check, which reads the signature


def variable_ttf():
    """the bytes of a TrueType header whose one table is fvar: a variable font to the table reader"""
    return b"\x00\x01\x00\x00" + (1).to_bytes(2, "big") + b"\0" * 6 + b"fvar" + b"\0" * 12


def template():
    return json.loads((REPO / "templates" / "dna.json").read_text())


def finished_md():
    return (REPO / "templates" / "DNA.md").read_text().replace("# TODO Brand", "# Acme").replace("TODO:", "Done:")


@pytest.fixture
def brand(tmp_path):
    """a finished Brand/ folder: identity filled, one font file, a DNA.md with no placeholder line. No styles/ folder, so the starters answer"""
    bd = tmp_path / "Brand"
    (bd / "assets" / "fonts").mkdir(parents=True)
    (bd / "assets" / "fonts" / "Acme-Bold.woff2").write_bytes(WOFF2)
    d = template()
    d.update(id="acme", name="Acme", handle="@acme")
    d["fonts"]["display"].update(family="Acme", file="fonts/Acme-Bold.woff2")
    (bd / "dna.json").write_text(json.dumps(d))
    (bd / "DNA.md").write_text(finished_md())
    return bd


def edit(bd, fn):
    """change dna.json in place: fn(the dict) edits it"""
    p = Path(bd) / "dna.json"
    d = json.loads(p.read_text())
    fn(d)
    p.write_text(json.dumps(d))


def check(bd):
    """validate(load(bd)) with every check on"""
    return dna.validate(dna.load(bd), bd)


def add_style(bd, sid, **fields):
    """write Brand/styles/<sid>.json: a plain style with a set block, plus whatever fields are given"""
    (Path(bd) / "styles").mkdir(exist_ok=True)
    st = {"id": sid, "name": sid.title(), "summary": "A test style.", "use_when": "a test needs one", "never_when": "a real video does", "set": {"zone": "chest"}, **fields}
    (Path(bd) / "styles" / f"{sid}.json").write_text(json.dumps(st))
    return st


def run_cli(*args, env=None):
    """engine/dna.py run as a program -> CompletedProcess"""
    return subprocess.run([sys.executable, "-B", str(REPO / "engine" / "dna.py"), *map(str, args)], capture_output=True, text=True, env=env)


def copy_starters(bd):
    (Path(bd) / "styles").mkdir(exist_ok=True)
    for p in (REPO / "templates" / "styles").glob("*.json"):
        shutil.copy(p, Path(bd) / "styles" / p.name)
