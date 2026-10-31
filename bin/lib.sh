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
