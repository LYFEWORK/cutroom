"""run_captions.sh end to end on a 3 s clip, with the three things that cost money or need a machine stubbed out by seeding:
the transcript (words.json) and the face track (facetrack.json) are written before the run, so the words and facetrack stages see a valid output and
skip, and `npx remotion render` is replaced by a fake that checks what the real renderer would need (the composition id, the staged source, the font files
the plan names) and encodes the staged source as a bt709 file. Everything else is the real pipeline: intake, conform, look, roomcheck, spec, plan, pacing,
the gate, devices, checks, render.sh's staging and remux, reel_qc and deliver.

Each sandbox is its own CUTROOM_HOME (config.json names a workspace of its own), a fixture Brand/ folder installed in that workspace (the DNA, its fonts and
its styles), a fake renderer folder (CUTROOM_REMOTION_DIR) and a fake npx first on PATH."""
import itertools
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import cap_helpers

REPO = cap_helpers.REPO
CAPTIONS = os.path.join(REPO, "engine", "captions")
RUN = os.path.join(CAPTIONS, "run_captions.sh")
SIDECARS = os.path.join(REPO, "engine", "sidecars.py")
WORDS = ["We", "build", "custom", "shirts", "for", "local", "teams", "in", "Ohio."]
AGE_S = 10

FAKE_NPX = '''#!%(python)s
# fake npx for the tests: npx remotion render index.ts Captions <out> --props=P --public-dir=D ...
# It fails the way the real renderer would when its inputs are wrong: another composition id, no staged source, or a font file the plan names that
# was not staged as a real file in the public dir. What it saw goes to $FAKE_NPX_LOG (the arguments) and $FAKE_NPX_LOG.json (one json line a render).
import json, os, subprocess, sys

args = sys.argv[1:]
with open(os.environ["FAKE_NPX_LOG"], "a") as f:
    f.write(" ".join(args) + "\\n")


def fail(msg):
    print("fake npx: " + msg, file=sys.stderr)
    sys.exit(1)


if args[:2] != ["remotion", "render"] or len(args) < 5:
    fail("expected: remotion render index.ts Captions <out> ..., got: " + " ".join(args))
entry, comp, out = args[2], args[3], args[4]
if comp != "Captions":
    fail("no composition with the id " + comp + " (the renderer registers Captions)")
opts = dict(a[2:].split("=", 1) for a in args[5:] if a.startswith("--") and "=" in a)
pub, props = opts.get("public-dir", ""), opts.get("props", "")
if not os.path.isfile(os.path.join(pub, "src.mp4")):
    fail("no src.mp4 in the public dir " + pub)
plan = json.load(open(props))
if plan.get("src") != "src.mp4":
    fail("the props name the source " + repr(plan.get("src")) + ", not the staged src.mp4")
faces = []
for key in ("font", "wordFont", "displayFont"):
    for face in (plan.get(key) or {}).get("faces") or []:
        faces.append(face["src"])
missing = [s for s in faces if not (os.path.isfile(os.path.join(pub, s)) and not os.path.islink(os.path.join(pub, s)) and os.path.getsize(os.path.join(pub, s)) > 0)]
if missing:
    fail("the font files the plan loads are not staged as real files in the public dir: " + ", ".join(missing))
with open(os.environ["FAKE_NPX_LOG"] + ".json", "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "public_dir": pub, "fonts": faces, "options": opts, "flags": [a for a in args[5:] if "=" not in a]}) + "\\n")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(pub, "src.mp4"), "-vf",
                "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,format=yuv420p",
                "-c:v", "libx264", "-crf", "14", "-preset", "ultrafast", "-an", out], check=True)
'''


def seed_take(take, words=WORDS, seconds=4, start=0.3, step=0.3, at=None):
    """words.json and facetrack.json for a take folder, so the words and facetrack stages find a valid output and skip. `at` (a list of start times) places
    each word; otherwise they run at `step` from `start`. The face is one steady box, sampled for every second of the clip."""
    os.makedirs(take, exist_ok=True)
    times = at or [round(start + i * step, 3) for i in range(len(words))]
    rows = [{"text": w, "start": t, "end": round(t + 0.25, 3), "filler": False, "double": False} for w, t in zip(words, times)]
    with open(os.path.join(take, "words.json"), "w") as f:
        json.dump({"source": "test", "transcript": " ".join(words), "words": rows}, f)
    box = {"cx": 540, "cy": 760, "w": 300, "h": 300}
    with open(os.path.join(take, "facetrack.json"), "w") as f:
        json.dump({"W": 1080, "H": 1920, "seconds": seconds, "samples": {str(s): dict(box) for s in range(seconds)}, "median": dict(box)}, f)


def make_clip(path, size="216x384", freq=440, seconds=3, vf=None, source=None):
    """A small 9:16 clip with a sine tone: the test picture, or `source` (an ffmpeg lavfi picture) with the filter chain `vf` over it."""
    src = source or f"testsrc2=size={size}:rate=30:duration={seconds}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
    cmd += (["-vf", vf] if vf else []) + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path]
    subprocess.run(cmd, check=True)


def age(path, seconds=AGE_S):
    """bash 3.2's -nt compares whole seconds. Push every file under `path` back by the same amount, so an edit made after this is strictly newer than
    everything built before it, and the files keep their order among themselves."""
    for folder, _dirs, files in os.walk(path):
        for name in files:
            p = os.path.join(folder, name)
            st = os.lstat(p)
            os.utime(p, (st.st_atime - seconds, st.st_mtime - seconds), follow_symlinks=False)


def read_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def read(path):
    with open(path) as f:
        return f.read()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class Env:
    """One sandbox: its own CUTROOM_HOME and workspace, a fixture Brand/ folder in it, a clip, a fake renderer and a fake npx, and a way to run
    run_captions.sh in it."""

    def __init__(self, root, brand="varsans", edit=None, clip=True):
        self.root = str(root)
        self.home = os.path.join(self.root, "home")
        self.ws = os.path.join(self.root, "ws")
        for d in (self.home, self.ws):
            os.makedirs(d)
        # the word engine is not installed in a sandbox (the transcripts are seeded): a stage that really needed it stops with one plain line
        write_json(os.path.join(self.home, "config.json"), {"workspace": self.ws, "whisper_model": os.path.join(self.home, "no-model.bin"),
                                                            "venv_python": os.path.join(self.home, "no-venv", "bin", "python"), "whisper_language": "en"})
        self.brand = cap_helpers.install_brand(self.ws, brand, edit=edit)
        self.renderer = os.path.join(self.root, "renderer")
        remotion_bin = os.path.join(self.renderer, "node_modules", ".bin", "remotion")
        os.makedirs(os.path.dirname(remotion_bin))
        write(remotion_bin, "#!/bin/sh\necho 'the fake renderer is reached through the fake npx' >&2\nexit 1\n")
        os.chmod(remotion_bin, 0o755)
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.bin)
        npx = os.path.join(self.bin, "npx")
        write(npx, FAKE_NPX % {"python": sys.executable})
        os.chmod(npx, 0o755)
        self.npx_log = os.path.join(self.root, "npx.log")
        write(self.npx_log, "")
        self.tmp = os.path.join(self.root, "tmp")
        os.makedirs(self.tmp)
        self.clip = os.path.join(self.root, "clip.mp4")
        if clip:
            make_clip(self.clip)
        self.finals = os.path.join(self.root, "finals")
        self.take = os.path.join(self.root, "work", "my-take")

    # -- running --------------------------------------------------------------------------------------------------------------------------

    def env(self, **extra):
        env = dict(os.environ)
        for k in ("WHISPER_MODEL", "CUTROOM_VENV_PYTHON", "CUTROOM_TRANSCRIBE", "CAPTION_STYLES_DIR", "CAPTION_SCHEMA", "DEEPGRAM_API_KEY", "FORCE", "MASTER_AUDIO"):
            env.pop(k, None)
        env.update(CUTROOM_HOME=self.home, CUTROOM_REMOTION_DIR=self.renderer, PATH=self.bin + os.pathsep + env["PATH"], FAKE_NPX_LOG=self.npx_log,
                   TMPDIR=self.tmp, PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return env

    def run(self, video=None, take=None, dest="default", style=None, force=False, seed=True, env_extra=None, shell_args=()):
        take = take or self.take
        if seed and not os.path.exists(os.path.join(take, "words.json")):
            seed_take(take)
        extra = dict(env_extra or {})
        if force:
            extra["FORCE"] = "1"
        args = ["bash", *shell_args, RUN, video or self.clip, take]
        if style:
            args += ["--style", style]
        if dest == "default":
            args += ["--dest", self.finals]
        elif dest:
            args += ["--dest", dest]
        return subprocess.run(args, capture_output=True, text=True, env=self.env(**extra), timeout=600)

    def approve(self, take=None):
        gate = os.path.join(take or self.take, "GATE.md")
        text = read(gate).replace("approved: no", "approved: yes", 1)
        write(gate, text)

    def edit_dna(self, fn):
        """change the sandbox's dna.json (the user's own look) the way a person editing the file would"""
        path = os.path.join(self.brand, "dna.json")
        doc = read_json(path)
        fn(doc)
        write_json(path, doc)

    def renders(self):
        return len([line for line in read(self.npx_log).splitlines() if line.strip()])

    def seen(self):
        """what the fake renderer saw, one dict a render"""
        path = self.npx_log + ".json"
        return [json.loads(line) for line in read(path).splitlines() if line.strip()] if os.path.exists(path) else []

    def status(self, take=None):
        return read_json(os.path.join(take or self.take, "status.json"))

    def t(self, name):
        return os.path.join(self.take, name)

    def build_dir(self, stem="My Take", dest=None):
        return os.path.join(dest or self.finals, "Build", stem)

    def settle(self):
        """age everything built so far: the next edit is then strictly newer than all of it, with no sleeping"""
        age(self.root)

    # -- a copy to go back to ------------------------------------------------------------------------------------------------------------------------

    def freeze(self, store):
        """keep a copy of the whole sandbox, aged, to restore before each test that changes it"""
        self.settle()
        shutil.copytree(self.root, store, symlinks=True)

    def restore(self, store):
        shutil.rmtree(self.root)
        shutil.copytree(store, self.root, symlinks=True)
        return self


def finish(env, take=None, **run_args):
    """both passes of a take: build to the gate, approve, run to delivery. Returns the second run."""
    first = env.run(take=take, **run_args)
    assert first.returncode == 2 and "set approved: yes" in first.stdout, first.stdout + first.stderr
    env.approve(take)
    second = env.run(take=take, **run_args)
    assert second.returncode == 0, second.stdout + second.stderr
    return second


@pytest.fixture(scope="module")
def delivered(tmp_path_factory):
    """A take run all the way through once (first pass, approval, second pass) in the fixture look that names the soft build as its default style. Tests
    get it back through env2, which restores a copy of it first."""
    env = Env(tmp_path_factory.mktemp("cap-run"))
    finish(env)
    store = str(tmp_path_factory.mktemp("cap-run-store") / "copy")
    env.freeze(store)
    return env, store


@pytest.fixture(scope="module")
def own_look(tmp_path_factory):
    """A take delivered the same way for a look that names no default style (captions.default_style null): the take is made in the look exactly as the
    DNA makes it."""
    def no_default(d):
        d["captions"]["default_style"] = None
    env = Env(tmp_path_factory.mktemp("cap-own"), edit=no_default)
    finish(env)
    store = str(tmp_path_factory.mktemp("cap-own-store") / "copy")
    env.freeze(store)
    return env, store


@pytest.fixture
def env2(delivered):
    """The delivered sandbox, restored: a test may change it freely."""
    env, store = delivered
    return env.restore(store)


@pytest.fixture
def own2(own_look):
    env, store = own_look
    return env.restore(store)
