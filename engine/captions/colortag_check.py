#!/usr/bin/env python3
"""colortag_check.py <mp4>
G9 COLOR. Guards a trap that already shipped once: a chain of ffmpeg passes produced a file tagged
yuvj420p + bt470bg instead of yuv420p + bt709, and Meta's uploader silently rejected it. Exits 0
when the video stream is tagged pix_fmt yuv420p, color_range tv (broadcast/limited, never "pc"/
full range), and color_space, color_primaries and color_transfer all bt709; exits 1 otherwise,
printing one plain sentence per bad tag. (render.sh asks Remotion for bt709 output, so the tags are
written by the encoder for pixels that really are bt709; nothing relabels a stream after the fact.)

Usable two ways:
 - standalone: `colortag_check.py <finished.mp4>` -- exit 0 pass / 1 fail, for spot-checking any
   render by hand.
 - imported: `colortag_check.check(path)` -> {"pass", "numbers", "detail", "tags"} -- this is what
   reel_qc.py's G9 calls, so the result rides inside the one reel-qc.json card instead of a second
   file to cross-reference."""
import json, os, subprocess, sys
from keys import cli, EngineStop

WANT_PIX_FMT = "yuv420p"
WANT_RANGE = "tv"
WANT_SPACE = "bt709"
WANT_PRIMARIES = "bt709"
WANT_TRANSFER = "bt709"
FIELDS = ("pix_fmt", "color_range", "color_space", "color_primaries", "color_transfer")


def _run(cmd, service):
    try:
        return subprocess.run(cmd, capture_output=True, **{})
    except OSError as e:
        raise EngineStop(f"{service} is down: {type(e).__name__}: {e}")


def probe_color(path):
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=" + ",".join(FIELDS), "-of", "json", path], "ffprobe")
    try:
        d = json.loads(r.stdout or b"{}")
        s = (d.get("streams") or [{}])[0]
    except (ValueError, KeyError, IndexError, TypeError):
        s = {}
    if not s:
        raise EngineStop(f"ffprobe could not read a video stream from {os.path.basename(path)}: "
                         f"{(r.stderr or b'').decode('utf-8', 'replace').strip()[:160] or 'no output'}")
    return {k: (s.get(k) or "unknown") for k in FIELDS}


def check(path):
    """{"pass": bool, "numbers": str, "detail": [...], "tags": {...}}. Each `detail` entry carries
    a `plain` field -- the one sentence a non-engineer reads to know what to do (re-export the
    source with the right color tags)."""
    tags = probe_color(path)
    bad = []
    if tags["pix_fmt"] != WANT_PIX_FMT:
        bad.append({"why": "pix_fmt", "got": tags["pix_fmt"], "want": WANT_PIX_FMT,
                    "plain": f"the video is tagged {tags['pix_fmt']} instead of {WANT_PIX_FMT}: re-export the source."})
    if tags["color_range"] != WANT_RANGE:
        bad.append({"why": "color_range", "got": tags["color_range"], "want": WANT_RANGE,
                    "plain": f"the video's color range is tagged {tags['color_range']} instead of {WANT_RANGE} (broadcast/limited): re-export the source."})
    if tags["color_space"] != WANT_SPACE:
        bad.append({"why": "color_space", "got": tags["color_space"], "want": WANT_SPACE,
                    "plain": f"the video's color tags are wrong ({tags['color_space']} instead of {WANT_SPACE}): re-export the source."})
    if tags["color_primaries"] != WANT_PRIMARIES:
        bad.append({"why": "color_primaries", "got": tags["color_primaries"], "want": WANT_PRIMARIES,
                    "plain": f"the video's color primaries are tagged {tags['color_primaries']} instead of {WANT_PRIMARIES}: re-render, or re-export the source."})
    if tags["color_transfer"] != WANT_TRANSFER:
        bad.append({"why": "color_transfer", "got": tags["color_transfer"], "want": WANT_TRANSFER,
                    "plain": f"the video's transfer curve is tagged {tags['color_transfer']} instead of {WANT_TRANSFER}: re-render, or re-export the source."})
    numbers = (f"pix_fmt {tags['pix_fmt']} (want {WANT_PIX_FMT}), color_range {tags['color_range']} "
               f"(want {WANT_RANGE}), color_space {tags['color_space']} (want {WANT_SPACE}), "
               f"primaries {tags['color_primaries']} (want {WANT_PRIMARIES}), transfer {tags['color_transfer']} (want {WANT_TRANSFER})")
    return {"pass": not bad, "numbers": numbers, "detail": bad, "tags": tags}


def _main():
    path = sys.argv[1]
    if not os.path.exists(path):
        raise EngineStop(f"no such file: {path}")
    r = check(path)
    print(("PASS " if r["pass"] else "FAIL ") + r["numbers"])
    for d in r["detail"]:
        print("  " + d["plain"])
    sys.exit(0 if r["pass"] else 1)


if __name__ == "__main__":
    cli(_main, "colortag_check.py <mp4>")
