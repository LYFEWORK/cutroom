#!/usr/bin/env python3
"""swiftbin.py: build an Apple helper when it is missing or older than its source.

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


def binary_path(helper_dir):
    helper_dir = os.path.abspath(helper_dir)
    return os.path.join(helper_dir, "bin", os.path.basename(helper_dir))


def ensure(helper_dir):
    """The path of the helper's binary, built first when it is missing or older than main.swift."""
    helper_dir = os.path.abspath(helper_dir)
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
        print(f"ok {ensure(d)}")


if __name__ == "__main__":
    support.cli(_main, "swiftbin.py <helper-dir>... | swiftbin.py --all [root]")
