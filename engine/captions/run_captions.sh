#!/bin/bash
# run_captions.sh <video> <take-dir> [--style <id>] [--title <name>] [--dest <folder>]
#
# The one command an operator runs, twice, per already-cut 9:16 clip. It chains every stage of the captions-only pipeline:
#
#   intake -> conform -> look -> words -> facetrack -> roomcheck
#     -> [gate apply, when GATE.md exists] -> spec -> plan -> [devices-propose, only when the look says devices: on] -> pacing -> legible
#     -> [gate build + STOP for approval, run 1]
#     -> devices -> checks -> matte -> render -> qc -> deliver (only on a QC PASS)
#
# <take-dir>  <workroot>/<take-name>, created if missing. Its basename is the take's NAME (it names <NAME>-reel.mp4 and the delivered title).
# --style     the caption style for this take (Brand/styles/<id>.json, then templates/styles; python3 styles.py list). It is written into
#             <take-dir>/caption-style.txt, so a later run of the same take keeps it. A `style: <id>` line under GATE.md's Overrides replaces it
#             (the human at the gate has the last word). With neither, the take gets the style your DNA names in captions.default_style, and with no
#             default your look exactly as the DNA makes it. The pick is checked first thing, before any time is spent, and look.json is made with
#             it, so spec, plan, render and the delivery records (NOTES.md, delivery.json) all carry it.
# --title     the delivered name ("<Title>"): the video is <dest>/<Title>.mp4 and the default dest is <workspace>/Finished/<Title>/. Without it the
#             take dir's basename is the title, which suits a take dir named after the video; an edit whose take dir is <project>/captions passes
#             its project's title here.
# --dest      where the finished video goes. Default: <workspace>/Finished/<Title>/ . The video is <dest>/<Title>.mp4, beside "<Title> - Transcript.md"
#             and "<Title> - Caption.txt", and its notes and evidence are <dest>/Build/<Title>/. A take is its folder, not its name: two takes that
#             share a Title (in different workroots) get " Take 2", " Take 3" ... and never overwrite each other, and delivering the same take again
#             replaces its own files (takectl.py delivery-name). Never inside the Cutroom install.
#
# Your look is your DNA (Brand/dna.json, through engine/dna.py), made into <take-dir>/look.json by lookprofile.py on every run.
#
# Resume. Every stage checks its own output and the files it was built from (make-style: an output older than an input is stale). A stage whose
# output is fresh is skipped, so editing your DNA (colors, layout, a font file), a style file, GATE.md or the words and running the same command
# again rebuilds exactly what depends on the edit: look.json -> spec.json -> plan.json -> render -> qc. Nothing needs FORCE for that.
#
# FORCE=1 wipes the DERIVED artifacts (cuts.json, devices.json, plan, matte, video, qc, and the tier/spec/gate records) and rebuilds them. It keeps the
# take's inputs (source copy, mezzanine, audio, transcript, face track) and look.json, the record of the look the take was made with (the look stage
# makes it again on every run), and never touches GATE.md; gate.json is re-applied from it. To re-transcribe, delete words.json. A take dir that was
# built from a different video, or from a file that has since been overwritten with a new export, stops with a plain line; FORCE=1 then rebuilds it
# from the new video (GATE.md is backed up to GATE.md.bak-<timestamp> and a fresh one is built, since the old one describes another transcript).
#
# The approval gate: GATE.md is built once and run_captions.sh never builds over an existing one (a forced rebuild from a different video moves the
# old one to GATE.md.bak-<ts> first). Every run before "approved: yes" stops with the same one-line instruction.
#
# status.json in the take dir is rewritten after every stage: {"stage", "ok", "message", "updated"}. Every failure prints one plain sentence; the full
# output of the failing stage is in <take-dir>/logs/<stage>.log.
#
# Exit codes: 0 = delivered. 1 = rendered but failed a QC gate, nothing shipped (reel-qc.json + evidence/ are ready to review). 2 = stopped before a
# finished render: the gate is not approved yet, or a stage failed.
set -Eeuo pipefail

SK="$(cd "$(dirname "$0")" && pwd)"
PY="$SK/takectl.py"
tk() { python3 "$PY" "$@"; }

usage() {
  echo "usage: run_captions.sh <video> <take-dir> [--style <id>] [--title <name>] [--dest <folder>]" >&2
  exit 2
}
[ $# -ge 2 ] || usage

VIDEO="$1"
T_RAW="$2"
shift 2
STYLE_FLAG=""
DEST_ARG=""
TITLE_FLAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --style) [ $# -ge 2 ] || usage; STYLE_FLAG="$2"; shift 2 ;;
    --dest) [ $# -ge 2 ] || usage; DEST_ARG="$2"; shift 2 ;;
    --title) [ $# -ge 2 ] || usage; TITLE_FLAG="$2"; shift 2 ;;
    *) usage ;;
  esac
done
FORCE="${FORCE:-0}"

[ -n "$VIDEO" ] && [ -n "$T_RAW" ] || usage
for tool in ffmpeg ffprobe python3 node; do
  command -v "$tool" >/dev/null 2>&1 || { echo "STOP: $tool is not installed (run bash install.sh, then bin/doctor.sh)" >&2; exit 2; }
done

# --- your look: checked before anything is created or any time is spent ---------------------------------------------
if ! LOOK_OUT="$(python3 "$SK/lookprofile.py" /dev/null 2>&1)"; then
  echo "${LOOK_OUT%%$'\n'*}" >&2
  exit 2
fi

mkdir -p "$T_RAW" "$T_RAW/logs"
T="$(cd "$T_RAW" && pwd)"
NAME="$(basename "$T")"
TITLE="${TITLE_FLAG:-$NAME}"
case "$TITLE" in */*) echo "STOP: the title '$TITLE' contains a slash" >&2; exit 2 ;; esac
[ -n "$TITLE" ] || { echo "STOP: --title needs a name" >&2; exit 2; }
CURRENT_STAGE="start"

if [ -n "$DEST_ARG" ]; then
  DEST="$DEST_ARG"
elif ! DEST="$(tk finished-dir "$TITLE" 2>&1)"; then
  echo "${DEST%%$'\n'*}" >&2
  exit 2
fi
if ! GUARD_OUT="$(tk guard-dest "$DEST" 2>&1)"; then
  echo "$GUARD_OUT" >&2
  exit 2
fi

# --- the caption style: --style (written to caption-style.txt), GATE.md's `style:` line, else caption-style.txt, else the DNA's default, else none;
# checked before any work ---------------------------------------------------------------------------------------------------------------------
if [ -n "$STYLE_FLAG" ]; then
  if ! SET_OUT="$(tk style-set "$T" "$STYLE_FLAG" 2>&1)"; then
    echo "${SET_OUT%%$'\n'*}" >&2
    exit 2
  fi
fi
if ! STYLE_ID="$(tk style "$T" 2>"$T/logs/style.log")"; then
  STYLE_MSG="$(grep -m1 '^STOP:' "$T/logs/style.log" 2>/dev/null || true)"
  echo "${STYLE_MSG:-STOP: the caption style check failed, see $T/logs/style.log}" >&2
  exit 2
fi
STYLE_ARGS=()
if [ -n "$STYLE_ID" ]; then STYLE_ARGS=(--style "$STYLE_ID"); fi

# --- small helpers ---------------------------------------------------------------------------------------------

write_status() { tk status "$T" "$1" "$2" "$3"; }   # write_status <stage> <true|false> <message>

extract_message() {
  # One plain sentence from a stage's log: the engine's own STOP: line (keys.py's cli() convention; the python scripts print it on stderr and the
  # bash ones too, and both land in the same log); otherwise the exception's last line for a raw traceback; else the log's last non-empty line.
  local log="$1" line
  line="$(grep -m1 '^STOP:' "$log" 2>/dev/null || true)"
  if [ -n "$line" ]; then echo "$line"; return; fi
  if grep -q 'Traceback (most recent call last)' "$log" 2>/dev/null; then
    awk '{a[NR]=$0} END{for(i=NR;i>=1;i--) print a[i]}' "$log" | grep -v '^[[:space:]]*File "' | grep -v '^[[:space:]]*$' | head -n 1
    return
  fi
  awk '{a[NR]=$0} END{for(i=NR;i>=1;i--) print a[i]}' "$log" | grep -v '^[[:space:]]*$' | head -n 1
}

on_err() {
  local ln="$1"
  local msg="an unexpected error stopped the take at line $ln in stage '$CURRENT_STAGE', see $T/logs/${CURRENT_STAGE}.log"
  write_status "$CURRENT_STAGE" false "$msg" 2>/dev/null || true
  echo "STOP: $msg" >&2
  exit 2
}
trap 'on_err $LINENO' ERR

stop_with() {   # stop_with <stage> <message>
  write_status "$1" false "$2"
  echo "STOP: ${2#STOP: }" >&2
  exit 2
}

# run_stage <name> <check-fn> -- <cmd...>
# Skips (status ok "already done") when <check-fn> passes. Otherwise runs <cmd...>, logging combined stdout and stderr to
# logs/<name>.log; a zero exit writes status ok, a non-zero exit prints one plain message from the log and exits 2.
run_stage() {
  local name="$1"; shift
  local check="$1"; shift
  [ "$1" = "--" ] && shift
  CURRENT_STAGE="$name"
  if "$check"; then
    write_status "$name" true "already done (skipped)"
    return 0
  fi
  local log="$T/logs/$name.log"
  trap - ERR; set +e
  "$@" >"$log" 2>&1
  local rc=$?
  set -e; trap 'on_err $LINENO' ERR
  if [ $rc -eq 0 ]; then
    write_status "$name" true "ok"
    return 0
  fi
  local msg
  msg="$(extract_message "$log")"
  [ -n "$msg" ] || msg="stage '$name' failed with no output, see $log"
  stop_with "$name" "$msg"
}

wipe_derived() {
  rm -rf "$T/matte" "$T/evidence"
  rm -f "$T/cuts.json" "$T/devices.json" "$T/devices-propose.json" "$T/plan.json" "$T/matte-windows.json" \
        "$T/render-props.json" "$T/render.mp4" "$T/render-meta.json" "$T/$NAME-reel.mp4" "$T/reel-qc.json" \
        "$T/gate.json" "$T/spec.json" "$T/tier.json"
}
wipe_inputs() {
  rm -f "$T/intake.json" "$T/mez.mp4" "$T/mez1080.mp4" "$T/mez.hdr.mov" "$T/audio.wav" "$T/audio16.wav" "$T/avskew.json" \
        "$T/words.json" "$T/words.json.pre-gate" "$T/words.json.pre-normalize" "$T/facetrack.json"
  rm -f "$T"/source.*
}

# --- the take dir's history: same source, a different one, or a forced rebuild -----------------------------------
SOURCE_CHANGED=0
if tk valid "$T/intake.json"; then
  if SRC_VERDICT="$(python3 "$PY" same-source "$VIDEO" "$T")"; then :; else
    PREV_SRC="$(tk get "$T/intake.json" source_original "unknown")"
    if [ "$FORCE" != "1" ]; then
      if [ "$SRC_VERDICT" = "edited" ]; then
        stop_with intake "$VIDEO has changed since this take was built from it (a new export over the old file); set FORCE=1 to rebuild the take from the new cut, or use a new take dir"
      fi
      stop_with intake "this take dir was already built from a different video ($PREV_SRC); set FORCE=1 to rebuild it from $VIDEO, or use a new take dir"
    fi
    SOURCE_CHANGED=1
  fi
fi
if [ "$SOURCE_CHANGED" = "1" ]; then
  if [ -f "$T/GATE.md" ]; then mv "$T/GATE.md" "$T/GATE.md.bak-$(date +%Y%m%d-%H%M%S)"; fi
  wipe_derived
  wipe_inputs
  echo "note: a different video, so the take was reset (the old GATE.md is saved as GATE.md.bak-*)"
elif [ "$FORCE" = "1" ]; then
  wipe_derived
fi
# a gate.json without its GATE.md describes nothing
if [ ! -s "$T/GATE.md" ]; then rm -f "$T/gate.json"; fi

# --- stage: intake ---------------------------------------------------------------------------------------------
check_intake() { tk valid "$T/intake.json"; }
run_stage intake check_intake -- python3 "$SK/intake.py" "$VIDEO" "$T"

# --- stage: conform --------------------------------------------------------------------------------------------
# The mezzanine is the picture everything reads: H.264, yuv420p, BT.709 limited range, 30 fps, no audio. There is no color grade; the cut keeps its color.
check_conform() { [ -s "$T/mez.mp4" ] && [ -s "$T/audio.wav" ] && [ -s "$T/audio16.wav" ] && tk valid "$T/avskew.json"; }
run_stage conform check_conform -- bash "$SK/conform.sh" "$T"

# --- stage: look -----------------------------------------------------------------------------------------------
# look.json is your look: lookprofile.py (the DNA's captions profile, with the take's caption style laid over it when it has one). It is regenerated on
# every run (milliseconds) and only replaced when its content differs, so an edit to the DNA, to a style file, or to the take's style pick reaches spec,
# plan and render by itself.
CURRENT_STAGE="look"
trap - ERR; set +e
python3 "$SK/lookprofile.py" "$T/look.json.new" ${STYLE_ARGS[@]+"${STYLE_ARGS[@]}"} >"$T/logs/look.log" 2>&1
LOOK_RC=$?
set -e; trap 'on_err $LINENO' ERR
if [ $LOOK_RC -ne 0 ]; then
  rm -f "$T/look.json.new"
  stop_with look "$(extract_message "$T/logs/look.log")"
fi
if tk valid "$T/look.json" && cmp -s "$T/look.json" "$T/look.json.new"; then
  rm -f "$T/look.json.new"
else
  mv "$T/look.json.new" "$T/look.json"
fi
write_status look true "ok"
TIER_MAX="$(tk get "$T/look.json" caption_tier_max FULL)"
MASTER_AUDIO="$(tk get "$T/look.json" master_audio off)"
AUTO_PUNCHES="$(tk get "$T/look.json" auto_punches false)"
DEVICES_ON="$(tk get "$T/look.json" devices off)"
TRANSCRIBE="$(tk get "$T/look.json" transcribe.engine local)"
SPEECH_LANG="$(tk get "$T/look.json" transcribe.language "")"
[ "$SPEECH_LANG" = "null" ] && SPEECH_LANG=""
export MASTER_AUDIO

# --- stage: words (transcription) -------------------------------------------------------------------------------
# Local by default (hear_local.py: engine/words.py, whisper.cpp + WhisperX, no key). Deepgram (hear.py) only when the look asks for it AND its key is set.
check_words() { tk valid "$T/words.json"; }
words_stage() {
  rm -f "$T/words.json.pre-gate" "$T/words.json.pre-normalize"
  if [ "$TRANSCRIBE" = "deepgram" ] && python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import keys; sys.exit(0 if keys.has("DEEPGRAM_API_KEY") else 1)' "$SK"; then
    python3 "$SK/hear.py" "$T/audio16.wav" "$T/words.json"
  else
    [ "$TRANSCRIBE" = "deepgram" ] && echo "note: transcribe is deepgram but DEEPGRAM_API_KEY is not in keys.env: using the local transcriber"
    python3 "$SK/hear_local.py" "$T/audio16.wav" "$T/words.json" ${SPEECH_LANG:+--language "$SPEECH_LANG"}
  fi
}
run_stage words check_words -- words_stage
# no-speech gate: every later stage assumes at least one word
CURRENT_STAGE="words"
if [ "$(tk nwords "$T/words.json")" = "0" ]; then
  rm -f "$T/words.json"
  stop_with words "no speech detected in $VIDEO, the transcript came back empty. Check the audio track (silent, no dialogue track, or the wrong stream)."
fi

# --- stage: facetrack ------------------------------------------------------------------------------------------
# facetrack.py's coordinate math is for a 1080x1920 canvas: it runs on the 1080 mezzanine, never the 4K one.
FACE_SRC="$T/mez.mp4"
[ -s "$T/mez1080.mp4" ] && FACE_SRC="$T/mez1080.mp4"
check_facetrack() { tk valid "$T/facetrack.json"; }
run_stage facetrack check_facetrack -- python3 "$SK/facetrack.py" "$FACE_SRC" "$T/facetrack.json"

# --- stage: roomcheck ------------------------------------------------------------------------------------------
check_roomcheck() { tk valid "$T/tier.json" && [ ! "$T/look.json" -nt "$T/tier.json" ] && [ ! "$T/facetrack.json" -nt "$T/tier.json" ]; }
run_stage roomcheck check_roomcheck -- python3 "$SK/roomcheck.py" "$T/facetrack.json" "$T/tier.json" "$TIER_MAX"

# --- the approval gate, apply side -----------------------------------------------------------------------------
# When GATE.md exists it is applied BEFORE the spec is built (spec.json is a function of tier.json + look.json +
# gate.json). Applied again whenever GATE.md is newer than gate.json, i.e. after any edit. Not approved: stop here.
GATE_MSG="Open $T/GATE.md, set approved: yes, run the same command again"
CURRENT_STAGE="gate"
if [ -s "$T/GATE.md" ]; then
  if tk approved "$T/gate.json" && [ ! "$T/GATE.md" -nt "$T/gate.json" ]; then
    write_status gate true "already approved"
  else
    trap - ERR; set +e
    python3 "$SK/capgate.py" apply "$T" >"$T/logs/gate-apply.log" 2>&1
    APPLY_RC=$?
    set -e; trap 'on_err $LINENO' ERR
    if [ $APPLY_RC -eq 3 ]; then
      write_status gate false "$GATE_MSG"
      echo "$GATE_MSG"
      exit 2
    elif [ $APPLY_RC -ne 0 ]; then
      stop_with gate "$(extract_message "$T/logs/gate-apply.log")"
    fi
    write_status gate true "approved and applied"
    grep '^warning:' "$T/logs/gate-apply.log" >&2 || true
  fi
fi

# --- stage: spec -----------------------------------------------------------------------------------------------
check_spec() {
  tk valid "$T/spec.json" && [ ! "$T/look.json" -nt "$T/spec.json" ] && [ ! "$T/tier.json" -nt "$T/spec.json" ] \
    && { [ ! -s "$T/gate.json" ] || [ ! "$T/gate.json" -nt "$T/spec.json" ]; }
}
spec_stage() {
  if [ -s "$T/gate.json" ]; then
    python3 "$SK/spec_adapter.py" "$T/tier.json" "$T/look.json" "$T/spec.json" --gate "$T/gate.json"
  else
    python3 "$SK/spec_adapter.py" "$T/tier.json" "$T/look.json" "$T/spec.json"
  fi
}
run_stage spec check_spec -- spec_stage

# --- stage: plan (normalize_words + cluster_plan, one resumable stage) ------------------------------------------
check_plan() {
  tk valid "$T/plan.json" && [ ! "$T/spec.json" -nt "$T/plan.json" ] && [ ! "$T/words.json" -nt "$T/plan.json" ] \
    && [ ! "$T/facetrack.json" -nt "$T/plan.json" ]
}
plan_stage() {
  local cf cut frames
  cf="$(tk cutframes "$T")" || return 1
  read -r cut frames <<< "$cf"
  python3 "$SK/normalize_words.py" "$T/words.json" "$T/look.json" || return 1
  python3 "$SK/cluster_plan.py" "$T/words.json" "$T/facetrack.json" "$T/plan.json" --spec "$T/spec.json" --cut "$cut" --frames "$frames"
}
run_stage plan check_plan -- plan_stage

# --- stage: devices-propose (devices.py propose): only when your look says devices: on ----------------------------
CURRENT_STAGE="devices-propose"
if [ "$DEVICES_ON" = "on" ]; then
  check_devices_propose() { tk valid "$T/devices-propose.json" && [ ! "$T/words.json" -nt "$T/devices-propose.json" ]; }
  run_stage devices-propose check_devices_propose -- python3 "$SK/devices.py" propose "$T/words.json" "$T/devices-propose.json"
else
  rm -f "$T/devices-propose.json"
  write_status devices-propose true "layout-event proposals are off (devices: on in your look turns them on)"
fi

# --- stage: pacing (cadence.py: source cuts, punch-ins, pace numbers) ---------------------------------------------
# a fresh plan.json has no "pace"; a plan that already went through cadence has it. Automatic punch-ins only when your DNA's auto_punches is true.
AUTO_FLAG=off; [ "$AUTO_PUNCHES" = "true" ] && AUTO_FLAG=on
check_pacing() { tk valid "$T/plan.json" && [ "$(tk get "$T/plan.json" pace.median_gap none)" != "none" ]; }
run_stage pacing check_pacing -- python3 "$SK/cadence.py" "$T/mez.mp4" "$T/plan.json" "$T/facetrack.json" "$T/plan.json" --cuts "$T/cuts.json" --auto "$AUTO_FLAG"

# --- stage: legible (legible.py: the picture under each soft build thought, through the pacing pass's punch-ins) ---------------------------------
# a fresh plan.json has no "legible"; a plan that went through this stage has it, and devices.py apply keeps it. A plan with no soft thought is left alone
CURRENT_STAGE="legible"
if [ "$(tk get "$T/plan.json" soft none)" != "none" ]; then
  check_legible() { tk valid "$T/plan.json" && [ "$(tk get "$T/plan.json" legible none)" != "none" ]; }
  run_stage legible check_legible -- python3 "$SK/legible.py" "$T"
else
  write_status legible true "no soft build thoughts in plan.json, nothing to read"
fi

# --- the approval gate, build side --------------------------------------------------------------------------------
CURRENT_STAGE="gate"
if [ ! -s "$T/GATE.md" ]; then
  trap - ERR; set +e
  python3 "$SK/capgate.py" build "$T" >"$T/logs/gate-build.log" 2>&1
  BUILD_RC=$?
  set -e; trap 'on_err $LINENO' ERR
  if [ $BUILD_RC -ne 0 ]; then
    stop_with gate "$(extract_message "$T/logs/gate-build.log")"
  fi
  write_status gate false "$GATE_MSG"
  echo "$GATE_MSG"
  exit 2
fi

# --- stage: devices (devices.py apply, only when the gate accepted layout events) ----------------------------------
# a plan rebuilt by cluster_plan.py never carries devicesApplied, so this always merges onto a fresh plan
CURRENT_STAGE="devices"
if [ -s "$T/devices.json" ]; then
  check_devices_apply() { tk valid "$T/plan.json" && [ "$(tk get "$T/plan.json" devicesApplied false)" = "true" ]; }
  run_stage devices check_devices_apply -- python3 "$SK/devices.py" apply "$T/plan.json" "$T/devices.json" "$T/facetrack.json" "$T/plan.json" "$T/spec.json"
else
  write_status devices true "no accepted layout events for this take"
fi

# --- what the plan the render draws asks a person to look at that GATE.md does not show (capgate.py checks) -------------------------------------------
# GATE.md is built once, from the first plan: a device a person's [bar] displaced, or a thought the legible stage flagged on the plan made after the gate,
# raises a CHECK line there would never be. Read after the devices stage, from the plan the render draws (a thought a page or a cta covers is gone from
# it, and so is any line about it), printed on every approved run the way the gate's warnings are, and filed in NOTES.md by deliver.sh from the same plan
CURRENT_STAGE="checks"
trap - ERR; set +e
python3 "$SK/capgate.py" checks "$T" >"$T/logs/checks.log" 2>&1
CHECKS_RC=$?
set -e; trap 'on_err $LINENO' ERR
if [ $CHECKS_RC -ne 0 ]; then
  stop_with checks "$(extract_message "$T/logs/checks.log")"
fi
grep '^CHECK ' "$T/logs/checks.log" >&2 || true

# --- stage: matte (personmatte.py, only when the plan has behind-text windows) -------------------------------------
# a 4K matte takes a long while: it is kept while the windows it was built for are unchanged and every frame is there
CURRENT_STAGE="matte"
if [ "$(tk needs-matte "$T/plan.json")" = "1" ]; then
  check_matte() { tk matte-sync "$T/plan.json" "$T/matte-windows.json" "$T/matte"; }
  run_stage matte check_matte -- python3 "$SK/personmatte.py" "$T/mez.mp4" "$T/matte-windows.json" "$T/matte"
else
  write_status matte true "no behind-text windows in plan.json, matte skipped"
fi

# --- stage: render (render.sh) -------------------------------------------------------------------------------------
# fresh when the video was rendered from this exact plan, audio setting and font files (render-meta.json records their hash)
check_render() {
  [ -s "$T/$NAME-reel.mp4" ] && tk valid "$T/render-meta.json" \
    && [ "$(tk get "$T/render-meta.json" render_key none)" = "$(tk render-key "$T" "$MASTER_AUDIO")" ]
}
run_stage render check_render -- bash "$SK/render.sh" "$T" "$NAME"

# --- the delivered name: "<Title>", the one this take already has in <dest>, else the first free one ----------------
STEM="$(tk delivery-name "$DEST" "$T" "$TITLE")"
if [ -n "$TITLE_FLAG" ] && [ "$STEM" != "$TITLE" ] && [ "${STEM#"$TITLE" Take }" = "$STEM" ]; then
  echo "note: this take already delivered as '$STEM' in $DEST, so that name stays; remove that delivery (its mp4, sidecars and Build/$STEM) to deliver as '$TITLE'"
fi

# --- stage: qc (reel_qc.py, the never-see gates; always re-run, it is the safety gate) ----------------------------
# the pace gate (G10) follows your look's pace_gate: off skips it, report measures it and never fails, enforce fails the take
CURRENT_STAGE="qc"
STRICT_FLAG=""
[ "$MASTER_AUDIO" = "loudnorm" ] && STRICT_FLAG="--strict-loudness"
trap - ERR; set +e
if [ -n "$STRICT_FLAG" ]; then
  python3 "$SK/reel_qc.py" "$T" "$NAME" "$STRICT_FLAG" >"$T/logs/qc.log" 2>&1
else
  python3 "$SK/reel_qc.py" "$T" "$NAME" >"$T/logs/qc.log" 2>&1
fi
QC_RC=$?
set -e; trap 'on_err $LINENO' ERR
if [ $QC_RC -eq 2 ]; then
  stop_with qc "$(extract_message "$T/logs/qc.log")"
elif [ $QC_RC -eq 1 ]; then
  FAILED_GATES="$(tk failed-gates "$T/reel-qc.json" 2>/dev/null || echo unknown)"
  WHY="$(grep -E '^  (G[-A-Z0-9]+): ' "$T/logs/qc.log" | head -n 3 | sed 's/^  //' | tr '\n' ' ' || true)"
  MSG="the captions did not pass QC ($FAILED_GATES), nothing was shipped. ${WHY}See $T/reel-qc.json and $T/evidence/contact-sheet.png."
  if [ -s "$DEST/$STEM.mp4" ]; then
    MSG="$MSG The file already in $DEST/$STEM.mp4 is from an earlier passing run and was not changed."
  fi
  write_status qc false "$MSG"
  echo "$MSG" >&2
  exit 1
fi
write_status qc true "PASS"

# --- stage: deliver (deliver.sh, PASS only) ------------------------------------------------------------------------
CURRENT_STAGE="deliver"
# re-deliver whenever the video changed, a sidecar is missing, a caption.md was written after the Caption.txt beside the video, or the caption style changed
# since the delivery (NOTES.md states it, and a restyled video can come out byte for byte the same)
check_deliver() { [ -s "$DEST/$STEM.mp4" ] && cmp -s "$T/$NAME-reel.mp4" "$DEST/$STEM.mp4" \
  && [ -f "$DEST/Build/$STEM/delivery.json" ] \
  && [ "$(tk get "$DEST/Build/$STEM/delivery.json" caption_style "")" = "$(tk style-record "$T")" ] \
  && [ -s "$DEST/$STEM - Transcript.md" ] && [ -s "$DEST/$STEM - Caption.txt" ] && [ ! "$T/caption.md" -nt "$DEST/$STEM - Caption.txt" ]; }
mkdir -p "$DEST"
run_stage deliver check_deliver -- bash "$SK/deliver.sh" "$T" "$NAME" "$STEM" "$DEST"

# the editor's note: reel_qc.py leaves one plain sentence in G7 only when G7 is audit-only and the mix is off the loudness target
LOUD_CHECK="$(tk get "$T/reel-qc.json" gates.G7.check "")"
if [ -n "$LOUD_CHECK" ]; then echo "CHECK: $LOUD_CHECK"; fi
if grep -q "CAPTION NEEDED" "$DEST/$STEM - Caption.txt" 2>/dev/null; then
  echo "CAPTION NEEDED -- write the post caption into $T/caption.md and run the same command again (it re-delivers the sidecar without a re-render)"
fi
echo "DONE -- $DEST/$STEM.mp4"
