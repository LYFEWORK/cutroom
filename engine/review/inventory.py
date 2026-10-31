#!/usr/bin/env python3
"""inventory.py: find every clip under the footage folders and write Review/inventory.json. Originals are only read.

    python3 inventory.py [--root PATH ...] [--allow-empty] [--jobs N]

Where a clip sits decides what it is:
    project   the first folder under the footage folder (a shoot, like "2026-01-01 Test Shoot")
    kind      take (under Interview/ or Takes/), broll (under B-Roll/), bad (under Bad Takes/), other (anything else)
    group     the folders below the project, minus the kind folder (for Interview/L01 Line/x.mp4 the group is "L01 Line")

One row per unique clip. The id is common.fingerprint(), a content identity, so a renamed or moved file keeps its decision
and a copy of a file folds into the same row with both paths listed. A re-exported file is a new clip and is reviewed again.

A re-run only reads files that are new or changed (path, size and modification time). It will not forget clips when a footage
folder is missing or has gone empty (an unplugged drive): it stops and says so instead of wiping the list.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402

KIND_DIRS = (
    ("take", re.compile(r"^(interview|takes?)$", re.I)),
    ("bad", re.compile(r"^bad[ _-]?takes?$", re.I)),
    ("broll", re.compile(r"(^|[\s_-])b[ _-]?roll$", re.I)),
)
KIND_RANK = {"take": 0, "broll": 1, "other": 2, "bad": 3}
SKIP_DIRS = {"raw", "raw clips", "$recycle.bin", "__macosx"}      # plus every folder that starts with a dot (.work, .review, ...)
LOOSE = "Loose clips"


def kind_of(dirname: str):
    for kind, rx in KIND_DIRS:
        if rx.search(dirname):
            return kind
    return None


def classify(rest_dirs: list[str]) -> tuple[str, str]:
    """(kind, group) from the folders between the project folder and the file."""
    for i, d in enumerate(rest_dirs):
        k = kind_of(d)
        if k:
            others = rest_dirs[:i] + rest_dirs[i + 1:]
            return k, " / ".join(others) or LOOSE
    return "other", " / ".join(rest_dirs) or LOOSE


def skip_dir(name: str) -> bool:
    return name.startswith(".") or name.lower() in SKIP_DIRS


def describe(root: Path, abs_path: str) -> dict:
    rel_parts = Path(abs_path).relative_to(root).parts
    if len(rel_parts) == 1:
        project, rest = "Unsorted", []
    else:
        project, rest = rel_parts[0], list(rel_parts[1:-1])
    kind, group = classify(rest)
    return {"root": str(root), "rel": "/".join(rel_parts), "project": project, "kind": kind, "group": group}


def scan(roots: list[Path], review_home: Path):
    """Every video file under the roots: [(root, absolute path)] and a count per root."""
    found, counts = [], {}
    rh = os.path.realpath(review_home)
    inside = any(rh == os.path.realpath(r) or rh.startswith(os.path.realpath(r) + os.sep) for r in roots)   # only then can a walk run into Review/
    for root in roots:
        n = 0
        for dp, dn, fn in os.walk(root):
            dn[:] = sorted(d for d in dn if not skip_dir(d) and not (inside and os.path.realpath(os.path.join(dp, d)) == rh))
            for f in sorted(fn):
                if f.startswith(".") or os.path.splitext(f)[1].lower() not in common.VIDEO_EXT:
                    continue
                found.append((root, os.path.join(dp, f)))
                n += 1
        counts[str(root)] = n
    return found, counts


PROBE_KEYS = ("dur", "w", "h", "fps", "codec", "pix", "audio", "vindex", "aindex")


def _descriptions() -> dict:
    """Optional Review/descriptions.json: {id: "text"} or {id: {"desc": "text", "flags": "a, b"}}. Filled by a describe step."""
    raw = store.read_json(store.f_descriptions(), {})
    out = {}
    if isinstance(raw, dict):
        for cid, v in raw.items():
            if isinstance(v, str):
                out[cid] = {"desc": v, "flags": ""}
            elif isinstance(v, dict):
                out[cid] = {"desc": str(v.get("desc", "")), "flags": str(v.get("flags", ""))}
    return out


def build(roots=None, jobs: int = 6, allow_empty: bool = False, say=print) -> dict:
    """Walk the footage folders and write inventory.json. Returns a summary: clips, new_ids, gone_ids, skipped, path."""
    roots = [Path(os.path.abspath(os.path.expanduser(str(r)))) for r in (roots or store.roots())]
    for r in roots:
        if not r.is_dir():
            common.stop(f"the footage folder {r} is not there (is the drive plugged in?). Nothing was changed.")
    old = store.load_inventory()
    old_ids = {c["id"] for c in old["clips"]}
    old_rows = {c["id"]: c for c in old["clips"]}
    old_by_abs = {}
    for c in old["clips"]:
        for p in c.get("paths", []):
            old_by_abs[store.abs_of(p)] = (c, p)

    found, counts = scan(roots, store.home())
    for root in roots:
        had = sum(1 for c in old["clips"] for p in c.get("paths", []) if p["root"] == str(root))
        if had and counts[str(root)] == 0 and not allow_empty:
            common.stop(f"{root} has no video files now, but {had} were listed before. If a drive is unplugged, plug it in. "
                        f"If you emptied it on purpose, run again with --allow-empty. Nothing was changed.")
    say(f"Looking in {len(roots)} footage folder{'s' if len(roots) != 1 else ''}: {len(found)} video file{'s' if len(found) != 1 else ''}")

    def read(item):
        root, p = item
        try:
            st = os.stat(p)
        except OSError:
            return item, None, None, None, False
        prev = old_by_abs.get(os.path.normpath(p))
        if prev and prev[1].get("size") == st.st_size and prev[1].get("mtime") == int(st.st_mtime):
            row = prev[0]
            return item, st, row["id"], {k: row.get(k) for k in PROBE_KEYS}, False
        try:
            pr = common.probe(p)
            if not pr:
                return item, st, None, None, True
            return item, st, common.fingerprint(p), {k: pr.get(k) for k in PROBE_KEYS}, True
        except OSError:
            return item, st, None, None, True

    t0 = time.time()
    rows: dict[str, dict] = {}
    skipped: list[str] = []
    n_read = 0
    with ThreadPoolExecutor(max(1, jobs)) as ex:
        for i, (item, st, cid, pr, fresh) in enumerate(ex.map(read, found), 1):
            root, p = item
            n_read += 1 if fresh else 0
            if i % 50 == 0:
                say(f"  checked {i} of {len(found)} files, {time.time() - t0:.0f}s")
            if st is None or cid is None:
                skipped.append(os.path.relpath(p, root))
                continue
            entry = describe(root, p)
            entry.update(size=st.st_size, mtime=int(st.st_mtime))
            if cid in rows:
                rows[cid]["paths"].append(entry)
                continue
            rows[cid] = {"id": cid, "bytes": st.st_size, "paths": [entry], **pr}

    descs = _descriptions()
    clips = []
    for cid, r in rows.items():
        r["paths"].sort(key=lambda p: (KIND_RANK[p["kind"]], p["rel"]))
        pp = r["paths"][0]
        prev = old_rows.get(cid, {})
        d = descs.get(cid) or {"desc": prev.get("desc", ""), "flags": prev.get("flags", "")}
        r.update(project=pp["project"], kind=pp["kind"], group=pp["group"], rel=pp["rel"], name=os.path.basename(pp["rel"]),
                 desc=d["desc"], flags=d["flags"], added=prev.get("added") or store.today())
        clips.append(r)
    clips.sort(key=lambda r: (r["project"], KIND_RANK[r["kind"]], r["group"].lower(), r["name"].lower()))

    inv = {"version": 1, "built": time.strftime("%Y-%m-%d %H:%M:%S"), "roots": [str(r) for r in roots], "clips": clips}
    common.atomic_write_json(store.f_inventory(), inv)
    new_ids = [c["id"] for c in clips if c["id"] not in old_ids]
    gone_ids = sorted(old_ids - set(rows))
    copies = sum(len(r["paths"]) for r in clips) - len(clips)
    say(f"{len(clips)} clip{'s' if len(clips) != 1 else ''} in the list: {len(new_ids)} new"
        + (f", {len(gone_ids)} no longer on disk" if gone_ids else "")
        + (f", {copies} extra cop{'ies' if copies != 1 else 'y'} folded in" if copies else ""))
    if skipped:
        say(f"  skipped {len(skipped)} file{'s' if len(skipped) != 1 else ''} with no picture: " + ", ".join(skipped[:4]) + (" ..." if len(skipped) > 4 else ""))
    return {"clips": len(clips), "new_ids": new_ids, "gone_ids": gone_ids, "skipped": skipped, "path": str(store.f_inventory()), "read": n_read}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="List every clip under the footage folders.")
    ap.add_argument("--root", action="append", help="a footage folder to use instead of the configured ones (repeatable)")
    ap.add_argument("--allow-empty", action="store_true", help="accept a footage folder that has no videos now")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args(argv)
    build(roots=a.root, jobs=a.jobs, allow_empty=a.allow_empty)
    return 0


if __name__ == "__main__":
    store.run_cli(main)
