#!/usr/bin/env python3
"""swiftbin.py: find the vision helper for this machine: on a Mac the Apple helper, built when it is missing or older than its source; on
Linux (Windows through WSL2) the stand-in of the same name in engine/vision/, which answers the same command line.

    swiftbin.py <helper-dir>...    build (or confirm) each helper; the binary is <helper-dir>/bin/<name of the dir>
    swiftbin.py --all [root]       every folder under root (default: the folder this file is in) that holds a main.swift

In code: import swiftbin; BIN = swiftbin.ensure(os.path.join(HERE, "facerect")). The compile goes to a temp file and is moved into place,
so two lanes starting at once never run a half-written binary, and a source that changed is rebuilt on the next use instead of keeping a
stale binary. A failed build stops with swiftc's first lines. One line is printed per helper: "ok <binary path>". Binaries are never
committed (engine/swift/*/bin/ is ignored); only main.swift ships. The compiler is swiftc from the Xcode command-line tools.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import support  # noqa: E402
import common  # noqa: E402

VISION = os.path.join(os.path.dirname(HERE), "vision")   # the Linux stand-ins, one executable per helper name
IS_MAC = sys.platform == "darwin"


def stand_in(helper_dir):
    """the Linux stand-in for a helper folder (engine/vision/<name>), made executable; one STOP line when there is none"""
    name = os.path.basename(os.path.abspath(helper_dir))
    shim = os.path.join(VISION, name)
    if not os.path.isfile(shim):
        common.stop(f"no stand-in for the {name} helper on this platform (engine/vision/{name} is missing)")
    if not os.access(shim, os.X_OK):
        os.chmod(shim, 0o755)
    return shim


def check_stand_in(helper_dir):
    """run the stand-in's --check: its `ok` line, or one STOP line"""
    shim = stand_in(helper_dir)
    r = subprocess.run([shim, "--check"], capture_output=True, text=True)
    if r.returncode != 0:
        line = next((x for x in (r.stderr + r.stdout).splitlines() if x.startswith("STOP:")), None)
        common.stop(line[5:].strip() if line else f"{os.path.basename(shim)} --check failed: {' '.join((r.stderr + r.stdout).split())[-200:]}")
    return shim


def binary_path(helper_dir):
    helper_dir = os.path.abspath(helper_dir)
    return os.path.join(helper_dir, "bin", os.path.basename(helper_dir))


def ensure(helper_dir):
    """The path of the helper's binary, built first when it is missing or older than main.swift; on Linux, the stand-in."""
    helper_dir = os.path.abspath(helper_dir)
    if not IS_MAC:
        return stand_in(helper_dir)
    src, bin_ = os.path.join(helper_dir, "main.swift"), binary_path(helper_dir)
    if not os.path.isfile(src):
        common.stop(f"no main.swift in {helper_dir}")
    if os.path.isfile(bin_) and os.access(bin_, os.X_OK) and os.path.getmtime(bin_) >= os.path.getmtime(src):
        return bin_
    os.makedirs(os.path.dirname(bin_), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".swiftbin-", dir=os.path.dirname(bin_))
    os.close(fd)
    try:
        try:
            r = subprocess.run(["swiftc", "-O", "-o", tmp, src], capture_output=True, text=True)
        except OSError as e:
            common.stop(f"swiftc is missing ({e}): run xcode-select --install, then try again")
        if r.returncode != 0:
            common.stop(f"swiftc could not build {os.path.basename(helper_dir)}: {' '.join(r.stderr.split())[:300]}")
        os.chmod(tmp, 0o755)
        os.replace(tmp, bin_)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return bin_


def helpers(root=HERE):
    """every folder directly under root that holds a main.swift, sorted"""
    return sorted(os.path.join(root, d) for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "main.swift")))


def _main():
    args = sys.argv[1:]
    if args and args[0] == "--all":
        dirs = helpers(args[1] if len(args) > 1 else HERE)
    else:
        dirs = args
    if not dirs:
        raise IndexError("no helpers")
    for d in dirs:
        print(f"ok {ensure(d) if IS_MAC else check_stand_in(d)}")


if __name__ == "__main__":
    support.cli(_main, "swiftbin.py <helper-dir>... | swiftbin.py --all [root]")
