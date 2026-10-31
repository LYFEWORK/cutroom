"""lookprofile.py is the caption chain's one door to the DNA (engine/dna.py). These tests run it against the two fixture brands: the look it makes, the style laid over
it, the keys the chain adds, and the plain STOP lines for a look the engine cannot use."""
import json
import os
import subprocess
import sys

import pytest

import cap_helpers
import lookprofile
from keys import EngineStop

SCRIPT = os.path.join(cap_helpers.REPO, "engine", "captions", "lookprofile.py")


@pytest.mark.parametrize("brand_id,family,accent,case", [
    ("varsans", "InterDyn", "#FFD60A", "mixed"),
    ("upstatic", "InterStaticDyn", "#38E1B0", "upper"),
])
def test_a_look_carries_the_colours_font_and_case_of_its_dna(brand_id, family, accent, case):
    p = cap_helpers.profile(brand_id)
    assert (p["font_family"], p["accent"], p["case"]) == (family, accent, case)
    assert p["font_faces"] and all(os.path.isfile(f) for f in p["font_abs"].values())
    assert p["font_faces"][0]["src"].startswith(f"brand-{brand_id}/fonts/")


def test_font_sources_name_every_file_the_plan_can_load_for_both_faces():
    p = cap_helpers.profile("upstatic")
    assert p["word_font"]["family"] == "InterRunDyn"
    srcs = p["font_sources"]
    assert srcs["brand-upstatic/fonts/Inter-ExtraBold.ttf"].endswith("Inter-ExtraBold.ttf")
    assert srcs["brand-upstatic/fonts/Inter-Variable.ttf"].endswith("Inter-Variable.ttf")
    assert all(os.path.isfile(v) for v in srcs.values())


def test_font_sources_still_list_both_faces_when_a_style_swaps_the_slot():
    p = cap_helpers.profile("upstatic", "rose-running")
    assert p["font_family"] == "InterRunDyn"
    assert {os.path.basename(v) for v in p["font_sources"].values()} == {"Inter-ExtraBold.ttf", "Inter-Variable.ttf"}


def test_a_style_is_recorded_as_caption_style_and_its_feed_block_is_not_carried():
    p = cap_helpers.profile("varsans", "keyword-slam")
    assert p["caption_style"] == "keyword-slam" and "style" not in p and "feed" not in p
    assert p["zone"] == "chest" and p["hero_max"] == 280 and p["row_px"] == [48, 60] and p["whip_frames"] == 4


def test_without_a_style_the_look_is_exactly_what_the_dna_makes():
    p = cap_helpers.profile("varsans")
    assert "caption_style" not in p
    import dna
    d = cap_helpers.dna_of("varsans")
    base = dna.captions_profile(d, cap_helpers.brand_dir("varsans"), dna.public_prefix(d))
    assert {k: v for k, v in p.items() if k in base} == base


def test_the_look_block_of_a_style_lays_colours_over_the_look():
    p = cap_helpers.profile("upstatic", "rose-running")
    assert p["accent"] == "#E8456B" and p["base"] == "#F7F2EE" and p["on_accent"] == "#1A1014"
    assert p["hero_tint"] == "#FFB3C4" and p["chip_neg"] == "#B3122B" and p["display_weight"] == 700 and p["hero_shadow"] == "0 2px 10px rgba(0,0,0,0.5)"
    # the colours of the DNA itself did not move
    assert cap_helpers.profile("upstatic")["accent"] == "#38E1B0"


def test_a_style_that_asks_for_the_running_face_on_a_look_without_one_is_one_plain_stop(tmp_path):
    bd = cap_helpers.materialize("varsans", str(tmp_path / "Brand"))
    import shutil
    shutil.copyfile(os.path.join(cap_helpers.FIXTURES, "styles", "rose-running.json"), os.path.join(bd, "styles", "rose-running.json"))
    import dna
    with pytest.raises(EngineStop, match="running"):
        lookprofile.build(dna.load(bd), bd, "rose-running")


def test_an_unknown_style_names_the_known_ones():
    with pytest.raises(EngineStop) as e:
        cap_helpers.profile("varsans", "no-such-style")
    assert "unknown style" in str(e.value) and "keyword-slam" in str(e.value)


def test_the_switches_the_dna_does_not_carry_default_off():
    p = cap_helpers.profile("varsans")
    assert p["devices"] == "off" and p["master_audio"] == "off" and p["auto_punches"] is False
    assert p["transcribe"] == {"engine": "local", "language": None}


def test_pace_gate_comes_from_the_dna():
    assert cap_helpers.profile("varsans")["pace_gate"] == "report"
    assert cap_helpers.profile("upstatic")["pace_gate"] == "enforce"


def test_devices_and_auto_punches_follow_the_dna(tmp_path):
    d = cap_helpers.dna_of("varsans")
    d["captions"]["devices"] = "on"
    d["motion"]["auto_punches"] = True
    # a closed schema would refuse the devices key, but the look reads whatever the profile carries: the engine's own switch
    import dna
    prof = dna.captions_profile(d, cap_helpers.brand_dir("varsans"), dna.public_prefix(d))
    assert prof["auto_punches"] is True and prof["devices"] == "on"
    p = lookprofile.build(d, cap_helpers.brand_dir("varsans"))
    assert p["auto_punches"] is True and p["devices"] == "on"


def test_deepgram_is_chosen_only_by_an_explicit_setting(monkeypatch):
    d = cap_helpers.dna_of("varsans")
    monkeypatch.setenv("CUTROOM_TRANSCRIBE", "deepgram")
    assert lookprofile.build(d, cap_helpers.brand_dir("varsans"))["transcribe"]["engine"] == "deepgram"
    monkeypatch.setenv("CUTROOM_TRANSCRIBE", "whisper")
    assert lookprofile.build(d, cap_helpers.brand_dir("varsans"))["transcribe"]["engine"] == "local"
    monkeypatch.delenv("CUTROOM_TRANSCRIBE")
    d["transcribe"] = {"engine": "deepgram", "language": "es"}
    assert lookprofile.build(d, cap_helpers.brand_dir("varsans"))["transcribe"] == {"engine": "deepgram", "language": "es"}


def test_a_colour_that_is_not_a_hex_stops_with_one_plain_line():
    p = cap_helpers.profile("varsans")
    p["accent"] = "yellow"
    with pytest.raises(EngineStop, match="accent is 'yellow'"):
        lookprofile.check_profile(p)


def test_a_missing_font_file_stops_naming_the_weight():
    p = cap_helpers.profile("varsans")
    p["font_abs"] = {**p["font_abs"], "800": "/no/such/font.ttf"}
    with pytest.raises(EngineStop, match="weight 800 is not there"):
        lookprofile.check_profile(p)


def test_an_unknown_pace_gate_stops():
    p = cap_helpers.profile("varsans")
    p["pace_gate"] = "sometimes"
    with pytest.raises(EngineStop, match="pace_gate"):
        lookprofile.check_profile(p)


def run_cli(*args, workspace=None):
    env = dict(os.environ)
    return subprocess.run([sys.executable, "-B", SCRIPT, *map(str, args)], capture_output=True, text=True, env=env)


def test_the_cli_writes_a_look_json_and_prints_one_summary_line(tmp_path, workspace):
    cap_helpers.install_brand(workspace, "varsans")
    out = tmp_path / "look.json"
    r = run_cli(out, "--style", "soft-build")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().startswith("look varsans: Variable Sans accent #FFD60A") and r.stdout.strip().endswith("style soft-build")
    assert json.load(open(out))["caption_style"] == "soft-build"


def test_the_cli_stops_with_one_line_on_stderr_for_a_dna_that_is_not_finished(tmp_path, workspace):
    bd = cap_helpers.install_brand(workspace, "varsans")
    with open(os.path.join(bd, "DNA.md"), "a") as f:
        f.write("\nTODO: who is on camera.\n")
    r = run_cli(tmp_path / "look.json")
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.startswith("dna: cannot be used:") and "TODO" in r.stderr
