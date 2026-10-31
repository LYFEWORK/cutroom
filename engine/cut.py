#!/usr/bin/env python3
"""cut.py: the cutter. It turns one take and its word timings into an edited cut in which no spoken word is ever clipped.

    cut.py cut       <plan.json>                                                  cut the take: the cut, its EDL, QC card and Premiere XML
    cut.py joints    <cut.mp4> <cut.edl.json> <words.json> <joints.json>          check every joint for a clipped word (exit 0 PASS, 1 FAIL)
    cut.py ladder    <plan.json> <joints.json> <cut.edl.json> [<previous edl>]    widen the cut edges after a FAIL (exit 3: nothing left to try)
    cut.py sentences <words.json> <sentences.json>                                the take's sentences, each with the words it covers
    cut.py plan      <words.json> <sentences.json> <name> <root> <workdir> [--hook-first N] [--faceless]   write plan.json and words.wx.json
    cut.py wx        <words.json> <words.wx.json>                                 words.json reshaped for the cutter
    cut.py check-plan <plan.json>                                                 resolve every segment and strike before cutting (exit 1 on a failure)

Why no word is clipped: four guards, all on.
  1. Every cut edge goes on the voice, not on the aligned word time (snap_edges). An aligner ends soft words early and starts words late, so
     each end moves on to where its last word's voice has decayed and each start back to where its first word's voice begins, read on the
     source's own 10 ms level and its 2.5 to 7 kHz band, never into a struck word's sound or the next word, at most 0.5 s. Two pieces of one
     take that the voice runs straight through are joined. An edge placed by hand ("edges_exact") is left alone.
  2. The QC card lists what it could not settle for an independent listener: edges_in_voice (an edge still inside the voice) and joint_words
     (every word the cut's own transcript hears differently within 0.6 s of a joint).
  3. The joint check compares each piece's edges with the source words (geometry), re-reads an edge word off the waveform when the stamps and
     the sound disagree, and never counts a struck word as a played one.
  4. The retry ladder: a FAIL widens every edge by 0.04 s (lead_extra for heads, tail_extra for tails, 0.12 s at most) and cuts again, three
     tries, and stops at once when a retry cannot move the failed edge.

plan.json (every key but src, words_json, output and segments is optional):
  src, words_json, output     the take (a file with audio), its words (words.wx.json or a whisper.cpp json) and the cut to write
  fps 30                      the frame grid every edge lands on
  pad_pre .06, pad_post .07   head and tail pad of a segment's first and last piece; split_pre .06, split_post .08 inside a segment
  gap_max .40, split_sil .45  a measured pause of this length is cut out (a talking-head plan uses .20 and .25)
  sil_noise_db -35            level under which silence is measured; qc_noise_db is the same for the QC card
  drop_fillers true           um, uh and the rest of FILLERS are struck; a segment's keep_fillers true keeps its own
  profanity "off"             "flag" makes a swear in the cut's transcript a CHECK on the QC card
  right_mode "off"            "off" never strips a speaker's "right?"; "most" strikes every tag-"right?" and keeps the content ones
                              ("all right", "right about here"); "pocket" strikes one with a pause on both sides. keep_right and force_right
                              are lists of source seconds that override "most"
  cut_ranges [[t0, t1]]       source time to excise even when the aligner merged the stutter into one token
  refine_joints true          listen to every joint with the speech engine and bisect until neighbour words survive (the planner writes false)
  collapse_interior true      cut every measured pause of split_sil or more inside a piece
  audio_clean true            highpass, denoise, compressor and loudnorm on the cut's audio (the planner writes false: the master is made later)
  lead_extra, tail_extra      what the retry ladder writes
  xml_src, xml_fps, src_w, src_h   point the Premiere XML at the raw camera file instead of the working copy
  segments [{"match", "occurrence", "label", "drop", "keep_fillers", "span", "edges_exact", "collapse"}]
                              match is the verbatim words to keep (a contiguous run of the transcript); drop lists words struck inside the
                              segment, each a whole-word match: "um," never matches inside "number,". An entry is a string, {"text",
                              "keep_last_words"} or {"text", "start"} (the struck word's own start time, which picks the exact one when
                              the text repeats). span [t0, t1] sets the source times by hand instead of matching words
Writes next to `output`: <cut>.edl.json (the pieces, and "dropped": the words struck, which the joint check never counts as played),
<cut>.qc.json, <cut>.words.json (the QC re-transcription), <cut>.xml (a Premiere/FCP7 timeline on the source) and the cut itself.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import difflib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import wave
from xml.sax.saxutils import escape as esc

import numpy as np

import common

stop = common.stop
USAGE = ("cut.py cut <plan.json> | joints <cut.mp4> <cut.edl.json> <words.json> <joints.json> | ladder <plan.json> <joints.json> <cut.edl.json> [<previous edl>] | "
         "sentences <words.json> <sentences.json> | plan <words.json> <sentences.json> <name> <root> <workdir> [--hook-first N] [--faceless] | wx <words.json> <words.wx.json> | "
         "check-plan <plan.json>")


# =================================================================================================================================
# The voice: what counts as speech on the 10 ms level scale, in ONE place for the cutter and the joint checker.
#
# A hop is 10 ms of audio, its level the RMS in dBFS. Speech is a hop 12 dB over the room: the quietest tenth of the surrounding 1.2 s plus
# RISE_DB, never under FLOOR_DB (a very quiet room would call its own hiss speech) and never over CEIL_DB (in running speech with no pause
# the "room" is the speech itself, and a threshold above -38 would call a real word start silent).
#
#     levels_db(y, sr)                  the level of every hop
#     threshold(db, t0, t1)             the speech threshold for the stretch around [t0, t1]
#     sound_runs(db, thr, t0, t1)       the stretches at or above the threshold, small dips inside one word closed
#     pause_run(db, thr, t)             the stretch below the threshold that holds t, or None when t is on speech
#
# Where a word really stops and starts, for the cutter's edges (snap_edges) and its QC gate (edges_in_voice). The speech threshold above ends a
# soft word early: a soft "responsibility" ran 0.43 s past its aligned end at -36 to -50 dBFS, and the z of a quiet "holes" on a noisy range never
# rose out of the room on the broadband level at all, only in the 2.5 to 7 kHz band. A word is still sounding at a hop EDGE_RISE_DB over the
# broadband room or EDGE_HI_RISE_DB over that band's room, and within EDGE_DROP_DB of the word's own body.
#
#     Voice(y, sr)                      both levels of a source, worked out once
#     Voice.end_of(t, body, stop)       where the word sounding at (or just before) t has stopped
#     Voice.start_of(t, body, stop)     where the word sounding at t began
#     fricative_start(word), fricative_end(word)   whether a word may begin or end on a sound only the band hears
# =================================================================================================================================

HOP_S = 0.010
RISE_DB, FLOOR_DB, CEIL_DB = 12.0, -50.0, -38.0
WINDOW_S = 0.6          # the room is read from this far either side of the stretch being judged
CLOSE_S = 0.03          # a dip shorter than this inside a sound is a closure or a glottal dip, not a gap between two sounds

HI_BAND = (2500.0, 7000.0)   # Hz: where a fricative ending sounds once the broadband level has sunk into a noisy room
EDGE_ROOM_S = 1.5            # an edge's room is the quietest tenth of this much either side of it
EDGE_RISE_DB = 6.0           # a word's decay still sounds this far over the broadband room
EDGE_HI_RISE_DB = 6.0        # and its fricative ending this far over the 2.5 to 7 kHz band's room
EDGE_DROP_DB = 30.0          # but nothing more than this under the word's body (its loudest tenth) is the word: a breath, the room swelling
DIP_MAX_S = 0.09             # a quiet stretch shorter than this, then a short weak sound, is the word's own ending (the l of "holes" sinking before
                             # its z); the joint check's PAUSE_MIN, the shortest quiet it calls a pause
TAIL_RUN_MAX_S = 0.12        # that ending is a release or a fricative: this long at most. A filler or the next word is longer
TAIL_WEAK_DB = 10.0          # and at least this far under the word's body. A filler or the next word is as loud as speech
EDGE_HI_ROOM_S = 0.5         # the band's room is read this close to the edge: its hiss is not even across a room
EDGE_STEP_S = 0.05           # a start walked back further than this must sit after a pause (DIP_MAX_S of quiet), or it ran into running speech
LOUD_DB = 6.0                # a hop this far over the word's body and the speech around it is not the word: a shot, a clap, a bump on the mic


def levels_db(y, sr, hop=HOP_S):
    """dBFS per hop of a mono float signal"""
    n = int(hop * sr)
    m = len(y) // n
    return 20 * np.log10(np.sqrt((np.asarray(y[:m * n], dtype=np.float32).reshape(m, n) ** 2).mean(1)) + 1e-9)


def _span(db, t0, t1, hop=HOP_S):
    return max(0, int(t0 / hop)), min(len(db), int(t1 / hop) + 1)


def threshold(db, t0, t1, hop=HOP_S):
    """12 dB over the room (the quietest tenth of the 1.2 s around the stretch), between FLOOR_DB and CEIL_DB"""
    a, b = _span(db, t0 - WINDOW_S, t1 + WINDOW_S, hop)
    if b - a < 5:
        return FLOOR_DB
    return min(CEIL_DB, max(FLOOR_DB, float(np.percentile(db[a:b], 10)) + RISE_DB))


def sound_runs(db, thr, t0, t1, close_s=CLOSE_S, hop=HOP_S):
    """[(start_s, end_s)] of the stretches at or above `thr` inside [t0, t1]; two stretches with fewer than close_s of quiet between
    them are one"""
    a, b = _span(db, t0, t1, hop)
    close = round(close_s / hop)
    runs = []
    for i in range(a, b):
        if db[i] < thr:
            continue
        if runs and i - runs[-1][1] - 1 < close:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return [(r0 * hop, (r1 + 1) * hop) for r0, r1 in runs]


def pause_run(db, thr, t, hop=HOP_S):
    """(start_s, end_s) of the stretch below `thr` that holds t, or None when the hop at t is speech"""
    i = int(t / hop)
    if i < 0 or i >= len(db) or db[i] >= thr:
        return None
    a = i
    while a > 0 and db[a - 1] < thr:
        a -= 1
    b = i
    while b + 1 < len(db) and db[b + 1] < thr:
        b += 1
    return a * hop, (b + 1) * hop



def band_db(y, sr, hop=HOP_S, band=HI_BAND, win_s=0.02):
    """dB per hop of the energy in `band` (Hz), from a 20 ms Hann window centred on each hop: levels_db's hops, on a scale of its own (a hop is
    only ever compared with the same band's room). Worked out in blocks, so an hour of audio stays small"""
    n, w = int(hop * sr), int(win_s * sr)
    y = np.asarray(y, dtype=np.float32)
    m = len(y) // n
    if m == 0:
        return np.zeros(0, dtype=np.float32)
    f = np.fft.rfftfreq(w, 1 / sr)
    sel = (f >= band[0]) & (f < band[1])
    win = np.hanning(w).astype(np.float32)
    pad = np.concatenate([np.zeros(w // 2, np.float32), y, np.zeros(w, np.float32)])
    view = np.lib.stride_tricks.sliding_window_view(pad, w)
    starts = np.arange(m) * n + n // 2          # the window centred on the middle of hop i starts here in `pad`
    out = np.empty(m, dtype=np.float32)
    for a in range(0, m, 8192):
        p = (np.abs(np.fft.rfft(view[starts[a:a + 8192]] * win, axis=1)[:, sel]) ** 2).sum(1)
        out[a:a + 8192] = 10 * np.log10(p + 1e-12)
    return out


class Voice:
    """A source's broadband level (levels_db) and its 2.5 to 7 kHz band (band_db, over 30 ms: the band's own hiss flickers hop to hop), per hop,
    for judging where a cut's edges sit against the voice. `body` is the word's own stretch (b0, b1) in seconds: its loudest tenth sets how
    quiet the word's ending may get, and how loud a hop may be and still be the word (LOUD_DB over it is a shot, a clap or a bump). `ref` is
    where the room is read (the word's own edge, so a second look at a moved edge reads the same room). `fric` says the word may begin or end
    on a sound only the band hears (a fricative, an aspirated stop), and never for longer than TAIL_RUN_MAX_S: a longer one is a breath or
    wind. end_of and start_of answer (time, how): how is "word" (its sound ends or starts at time), "pause" (the cut sits in quiet: time is
    where the sound before it ended, or where the next one starts) or "runs on" (the voice carries on to the furthest the edge may go, into
    the next sound with no pause between, or into something far louder than the word: no clean edge in reach, time is None)."""

    QUIET, WORD, BAND, LOUD = 0, 1, 2, 3

    def __init__(self, y, sr, hop=HOP_S):
        self.hop = hop
        self.db = levels_db(y, sr, hop)
        self.hi = np.convolve(band_db(y, sr, hop)[:len(self.db)], np.ones(3) / 3, mode="same")
        self.dur = len(self.db) * hop

    def _i(self, t):
        """the hop that holds t"""
        return min(max(int(t / self.hop + 1e-6), 0), len(self.db))

    def thresholds(self, t, body):
        """(broadband, band) levels a hop near t must reach to be the word: EDGE_RISE_DB over the broadband room (its quietest tenth over
        EDGE_ROOM_S either side), EDGE_HI_RISE_DB over the band's (its quietest tenth over EDGE_HI_ROOM_S either side: its hiss is not even
        across a room), and never more than EDGE_DROP_DB under the body"""
        a, b = self._i(t - EDGE_ROOM_S), self._i(t + EDGE_ROOM_S)
        ha, hb = self._i(t - EDGE_HI_ROOM_S), self._i(t + EDGE_HI_ROOM_S)
        b0, b1 = self._i(body[0]), self._i(body[1])
        if b1 - b0 < 2:
            b0, b1 = self._i(t - 0.3), self._i(t + 0.3)
        if b - a < 5 or hb - ha < 5 or b1 - b0 < 2:
            return FLOOR_DB, float("inf")
        rb, rh = float(np.percentile(self.db[a:b], 10)), float(np.percentile(self.hi[ha:hb], 10))
        bb, bh = float(np.percentile(self.db[b0:b1], 90)), float(np.percentile(self.hi[b0:b1], 90))
        return max(rb + EDGE_RISE_DB, bb - EDGE_DROP_DB), max(rh + EDGE_HI_RISE_DB, bh - EDGE_DROP_DB)

    def body_db(self, body):
        b0, b1 = self._i(body[0]), self._i(body[1])
        return float(np.percentile(self.db[b0:b1], 90)) if b1 - b0 >= 2 else -120.0

    def loud_db(self, ref, body):
        """a hop over this is not the word: LOUD_DB over its body and over the speech around it (the loudest twentieth of EDGE_ROOM_S either side), so a
        word whose aligned stretch trails its loud onset (an aligned stretch that starts 0.15 s after the word's loud onset) is not taken for a shot"""
        a, b = self._i(ref - EDGE_ROOM_S), self._i(ref + EDGE_ROOM_S)
        around = float(np.percentile(self.db[a:b], 95)) if b - a >= 5 else -120.0
        return max(self.body_db(body), around) + LOUD_DB

    def states(self, t0, t1, ref, body):
        """(index of the first hop, a state per hop of [t0, t1]): WORD where the broadband level is over its threshold at `ref`, BAND where only
        the band is, LOUD over loud_db (a shot, not the word), QUIET elsewhere. A dip shorter than CLOSE_S between two sounding hops is
        closed (BAND when a side is BAND)"""
        a, b = self._i(t0), self._i(t1)
        if b <= a:
            return a, np.zeros(0, np.int8)
        tb, th = self.thresholds(ref, body)
        d, h = self.db[a:b], self.hi[a:b]
        st = np.where(d >= tb, self.WORD, np.where(h >= th, self.BAND, self.QUIET)).astype(np.int8)
        st[d > self.loud_db(ref, body)] = self.LOUD
        on = np.flatnonzero((st == self.WORD) | (st == self.BAND))
        for p, q in zip(on, on[1:]):
            if 1 < q - p <= round(CLOSE_S / self.hop):
                st[p + 1:q] = self.BAND if self.BAND in (st[p], st[q]) else self.WORD
        return a, st

    def _runs(self, st):
        """[(a, b)] the sounding runs of a state array: hops that are not QUIET, back to back"""
        x = np.concatenate(([False], st != self.QUIET, [False])).astype(np.int8)
        e = np.flatnonzero(np.diff(x))
        return list(zip(e[::2].tolist(), e[1::2].tolist()))

    def _own_run(self, st, base, body, k, after):
        """the run of the word's own sound, for a cut at hop k: of the sounding runs with a broadband hop that overlap its aligned stretch, the last
        one to start by k for an end (`after` False; an aligned stretch can swallow a filler after the word), the first one to end
        after k for a start; else the run that holds k, or ends within DIP_MAX_S before it (an end) or starts within DIP_MAX_S after it (a start)"""
        b0, b1 = self._i(body[0]) - base, self._i(body[1]) - base
        runs = [r for r in self._runs(st) if self.WORD in st[r[0]:r[1]]]
        own = [r for r in runs if r[0] < b1 and r[1] > b0]
        if own:
            if after:
                return next((r for r in own if r[1] > k), own[-1])
            return next((r for r in reversed(own) if r[0] <= k), own[0])
        dip = round(DIP_MAX_S / self.hop)
        near = [r for r in runs if (r[0] <= k < r[1]) or (not after and 0 <= k - r[1] < dip) or (after and 0 <= r[0] - k < dip)]
        return near[0] if near else None

    def end_of(self, t, body, stop, ref=None, fric=True):
        """where the word stops, never past `stop`: its own sound (the run under its aligned stretch, `body`) followed from its first broadband
        hop to its end (a band-only stretch only when `fric`, and only up to TAIL_RUN_MAX_S past its last broadband hop), then over one dip
        shorter than DIP_MAX_S into a short weak ending (TAIL_RUN_MAX_S at most, TAIL_WEAK_DB under the body: the z of "holes"). The answer
        does not depend on where the cut sits, so a second look at a moved edge finds the same end. Still sounding at `stop` (the next word's
        aligned start, a struck word's sound, another piece): "runs on"."""
        hop, W, B, L = self.hop, self.WORD, self.BAND, self.LOUD
        base, st = self.states(min(t, body[0]) - DIP_MAX_S - CLOSE_S, stop + DIP_MAX_S + TAIL_RUN_MAX_S, t if ref is None else ref, body)
        k, s, n = self._i(t) - base, self._i(stop) - base, len(st)
        run_max = round(TAIL_RUN_MAX_S / hop)
        if k < 0 or k >= n:
            return None, "pause"
        r = self._own_run(st, base, body, k, after=False)
        if r is None:
            j = k
            while j > 0 and st[j - 1] == self.QUIET:
                j -= 1
            return ((base + j) * hop if j else None), "pause"
        b0 = max(r[0], self._i(body[0]) - base)
        j = next((x for x in range(b0, r[1]) if st[x] == W), next(x for x in range(r[0], r[1]) if st[x] == W))
        last_w = j
        while j < n and st[j] != self.QUIET:
            if st[j] == L or j >= s:
                return None, "runs on"
            if st[j] == W:
                last_w = j
            elif j - last_w > run_max:
                break                               # a band-only stretch too long to be the word's: a breath, wind
            j += 1
        if j - 1 > last_w and (not fric or j - 1 - last_w > run_max):
            j = last_w + 1                          # the word may not end on a band-only sound (it ends on a vowel or an l, m, n, r): it ended with its voice
        weak = self.body_db(body) - TAIL_WEAK_DB
        g = j
        while g < n and st[g] == self.QUIET:
            g += 1
        if g < n and (g - j) * hop < DIP_MAX_S and g < s:
            h = g
            while h < n and st[h] != self.QUIET:
                h += 1
            seg = st[g:h]
            if (h - g) <= run_max and h <= s and L not in seg and (fric or B not in seg) and float(self.db[base + g:base + h].max()) <= weak:
                j = h
        return (base + j) * hop, "word"

    def start_of(self, t, body, stop, ref=None, fric=True):
        """where the word begins, never before `stop`: its own sound (the run under its aligned stretch) walked back from its first broadband hop
        to where it starts (a band-only onset only when `fric`, and only up to TAIL_RUN_MAX_S before that hop), never over a dip: a sound before
        one is the previous word's or a filler's as often as this word's. A start more than EDGE_STEP_S before t with under DIP_MAX_S of quiet
        before it is running speech: "runs on". No sound of the word in reach: "pause" and the next sound's start, or None"""
        hop, W, B, L = self.hop, self.WORD, self.BAND, self.LOUD
        base, st = self.states(stop - DIP_MAX_S - CLOSE_S, max(t, body[1]) + DIP_MAX_S + CLOSE_S, t if ref is None else ref, body)
        k, s, n = self._i(t) - base, self._i(stop) - base, len(st)
        run_max = round(TAIL_RUN_MAX_S / hop)
        if k < 0 or k >= n:
            return None, "pause"
        r = self._own_run(st, base, body, k, after=True)
        if r is None:
            g = k
            while g < n and st[g] != W:
                g += 1
            return ((base + g) * hop if g < n else None), "pause"
        b0 = max(r[0], self._i(body[0]) - base)
        j = next((x for x in range(b0, r[1]) if st[x] == W), next(x for x in range(r[0], r[1]) if st[x] == W))
        while j > r[0] and st[j - 1] == W:          # back to the first broadband hop of the run that leads into the word
            j -= 1
        first_w = j
        while j > 0 and st[j - 1] != self.QUIET:
            x = st[j - 1]
            if x == L or j - 1 < s:
                return None, "runs on"
            if x == W:
                first_w = j - 1
            elif first_w - (j - 1) > run_max:
                break                               # a band-only stretch too long to be the word's: a breath
            j -= 1
        if j < first_w and (not fric or first_w - j > run_max):
            j = first_w                             # the word may not start on a band-only sound (it starts on a vowel or an l, m, n, r, w, y)
        start = (base + j) * hop
        prv = j
        while prv > 0 and st[prv - 1] == self.QUIET:
            prv -= 1
        if t - start > EDGE_STEP_S and prv > 0 and (j - prv) * hop < DIP_MAX_S:
            return None, "runs on"
        return start, "word"

    def quiet_before(self, t, body, reach=DIP_MAX_S, ref=None):
        """where the last sound before t ended, looking back `reach` at most (None when there is none: quiet all the way)"""
        base, st = self.states(t - reach, t, t if ref is None else ref, body)
        on = np.flatnonzero(st != self.QUIET)
        return (base + int(on[-1]) + 1) * self.hop if len(on) else None

    def quiet_after(self, t, body, reach=DIP_MAX_S, ref=None):
        """where the next sound after t starts, looking on `reach` at most (None when there is none)"""
        base, st = self.states(t, t + reach, t if ref is None else ref, body)
        on = np.flatnonzero(st != self.QUIET)
        return (base + int(on[0])) * self.hop if len(on) else None

    def sounds_through(self, t0, t1, body, ref=None):
        """True when the word's kind of sound carries on through all of [t0, t1] (dips shorter than CLOSE_S closed, nothing far louder)"""
        _, st = self.states(t0, t1, t0 if ref is None else ref, body)
        return bool(len(st)) and bool(((st == self.WORD) | (st == self.BAND)).all())


_SONORANT = set("aeiouylrmnw")


def fricative_start(word):
    """True when a word may begin on a sound only the 2.5 to 7 kHz band hears: an s, f, h, th, sh or ch, or the breath of an aspirated p, t or k.
    A word that starts on a vowel or on l, r, m, n, w or y does not, so a band-only sound right before "And" is a breath, not its onset"""
    w = "".join(c for c in word.lower() if c.isalpha())
    return bool(w) and w[0] not in _SONORANT


def fricative_end(word):
    """True when a word may end on a sound only the band hears (its s, z, f, v, th, sh, ch, or a stop's release): its last letter, or the one
    before a silent e ("have", "these"), is a consonant other than l, r, m, n, w and y, and a final h is one of th, sh, ch, gh, ph"""
    w = "".join(c for c in word.lower() if c.isalpha())
    if len(w) > 2 and w.endswith("e") and w[-2] not in _SONORANT and w[-2] != "h":
        w = w[:-1]
    if not w:
        return False
    if w[-1] == "h":
        return len(w) > 1 and w[-2] in "tscgp"
    return w[-1] not in _SONORANT


# =================================================================================================================================
# Words and matching: reading a words file, finding a verbatim run of words in it, resolving the words a plan strikes.
# =================================================================================================================================

WHISPER = "whisper-cli"
SWEARS = {"shit", "fuck", "fucking", "damn", "goddamn", "ass", "bitch", "hell"}
# The ONE list of filler words: the cutter strikes them, the word engine (words.py) flags them in words.json, the script gate shows them struck.
FILLERS = {"um", "uh", "umm", "uhh", "uhm", "hmm", "mm", "er", "erm", "ah"}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def whisper_model():
    return support.whisper_model()


# whisper is shown a short, punctuated, disfluent prompt. Without one, its timestamped text tends to come back lowercase with no punctuation (one
# giant sentence) and to tidy a repeated phrase away; with a punctuated prompt that has false starts and fillers in it, the same take comes back
# punctuated AND keeps its false starts, repeated words and ums ("if you're interested in, if you're interested in seeing..."), which are exactly
# what a cutter has to see. The cut's own QC transcript uses the same prompt, so the two agree. English only: a prompt in the wrong language would
# pull the decode toward it. config.json "whisper_prompt" overrides ("" turns it off).
DEFAULT_PROMPT = "Um, so, we, we start here. Okay, uh, and then, then we go. Right."


def prompt_for(language, cfg=None):
    """the --prompt whisper gets: config.json "whisper_prompt" when it is set ("" means none), else DEFAULT_PROMPT for English, else none"""
    cfg = support.config() if cfg is None else cfg
    if "whisper_prompt" in cfg and cfg["whisper_prompt"] is not None:
        return str(cfg["whisper_prompt"])
    return DEFAULT_PROMPT if language == "en" else ""


def language():
    return support.language()


def norm_word(w):
    return re.sub(r"[^a-z0-9%$]", "", w.lower())


_SILENCES = {}


def silences_of(path, noise=-35):
    """[(start, end)] of every stretch under `noise` dB that lasts 0.10 s or more (ffmpeg silencedetect). Remembered per file contents, so a file
    that is rewritten under the same name is measured again."""
    try:
        st = os.stat(path)
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = None
    key = (path, noise, stamp)
    if key in _SILENCES:
        return _SILENCES[key]
    r = run(["ffmpeg", "-i", path, "-af", f"silencedetect=noise={noise}dB:d=0.10", "-f", "null", "-"])
    vals, cur = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            cur = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            vals.append((cur, float(m.group(1))))
            cur = None
    _SILENCES[key] = vals
    return vals


def load_wx(path):
    """words from an aligned json ({"segments": [{"words": [{"word", "start", "end"}]}]}): a word the aligner could not stamp gets the room between
    its neighbours, and a word stretched past 1.2 s (an aligner stretches a word over an unheard slate or a pause) is pulled back toward the
    neighbour-corroborated edge"""
    d = json.load(open(path))
    words = []
    for seg in d["segments"]:
        for w in seg.get("words", []):
            words.append({"text": w["word"].strip(),
                          "start": w.get("start"), "end": w.get("end")})
    for i, w in enumerate(words):
        if w["start"] is None or w["end"] is None:
            prev_end = next((words[j]["end"] for j in range(i - 1, -1, -1)
                             if words[j]["end"] is not None), 0.0)
            nxt = next((words[j]["start"] for j in range(i + 1, len(words))
                        if words[j]["start"] is not None), prev_end + 0.4)
            w["start"] = prev_end + 0.02
            w["end"] = max(w["start"] + 0.05, nxt - 0.02)
    words = [w for w in words if norm_word(w["text"])]
    # wav2vec2 stretch guard: no real word lasts more than 1.2 s
    for i, w in enumerate(words):
        if w["end"] - w["start"] > 1.2:
            plaus = min(1.0, 0.09 * max(3, len(norm_word(w["text"]))))
            nxt = words[i + 1]["start"] if i + 1 < len(words) else None
            prv = words[i - 1]["end"] if i > 0 else None
            if nxt is not None and nxt - w["end"] < 0.2:
                w["start"] = w["end"] - plaus
            elif prv is not None and w["start"] - prv < 0.2:
                w["end"] = w["start"] + plaus
            else:
                w["start"] = w["end"] - plaus
    return words


def load_whispercpp(path):
    """words from a whisper.cpp json (token rows: a token with no leading space continues the word before it)"""
    d = json.load(open(path))

    def ts(x):
        h, m, rest = x.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
    words = []
    for s in d["transcription"]:
        raw = s["text"]
        t = raw.strip()
        if not t:
            continue
        if words and (not norm_word(t) or not raw.startswith(" ")
                      or t[0] in "'’" or t.lower() == "s"):
            words[-1]["text"] += t
            words[-1]["end"] = max(words[-1]["end"], ts(s["timestamps"]["to"]))
        else:
            words.append({"text": t, "start": ts(s["timestamps"]["from"]),
                          "end": ts(s["timestamps"]["to"])})
    return [w for w in words if norm_word(w["text"])]


def load_words(path):
    d = json.load(open(path))
    return load_wx(path) if "segments" in d else load_whispercpp(path)


def _stream(words):
    """the transcript as one run of normalised characters: (the run, the word each character belongs to, where words begin, where words end)"""
    stream, cmap, begins, ends = "", [], set(), set()
    for i, w in enumerate(words):
        t = norm_word(w["text"])
        if not t:
            continue
        begins.add(len(stream))
        stream += t
        ends.add(len(stream))
        cmap.extend([i] * len(t))
    return stream, cmap, begins, ends


def find_hits(words, match, whole=True):
    """[(first word index, last word index)] of every place `match` sits in `words`, in order. The comparison runs on the normalised character
    stream, so punctuation and case never matter and "chat gpt" finds "chatgpt". With `whole` (the default) a hit has to start where a word starts
    and end where a word ends: "um" is the word "um", never the middle of "number", and "ah" is never the end of "yeah"."""
    stream, cmap, begins, ends = _stream(words)
    frag = "".join(norm_word(x) for x in match.split())
    if not frag:
        return []
    hits, k = [], stream.find(frag)
    while k != -1:
        if not whole or (k in begins and k + len(frag) in ends):
            hits.append((cmap[k], cmap[k + len(frag) - 1]))
        k = stream.find(frag, k + 1)
    return hits


def locate(words, match, occurrence=1, whole=True):
    """(first word index, last word index) of the occurrence-th place `match` sits in `words` (occurrence -1 is the last), or None. A match of eight
    words or more that is not there whole is looked for by its first five and last five words, with a tight span guard so the fallback never
    bridges across a flubbed take."""
    hits = find_hits(words, match, whole)
    if len(hits) < (1 if occurrence == -1 else occurrence):
        mw = match.split()
        if len(mw) >= 8:
            heads, tails = find_hits(words, " ".join(mw[:5]), whole), find_hits(words, " ".join(mw[-5:]), whole)
            for h in heads:
                for t in tails:
                    if t[0] >= h[1] and (t[1] - h[0]) <= int(len(mw) * 1.35) + 4:
                        hits.append((h[0], t[1]))
            hits = sorted(set(hits))
    if not hits or (occurrence != -1 and len(hits) < occurrence):
        return None
    return hits[-1] if occurrence == -1 else hits[occurrence - 1]


def find_match(words, match, occurrence=1, whole=True):
    r = locate(words, match, occurrence, whole)
    if r is None:
        stop(f"the words {match[:70]!r} are not in the transcript (wanted number {occurrence}). The transcript begins: "
             f"{' '.join(w['text'] for w in words)[:160]}")
    return r


DROP_AT_S = 0.01   # a struck word given with its start time is picked out by it to within this


def resolve_drops(words, drops, fail=None):
    """The indices of `words` that a segment's drop list strikes. Every entry strikes WHOLE words and never a part of one. A bare string, or
    {"text": ...}, takes the first match not already claimed by an earlier entry (two "really"s in the list strike two of them);
    {"text": ..., "keep_last_words": n} leaves the last n words of the phrase; {"text": ..., "start": t} takes the match whose first word starts
    at t, which picks the right one when the text repeats (the script gate writes entries this way). A text that is not in the words as a whole
    word stops the run (through `fail(message)`, which defaults to stop): nothing is guessed."""
    fail = fail or stop
    out = set()
    for d in drops or []:
        txt, keep_last, at = (d, 0, None) if isinstance(d, str) else (d["text"], int(d.get("keep_last_words", 0)), d.get("start"))
        hits = find_hits(words, txt)
        if not hits:
            fail(f"the plan strikes {txt!r} but no whole word of that text is in the segment")
        chosen = None
        if at is not None:
            near = [h for h in hits if words[h[0]].get("start") is not None and abs(words[h[0]]["start"] - float(at)) <= DROP_AT_S]
            chosen = min(near, key=lambda h: abs(words[h[0]]["start"] - float(at))) if near else None
        if chosen is None:
            for h in hits:
                struck = set(range(h[0], h[1] + 1 - keep_last))
                if not struck or not struck <= out:
                    chosen = h
                    break
            if chosen is None:
                fail(f"the plan strikes {txt!r} more times than the segment says it")
        out.update(range(chosen[0], chosen[1] + 1 - keep_last))
    return out


# =================================================================================================================================
# The cutter: plan the pieces, put every edge on the voice, render in one pass, write the EDL, the QC card and the Premiere XML.
# =================================================================================================================================

EDL_INTERNAL = ("lo", "hi", "floor", "ceil", "keep", "_c0_was", "_c1_was", "refine_c0", "refine_c1")   # the planner's working keys: never written to the EDL
BT709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
         "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709"]   # every cut says what it is, so the colour gate passes (this ffmpeg build
                                                                               # writes the matrix from the flags above but leaves primaries and transfer
                                                                               # unknown unless x264 is told too)


def pix_fmt(path):
    """the pixel format of a file's first video stream, or "" when ffprobe cannot read it"""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", path], capture_output=True, text=True)
    except OSError:
        return ""
    return r.stdout.strip().split(",")[0] if r.returncode == 0 else ""


def deep(fmt):
    """whether a pixel format carries more than 8 bits a sample (yuv420p10le, yuv422p10le, p010le, yuv444p12le ...)"""
    return bool(re.search(r"p1[0246](le|be)?$|^p[02]1[0246]", fmt or ""))


def cut_format(src):
    """(pixel format, crf) the cut is rendered in: the source's own depth. A source deeper than 8 bits is cut yuv420p10le at crf 12 (a later
    stretch of the picture stretches the compression's own error with it); every other source is cut yuv420p at crf 15."""
    return ("yuv420p10le", "12") if deep(pix_fmt(src)) else ("yuv420p", "15")


def write_edl(plan, outp, fps, pieces):
    """<cut>.edl.json: {"src", "output", "fps", "pieces", "dropped"}. The planner's working keys are left out: the EDL is a contract other
    tools read. Each piece: seg, label, c0, c1 (source seconds), text, out_in, out_out (cut seconds) and, when they apply, explicit, exact, collapse."""
    clean = [{k: v for k, v in p.items() if k not in EDL_INTERNAL} for p in pieces]
    path = outp[:-4] + ".edl.json"
    with open(path, "w") as f:
        json.dump({"src": plan["src"], "output": outp, "fps": fps, "pieces": clean, "dropped": plan.get("_dropped", [])}, f, indent=1)
    return path


# "right" is CONTENT (never dropped) when the neighbour word makes it an adverb/adjective.
RIGHT_CONTENT_PREV = {"all", "the", "look", "looks", "looking", "feed", "alright", "its", "your", "my", "a"}
RIGHT_CONTENT_NEXT = {"about", "here", "there", "as", "data", "now", "away", "after", "before", "over",
                      "off", "back", "into", "in", "on", "down", "up", "to", "hand", "side", "at", "next",
                      "above", "below", "behind", "beside", "along", "across", "through", "from"}

def right_kind(words, i):
    raw = words[i]["text"].strip()
    if raw[-1:] in "?,.!":          # "right?" / "right," carries its own punctuation -> always a tag
        return "tag"
    prev = norm_word(words[i - 1]["text"]) if i > 0 else ""
    nxt = norm_word(words[i + 1]["text"]) if i + 1 < len(words) else ""
    return "content" if (prev in RIGHT_CONTENT_PREV or nxt in RIGHT_CONTENT_NEXT) else "tag"

def load_pcm(src, sr=16000):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0

def rms_valley(pcm, t0, t1, sr=16000, hop=0.010):
    """Lowest-energy 10ms hop (3-hop smoothed) inside [t0, t1] -> (time, rms) or None.
    Used for mid-flow joints where silencedetect has nothing to snap to (e.g. the /t/ closure
    between 'apart' and a dropped 'right')."""
    a, b = max(0, int(t0 * sr)), min(len(pcm), int(t1 * sr))
    n = int(hop * sr)
    if b - a < 3 * n: return None
    vals = [float(np.sqrt(np.mean(pcm[i:i + n] ** 2)) + 1e-9) for i in range(a, b - n + 1, n)]
    sm = [(vals[max(0, i - 1)] + vals[i] + vals[min(len(vals) - 1, i + 1)]) / 3 for i in range(len(vals))]
    k = int(np.argmin(sm))
    return (a + k * n + n / 2) / sr, sm[k]

EDGE_MAX_S = 0.5     # an edge moves at most this far to the voice's own decay or onset
EDGE_TAIL_S = 0.05   # a piece ends this long after its last word's sound has decayed (render's 15 ms fade-out comes off it)
EDGE_LEAD_S = 0.04   # and opens this long before its first word's onset
ONSET_LAG_S = 0.04   # an aligned start sits up to this far into its word (25 to 40 ms): an end stops this far before the next one
QC_EDGE_TOL_S = 0.02 # the QC gate lets an edge sit this far inside a decay or an onset before it lists it

def _edge_words(p, words, which, skip=()):
    """the word a piece plays at its edge: the last of its own words that starts before c1 ("last"), or the first that ends after c0 ("first"),
    as (index, word). Its own words are p["keep"] (the segment's kept words, or a span's row words); without them (an EDL's pieces), any word
    inside the piece that is not in `skip` (the words the plan struck)"""
    ids = p.get("keep")
    if ids is None:
        ids = [i for i, w in enumerate(words) if w["start"] < p["c1"] and w["end"] > p["c0"] and i not in skip]
    if which == "last":
        cand = [i for i in ids if words[i]["start"] < p["c1"] - 0.02 and words[i]["end"] > p["c0"]]
        return (cand[-1], words[cand[-1]]) if cand else (None, None)
    cand = [i for i in ids if words[i]["end"] > p["c0"] + 0.02 and words[i]["start"] < p["c1"]]
    return (cand[0], words[cand[0]]) if cand else (None, None)

def _row_words(words, match, c0, c1):
    """the indices of a span's own words: the transcript words around [c0, c1] that line up with its row's text (a span holds part of a row when the
    row is cut in several spans, and a word the transcript heard differently still lines up as a replace), and that lie inside the span (their
    middle in [c0, c1]). The words around them that the row does not say (a creaky 'a' the aligner called 'So'), and a row word
    the span was set to leave out (a span that opens after a repeated 'is' that the row's text also says), are not the span's"""
    toks = _norm_tokens(match)
    cand = [i for i, w in enumerate(words) if c0 - 0.3 <= w["start"] <= c1 + 0.3]
    sm = difflib.SequenceMatcher(a=toks, b=[norm_word(words[i]["text"]) for i in cand], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and abs((i2 - i1) - (j2 - j1)) <= 1):
            out += [i for i in cand[j1:j2] if c0 <= (words[i]["start"] + words[i]["end"]) / 2 <= c1]
    return sorted(out)

def _on_frame(t, lo, hi, fps):
    """t on the 1/fps grid render() cuts on: the frame nearest t inside [lo, hi], so render's own rounding (to the nearest frame, up to half a
    frame either way) cannot take the edge back into the voice; t itself when no frame fits there"""
    if not fps: return t
    fits = [k / fps for k in range(math.floor(lo * fps) - 1, math.ceil(hi * fps) + 2) if lo - 0.002 <= k / fps <= hi + 0.002]   # 2 ms: a span's 33.667 is frame 1010
    return min(fits, key=lambda f: abs(f - t)) if fits else t

def _neighbours(pieces, i):
    """(the piece whose source comes right before piece i's start, the one right after its end), among all the pieces, or None"""
    p = pieces[i]
    before = [q for j, q in enumerate(pieces) if j != i and q["c1"] <= p["c0"] + 1e-6]
    after = [q for j, q in enumerate(pieces) if j != i and q["c0"] >= p["c1"] - 1e-6]
    return (max(before, key=lambda q: q["c1"]) if before else None), (min(after, key=lambda q: q["c0"]) if after else None)

def _plays_on(pieces, i):
    """the next piece carries straight on from piece i's end (it starts where i ends in the source): no cut there"""
    return i + 1 < len(pieces) and abs(pieces[i + 1]["c0"] - pieces[i]["c1"]) < 0.02

def end_edge(pieces, i, voice, words, skip=(), check=False):
    """What the voice says about piece i's end: {"t": where the end should be, "how": "word" | "pause" | "runs on" | "join", "word", "decay"}, or None
    when there is nothing to judge (exact, no word of its own, or it plays straight on). The end follows its last word's sound to where it has
    decayed (Voice.end_of), then EDGE_TAIL_S on, never into the next sound, never past the next word's aligned start less ONSET_LAG_S, the
    planner's measured bound (`ceil`: a struck word's sound, an excised range), another piece's source or EDGE_MAX_S. When the voice runs straight
    on into the next piece of the same take, with nothing struck between, the two join (the drawn-out 'I' the cutter took for a gap)"""
    p = pieces[i]
    if (p.get("exact") and not check) or _plays_on(pieces, i): return None
    wi, w = _edge_words(p, words, "last", skip)
    if w is None: return None
    c1 = p["c1"]
    _, nxt_piece = _neighbours(pieces, i)
    stop = min(c1 + EDGE_MAX_S, voice.dur, p.get("ceil", 1e9))
    if wi + 1 < len(words): stop = min(stop, words[wi + 1]["start"] - ONSET_LAG_S)
    if nxt_piece is not None: stop = min(stop, nxt_piece["c0"])
    body, ref = (w["start"], max(w["end"], w["start"] + 0.1)), w["end"]   # the word's own stretch: a second look at a moved edge reads the same
    fric = fricative_end(w["text"])
    out = {"t": c1, "how": "pause", "word": w["text"], "decay": None}
    if stop <= c1 + 1e-6:                               # nowhere to go: say whether the cut chops running voice
        if voice.sounds_through(c1 - 0.02, c1 + 0.02, body, ref): out["how"] = "runs on"
        return out
    end, how = voice.end_of(c1, body, stop, ref, fric)
    out["how"], out["decay"] = how, end
    if how == "word":
        nxt_sound = voice.quiet_after(end, body, reach=EDGE_TAIL_S + 0.02, ref=ref)
        ub = min(stop, nxt_sound - 0.01) if nxt_sound is not None else stop
        out["t"] = max(c1, min(end + EDGE_TAIL_S, ub))
        out["ub"] = max(ub, out["t"])
    elif how == "runs on":
        q = pieces[i + 1] if i + 1 < len(pieces) else None
        if (q is not None and q is nxt_piece and not p.get("explicit") and not q.get("explicit") and q["c0"] - c1 <= EDGE_MAX_S
                and p.get("ceil", 1e9) >= q["c0"] - 0.01 and q.get("floor", -1e9) <= c1 + 0.01 and voice.sounds_through(c1, q["c0"], body, ref)):
            out["how"], out["t"] = "join", q["c0"]
    return out

def start_edge(pieces, i, voice, words, skip=(), check=False):
    """What the voice says about piece i's start, the mirror of end_edge: {"t", "how", "word", "onset"}, or None. The start walks back to its first
    word's onset (Voice.start_of), then EDGE_LEAD_S before it, never into the sound before, never before the previous word's aligned end, the
    planner's measured bound (`floor`: a struck word's sound, an excised range), another piece's source or EDGE_MAX_S"""
    p = pieces[i]
    if (p.get("exact") and not check) or (i > 0 and _plays_on(pieces, i - 1)): return None
    wi, w = _edge_words(p, words, "first", skip)
    if w is None: return None
    c0 = p["c0"]
    prv_piece, _ = _neighbours(pieces, i)
    stop = max(c0 - EDGE_MAX_S, 0.0, p.get("floor", -1e9))
    if wi > 0: stop = max(stop, words[wi - 1]["end"] + 0.02)
    if prv_piece is not None: stop = max(stop, prv_piece["c1"])
    body, ref = (min(w["start"], w["end"] - 0.1), w["end"]), w["start"]
    fric = fricative_start(w["text"])
    out = {"t": c0, "how": "pause", "word": w["text"], "onset": None}
    if stop >= c0 - 1e-6:
        if voice.sounds_through(c0 - 0.02, c0 + 0.02, body, ref): out["how"] = "runs on"
        return out
    start, how = voice.start_of(c0, body, stop, ref, fric)
    out["how"], out["onset"] = how, start
    if how == "pause" and start is not None and start - c0 >= EDGE_LEAD_S - 1e-6:
        return out                                      # opens in quiet, its word far enough on
    if how in ("word", "pause") and start is not None:
        before = voice.quiet_before(start, body, reach=EDGE_LEAD_S + 0.02, ref=ref)
        lb = max(stop, before + 0.01) if before is not None else stop
        out["how"] = "word"
        out["t"] = min(c0, max(start - EDGE_LEAD_S, lb))
        out["lb"] = min(lb, out["t"])
    return out

def snap_edges(pieces, voice, words, review, fps=None, final=False, log=print):
    """Every cut edge on the voice, not on the aligned word times: a cut must never clip a word. An aligner ends soft endings and drawn-out words early
    (a soft "responsibility" ended 0.36 s early, so did the z of "holes") and starts words 25 to 40 ms into them, so each end goes forward to where its
    last word's voice has decayed and each start back to where its first word's voice begins (end_edge, start_edge), within EDGE_MAX_S, and never into a
    sound the plan left out. Two pieces of one take that the voice runs straight through join. An "edges_exact" span keeps both edges. A moved edge goes
    on render's frame grid (fps). plan_pieces runs it first and shape_pieces last (`final`), and the last pass writes the review queue: every move of a
    span's edge, every other move of EDGE_STEP_S or more (against the edge as planned), and every edge still inside running voice; a join is written when
    it is made"""
    moved = 0
    for i in range(len(pieces)):
        e = end_edge(pieces, i, voice, words)
        if e is None: continue
        p = pieces[i]; was = p.setdefault("_c1_was", p["c1"]); old = p["c1"]
        if e["how"] == "join":
            p["c1"] = e["t"]; moved += 1
            review.append({"src_s": round(old, 2), "why": f"the voice runs straight on from '{e['word']}' ({old:.2f}) into the next piece "
                           f"({e['t']:.2f}), with no quiet between: the joint now plays through. If that stretch was a filler, cut it by hand"})
            continue
        if e["how"] == "word" and e["t"] > old + 0.005:
            p["c1"] = _on_frame(e["t"], e["decay"] + 0.01, e["ub"], fps); moved += 1
        if final:
            if e["how"] == "word" and abs(p["c1"] - was) > 0.005 and (p.get("explicit") or p["c1"] - was >= EDGE_STEP_S):
                review.append({"src_s": round(was, 2), "why": f"the voice of '{e['word']}' runs to {e['decay']:.2f}: the piece now ends at {p['c1']:.2f} "
                               f"(was {was:.2f}{', a span end' if p.get('explicit') else ''}): listen"})
            elif e["how"] == "runs on":
                review.append({"src_s": round(old, 2), "why": f"the piece ends at {old:.2f} inside the voice of '{e['word']}', which runs on into the next "
                               f"sound with no pause: listen for a clipped word or a stub of the next one"})
    for i in range(len(pieces)):
        e = start_edge(pieces, i, voice, words)
        if e is None: continue
        p = pieces[i]; was = p.setdefault("_c0_was", p["c0"]); old = p["c0"]
        if e["how"] == "word" and e["t"] < old - 0.005:
            p["c0"] = _on_frame(e["t"], e["lb"], e["onset"] - 0.01, fps); moved += 1
        if final:
            if e["how"] == "word" and abs(p["c0"] - was) > 0.005 and (p.get("explicit") or was - p["c0"] >= EDGE_STEP_S):
                review.append({"src_s": round(was, 2), "why": f"the voice of '{e['word']}' starts at {e['onset']:.2f}: the piece now opens at {p['c0']:.2f} "
                               f"(was {was:.2f}{', a span start' if p.get('explicit') else ''}): listen"})
            elif e["how"] == "runs on":
                review.append({"src_s": round(old, 2), "why": f"the piece opens at {old:.2f} inside running voice before '{e['word']}': listen for a lost "
                               f"start or a stub of the word before"})
    if moved: log(f"snap_edges: {moved} edges moved onto the voice")
    return moved

def edges_in_voice(pieces, voice, words, skip=()):
    """The QC gate on the cut's edges, read on the source's own level by the measure snap_edges cuts on: every edge that is a real cut and still sits
    inside the voice, as {"label", "edge", "src_s", "out_s", "word", "voice_s", "why", "exact"}: an end before its last word's voice has decayed
    (voice_s, past QC_EDGE_TOL_S), inside running voice, or with the voice running straight on into the next piece; a start after its first word's voice
    began, or inside running voice. A joint that plays straight on is no cut. An "edges_exact" span's edge, set by a listener, comes back with "exact": true"""
    out = []
    for i, p in enumerate(pieces):
        e = end_edge(pieces, i, voice, words, skip, check=True)
        if e is not None:
            c1, why = p["c1"], None
            if e["how"] == "word" and e["decay"] is not None and e["decay"] > c1 + QC_EDGE_TOL_S:
                why = f"ends {e['decay'] - c1:.2f} s before the voice of '{e['word']}' has decayed ({e['decay']:.2f})"
            elif e["how"] in ("runs on", "join"):
                why = f"ends inside the voice of '{e['word']}', which runs straight on" + (" into the next piece" if e["how"] == "join" else "")
            if why:
                out.append({"label": p.get("label", ""), "edge": "end", "src_s": round(c1, 3), "out_s": round(p.get("out_out", 0.0), 3), "word": e["word"],
                            "voice_s": round(e["decay"], 3) if e["decay"] is not None else None, "why": why, "exact": bool(p.get("exact"))})
        s = start_edge(pieces, i, voice, words, skip, check=True)
        if s is not None:
            c0, why = p["c0"], None
            if s["how"] == "word" and s["onset"] is not None and s["onset"] < c0 - QC_EDGE_TOL_S:
                why = f"opens {c0 - s['onset']:.2f} s after the voice of '{s['word']}' began ({s['onset']:.2f})"
            elif s["how"] == "runs on":
                why = f"opens inside running voice before '{s['word']}'"
            if why:
                out.append({"label": p.get("label", ""), "edge": "start", "src_s": round(c0, 3), "out_s": round(p.get("out_in", 0.0), 3), "word": s["word"],
                            "voice_s": round(s["onset"], 3) if s["onset"] is not None else None, "why": why, "exact": bool(p.get("exact"))})
    return out

def joint_words(pieces, words, heard, skip=(), near=0.6):
    """What the cut's own transcript (`heard`, the speech engine run on the cut) says at every joint, against the source words its pieces hold: the
    level at a range's noise floor can find nothing where the transcript hears "hole" for "holes". Every word missing, changed or added within `near` s of
    a joint, or of the cut's start or end, as {"out_s", "op", "expected", "heard", "where", "context_source", "context_cut"}, for a listener to judge:
    most are the transcriber's own noise, a clipped word is one of them. Words the plan struck (`skip`) are not expected, and a difference in spelling
    alone ("chat gpt" for "chatgpt") is not a difference"""
    if not pieces: return []
    exp = []
    for k, p in enumerate(pieces):
        for i, w in enumerate(words):
            if i not in skip and p["c0"] <= (w["start"] + w["end"]) / 2 < p["c1"] and norm_word(w["text"]):
                exp.append({"t": w["text"], "o": p["out_in"] + (w["start"] - p["c0"])})
    end = pieces[-1]["out_out"]
    joints = [0.0] + [p["out_in"] for k, p in enumerate(pieces) if k and abs(pieces[k - 1]["c1"] - p["c0"]) > 1e-3] + [end]
    A, B = [norm_word(x["t"]) for x in exp], [norm_word(x["text"]) for x in heard]
    flags = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=A, b=B, autojunk=False).get_opcodes():
        if op == "equal" or "".join(A[i1:i2]) == "".join(B[j1:j2]): continue
        ts = [exp[i]["o"] for i in range(i1, i2)] + [heard[j]["start"] for j in range(j1, j2)]
        t0, t1 = min(ts), max(ts)
        jn = min(joints, key=lambda j: 0 if t0 - near <= j <= t1 + near else min(abs(j - t0), abs(j - t1)))
        if not t0 - near <= jn <= t1 + near: continue
        k = next((n for n, p in enumerate(pieces) if n and abs(p["out_in"] - jn) < 1e-3), None)
        if jn == 0.0: where = f"the cut's start (source {pieces[0]['c0']:.3f}, {pieces[0].get('label', '')})"
        elif abs(jn - end) < 1e-3 or k is None: where = f"the cut's end (source {pieces[-1]['c1']:.3f}, {pieces[-1].get('label', '')})"
        else: where = f"joint {pieces[k - 1].get('label', '')} ends {pieces[k - 1]['c1']:.3f} | {pieces[k].get('label', '')} opens {pieces[k]['c0']:.3f}"
        flags.append({"out_s": round(jn, 3), "op": op, "expected": " ".join(x["t"] for x in exp[i1:i2]), "heard": " ".join(x["text"] for x in heard[j1:j2]),
                      "where": where, "context_source": " ".join(x["t"] for x in exp[max(0, i1 - 4):i2 + 3]),
                      "context_cut": " ".join(x["text"] for x in heard[max(0, j1 - 4):j2 + 3])})
    return flags

def _runs_of(runs, t0, t1, reach=0.25):
    """the stretches of sound that belong to a word stamped [t0, t1]: those overlapping the stamp by 30 ms or more, else the nearest one within
    `reach` s (a stamp that sits wholly before or after its sound)"""
    mine = [r for r in runs if min(r[1], t1) - max(r[0], t0) >= 0.03]
    if mine: return mine
    near = [(max(r[0] - t1, t0 - r[1], 0.0), r) for r in runs]
    near = [x for x in near if x[0] <= reach]
    return [min(near, key=lambda x: x[0])[1]] if near else []

def gap_after_dropped(lvl, pw, first):
    """(lo, hi): the quiet between a struck word's sound and the kept word after it, measured on the level scale instead of read from stamps.
    An aligner can stamp a struck "uh" 60 ms past its sound and the next word 65 ms late, and a stamp window that starts early
    holds quiet from BEFORE the struck word, so a valley searched over the stamps can sit before the uh or inside it. lo is where the struck
    word's sound has ended (dips under 30 ms inside it are closed), hi where the next sound begins. None when the audio has no gap to cut in
    (running speech) or the stamps and the sound disagree too much to say, so the caller keeps its stamp-based window."""
    t0, t1 = pw["start"] - 0.15, first["end"] + 0.15
    runs = sound_runs(lvl, threshold(lvl, t0, t1), t0, t1)
    mine = _runs_of(runs, pw["start"], pw["end"])
    if not mine: return None
    lo = mine[-1][1]
    if lo > first["start"] + 0.10: return None      # the struck word's stamp lies over the kept word's own sound: nothing to separate
    nxt = next((r for r in runs if r[0] >= lo + 0.02 - 1e-9), None)
    return (lo, nxt[0]) if nxt else None

def gap_before_dropped(lvl, last, dw):
    """(lo, hi): the quiet between a kept word's sound and the struck word after it, the mirror of gap_after_dropped. hi is where the struck word's
    sound begins, lo where the sound before it ends. None when there is no gap or the sound before it is not this kept word's."""
    t0, t1 = last["start"] - 0.15, dw["end"] + 0.15
    runs = sound_runs(lvl, threshold(lvl, t0, t1), t0, t1)
    mine = _runs_of(runs, dw["start"], dw["end"])
    if not mine: return None
    hi = mine[0][0]
    before = [r for r in runs if r[1] <= hi - 0.02 + 1e-9]
    if not before or before[-1][1] < last["start"]: return None
    return before[-1][1], hi

def sil_between(svals, t0, t1, need):
    """True if a measured silence covers >= need seconds inside [t0, t1]."""
    return any(min(e, t1) - max(st, t0) >= need for st, e in svals)

def plan_pieces(plan):
    src, fps = plan["src"], plan.get("fps", 30)
    words = load_words(plan["words_json"])
    # sil_noise_db: -35 by default; a quieter room can use -30 so breath-floor pauses count as silence
    svals = silences_of(src, noise=plan.get("sil_noise_db", -35))
    pad_pre, pad_post = plan.get("pad_pre", 0.06), plan.get("pad_post", 0.07)
    sp_pre, sp_post = plan.get("split_pre", 0.06), plan.get("split_post", 0.08)
    gap_max, split_sil = plan.get("gap_max", 0.40), plan.get("split_sil", 0.45)
    pocket = plan.get("right_pocket", 0.20)
    rmode = plan.get("right_mode", "off")
    if rmode not in ("off", "none", "most", "pocket"):
        stop(f"right_mode {rmode!r} is not one of off, most, pocket")
    keep_right = [float(x) for x in plan.get("keep_right", [])]
    force_right = [float(x) for x in plan.get("force_right", [])]
    pcm = load_pcm(src)
    lvl = levels_db(pcm, 16000)           # the 10 ms level scale (not `db`: the inner-drop matching below uses that name)
    dropped_all = set()      # every word index the plan struck, for the EDL's "dropped" list
    pieces, intent, reports, review = [], [], [], []
    right_inv = []   # every "right" token inside kept spans: decision + context
    for si, seg in enumerate(plan["segments"]):
        if seg.get("span"):
            # explicit source-time span (escape hatch for mis-aligned tokens); no interior splitting
            c0, c1 = float(seg["span"][0]), float(seg["span"][1])
            intent.append(seg["match"])
            # explicit = hand-set by ear: refine_joints listens only to the matched piece beside it, and snap_edges moves its
            # edges only within the voice of its own row's words ("keep"). "edges_exact": true keeps both edges as set (exact). Spans that meet
            # back to back in the source keep their shared boundary (moving both would repeat audio)
            pieces.append({"seg": si, "label": seg.get("label", ""), "c0": c0, "c1": c1, "text": seg["match"], "explicit": True,
                           "exact": bool(seg.get("edges_exact")), "collapse": bool(seg.get("collapse")), "keep": _row_words(words, seg["match"], c0, c1)})
            review.append({"src_s": round(c0, 2), "why": f"explicit span {c0}-{c1} (no word matching): check both edges"})
            continue
        a, b = find_match(words, seg["match"], seg.get("occurrence", 1))
        span = list(range(a, b + 1))
        drop = set()
        # explicit inner drops: whole words only, never a part of one (resolve_drops)
        drop.update(a + i for i in resolve_drops(words[a:b + 1], seg.get("drop", [])))
        for i in span:
            w = words[i]; nw = norm_word(w["text"])
            if plan.get("drop_fillers", True) and not seg.get("keep_fillers") and nw in FILLERS:
                drop.add(i)
            elif nw == "right" and i > 0 and i + 1 < len(words) and rmode not in ("off", "none"):
                before = sil_between(svals, words[i - 1]["end"], w["start"], pocket)
                after = sil_between(svals, w["end"], words[i + 1]["start"], pocket)
                kind = right_kind(words, i)
                if any(abs(w["start"] - k) <= 0.5 for k in force_right): kind = "tag"
                ctx = " ".join(x["text"] for x in words[max(0, i - 3):i + 3])
                if rmode == "pocket":
                    decision = "drop" if (before and after) else "keep"
                else:  # "most"
                    if kind == "content": decision = "keep"
                    elif any(abs(w["start"] - k) <= 0.5 for k in keep_right): decision = "keep"
                    else: decision = "drop"
                right_inv.append({"src_s": round(w["start"], 2), "kind": kind, "decision": decision,
                                  "sil_before": before, "sil_after": after, "ctx": ctx})
                if decision == "drop":
                    drop.add(i)
                    if not after:
                        review.append({"src_s": round(w["start"], 2),
                                       "why": f"mid-flow 'right' removed, no pause after (valley joint): '{ctx}'"})
        dropped_all |= drop
        kept_text = " ".join(words[i]["text"] for i in span if i not in drop)
        intent.append(kept_text)
        # runs of kept words
        runs, cur = [], []
        for i in span:
            if i in drop:
                if cur: runs.append(cur); cur = []
            else:
                cur.append(i)
        if cur: runs.append(cur)
        for r_i, run_idx in enumerate(runs):
            sub_words = [words[i] for i in run_idx]
            # interior split at real silences (the same measure the planner uses)
            span_sil = [(st, e) for st, e in svals if e - st >= split_sil
                        and st > sub_words[0]["start"] and e < sub_words[-1]["end"]]
            subs, cur, noisy_after = [], [run_idx[0]], set()
            for p, q in zip(run_idx, run_idx[1:]):
                wp, wq = words[p], words[q]
                # split on a WhisperX gap only when a REAL measured silence backs it (stamps drift; a
                # pointless split at a 0.13s pause puts the fade-in on the next word's onset)
                wx_gap = (wq["start"] - wp["end"] > gap_max) and sil_between(svals, wp["end"], wq["start"], min(0.20, split_sil - 0.05))
                forced = any(st >= wp["start"] and e <= wq["end"] and
                             min(e, wq["start"] + 0.3) - max(st, wp["end"] - 0.3) >= 0.35
                             for st, e in span_sil)
                # non-silent long gap (breath / unheard filler): collapse but flag
                noisy = (wq["start"] - wp["end"] >= 0.55) and not wx_gap
                if wx_gap or forced or noisy:
                    if noisy:
                        review.append({"src_s": round(wp["end"], 2),
                                       "why": f"non-silent gap {wq['start']-wp['end']:.2f}s collapsed (breath/um?) between '{wp['text']}' and '{wq['text']}'"})
                        noisy_after.add(len(subs))   # boundary after sub index len(subs)
                    subs.append(cur); cur = []
                cur.append(q)
            subs.append(cur)
            # stamp-independent guard: any measured silence >= split_sil strictly inside a sub forces a
            # split at the nearest word boundary (WhisperX stamps stretched over a 0.95 s pause hid one).
            fixed = []
            for sub in subs:
                todo = [sub]
                for st, e in span_sil:
                    out_ = []
                    for s_ in todo:
                        f0, l1 = words[s_[0]]["start"], words[s_[-1]]["end"]
                        if len(s_) < 2 or st < f0 + 0.15 or e > l1 - 0.15: out_.append(s_); continue
                        mid = (st + e) / 2
                        kbest = min(range(len(s_) - 1), key=lambda k: abs((words[s_[k]]["end"] + words[s_[k + 1]]["start"]) / 2 - mid))
                        gapmid = (words[s_[kbest]]["end"] + words[s_[kbest + 1]]["start"]) / 2
                        if abs(gapmid - mid) > 0.6: out_.append(s_); continue
                        out_ += [s_[:kbest + 1], s_[kbest + 1:]]
                    todo = out_
                fixed += todo
            subs = fixed
            for k, sub in enumerate(subs):
                first, last = words[sub[0]], words[sub[-1]]
                prev_end = words[sub[0] - 1]["end"] if sub[0] > 0 else 0.0
                next_start = words[sub[-1] + 1]["start"] if sub[-1] + 1 < len(words) else last["end"] + 5
                is_first = (r_i == 0 and k == 0); is_last = (r_i == len(runs) - 1 and k == len(subs) - 1)
                pre_k = pad_pre if is_first else sp_pre
                post_k = pad_post if is_last else sp_post
                if (k - 1) in noisy_after: pre_k = max(pre_k, 0.10)     # no silence to snap into: pad wider
                if k in noisy_after: post_k = max(post_k, 0.14)
                # WhisperX stamps drift around pauses (starts trail the onset, ends stretch into the
                # silence). A measured silence between the neighbour word and this word is the truth:
                # begin 0.04s before it ends / end 0.10s after it starts, ignoring the drifted stamps.
                prev_ws = words[sub[0] - 1]["start"] if sub[0] > 0 else -1.0
                # the LAST such silence: a mouth click or breath can split the room before the word in two, and the first fragment is
                # 0.3 s of dead air away from it (the piece would open 0.37 s of room tone before the word, and the lead-in gate fails)
                snap0 = next(((st, e) for st, e in reversed(svals)
                              if st >= prev_ws and e <= first["start"] + 0.15 and first["start"] - e <= 0.35), None)
                prev_dropped = sub[0] > 0 and (sub[0] - 1) in drop
                if snap0:
                    # room before the word: silencedetect marks the onset late (quiet consonants sit
                    # below -35dB), so leave 0.07s; after a dropped tag-"right?" ~0.12s so the joint breathes
                    room = 0.12 if prev_dropped else 0.07
                    c0 = max(snap0[1] - room, snap0[0] + 0.02, 0.0)
                    lo0 = snap0[0] + 0.02
                elif prev_dropped:
                    pw = words[sub[0] - 1]
                    # the quietest moment BETWEEN the struck word's sound and the kept word's: the stamps can put either edge 60 ms or more off, and
                    # a valley searched over a stamp window can land in a dip inside the struck word or inside the kept one
                    gap = gap_after_dropped(lvl, pw, first)
                    if gap:
                        w0, w1 = max(gap[0], gap[1] - 0.12), gap[1] - 0.01       # the last 0.12 s of the quiet before the kept word: a tight joint
                        v = rms_valley(pcm, w0, w1)
                        c0 = v[0] if v else (w0 + w1) / 2
                        lo0 = gap[0]
                    else:
                        v = rms_valley(pcm, max(pw["start"] + 0.06, first["start"] - 0.22), first["start"] + 0.06)
                        c0 = v[0] if v else max(first["start"] - pre_k, prev_end + 0.02, 0.0)
                        lo0 = c0                       # no measured gap: nothing to widen into
                else:
                    c0 = max(first["start"] - pre_k, prev_end + 0.02, 0.0)
                    lo0 = max(prev_end + 0.02, 0.0)
                next_ws = words[sub[-1] + 1]["start"] if sub[-1] + 1 < len(words) else last["end"] + 9
                snap1 = next(((st, e) for st, e in svals
                              if st >= last["start"] + 0.08 and st <= last["end"] + 0.30 and e <= next_ws + 0.15), None)
                next_dropped = (sub[-1] + 1) < len(words) and (sub[-1] + 1) in drop
                if snap1:
                    c1 = snap1[0] + 0.10
                    hi1 = snap1[1] - 0.02
                elif next_dropped:
                    nw_ = words[sub[-1] + 1]
                    gap = gap_before_dropped(lvl, last, nw_)
                    if gap:
                        w0, w1 = gap[0] + 0.01, min(gap[1], gap[0] + 0.14)      # the first 0.14 s of the quiet after the kept word: its tail, then the cut
                        v = rms_valley(pcm, w0, w1)
                        c1 = v[0] if v else (w0 + w1) / 2
                        hi1 = gap[1] - 0.01
                    else:
                        v = rms_valley(pcm, max(last["start"] + 0.08, last["end"] - 0.14), min(nw_["start"] + 0.10, nw_["end"] - 0.03))
                        c1 = v[0] if v else min(last["end"] + post_k, next_start - 0.02)
                        hi1 = None
                    c1 = max(c1, last["end"] - 0.02)
                else:
                    c1 = min(last["end"] + post_k, next_start - 0.02)
                    c1 = max(c1, last["end"] + 0.03)
                    hi1 = next_start - 0.02
                for st, e in svals:
                    if st <= c0 <= e:
                        c0 = max(c0, min(e - 0.06, c1 - 0.1)); break
                for st, e in svals:
                    if st <= c1 <= e:
                        c1 = min(c1, max(st + 0.08, c0 + 0.1)); break
                for st, e in svals:
                    if last["end"] - 0.25 <= st <= c1:
                        c1 = min(c1, max(st + 0.10, last["end"] - 0.05)); break
                # no stub of the next word. A word that finishes before the cut is snap_edges' job below: the voice says where it has decayed,
                # never the stamps or silencedetect
                c1 = min(c1, next_ws - 0.005)
                piece = {"seg": si, "label": seg.get("label", ""), "c0": c0, "c1": c1,
                         "text": " ".join(words[i]["text"] for i in sub), "keep": [i for i in span if i not in drop]}
                piece["lo"] = lo0                      # how far the retry ladder may move the edges outward: the end of the speech before c0
                piece["hi"] = c1 if hi1 is None else hi1   # and the start of the speech after c1 (widen_edges; never written to the EDL)
                # and how far snap_edges may: never onto a struck word's sound, measured (the ladder's lo and hi read silencedetect too, which
                # takes the quiet k inside "fix" for a pause). With no quiet between the two sounds, the edge stays where the planner put it
                if prev_dropped:
                    g_ = gap_after_dropped(lvl, words[sub[0] - 1], first)
                    piece["floor"] = g_[0] if g_ else c0
                if next_dropped:
                    g_ = gap_before_dropped(lvl, last, words[sub[-1] + 1])
                    piece["ceil"] = g_[1] - 0.01 if g_ else c1
                if next_dropped:
                    j = sub[-1] + 1; forb = []
                    while j < len(words) and j in drop and len(forb) < 3:
                        forb.append(norm_word(words[j]["text"])); j += 1
                    dw = words[sub[-1] + 1]
                    hi = dw["end"] - 0.05
                    s_after = next((st for st, e in svals if dw["end"] - 0.25 <= st <= dw["end"] + 0.35), None)
                    if s_after: hi = min(hi, s_after - 0.15)
                    piece["refine_c1"] = {"x": norm_word(last["text"]), "forbid": _clean_forbid([f for f in forb if f], [], []) or [f for f in forb if f in TAGS],
                                          "lo": max(last["start"] + 0.10, last["end"]), "hi": max(hi, last["end"] + 0.02)}   # the kept word always finishes
                if prev_dropped:
                    j = sub[0] - 1; forb = []
                    while j >= 0 and j in drop and len(forb) < 3:
                        forb.insert(0, norm_word(words[j]["text"])); j -= 1
                    dw = words[sub[0] - 1]
                    piece["refine_c0"] = {"y": norm_word(first["text"]), "forbid": _clean_forbid([f for f in forb if f], [], []) or [f for f in forb if f in TAGS],
                                          "lo": dw["start"] + 0.05, "hi": first["start"] + 0.10}
                pieces.append(piece)
    plan["_right_inventory"] = right_inv
    plan["_dropped"] = [{"text": words[i]["text"], "start": round(words[i]["start"], 3), "end": round(words[i]["end"], 3)} for i in sorted(dropped_all)]
    # ---- cut_ranges: excise by time (untranscribed stutters). Inside a piece -> split it; over an edge -> trim.
    for rng in plan.get("cut_ranges", []):
        t0, t1 = float(rng[0]), float(rng[1])
        out = []
        for p in pieces:
            if p["c1"] <= t0 + 0.02 or p["c0"] >= t1 - 0.02:
                out.append(p); continue
            if p["c0"] < t0 - 0.05 and p["c1"] > t1 + 0.05:          # strictly inside: split
                wl = [w for w in p["text"].split()]
                ws = [w for w in words if w["start"] >= p["c0"] - 0.05 and w["end"] <= p["c1"] + 0.05]
                left = [w["text"] for w in ws if (w["start"] + w["end"]) / 2 < (t0 + t1) / 2]
                right = [w["text"] for w in ws if (w["start"] + w["end"]) / 2 >= (t0 + t1) / 2]
                a = dict(p); a["c1"] = t0; a["text"] = " ".join(left) or p["text"]
                b = dict(p); b["c0"] = t1; b["text"] = " ".join(right) or p["text"]
                a.pop("refine_c1", None); b.pop("refine_c0", None)
                a["hi"], b["lo"] = t0, t1             # the excised range is not quiet to widen into
                a["ceil"], b["floor"] = t0, t1
                if left: a["refine_c1"] = {"x": norm_word(left[-1]), "forbid": [], "lo": t0 - 0.12, "hi": t0 + 0.10}
                if right: b["refine_c0"] = {"y": norm_word(right[0]), "forbid": [], "lo": t1 - 0.10, "hi": t1 + 0.12}
                review.append({"src_s": round(t0, 2), "why": f"cut_range {t0}-{t1} split a piece (stutter by time): listen"})
                out += [a, b]
            elif p["c0"] < t0:                                       # range covers the tail: trim c1
                p["c1"] = t0; p["hi"] = t0; p["ceil"] = t0
                if p.get("refine_c1"): p["refine_c1"]["hi"] = min(p["refine_c1"]["hi"], t0 + 0.05); p["refine_c1"]["lo"] = min(p["refine_c1"]["lo"], t0 - 0.12)
                out.append(p)
            else:                                                     # range covers the head: trim c0
                p["c0"] = t1; p["lo"] = t1; p["floor"] = t1
                if p.get("refine_c0"): p["refine_c0"]["lo"] = max(p["refine_c0"]["lo"], t1 - 0.05); p["refine_c0"]["hi"] = max(p["refine_c0"]["hi"], t1 + 0.12)
                out.append(p)
        pieces = out
    # every edge on the voice before the joints are listened to (shape_pieces does it again, last, for the edges its own steps move)
    snap_edges(pieces, Voice(pcm, 16000), words, review, fps=fps)
    return pieces, intent, review, fps

def _norm_tokens(txt):
    return [x for x in (norm_word(tk) for tk in txt.split()) if x]

def _score_tok(tk, word):
    if tk == word: return 1.0
    if len(word) >= 3 and (tk.endswith(word) or tk.startswith(word)): return 0.9
    if len(word) < 3 and len(tk) > 3 and tk.endswith(word): return 0.8
    return difflib.SequenceMatcher(a=tk, b=word, autojunk=False).ratio()

def _matches(tokens, word, thr=0.75):
    """indices whose token (or token+next token joined) matches word."""
    out = []
    for i, tk in enumerate(tokens):
        if _score_tok(tk, word) >= thr: out.append(i)
        elif i + 1 < len(tokens) and len(word) >= 6 and tk + tokens[i + 1] == word: out.append(i + 1)
    return out

STOPWORDS = {"i", "a", "an", "the", "we", "to", "and", "of", "in", "it", "is", "that", "this", "so", "you",
        "me", "my", "our", "for", "on", "at", "or", "be", "do", "go", "us", "if", "as", "but", "not", "are"}
TAGS = ("right", "um", "uh")

def _clean_forbid(forb, x_side, y_side):
    """forbidden tokens must be distinctive: no stopwords/short words, nothing that also occurs in the
    kept words next to the joint (a false 'leak' there once destroyed a clean joint)."""
    ctx = set(x_side) | set(y_side)
    out = []
    for f in forb:
        if f in TAGS and f not in ctx: out.append(f)
        elif len(f) >= 4 and f not in STOPWORDS and f not in ctx: out.append(f)
    return list(dict.fromkeys(out))

def _find_tok(tokens, word, thr=0.75, before=None, after=None):
    """best fuzzy match index for word (exact wins, else ratio>=thr), optionally bounded."""
    best, bi = 0.0, -1
    rng = range(len(tokens))
    for i in rng:
        if before is not None and i >= before: continue
        if after is not None and i <= after: continue
        tk = tokens[i]
        if tk == word: r = 1.0
        elif len(word) >= 3 and (tk.endswith(word) or tk.startswith(word)): r = 0.9   # "chatgpt" ~ "gpt"
        elif len(word) < 3 and len(tk) > 3 and tk.endswith(word): r = 0.8              # "chachubiti" ~ "t"
        else: r = difflib.SequenceMatcher(a=tk, b=word, autojunk=False).ratio()
        if r > best or (r == best and r >= thr and before is not None):   # prefer the latest before a bound
            best, bi = r, i
    return bi if best >= thr else -1

def _heard(pcm, a0, a1, b0, b1, tmp, sr=16000):
    """Transcribe source[a0:a1] + source[b0:b1] joined as one clip (a preview of the joint)."""
    seg = np.concatenate([pcm[int(max(0, a0) * sr):int(a1 * sr)], pcm[int(b0 * sr):int(min(b1, len(pcm) / sr) * sr)]])
    path = os.path.join(tmp, f"jp{int(a1 * 1000)}_{int(b0 * 1000)}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes())
    o = subprocess.run([WHISPER, "-m", whisper_model(), "-f", path, "-l", language(), "-nt", "-np"], capture_output=True, text=True).stdout
    return _norm_tokens(o)

def _judge(tokens, x, y, forbid):
    xs, ys = _matches(tokens, x), _matches(tokens, y)
    pairs = [(a, b) for a in xs for b in ys if b > a]
    if pairs:
        xi, yi = min(pairs, key=lambda p: p[1] - p[0])
        mid = tokens[xi + 1:yi]
        leak = any(_matches(mid, f, 0.85) for f in forbid)
        return True, True, leak
    # no X-then-Y: whichever is missing decides; a Y only BEFORE X means the real Y was cut
    return bool(xs), bool(ys) and not xs, False

def _edge_tok(p, words, which):
    """the word a piece's edge plays, normalised: a matched piece's own text says it; a span's text is its whole row, so the span's own words do"""
    if p.get("explicit") and words:
        _, w = _edge_words(p, words, which)
        if w is not None: return norm_word(w["text"])
    toks = _norm_tokens(p["text"])
    return (toks[-1] if which == "last" else toks[0]) if toks else ""

def _edge_ctx(p, words, which, n=6):
    """the words a piece plays next to its edge, normalised (the context _clean_forbid keeps out of a joint's forbidden list)"""
    if p.get("explicit") and words:
        ids = [i for i in (p.get("keep") or []) if words[i]["start"] < p["c1"] and words[i]["end"] > p["c0"]]
        toks = [norm_word(words[i]["text"]) for i in ids]
    else:
        toks = _norm_tokens(p["text"])
    return toks[-n:] if which == "last" else toks[:n]

def refine_joints(pieces, pcm, review, words=None, log=print):
    """Listen to every joint. Drop-joints (a token was removed) are bisected until the neighbour
    word survives and the dropped word is gone. Every other joint gets one listen; if the last or
    first word is not heard it gets generic bounds and the same bisection. (LOCKED: verify by ear,
    never trust stamps.) A hand-set span's own edges are never moved, but the matched piece beside it is
    still listened to and refined: pinning one row by ear must not leave the row next to it on the
    aligner's early end (with row 3 pinned by ear, row 2 would stay on the aligner's early end and the cut would read "hole")."""
    tmp = tempfile.mkdtemp(prefix="cutref-")
    n_ref = 0
    if words:
        for i, p in enumerate(pieces):
            q = pieces[i + 1] if i + 1 < len(pieces) else None
            if q is None or p.get("refine_c1") or q.get("refine_c0"): continue
            if p.get("explicit") and q.get("explicit"): continue   # both sides hand-set: nothing here to refine
            x, y = _edge_tok(p, words, "last"), _edge_tok(q, words, "first")
            if not x or not y: continue
            between = [norm_word(w["text"]) for w in words if w["start"] >= p["c1"] - 0.02 and w["end"] <= q["c0"] + 0.02]
            between = [b for b in between if b]
            forb = (between[:3] + between[-3:]) if between else []
            forb += [f for f in TAGS if f not in (x, y) and f not in forb]
            forb = _clean_forbid(forb, _edge_ctx(p, words, "last"), _edge_ctx(q, words, "first"))
            toks = _heard(pcm, max(p["c0"], p["c1"] - 2.2), p["c1"], q["c0"], q["c0"] + 2.2, tmp)
            x_ok, y_ok, leak = _judge(toks, x, y, forb)
            if x_ok and y_ok and not leak: continue
            log(f"  joint {p['c1']:.3f}|{q['c0']:.3f} [{x}|{y}] failed first listen (x {x_ok} y {y_ok} leak {leak}) heard: {' '.join(toks)[:80]}")
            xw = min((w for w in words if abs(w["end"] - p["c1"]) < 0.6 and norm_word(w["text"]) == x), key=lambda w: abs(w["end"] - p["c1"]), default=None)
            yw = min((w for w in words if abs(w["start"] - q["c0"]) < 0.6 and norm_word(w["text"]) == y), key=lambda w: abs(w["start"] - q["c0"]), default=None)
            # a span's own edges stay where they were set: only the matched side of its joint is refined
            if not x_ok and xw and not p.get("explicit"):
                p["refine_c1"] = {"x": x, "forbid": forb, "lo": max(xw["start"] + 0.10, xw["end"], p["c1"] - 0.10), "hi": max(xw["end"] + 0.35, q["c0"] - 0.04)}
            if not y_ok and yw and not q.get("explicit"):
                q["refine_c0"] = {"y": y, "forbid": forb, "lo": min(yw["start"] - 0.45, p["c1"] + 0.04), "hi": yw["start"] + 0.05}
            if leak and x_ok and y_ok:
                # something between X and Y survived (an untranscribed tag absorbed into a stamp): try the head of Y first
                if yw and not q.get("explicit"): q["refine_c0"] = {"y": y, "forbid": forb, "lo": q["c0"], "hi": yw["start"] + 0.45}
                elif xw and not p.get("explicit"): p["refine_c1"] = {"x": x, "forbid": forb, "lo": xw["start"] + 0.10, "hi": p["c1"]}
    for i, p in enumerate(pieces):
        q = pieces[i + 1] if i + 1 < len(pieces) else None
        if q is not None and p.get("refine_c1"):
            inf = p["refine_c1"]; lo, hi = inf["lo"], inf["hi"]; c1 = min(max(p["c1"], lo), hi)
            y = _edge_tok(q, words, "first"); ok = False
            for it in range(6):
                toks = _heard(pcm, max(p["c0"], c1 - 2.2), c1, q["c0"], q["c0"] + 2.2, tmp)
                x_ok, y_ok, leak = _judge(toks, inf["x"], y, inf["forbid"])
                if x_ok and not leak: ok = True; break
                if leak: hi = min(hi, c1 - 0.03)
                elif not x_ok: lo = max(lo, c1 + 0.03)
                if lo > hi + 1e-6: break
                c1 = (lo + hi) / 2
            n_ref += 1
            if not ok: c1 = p["c1"]      # revert: never ship a bisect that never passed
            log(f"  refine c1 {p['c1']:.3f}->{c1:.3f} [{inf['x']}|{' '.join(inf['forbid'])}] {'OK' if ok else 'UNRESOLVED (kept original)'} heard: {' '.join(toks)[:90]}")
            if not ok:
                review.append({"src_s": round(c1, 2), "why": f"joint after '{inf['x']}' could not be verified clean (heard: {' '.join(toks)[:70]})"})
            p["c1"] = c1
        if i > 0 and p.get("refine_c0"):
            pp = pieces[i - 1]
            inf = p["refine_c0"]; lo, hi = inf["lo"], inf["hi"]; c0 = min(max(p["c0"], lo), hi)
            x = _edge_tok(pp, words, "last"); ok = False
            for it in range(6):
                toks = _heard(pcm, max(pp["c0"], pp["c1"] - 2.2), pp["c1"], c0, c0 + 2.2, tmp)
                x_ok, y_ok, leak = _judge(toks, x, inf["y"], inf["forbid"])
                if y_ok and not leak: ok = True; break
                if leak: lo = max(lo, c0 + 0.03)
                elif not y_ok: hi = min(hi, c0 - 0.03)
                if lo > hi + 1e-6: break
                c0 = (lo + hi) / 2
            n_ref += 1
            if not ok: c0 = p["c0"]
            log(f"  refine c0 {p['c0']:.3f}->{c0:.3f} [{' '.join(inf['forbid'])}|{inf['y']}] {'OK' if ok else 'UNRESOLVED (kept original)'} heard: {' '.join(toks)[:90]}")
            if not ok:
                review.append({"src_s": round(c0, 2), "why": f"joint before '{inf['y']}' could not be verified clean (heard: {' '.join(toks)[:70]})"})
            p["c0"] = c0
    shutil.rmtree(tmp, ignore_errors=True)
    return n_ref

def collapse_interior(pieces, svals, split_sil, log=print):
    """Every measured pause >= split_sil that sits inside a piece (0.25s clear of both edges) becomes a joint: c1 = pause start + 0.10,
    c0 = pause end - 0.07. Stamp-independent."""
    out, n = [], 0
    for p in pieces:
        if p.get("explicit") and not p.get("collapse"): out.append(p); continue   # a span opts in with "collapse": true
        cur = dict(p)
        for st, e in svals:
            if e - st >= split_sil and st > cur["c0"] + 0.25 and e < cur["c1"] - 0.25:
                a = dict(cur); a["c1"] = st + 0.10; a["text"] = cur["text"]; a.pop("_c1_was", None); out.append(a)
                cur = dict(cur); cur["c0"] = e - 0.07; cur.pop("_c0_was", None); n += 1
        out.append(cur)
    if n: log(f"collapse_interior: {n} measured pauses cut")
    return out

def render(plan, pieces, fps):
    """One frame-accurate pass: every piece snapped to the 1/fps grid, trimmed from a single decode
    and joined with the concat FILTER (keeps A/V aligned per segment). The old per-piece encode +
    concat demuxer let frame-quantized video and exact audio drift apart by up to 0.18 s."""
    src, outp = plan["src"], plan["output"]
    tmp = tempfile.mkdtemp(prefix="cut-")
    for p in pieces:
        p["c0"] = round(p["c0"] * fps) / fps
        p["c1"] = round(p["c1"] * fps) / fps
        if p["c1"] - p["c0"] < 2 / fps: p["c1"] = p["c0"] + 2 / fps
    n = len(pieces)
    lines = [f"[0:v]fps={fps},split={n}" + "".join(f"[vi{k}]" for k in range(n)),
             f"[0:a]aresample=48000,asplit={n}" + "".join(f"[ai{k}]" for k in range(n))]
    t = 0.0
    for k, p in enumerate(pieces):
        d = p["c1"] - p["c0"]
        lines.append(f"[vi{k}]trim=start={p['c0']:.4f}:end={p['c1']:.4f},setpts=PTS-STARTPTS[v{k}]")
        lines.append(f"[ai{k}]atrim=start={p['c0']:.4f}:end={p['c1']:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.015,afade=t=out:st={max(0.0, d - 0.015):.4f}:d=0.015[a{k}]")
        p["out_in"], p["out_out"] = round(t, 4), round(t + d, 4); t += d
    lines.append("".join(f"[v{k}][a{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    gpath = os.path.join(tmp, "graph.txt")
    open(gpath, "w").write(";\n".join(lines))
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    raw = outp[:-4] + ".rawaudio.mov"
    pix, crf = cut_format(src)   # yuv420p at crf 15; a source deeper than 8 bits stays 10-bit at crf 12
    r = run(["ffmpeg", "-v", "error", "-i", src, "-/filter_complex", gpath, "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "medium", "-crf", crf, "-pix_fmt", pix, "-r", str(fps),
             "-video_track_timescale", "30000", *BT709, "-c:a", "pcm_s16le", raw, "-y"])
    if r.returncode != 0:
        stop(f"the cut did not render: {r.stderr.strip()[-500:]}")
    if plan.get("audio_clean", True):
        chain = plan.get("audio_chain",
            "highpass=f=70,afftdn=nf=-42:nr=10:tn=1,acompressor=threshold=-22dB:ratio=2.5:attack=6:release=110:makeup=2,"
            "loudnorm=I=-16:TP=-1.5:LRA=8,aresample=48000")
        r = run(["ffmpeg", "-v", "error", "-i", raw, "-c:v", "copy", "-af", chain,
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", outp, "-y"])
        if r.returncode != 0:
            stop(f"the audio chain failed: {r.stderr.strip()[-300:]}")
    else:
        r = run(["ffmpeg", "-v", "error", "-i", raw, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", outp, "-y"])
        if r.returncode != 0:
            stop(f"the audio encode failed: {r.stderr.strip()[-300:]}")
    os.remove(raw)
    shutil.rmtree(tmp, ignore_errors=True)
    return outp

def _struck(plan, words):
    """the indices of the source words the plan struck (plan["_dropped"], written by plan_pieces), matched by text and start"""
    out = set()
    for d in plan.get("_dropped", []):
        k = next((i for i, w in enumerate(words) if w["text"] == d["text"] and abs(w["start"] - d["start"]) <= 0.005), None)
        if k is not None: out.add(k)
    return out

def qc(plan, outp, pieces, intent, review):
    """The cut's QC card (<cut>.qc.json): the cut heard again against the plan's words, its pauses, stutters and (when the plan asks) swears, and the
    two edge gates that keep a word from being clipped: edges_in_voice, every edge still inside the source's voice, and joint_words, every word the cut's
    own transcript hears differently at a joint. Each is a CHECK for an independent listener to confirm; an "edges_exact" span's edges (set by a
    listener already) are listed apart, in exact_edges_in_voice, and do not count"""
    tmp = tempfile.mkdtemp(prefix="cutqc-")
    wav = os.path.join(tmp, "out.wav")
    run(["ffmpeg", "-v", "error", "-i", outp, "-ar", "16000", "-ac", "1", wav, "-y"])
    prompt = prompt_for(language())
    run([WHISPER, "-m", whisper_model(), "-l", language(), "-f", wav] + (["--prompt", prompt] if prompt else []) + ["-ml", "1", "-oj", "-of", outp + ".words"])
    qc_words = load_whispercpp(outp + ".words.json")
    got = [norm_word(w["text"]) for w in qc_words]
    exp = [norm_word(t) for t in " ".join(intent).split() if norm_word(t)]
    exp_c, got_c = "".join(exp), "".join(got)
    sm = difflib.SequenceMatcher(a=exp_c, b=got_c, autojunk=False)
    missing, extra = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace") and i2 - i1 >= 3: missing.append(exp_c[i1:i2])
        if op in ("insert", "replace") and j2 - j1 >= 3: extra.append(got_c[j1:j2])
    # pause scan threshold: -35 dB misses breath-floor gaps in loudness-normalised audio; a plan can pass qc_noise_db -30 to count them
    r = run(["ffmpeg", "-i", outp, "-af", f"silencedetect=noise={plan.get('qc_noise_db', -35)}dB:d=0.10", "-f", "null", "-"])
    sil, cur = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m: cur = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None: sil.append((cur, float(m.group(1)))); cur = None
    dur_r = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                       "-of", "csv=p=0", outp]).stdout.strip())
    interior = [(round(s, 2), round(e - s, 2)) for s, e in sil if s > 0.2 and e < dur_r - 0.2]
    max_gap = max((g for _, g in interior), default=0.0)
    q_rev, q_fail = plan.get("qc_pause_review", 0.25), plan.get("qc_pause_fail", 0.45)
    queue = [{"at_s": a, "why": f"pause {g}s > {q_rev}"} for a, g in interior if g > q_rev]
    fails = [p for p in interior if p[1] > q_fail]
    stutters = []
    for w in range(3, 6):
        for j in range(len(got) - 2 * w):
            if got[j:j + w] == got[j + w:j + 2 * w] and len("".join(got[j:j + w])) > 8:
                stutters.append(" ".join(got[j:j + w]))
    swears = sorted({g for g in got if g in SWEARS}) if plan.get("profanity") == "flag" else []   # a swear is a CHECK only when the plan asks
    unheard_right = sum(1 for m in missing if m == "right")
    word_missing = [m for m in missing if len(m) > 3 and m != "right"]
    # joint map in output time for the review
    joints = [round(p["out_in"], 2) for p in pieces[1:]]
    for rv in review:  # map review src times -> out times
        for p in pieces:
            if p["c0"] - 0.3 <= rv["src_s"] <= p["c1"] + 0.3:
                rv["out_s"] = round(p["out_in"] + max(0.0, rv["src_s"] - p["c0"]), 2); break
    inv = plan.get("_right_inventory", [])
    src_words = load_words(plan["words_json"])
    struck = _struck(plan, src_words)
    edges = edges_in_voice(pieces, Voice(load_pcm(plan["src"]), 16000), src_words, struck)
    exact_edges = [e for e in edges if e["exact"]]
    edges = [e for e in edges if not e["exact"]]
    jwords = joint_words(pieces, src_words, qc_words, struck)
    overlaps = [round(b["c0"], 2) for a, b in zip(pieces, pieces[1:]) if b["c0"] < a["c1"] - 0.005 and abs(b["c0"] - a["c1"]) < 1.0]
    if overlaps: fails.append(("overlap", overlaps))
    card = {"output": outp, "duration_s": round(dur_r, 2), "pieces": len(pieces), "piece_overlaps": overlaps,
            "right_inventory": inv,
            "unheard_kept_right": unheard_right,
            "right_removed": sum(1 for r in inv if r["decision"] == "drop"),
            "right_kept": sum(1 for r in inv if r["decision"] == "keep"),
            "joints_out_s": joints, "missing_words": missing, "extra_words": extra,
            "interior_pauses": interior, "max_interior_pause_s": round(max_gap, 3),
            "review_queue": review + queue, "pause_failures": fails,
            "stutters": sorted(set(stutters)), "profanity": swears,
            "edges_in_voice": edges, "exact_edges_in_voice": exact_edges, "joint_words": jwords,
            "verdict": "PASS" if (not word_missing and not fails and not swears and not stutters and not edges and not jwords) else "CHECK",
            "transcript": " ".join(w["text"] for w in qc_words)}
    json.dump(card, open(outp + ".qc.json", "w"), indent=1)
    shutil.rmtree(tmp, ignore_errors=True)
    return card

def _timebase(rate):
    """(timebase, ntsc flag) for a frame rate: 29.97 is timebase 30 with ntsc TRUE, 23.976 is 24, 59.94 is 60; whole rates are FALSE.
    A rate that is neither (12.5, 0) stops: a Premiere timeline cannot carry it."""
    tb = int(round(rate))
    if tb > 0 and abs(rate - tb) <= 0.005: return tb, "FALSE"
    if tb > 0 and abs(rate - tb * 1000 / 1001) <= 0.01: return tb, "TRUE"
    stop(f"the frame rate {rate} is neither a whole rate nor an NTSC one (23.976, 29.97, 59.94): set xml_fps in plan.json to the file's real rate")

def _probe_dims(path):
    """(width, height) as the picture is DISPLAYED: a phone file stored 1920x1080 with a 90 degree rotation tag is 1080x1920"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", path], capture_output=True, text=True)
    try:
        st = json.loads(r.stdout)["streams"][0]
        w, h = int(st["width"]), int(st["height"])
    except (ValueError, KeyError, IndexError):
        return None
    rot = next((int(d["rotation"]) for d in st.get("side_data_list", []) if "rotation" in d), 0)
    if not rot and (st.get("tags") or {}).get("rotate"): rot = int(st["tags"]["rotate"])
    return (h, w) if rot % 360 in (90, 270) else (w, h)

def premiere_xml(plan, pieces, fps, path):
    # xml_src/xml_fps: point the timeline at the RAW camera file (own frame rate) when the cut
    # itself ran on a 30fps mezzanine; clip in/out are in the FILE's timebase, start/end in the sequence's.
    # Names, paths and markers are XML-escaped, the pathurl is percent-encoded, an NTSC rate keeps its real
    # frame count (29.97 is timebase 30 with ntsc TRUE), and the frame size is read from the file.
    src = plan.get("xml_src", plan["src"])
    ffps = float(plan.get("xml_fps", fps))
    ftb, fntsc = _timebase(ffps)
    stb, sntsc = _timebase(float(fps))
    dims = (plan.get("src_w"), plan.get("src_h"))
    if not all(dims):
        dims = _probe_dims(src)
        if not dims:
            stop(f"cannot read the frame size of {src} for the Premiere XML (set src_w and src_h in plan.json)")
    W, H = dims
    total = int(round(pieces[-1]["out_out"] * fps))
    name = esc(os.path.basename(plan["output"])[:-4])
    fname = esc(os.path.basename(src))
    url = "file://" + urllib.parse.quote(os.path.abspath(src), safe="/")
    def fr(s): return int(round(s * fps))
    def ff(s): return int(round(s * ffps))
    filedef = (f'<file id="src1"><name>{fname}</name><pathurl>{esc(url)}</pathurl>'
               f'<rate><timebase>{ftb}</timebase><ntsc>{fntsc}</ntsc></rate><media><video><samplecharacteristics><width>{W}</width>'
               f'<height>{H}</height></samplecharacteristics></video><audio><channelcount>1</channelcount></audio></media></file>')
    def clip(i, p, kind):      # the first clip defines the file, every later one points at it (FCP7 xmeml)
        track = '<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>' if kind == "a" else ""
        fdef = filedef if (i, kind) == (0, "v") else '<file id="src1"/>'
        return (f'<clipitem id="{kind}-{i}"><name>{fname}</name><enabled>TRUE</enabled><duration>{ff(p["c1"] - p["c0"])}</duration>'
                f'<rate><timebase>{ftb}</timebase><ntsc>{fntsc}</ntsc></rate><start>{fr(p["out_in"])}</start><end>{fr(p["out_out"])}</end>'
                f'<in>{ff(p["c0"])}</in><out>{ff(p["c1"])}</out>{fdef}{track}</clipitem>')
    v = "".join(clip(i, p, "v") for i, p in enumerate(pieces))
    a = "".join(clip(i, p, "a") for i, p in enumerate(pieces))
    markers = "".join(f"<marker><name>{esc(m['name'])}</name><comment>{esc(m.get('comment', ''))}</comment><in>{fr(m['out_s'])}</in><out>-1</out></marker>" for m in plan.get("markers", []))
    rate = f"<rate><timebase>{stb}</timebase><ntsc>{sntsc}</ntsc></rate>"
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq1"><name>{name}</name><duration>{total}</duration>{rate}'
           f'<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>'
           f'<media><video><format><samplecharacteristics><width>{W}</width><height>{H}</height>{rate}</samplecharacteristics></format><track>{v}</track></video>'
           f'<audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format><track>{a}</track></audio></media>{markers}</sequence></xmeml>')
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)

def widen_edges(pieces, lead, tail):
    """The retry ladder's knob: every non-explicit edge moves outward by `lead` (c0, earlier) or `tail` (c1, later) seconds into the quiet beside
    it, never past the speech next to it (the "lo" / "hi" plan_pieces measured) and never into another piece. Done last, so no later trim
    takes it back. Pieces are taken in order and each sees the edges already widened, so two neighbours never end up overlapping."""
    for i, p in enumerate(pieces):
        if p.get("explicit"): continue
        others = [q for j, q in enumerate(pieces) if j != i]
        if lead > 0:
            floor = max([p.get("lo", p["c0"])] + [q["c1"] for q in others if q["c1"] <= p["c0"] + 1e-6])
            if p["c0"] > floor: p["c0"] = round(max(floor, p["c0"] - lead), 3)
        if tail > 0:
            ceil = min([p.get("hi", p["c1"])] + [q["c0"] for q in others if q["c0"] >= p["c1"] - 1e-6])
            if p["c1"] < ceil: p["c1"] = round(min(ceil, p["c1"] + tail), 3)

def shape_pieces(plan, pieces, review):
    """Everything between planning and render: listen to the joints (refine_joints), cut every measured pause (collapse_interior), resolve
    overlaps, trim dead air at the edges, apply the retry ladder's lead_extra / tail_extra, and last put every edge on the voice (snap_edges: no step
    before it may leave a word clipped). The planner's working keys are dropped from the pieces on the way out."""
    pcm, words = load_pcm(plan["src"]), load_words(plan["words_json"])
    if plan.get("refine_joints", True):
        print("refining drop-joints by ear...")
        refine_joints(pieces, pcm, review, words=words)
    if plan.get("collapse_interior", True):
        pieces = collapse_interior(pieces, silences_of(plan["src"], noise=plan.get("sil_noise_db", -35)), plan.get("split_sil", 0.45))
    # adjacent pieces from the same take must never overlap in source time (an overlap replays audio = a stutter):
    # resolve on the measured silence inside the overlap when there is one, else at the midpoint
    sv_all = silences_of(plan["src"], noise=plan.get("sil_noise_db", -35))
    fixed = 0
    for a, b in zip(pieces, pieces[1:]):
        if b["c0"] < a["c1"] and b["c0"] > a["c0"] and abs(b["c0"] - a["c1"]) < 1.0:
            lo, hi = b["c0"], a["c1"]
            sil = next(((st, e) for st, e in sv_all if e > lo - 0.15 and st < hi + 0.15 and e - st >= 0.08), None)
            # the silence must leave both pieces whole: one that ends past b's own end (an aligned word that swallowed a pause and a filler)
            # would start b after it ends
            if sil and sil[0] + 0.14 < sil[1] - 0.07 and sil[0] + 0.14 > a["c0"] + 0.1 and sil[1] - 0.07 < b["c1"] - 0.1:
                a["c1"], b["c0"] = round(sil[0] + 0.14, 3), round(sil[1] - 0.07, 3)
            else: mid = round((a["c1"] + b["c0"]) / 2, 3); a["c1"] = mid; b["c0"] = mid
            fixed += 1
    if fixed: print(f"joint overlap fixed on {fixed} boundaries")
    # head / tail dead air: a piece that opens or closes on >= 0.15s of measured silence gets trimmed to the pad
    trimmed = 0
    for pc in pieces:
        if pc.get("explicit"): continue
        for st, e in sv_all:
            if st <= pc["c0"] + 0.03 and e - pc["c0"] >= 0.15 and e < pc["c1"] - 0.2: pc["c0"] = round(e - 0.07, 3); trimmed += 1
            if e >= pc["c1"] - 0.03 and pc["c1"] - st >= 0.20 and st > pc["c0"] + 0.2: pc["c1"] = round(st + 0.14, 3); trimmed += 1
    if trimmed: print(f"head/tail dead air trimmed on {trimmed} edges")
    lead, tail = float(plan.get("lead_extra", 0.0)), float(plan.get("tail_extra", 0.0))
    widen_edges(pieces, lead, tail)
    if lead or tail: print(f"retry ladder: cut edges widened by lead_extra {lead:g} s and tail_extra {tail:g} s")
    # the voice has the last word on every edge: an end the dead-air trim or a split left inside a decaying word goes on to the decay, on render's
    # frame grid, and whatever still sits inside running voice is on the review queue
    snap_edges(pieces, Voice(pcm, 16000), words, review, fps=plan.get("fps", 30), final=True)
    for pc in pieces:
        for k in EDL_INTERNAL: pc.pop(k, None)
    return pieces



# =================================================================================================================================
# The joint check: did any piece start or end inside a word?
#
# Geometry is the primary test: for each EDL piece, does it start at or before its first source word's start (head >= 0) and end at or after its
# last source word's end (tail >= 0)? It never re-hears anything: it compares the EDL's own timestamps with the source transcript. A transcript
# cannot hear 'ecause' as clipped and a duration can, but a duration test needs the RIGHT word, and a speech engine alone does not reliably find it,
# so the speech-engine ratio is only a secondary, corroborating signal.
#
# Three details keep the verdict honest. (1) Aligner stamps jitter: a word's stamp can sit a few tens of ms early or late, so a joint is only called
# clipped past STAMP_TOL = 0.08 s, and both the raw and the tolerance-adjusted numbers are recorded on every joint. (2) A clip candidate is MEASURED:
# the first moment the 10 ms level rises 12 dB over the room (never under -50 dBFS) inside the word's stamped window is the real onset, and the real
# end the last such moment, so head and tail are read off the waveform whenever the cut's source audio is available. (3) A struck word is not a
# played word, and a cut inside a pause (PAUSE_MIN or more of quiet) is not a clip: no word can be cut where there is no sound.
#
# The retry ladder lives here too (`ladder`, `cut.py ladder`): each retry widens plan "lead_extra" (head) or "tail_extra" (tail), as `needed_pads`
# says, and the cutter applies it to every edge into the quiet beside it. It stops at once when a retry moved no clipped edge (a hand-set span, or
# an edge already against the speech next to it).
#
# JOINTS_ASR (env: "on" | "off"): the corroboration pass transcribes the whole cut again, which costs a full extra transcription on every attempt, so
# it is OFF whenever an aligned sidecar sits beside words.json and ON otherwise. Switching it off is not the same as handing check_joints an empty
# transcript: ratio() returns 0.0 for a joint it cannot find, which would label a clean take `asr_corroborated`, a FAIL. check_joints(asr=False)
# turns the ratio machinery off instead, records `ratio: None`, and leaves geometry as the verdict.
# =================================================================================================================================

# ASR word-onset jitter: below this, a negative head/tail is the transcript disagreeing with the
# waveform, not the cut landing inside a word. Sized from the measured false positives (0.013 -
# 0.160 s of "clipping" over lead-ins the waveform showed to be positive) against the shortest
# words these takes actually contain ("are" 0.082 s, "see" 0.121 s): a tolerance much past 0.08
# would be wider than a whole short word and the guard would stop catching genuine clips.
STAMP_TOL = 0.08
FUNCTION_STAMP_MAX = 0.15          # seconds: the longest a one-syllable function word is allowed to read when no audio can measure it
FUNCTION = frozenset("a an the of to and or but so if is it its in on at for as be i we you he she they my your our that this with".split())
ONSET_RISE_DB, ONSET_FLOOR_DB, ONSET_CEIL_DB = RISE_DB, FLOOR_DB, CEIL_DB
OVERLAP_REACH = 0.15
PAUSE_MIN, PAUSE_EDGE = 0.09, 0.02   # seconds: a cut at least PAUSE_EDGE inside a stretch of at least PAUSE_MIN under the speech threshold has no sound to clip (a stop closure inside a word is shorter)
DROP_MATCH_S = 0.05                  # a struck word is found in the source words by its text and a start this close
TAIL_RUN_MAX, DIP_MIN = 0.15, 0.06   # a window that opens above the threshold for no longer than TAIL_RUN_MAX and then dips for at least DIP_MIN opens on the previous word's tail
def _tok(t): return re.sub(r"[^a-z0-9']", "", t.lower())
class Audio:
    """The cut's SOURCE audio (16 kHz mono, loaded once) for measuring where a word really starts and ends."""
    SR = 16000
    def __init__(self, path):
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(self.SR), "-f", "f32le", "-"], capture_output=True)
        except OSError as e:
            stop(f"ffmpeg is down: could not read the audio of {path}: {type(e).__name__}: {e}")
        if r.returncode != 0 or not r.stdout:
            stop(f"ffmpeg could not read the audio of {path}: {r.stderr.decode(errors='replace').strip()[-160:]}")
        import numpy as np
        self.np = np
        self.y = np.frombuffer(r.stdout, dtype=np.float32)
        self.db = levels_db(self.y, self.SR)   # dBFS per 10 ms hop
    def _span(self, t0, t1):
        a, b = max(0, int(t0 / HOP_S)), min(len(self.db), int(t1 / HOP_S) + 1)
        return a, b
    def threshold(self, t0, t1):
        """12 dB over the room (the quietest tenth of the surrounding 1.2 s), never under -50 dBFS and never over -38 dBFS: in running
        speech with no pause the 'room' is the speech itself, and a threshold above -38 would call a real word start silent"""
        return threshold(self.db, t0, t1)
    def onset(self, t0, t1):
        """The first RISE through the threshold in [t0, t1], or None when nothing in the window does. When the window opens already above
        it (the aligner stamps a word from inside the previous word's decaying tail) and that short run is followed by a real dip,
        the tail is skipped and the onset is the rise after the dip: the first hop above the threshold is the previous word's, not this
        one's (a word stamped 0.3 s early, over a tail of -28 dBFS that falls silent before the word is spoken). Running speech with no dip keeps the
        window start, as before."""
        thr = self.threshold(t0, t1)
        a, b = self._span(t0, t1)
        i = a
        if i < b and self.db[i] >= thr:
            j = i
            while j < b and self.db[j] >= thr: j += 1
            k = j
            while k < b and self.db[k] < thr: k += 1
            if j < b and (j - i) * HOP_S <= TAIL_RUN_MAX and (k - j) * HOP_S >= DIP_MIN and k < b: i = k
            else: return round(a * HOP_S, 3)
        for n in range(i, b):
            if self.db[n] >= thr: return round(n * HOP_S, 3)
        return None
    def pause_at(self, t, need=PAUSE_MIN, edge=PAUSE_EDGE):
        """(start, end) of the pause that holds t: a stretch of at least `need` seconds under the speech threshold with t at least `edge`
        inside both ends. None when t is on speech, in a shorter dip (a stop closure inside a word) or at the rim of a pause."""
        q = pause_run(self.db, self.threshold(t, t), t)
        if q and q[1] - q[0] >= need - 1e-9 and t - q[0] >= edge - 1e-9 and q[1] - t >= edge - 1e-9: return (round(q[0], 3), round(q[1], 3))
        return None
    def offset(self, t0, t1):
        """last time in [t0, t1] still above the threshold (end of that hop), or None"""
        thr = self.threshold(t0, t1)
        a, b = self._span(t0, t1)
        for i in range(b - 1, a - 1, -1):
            if self.db[i] >= thr: return round((i + 1) * HOP_S, 3)
        return None
def stamp_span(w):
    """(start, end) of a word's stamp, a function word's stretched start pulled back to FUNCTION_STAMP_MAX before its end"""
    s, e = w["start"], w["end"]
    if _tok(w["text"]) in FUNCTION and e - s > FUNCTION_STAMP_MAX: s = e - FUNCTION_STAMP_MAX
    return s, e
def wx_sidecar_path(words_p):
    """`cut.py plan` and `cut.py wx` write the aligned sidecar as `words.wx.json` beside `words.json`: the
    words file's stem + '.wx.json'."""
    return os.path.splitext(words_p)[0] + ".wx.json"
def wx_words(doc):
    """words.wx.json ({"segments":[{"words":[{word,start,end}]}]}) -> the words.json word shape."""
    out = []
    for s in doc.get("segments") or []:
        for w in s.get("words") or []:
            if w.get("start") is None or w.get("end") is None: continue
            out.append({"text": (w.get("word") or w.get("text") or "").strip(),
                        "start": float(w["start"]), "end": float(w["end"])})
    return out
def load_src_words(words_p):
    """(src_words, stamps): the aligned sidecar (words.wx.json, written beside words.json) wins when it exists and carries words; words.json's own
    stamps are the fallback. The fallback is never silent: the reason rides in the returned label, into joints.json and onto the command line. A
    sidecar that exists but will not parse stops the run, like any other malformed input: reverting quietly to other stamps is exactly the failure a
    joint check exists to prevent."""
    try:
        dg = json.load(open(words_p))["words"]
    except (OSError, json.JSONDecodeError, KeyError) as e:
        stop(f"could not read words json {words_p}: {type(e).__name__}: {e}")
    wxp = wx_sidecar_path(words_p)
    if not os.path.exists(wxp):
        return dg, "words.json (no %s beside it)" % os.path.basename(wxp)
    try:
        doc = json.load(open(wxp))
    except (OSError, json.JSONDecodeError) as e:
        stop(f"could not read the aligned sidecar {wxp}: {type(e).__name__}: {e}")
    wx = wx_words(doc)
    if not wx:
        return dg, "words.json (%s carries no words)" % os.path.basename(wxp)
    return wx, "whisperx (%s)" % os.path.basename(wxp)
def asr_enabled(words_p, env=None):
    """Should the speech-engine corroboration pass run for THIS take? It is off whenever an aligned sidecar sits beside words.json (the sidecar's stamps are the better measure), on otherwise; JOINTS_ASR=on|off overrides either way.

    `env` is injectable so the default can be tested without mutating the process environment."""
    e = os.environ if env is None else env
    v = (e.get("JOINTS_ASR") or "").strip().lower()
    if v in ("off", "0", "no", "false"): return False
    if v in ("on", "1", "yes", "true"): return True
    return not os.path.exists(wx_sidecar_path(words_p))
def first_word_after(words, c0):
    for w in words:
        if w["start"] >= c0 - 0.02: return w
    return None
def ratio(out_words, t_out, src_word, window=0.25):
    cands = [w for w in out_words if abs(w["start"] - t_out) <= window and _tok(w["text"]) == _tok(src_word["text"])]
    if not cands: cands = [w for w in out_words if abs(w["start"] - t_out) <= window]
    if not cands: return 0.0
    w = min(cands, key=lambda x: abs(x["start"] - t_out))
    src = max(0.05, src_word["end"] - src_word["start"])
    return round((w["end"] - w["start"]) / src, 3)
def _valley(audio, t0, t1):
    """the quietest moment in [t0, t1]: where two words that the aligner overlapped really meet (the midpoint without audio)"""
    if audio is None or t1 <= t0: return (t0 + t1) / 2
    lo, hi = audio._span(t0, t1)
    if hi - lo < 1: return (t0 + t1) / 2
    return round((lo + int(audio.np.argmin(audio.db[lo:hi])) + 0.5) * HOP_S, 3)
def measured_span(w, audio, prev=None, nxt=None):
    """(start, end) of a word: its stamp, a stretched function word pulled back to FUNCTION_STAMP_MAX, stamps that overlap the
    neighbouring word's cut apart at the quietest moment around the overlap (an aligner that overlaps two words is wrong about at
    least one edge; the real word break is the dip in the sound, which can sit just past the earlier word's stamped end), then pulled in to the part of the stamp window the waveform actually carries (never widened: an aligner stretches words
    into silence, it does not shrink them)"""
    s, e = stamp_span(w)
    if prev is not None and prev["end"] > s: s = max(s, min(_valley(audio, s, min(e, prev["end"] + OVERLAP_REACH)), e))
    if nxt is not None and nxt["start"] < e: e = min(e, max(_valley(audio, max(s, nxt["start"] - OVERLAP_REACH), e), s))
    if audio is not None:
        on, off = audio.onset(s, e), audio.offset(s, e + 0.05)
        if on is not None: s = max(s, on)
        if off is not None: e = min(e, off)
    return s, e
CONTIG_S = 0.02    # the cutter snaps c0/c1 to the frame grid; closer than this is one edge, not two
def dropped_indices(src_words, dropped):
    """Indexes of the source words the plan struck (the EDL's "dropped": [{text, start, end}]): found by text and a start within DROP_MATCH_S.
    A struck word is never played, so it is never the first or last word of a piece and can never be the clipped one."""
    out = set()
    for d in dropped or []:
        tk = _tok(d.get("text", ""))
        k = next((i for i, w in enumerate(src_words) if i not in out and _tok(w["text"]) == tk and abs(w["start"] - d["start"]) <= DROP_MATCH_S), None)
        if k is not None: out.add(k)
    return out
def geometry(pieces, src_words, eps=0.02, tol=STAMP_TOL, audio=None, contig=CONTIG_S, dropped=None):
    """Deterministic clipped-word test: for each EDL piece, `first` is the first source word with end > c0 + eps and `last` is the last source word
    with start < c1 - eps (the words the piece actually plays). head = first.start - c0 (seconds of pad before the first word; negative means the piece
    starts INSIDE that word). tail = c1 - last.end (seconds of pad after the last word; negative means the piece ends INSIDE that word). A piece with
    no source words strictly inside it (pure b-roll or silence) is never clipped: there is no word for the cut to land in.

    Tolerance: head/tail stay RAW (what the stamps say); head_adj/tail_adj carry the same number credited with `tol` seconds of aligner onset jitter
    (what the verdict is made on). Clipped means the tolerance-adjusted number is still negative: head < -tol, tail < -tol.

    Measured edges: with `audio` (an Audio of the cut's source) an edge word is re-read off the waveform: a word whose stamp straddles the edge but
    whose sound is over before c0 (or starts after c1) is not in the piece at all, and a stretched start or end is measured where the sound really
    begins or ends; where the aligner overlapped an edge word with its neighbour, the two are split at the dip in the sound between them
    (measured_span). Without audio a stretched function-word stamp is capped (stamp_span). head_stamp / tail_stamp record the stamp numbers when
    they were replaced.

    Shared edges: an edge shared with its neighbour (the previous piece's c1 == this c0, as the overlap fixer and a pair of adjacent segments leave
    it) drops no source: the sound plays straight through it, so a word straddling it is not clipped. head_splice / tail_splice say which edges are
    real cuts; only those can set `clipped`.

    Struck words and pauses: `dropped` (the EDL's list of struck words) takes those words out of the words a piece plays; an edge the stamps call
    clipped that sits inside a pause of PAUSE_MIN or more (Audio.pause_at) is read off the waveform: head is then the silence before the next sound,
    tail the silence after the last one, and `head_pause` / `tail_pause` say which pause held the cut."""
    skip = dropped_indices(src_words, dropped)
    out = []
    for i, p in enumerate(pieces):
        head_splice = i == 0 or abs(pieces[i - 1]["c1"] - p["c0"]) > contig
        tail_splice = i == len(pieces) - 1 or abs(pieces[i + 1]["c0"] - p["c1"]) > contig
        c0, c1 = p["c0"], p["c1"]
        idx = [k for k, w in enumerate(src_words) if w["end"] > c0 + eps and w["start"] < c1 - eps and k not in skip]
        around = lambda k: (src_words[k - 1] if k > 0 else None, src_words[k + 1] if k + 1 < len(src_words) else None)
        # an edge word whose sound lies wholly outside the piece (its stamp was stretched across the edge) is not played by it
        while idx and measured_span(src_words[idx[0]], audio, *around(idx[0]))[1] <= c0 + eps and (audio is not None or src_words[idx[0]]["start"] < c0 - tol):
            idx = idx[1:]
        while idx and measured_span(src_words[idx[-1]], audio, *around(idx[-1]))[0] >= c1 - eps and (audio is not None or src_words[idx[-1]]["end"] > c1 + tol):
            idx = idx[:-1]
        inside = [src_words[k] for k in idx]
        if not inside:
            out.append({"seg": p.get("seg"), "c0": c0, "c1": c1, "out_in": p.get("out_in"),
                        "first": None, "last": None, "head": None, "tail": None,
                        "head_adj": None, "tail_adj": None, "clipped": False, "head_splice": head_splice, "tail_splice": tail_splice})
            continue
        first, last = inside[0], inside[-1]
        k_first, k_last = idx[0], idx[-1]
        head = round(first["start"] - c0, 3)
        tail = round(c1 - last["end"], 3)
        rec = {}
        if head < -tol:
            h2 = round(measured_span(first, audio, *around(k_first))[0] - c0, 3)
            if h2 != head: rec["head_stamp"], head = head, h2
            q = audio.pause_at(c0) if head < -tol and audio is not None else None
            if q: rec.setdefault("head_stamp", head); rec["head_pause"] = list(q); head = round(q[1] - c0, 3)
        if tail < -tol:
            t2 = round(c1 - measured_span(last, audio, *around(k_last))[1], 3)
            if t2 != tail: rec["tail_stamp"], tail = tail, t2
            q = audio.pause_at(c1) if tail < -tol and audio is not None else None
            if q: rec.setdefault("tail_stamp", tail); rec["tail_pause"] = list(q); tail = round(c1 - q[0], 3)
        head_adj, tail_adj = round(head + tol, 3), round(tail + tol, 3)
        out.append(dict({"seg": p.get("seg"), "c0": c0, "c1": c1, "out_in": p.get("out_in"),
                    "first": first["text"], "last": last["text"], "head": head, "tail": tail,
                    "head_adj": head_adj, "tail_adj": tail_adj, "head_splice": head_splice, "tail_splice": tail_splice,
                    "clipped": (head < -tol and head_splice) or (tail < -tol and tail_splice)}, **rec))
    return out
def check_joints(edl, src_words, out_words, floor=0.8, eps=0.02, tol=STAMP_TOL, stamps="words.json", asr=True, audio=None):
    """Geometry decides PASS/FAIL; the speech-engine ratio only corroborates a marginal geometry reading or gets recorded as a non-failing
    `asr_variance` warning. Each joint (one per piece after the first, `edl["pieces"][1:]`) carries its OWN piece's head (does IT start inside its
    first word) and the PREVIOUS piece's tail (does the piece that just finished playing end inside ITS last word): together the two source-side edges
    of that one splice point in the output. The very last piece's own tail never finishes as anyone's "previous piece", since nothing splices in after
    it, so it gets one extra check below. A head failure is labelled `fixable_by: "head_pad"` and a tail failure `fixable_by: "tail_pad"`, because
    the retry ladder widens the two sides separately; `needed_pads()` turns a whole result into the pad to widen.

    The asr_corroborated band deliberately stays at 0 <= head < 0.02 rather than widening with the tolerance: `first_word_after` only keeps
    reporting on the joint's OWN first word while head >= -0.02, so below that the ratio describes a LATER word and cannot corroborate anything
    about this cut.

    `asr=False` drops the ratio machinery entirely: no corroboration, no variance warnings, `ratio`/`out_dur` recorded as None. Geometry is the
    verdict either way; this only stops the run paying a whole extra transcription per attempt for a warning."""
    pieces = edl["pieces"]
    geo = geometry(pieces, src_words, eps, tol, audio, dropped=edl.get("dropped"))
    skip = dropped_indices(src_words, edl.get("dropped"))
    played = [w for k, w in enumerate(src_words) if k not in skip]    # the words a joint can be named for: never a struck one
    joints, warnings = [], []
    for i in range(1, len(pieces)):
        p = pieces[i]
        w = first_word_after(played, p["c0"])
        if not w: continue
        r = ratio(out_words, p["out_in"], w) if asr else None
        head, tail = geo[i]["head"], geo[i - 1]["tail"]
        head_adj, tail_adj = geo[i]["head_adj"], geo[i - 1]["tail_adj"]
        head_clip = head is not None and head < -tol and geo[i].get("head_splice", True)
        tail_clip = tail is not None and tail < -tol and geo[i - 1].get("tail_splice", True)
        j = {"t": round(p["out_in"], 3), "word": w["text"], "src_dur": round(w["end"] - w["start"], 3),
             "out_dur": round(r * (w["end"] - w["start"]), 3) if asr else None, "ratio": r,
             "head": head, "tail": tail,
             "head_adj": head_adj, "tail_adj": tail_adj, "clipped": head_clip or tail_clip}
        if head_clip:
            j["reason"], j["ok"], j["fixable_by"] = "clipped_head", False, "head_pad"
        elif tail_clip:
            j["reason"], j["ok"], j["fixable_by"] = "clipped_tail", False, "tail_pad"
        elif asr and head is not None and 0 <= head < 0.02 and r < floor:
            j["reason"], j["ok"], j["fixable_by"] = "asr_corroborated", False, "head_pad"
        elif asr and r < floor:
            j["reason"], j["ok"], j["fixable_by"] = "asr_variance", True, None
            warnings.append({"word": w["text"], "ratio": r, "head": head, "t": j["t"]})
        else:
            j["reason"], j["ok"], j["fixable_by"] = "ok", True, None
        joints.append(j)
    if pieces:
        last = geo[-1]
        if last["tail"] is not None and last["tail"] < -tol and last.get("tail_splice", True):
            joints.append({"t": round(pieces[-1]["out_out"], 3), "word": last["last"], "src_dur": None,
                           "out_dur": None, "ratio": None, "head": None, "tail": last["tail"],
                           "head_adj": None, "tail_adj": last["tail_adj"], "clipped": True,
                           "reason": "clipped_tail", "ok": False, "fixable_by": "tail_pad"})
    failed = sum(1 for j in joints if not j["ok"])
    return {"joints": joints, "verdict": "PASS" if failed == 0 else "FAIL", "failed": failed,
            "floor": floor, "stamp_tol": tol, "stamps": stamps, "asr": asr, "warnings": warnings}
def needed_pads(result):
    """Which pad the retry ladder should widen for THIS result: 'head' | 'tail' | 'both' | 'none'.

    Each joint reports ONE reason, so a joint clipped at both ends is labelled `fixable_by:
    "head_pad"` and its tail would only surface on the next pass -- a wasted re-cut out of three.
    Both edges are read here instead: head_adj/tail_adj are already the tolerance-adjusted numbers,
    so `< 0` on a failing joint is exactly "this edge is genuinely clipped".

    'none' means nothing failed, or every failure is something no pad can move -- the ladder must
    stop and say so instead of burning re-cuts."""
    head = tail = False
    for j in result.get("joints") or []:
        if j.get("ok", True): continue
        if j.get("fixable_by") == "head_pad" or (j.get("head_adj") is not None and j["head_adj"] < 0): head = True
        if j.get("fixable_by") == "tail_pad" or (j.get("tail_adj") is not None and j["tail_adj"] < 0): tail = True
    if head and tail: return "both"
    if head: return "head"
    if tail: return "tail"
    return "none"
LADDER_STEP, LADDER_MAX = 0.04, 0.12   # seconds a retry widens plan "lead_extra" / "tail_extra" by, and the most they may reach
def failing_edges(result, edl):
    """[(piece index, "c0" | "c1")]: the cut edges the failed joints are about. A joint's `t` is the output time its piece starts at; a head
    failure is that piece's c0, a tail failure the c1 of the piece before it (the last piece's own c1 for the final joint)."""
    pieces, out = edl["pieces"], []
    for j in result.get("joints") or []:
        if j.get("ok", True): continue
        t = j.get("t")
        i = next((k for k, p in enumerate(pieces) if t is not None and abs(p.get("out_in", -1.0) - t) < 0.01), None)
        if j.get("fixable_by") == "head_pad" or (j.get("head_adj") is not None and j["head_adj"] < 0):
            if i is not None: out.append((i, "c0"))
        if j.get("fixable_by") == "tail_pad" or (j.get("tail_adj") is not None and j["tail_adj"] < 0):
            k = i - 1 if i is not None else (len(pieces) - 1 if pieces and t is not None and abs(pieces[-1].get("out_out", -1.0) - t) < 0.01 else None)
            if k is not None and k >= 0: out.append((k, "c1"))
    return out
def retry_moved_an_edge(result, edl, prev_edl):
    """did the retry that produced `edl` move a cut edge the failed joints are about? Edges are compared to the millisecond; a take whose piece
    count changed counts as moved. When the failed joints name no edge, any edge anywhere counts."""
    a, b = prev_edl["pieces"], edl["pieces"]
    if len(a) != len(b): return True
    named = failing_edges(result, edl) or [(i, key) for i in range(len(b)) for key in ("c0", "c1")]
    return any(abs(round(a[i][key], 3) - round(b[i][key], 3)) >= 0.0005 for i, key in named)
def ladder(plan, result, edl, prev_edl=None):
    """One retry of the clipped-word ladder: (True, message) with `plan` widened in place, or (False, reason) when no retry can help.

    The knob is plan "lead_extra" (head) and "tail_extra" (tail): the cutter moves EVERY non-explicit edge outward by that much into the
    quiet beside it, never into the neighbouring speech. (pad_pre alone only moves the start of the first piece of a segment, so it
    cannot be the knob.) `needed_pads` says which side. The ladder stops
    at once when nothing a pad can move failed, when the retry before this one did not move the edge that is still clipped (`prev_edl`
    is the cut it started from: a hand-set span, or an edge already against the speech beside it), and when both knobs are at LADDER_MAX."""
    need = needed_pads(result)
    if need == "none":
        return False, "no pad can move this failure"
    if prev_edl is not None and not retry_moved_an_edge(result, edl, prev_edl):
        return False, "the last retry moved no clipped edge: it is a hand-set span, or already against the speech next to it"
    lead, tail = float(plan.get("lead_extra", 0.0)), float(plan.get("tail_extra", 0.0))
    new_lead = min(LADDER_MAX, lead + LADDER_STEP) if need in ("head", "both") else lead
    new_tail = min(LADDER_MAX, tail + LADDER_STEP) if need in ("tail", "both") else tail
    if (new_lead, new_tail) == (lead, tail):
        return False, f"lead_extra and tail_extra are already at the {LADDER_MAX:g} s maximum"
    plan["lead_extra"], plan["tail_extra"] = round(new_lead, 3), round(new_tail, 3)
    return True, f"widening the cut edges: lead_extra {plan['lead_extra']:g} s, tail_extra {plan['tail_extra']:g} s"
def joints_run(cut, edl_p, words_p, out):
    """Do the joint check's work and write `out`; stop on any input or service failure (no silent fallback)."""
    try:
        edl = json.load(open(edl_p))
    except (OSError, json.JSONDecodeError) as e:
        stop(f"could not read edl json {edl_p}: {type(e).__name__}: {e}")
    src_words, stamps = load_src_words(words_p)
    asr = asr_enabled(words_p)
    audio = Audio(edl["src"]) if edl.get("src") and os.path.exists(edl["src"]) else None   # the source the pieces were cut from
    if not asr:                                  # geometry only: no wav, no speech engine, no wall time
        r = check_joints(edl, src_words, [], stamps=stamps, asr=False, audio=audio)
        json.dump(r, open(out, "w"), indent=1)
        return r
    tmp = tempfile.mkdtemp(prefix="jc-"); wav = os.path.join(tmp, "cut.wav")
    try:
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", cut, "-ar", "16000", "-ac", "1", wav], check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            stop(f"ffmpeg is down: could not extract audio from {cut}: {type(e).__name__}: {e}")
        import words as word_engine              # here, not at the top: the word engine imports this module
        out_words = word_engine.transcribe(wav)
        r = check_joints(edl, src_words, out_words, stamps=stamps, audio=audio)
        json.dump(r, open(out, "w"), indent=1)
        return r
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # one wav per attempt, up to 3 attempts per take


# =================================================================================================================================
# The plan: the take's sentences and the segments the cutter is asked for.
# =================================================================================================================================

RUN_ON_WORDS = 25   # a take with no ending mark at all and more words than this is split at its pauses
RUN_ON_PAUSE_S = 0.6


def _sentence(ws):
    return {"text": " ".join(x["text"] for x in ws), "start": ws[0]["start"], "end": ws[-1]["end"], "words": ws}


def sentences(words):
    """The take's sentences: runs of words that end on . ? or !, each {"text", "start", "end", "words"} (a last run with no ending mark is one too).
    A take the transcriber left with no ending mark at all (more than RUN_ON_WORDS words in one run) is split where the speaker paused for
    RUN_ON_PAUSE_S or more, five words at the least to a sentence, so a long unpunctuated take is still a list of sentences to edit."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.?!]$", w["text"]):
            out.append(_sentence(cur))
            cur = []
    if cur:
        out.append(_sentence(cur))
    if len(out) == 1 and len(out[0]["words"]) > RUN_ON_WORDS:
        ws, pieces, start = out[0]["words"], [], 0
        for i in range(1, len(ws)):
            if ws[i]["start"] - ws[i - 1]["end"] >= RUN_ON_PAUSE_S and i - start >= 5 and len(ws) - i >= 3:
                pieces.append(_sentence(ws[start:i]))
                start = i
        pieces.append(_sentence(ws[start:]))
        out = pieces
    return out


def sentence_spans(words):
    """Sentences of the take, each carrying the UNFILTERED word range it covers. Boundaries are decided on the words without fillers (a filler never
    ends or opens a sentence), but `match` is rebuilt from the full word list between the sentence's first and last kept word: a contiguous run
    of words.json, which is what both the cutter's matcher and the script gate look for. `occurrence` counts identical match strings in transcript
    order, so two textually identical sentences are cut from two different places."""
    idx = [i for i, w in enumerate(words) if not w.get("filler")]
    out, pos, seen = [], 0, {}
    for s in sentences([words[i] for i in idx]):
        n = len(s["words"])
        a, b = idx[pos], idx[pos + n - 1]
        pos += n
        match = " ".join(w["text"] for w in words[a:b + 1])
        seen[match] = seen.get(match, 0) + 1
        out.append({"n": len(out) + 1, "start": s["start"], "end": s["end"], "i0": a, "i1": b, "match": match, "occurrence": seen[match]})
    return out


def segments(spans, hook_first=None):
    """plan.segments: one segment per sentence, in transcript order, labelled 01, 02 ... With hook_first (a sentence number) that sentence goes
    first and is labelled "hook". Off by default: a cut keeps the order the speaker said things in."""
    segs = [{"match": s["match"], "occurrence": s["occurrence"], "label": "%02d" % s["n"]} for s in spans]
    if hook_first:
        k = int(hook_first)
        if not 1 <= k <= len(segs):
            stop(f"--hook-first {k}: the take has {len(segs)} sentences")
        segs.insert(0, dict(segs.pop(k - 1), label="hook"))
    return segs


def build_plan(spans, name, root, work, hook_first=None):
    """The plan for one take of the talking-head chain: the mezzanine at <root>/mez/<name>.mp4, the words in <work>/words.wx.json, the cut in <work>."""
    plan = {"name": name, "src": "%s/mez/%s.mp4" % (root, name),
            "words_json": "%s/words.wx.json" % work,
            "output": "%s/%s-cut.mp4" % (work, name), "fps": 30,
            "pad_pre": 0.06, "pad_post": 0.07, "split_pre": 0.06, "split_post": 0.08,
            "gap_max": 0.20, "split_sil": 0.25,
            # -35 dB: a -30 dB floor reads quiet word onsets as silence and cuts inside words. The joint listener stays off because the voice
            # measurements in snap_edges already decide every edge.
            "sil_noise_db": -35, "qc_noise_db": -35, "refine_joints": False,
            "drop_fillers": True, "right_mode": "off", "profanity": "off",
            "cut_ranges": [], "audio_clean": False,
            "segments": segments(spans, hook_first),
            "gate": {"approved": False}}
    if hook_first:
        plan["hook_first"] = int(hook_first)
    return plan


def wx_sidecar(words):
    """words.json's words in the shape the cutter's loader reads: one transcript, source time"""
    return {"segments": [{"words": [{"word": w["text"], "start": w["start"], "end": w["end"]}
                                    for w in words]}]}


def check_plan(plan, words_path=None):
    """Static check of a plan BEFORE cutting: every segment match resolves (occurrence honoured), every strike resolves inside its segment as a
    whole word, a match found only by the head-and-tail fallback is flagged (the span is much longer than the text), and the kept text is
    listed in output order with source times and an estimated duration. -> (lines, failures)"""
    words = load_words(words_path or plan["words_json"])
    fails, warns, total = [], [], 0.0
    lines = [f"{'#':>2} {'src_in':>7} {'src_out':>7} {'dur':>5}  label     kept text"]
    for i, seg in enumerate(plan["segments"]):
        if seg.get("span"):
            c0, c1 = seg["span"]
            total += c1 - c0
            lines.append(f"{i:>2} {c0:7.2f} {c1:7.2f} {c1 - c0:5.1f}  {seg.get('label', '')[:8]:8s}  [explicit span] {seg['match'][:70]}")
            continue
        hit = locate(words, seg["match"], seg.get("occurrence", 1))
        if hit is None:
            fails.append(f"segment {i} ({seg.get('label', '')}): its words are not in the transcript: {seg['match'][:80]!r}")
            continue
        a, b = hit
        n_match = len([t for t in seg["match"].split() if norm_word(t)])
        if (b - a + 1) > n_match * 1.25 + 3:
            warns.append(f"segment {i}: spans {b - a + 1} words for a match of {n_match}: check for a restart inside")
        problems = []

        def bad(msg):
            problems.append(msg)
            raise ValueError(msg)
        try:
            struck = {a + k for k in resolve_drops(words[a:b + 1], seg.get("drop", []), fail=bad)}
        except ValueError:
            struck = set()
        fails += [f"segment {i}: {m}" for m in problems]
        kept = [words[k] for k in range(a, b + 1) if k not in struck]
        dur = sum(w["end"] - w["start"] for w in kept) + 0.08 * max(0, len(kept) - 1)
        total += dur
        lines.append(f"{i:>2} {words[a]['start']:7.2f} {words[b]['end']:7.2f} {dur:5.1f}  {seg.get('label', '')[:8]:8s}  {' '.join(w['text'] for w in kept)[:110]}")
    lines.append(f"\nestimated kept speech: {total:.1f}s over {len(plan['segments'])} segments")
    lines += ["WARN " + w for w in warns] + ["FAIL " + f for f in fails]
    return lines, fails



# =================================================================================================================================
# Command line
# =================================================================================================================================

def cut_main(plan_path):
    plan = json.load(open(plan_path))
    pieces, intent, review, fps = plan_pieces(plan)
    if plan.get("dry_run"):
        for p in pieces:
            print(f"{p['c0']:8.3f} {p['c1']:8.3f} {p['c1']-p['c0']:6.3f}  {p['label']:8s} {p['text'][:80]}")
        print("est duration", round(sum(p['c1'] - p['c0'] for p in pieces), 2), "review", len(review))
        inv = plan.get("_right_inventory", [])
        print(f"\n'right' inventory: {len(inv)} in kept spans, {sum(1 for r in inv if r['decision'] == 'drop')} dropped")
        for r in inv:
            print(f"  {r['src_s']:7.2f} {r['kind']:7s} {r['decision']:4s} sil {'Y' if r['sil_before'] else '-'}/{'Y' if r['sil_after'] else '-'}  {r['ctx']}")
        return
    pieces = shape_pieces(plan, pieces, review)
    outp = render(plan, pieces, fps)
    write_edl(plan, outp, fps, pieces)
    card = qc(plan, outp, pieces, intent, review)
    premiere_xml(plan, pieces, fps, outp[:-4] + ".xml")
    print(json.dumps({k: card[k] for k in ("verdict", "duration_s", "pieces", "missing_words",
                                           "extra_words", "max_interior_pause_s", "pause_failures", "stutters", "profanity")}, indent=1))
    for e in card["edges_in_voice"]:
        print(f"CHECK edge {e['label']} {e['edge']} at {e['src_s']:.3f} (out {e['out_s']:.2f} s): {e['why']}")
    for j in card["joint_words"]:
        print(f"CHECK heard at {j['out_s']:.2f} s ({j['where']}): expected '{j['expected']}', heard '{j['heard']}'")
    print("review_queue:", len(card["review_queue"]))


def joints_cmd(cut, edl_p, words_p, out):
    r = joints_run(cut, edl_p, words_p, out)
    print(r["verdict"], f"{r['failed']} clipped of {len(r['joints'])} joints",
          f"(stamps: {r['stamps']}, tolerance {r['stamp_tol']}s, asr: {'on' if r['asr'] else 'off'})")
    for j in r["joints"]:
        if j["ok"]:
            continue
        if j["reason"] == "clipped_tail":
            print(f"  clipped (tail): '{j['word']}' at {j['t']}s: ends {abs(j['tail'])}s inside its last word (tolerance {r['stamp_tol']}s); widen tail_extra")
        elif j["reason"] == "clipped_head":
            print(f"  clipped (head): '{j['word']}' at {j['t']}s: starts {abs(j['head'])}s inside its first word (tolerance {r['stamp_tol']}s); widen lead_extra")
        else:
            print(f"  clipped: '{j['word']}' at {j['t']}s, ratio {j['ratio']} (speech-engine corroborated); widen lead_extra")
    if r["verdict"] == "FAIL":
        print(f"  edge to widen: {needed_pads(r)}")
    for wnote in r["warnings"]:
        print(f"  asr variance (not a failure): '{wnote['word']}' ratio {wnote['ratio']} at {wnote['t']}s")
    sys.exit(0 if r["verdict"] == "PASS" else 1)


def ladder_cmd(args):
    plan_p, joints_p, edl_p = args[0:3]
    prev_p = args[3] if len(args) > 3 else None
    with open(plan_p) as f1, open(joints_p) as f2, open(edl_p) as f3:
        plan, result, edl = json.load(f1), json.load(f2), json.load(f3)
    prev = json.load(open(prev_p)) if prev_p and os.path.exists(prev_p) else None
    ok, msg = ladder(plan, result, edl, prev)
    if not ok:
        print(f"LADDER STOP: {msg}")
        sys.exit(3)
    with open(plan_p, "w") as f:
        json.dump(plan, f, indent=1)
    print(f"LADDER: {msg}")


def _main():
    cmd, a = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "cut":
        cut_main(a[0])
    elif cmd == "joints":
        if len(a) < 4:
            raise IndexError("joints needs <cut.mp4> <cut.edl.json> <words.json> <joints.json>")
        joints_cmd(*a[:4])
    elif cmd == "ladder":
        if len(a) < 3:
            raise IndexError("ladder needs <plan.json> <joints.json> <cut.edl.json>")
        ladder_cmd(a[:4])
    elif cmd == "sentences":
        words_p, out = a[0], a[1]
        spans = sentence_spans(json.load(open(words_p))["words"])
        with open(out, "w") as f:
            json.dump({"sentences": spans}, f, indent=1)
        print(f"{len(spans)} sentences -> {out}")
    elif cmd == "plan":
        words_p, sent_p, name, root, work = a[0:5]
        hook = int(a[a.index("--hook-first") + 1]) if "--hook-first" in a else None
        words = json.load(open(words_p))["words"]
        plan = build_plan(json.load(open(sent_p))["sentences"], name, root, work, hook)
        if "--faceless" in a:
            plan["speaker"] = False                  # nobody on camera: the QC gates leave the face rule out
        with open(os.path.join(work, "words.wx.json"), "w") as f:
            json.dump(wx_sidecar(words), f, indent=1)
        with open(os.path.join(work, "plan.json"), "w") as f:
            json.dump(plan, f, indent=1)
        print("plan:", len(plan["segments"]), "segments")
    elif cmd == "wx":
        src, out = a[0], a[1]
        with open(out, "w") as f:
            json.dump(wx_sidecar(json.load(open(src))["words"]), f, indent=1)
        print("wx sidecar ->", out)
    elif cmd == "check-plan":
        lines, fails = check_plan(json.load(open(a[0])), a[1] if len(a) > 1 else None)
        print("\n".join(lines))
        sys.exit(1 if fails else 0)
    elif not cmd:
        raise IndexError("no command")
    else:
        stop(f"unknown command {cmd!r}: usage: {USAGE}")


if __name__ == "__main__":
    support.cli(_main, USAGE)
