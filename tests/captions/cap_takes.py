"""cap_takes.py: a small, hand-written take folder for the gate tests: a transcript, a tier, a spec and a plan, the files capgate.py reads. No media."""
import json

SENTENCE = "Hey. What's going on everybody? This is Sam over here at Acme."


def write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def make_take(tmp_path, zone="top", tier="FULL", name="sam-intro"):
    """<tmp_path>/<name>/ holding words.json, tier.json, spec.json and plan.json for the 13-word line SENTENCE, and nothing else. Returns the folder (a Path)."""
    take_dir = tmp_path / name
    take_dir.mkdir()
    write(take_dir / "words.json", {
        "source": "whisper",
        "transcript": SENTENCE,
        "words": [
            {"text": "Hey.", "start": 0.16, "end": 0.56},
            {"text": "What's", "start": 0.63, "end": 0.80},
            {"text": "going", "start": 0.80, "end": 0.96},
            {"text": "on", "start": 0.96, "end": 1.12},
            {"text": "everybody?", "start": 1.12, "end": 1.60},
            {"text": "This", "start": 1.60, "end": 1.76},
            {"text": "is", "start": 1.76, "end": 1.92},
            {"text": "Sam", "start": 1.92, "end": 2.20},
            {"text": "over", "start": 2.20, "end": 2.40},
            {"text": "here", "start": 2.40, "end": 2.60},
            {"text": "at", "start": 2.60, "end": 2.70},
            {"text": "Acme", "start": 2.70, "end": 2.90},
            {"text": "Corp.", "start": 2.90, "end": 3.20},
        ],
    })
    write(take_dir / "tier.json", {
        "tier": tier,
        "reason": "392px above the crown (behind text fits), 505px below the chin",
        "room": {"above": 392, "below": 505, "left": 400, "right": 281},
        "safe": {"x0": 65, "x1": 940, "y0": 270, "y1": 1420},
    })
    write(take_dir / "spec.json", {"zone": zone, "accent": "#96ff00", "width": 960})
    write(take_dir / "plan.json", {
        "font": {"prefix": "InterRun", "family": "InterRunDyn"},
        "events": [
            {"mode": "behind", "words": [{"text": "Hey", "at": 0.16}], "start": 0.16, "end": 0.6},
            {"mode": "front", "words": [{"text": "Acme"}, {"text": "Corp"}], "start": 2.70, "end": 3.2},
            {"mode": "page", "words": [{"text": "over"}], "start": 2.20, "end": 2.40},
        ],
    })
    return take_dir
