#!/usr/bin/env python3
"""organize.py: turns per-clip catalog records into the standard tree, files the originals, writes the manifest and the README.

  python3 organize.py --job JOB.json plan [--allow-unverified]    print the move plan, write plan.json and a manifest preview (no moves)
  python3 organize.py --job JOB.json apply [--allow-unverified]   write plan.json, the manifest and README FIRST, then file the clips and their sidecars
  python3 organize.py --job JOB.json reverse                      put the camera files back and delete everything this tool generated

Record source: <scratch>/results/<clip>.final.json if present, else <clip>.classify.json.
Never re-encodes, never trims. On the same volume a clip is renamed into place (nothing is copied). When the source is on ANOTHER volume (an SD card), each clip is
COPIED instead, checked by size and by a hash of its first and last MiB, and the card is left exactly as it was: an apply from a card never touches the card.

plan and apply exit 2 when any clip has no record, or has a record nobody verified (a final.json, or the classify, visual and content files of the workflow route),
unless --allow-unverified.
apply refuses when a destination already exists or when a sidecar would land on another file. A move that fails half way is rolled back (a copy is deleted, a
rename is renamed back). reverse restores the camera files byte for byte and removes the index, manifests, notes, README, cut takes, Edit/ and the folders that
held them; after an apply from another volume it files the copies back in <shoot>/RAW instead, because the originals never left the card.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from common import stop  # noqa: E402
import job as jobmod  # noqa: E402
from job import CATEGORIES, GENERATED, MARK, TOP, VIDEO_EXT, bad_takes_folder, clean, is_os_junk, load_job, question_folder, title, write_readme  # noqa: E402

NAME_CAP = 140        # longest file name a clip is given, extension included
HASH_SPAN = 1 << 20   # a copy is checked by size and by the sha256 of the first and the last MiB
SPACE_MARGIN = 256 << 20


# ---------------------------------------------------------------- the files on disk
def list_sources(src):
    """{stem: {"video": path, "sidecars": [paths]}} for every video file directly inside src.
    A sidecar belongs to the clip whose stem it starts with, followed by . _ - or M<digits> (C0001M01.XML, C0001.LRF); C0001 never claims C00010.XML."""
    files = sorted(f for f in os.listdir(src) if not f.startswith(".") and os.path.isfile(os.path.join(src, f)))
    vids = {}
    for f in files:
        stem, ext = os.path.splitext(f)
        if ext.lower() in VIDEO_EXT:
            if stem in vids:
                stop(f"DUPLICATE NAME {stem}: {vids[stem]} and {f}. Rename or remove one before organizing.")
            vids[stem] = f
    out = {s: {"video": os.path.join(src, v), "sidecars": []} for s, v in vids.items()}
    for f in files:
        if os.path.splitext(f)[1].lower() in VIDEO_EXT:
            continue
        owners = [s for s in vids if re.match(re.escape(s) + r"(?:[._-]|M\d+)", f)]
        if owners:
            out[max(owners, key=len)]["sidecars"].append(os.path.join(src, f))
    return out


def head_tail_digest(path):
    """(size, sha256 of the first and the last MiB; the whole file when it is under 2 MiB): what a copy is checked by"""
    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if size <= 2 * HASH_SPAN:
            h.update(f.read())
        else:
            h.update(f.read(HASH_SPAN))
            f.seek(size - HASH_SPAN)
            h.update(f.read(HASH_SPAN))
    return size, h.hexdigest()


class CopyFailed(Exception):
    pass


def verified_copy(src, dst):
    """Copy src to dst: written beside the destination under a .part name, flushed, checked against the source (size and head/tail hash), then renamed into place.
    The source is never touched. A copy that does not check out is deleted and CopyFailed says so."""
    if os.path.lexists(dst):
        raise FileExistsError(f"{dst} already exists, and a copy never overwrites a file")
    tmp = dst + ".part"
    try:
        shutil.copyfile(src, tmp)
        st = os.stat(src)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))   # a camera file keeps the time it was recorded
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        if head_tail_digest(src) != head_tail_digest(tmp):
            raise CopyFailed(f"the copy of {os.path.basename(src)} does not match the original (size or the first and last MiB differ)")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def nearest_existing(path):
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return p


def same_volume(a, b):
    """True when a and b are on one volume, so a file can be renamed from one to the other. b may not exist yet: its nearest existing folder is used"""
    return os.stat(nearest_existing(a)).st_dev == os.stat(nearest_existing(b)).st_dev


# ---------------------------------------------------------------- records
def read_record(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        stop(f"{path} is not valid JSON ({e}): rewrite that record, then run this again")


def load_records(job):
    """One record per probed clip, in clip order: the final.json (else the classify.json) with what prep measured laid over it. verified = a final.json,
    or both verdict files. A clip with no record at all comes back as category MISSING."""
    scr = job["scratch"]
    res = f"{scr}/results"
    probe = {}
    with open(f"{scr}/probe.tsv", encoding="utf-8") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2:
                probe[p[0]] = float(p[1])
    with open(f"{scr}/evidence.json", encoding="utf-8") as fh:
        ev = {e["clip"]: e for e in json.load(fh)}
    recs = []
    for clip in sorted(probe):
        final, cls = f"{res}/{clip}.final.json", f"{res}/{clip}.classify.json"
        src = final if os.path.exists(final) else cls
        base = {"clip": clip, "duration_s": probe[clip], "silent": ev[clip]["digitally_silent"], "mean_db": ev[clip]["mean_db"],
                "max_db": ev[clip]["max_db"], "transcript": ev[clip]["transcript"], "ext": ev[clip]["ext"], "orientation": ev[clip].get("orientation", "vertical")}
        if not os.path.exists(src):
            recs.append({**base, "category": "MISSING", "reel": "NONE", "reel_title": "", "name": "", "flags": [], "source": None,
                         "visual_agree": None, "content_agree": None, "verified": False})
            continue
        r = read_record(src)
        if not isinstance(r, dict):
            stop(f"{src} must hold one JSON object")
        r.update(base)
        r["source"] = os.path.basename(src)
        sf = str(r.get("subject_folder") or "").strip()   # the folders are the job's fixed list: a different case is the same folder, a folder that is not on the list files as Other under its own name
        if sf:
            known = {x.lower(): x for x in list(job["subjects"]) + ["Other"]}
            if sf.lower() in known:
                r["subject_folder"] = known[sf.lower()]
            else:
                r["subject_other"] = r.get("subject_other") or sf
                r["subject_folder"] = "Other"
        for lens in ("visual", "content"):
            f = f"{res}/{clip}.{lens}.json"
            r[f"{lens}_agree"] = read_record(f).get("agree") if os.path.exists(f) else None
        r["verified"] = os.path.exists(final) or all(os.path.exists(f"{res}/{clip}.{lens}.json") for lens in ("visual", "content"))
        recs.append(r)
    return recs


TEXT_FIELDS = ("name", "reel", "reel_title", "question_asked", "answer_summary", "key_quote", "on_screen", "people", "subject_folder", "subject_other", "shot", "camera", "notes")


def record_problems(recs):
    """one plain line per record that cannot be filed as written: a category that is not one of the six, flags that are not a list. What a classifier may write loosely is
    tidied on the way (a number where a text goes becomes text, "false" where a boolean goes is false)"""
    out = []
    for r in recs:
        if r["category"] == "MISSING":
            continue
        for k in TEXT_FIELDS:
            if r.get(k) is not None and not isinstance(r[k], str):
                r[k] = str(r[k])
        if isinstance(r.get("usable"), str):
            r["usable"] = r["usable"].strip().lower() not in ("false", "no", "0", "")
        if r.get("category") not in CATEGORIES:
            out.append(f"{r['clip']}: category {r.get('category')!r} is not one of {', '.join(CATEGORIES)}")
        if not isinstance(r.get("flags", []) or [], list):
            out.append(f"{r['clip']}: flags must be a list")
        r["reel"] = r.get("reel") or "NONE"
        if r.get("flags") is None:
            r["flags"] = []
    return out


# ---------------------------------------------------------------- the plan
def fit_name(prefix, name, tail, ext):
    """<prefix><name><tail><ext> within NAME_CAP: when it is too long the descriptive name gives way (by words), never the take number or the extension"""
    def fits(n):
        return len(prefix) + len(n) + len(tail) + len(ext) <= NAME_CAP
    words = name.split()
    while len(words) > 1 and not fits(" ".join(words)):
        words.pop()
    n = " ".join(words)
    if not fits(n):
        n = n[:max(NAME_CAP - len(ext) - len(prefix) - len(tail), 1)]
    return clean(f"{prefix}{n}{tail}", NAME_CAP - len(ext)) + ext


def plan(job, recs):
    reel_folder = {q["id"]: question_folder(q) for q in job["questions"]}
    per_reel = {}
    for r in recs:
        if r["category"] == "interview" and r["reel"] in reel_folder:
            per_reel.setdefault(r["reel"], []).append(r["clip"])
    moves = []
    for r in recs:
        clip, cat, ext = r["clip"], r["category"], r["ext"]
        name = title(clean(r.get("name", "") or ""))
        if cat == "MISSING":
            rel = f"{TOP['unusable']}/{clip} - UNCLASSIFIED{ext}"
        elif cat == "off_topic":
            rel = f"{TOP['offtopic']}/" + fit_name(f"{clip} - ", name or "Off Topic Footage", "", ext)
        elif cat == "unusable" or not r.get("usable", True):
            rel = f"{TOP['unusable']}/" + fit_name(f"{clip} - ", name or "Unusable", "", ext)
        elif cat == "interview":
            reel = r.get("reel", "NONE")
            if reel in reel_folder:
                rel = f"{TOP['interview']}/{reel_folder[reel]}/" + fit_name(f"{clip} - {reel} - ", name or "Untitled", f" - Take {per_reel[reel].index(clip) + 1}", ext)
            elif job["questions"]:
                rel = f"{TOP['interview']}/{TOP['bonus']}/" + fit_name(f"{clip} - Bonus - ", name or "Untitled", "", ext)
            else:  # no question list: group by the topic the classifier named
                topic = title(clean(r.get("reel_title") or "Untitled Topic", 60)) or "Untitled Topic"
                rel = f"{TOP['interview']}/{topic}/" + fit_name(f"{clip} - ", name or "Untitled", "", ext)
        else:
            subj = clean(job["subject_overrides"].get(clip) or r.get("subject_folder") or "", 60) or "Other"
            if subj == "Other" and r.get("subject_other"):
                subj = title(clean(r["subject_other"], 40)) or "Other"
            suffix = " - No Audio" if r.get("silent") else (" - Candid Talk" if cat == "candid_talk" else "")
            rel = f"{TOP['broll']}/{subj}/" + fit_name(f"{clip} - ", name or "Untitled", suffix, ext)
        moves.append({"clip": clip, "rel": rel, "dst": os.path.join(job["root"], rel)})
    seen = {}
    for m in moves:
        if m["rel"] in seen:
            stop(f"COLLISION {m['clip']} vs {seen[m['rel']]}: {m['rel']}")
        seen[m["rel"]] = m["clip"]
    return moves


def write_manifest(recs, moves, path, src_rel):
    mv = {m["clip"]: m for m in moves}
    cols = ["clip", "new_path", "original", "category", "reel", "reel_title", "subject_folder", "duration_s", "audio", "name",
            "question_asked", "answer_summary", "key_quote", "on_screen", "people", "shot", "camera", "flags", "usable",
            "confidence", "visual_agree", "content_agree", "source", "notes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in recs:
            m = mv[r["clip"]]
            audio = "none (silent)" if r.get("silent") else f"yes (mean {r.get('mean_db')} dB)"
            w.writerow([r["clip"], m["rel"], f"{src_rel}/{r['clip']}{r['ext']}", r.get("category"), r.get("reel"), r.get("reel_title", ""),
                        r.get("subject_folder", ""), r.get("duration_s"), audio, r.get("name", ""), r.get("question_asked", ""),
                        r.get("answer_summary", ""), r.get("key_quote", ""), r.get("on_screen", ""), r.get("people", ""),
                        r.get("shot", ""), r.get("camera", ""), ";".join(r.get("flags", []) or []), r.get("usable", ""),
                        r.get("confidence", ""), r.get("visual_agree"), r.get("content_agree"), r.get("source"), r.get("notes", "")])


def save_plan(job, recs, moves, pre, sidecars, applied, mode="move", left_alone=()):
    d = {"job": job, "records": recs, "moves": moves, "applied": applied, "mode": mode, "src": job["src"], "left_alone": list(left_alone)}
    if pre is not None:
        d["pre_move_bytes"] = pre
    d["sidecars"] = sidecars
    old = jobmod.read_plan(job) or {}
    if applied:
        d["organized_at"] = old.get("organized_at") or jobmod.today()
    jobmod.write_plan(job, d)   # and Edit/plan.json once applied


# ---------------------------------------------------------------- apply
def preflight(job, moves, sources):
    """Everything that can be known before the first file moves. -> ({sidecar destinations}, copy) where copy is True when the source is on another volume."""
    root = job["root"]
    problems = []
    for m in moves:
        if os.path.exists(m["dst"]):
            problems.append(f"destination exists: {m['rel']}")
    side_dst = {}
    for m in moves:
        for sc in sources[m["clip"]]["sidecars"]:
            d = os.path.join(root, TOP["sidecars"], os.path.basename(sc))
            if d in side_dst:
                problems.append(f"two sidecars would both land on {TOP['sidecars']}/{os.path.basename(sc)}")
            if os.path.exists(d):
                problems.append(f"destination exists: {TOP['sidecars']}/{os.path.basename(sc)}")
            side_dst[d] = sc
    os.makedirs(root, exist_ok=True)
    copy = not same_volume(job["src"], root)
    if copy:
        need = sum(os.path.getsize(sources[m["clip"]]["video"]) + sum(os.path.getsize(s) for s in sources[m["clip"]]["sidecars"]) for m in moves)
        free = shutil.disk_usage(root).free
        if free < need * 1.01 + SPACE_MARGIN:
            problems.append(f"not enough room for the copy: {need / 1e9:.1f} GB of footage and {free / 1e9:.1f} GB free where the shoot folder is")
    if problems:
        stop("REFUSED, nothing moved:\n  " + "\n  ".join(problems[:20]))
    return side_dst, copy


def apply_moves(job, moves, sources, copy=False, say=lambda s: None):
    """File every clip and its sidecars. copy=False renames each into place; copy=True copies each (checked) and leaves the source alone.
    A failure, or Ctrl-C, undoes what this run did (renames renamed back, copies deleted) and removes what was generated first."""
    root = job["root"]
    done = []
    n = len(moves)
    try:
        for i, m in enumerate(moves, 1):
            s = sources[m["clip"]]
            os.makedirs(os.path.dirname(m["dst"]), exist_ok=True)
            pairs = [(s["video"], m["dst"])]
            for sc in s["sidecars"]:
                d = os.path.join(root, TOP["sidecars"])
                os.makedirs(d, exist_ok=True)
                pairs.append((sc, os.path.join(d, os.path.basename(sc))))
            for orig, dst in pairs:
                if copy:
                    if orig == s["video"]:
                        say(f"[copy {i}/{n}] {os.path.basename(orig)} ({os.path.getsize(orig) / 1e9:.2f} GB)")
                    verified_copy(orig, dst)
                else:
                    os.rename(orig, dst)
                done.append((dst, orig))
    except (Exception, KeyboardInterrupt) as e:
        for dst, orig in reversed(done):
            try:
                if copy:
                    os.remove(dst)
                else:
                    os.rename(dst, orig)
            except OSError:
                pass
        remove_generated(job)
        what = "copies deleted, the source was not touched" if copy else "renames rolled back, camera files are where they were"
        if isinstance(e, KeyboardInterrupt):
            stop(f"STOPPED by you; {len(done)} {what}")
        stop(f"MOVE FAILED ({e}); {len(done)} {what}")


def prune_empty(root, tops):
    """rmdir every empty directory under the given top-level folders (deepest first), then the tops themselves.
    A folder that still holds a file this tool did not make (an editor's note, a stray render) is left alone; those folders are returned."""
    kept = []
    for top in tops:
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for d, _, fs in sorted(os.walk(base), key=lambda x: -len(x[0])):
            for f in fs:
                if f == ".DS_Store":
                    os.remove(os.path.join(d, f))
            try:
                os.rmdir(d)
            except OSError:
                kept.append(os.path.relpath(d, root))
    return kept


def inside(root, path):
    """True when path is inside root. A manifest this tool wrote never points outside the shoot; one that was edited by hand or damaged must not be able to move or delete a file elsewhere"""
    root = os.path.abspath(root)
    return os.path.abspath(path).startswith(root + os.sep)


def has_mark(path):
    """True for a notes file this tool wrote: its first line starts with MARK"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.readline().startswith(MARK)
    except OSError:
        return False


def remove_generated(job):
    """Delete everything this tool generated under ROOT (cut takes, per-video notes, index, manifests, README, Edit/plan.json) and the folders that held it."""
    root, src, scr = job["root"], job["src"], job["scratch"]
    gone = 0
    skip = [os.path.abspath(scr)] + ([os.path.abspath(src)] if os.path.abspath(src) != os.path.abspath(root) else [])
    for r in jobmod.read_csv(os.path.join(root, "Takes Manifest.csv")):
        p = os.path.join(root, r["file"])
        if inside(root, p) and os.path.exists(p):
            os.remove(p)
            gone += 1
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if os.path.abspath(os.path.join(d, x)) not in skip]
        for f in fs:
            p = os.path.join(d, f)
            if f.endswith(".txt") and not f.startswith(".") and has_mark(p):
                os.remove(p)
                gone += 1
    for g in GENERATED + ("Edit/plan.json",):
        p = os.path.join(root, g)
        if os.path.exists(p):
            os.remove(p)
            gone += 1
    tops = [TOP["interview"], TOP["broll"], TOP["offtopic"], TOP["unusable"], TOP["sidecars"], "Raw Clips", "Edit"]
    bad = bad_takes_folder(job)
    if bad:
        tops.append(bad)
    kept = prune_empty(root, tops)
    return gone, kept


def reverse(job):
    root, src = job["root"], job["src"]
    manifest = os.path.join(root, "Footage Manifest.csv")
    P = jobmod.read_plan(job)
    if os.path.exists(manifest):
        rows = [(r["new_path"], os.path.basename(r["original"])) for r in jobmod.read_csv(manifest)]
    elif P:
        ext = {r["clip"]: r["ext"] for r in P["records"]}
        rows = [(m["rel"], m["clip"] + ext[m["clip"]]) for m in P["moves"]]
    else:
        stop("nothing to reverse: there is no Footage Manifest.csv and no plan.json for this shoot")
    mode = (P or {}).get("mode", "move")
    back = src if mode != "copy" else os.path.join(root, "RAW")   # after a copy the originals never left their source: the copies go to RAW inside the shoot
    side = os.path.join(root, TOP["sidecars"])
    side_files = sorted(f for f in os.listdir(side) if not f.startswith(".")) if os.path.isdir(side) else []
    targets = [n for _, n in rows] + side_files
    clash = [n for n in targets if os.path.exists(os.path.join(back, n))]
    if clash:
        stop(f"REFUSED: {back} already holds {clash[:5]}")
    os.makedirs(back, exist_ok=True)
    n, missing = 0, []
    for rel, name in rows:
        p = os.path.join(root, rel)
        if not inside(root, p) or os.path.basename(name) != name:
            stop(f"REFUSED: the manifest names a path outside this shoot ({rel!r} to {name!r}); nothing was moved")
        if os.path.exists(p):
            os.rename(p, os.path.join(back, name))
            n += 1
        else:
            missing.append(rel)
    for f in side_files:
        os.rename(os.path.join(side, f), os.path.join(back, f))
    pj = f"{job['scratch']}/plan.json"   # the camera files are back: record that before anything else can fail, or `plan` stays locked behind a finished reverse
    if os.path.exists(pj):
        d = read_record(pj)
        d["applied"] = False
        common.atomic_write_json(pj, d)
    gone, kept = remove_generated(job)
    print(f"reversed {n} clips back to {back}; removed {gone} generated files and the folders that held them")
    if missing:
        print(f"not found, so not moved back ({len(missing)}): {', '.join(missing[:5])}")
    if kept:
        print(f"left in place (not made by this tool, move or delete by hand): {', '.join(kept)}")
    return n


# ---------------------------------------------------------------- the run
def remove_source_if_empty(src, root):
    """RAW goes when nothing but the operating system's own junk (.DS_Store, ._name, Thumbs.db) is left in it; a folder that still holds anything else stays"""
    if os.path.abspath(src) == os.path.abspath(root) or not os.path.isdir(src):
        return True
    for f in os.listdir(src):
        if is_os_junk(f) and os.path.isfile(os.path.join(src, f)) and not os.path.islink(os.path.join(src, f)):
            os.remove(os.path.join(src, f))
    try:
        os.rmdir(src)
        return True
    except OSError:
        return False


def run(job, mode, allow_unverified=False, say=print):
    root, src, scr = job["root"], job["src"], job["scratch"]
    manifest = os.path.join(root, "Footage Manifest.csv")
    old = jobmod.read_plan(job) or {}   # the scratch copy, else Edit/plan.json
    if old.get("applied"):
        stop("plan.json says this shoot is already organized. Run `reverse` first to plan again.")
    recs = load_records(job)
    problems = record_problems(recs)
    if problems:
        say("RECORDS THAT CANNOT BE FILED:\n  " + "\n  ".join(problems))
        stop(f"{len(problems)} record(s) need a fix; rewrite those final.json files and run this again")
    moves = plan(job, recs)
    cats = {}
    for r in recs:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    say(f"categories: {cats}")
    say(f"missing: {[r['clip'] for r in recs if r['category'] == 'MISSING']}")
    for m in moves:
        say(f"  {m['clip']} -> {m['rel']}")
    unverified = [r["clip"] for r in recs if not r.get("verified")]
    if unverified:
        say(f"\nUNVERIFIED ({len(unverified)}): no record, or a record nobody verified (no final.json, and not both verdict files) for {', '.join(unverified)}")
        if not allow_unverified:
            say("Nothing is filed off a single read. Write a final.json for each of them (prompts/classify-batch.md), or pass --allow-unverified.")
            stop(f"{len(unverified)} clip(s) have no verified record")
    if not os.path.isdir(src):
        stop(f"source folder missing: {src}")
    sources = list_sources(src)
    absent = [m["clip"] for m in moves if m["clip"] not in sources]
    if absent:
        stop(f"source missing for {absent[:5]}: if an earlier run was interrupted, run `reverse` first to put every file back, then plan again")
    pre = sum(os.path.getsize(sources[m["clip"]]["video"]) for m in moves)
    side_map = {m["clip"]: [os.path.basename(s) for s in sources[m["clip"]]["sidecars"]] for m in moves}
    if mode == "plan":
        save_plan(job, recs, moves, pre, side_map, False)
        write_manifest(recs, moves, f"{scr}/Footage Manifest.preview.csv", os.path.basename(src))
        say("\nPLAN ONLY, nothing moved.")
        return moves
    side_dst, copy = preflight(job, moves, sources)
    filed = {m["clip"] for m in moves}
    left_alone = sorted(os.path.basename(s["video"]) for stem, s in sources.items() if stem not in filed)   # video files prep could not read: they stay where they are
    # the manifest and plan exist BEFORE the first file moves, so any crash can be reversed from them
    save_plan(job, recs, moves, pre, side_map, False, mode="copy" if copy else "move")
    write_manifest(recs, moves, manifest, os.path.basename(src))
    write_readme(job, recs, moves)
    apply_moves(job, moves, sources, copy=copy, say=say)
    save_plan(job, recs, moves, pre, side_map, True, mode="copy" if copy else "move", left_alone=left_alone)
    left = sorted(f for f in os.listdir(src) if not f.startswith("."))
    if copy:
        say(f"copied {len(moves)} clips ({pre / 1e9:.2f} GB), each checked against the original. {src} was not changed: the card can be ejected.")
    else:
        say(f"moved {len(moves)} clips. source leftovers: {left}")
        if remove_source_if_empty(src, root) and os.path.abspath(src) != os.path.abspath(root):
            say(f"{src} was empty, so it was removed")
    return moves


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("mode", choices=["plan", "apply", "reverse"])
    ap.add_argument("--allow-unverified", action="store_true")
    a = ap.parse_args(argv)
    job = load_job(a.job)
    if a.mode == "reverse":
        return reverse(job)
    return run(job, a.mode, a.allow_unverified)


if __name__ == "__main__":
    main()
