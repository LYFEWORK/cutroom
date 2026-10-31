"""Fixtures for the review tests: a throwaway workspace and tiny synthetic clips. Nothing here touches the real ~/.cutroom."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[2] / "engine"
sys.path.insert(0, str(ENGINE))
os.environ.setdefault("CUTROOM_HOME", str(Path(os.environ.get("TMPDIR", "/tmp")) / "cutroom-review-tests-home"))

import common  # noqa: E402  (CUTROOM_HOME must be set before this import)
from review import store  # noqa: E402

SHOOT = "2026-01-01 Test Shoot"
HAVE_X265 = "libx265" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout


def ffmpeg(*args) -> None:
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def make_clip(path: Path, size="216x384", seconds=4, sound=True, hevc=False, gop=24, freq=440, extra=()) -> Path:
    """A tiny test clip with a keyframe every `gop` frames (24 fps, so 1 s by default) and an optional sine tone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration={seconds}"]
    if sound:
        args += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
    if hevc:
        args += ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", f"keyint={gop}:min-keyint={gop}:scenecut=0:log-level=error", "-tag:v", "hvc1"]
    else:
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"]
    args += ["-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-shortest"] if sound else ["-an"]
    ffmpeg(*args, *extra, path)
    return path


@pytest.fixture(scope="session")
def clips(tmp_path_factory):
    """Source clips, made once. Tests copy from here."""
    d = tmp_path_factory.mktemp("clips")
    out = {
        "v": make_clip(d / "desk_v.mp4", "216x384", 6, freq=440),
        "h": make_clip(d / "desk_h.mp4", "384x216", 5, freq=330),
        "silent": make_clip(d / "silent.mp4", "216x384", 4, sound=False),
        "take": make_clip(d / "take1.mp4", "216x384", 6, freq=550),
    }
    if HAVE_X265:
        out["hevc"] = make_clip(d / "hevc.mov", "216x384", 6, hevc=True, freq=660)
    return out


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """An empty workspace with its own config. common.CONF points inside tmp_path, so tests never read or write ~/.cutroom."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(common, "CONF_DIR", home)
    monkeypatch.setattr(common, "CONF", home / "config.json")
    monkeypatch.setenv("CUTROOM_HOME", str(home))
    root = common.make_workspace(tmp_path / "ws")
    common.save_config({**common.DEFAULTS, "workspace": str(root)})
    return root


def make_shoot(ws: Path, clips: dict) -> Path:
    """A small shoot filed the way the footage organizer files it."""
    base = ws / "Footage" / SHOOT
    for sub, name, key in (("B-Roll/Desk", "desk_v.mp4", "v"), ("B-Roll/Desk", "desk_h.mp4", "h"), ("B-Roll/Desk", "silent.mp4", "silent"),
                           ("Interview/L01 Line", "take1.mp4", "take")):
        (base / sub).mkdir(parents=True, exist_ok=True)
        shutil.copy(clips[key], base / sub / name)
    return base


@pytest.fixture()
def shoot(ws, clips):
    return make_shoot(ws, clips)


@pytest.fixture()
def fake_trash(monkeypatch, tmp_path):
    """common.trash replaced by a mover into tmp_path/Trash that records what it was given. Never touches the real Trash."""
    bin_ = tmp_path / "Trash"
    bin_.mkdir()
    calls = []

    def fake(path):
        path = Path(path)
        if not path.exists():
            return "missing"
        dest = bin_ / path.name
        n = 1
        while dest.exists():
            dest = bin_ / f"{path.stem} ({n}){path.suffix}"
            n += 1
        shutil.move(str(path), str(dest))
        calls.append(str(path))
        return "trash"

    monkeypatch.setattr(common, "trash", fake)
    fake.calls = calls
    fake.dir = bin_
    return fake


def ingest_quiet(**kw):
    from review import ingest
    return ingest.refresh(say=lambda *_: None, **kw)


def decide(cid: str, s: str, r=None, at=None) -> None:
    """Save a decision straight into decisions.json, the way the server would."""
    store.merge_decisions({cid: {"s": s, "r": r, "at": at or store.now_ms()}}, source="test")


def clip_by_name(name: str) -> dict:
    return next(c for c in store.load_inventory()["clips"] if c["name"] == name)
