"""The golden pipeline: one frozen transcript, one frozen face track and one frozen look per fixture brand, run through spec_adapter.build and cluster_plan.build
exactly as run_captions.sh does, and the text of what comes out. golden/<brand>.spec.json and golden/<brand>.plan.json are that text as the engine wrote it;
tests/captions/test_cap_style_golden.py checks the engine still writes the same bytes.

The look is frozen on purpose (golden/<brand>.profile.json, lookprofile's output for the fixture brand), so editing a fixture's dna.json does not break the golden:
what it pins is the planner and the spec adapter, not the DNA. The only thing read live is each brand's font file (the planner measures every row with it): the
look and the output name it as ${BRANDS}/<brand>/Brand/assets/fonts/<file>, which is the folder cap_helpers made for the run.

  python3 tests/captions/golden_pipeline.py write        rewrite the golden files from the engine as it is now (do this only when the planner is changed on purpose)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GOLDEN = os.path.join(HERE, "golden")
TOKEN = "${BRANDS}"
for p in (os.path.join(REPO, "engine"), os.path.join(REPO, "engine", "captions"), HERE):
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
import cap_helpers  # noqa: E402

TIER_DOC = {"tier": "FULL", "reason": "golden", "room": {"above": 500, "below": 500, "left": 100, "right": 100},
            "safe": {"x0": 65, "x1": 940, "y0": 270, "y1": 1420}}
BRAND_IDS = cap_helpers.BRANDS

# a take that touches every word device and a few weak phrases: a negative word, a strike, numerals that count up, a place, a platform logo, an icon word, glue words
PHRASES = [
    (0.4, "Most owners waste money on ads."),
    (3.0, "We don't do long term contracts."),
    (5.8, "Last month we booked 12 calls in 30 days."),
    (9.6, "That's gonna be a big win for your shop."),
    (12.4, "Call us in Portland, Oregon."),
    (15.4, "Follow us on Instagram and book a call."),
    (18.6, "Custom shirts, fast turnaround, no surprises."),
]


def words():
    out = []
    for start, text in PHRASES:
        for i, w in enumerate(text.split()):
            s = round(start + i * 0.3, 3)
            out.append({"text": w, "start": s, "end": round(s + 0.25, 3), "filler": False})
    return out


def track():
    """a speaker who sways a little: the crown and the chin move between samples, so the planner's worst-case sampling has something to do"""
    samples = {str(s): {"cx": 540 + (s * 17) % 40 - 20, "cy": 740 + (s * 37) % 60, "w": 300, "h": 300} for s in range(0, 24)}
    return {"W": 1080, "H": 1920, "seconds": 24, "samples": samples, "median": {"cx": 540, "cy": 770, "w": 300, "h": 300}}


def cut_and_frames():
    cut = round(words()[-1]["end"] + 0.3, 3)
    return cut, int(cut * 30) + 1


def tokenise(text):
    return text.replace(cap_helpers.ROOT, TOKEN)


def expand(text):
    return text.replace(TOKEN, cap_helpers.ROOT)


def frozen_profile(brand_id, style=None):
    """the brand's look as a golden file holds it: JSON text with the fixture folder spelled ${BRANDS}"""
    return tokenise(json.dumps(cap_helpers.profile(brand_id, style), indent=1))


def load_profile(brand_id):
    # the fixture folder must exist before its paths are read
    cap_helpers.brand_dir(brand_id)
    with open(os.path.join(GOLDEN, f"{brand_id}.profile.json"), encoding="utf-8") as fh:
        return json.loads(expand(fh.read()))


def outputs(profile, tier_doc=None, gate=None, spec_hook=None):
    """(spec text, plan text) the way run_captions.sh writes spec.json and plan.json (json.dump indent 1), with the fixture folder spelled ${BRANDS}.
    spec_hook(spec) may change the spec between the two stages (the tests that pin a style use it)"""
    import cluster_plan
    import spec_adapter
    spec = spec_adapter.build(tier_doc or TIER_DOC, profile, gate=gate)
    if spec_hook:
        spec_hook(spec)
    cut, frames = cut_and_frames()
    plan = cluster_plan.build(words(), track(), spec, cut, frames)
    return tokenise(json.dumps(spec, indent=1)), tokenise(json.dumps(plan, indent=1))


def write_golden():
    os.makedirs(GOLDEN, exist_ok=True)
    with open(os.path.join(GOLDEN, "words.json"), "w", encoding="utf-8") as fh:
        json.dump({"words": words()}, fh, indent=1)
    with open(os.path.join(GOLDEN, "facetrack.json"), "w", encoding="utf-8") as fh:
        json.dump(track(), fh, indent=1)
    for b in BRAND_IDS:
        text = frozen_profile(b)
        with open(os.path.join(GOLDEN, f"{b}.profile.json"), "w", encoding="utf-8") as fh:
            fh.write(text)
        spec, plan = outputs(json.loads(expand(text)))
        for kind, body in (("spec", spec), ("plan", plan)):
            with open(os.path.join(GOLDEN, f"{b}.{kind}.json"), "w", encoding="utf-8") as fh:
                fh.write(body)
        print(f"wrote golden/{b}.profile.json, {b}.spec.json and {b}.plan.json")
    import shutil
    shutil.rmtree(cap_helpers.ROOT, ignore_errors=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["write"]:
        write_golden()
    else:
        print(__doc__)
        sys.exit(2)
