#!/usr/bin/env python3
"""approved.py: what an edit may use. Only clips marked Keep may be placed, and a trimmed clip only inside its kept parts.

    python3 approved.py list [--shoot S] [--kind take|broll|bad|other] [--status keep|cut|todo] [--json]
    python3 approved.py check <path> [--json]
    python3 approved.py status [--json]

list    the clips with that status (default keep): absolute path, id, shoot, kind, group, length, upright or not, notes, and the
        kept ranges. This is the only list an edit should pick footage from. todo means never reviewed. A clip whose file is
        gone is left out.
check   exit 0 only when that file is a kept clip, and it prints the kept ranges. Exit 1 when it is cut or not reviewed yet, 2
        when it is not a clip the review knows (not in the list, not on disk, or changed since it was reviewed). A path is
        matched by where it is, and any other file by its content, so a copy or a renamed file resolves to the same decision.
status  how many clips are waiting for review, per shoot.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402


def fmt_ranges(r) -> str:
    return ", ".join(f"{a:.2f}-{b:.2f}s" for a, b in r)


def listing(shoot=None, kind=None, status="keep") -> list[dict]:
    inv = store.load_inventory()
    decs = store.load_decisions()
    out = []
    for c in inv["clips"]:
        if shoot and c["project"].lower() != shoot.lower():
            continue
        if kind and c["kind"] != kind:
            continue
        v = store.verdict(c, decs)
        if v != status:
            continue
        paths = [p for p in store.abs_paths(c) if os.path.exists(p)]
        if not paths:
            continue
        d = decs.get(c["id"])
        rng = store.kept_ranges(c, d) if v == "keep" else None
        out.append({"path": paths[0], "paths": paths, "id": c["id"], "shoot": c["project"], "kind": c["kind"], "group": c["group"],
                    "dur": c["dur"], "vertical": c["h"] >= c["w"], "desc": c.get("desc", ""), "status": v,
                    "ranges": rng, "whole": bool(rng) and not (d or {}).get("r")})
    return out


def check(path: str):
    """(exit code, result dict) for one file."""
    idx = store.Index()
    decs = store.load_decisions()
    clip, how = idx.lookup_file(path)
    if clip is None:
        return 2, {"status": "unknown", "path": store.norm(path), "reason": how}
    v = store.verdict(clip, decs)
    res = {"status": v, "path": store.norm(path), "id": clip["id"], "shoot": clip["project"], "kind": clip["kind"], "how": how}
    if v == "keep":
        d = decs[clip["id"]]
        res.update(ranges=store.kept_ranges(clip, d), whole=not d.get("r"))
        return 0, res
    return 1, res


def status_counts() -> dict:
    inv = store.load_inventory()
    decs = store.load_decisions()
    shoots: dict[str, dict] = {}
    for c in inv["clips"]:
        s = shoots.setdefault(c["project"], {"todo": 0, "keep": 0, "cut": 0, "total": 0})
        s[store.verdict(c, decs)] += 1
        s["total"] += 1
    total = {k: sum(s[k] for s in shoots.values()) for k in ("todo", "keep", "cut", "total")}
    return {"shoots": shoots, "all": total}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="What an edit may use.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--shoot")
    s.add_argument("--kind", choices=store.KINDS)
    s.add_argument("--status", choices=("keep", "cut", "todo"), default="keep")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("check")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        rows = listing(a.shoot, a.kind, a.status)
        if a.json:
            print(json.dumps(rows, indent=1, ensure_ascii=False))
        else:
            for r in rows:
                print(r["path"] + (f"  keep {fmt_ranges(r['ranges'])}" if r["status"] == "keep" and not r["whole"] else ""))
        return 0

    if a.cmd == "check":
        code, res = check(a.path)
        if a.json:
            print(json.dumps(res, indent=1, ensure_ascii=False))
            return code
        p = res["path"]
        if res["status"] == "unknown":
            print(f"NOT IN THE REVIEW LIST (off limits): {p} ({res['reason']})")
        elif res["status"] == "todo":
            print(f"NOT REVIEWED YET (off limits): {p}")
        elif res["status"] == "cut":
            print(f"CUT (off limits): {p}")
        else:
            note = " (a copy: found by its content)" if res["how"] == "content" else ""
            print(f"KEEP {p}{note} " + ("whole clip" if res["whole"] else "only " + fmt_ranges(res["ranges"])))
        return code

    res = status_counts()
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0
    for name, s in sorted(res["shoots"].items(), reverse=True):
        print(f"{name}: {s['todo']} to review of {s['total']} ({s['keep']} kept, {s['cut']} cut)")
    t = res["all"]
    print(f"All shoots: {t['todo']} clip{'s' if t['todo'] != 1 else ''} to review, {t['keep']} kept, {t['cut']} cut" if t["total"] else "No clips yet. Run ingest.")
    return 0


if __name__ == "__main__":
    store.run_cli(main)
