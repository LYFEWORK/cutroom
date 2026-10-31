#!/bin/bash
# install.sh: one command that makes a Mac run Cutroom. Safe to re-run; it only does what is missing.
#   bash ~/cutroom/install.sh [--skip-model] [--skip-npm] [--workspace <folder>]
#   --skip-model        skip the 1.6 GB Whisper model download (the doctor will flag it)
#   --skip-npm          skip the Remotion install
#   --workspace DIR     write the workspace path into ~/.cutroom/config.json now (the setup wizard asks otherwise)
set -euo pipefail
source "$(cd "$(dirname "$0")" && pwd)/bin/lib.sh"

SKIP_MODEL=0; SKIP_NPM=0; WORKSPACE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-model) SKIP_MODEL=1; shift ;;
    --skip-npm) SKIP_NPM=1; shift ;;
    --workspace) WORKSPACE="${2:?--workspace needs a folder}"; shift 2 ;;
    -h | --help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1 (see --help)"; exit 2 ;;
  esac
done

step "1/8  This Mac"
[ "$(uname -s)" = Darwin ] || { fail "macOS only: the face tracking and the person matte run on Apple Vision, the fast encodes on VideoToolbox"; exit 1; }
pass "macOS $(sw_vers -productVersion) on $(uname -m)"
if ! xcode-select -p > /dev/null 2>&1; then
  xcode-select --install > /dev/null 2>&1 || true
  fail "Xcode command-line tools missing. An installer window just opened: finish it, then run this script again."
  exit 1
fi
pass "Xcode command-line tools"
BREW=$(command -v brew || true)
[ -z "$BREW" ] && [ -x /opt/homebrew/bin/brew ] && BREW=/opt/homebrew/bin/brew
[ -z "$BREW" ] && [ -x /usr/local/bin/brew ] && BREW=/usr/local/bin/brew
if [ -z "$BREW" ]; then
  fail "Homebrew missing. Install it (it asks for your Mac password), then run this script again:"
  echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi
eval "$("$BREW" shellenv)"
pass "Homebrew"
free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')
if [ "${free_gb:-0}" -lt 12 ]; then warn "only ${free_gb} GB free on this disk; the toolchain needs about 12 GB"; else pass "${free_gb} GB free"; fi

step "2/8  Tools"
need() { # need <command> <formula> [check]
  if have "$1" && { [ -z "${3:-}" ] || eval "$3"; }; then pass "$1"; return; fi
  echo "  installing $2..."
  if "$BREW" install --quiet "$2" > /dev/null; then pass "$1 (installed $2)"; else fail "brew install $2 failed"; exit 1; fi
}
need ffmpeg ffmpeg
need whisper-cli whisper-cpp
need node node '[ "$(node -p "process.versions.node.split(\".\")[0]")" -ge 18 ]'
need python3 python '[ "$(command -v python3)" != /usr/bin/python3 ]'
need yt-dlp yt-dlp
hash -r
ffmpeg -hide_banner -encoders 2> /dev/null | grep -q h264_videotoolbox && pass "ffmpeg has the VideoToolbox encoder" || warn "ffmpeg lacks h264_videotoolbox: encodes fall back to software (slower)"
[ -d "/Applications/Google Chrome.app" ] && pass "Google Chrome" || warn "Google Chrome is not installed: the review page and the index open in your default browser; the manual prints through Chrome"

step "3/8  Python"
missing=""
for m in PIL:pillow numpy:numpy pytest:pytest; do python3 -c "import ${m%%:*}" 2> /dev/null || missing="$missing ${m#*:}"; done
if [ -n "$missing" ]; then
  echo "  installing${missing}..."
  python3 -m pip install --quiet --break-system-packages $missing
fi
pass "pillow, numpy, pytest"
# WhisperX (word timing) needs Python 3.12 or 3.13 and torch, so it lives in its own venv under ~/.cutroom.
if [ ! -x "$VENV_PYTHON" ]; then
  PY=$(command -v python3.12 || command -v python3.13 || true)
  [ -z "$PY" ] && [ -x "$("$BREW" --prefix python@3.12 2> /dev/null)/bin/python3.12" ] && PY="$("$BREW" --prefix python@3.12)/bin/python3.12"
  if [ -z "$PY" ]; then echo "  installing python@3.12..."; "$BREW" install --quiet python@3.12 > /dev/null; PY="$("$BREW" --prefix python@3.12)/bin/python3.12"; fi
  mkdir -p "$(dirname "$VENV")"
  "$PY" -m venv "$VENV" && "$VENV/bin/pip" install --quiet --upgrade pip && pass "word-timing venv created ($(tilde "$VENV"), $("$VENV_PYTHON" --version))" || { fail "could not create the venv at $VENV"; exit 1; }
fi
if ! "$VENV_PYTHON" -c "import whisperx, torch, scipy, numpy, PIL" 2> /dev/null; then
  echo "  installing whisperx, torch, scipy, numpy, pillow into the venv (a few minutes the first time)..."
  "$VENV_PYTHON" -m pip install --quiet whisperx torch scipy numpy pillow || { fail "pip could not install the word-timing packages"; exit 1; }
fi
"$VENV_PYTHON" -c "import whisperx, torch, scipy, numpy, PIL" 2> /dev/null && pass "venv: whisperx, torch $("$VENV_PYTHON" -c 'import torch; print(torch.__version__)'), scipy, numpy, pillow" || { fail "the venv still cannot import its packages"; exit 1; }
if ! ls "$HOME"/.cache/torch/hub/checkpoints/wav2vec2_*.pth > /dev/null 2>&1; then
  echo "  fetching the alignment weights (about 360 MB)..."
  "$VENV_PYTHON" -c "import whisperx; whisperx.load_align_model(language_code='en', device='cpu')" > /dev/null 2>&1 || warn "could not fetch the alignment weights now (needs network); the doctor checks for them"
fi

step "4/8  Whisper model (large-v3-turbo, 1.6 GB)"
size() { stat -f %z "$1" 2> /dev/null || echo 0; }
if [ "$(size "$WHISPER_MODEL")" = "$WHISPER_MODEL_BYTES" ]; then
  pass "$(tilde "$WHISPER_MODEL")"
elif [ "$SKIP_MODEL" = 1 ]; then
  warn "skipped (--skip-model)"
else
  mkdir -p "$(dirname "$WHISPER_MODEL")"
  part="$WHISPER_MODEL.part"
  if [ "$(size "$part")" != "$WHISPER_MODEL_BYTES" ]; then
    echo "  downloading 1.6 GB (resumable: run again if it stops)..."
    curl -L --fail --retry 3 -C - -o "$part" "$WHISPER_MODEL_URL"
  fi
  [ "$(size "$part")" = "$WHISPER_MODEL_BYTES" ] || { fail "download incomplete; run again to resume"; exit 1; }
  mv "$part" "$WHISPER_MODEL"
  pass "$(tilde "$WHISPER_MODEL")"
fi

step "5/8  Apple Vision helpers"
if have swiftc && [ -d "$REPO/engine/swift" ]; then
  if out=$(python3 "$REPO/engine/swift/swiftbin.py" --all 2>&1); then pass "built: $(echo "$out" | tr '\n' ' ')"; else fail "a helper does not build: $(echo "$out" | tail -1)"; fi
else
  warn "swiftc or engine/swift missing: face tracking and the person matte will not run until it is"
fi

step "6/8  Remotion"
if [ -f "$REPO/remotion/package.json" ]; then
  r="$REPO/remotion"
  if [ -e "$r/node_modules/remotion" ] && [ -f "$r/node_modules/.package-lock.json" ] && [ ! "$r/package-lock.json" -nt "$r/node_modules/.package-lock.json" ]; then
    pass "up to date"
  elif [ "$SKIP_NPM" = 1 ]; then
    warn "skipped (--skip-npm)"
  else
    echo "  npm ci in remotion/ (a few minutes the first time)..."
    if (cd "$r" && npm ci --no-audit --no-fund --no-update-notifier --loglevel=error > /dev/null); then pass "installed"; else fail "npm ci failed in remotion/"; fi
    (cd "$r" && npx remotion browser ensure > /dev/null 2>&1) && pass "headless browser for renders" || warn "could not fetch Remotion's headless browser now; the first render will try again"
  fi
else
  warn "remotion/ not present yet"
fi

step "7/8  Config and skills"
mkdir -p "$CONF_DIR"; chmod 700 "$CONF_DIR"
if [ ! -f "$CONF" ]; then
  printf '{\n  "workspace": "%s",\n  "whisper_model": "%s",\n  "venv_python": "%s"\n}\n' "$WORKSPACE" "$WHISPER_MODEL" "$VENV_PYTHON" > "$CONF"
  pass "wrote $(tilde "$CONF")"
else
  if [ -n "$WORKSPACE" ]; then
    python3 - "$CONF" "$WORKSPACE" <<'PY'
import json, sys
p, w = sys.argv[1], sys.argv[2]
c = json.load(open(p)); c["workspace"] = w; json.dump(c, open(p, "w"), indent=2)
PY
  fi
  pass "$(tilde "$CONF") kept"
fi
[ -f "$KEYS" ] || { umask 077; printf '# Optional services. Leave empty to run fully local.\n# DEEPGRAM_API_KEY=\n# ELEVENLABS_API_KEY=\n# EPIDEMIC_SOUND_MCP_API_KEY=\n# FAL_KEY=\n' > "$KEYS"; }
chmod 600 "$KEYS"
pass "$(tilde "$KEYS") (optional services, all off)"
link_skills

step "8/8  Doctor"
printf '\n%sInstall: %s ok, %s warnings, %s failed. Running the doctor...%s\n' "$B" "$PASS" "$WARN" "$FAIL" "$N"
exec bash "$REPO/bin/doctor.sh"
