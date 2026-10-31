#!/bin/bash
# Build docs/GUIDE.pdf from docs/guide/guide.html through headless Chrome, then prove it prints plain:
# no soft masks (blur or see-through colour becomes solid blocks in some PDF viewers), every page rendered and listed.
#   bash docs/guide/build.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../GUIDE.pdf"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || { echo "STOP: Google Chrome is needed to print the guide"; exit 2; }
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="$OUT" "file://$HERE/guide.html" > /dev/null 2>&1
masks=$(grep -a -c /SMask "$OUT" || true)
if [ "${masks:-0}" != "0" ]; then echo "STOP: the PDF carries $masks soft mask(s); remove blur and transparency from guide.html"; exit 1; fi
pages=$(python3 - "$OUT" <<'PY'
import re, sys
data = open(sys.argv[1], "rb").read()
m = re.findall(rb"/Type\s*/Page[^s]", data)
print(len(m))
PY
)
echo "GUIDE.pdf: $pages pages, no soft masks, $(du -h "$OUT" | cut -f1)"
# thumbnails of every page for a look (qlmanage renders the first page; pdftoppm if present renders them all)
THUMBS="$HERE/.pages"; rm -rf "$THUMBS"; mkdir -p "$THUMBS"
if command -v pdftoppm > /dev/null 2>&1; then
  pdftoppm -r 60 -png "$OUT" "$THUMBS/page" > /dev/null 2>&1 && echo "page renders in $THUMBS/"
else
  qlmanage -t -s 1200 -o "$THUMBS" "$OUT" > /dev/null 2>&1 && echo "first page render in $THUMBS/ (install poppler for every page: brew install poppler)"
fi
