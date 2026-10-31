#!/usr/bin/env python3
"""render.py: draw an edit plan over its cut and finish the sound. One command, one finished mp4.

  python3 engine/render.py EDIT.json --out FINAL.mp4 [--brand DIR] [--work DIR] [--keep] [--prep-only] [--concurrency N]

The edit plan (JSON; relative paths are read from the plan's own folder):

  cut         the one video the edit is laid over. Its own audio track is the voice and is used as it is (master it first).
  theme       the design theme dict (engine/dna.py theme), or a path to a JSON file holding it (remotion/README.md has the contract)
  output      {size: [1080, 1920], fps: 30, crf: 17, max_mbps: null}   size defaults to the cut's size, fps to 30
  face        {cx, cy, h}   the speaker's face box in the cut's pixels (or 0..1 fractions of it); keeps a top card above the head.
              engine/face.py anchor's file works as it is, by content or by path
  track       {"0": {cx, cy, h}, "1": ...}   the face box each second (optional, better than one face). engine/face.py track's file works as it is, by content or by path
  words       [{text, start_s, end_s, accent?, neg?}]   the spoken words; they drive the caption pages and where the music ducks.
              engine/words.py's words.json ({"words": [{text, start, end}]}) works as it is, by content or by path
  captions_mode  "feed" (default when there are words) | "none" | "external": the last two draw no caption pages
  feed        {place?, size?, words?, motion?, hero?}   look knobs for the caption pages (a DNA caption style's feed block)
  zooms       [{start_s, end_s, z, cx?, cy?, ease_s?}]   eased emphasis zooms
  inserts     [{src, start_s, end_s, mode: full|window|pip, transition: cut|whip-l|whip-r|zoom|soft|band, from_s?, rate?, pos?, side?}]
  hook        {lines: ["..."], end_s, look?, top?, size?, turn_s?}   the hook card, on screen from frame 0 (none when the theme says hook_on false)
  graphics    [{component, start_s, end_s, props, captions?: "hide"}]   components registered in remotion/graphics/index.ts or your own in remotion/graphics/user/index.ts
  mark        true | false   the brand mark (default: the theme's lockup_on)
  safe        {top, bottom, rail, railTop}   platform UI to stay out of, as fractions of the frame (remotion/README.md)
  end_card    {at_s, line?, handle?} | false   line and handle default to the theme's end_card
  music       {file, under_speech_db?: 22, alone_db?: 14, ease_s?: 1.5}   one bed under the voice (engine/sound.py bed)
  sfx         omitted or "auto": clicks and pops placed on graphics, inserts and the end card, in pauses between words, within a budget
              false | "off" | [] | null: none
              {mode: "auto", per_min?: 12, under_speech_db?: 14}: auto with its numbers
              [{file | family, at_s, db?}]: your own effects at their times; db is where the effect's loud part sits against the
              speaker's level (default -14, extremely subtle); a family is click, pop, punch or shutter from the engine's sound kit
  montage     {clips: [{src, at, dur, from_s?, rate?, z?, pos?, in?, inDur?, dir?}], vo: [{src, at}], duration_s?}
              a b-roll piece with no talking head, used instead of cut. The vo lines are the voice.

What it does: conforms every insert (or montage clip) to the output size and rate into public/broll/, puts the theme's files in
public/brand-<id>/, writes the props, renders the Edit (or Montage) composition video-only through Remotion, then mixes the voice, the
music bed and the effects, normalizes the mix to -16 LUFS / -2 dBTP (a straight gain, with the peaks limited on a 4x oversampled signal
where they would pass the true-peak target), muxes it with the picture (BT.709 tags) and checks the result: size, duration within 0.1 s
of the cut, loudness within 1 LU of the target, true peak at -1.5 dBTP or lower.

The music bed comes from engine/sound.py: 22 dB under the speaker's level through the talk and 14 dB under it where the voice is absent,
eased over 1.5 s on a raised cosine in dB. The theme's music_level is not read: the bed follows the voice, not an absolute level.

Exit 0 and one summary line on success. A failure is one "STOP: ..." line on stderr and exit 2.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import common
from common import stop

REMOTION = common.REPO / "remotion"
SR = 48000
LUFS_TARGET = -16.0
TP_TARGET = -2.0
TP_FAIL = -1.5  # a final over this true peak fails (the target leaves room for the AAC encode)
MUSIC_UNDER_SPEECH_DB = 22.0
MUSIC_ALONE_DB = 14.0
MUSIC_EASE_S = 1.5
FADE_IN_S = 0.25
FADE_OUT_S = 1.6
SFX_UNDER_DB = 14.0
SFX_PER_MIN = 12.0
BT709_TAGS = "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"
MODES = ("full", "window", "pip")
TRANSITIONS = ("cut", "whip-l", "whip-r", "zoom", "soft", "band")
HOOK_LOOKS = ("card", "accent", "ink", "bigtype", "bar", "mono")
CAPTION_MODES = ("feed", "none", "external")
STILL_EXT = {".jpg", ".jpeg", ".png", ".webp"}
FONT_MAGIC = (b"wOF2", b"wOFF", b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")
USAGE = "render.py EDIT.json --out FINAL.mp4 [--brand DIR] [--work DIR] [--keep] [--prep-only] [--concurrency N]"
BRAND_OVERRIDE = None  # --brand: the Brand folder theme files are looked up in


class Parser(argparse.ArgumentParser):
    def error(self, message):  # the engine's contract: a bad invocation is one STOP line, not argparse's usage block
        stop(f"usage: {USAGE} ({message})")


# ---------------------------------------------------------------- small helpers
def say(msg):
    print(f"render: {msg}", flush=True)


def _num(v, what, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        stop(f"{what} must be a number, got {v!r}")
    if lo is not None and v < lo:
        stop(f"{what} must be at least {lo:g}, got {v:g}")
    if hi is not None and v > hi:
        stop(f"{what} must be at most {hi:g}, got {v:g}")
    return float(v)


def _np():
    try:
        import numpy as np
    except ImportError:
        stop("numpy is not installed: pip install numpy")
    return np


def _sound():
    """engine/sound.py, loaded only when the edit has music or effects"""
    try:
        import sound
    except ImportError as e:
        stop(f"engine/sound.py could not be loaded: {e}")
    return sound


def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def ffprobe_video(path):
    """{w, h, fps, dur, audio}: the first picture stream's size, rate and own duration, and whether the file has sound"""
    r = sh(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    try:
        info = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        info = {}
    streams = info.get("streams", [])
    vs = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) != 1]
    if not vs:
        stop(f"{path} has no picture stream ffprobe can read")
    v = vs[0]
    num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    nb = v.get("nb_frames")
    dur = float(v.get("duration") or 0)
    if not dur and nb and str(nb).isdigit() and fps > 0:
        dur = int(nb) / fps
    if not dur:
        dur = float(info.get("format", {}).get("duration") or 0)
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    for sd in v.get("side_data_list", []):
        if "rotation" in sd and abs(int(float(sd["rotation"]))) in (90, 270):
            w, h = h, w
    return {"w": w, "h": h, "fps": fps, "dur": dur, "audio": any(s.get("codec_type") == "audio" for s in streams), "transfer": v.get("color_transfer")}


def link_or_copy(src, dst):
    """a hard link where the two sit on one disk (instant), else a copy. Not a symlink: Remotion's public folder does not follow them."""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)  # content only: a system file's flags and a read-only mode are not carried over


def parse_size(v, default):
    bad = f'output.size must look like [1080, 1920] or "1080x1920", got {v!r}'
    if v is None:
        return default
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", v)
        if not m:
            stop(bad)
        return int(m.group(1)), int(m.group(2))
    if isinstance(v, dict):
        v = [v.get("w") or v.get("width"), v.get("h") or v.get("height")]
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) and x > 0 for x in v)):
        stop(bad)
    return int(v[0]), int(v[1])


# ---------------------------------------------------------------- the plan
def load_plan(path):
    path = Path(path).expanduser()
    if not path.is_file():
        stop(f"edit plan not found: {path}")
    try:
        plan = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        stop(f"{path} is not valid JSON ({e})")
    if not isinstance(plan, dict):
        stop(f"{path} must hold one JSON object (the edit plan)")
    return plan, path.resolve().parent


def _brand_dir():
    if BRAND_OVERRIDE:
        return Path(BRAND_OVERRIDE)
    try:
        cfg = common.load_config(require_workspace=False)
    except SystemExit:
        return None
    return (Path(cfg["workspace"]).expanduser() / "Brand") if cfg.get("workspace") else None


def find_file(p, base, what, strip=None):
    """an absolute path, or one relative to the plan's folder (then the Brand folder, then the working folder). `strip` is a prefix the
    renderer's public folder adds to a theme's files (brand-<id>/): a path that starts with it is also looked up without it."""
    p = str(p)
    tries = [p] + ([p[len(strip):]] if strip and p.startswith(strip) else [])
    cands = []
    for q in tries:
        if os.path.isabs(os.path.expanduser(q)):
            cands.append(Path(q).expanduser())
        else:
            cands.append(base / q)
            b = _brand_dir()
            if b:
                cands.append(b / q)
            cands.append(Path.cwd() / q)
    for c in cands:
        if c.is_file():
            return c.resolve()
    where = ", ".join(dict.fromkeys(str(c.parent) for c in cands))
    stop(f"{what} not found: {p} (looked in {where})")


def load_theme(plan, base):
    t = plan.get("theme")
    if isinstance(t, str):
        tp = find_file(t, base, "theme file")
        try:
            t = json.loads(tp.read_text())
        except json.JSONDecodeError as e:
            stop(f"{tp} is not valid JSON ({e})")
    if not isinstance(t, dict):
        stop("the plan needs a theme: the DNA's theme dict (engine/dna.py theme), or a path to the JSON file that holds it")
    if not t.get("id") or not isinstance(t.get("colors"), dict) or not t["colors"].get("accent"):
        stop("the theme needs an id and colors.accent")
    if t.get("hook_look") is not None and t["hook_look"] not in HOOK_LOOKS:
        stop(f"theme.hook_look must be one of {', '.join(HOOK_LOOKS)}, got {t['hook_look']!r}")
    return t


def registered_graphics():
    """the ids registered in remotion/graphics/index.ts and the user's own in remotion/graphics/user/index.ts (read from the files, so a
    plan is checked before a render starts)"""
    out = set()
    for f, var in ((REMOTION / "graphics" / "index.ts", "GRAPHICS"), (REMOTION / "graphics" / "user" / "index.ts", "USER_GRAPHICS")):
        if not f.is_file():
            continue
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", f.read_text(), flags=re.S)
        m = re.search(r"export\s+const\s+" + var + r"\b[^=]*=\s*\{(.*?)\}", text, re.S)
        if not m:
            continue
        names = (part.split(":")[0].strip() for part in m.group(1).split(","))
        out |= {n for n in names if re.fullmatch(r"[A-Za-z_]\w*", n)}
    return out


def dna_graphics():
    """{id: component} from the Brand folder's dna.json graphics.built, so a plan can ask for a graphic by the id the DNA gave it ({} without one)"""
    b = _brand_dir()
    try:
        built = json.loads((b / "dna.json").read_text()).get("graphics", {}).get("built", []) if b else []
        return {x["id"]: x["component"] for x in built if isinstance(x, dict) and x.get("id") and x.get("component")}
    except (OSError, ValueError, AttributeError):
        return {}


def scale_face(f, cut, out):
    """a face box in the cut's frame -> pixels in the output frame (the cut is cover-fitted into the output)"""
    if not isinstance(f, dict) or "cx" not in f or "cy" not in f:
        stop(f"a face box needs cx and cy, got {f!r}")
    cw, ch = cut
    W, H = out
    cx, cy = _num(f["cx"], "face.cx"), _num(f["cy"], "face.cy")
    h = f.get("h")
    if f.get("W") and f.get("H"):  # measured in a frame of its own size
        fx, fy, fh = cx / f["W"], cy / f["H"], (h / f["H"] if h else None)
    elif cx <= 1.5 and cy <= 1.5:  # fractions of the cut frame
        fx, fy, fh = cx, cy, h
    else:  # pixels in the cut frame
        fx, fy, fh = cx / cw, cy / ch, (h / ch if h else None)
    s = max(W / cw, H / ch)
    ox, oy = (W - cw * s) / 2, (H - ch * s) / 2
    r = {"cx": round(fx * cw * s + ox, 1), "cy": round(fy * ch * s + oy, 1)}
    if fh:
        r["h"] = round(fh * ch * s, 1)
    return r


def check_font(p):
    with open(p, "rb") as fh:
        head = fh.read(4)
    if head not in FONT_MAGIC:
        stop(f"{p} is not a font file (a .woff2, .woff, .ttf or .otf): check the theme's font files")


def link_theme_assets(theme, public, base):
    """put the theme's files in public/brand-<id>/ and point the theme at them (a path relative to the public folder). Paths the DNA
    already prefixed with brand-<id>/ are looked up in the Brand folder without it."""
    t = json.loads(json.dumps(theme))
    folder = "brand-" + re.sub(r"[^A-Za-z0-9_-]+", "-", str(t["id"])).strip("-")
    used = {}

    def put(p, what, font=False):
        if not isinstance(p, str) or not p or re.match(r"^(https?:|data:)", p):
            return p
        src = find_file(p, base, what, strip=folder + "/")
        if font:
            check_font(src)
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", src.name)  # a name every URL and every browser agrees on
        if name in used and used[name] != src:
            name = f"{len(used)}-{name}"
        used[name] = src
        link_or_copy(src, public / folder / name)
        return f"{folder}/{name}"

    for k in ("lockup_src", "lockup_dark_src", "emblem_src"):
        if t.get(k):
            t[k] = put(t[k], f"theme.{k}")
    for key in ("font_faces", "running_faces", "mono_faces"):
        for f in t.get(key) or []:
            f["src"] = put(f.get("src"), f"theme.{key} file", font=True)
    return t


def link_prop_files(obj, public):
    """in a graphic's props, a string that is an absolute path to a real file becomes a file in public/assets/ (so a component can
    show an image with staticFile); everything else is left alone"""
    if isinstance(obj, dict):
        return {k: link_prop_files(v, public) for k, v in obj.items()}
    if isinstance(obj, list):
        return [link_prop_files(v, public) for v in obj]
    if isinstance(obj, str) and os.path.isabs(obj) and os.path.isfile(obj):
        name = f"{common.fingerprint(obj)[:8]}-{re.sub(r'[^A-Za-z0-9._-]+', '-', Path(obj).name)}"
        link_or_copy(obj, public / "assets" / name)
        return f"assets/{name}"
    return obj


def _times(obj, what, dur):
    s = _num(obj.get("start_s"), f"{what}.start_s", 0)
    e = _num(obj.get("end_s"), f"{what}.end_s", 0)
    if e <= s:
        stop(f"{what} ends ({e:g}) before it starts ({s:g})")
    if s > dur + 0.05:
        stop(f"{what} starts at {s:g} s, after the end of the video ({dur:.2f} s)")
    return s, e


# ---------------------------------------------------------------- conforming picture
def conform_cmd(src, dst, W, H, fps, start, need, pos=None):
    """ffmpeg argv: a clip cover-cropped to W x H, constant rate, silent, dense keyframes (so a seek is exact), `need` seconds from `start`"""
    px, py = 0.5, 0.5
    if pos:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%\s*", str(pos))
        if not m:
            stop(f'a clip pos must look like "50% 40%", got {pos!r}')
        px, py = float(m.group(1)) / 100, float(m.group(2)) / 100
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H}:x='(iw-ow)*{px}':y='(ih-oh)*{py}',fps={fps},format=yuv420p"
    )
    still = Path(src).suffix.lower() in STILL_EXT
    pre = ["-loop", "1", "-framerate", str(fps)] if still else ["-ss", f"{start:g}"]
    return [
        "ffmpeg", "-v", "error", "-y", *pre, "-t", f"{need:.2f}", "-i", str(src),
        "-filter_threads", "1",  # ffmpeg 9.0 can crash in a threaded filter after a crop
        "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-g", "15", "-an", str(dst),
    ]  # fmt: skip


def conform(src, public, name, W, H, fps, start, need, pos=None):
    """one conformed clip in public/broll/, kept between runs while its source, window and size are unchanged. Returns its public path."""
    dst = public / "broll" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    st = os.stat(src)
    sig = json.dumps([str(src), st.st_mtime_ns, st.st_size, start, round(need, 2), W, H, fps, pos])
    sigf = Path(str(dst) + ".sig")
    if not (dst.exists() and sigf.exists() and sigf.read_text() == sig):
        r = sh(conform_cmd(src, dst, W, H, fps, start, need, pos))
        if r.returncode != 0 or not dst.exists():
            tail = (r.stderr or "").strip().splitlines()
            stop(f"could not conform {Path(src).name}: {tail[-1] if tail else 'ffmpeg failed'}")
        sigf.write_text(sig)
    return f"broll/{name}"


# ---------------------------------------------------------------- Remotion
def last_error_line(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip() and not re.match(r"^\s*at\s", ln)]
    err = [ln for ln in lines if re.search(r"error|fail|cannot|not found|invalid|must", ln, re.I)]
    return (err or lines or ["no output"])[-1][:300]


def run_remotion(comp, props, public, out, crf, concurrency, dur, max_mbps=None):
    """one Remotion render with a watchdog: a render that fails or runs past its budget is killed with everything it started and
    tried once more at half the concurrency"""
    if not (REMOTION / "node_modules" / "remotion").exists():
        stop(f"remotion is not installed: run npm ci in {REMOTION}")
    cmd = [
        "npx", "--no-install", "remotion", "render", "index.ts", comp, str(out),
        f"--props={props}", f"--public-dir={public}", "--log=error", "--codec=h264", f"--crf={crf}",
        "--image-format=png", "--color-space=bt709", "--muted",
    ]  # fmt: skip
    if crf < 17:  # a sharper render than the default gets the slow preset and a rate cap that keeps it under a platform's upload limit
        cap = int(max_mbps or 24)
        cmd += ["--x264-preset=slow", f"--encoding-max-rate={cap}M", f"--encoding-buffer-size={cap * 2}M"]
    budget, why = max(600.0, dur * 10), ""
    for attempt, c in enumerate((concurrency, max(2, concurrency // 2))):
        pr = subprocess.Popen(cmd + [f"--concurrency={c}"], cwd=REMOTION, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            _, err = pr.communicate(timeout=budget)
            if pr.returncode == 0 and Path(out).exists():
                return
            why = last_error_line(err)
        except subprocess.TimeoutExpired:
            os.killpg(pr.pid, signal.SIGKILL)
            pr.communicate()
            why = f"no finish in {budget:.0f} s (hung)"
        if attempt == 0:
            say(f"remotion retry at concurrency {max(2, concurrency // 2)}: {why}")
    stop(f"the Remotion render failed: {why}")


# ---------------------------------------------------------------- sound
def read_audio(path):
    """a file's first audio stream as float64 (samples x 2) at 48 kHz"""
    np = _np()
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        tail = (r.stderr or b"").decode(errors="replace").strip().splitlines()
        stop(f"could not read the sound of {Path(path).name}: {tail[-1] if tail else 'no audio stream'}")
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def write_wav(path, Y):
    """float (samples x 2) -> a 48 kHz stereo 16-bit wav (peaks past full scale are held at it)"""
    import wave

    np = _np()
    pcm = (np.clip(Y, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def loudness_of(src):
    """integrated loudness (LUFS) and true peak (dBTP) of a file's sound (or of ffmpeg input args), by ffmpeg's ebur128"""
    args = src if isinstance(src, list) else ["-i", str(src)]
    r = sh(["ffmpeg", "-hide_banner", "-nostats", *args, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    vals = [float(x.split()[1]) for x in r.stderr.splitlines() if x.strip().startswith("I:") and "LUFS" in x]
    peaks = [float(x.split()[1]) for x in r.stderr.splitlines() if x.strip().startswith("Peak:")]
    return (vals[-1] if vals else None), (peaks[-1] if peaks else None)


def normalize_filter(i_in, tp_in, bias=0.0):
    """a straight gain that lands the sound on the loudness target; when that would push its peaks over the true-peak target they are
    limited, on a 4x oversampled signal so an inter-sample peak is caught before the AAC encode"""
    gain = LUFS_TARGET - i_in + bias
    f = f"volume={gain:.3f}dB"
    ceiling = TP_TARGET - 0.05
    if tp_in + gain > ceiling:
        f += f",aresample={SR * 4},alimiter=limit={10 ** (ceiling / 20):.4f}:attack=3:release=40:level=false,aresample={SR}"
    return f


def into_pause(t, words, clear=0.06, room=0.12):
    """t moved to the nearest time that is in a pause between words: not on a word, not in the `clear` s after one, and `room` s clear after
    it too. None when there is no such time within half a second."""
    spans = [(w["start_s"], w["end_s"]) for w in words]

    def free(x):  # a couple of milliseconds of margin, so a time that sits exactly on an edge is not called free
        return x >= 0 and not any(s - 0.022 <= y <= e + clear + 0.002 for s, e in spans for y in (x, x + room))

    if not spans or free(t):
        return t
    for i in range(1, 26):
        for x in (t + i * 0.02, t - i * 0.02):
            if free(x):
                return round(x, 3)
    return None


def kit_has(sound, families):
    """does the sound kit have every family (so a missing kit skips the effects instead of stopping the render)"""
    dirs = [d for d in (sound.sfx_dir(), sound.PACK) if d and os.path.isdir(d)]
    return all(any(re.match(rf"^{re.escape(f)}(-\d+)?\.wav$", n) for d in dirs for n in os.listdir(d)) for f in families)


def sfx_candidates(job):
    """[(time, tier, family)] where an effect could land with something the eye sees arrive: a graphic, an insert, the end card.
    Tiers follow the budget's order: 1 full-screen entrances, 2 a graphic arriving or a cut inside a run of inserts. Plain cuts, zooms,
    captions, exits and the return to the speaker stay silent."""
    c = []
    for g in job["graphics"]:
        c.append((g["start_s"] + 0.06, 2, "pop"))
    full = [i for i in job["inserts"] if i["mode"] != "window"]
    for i in job["inserts"]:
        a = i["start_s"]
        if i["mode"] == "window":
            c.append((a + 0.12, 2, "pop"))
        elif any(x is not i and abs(x["end_s"] - a) < 0.15 for x in full):
            c.append((a + 0.1, 2, "click"))
        else:
            c.append((a + 0.1, 1, "shutter"))
    for n, clip in enumerate(job["clips"]):
        if n:
            c.append((clip["at"] + 0.1, 2, "click"))
    if job["end_card"]:
        c.append((job["end_card"]["at_s"] + 0.05, 1, "shutter"))
    return c


def auto_sfx(job, sound):
    """the budgeted effects for this edit: [{clip, at_s, db, centerd}] (none when the kit is missing or nothing lands)"""
    cfg = job["sfx"]
    dur = job["dur"]
    cand = []
    for t, tier, fam in sfx_candidates(job):
        t = into_pause(t, job["words"]) if job["words"] else t
        if t is not None and 0.5 <= t <= dur - 0.3:  # none in the first 0.5 s or the last 0.3 s
            cand.append((round(t, 3), tier, ((fam, 0, fam == "punch"),)))
    if not cand:
        return []
    fams = {s[0] for _, _, snd in cand for s in snd}
    if not kit_has(sound, fams):
        say("note: no sound kit found (run engine/sfx_synth.py), so no effects are placed")
        return []
    K = sound.kit(fams)
    budget = round(float(cfg["per_min"]) * dur / 60)
    if dur < 15:
        budget = max(3, budget)  # a short video keeps a floor of three
    ev = sound.pick(cand, budget)
    used, out = {}, []
    for t, _tier, snd in ev:
        fam, _d, centerd = snd[0]
        i = used.get(fam, 0)
        used[fam] = i + 1
        out.append({"clip": K[fam][i % len(K[fam])], "at_s": t, "db": -float(cfg["under_db"]), "centerd": centerd, "family": fam})
    return out


def explicit_sfx(job, sound):
    out = []
    for ev in job["sfx"]["events"]:
        if ev.get("file"):
            clip = sound.load_clip(str(ev["file"]))
        else:
            fam = ev["family"]
            if not kit_has(sound, [fam]):
                stop(f"sfx family {fam!r} is not in the sound kit: run engine/sfx_synth.py, or give a file")
            clip = sound.kit([fam])[fam][0]
        name = ev.get("family") or Path(ev["file"]).stem
        out.append({"clip": clip, "at_s": ev["at_s"], "db": float(ev.get("db", -SFX_UNDER_DB)), "centerd": bool(ev.get("centerd")), "family": name})
    return out


def place_sfx(Y, events, vref_db):
    """lay each effect at its time, its loud part (90th percentile of 20 ms RMS) `db` from the speaker's level, off-center and
    alternating sides so the voice keeps the middle; returns how many were placed"""
    np = _np()
    side, placed = 1, 0
    for ev in events:
        clip = ev["clip"]
        h = int(SR * 0.02)
        k = len(clip) // h
        lv = np.sqrt((clip[: k * h].reshape(k, h) ** 2).mean(axis=1)) if k else np.array([])
        lv = lv[lv > 1e-5]
        a = int(float(ev["at_s"]) * SR)
        if not len(lv) or a >= len(Y):
            continue
        g = 10 ** ((vref_db + ev["db"] - 20 * np.log10(np.percentile(lv, 90))) / 20)
        part = clip[: len(Y) - a] * g
        side = -side
        gl, gr = (1.0, 1.0) if ev.get("centerd") else ((0.42, 1.0) if side > 0 else (1.0, 0.42))
        Y[a : a + len(part), 0] += part * gl
        Y[a : a + len(part), 1] += part * gr
        placed += 1
    return placed


def check_notes(events, words):
    """a plain note for an effect that lands on a word or in the 60 ms after one (the transcript would hear it as part of the word)"""
    for ev in events:
        for w in words:
            if w["start_s"] - 0.02 <= ev["at_s"] <= w["end_s"] + 0.06:
                say(f"note: the {ev['family']} effect at {ev['at_s']:.2f} s lands on the word {w['text']!r}: move it into a pause")
                break


# ---------------------------------------------------------------- the plan -> props
def build(plan, base, theme, public):
    """check the whole plan, conform every clip, link the theme's files. Returns the render's settings and its Remotion props."""
    montage = plan.get("montage")
    cut = cut_info = None
    if montage:
        if not isinstance(montage, dict) or not montage.get("clips"):
            stop("a montage needs clips: [{src, at, dur}]")
    else:
        if not plan.get("cut"):
            stop("the plan needs a cut (the video the edit is laid over) or a montage")
        cut = find_file(plan["cut"], base, "cut")
        cut_info = ffprobe_video(cut)
        if cut_info["transfer"] in ("arib-std-b67", "smpte2084"):
            stop(f"{cut.name} is HDR ({cut_info['transfer']}): the final is tagged BT.709, so convert the cut to SDR first")

    o = plan.get("output") or {}
    W, H = parse_size(o.get("size"), (cut_info["w"] // 2 * 2, cut_info["h"] // 2 * 2) if cut_info else (1080, 1920))
    if W % 2 or H % 2:
        stop(f"output.size {W}x{H} must be even (H.264 needs even sides)")
    fps = int(_num(o.get("fps", 30), "output.fps", 1, 120))
    crf = int(_num(o.get("crf", 17), "output.crf", 0, 51))
    if cut_info and max(W / cut_info["w"], H / cut_info["h"]) > 1.01:
        say(f"note: the cut is {cut_info['w']}x{cut_info['h']} and the output {W}x{H}, so the picture is upscaled")

    vo = []
    if montage:
        ends = []
        for i, c in enumerate(montage["clips"]):
            ends.append(_num(c.get("at"), f"montage.clips[{i}].at", 0) + _num(c.get("dur"), f"montage.clips[{i}].dur", 0.1))
        for i, v in enumerate(montage.get("vo") or []):
            vo.append((find_file(v.get("src"), base, f"montage.vo[{i}].src"), _num(v.get("at", 0), f"montage.vo[{i}].at", 0)))
        dur = float(montage.get("duration_s") or max(ends))
    else:
        dur = cut_info["dur"]
    n_frames = max(1, round(dur * fps))

    words = []
    raw_words = plan.get("words") or []
    if isinstance(raw_words, dict):  # the word engine's own file ({"words": [{text, start, end}]})
        raw_words = raw_words.get("words") or []
    for i, w in enumerate(raw_words):
        if not isinstance(w, dict) or not str(w.get("text", "")).strip():
            stop(f"words[{i}] needs text")
        s = _num(w.get("start_s", w.get("start")), f"words[{i}].start_s", 0)
        e = _num(w.get("end_s", w.get("end")), f"words[{i}].end_s", 0)
        words.append({**{k: w[k] for k in ("accent", "neg") if k in w}, "text": str(w["text"]), "start_s": s, "end_s": max(e, s)})
    words.sort(key=lambda w: w["start_s"])
    cmode = plan.get("captions_mode") or ("feed" if words else "none")
    if cmode not in CAPTION_MODES:
        stop(f"captions_mode must be one of {', '.join(CAPTION_MODES)}, got {cmode!r}")

    feed = plan.get("feed")
    if feed is not None and not isinstance(feed, dict):
        stop(f"feed must be an object (the caption style's feed block), got {feed!r}")

    zooms = []
    for i, z in enumerate(plan.get("zooms") or []):
        s, e = _times(z, f"zooms[{i}]", dur)
        zooms.append({**z, "start_s": s, "end_s": e, "z": _num(z.get("z"), f"zooms[{i}].z", 0.5, 4)})

    inserts = []
    for i, ins in enumerate(plan.get("inserts") or []):
        what = f"inserts[{i}]"
        if not isinstance(ins, dict) or not ins.get("src"):
            stop(f"{what} needs a src (the clip file)")
        s, e = _times(ins, what, dur)
        mode = ins.get("mode", "full")
        if mode not in MODES:
            stop(f"{what}.mode must be one of {', '.join(MODES)}, got {mode!r}")
        tr = ins.get("transition")
        if tr is not None and tr not in TRANSITIONS:
            stop(f"{what}.transition must be one of {', '.join(TRANSITIONS)}, got {tr!r}")
        rate = _num(ins.get("rate", 1), f"{what}.rate", 0.25, 4)
        src = find_file(ins["src"], base, f"{what}.src")
        start = _num(ins.get("from_s", 0), f"{what}.from_s", 0)
        pub = conform(src, public, f"insert-{i:02d}.mp4", W, H, fps, start, (e - s) * rate + 1.0, ins.get("pos"))
        inserts.append({**{k: v for k, v in ins.items() if k not in ("pos", "from_s")}, "src": pub, "from_s": 0, "start_s": s, "end_s": e, "mode": mode})

    hook = plan.get("hook")
    if hook:
        if not isinstance(hook, dict) or not (hook.get("lines") or hook.get("text")):
            stop('hook needs lines: ["..."] (or text)')
        if "lines" in hook and not (isinstance(hook["lines"], list) and all(isinstance(x, (str, dict)) for x in hook["lines"])):
            stop(f'hook.lines must be a list of strings, got {hook["lines"]!r}')
        if hook.get("look") is not None and hook["look"] not in HOOK_LOOKS:
            stop(f"hook.look must be one of {', '.join(HOOK_LOOKS)}, got {hook['look']!r}")
        hook = {**hook, "end_s": _num(hook.get("end_s", 4.5), "hook.end_s", 0.1)}
        if theme.get("hook_on") is False:
            say("note: the theme turns text hooks off, so the hook card is not drawn")
            hook = None

    graphics = []
    known = registered_graphics()
    built = dna_graphics()
    for i, g in enumerate(plan.get("graphics") or []):
        what = f"graphics[{i}]"
        if not isinstance(g, dict):
            stop(f"{what} must be an object: {{component, start_s, end_s, props}}")
        cid = g.get("component") or g.get("id")
        cid = cid if cid in known else built.get(cid, cid)  # a DNA graphics.built id names its component
        if cid not in known:
            stop(f"{what}.component {g.get('component') or g.get('id')!r} is not registered in remotion/graphics/index.ts (registered: {', '.join(sorted(known)) or 'none'})")
        s, e = _times(g, what, dur)
        graphics.append({**{k: v for k, v in g.items() if k != "id"}, "component": cid, "start_s": s, "end_s": e, "props": link_prop_files(g.get("props") or {}, public)})

    end_card = plan.get("end_card")
    theme_ec = theme.get("end_card") or {}
    if isinstance(end_card, dict) and end_card.get("on") is not False:
        at = _num(end_card.get("at_s"), "end_card.at_s", 0)
        if at >= dur:
            stop(f"end_card.at_s {at:g} is past the end of the video ({dur:.2f} s)")
        end_card = {"at_s": at, "line": end_card.get("line", theme_ec.get("line", "")), "handle": end_card.get("handle", theme_ec.get("handle", ""))}
    else:
        if end_card is None and theme_ec.get("on"):
            say("note: the theme turns the end card on but the plan has no end_card.at_s, so none is drawn")
        end_card = None

    face, track = plan.get("face"), plan.get("track")
    if isinstance(face, dict) and face.get("fallback"):  # engine/face.py anchor found no face: its box is a guess, not a head
        say("note: the face box is the fallback guess (no face was found), so it is not used")
        face = None
    boxes = None
    if isinstance(track, dict) and isinstance(track.get("samples"), dict):  # engine/face.py track: boxes on a notional canvas W x H
        canvas = {"W": track.get("W", 1080), "H": track.get("H")}
        boxes = {k: {**v, **canvas} for k, v in track["samples"].items()}
        if face is None and isinstance(track.get("median"), dict) and not track["median"].get("fallback"):
            face = {**track["median"], **canvas}
    elif isinstance(track, dict):
        boxes = track
    if cut_info and (face or boxes):
        cs = (cut_info["w"], cut_info["h"])
        face = scale_face(face, cs, (W, H)) if face else None
        track = {str(k): scale_face(v, cs, (W, H)) for k, v in (boxes or {}).items()}
    else:
        face = track = None
        if hook and cut_info:
            say("note: the plan has no face box, so the hook card is not fitted above the speaker's head")

    music = plan.get("music") or None
    if isinstance(music, str):
        music = {"file": music}
    music_file = None
    if isinstance(music, dict) and music.get("file"):
        music_file = find_file(music["file"], base, "music file")
        for k in ("under_speech_db", "alone_db", "ease_s"):
            if k in music:
                _num(music[k], f"music.{k}", 0, 60)

    sfx_plan = plan["sfx"] if "sfx" in plan else "auto"
    if sfx_plan in (False, None, "off", []):
        sfx = {"mode": "off", "events": []}
    elif isinstance(sfx_plan, list):
        evs = []
        for i, ev in enumerate(sfx_plan):
            if not isinstance(ev, dict) or not (ev.get("file") or ev.get("family")):
                stop(f"sfx[{i}] needs a file or a family (click, pop, punch, shutter)")
            e2 = {**ev, "at_s": _num(ev.get("at_s"), f"sfx[{i}].at_s", 0)}
            if ev.get("file"):
                e2["file"] = str(find_file(ev["file"], base, f"sfx[{i}].file"))
            if "db" in ev:
                _num(ev["db"], f"sfx[{i}].db", -60, 20)
            evs.append(e2)
        sfx = {"mode": "events", "events": evs}
    elif sfx_plan in ("auto", "clicks") or isinstance(sfx_plan, dict):
        cfg = sfx_plan if isinstance(sfx_plan, dict) else {}
        if cfg.get("mode") in ("off", False):
            sfx = {"mode": "off", "events": []}
        else:
            sfx = {"mode": "auto", "events": [], "per_min": _num(cfg.get("per_min", SFX_PER_MIN), "sfx.per_min", 0, 60), "under_db": _num(cfg.get("under_speech_db", SFX_UNDER_DB), "sfx.under_speech_db", 0, 40)}
    else:
        stop(f'sfx must be a list of events, "auto", an object, or false, got {sfx_plan!r}')

    th = link_theme_assets(theme, public, base)
    common_props = {
        "theme": th, "words": words, "captions_mode": cmode, "feed": feed, "hook": hook, "mark": plan.get("mark"), "endCard": end_card,
        "safe": plan.get("safe"), "durationInFrames": n_frames, "fps": fps, "width": W, "height": H,
    }  # fmt: skip
    clips = []
    if montage:
        for i, c in enumerate(montage["clips"]):
            what = f"montage.clips[{i}]"
            src = find_file(c.get("src"), base, f"{what}.src")
            rate = _num(c.get("rate", 1), f"{what}.rate", 0.25, 4)
            start = _num(c.get("from_s", 0), f"{what}.from_s", 0)
            pub = conform(src, public, f"clip-{i:02d}.mp4", W, H, fps, start, float(c["dur"]) * rate + 1.0, c.get("pos"))
            clips.append({**{k: v for k, v in c.items() if k not in ("pos", "from_s")}, "src": pub, "from_s": 0})
        comp, props = "Montage", {"clips": clips, "audio": False, **common_props}
        n_clips = len(clips)
    else:
        link_or_copy(cut, public / "cut.mp4")
        comp = "Edit"
        props = {"src": "cut.mp4", "zooms": zooms, "inserts": inserts, "graphics": graphics, "face": face, "track": track, **common_props}
        n_clips = len(inserts)
    return {
        "comp": comp, "props": props, "dur": dur, "W": W, "H": H, "fps": fps, "crf": crf, "max_mbps": o.get("max_mbps"),
        "cut": cut, "has_voice": bool(vo) if montage else cut_info["audio"], "vo": vo, "words": words, "music": music or {},
        "music_file": music_file, "sfx": sfx, "inserts": inserts, "graphics": graphics, "clips": clips, "end_card": end_card,
        "theme": theme, "n_clips": n_clips, "frames": n_frames,
    }  # fmt: skip


def voice_track(job, work):
    """the voice as one wav: the cut's own sound, or the montage's voiceover lines laid at their times. None when there is none."""
    if job["vo"]:
        np = _np()
        N = int(round(job["dur"] * SR))
        Y = np.zeros((N, 2))
        for vf, at in job["vo"]:
            V = read_audio(vf)
            a = int(at * SR)
            part = V[: max(0, N - a)]
            Y[a : a + len(part)] += part
        write_wav(work / "voice.wav", Y)
        return work / "voice.wav"
    return job["cut"] if job["has_voice"] else None


def music_only(job, work, sound):
    """a montage with no voice: the track's sounding body looped to the length, faded in and out (the level is set by the final normalize)"""
    np = _np()
    N = int(round(job["dur"] * SR))
    M = read_audio(job["music_file"])
    lo, hi = sound.span(M, SR)
    Y = sound.loop(M[lo:hi], N, SR)
    t = np.arange(N) / SR
    env = np.clip(t / FADE_IN_S, 0, 1) * (0.5 - 0.5 * np.cos(np.pi * np.clip((N / SR - t) / FADE_OUT_S, 0, 1)))
    write_wav(work / "bed.wav", Y * env[:, None])
    return work / "bed.wav"


def mix_sound(job, work):
    """the sound that gets normalized, as ffmpeg input args: the cut's own audio, or a mix of the voice, the music bed (engine/sound.py)
    and the effects. None when the video has no sound at all."""
    music_file, mode = job["music_file"], job["sfx"]["mode"]
    voice = voice_track(job, work)
    if voice is None and not music_file:
        return None
    sound = _sound() if (music_file or mode != "off") else None
    base = voice  # what gets normalized, or mixed into
    if music_file:
        if voice is None:
            base = music_only(job, work, sound)
            say(f"music: {Path(music_file).name} alone, looped to {job['dur']:.1f} s")
        else:
            m = job["music"]
            under, alone, ease = (float(m.get(k, d)) for k, d in (("under_speech_db", MUSIC_UNDER_SPEECH_DB), ("alone_db", MUSIC_ALONE_DB), ("ease_s", MUSIC_EASE_S)))
            ws = [{"start": w["start_s"], "end": w["end_s"], "text": w["text"]} for w in job["words"]] or None
            rep = sound.bed(str(voice), str(music_file), str(work / "bed.wav"), under, alone, ease, ws)
            got, got_alone = rep["under_speech_db_measured"], rep["alone_db_measured"]
            if got is not None and abs(got - under) > 3.0:
                stop(f"the music bed sits {got:.1f} dB under the talk, not {under:g} (within 3)")
            n_talk = len(rep["spans"])
            alone_txt = f"{got_alone} dB under the voice alone" if got_alone is not None else "no stretch without the voice to measure alone"
            say(f"music: {Path(music_file).name}, {got} dB under the talk and {alone_txt} (asked {under:g} and {alone:g}), {n_talk} stretch{'' if n_talk == 1 else 'es'} of talk")
            base = work / "bed.wav"
    events = []
    if mode == "events":
        events = explicit_sfx(job, sound)
    elif mode == "auto":
        events = auto_sfx(job, sound)
    if events:
        np = _np()
        Y = read_audio(base)
        spans = [(w["start_s"], w["end_s"]) for w in job["words"]] or None
        vref = sound.speaking_level(str(voice or base), spans)
        n = place_sfx(Y, events, float(20 * np.log10(vref)))
        check_notes(events, job["words"])
        shown = ", ".join(f"{e['family']} {e['at_s']:.2f}" for e in events[:6])
        say(f"sfx: {n} effect{'' if n == 1 else 's'} placed ({shown}{' ...' if len(events) > 6 else ''})")
        np.asarray(Y, dtype=np.float32).tofile(work / "mix.f32")
        return ["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(work / "mix.f32")]
    return ["-i", str(base)]


def mux(video, audio_args, dur, out_part):
    """picture (copied, tagged BT.709) + the sound normalized to the loudness and true-peak targets, into out_part. Where limiting took a
    little loudness off, the gain is corrected once and the sound encoded again."""
    base = ["ffmpeg", "-v", "error", "-y", "-i", str(video)]
    tail = ["-c:v", "copy", "-bsf:v", BT709_TAGS, "-tag:v", "avc1", "-t", f"{dur:.3f}", "-movflags", "+faststart", "-f", "mp4", str(out_part)]
    if not audio_args:
        say("note: there is no sound to mix, so the final has no audio track")
        r = sh([*base, "-map", "0:v:0", "-an", *tail])
        if r.returncode != 0 or not out_part.exists():
            stop(f"the final mux failed: {last_error_line(r.stderr)}")
        return
    i_in, tp_in = loudness_of(audio_args)
    if i_in is None or i_in < -60:
        stop("the sound is silent: there is nothing to normalize to -16 LUFS")
    bias = 0.0
    for attempt in range(2):
        r = sh([*base, *audio_args, "-map", "0:v:0", "-map", "1:a:0", "-af", normalize_filter(i_in, tp_in if tp_in is not None else 0.0, bias),
                "-c:a", "aac", "-b:a", "256k", "-ar", str(SR), *tail])  # fmt: skip
        if r.returncode != 0 or not out_part.exists():
            stop(f"the final mux failed: {last_error_line(r.stderr)}")
        i_out, _ = loudness_of(out_part)
        if i_out is None or abs(i_out - LUFS_TARGET) <= 0.3:
            return
        bias += LUFS_TARGET - i_out


def cap_bitrate(part, crf, max_mbps):
    """a sharp render can run over a platform's upload limit: encode the picture once more under the cap, the sound copied"""
    vb = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=bit_rate", "-of", "csv=p=0", str(part)]).stdout.strip()
    if not (vb.isdigit() and int(vb) > int(float(max_mbps) * 1_000_000)):
        return
    capped = part.with_name(part.name + ".cap.mp4")
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", str(part), "-map", "0:v", "-map", "0:a?", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
            "-maxrate", f"{int(max_mbps)}M", "-bufsize", f"{int(max_mbps) * 2}M", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-c:a", "copy", "-movflags", "+faststart", "-f", "mp4", str(capped)])  # fmt: skip
    if r.returncode != 0:
        stop(f"re-encoding under {max_mbps} Mbps failed: {last_error_line(r.stderr)}")
    os.replace(capped, part)
    say(f"the picture ran at {int(vb) / 1e6:.1f} Mbps, over {max_mbps}, so it was encoded again under the cap")


def check(part, job, has_audio):
    """the final is the size asked for, runs as long as the cut (within 0.1 s) and, with sound, sits at -16 LUFS (within 1) at -1.5 dBTP or lower"""
    pr = ffprobe_video(part)
    if (pr["w"], pr["h"]) != (job["W"], job["H"]):
        stop(f"the final is {pr['w']}x{pr['h']}, not {job['W']}x{job['H']}")
    d = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(part)]).stdout.strip() or 0)
    if abs(d - job["dur"]) > 0.1:
        stop(f"the final runs {d:.2f} s but the cut is {job['dur']:.2f} s (more than 0.1 s apart)")
    lufs = tp = None
    if has_audio:
        lufs, tp = loudness_of(part)
        if lufs is None or abs(lufs - LUFS_TARGET) > 1.0:
            stop(f"the final's loudness is {lufs} LUFS, not {LUFS_TARGET:g} (within 1)")
        if tp is not None and tp > TP_FAIL:
            stop(f"the final's true peak is {tp:.1f} dBTP, over {TP_FAIL:g}")
    return d, lufs, tp


# ---------------------------------------------------------------- the whole render
def main(argv=None):
    global BRAND_OVERRIDE
    ap = Parser(description="Draw an edit plan over its cut, mix the sound and write the finished mp4.", usage=USAGE)
    ap.add_argument("plan", help="the edit plan JSON")
    ap.add_argument("--out", required=True, help="the finished mp4")
    ap.add_argument("--brand", help="the Brand folder the theme's files are read from (default: the workspace's Brand/)")
    ap.add_argument("--work", help="scratch folder (default: .<name>.render next to --out); kept when given")
    ap.add_argument("--keep", action="store_true", help="keep the scratch folder")
    ap.add_argument("--prep-only", action="store_true", help="conform the pictures and write the props, then stop")
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("CUTROOM_CONCURRENCY") or min(6, os.cpu_count() or 4)))
    args = ap.parse_args(argv)
    t0 = time.time()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            stop(f"{tool} not found: install ffmpeg first (brew install ffmpeg)")
    BRAND_OVERRIDE = args.brand

    plan, base = load_plan(args.plan)
    for k in ("words", "track", "face"):  # the word and face engines' files may be named by path: they sit in the take folder
        if isinstance(plan.get(k), str):
            f = find_file(plan[k], base, k)
            try:
                plan[k] = json.loads(Path(f).read_text())
            except json.JSONDecodeError as e:
                stop(f"{k} file {f} is not valid JSON ({e})")
    theme = load_theme(plan, base)
    out = Path(args.out).expanduser().resolve()
    work = Path(args.work).expanduser().resolve() if args.work else out.parent / f".{out.stem}.render"
    public = work / "public"
    public.mkdir(parents=True, exist_ok=True)
    for stale in (work / "video-only.mp4", work / "props.json", work / "mix.f32", work / "bed.wav", work / "voice.wav"):
        stale.unlink(missing_ok=True)

    job = build(plan, base, theme, public)
    W, H, fps, dur = job["W"], job["H"], job["fps"], job["dur"]
    props_path = work / "props.json"
    props_path.write_text(json.dumps(job["props"], indent=1))
    n = job["n_clips"]
    say(f"{job['comp']} {W}x{H} {fps} fps, {dur:.2f} s ({job['frames']} frames), {n} {'clip' if job['comp'] == 'Montage' else 'insert'}{'' if n == 1 else 's'}")
    if args.prep_only:
        say(f"props written: {props_path}")
        return 0

    video = work / "video-only.mp4"
    run_remotion(job["comp"], props_path, public, video, job["crf"], max(2, args.concurrency), dur, job["max_mbps"])
    t_pic = time.time()

    audio_args = mix_sound(job, work)
    part = out.with_name(out.name + ".part.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    mux(video, audio_args, dur, part)
    if job["max_mbps"]:
        cap_bitrate(part, job["crf"], job["max_mbps"])
    final_dur, lufs, tp = check(part, job, audio_args is not None)
    os.replace(part, out)
    if not args.keep:
        if args.work:
            for f in (video, work / "mix.f32", work / "bed.wav", work / "voice.wav", props_path):
                f.unlink(missing_ok=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    snd = f", {lufs:.1f} LUFS, {tp:.1f} dBTP" if lufs is not None and tp is not None else ""
    say(f"{out} {W}x{H} {fps} fps {final_dur:.2f} s{snd}, {out.stat().st_size / 1e6:.1f} MB (picture {t_pic - t0:.0f} s, total {time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        stop("interrupted")
    except OSError as e:
        stop(f"{e.strerror or e}{': ' + str(e.filename) if e.filename else ''}")
