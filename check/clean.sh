#!/bin/bash
# The banned-token gate: nothing from the house (names, clients, paths, design) may ship in this repo.
# Usage: bash check/clean.sh [root]   exit 0 clean, 1 with one line per hit.
# The one allowed appearance of the org name is the repo's own clone URL (github.com/LYFEWORK/cutroom), which is stripped before matching.
ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT" || exit 2
python3 - <<'PY'
import os, re, sys
PATTERN = re.compile(r"Lyfework|Lifework|LYFEWORK|lyfework|Snouffer|Justyn|Rip ?Print|ripprint|Zen Medicine|zenmedicine|Fireside|fireside610|Klutch|klutch|Salon 80|Nice Boudie|niceboudie|Move More|movemorecollective|RYANS DRIVE|Nexus|Metricool|Axiom|Electric Lava|Geist|\blava\b|GoHighLevel|\bGHL\b|lit-card|lit cards|CLIENT MEDIA|\.lyfework|Treasure Coast|Dashia|Ollie")
ALLOW = re.compile(r"github\.com/LYFEWORK/cutroom(?:\.git)?")
SKIP_DIRS = {"node_modules", ".git", ".venv", "out", ".remotion", "bin", "__pycache__", ".pytest_cache", ".pages"}
SKIP_EXT = {".woff2", ".woff", ".ttf", ".otf", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".wav", ".pdf", ".pyc", ".lock"}
hits = []
for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for f in files:
        p = os.path.join(root, f)
        if f == "clean.sh" or os.path.splitext(f)[1].lower() in SKIP_EXT:
            continue
        try:
            text = open(p, "rb").read()
        except OSError:
            continue
        if b"\0" in text[:4096]:
            continue
        for n, line in enumerate(text.decode("utf-8", "replace").splitlines(), 1):
            if PATTERN.search(ALLOW.sub("", line)):
                hits.append(f"{p[2:]}:{n}:{line.strip()[:160]}")
if hits:
    print("\n".join(hits)); print(); print(f"clean.sh: {len(hits)} banned token hit(s)"); sys.exit(1)
print("clean.sh: clean")
PY
