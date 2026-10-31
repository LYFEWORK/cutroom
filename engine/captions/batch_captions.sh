#!/bin/bash
# batch_captions.sh <folder-of-cuts> <workroot> [--style <id>] [--dest <folder>]
#
# run_captions.sh for every already-cut 9:16 clip in a folder, one after another, each in its own take dir
# (<workroot>/<clip-name>/), then one summary. A clip that fails, stops, or fails QC never stops the batch.
#
#   <folder-of-cuts>  the clips: *.mp4 *.mov *.m4v directly inside it (no subfolders, no dotfiles)
#   <workroot>        where the take dirs, batch-logs/ and batch-summary.txt go
#   --style <id>      the caption style every clip gets (passed on to run_captions.sh; without it each clip's own caption-style.txt, else your DNA's default)
#   --dest <folder>   passed on to run_captions.sh (default: each clip delivers to <workspace>/Finished/<Title>/)
#
# Run it twice, like run_captions.sh: the first pass builds every clip's GATE.md and stops each at the approval gate (that is the
# normal first result, listed as "needs approval"); open each GATE.md, set approved: yes, run the same command again. A clip
# already delivered is skipped fast (every stage reports done). FORCE=1 passes through to every take.
#
# Each line of the summary: <clip> <result> <one plain sentence>. Results: delivered, needs approval, failed QC, stopped.
# A delivered clip with something to look at (a mix off the loudness target) carries the take's CHECK sentences on its line.
# The full output of each take is <workroot>/batch-logs/<clip>.log; each take's own logs are in its take dir.
#
# Exit codes: 0 = every clip delivered. 3 = none failed, some still need approval. 1 = at least one clip failed QC or stopped.
# 2 = bad invocation (no clips, a DNA that does not load, ...), nothing was run.
set -uo pipefail

SK="$(cd "$(dirname "$0")" && pwd)"
usage() {
  echo "usage: batch_captions.sh <folder-of-cuts> <workroot> [--style <id>] [--dest <folder>]" >&2
  exit 2
}
[ $# -ge 2 ] || usage
DIR="$1"; ROOT="$2"
shift 2
EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --style | --dest) [ $# -ge 2 ] || usage; EXTRA+=("$1" "$2"); shift 2 ;;
    *) usage ;;
  esac
done
[ -d "$DIR" ] || { echo "STOP: folder of cuts not found: $DIR" >&2; exit 2; }

# your look is checked once, up front: a DNA that does not load would otherwise repeat the same STOP for every clip
if ! LOOK_OUT="$(python3 "$SK/lookprofile.py" /dev/null 2>&1)"; then
  echo "${LOOK_OUT%%$'\n'*}" >&2
  exit 2
fi

CLIPS=()
while IFS= read -r f; do
  [ -n "$f" ] && CLIPS+=("$f")
done < <(find "$DIR" -maxdepth 1 -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' \) ! -name '.*' | sort)
[ "${#CLIPS[@]}" -gt 0 ] || { echo "STOP: no .mp4, .mov or .m4v clips directly inside $DIR" >&2; exit 2; }

mkdir -p "$ROOT/batch-logs"
ROOT="$(cd "$ROOT" && pwd)"
SUMMARY="$ROOT/batch-summary.txt"
: > "$SUMMARY"

SEEN=""
N_OK=0; N_WAIT=0; N_QC=0; N_STOP=0
echo "batch: ${#CLIPS[@]} clip(s) from $DIR, takes in $ROOT"
for clip in "${CLIPS[@]}"; do
  # take name: the clip's stem with spaces as hyphens; a second clip with the same stem keeps its extension
  base="$(basename "$clip")"; stem="${base%.*}"; ext="${base##*.}"
  name="$(echo "$stem" | tr ' ' '-')"
  case " $SEEN " in *" $name "*) name="$name-$(echo "$ext" | tr 'A-Z' 'a-z')" ;; esac
  SEEN="$SEEN $name"
  take="$ROOT/$name"
  log="$ROOT/batch-logs/$name.log"
  ( bash "$SK/run_captions.sh" "$clip" "$take" ${EXTRA[@]+"${EXTRA[@]}"} ) </dev/null >"$log" 2>&1
  rc=$?
  # one plain sentence: the take's status.json message, else the last line of its output
  msg=""
  [ -s "$take/status.json" ] && msg="$(python3 "$SK/takectl.py" get "$take/status.json" message "" 2>/dev/null || true)"
  [ -n "$msg" ] || msg="$(grep -v '^[[:space:]]*$' "$log" | tail -n 1)"
  case "$rc" in
    0) result="delivered"; N_OK=$((N_OK + 1)); msg="$(grep '^DONE' "$log" | tail -n 1)"
       chk="$(grep '^CHECK' "$log" | tr '\n' ' ')"; [ -z "$chk" ] || msg="$msg $chk" ;;      # the editor's notes (the loudness line) ride on the line
    1) result="failed QC"; N_QC=$((N_QC + 1)) ;;
    2) case "$msg" in
         "Open "*"GATE.md"*) result="needs approval"; N_WAIT=$((N_WAIT + 1)) ;;
         *) result="stopped"; N_STOP=$((N_STOP + 1)) ;;
       esac ;;
    *) result="stopped"; N_STOP=$((N_STOP + 1)); msg="run_captions.sh exited $rc; see $log" ;;
  esac
  line="$(printf '%-28s %-15s %s' "$name" "$result" "$msg")"
  echo "$line"
  echo "$line" >> "$SUMMARY"
done

TOTAL="${#CLIPS[@]}"
FOOT="batch done: $N_OK delivered, $N_WAIT need approval, $N_QC failed QC, $N_STOP stopped (of $TOTAL). Summary: $SUMMARY"
echo "$FOOT"
echo "$FOOT" >> "$SUMMARY"

if [ $((N_QC + N_STOP)) -gt 0 ]; then exit 1; fi
if [ "$N_WAIT" -gt 0 ]; then exit 3; fi
exit 0
