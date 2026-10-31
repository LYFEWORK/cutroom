"""job.bad_takes_folder: takes.py cuts every flubbed take into one folder at the shoot root and keeps the clean and usable takes in their line folders, so a line
folder holds only good takes. An ad shoot gets "Bad Takes" unless its job says otherwise; an interview shoot, or a job that sets "", keeps every take in its line folder.
The synthetic shoot's C0007 holds a false start (a flub) and then a clean read of Q01."""
import json
import os

import pytest

from job import bad_takes_folder
from conftest import intake, ok, sha_tree
from test_takes_encode import read_manifest, run_takes, shoot  # noqa: F401 (shoot is a pytest fixture)


def set_job(root, **kw):
    p = root / "job.json"
    j = json.loads(p.read_text())
    j.update(kw)
    p.write_text(json.dumps(j, indent=1))


def test_an_ad_shoot_puts_its_bad_takes_in_bad_takes_unless_the_job_says_otherwise():
    assert bad_takes_folder({"name": "Test", "shoot_type": "ad"}) == "Bad Takes"
    assert bad_takes_folder({"name": "Test", "shoot_type": "ad", "bad_takes_folder": "Rejects"}) == "Rejects"


def test_an_interview_shoot_or_a_blank_name_keeps_every_take_in_its_line_folder():
    assert bad_takes_folder({"name": "Test"}) == ""
    assert bad_takes_folder({"name": "Test", "shoot_type": "interview"}) == ""
    assert bad_takes_folder({"name": "Test", "shoot_type": "ad", "bad_takes_folder": ""}) == ""
    assert bad_takes_folder({"name": "Test", "shoot_type": "ad", "bad_takes_folder": "  "}) == ""


def test_a_plain_name_comes_back_in_title_case():
    assert bad_takes_folder({"name": "Test", "bad_takes_folder": "Bad Takes"}) == "Bad Takes"
    assert bad_takes_folder({"name": "Test", "bad_takes_folder": "bad takes"}) == "Bad Takes"


@pytest.mark.parametrize("name", ["Interview", "B-Roll", "Raw Clips", "Edit", "Unusable", "Camera Sidecars", "Off Topic", "RAW", "a/b", "../Bad", ".hidden"])
def test_a_path_or_one_of_the_organizers_own_folders_stops_the_run(name, capsys):
    with pytest.raises(SystemExit) as e:
        bad_takes_folder({"name": "Test", "bad_takes_folder": name})
    assert e.value.code == 2 and capsys.readouterr().err.startswith("STOP: job.bad_takes_folder must be one plain folder name")


def test_on_an_ad_shoot_every_flub_is_cut_into_bad_takes_and_the_line_folders_keep_the_good_takes(shoot):
    set_job(shoot, shoot_type="ad")                                       # no bad_takes_folder key: the ad-shoot default
    r = run_takes(shoot)
    assert r.returncode == 0, ok(r)
    rows = read_manifest(shoot)
    flubs = [x for x in rows if x["kind"] == "take" and x["quality"] == "flub"]
    good = [x for x in rows if x["kind"] == "take" and x["quality"] != "flub"]
    assert flubs and good
    assert all(x["file"].startswith("Bad Takes/") for x in flubs)
    assert all(x["file"].startswith("Interview/") for x in good)
    assert all(x["file"].startswith("B-Roll/Bloopers/") for x in rows if x["kind"] == "blooper")
    assert all(os.path.exists(shoot / x["file"]) for x in rows)
    readme = (shoot / "README.txt").read_text()
    assert "Bad Takes/ holds the 1 flubbed take" in readme and "Interview/, B-Roll/Bloopers/, Bad Takes/" in readme


def test_the_qc_counts_the_cuts_in_the_bad_takes_folder_and_the_index_lists_them(shoot):
    set_job(shoot, shoot_type="ad")
    assert run_takes(shoot).returncode == 0
    r = intake("takes", shoot, "--cut", "--handle", "1")                  # a rerun through intake: cut again (kept), index again, QC
    assert r.returncode == 0 and r.stdout.strip().splitlines()[-1] == "QC OK", ok(r)
    assert "Bad Takes/" in (shoot / "START HERE.html").read_text() or "Bad%20Takes/" in (shoot / "START HERE.html").read_text()
    stray = shoot / "Bad Takes" / "C9999-01 - Q01 - Take 9 - Flub.mov"
    stray.write_bytes(b"not a cut anyone listed")
    r = intake("takes", shoot, "--cut", "--handle", "1")                  # a file in the folder that no manifest lists is a stray
    assert r.returncode == 1 and "QC: every cut file on disk is listed in Takes Manifest.csv" in r.stdout


def test_on_an_interview_shoot_a_flub_stays_in_its_line_folder(shoot):
    r = run_takes(shoot, "--dry-run")
    assert r.returncode == 0, ok(r)
    assert "Bad Takes" not in r.stdout
    assert any(line.strip().startswith("Interview/") and " - Flub" in line for line in r.stdout.splitlines())


def test_an_ad_shoot_that_sets_it_blank_keeps_its_flubs_in_their_line_folders(shoot):
    set_job(shoot, shoot_type="ad", bad_takes_folder="")
    r = run_takes(shoot, "--dry-run")
    assert r.returncode == 0, ok(r)
    assert "Bad Takes" not in r.stdout
    assert any(line.strip().startswith("Interview/") and " - Flub" in line for line in r.stdout.splitlines())


def test_an_ad_shoot_can_name_its_own_folder(shoot):
    set_job(shoot, shoot_type="ad", bad_takes_folder="Rejects")
    assert run_takes(shoot).returncode == 0
    assert (shoot / "Rejects").is_dir() and any(f.startswith("C0007-01") for f in os.listdir(shoot / "Rejects"))


def test_reverse_removes_the_bad_takes_folder_it_made_and_every_cut_and_puts_the_camera_files_back(shoot, started_master):
    set_job(shoot, shoot_type="ad")
    assert run_takes(shoot).returncode == 0 and (shoot / "Bad Takes").is_dir()
    r = intake("reverse", shoot)
    assert r.returncode == 0, ok(r)
    left = sha_tree(shoot)
    assert {k.replace("RAW/", "", 1): v for k, v in left.items() if k.startswith("RAW/")} == started_master["hashes"]
    assert sorted(k for k in left if not k.startswith("RAW/")) == ["job.json"]
    assert not any((shoot / d).exists() for d in ("Bad Takes", "Raw Clips", "Interview", "B-Roll", "Edit"))
