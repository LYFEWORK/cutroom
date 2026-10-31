#!/bin/bash
# install.sh: one command that makes a Mac, or an Ubuntu or Debian machine (Windows through WSL2 included), run Cutroom. Safe to re-run; it only does what is missing.
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

# ---- Linux helpers (Ubuntu and Debian). Only functions here: a Mac never calls them. ----
LINK_WARNED=0

# apt_get <args>: apt-get as root, never stopping to ask a question, waiting out another apt that holds the lock.
apt_get() { $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=3 "$@"; }

# apt_has <package>: installed, under its own name or its t64 name (Ubuntu 24.04 renamed libcups2 to libcups2t64, and so on).
apt_has() {
  local p
  for p in "$1" "${1}t64"; do
    case "$(dpkg-query -W -f='${Status}' "$p" 2> /dev/null)" in *" ok installed") return 0 ;; esac
  done
  return 1
}

# nodesource_repo: NodeSource's apt repository for Node 22, set up the documented way (their signing key into a keyring, one sources line),
# never a script piped into a root shell. Returns 1 when the key or the list could not be written.
nodesource_repo() {
  local key=/etc/apt/keyrings/nodesource.gpg list=/etc/apt/sources.list.d/nodesource.list f
  # an entry for the same repository left by NodeSource's own setup script (another file, another keyring path) makes apt refuse both:
  # one entry for deb.nodesource.com, ours
  for f in /etc/apt/sources.list.d/*; do
    [ -f "$f" ] && [ "$f" != "$list" ] && grep -qs "deb.nodesource.com" "$f" && $SUDO rm -f "$f"
  done
  $SUDO mkdir -p /etc/apt/keyrings || return 1
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | $SUDO gpg --dearmor --yes -o "$key" > /dev/null 2>&1 || return 1
  echo "deb [signed-by=$key] https://deb.nodesource.com/node_22.x nodistro main" | $SUDO tee "$list" > /dev/null || return 1
  apt_get update -qq > /dev/null 2>&1 || true
}

# path_local: $LOCAL_BIN first on PATH, for this run.
path_local() {
  case ":$PATH:" in *":$LOCAL_BIN:"*) ;; *) export PATH="$LOCAL_BIN:$PATH" ;; esac
  hash -r
}

# link_bin [--local] <file> <name>: make <name> a command. A binary goes into /usr/local/bin as a root-owned COPY (install -o root -m 0755),
# never as a symlink to a file the user can rewrite. When that cannot be done, or with --local (a script that runs out of the user's own venv),
# a link goes in $LOCAL_BIN, which joins PATH for this run and, once ever, ~/.profile (a line that is already there is never added again).
link_bin() {
  local local_only=0 src name line
  if [ "$1" = "--local" ]; then local_only=1; shift; fi
  src="$1"; name="$2"
  if [ "$local_only" = 0 ] && $SUDO install -o root -g root -m 0755 "$src" "/usr/local/bin/$name" 2> /dev/null; then hash -r; return 0; fi
  mkdir -p "$LOCAL_BIN"
  if [ "$src" != "$LOCAL_BIN/$name" ]; then ln -sf "$src" "$LOCAL_BIN/$name"; fi
  path_local
  if [ "$LINK_WARNED" = 0 ]; then
    LINK_WARNED=1
    line="export PATH=\"$LOCAL_BIN:\$PATH\""
    local why="could not link into /usr/local/bin:"
    [ "$local_only" = 1 ] && why="$name runs from the Python environment, so"
    if grep -qxF "$line" "$HOME/.profile" 2> /dev/null; then
      warn "$why $(tilde "$LOCAL_BIN") is on PATH for this run, and ~/.profile already adds it for new terminals"
    elif printf '\n# added by Cutroom: the tools it fetched\n%s\n' "$line" 2> /dev/null >> "$HOME/.profile"; then
      warn "$why $(tilde "$LOCAL_BIN") is on PATH for this run and now in ~/.profile (a new terminal picks it up, or run: source ~/.profile)"
    else
      warn "$why add this line to your shell profile: $line"
    fi
  fi
}

# fetch_model <path> <url> <bytes> <what> <sha256>: one model file, checked by size and SHA-256 (the numbers in bin/lib.sh). The download is
# resumable: it goes to <path>.part and moves into place only when both match; a file already there with another hash stops the install.
fetch_model() {
  local path="$1" url="$2" bytes="$3" what="$4" sha="$5" part="$1.part"
  if [ "$(filesize "$path")" = "$bytes" ]; then
    if [ "$(sha256_of "$path")" = "$sha" ]; then pass "$(tilde "$path")"; return 0; fi
    fail "$(tilde "$path") is not the file this version pins (its hash differs): move it away and run again"; exit 1
  fi
  mkdir -p "$(dirname "$path")"
  if [ "$(filesize "$part")" -gt "$bytes" ]; then rm -f "$part"; fi
  if [ "$(filesize "$part")" != "$bytes" ]; then
    echo "  downloading $what (resumable: run again if it stops)..."
    curl -L --fail --retry 3 -C - -o "$part" "$url" || true
  fi
  [ "$(filesize "$part")" = "$bytes" ] || { fail "$what: download incomplete; run again to resume"; exit 1; }
  if [ "$(sha256_of "$part")" != "$sha" ]; then rm -f "$part"; fail "$what: the download's hash is not the one this version pins ($url); nothing was kept"; exit 1; fi
  mv "$part" "$path"
  pass "$(tilde "$path")"
}

# ffmpeg_ok: the ffmpeg on PATH is 7 or newer and has the zscale filter (the HDR tone mapper needs both). ffmpeg_version: its version word.
ffmpeg_ok() { local m; m=$(ffmpeg_major); [ "${m:-0}" -ge 7 ] && ffmpeg_has_filter zscale; }
ffmpeg_version() { ffmpeg -version 2> /dev/null | awk 'NR == 1 { print $3 }' || true; }

# fetch_ffmpeg: one pinned static ffmpeg build (BtbN's, with libx264, libx265 and libzimg; Ubuntu's own is too old) into $LOCAL_BIN, then onto PATH.
# The tarball is checked against the SHA-256 in bin/lib.sh before anything is unpacked. The download is resumable, and a tarball an earlier run
# fetched is reused instead of fetched again.
fetch_ffmpeg() {
  local url sha tarball part
  case "$ARCH" in x86_64) url="$FFMPEG_URL_X86_64"; sha="$FFMPEG_SHA256_X86_64" ;; *) url="$FFMPEG_URL_ARM64"; sha="$FFMPEG_SHA256_ARM64" ;; esac
  tarball="$CONF_DIR/src/$(basename "$url")"; part="$tarball.part"
  mkdir -p "$CONF_DIR/src" "$LOCAL_BIN"
  if [ ! -x "$LOCAL_BIN/ffprobe" ] || ! (PATH="$LOCAL_BIN:$PATH"; hash -r; ffmpeg_ok); then
    if [ "$(sha256_of "$tarball")" != "$sha" ]; then
      echo "  downloading ffmpeg $FFMPEG_BUILD (about 150 MB; resumable: run again if it stops)..."
      curl -L --fail --retry 3 -C - -o "$part" "$url" || true
      [ "$(sha256_of "$part")" = "$sha" ] || { rm -f "$part" 2> /dev/null; fail "the ffmpeg download is incomplete or is not the pinned build; run again"; exit 1; }
      mv "$part" "$tarball"
    fi
    tar -xJf "$tarball" -C "$LOCAL_BIN" --strip-components=2 --wildcards '*/bin/ffmpeg' '*/bin/ffprobe' || { fail "could not unpack $(tilde "$tarball")"; exit 1; }
  fi
  link_bin "$LOCAL_BIN/ffmpeg" ffmpeg
  link_bin "$LOCAL_BIN/ffprobe" ffprobe
  hash -r
  if ffmpeg_ok; then
    rm -f "$tarball"
    pass "ffmpeg $(ffmpeg_version) (pinned static build, has zscale)"
  else
    fail "the ffmpeg on PATH is still older than 7 or has no zscale filter ($(command -v ffmpeg || echo none)): put $(tilde "$LOCAL_BIN") first on PATH"; exit 1
  fi
}

# build_whisper: whisper.cpp built from source (apt has no package for it), at the release bin/lib.sh pins, and put on PATH as whisper-cli. A
# source folder at another commit is moved to the pinned tag and built again; a finished build of the pinned release is reused.
build_whisper() {
  local src="$CONF_DIR/src/whisper.cpp" log="$CONF_DIR/src/whisper-build.log" build=0
  mkdir -p "$CONF_DIR/src"
  if [ -d "$src/.git" ]; then
    if [ "$(git -C "$src" rev-parse HEAD 2> /dev/null)" != "$WHISPER_CPP_COMMIT" ]; then
      echo "  moving the whisper.cpp source to release $WHISPER_CPP_TAG..."
      if ! (git -C "$src" fetch --quiet --depth 1 origin tag "$WHISPER_CPP_TAG" && git -C "$src" checkout --quiet --detach "$WHISPER_CPP_TAG") > "$log" 2>&1; then
        fail "could not fetch whisper.cpp $WHISPER_CPP_TAG into $(tilde "$src"): $(tail -1 "$log"); move that folder away and run again"; exit 1
      fi
      build=1
    fi
  else
    echo "  downloading whisper.cpp $WHISPER_CPP_TAG..."
    git clone --quiet --depth 1 --branch "$WHISPER_CPP_TAG" https://github.com/ggml-org/whisper.cpp "$src" > "$log" 2>&1 || { fail "could not download whisper.cpp $WHISPER_CPP_TAG: $(tail -1 "$log")"; exit 1; }
    build=1
  fi
  if [ "$(git -C "$src" rev-parse HEAD 2> /dev/null)" != "$WHISPER_CPP_COMMIT" ]; then
    fail "whisper.cpp at $(tilde "$src") is not release $WHISPER_CPP_TAG (commit $WHISPER_CPP_COMMIT): move that folder away and run again"; exit 1
  fi
  [ -x "$src/build/bin/whisper-cli" ] || build=1
  if [ "$build" = 1 ]; then
    echo "  building whisper.cpp from source (several minutes the first time)..."
    if ! (cd "$src" && cmake -B build -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON \
      && cmake --build build -j "$(nproc)" --config Release) >> "$log" 2>&1; then
      fail "whisper.cpp did not build: $(tail -1 "$log") (full log: $(tilde "$log"))"; exit 1
    fi
  fi
  [ -x "$src/build/bin/whisper-cli" ] || { fail "whisper.cpp built without build/bin/whisper-cli (log: $(tilde "$log"))"; exit 1; }
  link_bin "$src/build/bin/whisper-cli" whisper-cli
  pass "whisper-cli $WHISPER_CPP_TAG (built from source)"
}

# node_ok: node 18 or newer, with the npm that comes with a real Node install (Debian's own nodejs package ships without npm).
node_ok() { have node && have npm && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 18 ]; }
# ---- end of the Linux helpers ----

step "1/8  This machine"
BREW=""
case "$OS" in
  Darwin)
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
    ;;
  Linux)
    distro=$(. /etc/os-release > /dev/null 2>&1 && printf '%s' "${PRETTY_NAME:-}" || true)
    [ -n "$distro" ] || distro="Linux"
    if [ "$IS_WSL" = 1 ]; then distro="$distro (WSL2)"; fi
    pass "$distro on $ARCH"
    have apt-get || { fail "Cutroom's Linux install knows Debian and Ubuntu (apt-get); on another distro install ffmpeg 7, whisper-cli, node 18+, python3.12 and tesseract yourself, then run again"; exit 1; }
    case "$ARCH" in x86_64 | aarch64) ;; *) fail "Cutroom's Linux install runs on x86_64 and aarch64 CPUs; this one is $ARCH"; exit 1 ;; esac
    ;;
  *)
    fail "Cutroom installs on macOS and on Ubuntu or Debian (Windows through WSL2); this machine is $OS"
    exit 1
    ;;
esac
if [ "$IS_MAC" = 1 ]; then free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}'); else free_gb=$(df -Pk "$HOME" | awk 'NR==2 {print int($4 / 1048576)}'); fi
if [ "${free_gb:-0}" -lt 12 ]; then warn "only ${free_gb} GB free on this disk; the toolchain needs about 12 GB"; else pass "${free_gb} GB free"; fi

step "2/8  Tools"
if [ "$IS_MAC" = 1 ]; then
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
else
  # System packages through apt. Only what is missing is installed, so a second run never asks for the password. The Remotion libraries and
  # fonts are what its headless Chrome needs. libasound2t64 is Ubuntu 24.04's name for libasound2 (older releases get libasound2 below).
  APT_BUILD="build-essential cmake git curl ca-certificates xz-utils gnupg"
  APT_PYTHON="python3 python3-venv python3-pip python3-pil python3-numpy python3-pytest"
  APT_MEDIA="tesseract-ocr espeak-ng"
  APT_CHROME="libnss3 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 fonts-liberation"
  missing=""
  for p in $APT_BUILD $APT_PYTHON $APT_MEDIA $APT_CHROME; do
    if [ "$p" = libasound2t64 ]; then apt_has libasound2t64 || apt_has libasound2 || missing="$missing $p"
    else apt_has "$p" || missing="$missing $p"; fi
  done
  if [ -n "$missing" ]; then
    if [ -z "$SUDO" ] && [ "$(id -u)" -ne 0 ]; then fail "apt-get needs root and sudo is not installed: run this script as root, or install sudo first"; exit 1; fi
    echo "  installing system packages (sudo may ask for your password):"
    echo "$missing" | fold -s -w 100 | sed 's/^ */    /; s/ *$//'
    apt_get update -qq > /dev/null || warn "apt-get update reported errors; going on with the package lists it has"
    # Each name as this release knows it: Ubuntu 24.04 renamed several libraries (libcups2 is libcups2t64 there), and the list's libasound2t64
    # is plain libasound2 on older releases. A name apt does not know either way goes through unchanged, so apt-get reports it.
    want=""
    for p in $missing; do
      if [ "$p" = libasound2t64 ]; then
        apt-cache show libasound2t64 > /dev/null 2>&1 || p=libasound2
      elif ! apt-cache show "$p" > /dev/null 2>&1 && apt-cache show "${p}t64" > /dev/null 2>&1; then
        p="${p}t64"
      fi
      want="$want $p"
    done
    apt_get install -y -qq $want > /dev/null || { fail "apt-get could not install the system packages: the lines above say what stopped it"; exit 1; }
  fi
  pass "build tools (gcc, make, cmake, git, curl)"
  pass "python3 with venv, pip, pillow, numpy and pytest"
  pass "tesseract (on-screen text) and espeak-ng"
  pass "libraries and fonts for Remotion's headless Chrome"
  if [ "$IS_WSL" = 1 ]; then
    if have wslview; then
      pass "wslu (the review page opens in your Windows browser)"
    elif apt-cache show wslu > /dev/null 2>&1 && apt_get install -y -qq wslu > /dev/null 2>&1; then
      hash -r
      pass "wslu (the review page opens in your Windows browser)"
    else
      warn "wslu is not available from apt: open the review page's address in your Windows browser yourself"
    fi
  fi
  # python first: with anything older than 3.12 nothing after this can work, so stop before the long steps.
  pyv=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    pass "python3 $pyv"
  else
    fail "python3 is $pyv and Cutroom needs 3.12 or newer: use Ubuntu 24.04 or newer (or Debian 13)"; exit 1
  fi
  if ffmpeg_ok; then pass "ffmpeg $(ffmpeg_version) (has zscale)"; else fetch_ffmpeg; fi
  # node before whisper.cpp: the sudo calls stay together, so the password asked at the start is still remembered when they run.
  if node_ok; then
    pass "node $(node -v)"
  else
    echo "  installing node 22 from NodeSource... (sudo may ask for your password)"
    if nodesource_repo && apt_get install -y -qq nodejs > /dev/null; then
      hash -r
      if node_ok; then pass "node $(node -v) (installed from NodeSource)"; else fail "node is still missing or older than 18 after the NodeSource install"; exit 1; fi
    else
      fail "could not install node from NodeSource (it needs the network and sudo)"; exit 1
    fi
  fi
  if have whisper-cli; then pass "whisper-cli"; else build_whisper; fi
  warn "software encodes (libx264): fine, slower than a Mac"
fi

step "3/8  Python"
if [ "$IS_MAC" = 1 ]; then
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
else
  # pillow, numpy and pytest came from apt in step 2, so they are only checked. Everything else lives in the word-timing venv, which also holds
  # the Linux vision stand-ins' packages (OpenCV, onnxruntime, pytesseract) and yt-dlp.
  python3 -c "import PIL, numpy, pytest" 2> /dev/null && pass "pillow, numpy, pytest" || { fail "python3 cannot import pillow, numpy and pytest (apt installs them for /usr/bin/python3): is another python3 first on PATH?"; exit 1; }
  if [ ! -x "$VENV_PYTHON" ]; then
    mkdir -p "$(dirname "$VENV")"
    python3 -m venv "$VENV" && "$VENV/bin/pip" install --quiet --upgrade pip && pass "word-timing venv created ($(tilde "$VENV"), $("$VENV_PYTHON" --version))" || { fail "could not create the venv at $VENV"; exit 1; }
  fi
  VENV_MODS="whisperx, torch, scipy, numpy, PIL, cv2, onnxruntime, pytesseract"
  if ! "$VENV_PYTHON" -c "import $VENV_MODS" 2> /dev/null; then
    # torch first, so the CPU build is the one the other packages find already installed; torchaudio comes from the same index so the pair always matches.
    if have nvidia-smi; then
      torch_pkgs=(torch torchaudio)
      echo "  installing torch (the CUDA build, a few GB), then whisperx, scipy, numpy, pillow, opencv, onnxruntime and pytesseract into the venv..."
    else
      torch_pkgs=(torch torchaudio --index-url https://download.pytorch.org/whl/cpu)
      echo "  installing torch (the CPU build), then whisperx, scipy, numpy, pillow, opencv, onnxruntime and pytesseract into the venv (a few minutes the first time)..."
    fi
    "$VENV_PYTHON" -m pip install --quiet "${torch_pkgs[@]}" || { fail "pip could not install torch"; exit 1; }
    "$VENV_PYTHON" -m pip install --quiet whisperx scipy numpy pillow opencv-python-headless onnxruntime pytesseract || { fail "pip could not install the word-timing packages"; exit 1; }
  fi
  "$VENV_PYTHON" -c "import $VENV_MODS" 2> /dev/null && pass "venv: whisperx, torch $("$VENV_PYTHON" -c 'import torch; print(torch.__version__)'), scipy, numpy, pillow, opencv, onnxruntime, pytesseract" || { fail "the venv still cannot import its packages"; exit 1; }
  # yt-dlp (reference study) is optional: a failure here only warns, as the doctor does.
  if [ ! -x "$VENV/bin/yt-dlp" ]; then "$VENV_PYTHON" -m pip install --quiet yt-dlp || warn "could not install yt-dlp: reference links cannot be downloaded until it is"; fi
  if [ -x "$VENV/bin/yt-dlp" ]; then
    if ! have yt-dlp; then link_bin --local "$VENV/bin/yt-dlp" yt-dlp; fi
    pass "yt-dlp"
  fi
fi
if ! ls "$HOME"/.cache/torch/hub/checkpoints/wav2vec2_*.pth > /dev/null 2>&1; then
  echo "  fetching the alignment weights (about 360 MB)..."
  "$VENV_PYTHON" -c "import whisperx; whisperx.load_align_model(language_code='en', device='cpu')" > /dev/null 2>&1 || warn "could not fetch the alignment weights now (needs network); the doctor checks for them"
fi

step "4/8  Whisper model (large-v3-turbo, 1.6 GB)"
if [ "$(filesize "$WHISPER_MODEL")" = "$WHISPER_MODEL_BYTES" ]; then
  pass "$(tilde "$WHISPER_MODEL")"
elif [ "$SKIP_MODEL" = 1 ]; then
  warn "skipped (--skip-model)"
else
  mkdir -p "$(dirname "$WHISPER_MODEL")"
  part="$WHISPER_MODEL.part"
  if [ "$(filesize "$part")" != "$WHISPER_MODEL_BYTES" ]; then
    echo "  downloading 1.6 GB (resumable: run again if it stops)..."
    curl -L --fail --retry 3 -C - -o "$part" "$WHISPER_MODEL_URL"
    if [ "$(filesize "$part")" = "$WHISPER_MODEL_BYTES" ] && [ "$(sha256_of "$part")" != "$WHISPER_MODEL_SHA256" ]; then
      rm -f "$part"; fail "the Whisper model download is not the file Hugging Face publishes (hash mismatch); nothing was kept, run again"; exit 1
    fi
  fi
  [ "$(filesize "$part")" = "$WHISPER_MODEL_BYTES" ] || { fail "download incomplete; run again to resume"; exit 1; }
  mv "$part" "$WHISPER_MODEL"
  pass "$(tilde "$WHISPER_MODEL")"
fi

if [ "$IS_MAC" = 0 ]; then
  step "4b/8  Vision models (face detector and person matte, 15 MB)"
  fetch_model "$YUNET_MODEL" "$YUNET_MODEL_URL" "$YUNET_MODEL_BYTES" "the face detector (0.2 MB)" "$YUNET_MODEL_SHA256"
  fetch_model "$RVM_MODEL" "$RVM_MODEL_URL" "$RVM_MODEL_BYTES" "the person matte model (15 MB)" "$RVM_MODEL_SHA256"
fi

step "5/8  Vision helpers"
if [ "$IS_MAC" = 1 ]; then
  if have swiftc && [ -d "$REPO/engine/swift" ]; then
    if out=$(python3 "$REPO/engine/swift/swiftbin.py" --all 2>&1); then pass "built: $(echo "$out" | tr '\n' ' ')"; else fail "a helper does not build: $(echo "$out" | tail -1)"; fi
  else
    warn "swiftc or engine/swift missing: face tracking and the person matte will not run until it is"
  fi
elif [ -f "$REPO/engine/swift/swiftbin.py" ]; then
  # Linux: the same six helper names answer from engine/vision/. The stand-ins find the venv and the models through these three, and step 7 has not
  # written config.json yet, so they are passed here instead.
  if out=$(CUTROOM_HOME="$CONF_DIR" CUTROOM_MODELS="$MODEL_DIR" CUTROOM_VENV_PYTHON="$VENV_PYTHON" python3 "$REPO/engine/swift/swiftbin.py" --all 2>&1); then pass "ready: $(echo "$out" | tr '\n' ' ')"; else fail "a vision helper cannot run: $(echo "$out" | tail -1)"; fi
else
  warn "engine/swift missing: face tracking and the person matte will not run until it is"
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
