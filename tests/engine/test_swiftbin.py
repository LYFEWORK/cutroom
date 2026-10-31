"""swiftbin.py: build an Apple helper when it is missing or older than its source, into <helper>/bin/<name>, where git ignores it. The compiler is replaced by
a fake swiftc that copies main.swift and counts its calls, except in the tests that say they build the real helpers."""
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

import swiftbin
from helpers_misc import ENGINE, run_engine, stop_lines

SWIFT = os.path.join(ENGINE, "swift")
REPO = os.path.dirname(ENGINE)
SHIPPED = {"facepts", "facerect", "hdr2sdr", "personmask", "personmatte", "textboxes"}


def fake_swiftc(tmp_path, monkeypatch, fail=False, slow=0.0):
    """a swiftc that copies main.swift to the -o path and counts its calls; returns (calls(), the directory the fake lives in)"""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    counter = tmp_path / "calls"
    counter.write_text("")
    sc = bindir / "swiftc"
    sc.write_text(f'#!/bin/bash\necho x >> "{counter}"\n' + ('echo "boom: syntax" >&2; exit 1\n' if fail else
                  f'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; -O) shift ;; *) src="$1"; shift ;; esac; done\n'
                  + (f"sleep {slow}\n" if slow else "") + 'cp "$src" "$out"\n'))
    sc.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return (lambda: len(counter.read_text().split())), str(bindir)


def helper(tmp_path, name="facerect", text="// helper"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "main.swift").write_text(text)
    return str(d)



@pytest.fixture(autouse=True)
def the_swift_build_path(monkeypatch):
    """these tests are about building the Apple helpers with swiftc (faked below); off a Mac the locator would hand out engine/vision/ stand-ins instead,
    so the Mac branch is forced in this process. The tests that run swiftbin.py as a subprocess skip off a Mac."""
    monkeypatch.setattr(swiftbin, "IS_MAC", True)

def stops(capsys, fn, *args):
    with pytest.raises(SystemExit) as e:
        fn(*args)
    assert e.value.code == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("STOP: ") and err.count("STOP:") == 1
    return err


def test_a_missing_binary_is_built_then_reused_then_rebuilt_when_the_source_is_newer(tmp_path, monkeypatch):
    calls, _ = fake_swiftc(tmp_path, monkeypatch)
    d = helper(tmp_path)
    b = swiftbin.ensure(d)
    assert b == os.path.join(d, "bin", "facerect") and os.access(b, os.X_OK) and calls() == 1
    assert swiftbin.binary_path(d) == b
    swiftbin.ensure(d)
    assert calls() == 1                                           # fresh: not rebuilt
    later = time.time() + 5
    os.utime(os.path.join(d, "main.swift"), (later, later))       # a git pull edited the .swift
    swiftbin.ensure(d)
    assert calls() == 2
    for where in (d, os.path.join(d, "bin")):
        assert not [f for f in os.listdir(where) if f.startswith(".swiftbin-")], f"a temp file was left behind in {where}"


def test_the_binary_is_the_compiled_source(tmp_path, monkeypatch):
    fake_swiftc(tmp_path, monkeypatch)
    d = helper(tmp_path, text="// version one")
    assert open(swiftbin.ensure(d)).read() == "// version one"


def test_a_binary_that_is_not_executable_is_built_again(tmp_path, monkeypatch):
    calls, _ = fake_swiftc(tmp_path, monkeypatch)
    d = helper(tmp_path)
    b = swiftbin.ensure(d)
    os.chmod(b, 0o644)
    assert os.access(swiftbin.ensure(d), os.X_OK) and calls() == 2


def test_a_failed_build_stops_with_swiftcs_words_and_leaves_no_half_binary(tmp_path, monkeypatch, capsys):
    fake_swiftc(tmp_path, monkeypatch, fail=True)
    d = helper(tmp_path)
    err = stops(capsys, swiftbin.ensure, d)
    assert err == "STOP: swiftc could not build facerect: boom: syntax" and not os.path.exists(os.path.join(d, "bin", "facerect"))
    assert not [f for f in os.listdir(os.path.join(d, "bin")) if f.startswith(".swiftbin-")]


def test_a_failed_rebuild_leaves_the_old_binary_alone(tmp_path, monkeypatch, capsys):
    fake_swiftc(tmp_path, monkeypatch)
    d = helper(tmp_path, text="// good")
    b = swiftbin.ensure(d)
    fake_swiftc(tmp_path, monkeypatch, fail=True)
    later = time.time() + 5
    os.utime(os.path.join(d, "main.swift"), (later, later))
    stops(capsys, swiftbin.ensure, d)
    assert open(b).read() == "// good" and os.access(b, os.X_OK)


def test_no_source_stops(tmp_path, capsys):
    (tmp_path / "x").mkdir()
    assert "no main.swift in" in stops(capsys, swiftbin.ensure, str(tmp_path / "x"))


def test_a_missing_compiler_is_one_plain_line_that_says_how_to_get_it(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    err = stops(capsys, swiftbin.ensure, helper(tmp_path))
    assert "swiftc is missing" in err and "xcode-select --install" in err


def test_two_lanes_starting_at_once_never_run_a_half_written_binary(tmp_path, monkeypatch):
    calls, _ = fake_swiftc(tmp_path, monkeypatch, slow=0.3)
    d = helper(tmp_path, text="// whole")
    got, errors = [], []

    def lane():
        try:
            b = swiftbin.ensure(d)
            got.append((b, open(b).read(), os.access(b, os.X_OK)))
        except BaseException as e:                     # noqa: BLE001: the lane's own failure is what the assertion below reports
            errors.append(e)
    threads = [threading.Thread(target=lane) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(got) == 4 and {g[1] for g in got} == {"// whole"} and all(g[2] for g in got)
    assert not [f for f in os.listdir(os.path.join(d, "bin")) if f.startswith(".swiftbin-")] and os.listdir(os.path.join(d, "bin")) == ["facerect"]


def test_helpers_lists_the_folders_that_hold_a_main_swift_sorted(tmp_path):
    for n in ("zeta", "alpha", "mid"):
        helper(tmp_path, n)
    (tmp_path / "no_source").mkdir()
    (tmp_path / "note.txt").write_text("x")
    assert swiftbin.helpers(str(tmp_path)) == [str(tmp_path / n) for n in ("alpha", "mid", "zeta")]


# --- the command line ---------------------------------------------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "darwin", reason="the Swift build path is a Mac's")
def test_all_builds_every_helper_under_a_root_and_prints_one_ok_line_each(tmp_path, monkeypatch):
    calls, bindir = fake_swiftc(tmp_path, monkeypatch)
    root = tmp_path / "root"
    root.mkdir()
    for n in ("beta", "alpha"):
        helper(root, n)
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    r = run_engine("swift/swiftbin.py", "--all", root, env=env)
    assert r.returncode == 0 and r.stdout.splitlines() == [f"ok {root / n / 'bin' / n}" for n in ("alpha", "beta")], r.stdout + r.stderr
    assert calls() == 2
    r = run_engine("swift/swiftbin.py", "--all", root, env=env)
    assert r.returncode == 0 and len(r.stdout.splitlines()) == 2 and calls() == 2           # nothing is built twice


@pytest.mark.skipif(sys.platform != "darwin", reason="the Swift build path is a Mac's; off a Mac the locator hands out the stand-ins (tests/engine/test_vision.py)")
def test_named_helper_folders_work_without_all(tmp_path, monkeypatch):
    _, bindir = fake_swiftc(tmp_path, monkeypatch)
    r = run_engine("swift/swiftbin.py", helper(tmp_path, "one"), helper(tmp_path, "two"), env={"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"})
    assert r.returncode == 0 and [l.split("/")[-1] for l in r.stdout.splitlines()] == ["one", "two"]


def test_all_over_a_root_with_no_helper_is_one_usage_line(tmp_path):
    r = run_engine("swift/swiftbin.py", "--all", tmp_path)
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and r.stderr.startswith("STOP: usage: swiftbin.py"), r.stdout + r.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="the Swift build path is a Mac's")
def test_a_helper_that_does_not_build_is_one_stop_line_naming_it(tmp_path, monkeypatch):
    _, bindir = fake_swiftc(tmp_path, monkeypatch, fail=True)
    r = run_engine("swift/swiftbin.py", helper(tmp_path, "broken"), env={"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"})
    assert r.returncode == 2 and len(stop_lines(r)) == 1 and "swiftc could not build broken: boom: syntax" in r.stderr


# --- what ships --------------------------------------------------------------------------------------------------------------------------

def test_the_shipped_helpers_are_sources_only_and_all_there():
    names = {os.path.basename(d) for d in swiftbin.helpers()}
    assert SHIPPED <= names
    for n in SHIPPED:
        top = set(os.listdir(os.path.join(SWIFT, n)))
        assert "main.swift" in top and top <= {"main.swift", "bin"}, (n, top)            # a compiled binary only ever sits in bin/
        assert os.path.getsize(os.path.join(SWIFT, n, "main.swift")) > 0


@pytest.mark.skipif(not shutil.which("git") or not os.path.isdir(os.path.join(REPO, ".git")), reason="needs git and the repository's own .git folder")
@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_the_binary_lands_where_git_ignores_it(name):
    rel = os.path.relpath(swiftbin.binary_path(os.path.join(SWIFT, name)), REPO)
    assert rel == f"engine/swift/{name}/bin/{name}"
    assert subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO).returncode == 0, f"{rel} would be committed"
    assert subprocess.run(["git", "check-ignore", "-q", f"engine/swift/{name}/main.swift"], cwd=REPO).returncode == 1


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="needs macOS and swiftc")
def test_the_shipped_helpers_build_with_the_real_compiler():
    """what install.sh and the doctor run: one `ok <binary>` line per helper, each binary executable"""
    r = run_engine("swift/swiftbin.py", "--all")
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert {l.split("/")[-1] for l in lines} >= SHIPPED and all(l.startswith("ok ") and os.access(l[3:], os.X_OK) for l in lines)
