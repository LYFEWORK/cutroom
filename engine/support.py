"""support.py: what every engine script needs before it does anything else. Import it first: `import support`.

  the version check      Python 3.12 or newer, or one STOP line and exit 2 (runs at import, before the rest of the script is read)
  cli(fn, usage)         runs a script's main body under the engine's stop contract: every failure is ONE "STOP: ..." line and exit 2,
                         never a traceback. A missing or malformed argument is "STOP: usage: <usage>", an unreadable or corrupt file, or a JSON file missing a key, is
                         "STOP: bad input: ..."; stop() calls inside fn pass through untouched
  whisper_model()        path of the ggml model: the WHISPER_MODEL environment variable, else config.json "whisper_model"
  venv_python()          path of the python that has whisperx and scipy: CUTROOM_VENV_PYTHON, else config.json "venv_python"
  language()             spoken language for the word engine: config.json "whisper_language" ("en" by default, "auto" allowed)
  config()               config.json as a dict (no workspace needed)

These four helpers belong in common.py; they live here until they are moved (one import line per script changes, nothing else).
"""
import sys

if sys.version_info < (3, 12):   # plain code only above this line: it has to compile on the oldest Python that might run it
    print("STOP: Cutroom needs Python 3.12 or newer; this is Python %d.%d. Install a newer one (brew install python@3.12) and run again."
          % (sys.version_info[0], sys.version_info[1]), file=sys.stderr)
    sys.exit(2)

import json
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def cli(fn, usage):
    """Run one script's main body under the stop contract. JSONDecodeError is a ValueError, so the bad-input clause has to come first."""
    try:
        fn()
    except (OSError, json.JSONDecodeError) as e:
        common.stop(f"bad input: {type(e).__name__}: {e}")
    except KeyError as e:
        common.stop(f"bad input: a file is missing the key {e} (the wrong kind of JSON file, or one written by an older run)")
    except (IndexError, ValueError):
        common.stop(f"usage: {usage}")


def config():
    return common.load_config(require_workspace=False)


def whisper_model(must_exist=True):
    p = Path(os.environ.get("WHISPER_MODEL") or config()["whisper_model"]).expanduser()
    if must_exist and not p.is_file():
        common.stop(f"the Whisper model is not at {p}: run install.sh (it downloads it) or set whisper_model in {common.CONF}")
    return str(p)


def venv_python(must_exist=True):
    p = Path(os.environ.get("CUTROOM_VENV_PYTHON") or config()["venv_python"]).expanduser()
    if must_exist and not (p.is_file() and os.access(p, os.X_OK)):
        common.stop(f"the word-timing python is not at {p}: run install.sh (it builds the environment) or set venv_python in {common.CONF}")
    return str(p)


def language():
    return str(config().get("whisper_language") or "en")
