"""Every engine command-line script answers a bad invocation the same way: ONE `STOP: ...` line, exit 2, no traceback. And every script refuses to run
under a Python older than 3.12 with that same single line, before anything else in it is read.

The guard and the failure shapes live in engine/support.py, which every script imports first (common.py will take them over; these tests will not
change when it does)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

import common
import support
from helpers_misc import ENGINE, run_engine, stop_lines

NO_PYC = {"PYTHONDONTWRITEBYTECODE": "1"}       # the older interpreters must not leave their own .pyc files in the engine folder
CLIS = ["cut.py", "script.py", "words.py", "wx_align.py", "face.py", "facelock.py", "reframe.py", "matte.py", "avskew.py", "hdr.py", "prep.py", "sound.py", "qc.py",
        "sidecars.py", "swift/swiftbin.py"]
# every module that carries the version guard: the CLIs, support itself, and sfx_synth (which writes files, so it is not run with no arguments)
GUARDED = ["support.py", "sfx_synth.py"] + CLIS
# wx_align.py is the one script with no guard: it runs only under the word-timing python (config.json "venv_python") and imports nothing of the engine
NO_GUARD = pytest.mark.xfail(strict=True, reason="engine/wx_align.py has no Python version guard (it does not import support); see the report")


def marked(names, special=("wx_align.py",)):
    return [pytest.param(n, marks=NO_GUARD) if n in special else n for n in names]


def old_python():
    """the system python3 when it is older than 3.12 (macOS ships 3.9), else None"""
    py = "/usr/bin/python3"
    if not os.path.exists(py):
        return None
    r = subprocess.run([py, "-c", "import sys; print(sys.version_info[0] * 100 + sys.version_info[1])"], capture_output=True, text=True)
    try:
        return py if int(r.stdout.strip()) < 312 else None
    except ValueError:
        return None


OLD = old_python()


# --- 1. no arguments: one STOP line, exit 2, no traceback ----------------------------------------------------------------------------

@pytest.mark.parametrize("script", CLIS)
def test_a_cli_with_no_arguments_stops_instead_of_raising(script):
    """a missing argument used to print a raw IndexError/ValueError traceback and exit 1: no actionable line, and the chain's status recorded the wrong message"""
    r = run_engine(script, env=NO_PYC)
    out = (r.stdout + r.stderr).strip()
    assert r.returncode == 2, f"{script} exited {r.returncode}:\n{out}"
    assert len(stop_lines(r)) == 1, f"{script}:\n{out}"
    assert "Traceback" not in out, f"{script}:\n{out}"
    assert r.stderr.startswith("STOP: ") and not r.stdout.strip(), f"{script} must say it on stderr only:\n{out}"


@pytest.mark.parametrize("script, word", [("cut.py", "cut.py cut"), ("script.py", "script.py build"), ("face.py", "face.py track"), ("matte.py", "matte.py frames"), ("sound.py", "sound.py downmix")])
def test_a_cli_with_no_arguments_prints_its_own_usage(script, word):
    r = run_engine(script, env=NO_PYC)
    assert r.stderr.startswith("STOP: usage: ") and word in r.stderr


@pytest.mark.parametrize("script", ["cut.py", "script.py", "face.py", "matte.py", "sound.py"])
def test_an_unknown_command_is_one_stop_line(script):
    r = run_engine(script, "wander", env=NO_PYC)
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "unknown command 'wander'" in r.stderr and "Traceback" not in r.stderr


def test_run_take_with_too_few_arguments_says_its_usage_and_exits_2():
    r = subprocess.run(["bash", os.path.join(ENGINE, "run_take.sh")], capture_output=True, text=True)
    assert r.returncode == 2 and "run_take.sh" in r.stderr and "Traceback" not in r.stderr


# --- 2. the Python version guard, under a faked old interpreter --------------------------------------------------------------------------

def fake_old_import(module, version="(3, 11, 9, 'final', 0)"):
    path = os.path.join(ENGINE, "swift") if module == "swiftbin" else ENGINE
    code = f"import sys; sys.version_info = {version}; sys.path.insert(0, {path!r}); import {module}"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ, **NO_PYC))


@pytest.mark.parametrize("script", marked(GUARDED))
def test_every_module_refuses_a_python_older_than_3_12_with_one_line(script):
    module = os.path.splitext(os.path.basename(script))[0]
    r = fake_old_import(module)
    assert r.returncode == 2, (module, r.stdout, r.stderr)
    assert len(stop_lines(r)) == 1 and r.stderr.count("\n") == 1 and not r.stdout, r.stdout + r.stderr
    assert "Python 3.12 or newer" in r.stderr and "this is Python 3.11" in r.stderr and "Traceback" not in r.stderr


def test_the_guard_names_the_interpreter_it_found_and_how_to_get_a_newer_one():
    r = fake_old_import("support", "(3, 8, 2, 'final', 0)")
    assert r.returncode == 2 and r.stderr.strip() == "STOP: Cutroom needs Python 3.12 or newer; this is Python 3.8. Install a newer one (brew install python@3.12) and run again."


def test_python_3_12_itself_is_accepted_and_the_guard_costs_nothing():
    r = fake_old_import("support", "(3, 12, 0, 'final', 0)")
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_a_script_run_as_a_file_under_a_faked_old_interpreter_stops_before_its_own_code(tmp_path):
    """the way the chain runs them: `python3 engine/<script>.py ...`, with the guard reached before the usage line it would otherwise print"""
    code = f"import sys, runpy; sys.version_info = (3, 10, 4, 'final', 0); sys.path.insert(0, {ENGINE!r}); runpy.run_path({os.path.join(ENGINE, 'cut.py')!r}, run_name='__main__')"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ, **NO_PYC))
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "Python 3.12 or newer" in r.stderr and "usage" not in r.stderr


def test_run_take_refuses_an_old_python_before_it_makes_anything(tmp_path):
    if OLD is None:
        pytest.skip("no system python older than 3.12 to point it at")
    work = tmp_path / "work"
    r = subprocess.run(["bash", os.path.join(ENGINE, "run_take.sh"), str(work), "t", str(tmp_path / "src.mp4")], capture_output=True, text=True, env=dict(os.environ, CUTROOM_PYTHON=OLD))
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "needs Python 3.12 or newer" in r.stderr and not work.exists(), r.stdout + r.stderr


# --- 3. the older interpreter really running every file: a construct newer than the guard's reach would be a SyntaxError -------------------

@pytest.mark.skipif(OLD is None, reason="no system python older than 3.12 (macOS ships /usr/bin/python3 at 3.9)")
@pytest.mark.parametrize("script", marked(GUARDED))
def test_an_old_python_running_the_file_reaches_the_guard_and_never_a_syntax_error(script):
    r = subprocess.run([OLD, os.path.join(ENGINE, script)], capture_output=True, text=True, env=dict(os.environ, **NO_PYC))
    out = r.stdout + r.stderr
    assert "SyntaxError" not in out and "Traceback" not in out, f"{script} under {OLD}:\n{out}"
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and r.stderr.count("\n") == 1, out
    assert "Python 3.12 or newer" in r.stderr and "this is Python 3." in r.stderr, out


# --- 4. the failure shapes of support.cli ------------------------------------------------------------------------------------------------

def boom(exc):
    def fn():
        raise exc
    return fn


@pytest.mark.parametrize("exc, expect", [
    (IndexError("list index out of range"), "STOP: usage: prog <a> <b>"),
    (ValueError("bad float"), "STOP: usage: prog <a> <b>"),
    (OSError(5, "Input/output error"), "STOP: bad input: OSError: [Errno 5] Input/output error"),
    (FileNotFoundError(2, "No such file or directory", "x.json"), "STOP: bad input: FileNotFoundError"),
    (json.JSONDecodeError("Expecting value", "{", 0), "STOP: bad input: JSONDecodeError"),       # a ValueError subclass: bad input, never "usage"
])
def test_cli_classifies_the_failure_shapes(capsys, exc, expect):
    with pytest.raises(SystemExit) as e:
        support.cli(boom(exc), "prog <a> <b>")
    out, err = capsys.readouterr()
    assert e.value.code == 2 and err.startswith(expect) and err.count("\n") == 1 and out == ""


def test_a_stop_inside_the_script_passes_through_untouched(capsys):
    def fn():
        common.stop("service is down")
    with pytest.raises(SystemExit) as e:
        support.cli(fn, "prog <a> <b>")
    assert e.value.code == 2 and capsys.readouterr().err == "STOP: service is down\n"


def test_an_exit_code_the_script_chose_is_not_rewritten(capsys):
    """the chain branches on cut.py's own exit codes (0 pass, 1 fail, 3 nothing left to try): support.cli must not turn them into 2"""
    for code in (0, 1, 3):
        def fn(c=code):
            sys.exit(c)
        with pytest.raises(SystemExit) as e:
            support.cli(fn, "prog")
        assert e.value.code == code
    assert capsys.readouterr().err == ""


def test_a_script_that_finishes_prints_nothing_of_its_own(capsys):
    support.cli(lambda: None, "prog")
    assert capsys.readouterr() == ("", "")


# --- a cut that ffmpeg cannot render ends the run on a STOP line ---------------------------------------------------------------------------

def test_a_cut_that_ffmpeg_cannot_render_stops_on_a_stop_line_with_exit_2(tmp_path, capsys):
    import cut
    plan = {"src": str(tmp_path / "no-such.mp4"), "output": str(tmp_path / "out" / "cut.mp4")}
    with pytest.raises(SystemExit) as e:
        cut.render(plan, [{"c0": 0.0, "c1": 1.0, "label": "x"}], 30)
    err = capsys.readouterr().err
    assert e.value.code == 2 and err.startswith("STOP: the cut did not render") and "no-such.mp4" in err, err


# --- 5. the settings every script reads: model, word-timing python, language ---------------------------------------------------------

@pytest.fixture
def conf(tmp_path, monkeypatch):
    """point common at a config file in tmp_path; conf(key=value, ...) writes it; no overrides from the environment"""
    path = tmp_path / "config.json"
    monkeypatch.setattr(common, "CONF", path)
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.delenv("CUTROOM_VENV_PYTHON", raising=False)

    def write(**kv):
        path.write_text(json.dumps(kv))
        return path
    return write


def one_stop_line(capsys):
    err = capsys.readouterr().err
    assert err.startswith("STOP: ") and err.count("\n") == 1, err
    return err.strip()


def test_the_model_comes_from_the_config_and_the_environment_wins(conf, tmp_path, monkeypatch):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    conf(whisper_model=str(a))
    assert support.whisper_model() == str(a)
    monkeypatch.setenv("WHISPER_MODEL", str(b))
    assert support.whisper_model() == str(b)


def test_a_model_path_may_start_with_a_tilde(conf, tmp_path, monkeypatch):
    (tmp_path / "m.bin").write_bytes(b"x")
    monkeypatch.setenv("HOME", str(tmp_path))
    conf(whisper_model="~/m.bin")
    assert support.whisper_model() == str(tmp_path / "m.bin")


def test_a_missing_model_is_one_plain_line_that_names_the_path_and_the_way_out(conf, tmp_path, capsys):
    conf(whisper_model=str(tmp_path / "gone.bin"))
    with pytest.raises(SystemExit) as e:
        support.whisper_model()
    line = one_stop_line(capsys)
    assert e.value.code == 2 and str(tmp_path / "gone.bin") in line and "install.sh" in line and "whisper_model" in line


def test_a_missing_model_is_not_an_error_when_the_caller_only_wants_the_path(conf, tmp_path):
    conf(whisper_model=str(tmp_path / "gone.bin"))
    assert support.whisper_model(must_exist=False) == str(tmp_path / "gone.bin")


def test_the_environment_model_is_checked_too(conf, tmp_path, monkeypatch, capsys):
    (tmp_path / "real.bin").write_bytes(b"x")
    conf(whisper_model=str(tmp_path / "real.bin"))
    monkeypatch.setenv("WHISPER_MODEL", str(tmp_path / "env-gone.bin"))
    with pytest.raises(SystemExit):
        support.whisper_model()
    assert str(tmp_path / "env-gone.bin") in one_stop_line(capsys)


def test_the_word_timing_python_comes_from_the_config_and_the_environment_wins(conf, tmp_path, monkeypatch):
    conf(venv_python=sys.executable)
    assert support.venv_python() == sys.executable
    other = shutil.which("python3") or sys.executable
    monkeypatch.setenv("CUTROOM_VENV_PYTHON", other)
    assert support.venv_python() == other


def test_a_missing_or_non_executable_python_is_one_plain_line_that_names_the_path(conf, tmp_path, capsys):
    conf(venv_python=str(tmp_path / "no-python"))
    with pytest.raises(SystemExit):
        support.venv_python()
    line = one_stop_line(capsys)
    assert str(tmp_path / "no-python") in line and "install.sh" in line and "venv_python" in line
    flat = tmp_path / "flat"
    flat.write_text("x")
    flat.chmod(0o644)
    conf(venv_python=str(flat))
    with pytest.raises(SystemExit):
        support.venv_python()
    assert str(flat) in one_stop_line(capsys)
    assert support.venv_python(must_exist=False) == str(flat)


def test_the_language_defaults_to_english_and_accepts_auto(conf):
    assert support.language() == "en"                       # no config file at all
    conf(workspace="/x")
    assert support.language() == "en"
    conf(whisper_language="fr")
    assert support.language() == "fr"
    conf(whisper_language="auto")
    assert support.language() == "auto"
    conf(whisper_language="")
    assert support.language() == "en"
    conf(whisper_language=None)
    assert support.language() == "en"


def test_config_needs_no_workspace_and_fills_the_defaults(conf):
    cfg = support.config()
    assert cfg["whisper_language"] == "en" and cfg["workspace"] == "" and "whisper_model" in cfg and "venv_python" in cfg


def test_a_config_that_is_not_json_is_one_stop_line(conf, tmp_path, capsys):
    conf_path = conf()
    conf_path.write_text("{not json")
    with pytest.raises(SystemExit):
        support.config()
    line = one_stop_line(capsys)
    assert "is not valid JSON" in line and str(conf_path) in line


# --- what the contract still lacks: each of these is a real gap in the engine (see the report), pinned so a fix is noticed -----------------

GAP_SHAPE = pytest.mark.xfail(strict=True, reason="support.cli does not catch KeyError, so a JSON file of the wrong shape ends in a traceback; see the report")
GAP_LINES = pytest.mark.xfail(strict=True, reason="a STOP message embeds the first 200 characters of a tool's own multi-line stderr; see the report")


@pytest.mark.parametrize("args", [
    pytest.param(("cut.py", "cut", "{f}"), marks=GAP_SHAPE, id="cut"),
    pytest.param(("cut.py", "sentences", "{f}", "{o}"), marks=GAP_SHAPE, id="sentences"),
    pytest.param(("cut.py", "plan", "{f}", "{f}", "n", "r", "{d}"), marks=GAP_SHAPE, id="plan"),
    pytest.param(("cut.py", "wx", "{f}", "{o}"), marks=GAP_SHAPE, id="wx"),
    pytest.param(("cut.py", "check-plan", "{f}"), marks=GAP_SHAPE, id="check-plan"),
    pytest.param(("script.py", "build", "{f}", "{f}", "{o}"), marks=GAP_SHAPE, id="script-build"),
    pytest.param(("face.py", "room", "{f}", "{o}"), marks=GAP_SHAPE, id="room"),
])
def test_a_json_file_of_the_wrong_shape_is_one_stop_line_not_a_traceback(tmp_path, args):
    f = tmp_path / "empty.json"
    f.write_text("{}")
    sub = {"{f}": str(f), "{o}": str(tmp_path / "out"), "{d}": str(tmp_path)}
    r = run_engine(args[0], *[sub.get(a, a) for a in args[1:]], env=NO_PYC)
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "Traceback" not in r.stderr, r.stdout + r.stderr


@pytest.mark.parametrize("args", [
    pytest.param(("face.py", "track", "{c}", "{o}"), marks=GAP_LINES, id="face-track"),
    pytest.param(("reframe.py", "{c}", "{o}"), marks=GAP_LINES, id="reframe"),
    pytest.param(("facelock.py", "{c}", "{o}", "--strength", "0.5"), marks=GAP_LINES, id="facelock"),
    pytest.param(("sound.py", "bed", "{c}", "{c}", "{o}"), marks=GAP_LINES, id="sound-bed"),
])
def test_a_stop_message_is_one_whole_line_even_when_a_tool_complains_at_length(tmp_path, args):
    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(b"not a video at all")
    sub = {"{c}": str(bad), "{o}": str(tmp_path / "out.mp4")}
    r = run_engine(args[0], *[sub.get(a, a) for a in args[1:]], env=NO_PYC)
    assert r.returncode == 2 and r.stderr.startswith("STOP: ") and r.stderr.count("\n") == 1, r.stdout + r.stderr
