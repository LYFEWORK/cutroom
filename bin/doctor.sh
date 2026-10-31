#!/bin/bash
# doctor.sh: proves this machine (a Mac, or Ubuntu or Debian with WSL2) can run Cutroom. Changes nothing except rebuilding a stale compiled helper.
#   bash ~/cutroom/bin/doctor.sh [--render]
#   --render   add a 10-frame Remotion smoke render
# Exits 1 when anything the pipeline needs is missing or broken. Warnings are for optional parts.
set -uo pipefail
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
RENDER=0
for a in "$@"; do case "$a" in --render) RENDER=1 ;; -h | --help) sed -n '2,5p' "$0"; exit 0 ;; esac; done
[ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)"
with_timeout() { local s="$1"; shift; perl -e 'alarm shift; exec @ARGV' "$s" "$@"; }
# hint <what a Mac does> <what Linux does>: the fix a failing line names
hint() { if [ "$IS_MAC" = 1 ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

step "Tools"
if have ffmpeg; then
  ffv=$(ffmpeg -version | head -1 | awk '{print $3}')
  ffm=$(ffmpeg_major)
  if [ "${ffm:-0}" -lt 7 ]; then fail "ffmpeg $ffv is older than 7 ($(hint "brew upgrade ffmpeg" "install.sh fetches a static build"))"
  elif [ "$IS_MAC" = 0 ] && ! ffmpeg_has_filter zscale; then fail "ffmpeg $ffv has no zscale filter (install.sh fetches a static build that has it)"
  else pass "ffmpeg $ffv"; fi
else fail "ffmpeg missing ($(hint "brew install ffmpeg" "install.sh fetches a static build"))"; fi
have ffprobe && pass "ffprobe" || fail "ffprobe missing ($(hint "brew install ffmpeg" "install.sh fetches it with ffmpeg"))"
if [ "$IS_MAC" = 1 ]; then
  ffmpeg -hide_banner -encoders 2> /dev/null | grep -q h264_videotoolbox && pass "VideoToolbox encoder" || warn "no h264_videotoolbox: software encodes only"
else
  warn "software encodes (libx264): fine, slower than a Mac"
fi
have whisper-cli && pass "whisper-cli" || fail "whisper-cli missing ($(hint "brew install whisper-cpp" "install.sh builds it"))"
if [ "$(filesize "$WHISPER_MODEL")" = "$WHISPER_MODEL_BYTES" ]; then pass "Whisper model $(tilde "$WHISPER_MODEL")"; else fail "Whisper model missing or partial at $(tilde "$WHISPER_MODEL") (install.sh downloads it)"; fi
if have node && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 18 ]; then pass "node $(node -v)"; else fail "node 18+ missing ($(hint "brew install node" "install.sh installs it"))"; fi
if python3 -c "import PIL, numpy" 2> /dev/null; then pass "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))') + pillow + numpy"; else fail "python3 pillow/numpy missing (install.sh installs them)"; fi
if have yt-dlp; then pass "yt-dlp (reference study)"
elif [ -x "$VENV/bin/yt-dlp" ]; then pass "yt-dlp (reference study; in the Python environment, $(tilde "$VENV/bin"), which install.sh adds to PATH for new terminals)"
else warn "yt-dlp missing: reference links cannot be downloaded ($(hint "brew install yt-dlp" "install.sh installs it"))"; fi
if [ "$IS_MAC" = 1 ]; then
  [ -d "/Applications/Google Chrome.app" ] && pass "Google Chrome" || warn "Google Chrome not installed (optional)"
  have swiftc && pass "swiftc" || fail "swiftc missing (xcode-select --install)"
else
  have tesseract && pass "tesseract (on-screen text reading)" || warn "tesseract missing: on-screen text reading is off until: sudo apt-get install -y tesseract-ocr"
  have espeak-ng && pass "espeak-ng (voice for the word timing smoke test)" || warn "espeak-ng missing: the word timing smoke test is skipped until: sudo apt-get install -y espeak-ng"
fi

step "Word timing (WhisperX venv)"
if [ "$IS_MAC" = 1 ]; then
  venv_mods="whisperx, torch, scipy, numpy, PIL"; venv_say="scipy + numpy + pillow"
else
  venv_mods="whisperx, torch, scipy, numpy, PIL, cv2, onnxruntime, pytesseract"; venv_say="scipy + numpy + pillow + opencv + onnxruntime + pytesseract"
fi
if [ ! -x "$VENV_PYTHON" ]; then
  fail "venv python missing: $(tilde "$VENV_PYTHON") (install.sh creates it)"
elif out=$("$VENV_PYTHON" -c "import $venv_mods; print(torch.__version__)" 2>&1); then
  torch_v=$out; [ "$IS_MAC" = 1 ] || torch_v=$(printf '%s\n' "$out" | tail -1)
  pass "venv imports whisperx + torch $torch_v + $venv_say"
  ls "$HOME"/.cache/torch/hub/checkpoints/wav2vec2_*.pth > /dev/null 2>&1 && pass "alignment weights cached" || fail "alignment weights not cached (~/.cache/torch/hub/checkpoints/wav2vec2_*.pth): run install.sh with network"
  if [ -f "$REPO/engine/words.py" ]; then
    smoke_src=""
    if [ "$IS_MAC" = 1 ]; then
      if have say; then say -o "$tmp/hello.aiff" "hello there this is a short test" 2> /dev/null; smoke_src="$tmp/hello.aiff"; fi
    elif have espeak-ng; then
      espeak-ng -w "$tmp/hello.espeak.wav" "hello there this is a short test" 2> /dev/null; smoke_src="$tmp/hello.espeak.wav"
    else
      warn "word timing smoke test skipped: espeak-ng is missing"
    fi
    if [ -n "$smoke_src" ]; then
      ffmpeg -v error -y -i "$smoke_src" -ar 16000 -ac 1 "$tmp/hello.wav" 2> /dev/null
      if out=$(CUTROOM_HOME="${CUTROOM_HOME:-$CONF_DIR}" with_timeout 240 python3 "$REPO/engine/words.py" "$tmp/hello.wav" --out "$tmp/hello.words.json" 2>&1) && [ -s "$tmp/hello.words.json" ]; then
        pass "word timing smoke: $(python3 -c "import json,sys; w=json.load(open(sys.argv[1])); print(len(w.get('words', w)), 'words')" "$tmp/hello.words.json" 2> /dev/null)"
      else fail "word timing smoke failed: $(echo "$out" | tail -1)"; fi
    fi
  fi
else fail "venv cannot import its packages: $(echo "$out" | tail -1) (install.sh repairs it)"; fi

step "Vision helpers"
if [ -d "$REPO/engine/swift" ]; then
  if [ "$IS_MAC" = 1 ]; then
    if out=$(xcrun --find swiftc > /dev/null 2>&1 && python3 "$REPO/engine/swift/swiftbin.py" --all 2>&1); then pass "built and current: $(echo "$out" | tr '\n' ' ')"; else fail "a helper does not build: ${out:-swiftc missing}"; fi
  elif out=$(CUTROOM_HOME="$CONF_DIR" CUTROOM_MODELS="$MODEL_DIR" CUTROOM_VENV_PYTHON="$VENV_PYTHON" python3 "$REPO/engine/swift/swiftbin.py" --all 2>&1); then
    pass "stand-ins ready: $(echo "$out" | tr '\n' ' ')"
  else fail "a vision helper cannot run: $(echo "$out" | tail -1)"; fi
else warn "engine/swift not present yet"; fi
if [ "$IS_MAC" = 0 ]; then
  if [ "$(filesize "$YUNET_MODEL")" = "$YUNET_MODEL_BYTES" ] && [ "$(filesize "$RVM_MODEL")" = "$RVM_MODEL_BYTES" ]; then pass "vision models in $(tilde "$MODEL_DIR") (face detector, person matte)"
  else fail "a vision model is missing or partial in $(tilde "$MODEL_DIR") (install.sh downloads them)"; fi
fi

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
[ "$(filemode "$CONF_DIR")" = "700" ] && pass "~/.cutroom is private (700)" || warn "chmod 700 $(tilde "$CONF_DIR")"
for k in $OPTIONAL_KEYS; do
  if [ -n "$(read_key "$KEYS" "$k")" ]; then pass "$k set (optional service on)"; fi
done

printf '\n%s%s passed, %s failed, %s warnings%s\n' "$B" "$PASS" "$FAIL" "$WARN" "$N"
[ "$FAIL" -eq 0 ]
