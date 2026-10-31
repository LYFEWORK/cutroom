#!/bin/bash
# run_take.sh <workroot> <name> <src>
# One talking-head take, from the camera file to a mastered cut with its QC and sidecars. Everything runs on this Mac.
# Stops on the first failure and writes <workroot>/<name>/status.json. Edit SCRIPT.md (set `approved: yes`) and run it again to go on from the gate.
#
#   prep -> words -> face -> room -> sentences -> plan -> SCRIPT.md gate -> apply -> cut + clipped-word ladder -> cut audio -> voice master -> mux
#   -> the master's own words -> face on the master -> QC gates -> sidecars
#
# Env: AUDIO=skip     the cut's own audio is the master (test only)
#      JOINTS=warn    an exhausted clipped-word ladder records `waived` in joints.json and goes on, instead of stopping
#      REFRAME=on     a landscape source is cropped to a face-centred 9:16 in prep
#      KEEP_LANDSCAPE=on   a landscape source gets a 1920x1080 mezzanine in prep
#      FACELESS=on    nobody is on camera: no face stages, and the QC face rule is off
#      HOOK_FIRST=N   sentence N goes first in the cut, labelled hook (off by default: the cut keeps the order the speaker said things in)
# Settings (the Whisper model, the word-timing python, the language) come from ~/.cutroom/config.json (CUTROOM_HOME moves it).
#
# Resume: re-running the same name continues at the first missing artifact. A changed source moves the stale artifacts to <name>/stale-<time>/.
# Every stage writes its output to <name>/last-stage.log; a failure prints ONE "STOP: ..." line and exits 2.
set -u
if [ $# -lt 3 ]; then
  echo "usage: run_take.sh <workroot> <name> <src>" >&2; exit 2
fi
PY="${CUTROOM_PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2> /dev/null \
  || { echo "STOP: Cutroom needs Python 3.12 or newer ($PY is $("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2> /dev/null || echo missing)): install one (brew install python@3.12) and run again" >&2; exit 2; }
mkdir -p "$1"
ROOT="$(cd "$1" && pwd)"; N="$2"; SRC="$3"
E="$(cd "$(dirname "$0")" && pwd -P)"; T="$ROOT/$N"; mkdir -p "$T"
# status.json is the machine-readable record of the take: {stage, ok, message, notes[], flags[], updated}. notes ACCUMULATE across calls (a note
# has to survive to done), and flags are copied from a captions plan when one sits beside the take.
status(){ "$PY" - "$T/status.json" "$1" "$2" "$3" "${4:-}" "$T/captions-plan.json" <<'PYEOF'
import json, os, sys, datetime
p, stage, ok, msg, note, planp = sys.argv[1:7]
cur = {}
if os.path.exists(p):
    try: cur = json.load(open(p))
    except (OSError, ValueError): cur = {}
notes = [n for n in (cur.get("notes") or []) if isinstance(n, str)]
if note and note not in notes: notes.append(note)
flags = cur.get("flags") or []
if os.path.exists(planp):
    try: flags = json.load(open(planp)).get("flags") or []
    except (OSError, ValueError): pass
json.dump({"stage": stage, "ok": ok == "1", "message": msg, "notes": notes, "flags": flags,
           "updated": datetime.datetime.now().isoformat(timespec="seconds")}, open(p, "w"), indent=1)
PYEOF
}
# Every staged command's combined stdout+stderr is TRUNCATED into $T/last-stage.log right before it runs (the log always holds only the stage that just
# ran, so a grep can never surface a stale STOP: line from three stages ago). last_stop() reads the last such line out of it.
last_stop(){ local line; line=$({ grep '^STOP:' "$1" 2> /dev/null || true; } | tail -1); printf '%.300s' "$line"; }
stage(){ local name="$1"; shift; STAGE="$name"; echo "== [$N] $name"
  : > "$T/last-stage.log"
  if "$@" 2>&1 | tee -a "$T/last-stage.log"; then
    status "$name" 1 ok
  else
    local rc="${PIPESTATUS[0]}" stop; stop="$(last_stop "$T/last-stage.log")"
    if [ -n "$stop" ]; then status "$name" 0 "$stop"; else status "$name" 0 "$name failed (exit $rc); see the output above"; fi
    exit 2
  fi
}
# Anything NOT wrapped in `stage` (ffmpeg, the small python one-liners) would fail silently and surface stages later as the wrong message: set -Ee
# and this trap make every one of them terminal. The marker is a file, not a variable: a failure inside a $(...) runs the trap in a SUBSHELL, and the
# parent would otherwise report the same failure a second time.
STAGE=start; STOPMARK="$T/.run-take-stop"; rm -f "$STOPMARK"
rm -f "$T/status.json"   # notes and flags describe THIS run
on_err(){ local rc=$?; local ln="$1"
  if [ -e "$STOPMARK" ]; then exit "$rc"; fi
  : > "$STOPMARK"
  local stop; stop="$(last_stop "$T/last-stage.log")"
  if [ -n "$stop" ]; then
    echo "STOP: $STAGE failed at run_take.sh line $ln (exit $rc): ${stop#STOP: }" >&2
    status "$STAGE" 0 "$stop"
  else
    echo "STOP: $STAGE failed at run_take.sh line $ln (exit $rc)" >&2
    status "$STAGE" 0 "$STAGE failed at run_take.sh line $ln (exit $rc); see the output above"
  fi
  exit 2
}
trap 'on_err $LINENO' ERR
set -Ee -o pipefail
# The resume key is the take NAME, so the source's identity is recorded and the derived artifacts are MOVED ASIDE when it changes: the same name over a
# different source must never reuse the first run's words and plan. The engine never deletes.
STAGE=source; : > "$T/last-stage.log"
"$PY" - "$T" "$SRC" "$ROOT/mez/$N.mp4" "$ROOT/wx/$N.wav" "$ROOT/wx/$N.wx.json" "$ROOT/wx/$N.words.json" <<'PYEOF' 2>&1 | tee -a "$T/last-stage.log"
import json, os, sys, shutil, datetime
T, src = sys.argv[1], sys.argv[2]; outside = sys.argv[3:]
if not os.path.exists(src):
    print("STOP: source not found:", src); sys.exit(2)
st = os.stat(src); now = {"src": os.path.abspath(src), "mtime": int(st.st_mtime), "size": st.st_size}
p = os.path.join(T, "source.json"); old = None
if os.path.exists(p):
    try: old = json.load(open(p))
    except (OSError, ValueError): old = None
if old and any(old.get(k) != now[k] for k in now):
    stale = os.path.join(T, "stale-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")); os.makedirs(stale, exist_ok=True)
    moved = []
    for f in ["words.json", "facetrack.json", "tier.json", "sentences.json", "plan.json", "words.wx.json", "SCRIPT.md"]:
        a = os.path.join(T, f)
        if os.path.exists(a): shutil.move(a, os.path.join(stale, f)); moved.append(f)
    for a in outside:
        if os.path.exists(a): shutil.move(a, os.path.join(stale, os.path.basename(a))); moved.append(os.path.basename(a))
    print("source changed (%s -> %s): moved %d stale artifacts to %s" % (old.get("src"), now["src"], len(moved), stale))
json.dump(now, open(p, "w"), indent=1)
PYEOF
# Resume guards test -s, not -f: a stage that died mid-write leaves a 0-byte artifact, and -f would skip the stage on the next run and report it green.
# The prep resume key is its LAST artifacts (the face json, the transcript pack and the words are written after the mezzanine).
PREPARGS=(); [ "${REFRAME:-}" = "on" ] && PREPARGS=(--reframe); [ "${KEEP_LANDSCAPE:-}" = "on" ] && PREPARGS=(${PREPARGS[@]+"${PREPARGS[@]}"} --keep-landscape)
FACELESS_ON=0; [ "${FACELESS:-}" = "on" ] && FACELESS_ON=1
if [ ! -s "$ROOT/mez/$N.mp4" ] || [ ! -s "$ROOT/wx/$N.wx.json" ] || [ ! -s "$ROOT/wx/$N.face.json" ] || [ ! -s "$ROOT/tx/$N.txt" ]; then
  stage prep "$PY" "$E/prep.py" "$ROOT" "$N" "$SRC" ${PREPARGS[@]+"${PREPARGS[@]}"}
fi
[ -s "$T/words.json" ] || stage words "$PY" "$E/words.py" "$ROOT/wx/$N.wav" --out "$T/words.json" --work "$ROOT/wx" --logs "$ROOT/logs"
if [ "$FACELESS_ON" = 0 ]; then
  [ -s "$T/facetrack.json" ] || stage face "$PY" "$E/face.py" track "$ROOT/mez/$N.mp4" "$T/facetrack.json"
  [ -s "$T/tier.json" ] || stage room "$PY" "$E/face.py" room "$T/facetrack.json" "$T/tier.json"
fi
[ -s "$T/sentences.json" ] || stage sentences "$PY" "$E/cut.py" sentences "$T/words.json" "$T/sentences.json"
PLANARGS=(); [ -n "${HOOK_FIRST:-}" ] && PLANARGS=(--hook-first "$HOOK_FIRST"); [ "$FACELESS_ON" = 1 ] && PLANARGS=(${PLANARGS[@]+"${PLANARGS[@]}"} --faceless)
[ -s "$T/plan.json" ] && [ -s "$T/words.wx.json" ] || stage plan "$PY" "$E/cut.py" plan "$T/words.json" "$T/sentences.json" "$N" "$ROOT" "$T" ${PLANARGS[@]+"${PLANARGS[@]}"}
[ -s "$T/SCRIPT.md" ] || stage gate "$PY" "$E/script.py" build "$T/words.json" "$T/plan.json" "$T/SCRIPT.md"
if ! grep -q '^approved: yes' "$T/SCRIPT.md"; then status gate 1 "waiting for approval: edit $T/SCRIPT.md and set approved: yes"; echo "waiting for approval"; exit 0; fi
stage apply "$PY" "$E/script.py" apply "$T/SCRIPT.md" "$T/plan.json" "$T/plan.json" --words "$T/words.json"
# The clipped-word ladder. A FAIL widens plan.json's lead_extra (head) or tail_extra (tail) by 0.04 s: the cutter moves EVERY cut edge outward by that
# much into the quiet beside it, never into the next speech. `cut.py ladder` picks the side, and refuses a retry that cannot move anything: when nothing
# a pad can move failed, when the last retry did not move the clipped edge, when both knobs are at their 0.12 s limit. The widened values live in
# plan.json (script.py apply keeps them), so a re-run of an exhausted take starts from them, not from the default.
STAGE=joints; TRIES=0; rm -f "$T/.joints-prev.edl.json"
while :; do
  # Skip the cut only when it is provably still fresh (non-empty, NEWER than plan.json, and its EDL carries the struck-word list the checker reads),
  # otherwise every post-gate re-run would cut and listen again. The ladder REWRITES plan.json when it widens, which makes this check stale on the next
  # pass through the loop, so the retry still re-cuts.
  [ -s "$T/$N-cut.mp4" ] && grep -q '"dropped"' "$T/$N-cut.edl.json" 2> /dev/null && [ "$T/$N-cut.mp4" -nt "$T/plan.json" ] && [ -s "$T/$N-cut.edl.json" ] || stage cut "$PY" "$E/cut.py" cut "$T/plan.json"
  STAGE=joints; echo "== [$N] joints"
  rm -f "$T/joints.json"
  JC=0; "$PY" "$E/cut.py" joints "$T/$N-cut.mp4" "$T/$N-cut.edl.json" "$T/words.json" "$T/joints.json" || JC=$?
  if [ "$JC" -eq 0 ]; then break; fi
  # Branch on the written verdict, not on the exit code: the checker exits 1 for a FAIL *and* for any unhandled exception, so a crash must not burn
  # three full re-cuts and then report "clipped words".
  VERD=""
  if [ -s "$T/joints.json" ]; then
    VERD=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("verdict",""))' "$T/joints.json")
  fi
  if [ "$VERD" != "FAIL" ]; then
    echo "STOP: the joint check exited $JC without writing a FAIL verdict: it stopped or crashed, this is not a clipped-word retry" >&2
    status joints 0 "the joint check exited $JC without a verdict (stopped or crashed before writing $T/joints.json); see the output above"
    exit 2
  fi
  TRIES=$((TRIES+1))
  WHY=""
  if [ "$TRIES" -ge 3 ]; then
    WHY="clipped words after 3 tries"
  else
    LRC=0; LADDER=$("$PY" "$E/cut.py" ladder "$T/plan.json" "$T/joints.json" "$T/$N-cut.edl.json" "$T/.joints-prev.edl.json") || LRC=$?
    echo "$LADDER"
    if [ "$LRC" -ne 0 ]; then WHY="clipped words and no retry can move them: ${LADDER#LADDER STOP: }"; fi
  fi
  if [ -n "$WHY" ]; then
    # JOINTS=warn (env): with the switch set, an exhausted ladder RECORDS the clip list in the status notes and continues instead of stopping; the
    # finished video is then judged by listening. Unset (the default) the ladder still stops the run. This never fakes a PASS.
    if [ "${JOINTS:-}" = "warn" ]; then
      echo "JOINTS=warn: $WHY; continuing and recording it"
      "$PY" -c 'import json,sys; j=json.load(open(sys.argv[1])); j["waived"]="JOINTS=warn"; json.dump(j,open(sys.argv[1],"w"),indent=1)' "$T/joints.json"
      status joints 1 "JOINTS=warn: clipped-word check unresolved ($WHY); see $T/joints.json"
      break
    fi
    status joints 0 "$WHY; see $T/joints.json"; exit 2
  fi
  cp "$T/$N-cut.edl.json" "$T/.joints-prev.edl.json"
done
status joints 1 ok
stage cut-audio "$PY" "$E/sound.py" downmix "$T/$N-cut.mp4" "$T/$N-cut.wav"
STAGE=audio
if [ "${AUDIO:-}" = "skip" ]; then
  echo "== [$N] audio"; echo "AUDIO=skip: using the cut's own audio as master (test only)"
  cp "$T/$N-cut.wav" "$T/master.wav"; status audio 1 "AUDIO=skip: the cut's own audio is the master (test only)" "AUDIO=skip: the cut's own audio is the master (test only)"
else
  # the voice master: the clean-up chain and a TWO-PASS loudnorm at -16 LUFS / -2.0 dBTP, so the AAC encode that follows still lands inside the loudness gate
  stage audio "$PY" "$E/sound.py" master "$T/$N-cut.wav" "$T/master.wav"
fi
[ -s "$T/$N-cut-master.mp4" ] && [ "$T/$N-cut-master.mp4" -nt "$T/master.wav" ] && [ "$T/$N-cut-master.mp4" -nt "$T/$N-cut.mp4" ] || stage mux ffmpeg -y -v error -i "$T/$N-cut.mp4" -i "$T/master.wav" -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -ac 2 -shortest -movflags +faststart "$T/$N-cut-master.mp4"
stage master16 ffmpeg -y -v error -i "$T/master.wav" -ar 16000 -ac 1 "$T/master16.wav"
stage master-words "$PY" "$E/words.py" "$T/master16.wav" --out "$T/cut.words.json" --work "$T/words-work" --logs "$T/logs"
if [ "$FACELESS_ON" = 0 ]; then stage master-face "$PY" "$E/face.py" track "$T/$N-cut-master.mp4" "$T/cut.facetrack.json"; fi
# The QC gates. NOT run through stage(): qc.py exits 0 PASS / 1 FAIL / 2 STOP, and the two failures are not the same kind of thing. A STOP is a
# missing input, a stage failure like any other. A FAIL is a finished measurement saying the video is not shippable, which belongs on `done`;
# stage() would file it as "qc failed" and leave a batch to work out whether the engine broke or the video did.
STAGE=qc; echo "== [$N] qc"; : > "$T/last-stage.log"
QCARGS=(); [ "$FACELESS_ON" = 1 ] && QCARGS=(--faceless)
RQ=0
if "$PY" "$E/qc.py" "$T" "$N" ${QCARGS[@]+"${QCARGS[@]}"} 2>&1 | tee -a "$T/last-stage.log"; then RQ=0; else RQ="${PIPESTATUS[0]}"; fi
if [ "$RQ" -eq 2 ]; then
  QCSTOP="$(last_stop "$T/last-stage.log")"
  status qc 0 "${QCSTOP:-qc.py stopped (exit 2); see the output above}"; exit 2
fi
if [ "$RQ" -ne 0 ]; then
  FAILED=$("$PY" -c 'import json,sys
g=json.load(open(sys.argv[1]))["gates"]
print(", ".join("%s (%s)" % (k, v["numbers"]) for k, v in sorted(g.items()) if v.get("pass") is False) or "unknown")' "$T/qc.json")
  echo "STOP: the QC gates FAILED: $FAILED" >&2
  status done 0 "QC gates FAILED: $FAILED; see $T/qc.json, $T/evidence/strip.png and $T/evidence/contact-sheet.png" "qc FAIL: $FAILED"
  exit 2
fi
status qc 1 ok "qc PASS: gates G1-G5, G7, COLOR and WORDS; G6 is the human read, $T/evidence/strip.png"
# Every finished video ships with "<stem> - Transcript.md" and "<stem> - Caption.txt". The caption is $T/caption.md when one has been written;
# otherwise a CAPTION NEEDED placeholder lands beside the video.
CAP=(); if [ -s "$T/caption.md" ]; then CAP=(--caption "$T/caption.md"); fi
stage sidecars "$PY" "$E/sidecars.py" "$T/$N-cut-master.mp4" --words "$T/cut.words.json" ${CAP[@]+"${CAP[@]}"}
if grep -q 'CAPTION NEEDED' "$T/$N-cut-master - Caption.txt" 2> /dev/null; then
  # A video with a placeholder caption is not delivered: done, but ok:false until the caption exists.
  status done 0 "caption pending: the video is at $T/$N-cut-master.mp4; write $T/caption.md, then run sidecars.py \"$T/$N-cut-master.mp4\" --words \"$T/cut.words.json\" --caption \"$T/caption.md\"" "caption pending"
  echo "== [$N] VIDEO READY, CAPTION PENDING -> $T/$N-cut-master.mp4"; exit 0
fi
status done 1 "video: $T/$N-cut-master.mp4" "qc PASS"; echo "== [$N] DONE -> $T/$N-cut-master.mp4"
