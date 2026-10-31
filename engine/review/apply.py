#!/usr/bin/env python3
"""apply.py: make the review decisions real on disk. Cut clips go to the Trash, and trimmed clips become files that hold only
the parts that were kept. Nothing is deleted outright: the Trash (or the holding folder under Review/trash) keeps the original.

    python3 apply.py plan [--shoot S]       writes Review/apply-plan.json and prints what would happen. Changes nothing.
    python3 apply.py go [--sidecars]        runs exactly that plan. Only after the user says go.

A trim is a stream copy, never a re-encode. The part starts on the keyframe at or before the chosen start, so it begins a
little early; how much is measured on the finished part (not guessed) and saved as that part's own kept range, so an edit
still starts exactly where the user set it. Only parts that cut something worth cutting are made: a stream copy can only cut on a
keyframe, so a trim that would remove under a second is left as a kept range on the clip.

Before the original is touched, every part must pass all of these, and any failure removes every part and leaves the original:
  - ffmpeg finished, and the part is about the length the plan said
  - the whole part decodes with no error (video and its first sound track; a track ffmpeg cannot decode is not copied)
  - the picture at three times inside the part matches the original at the matching times (this proves the saved lead is right)
Each step also checks that nothing changed since the plan: the file's size, modification time and content fingerprint, and the
decision itself. A step that fails any check is skipped and reported, and the rest carry on. A plan runs once.

Files next to a clip (notes, transcripts) are never touched. Timecode and other data tracks are not copied into parts.
macOS leaves ._ stub files on some drives: the ones beside new parts are removed.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402

MIN_SAVE_S = 1.0            # a single trim must be able to remove at least this much, or it stays a kept range
FRAME_GAP = 3.0             # mean difference (0 to 255) allowed between original and part pictures. Identical decodes give 0.
MOV_LIKE = {".mp4", ".mov", ".m4v"}
NEVER_COPY_AUDIO = {"apple_apac"}   # ffmpeg copies it but its timing drags the whole part, and nothing can play it
SKIP_DIRS = {"node_modules", ".git", "out", "renders"}


class Fail(Exception):
    """One step could not be done safely. The message is what the user reads."""


# ---------------------------------------------------------------- ffprobe and ffmpeg helpers

def streams_of(path) -> list[dict]:
    r = common.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)], check=False)
    try:
        return json.loads(r.stdout or "{}").get("streams", [])
    except ValueError:
        return []


@functools.lru_cache(maxsize=1)
def decoder_names() -> frozenset:
    r = common.run(["ffmpeg", "-hide_banner", "-decoders"], check=False)
    names = set()
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def copyable_audio(streams: list[dict]) -> list[int]:
    """Indexes of the sound tracks worth copying: the ones ffmpeg can decode."""
    names = decoder_names()
    out = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        codec = s.get("codec_name", "")
        if codec in NEVER_COPY_AUDIO or (names and codec not in names):
            continue
        out.append(int(s["index"]))
    return out


def packets(path, selector: str, interval: str | None = None):
    """([(pts, dts, size, is_key)], container start time) for one stream, in file order."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", selector, "-show_entries", "packet=pts_time,dts_time,size,flags:format=start_time", "-of", "json"]
    if interval:
        cmd += ["-read_intervals", interval]
    r = common.run(cmd + [str(path)], check=False)
    try:
        d = json.loads(r.stdout or "{}")
    except ValueError:
        return [], 0.0
    out = []
    for p in d.get("packets", []):
        if p.get("pts_time") in (None, "N/A"):
            continue
        dts = p.get("dts_time")
        out.append((float(p["pts_time"]), float(dts) if dts not in (None, "N/A") else None, int(p.get("size") or 0), "K" in (p.get("flags") or "")))
    try:
        start = float(d.get("format", {}).get("start_time") or 0.0)
    except ValueError:
        start = 0.0
    return out, start


def keyframe_estimate(path, vindex: int, t: float) -> float:
    """The last keyframe at or before t. An estimate for the plan (the cut measures the real start itself)."""
    if t <= 0.05:
        return 0.0
    for back in (12, 60, 300, None):
        window = f"{max(0.0, t - back):.3f}%{t + 0.05:.3f}" if back else f"0%{t + 0.05:.3f}"
        pk, start = packets(path, str(vindex), window)
        ks = [p[0] - start for p in pk if p[3] and p[0] - start <= t + 0.001]
        if ks:
            return max(ks)
        if back and t - back <= 0:
            break
    return 0.0


def duration_of(path) -> float:
    r = common.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], check=False)
    try:
        return float((r.stdout or "").strip())
    except ValueError:
        return 0.0


def align_offset(src, vindex: int, part, a: float):
    """Where the part sits in the original's timeline: part_time = original_time + offset. Found by matching the part's first
    video packets to the original's by size (a stream copy keeps every packet byte for byte), so it does not depend on which
    keyframe ffmpeg chose. Times are the ones a player or ffmpeg sees, which is the packet time minus the container's start time
    (a copied part can start a fraction of a second in when its sound track begins first). Returns (offset, the original's time of
    the part's first frame, how many places matched) or None. With more than one match the earliest is taken: the part then
    starts later, never earlier, than it should."""
    pp, part_start = packets(part, "v:0", "%+#6")
    want = [p[2] for p in pp[:4]]
    if not want:
        return None
    for back in (30, 120, 600, None):
        window = f"{max(0.0, a - back):.3f}%{a + 0.05:.3f}" if back else None
        op, src_start = packets(src, str(vindex), window)
        sizes = [p[2] for p in op]
        hits = [i for i in range(len(op) - len(want) + 1) if sizes[i:i + len(want)] == want and op[i][3]]
        if hits:
            first = op[hits[0]][0] - src_start
            return (pp[0][0] - part_start) - first, first, len(hits)
    return None


def grey(path, t: float, stream: str) -> bytes:
    r = common.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.4f}", "-i", str(path), "-map", stream, "-frames:v", "1",
                    "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "-"], check=False, text=False)
    return r.stdout if isinstance(r.stdout, bytes) else b""


def gap(x: bytes, y: bytes) -> float:
    if not x or not y or len(x) != len(y):
        return 255.0
    return sum(abs(p - q) for p, q in zip(x, y)) / len(x)


def decode_clean(part) -> tuple[bool, str]:
    """Decode the whole part. Video and its first sound track only: a track ffmpeg has no decoder for would fail this for good files."""
    r = common.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(part), "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"], check=False)
    err = (r.stderr or "").strip()
    if r.returncode == 0 and not err:
        return True, ""
    return False, err.splitlines()[-1][:200] if err else f"ffmpeg exit {r.returncode}"


def verify_cut(src, vindex: int, part, a: float, b: float, offset: float, fps: float = 0.0) -> tuple[bool, str]:
    """The picture in the part at (t + offset) must be the original's picture at t, near the start, in the middle and near the end.
    Times are nudged to the middle of a frame, and one frame either side is accepted (the saved lead is rounded to a thousandth
    of a second, so a frame boundary can fall either way). Being a few frames out would show up as a mismatch."""
    step = 1.0 / fps if fps and fps > 1 else 1.0 / 30
    span = b - a
    worst = 0.0
    for t in (a + min(0.3, span * 0.25), a + span * 0.5, b - min(0.25, span * 0.1)):
        t += step / 2
        ref = grey(src, t, f"0:{vindex}")
        best = 255.0
        for k in (0, -1, 1):
            best = min(best, gap(ref, grey(part, t + offset + k * step, "0:v:0")))
            if best <= FRAME_GAP:
                break
        worst = max(worst, best)
    return worst <= FRAME_GAP, f"the part's picture does not match the original (off by {worst:.0f} of 255), so its start cannot be trusted"


def cut_command(src, vindex: int, audio: list[int], codec: str, start: float, dur: float, tmp: Path) -> list[str]:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.6f}", "-i", str(src), "-t", f"{dur:.3f}", "-map", f"0:{vindex}"]
    for i in audio:
        cmd += ["-map", f"0:{i}"]
    cmd += ["-c", "copy"]
    mov = tmp.suffix.lower() in MOV_LIKE
    if mov and codec in ("hevc", "h265"):
        cmd += ["-tag:v", "hvc1"]               # without it QuickTime and Safari will not open copied HEVC
    cmd += ["-map_metadata", "0", "-avoid_negative_ts", "make_zero"]
    if mov:
        cmd += ["-movflags", "+faststart", "-write_tmcd", "0"]
    return cmd + [str(tmp)]


# ---------------------------------------------------------------- plan

def _gb(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def project_mentions(names: set[str]) -> dict[str, list[str]]:
    """File names that a project's .json files mention, so the plan can warn before a clip in use goes away."""
    out: dict[str, list[str]] = {}
    try:
        proj = common.ws("Projects")
    except SystemExit:
        return out
    if not proj.is_dir():
        return out
    for dp, dn, fn in os.walk(proj):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        for f in fn:
            if f.endswith(".json") and not f.startswith("._"):
                p = Path(dp) / f
                try:
                    if p.stat().st_size > 3 << 20:
                        continue
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for n in names:
                    if n in text:
                        out.setdefault(n, []).append(str(p.relative_to(proj)))
    return out


def _free_name(taken: set, folder: Path, stem: str, ext: str) -> Path:
    n, cand = 1, folder / f"{stem}{ext}"
    while cand.exists() or str(cand) in taken:
        n += 1
        cand = folder / f"{stem} {n}{ext}"
    taken.add(str(cand))
    return cand


def make_plan(shoot: str | None = None, say=print) -> dict:
    inv = store.load_inventory()
    decs = store.load_decisions()                       # drafts are not decisions: they never reach a plan
    steps, skipped, taken = [], [], set()
    for c in inv["clips"]:
        d = decs.get(c["id"])
        if not d or (shoot and c["project"].lower() != shoot.lower()):
            continue
        if d["s"] == "keep" and not d.get("r"):
            continue
        dur = float(c.get("dur") or 0)
        for p in c["paths"]:
            full = store.abs_of(p)
            if not os.path.exists(full):
                continue
            st = os.stat(full)
            if st.st_size != p.get("size") or int(st.st_mtime) != p.get("mtime"):
                skipped.append({"rel": p["rel"], "why": "changed since the last ingest: run ingest and review it again"})
                continue
            step = {"id": c["id"], "shoot": c["project"], "kind": c["kind"], "root": p["root"], "rel": p["rel"], "size": st.st_size, "mtime": int(st.st_mtime),
                    "dur": dur, "vindex": c.get("vindex") or 0, "codec": c.get("codec"), "fps": c.get("fps") or 0.0, "s": d["s"], "r": d.get("r"), "at": d["at"]}
            if d["s"] == "cut":
                step["do"] = "trash"
                steps.append(step)
                continue
            parts = []
            for a, b in sorted(d["r"]):
                a, b = max(0.0, float(a)), min(float(b), dur)
                if a < b:
                    parts.append((a, b))
            if not parts:
                skipped.append({"rel": p["rel"], "why": "its kept range is outside the clip"})
                continue
            kf = [keyframe_estimate(full, step["vindex"], a) for a, _ in parts]
            if len(parts) == 1:
                removed = kf[0] + max(0.0, dur - parts[0][1])
                if removed < MIN_SAVE_S:
                    skipped.append({"rel": p["rel"], "why": f"a stream copy could only remove about {removed:.1f}s, so it stays a kept range on the clip"})
                    continue
            folder, stem, ext = Path(full).parent, Path(full).stem, Path(full).suffix
            step["do"] = "trim"
            step["parts"] = []
            for i, ((a, b), k) in enumerate(zip(parts, kf), 1):
                label = "Trimmed" if len(parts) == 1 else f"Part {i}"
                out = _free_name(taken, folder, f"{stem} - {label}", ext)
                step["parts"].append({"a": round(a, 3), "b": round(b, 3), "kf": round(k, 3), "out": os.path.relpath(out, p["root"]).replace(os.sep, "/")})
            step["keep_s"] = round(sum(b - a for a, b in parts), 2)
            steps.append(step)
    trash = [s for s in steps if s["do"] == "trash"]
    trims = [s for s in steps if s["do"] == "trim"]
    plan = {"made": store.stamp(), "shoot": shoot, "steps": steps, "skipped": skipped}
    common.atomic_write_json(store.f_plan(), plan)
    saved = sum(s["size"] * (1 - s["keep_s"] / max(s["dur"], 0.01)) for s in trims)
    if not steps:
        say("Nothing to apply: no clip is marked Cut, and no trim would remove enough to be worth a new file.")
    else:
        say(f"PLAN{f' ({shoot})' if shoot else ''}: {len(trash)} file{'s' if len(trash) != 1 else ''} to the Trash ({_gb(sum(s['size'] for s in trash))}), "
            f"{len(trims)} file{'s' if len(trims) != 1 else ''} trimmed into {sum(len(s['parts']) for s in trims)} part{'s' if sum(len(s['parts']) for s in trims) != 1 else ''} "
            f"(about {_gb(saved)} saved). Space comes back when the Trash is emptied.")
    for s in trims:
        say(f"  TRIM   {s['rel']}  ->  keep " + ", ".join(f"{p['a']:.2f}-{p['b']:.2f}s" for p in s["parts"]))
    for s in trash:
        say(f"  TRASH  {s['rel']}")
    for k in skipped:
        say(f"  SKIP   {k['rel']}: {k['why']}")
    mentions = project_mentions({os.path.basename(s["rel"]) for s in steps})
    named = [s for s in steps if os.path.basename(s["rel"]) in mentions]
    if named:
        say(f"\nHEADS UP: {len(named)} of these {'is' if len(named) == 1 else 'are'} named in a project file:")
        for s in named:
            say(f"  {s['rel']}\n      in " + "; ".join(mentions[os.path.basename(s['rel'])][:3]))
    if steps:
        say(f"\nSaved {store.f_plan()}. Show this to the user. Run 'apply.py go' only after they say go.")
    return plan


# ---------------------------------------------------------------- go

def _remove(*paths) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _stub(p: Path) -> None:
    _remove(p.with_name("._" + p.name))


def do_trash(full: str) -> None:
    """Move one file to the Trash. Judged by the result on disk, not by what the helper says: if the file is gone from its place it
    was moved, even when the helper complained afterwards, and if it is still there nothing was done."""
    err = None
    try:
        common.trash(full)
    except Exception as e:      # shutil errors from the holding-folder fallback, a vanished Trash, a permission error
        err = e
    if os.path.exists(full):
        raise Fail("it could not be moved to the Trash" + (f" ({err})" if err else " (it is still there)") + ", so it was left alone")


def do_trim(s: dict, full: str, sidecars: bool) -> dict:
    """Make every part, check each, put them in place, trash the original. Returns the new decisions {id: doc}."""
    root = Path(s["root"])
    ext = Path(full).suffix
    vindex = s["vindex"]
    streams = streams_of(full)
    audio = copyable_audio(streams)
    tmps, finals, checked = [], [], []
    try:
        for part in s["parts"]:
            out = root / part["out"]
            if out.exists():
                raise Fail(f"a file called {out.name} is already there")
            a, b = part["a"], part["b"]
            tmp = out.with_name(f".{out.stem}.cutroom-part{ext}")
            tmps.append(tmp)
            _remove(tmp)
            r = common.run(cut_command(full, vindex, audio, s.get("codec") or "", a, b - a, tmp), check=False)
            if r.returncode != 0 or not tmp.exists():
                tail = (r.stderr or "").strip().splitlines()
                raise Fail("ffmpeg could not make the part: " + (tail[-1][:160] if tail else f"exit {r.returncode}"))
            got = duration_of(tmp)
            al = align_offset(full, vindex, tmp, a)
            if al is None:
                raise Fail("could not line the part up with the original, so its start cannot be trusted")
            offset, first, _ = al
            if not (b - a - 0.25 <= got <= (b - first) + 1.5):
                raise Fail(f"the part is {got:.2f}s long but about {b - first:.2f}s was expected")
            ok, why = decode_clean(tmp)
            if not ok:
                raise Fail("the part does not play through cleanly: " + why)
            ok, why = verify_cut(full, vindex, tmp, a, b, offset, s.get("fps") or 0.0)
            if not ok:
                raise Fail(why)
            checked.append((tmp, out, a, b, offset, got))
        for tmp, out, *_ in checked:
            if out.exists():
                raise Fail(f"a file called {out.name} appeared while the parts were being made")
            os.replace(tmp, out)
            finals.append(out)
            _stub(out)
        try:
            do_trash(full)
        except Fail:
            _remove(*finals)
            finals.clear()
            raise
    except Fail:
        _remove(*tmps, *finals)
        raise
    except Exception as e:
        _remove(*tmps, *finals)
        raise Fail(f"{type(e).__name__}: {e}") from e
    docs = {}
    for (tmp, out, a, b, offset, got) in checked:
        head = max(0.0, a + offset)
        end = min(b + offset, got)
        doc = {"s": "keep", "r": [[round(head, 3), round(end, 3)]] if head > 0.04 else None, "at": store.now_ms(),
               "p": os.path.relpath(out, root).replace(os.sep, "/"), "b": s["shoot"], "k": s["kind"], "n": out.name, "from_id": s["id"]}
        docs[common.fingerprint(out)] = doc
        if sidecars:
            note = f"TRIMMED FROM  {os.path.basename(full)}  (kept {a:.2f}s to {b:.2f}s of {s['dur']:.2f}s, {time.strftime('%Y-%m-%d')})\n"
            if head > 0.04:
                note += f"STARTS EARLY  this file starts {head:.2f}s before the kept part; use it from {head:.2f}s\n"
            out.with_suffix(".txt").write_text(note, encoding="utf-8")
    return docs


def run_step(s: dict, sidecars: bool) -> tuple[str, str, dict]:
    """(result, detail, new decisions). result is done, skipped or failed."""
    full = os.path.join(s["root"], s["rel"])
    if not os.path.exists(full):
        return "skipped", "already gone", {}
    st = os.stat(full)
    if st.st_size != s["size"] or int(st.st_mtime) != s["mtime"]:
        return "skipped", "the file changed since the plan", {}
    if common.fingerprint(full) != s["id"]:
        return "skipped", "the file's content is not what was reviewed", {}
    cur = store.load_decisions().get(s["id"])
    if not cur or cur.get("s") != s["s"] or cur.get("at") != s["at"]:
        return "skipped", "the decision changed since the plan", {}
    try:
        if s["do"] == "trash":
            do_trash(full)
            return "done", "to the Trash", {}
        docs = do_trim(s, full, sidecars)
        return "done", f"trimmed into {len(docs)} part{'s' if len(docs) != 1 else ''}, original to the Trash", docs
    except Fail as e:
        return "failed", str(e), {}


def go(sidecars: bool = False, say=print, refresh: bool = True) -> dict:
    plan = store.read_json(store.f_plan(), None)
    if not plan or not plan.get("steps"):
        common.stop("there is no plan to run. Run 'apply.py plan' first, and show it to the user.")
    if plan.get("ran"):
        common.stop(f"this plan already ran on {plan['ran']}. Run 'apply.py plan' to make a new one.")
    results, new_docs = [], {}
    say(f"Running {len(plan['steps'])} step{'s' if len(plan['steps']) != 1 else ''} from the plan made {plan.get('made', '')}")
    for s in plan["steps"]:
        res, detail, docs = run_step(s, sidecars)
        new_docs.update(docs)
        results.append({"rel": s["rel"], "do": s["do"], "result": res, "detail": detail})
        store.log_apply(step=s["rel"], do=s["do"], result=res, detail=detail, parts=[d["p"] for d in docs.values()])
        say(f"  {res.upper():<8} {s['rel']}: {detail}")
    if new_docs:
        store.log_apply(event="new part decisions", docs=new_docs)       # on record before they are saved, in case the save fails
        store.merge_decisions(new_docs, source="apply")
    plan["ran"] = store.stamp()
    plan["results"] = results
    common.atomic_write_json(store.f_plan(), plan)
    done = [r for r in results if r["result"] == "done"]
    n_trash = sum(1 for r in done if r["do"] == "trash")
    n_trim = sum(1 for r in done if r["do"] == "trim")
    say(f"\nDONE: {len(done)} of {len(results)} steps ({n_trash} to the Trash, {n_trim} trimmed into {len(new_docs)} new part{'s' if len(new_docs) != 1 else ''}).")
    for r in results:
        if r["result"] != "done":
            say(f"  NOT DONE {r['rel']}: {r['detail']}")
    if refresh and done:
        try:
            from review import ingest
            say("Refreshing the review list...")
            ingest.refresh(say=say)
        except (Exception, SystemExit) as e:            # the files are already changed: a failed refresh must not read as a failed apply
            say(f"Could not refresh the list ({e}). Run ingest.")
    return {"results": results, "new_parts": new_docs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Trash the clips marked Cut and trim the ones marked with parts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="show what would happen")
    p.add_argument("--shoot", help="only this shoot")
    g = sub.add_parser("go", help="do exactly what the plan says")
    g.add_argument("--sidecars", action="store_true", help="write a .txt next to each new part saying where it came from")
    g.add_argument("--no-refresh", action="store_true", help="do not rebuild the review list afterwards")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        make_plan(a.shoot)
        return 0
    res = go(sidecars=a.sidecars, refresh=not a.no_refresh)
    return 0 if all(r["result"] == "done" for r in res["results"]) else 1


if __name__ == "__main__":
    store.run_cli(main)
