#!/usr/bin/env python3
"""job.py: the job model of the footage module. Every script in this folder reads its job through load_job().

A job is one shoot. It lives in <shoot>/job.json, written by `intake.py start` and changed by `intake.py subjects` or by hand.

  load_job(path)            job.json as a dict: ~ expanded, <ROOT> substituted, defaults filled, mistakes stopped with one plain line
  clean / title / slug      the one naming rule for every folder and file this module makes from a title
  question_folder(q)        Interview/<this>: the folder of one question
  cut_id(row)               the id of a cut from its Takes Manifest.csv row
  bad_takes_folder(job)     the one folder at the shoot root that every flubbed take is cut into ("" when takes stay in their line's folder)
  read_plan / write_plan    plan.json in the scratch folder, and once the shoot is organized a copy in <root>/Edit
  speech_text(text)         the text when it holds real speech, else "" (whisper labels hum, music and room tone)
  write_readme(...)         README.txt of the shoot folder
  resolve_brand(job)        {accent, ink, font_display} for START HERE.html, with neutral defaults
  is_junk(rel)              True for the files a card or a Finder window scatters (.DS_Store, ._name, Thumbs.db, ...); they are never taken in
  is_os_junk(name)          the four of them a tidy-up may delete (.DS_Store, ._name, Thumbs.db, desktop.ini); nothing else is ever deleted from a folder this module did not make

job.json
  name            the shoot's name (default: the shoot folder's name without its date)
  root            the shoot folder (default: the folder job.json sits in; a job.json inside a shoot folder always means that folder, so a moved or copied shoot works)
  src             where the camera files are before they are organized (default <ROOT>/RAW)
  scratch         where the run keeps what it learns (default <ROOT>/.work)
  shoot_date      YYYY-MM-DD
  shoot_type      "interview" (answers in the person's own words) or "ad" (the person reads a written script line by line)
  questions       [{id, title, question, followups, hints}], empty = group interviews by topic (an ad shoot's script lines are questions too)
  subjects        the B-roll folders, written from the contact sheets
  subject_overrides {clip: folder}, shot_list, notes, people, context, camera_label
  brand           {accent, ink, font_display} for START HERE.html, all optional
  bad_takes_folder  "Bad Takes" by default on an ad shoot, "" on an interview shoot
  whisper_model, whisper_language   default to ~/.cutroom/config.json
"""
import csv
import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from common import VIDEO_EXT, atomic_write_json, stop  # noqa: E402,F401  (VIDEO_EXT is re-exported: every module here asks job for it)

SILENT_MAX_DB = -60.0    # max_volume below this = the camera recorded nothing; whisper text on it is hallucination
MARK = "CLIP NOTES · "   # first line of every per-video .txt notes.py writes; reverse and re-runs recognize their own files by it
TOP = {"interview": "Interview", "broll": "B-Roll", "bonus": "Bonus Answers", "unusable": "Unusable", "sidecars": "Camera Sidecars", "offtopic": "Off Topic"}
GENERATED = ("README.txt", "START HERE.html", "Footage Manifest.csv", "Takes Manifest.csv")
SHOOT_TYPES = ("interview", "ad")   # "ad" = the person on camera reads a written script line by line; "interview" = answers in their own words
CATEGORIES = ("interview", "candid_talk", "broll_with_sound", "broll_silent", "off_topic", "unusable")
OWN_FOLDERS = (TOP["interview"], TOP["broll"], TOP["unusable"], TOP["sidecars"], TOP["offtopic"], "Raw Clips", "Edit", "RAW")
DEFAULT_BRAND = {"accent": "#4F8CFF", "ink": "#0B0C10", "font_display": "system-ui"}


# ---------------------------------------------------------------- job
def _expand(p):
    return os.path.abspath(os.path.expanduser(str(p)))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        stop(f"job file not found: {path}")
    except (OSError, ValueError) as e:
        stop(f"{path} is not a readable job.json ({e})")


def load_job(path):
    """Read job.json, expand ~, substitute <ROOT> in every path, fill the defaults, and stop with one plain line on a mistake."""
    path = _expand(path)
    job = _read_json(path)
    if not isinstance(job, dict):
        stop(f"{path} must hold one JSON object")
    here = os.path.dirname(path)
    job["root"] = _expand(job.get("root") or here)
    if job["root"] != here and any(os.path.exists(os.path.join(here, m)) for m in ("RAW", ".work", "Footage Manifest.csv")):
        job["root"] = here   # job.json sits inside a shoot folder: that folder is the shoot, even when the root it names is where the folder used to be (moved, renamed or copied)
    root = job["root"]

    def sub(v):
        if isinstance(v, str):
            return re.sub(r"<ROOT>", lambda m: root, v, flags=re.I)
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        return v
    for k in ("src", "scratch", "whisper_model"):
        if k in job:
            job[k] = sub(job[k])
    job["src"] = _expand(job.get("src") or os.path.join(root, "RAW"))
    job["scratch"] = _expand(job.get("scratch") or os.path.join(root, ".work"))
    if not job.get("name"):
        job["name"] = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", os.path.basename(root)) or "Shoot"
    job["name"] = str(job["name"])
    for k in ("root", "src", "scratch", "name"):
        if re.search(r"<[^>]*>", job[k]):
            stop(f"job.{k} still holds a placeholder: {job[k]} (fill it in)")
    if job.get("shoot_type") not in SHOOT_TYPES:
        stop(f'job.shoot_type must be "interview" or "ad" (got {job.get("shoot_type")!r}). Set it when you start the shoot: "ad" when '
             'the person on camera reads a written script line by line, "interview" otherwise')
    cfg = common.load_config(require_workspace=False)
    job["whisper_model"] = _expand(job.get("whisper_model") or cfg["whisper_model"])
    job["whisper_language"] = str(job.get("whisper_language") or cfg["whisper_language"])
    job.setdefault("questions", [])
    job.setdefault("subjects", [])
    job.setdefault("subject_overrides", {})
    check_questions(job["questions"])
    if not isinstance(job["subjects"], list):
        stop("job.subjects must be a list of folder names")
    if not isinstance(job["subject_overrides"], dict):
        stop("job.subject_overrides must be a {clip: folder} object")
    resolve_brand(job)   # a bad color stops here, before anything is probed, moved or written
    return job


def check_questions(qs):
    """Every question needs a short id and a title; ids are unique and never a word the records already use (BONUS, NONE)."""
    if not isinstance(qs, list):
        stop("job.questions must be a list of {id, title, question} objects")
    seen = set()
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict) or not str(q.get("id", "")).strip() or not str(q.get("title", "")).strip():
            stop(f"job.questions item {i} needs an id and a title (for example {{\"id\": \"Q01\", \"title\": \"Why Our Process Takes Three Days\"}})")
        qid = str(q["id"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", qid):
            stop(f"job.questions item {i}: the id {qid!r} may hold only letters, digits, dot, dash and underscore (no spaces)")
        if qid.upper() in ("BONUS", "NONE"):
            stop(f"job.questions item {i}: {qid!r} is reserved for answers that match no question")
        if qid in seen:
            stop(f"job.questions: the id {qid} is used twice")
        seen.add(qid)
        q["id"], q["title"] = qid, str(q["title"]).strip()


# ---------------------------------------------------------------- names
# One naming rule for every folder and file this module makes from a title. organize.py names the question folders, takes.py cuts into them, and the
# two must never differ on an apostrophe (kept in one, a space in the other), so a line titled "Don't Skip The Hook" gets one folder.
BAD = re.compile(r'[\\/:*?"<>|#%]+')
KEEP_UPPER = {"QC", "AI", "TV", "USA", "LED", "HVAC", "CTA", "DIY", "4K", "HD"}


def clean(s, maxlen=90):
    """a name safe for a path: no / \\ : * ? " < > | # %, no commas, one space between words, nothing at the ends but letters and digits; an apostrophe stays"""
    s = BAD.sub(" ", s if isinstance(s, str) else str(s or "")).strip().replace(",", "")
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s[:maxlen].strip(" .-")


def title(s):
    """Title Case that keeps what is already written right: the words in KEEP_UPPER stay upper, a SHOUTED word becomes Title, a word with its own capitals keeps them"""
    out = []
    for w in s.split(" "):
        if w.upper() in KEEP_UPPER:
            out.append(w.upper())
        elif w.isupper() and len(w) > 3:
            out.append(w.title())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def slug(s, maxlen=60):
    """the one function that turns a title into a name: cleaned, then Title Case"""
    return title(clean(s, maxlen))


def question_folder(q):
    """Interview/<this>: the folder of a question, from its id and title. organize.py plans the answers into it and takes.py cuts the takes into it"""
    return f"{q['id']} {slug(q['title'], 60)}"


def cut_id(row):
    """the id of a cut (<clip>-NN, the front of its file name) from a Takes Manifest.csv row. A clip's name may hold spaces, so the id is read off the front by its
    length (the clip name, a dash and two digits), never by the first word"""
    return os.path.basename(row["file"])[:len(row["source_clip"]) + 3]


def bad_takes_folder(job):
    """The one folder at the shoot root that takes.py cuts every flubbed take into, in place of its line's folder, so the line folders hold only the good takes.
    From job.bad_takes_folder, which defaults to "Bad Takes" on an ad shoot. "" on an interview shoot, or when the job sets it to "", and every take stays in
    its line's folder. A name that is a path, or one of the organizer's own top folders, stops the run."""
    raw = job.get("bad_takes_folder")
    if raw is None:
        raw = "Bad Takes" if job.get("shoot_type") == "ad" else ""
    raw = str(raw).strip()
    if not raw:
        return ""
    name = slug(raw, 60)
    if re.search(r"[\\/]", raw) or raw.startswith(".") or not name or name in OWN_FOLDERS:
        stop(f'job.bad_takes_folder must be one plain folder name at the shoot root that the organizer does not use already (got {raw!r}; "Bad Takes" works)')
    return name


# ---------------------------------------------------------------- junk
# One junk list for the whole module. A pattern ending in "/" names a folder. A path is junk when any of its segments matches any pattern.
JUNK_PATTERNS = (".DS_Store", "._*", "Thumbs.db", "desktop.ini", ".Spotlight-V100/", ".fseventsd/", ".Trashes/", "*.part.*")


OS_JUNK = (".DS_Store", "._*", "Thumbs.db", "desktop.ini")   # what a Mac or a PC scatters; the only files this module deletes from a folder it did not make


def is_os_junk(name):
    """True for a file name the operating system scatters (not a path): the only kind of file a tidy-up here ever deletes"""
    return any(fnmatch.fnmatchcase(str(name), p) for p in OS_JUNK)


def is_junk(rel_path):
    """True when any segment of rel_path (split on "/" and "\\") matches a JUNK_PATTERNS entry, case-sensitively"""
    segs = [s for s in re.split(r"[\\/]", str(rel_path)) if s]
    return any(fnmatch.fnmatchcase(s, p.rstrip("/")) for s in segs for p in JUNK_PATTERNS)


# ---------------------------------------------------------------- plan.json
def _plan_paths(job):
    return os.path.join(job["scratch"], "plan.json"), os.path.join(job["root"], "Edit", "plan.json")


def read_plan(job):
    """plan.json from the scratch folder, else the copy the organizer keeps in <ROOT>/Edit (the scratch folder was cleaned); None when neither exists"""
    for p in _plan_paths(job):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
    return None


def write_plan(job, data):
    """plan.json into the scratch folder, and once the shoot is organized (applied) a copy into <ROOT>/Edit, so qc.py passes after the scratch folder is gone"""
    scratch_p, edit_p = _plan_paths(job)
    atomic_write_json(scratch_p, data)
    if data.get("applied"):
        atomic_write_json(edit_p, data)


# ---------------------------------------------------------------- speech filter
_FILLER = {"thank you", "thanks", "thank you very much", "thanks for watching", "thank you for watching", "you", "bye", "bye bye",
           "okay", "ok", "uh", "um", "mm", "hmm", "so"}


def speech_text(text):
    """Return text when it holds real speech, else "". Whisper writes "Thank you." or "." over hum and room tone, and labels a sound it cannot
    transcribe in brackets, parentheses or stars ("[MUSIC]", "(humming)", "*sad music*"): a label is not speech."""
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*", " ", text or "")
    if re.search(r"castingwords|amara\.org", t, re.I):
        return ""
    alnum = re.sub(r"[^0-9a-z]", "", t.lower())
    if len(alnum) < 3:
        return ""
    norm = re.sub(r"\s+", " ", re.sub(r"[^0-9a-z ]", " ", t.lower())).strip()
    if norm in _FILLER:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- brand
_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def _luminance(c):
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
_FONT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,40}")


def resolve_brand(job):
    """{accent, ink, font_display} for START HERE.html: job.brand over the neutral defaults, and nothing else. A value that is not what it should be stops the run."""
    jb = job.get("brand") or {}
    if not isinstance(jb, dict):
        stop('job.brand must be an object like {"accent": "#4F8CFF", "ink": "#0B0C10", "font_display": "system-ui"}')
    b = dict(DEFAULT_BRAND)
    for k in ("accent", "ink"):
        if k in jb:
            if not _HEX.fullmatch(str(jb[k]).strip()):
                stop(f"job.brand.{k} must be a hex color like {DEFAULT_BRAND[k]} (got {jb[k]!r})")
            if k == "ink" and _luminance(str(jb[k]).strip()) > 0.08:   # lighter than about #4a4a4a and no text color reads on the surfaces derived from it
                stop(f"job.brand.ink must be a dark color, like {DEFAULT_BRAND['ink']}: it is the page's background in dark mode (got {jb[k]!r})")
            b[k] = str(jb[k]).strip()
    if "font_display" in jb:
        if not _FONT.fullmatch(str(jb["font_display"]).strip()):
            stop(f"job.brand.font_display must be the name of an installed font, letters and spaces only (got {jb['font_display']!r})")
        b["font_display"] = str(jb["font_display"]).strip()
    return b


# ---------------------------------------------------------------- README
def write_readme(job, recs, moves, cuts=None, handle=None):
    """README.txt for the shoot folder. cuts = rows of Takes Manifest.csv once takes.py has run (the README then says what was cut)."""
    name = job["name"]
    qs = job["questions"]
    reel_ids = [q["id"] for q in qs]
    n_int = sum(1 for r in recs if r["category"] == "interview")
    n_bonus = sum(1 for r in recs if r["category"] == "interview" and r["reel"] not in reel_ids)
    n_broll = sum(1 for m in moves if m["rel"].startswith(TOP["broll"] + "/"))
    in_broll = {m["clip"] for m in moves if m["rel"].startswith(TOP["broll"] + "/")}
    n_silent = sum(1 for r in recs if r.get("silent") and r["clip"] in in_broll)
    n_candid = sum(1 for r in recs if r["category"] == "candid_talk")
    covered = sorted({r["reel"] for r in recs if r["category"] == "interview" and r["reel"] in reel_ids})
    missed = [k for k in reel_ids if k not in covered]

    def plural(n, w):
        return f"{n} {w}" + ("" if n == 1 else "s")
    cov = (f"QUESTION COVERAGE\n  Answered on camera: {', '.join(covered) or 'none'}\n  Not filmed:         {', '.join(missed) or 'none'}\n\n") if qs else ""
    bonus_line = (f", Bonus Answers/ for the {plural(n_bonus, 'thing')} said that {'was' if n_bonus == 1 else 'were'} not on the question list" if qs
                  else ", one folder per topic")
    name_line = ("Filenames: <clip> - <question id> - <topic> - Take n. Take numbers follow shoot order." if qs
                 else "Filenames: <clip> - <what is said>. One folder per topic.")
    mode = (read_plan(job) or {}).get("mode", "move")
    if cuts:
        takes = [c for c in cuts if c["kind"] == "take"]
        bl = [c for c in cuts if c["kind"] != "take"]
        h = f"{handle:g} s" if handle is not None else "5 s"
        badf = bad_takes_folder(job)
        nbad = sum(1 for c in takes if badf and c["file"].startswith(badf + "/"))
        folders = ", ".join(["Interview/"] + (["B-Roll/Bloopers/"] if bl else []) + ([f"{badf}/"] if nbad else []))
        orig = (f"TAKES: {plural(len(takes), 'take')} and {plural(len(bl), 'blooper')} were cut out of {len({c['source_clip'] for c in cuts})} raw clips.\n"
                f"  Every cut is its own file in {folders}, named <clip>-NN - <line> - Take n - <Clean|Usable|Flub> - <what happened>.mov.\n"
                f"  Cuts are RE-ENCODED: h264 video at the source's native resolution, aimed at 1.3x the camera clip's own video bitrate and never under it (a cut that measures under its camera clip is cut again), PCM audio, {h} of real footage before the first word and after the last. No slate, no overlay.\n"
                f"  The camera originals were NOT cut or re-encoded. They moved to Raw Clips/ and keep their names.\n"
                f'  "Takes Manifest.csv" lists every cut: source clip, in/out times in the source, line, take number, quality, note.\n'
                + (f"  {badf}/ holds the {plural(nbad, 'flubbed take')} (a read that broke down, restarted or stopped), one file each, named for its line. The line folders in Interview/ hold only the good takes.\n" if nbad else ""))
        move_line = ("Camera files were copied from the source folder, which was left as it was; the cuts above are new files." if mode == "copy"
                     else "Camera files were moved (not copied) out of the source folder; the cuts above are new files.")
    else:
        orig = ("NOTHING WAS CUT OR RE-ENCODED. Every file is the original camera file, renamed and "
                + ("copied (the source folder was left as it was)." if mode == "copy" else "moved (not copied) out of the source folder.") + "\n")
        move_line = ""
    head = f"{name.upper()}, SHOOT {job.get('shoot_date', '')}" + (f" ({job['camera_label']})" if job.get("camera_label") else "")
    total_min = sum(float(r["duration_s"]) for r in recs) / 60
    txt = f"""{head}
{plural(len(moves), 'clip')} · {total_min:.1f} min

OPEN "START HERE.html" FIRST: thumbnails, what is said in every interview take, what is on screen in every b-roll clip, search and filters.

FOLDERS (a folder that no clip needed is not made)
  Interview/            {n_int} on-camera {'answer' if n_int == 1 else 'answers'}{bonus_line}.
                        {name_line}
  B-Roll/               {plural(n_broll, 'clip')} grouped by what is on screen.
                        " - No Audio" = the camera recorded nothing on that clip ({n_silent} in B-Roll).
                        " - Candid Talk" = real conversation on the clip that is not an interview answer ({n_candid} {'clip' if n_candid == 1 else 'clips'}). Check it before using the sound.
  Off Topic/            clips that belong to something else (another shoot on the same card, unrelated footage).
  Unusable/             nothing usable (accidental, under 2 seconds, black).
  Camera Sidecars/      camera metadata files (XML, LRF, THM), one per clip where the camera wrote one. Keep this folder with the shoot.
  Edit/                 plan.json, the applied plan. It is how a later session checks the shoot and undoes it.
  .work/                what the run learned about each clip: transcripts, contact sheets, frames, the classification records. Keep it to cut takes or rewrite notes later.

A NOTES FILE SITS NEXT TO EVERY VIDEO once the notes step has run: a .txt with the same name as the clip. What is said, with times, and what is on screen.

{cov}{orig}Every filename starts with the original clip name.
"Footage Manifest.csv" = the full table (original name, new path, category, question, question asked, summary, best quote, on-screen description, flags, confidence).
{move_line + chr(10) if move_line else ''}To put everything back: intake.py reverse <this folder>. It {'files the camera copies back in RAW/ and deletes' if mode == 'copy' else 'moves the camera files back and deletes'} every file this tool generated (index, manifests, notes, README, cuts, Edit/) and the folders it made.

Flags used in the index and manifest: profanity · confidential · cut_off_early · false_start · shaky · out_of_focus · interviewer_audible · scripted_take · freestyle_take · staff_in_frame · phone_in_frame · multiple_topics · off_topic_content.
"""
    with open(os.path.join(job["root"], "README.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt)


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def today():
    return time.strftime("%Y-%m-%d")
