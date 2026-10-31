#!/usr/bin/env python3
"""intake.py <video> <take-dir>  — the door this engine's every take walks through first.

Three traps this closes before any expensive processing (transcription, face tracking, render)
ever runs on a take:

1. iCloud eviction. A folder that has been "Optimize Mac Storage"-evicted reports
   `dataless` from `stat -f %Sf` — the file LOOKS present (ls shows it, size is right) but reading
   it blocks for 20-30s per read while macOS re-downloads it, and a naive ffprobe call reads it
   many times over. Caught here, once, with a plain instruction instead of a mystery hang three
   stages downstream.
2. A file that will not decode. A partial download, a corrupt export, or a container ffmpeg can't
   read wastes every later stage's work before failing. ffprobe alone is not enough — a truncated
   MP4 can still report valid stream metadata and then fail to decode a single frame — so this
   also asks ffmpeg to actually decode a couple of frames.
3. The webpack public/ symlink trap. Remotion's bundler does not follow symlinks placed under
   remotion/public, so a take built on a symlinked source silently ships without its footage. The
   fix is at the door: intake.py always makes a PHYSICAL byte copy into the take dir, never a
   symlink, whatever kind of path (including an existing symlink) it was handed.

4. A picture the engine cannot caption correctly. Captions are built for a 9:16 vertical frame, so a
   clip whose DISPLAYED shape (after the rotation tag and the pixel aspect ratio) is not 9:16 within 1%
   stops with the size it has and the fix (reframe in the editor). An HDR clip (HLG or PQ transfer, the
   iPhone's "HDR Video") is accepted only when engine/hdr.py is installed: conform.sh converts it to SDR through
   that module, and intake.json records `hdr` ("hlg" or "pq"). Without it the clip stops with the
   SDR re-export instruction, and so does a clip tagged BT.2020 with no HDR transfer (this engine cannot convert it).

Writes <take-dir>/source.<ext> (the physical copy) and <take-dir>/intake.json:
{source, source_original, sha256_64mb, size_bytes, width, height (as coded), display_width, display_height
(as shown, rotation and pixel aspect applied), rotation, color_transfer, fps, duration, vcodec, acodec,
channels, output_size, hdr}. output_size is [2160, 3840] when the displayed SHORT side is already >= 2160 px,
otherwise native [1080, 1920]: this engine never upscales a source that arrived smaller than 4K
(spec: "Output size ... never upscale").

Every failure path prints one plain "STOP: ..." line a non-engineer can act on and exits 2 —
never a stack trace (keys.py's cli() contract)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from avskew import parse_rate  # noqa: E402
from keys import EngineStop, cli  # noqa: E402

HDR_MODULE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "engine", "hdr.py")   # converts an HDR clip to SDR when it is in the install

HASH_BYTES = 64 * 1024 * 1024   # first 64 MB — a fast, stable fingerprint, not a full-file hash
DECODE_PROBE_FRAMES = "2"       # enough to prove the codec/container actually decodes


# --- pure -----------------------------------------------------------------------------------

def is_dataless(stat_flags):
    """`stat -f %Sf` prints a space-separated flag list; `dataless` is macOS's iCloud-eviction
    flag. Pure so the flag-parsing logic is testable without a real evicted file on disk."""
    return "dataless" in (stat_flags or "").split()


HDR_KINDS = {"arib-std-b67": "hlg", "smpte2084": "pq"}      # the transfer tag of the iPhone's HDR Video (HLG) and of PQ
WIDE_GAMUT = ("bt2020", "bt2020nc", "bt2020c")      # a BT.2020 clip is HDR or wide gamut even when its transfer tag is missing


def hdr_installed():
    """Is the HDR converter there? It is what turns an HDR clip into SDR (conform.sh calls engine/hdr.py)."""
    return os.path.isfile(HDR_MODULE)
ASPECT_TOLERANCE = 0.01


def display_size(width, height, rotation=0, sar=None):
    """(w, h) as the clip is SHOWN: the pixel aspect ratio widens the coded width, then a 90/270 degree
    rotation tag swaps the axes. A phone clip coded 2560x1440 with rotation 90 is 1440x2560 on screen."""
    w = width
    if sar:
        try:
            n, d = (float(x) for x in str(sar).replace("/", ":").split(":"))
            if n > 0 and d > 0:
                w = width * n / d
        except (ValueError, ZeroDivisionError):
            pass
    w, h = int(round(w)), height
    if int(round(rotation or 0)) % 180 == 90:
        w, h = h, w
    return w, h


def is_9x16(dw, dh, tol=ASPECT_TOLERANCE):
    return dh > 0 and abs(dw / dh - 9 / 16) <= tol * (9 / 16)


def output_size_for(display_w, display_h=None):
    """[2160, 3840] once the displayed SHORT side is already 4K (>= 2160), otherwise the native
    [1080, 1920], never upsized. Matches the plan's 1080-space-composition/--scale=2 design: a sub-4K
    source simply renders at its own native size instead of being stretched up to it."""
    short = display_w if display_h is None else min(display_w, display_h)
    return [2160, 3840] if short >= 2160 else [1080, 1920]


def build_intake(source_copy, source_original, sha256_64mb, size_bytes,
                  width, height, fps, duration, vcodec, acodec, channels,
                  display=None, rotation=0, color_transfer="unknown", hdr=None):
    """Assembles the intake.json dict. Pure so the exact shape is unit-testable without touching
    ffprobe or the filesystem."""
    dw, dh = display or (width, height)
    return {
        "source": source_copy,
        "source_original": source_original,
        "sha256_64mb": sha256_64mb,
        "size_bytes": size_bytes,
        "width": width,
        "height": height,
        "display_width": dw,
        "display_height": dh,
        "rotation": rotation,
        "color_transfer": color_transfer,
        "fps": round(fps, 3),
        "duration": round(duration, 3),
        "vcodec": vcodec,
        "acodec": acodec,
        "channels": channels,
        "output_size": output_size_for(dw, dh),
        "hdr": hdr,
    }


# --- filesystem / ffprobe --------------------------------------------------------------------

def stat_flags(path):
    """the file's flag words. macOS: `stat -f %Sf`, where `dataless` marks an iCloud-evicted file. Elsewhere (Linux, Windows through WSL2) there is
    no iCloud eviction and GNU stat reads -f as a file-system query, so the file is only checked to be there and the flag list is empty."""
    if sys.platform != "darwin":
        try:
            os.stat(path)
        except OSError as e:
            raise EngineStop(f"could not stat {path}: {e.strerror or e}")
        return ""
    r = subprocess.run(["stat", "-f", "%Sf", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise EngineStop(f"could not stat {path}: {(r.stderr or '').strip()}")
    return r.stdout.strip()


def _ffprobe_json(path, stream, keys, extra=""):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
         "stream=" + ",".join(keys) + extra, "-of", "json", path],
        capture_output=True, text=True)
    try:
        streams = json.loads(r.stdout or "{}").get("streams") or []
    except ValueError:
        raise EngineStop(
            f"ffprobe could not read {os.path.basename(path)}. It may be corrupt or "
            f"still downloading: {(r.stderr or '').strip()[:200]}")
    return streams[0] if streams else None


def _format_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    try:
        return float((r.stdout or "").strip())
    except ValueError:
        raise EngineStop(
            f"ffprobe could not read a duration for {os.path.basename(path)}. It may be "
            f"corrupt or still downloading from iCloud")


def probe_streams(path):
    """{width, height, fps, duration, vcodec, acodec, channels}. STOPs with a plain message if
    the video or audio stream is missing — this engine remuxes the take's own audio onto the
    final render, so a video with no audio track has nothing to ship with."""
    v = _ffprobe_json(path, "v:0", ["codec_name", "width", "height", "r_frame_rate", "avg_frame_rate",
                                    "sample_aspect_ratio", "color_transfer", "color_primaries", "color_space"],
                      ":stream_tags=rotate:stream_side_data=rotation")
    if v is None:
        raise EngineStop(
            f"{os.path.basename(path)} has no readable video stream: this is not a video file, "
            f"or the file is corrupt/truncated")
    a = _ffprobe_json(path, "a:0", ["codec_name", "sample_rate", "channels"])
    if a is None:
        raise EngineStop(
            f"{os.path.basename(path)} has no audio track: this engine remuxes the take's own "
            f"mixed audio onto the final render, so the clip must arrive with sound. Re-export it "
            f"with audio and try again")
    fps = parse_rate(v.get("r_frame_rate")) or parse_rate(v.get("avg_frame_rate"))
    if not fps:
        raise EngineStop(f"could not determine a frame rate for {os.path.basename(path)}")
    try:
        width, height = int(v["width"]), int(v["height"])
    except (KeyError, TypeError, ValueError):
        raise EngineStop(f"could not read the resolution of {os.path.basename(path)}")
    try:
        channels = int(a.get("channels"))
    except (TypeError, ValueError):
        channels = 1
    rotation = 0
    for sd in v.get("side_data_list") or []:
        if sd.get("rotation") is not None:
            try:
                rotation = float(sd["rotation"])
            except (TypeError, ValueError):
                pass
    if not rotation and (v.get("tags") or {}).get("rotate"):
        try:
            rotation = float(v["tags"]["rotate"])
        except (TypeError, ValueError):
            pass
    return {
        "width": width, "height": height, "fps": fps,
        "rotation": rotation, "sar": v.get("sample_aspect_ratio"), "color_transfer": v.get("color_transfer") or "unknown",
        "color_primaries": v.get("color_primaries") or "unknown", "color_space": v.get("color_space") or "unknown",
        "duration": _format_duration(path),
        "vcodec": v.get("codec_name") or "unknown",
        "acodec": a.get("codec_name") or "unknown",
        "channels": channels,
    }


def check_picture(name, dw, dh, color_transfer, color_primaries="unknown", color_space="unknown", hdr_ok=False):
    """STOP, with the fix in plain words, for a clip the captions would be built wrongly on. `hdr_ok`: the HDR converter can turn an HDR clip
    (HLG or PQ) into SDR, so it is let through; a clip tagged BT.2020 with no HDR transfer is not something it converts, so it still stops."""
    if color_transfer in HDR_KINDS and not hdr_ok:
        raise EngineStop(
            f"{name} is HDR ({HDR_KINDS[color_transfer].upper()}), and this engine cannot turn HDR into SDR. "
            f"Export an SDR version (Rec.709): on an iPhone turn off Settings > Camera > Record Video > HDR Video and "
            f"shoot again, or export an SDR version from your editor, then run it again.")
    if color_transfer not in HDR_KINDS and (color_primaries in WIDE_GAMUT or color_space in WIDE_GAMUT):
        raise EngineStop(
            f"{name} is tagged BT.2020 with no HDR transfer, and this engine cannot convert it. "
            f"Re-export it as SDR (Rec.709) from your editor, then run it again.")
    if not is_9x16(dw, dh):
        shape = "landscape" if dw > dh else ("square-ish" if abs(dw - dh) / max(dw, dh) < 0.1 else "not 9:16")
        raise EngineStop(
            f"{name} is {dw}x{dh} as shown ({shape}); captions are built for a 9:16 vertical frame "
            f"(1080x1920 or 2160x3840). Reframe it to 9:16 in your editor, export, and run it again.")


def can_decode(path):
    """ffprobe can report plausible stream metadata for a file that will not actually decode (a
    truncated iCloud download, a half-written export). Ask ffmpeg to decode a couple of real
    frames before trusting the file at all."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-frames:v", DECODE_PROBE_FRAMES, "-f", "null", "-"],
        capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()


def sha256_first_64mb(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(HASH_BYTES))
    return h.hexdigest()


def copy_physical(src, dest):
    """A true byte copy, never a symlink — closes the webpack public/ symlink trap. shutil.copy2
    dereferences a symlinked src automatically, so a source that is itself a symlink still lands
    as a real file at dest."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    if os.path.islink(dest):
        raise EngineStop(f"{dest} came out as a symlink, not a physical copy; refusing to proceed")
    if os.path.getsize(dest) != os.path.getsize(src):
        raise EngineStop(
            f"the copy to {dest} is a different size than the source: the copy is incomplete "
            f"or the disk is full")


# --- cli ------------------------------------------------------------------------------------

def _main():
    video, take_dir = sys.argv[1], sys.argv[2]

    if not os.path.exists(video):
        raise EngineStop(f"source file not found: {video}")

    flags = stat_flags(video)
    if is_dataless(flags):
        raise EngineStop(
            f"{video} is evicted to iCloud (not actually on this Mac yet). Download it first:\n"
            f"  brctl download \"{video}\"\n"
            f"then wait for the download to finish and run intake.py again.")

    ok, err = can_decode(video)
    if not ok:
        raise EngineStop(
            f"{os.path.basename(video)} will not decode. It may be corrupt or only partially "
            f"downloaded. Re-export or re-copy it and try again. ({err[:200]})")

    info = probe_streams(video)
    dw, dh = display_size(info["width"], info["height"], info["rotation"], info["sar"])
    check_picture(os.path.basename(video), dw, dh, info["color_transfer"], info["color_primaries"], info["color_space"], hdr_ok=hdr_installed())

    ext = os.path.splitext(video)[1] or ".mp4"
    dest = os.path.join(take_dir, "source" + ext)
    copy_physical(video, dest)

    intake = build_intake(
        source_copy=os.path.abspath(dest),
        source_original=os.path.abspath(video),
        sha256_64mb=sha256_first_64mb(dest),
        size_bytes=os.path.getsize(dest),
        width=info["width"], height=info["height"], fps=info["fps"],
        duration=info["duration"], vcodec=info["vcodec"], acodec=info["acodec"],
        channels=info["channels"],
        display=(dw, dh), rotation=info["rotation"], color_transfer=info["color_transfer"],
        hdr=HDR_KINDS.get(info["color_transfer"]),
    )
    json.dump(intake, open(os.path.join(take_dir, "intake.json"), "w"), indent=1)

    print(
        f"intake ok: {info['width']}x{info['height']} @ {info['fps']:.3f}fps, "
        f"{info['duration']:.2f}s, {info['vcodec']}/{info['acodec']} "
        f"({info['channels']}ch) -> output {intake['output_size'][0]}x{intake['output_size'][1]}"
    )


if __name__ == "__main__":
    cli(_main, "intake.py <video> <take-dir>")
