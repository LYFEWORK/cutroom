#!/usr/bin/env python3
"""encode.py: one small playable copy (a proxy) of every clip, for watching and trimming on the review page.

    python3 encode.py [--jobs N] [--redo] [--prune]

Review/proxies/<id>.mp4 plus Review/proxies.json {id: {dur, v, bytes, vs, h, made}}. Originals are only read.
  - H.264, 24 fps, mono AAC, keyframe every 2 s, faststart. The short side is proxy_height (config, default 360), never scaled up.
    Browsers play this everywhere, which they do not for 10-bit or 4:2:2 camera files.
  - Source sound is mapped by its probed stream index, never by position. A silent source gets a silent track so every proxy
    has the same shape. The picture comes from the probed video stream, so a cover image stored first is never encoded.
  - Hardware decode (VideoToolbox) is tried first and used only when it finishes cleanly: exit code 0, nothing on stderr, and a
    length that matches the clip within a quarter second. It can drop frames while still exiting 0, so anything else is redone in
    software. After three hardware failures in a run it stops trying.
  - "vs" is the time of the proxy's first picture, which the page never seeks before.
  - Idempotent and atomic: a finished proxy is skipped, files are written as .part and renamed.
  - --prune removes proxies, posters and filmstrips whose clip is no longer in the inventory.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402

FPS = 24
CRF = 30
HW_GIVE_UP = 3


class _Hw:
    def __init__(self, enabled: bool):
        self.enabled = enabled and sys.platform == "darwin"
        self.fails = 0
        self._lock = threading.Lock()

    def ok(self) -> bool:
        return self.enabled and self.fails < HW_GIVE_UP

    def failed(self) -> None:
        with self._lock:
            self.fails += 1


def hw_ok(returncode: int, stderr: str, got: float, want: float) -> bool:
    """A hardware-decoded proxy is trusted only if it ended cleanly: exit 0, nothing on stderr, and the right length."""
    return returncode == 0 and not (stderr or "").strip() and abs(got - want) <= max(0.25, want * 0.01)


def sw_ok(returncode: int, got: float, want: float) -> bool:
    return returncode == 0 and abs(got - want) < max(0.6, want * 0.02)


def video_filter(clip: dict, height: int) -> str:
    """Scale so the short side is `height` (never up), square pixels, even sizes, 24 fps, 4:2:0."""
    h = int(height)
    if clip["h"] >= clip["w"]:     # upright or square: the short side is the width
        return f"scale=w='trunc(min({h}\\,iw)/2)*2':h='trunc(ow/dar/2)*2',setsar=1,fps={FPS},format=yuv420p"
    return f"scale=w='trunc(oh*dar/2)*2':h='trunc(min({h}\\,ih)/2)*2',setsar=1,fps={FPS},format=yuv420p"


def command(clip: dict, src: str, tmp: str, height: int, hw: bool, silent: bool) -> list[str]:
    """The ffmpeg call. silent=True ignores the source sound and writes a silent track."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if hw:
        cmd += ["-hwaccel", "videotoolbox"]
    cmd += ["-i", src]
    if silent:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
    vindex = clip.get("vindex")
    aindex = clip.get("aindex")
    cmd += ["-map", f"0:{vindex if vindex is not None else 'v:0'}"]
    cmd += ["-map", "1:a:0" if silent else f"0:{aindex}"]
    cmd += ["-vf", video_filter(clip, height)]
    if not silent:
        cmd += ["-af", "aresample=44100,apad"]
    cmd += ["-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(CRF), "-g", str(FPS * 2),
            "-c:a", "aac", "-ac", "1", "-ar", "44100", "-b:a", "32k", "-write_tmcd", "0", "-movflags", "+faststart", tmp]
    return cmd


def duration_of(path) -> float:
    r = common.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], check=False)
    try:
        return float((r.stdout or "").strip())
    except ValueError:
        return 0.0


def first_picture_time(path) -> float:
    """When the first picture shows (some files start a fraction of a second in). Smallest of the first three packets."""
    r = common.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-read_intervals", "%+#3",
                    "-of", "csv=p=0", path], check=False)
    ts = []
    for x in (r.stdout or "").split():
        try:
            ts.append(float(x.strip(",")))
        except ValueError:
            pass
    return round(min(ts), 3) if ts else 0.0


def encode_one(clip: dict, src: str, out: Path, height: int, hw: _Hw):
    """Make one proxy. Returns (info dict, note) or (None, reason)."""
    tmp = out.with_name(out.stem + ".part.mp4")
    dur = float(clip.get("dur") or 0)
    has_sound = bool(clip.get("audio")) and clip.get("aindex") is not None
    attempts = []                                                    # (label, hardware decode, silent track)
    if hw.ok() and clip.get("pix") in ("yuv420p", "yuvj420p", "yuv420p10le"):
        attempts.append(("hardware", True, not has_sound))
    attempts.append(("software", False, not has_sound))
    if has_sound:
        attempts.append(("software, sound left out", False, True))   # a sound track ffmpeg cannot decode must not block the picture
    last = "no attempt ran"
    for name, use_hw, silent in attempts:
        if tmp.exists():
            tmp.unlink()
        r = common.run(command(clip, src, str(tmp), height, use_hw, silent), check=False)
        d = duration_of(tmp) if tmp.exists() else 0.0
        if use_hw:
            good = hw_ok(r.returncode, r.stderr, d, dur)
            if not good:
                hw.failed()
        else:
            good = sw_ok(r.returncode, d, dur)
        if good:
            os.replace(tmp, out)
            return {"dur": round(duration_of(out), 3), "v": clip["h"] >= clip["w"], "bytes": out.stat().st_size,
                    "vs": first_picture_time(out), "h": height, "made": store.stamp()}, name
        tail = (r.stderr or "").strip().splitlines()
        last = f"{name}: " + (tail[-1][:160] if tail else f"exit {r.returncode}") + f" (got {d:.2f}s of {dur:.2f}s)"
    if tmp.exists():
        tmp.unlink()
    return None, last


def needs(clip: dict, entry, out: Path, height: int, redo: bool) -> bool:
    return redo or not entry or not out.exists() or entry.get("h") != height


def encode_all(clips: list[dict], jobs: int = 6, redo: bool = False, say=print, hw_enabled: bool = True) -> dict:
    """Make every missing proxy. Returns {"made": [ids], "failed": {id: reason}, "total": n}."""
    height = store.proxy_height()
    pdir = store.d_proxies()
    pdir.mkdir(parents=True, exist_ok=True)
    done = store.load_proxies()
    todo = []
    for c in clips:
        out = pdir / f"{c['id']}.mp4"
        if needs(c, done.get(c["id"]), out, height, redo):
            src = next((p for p in store.abs_paths(c) if os.path.exists(p)), None)
            if src:
                todo.append((c, src, out))
    hw = _Hw(hw_enabled)
    made, failed = [], {}
    names = {c["id"]: c["name"] for c, _, _ in todo}
    if todo:
        say(f"Making previews for {len(todo)} clip{'s' if len(todo) != 1 else ''} ({height}p)")
    t0 = time.time()

    def work(item):
        c, src, out = item
        try:
            return c["id"], *encode_one(c, src, out, height, hw)
        except Exception as e:      # one odd file must not stop the rest
            return c["id"], None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max(1, jobs)) as ex:
        for n, (cid, info, note) in enumerate(ex.map(work, todo), 1):
            if info:
                done[cid] = info
                made.append(cid)
            else:
                failed[cid] = note
                say(f"  no preview for {names[cid]}: {note}")
            if n % 25 == 0 or n == len(todo):
                common.atomic_write_json(store.f_proxies(), done)
                say(f"  {n} of {len(todo)} previews, {time.time() - t0:.0f}s")
    if todo:
        common.atomic_write_json(store.f_proxies(), done)
    return {"made": made, "failed": failed, "total": len(todo)}


def prune(valid_ids: set, say=print) -> int:
    """Remove derived files for clips that are gone. Returns how many files went."""
    n = 0
    for d, exts in ((store.d_proxies(), (".mp4",)), (store.d_posters(), (".jpg",)), (store.d_strips(), (".jpg",))):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            stem = f.name.split(".")[0]
            orphan = stem not in valid_ids or f.name != stem + f.suffix or f.suffix not in exts     # includes stray .part files
            if orphan and f.is_file():
                f.unlink()
                n += 1
    done = store.load_proxies()
    keep = {i: v for i, v in done.items() if i in valid_ids}
    if len(keep) != len(done):
        common.atomic_write_json(store.f_proxies(), keep)
    if n:
        say(f"Removed {n} old preview file{'s' if n != 1 else ''} for clips that are gone")
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Make the small playable copy of every clip.")
    ap.add_argument("--jobs", type=int, default=min(6, os.cpu_count() or 4))
    ap.add_argument("--redo", action="store_true", help="make every preview again")
    ap.add_argument("--prune", action="store_true", help="remove previews, thumbnails and filmstrips for clips that are gone")
    ap.add_argument("--no-hardware", action="store_true", help="decode in software only")
    a = ap.parse_args(argv)
    inv = store.load_inventory()
    if a.prune:
        prune({c["id"] for c in inv["clips"]})
    res = encode_all(inv["clips"], jobs=a.jobs, redo=a.redo, hw_enabled=not a.no_hardware)
    print(f"{len(res['made'])} made, {len(res['failed'])} failed")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    store.run_cli(main)
