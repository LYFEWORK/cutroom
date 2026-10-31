#!/usr/bin/env python3
"""build_site.py: write the review page. Review/site/index.html is page.html as is; Review/site/data.js is the clip list.

    python3 build_site.py

data.js sets window.REVIEW_DATA = {built, shoots, groups, clips, dec}. A clip is one array (the page documents the order):
[id, shoot, kind, group, name, path, length, upright, notes, flags, copies, first-picture time, width, height, sound, media, added, bytes]
where media is a bit field: 1 there is a preview, 2 there is a thumbnail, 4 there is a filmstrip, and kind is an index into
take, broll, bad, other. "dec" is the decisions saved so far, so the page shows them even before the server answers.

The page reads previews, thumbnails and filmstrips from proxies/, posters/ and filmstrips/ next to it. Those are links to the
Review folder's own folders, so the page also works opened straight from disk (it just cannot save without the server).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402

PAGE = Path(__file__).resolve().parent / "page.html"
KIND_RANK = {"take": 0, "broll": 1, "other": 2, "bad": 3}


def short(s: str, n: int = 320) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 3].rsplit(" ", 1)[0] + "..."


def _link(site: Path, name: str, target: Path) -> None:
    """site/<name> -> ../<name>, so a relative proxies/ URL works from the page on disk and from the server alike."""
    link = site / name
    want = os.path.relpath(target, site)
    try:
        if link.is_symlink():
            if os.readlink(link) == want:
                return
            link.unlink()
        elif link.exists():
            return
        os.symlink(want, link)
    except OSError:
        pass


def build(say=print) -> dict:
    inv = store.load_inventory()
    clips = inv["clips"]
    proxies = store.load_proxies()
    pdir, sdir, fdir = store.d_proxies(), store.d_posters(), store.d_strips()
    shoots = sorted({c["project"] for c in clips}, reverse=True)       # dated names: newest shoot first
    si = {s: i for i, s in enumerate(shoots)}
    groups = sorted({c["group"] for c in clips})
    gi = {g: i for i, g in enumerate(groups)}
    ordered = sorted(clips, key=lambda c: (si[c["project"]], KIND_RANK.get(c["kind"], 2), c["group"].lower(), c["name"].lower()))
    rows = []
    for c in ordered:
        cid = c["id"]
        info = proxies.get(cid)
        media = (1 if info and (pdir / f"{cid}.mp4").exists() else 0) | (2 if (sdir / f"{cid}.jpg").exists() else 0) | (4 if (fdir / f"{cid}.jpg").exists() else 0)
        rows.append([cid, si[c["project"]], store.KINDS.index(c["kind"]) if c["kind"] in store.KINDS else 3, gi[c["group"]], c["name"], c["rel"],
                     round(float(c.get("dur") or 0), 2), 1 if c["h"] >= c["w"] else 0, short(c.get("desc", "")), c.get("flags", ""), len(c["paths"]),
                     (info or {}).get("vs", 0), c["w"], c["h"], 1 if c.get("audio") else 0, media, c.get("added", ""), c.get("bytes", 0)])
    known = {c["id"] for c in clips}
    dec = {i: {"s": d.get("s"), "r": d.get("r"), "at": d.get("at", 0)} for i, d in store.load_decisions(drafts=True).items() if i in known}
    data = {"built": inv.get("built", ""), "shoots": shoots, "groups": groups, "clips": rows, "dec": dec}
    site = store.d_site()
    site.mkdir(parents=True, exist_ok=True)
    js = "window.REVIEW_DATA=" + json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/") + ";\n"
    tmp = site / ".data.js.part"
    tmp.write_text(js, encoding="utf-8")
    os.replace(tmp, site / "data.js")
    version = "".join(ch for ch in inv.get("built", "") if ch.isdigit()) or "0"
    html = PAGE.read_text(encoding="utf-8").replace('<script src="data.js"></script>', f'<script src="data.js?v={version}"></script>')
    tmp = site / ".index.html.part"
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, site / "index.html")
    for name, target in (("proxies", pdir), ("posters", sdir), ("filmstrips", fdir)):
        target.mkdir(parents=True, exist_ok=True)
        _link(site, name, target)
    playable = sum(1 for r in rows if r[15] & 1)
    say(f"Page built: {len(rows)} clips ({playable} with a preview), {len(shoots)} shoot{'s' if len(shoots) != 1 else ''}, {len(js) / 1e3:.0f} KB of data")
    return {"clips": len(rows), "playable": playable, "path": str(site / "index.html")}


def main(argv=None) -> int:
    build()
    return 0


if __name__ == "__main__":
    store.run_cli(main)
