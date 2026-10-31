"""Constants and markers the study tests share (a module of its own, so no test imports another folder's conftest)."""
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine"
STUDY = ENGINE / "study.py"
if str(ENGINE) not in sys.path:
    sys.path.append(str(ENGINE))
def _model():
    """the Whisper model the study tests transcribe with: the one the environment names, else the installed Cutroom's (config.json), else the two
    usual folders (install.sh's models folder, then ~/whisper-models)"""
    cfg = Path(os.environ.get("CUTROOM_HOME") or "~/.cutroom").expanduser() / "config.json"
    try:
        installed = json.loads(cfg.read_text()).get("whisper_model")
    except (OSError, ValueError, AttributeError):
        installed = None
    for p in (os.environ.get("CUTROOM_TEST_WHISPER_MODEL"), installed, "~/.cutroom/models/ggml-large-v3-turbo.bin", "~/whisper-models/ggml-large-v3-turbo.bin"):
        if p and Path(p).expanduser().is_file():
            return Path(p).expanduser()
    return Path("~/whisper-models/ggml-large-v3-turbo.bin").expanduser()


def installed_config():
    """the installed Cutroom's config.json as a dict ({} when there is none): the tests' own config home copies what a tool shell needs from it"""
    cfg = Path(os.environ.get("CUTROOM_HOME") or "~/.cutroom").expanduser() / "config.json"
    try:
        return json.loads(cfg.read_text())
    except (OSError, ValueError):
        return {}


MODEL = _model()
HAVE_WHISPER = bool(shutil.which("whisper-cli")) and MODEL.is_file()
HAVE_VISION = sys.platform == "darwin" and bool(shutil.which("swiftc"))
needs_vision = pytest.mark.skipif(not HAVE_VISION, reason="reading on-screen text needs Apple Vision and swiftc")
needs_whisper = pytest.mark.skipif(not (HAVE_WHISPER and shutil.which("say")), reason="needs whisper-cli, its model and the say command")

# the text cards of the synthetic reel: (words, shown from, shown until), and the mark that never leaves
TEXTS = [("FIRST WORDS HERE", 0.4, 3.4), ("SECOND CLAIM LANDS", 5.4, 8.4), ("THIRD POINT MADE", 10.4, 13.4), ("LAST CALL TO ACT", 15.4, 18.4)]
WATERMARK = "@WATERMARKHANDLE"


class LazyModule:
    """stands for a module until a test uses it: collecting these tests then imports nothing from the engine, so another test folder that
    wants to import `common` first (after setting CUTROOM_HOME) still can. Reads and writes go to the real module, so monkeypatch works."""

    def __init__(self, name):
        object.__setattr__(self, "_name", name)

    def _real(self):
        return importlib.import_module(object.__getattribute__(self, "_name"))

    def __getattr__(self, key):
        if key.startswith("_"):   # pytest probes module members for markers and fixtures: that must not import the engine
            raise AttributeError(key)
        return getattr(self._real(), key)

    def __setattr__(self, key, value):
        setattr(self._real(), key, value)

    def __delattr__(self, key):
        delattr(self._real(), key)


study = LazyModule("study")
