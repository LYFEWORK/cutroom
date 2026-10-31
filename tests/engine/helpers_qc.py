"""Synthetic takes for the QC tests: a take folder the way run_take.sh leaves one, built from ffmpeg lavfi sources and pink noise. No media is stored.

    <root>/mez/<name>.mp4                  the mezzanine (what the cut was cut from)
    <root>/wx/<name>.avskew.json           only when a test asks for one
    <root>/<name>/<name>-cut.mp4           the cut
    <root>/<name>/<name>-cut-master.mp4    the finished file: the cut's picture (stream copy) with the mastered voice
    <root>/<name>/<name>-cut.edl.json, words.json, words.wx.json, cut.facetrack.json, and (when asked for) cut.words.json,
    <name>-cut.mp4.qc.json, captions-plan.json, plan.json, joints.json

Every picture is a 1 s (unless a test asks for longer), 270x480, 30 fps lavfi source, lossless (so the only difference between two files is what a test drew on one of them) and
tagged BT.709. This ffmpeg build writes the primaries and transfer of an x264 stream only when x264 is told too, so the tags are written both ways.
The audio is pink noise: moving enough for the lag measurement, and loudness-calibrated for the finished file so G7 can pass.
"""
import json
import os
import re
import shutil
import subprocess

TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709"]
FACE = {"cx": 540, "cy": 900, "w": 400, "h": 400}
WORDS = [{"text": "why", "start": 0.02, "end": 0.90, "filler": False}]
# A smooth gray picture that moves: three crossing sine waves. A gate that compares the per-channel MEDIAN of a frame needs a picture whose
# histogram is dense at its median (a test card of flat color bars is not: a few pixels of caption flip its median by 60 levels), and the frame
# match needs something that moves and has structure. {size} is filled in by video().
SMOOTH = "nullsrc=size={size}:rate=30,geq=lum='128+28*sin(2*PI*(X/45+T))+28*sin(2*PI*(Y/70+1.3*T))+28*sin(2*PI*((X+Y)/90-0.7*T))':cb=128:cr=128"


def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def loudness(path):
    """(integrated LUFS, true peak dBTP) of a file's audio, the way qc.py measures it (ffmpeg loudnorm)"""
    r = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    d = json.loads(m.group(0))
    return float(d["input_i"]), float(d["input_tp"])


def noise_wav(path, gain_db=0.0, dur=1.0, delay_ms=0):
    """48 kHz pink noise (a fixed seed: the same noise every time), `gain_db` over the base level, optionally `delay_ms` late"""
    af = f"volume={gain_db}dB" + (f",adelay={delay_ms}|{delay_ms}" if delay_ms else "")
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anoisesrc=color=pink:duration={dur}:sample_rate=48000:seed=42:amplitude=0.2",
            "-af", af, "-ar", "48000", "-c:a", "pcm_s16le", path])
    assert r.returncode == 0, r.stderr
    return str(path)


def calibrated_noise(path, lufs=-16.0, dur=1.0, delay_ms=0):
    """the same pink noise, gained until its integrated loudness is `lufs` (the file is rewritten); the true peak is checked on the way"""
    base = str(path) + ".base.wav"
    noise_wav(base, 0.0, dur)
    i, _ = loudness(base)
    noise_wav(path, lufs - i, dur, delay_ms)
    os.remove(base)
    return str(path)


def video(path, pattern=SMOOTH, size="270x480", dur=1.0, audio=None, tagged=True, lossless=True, vf=None, crf=12):
    """a lavfi picture (with `audio`, a wav, as AAC when given), lossless by default, tagged BT.709 unless told otherwise. `pattern` is a lavfi source
    name ("mandelbrot": size and rate are added) or a whole graph with a {size} in it (SMOOTH)"""
    src = pattern.format(size=size) if "{size}" in pattern else f"{pattern}=size={size}:rate=30"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src]
    if audio:
        cmd += ["-i", audio]
    cmd += ["-t", str(dur)]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"] + (["-qp", "0"] if lossless else ["-crf", str(crf)])
    if tagged:
        cmd += TAGS
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest"]
    cmd += [str(path)]
    r = sh(cmd)
    assert r.returncode == 0, r.stderr
    return str(path)


def mux(video_path, audio_path, out):
    """the video stream copied (its tags and pixels untouched) with `audio_path` as AAC: what the chain's mux stage does"""
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", video_path, "-i", audio_path, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-ac", "2", "-shortest", str(out)])
    assert r.returncode == 0, r.stderr
    return str(out)


def draw_box(src, out, box, color="white"):
    """`src` again with one filled box on it (x, y, w, h): lossless, tagged, audio copied. The box IS the caption ink the sweep has to find."""
    x, y, w, h = box
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill", "-c:v", "libx264", "-preset", "ultrafast",
            "-qp", "0", "-pix_fmt", "yuv420p", *TAGS, "-c:a", "copy", str(out)])
    assert r.returncode == 0, r.stderr
    return str(out)


class Take:
    """paths of one take folder"""

    def __init__(self, root, name="t1"):
        self.root, self.name = str(root), name
        self.T = os.path.join(self.root, name)
        self.mez = os.path.join(self.root, "mez", f"{name}.mp4")
        self.cut = os.path.join(self.T, f"{name}-cut.mp4")
        self.master = os.path.join(self.T, f"{name}-cut-master.mp4")
        self.edl = os.path.join(self.T, f"{name}-cut.edl.json")
        self.cut_card = os.path.join(self.T, f"{name}-cut.mp4.qc.json")
        self.avskew = os.path.join(self.root, "wx", f"{name}.avskew.json")

    def path(self, *parts):
        return os.path.join(self.T, *parts)

    def write(self, rel, obj):
        p = self.path(rel)
        with open(p, "w") as f:
            json.dump(obj, f)
        return p

    def clone(self, dest):
        """a copy of the whole root under `dest`, as a new Take (the media is small: copying beats building it again)"""
        root = os.path.join(str(dest), "root")
        shutil.copytree(self.root, root)
        return Take(root, self.name)


def make_take(base, name="t1", dur=1.0, master_lufs=-16.0, tagged_master=True, tagged_cut=True, mez_pattern=SMOOTH, cut_pattern=SMOOTH,
              words=None, pieces=None, face="track", cut_words="why", master_delay_ms=0, silent_master=False):
    """Build a take folder under `base`/root and return its Take.

    master_lufs      the finished file's loudness (None leaves the noise at its base level, about -28 LUFS)
    mez_pattern      the lavfi source of the mezzanine; a different one makes a "stale picture" against the cut
    face             "track" a face in every second, "empty" a track that never found one, "none" no track file
    cut_words        the text of both the finished file's own words (cut.words.json) and the cut's QC transcript; None writes neither
    master_delay_ms  the mastered voice this late against the mezzanine's sound
    silent_master    the finished file carries digital silence instead of the voice
    """
    t = Take(os.path.join(str(base), "root"), name)
    for d in (t.T, os.path.dirname(t.mez), os.path.dirname(t.avskew)):
        os.makedirs(d, exist_ok=True)
    raw = noise_wav(t.path("noise.wav"), 0.0, dur)
    video(t.mez, mez_pattern, dur=dur, audio=raw)
    video(t.cut, cut_pattern, dur=dur, audio=raw, tagged=tagged_cut)
    # the finished file carries the cut's picture untouched; a tagged cut with an untagged master is made by encoding the master's picture untagged
    master_video = t.cut if tagged_master == tagged_cut else video(t.path("master-video.mp4"), cut_pattern, dur=dur, tagged=tagged_master)
    if silent_master:
        voice = t.path("master.wav")
        assert sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(dur), voice]).returncode == 0
    else:
        voice = calibrated_noise(t.path("master.wav"), master_lufs, dur, master_delay_ms) if master_lufs is not None else noise_wav(t.path("master.wav"), 0.0, dur, master_delay_ms)
    mux(master_video, voice, t.master)
    words = words if words is not None else WORDS
    pieces = pieces if pieces is not None else [{"seg": 0, "c0": 0.0, "c1": float(dur), "out_in": 0.0, "out_out": float(dur)}]
    t.write(f"{name}-cut.edl.json", {"src": t.mez, "output": t.cut, "fps": 30, "pieces": pieces, "dropped": []})
    t.write("words.json", {"source": "whisper", "transcript": " ".join(w["text"] for w in words), "words": words})
    t.write("words.wx.json", {"segments": [{"words": [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in words]}]})
    if face == "track":
        t.write("cut.facetrack.json", {"W": 1080, "H": 1920, "seconds": int(dur) + 1, "samples": {str(s): dict(FACE) for s in range(int(dur) + 1)}, "median": dict(FACE),
                                       "extracted": list(range(int(dur) + 1))})
    elif face == "empty":
        t.write("cut.facetrack.json", {"W": 1080, "H": 1920, "seconds": int(dur) + 1, "samples": {}, "median": {"cx": 540, "cy": 864, "w": 432, "h": 432, "fallback": True},
                                       "extracted": list(range(int(dur) + 1))})
    if cut_words is not None:
        t.write("cut.words.json", {"source": "whisper", "transcript": cut_words, "words": [{"text": w, "start": 0.02, "end": 0.90, "filler": False} for w in cut_words.split()]})
        t.write(f"{name}-cut.mp4.qc.json", {"verdict": "PASS", "transcript": cut_words})
    for f in ("noise.wav", "master.wav", "master-video.mp4"):
        if os.path.exists(t.path(f)):
            os.remove(t.path(f))
    return t


# --- text on a frame, for the platform-zone check ------------------------------------------------------------------------------------

def text_frame(path, text, y, size=130, canvas=(1080, 1920)):
    """a black 1080x1920 PNG with `text` in large white letters, its top edge at `y` (None: no text at all): what the text reader has to find"""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", canvas, (0, 0, 0))
    if text:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:                                        # an older Pillow has no sized default font
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        ImageDraw.Draw(im).text((60, y), text, fill=(255, 255, 255), font=font)
    im.save(path)
    return str(path)


def still_video(png, out, dur=2, fps=2):
    """the PNG held for `dur` seconds as a small h264 video"""
    r = sh(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-i", png, "-t", str(dur), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "12", str(out)])
    assert r.returncode == 0, r.stderr
    return str(out)
