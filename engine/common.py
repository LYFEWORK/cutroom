#!/usr/bin/env python3
"""common.py: the shared base of the engine. Stdlib only. Every engine module imports this and nothing else from outside its folder.

    import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1])); import common

What is here
  REPO, CONF_DIR, CONF, KEYS      where the repo and the user's config live (CUTROOM_HOME overrides ~/.cutroom)
  load_config()                   ~/.cutroom/config.json as a dict with defaults filled; stop()s when the workspace is not set
  save_config(cfg)                atomic write
  workspace() / ws("Footage")     the workspace root, or one of its folders (Drop Footage Projects Finished Brand Review Archive)
  brand_dir()                     the user's DNA folder (Brand/)
  probe(path)                     one ffprobe call: {dur, w, h, fps, codec, pix, audio, vindex, aindex, rotation, bytes}
  fingerprint(path)               16 hex chars from size + first 512 KiB + last 512 KiB: a content identity that survives a rename
  clean(name)                     a file name with the characters Finder and the web mind removed, capped at 140 chars
  atomic_write_json / read_json   never a half-written file
  run(cmd)                        subprocess with the plain-line failure rule (UTF-8, replacement on odd bytes)
  need(tool, hint)                stop() unless the tool is on PATH
  stop(msg)                       print "STOP: <msg>" and exit 2 (the only way a module gives up)
  trash(path)                     macOS Trash through /usr/bin/trash, else a holding folder under the workspace (undo by hand)
  VIDEO_EXT, AUDIO_EXT, IMAGE_EXT what counts as footage
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONF_DIR = Path(os.environ.get("CUTROOM_HOME") or "~/.cutroom").expanduser()
CONF = CONF_DIR / "config.json"
KEYS = CONF_DIR / "keys.env"

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".mts", ".m2ts", ".avi", ".mxf", ".braw", ".r3d", ".insv"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".caf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".dng", ".cr2", ".cr3", ".arw", ".raf", ".nef", ".tif", ".tiff", ".webp"}
WORKSPACE_FOLDERS = ("Drop", "Footage", "Projects", "Finished", "Brand", "Review", "Archive")

DEFAULTS = {
    "workspace": "",
    "whisper_model": str(CONF_DIR / "models" / "ggml-large-v3-turbo.bin"),
    "venv_python": str(CONF_DIR / "venv" / "bin" / "python"),
    "whisper_language": "en",
    "proxy_height": 360,
    "review_port": 8765,
    "takes_handle_s": 5.0,
    "cleanup": "ask",            # ask | auto: whether finish.py deletes a project's intermediates without asking
    "archive": "",               # where archived shoots go (an SSD path), empty until the wizard sets it
}


def stop(msg):
    print("STOP: " + str(msg).strip(), file=sys.stderr)
    sys.exit(2)


def load_config(require_workspace=True):
    cfg = dict(DEFAULTS)
    if CONF.exists():
        try:
            cfg.update(json.loads(CONF.read_text()))
        except json.JSONDecodeError as e:
            stop(f"{CONF} is not valid JSON ({e}); fix it or delete it and run the setup again")
    if require_workspace and not cfg.get("workspace"):
        stop("no workspace is set: run /cutroom-setup (it writes ~/.cutroom/config.json)")
    return cfg


def save_config(cfg):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CONF, cfg)


def workspace():
    return Path(load_config()["workspace"]).expanduser()


def ws(sub):
    if sub not in WORKSPACE_FOLDERS:
        stop(f"no workspace folder called {sub!r} (one of {', '.join(WORKSPACE_FOLDERS)})")
    return workspace() / sub


def brand_dir():
    return ws("Brand")


def make_workspace(root):
    root = Path(root).expanduser()
    for f in WORKSPACE_FOLDERS:
        (root / f).mkdir(parents=True, exist_ok=True)
    return root


def need(tool, hint=""):
    """stop() unless <tool> is on PATH."""
    if shutil.which(tool) is None:
        stop(f"{tool} is not installed or not on PATH" + (f" ({hint})" if hint else ""))
    return shutil.which(tool)


def run(cmd, check=True, capture=True, text=True, **kw):
    """Run a command. On failure with check=True, stop() with the command's last stderr line, never a stack trace.
    Output is read as UTF-8 with replacement, so an odd byte in a file tag never crashes a run."""
    if text:
        kw.setdefault("encoding", "utf-8")
        kw.setdefault("errors", "replace")
    r = subprocess.run([str(c) for c in cmd], capture_output=capture, text=text, **kw)
    if check and r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        stop(f"{Path(str(cmd[0])).name} failed ({r.returncode}): {tail[-1] if tail else 'no output'}")
    return r


def probe(path):
    """One ffprobe call. Returns None when the file has no picture stream."""
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], check=False)
    try:
        pr = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return None
    streams = pr.get("streams", [])
    vs = [s for s in streams if s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg", "png", "bmp", "gif")
          and s.get("disposition", {}).get("attached_pic", 0) != 1]
    if not vs:
        return None
    v = vs[0]
    aud = [s for s in streams if s.get("codec_type") == "audio"]
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if not rot and v.get("tags", {}).get("rotate"):
        rot = int(v["tags"]["rotate"])
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    if abs(rot) in (90, 270):
        w, h = h, w
    num, _, den = (v.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    dur = float(pr.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "dur": dur, "w": w, "h": h, "fps": round(fps, 3), "codec": v.get("codec_name"), "pix": v.get("pix_fmt"),
        "transfer": v.get("color_transfer"), "audio": bool(aud), "vindex": int(v.get("index", 0)),
        "aindex": int(aud[0]["index"]) if aud else None, "rotation": rot, "bytes": int(pr.get("format", {}).get("size") or os.path.getsize(path)),
        "vertical": h >= w,
    }


def fingerprint(path):
    import hashlib
    p = str(path)
    s = os.path.getsize(p)
    h = hashlib.sha1(str(s).encode())
    with open(p, "rb") as f:
        h.update(f.read(1 << 19))
        if s > 1 << 20:
            f.seek(s - (1 << 19))
            h.update(f.read())
    return h.hexdigest()[:16]


_BAD = re.compile(r'[\\/:*?"<>|#%]+')


def clean(name, cap=140):
    s = _BAD.sub(" ", str(name)).replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:cap].rstrip(" .") or "Untitled"


def atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        stop(f"{path} is not valid JSON ({e})")


def trash(path):
    """Move a file to the Trash. macOS: /usr/bin/trash (Finder's Put Back works). Otherwise a holding folder in the workspace."""
    path = Path(path)
    if not path.exists():
        return "missing"
    if os.path.exists("/usr/bin/trash"):
        r = subprocess.run(["/usr/bin/trash", str(path)], capture_output=True, text=True)
        if r.returncode == 0:
            return "trash"
    hold = ws("Review") / "trash" / time.strftime("%Y-%m-%d")
    hold.mkdir(parents=True, exist_ok=True)
    dest = hold / path.name
    n = 1
    while dest.exists():
        dest = hold / f"{path.stem} ({n}){path.suffix}"
        n += 1
    shutil.move(str(path), str(dest))
    return str(dest)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "config"
    if cmd == "config":
        print(json.dumps(load_config(require_workspace=False), indent=1))
    elif cmd == "probe" and len(sys.argv) > 2:
        print(json.dumps(probe(sys.argv[2]), indent=1))
    elif cmd == "fingerprint" and len(sys.argv) > 2:
        print(fingerprint(sys.argv[2]))
    elif cmd == "set" and len(sys.argv) > 3:
        # common.py set <key> <value>: write one config key (workspace, archive, cleanup, proxy_height, review_port ...)
        cfg = load_config(require_workspace=False)
        key, val = sys.argv[2], sys.argv[3]
        if key not in DEFAULTS:
            stop(f"unknown config key {key!r}; known: {', '.join(DEFAULTS)}")
        if isinstance(DEFAULTS[key], (int, float)) and not isinstance(DEFAULTS[key], bool):
            val = type(DEFAULTS[key])(val)
        if key in ("workspace", "archive") and val:
            val = str(Path(val).expanduser())
        cfg[key] = val
        save_config(cfg)
        print(f"{key} = {val}")
    elif cmd == "make-workspace" and len(sys.argv) > 2:
        root = make_workspace(sys.argv[2])
        cfg = load_config(require_workspace=False); cfg["workspace"] = str(root); save_config(cfg)
        print(f"workspace ready at {root}: " + ", ".join(WORKSPACE_FOLDERS))
    else:
        print(__doc__)
