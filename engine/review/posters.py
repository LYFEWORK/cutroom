#!/usr/bin/env python3
"""posters.py: a thumbnail for every clip, and an optional filmstrip for the trim bar. Both come from the proxy, so it is quick.

    python3 posters.py [--no-filmstrips] [--redo]

Review/posters/<id>.jpg      one frame at 35% of the clip, 128 px on the long side
Review/filmstrips/<id>.jpg   eight frames in a row (eight cells of 120 x 90), sampled across the clip, drawn behind the trim bar

Idempotent: a file newer than its proxy is kept. Written as .part and renamed.
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

POSTER_LONG = 128
STRIP_FRAMES = 8
STRIP_CELL = (120, 90)


def poster_command(proxy: Path, out: Path, dur: float, vertical: bool) -> list[str]:
    t = max(0.0, min(dur - 0.2, dur * 0.35))
    vf = f"scale=-2:{POSTER_LONG}" if vertical else f"scale={POSTER_LONG + 16}:-2"
    return ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(proxy), "-frames:v", "1", "-vf", vf, "-q:v", "4", str(out)]


def strip_command(proxy: Path, out: Path, dur: float) -> list[str]:
    w, h = STRIP_CELL
    dur = max(dur, 0.1)
    vf = f"fps={STRIP_FRAMES}/{dur:.4f},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},tile={STRIP_FRAMES}x1"
    return ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{dur / (2 * STRIP_FRAMES):.3f}", "-i", str(proxy), "-vf", vf, "-frames:v", "1", "-q:v", "5", str(out)]


def _fresh(out: Path, proxy: Path) -> bool:
    return out.exists() and out.stat().st_size > 0 and out.stat().st_mtime >= proxy.stat().st_mtime


def _make(cmd_fn, proxy: Path, out: Path) -> bool:
    tmp = out.with_name(f".{out.stem}.part.jpg")
    r = common.run(cmd_fn(tmp), check=False)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        os.replace(tmp, out)
        return True
    if tmp.exists():
        tmp.unlink()
    return False


def make_all(clips: list[dict], strips: bool = True, redo: bool = False, jobs: int = 4, say=print) -> dict:
    """Make the missing thumbnails and filmstrips. Returns {"posters": n, "strips": n, "failed": [ids]}."""
    proxies = store.load_proxies()
    pdir, sdir = store.d_posters(), store.d_strips()
    pdir.mkdir(parents=True, exist_ok=True)
    if strips:
        sdir.mkdir(parents=True, exist_ok=True)
    todo = []
    for c in clips:
        info = proxies.get(c["id"])
        proxy = store.d_proxies() / f"{c['id']}.mp4"
        if info and proxy.exists():
            todo.append((c, info, proxy))
    counts = {"posters": 0, "strips": 0, "failed": []}

    def work(item):
        c, info, proxy = item
        dur = float(info.get("dur") or c.get("dur") or 0)
        res = {"id": c["id"], "poster": None, "strip": None}
        po = pdir / f"{c['id']}.jpg"
        if redo or not _fresh(po, proxy):
            res["poster"] = _make(lambda tmp: poster_command(proxy, tmp, dur, bool(info.get("v"))), proxy, po)
        if strips:
            so = sdir / f"{c['id']}.jpg"
            if redo or not _fresh(so, proxy):
                res["strip"] = _make(lambda tmp: strip_command(proxy, tmp, dur), proxy, so)
        return res

    with ThreadPoolExecutor(max(1, jobs)) as ex:
        results = list(ex.map(work, todo))
    for r in results:
        counts["posters"] += 1 if r["poster"] else 0
        counts["strips"] += 1 if r["strip"] else 0
        if r["poster"] is False or r["strip"] is False:
            counts["failed"].append(r["id"])
    if counts["posters"] or counts["strips"]:
        say(f"Made {counts['posters']} thumbnail{'s' if counts['posters'] != 1 else ''}" + (f" and {counts['strips']} filmstrip{'s' if counts['strips'] != 1 else ''}" if strips else ""))
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Make thumbnails and filmstrips from the previews.")
    ap.add_argument("--no-filmstrips", action="store_true")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args(argv)
    res = make_all(store.load_inventory()["clips"], strips=not a.no_filmstrips, redo=a.redo, jobs=a.jobs)
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    store.run_cli(main)
