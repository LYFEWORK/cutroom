"""Shared setup for the caption chain's tests.

CUTROOM_HOME is pointed at a throwaway folder BEFORE any engine module is imported (common.py reads it at import), with a config.json that holds a scratch
workspace and, when this machine has them, the real Whisper model and the word-timing python. Tests that need those two are marked `speech` and skip with one plain
reason when they are missing. Where the two come from, first match wins:

    CUTROOM_TEST_MODEL / CUTROOM_TEST_VENV_PYTHON       explicit paths
    ~/.cutroom/config.json                              what the installer wrote ("whisper_model", "venv_python")
    ~/.cutroom/models/ggml-large-v3-turbo.bin and ~/.cutroom/venv/bin/python     the installer's own defaults

The chain is run on small ffmpeg clips with a seeded words.json and facetrack.json (no model, no Apple Vision, no Remotion): the two fixture looks live in
tests/captions/fixtures/brands/ and cap_helpers.py makes real Brand/ folders from them.
"""
import json
import os
import shutil
import sys
import tempfile

import pytest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ENGINE = os.path.join(REPO, "engine")
CAPTIONS = os.path.join(ENGINE, "captions")
for p in (ENGINE, CAPTIONS, HERE):
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)


def _installed():
    try:
        return json.load(open(os.path.expanduser("~/.cutroom/config.json")))
    except (OSError, ValueError):
        return {}


def _first(*cands):
    for c in cands:
        if c and os.path.isfile(os.path.expanduser(c)):
            return os.path.expanduser(c)
    return None


_inst = _installed()
MODEL = _first(os.environ.get("CUTROOM_TEST_MODEL"), _inst.get("whisper_model"), "~/.cutroom/models/ggml-large-v3-turbo.bin")
VENV_PYTHON = _first(os.environ.get("CUTROOM_TEST_VENV_PYTHON"), _inst.get("venv_python"), "~/.cutroom/venv/bin/python")
HAVE_SPEECH = bool(MODEL and VENV_PYTHON and shutil.which("whisper-cli") and shutil.which("ffmpeg") and shutil.which("say"))

HOME = tempfile.mkdtemp(prefix="captions-test-home-")
WORKSPACE = os.path.join(HOME, "workspace")
os.makedirs(WORKSPACE, exist_ok=True)
with open(os.path.join(HOME, "config.json"), "w") as f:
    json.dump({"workspace": WORKSPACE, "whisper_model": MODEL or os.path.join(HOME, "no-model.bin"),
               "venv_python": VENV_PYTHON or os.path.join(HOME, "no-venv", "bin", "python"), "whisper_language": "en"}, f)
os.environ["CUTROOM_HOME"] = HOME
for k in ("WHISPER_MODEL", "CUTROOM_VENV_PYTHON", "CUTROOM_TRANSCRIBE", "CAPTION_STYLES_DIR", "CAPTION_SCHEMA", "CUTROOM_REMOTION_DIR"):
    os.environ.pop(k, None)

speech = pytest.mark.skipif(not HAVE_SPEECH, reason="needs the Whisper model, the word-timing python and macOS `say` "
                            "(set CUTROOM_TEST_MODEL and CUTROOM_TEST_VENV_PYTHON, or run the installer)")


def pytest_sessionstart(session):
    """Stop at once, with the install line, when ffmpeg is missing: a dozen tests build real clips with it and would
    otherwise each fail with a bare FileNotFoundError."""
    gone = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if gone:
        pytest.exit(f"{' and '.join(gone)} not found on PATH. This suite renders and probes real clips. "
                    f"Install it with: brew install ffmpeg", returncode=2)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(HOME, ignore_errors=True)
    try:
        import cap_helpers
        shutil.rmtree(cap_helpers.ROOT, ignore_errors=True)
    except ImportError:
        pass


@pytest.fixture
def home():
    return HOME


@pytest.fixture
def workspace():
    return WORKSPACE
