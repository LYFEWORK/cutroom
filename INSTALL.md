# Install Cutroom

**For Claude Code, on the user's Mac.** Read this whole file first, then do the steps in order. Every step is safe to run again. When you are done, report back with the last section.

**What the user pastes to start:**

> Clone https://github.com/LYFEWORK/cutroom to ~/cutroom (or `git pull` it if it is already there), then read ~/cutroom/INSTALL.md and follow it exactly.

That paste is the go-ahead for Steps 0 to 4: about 6 GB of downloads, Homebrew installs, a Python environment, a Remotion install, and linking the skills into `~/.claude/skills/`. Nothing here touches the user's footage, posts anything, or sends anything anywhere.

## What you are installing

| Piece | What it is |
|---|---|
| 8 skills | `cutroom` (the router), `cutroom-setup` (the setup wizard), `cutroom-intake` (footage in), `cutroom-review` (keep, cut, trim), `cutroom-edit` (make a video), `cutroom-captions` (caption a finished cut), `cutroom-study` (learn from reference videos), `cutroom-storage` (cleanup and archive). Symlinked from this repo into `~/.claude/skills/`, so `git pull` updates them live. |
| The engine | Python scripts in `engine/` that do the mechanical work: transcription and word timing, the cutter, face tracking, captions planning, QC gates, sound, the footage organizer, the review page, cleanup. |
| The renderer | One Remotion project in `remotion/` that draws captions, cards and the user's own graphics over the cut. |
| Tools | ffmpeg, whisper.cpp with the large-v3-turbo model (1.6 GB), Node, Python with Pillow and numpy, a Python 3.12 environment with WhisperX for word timing (about 1.3 GB), yt-dlp for reference links, the Apple Vision helpers (built here with swiftc). |
| Config | `~/.cutroom/config.json` (paths) and `~/.cutroom/keys.env` (optional services, empty by default). |

Nothing needs a paid API. The core runs on the Mac.

## Ground rules

1. **Password prompts and installer windows belong to the user.** Homebrew's installer and Apple's command-line-tools window ask for them. Show the exact command or window, wait until the user says it is finished, then continue.
2. **Never print a key.** If the user later adds a key to `~/.cutroom/keys.env`, refer to it by name only.
3. **Move, never delete.** The installer keeps anything it replaces (`~/.cutroom/skills-backup/`).
4. **Opus 5.5.** This system is built to run with the main session on Opus 5.5. If `claude --version` works, check the model the session is on; if it is not Opus 5.5, tell the user how to switch (the model picker in the app, `claude --model claude-opus-5-5`, or `"model": "claude-opus-5-5"` in `~/.claude/settings.json`) before running the setup wizard.

## Step 0: Check the Mac (look, don't assume)

| Need | Check | If it fails |
|---|---|---|
| macOS on Apple Silicon (Intel runs, slower) | `sw_vers -productVersion; uname -m` | Intel: continue, warn the user that encodes and word timing are slower. Not a Mac: stop. The face tracking and the person matte need Apple Vision. |
| About 12 GB free | `df -h ~` | Ask the user what to clear. |
| Xcode command-line tools | `xcode-select -p` | `xcode-select --install` opens Apple's installer. The user clicks Install. Wait for it. |
| Homebrew | `command -v brew \|\| ls /opt/homebrew/bin/brew /usr/local/bin/brew` | The user runs `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` (it asks for the password). |
| Reaching GitHub | the clone worked | The repo is public, so no account or sign-in is needed. A clone that fails is the network, or git itself (`xcode-select --install` brings it); fix that and clone again. |
| Google Chrome (optional) | `ls -d "/Applications/Google Chrome.app"` | Not required. The review page opens in the default browser. |

## Step 1: Download

```bash
git clone https://github.com/LYFEWORK/cutroom.git ~/cutroom || git -C ~/cutroom pull
```

## Step 2: Install (one command)

```bash
bash ~/cutroom/install.sh
```

The first run takes 10 to 20 minutes (the 1.6 GB model and the word-timing packages). Run it with a long timeout or in the background and read its output when it ends. If it gets cut off, run it again: it resumes and only redoes what is missing.

It prints one line per item: **✓** done, **!** optional or skipped, **✗** must be fixed.

| # | Step | Notes |
|---|---|---|
| 1 | This Mac | Stops if the command-line tools or Homebrew are missing (Step 0), then run again. |
| 2 | Tools | installs any missing: ffmpeg, whisper-cpp, node, python, yt-dlp |
| 3 | Python | Pillow, numpy, pytest, then the word-timing venv at `~/.cutroom/venv` and its alignment weights |
| 4 | Whisper model | `~/.cutroom/models/ggml-large-v3-turbo.bin`, resumable, size-checked |
| 5 | Apple Vision helpers | compiled with swiftc |
| 6 | Remotion | `npm ci` in `remotion/`, plus its headless browser |
| 7 | Config and skills | `~/.cutroom/config.json`, an empty `keys.env`, and every skill linked into `~/.claude/skills/` |
| 8 | Doctor | proves it all works |

**Step 2 is finished when the doctor's last line shows `0 failed`.** Warnings are fine. For any ✗, see "If something fails" and run `install.sh` again.

## Step 3: Verify

```bash
bash ~/cutroom/bin/doctor.sh
```

The doctor is safe any time. It exits 1 when anything the pipeline needs is missing. "no workspace yet" and "no Brand/dna.json yet" are expected before the wizard has run.

## Step 4: Start a new session and run the wizard

Skills load when a session starts. Tell the user to start a new Claude Code session (on Opus 5.5) in any folder and type:

```
/cutroom-setup
```

The wizard asks where the footage should live, who they are, their colors and fonts, their reference videos, and builds their caption styles, formats and graphics with them. It takes 20 to 40 minutes with the references. Everything it writes lands in their workspace under `Brand/`, never in this repo.

## If something fails

| The doctor says | Do |
|---|---|
| Whisper model missing or partial | `bash ~/cutroom/install.sh` (resumes the download) |
| venv python missing, or cannot import | `bash ~/cutroom/install.sh` (rebuilds `~/.cutroom/venv`) |
| alignment weights not cached | run `install.sh` once with network |
| a helper does not build | `xcode-select --install`, then run the doctor again |
| remotion not installed | `cd ~/cutroom/remotion && npm ci` |
| skill not linked | `bash ~/cutroom/install.sh` |
| brew install failed | run the printed `brew install <formula>` by hand and read its error |

## Updating later

```bash
git -C ~/cutroom pull && bash ~/cutroom/install.sh
```

The workspace and `Brand/` are the user's and are never touched by an update.

## Report back

Tell the user, in plain words: the doctor's counts (passed, failed, warnings), anything you could not fix and why, where the config lives, and that the next step is a new session and `/cutroom-setup`.
