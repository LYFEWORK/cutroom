#!/usr/bin/env python3
"""storage.py: what is on disk, what can leave, and moving old shoots to an archive drive.

    python3 engine/storage.py report                 sizes by folder and shoot, free space, what is reclaimable
    python3 engine/storage.py report --json          the same as JSON
    python3 engine/storage.py archive <shoot> --to <folder> [--go]
                                                      copy a Footage/<shoot> folder to the archive (verified file by file),
                                                      then move the original to the Trash; without --go it only prints the plan
    python3 engine/storage.py archive <shoot> --go   uses the archive folder saved in the DNA (storage.archive) or config

Rules: nothing is removed outright; a shoot leaves only after every file's size and content hash match in the archive;
Finished/ is never archived by this tool (your finals are small, keep them); the review page forgets archived clips on its
next ingest because the folder is gone, and remembers them again if the archive is added as a review root.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

VIDEO = common.VIDEO_EXT | common.AUDIO_EXT


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def du(path):
    total, newest, oldest = 0, 0.0, None
    for root, dirs, files in os.walk(path):
        for f in files:
            p = Path(root) / f
            try:
                st = p.stat()
            except OSError:
                continue
            total += st.st_size
            newest = max(newest, st.st_mtime)
            oldest = st.st_mtime if oldest is None else min(oldest, st.st_mtime)
    return total, newest, oldest


def delivered(project):
    f = common.ws("Finished") / project.name / f"{project.name}.mp4"
    if f.exists():
        return True
    pj = common.read_json(project / "project.json", {}) or {}
    return bool(pj.get("final") and Path(pj["final"]).expanduser().exists())


def heavy(project):
    """Bytes of intermediates finish.py would remove."""
    total = 0
    for root, dirs, files in os.walk(project):
        for f in files:
            p = Path(root) / f
            try:
                s = p.stat().st_size
            except OSError:
                continue
            if p.suffix.lower() in {".json", ".md", ".txt", ".csv", ".edl", ".xml", ".srt", ".png", ".jpg", ".jpeg", ".webp", ".html", ".log"} and s <= 2 * 1024 * 1024:
                continue
            total += s
    return total


def report(as_json=False):
    ws = common.workspace()
    out = {"workspace": str(ws), "folders": {}, "shoots": [], "projects": [], "reclaimable": {}, "disk": {}}
    for name in common.WORKSPACE_FOLDERS:
        p = ws / name
        if p.exists():
            t, newest, oldest = du(p)
            out["folders"][name] = {"bytes": t, "newest": newest}
    foot = ws / "Footage"
    if foot.exists():
        for s in sorted(p for p in foot.iterdir() if p.is_dir() and not p.name.startswith(".")):
            t, newest, oldest = du(s)
            out["shoots"].append({"name": s.name, "bytes": t, "age_days": round((time.time() - newest) / 86400) if newest else None})
    proj = ws / "Projects"
    recl_proj = 0
    if proj.exists():
        for p in sorted(x for x in proj.iterdir() if x.is_dir() and not x.name.startswith(".")):
            t, newest, _ = du(p)
            d = delivered(p)
            h = heavy(p) if d else 0
            recl_proj += h
            out["projects"].append({"name": p.name, "bytes": t, "delivered": d, "reclaimable": h, "age_days": round((time.time() - newest) / 86400) if newest else None})
    out["reclaimable"]["delivered_project_intermediates"] = recl_proj
    rev = ws / "Review"
    if rev.exists():
        prox = rev / "proxies"
        out["reclaimable"]["review_proxies"] = du(prox)[0] if prox.exists() else 0
        trash_hold = rev / "trash"
        out["reclaimable"]["review_trash_folder"] = du(trash_hold)[0] if trash_hold.exists() else 0
    st = shutil.disk_usage(ws)
    out["disk"] = {"total": st.total, "free": st.free, "used": st.used}
    if as_json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"Workspace: {ws}")
    print(f"Disk: {human(st.free)} free of {human(st.total)}")
    print()
    print("Folders")
    for name, v in out["folders"].items():
        print(f"  {human(v['bytes']):>9}  {name}")
    if out["shoots"]:
        print()
        print("Shoots (Footage/), oldest first")
        for s in sorted(out["shoots"], key=lambda x: -(x["age_days"] or 0)):
            print(f"  {human(s['bytes']):>9}  {s['name']}  (last touched {s['age_days']} days ago)")
    if out["projects"]:
        print()
        print("Projects")
        for p in out["projects"]:
            tag = "delivered" if p["delivered"] else "in progress"
            extra = f", {human(p['reclaimable'])} can leave (finish.py)" if p["reclaimable"] else ""
            print(f"  {human(p['bytes']):>9}  {p['name']}  ({tag}{extra})")
    print()
    print("Reclaimable")
    print(f"  {human(recl_proj):>9}  intermediates of delivered projects: python3 engine/finish.py --all --go")
    if "review_proxies" in out["reclaimable"]:
        print(f"  {human(out['reclaimable']['review_proxies']):>9}  review proxies (regenerable: python3 engine/review/ingest.py --prune after archiving)")
    if out["reclaimable"].get("review_trash_folder"):
        print(f"  {human(out['reclaimable']['review_trash_folder']):>9}  Review/trash holding folder (empty it yourself when sure)")
    big_old = [s for s in out["shoots"] if (s["age_days"] or 0) > 60 and s["bytes"] > 5 * 1024 ** 3]
    if big_old:
        print()
        print("Suggested to archive (over 60 days old and over 5 GB):")
        for s in big_old:
            print(f"  python3 engine/storage.py archive \"{s['name']}\" --to <archive folder> --go")
    return 0


def sha(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def archive(shoot, to, go):
    src = Path(shoot).expanduser()
    if not src.is_dir():
        src = common.ws("Footage") / shoot
    if not src.is_dir():
        common.stop(f"no shoot folder called {shoot} under {common.ws('Footage')}")
    if not to:
        cfg = common.load_config()
        to = cfg.get("archive") or ""
        dna = common.read_json(common.brand_dir() / "dna.json", {}) or {}
        to = to or (dna.get("storage", {}) or {}).get("archive", "")
    if not to:
        common.stop("no archive folder: pass --to <folder> (an external drive), or set it in the setup")
    dest_root = Path(to).expanduser()
    if not dest_root.exists():
        common.stop(f"archive folder is not there: {dest_root} (is the drive connected?)")
    dest = dest_root / src.name
    total, _, _ = du(src)
    free = shutil.disk_usage(dest_root).free
    print(f"Archive {src.name}: {human(total)} to {dest}")
    if free < total * 1.02:
        common.stop(f"not enough room on {dest_root}: {human(free)} free, {human(total)} needed")
    if dest.exists():
        common.stop(f"{dest} already exists; remove it or pick another folder")
    if not go:
        print("Nothing changed. Run again with --go to copy, verify and move the original to the Trash.")
        return 0
    files = [Path(r) / f for r, d, fs in os.walk(src) for f in fs if not f.startswith("._")]
    n = 0
    for p in files:
        rel = p.relative_to(src)
        q = dest / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        if p.stat().st_size != q.stat().st_size or sha(p) != sha(q):
            common.stop(f"copy did not verify for {rel}; the original stays, remove {dest} and try again")
        n += 1
        if n % 25 == 0:
            print(f"  {n}/{len(files)} copied and verified")
    print(f"  {n}/{len(files)} copied and verified")
    where = common.trash(src)
    (dest / "ARCHIVED.txt").write_text(f"Archived {time.strftime('%Y-%m-%d %H:%M')} from {src}\n{n} files, {human(total)}\nOriginal moved to: {where}\n")
    print(f"Done. Original moved to {'the Trash' if where == 'trash' else where}. Empty the Trash to get {human(total)} back.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("report"); r.add_argument("--json", action="store_true")
    a = sub.add_parser("archive"); a.add_argument("shoot"); a.add_argument("--to", default=""); a.add_argument("--go", action="store_true")
    args = ap.parse_args()
    if args.cmd == "report":
        return report(args.json)
    if args.cmd == "archive":
        return archive(args.shoot, args.to, args.go)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
