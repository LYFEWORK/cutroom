#!/bin/bash
# conform.sh <take-dir>
#
# Turns intake.py's physical copy into the mezzanine every later stage works from. Reads
# <take-dir>/intake.json (source path, channel count, output_size, hdr) and writes:
#   <take-dir>/mez.mp4       : H.264, yuv420p, tv/bt709, native output_size, 30fps, VIDEO ONLY
#   <take-dir>/mez1080.mp4   : same, downscaled to 1080x1920, ONLY when output_size is 4K
#                              (facetrack.py and the caption planner work in 1080 space; the 4K
#                              person matte is generated from mez.mp4 and scales into place)
#   <take-dir>/audio.wav     : 48 kHz, channel count preserved from the source, for the final remux
#   <take-dir>/audio16.wav   : 16 kHz mono, for the transcriber
#   <take-dir>/avskew.json   : the source's video/audio timeline measurement
#
# There is no colour grade here: the picture is conformed, tagged BT.709 and left otherwise as shot. The captions are drawn over
# a finished cut, and the colour of the cut is the editor's. An HDR clip (HLG or PQ) cannot be conformed by the plain pass below, so
# it goes through engine/hdr.py first (Apple's own tone mapper) when that module is installed; intake.py stops an HDR clip with the
# "export an SDR version" line when it is not.
#
# Three traps this guards against:
# 1. Camera HEVC frame-repeat. OffthreadVideo over an HEVC source made the Remotion compositor
#    repeat frames. Re-encoding to H.264 here, before the source ever reaches Remotion, removes
#    the trap by construction rather than working around it downstream.
# 2. yuvj420p / bt470bg drift. A prior ffmpeg pass chain produced full-range yuvj420p with
#    bt470bg tags and an uploader rejected the file. Every ffmpeg pass in this script tags
#    color_range/colorspace/color_primaries/color_trc explicitly; an iPhone HEVC source commonly
#    reports color_range=pc (full range), which needs the scale filter's own in_range=full /
#    out_range=limited conversion: a bare -color_range flag alone does not remap the pixel
#    values, only the tag, and a mistagged-but-unconverted file is the exact failure this guards.
# 3. A stalled capture frozen into fake picture time (avskew.py's own documented failure mode:
#    a thermal/storage stall duplicates frames to fill dead air, so the picture runs late against
#    its own audio). avskew.py runs on the source BEFORE any encode; a clean source (the common
#    case) encodes directly to the final audio-less mez.mp4 in one pass. A stalled source is
#    conformed exactly as avskew.py's own docstring prescribes: frames renumbered at the
#    source's nominal fps so the dead time is dropped, never frozen. Because mez.mp4 must
#    ship with NO audio track while avskew.py's --verify can only compare two streams inside the
#    SAME file, the conform path builds one temporary file WITH audio to verify against, then
#    strips the audio with a fast stream copy (no re-encode) to produce the final mez.mp4. This
#    is a re-timing of dead air the camera itself introduced, not a trim/reorder of anything the
#    subject said or did: it stays inside the "never cuts footage" rule.
#
# Never touches the file intake.py copied in place. Physical files only, nothing symlinked.
set -e

T="$1"
SK="$(cd "$(dirname "$0")" && pwd)"
HDR_PY="$(cd "$SK/.." && pwd)/hdr.py"

die() { echo "STOP: $1" >&2; exit 2; }

[ -n "$T" ] || die "usage: conform.sh <take-dir>"
[ -d "$T" ] || die "no such take dir: $T"
[ -f "$T/intake.json" ] || die "no intake.json in $T: run intake.py <video> $T first"

pyget() {
python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
v = d[sys.argv[2]]
idx = sys.argv[3] if len(sys.argv) > 3 else ''
if idx != '':
    v = v[int(idx)]
print(v)
" "$T/intake.json" "$1" "$2" 2>/dev/null
}

SRC="$(pyget source)"
[ -n "$SRC" ] || die "intake.json in $T is missing a 'source' field: re-run intake.py"
[ -f "$SRC" ] || die "intake.json points at a source file that no longer exists: $SRC"

OUT_W="$(pyget output_size 0)"
OUT_H="$(pyget output_size 1)"
CHANNELS="$(pyget channels)"
[ -n "$OUT_W" ] && [ -n "$OUT_H" ] && [ -n "$CHANNELS" ] || die "intake.json in $T is malformed (missing output_size/channels): re-run intake.py"

STAMP() { date +%T; }
NAME="$(basename "$T")"

# --- the picture the mezzanine is made from: the source, or for an HDR source the SDR picture engine/hdr.py makes from it -------
# iPhone "HDR Video" (HLG or PQ) is not a picture the plain pass below can conform: it would stamp BT.2020 pixels BT.709. `engine/hdr.py <in> --to <out.mov>`
# tone maps it to SDR (BT.709, limited range) keeping every frame's timestamp; its audio is not used, the audio below always comes from the source.
HDR_KIND="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("hdr") or "")' "$T/intake.json" 2>/dev/null || true)"
VSRC="$SRC"
if [ -n "$HDR_KIND" ]; then
  [ -f "$HDR_PY" ] || die "$NAME is HDR ($HDR_KIND) and engine/hdr.py is not installed, so it cannot be converted to SDR. Export an SDR version (Rec.709) and run it again"
  VSRC="$T/mez.hdr.mov"; rm -f "$VSRC"
  echo "[$(STAMP)] $NAME HDR ($HDR_KIND): converting to SDR"
  mkdir -p "$T/logs"
  python3 "$HDR_PY" "$SRC" --to "$VSRC" >"$T/logs/hdr.log" 2>&1 || true
  [ -s "$VSRC" ] || die "$NAME is HDR ($HDR_KIND) and the converter could not make an SDR picture ($(grep -m1 '^STOP:' "$T/logs/hdr.log" 2>/dev/null | cut -c1-160 || echo 'see logs/hdr.log')). Export an SDR version (Rec.709) and run it again"
fi

# --- full-range and BT.601 source detection: iPhone HEVC commonly reports color_range=pc; an older render reports yuvj420p + bt470bg ------
# (read with ffprobe's default writer: a stream that carries side data, such as a Remotion render's ICC profile, makes the csv writer print "pc," and the
# comparison below never matched)
CRANGE="$(ffprobe -v error -select_streams v:0 -show_entries stream=color_range -of default=nw=1:nk=1 "$VSRC" 2>/dev/null | tr -d '[:space:]')"
RANGEOPT=""
[ "$CRANGE" = "pc" ] && RANGEOPT=":in_range=full:out_range=limited"
# A BT.601 source (tagged bt470bg or smpte170m: a webcam, an older render) has BT.601 pixels. The plain pass below only stamps the frames bt709, so without this
# the picture's colours shift (a lime #96FF00 came back with its green 5 levels low). Convert the matrix too; an untagged or BT.709 source is left alone.
CSPACE="$(ffprobe -v error -select_streams v:0 -show_entries stream=color_space -of default=nw=1:nk=1 "$VSRC" 2>/dev/null | tr -d '[:space:]')"
MATRIXOPT=""
case "$CSPACE" in bt470bg|smpte170m) MATRIXOPT=":in_color_matrix=bt601:out_color_matrix=bt709" ;; esac

# --- avskew: measure the source's own timeline before any encode --------------------------------
AVJ="$T/avskew.json"; rm -f "$AVJ"
set +e
AVOUT="$(python3 "$SK/avskew.py" "$SRC" --json "$AVJ" 2>&1)"; AVRC=$?
set -e
CONFORM=0
if [ "$AVRC" -eq 0 ]; then
  echo "[$(STAMP)] $NAME $AVOUT"
elif [ "$AVRC" -eq 2 ] && [ -s "$AVJ" ]; then
  # A measured, named stall: conform instead of refusing, per avskew.py's own docstring.
  CONFORM=1
  echo "[$(STAMP)] $NAME NOTE: ${AVOUT#STOP: }"
else
  echo "$AVOUT" >&2
  exit "$AVRC"
fi

# setparams stamps the FRAMES bt709/tv: the encoder copies primaries and transfer from the frames, and -color_primaries
# alone leaves them "unknown" on an untagged source, which would reach Remotion as an unlabelled picture.
TAGS="setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
if [ -n "$RANGEOPT$MATRIXOPT" ]; then
  # A YUV to YUV scale does not convert the matrix and rounds the range coarsely (measured on flat colours: a lime #96FF00 came back 2 to 3 levels low and a white
  # up to 4, which is also what the finals of a BT.601 export showed). Going through planar RGB makes both exact (within 1 level).
  IN_M=bt709; [ -z "$MATRIXOPT" ] || IN_M=bt601
  IN_R=tv; [ -z "$RANGEOPT" ] || IN_R=full
  VF_BASE="scale=in_color_matrix=${IN_M}:in_range=${IN_R}:flags=accurate_rnd+full_chroma_int,format=gbrp,scale=${OUT_W}:${OUT_H}:flags=lanczos,scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p,${TAGS}"
else
  VF_BASE="scale=${OUT_W}:${OUT_H}:flags=lanczos,format=yuv420p,${TAGS}"
fi
COLORARGS=(-color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 -pix_fmt yuv420p)

echo "[$(STAMP)] $NAME mezzanine start (${OUT_W}x${OUT_H})"

if [ "$CONFORM" = "1" ]; then
  NOM="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['nominal_fps'])" "$AVJ")"
  DROP="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['holes_total'])" "$AVJ")"
  echo "[$(STAMP)] $NAME conforming: dropping ${DROP}s of stall time, frames renumbered at $NOM fps"
  TMP="$T/mez.withaudio.tmp.mp4"
  ffmpeg -v error -y -i "$VSRC" -i "$SRC" -map 0:v:0 -map 1:a:0 \
    -vf "setpts=N/$NOM/TB,$VF_BASE" -fps_mode cfr -shortest -r 30 \
    -c:v libx264 -preset fast -crf 16 "${COLORARGS[@]}" \
    -c:a aac -b:a 192k -ar 48000 \
    -movflags +faststart "$TMP"
  python3 "$SK/avskew.py" --verify "$TMP" --json "$AVJ" --dropped "$DROP" \
    || { rm -f "$TMP"; die "the conformed mezzanine still desyncs, see $AVJ"; }
  # Strip the verify-only audio track with a stream copy: no re-encode, tags untouched.
  ffmpeg -v error -y -i "$TMP" -map 0:v:0 -c:v copy -an -movflags +faststart "$T/mez.mp4"
  rm -f "$TMP"
else
  ffmpeg -v error -y -i "$VSRC" -map 0:v:0 \
    -vf "$VF_BASE" -fps_mode cfr -r 30 \
    -c:v libx264 -preset fast -crf 16 -an "${COLORARGS[@]}" \
    -movflags +faststart "$T/mez.mp4"
fi
rm -f "$T/mez.hdr.mov"

# --- self-check: catch a wrong-tag output here, not three stages downstream ---------------------
GOTFMT="$(ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of default=nw=1:nk=1 "$T/mez.mp4" | tr -d '[:space:]')"
[ "$GOTFMT" = "yuv420p" ] || die "mez.mp4 came out pix_fmt=$GOTFMT instead of yuv420p, do not proceed"
GOTRANGE="$(ffprobe -v error -select_streams v:0 -show_entries stream=color_range -of default=nw=1:nk=1 "$T/mez.mp4" 2>/dev/null | tr -d '[:space:]')"
[ "$GOTRANGE" != "pc" ] || die "mez.mp4 still came out full-range (color_range=pc). An uploader will reject this"

echo "[$(STAMP)] $NAME mezzanine done: $(ffprobe -v error -show_entries stream=width,height:format=duration -of csv=p=0 "$T/mez.mp4" | tr '\n' ' ')"

# --- 1080 mezzanine for facetrack/planning, only when the deliverable is 4K ----------------------
if [ "$OUT_W" -ge 2160 ]; then
  echo "[$(STAMP)] $NAME writing 1080 mezzanine for facetrack/planning"
  ffmpeg -v error -y -i "$T/mez.mp4" \
    -vf "scale=1080:1920:flags=lanczos,format=yuv420p,${TAGS}" -fps_mode cfr -r 30 \
    -c:v libx264 -preset fast -crf 16 -an "${COLORARGS[@]}" \
    -movflags +faststart "$T/mez1080.mp4"
  echo "[$(STAMP)] $NAME mez1080 done: $(ffprobe -v error -show_entries stream=width,height:format=duration -of csv=p=0 "$T/mez1080.mp4" | tr '\n' ' ')"
fi

# --- audio extraction: always from the ORIGINAL source, never from a re-timed video pass --------
echo "[$(STAMP)] $NAME extracting audio"
ffmpeg -v error -y -i "$SRC" -map 0:a:0 -vn -ar 48000 -ac "$CHANNELS" "$T/audio.wav"
ffmpeg -v error -y -i "$SRC" -map 0:a:0 -vn -ar 16000 -ac 1 "$T/audio16.wav"

echo "[$(STAMP)] $NAME CONFORM DONE"
