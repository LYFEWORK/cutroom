#!/usr/bin/env python3
"""ingest.py: the one command to run whenever footage lands, and before any edit.

    python3 ingest.py [--serve] [--open] [--root PATH ...] [--jobs N] [--no-filmstrips] [--allow-empty]

Finds new clips (inventory), makes their previews (encode), thumbnails and filmstrips (posters), checks the previews against the
originals (verify_proxies), removes derived files for clips that are gone, and rebuilds the page (build_site). Then it says how
many new clips are waiting for review and where the page is. Everything is incremental: a second run with nothing new is a
directory walk. With --serve it also starts the review server in the background if it is not already running.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import build_site, encode, inventory, posters, serve, store, verify_proxies  # noqa: E402


def refresh(say=print, roots=None, jobs: int | None = None, filmstrips: bool = True, allow_empty: bool = False) -> dict:
    """The whole pass. Returns {"new": [ids], "waiting": n, "made": n, "failed": {...}, "mismatched": [...]}."""
    jobs = jobs or min(6, os.cpu_count() or 4)
    inv_res = inventory.build(roots=roots, jobs=jobs, allow_empty=allow_empty, say=say)
    clips = store.load_inventory()["clips"]
    encode.prune({c["id"] for c in clips}, say=say)
    enc = encode.encode_all(clips, jobs=jobs, say=say)
    posters.make_all(clips, strips=filmstrips, jobs=max(2, jobs // 2), say=say)
    bad = verify_proxies.verify(clips, ids=enc["made"], jobs=max(2, jobs // 2), say=say) if enc["made"] else []
    build_site.build(say=say)
    decided = store.load_decisions()
    new_waiting = [i for i in inv_res["new_ids"] if i not in decided]
    waiting = sum(1 for c in clips if c["id"] not in decided)
    return {"new": inv_res["new_ids"], "new_waiting": new_waiting, "waiting": waiting, "made": len(enc["made"]), "failed": enc["failed"], "mismatched": bad}


def ensure_server(port: int, say=print) -> bool:
    """Make sure a review server is answering on the port, starting one in the background if it is not."""
    if serve.ping(port):
        return True
    logs = store.home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "serve.log", "a") as log:
        proc = subprocess.Popen([sys.executable, str(Path(serve.__file__).resolve()), "--port", str(port)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
    for _ in range(40):
        time.sleep(0.15)
        if serve.ping(port):
            return True
        if proc.poll() is not None:
            break
    say(f"The review server did not start. See {logs / 'serve.log'}")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bring new footage onto the review page.")
    ap.add_argument("--serve", action="store_true", help="start the review server in the background if it is not running")
    ap.add_argument("--open", action="store_true", help="open the page in the browser (implies --serve)")
    ap.add_argument("--root", action="append", help="a footage folder to use instead of the configured ones (repeatable)")
    ap.add_argument("--jobs", type=int)
    ap.add_argument("--no-filmstrips", action="store_true")
    ap.add_argument("--allow-empty", action="store_true", help="accept a footage folder that has no videos now")
    a = ap.parse_args(argv)
    t0 = time.time()
    res = refresh(roots=a.root, jobs=a.jobs, filmstrips=not a.no_filmstrips, allow_empty=a.allow_empty)
    port = store.port()
    link = serve.url(port)
    n, w = len(res["new_waiting"]), res["waiting"]
    if w == 0:
        print("Everything is reviewed.")
    elif n:
        print(f"{n} new clip{'s' if n != 1 else ''} to review" + (f" ({w} waiting in all)" if w != n else ""))
    else:
        print(f"No new clips. {w} still waiting for review.")
    if res["failed"]:
        print(f"{len(res['failed'])} clip{'s' if len(res['failed']) != 1 else ''} could not get a preview. They can still be marked Keep or Cut by name.")
    up = False
    if a.serve or a.open:
        up = ensure_server(port)
        if up and a.open:
            webbrowser.open(link)
    else:
        up = serve.ping(port) is not None
    print(f"Review page: {link}" + ("" if up else f"   (the server is not running: python3 {Path(serve.__file__).resolve()} --open)"))
    print(f"Done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    store.run_cli(main)
