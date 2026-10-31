# bin/lib.sh: shared by install.sh and doctor.sh. Source it, don't run it.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF_DIR="${CUTROOM_HOME:-$HOME/.cutroom}"
CONF="$CONF_DIR/config.json"
KEYS="$CONF_DIR/keys.env"
CLAUDE_SKILLS="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
VENV="${CUTROOM_VENV:-$CONF_DIR/venv}"
VENV_PYTHON="$VENV/bin/python"
MODEL_DIR="${CUTROOM_MODELS:-$CONF_DIR/models}"
WHISPER_MODEL="${WHISPER_MODEL:-$MODEL_DIR/ggml-large-v3-turbo.bin}"
WHISPER_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
WHISPER_MODEL_BYTES=1624555275
WHISPER_MODEL_SHA256=1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69   # the hash Hugging Face publishes for the file
# Away from a Mac the face and person helpers are the stand-ins in engine/vision/, which read these two small models from $MODEL_DIR.
# install.sh downloads them; the doctor checks their sizes.
YUNET_MODEL="$MODEL_DIR/face_detection_yunet_2023mar.onnx"
YUNET_MODEL_URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_MODEL_BYTES=232589
YUNET_MODEL_SHA256=8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
RVM_MODEL="$MODEL_DIR/rvm_mobilenetv3_fp32.onnx"
RVM_MODEL_URL="https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx"
RVM_MODEL_BYTES=14975696
RVM_MODEL_SHA256=88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828
# whisper.cpp is built from one pinned release on Linux: the tag, and the commit the tag points at (checked after the clone)
# ffmpeg on Linux: one pinned static build from BtbN/FFmpeg-Builds (gpl: libx264, libx265, libzimg for zscale), checked against these hashes before
# it is unpacked. BtbN keeps its month-end builds for about two years, so the pin is a month-end build; move it (URL and both hashes, after a
# run of the tests) when a newer one is wanted. Mac: Homebrew's ffmpeg.
FFMPEG_BUILD="n8.1.3 (BtbN, 2026-10-31)"
FFMPEG_URL_X86_64="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/ffmpeg-n8.1.3-27-g8dca1586f4-linux64-gpl-8.1.tar.xz"
FFMPEG_SHA256_X86_64=41e9c565594c390622cf968d82fa4135d9e9a11d06c1515644288cbc844e3380
FFMPEG_URL_ARM64="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/ffmpeg-n8.1.3-27-g8dca1586f4-linuxarm64-gpl-8.1.tar.xz"
FFMPEG_SHA256_ARM64=a08c0ee6e1c4e02139344b303002dcc7c37cf125d639f1fbd15dabee40fcb525
WHISPER_CPP_TAG=v1.9.5
WHISPER_CPP_COMMIT=d1be6fde11ac6e0407606b4e42fe72d34add8037
LOCAL_BIN="$CONF_DIR/bin"   # on Linux, where a fetched ffmpeg lands, and what joins PATH when /usr/local/bin cannot be written

# Optional paid services. Each is off until its key is in $KEYS. Nothing in the core needs one.
OPTIONAL_KEYS="DEEPGRAM_API_KEY ELEVENLABS_API_KEY EPIDEMIC_SOUND_MCP_API_KEY FAL_KEY"

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'; else G=; R=; Y=; B=; N=; fi
PASS=0; FAIL=0; WARN=0
pass() { printf '  %s✓%s %s\n' "$G" "$N" "$*"; PASS=$((PASS + 1)); }
fail() { printf '  %s✗%s %s\n' "$R" "$N" "$*"; FAIL=$((FAIL + 1)); }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; WARN=$((WARN + 1)); }
step() { printf '\n%s%s%s\n' "$B" "$*" "$N"; }
tilde() { printf '%s' "${1/#$HOME/~}"; }
have() { command -v "$1" > /dev/null 2>&1; }

# What this machine is. IS_MAC is 1 on macOS; anything else that gets past install.sh's first step is Debian or Ubuntu, IS_WSL 1 inside WSL.
OS="$(uname -s)"
ARCH="$(uname -m)"
if [ "$OS" = Darwin ]; then IS_MAC=1; else IS_MAC=0; fi
if [ -r /proc/version ] && grep -qi microsoft /proc/version 2> /dev/null; then IS_WSL=1; else IS_WSL=0; fi
# SUDO is empty for root and when sudo is not installed, so `$SUDO cmd` always runs cmd (as root when it can).
SUDO=""
if [ "$(id -u)" -ne 0 ] && have sudo; then SUDO=sudo; fi

# filesize <path>: size in bytes, 0 when the file is missing. filemode <path>: octal permissions (700), nothing when the path is missing.
# BSD stat (macOS) and GNU stat (Linux) take different flags, so every script goes through these two.
filesize() { if [ "$OS" = Darwin ]; then stat -f %z "$1" 2> /dev/null || echo 0; else stat -c %s "$1" 2> /dev/null || echo 0; fi; }
filemode() { if [ "$OS" = Darwin ]; then stat -f %Lp "$1" 2> /dev/null; else stat -c %a "$1" 2> /dev/null; fi; }
# sha256_of <path>: the file's SHA-256 (shasum on a Mac, sha256sum on Linux), nothing when the file is missing.
sha256_of() { if [ "$OS" = Darwin ]; then shasum -a 256 "$1" 2> /dev/null | cut -d" " -f1; else sha256sum "$1" 2> /dev/null | cut -d" " -f1; fi; }

# ffmpeg_major: the major version of the ffmpeg on PATH (7 for 7.0.2-static or n7.1); nothing when there is none or the build carries no release number.
ffmpeg_major() { ffmpeg -version 2> /dev/null | sed -n '1s/^ffmpeg version [nN]*\([0-9][0-9]*\).*$/\1/p' || true; }
# ffmpeg_has_filter <name>: the ffmpeg on PATH lists that filter (zscale, the HDR tone mapper's color-space step, needs a libzimg build).
ffmpeg_has_filter() { ffmpeg -hide_banner -filters 2> /dev/null | awk -v f="$1" '$2 == f { found = 1 } END { exit !found }'; }

# claude_cli: the Claude Code CLI on PATH, else the newest one the desktop app bundles. Empty when there is neither.
claude_cli() {
  command -v claude 2> /dev/null || ls -t "$HOME"/Library/Application\ Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude 2> /dev/null | head -1 || true
}

# read_key FILE NAME: the value of NAME= in FILE, trimmed, quotes stripped; empty when absent. Callers never echo it.
read_key() {
  [ -f "$1" ] || return 0
  local line v
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
    case "$line" in
      "$2="*)
        v="${line#*=}"; v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
        while [ "${v#\"}" != "$v" ]; do v="${v#\"}"; done; while [ "${v%\"}" != "$v" ]; do v="${v%\"}"; done
        while [ "${v#\'}" != "$v" ]; do v="${v#\'}"; done; while [ "${v%\'}" != "$v" ]; do v="${v%\'}"; done
        printf '%s' "$v"; return 0 ;;
    esac
  done < "$1"
}

# link_skills: every skills/<name>/ with a SKILL.md becomes ~/.claude/skills/<name> -> that folder, so a git pull updates it live.
# A real folder already in the way is moved aside first.
link_skills() {
  local backup="$CONF_DIR/skills-backup/$(date +%Y-%m-%d)" d s src t
  mkdir -p "$CLAUDE_SKILLS"
  for d in "$REPO"/skills/*/; do
    [ -f "$d/SKILL.md" ] || continue
    s=$(basename "$d"); src="$REPO/skills/$s"; t="$CLAUDE_SKILLS/$s"
    if [ -L "$t" ] && [ "$(readlink "$t")" = "$src" ]; then pass "$s"; continue; fi
    if [ -e "$t" ] && [ ! -L "$t" ]; then mkdir -p "$backup"; mv "$t" "$backup/$s"; echo "  kept your old $s in $(tilde "$backup")/"; fi
    ln -sfn "$src" "$t"
    pass "$s (linked)"
  done
}
