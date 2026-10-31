#!/bin/bash
# deliver.sh <take-dir> <take-name> "<delivered name>" "<dest-folder>"
#
# Files a finished, PASSED take into its delivery folder under its delivered name (run_captions.sh passes `takectl.py delivery-name`: "<Title>", or
# "<Title> Take 2" when another take owns that name):
#
#   <dest>/<name>.mp4                   the captioned video
#   <dest>/<name> - Transcript.md       the take's words      (written by engine/sidecars.py)
#   <dest>/<name> - Caption.txt         the post caption: <take-dir>/caption.md when one has been written (engine/sidecars.py decides what a take with none gets)
#   <dest>/Build/<name>/                this take's own audit trail, so two takes never overwrite each other's evidence:
#       NOTES.md                        plain-language notes (look, tier/zone, caption style, hero words, verdict, loudness, source, render),
#                                       with a CHECK line on top for each thing a person should look at: a mix off the loudness target, and each
#                                       soft build CHECK line GATE.md does not show (capgate.py checks: what a person's edits or the plan made after the gate raised)
#       delivery.json                   whose delivery this is: the take's folder, so delivering the same take again replaces its own files and a
#                                       different take with the same folder name never does; and the caption style it was filed with
#       GATE.md  gate.json  reel-qc.json  evidence/
#
# Refuses to deliver anything that has not PASSED reel_qc.py's gates: if reel-qc.json is missing, the QC stage never
# ran and this script stops rather than guessing a verdict. Refuses a dest inside the Cutroom install, and a name another take owns.
#
# NOTES.md is built only from what already exists in <take-dir>; every field is read defensively (a missing file or
# key becomes "unknown" or is left out).
set -Eeuo pipefail

usage() { echo "usage: deliver.sh <take-dir> <take-name> \"<delivered name>\" \"<dest-folder>\"" >&2; exit 2; }
[ $# -ge 4 ] || usage
[ -d "$1" ] || { echo "STOP: take-dir not found: $1" >&2; exit 2; }
T="$(cd "$1" && pwd)"
N="$2"
STEM="$3"
DEST="$4"
[ -n "$N" ] || usage
[ -n "$STEM" ] || usage
[ -n "$DEST" ] || usage
case "$STEM" in */*) echo "STOP: the delivered name '$STEM' contains a slash; rename the take" >&2; exit 2 ;; esac

SK="$(cd "$(dirname "$0")" && pwd)"
python3 "$SK/takectl.py" guard-dest "$DEST"

REEL="$T/$N-reel.mp4"
[ -s "$REEL" ] || { echo "STOP: $REEL is missing. Run render.sh first" >&2; exit 2; }

QC="$T/reel-qc.json"
[ -s "$QC" ] || { echo "STOP: $QC is missing. Run reel_qc.py first; nothing ships without a QC verdict" >&2; exit 2; }
VERDICT="$(python3 "$SK/takectl.py" get "$QC" verdict | tr '[:lower:]' '[:upper:]')"
if [ "$VERDICT" != "PASS" ]; then
  echo "STOP: $QC verdict is '$VERDICT', not PASS. Fix the failing gate(s) and re-render before delivering" >&2
  exit 2
fi

python3 "$SK/takectl.py" delivery-free "$DEST" "$STEM" "$T"
BUILD="$DEST/Build/$STEM"
mkdir -p "$BUILD"
python3 "$SK/takectl.py" delivery-record "$BUILD" "$STEM" "$T"
cp "$REEL" "$DEST/$STEM.mp4"

# Every exported video ships with "<name> - Transcript.md" and "<name> - Caption.txt" beside it (engine/sidecars.py writes both). The transcript is this
# take's own words; the caption is <take-dir>/caption.md when one has been written.
SIDECARS="$(cd "$SK/.." && pwd)/sidecars.py"
[ -f "$SIDECARS" ] || SIDECARS="$SK/sidecars_fallback.py"
CAP=(); if [ -s "$T/caption.md" ]; then CAP=(--caption "$T/caption.md"); fi
WORDS=(); if [ -s "$T/words.json" ]; then WORDS=(--words "$T/words.json"); fi
python3 "$SIDECARS" "$DEST/$STEM.mp4" ${WORDS[@]+"${WORDS[@]}"} ${CAP[@]+"${CAP[@]}"}

if [ -d "$T/evidence" ]; then rm -rf "$BUILD/evidence"; cp -R "$T/evidence" "$BUILD/evidence"; fi
for f in GATE.md gate.json reel-qc.json; do
  if [ -f "$T/$f" ]; then cp "$T/$f" "$BUILD/$f"; fi
done

SOFT_CHECKS="$(python3 "$SK/capgate.py" checks "$T")"
python3 - "$T" "$N" "$STEM" "$BUILD/NOTES.md" "$(python3 "$SK/takectl.py" style-record "$T")" "$SOFT_CHECKS" <<'PYEOF'
import json, os, sys, datetime

T, N, title, out_path, style_record, soft_checks = sys.argv[1:7]


def load(name):
    p = os.path.join(T, name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


look = load("look.json") or {}
tier = load("tier.json") or {}
spec = load("spec.json") or {}
gate = load("gate.json") or {}
plan = load("plan.json") or {}
intake = load("intake.json") or {}
render_meta = load("render-meta.json") or {}
qc = load("reel-qc.json") or {}
ov = gate.get("overrides") or {}

brand_name = look.get("brand") or "unknown"
tier_used = ov.get("tier") or spec.get("tier") or tier.get("tier") or "unknown"
zone_used = ov.get("zone") or spec.get("zone") or look.get("zone") or "unknown"

# the hero row of each cluster with more than one row (the same rule GATE.md stars by), once per word
seen, hero_words = set(), []
for e in plan.get("events") or []:
    if e.get("mode") not in ("behind", "front"):
        continue
    rows = [w for w in e.get("words") or [] if w.get("text")]
    if len(rows) < 2:
        continue
    for w in rows:
        text = w["text"]
        key = "".join(c for c in text.lower() if c.isalnum())
        if w.get("hero") and key not in seen:
            seen.add(key)
            hero_words.append(text)

verdict = str(qc.get("verdict") or "unknown").upper()
src = intake.get("source_original") or intake.get("source") or ""
source_filename = os.path.basename(str(src)) if src else "unknown"

rendered_at = render_meta.get("rendered_at")
if not rendered_at:
    p = os.path.join(T, f"{N}-reel.mp4")
    rendered_at = (datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")
                   if os.path.isfile(p) else "unknown")
scale = render_meta.get("scale", "unknown")

# loudness (G7): always in the notes; one CHECK line on top when the mix as delivered is off the target and the engine left it alone
g7 = (qc.get("gates") or {}).get("G7") or {}
if g7.get("lufs") is None:
    loudness = "not measured"
else:
    loudness = f"{g7['lufs']} LUFS, true peak {g7['true_peak']} dBTP"
    if g7.get("mode") == "enforced":
        loudness += " (your look's master_audio is loudnorm: normalised to -16 LUFS, true peak -2 dBTP, and checked at -16 +/- 1 LUFS)"
    else:
        loudness += " (audit only: your look's master_audio is off, so the engine leaves the mix as it came)"

# the caption style: the one this take was made with (its pick, or your DNA's default style), else your own look
style_text = style_record if style_record else "none (your own look)"

lines = [f"# {title}", ""]
checks = [(qc.get("gates") or {}).get(k, {}).get("check") for k in ("G7",)]
soft = [c for c in soft_checks.splitlines() if c.strip()]     # capgate.py checks: already whole CHECK lines, as run_captions.sh printed them
if any(checks) or soft:
    lines += [f"CHECK: {c}" for c in checks if c] + soft + [""]
lines += [
    f"- Look: {brand_name}" + (f" ({look['id']})" if look.get("id") else ""),
    f"- Take: {N}",
    f"- Tier / zone: {tier_used} / {zone_used}",
    f"- Caption style: {style_text}",
    f"- Hero words: {', '.join(hero_words) if hero_words else '(none)'}",
    f"- Gate verdict: {verdict}",
    f"- Loudness: {loudness}",
    f"- Source file: {source_filename}",
    f"- Rendered: {rendered_at} ({scale}, {render_meta.get('color_space', 'unknown')})",
]

applied = []
for fx in ov.get("fixes") or []:
    applied.append(f'fix "{fx.get("from")}" -> "{fx.get("to")}"')
for k, label in (("hero_add", "hero"), ("hero_drop", "no-hero"), ("allow", "allow")):
    for v in ov.get(k) or []:
        applied.append(f'{label} "{v}"')
if ov.get("subject"):
    applied.append(f'subject: {ov["subject"]}')
if ov.get("tier"):
    applied.append(f'tier: {ov["tier"]}')
if ov.get("zone"):
    applied.append(f'zone: {ov["zone"]}')
dev = ov.get("devices") or {}
for ev in dev.get("layout_events") or []:
    applied.append(f'{ev.get("mode")} {ev.get("start")}-{ev.get("end")}')
for k in sorted((dev.get("device_overrides") or {}).keys()):
    if k not in ("hero_add", "hero_drop"):
        applied.append(f"device override: {k}")
if applied:
    lines += ["", "Overrides applied at the gate:"] + [f"- {a}" for a in applied]
if gate.get("warnings"):
    lines += ["", "Warnings from the gate:"] + [f"- {w}" for w in gate["warnings"]]

notes_reason = gate.get("notes") or tier.get("reason") or ""
if notes_reason:
    lines += ["", f"Notes: {notes_reason}"]

with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")
PYEOF

echo "DELIVER-DONE $DEST/$STEM.mp4"
ls -la "$DEST"
