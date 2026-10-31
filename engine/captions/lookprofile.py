#!/usr/bin/env python3
"""lookprofile.py [<out.look.json>] [--style <id>]

The caption chain's one door to your DNA (Brand/dna.json, read only through engine/dna.py). Prints your look as JSON, or writes it to <out.look.json> and
prints one summary line. The look is the flat profile dna.captions_profile() makes (colours, fonts and their files, layout numbers, device switches, word
lists, the pacing settings), with the take's caption style laid over it when there is one (dna.style(): its `set` fields replace the look's layout and
motion, its `look` block replaces colours and the font slot), plus what this chain adds:

  caption_style  the style's id, when the take has one (the DNA's own "style" key, renamed: every later stage records it under this name)
  font_sources   {the font src the plan names: the file on disk}, for every face the look can draw (display and running), so the renderer's staging
                 folder gets real copies of exactly the files the plan loads
  transcribe     {"engine": "local" or "deepgram", "language": or null}: local unless the DNA, config.json (key `transcribe`) or CUTROOM_TRANSCRIBE says deepgram
  devices        "on" or "off": whether layout-event proposals are made (a `devices` key in the captions section turns it on; off by default)
  master_audio   "off" or "loudnorm" (off by default)

A DNA that does not load, or a look the engine cannot use (a colour that is not #RRGGBB, a font file that is missing), stops with one plain "STOP:" line and
exit 2.

  --style <id>   the style to lay over the look (Brand/styles, then templates/styles). Without it the profile is exactly what the DNA makes. run_captions.sh
                 passes the take's style (takectl.py style).

Defaults that differ from the engine this was ported from, on purpose: the pace gate is the DNA's (report in the template; the old engine enforced it),
auto_punches off, devices off, words by the local transcriber.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
for p in (ENGINE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from keys import cli, EngineStop  # noqa: E402
import common  # noqa: E402
import dna  # noqa: E402  (engine/dna.py: the one reader of the DNA)

# what a look gets for a key the DNA's profile does not carry. The DNA owns every layout number (templates/dna.json); these are the switches that are not in it.
DEFAULTS = {
    "master_audio": "off", "devices": "off", "pace_gate": "report", "auto_punches": False,
    "neg_words": [], "places": [], "icon_words": [], "pill_icons": [], "spelling": {}, "do_not_say": [],
}
PACE_GATES = ("off", "report", "enforce")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
COLOUR_KEYS = ("accent", "base", "on_accent")


def _on(v):
    return v is True or str(v).strip().lower() in ("on", "yes", "true", "auto")


def _sources(profile):
    """{src: file on disk} for every face the look can draw, read before a style swaps the slot"""
    out = {}
    for files, abs_ in ((profile.get("font_files"), profile.get("font_abs")), ((profile.get("word_font") or {}).get("files"), (profile.get("word_font") or {}).get("abs"))):
        for w, src in (files or {}).items():
            if src and (abs_ or {}).get(w):
                out[src] = abs_[w]
    return out


def check_profile(profile):
    """One plain EngineStop line for a look the engine cannot draw: a colour that is not #RRGGBB, a font with no file on disk, a pace gate it does not know."""
    for k in COLOUR_KEYS:
        if not isinstance(profile.get(k), str) or not HEX.match(profile[k]):
            raise EngineStop(f"look: {k} is {profile.get(k)!r}, which is not a colour like #FFD60A (fix it in your DNA)")
    for k in ("font_family", "font_faces", "font_abs", "font_files"):
        if not profile.get(k):
            raise EngineStop(f"look: the profile has no {k}: the DNA names no display font file")
    for w, p in sorted(profile["font_abs"].items()):
        if not os.path.isfile(p):
            raise EngineStop(f"look: the display font file for weight {w} is not there ({p})")
    for w, p in sorted(((profile.get("word_font") or {}).get("abs") or {}).items()):
        if not os.path.isfile(p):
            raise EngineStop(f"look: the running font file for weight {w} is not there ({p})")
    if profile["pace_gate"] not in PACE_GATES:
        raise EngineStop(f"look: pace_gate is {profile['pace_gate']!r}, which is not one of {', '.join(PACE_GATES)}")


def _engine(d):
    """local or deepgram: the DNA's `transcribe` when it has one, else config.json's, else CUTROOM_TRANSCRIBE. Anything but deepgram is local."""
    t = d.get("transcribe")
    want = (t.get("engine") if isinstance(t, dict) else t) or common.load_config(require_workspace=False).get("transcribe") or ""
    return "deepgram" if str(os.environ.get("CUTROOM_TRANSCRIBE") or want).strip().lower() == "deepgram" else "local"


def build(d, brand_dir, style=None):
    """The look for a DNA dict: dna.captions_profile, the style over it (dna.style), the switches the DNA does not carry filled in. EngineStop carries one plain line."""
    prefix = dna.public_prefix(d)
    try:
        base = dna.captions_profile(d, brand_dir, prefix)
        profile = dict(base)
        if style:
            profile = dna.style(d, style, brand_dir, prefix)
            profile["caption_style"] = profile.pop("style")
            profile.pop("feed", None)
    except dna.DnaError as e:
        raise EngineStop(f"style: {e}" if style else f"dna: {e}")
    for k, v in DEFAULTS.items():
        if profile.get(k) is None:
            profile[k] = json.loads(json.dumps(v))
    profile["auto_punches"] = _on(profile["auto_punches"])
    profile["devices"] = "on" if _on(profile["devices"]) else "off"
    t = d.get("transcribe")
    profile["transcribe"] = {"engine": _engine(d), "language": t.get("language") if isinstance(t, dict) else None}
    check_profile(profile)
    profile["font_sources"] = _sources(base)
    return profile


def load(style=None, d=None, brand_dir=None):
    """The look for the user's DNA (or d, with brand_dir: the tests pass theirs). A DNA that does not load stops with the DNA module's own line."""
    if d is None:
        d = dna.load_or_exit()
    return build(d, brand_dir or str(common.brand_dir()), style)


def _main():
    a = sys.argv[1:]
    style = None
    if "--style" in a:
        i = a.index("--style")
        style = a[i + 1]
        del a[i:i + 2]
    profile = load(style)
    if a:
        with open(a[0], "w") as f:
            json.dump(profile, f, indent=1)
        print(f"look {profile['id']}: {profile['brand']} accent {profile['accent']} font {profile['font']} "
              f"({profile['font_family']}) case {profile.get('case', 'mixed')} zone {profile.get('zone', 'chest')}"
              + (f" style {style}" if style else ""))
    else:
        print(json.dumps(profile, indent=1))


if __name__ == "__main__":
    cli(_main, "lookprofile.py [<out.look.json>] [--style <id>]")
