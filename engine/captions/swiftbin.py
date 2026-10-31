#!/usr/bin/env python3
"""swiftbin.py: find the vision helper for this machine: on a Mac the Apple helper, built when it is missing or older than its source; on
Linux (Windows through WSL2) the stand-in of the same name in engine/vision/, which answers the same command line.

    swiftbin.py <helper-dir>...   build (or confirm) each helper; the binary is <helper-dir>/bin/<name of the dir>
    swiftbin.py --all [root]      every folder under root (default engine/swift) that holds a main.swift

In code: import swiftbin; BIN = swiftbin.ensure(os.path.join(SWIFT, "facerect")). The compile goes to a temp file and is moved into
place, so two lanes starting at once never run a half-written binary, and a source that changed is rebuilt on the next use instead of
keeping a stale binary. Raises EngineStop with swiftc's first lines when the build fails. The binary folder is ignored by git: only
main.swift ships. This is the same layout engine/swift/swiftbin.py uses, so the two build the same file.
"""
import glob
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from keys import cli, EngineStop  # noqa: E402

SWIFT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "engine", "swift")
VISION = os.path.join(os.path.dirname(SWIFT), "vision")
IS_MAC = sys.platform == "darwin"


def stand_in(helper_dir):
    """the Linux stand-in for a helper folder (engine/vision/<name>), made executable; EngineStop when there is none"""
    name = os.path.basename(os.path.abspath(helper_dir))
    shim = os.path.join(VISION, name)
    if not os.path.isfile(shim):
        raise EngineStop(f"no stand-in for the {name} helper on this platform (engine/vision/{name} is missing)")
    if not os.access(shim, os.X_OK):
        os.chmod(shim, 0o755)
    return shim


def binary_path(helper_dir):
    helper_dir = os.path.abspath(helper_dir)
    return os.path.join(helper_dir, "bin", os.path.basename(helper_dir))


def ensure(helper_dir):
    helper_dir = os.path.abspath(helper_dir)
    if not IS_MAC:
        return stand_in(helper_dir)
    src, bin_ = os.path.join(helper_dir, "main.swift"), binary_path(helper_dir)
    if not os.path.isfile(src):
        raise EngineStop(f"no main.swift in {helper_dir}")
    if os.path.isfile(bin_) and os.access(bin_, os.X_OK) and os.path.getmtime(bin_) >= os.path.getmtime(src):
        return bin_
    os.makedirs(os.path.dirname(bin_), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".swiftbin-", dir=os.path.dirname(bin_))
    os.close(fd)
    try:
        try:
            r = subprocess.run(["swiftc", "-O", "-o", tmp, src], capture_output=True, text=True)
        except OSError as e:
            raise EngineStop(f"swiftc is missing ({e}); run xcode-select --install")
        if r.returncode != 0:
            raise EngineStop(f"swiftc could not build {os.path.basename(helper_dir)}: {r.stderr.strip()[:300]}")
        os.chmod(tmp, 0o755)
        os.replace(tmp, bin_)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return bin_


def _main():
    args = sys.argv[1:]
    if args and args[0] == "--all":
        root = args[1] if len(args) > 1 else SWIFT
        dirs = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(root, "*", "main.swift")))
    else:
        dirs = args
    if not dirs:
        raise IndexError("no helpers")
    for d in dirs:
        print(f"ok {ensure(d)}")


if __name__ == "__main__":
    cli(_main, "swiftbin.py <helper-dir>... | --all [root]")
