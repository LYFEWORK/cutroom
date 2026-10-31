#!/usr/bin/env python3
"""avskew.py <src> [--json out] [--tol S]                         measure a source's video against its audio timeline
   avskew.py --verify <mez.mp4> [--json j] [--tol S] [--dropped S]   check a conformed mezzanine

A stalled capture (a phone's thermal or storage stall, dropped packets) stamps its video timestamps with time in which no frame was recorded.
Converting it to a constant frame rate then FILLS that time with duplicate frames, so the mezzanine's picture runs late against its own audio from
the first stall onward, and every c0/c1 in the EDL is an audio-domain number applied to both streams, so the cut shows the wrong moment. A real
case: 8371 packets over a 294.762 s span, 15.695 s of stalls, audio 279.668 s; the closing line played the speaker mouth-shut under a live voice.

The gate runs BEFORE the mezzanine. Exit 0 = the two timelines agree, build it as is. Exit 2 = they do not; the STOP line names the stall spans and
both spans, and --json still gets written so the caller can conform instead of refusing. --verify is the other half: after a conform, the
mezzanine's own video and audio durations must agree and freezedetect must find no held frame longer than half a second, or the mezzanine is
not shipped."""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import statistics
import subprocess

import common

stop = common.stop

TOL_S = 0.10          # one frame plus a little; the joint check already works to 0.040 s
FACTOR = 2.5          # a gap this many nominal frame intervals wide is a stall, not jitter
JOIN_S = 1.0          # stalls closer than this read as one span in the message
FREEZE_MIN_S = 0.5    # a held frame shorter than this is a still moment, not a stalled capture


# --- pure ---------------------------------------------------------------------------------------

def parse_rate(s):
    """'30000/1001' -> 29.97. ffprobe writes '0/0' when it cannot guess a rate at all."""
    n, _, d = (s or "").partition("/")
    try:
        num = float(n); den = float(d) if d else 1.0
    except ValueError:
        return 0.0
    return num / den if den and num else 0.0


def nominal_fps(r_rate, dts):
    """The rate the capture MEANT to run at: what the conform renumbers frames onto.

    r_frame_rate is only a guess: a screen recorder writes 600/1 on a 60 fps capture, and
    renumbering onto 600 would hand the cut a mezzanine ten times too short. Trust the container
    only when it agrees with the median packet interval; otherwise the packets win."""
    med = statistics.median(dts) if dts else 0.0
    from_pkts = 1.0 / med if med > 0 else 0.0
    if r_rate > 0 and (from_pkts == 0 or abs(r_rate - from_pkts) <= 0.05 * from_pkts):
        return r_rate
    if from_pkts > 0:
        return from_pkts
    stop("cannot tell what frame rate this source runs at: no usable r_frame_rate and no packet intervals")


def holes_of(pts, fps, factor=FACTOR):
    """[(t, gap)] for every packet-to-packet PTS gap wider than factor x the frame interval.

    ffprobe hands back packets in DECODE order, so a B-pyramid looks like a hole at every reorder
    point until the list is sorted."""
    p = sorted(float(x) for x in pts)
    nom = 1.0 / fps
    return [(p[i], p[i + 1] - p[i]) for i in range(len(p) - 1) if p[i + 1] - p[i] > factor * nom]


def holes_total(hs, fps):
    """Fake time only: each stall still carries one legitimate frame interval."""
    nom = 1.0 / fps
    return sum(max(0.0, g - nom) for _, g in hs)


def cluster(hs, fps, join_s=JOIN_S):
    """[(t0, t1, fake_s, n)]: a run of micro-stalls is one stall to the person reading the line. A middle stall that is 8 packet gaps in a row,
    listed one by one, makes the STOP message unreadable; what the reader needs is where to scrub."""
    nom = 1.0 / fps
    out = []
    for t, g in sorted(hs):
        if out and t - out[-1][1] <= join_s:
            a, _, exc, n = out[-1]
            out[-1] = (a, t + g, exc + max(0.0, g - nom), n + 1)
        else:
            out.append((t, t + g, max(0.0, g - nom), 1))
    return out


def render_holes(clusters):
    return ", ".join(f"{a:.3f} s ({e:.3f} s)" if n == 1 else f"{a:.3f}-{b:.3f} s ({e:.3f} s)"
                     for a, b, e, n in clusters)


def classify(video_pts_span, audio_duration, holes_s, fps, tol_s=TOL_S):
    """{"ok", "reason"}. ok when the two timelines measure the same content: the video PTS span
    and the audio duration agree within tol_s AND no stall time is hiding inside the span."""
    skew = video_pts_span - audio_duration
    spans = (f"video span {video_pts_span:.3f} s vs audio {audio_duration:.3f} s "
             f"(skew {skew:+.3f} s = {skew * fps:+.1f} frames)")
    if abs(skew) <= tol_s and holes_s <= tol_s:
        return {"ok": True, "reason": f"{spans}, no stalls"}
    if holes_s > tol_s:
        return {"ok": False, "reason": f"{holes_s:.3f} s of video stalls; {spans}"}
    return {"ok": False, "reason": f"{spans}: no packet stalls found, so frames are missing off "
                                   f"the end of the capture or the audio clock is wrong"}


def parse_freezes(text):
    """freezedetect logs freeze_start / freeze_duration / freeze_end, one metadata line each. A
    freeze still running at EOF never gets its freeze_end, so the duration line is what closes an
    entry."""
    out, start = [], None
    for line in text.splitlines():
        if "freezedetect.freeze_start:" in line:
            start = float(line.rsplit(":", 1)[1])
        elif "freezedetect.freeze_duration:" in line:
            out.append({"start": start if start is not None else 0.0,
                        "duration": float(line.rsplit(":", 1)[1])})
            start = None
    return out


def verify_conform(vdur, adur, freezes, tol_s=TOL_S, freeze_min_s=FREEZE_MIN_S):
    """The mezzanine is only shippable when its own two streams measure the same length and no
    frame is held long enough to be a stall. Either failure means the cut would desync."""
    long = [f for f in freezes if f["duration"] > freeze_min_s]
    diff = abs(vdur - adur)
    bad = []
    if diff > tol_s:
        bad.append(f"video {vdur:.3f} s vs audio {adur:.3f} s ({diff:.3f} s apart)")
    if long:
        bad.append("held frames at " + ", ".join(f"{f['start']:.3f} s ({f['duration']:.3f} s)" for f in long))
    if bad:
        return {"ok": False, "reason": "the conformed mezzanine still desyncs: " + "; ".join(bad),
                "video_duration": round(vdur, 4), "audio_duration": round(adur, 4),
                "freezes_over_min": long}
    return {"ok": True, "reason": f"video {vdur:.3f} s == audio {adur:.3f} s, no held frame over "
                                  f"{freeze_min_s:.1f} s", "video_duration": round(vdur, 4),
            "audio_duration": round(adur, 4), "freezes_over_min": []}


# --- ffprobe / ffmpeg ---------------------------------------------------------------------------

def _run(cmd, service):
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        stop(f"{service} is down: {type(e).__name__}: {e}")


def _entries(src, stream, keys):
    r = _run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
              "stream=" + ",".join(keys), "-of", "json", src], "ffprobe")
    try:
        s = json.loads(r.stdout or "{}").get("streams") or []
    except ValueError:
        stop(f"ffprobe is down: unreadable output for {os.path.basename(src)}: {r.stderr.strip()[:200]}")
    return s[0] if s else None


def _float(d, key):
    try:
        return float(d.get(key))
    except (TypeError, ValueError):
        return None


def probe(src):
    """Everything the gate and the conform need, in one dict. Written to <name>.avskew.json so
    the chain can surface the conform in its status notes."""
    if not os.path.exists(src):
        stop(f"source not found: {src}")
    v = _entries(src, "v:0", ["codec_type", "avg_frame_rate", "r_frame_rate", "nb_frames", "duration", "start_time"])
    a = _entries(src, "a:0", ["codec_type", "duration", "start_time", "sample_rate"])
    if v is None:
        stop(f"{os.path.basename(src)} has no video stream")
    if a is None:
        stop(f"{os.path.basename(src)} has no audio stream; the take has nothing to cut on")
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
              "-of", "csv=p=0", src], "ffprobe")
    pts = []
    for line in r.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line or line == "N/A":
            continue
        try:
            pts.append(float(line))
        except ValueError:
            continue
    if len(pts) < 2:
        stop(f"ffprobe read {len(pts)} video packets from {os.path.basename(src)}; "
                         f"the file is truncated or still downloading from iCloud")
    pts.sort()
    span = pts[-1] - pts[0]
    if span <= 0:
        stop(f"{os.path.basename(src)}: all {len(pts)} video packets carry the same "
                         f"timestamp; the capture has no usable timeline")
    dts = [pts[i + 1] - pts[i] for i in range(len(pts) - 1)]
    fps = nominal_fps(parse_rate(v.get("r_frame_rate")), dts)
    avg = parse_rate(v.get("avg_frame_rate")) or (len(pts) - 1) / span
    adur = _float(a, "duration")
    if adur is None:
        stop(f"ffprobe could not read the audio duration of {os.path.basename(src)}")
    hs = holes_of(pts, fps)
    ht = holes_total(hs, fps)
    cl = cluster(hs, fps)
    d = classify(span, adur, ht, fps, TOL_S)
    return {"src": os.path.abspath(src), "tol_s": TOL_S,
            "nominal_fps": round(fps, 6), "avg_fps": round(avg, 6),
            "video_pts_span": round(span, 4), "video_frames": len(pts),
            "video_duration_at_nominal": round(len(pts) / fps, 4),
            "expected_frames_at_avg_fps": round(span * avg) + 1,
            "expected_frames_at_nominal_fps": round(span * fps) + 1,
            "audio_duration": round(adur, 6), "audio_start_time": _float(a, "start_time"),
            "skew": round(span - adur, 4), "holes_total": round(ht, 4),
            "holes": [{"t": round(t, 4), "gap": round(g, 4)} for t, g in hs],
            "hole_spans": [{"start": round(x, 4), "end": round(y, 4), "fake_s": round(e, 4), "n": n} for x, y, e, n in cl],
            "hole_spans_text": render_holes(cl), "ok": d["ok"], "reason": d["reason"]}


def freezes_of(mez, noise_db=-60, min_s=FREEZE_MIN_S):
    r = _run(["ffmpeg", "-v", "info", "-nostats", "-i", mez, "-map", "0:v:0",
              "-vf", f"freezedetect=n={noise_db}dB:d={min_s}", "-f", "null", "-"], "ffmpeg")
    if r.returncode != 0:
        stop(f"ffmpeg is down: freezedetect failed on {os.path.basename(mez)}: {r.stderr.strip()[-200:]}")
    return parse_freezes(r.stderr)


def verify(mez, tol_s=TOL_S):
    v = _entries(mez, "v:0", ["duration"]); a = _entries(mez, "a:0", ["duration"])
    vd, ad = (_float(v or {}, "duration"), _float(a or {}, "duration"))
    if vd is None or ad is None:
        stop(f"the mezzanine {os.path.basename(mez)} is missing a video or audio stream")
    return verify_conform(vd, ad, freezes_of(mez), tol_s)


# --- cli ----------------------------------------------------------------------------------------

def _opt(argv, name, cast=str, default=None):
    if name not in argv:
        return default
    i = argv.index(name)
    del argv[i]
    return cast(argv.pop(i))


def _write(path, d):
    """Written BEFORE the stop is raised: prep.py reads it to decide conform-vs-refuse, so a
    stalled source that exits 2 still has to leave its measurements behind."""
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(d, open(path, "w"), indent=1)


def _merge(path, key, value):
    if not path:
        return
    cur = {}
    if os.path.exists(path):
        try:
            cur = json.load(open(path))
        except (OSError, ValueError):
            cur = {}
    cur[key] = value
    _write(path, cur)


def _main():
    argv = sys.argv[1:]
    out = _opt(argv, "--json")
    tol = _opt(argv, "--tol", float, TOL_S)
    dropped = _opt(argv, "--dropped", float)
    if argv and argv[0] == "--verify":
        mez = argv[1]
        r = verify(mez, tol)
        if dropped is not None:
            r["dropped_s"] = round(dropped, 4)
            r["note"] = (f"source had {dropped:.3f} s of video stalls; the mezzanine was conformed so "
                         f"video time == audio time")
        _merge(out, "conform", r)
        if not r["ok"]:
            stop(r["reason"])
        print("avskew verify ok:", r["reason"])
        return
    src = argv[0]
    d = probe(src)
    d["tol_s"] = tol
    if tol != TOL_S:
        d.update(classify(d["video_pts_span"], d["audio_duration"], d["holes_total"], d["nominal_fps"], tol))
    _write(out, d)
    if not d["ok"]:
        if d["holes_total"] > tol:
            stop(f"source has {d['holes_total']:.3f} s of video stalls at {d['hole_spans_text']}; "
                             f"video span {d['video_pts_span']:.3f} s vs audio {d['audio_duration']:.3f} s "
                             f"(skew {d['skew']:+.3f} s = {d['skew'] * d['nominal_fps']:+.1f} frames)")
        stop(f"source video and audio timelines disagree: {d['reason']}")
    print(f"avskew ok: {d['reason']} ({d['video_frames']} frames @ {d['nominal_fps']:.3f} fps)")


if __name__ == "__main__":
    support.cli(_main, "avskew.py <src> [--json out] [--tol S] | avskew.py --verify <mez.mp4> [--json out] [--tol S] [--dropped S]")
