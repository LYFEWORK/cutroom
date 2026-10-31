#!/usr/bin/env python3
"""takes.py: cut every take and every blooper out of the raw clips into its OWN file, with handles on each side.

   python3 takes.py --job JOB.json [--handle 5] [--dry-run]

Runs AFTER organize.py apply and AFTER the segmenting step wrote <scratch>/results/<clip>.segments.json (see prompts/segment.md).
Never assembles anything: one file per take, that is the deliverable.

<scratch>/results/<clip>.segments.json = {"clip": "C0383", "segments": [
    {"start": 25.7, "end": 33.9, "kind": "take" | "blooper", "line": "L02", "quality": "clean" | "usable" | "flub", "note": "..."} ]}

Tree after this runs:
   Interview/<Lxx Title>/<clip>-NN - <Lxx> - Take n - <Quality> - <Note>.mov      one file per attempt, shoot order
   B-Roll/Bloopers/<clip>-NN - Blooper - <Lxx> - <Note>.mov                         one file per blooper moment
   Bad Takes/<clip>-NN - <Lxx> - Take n - Flub - <Note>.mov                       every flubbed take on an ad shoot (job.bad_takes_folder), so the line folders hold the good ones
   Raw Clips/<original filed name>                                                  the untouched camera files
   Takes Manifest.csv                                                               every cut: source clip, in/out, line, take, quality, note

Rules:
- HANDLE s of real footage on the front and the back of every cut (default 5, or takes_handle_s in config.json), clamped to the clip edges. Never black, never trimmed tighter.
- Bloopers (laughs, swears, self-roasts, meltdowns, crew jokes) are cut out the same way and filed under B-Roll/Bloopers.
- Native resolution per clip: a 1080p clip stays 1080p, a 4K clip stays 4K. Rotation metadata is honored (vertical stays vertical). No slate, no overlay.
- Video is re-encoded frame-accurately to H.264 at 1.3x the camera clip's own video bitrate (floor 8 Mbit/s), on h264_videotoolbox when this Mac has it,
  else (Linux, Windows through WSL2, a Mac without it) on libx264 told the same average bitrate (its single-pass rate control lands close to the target).
  No -maxrate and no cap. A -maxrate is what throttles VideoToolbox: on a shoot's leanest take it left 0.10x to 0.16x of the camera clip's bitrate whatever the
  target, and a first version that set one cut 104 takes to 0.10 to 0.69 of their camera clips (0.50 at the median). Without it VideoToolbox is still not exact:
  the same take at 1.3x came out at 0.85x, 0.91x and 1.03x of the camera clip in three runs, so every cut is measured (below). Audio PCM, from the clip's first audio
  stream (a clip with no sound is cut without any). The camera originals are never cut or re-encoded: they move to Raw Clips/.
- Every cut is measured: its video bitrate (the video packets' sizes over their duration) against the same span of its camera clip, read the same way.
  A cut under its camera clip is redone at 1.6x, then with libx264 -crf 9 -preset medium (4K at 97 Mbit/s on one shoot: crf 14 landed at 0.41x to 0.76x
  of the camera clip, crf 10 at 0.96x on a lean span, crf 9 at 1.15x to 1.74x on all five spans tried, and 0.99x once on an 81 Mbit/s 1080p take on Linux,
  which is why crf 9 is the last rung and not the only one off a Mac); a cut still under it FAILS, in one line naming the
  take and both bitrates, and the run stops before the manifest and before the camera files move. A rerun cuts an existing take again when it is under its
  camera clip and keeps it when it is not. Two takes are cut at once.
- Also writes a thumbnail per cut (<scratch>/thumbs/<cut id>.jpg) and rewrites README.txt to say what was cut.
"""
import argparse, csv, functools, json, os, re, shutil, subprocess, sys, threading
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common
from common import stop
import prep
from job import bad_takes_folder, cut_id, load_job, question_folder, read_csv, read_plan, write_plan, write_readme

# ---------------------------------------------------------------- how a take is encoded
FLOOR_BPS = 8_000_000   # a camera clip thinner than this (a phone at 4 Mbit/s) is still cut at 8: the re-encode must not thin it more
LANES = 2               # takes cut at once (VideoToolbox runs two sessions well)
CRF = 9                 # libx264's quality setting, the last rung
# The ladder a take climbs until its video bitrate is not under its camera clip's: (encoder, multiple of the camera clip's bitrate), and a last rung with no
# multiple, libx264 at a fixed quality (CRF). A Mac climbs VideoToolbox; anything without it (Linux, WSL2) climbs libx264 at the same two targets first.
RUNGS = (("h264_videotoolbox", 1.3), ("h264_videotoolbox", 1.6), ("libx264", None))
RUNGS_X264 = (("libx264", 1.3), ("libx264", 1.6), ("libx264", None))
EPS = 1e-5              # seconds: ffprobe prints a presentation time to the microsecond


class TakeFailed(Exception):
    """a take no encode got to its camera clip's bitrate; str(e) is the one plain line that names it"""


class Stopped(Exception):
    """the run is stopping (Ctrl-C, or an error that is not a failed take): nothing new is started"""


STOP = threading.Event()


@functools.lru_cache(maxsize=None)
def have_videotoolbox():
    return "h264_videotoolbox" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout


def ladder():
    return RUNGS if have_videotoolbox() else RUNGS_X264


def rung_name(rung):
    enc, mult = rung
    if mult is None: return f"libx264 crf {CRF}"
    return f"{'libx264' if enc == 'libx264' else 'VideoToolbox'} {mult:g}x"


def mbps(bps):
    """a bitrate as Mbit/s for a line of output, with enough digits to tell two thin ones apart (a phone clip, a test fixture)"""
    m = bps / 1e6
    return f"{m:.1f}" if m >= 10 else f"{m:.2f}" if m >= 1 else f"{m:.3f}"


def enc_args(rung, src_bps):
    """the video encoder arguments of one rung. VideoToolbox is told its average bitrate and nothing else: a -maxrate throttles it to a fraction of it"""
    enc, mult = rung
    if mult is None: return ["-c:v", "libx264", "-preset", "medium", "-crf", str(CRF)]
    if enc == "libx264": return ["-c:v", "libx264", "-preset", "medium", "-b:v", str(int(max(FLOOR_BPS, mult * src_bps)))]
    return ["-c:v", enc, "-b:v", str(int(max(FLOOR_BPS, mult * src_bps)))]


def cut_cmd(src, tmp, t_in, t_out, enc):
    return ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{t_in:.3f}", "-to", f"{t_out:.3f}", "-i", src,
            "-map", "0:v:0", "-map", "0:a:0?", *enc, "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-movflags", "+faststart", tmp]


def run_ffmpeg(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def sh(cmd): return subprocess.run(cmd, check=True, text=True, capture_output=True)


# ---------------------------------------------------------------- the measure
def _num(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def _packets(path, interval=None):
    """(the file's start_time, its first video stream's packets as ffprobe prints them); only the interval 'a%b' (absolute seconds) when given"""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "format=start_time:packet=pts_time,dts_time,duration_time,size", "-of", "json"]
    if interval: cmd += ["-read_intervals", interval]
    r = subprocess.run(cmd + [path], capture_output=True, text=True)
    try: d = json.loads(r.stdout or "{}")
    except ValueError: d = {}
    return _num((d.get("format") or {}).get("start_time")) or 0.0, d.get("packets") or []


def video_bps(path, t_in=None, t_out=None):
    """The video bitrate of `path` in bits/s, measured from the packets of its first video stream: their sizes added up over the time they cover.
    With t_in and t_out (seconds into the file, the way ffmpeg's -ss and -to read them) only the packets shown inside that span count, which is how a camera
    clip is measured over the span a take was cut from. 0.0 when nothing can be read."""
    lo, hi = float("-inf"), float("inf")
    if t_in is None:
        _, packets = _packets(path)
    else:
        # ffprobe seeks to the key frame before the interval and stops at the first packet past it, so ask a second wider each way and keep only the packets
        # shown inside the span (a B-frame can be shown before the packet that precedes it in the file)
        start, packets = _packets(path, f"{max(0.0, t_in - 1.0):.3f}%{t_out + 1.0:.3f}")
        if abs(start) > 1e-3:   # a file whose clock does not start at 0 (MPEG-TS, MXF): ffprobe reads absolute times, ffmpeg's -ss counts from the start
            start, packets = _packets(path, f"{max(0.0, t_in + start - 1.0):.3f}%{t_out + start + 1.0:.3f}")
        lo, hi = t_in + start - EPS, t_out + start - EPS
    seen = []
    for p in packets:
        t = _num(p.get("pts_time"))
        if t is None: t = _num(p.get("dts_time"))
        size = _num(p.get("size"))
        if t is not None and size is not None and lo <= t < hi: seen.append((t, size, _num(p.get("duration_time"))))
    if not seen: return 0.0
    durs = [d for _, _, d in seen]
    if all(d for d in durs): secs = sum(durs)
    else:   # no frame durations: first to last shown, plus one average frame
        times = [t for t, _, _ in seen]
        secs = (max(times) - min(times)) * len(seen) / (len(seen) - 1) if len(seen) > 1 else 0.0
    return sum(s for _, s, _ in seen) * 8 / secs if secs > 0 else 0.0


# ---------------------------------------------------------------- one take
def encode_take(name, src, dst, t_in, t_out, src_bps, say=lambda s: None):
    """Cut src[t_in:t_out] to dst, climbing the ladder until the take's video bitrate is not under the camera clip's (src_bps, measured over the same
    span). Each rung is cut to dst + '.part.mov' and only a rung that gets there replaces dst: a failed take never leaves a file, and a take that was
    already there is left alone. -> {"rung", "take_bps", "src_bps"}. Raises TakeFailed (one plain line) when no rung gets there."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part.mov"; rungs = ladder(); tried, best = [], 0.0
    try:
        for n, rung in enumerate(rungs, 1):
            if STOP.is_set(): raise Stopped()
            label = rung_name(rung); tail = "; trying " + rung_name(rungs[n]) if n < len(rungs) else ""
            p = run_ffmpeg(cut_cmd(src, tmp, t_in, t_out, enc_args(rung, src_bps)))
            if p.returncode != 0:
                if STOP.wait(0.25): raise Stopped()   # Ctrl-C ends the cut under way as well: that is the stop, not a rung that failed, and nothing new is started
                why = (p.stderr.strip().splitlines() or ["failed"])[-1][:160]
                tried.append(f"{label} did not run ({why})"); say(f"  {name}: {label} did not run ({why}){tail}")
                continue
            got = video_bps(tmp); best = max(best, got)
            if got >= src_bps:
                os.replace(tmp, dst)
                return {"rung": label, "take_bps": got, "src_bps": src_bps}
            tried.append(label); say(f"  {name}: {label} wrote {mbps(got)} Mbit/s, {got / src_bps:.2f}x its camera clip's {mbps(src_bps)}{tail}")
        if best: raise TakeFailed(f"{name}: video {mbps(best)} Mbit/s is under its camera clip's {mbps(src_bps)} Mbit/s after " + ", ".join(tried))
        raise TakeFailed(f"{name}: no encode finished (" + "; ".join(tried) + f"); its camera clip's video is {mbps(src_bps)} Mbit/s")
    finally:
        if os.path.exists(tmp): os.remove(tmp)


def cut_take(c, src, dst, scr, say=lambda s: None):
    """One cut of the run: an existing take that is not under its camera clip is kept, one that is (or that will not play) is cut again, a missing one is cut.
    A new cut also gets its thumbnail, from the camera original. -> {"kept" or "rung", "take_bps", "src_bps"}. Raises TakeFailed."""
    if not os.path.exists(src):
        if os.path.exists(dst): return {"kept": True, "take_bps": video_bps(dst), "src_bps": 0.0, "unchecked": True}   # no camera clip to measure against or to cut from
        raise TakeFailed(f"{c['id']}: its camera clip is not there ({src})")
    src_bps = video_bps(src, c["in"], c["out"])
    if not src_bps: raise TakeFailed(f"{c['id']}: cannot read the video bitrate of its camera clip over {c['in']:.2f} to {c['out']:.2f} s ({src})")
    if os.path.exists(dst):
        got = video_bps(dst)   # 0 for a file that will not play: it is cut again
        if got >= src_bps: return {"kept": True, "take_bps": got, "src_bps": src_bps}
        say(f"  {c['id']}: the take on disk is {mbps(got)} Mbit/s, under its camera clip's {mbps(src_bps)}: cutting it again")
    r = encode_take(c["id"], src, dst, c["in"], c["out"], src_bps, say)
    mid = (c["word_in"] + c["word_out"]) / 2   # thumbnail for START HERE.html, from the camera original
    os.makedirs(f"{scr}/thumbs", exist_ok=True)
    try:
        sh(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{mid:.2f}", "-i", src, "-frames:v", "1", "-vf", f"scale={c.get('thumb_w', 240)}:-2", "-q:v", "5", f"{scr}/thumbs/{c['id']}.jpg"])
    except subprocess.CalledProcessError:   # a cut that played is never thrown away over its thumbnail: the index falls back to the camera clip's own
        say(f"  {c['id']}: no thumbnail could be made, the camera clip's own is used")
    return r


_say_lock = threading.Lock()


def say(msg):
    with _say_lock: print(msg, flush=True)


def run_lanes(n, work, lanes=LANES):
    """work(i) for i in range(n), `lanes` at once. A TakeFailed does not stop the others: its line comes back, in cut order. Anything else stops the
    run (nothing new is started, the cuts under way finish) and is raised."""
    failed = []
    STOP.clear()

    def lane(i):
        if STOP.is_set(): return
        try: work(i)
        except TakeFailed as e: failed.append((i, str(e)))
        except Stopped: pass
    try:
        with ThreadPoolExecutor(max_workers=lanes) as pool:
            try:
                done, _ = wait([pool.submit(lane, i) for i in range(n)], return_when=FIRST_EXCEPTION)
                for f in done: f.result()
            except BaseException:
                STOP.set(); raise
    finally:
        STOP.clear()
    return [m for _, m in sorted(failed)]


# ---------------------------------------------------------------- names
def tc(s): m, s = divmod(int(round(s)), 60); return f"{m}:{s:02d}"


def note_text(s, n=45):
    """what happened in a take, for the end of its file name (a note is not a title: all punctuation goes, and the cut falls on a word)"""
    s = re.sub(r"['\u2019]", "", s or ""); s = re.sub(r"[^\w\s&+-]", " ", s); s = re.sub(r"\s+", " ", s).strip()
    out = " ".join(w if w.isupper() and len(w) <= 3 else w.capitalize() for w in s.split())
    return out[:n].rsplit(" ", 1)[0].strip() if len(out) > n else out


def drop_stale_cuts(root, scr, cuts):
    """A rerun with other notes, handles or segments must not leave the old cuts behind (they would sit in the tree unlisted, and a same-named file with
    different timings would be skipped as "done"). Drop every earlier cut whose name or in/out changed."""
    new = {c["rel"]: (f"{c['in']:.2f}", f"{c['out']:.2f}") for c in cuts}; n = 0
    for r in read_csv(os.path.join(root, "Takes Manifest.csv")):
        if new.get(r["file"]) == (r["in_s"], r["out_s"]): continue
        for p in (os.path.join(root, r["file"]), os.path.splitext(os.path.join(root, r["file"]))[0] + ".txt", f"{scr}/thumbs/{cut_id(r)}.jpg"):
            if os.path.exists(p): os.remove(p); n += 1
        d = os.path.dirname(os.path.join(root, r["file"]))
        if os.path.isdir(d) and not [x for x in os.listdir(d) if not x.startswith(".")]: os.rmdir(d)
    if n: print(f"[takes] removed {n} files from an earlier run (changed or no longer a cut)", flush=True)


def result_line(r):
    if r.get("unchecked"): return "kept (its camera clip is not there to measure against)"
    x = f"{mbps(r['take_bps'])} Mbit/s = {r['take_bps'] / r['src_bps']:.2f}x its camera clip's {mbps(r['src_bps'])}"
    return f"kept, {x}" if r.get("kept") else f"{r['rung']}: {x}"


QUALITIES = ("clean", "usable", "flub")


def load_segments(path, dur):
    """The segments of one clip, checked: every one has a kind (take or blooper), a start and an end in seconds, and a take has a quality. A mistake in a file an
    agent wrote stops the run with one plain line that names the file and the segment, so it can be fixed and the run repeated. end is held inside the clip."""
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as e:
        stop(f"{name} is not valid JSON ({e}): rewrite it (prompts/segment.md), then run this again")
    segs = d.get("segments") if isinstance(d, dict) else None
    if not isinstance(segs, list):
        stop(f'{name} needs a "segments" list (prompts/segment.md)')
    out = []
    for i, s in enumerate(segs, 1):
        where = f"{name}, segment {i}"
        if not isinstance(s, dict):
            stop(f"{where} must be an object")
        kind = s.get("kind")
        if kind not in ("take", "blooper"):
            stop(f'{where}: kind must be "take" or "blooper" (got {kind!r})')
        try:
            start, end = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            stop(f"{where}: start and end must be numbers of seconds")
        start, end = max(0.0, start), min(dur, end)
        if not end > start:
            stop(f"{where}: end must be after start and inside the clip, which is {dur:.2f} s long")
        quality = str(s.get("quality") or "").strip().lower()
        if kind == "take" and quality not in QUALITIES:
            stop(f'{where}: a take needs quality "clean", "usable" or "flub" (got {s.get("quality")!r})')
        out.append({"start": start, "end": end, "kind": kind, "line": str(s.get("line") or "").strip(), "quality": quality if kind == "take" else "",
                    "note": str(s.get("note") or "").strip()})
    return sorted(out, key=lambda s: s["start"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--handle", type=float, default=None, help="seconds of real footage on each side of a cut (default takes_handle_s in config.json, 5)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    J = load_job(a.job)
    ROOT, SCR = J["root"], J["scratch"]
    HANDLE = a.handle if a.handle is not None else float(common.load_config(require_workspace=False).get("takes_handle_s", 5.0))
    RES = f"{SCR}/results"

    manifest = os.path.join(ROOT, "Footage Manifest.csv")
    if not os.path.exists(manifest):
        stop("Footage Manifest.csv is missing: organize the shoot first (intake.py apply)")
    rows = read_csv(manifest)
    filed = {r["clip"]: os.path.join(ROOT, r["new_path"]) for r in rows}
    if not os.path.exists(f"{SCR}/evidence.json"):
        stop(f"{SCR}/evidence.json is missing: takes need what prep measured")
    with open(f"{SCR}/evidence.json", encoding="utf-8") as fh:
        ev = {e["clip"]: e for e in json.load(fh)}
    qs = J["questions"]
    order = {q["id"]: i for i, q in enumerate(qs)}
    folder = {q["id"]: question_folder(q) for q in qs}   # the same rule organize.py plans the question folders with: one name per question
    bad_home = bad_takes_folder(J)   # "" unless the job puts every flubbed take in one folder of its own

    cuts = []
    for clip in sorted(filed):
        f = f"{RES}/{clip}.segments.json"
        if not os.path.exists(f):
            continue
        dur = float(ev[clip]["duration_s"])
        box_w = prep.BOXES[ev[clip].get("orientation", "vertical")][0]
        for i, s in enumerate(load_segments(f, dur), 1):
            cuts.append({"id": f"{clip}-{i:02d}", "clip": clip, "kind": s["kind"], "line": s["line"], "quality": s["quality"], "note": s["note"],
                         "word_in": s["start"], "word_out": s["end"], "in": max(0.0, s["start"] - HANDLE), "out": min(dur, s["end"] + HANDLE), "thumb_w": box_w})
    if not cuts:
        stop("no segments.json files in the results folder: write one per interview clip first (prompts/segment.md)")
    bad_line = sorted({f"{c['id']} (line '{c['line']}')" for c in cuts if c["kind"] == "take" and c["line"] not in order})
    if bad_line:
        stop("REFUSED: these takes name a line that is not in job.questions, so they would be filed as bloopers: " + ", ".join(bad_line) + ". Fix the segments.json (or job.questions) and run again.")
    uncut = [r["clip"] for r in rows if r["category"] == "interview" and not os.path.exists(f"{RES}/{r['clip']}.segments.json")]
    if uncut:
        print(f"[warn] interview clips with no segments.json stay uncut where they are: {', '.join(uncut)}", flush=True)

    takes = {}
    for c in cuts:
        if c["kind"] == "take" and c["line"] in order:
            takes.setdefault(c["line"], []).append(c)
    for line, lst in takes.items():
        for n, c in enumerate(lst, 1):
            c["take"] = n

    for c in cuts:
        if c["kind"] == "take" and c["line"] in order:
            name = f"{c['id']} - {c['line']} - Take {c['take']} - {c['quality'].capitalize() or 'Take'}" + (f" - {note_text(c['note'])}" if c["note"] else "")
            home = bad_home if bad_home and c["quality"] == "flub" else f"Interview/{folder[c['line']]}"
            c["rel"] = f"{home}/{name}.mov"
        else:
            name = f"{c['id']} - Blooper" + (f" - {c['line']}" if c["line"] else "") + (f" - {note_text(c['note'])}" if c["note"] else "")
            c["rel"] = f"B-Roll/Bloopers/{name}.mov"

    raw_moves = {clip: f"Raw Clips/{os.path.basename(filed[clip])}" for clip in {c["clip"] for c in cuts}}

    print(f"{len(cuts)} cuts from {len(raw_moves)} raw clips · {HANDLE:g}s handles")
    for c in cuts:
        print(f"  {c['rel']}   [{tc(c['in'])} to {tc(c['out'])} of {c['clip']}]")
    print("raw clips ->", "Raw Clips/", f"({len(raw_moves)})")
    print("encode:", " then ".join(rung_name(r) for r in ladder()), f"(a cut under its camera clip's video bitrate goes to the next; {LANES} at a time)")
    if a.dry_run:
        return cuts

    drop_stale_cuts(ROOT, SCR, cuts)

    def work(i):
        c = cuts[i]
        say(f"[cut {i + 1}/{len(cuts)}] {c['rel']}")
        say(f"  {c['id']}: " + result_line(cut_take(c, filed[c["clip"]], os.path.join(ROOT, c["rel"]), SCR, say)))
    failed = run_lanes(len(cuts), work)
    if failed:
        for m in failed:
            print("FAILED " + m, file=sys.stderr)
        stop(f"{len(failed)} of {len(cuts)} takes stayed under their camera clip's bitrate. Stopped before the manifest, the README"
             " and the camera files, which are as they were. Fix the cause and run again: the takes already cut and not under their camera clip are kept.")

    with open(os.path.join(ROOT, "Takes Manifest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "source_clip", "raw_path", "kind", "line", "take", "quality", "in_s", "out_s", "spoken_from_s", "spoken_to_s", "note"])
        for c in cuts:
            w.writerow([c["rel"], c["clip"], raw_moves[c["clip"]], c["kind"], c["line"], c.get("take", ""), c["quality"],
                        f"{c['in']:.2f}", f"{c['out']:.2f}", f"{c['word_in']:.2f}", f"{c['word_out']:.2f}", c["note"]])

    os.makedirs(os.path.join(ROOT, "Raw Clips"), exist_ok=True)
    for clip, rel in raw_moves.items():
        src = filed[clip]
        dst = os.path.join(ROOT, rel)
        if os.path.exists(src) and src != dst:
            shutil.move(src, dst)
        for r in rows:
            if r["clip"] == clip:
                r["new_path"] = rel
    with open(manifest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    # plan.json feeds index.py and qc.py: point the moved raw clips at Raw Clips/ so links and byte checks stay true
    P = read_plan(J)
    for m in P["moves"]:
        if m["clip"] in raw_moves:
            m["rel"] = raw_moves[m["clip"]]
            m["dst"] = os.path.join(ROOT, m["rel"])
    write_plan(J, P)
    for d in {os.path.dirname(filed[c]) for c in raw_moves}:
        if os.path.isdir(d) and not [x for x in os.listdir(d) if not x.startswith(".")]:
            shutil.rmtree(d)
    write_readme(J, P["records"], P["moves"], cuts=read_csv(os.path.join(ROOT, "Takes Manifest.csv")), handle=HANDLE)
    print("[takes] done ->", ROOT, "\n[takes] next: index (lists the cuts), notes (a .txt beside every cut), qc (the gate)")
    return cuts


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:   # Ctrl-C: the cuts under way are deleted by their own cleanup; nothing new is started, and the camera files never moved
        print("STOP: stopped by you. No half-written file is left, and the camera files are where they were.", file=sys.stderr)
        sys.exit(130)
