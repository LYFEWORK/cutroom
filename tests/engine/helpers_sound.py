"""Synthetic sound for the word-engine and sound tests: voices made of tone bursts, music made of noise or exact tones, clips in any wav format.
Everything is generated with numpy or ffmpeg's lavfi sources. No audio file is stored in the repo.

A note on the signals: a pure tone at a round frequency and sample rate repeats byte for byte, and ffmpeg's format probing can take such a file for
an MPEG transport stream. Every wav made here carries a little noise (or is made by ffmpeg itself) so that never happens.
"""
import subprocess
import wave

import numpy as np

SR = 48000
SHAPE_RMS = float(np.sqrt((0.9 ** 2 + 0.3 ** 2) / 2))   # the RMS of the voiced shape below, so a burst can be set to an exact RMS


def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


def write_pcm16(path, y, sr=SR):
    """a 16-bit wav of y: (n,) mono or (n, 2) stereo float in -1..1"""
    y = np.asarray(y)
    ch = 1 if y.ndim == 1 else y.shape[1]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return str(path)


def decode(path, channels=2, sr=SR):
    """a file's first audio stream as float32 (n, channels) at sr, through ffmpeg (the same upmix and resample any reader would do)"""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    assert r.returncode == 0 and r.stdout, r.stderr
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, channels).astype(np.float64)


def rms_db(x):
    """the power-mean level of a block of samples in dBFS"""
    return float(10 * np.log10(np.mean(np.square(np.asarray(x, dtype=np.float64))) + 1e-20))


def window_db(x, a, b, sr=SR):
    """the level of x between a and b seconds"""
    return rms_db(x[int(a * sr):int(b * sr)])


def burst_voice(bursts, dur, rms=0.1, room_rms=0.0005, sr=SR, seed=1):
    """a mono float voice: voiced bursts [(start_s, end_s)] of a 220 Hz tone with a second harmonic (all under 2 kHz, 10 ms ramps) at exactly `rms`
    (power mean over a burst), over a quiet noise room"""
    t = np.arange(int(dur * sr)) / sr
    y = np.random.default_rng(seed).standard_normal(len(t)) * room_rms
    shape = np.sin(2 * np.pi * 220 * t) * 0.9 + np.sin(2 * np.pi * 440 * t) * 0.3
    for a, b in bursts:
        m = (t >= a) & (t < b)
        ramp = np.clip(np.minimum(t[m] - a, b - t[m]) / 0.01, 0, 1)
        y[m] += rms / SHAPE_RMS * ramp * shape[m]
    return y


def noise_music(dur, rms=0.05, sr=SR, seed=2):
    """stereo white-noise 'music' at a steady RMS (long windows of it read the same level)"""
    return np.random.default_rng(seed).standard_normal((int(dur * sr), 2)) * rms


TONE_HZ = (200, 300, 500, 700, 1100, 1700)


def tone_music(dur, rms=0.05, sr=SR):
    """stereo 'music' of six tones that are all multiples of 100 Hz: every 10 ms block holds whole periods of each, so its RMS is the same in every
    10 ms block (to float rounding), which makes a level step or a corner in a bed made of it show as a step or a corner and nothing else"""
    t = np.arange(int(dur * sr)) / sr
    a = rms / np.sqrt(len(TONE_HZ) / 2)
    left = sum(a * np.sin(2 * np.pi * f * t + 0.7 * i) for i, f in enumerate(TONE_HZ))
    right = sum(a * np.sin(2 * np.pi * f * t + 1.9 * i + 0.4) for i, f in enumerate(TONE_HZ))
    return np.stack([left, right], axis=1)


def block_db(x, sr=SR, block_s=0.01):
    """the level in dB of every whole block of block_s of x (n,) or (n, ch), power mean over channels"""
    h = int(block_s * sr)
    k = len(x) // h
    y = np.asarray(x[:k * h], dtype=np.float64).reshape(k, h, -1)
    return 10 * np.log10(np.maximum((y ** 2).mean(axis=(1, 2)), 1e-20))


def clip(path, fmt="pcm_s16le", secs=0.5, rate=44100, ch=1, hz=440):
    """a sine clip made by ffmpeg in any wav sample format"""
    r = sh(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={hz}:d={secs}:r={rate}", "-ac", str(ch), "-c:a", fmt, path])
    assert r.returncode == 0, r.stderr
    return str(path)


def ffmpeg(*args):
    r = sh(["ffmpeg", "-v", "error", "-y", *args])
    assert r.returncode == 0, r.stderr


def mean_db(path):
    """ffmpeg's volumedetect mean volume of a file"""
    import re
    r = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "volumedetect", "-f", "null", "-"])
    return float(re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr).group(1))


def loudness(path):
    """(integrated LUFS, true peak dBTP) of a file, from ffmpeg's loudnorm measurement"""
    import json
    r = sh(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    d = json.loads(r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1])
    return float(d["input_i"]), float(d["input_tp"])
