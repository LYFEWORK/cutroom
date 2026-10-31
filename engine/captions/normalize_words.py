#!/usr/bin/env python3
"""normalize_words.py <words.json> [<look.json>]
Rewrites the transcript's tokens in place, before planning, so the plan, the gate transcript and the QC coverage check all
see the same tokens:
  - spoken numbers become numerals ("eleven" -> "11", "twenty five" -> "25", "two hundred" -> "200"), merging the source
    tokens' timing;
  - with the take's look.json, your spelling map (words.spelling in your DNA: "acme corp" -> "ACME Corp", "jon," -> "John,")
    fixes names the transcriber gets wrong. Keys may span several words; matching ignores case and
    the punctuation around a word, and the word's own punctuation is kept ("jon," -> "John,").
Backup: words.json.pre-normalize (written the first time a rewrite happens; a second run rewrites nothing)."""
import json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cluster_plan import numerals
from keys import cli, EngineStop

_EDGE = re.compile(r"^([^\w']*)(.*?)([^\w']*)$", re.S)


def _split(text):
    """(leading punctuation, the word, trailing punctuation); the word is lowercased with curly apostrophes made straight."""
    lead, core, trail = _EDGE.match(str(text)).groups()
    return lead, core.replace("\u2019", "'").lower(), trail


def apply_spelling(words, spelling):
    """The words with every spelling key replaced by its spelling. A key of several words merges the matched tokens into one
    (first start, last end); the first token's leading and the last token's trailing punctuation stay."""
    keys = sorted(((tuple(k.lower().split()), v) for k, v in (spelling or {}).items() if k.strip()), key=lambda kv: -len(kv[0]))
    if not keys:
        return words
    norm = [_split(w["text"])[1] for w in words]
    out, i = [], 0
    while i < len(words):
        for toks, fixed in keys:
            n = len(toks)
            if tuple(norm[i:i + n]) == toks and i + n <= len(words):
                lead, trail = _split(words[i]["text"])[0], _split(words[i + n - 1]["text"])[2]
                merged = dict(words[i])
                merged["text"] = f"{lead}{fixed}{trail}"
                merged["end"] = words[i + n - 1]["end"]
                out.append(merged)
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


def _main():
    p = sys.argv[1]
    with open(p) as f:
        d = json.load(f)
    if not isinstance(d, dict) or not isinstance(d.get("words"), list):
        raise EngineStop(f"{os.path.basename(p)} has no \"words\" list; re-run the hear stage")
    spelling = {}
    if len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            spelling = json.load(f).get("spelling") or {}
    old = [w["text"] for w in d["words"]]
    d["words"] = apply_spelling(numerals(d["words"]), spelling)
    new = [w["text"] for w in d["words"]]
    changed = sum(1 for w in new if w not in old) + (len(old) - len(new))
    if old != new:
        shutil.copy(p, p + ".pre-normalize")
        d["transcript"] = " ".join(w["text"] for w in d["words"])
        with open(p, "w") as f:
            json.dump(d, f, indent=1)
    print(f"normalize: {changed} token(s) rewritten")


if __name__ == "__main__":
    cli(_main, "normalize_words.py <words.json> [<look.json>]")
