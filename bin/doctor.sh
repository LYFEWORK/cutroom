#!/bin/bash
# doctor.sh: proves this Mac can run Cutroom. Changes nothing except rebuilding a stale compiled helper.
#   bash ~/cutroom/bin/doctor.sh [--render]
#   --render   add a 10-frame Remotion smoke render
# Exits 1 when anything the pipeline needs is missing or broken. Warnings are for optional parts.
set -uo pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
RENDER=0
for a in "$@"; do case "$a" in --render) RENDER=1 ;; -h | --help) sed -n '2,5p' "$0"; exit 0 ;; esac; done
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
with_timeout() { local s="$1"; shift; perl -e 'alarm shift; exec @ARGV' "$s" "$@"; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

step "Tools"
if have ffmpeg; then pass "ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}')"; else fail "ffmpeg missing (brew install ffmpeg)"; fi
have ffprobe && pass "ffprobe" || fail "ffprobe missing (brew install ffmpeg)"
ffmpeg -hide_banner -encoders 2> /dev/null | grep -q h264_videotoolbox && pass "VideoToolbox encoder" || warn "no h264_videotoolbox: software encodes only"
have whisper-cli && pass "whisper-cli" || fail "whisper-cli missing (brew install whisper-cpp)"
if [ "$(stat -f %z "$WHISPER_MODEL" 2> /dev/null || echo 0)" = "$WHISPER_MODEL_BYTES" ]; then pass "Whisper model $(tilde "$WHISPER_MODEL")"; else fail "Whisper model missing or partial at $(tilde "$WHISPER_MODEL") (install.sh downloads it)"; fi
if have node && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 18 ]; then pass "node $(node -v)"; else fail "node 18+ missing (brew install node)"; fi
if python3 -c "import PIL, numpy" 2> /dev/null; then pass "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))') + pillow + numpy"; else fail "python3 pillow/numpy missing (install.sh installs them)"; fi
have yt-dlp && pass "yt-dlp (reference study)" || warn "yt-dlp missing: reference links cannot be downloaded (brew install yt-dlp)"
[ -d "/Applications/Google Chrome.app" ] && pass "Google Chrome" || warn "Google Chrome not installed (optional)"
have swiftc && pass "swiftc" || fail "swiftc missing (xcode-select --install)"

step "Word timing (WhisperX venv)"
if [ ! -x "$VENV_PYTHON" ]; then
  fail "venv python missing: $(tilde "$VENV_PYTHON") (install.sh creates it)"
elif out=$("$VENV_PYTHON" -c "import whisperx, torch, scipy, numpy, PIL; print(torch.__version__)" 2>&1); then
  pass "venv imports whisperx + torch $out + scipy + numpy + pillow"
  ls "$HOME"/.cache/torch/hub/checkpoints/wav2vec2_*.pth > /dev/null 2>&1 && pass "alignment weights cached" || fail "alignment weights not cached (~/.cache/torch/hub/checkpoints/wav2vec2_*.pth): run install.sh with network"
  if [ -f "$REPO/engine/words.py" ] && have say; then
    say -o "$tmp/hello.aiff" "hello there this is a short test" 2> /dev/null
    ffmpeg -v error -y -i "$tmp/hello.aiff" -ar 16000 -ac 1 "$tmp/hello.wav" 2> /dev/null
    if out=$(CUTROOM_HOME="${CUTROOM_HOME:-$CONF_DIR}" with_timeout 240 python3 "$REPO/engine/words.py" "$tmp/hello.wav" --out "$tmp/hello.words.json" 2>&1) && [ -s "$tmp/hello.words.json" ]; then
      pass "word timing smoke: $(python3 -c "import json,sys; w=json.load(open(sys.argv[1])); print(len(w.get('words', w)), 'words')" "$tmp/hello.words.json" 2> /dev/null)"
    else fail "word timing smoke failed: $(echo "$out" | tail -1)"; fi
  fi
else fail "venv cannot import its packages: $(echo "$out" | tail -1) (install.sh repairs it)"; fi

step "Apple Vision helpers"
if [ -d "$REPO/engine/swift" ]; then
  if out=$(xcrun --find swiftc > /dev/null 2>&1 && python3 "$REPO/engine/swift/swiftbin.py" --all 2>&1); then pass "built and current: $(echo "$out" | tr '\n' ' ')"; else fail "a helper does not build: ${out:-swiftc missing}"; fi
else warn "engine/swift not present yet"; fi

step "Renderer"
if [ -f "$REPO/remotion/package.json" ]; then
  want=$(python3 -c "import json;print(json.load(open('$REPO/remotion/package-lock.json'))['packages']['node_modules/remotion']['version'])" 2> /dev/null)
  got=$(python3 -c "import json;print(json.load(open('$REPO/remotion/node_modules/remotion/package.json'))['version'])" 2> /dev/null)
  if [ -n "$got" ] && [ "$got" = "$want" ]; then pass "remotion $got"; else fail "remotion ${got:-not installed} (lockfile wants ${want:-?}; install.sh runs npm ci)"; fi
  if [ -x "$REPO/remotion/node_modules/.bin/tsc" ]; then
    if out=$(cd "$REPO/remotion" && node_modules/.bin/tsc --noEmit -p . 2>&1); then pass "TypeScript compiles"; else fail "TypeScript does not compile: $(echo "$out" | head -1)"; fi
  fi
  if [ "$RENDER" = 1 ] && [ -f "$REPO/remotion/index.ts" ]; then
    mkdir -p "$tmp/public"
    ffmpeg -v error -f lavfi -i testsrc2=size=1080x1920:rate=30:duration=2 -f lavfi -i sine=frequency=440:duration=2 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$tmp/public/src.mp4"
    printf '{"src":"src.mp4","durationInFrames":60,"events":[]}' > "$tmp/props.json"
    if (cd "$REPO/remotion" && npx remotion render index.ts Captions "$tmp/smoke.mp4" --props="$tmp/props.json" --frames=0-10 --public-dir="$tmp/public" --log=error > "$tmp/render.log" 2>&1); then pass "smoke render"; else fail "smoke render failed: $(grep -vE '^[[:space:]]+at ' "$tmp/render.log" | tail -1)"; fi
  fi
else warn "remotion/ not present yet"; fi

step "Config and skills"
if [ -f "$CONF" ]; then
  wsp=$(python3 -c "import json;print(json.load(open('$CONF')).get('workspace',''))" 2> /dev/null)
  if [ -n "$wsp" ] && [ -d "$wsp" ]; then pass "workspace $(tilde "$wsp")"; elif [ -n "$wsp" ]; then fail "workspace folder missing: $wsp"; else warn "no workspace yet: run /cutroom-setup"; fi
  if [ -n "$wsp" ] && [ -f "$wsp/Brand/dna.json" ]; then
    if [ -f "$REPO/engine/dna.py" ]; then
      if out=$(CUTROOM_HOME="${CUTROOM_HOME:-$CONF_DIR}" python3 "$REPO/engine/dna.py" check 2>&1); then pass "DNA: $out"; else while IFS= read -r l; do fail "DNA: $l"; done <<< "$out"; fi
    fi
  elif [ -n "$wsp" ]; then warn "no Brand/dna.json yet: run /cutroom-setup"; fi
else fail "$(tilde "$CONF") missing (install.sh writes it)"; fi
for d in "$REPO"/skills/*/; do
  [ -f "$d/SKILL.md" ] || continue
  s=$(basename "$d"); t="$CLAUDE_SKILLS/$s"
  if [ -L "$t" ] && [ "$(readlink "$t")" = "$REPO/skills/$s" ]; then pass "skill $s"
  elif [ -e "$t" ]; then fail "skill $s exists but is not linked to this repo (install.sh relinks it)"
  else fail "skill $s not linked (install.sh links it)"; fi
done
[ "$(stat -f %Lp "$CONF_DIR" 2> /dev/null)" = "700" ] && pass "~/.cutroom is private (700)" || warn "chmod 700 $(tilde "$CONF_DIR")"
for k in $OPTIONAL_KEYS; do
  if [ -n "$(read_key "$KEYS" "$k")" ]; then pass "$k set (optional service on)"; fi
done

printf '\n%s%s passed, %s failed, %s warnings%s\n' "$B" "$PASS" "$FAIL" "$WARN" "$N"
[ "$FAIL" -eq 0 ]
