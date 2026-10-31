#!/usr/bin/env python3
"""join.py: join several take files into one working clip, in order, so a scripted video made of many takes runs through the
cutter as one source. Every input is conformed to one size, frame rate and audio layout first (so the join is clean), then
concatenated. Writes <out>.parts.json beside the result: where each input starts in the joined clip (so word times and edits
can be mapped back to the camera files).

    python3 engine/join.py <out.mp4> <take1> <take2> ... [--size 1080x1920|1920x1080|auto] [--fps 30] [--crf 16]

auto (the default) uses the first take's orientation: 1080x1920 for a vertical source, 1920x1080 otherwise. A take that is not
the same orientation is centre-cropped to fit (cover). Audio: first stream, stereo 48 kHz AAC; a silent take gets silence.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def conform(src, dst, w, h, fps, crf, has_audio):
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},format=yuv420p,setsar=1"
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart", str(dst)]
    common.run(cmd)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("takes", nargs="+")
    ap.add_argument("--size", default="auto")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=16)
    a = ap.parse_args()
    takes = [Path(t).expanduser() for t in a.takes]
    for t in takes:
        if not t.exists():
            common.stop(f"take not found: {t}")
    probes = [common.probe(t) for t in takes]
    if any(p is None for p in probes):
        common.stop("a take has no picture stream")
    if a.size == "auto":
        w, h = (1080, 1920) if probes[0]["vertical"] else (1920, 1080)
    else:
        try:
            w, h = (int(x) for x in a.size.lower().split("x"))
        except ValueError:
            common.stop("--size looks like 1080x1920")
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="join-") as td:
        parts, t0, lst = [], 0.0, []
        for i, (t, pr) in enumerate(zip(takes, probes)):
            dst = Path(td) / f"p{i:03d}.mp4"
            conform(t, dst, w, h, a.fps, a.crf, pr["audio"])
            d = common.probe(dst)["dur"]
            parts.append({"index": i, "source": str(t), "start": round(t0, 3), "end": round(t0 + d, 3), "dur": round(d, 3)})
            t0 += d
            lst.append(f"file '{dst}'")
        (Path(td) / "list.txt").write_text("\n".join(lst) + "\n")
        common.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(Path(td) / "list.txt"),
                    "-c", "copy", "-movflags", "+faststart", str(out)])
    got = common.probe(out)["dur"]
    if abs(got - t0) > 0.25:
        common.stop(f"joined clip runs {got:.2f}s but the parts add up to {t0:.2f}s")
    common.atomic_write_json(out.with_suffix(out.suffix + ".parts.json"), {"out": str(out), "size": [w, h], "fps": a.fps, "parts": parts})
    print(f"joined {len(parts)} takes into {out} ({got:.1f}s, {w}x{h} at {a.fps} fps); parts in {out.name}.parts.json")


if __name__ == "__main__":
    main()
