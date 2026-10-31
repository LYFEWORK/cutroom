"""Synthetic media for the engine tests: everything is made with ffmpeg's lavfi sources, numpy tones and macOS `say`. No footage is stored."""
import json
import os
import subprocess
import wave

import numpy as np

SR = 16000


def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def write_wav(path, y, sr=SR):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return str(path)


def video(path, size="320x240", rate=30, dur=1, audio=True, pix="yuv420p", extra=()):
    """a testsrc video (with a sine track unless audio=False)"""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    cmd += ["-pix_fmt", pix, "-c:v", "libx264", *extra]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    r = sh(cmd)
    assert r.returncode == 0, r.stderr
    return str(path)


def speak(path, text, rate=170, voice=None):
    """a 16 kHz mono wav of the macOS voice saying `text`"""
    aiff = str(path) + ".aiff"
    cmd = ["say", "-r", str(rate)] + (["-v", voice] if voice else []) + ["-o", aiff, text]
    r = sh(cmd)
    assert r.returncode == 0, r.stderr
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", aiff, "-ar", "16000", "-ac", "1", str(path)])
    assert r.returncode == 0, r.stderr
    os.remove(aiff)
    return str(path)


def talking_video(path, text, size="1080x1920", rate=30, pad_s=0.6):
    """a portrait video (a moving test picture) whose sound is the voice saying `text`, with `pad_s` of room tone either side"""
    wav = str(path) + ".voice.wav"
    speak(wav, text)
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r=16000:cl=mono", "-t", str(pad_s), str(path) + ".pad.wav"])
    assert r.returncode == 0, r.stderr
    dur = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", wav]).stdout) + 2 * pad_s
    r = sh(["ffmpeg", "-v", "error", "-y", "-i", str(path) + ".pad.wav", "-i", wav, "-i", str(path) + ".pad.wav",
            "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1[a]", "-map", "[a]", "-ar", "48000", str(path) + ".full.wav"])
    assert r.returncode == 0, r.stderr
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur:.3f}", "-i", str(path) + ".full.wav",
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)])
    assert r.returncode == 0, r.stderr
    for x in (".voice.wav", ".pad.wav", ".full.wav"):
        os.remove(str(path) + x)
    return str(path)


def wx_json(path, words):
    """an aligned words file ({"segments": [{"words": [...]}]}) from [(text, start, end)]"""
    with open(path, "w") as f:
        json.dump({"segments": [{"words": [{"word": w, "start": s, "end": e} for w, s, e in words]}]}, f)
    return str(path)


def words_json(path, words, **kw):
    """a words.json ({"source", "transcript", "words": [...]}) from [(text, start, end)]; fillers and doubles are flagged by the caller's `flags`"""
    out = [{"text": t, "start": s, "end": e, "filler": t.strip(".,?!").lower() in kw.get("fillers", ()), "double": False, "cap": t[:1].isupper()} for t, s, e in words]
    with open(path, "w") as f:
        json.dump({"source": "whisper", "transcript": " ".join(t for t, _, _ in words), "words": out}, f)
    return str(path)
