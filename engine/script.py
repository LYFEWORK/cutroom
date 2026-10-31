#!/usr/bin/env python3
"""script.py: the script gate. The person edits words, the cut follows.

    script.py build <words.json> <plan.json> <SCRIPT.md>
    script.py apply <SCRIPT.md> <plan.json> <out plan.json> [--words words.json]

SCRIPT.md holds one section per plan segment (## 01, ## 02 ...; ## Hook for a segment moved first). Words the cutter will strike are shown
~~struck~~: fillers, and any word the plan already strikes. The person edits the file: wrap a word in ~~ ~~ to cut it, take the marks off to
keep it, delete a whole section to drop the segment. `approved: yes` at the top releases the take; the chain stops at "waiting for approval"
until it is there.

`apply` rewrites each segment's drop list from the file: SCRIPT.md is the source of truth for it, not a log that only grows, so un-striking a
word works however many times the file is applied. A strike is a WHOLE-WORD match (cut.resolve_drops): striking "um," never removes the middle
of "number,", and striking "ah," never removes the end of "Yeah,". With --words each strike is written as {"text", "start"}, the struck
word's own start time, so the exact word goes even when its text repeats in the segment.
"""
import sys

import support  # first: stops with one line on a Python older than 3.12

import json
import os
import re

import common
from cut import FILLERS, resolve_drops   # ONE filler list: the cut's own, so the gate can never disagree with the cutter

stop = common.stop
USAGE = "script.py build <words.json> <plan.json> <SCRIPT.md> | script.py apply <SCRIPT.md> <plan.json> <out plan.json> [--words words.json]"
NOTE = ["> Edit the words below. Wrap a word in ~~ ~~ to cut it, take the ~~ off to keep it, delete a whole section to drop it.",
        "> Then change the line above to `approved: yes`."]


def _tok(t):
    return re.sub(r"[^a-z0-9']", "", t.lower())


def _find_span(W, toks, occurrence=1):
    """Locate the occurrence-th (1-indexed) contiguous run in W (words.json's "words") whose normalized text equals `toks` (already-normalized
    tokens for one segment's match text). Returns (start, end), end exclusive. Positional correlation, not a text scan of the whole transcript, so
    filler flags and start times are read off the aligned word and never off another word that shares its text."""
    n = len(toks)
    hits = []
    if n:
        for start in range(0, len(W) - n + 1):
            if all(_tok(W[start + j]["text"]) == toks[j] for j in range(n)):
                hits.append(start)
    occ = occurrence if occurrence and occurrence > 0 else 1
    if len(hits) < occ:
        stop(f"script: a segment's words are not in words.json (number {occ} of {len(hits)} found): {' '.join(toks)[:70]!r}")
    start = hits[occ - 1]
    return start, start + n


def _title(label):
    return {"hook": "Hook"}.get(label.lower(), label.upper() if label.isalpha() else label)


def build(words, plan):
    """SCRIPT.md text for a plan: a section a segment, its words from words.json with the ones the cutter will strike marked."""
    name = plan.get("name", "take")
    lines = [f"# {name}", "approved: no"] + NOTE + [""]
    W = words["words"]
    for i, seg in enumerate(plan["segments"]):
        label = seg.get("label") or f"{i + 1:02d}"
        toks = seg["match"].split()
        start, _ = _find_span(W, [_tok(t) for t in toks], seg.get("occurrence", 1))
        mine = W[start:start + len(toks)]
        struck = {j for j, w in enumerate(mine) if w.get("filler")} | resolve_drops(mine, seg.get("drop", []))   # THIS occurrence's own words, by position
        lines += [f"## {_title(label)}", " ".join(f"~~{t}~~" if j in struck else t for j, t in enumerate(toks)), ""]
    return "\n".join(lines)


def parse(md):
    """SCRIPT.md -> {"approved", "lines": [{"label", "kept", "struck", "tokens", "groups"}]}. `groups` is every ~~strike~~ in order as
    {"text", "at", "n"}: its words and where the first one sits among the section's tokens."""
    out = {"approved": False, "lines": []}
    m = re.search(r"^approved:\s*(yes|no)", md, re.M)
    out["approved"] = bool(m and m.group(1) == "yes")
    for sec in re.split(r"^## ", md, flags=re.M)[1:]:
        head, _, body = sec.partition("\n")
        text = body.strip()
        tokens, groups = [], []
        for m in re.finditer(r"~~(.+?)~~|\S+", text):
            if m.group(1) is not None:
                ws = m.group(1).split()
                groups.append({"text": m.group(1).strip(), "at": len(tokens), "n": len(ws)})
                tokens += ws
            else:
                tokens.append(m.group(0).replace("~~", ""))
        struck = [g["text"] for g in groups]
        kept = [t for t in re.sub(r"~~.+?~~", "", text).split() if t]
        out["lines"].append({"label": head.strip(), "kept": kept, "struck": struck, "tokens": tokens, "groups": groups})
    return out


def apply(md, plan, words=None):
    """The plan with SCRIPT.md's edits in it: struck words become each segment's drop list, a section that was deleted removes its segment, a filler
    left unstruck is kept (keep_fillers), and gate.approved says whether the file is approved. With `words` (words.json's content) each strike is
    {"text", "start"}; without, the bare text."""
    p = parse(md)
    new = json.loads(json.dumps(plan))
    W = words["words"] if words else None

    def seg_title(i, seg):
        label = seg.get("label") or f"{i + 1:02d}"
        return label.lower()
    consumed = [False] * len(p["lines"])
    kept_segments = []
    for i, seg in enumerate(new["segments"]):
        t = seg_title(i, seg)
        expected = [_tok(w) for w in seg["match"].split()]
        line = None
        # Match by label AND by the section's actual (strike-stripped) word content, not position alone: when several segments share a label,
        # a section deleted out of the MIDDLE of that run still leaves the surviving sections bound to their own segment's content.
        for li, l in enumerate(p["lines"]):
            if consumed[li] or l["label"].lower() != t:
                continue
            if [_tok(w) for w in l["tokens"]] == expected:
                line = l
                consumed[li] = True
                break
        if line is None:
            continue                                            # section deleted: the segment goes
        if W is not None:
            start, _ = _find_span(W, expected, seg.get("occurrence", 1))
            seg["drop"] = [{"text": g["text"], "start": W[start + g["at"]]["start"]} if W[start + g["at"]].get("start") is not None else g["text"]
                           for g in line["groups"]]
        else:
            seg["drop"] = list(line["struck"])
        if any(_tok(w) in FILLERS for w in line["kept"]):
            seg["keep_fillers"] = True                          # a filler left unstruck has to survive the cut: drop_fillers is plan-wide
        else:
            seg.pop("keep_fillers", None)
        kept_segments.append(seg)
    new["segments"] = kept_segments
    new["gate"] = {"approved": p["approved"]}
    return new


def _main():
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "build":
        words_p, plan_p, out = a[0], a[1], a[2]
        md = build(json.load(open(words_p)), json.load(open(plan_p)))   # the text first: a failure leaves no half-written SCRIPT.md to read as "waiting"
        with open(out, "w") as f:
            f.write(md)
        print("wrote", out)
    elif cmd == "apply":
        md_p, plan_p, out = a[0], a[1], a[2]
        words = json.load(open(a[a.index("--words") + 1])) if "--words" in a else None
        new = apply(open(md_p).read(), json.load(open(plan_p)), words)
        # Write ONLY when the plan changes: the chain's cut guard skips a re-cut while the cut is newer than plan.json, and `apply` runs on every
        # resume. A real SCRIPT.md edit, and the widened lead_extra or tail_extra the retry ladder writes, still change the bytes.
        blob = json.dumps(new, indent=1)
        same = os.path.exists(out) and open(out).read() == blob
        if not same:
            with open(out, "w") as f:
                f.write(blob)
        print("approved:", new["gate"]["approved"], "| segments:", len(new["segments"]), "| plan.json:", "unchanged" if same else "written")
    else:
        stop(f"script: unknown command {cmd!r} (build | apply): usage: {USAGE}")


if __name__ == "__main__":
    support.cli(_main, USAGE)
