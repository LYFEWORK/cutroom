#!/usr/bin/env python3
"""sfx_synth.py [outdir]

Synthesizes the engine's sound kit from noise and envelopes: nothing is recorded, downloaded or licensed. The default folder is engine/sfx/, the
one sound.py's kit() looks in. Every file is a 48 kHz mono wav at a peak of -6 dBFS; the mix puts each one far under the voice (sound.py, "sfx").

The four families the sfx budget asks for, three variants each so a run never repeats one sound:
    click-1..3     a crisp UI or camera-button click: a 2 ms noise tick and a short decaying tone
    pop-1..3       a small pop: a sine that drops in pitch over about 80 ms
    punch-1..3     a soft low punch: a falling low sine under a muffled thump
    shutter-1..3   a camera shutter: two clicks a few tens of ms apart and a burst of band noise
and the older family a scene can ask for by name:
    whoosh-1..4, whoosh, whoosh-out, swish-1..2, swish, tick-1..2, tick, thud

Run it again and the files come out byte for byte the same (a fixed seed).
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import os
import wave

import numpy as np

import common

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 48000


def _write(outdir, name, y):
    y = np.asarray(y, dtype=np.float64)
    y = y / (np.max(np.abs(y)) + 1e-9) * 0.5            # peak -6 dBFS
    with wave.open(os.path.join(outdir, name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype(np.int16).tobytes())


def _pink(rng, n):
    """pink-ish noise: white noise through a fixed IIR filter, unit variance"""
    w = rng.standard_normal(n)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1, -2.494956002, 2.017265875, -0.522189400]
    y = np.zeros(n)
    x1 = x2 = x3 = y1 = y2 = y3 = 0.0
    for i in range(n):
        xi = w[i]
        yi = b[0] * xi + b[1] * x1 + b[2] * x2 + b[3] * x3 - a[1] * y1 - a[2] * y2 - a[3] * y3
        x3, x2, x1 = x2, x1, xi
        y3, y2, y1 = y2, y1, yi
        y[i] = yi
    return y / (np.std(y) + 1e-9)


def _bandsweep(rng, n, f0, f1, q=1.2):
    """noise through a band-pass whose centre sweeps f0 to f1 (the biquad is recomputed every 64 samples)"""
    x = _pink(rng, n)
    y = np.zeros(n)
    z1 = z2 = 0.0
    for s in range(0, n, 64):
        fc = f0 * (f1 / f0) ** (s / n)
        w0 = 2 * np.pi * fc / SR
        al = np.sin(w0) / (2 * q)
        b0, b2 = al, -al
        a0, a1, a2 = 1 + al, -2 * np.cos(w0), 1 - al
        for i in range(s, min(n, s + 64)):
            xi = x[i]
            yi = (b0 * xi + b2 * (x[i - 2] if i > 1 else 0) - a1 * z1 - a2 * z2) / a0
            z2, z1 = z1, yi
            y[i] = yi
    return y


def _env(n, a, shape=2.0):
    t = np.linspace(0, 1, n)
    return np.where(t < a, (t / a) ** 1.5, ((1 - t) / (1 - a)) ** shape)


def _lowpass(x, fc):
    """one-pole low-pass: a muffled copy of x"""
    k = 1 - np.exp(-2 * np.pi * fc / SR)
    y = np.zeros(len(x))
    s = 0.0
    for i, v in enumerate(x):
        s += k * (v - s)
        y[i] = s
    return y


def _click(rng, dur, hz, decay, noise_s=0.002):
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * hz * t) * np.exp(-t * decay) + 0.45 * np.sin(2 * np.pi * hz * 1.9 * t) * np.exp(-t * decay * 1.6)
    k = int(noise_s * SR)
    y[:k] += rng.standard_normal(k) * 0.9
    return y


def _pop(dur, f0, f1, decay):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t * 38)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * decay)


def _punch(rng, dur, f0, f1, thump):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t * 22)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 15)
    k = int(0.012 * SR)
    hit = _lowpass(rng.standard_normal(n), 900) * np.exp(-t * 90)
    hit[k:] = hit[k:] * np.exp(-np.arange(n - k) / SR * 120)
    return body + thump * hit / (np.max(np.abs(hit)) + 1e-9)


def _shutter(rng, gap_s, dur=0.16):
    n = int(dur * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for start, hz in ((0.0, 2600), (gap_s, 1900)):
        i = int(start * SR)
        c = _click(rng, 0.05, hz, 120, noise_s=0.0015)
        y[i:i + len(c)] += c[:max(0, n - i)]
    hiss = _bandsweep(rng, n, 2500, 6500, 1.0) * np.exp(-t * 28) * 0.18
    return y + hiss


def synth(outdir=None):
    """write the whole kit into outdir (engine/sfx by default); returns the sorted list of file names"""
    outdir = outdir or os.path.join(HERE, "sfx")
    os.makedirs(outdir, exist_ok=True)
    rng = np.random.default_rng(7)
    wr = lambda name, y: _write(outdir, name, y)
    # the four families the sfx budget asks for
    for i, (hz, decay) in enumerate(((3100, 260), (2500, 220), (3700, 300)), 1):
        wr(f"click-{i}.wav", _click(rng, 0.07, hz, decay))
    for i, (f0, f1, decay) in enumerate(((760, 260, 36), (640, 220, 30), (880, 300, 42)), 1):
        wr(f"pop-{i}.wav", _pop(0.11, f0, f1, decay))
    for i, (f0, f1, thump) in enumerate(((150, 52, 0.55), (130, 48, 0.7), (170, 58, 0.45)), 1):
        wr(f"punch-{i}.wav", _punch(rng, 0.22, f0, f1, thump))
    for i, gap in enumerate((0.032, 0.045, 0.026), 1):
        wr(f"shutter-{i}.wav", _shutter(rng, gap))
    # whooshes: four cousins (length, sweep and attack differ) so a run of entrances never repeats one sound
    for i, (dur, f0, f1, q, a, sh) in enumerate([(0.55, 300, 2400, 1.2, 0.35, 2.2), (0.48, 420, 1800, 1.0, 0.30, 2.0), (0.62, 240, 3000, 1.4, 0.40, 2.4), (0.42, 600, 2600, 1.1, 0.28, 1.9)], 1):
        n = int(dur * SR)
        wr(f"whoosh-{i}.wav", _bandsweep(rng, n, f0, f1, q) * _env(n, a, sh))
    n = int(0.55 * SR)
    wr("whoosh.wav", _bandsweep(rng, n, 300, 2400) * _env(n, 0.35, 2.2))
    n = int(0.28 * SR)
    wr("swish-1.wav", _bandsweep(rng, n, 900, 4200, 1.6) * _env(n, 0.25, 2.6))
    n = int(0.24 * SR)
    wr("swish-2.wav", _bandsweep(rng, n, 1200, 3400, 1.3) * _env(n, 0.22, 2.4))
    n = int(0.28 * SR)
    wr("swish.wav", _bandsweep(rng, n, 900, 4200, 1.6) * _env(n, 0.25, 2.6))
    n = int(0.42 * SR)
    wr("whoosh-out.wav", _bandsweep(rng, n, 2400, 300) * _env(n, 0.2, 1.8))
    # tick: a glass tap, three short decaying sines and a click
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    tick = sum(np.sin(2 * np.pi * f * t) * np.exp(-t * k) * g for f, k, g in ((1850, 60, 1.0), (2740, 90, 0.5), (4100, 140, 0.25)))
    tick[:int(0.002 * SR)] += rng.standard_normal(int(0.002 * SR)) * 0.6
    wr("tick.wav", tick)
    wr("tick-1.wav", tick)
    n2 = int(0.10 * SR)
    t2 = np.arange(n2) / SR
    wr("tick-2.wav", sum(np.sin(2 * np.pi * f * t2) * np.exp(-t2 * k) * g for f, k, g in ((1520, 70, 1.0), (2260, 100, 0.5), (3300, 150, 0.25))))
    # thud: a low sine drop, 120 to 55 Hz, for a big landing
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    f = 120 * (55 / 120) ** (t / t[-1])
    wr("thud.wav", np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 14))
    return sorted(f for f in os.listdir(outdir) if f.endswith(".wav"))


def _main():
    out = sys.argv[1] if len(sys.argv) > 1 else None
    names = synth(out)
    print(f"sfx kit: {len(names)} files -> {out or os.path.join(HERE, 'sfx')}")


if __name__ == "__main__":
    support.cli(_main, "sfx_synth.py [outdir]")
