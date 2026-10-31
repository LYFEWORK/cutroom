"""Tests of the footage module (engine/footage). Nothing is checked in but code: the clips are made at test time with ffmpeg, Pillow and macOS `say`.

CUTROOM_HOME is set to a temporary folder BEFORE the engine is imported (common reads it once), so a test never touches the user's own ~/.cutroom. The real whisper
model is used (CUTROOM_TEST_WHISPER_MODEL, else ~/.cutroom/models, else ~/whisper-models); a test that needs speech skips when the model, whisper-cli or `say` is missing.
"""
import atexit
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE = REPO / "engine"
FOOTAGE = ENGINE / "footage"

HOME = Path(tempfile.mkdtemp(prefix="cutroom-test-home-"))
os.environ["CUTROOM_HOME"] = str(HOME)
atexit.register(shutil.rmtree, str(HOME), True)

# The footage folder first on the path, then the engine folder (for common), then this one. Other engine modules have the same flat names (engine/words.py is not
# engine/footage/words.py), so a module of ours that something else imported earlier is dropped and imported again from here.
for _p in (str(HERE), str(ENGINE), str(FOOTAGE)):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
for _name in ("common", "job", "prep", "workflow_args", "merge", "organize", "index", "qc", "words", "takes", "notes", "intake"):
    _m = sys.modules.get(_name)
    _want = ENGINE if _name == "common" else FOOTAGE
    if _m is not None and Path(getattr(_m, "__file__", "") or "").resolve().parent != _want:
        del sys.modules[_name]

import pytest  # noqa: E402

import common  # noqa: E402

assert str(common.CONF_DIR) == str(HOME), "common was imported before CUTROOM_HOME was set"


# ---------------------------------------------------------------- what this Mac has
def _model():
    for p in (os.environ.get("CUTROOM_TEST_WHISPER_MODEL"), "~/.cutroom/models/ggml-large-v3-turbo.bin", "~/whisper-models/ggml-large-v3-turbo.bin"):
        if p and os.path.exists(os.path.expanduser(p)):
            return os.path.expanduser(p)
    return None


MODEL = _model()
HAVE_MEDIA = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAVE_SPEECH = bool(HAVE_MEDIA and shutil.which("say") and shutil.which("whisper-cli") and MODEL)


def need_media():
    if not HAVE_MEDIA:
        pytest.skip("needs ffmpeg and ffprobe")


def need_speech():
    if not HAVE_SPEECH:
        pytest.skip("needs ffmpeg, macOS say, whisper-cli and the whisper model")


# ---------------------------------------------------------------- running the tools
def tool_env(extra=None):
    env = {**os.environ, "CUTROOM_HOME": str(HOME), "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(extra or {})
    return env


def run_tool(script, *args, cwd=None, env=None):
    """run one of the module's scripts the way a user does: a separate process, plain output"""
    return subprocess.run([sys.executable, str(FOOTAGE / script), *[str(a) for a in args]], capture_output=True, text=True, env=tool_env(env), cwd=cwd)


def intake(*args, **kw):
    return run_tool("intake.py", *args, **kw)


def ok(r):
    """the output of a finished process, for an assert message"""
    return f"exit {r.returncode}\n--- stdout\n{r.stdout[-3000:]}\n--- stderr\n{r.stderr[-3000:]}"


# ---------------------------------------------------------------- the workspace
class Workspace:
    def __init__(self, root):
        self.root = Path(root)
        common.make_workspace(self.root)
        self.drop, self.footage = self.root / "Drop", self.root / "Footage"
        write_config(self.root)


def write_config(workspace, **extra):
    cfg = {"workspace": str(workspace), "whisper_model": MODEL or str(HOME / "models" / "missing.bin")}
    cfg.update(extra)
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "config.json").write_text(json.dumps(cfg))


@pytest.fixture
def ws(tmp_path):
    """a fresh workspace (Drop, Footage, ...) in a temporary folder, and the config that points at it"""
    return Workspace(tmp_path / "workspace")


# ---------------------------------------------------------------- synthetic clips
SENTENCES = {
    "a": "Our process takes three days because every batch has to cure overnight.",
    "b": "Welcome to the workshop, where we make candles by hand.",
    "c": "We pour the wax slowly so the jar stays clear.",
}
SIZES = {"vertical": (270, 480), "horizontal": (480, 270), "square": (320, 320)}


def _run(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-800:]}")


def _card(path, size, lines):
    """a title card: a flat color with the clip's name on it, so a contact sheet shows something to read"""
    from PIL import Image, ImageDraw, ImageFont
    w, h = size
    im = Image.new("RGB", size, (28 + 40 * (len(lines[0]) % 4), 60, 96))
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=max(14, w // 12))
    except TypeError:
        font = ImageFont.load_default()
    for i, t in enumerate(lines):
        dr.text((12, 14 + i * (max(14, w // 12) + 8)), t, fill=(240, 240, 240), font=font)
    dr.rectangle([w // 4, h // 2, 3 * w // 4, 3 * h // 4], outline=(240, 200, 80), width=4)
    im.save(path)


def _voice(path, text):
    """text spoken by macOS `say`, as a 16-bit mono 16 kHz wav"""
    aiff = str(path) + ".aiff"
    _run(["say", "-o", aiff, text])
    _run(["ffmpeg", "-y", "-v", "error", "-i", aiff, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", path])
    os.remove(aiff)


def _wav_join(out, parts, gap_s=1.0):
    """the wavs one after another with gap_s of silence between them -> [(start, end) seconds of each part]"""
    spans, frames, rate = [], b"", 16000
    for i, p in enumerate(parts):
        with wave.open(str(p)) as w:
            rate = w.getframerate()
            data = w.readframes(w.getnframes())
        if i:
            frames += struct.pack("<h", 0) * int(gap_s * rate)
        start = len(frames) / 2 / rate
        frames += data
        spans.append((round(start, 2), round(len(frames) / 2 / rate, 2)))
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return spans


def make_clip(path, shape="vertical", audio="speech", wav=None, seconds=3.0, label=""):
    """one tiny clip. audio: "speech" (the wav given), "noise" (ambient, no words), "silent" (an audio stream of digital silence) or "none" (no audio stream at all)"""
    path = Path(path)
    size = SIZES[shape]
    png = str(path) + ".png"
    _card(png, size, [label or path.stem, shape])
    cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", "15", "-i", png]
    if audio == "speech":
        cmd += ["-i", wav, "-shortest"]
    elif audio == "noise":
        cmd += ["-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude=0.05:sample_rate=48000:seed=7:duration={seconds}"]
    elif audio == "silent":
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono"]
    cmd += ["-t", str(seconds if audio != "speech" else 30), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "64k"] if audio != "none" else ["-an"]
    _run(cmd + [path])
    os.remove(png)
    return path


def build_card(dest):
    """the card dump the tests start from: dest/<files>. -> {"files": {name: path}, "attempts": {...}}"""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="cutroom-clips-"))
    try:
        voices = {}
        for k, text in SENTENCES.items():
            voices[k] = tmp / f"voice-{k}.wav"
            _voice(voices[k], text)
        # a take clip: a false start, a pause, then the whole line
        _voice(tmp / "partial.wav", "Our process takes three")
        spans = _wav_join(tmp / "attempts.wav", [tmp / "partial.wav", voices["a"]], gap_s=1.2)
        files = {
            "C0001.MP4": make_clip(dest / "C0001.MP4", "vertical", "speech", voices["a"], label="C0001 workshop talk"),
            "C0002.MP4": make_clip(dest / "C0002.MP4", "vertical", "speech", voices["b"], label="C0002 welcome"),
            "C0003.MP4": make_clip(dest / "C0003.MP4", "horizontal", "noise", seconds=3, label="C0003 ambient"),
            "C0004.MP4": make_clip(dest / "C0004.MP4", "vertical", "silent", seconds=3, label="C0004 silent"),
            "C0005.MP4": make_clip(dest / "C0005.MP4", "horizontal", "none", seconds=3, label="C0005 no audio"),
            "C0006.MOV": make_clip(dest / "C0006.MOV", "square", "speech", voices["c"], label="C0006 candid"),
            "C0007.MP4": make_clip(dest / "C0007.MP4", "vertical", "speech", tmp / "attempts.wav", label="C0007 two attempts"),
        }
        (dest / "C0001M01.XML").write_text("<NonRealTimeMeta/>\n")
        (dest / "C0006.LRF").write_bytes(b"lrf-sidecar-bytes")
        files["C0001M01.XML"], files["C0006.LRF"] = dest / "C0001M01.XML", dest / "C0006.LRF"
        return {"files": files, "attempts": spans}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def card_master(tmp_path_factory):
    """the synthetic card dump, made once per run and never changed: tests copy it"""
    need_speech()
    d = tmp_path_factory.mktemp("card-master") / "card"
    info = build_card(d)
    info["dir"] = d
    return info


@pytest.fixture
def card(card_master, ws):
    """a private copy of the card dump in <workspace>/Drop/Card A, with the question sheet; -> a namespace"""
    drop = ws.drop / "Card A"
    shutil.copytree(card_master["dir"], drop)
    sheet = ws.root / "questions.md"
    sheet.write_text('Q01 Why Our Process Takes Three Days: "Why does your process take three days?" Follow-ups: what happens overnight.\n'
                     'Q02 Welcome To The Workshop: "Introduce yourself and the workshop."\n')
    return type("Card", (), {"dir": drop, "sheet": sheet, "attempts": card_master["attempts"], "names": sorted(os.listdir(drop)), "master": card_master["dir"]})


# ---------------------------------------------------------------- records
RECORD = dict(category="broll_with_sound", reel="NONE", reel_title="", question_asked="", answer_summary="", key_quote="", on_screen="", shot="medium", camera="static",
              subject_folder="Other", subject_other="", people="", name="", flags=[], usable=True, confidence=0.9, notes="")


def write_final(scratch, clip, **kw):
    rec = {**RECORD, "clip": clip, **kw}
    Path(scratch, "results").mkdir(parents=True, exist_ok=True)
    Path(scratch, "results", f"{clip}.final.json").write_text(json.dumps(rec, indent=1))
    return rec


def classify_card(scratch):
    """final records for the card's 7 clips, the way a classifier would write them: two interview answers, a candid chat, ambient b-roll, silent b-roll, b-roll with no audio,
    and the take clip (its two attempts at Q01)"""
    spec = {
        "C0001": dict(category="interview", reel="Q01", reel_title="Why Our Process Takes Three Days", name="Why Our Process Takes Three Days Clean Read", subject_folder="Workshop Bench",
                      question_asked="Why does your process take three days?", answer_summary="Every batch has to cure overnight.", key_quote="every batch has to cure overnight",
                      on_screen="A title card in blue with the name of the clip.", people="one speaker", flags=["scripted_take"]),
        "C0002": dict(category="interview", reel="Q02", reel_title="Welcome To The Workshop", name="Welcome To The Workshop Intro", subject_folder="Workshop Bench",
                      question_asked="Introduce yourself and the workshop. (inferred)", answer_summary="Welcomes the viewer to the workshop.", key_quote="we make candles by hand",
                      on_screen="A title card in blue with the name of the clip.", people="one speaker"),
        "C0003": dict(category="broll_with_sound", name="Ambient Hum Of The Workshop Wide", subject_folder="Wax Station", on_screen="A wide title card, ambient noise only.", people="nobody", shot="wide"),
        "C0004": dict(category="broll_silent", name="Silent Shelf Of Candle Jars", subject_folder="Shelves", on_screen="A title card, no sound at all.", people="nobody"),
        "C0005": dict(category="broll_silent", name="Pouring Wax Into A Jar Wide", subject_folder="Wax Station", on_screen="A wide title card, the file has no audio stream.", people="nobody", shot="wide"),
        "C0006": dict(category="candid_talk", name="Chat About Wax Pouring", subject_folder="Wax Station", answer_summary="She talks about pouring slowly so the jar stays clear.",
                      on_screen="A square title card.", people="one speaker", flags=["interviewer_audible"]),
        "C0007": dict(category="interview", reel="Q01", reel_title="Why Our Process Takes Three Days", name="Why Our Process Takes Three Days Two Attempts", subject_folder="Workshop Bench",
                      question_asked="Why does your process take three days?", answer_summary="A false start, then the full answer.", key_quote="every batch has to cure overnight",
                      on_screen="A title card in blue with the name of the clip.", people="one speaker", flags=["false_start"]),
    }
    for clip, kw in spec.items():
        write_final(scratch, clip, **kw)
    return spec


def sha_tree(root):
    """{relative path: sha256} of every file under root (not .work)"""
    out = {}
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".work"]
        for f in fs:
            p = os.path.join(d, f)
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[os.path.relpath(p, root)] = h.hexdigest()
    return out


# ---------------------------------------------------------------- a shoot that has been started (prep and all), made once
@pytest.fixture(scope="session")
def started_master(tmp_path_factory, card_master):
    """`intake.py start` run once on the card dump with the real whisper model; never changed. -> {"shoot": path, "hashes": {name: sha256 of every camera file}, "attempts": ...}"""
    need_speech()
    base = tmp_path_factory.mktemp("started")
    wsp = Workspace(base / "workspace")
    drop = wsp.drop / "Card A"
    shutil.copytree(card_master["dir"], drop)
    sheet = base / "questions.md"
    sheet.write_text('Q01 Why Our Process Takes Three Days: "Why does your process take three days?" Follow-ups: what happens overnight.\n'
                     'Q02 Welcome To The Workshop: "Introduce yourself and the workshop."\n')
    hashes = sha_tree(drop)
    r = intake("start", drop, "--name", "Candle Workshop", "--date", "2026-10-31", "--type", "interview", "--questions", sheet,
               "--context", "A candle workshop; one speaker answers a question or two.", "--people", "One speaker on camera, a second person asks off camera.")
    assert r.returncode == 0, ok(r)
    shoot = wsp.footage / "2026-10-31 Candle Workshop"
    return {"shoot": shoot, "hashes": hashes, "attempts": card_master["attempts"], "workspace": wsp.root, "start_output": r.stdout}


@pytest.fixture
def started(started_master, ws):
    """a private copy of the started shoot in the test's own workspace: -> the shoot folder (its job.json follows the copy)"""
    dst = ws.footage / started_master["shoot"].name
    shutil.copytree(started_master["shoot"], dst)
    return dst


@pytest.fixture
def classified(started):
    """the started shoot with its B-roll folders set and every clip's final.json written"""
    r = intake("subjects", started, "--set", "Workshop Bench,Wax Station,Shelves,Other")
    assert r.returncode == 0, ok(r)
    classify_card(started / ".work")
    return started


@pytest.fixture
def organized(classified):
    """the classified shoot after `intake.py apply`: -> the shoot folder"""
    r = intake("apply", classified)
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)
    return classified


# ---------------------------------------------------------------- a small shoot with no speech, for the tests that are about files and not about words
def mini_shoot(tmp_path, clips, sidecars=None, questions=None, subjects=None, shoot_type="interview", name="Mini Shoot", job_over=None):
    """<tmp>/<name>/RAW holds the clips ({file name: (shape, audio)}, audio "noise", "silent" or "none") and the sidecars ({file name: bytes}); job.json is written and prep run
    with --skip-whisper. -> the shoot folder"""
    need_media()
    shoot = Path(tmp_path) / name.replace(" ", "-")
    raw = shoot / "RAW"
    raw.mkdir(parents=True)
    for fname, (shape, audio) in clips.items():
        make_clip(raw / fname, shape, audio, seconds=2, label=Path(fname).stem)
    for fname, data in (sidecars or {}).items():
        (raw / fname).write_bytes(data)
    job = {"name": name, "shoot_date": "2026-10-31", "shoot_type": shoot_type, "questions": questions or [], "subjects": subjects or ["Other"]}
    job.update(job_over or {})
    (shoot / "job.json").write_text(json.dumps(job))
    r = run_tool("prep.py", "--job", shoot / "job.json", "--skip-whisper")
    assert r.returncode == 0, ok(r)
    return shoot
