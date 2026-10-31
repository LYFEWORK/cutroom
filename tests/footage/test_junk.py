"""The junk list: a path is junk when any segment matches a pattern. A card and a Finder window scatter these files; none of them is footage."""
import pytest

import job

LIST = [".DS_Store", "._*", "Thumbs.db", "desktop.ini", ".Spotlight-V100/", ".fseventsd/", ".Trashes/", "*.part.*"]


def test_the_list_is_the_eight_patterns_in_order():
    assert list(job.JUNK_PATTERNS) == LIST


@pytest.mark.parametrize("rel", [".DS_Store", "B-Roll/Other/.DS_Store", "B-Roll/Other/._C0004 - Dryer.MP4", "Thumbs.db", "Unusable/desktop.ini",
                                 ".Spotlight-V100/Store-V2/index", ".fseventsd/0000001", ".Trashes/501/C0001.MP4",
                                 "Interview/Q01 Welcome/C0002-01 - Q01 - Take 1.mov.part.mov", "B-Roll\\Other\\.DS_Store"])
def test_junk(rel):
    assert job.is_junk(rel)


@pytest.mark.parametrize("rel", ["Interview/Q01 Welcome To The Workshop/C0002 - Q01 - Welcome - Take 1.MP4", "README.txt", "Footage Manifest.csv",
                                 "B-Roll/Other/C0004 - Conveyor Dryer Ambient Hum Close.MP4", "Edit/plan.json", "a/Desktop.ini", "Off Topic/C0007.MP4",
                                 "x/.ds_store", "x/thumbs.db"])
def test_not_junk(rel):
    assert not job.is_junk(rel)


@pytest.mark.parametrize("name", [".DS_Store", "._C0001.MP4", "Thumbs.db", "desktop.ini"])
def test_only_the_operating_systems_own_files_may_be_deleted_by_a_tidy_up(name):
    assert job.is_os_junk(name)


@pytest.mark.parametrize("name", ["clip.part.1.mov", "C0001.MP4", ".Spotlight-V100", ".hidden", "thumbs.db", "notes.txt", "x.part.mov", "Desktop.ini"])
def test_nothing_else_is_deleted_even_when_the_card_list_would_leave_it_behind(name):
    assert not job.is_os_junk(name)
