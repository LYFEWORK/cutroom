#!/usr/bin/env python3
"""verify_proxies.py: check that each proxy really shows its original, so a kept range means the same thing on both.

    python3 verify_proxies.py [--all] [--ids ID ...]

Takes a 32 x 32 grey frame from the proxy and from the original at 30% and 90% of the clip and compares them. A big gap means
the proxy is the wrong picture, the wrong stream, or out of step with the original, and ranges set on it would land in the
wrong place. Exit 0 when every clip matches, 1 when any does not. Ingest runs this for the proxies it just made and prints a
WARN line per mismatch.
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402

POINTS = (0.30, 0.90)
SIZE = 32
TOLERANCE = 22.0      # mean difference on a 0 to 255 scale. Compression and colour range stay well under it; a wrong picture does not.


def grey_frame(path: str, t: float, stream: str) -> bytes:
    r = common.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-map", stream, "-frames:v", "1",
                    "-vf", f"scale={SIZE}:{SIZE},format=gray", "-f", "rawvideo", "-"], check=False, text=False)
    return r.stdout if isinstance(r.stdout, bytes) else b""


def gap(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def check_clip(clip: dict, proxy: Path, src: str) -> tuple[bool, str]:
    dur = float(clip.get("dur") or 0)
    if dur <= 0:
        return True, ""
    stream = f"0:{clip['vindex']}" if clip.get("vindex") is not None else "0:v:0"
    worst = 0.0
    for p in POINTS:
        t = dur * p
        g = gap(grey_frame(str(proxy), t, "0:v:0"), grey_frame(src, t, stream))
        worst = max(worst, g)
    return worst <= TOLERANCE, f"frames differ by {worst:.0f} of 255"


def verify(clips: list[dict], ids=None, jobs: int = 4, say=print) -> list[dict]:
    """Returns one {"id", "name", "why"} per clip that does not match its original."""
    want = set(ids) if ids is not None else None
    todo = []
    for c in clips:
        if want is not None and c["id"] not in want:
            continue
        proxy = store.d_proxies() / f"{c['id']}.mp4"
        src = next((p for p in store.abs_paths(c) if os.path.exists(p)), None)
        if proxy.exists() and src:
            todo.append((c, proxy, src))
    bad = []

    def work(item):
        c, proxy, src = item
        ok, why = check_clip(c, proxy, src)
        return c, ok, why

    with ThreadPoolExecutor(max(1, jobs)) as ex:
        for c, ok, why in ex.map(work, todo):
            if not ok:
                bad.append({"id": c["id"], "name": c["name"], "why": why})
                say(f"WARN: the preview of {c['name']} does not look like the original ({why}). Marks set on it may land in the wrong place.")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare each preview with its original.")
    ap.add_argument("--all", action="store_true", help="check every clip (default: the same)")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    clips = store.load_inventory()["clips"]
    bad = verify(clips, ids=a.ids, jobs=a.jobs)
    n = len([c for c in clips if a.ids is None or c["id"] in set(a.ids)])
    print(f"{n - len(bad)} of {n} previews match their originals" if not bad else f"{len(bad)} of {n} previews do not match")
    return 1 if bad else 0


if __name__ == "__main__":
    store.run_cli(main)
