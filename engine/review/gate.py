#!/usr/bin/env python3
"""gate.py: stop an edit that uses footage it may not. Run it on a plan before anything is rendered.

    python3 gate.py <plan.json | folder> [--allow-outside] [--json]

Finds every media reference in the JSON (any string that names a video file, as a path or just a file name) and resolves it to
a clip: by its path, by the file's content (a copy or a renamed file), or by the name in the review list. Exit 0 when every
reference is a kept clip used inside its kept parts. Exit 1 and a list of every problem otherwise:

    CUT          the clip was marked Cut
    UNREVIEWED   the clip was never marked (or is new footage that was not ingested)
    OUTSIDE      the plan uses seconds of the clip that were not kept (or past its end)
    NO TIMES     a trimmed clip is named without in and out, so the whole file would be used
    UNKNOWN      the file is not in the review list, changed since it was reviewed, or was not found
    AMBIGUOUS    a bare file name that matches more than one clip: name the full path

Times are read from "in" and "out" (or "source_in" and "source_out") in the same object as the file name, in seconds of the
clip. Put them next to the path. With --allow-outside, a file that exists but sits outside every review folder (a logo
sting, a render) is let through. A folder is scanned for every .json inside it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402

TIME_KEYS = (("in", "out"), ("source_in", "source_out"))
SKIP_DIRS = {"node_modules", ".git", "out", "renders", ".venv"}
SKIP_FILES = {"inventory.json", "proxies.json", "decisions.json", "apply-plan.json", "descriptions.json"}
MAX_JSON = 5 << 20
TOL = 0.1       # seconds of slack on a range edge: the page rounds to hundredths and containers round their own way


def media_name(s: str):
    """The file reference inside a string, or None when it is not a local video file."""
    if not isinstance(s, str) or not s or len(s) > 1024 or "\n" in s:
        return None
    t = s.strip()
    if t.startswith("file://"):
        t = unquote(t[7:])
    elif "://" in t:
        return None
    t = t.split("?")[0].split("#")[0]
    return t if os.path.splitext(t)[1].lower() in common.VIDEO_EXT else None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def refs_in(node, where="", out=None):
    """[(reference, in, out, where)] for every media string in a parsed JSON value."""
    out = [] if out is None else out
    if isinstance(node, dict):
        t_in = t_out = None
        for a, b in TIME_KEYS:
            if _num(node.get(a)) is not None or _num(node.get(b)) is not None:
                t_in, t_out = _num(node.get(a)), _num(node.get(b))
                break
        for k, v in node.items():
            name = media_name(v)
            if name:
                out.append((name, t_in, t_out, f"{where}/{k}"))
            else:
                refs_in(v, f"{where}/{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            name = media_name(v)
            if name:
                out.append((name, None, None, f"{where}[{i}]"))
            else:
                refs_in(v, f"{where}[{i}]", out)
    return out


def json_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    skip_home = os.path.realpath(store.home())
    found = []
    for dp, dn, fn in os.walk(target):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith(".") and os.path.realpath(os.path.join(dp, d)) != skip_home)
        for f in sorted(fn):
            p = Path(dp) / f
            if f.endswith(".json") and not f.startswith("._") and f not in SKIP_FILES:
                try:
                    if p.stat().st_size <= MAX_JSON:
                        found.append(p)
                except OSError:
                    pass
    return found


def resolve(ref: str, bases: list[Path], idx: store.Index):
    """(clips, how): the clips a reference can mean. [] with a reason when none, several when it is ambiguous."""
    p = os.path.expanduser(ref)
    cands = [Path(p)] if os.path.isabs(p) else [b / p for b in bases]
    for c in cands:
        if c.is_file():
            clip, how = idx.lookup_file(c)
            return ([clip], how) if clip else ([], how + f"|{c}")
    hit = idx.lookup_path(p) if os.path.isabs(p) else None
    if hit:
        return [hit[0]], "path"
    if os.path.isabs(p):
        return [], "not found"
    found = idx.by_suffix(p) or idx.by_suffix(os.path.basename(p))
    return (found, "name") if found else ([], "not found")


def under_a_root(path: Path, roots: list[Path]) -> bool:
    rp = os.path.realpath(path)
    return any(rp == os.path.realpath(r) or rp.startswith(os.path.realpath(r) + os.sep) for r in roots)


def judge(clip: dict, t_in, t_out, decs: dict) -> tuple[str, str]:
    """(status, detail) for one use of a clip: ok, CUT, UNREVIEWED, OUTSIDE or NO TIMES."""
    v = store.verdict(clip, decs)
    if v == "cut":
        return "CUT", "marked Cut"
    if v == "todo":
        return "UNREVIEWED", "never marked Keep or Cut"
    dec = decs[clip["id"]]
    dur = float(clip.get("dur") or 0)
    kept = store.kept_ranges(clip, dec)
    if t_in is None and t_out is None:
        if dec.get("r"):
            return "NO TIMES", f"only {', '.join(f'{a:.2f}-{b:.2f}s' for a, b in kept)} is kept; add in and out"
        return "ok", ""
    a = 0.0 if t_in is None else t_in
    b = dur if t_out is None else t_out
    if not a < b:
        return "OUTSIDE", f"in {a:.2f}s is not before out {b:.2f}s"
    if a < -TOL or b > dur + TOL:
        return "OUTSIDE", f"uses {a:.2f}-{b:.2f}s but the clip is {dur:.2f}s long"
    if not any(ra - TOL <= a and b <= rb + TOL for ra, rb in kept):
        return "OUTSIDE", f"uses {a:.2f}-{b:.2f}s but only {', '.join(f'{x:.2f}-{y:.2f}s' for x, y in kept)} is kept"
    return "ok", ""


def run_gate(target, allow_outside: bool = False) -> dict:
    """Check a plan file or a folder of them. Returns {"problems": [...], "checked": n, "files": n, "references": n}."""
    target = Path(target).expanduser()
    if not target.exists():
        common.stop(f"{target} does not exist")
    idx = store.Index()
    decs = store.load_decisions()
    roots = store.roots()
    ws = common.workspace()
    problems, checked, refs_total, files = [], set(), 0, json_files(target)
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeDecodeError):
            if target.is_file():
                common.stop(f"{f} is not valid JSON")
            continue
        bases = [f.parent, f.parent.parent, f.parent.parent.parent, ws, Path.cwd(), *roots]
        for ref, t_in, t_out, where in refs_in(doc):
            refs_total += 1
            clips, how = resolve(ref, bases, idx)
            row = {"file": str(f), "ref": ref, "where": where}
            if len(clips) > 1:
                problems.append({**row, "status": "AMBIGUOUS", "detail": f"matches {len(clips)} clips: " + ", ".join(c["rel"] for c in clips[:3])})
                continue
            if not clips:
                reason, _, on_disk = how.partition("|")
                if on_disk and reason == "not in the inventory":
                    p = Path(on_disk)
                    if under_a_root(p, roots):
                        problems.append({**row, "status": "UNREVIEWED", "detail": "new footage that was not ingested; run ingest and review it"})
                    elif not allow_outside:
                        problems.append({**row, "status": "UNKNOWN", "detail": "not in the review list (outside every review folder; --allow-outside lets such files through)"})
                else:
                    problems.append({**row, "status": "UNKNOWN", "detail": reason if reason else "not found"})
                continue
            clip = clips[0]
            checked.add(clip["id"])
            status, detail = judge(clip, t_in, t_out, decs)
            if status != "ok":
                problems.append({**row, "status": status, "detail": detail, "id": clip["id"]})
    return {"problems": problems, "checked": len(checked), "files": len(files), "references": refs_total}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check a plan only uses kept footage.")
    ap.add_argument("target", help="a plan .json, or a folder to scan for them")
    ap.add_argument("--allow-outside", action="store_true", help="let through files that exist outside every review folder")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run_gate(a.target, a.allow_outside)
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 1 if res["problems"] else 0
    for p in res["problems"]:
        print(f"{p['status']:<11} {p['ref']}\n            {p['detail']}  [{os.path.basename(p['file'])}{p['where']}]")
    if res["problems"]:
        n = len(res["problems"])
        print(f"\ngate: {n} problem{'s' if n != 1 else ''}. Nothing may be rendered until each is fixed.")
        return 1
    if not res["references"]:
        print(f"gate: no media references found in {res['files']} file{'s' if res['files'] != 1 else ''}. "
              "If the plan names clips some other way, this check cannot see them.")
    else:
        print(f"gate: clean ({res['references']} reference{'s' if res['references'] != 1 else ''} to {res['checked']} clip{'s' if res['checked'] != 1 else ''}, all kept and inside their kept parts)")
    return 0


if __name__ == "__main__":
    store.run_cli(main)
