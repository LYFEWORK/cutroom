#!/usr/bin/env python3
"""finish.py: clean up a project after its video is delivered. Keeps the small files that say what was made; moves the heavy
intermediates (mezzanines, word wavs, renders, proxies, mattes, frames) to the Trash. Never touches Footage/ or Finished/.

    python3 engine/finish.py <project>              what would leave, with sizes (changes nothing)
    python3 engine/finish.py <project> --go         move it to the Trash and write CLEANUP.md
    python3 engine/finish.py <project> --go --delete   remove outright instead of the Trash (space comes back at once)
    python3 engine/finish.py --all [--go]           every project whose final is in Finished/

<project> is a folder under Projects/ or its name. A project counts as delivered when Finished/<Title>/<Title>.mp4 exists and
plays (ffprobe reads a duration); without that, finish.py refuses unless --force is given.

Kept (never removed): every .json .md .txt .csv .edl .xml .srt .png .jpg .jpeg .webp under 2 MB, GATE.md, SCRIPT.md, CLEANUP.md.
Removed: everything else (video, audio, big images, frame folders, node public dirs, logs over 2 MB).
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

KEEP_EXT = {".json", ".md", ".txt", ".csv", ".edl", ".xml", ".srt", ".png", ".jpg", ".jpeg", ".webp", ".html", ".log"}
KEEP_NAMES = {"GATE.md", "SCRIPT.md", "CLEANUP.md", "edit.json", "project.json", "words.json", "plan.json"}
KEEP_MAX = 2 * 1024 * 1024


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def project_dir(arg):
    p = Path(arg).expanduser()
    if p.is_dir():
        return p.resolve()
    q = common.ws("Projects") / arg
    if q.is_dir():
        return q.resolve()
    common.stop(f"no project folder called {arg} (under {common.ws('Projects')})")


def final_for(project):
    """The delivered final: Finished/<Title>/<Title>.mp4 where <Title> is the project folder name, or a project.json 'final' path."""
    pj = common.read_json(project / "project.json", {}) or {}
    cands = []
    if pj.get("final"):
        cands.append(Path(pj["final"]).expanduser())
    cands.append(common.ws("Finished") / project.name / f"{project.name}.mp4")
    for c in cands:
        if c.exists():
            pr = common.probe(c)
            if pr and pr["dur"] > 0:
                return c
    return None


def plan(project):
    removes, keeps = [], []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in (".git",)]
        for f in files:
            p = Path(root) / f
            try:
                size = p.stat().st_size
            except OSError:
                continue
            keep = (f in KEEP_NAMES) or (p.suffix.lower() in KEEP_EXT and size <= KEEP_MAX)
            (keeps if keep else removes).append((p, size))
    removes.sort(key=lambda t: -t[1])
    return removes, keeps


def run(project, go, delete, force):
    final = final_for(project)
    if not final and not force:
        common.stop(f"{project.name} has no delivered final in Finished/ (make one, or pass --force to clean a project you are dropping)")
    removes, keeps = plan(project)
    total = sum(s for _, s in removes)
    print(f"{project.name}: {len(removes)} files, {human(total)} would leave; {len(keeps)} small files stay" + (f"; final: {final}" if final else "; no final (forced)"))
    for p, s in removes[:40]:
        print(f"  {human(s):>9}  {p.relative_to(project)}")
    if len(removes) > 40:
        print(f"  ... and {len(removes) - 40} more")
    if not go:
        print("Nothing changed. Run again with --go to move them to the Trash" + ("" if delete else " (or --go --delete to remove outright)."))
        return 0
    freed, moved, failed = 0, 0, []
    for p, s in removes:
        try:
            if delete:
                p.unlink()
            else:
                common.trash(p)
            freed += s
            moved += 1
        except Exception as e:  # noqa: BLE001
            failed.append((p, str(e)))
    # empty folders left behind
    for root, dirs, files in os.walk(project, topdown=False):
        for d in dirs:
            dp = Path(root) / d
            try:
                if not any(dp.iterdir()):
                    dp.rmdir()
            except OSError:
                pass
    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = [f"# Cleanup: {project.name}", "", f"Run: {stamp}", f"Final kept: {final if final else 'none (forced)'}",
             f"Removed: {moved} files, {human(freed)}" + ("" if delete else " (in the Trash until you empty it)"), ""]
    if failed:
        lines += ["## Could not remove", ""] + [f"- {p.relative_to(project)}: {why}" for p, why in failed] + [""]
    lines += ["## Kept", ""] + [f"- {p.relative_to(project)} ({human(s)})" for p, s in sorted(keeps)]
    (project / "CLEANUP.md").write_text("\n".join(lines) + "\n")
    print(f"Done: {moved} files, {human(freed)} " + ("removed." if delete else "moved to the Trash. Empty the Trash to get the space back.") + f" Receipt: {project / 'CLEANUP.md'}")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.all:
        rc = 0
        for p in sorted(common.ws("Projects").iterdir()):
            if p.is_dir() and not p.name.startswith("."):
                if final_for(p) or a.force:
                    rc |= run(p, a.go, a.delete, a.force)
                else:
                    print(f"{p.name}: not delivered yet, skipped")
        return rc
    if not a.project:
        common.stop("name a project folder, or --all")
    return run(project_dir(a.project), a.go, a.delete, a.force)


if __name__ == "__main__":
    sys.exit(main())
