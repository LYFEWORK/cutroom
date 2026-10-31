#!/bin/bash
# render.sh <take-dir> <take-name>
#
# Renders the Captions composition (remotion/captions/CaptionsDynamic.tsx) over a take's plan.json and remuxes the take's own conformed audio under it.
# This is the ONLY place that produces video pixels in the caption chain.
#
# Reads:
#   <take-dir>/intake.json  : output_size decides 4K (--scale=2, source = mez.mp4) vs native 1080x1920
#                             (no scale, source = mez1080.mp4 if present, else mez.mp4). Never upscales.
#   <take-dir>/plan.json    : cluster_plan.py's output. Its "src" and "matte"."dir" fields are placeholders; this script
#                             renders from a rewritten copy (render-props.json) and leaves plan.json untouched.
#   <take-dir>/look.json    : your look, for the font files (the paths the plan names, brand-<id>/fonts/..., are copied from where look.json's
#                             font_sources says they are into the staging folder).
#   <take-dir>/audio.wav    : conform.sh's extracted audio (the take's own mix; never re-cut or re-scored here).
#   <take-dir>/matte/       : personmatte.py's webp sequence, only when plan.matte.windows is non-empty.
#
# Staging: Remotion reads files through a public folder. This script builds one per run under a temporary directory
# outside the repo (mktemp), passes it with --public-dir, and removes it on exit, success or failure. Nothing per-take
# is ever written inside the repo, two takes with the same name never collide, and a failed 4K render leaves no
# gigabytes behind. The source video, the matte and the font files are real COPIES (Remotion's bundler does not follow a symlinked file).
#
# Color: the render is asked for bt709 (--color-space=bt709) with PNG frames (--image-format=png). Remotion then converts
# the pixels to limited-range bt709 (zscale) and its encoder writes the matrix and range tags itself. What the encoder
# leaves unlabeled is the primaries and transfer fields (ffprobe: "unknown"), so the remux below fills exactly those
# two with h264_metadata, still a stream copy (-c:v copy), no second encode. reel_qc.py's G9 checks all five tags AND
# compares the reel's pixels with the mezzanine's, so a label that hides a wrong picture cannot pass.
#
# Remotion's own audio mux carries a documented ~42.7 ms delay, so whatever audio it wrote is discarded and the take's
# audio.wav is remuxed on top with -c:a aac.
#
# Env:
#   MASTER_AUDIO=loudnorm   normalize the remuxed audio to -16 LUFS, true peak -2 dBTP (QC's limit is -1.5), LRA 8; two-pass loudnorm, measured then linear.
#                           Default off: the clip arrives already mixed. run_captions.sh exports your look's master_audio.
#   CUTROOM_REMOTION_DIR    the Remotion project to render in (default: remotion/ in the repo). It must register a composition called Captions
#                           (remotion/captions/register.ts exports its config) and have its node_modules installed.
#
# Writes:
#   <take-dir>/<take-name>-reel.mp4  the final output (4K when the source qualified, otherwise native), yuv420p, bt709.
#   <take-dir>/render-meta.json      {rendered_at, scale, master_audio, elapsed_s, plan_sha, render_key, color_space, image_format}
#                                    render_key hashes the plan, master_audio and the look's font files: run_captions.sh re-renders when it changes.

#
# Exit: STOP: <reason> on stderr + exit 2 for any missing or malformed input (never a raw traceback).
set -Eeuo pipefail

usage() { echo "usage: render.sh <take-dir> <take-name>" >&2; exit 2; }
[ $# -ge 2 ] || usage
[ -d "$1" ] || { echo "STOP: take-dir not found: $1" >&2; exit 2; }
T="$(cd "$1" && pwd)"
N="$2"
[ -n "$N" ] || usage

SK="$(cd "$(dirname "$0")" && pwd)"
R="${CUTROOM_REMOTION_DIR:-$(cd "$SK/../.." && pwd)/remotion}"
tk() { python3 "$SK/takectl.py" "$@"; }
[ -d "$R" ] || { echo "STOP: the renderer folder was not found ($R)" >&2; exit 2; }
[ -x "$R/node_modules/.bin/remotion" ] || { echo "STOP: Remotion is not installed in $R. Run: cd $R && npm ci (bash install.sh does it)" >&2; exit 2; }

PLAN="$T/plan.json"
[ -s "$PLAN" ] || { echo "STOP: $PLAN is missing or empty. Run the plan stage first" >&2; exit 2; }
AUDIO="$T/audio.wav"
[ -s "$AUDIO" ] || { echo "STOP: $AUDIO is missing. Run conform.sh first (it extracts the take's audio)" >&2; exit 2; }
INTAKE="$T/intake.json"
[ -s "$INTAKE" ] || { echo "STOP: $INTAKE is missing or empty. Run intake.py first" >&2; exit 2; }
LOOKJ="$T/look.json"
[ -s "$LOOKJ" ] || { echo "STOP: $LOOKJ is missing. Run lookprofile.py first" >&2; exit 2; }

MASTER_AUDIO="${MASTER_AUDIO:-off}"
case "$MASTER_AUDIO" in
  off|loudnorm) ;;
  *) echo "STOP: MASTER_AUDIO must be 'off' or 'loudnorm', got '$MASTER_AUDIO'" >&2; exit 2 ;;
esac

# --- output size: 4K when the take's output_size (decided from the displayed short side) is 2160 wide
OUT_W="$(tk get "$INTAKE" output_size.0)"
if [ "$OUT_W" -ge 2160 ]; then
  SRC="$T/mez.mp4"
  SCALE_ARG="--scale=2"
  SIZE_LABEL="4k"
  [ -s "$SRC" ] || { echo "STOP: intake.json says 4K but $SRC is missing. Run conform.sh first" >&2; exit 2; }
else
  if [ -s "$T/mez1080.mp4" ]; then SRC="$T/mez1080.mp4"; else SRC="$T/mez.mp4"; fi
  SCALE_ARG=""
  SIZE_LABEL="native"
  [ -s "$SRC" ] || { echo "STOP: no mezzanine found ($T/mez1080.mp4 or $T/mez.mp4). Run conform.sh first" >&2; exit 2; }
fi

# --- staging folder, per run, outside the repo, removed on exit
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/captions-render.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
cp -c "$SRC" "$STAGE/src.mp4" 2>/dev/null || cp "$SRC" "$STAGE/src.mp4"
NEEDS_MATTE="$(tk needs-matte "$PLAN")"
if [ "$NEEDS_MATTE" = "1" ]; then
  [ -d "$T/matte" ] || { echo "STOP: plan.json declares matte windows but $T/matte is missing. Run personmatte.py first" >&2; exit 2; }
  cp -cR "$T/matte" "$STAGE/matte" 2>/dev/null || cp -R "$T/matte" "$STAGE/matte"
fi
tk stage-fonts "$T" "$STAGE"

PROPS="$T/render-props.json"
python3 -c '
import json, sys
plan_path, out_path, needs_matte = sys.argv[1:4]
p = json.load(open(plan_path))
p["src"] = "src.mp4"
if needs_matte == "1":
    p.setdefault("matte", {})
    p["matte"]["dir"] = "matte"
json.dump(p, open(out_path, "w"), indent=1)
' "$PLAN" "$PROPS" "$NEEDS_MATTE"

# --- render
OUT_VIDEO="$T/render.mp4"
START_TS=$(date +%s)
echo "== [$N] render ($SIZE_LABEL, bt709, png frames) =="
(
  cd "$R" && npx remotion render index.ts Captions "$OUT_VIDEO" \
    --props="$PROPS" --public-dir="$STAGE" $SCALE_ARG \
    --codec=h264 --crf=18 --concurrency=3 \
    --color-space=bt709 --image-format=png --log=error
)

# --- remux: the take's own audio.wav, never Remotion's own mux (the 42.7 ms delay trap); the video stream is copied as is
FINAL="$T/$N-reel.mp4"
PRIMARIES_BSF="h264_metadata=colour_primaries=1:transfer_characteristics=1"
echo "== [$N] remux (audio: $MASTER_AUDIO) =="
if [ "$MASTER_AUDIO" = "loudnorm" ]; then
  LOUDNORM="$(tk loudnorm-filter "$AUDIO")"
  ffmpeg -v error -y -i "$OUT_VIDEO" -i "$AUDIO" \
    -map 0:v:0 -map 1:a:0 -c:v copy -bsf:v "$PRIMARIES_BSF" \
    -af "$LOUDNORM" -c:a aac -b:a 256k -ar 48000 \
    -shortest -movflags +faststart "$FINAL"
else
  ffmpeg -v error -y -i "$OUT_VIDEO" -i "$AUDIO" \
    -map 0:v:0 -map 1:a:0 -c:v copy -bsf:v "$PRIMARIES_BSF" -c:a aac -b:a 256k -ar 48000 \
    -shortest -movflags +faststart "$FINAL"
fi
END_TS=$(date +%s)

python3 -c '
import json, sys, datetime
out, size_label, master_audio, elapsed, plan_sha, render_key = sys.argv[1:7]
json.dump({
    "rendered_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "scale": size_label,
    "master_audio": master_audio,
    "elapsed_s": int(elapsed),
    "plan_sha": plan_sha,
    "render_key": render_key,
    "color_space": "bt709",
    "image_format": "png",
}, open(out, "w"), indent=1)
' "$T/render-meta.json" "$SIZE_LABEL" "$MASTER_AUDIO" "$((END_TS - START_TS))" "$(tk plan-sha "$PLAN")" "$(tk render-key "$T" "$MASTER_AUDIO")"

echo "RENDER-DONE $FINAL"
