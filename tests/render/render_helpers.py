"""Shared helpers for the render tests: synthetic media made with ffmpeg, frame comparisons and a plan that carries every layer."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine"
REMOTION = REPO / "remotion"
RENDER = ENGINE / "render.py"
FONT = REPO / "templates" / "fonts" / "Inter-Variable.ttf"

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAVE_REMOTION = (REMOTION / "node_modules" / "remotion").exists() and bool(shutil.which("node"))
HAVE_BROWSER = (REMOTION / "node_modules" / ".remotion" / "chrome-headless-shell").exists()  # npx remotion browser ensure

needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg and ffprobe are not installed")
needs_remotion = pytest.mark.skipif(
    not (HAVE_FFMPEG and HAVE_REMOTION and HAVE_BROWSER), reason="Remotion or its browser is not installed: run npm ci in remotion/, then npx remotion browser ensure"
)

# the theme exactly as the DNA emits it (engine/dna.py theme): the contract keys and nothing else
THEME = {
    "id": "testbrand",
    "name": "Test Co",
    "colors": {"accent": "#FF5A1F", "ink": "#101114", "base": "#FFFFFF", "on_accent": "#101114", "negative": "#E3161B", "bg": "#101114", "card": "#232427"},
    "surface": {"background": "rgba(35,36,39,0.94)", "border": "1px solid rgba(255,255,255,0.08)"},
    "font_family": "Inter",
    "font_faces": [{"weight": "100 900", "src": str(FONT)}],
    "caps": False,
    "skew": 0,
    "cap_words": 3,
    "caption_size": 84,
    "caption_top": 1190,
    "caption_stroke": 0,
    "hook_look": "card",
    "hook_top": 300,
    "hook_size": None,
    "hook_font": None,
    "lockup_src": None,
    "lockup_dark_src": None,
    "emblem_src": None,
    "lockup_on": False,
    "broll_style": "cut",
    "smooth_motion": True,
    "music_level": -30,
    "end_card": {"on": True, "line": "Follow for *more*", "handle": "@testco"},
}


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def ffmpeg(*args):
    r = run(["ffmpeg", "-v", "error", "-y", *args])
    assert r.returncode == 0, r.stderr
    return r


# ---------------------------------------------------------------- synthetic media
VOICE_TEXT = "Three things nobody tells you about running a small shop. Here is the first one, and it matters."


def make_voice(path, dur):
    """a 48 kHz stereo wav that sounds like speech to a level meter: a macOS voice when there is one, espeak-ng's on Linux (install.sh puts it
    there), else pulsing tones. Real speech has pauses and a silent tail, which the music bed tests need (a stretch of talk, then music alone);
    the tones have neither, so those tests only hold with a voice."""
    if shutil.which("say"):
        aiff = Path(path).with_suffix(".aiff")
        r = run(["say", "-o", aiff, VOICE_TEXT])
        if r.returncode == 0 and aiff.exists():
            ffmpeg("-i", aiff, "-af", f"apad,aresample=48000", "-ac", "2", "-t", f"{dur}", path)
            return Path(path)
    if shutil.which("espeak-ng"):
        wav = Path(path).with_name(Path(path).stem + "-espeak.wav")
        r = run(["espeak-ng", "-a", "60", "-w", str(wav), VOICE_TEXT])   # -a 60: a speaking level like a Mac voice's, not espeak-ng's near-full-scale default
        if r.returncode == 0 and wav.exists():
            ffmpeg("-i", wav, "-af", f"apad,aresample=48000", "-ac", "2", "-t", f"{dur}", path)
            return Path(path)
    ffmpeg("-f", "lavfi", "-i", f"sine=f=190:d={dur}", "-f", "lavfi", "-i", f"sine=f=2.2:d={dur}",
           "-filter_complex", "[0][1]amultiply,volume=4,aformat=sample_rates=48000:channel_layouts=stereo", "-t", f"{dur}", path)
    return Path(path)


def make_cut(path, w, h, dur=6.0, fps=30):
    """a cut with a busy picture (lavfi testsrc2) and a voice: the kind of file the cutter hands over"""
    path = Path(path)
    voice = make_voice(path.with_name(path.stem + "-voice.wav"), dur)
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={dur}", "-i", voice, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-g", str(fps), "-colorspace", "bt709",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{dur}", path)
    return path


def make_clip(path, w=640, h=360, dur=3.0, fps=25, pattern="smptebars"):
    """a short silent clip in a shape (and a rate) that is not the output's, so the conform has work to do"""
    ffmpeg("-f", "lavfi", "-i", f"{pattern}=size={w}x{h}:rate={fps}:duration={dur}", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", path)
    return Path(path)


def make_music(path, dur=20.0):
    """a steady chord with a slow tremolo: a bed for the music tests"""
    ffmpeg("-f", "lavfi", "-i", f"sine=f=220:d={dur},volume=0.5", "-f", "lavfi", "-i", f"sine=f=330:d={dur},volume=0.4",
           "-f", "lavfi", "-i", f"sine=f=440:d={dur},volume=0.3",
           "-filter_complex", "[0][1][2]amix=inputs=3:normalize=0,tremolo=f=0.5:d=0.15,aformat=sample_rates=48000:channel_layouts=stereo", path)
    return Path(path)


def make_click(path):
    ffmpeg("-f", "lavfi", "-i", "sine=f=1800:d=0.12,afade=t=out:st=0.04:d=0.08,aformat=sample_rates=48000:channel_layouts=stereo", path)
    return Path(path)


# ---------------------------------------------------------------- measuring
def probe(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    info = json.loads(r.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    num, den = v["r_frame_rate"].split("/")
    return {
        "w": int(v["width"]), "h": int(v["height"]), "fps": float(num) / float(den), "codec": v["codec_name"], "tag": v.get("codec_tag_string"),
        "pix": v["pix_fmt"], "range": v.get("color_range"), "space": v.get("color_space"), "primaries": v.get("color_primaries"),
        "transfer": v.get("color_transfer"), "dur": float(info["format"]["duration"]), "audio": a is not None,
        "a_rate": int(a["sample_rate"]) if a else None,
    }  # fmt: skip


def loudness(path):
    """(integrated LUFS, true peak dBTP) of a file's sound"""
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    i = [float(x.split()[1]) for x in r.stderr.splitlines() if x.strip().startswith("I:") and "LUFS" in x]
    p = [float(x.split()[1]) for x in r.stderr.splitlines() if x.strip().startswith("Peak:")]
    return i[-1], p[-1]


def ebur_log(path):
    return run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr


def gray(path, t, w, h):
    """one frame as w x h 8-bit gray bytes (area-averaged, so a little encoder noise does not matter)"""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    assert r.returncode == 0 and len(r.stdout) == w * h, r.stderr
    return r.stdout


def rows(frame, w, y0, y1):
    return frame[y0 * w : y1 * w]


def mean_abs_diff(a, b):
    assert len(a) == len(b)
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def median_abs_diff(a, b):
    d = sorted(abs(x - y) for x, y in zip(a, b))
    return d[len(d) // 2]


def mean(a):
    return sum(a) / len(a)


# ---------------------------------------------------------------- plans
def full_plan(cut, clip, size=(1080, 1920), theme=None, **over):
    """an edit plan that carries every layer: a hook, a zoom, an insert, a lower third, a title card and an end card"""
    plan = {
        "cut": str(cut),
        "theme": theme or THEME,
        "output": {"size": list(size), "fps": 30, "crf": 20},
        "face": {"cx": 540, "cy": 1100, "h": 360} if size[1] > size[0] else {"cx": 960, "cy": 600, "h": 300},
        "hook": {"lines": ["Three things", "nobody *tells* you"], "end_s": 2.0},
        "zooms": [{"start_s": 2.2, "end_s": 3.4, "z": 1.12}],
        "inserts": [{"src": str(clip), "start_s": 2.5, "end_s": 4.0, "mode": "full", "transition": "soft"}],
        "graphics": [{"component": "LowerThird", "start_s": 0.8, "end_s": 2.2, "props": {"name": "Alex Rivera", "role": "Founder"}}],
        "end_card": {"at_s": 4.8},
        "sfx": False,
    }
    plan.update(over)
    return plan


def write_plan(path, plan):
    Path(path).write_text(json.dumps(plan, indent=1))
    return Path(path)


def run_render(plan_path, out, *extra, env=None):
    """render.py as a user runs it; returns the CompletedProcess"""
    import os

    e = {**os.environ, "CUTROOM_CONCURRENCY": "4", **(env or {})}
    return subprocess.run([sys.executable, str(RENDER), str(plan_path), "--out", str(out), *extra], capture_output=True, text=True, env=e)


def stop_line(proc):
    """the one STOP line a failed run printed (None when it did not stop)"""
    lines = [ln for ln in proc.stderr.splitlines() if ln.startswith("STOP:")]
    return lines[-1] if lines else None


def words(text, start=0.4, step=0.28):
    out, t = [], start
    for w in text.split():
        out.append({"text": w, "start_s": round(t, 2), "end_s": round(t + step - 0.04, 2)})
        t += step
    return out
