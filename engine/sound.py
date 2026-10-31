#!/usr/bin/env python3
"""sound.py: everything about sound that is not the cut itself.

    sound.py downmix <media> <out.wav> [rate]                      one mono wav from a file's first audio track (48000 Hz unless a rate is given)
    sound.py master  <in.wav> <out.wav> [--i LUFS] [--tp dBTP] [--lra LU]    the voice master: clean-up chain and a two-pass loudnorm
    sound.py bed     <voice.wav> <music> <out.wav> [--under-speech 22] [--alone 14] [--ease 1.5] [--words words.json]
                                                                    the music bed under a voice (a mix, 48 kHz stereo)
    sound.py kit     [folder]                                       check that the sound kit has every family the sfx budget needs

downmix  A take with a dead or one-sided mic (a lav on the left, nothing on the right) must not be averaged: `-ac 1` adds the dead channel's
         noise into the voice and costs 6 dB. Every channel is measured, whatever the count; those 30 dB or more under the loudest are left out and
         the live ones alone are taken. When every channel is live they are averaged.
master   The voice chain (highpass, denoise, boxiness cut, presence, de-ess, compressor) and a TWO-PASS loudnorm. A single pass lands anywhere
         around the target and leaves no headroom for the AAC encode that follows; pass one measures the chain's output, pass two applies the
         measured values with linear=true. Target -16 LUFS, true peak -2.0 dBTP (0.5 dB of room for the encode, which the QC gate judges at -1.5).
bed      Music that sits under the talk and never pops up between lines. Levels are relative to the voice's own speaking level: `under_speech_db`
         under it while someone is talking (22 dB by default, an undertone: the voice leads), `alone_db` under it where no voice is present (14 dB:
         an open before the first word, an end card). Any pause shorter than 4 s is held at the under-speech level; the bed eases down over
         `ease_s` before a voice and back up over as long after one (a raised cosine in dB, so it never steps), and fades out at the end.
sfx      A budget rule for subtle sound effects (clicks, pops, punches, shutters): about 12 a minute at most, each placed in a gap, each a few
         dB over the speaker's speaking level at its loudest, off-centre sounds swapping sides so the voice keeps the middle. The kit is
         engine/sfx/ (synthesized by sfx_synth.py, nothing recorded or licensed) or the folder config.json "sfx_dir" names.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import re
import subprocess

import numpy as np

import common
from cut import Voice, fricative_end, fricative_start, levels_db, sound_runs, threshold

stop = common.stop
HERE = os.path.dirname(os.path.abspath(__file__))
USAGE = ("sound.py downmix <media> <out.wav> [rate] | master <in.wav> <out.wav> [--i LUFS] [--tp dBTP] [--lra LU] | "
         "bed <voice.wav> <music> <out.wav> [--under-speech 22] [--alone 14] [--ease 1.5] [--words words.json] | kit [folder]")


# =================================================================================================================================
# downmix
# =================================================================================================================================

DEAD_DB = 30.0


def channel_count(path):
    if not os.path.isfile(path): stop(f"file not found: {path}")
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except ValueError:
        stop(f"no audio stream in {path}")


def channel_means(path, n):
    """mean volume in dB of each of the n channels (-120 when a channel is digital silence)"""
    out = []
    for c in range(n):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af", f"pan=mono|c0=c{c},volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
        out.append(-120.0 if (not m or m.group(1) == "-inf") else float(m.group(1)))
    return out


def live_channels(means, dead_db=DEAD_DB):
    """the channels within dead_db of the loudest one, or None when every channel is live (or there is only one): those are averaged as before"""
    if len(means) < 2: return None
    top = max(means)
    live = [c for c, m in enumerate(means) if top - m < dead_db]
    return None if len(live) == len(means) else live


def filter_for(means, dead_db=DEAD_DB):
    """(ffmpeg audio filter or None, human note): the live channels alone, averaged, when some of the others are dead"""
    live = live_channels(means, dead_db)
    if live is None:
        return None, "averaged to mono"
    expr = "+".join(f"{1 / len(live):.6g}*c{c}" for c in live) if len(live) > 1 else f"c{live[0]}"
    dead = [c for c in range(len(means)) if c not in live]
    return f"pan=mono|c0={expr}", f"channel{'s' if len(live) > 1 else ''} {', '.join(map(str, live))} only (channel{'s' if len(dead) > 1 else ''} {', '.join(map(str, dead))} {'are' if len(dead) > 1 else 'is'} dead)"


def downmix(media, out, rate=48000):
    n = channel_count(media)
    note = "mono source"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", media, "-vn", "-map", "0:a:0"]
    if n >= 2:
        flt, note = filter_for(channel_means(media, n))
        cmd += ["-af", flt] if flt else ["-ac", "1"]
    else:
        cmd += ["-ac", "1"]
    cmd += ["-ar", str(rate), out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        stop(f"ffmpeg could not write {out}: {r.stderr.strip()[-200:]}")
    return note


# =================================================================================================================================
# the voice master
# =================================================================================================================================

SILENT_LUFS = -70.0   # loudnorm cannot normalise below this: a master that quiet is an empty one

CHAIN = ("highpass=f=80,afftdn=nf=-45:nr=12:tn=1,atrim=start=0.025,asetpts=PTS-STARTPTS,equalizer=f=250:t=q:w=1.2:g=-2,"
         "equalizer=f=3200:t=q:w=1:g=1.5,deesser=i=0.4,acompressor=threshold=-20dB:ratio=2.5:attack=6:release=110:makeup=2")


def _why(stderr):
    """the last two whole lines ffmpeg said (never a string cut off mid-word)"""
    lines = [l.strip() for l in stderr.splitlines() if l.strip()]
    return " | ".join(lines[-2:]) or "ffmpeg printed nothing"


def measure(src, i, tp, lra):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", src, "-af", f"{CHAIN},loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if r.returncode != 0 or not m:
        stop(f"loudnorm pass 1 failed: {_why(r.stderr)}")
    j = json.loads(m.group(0))
    try:
        meas = {k: float(j[k]) for k in ("input_i", "input_lra", "input_tp", "input_thresh", "target_offset")}
    except (KeyError, ValueError):
        stop("loudnorm pass 1 measured nothing (is the file silent?)")
    if any(v != v or v in (float("inf"), float("-inf")) for v in meas.values()) or meas["input_i"] < SILENT_LUFS:
        stop(f"{src} is silent or too short to master: the voice chain measured {j['input_i']} LUFS (loudnorm needs about a second of speech above {SILENT_LUFS:g} LUFS)")
    return meas


def master(src, out, i=-16.0, tp=-2.0, lra=8.0):
    m = measure(src, i, tp, lra)
    ln = (f"loudnorm=I={i}:TP={tp}:LRA={lra}:measured_I={m['input_i']}:measured_LRA={m['input_lra']}:measured_TP={m['input_tp']}"
          f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af", f"{CHAIN},{ln},aresample=48000", "-ac", "1", "-ar", "48000", out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        stop(f"loudnorm pass 2 failed: {_why(r.stderr)}")
    return m


# =================================================================================================================================
# the music bed: the track trimmed to its sounding body, looped end to start with an equal-power crossfade, and the envelope it plays at
# =================================================================================================================================

# A music file often opens on a few seconds of near silence and fades out over its last seconds; looped raw it would leave a hole in the bed of
# any video longer than the file. So the body is found (span), looped with an equal-power crossfade (loop), and given an envelope (envelope)
# that holds the bed under the talk, steady between lines, easing over a voice that music alone leads into.
QUIET_DB = 25    # a leading or trailing stretch this far under the track's median 1-second RMS is not part of the body
XFADE_S = 1.5    # the crossfade at each loop seam
FRAME_S = 0.05   # how finely the body's edges are found
DUCK_DB = 8.0    # the default distance between the bed alone and the bed under talk (under_speech_db 22 minus alone_db 14)
EASE_S = 1.5     # it eases down over this long before a voice that music alone leads into, and back up over as long after one
HOLD_S = 4.0     # no voice for less than this is a pause between lines, and the bed holds under it (pauses between lines are almost always shorter: the longest in a large sample of talking-head videos was 3.86 s)
LEAD_S = 0.15    # where the voice cannot say, a stretch of talk starts this far before its first word's aligned start
TAIL_S = 0.45    # and ends this far after its last word's aligned end (an aligner can end a soft word 0.36 s early)
EDGE_MAX_S = 0.6  # the voice moves an edge this far from the aligned time at most

def _db(x):
    return 20 * np.log10(np.maximum(x, 1e-12))


def _rms(M, n):
    """the RMS of each whole n-sample block of M (samples x channels)"""
    k = len(M) // n
    return np.sqrt((M[:k * n].reshape(k, n, -1) ** 2).mean(axis=(1, 2)))


def span(M, sr, quiet_db=QUIET_DB):
    """(first, last) sample of the track's sounding body: M (samples x channels) without its leading and trailing stretches more than quiet_db
    under its median 1-second RMS. A track shorter than a second, or with nothing above that line, is all body"""
    if len(M) < sr: return 0, len(M)
    line = np.median(_db(_rms(M, sr))) - quiet_db
    h = max(1, int(sr * FRAME_S))
    on = np.nonzero(_db(_rms(M, h)) > line)[0]
    if not len(on): return 0, len(M)
    return int(on[0] * h), (len(M) if on[-1] == len(M) // h - 1 else int((on[-1] + 1) * h))   # the last full frame sounds: keep the remainder too


def loop(M, n, sr, xfade_s=XFADE_S):
    """n samples of M (samples x channels), M repeated end to start as often as it takes, each seam an equal-power crossfade (the outgoing
    tail on a cosine, the incoming head on a sine: uncorrelated music holds its level through it). M at least n long is just cut to n"""
    if len(M) >= n: return M[:n].copy()
    if not len(M): return np.zeros((n,) + M.shape[1:])
    x = min(int(xfade_s * sr), len(M) // 3)   # a body shorter than 4.5 s crossfades over a third of itself
    th = (np.arange(x) + 0.5) / max(x, 1) * np.pi / 2
    fo, fi = np.cos(th)[:, None], np.sin(th)[:, None]
    out = np.empty((n + len(M),) + M.shape[1:])
    out[:len(M)] = M
    at = len(M)
    while at < n:
        out[at - x:at] = out[at - x:at] * fo + M[:x] * fi
        out[at:at + len(M) - x] = M[x:]
        at += len(M) - x
    return out[:n]


def talk(words, hold_s=HOLD_S):
    """[(first word, last word)] of each stretch of talk the bed holds steady under: words (dicts with start_s and end_s, in time order), any two
    with less than hold_s of no voice between them in one stretch"""
    out = []
    for w in words:
        if out and w["start_s"] - out[-1][1]["end_s"] < hold_s:
            if w["end_s"] >= out[-1][1]["end_s"]: out[-1][1] = w
        else:
            out.append([w, w])
    return [(a, b) for a, b in out]


def edges(runs, voice=None, audio_s=float("inf")):
    """[(onset, decay)] of each stretch of talk (talk()): where its first word's voice begins and where its last word's voice has decayed (a voice
    starts in the gap before its first word and ends after its last word's decay). Read on the voice when one is given (voice: a Voice of the audio),
    because the aligner starts 25 to 40 ms into words and ends early on soft endings, and never more than EDGE_MAX_S from the aligned times. Where
    the voice cannot say, LEAD_S before the first word and TAIL_S after the last; where it runs on into another sound, as far as an edge may go"""
    out = []
    for w0, w1 in runs:
        t0, t1 = w0["start_s"], w1["end_s"]
        a, b = t0 - LEAD_S, t1 + TAIL_S
        if voice is not None and t0 > 0.02:
            s, how = voice.start_of(t0, (min(t0, w0["end_s"] - 0.1), w0["end_s"]), max(0.0, t0 - EDGE_MAX_S), t0, fricative_start(w0.get("text", "")))
            a = t0 - EDGE_MAX_S if how == "runs on" else min(a, s) if how == "word" and s is not None else a
        if voice is not None and audio_s - t1 > 0.02:
            e, how = voice.end_of(t1, (w1["start_s"], max(t1, w1["start_s"] + 0.1)), min(audio_s, t1 + EDGE_MAX_S), t1, fricative_end(w1.get("text", "")))
            b = t1 + EDGE_MAX_S if how == "runs on" else max(t1, e) if how == "word" and e is not None else t1 if how == "pause" else b
        out.append((max(a, t0 - EDGE_MAX_S, 0.0), min(b, t1 + EDGE_MAX_S, audio_s)))
    return out


def envelope(n, sr, spans, duck_db=DUCK_DB, ease_s=EASE_S):
    """the bed's gain per sample, n samples at sr: 1 where music plays alone and 10 ** (-duck_db / 20) through each (onset, decay) span, eased down
    over the ease_s before it and back up over the ease_s after it. The ease is a raised cosine in dB: the bed never steps, and an ease leaves and
    lands with no corner"""
    e = max(ease_s, 1e-3)
    k = np.zeros(n)
    for a, b in spans:
        i0, i1 = max(0, int((a - e) * sr)), min(n, int((b + e) * sr) + 2)      # outside this window the span's ease is 0: only the window is worked out
        if i1 <= i0:
            continue
        t = np.arange(i0, i1) / sr
        x = np.clip(np.minimum(t - (a - e), (b + e) - t) / e, 0, 1)
        k[i0:i1] = np.maximum(k[i0:i1], 0.5 - 0.5 * np.cos(np.pi * x))
    return 10 ** (-duck_db * k / 20)


def alone(spans, start, end, ease_s=EASE_S, min_s=1.0):
    """[(a, b)] the stretches of [start, end] where music plays alone at its full level: clear of every (onset, decay) span and its eases, min_s
    long at least"""
    out, at = [], start
    for a, b in sorted(spans):
        if min(a - ease_s, end) - at >= min_s: out.append((at, min(a - ease_s, end)))
        at = max(at, b + ease_s)
    if end - at >= min_s: out.append((at, end))
    return out


_EBU = re.compile(r"\bt:\s*([\d.]+).*?\bM:\s*(-?[\d.]+|-?inf)")


def loudness(log, windows, block_s=0.4):
    """the loudness (LUFS) of the (a, b) windows of a file, from ffmpeg's ebur128 log of it (a momentary reading every 100 ms, of the 400 ms before
    it): the power mean of the readings whose block lies inside a window, readings under -70 left out. None with no reading"""
    p = []
    for m in _EBU.finditer(log):
        t, v = float(m.group(1)), m.group(2)
        if "inf" in v or float(v) < -70: continue
        if any(a <= t - block_s and t <= b for a, b in windows): p.append(10 ** (float(v) / 10))
    return float(10 * np.log10(np.mean(p))) if p else None


def _decode(path, channels, sr=None):
    """a file's first audio stream as float32 samples, shape (n, channels), at `sr` (48 kHz), through ffmpeg"""
    sr = sr or SR
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        stop(f"ffmpeg cannot read the audio of {path}: {r.stderr.decode(errors='replace').strip()[-160:] or 'no audio'}")
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, channels).copy()


def _rms_db(x):
    """the power-mean level of a block of samples in dBFS"""
    return float(10 * np.log10(np.mean(np.square(np.asarray(x, dtype=np.float64))) + 1e-12))


def voice_spans(y, sr=None, hold_s=HOLD_S):
    """([(onset, decay)], [(start, end)]) for a mono voice track: the stretches of talk the bed holds steady under, and the sounding runs they are
    made of. A run is speech on the cutter's level scale (a hop 12 dB over the room); runs closer than hold_s are one stretch; a stretch starts LEAD_S
    before its first sound and ends TAIL_S after its last (a voice starts in the gap before its first word and ends after its last word's decay)."""
    sr = sr or SR
    dur = len(y) / sr
    db = levels_db(y, sr)
    runs = sound_runs(db, threshold(db, 0.0, dur), 0.0, dur)
    talk_ = []
    for a, b in runs:
        if talk_ and a - talk_[-1][1] < hold_s:
            talk_[-1][1] = max(talk_[-1][1], b)
        else:
            talk_.append([a, b])
    return [(max(0.0, a - LEAD_S), min(dur, b + TAIL_S)) for a, b in talk_], runs


def _write_stereo(path, mix, sr=None):
    sr = sr or SR
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", "-", "-c:a", "pcm_s16le", str(path)],
                       input=np.ascontiguousarray(mix, dtype=np.float32).tobytes(), capture_output=True)
    if r.returncode != 0:
        stop(f"ffmpeg could not write {path}: {r.stderr.decode(errors='replace').strip()[-160:]}")


def bed(voice_wav, music_file, out_wav, under_speech_db=22.0, alone_db=14.0, ease_s=EASE_S, words=None):
    """Mix a music bed under a voice and write `out_wav` (48 kHz stereo). Returns the report: the levels asked for and the levels measured.

    The levels are relative to the voice's own speaking level: its power-mean level over the sounding stretches of speech. The bed sits
    `under_speech_db` under it where someone is talking and `alone_db` under it where music plays alone (before the first word, between
    stretches of talk more than HOLD_S apart, after the last). Where the voice is, the stretches come from the voice itself (voice_spans), or from
    `words` ([{"start", "end", "text"}] in the voice's time) read on the voice's own onsets and decays (talk, edges). The bed eases between the two
    levels over `ease_s`, never steps, and fades out over its last ease_s."""
    if under_speech_db < alone_db:
        stop(f"the bed cannot sit louder under the talk ({under_speech_db:g} dB under the voice) than alone ({alone_db:g} dB under it)")
    duck_db = under_speech_db - alone_db
    voice = _decode(voice_wav, 2)
    mono = voice.mean(axis=1)
    n = len(mono)
    if n < SR // 2:
        stop(f"{voice_wav} is too short to put a bed under")
    if words:
        ws = [{"start_s": float(w["start"]), "end_s": float(w["end"]), "text": w.get("text", "")} for w in words]
        spans = edges(talk(ws), Voice(mono, SR), n / SR)
        runs = voice_spans(mono)[1]
    else:
        spans, runs = voice_spans(mono)
    talking = np.zeros(n, bool)
    for a, b in runs:
        talking[int(a * SR):int(b * SR)] = True
    v_db = _rms_db(mono[talking]) if talking.any() else _rms_db(mono)
    music = _decode(music_file, 2)
    lo, hi = span(music, SR)
    track = loop(music[lo:hi], n, SR)
    m_db = _rms_db(track)
    if m_db < -80:
        stop(f"the music file {music_file} is silent")
    gain_db = (v_db - alone_db) - m_db            # the bed at its alone level sits alone_db under the voice
    env = envelope(n, SR, spans, duck_db, ease_s)
    fade = np.ones(n)
    k = min(n, int(ease_s * SR))
    fade[-k:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(k) / max(k, 1))      # out over the last ease_s
    k0 = min(n, int(0.05 * SR))
    fade[:k0] *= np.sin(0.5 * np.pi * np.arange(k0) / max(k0, 1))         # in over 50 ms: no click on the first sample
    placed = track * (env * fade * 10 ** (gain_db / 20))[:, None]
    mix = voice + placed
    peak = float(np.abs(mix).max())
    scaled = peak > 0.999
    if scaled:
        mix *= 0.999 / peak                        # the voice master leaves 2 dB of headroom, so this is a rare safety, never a level change worth hearing
    _write_stereo(out_wav, mix)
    free = alone(spans, 0.0, n / SR, ease_s)
    in_free = np.zeros(n, bool)
    for a, b in free:
        in_free[int(a * SR):int(b * SR)] = True
    return {"voice_db": round(v_db, 1), "music_db": round(m_db, 1), "gain_db": round(gain_db, 1), "duck_db": duck_db,
            "under_speech_db": under_speech_db, "alone_db": alone_db, "ease_s": ease_s,
            "under_speech_db_measured": round(v_db - _rms_db(placed[talking]), 1) if talking.any() else None,
            "alone_db_measured": round(v_db - _rms_db(placed[in_free]), 1) if in_free.any() else None,
            "spans": [[round(a, 3), round(b, 3)] for a, b in spans], "alone_windows": [[round(a, 3), round(b, 3)] for a, b in free],
            "peak_scaled": scaled}



# =================================================================================================================================
# sfx budget: a few subtle sounds, spent where they help
# =================================================================================================================================

SR, GAP = 48000, 1.0
LEVEL_DEFAULT = 0.1   # -20 dBFS: the speaking level when there is nothing to measure
FAM = {"punch": 2.5, "click": 4.5, "shutter": 4.5, "pop": 3.5}   # dB over the speaking level, at the louder side
# Named sounds a scene asks for ("sfx": "<name>") play 3 dB over unless listed here. These are long or loud, so they sit lower. They stay
# out of FAM on purpose: every family in FAM must be in the kit, and a kit is free to leave these out.
EXTRA_DB = {"riser": 1.0, "swell": 0.5, "sweep": 1.5, "drop": 1.0, "reverse": 2.0, "glitch": 1.5,
            "airhorn": 1.0, "applause": 1.0, "crickets": 0.0, "drumroll": 1.0, "sneaky-walk": 0.0}
TIERS = {0: "flash cuts + payoff", 1: "entrances", 2: "arrivals", 3: "landings", 4: "rows"}
PACK = os.path.join(HERE, "sfx")   # the synthesized kit that ships with the engine (sfx_synth.py writes it)


def sfx_dir():
    """the folder of extra sounds: config.json "sfx_dir" (empty when there is none)"""
    return str(support.config().get("sfx_dir") or "")


def load_clip(p):
    """mono, 48 kHz, peak 1: the level is set per family at the mix. ffmpeg decodes (24-bit and float wavs included)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        stop(f"ffmpeg cannot read the sound {p}: {r.stderr.decode(errors='replace').strip()[-160:]}")
    y = np.frombuffer(r.stdout, dtype=np.float32).copy()
    if not len(y):
        stop(f"the sound {p} is empty")
    return y / (np.abs(y).max() + 1e-9)


def kit(families, bank=None):
    """{family: [clips]} from numbered variants (<family>-1.wav ...), or a single <family>.wav, looked up in the bank (None: config.json "sfx_dir";
    "": no bank), then in the engine's own sfx/ folder. Stops with one line naming every requested family that has no clip, and says so when the
    whole kit is empty: a silent mix with exit 0 is never an answer."""
    if bank is None:
        bank = sfx_dir()
    dirs = [d for d in dict.fromkeys(x for x in (bank, PACK) if x) if os.path.isdir(d)]
    out, missing = {}, []
    for n in sorted(families):
        for d in dirs:
            vs = sorted(f for f in os.listdir(d) if re.match(rf"^{re.escape(n)}-\d+\.wav$", f)) or ([f"{n}.wav"] if os.path.exists(os.path.join(d, f"{n}.wav")) else [])
            if vs:
                out[n] = [load_clip(os.path.join(d, v)) for v in vs]
                break
        else:
            missing.append(n)
    if not out:
        stop(f"the sound kit is empty: none of {', '.join(sorted(families))} found in {', '.join(dirs) or 'any sound folder'} (run sfx_synth.py, or set sfx_dir in config.json)")
    if missing:
        stop(f"no sound for {', '.join(missing)} in {', '.join(dirs)}: add the files or take the name out of the spec")
    return out


class Plan:
    """candidate sounds: want(t, tier, (family, dB, centred), ...); lo/hi keep a sound inside its scene. Every family is in the kit (kit() stops otherwise)."""
    def __init__(self, K):
        self.K, self.cand = K, []

    def want(self, t, tier, *snd, lo=None, hi=None):
        if snd and isinstance(t, (int, float)) and 0 <= t < 9000 and (lo is None or t >= lo - 0.05) and (hi is None or t <= hi):
            self.cand.append((round(t, 3), tier, snd))


def pick(cand, budget, gap=GAP):
    ev = []
    for p in sorted({c[1] for c in cand}):
        pool = sorted((c for c in cand if c[1] == p), key=lambda c: c[0])
        while pool and len(ev) < budget:
            far = lambda c: min((abs(c[0] - e[0]) for e in ev), default=1e9)
            best = max(pool, key=lambda c: (far(c), -c[0]))   # spread: the gap farthest from every sound so far
            if far(best) < gap: break
            ev.append(best); pool.remove(best)
    return sorted(ev)


def span_windows(spans, n, hop):
    """start sample of every whole hop-long window inside the spoken spans, each span clamped to the n decoded samples. WhisperX can stamp a last
    word past the end of the audio, and a window with no samples averages to NaN, which would silence every sound the level scales. A span that is
    empty, backwards or not a number gives no windows."""
    for a, b in spans:
        try:
            lo, hi = max(0, int(float(a) * SR)), min(int(float(b) * SR), n)
        except (TypeError, ValueError, OverflowError):
            continue
        yield from range(lo, hi - hop, hop)


def speaking_level(media, spans=None):
    """80th percentile of 20 ms RMS inside the spoken spans [(a, b)] (each clamped to the audio); with no spans, of the windows within 20 dB of the
    loudest speech. Always a finite number: LEVEL_DEFAULT (0.1, -20 dBFS) with a WARN line on stderr when fewer than 21 windows are left to measure."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True).stdout
    v = np.frombuffer(raw, dtype=np.float32); hop = int(0.02 * SR)
    if spans:
        r = np.array([np.sqrt(np.mean(v[i:i + hop] ** 2)) for i in span_windows(spans, len(v), hop)])
    else:
        r = np.array([np.sqrt(np.mean(v[i:i + hop] ** 2)) for i in range(0, len(v) - hop, hop)])
        r = r[r > 0.1 * np.percentile(r, 95)] if len(r) else r
    r = r[np.isfinite(r)]
    if len(r) > 20:
        return float(np.percentile(r, 80))
    print(f"WARN: sfxbudget.speaking_level: {len(r)} windows of speech to measure in {media} (it needs more than 20), so the level is the fixed "
          f"{LEVEL_DEFAULT} ({20 * np.log10(LEVEL_DEFAULT):.0f} dBFS)", file=sys.stderr)
    return LEVEL_DEFAULT


def mix(ev, K, n, vref, level=0.0):
    """(n, 2) float array of the picked sounds"""
    yl, yr, cnt, side = np.zeros(n), np.zeros(n), {}, 1
    for t0, p, snd in ev:
        side = -side   # off-centre sounds swap sides event to event
        for fam, d_, centred in snd:
            i = cnt.get(fam, 0); cnt[fam] = i + 1
            clip = K[fam][i % len(K[fam])]   # never the same sound twice in a row
            k_ = clip[:max(0, n - int(t0 * SR))] * vref * 10 ** ((FAM.get(fam, EXTRA_DB.get(fam, 3)) + d_ + level - (3 if p >= 3 else 0)) / 20)
            gl, gr = (1, 1) if centred else ((0.42, 1) if side > 0 else (1, 0.42))
            s0 = int(t0 * SR); yl[s0:s0 + len(k_)] += k_ * gl; yr[s0:s0 + len(k_)] += k_ * gr
    return np.stack([yl, yr], 1)


def report(ev, cand, per_min, vref, length_s, **extra):
    """(sfx.json dict, one-line summary)"""
    j = {"budget_per_min": per_min, "speech_dbfs": round(20 * np.log10(vref), 1), **extra,
         "sounds": [{"t": t0, "tier": TIERS[p], "kit": [n for n, _, _ in snd]} for t0, p, snd in ev],
         "silent": [{"t": c[0], "tier": TIERS[c[1]]} for c in cand if c not in ev]}
    line = (f"sfx {len(ev)} = {len(ev) / (max(length_s, 1) / 60):.0f} a minute (budget {per_min:g}), level {20 * np.log10(vref):.1f} dBFS speech: "
            + "  ".join(f"{TIERS[p]} {sum(e[1] == p for e in ev)}/{sum(c[1] == p for c in cand)}" for p in sorted({c[1] for c in cand})))
    return j, line


# =================================================================================================================================
# Command line
# =================================================================================================================================

def _opts(argv, names, cast=float):
    """pop `--name value` pairs out of argv: {name: value or None}"""
    out = {}
    for n in names:
        if n in argv:
            i = argv.index(n)
            out[n] = cast(argv[i + 1])
            del argv[i:i + 2]
    return out


def _main():
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "downmix":
        print(f"downmix {a[1]}: {downmix(a[0], a[1], int(a[2]) if len(a) > 2 else 48000)}")
    elif cmd == "master":
        o = _opts(a, ("--i", "--tp", "--lra"))
        m = master(a[0], a[1], o.get("--i", -16.0), o.get("--tp", -2.0), o.get("--lra", 8.0))
        print(f"master {a[1]}: chain in {m['input_i']:.1f} LUFS / {m['input_tp']:.1f} dBTP -> target {o.get('--i', -16.0):g} LUFS, TP {o.get('--tp', -2.0):g}")
    elif cmd == "bed":
        o = _opts(a, ("--under-speech", "--alone", "--ease"))
        wp = _opts(a, ("--words",), cast=str).get("--words")
        words = json.load(open(wp))["words"] if wp else None
        r = bed(a[0], a[1], a[2], o.get("--under-speech", 22.0), o.get("--alone", 14.0), o.get("--ease", EASE_S), words)
        print(f"bed {a[2]}: voice {r['voice_db']} dB, bed {r['under_speech_db_measured']} dB under the talk and "
              f"{r['alone_db_measured']} dB under the voice alone (asked {r['under_speech_db']:g} and {r['alone_db']:g}), {len(r['spans'])} stretches of talk")
    elif cmd == "kit":
        k = kit(set(FAM), a[0] if a else None)
        print("kit ok: " + ", ".join(f"{f} x{len(c)}" for f, c in sorted(k.items())))
    else:
        stop(f"unknown command {cmd!r}: usage: {USAGE}")


if __name__ == "__main__":
    support.cli(_main, USAGE)
