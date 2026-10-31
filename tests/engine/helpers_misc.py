"""Shared helpers for the avskew, face, reframe, matte, swiftbin, sidecars and CLI-contract tests: stub executables, a way to run an engine script in a
subprocess, and synthetic stalled captures. Everything is made with ffmpeg's lavfi sources; no media is stored."""
import os
import shutil
import subprocess
import sys

import pytest

import common
from helpers import sh

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "engine")


def _real_file(p):
    p = os.path.expanduser(str(p or ""))
    return p if p and os.path.isfile(p) else None


# The word-timing python and the Whisper model as the test config names them (conftest.py wrote that config); None when this machine has no such file.
# Read here, not imported from conftest, so the name `conftest` never has to resolve to the right folder's file in a whole-suite run.
_CFG = common.load_config(require_workspace=False)
VENV_PYTHON = _real_file(_CFG.get("venv_python"))
MODEL = _real_file(_CFG.get("whisper_model"))
HAVE_SPEECH = bool(MODEL and VENV_PYTHON and shutil.which("whisper-cli") and shutil.which("ffmpeg") and shutil.which("say"))
speech = pytest.mark.skipif(not HAVE_SPEECH, reason="needs the Whisper model, the word-timing python and macOS `say` "
                            "(set CUTROOM_TEST_MODEL and CUTROOM_TEST_VENV_PYTHON, or run the installer)")


def stub_exe(path, body):
    """an executable python script at `path` (a #! line to this interpreter, then `body`): a stand-in for an Apple helper"""
    path = str(path)
    with open(path, "w") as f:
        f.write(f"#!{sys.executable}\n{body}\n")
    os.chmod(path, 0o755)
    return path


FACERECT_ONE_FACE = "import sys\nfor f in sys.argv[1:]:\n    print(f + '\\t100\\t200\\t120\\t120')\n"
FACERECT_NO_FACE = "import sys\nfor f in sys.argv[1:]:\n    print(f + '\\tnone')\n"


def run_engine(script, *args, python=None, cwd=None, env=None):
    """run engine/<script> in a subprocess; returns the CompletedProcess (text mode)"""
    e = dict(os.environ, **(env or {}))
    return subprocess.run([python or sys.executable, os.path.join(ENGINE, script), *[str(a) for a in args]], capture_output=True, text=True, cwd=cwd, env=e)


def stop_lines(r):
    """the STOP lines a finished process printed, stdout and stderr together"""
    return [l for l in (r.stdout + r.stderr).splitlines() if l.startswith("STOP:")]


def stalled_capture(path, stall_s=3.0, dur=2):
    """a capture whose video timestamps jump `stall_s` seconds forward after `dur`/2 seconds of picture while its audio runs on unbroken for `dur`
    seconds: the shape of a phone capture that stalled. Its video span is dur/2 + stall_s long, its audio dur."""
    half = int(dur * 30 / 2)
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={dur}", "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
            "-vf", f"setpts='if(gte(N,{half}),PTS+{stall_s}/TB,PTS)'", "-fps_mode", "passthrough", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "0",
            "-video_track_timescale", "30000", "-c:a", "aac", str(path)])
    assert r.returncode == 0, r.stderr
    return str(path)


def conformed_mezzanine(src, out, nominal=30.0):
    """what prep.py builds from a stalled capture: frames renumbered contiguously at the nominal rate, so the stall time is dropped"""
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0", "-vf", f"setpts=N/{nominal}/TB,scale=320:240,fps=30,format=yuv420p",
            "-fps_mode", "cfr", "-shortest", "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-c:a", "aac", "-ar", "48000", str(out)])
    assert r.returncode == 0, r.stderr
    return str(out)


def naive_mezzanine(src, out):
    """what a plain `fps=30` makes of a stalled capture: the stall is filled with copies of one frame"""
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0", "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-c:a", "aac", "-ar", "48000", str(out)])
    assert r.returncode == 0, r.stderr
    return str(out)
