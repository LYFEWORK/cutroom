#!/usr/bin/env python3
"""takectl.py <command> ... : the small JSON and file checks run_captions.sh needs between stages.

run_captions.sh never builds Python source out of paths. Every read of a take's files goes through here, with the
paths passed as arguments.

  status <take-dir> <stage> <true|false> <message>   write <take-dir>/status.json
  valid <json>                                       exit 0 when the file is non-empty valid JSON, else 1
  get <json> <dotted.key> [default]                  print one value (strings as they are, the rest as JSON)
  nwords <words.json>                                number of transcribed words
  cutframes <take-dir>                               "<cut seconds> <frames>" for the planner (never past the mezzanine)
  same-source <video> <take-dir>                     exit 0 when the take was built from this video's bytes; exit 1 when not, printing
                                                     "edited" (the file the take was built from is now different) or "other"
  needs-matte <plan.json>                            prints 1 or 0
  matte-sync <plan.json> <windows.json> <matte-dir>  write the windows; exit 0 when matte-dir holds every frame, else
                                                     remove the partial dir and exit 1
  failed-gates <reel-qc.json>                        the names of the gates that failed, comma separated
  plan-sha <plan.json>                               a hash of the plan's content
  delivery-name <dest> <take-dir> <take-name>        the name this take delivers under in <dest>: "<Title>", the one it already delivered
                                                     under when it did, else the first free " Take 2", " Take 3" ...
  delivery-free <dest> <name> <take-dir>             STOP when another take's folder already owns <dest>'s <name> (file or Build folder)
  delivery-record <build-dir> <name> <take-dir>      write <build-dir>/delivery.json: which take folder this delivery belongs to and the
                                                     caption style it was filed with
  render-key <take-dir> <master-audio>               a hash of everything the render depends on: the plan, the audio setting and the
                                                     look's font files
  finished-dir <take-name>                           <workspace>/Finished/<Title>: where a take delivers when no folder is given
  stage-fonts <take-dir> <stage-dir>                 copy the font files the plan loads into the render's staging folder, at the paths the plan names
  style <take-dir>                                   the caption style this take asks for: the GATE.md `style:` line, else the one in
                                                     <take-dir>/caption-style.txt, else the look's default_style, else nothing (prints
                                                     nothing). STOP when it is not a style in Brand/styles or templates/styles
  style-set <take-dir> <id>                          check that <id> is a style (Brand/styles or templates/styles), then write it as the take's
                                                     pick in <take-dir>/caption-style.txt (run_captions.sh --style does this)
  style-record <take-dir>                            the style id the take's delivery records: the style look.json was made with, else nothing
  loudnorm-filter <audio.wav>                        the two-pass loudnorm filter string for this audio (measured, linear)
  approved <gate.json>                               exit 0 when the gate is approved
  guard-dest <folder>                                STOP when the folder is inside the Cutroom install
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from keys import cli, EngineStop  # noqa: E402

REPO = os.path.dirname(os.path.dirname(HERE))              # the Cutroom install: engine/captions/../..
FPS = 30
TAIL = 0.3          # seconds the last caption holds after the last spoken word


def _load(path):
    with open(path) as f:
        return json.load(f)


def _valid(path):
    try:
        if os.path.getsize(path) == 0:
            return False
        _load(path)
        return True
    except (OSError, ValueError):
        return False


def _dig(obj, dotted):
    """obj[a][b][0]... for "a.b.0"; None when any step is missing."""
    for k in dotted.split("."):
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        elif isinstance(obj, list) and k.isdigit() and int(k) < len(obj):
            obj = obj[int(k)]
        else:
            return None
    return obj


def mez_frames(take):
    """frame count of the mezzanine the render reads (mez1080 when the take is 4K: same frame count)."""
    for name in ("mez1080.mp4", "mez.mp4"):
        p = os.path.join(take, name)
        if os.path.isfile(p):
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames:format=duration",
                                "-of", "json", p], capture_output=True, text=True)
            try:
                d = json.loads(r.stdout or "{}")
                n = (d.get("streams") or [{}])[0].get("nb_frames")
                if n and str(n).isdigit() and int(n) > 0:
                    return int(n)
                return int(round(float((d.get("format") or {}).get("duration")) * FPS))
            except (TypeError, ValueError):
                break
    raise EngineStop(f"no mezzanine to count frames in {take}; run the conform stage first")


def cut_and_frames(take):
    """cut = last spoken word + TAIL, clamped to the mezzanine; frames = the mezzanine's frame count, so the composition is
    never longer than the picture it sits on."""
    words = _load(os.path.join(take, "words.json")).get("words") or []
    if not words:
        raise EngineStop("no speech detected: the transcript came back empty")
    frames = mez_frames(take)
    cut = min(round(words[-1]["end"] + TAIL, 3), round(frames / FPS, 3))
    return cut, frames


def same_source(video, take):
    """"same" when the take's intake.json was built from this video's bytes (same size and first-64MB hash, whatever the path:
    a symlinked or re-copied spelling of one file is the same file). Otherwise "edited" when the path the take was built from
    now holds different bytes (a cut re-exported over the old file), else "other"."""
    intake = _load(os.path.join(take, "intake.json"))
    prev = intake.get("source_original") or ""
    if os.path.isfile(video) and os.path.getsize(video) == intake.get("size_bytes"):
        h = hashlib.sha256()
        with open(video, "rb") as f:
            h.update(f.read(64 * 1024 * 1024))
        if h.hexdigest() == intake.get("sha256_64mb"):
            return "same"
    if prev and os.path.realpath(prev) == os.path.realpath(video):
        return "edited"
    return "other"


def matte_frames(windows):
    s = set()
    for f0, f1 in windows:
        s.update(range(int(f0), int(f1) + 1))
    return sorted(s)


def plan_sha(path):
    with open(path) as f:
        d = json.load(f)
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


def title_of(name):
    """Title Case for a take name; a word that already carries a capital or a digit (MMC, Q3, iPhone) is left alone."""
    out = []
    for w in re.sub(r"[-_]+", " ", name).split():
        out.append(w if (re.search(r"[A-Z0-9]", w)) else w.capitalize())
    return " ".join(out)


def _recorded_take_dir(build_dir):
    """The take folder (real path) that <build_dir>/delivery.json says it was delivered from, or None when there is none."""
    try:
        d = _load(os.path.join(build_dir, "delivery.json"))
    except (OSError, ValueError):
        return None
    return os.path.realpath(d["take_dir"]) if isinstance(d, dict) and d.get("take_dir") else None


def _taken_names(dest):
    """Every delivered name in <dest>, case-folded (the disk is case-insensitive): the Build folders and the videos beside them."""
    out = set()
    for folder, ext in ((os.path.join(dest, "Build"), None), (dest, ".mp4")):
        if os.path.isdir(folder):
            for entry in os.listdir(folder):
                if ext is None:
                    out.add(entry.casefold())
                elif entry.lower().endswith(ext):
                    out.add(entry[:-len(ext)].casefold())
    return out


def delivery_name(dest, take_dir, name):
    """What this take delivers under in <dest>. A take is its FOLDER, never its name: two takes in different workroots can share a
    folder name, and would overwrite each other's video and Build folder. The name is the take's Title; when another take owns that name too,
    " Take 2", " Take 3" ... until one is free. A take that has delivered before gets its own name back, so delivering again replaces its own
    files and nothing else (Build/<name>/delivery.json says whose a delivery is)."""
    mine = os.path.realpath(take_dir)
    build = os.path.join(dest, "Build")
    if os.path.isdir(build):
        for entry in sorted(os.listdir(build)):
            if _recorded_take_dir(os.path.join(build, entry)) == mine:
                return entry
    taken = _taken_names(dest)
    base = title_of(name)
    stem, n = base, 1
    while stem.casefold() in taken:
        n += 1
        stem = f"{base} Take {n}"
    return stem


def check_delivery_free(dest, stem, take_dir):
    """deliver.sh's guard: <stem> must be free in <dest> or already this take's (a delivery.json naming this take's folder). Anything else, a
    Build folder of another take or of unknown origin, or a reel nobody recorded, is somebody's file and is never written over."""
    build = os.path.join(dest, "Build", stem)
    if os.path.isdir(build) and _recorded_take_dir(build) == os.path.realpath(take_dir):
        return
    if stem.casefold() in _taken_names(dest):
        other = _recorded_take_dir(build) if os.path.isdir(build) else None
        raise EngineStop(f"the delivered name '{stem}' is already used in {dest}" + (f" by the take in {other}" if other else "")
                         + "; deliver under another name (run_captions.sh picks a free one by itself)")


def style_in_file(take):
    """the style id written on the first line of <take>/caption-style.txt (blank lines and # comments are skipped), or None when there is no such line"""
    try:
        with open(os.path.join(take, "caption-style.txt")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError:
        pass
    return None


def chosen_style(take):
    """The caption style this take asks for, checked: GATE.md's active `style:` line (the human at the gate has the last word), else <take>/caption-style.txt (a
    pick made before the first run, or run_captions.sh --style), else the look's own default_style; with none, None (your look, untouched). It must be a style
    in Brand/styles or templates/styles; anything else is one plain STOP, before any time is spent."""
    import capgate
    import lookprofile
    import styles
    source, sid = "GATE.md", capgate.style_of(take)
    if not sid:
        source, sid = "caption-style.txt", style_in_file(take)
    profile = lookprofile.load()
    if not sid:
        source, sid = "your DNA's captions.default_style", profile.get("default_style")
    if not sid:
        return None
    try:
        styles.usable(styles.load(sid), profile.get("id"))
    except styles.StyleError as e:
        raise EngineStop(f"style: {e} (asked for in {source})")
    return sid


def style_set(take, sid):
    """Write <take>/caption-style.txt with the style id, after checking it is a style the library has (one plain STOP otherwise, and nothing written)."""
    import lookprofile
    lookprofile.load(sid)
    with open(os.path.join(take, "caption-style.txt"), "w") as f:
        f.write(sid + "\n")


def style_record(take):
    """What a delivery records as the caption style: the one look.json was made with, else ''."""
    try:
        look = _load(os.path.join(take, "look.json"))
    except (OSError, ValueError):
        return ""
    return look.get("caption_style") or ""


def record_delivery(build_dir, stem, name, take_dir):
    """Write <build_dir>/delivery.json: the delivered name, the take's name and the take's folder (real path), the identity delivering again uses, and the
    caption style (null for a take made with none)."""
    with open(os.path.join(build_dir, "delivery.json"), "w") as f:
        json.dump({"name": stem, "take": name, "take_dir": os.path.realpath(take_dir), "caption_style": style_record(take_dir) or None,
                   "delivered": datetime.datetime.now().isoformat(timespec="seconds")}, f, indent=1)


def _font_files(look):
    """Every font file the look's plan can load, as absolute paths (the display face, the running face, and any face a style swapped in)."""
    out = list((look.get("font_sources") or {}).values())
    out += list((look.get("font_abs") or {}).values())
    out += list(((look.get("word_font") or {}).get("abs") or {}).values())
    return sorted(set(out))


def render_key(take, master_audio):
    """A hash of what the render depends on beyond the plan: the audio setting and the bytes of the look's font files, so a changed look (loudnorm
    switched on, a font file replaced under the same name) re-renders when the plan did not change."""
    h = hashlib.sha256()
    h.update(plan_sha(os.path.join(take, "plan.json")).encode())
    h.update(str(master_audio).encode())
    for path in _font_files(_load(os.path.join(take, "look.json"))):
        try:
            with open(path, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:16]


def stage_fonts(take, stage):
    """Copy the font files the plan loads into the render's staging folder at the paths the plan names (real copies: Remotion's bundler does not follow a
    symlinked file). Every src in the plan's font, wordFont and displayFont faces must be a file the look knows."""
    stage_fonts_for(_load(os.path.join(take, "look.json")), _load(os.path.join(take, "plan.json")), stage)


def stage_fonts_for(look, plan, stage):
    """stage_fonts for a look and a plan already in hand (preview_style.py has no take folder)."""
    sources = dict(look.get("font_sources") or {})
    by_name = {os.path.basename(v): v for v in sources.values()}
    srcs = []
    for key in ("font", "wordFont", "displayFont"):
        srcs += [f.get("src") for f in ((plan.get(key) or {}).get("faces") or [])]
    for src in sorted({x for x in srcs if x}):
        if os.path.isabs(src) or ".." in src.split("/"):
            raise EngineStop(f"the plan names the font {src}, which is not a path inside the render's folder")
        found = sources.get(src) or by_name.get(os.path.basename(src))
        if not found or not os.path.isfile(found):
            raise EngineStop(f"the plan loads the font {src}, but the look has no file on disk for it; run lookprofile.py again")
        dest = os.path.join(stage, src)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(found, dest)


def finished_dir(name):
    """<workspace>/Finished/<Title>: where a take delivers when run_captions.sh is given no --dest."""
    engine = os.path.dirname(HERE)
    if engine not in sys.path:
        sys.path.insert(0, engine)
    import common
    return os.path.join(str(common.ws("Finished")), title_of(name))


LOUD_I, LOUD_TP, LOUD_LRA = -16, -2, 8      # QC's true-peak limit is -1.5: half a dB of room for the AAC encode


def loudnorm_filter(audio):
    """Two-pass loudnorm: measure the clip, then apply the measured values with linear=true. A single pass runs in dynamic mode
    and on a short clip lands several LU off target (-17.9 LUFS against -16 on a 7 s take)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", audio, "-vn", "-af",
                        f"loudnorm=I={LOUD_I}:TP={LOUD_TP}:LRA={LOUD_LRA}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    text = r.stderr or ""
    i = text.rfind("{")
    try:
        m = json.loads(text[i:text.rfind("}") + 1])
        vals = [float(m[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")]
    except (ValueError, KeyError):
        raise EngineStop("could not measure the audio's loudness for loudnorm")
    if not all(abs(v) < 1e6 for v in vals) or vals[0] < -70:    # silence measures as -inf / -70
        return f"loudnorm=I={LOUD_I}:TP={LOUD_TP}:LRA={LOUD_LRA}"
    return (f"loudnorm=I={LOUD_I}:TP={LOUD_TP}:LRA={LOUD_LRA}:measured_I={vals[0]}:measured_TP={vals[1]}:measured_LRA={vals[2]}"
            f":measured_thresh={vals[3]}:offset={vals[4]}:linear=true")


def _main():
    a = sys.argv[1:]
    cmd = a[0]
    if cmd == "status":
        take, stage, ok, message = a[1:5]
        with open(os.path.join(take, "status.json"), "w") as f:
            json.dump({"stage": stage, "ok": ok == "true", "message": message,
                       "updated": datetime.datetime.now().isoformat(timespec="seconds")}, f, indent=1)
    elif cmd == "valid":
        sys.exit(0 if _valid(a[1]) else 1)
    elif cmd == "get":
        v = _dig(_load(a[1]), a[2])
        if v is None:
            if len(a) > 3:
                print(a[3])
                return
            raise EngineStop(f"{os.path.basename(a[1])} has no \"{a[2]}\"")
        print(v if isinstance(v, str) else json.dumps(v))
    elif cmd == "nwords":
        print(len(_load(a[1]).get("words") or []))
    elif cmd == "cutframes":
        cut, frames = cut_and_frames(a[1])
        print(cut, frames)
    elif cmd == "same-source":
        verdict = same_source(a[1], a[2])
        if verdict != "same":
            print(verdict)
            sys.exit(1)
    elif cmd == "needs-matte":
        print("1" if ((_load(a[1]).get("matte") or {}).get("windows")) else "0")
    elif cmd == "matte-sync":
        plan, wpath, mdir = a[1:4]
        windows = (_load(plan).get("matte") or {}).get("windows") or []
        old = _load(wpath) if _valid(wpath) else None
        with open(wpath, "w") as f:
            json.dump(windows, f)
        want = [f"f{n:05d}.webp" for n in matte_frames(windows)]
        have = set(os.listdir(mdir)) if os.path.isdir(mdir) else set()
        if old == windows and want and all(w in have for w in want):
            sys.exit(0)
        if os.path.isdir(mdir):
            import shutil
            shutil.rmtree(mdir)
        sys.exit(1)
    elif cmd == "failed-gates":
        card = _load(a[1])
        print(", ".join(k for k, g in card["gates"].items() if g.get("pass") is False))
    elif cmd == "plan-sha":
        print(plan_sha(a[1]))
    elif cmd == "delivery-name":
        print(delivery_name(a[1], a[2], a[3]))
    elif cmd == "delivery-free":
        check_delivery_free(a[1], a[2], a[3])
    elif cmd == "delivery-record":
        record_delivery(a[1], a[2], os.path.basename(os.path.normpath(a[3])), a[3])
    elif cmd == "render-key":
        print(render_key(a[1], a[2]))
    elif cmd == "finished-dir":
        print(finished_dir(a[1]))
    elif cmd == "stage-fonts":
        stage_fonts(a[1], a[2])
    elif cmd == "style":
        print(chosen_style(a[1]) or "")
    elif cmd == "style-set":
        style_set(a[1], a[2])
    elif cmd == "style-record":
        print(style_record(a[1]))
    elif cmd == "loudnorm-filter":
        print(loudnorm_filter(a[1]))
    elif cmd == "approved":
        sys.exit(0 if (_valid(a[1]) and _load(a[1]).get("approved") is True) else 1)
    elif cmd == "guard-dest":
        dest = os.path.realpath(os.path.expanduser(a[1]))
        repo = os.path.realpath(REPO)
        if dest == repo or dest.startswith(repo + os.sep):
            raise EngineStop(f"refusing to write finished videos inside the Cutroom install ({dest}); give a folder outside {repo} with --dest")
    else:
        raise ValueError(f"unknown command {cmd!r}")


if __name__ == "__main__":
    cli(_main, "takectl.py status|valid|get|nwords|cutframes|same-source|needs-matte|matte-sync|failed-gates|plan-sha|delivery-name|delivery-free|delivery-record|render-key|finished-dir|stage-fonts|style|style-set|style-record|loudnorm-filter|approved|guard-dest ...")
