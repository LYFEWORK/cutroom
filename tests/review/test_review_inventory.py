from __future__ import annotations

import json
import shutil

import pytest

import common
from review import inventory, store
from conftest import SHOOT, ffmpeg, make_clip


def rows_by_name():
    return {c["name"]: c for c in store.load_inventory()["clips"]}


def test_kinds_projects_and_groups(shoot):
    inventory.build(say=lambda *_: None)
    r = rows_by_name()
    assert set(r) == {"desk_v.mp4", "desk_h.mp4", "silent.mp4", "take1.mp4"}
    assert (r["take1.mp4"]["kind"], r["take1.mp4"]["group"]) == ("take", "L01 Line")
    assert (r["desk_v.mp4"]["kind"], r["desk_v.mp4"]["group"]) == ("broll", "Desk")
    assert all(c["project"] == SHOOT for c in r.values())
    assert r["desk_v.mp4"]["rel"] == f"{SHOOT}/B-Roll/Desk/desk_v.mp4"


def test_every_kind_folder(ws, clips):
    base = ws / "Footage" / SHOOT
    layout = {                                  # folder under the shoot -> (kind, group)
        "Interview/Q1": ("take", "Q1"),
        "Takes/Line 2": ("take", "Line 2"),
        "Bad Takes": ("bad", "Loose clips"),
        "Drone B-Roll/Sky": ("broll", "Sky"),
        "Off Topic": ("other", "Off Topic"),
        "Unusable": ("other", "Unusable"),
        "": ("other", "Loose clips"),
    }
    for i, sub in enumerate(layout):
        (base / sub).mkdir(parents=True, exist_ok=True)
        ffmpeg("-i", clips["silent"], "-c", "copy", "-metadata", f"comment=n{i}", base / sub / f"clip{i}.mp4")      # distinct content per file
    inventory.build(say=lambda *_: None)
    got = {c["rel"].split("/", 1)[1]: (c["kind"], c["group"]) for c in store.load_inventory()["clips"]}
    for i, (sub, want) in enumerate(layout.items()):
        assert got[f"{sub}/clip{i}.mp4".lstrip("/")] == want, sub


def test_a_clip_directly_in_the_footage_folder_gets_a_project(ws, clips):
    shutil.copy(clips["silent"], ws / "Footage" / "loose.mp4")
    inventory.build(say=lambda *_: None)
    assert store.load_inventory()["clips"][0]["project"] == "Unsorted"


def test_what_is_skipped(shoot, clips):
    for folder in (".work", "RAW", "Raw Clips", ".review", ".hidden"):
        (shoot / folder).mkdir()
        ffmpeg("-i", clips["silent"], "-c", "copy", "-metadata", f"comment={folder}", shoot / folder / "x.mp4")
    ffmpeg("-i", clips["silent"], "-c", "copy", "-metadata", "comment=stub", shoot / "B-Roll" / "Desk" / "._stub.mp4")
    ffmpeg("-i", clips["silent"], "-c", "copy", "-metadata", "comment=hid", shoot / "B-Roll" / "Desk" / ".hidden.mp4")
    (shoot / "notes.txt").write_text("not a video")
    inventory.build(say=lambda *_: None)
    assert set(rows_by_name()) == {"desk_v.mp4", "desk_h.mp4", "silent.mp4", "take1.mp4"}


def test_the_review_folder_is_never_walked(ws, shoot):
    inventory.build(say=lambda *_: None)
    from conftest import ingest_quiet
    ingest_quiet()                                                  # makes proxies under Review/
    common.save_config({**common.load_config(), "review": {"roots": [str(ws)]}})     # a root that contains Review/
    inventory.build(say=lambda *_: None)
    assert not any("Review" in c["rel"].split("/") for c in store.load_inventory()["clips"])


def test_a_file_without_a_picture_is_skipped_and_named(shoot):
    ffmpeg("-f", "lavfi", "-i", "sine=duration=2", "-c:a", "aac", shoot / "B-Roll" / "Desk" / "audio_only.mp4")
    res = inventory.build(say=lambda *_: None)
    assert res["clips"] == 4 and res["skipped"] == [f"{SHOOT}/B-Roll/Desk/audio_only.mp4"]


def test_rows_carry_the_probed_stream_indexes(shoot):
    inventory.build(say=lambda *_: None)
    r = rows_by_name()
    assert (r["desk_v.mp4"]["vindex"], r["desk_v.mp4"]["aindex"]) == (0, 1)
    assert r["silent.mp4"]["audio"] is False and r["silent.mp4"]["aindex"] is None
    assert r["desk_h.mp4"]["w"] == 384 and r["desk_h.mp4"]["h"] == 216 and abs(r["desk_h.mp4"]["dur"] - 5.0) < 0.2


def test_a_cover_image_stored_first_is_not_the_picture(shoot, tmp_path):
    cover = tmp_path / "cover.jpg"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:size=100x100", "-frames:v", "1", cover)
    out = shoot / "B-Roll" / "Desk" / "with_cover.mp4"
    ffmpeg("-i", cover, "-i", shoot / "B-Roll" / "Desk" / "desk_v.mp4", "-map", "0", "-map", "1", "-c", "copy", out)
    first = json.loads(common.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", out]).stdout)["streams"][0]
    assert first["codec_name"] == "mjpeg"                       # the premise: a picture that is not the video sits at stream 0
    inventory.build(say=lambda *_: None)
    c = rows_by_name()["with_cover.mp4"]
    assert c["vindex"] == 1 and (c["w"], c["h"]) == (216, 384) and c["codec"] == "h264"


def test_second_run_reads_nothing(shoot, monkeypatch):
    first = inventory.build(say=lambda *_: None)
    assert first["read"] == 4 and len(first["new_ids"]) == 4
    monkeypatch.setattr(common, "probe", lambda p: pytest.fail("an unchanged file was probed again"))
    monkeypatch.setattr(common, "fingerprint", lambda p: pytest.fail("an unchanged file was fingerprinted again"))
    second = inventory.build(say=lambda *_: None)
    assert second["read"] == 0 and second["new_ids"] == [] and second["clips"] == 4


def test_a_changed_file_is_read_again_and_is_a_new_clip(shoot, clips):
    inventory.build(say=lambda *_: None)
    old = rows_by_name()["desk_v.mp4"]["id"]
    target = shoot / "B-Roll" / "Desk" / "desk_v.mp4"
    make_clip(target, "216x384", 3, freq=900)                       # re-exported: different content at the same path
    res = inventory.build(say=lambda *_: None)
    new = rows_by_name()["desk_v.mp4"]["id"]
    assert new != old and new in res["new_ids"] and old in res["gone_ids"]


def test_a_duplicate_copy_folds_into_one_row_with_both_paths(shoot):
    (shoot / "B-Roll" / "Copy").mkdir()
    shutil.copy(shoot / "B-Roll" / "Desk" / "desk_v.mp4", shoot / "B-Roll" / "Copy" / "desk_v copy.mp4")
    res = inventory.build(say=lambda *_: None)
    assert res["clips"] == 4
    both = [c for c in store.load_inventory()["clips"] if len(c["paths"]) == 2]
    assert len(both) == 1
    assert sorted(p["rel"].split("/")[-1] for p in both[0]["paths"]) == ["desk_v copy.mp4", "desk_v.mp4"]
    assert both[0]["rel"] == both[0]["paths"][0]["rel"]               # the first sorted path is the one shown


def test_a_renamed_file_keeps_its_id(shoot):
    inventory.build(say=lambda *_: None)
    cid = rows_by_name()["desk_h.mp4"]["id"]
    (shoot / "B-Roll" / "Desk" / "desk_h.mp4").rename(shoot / "B-Roll" / "Desk" / "renamed.mp4")
    inventory.build(say=lambda *_: None)
    assert rows_by_name()["renamed.mp4"]["id"] == cid


def test_added_date_is_kept_across_runs(shoot):
    inventory.build(say=lambda *_: None)
    inv = store.load_inventory()
    for c in inv["clips"]:
        c["added"] = "2020-02-02"
    common.atomic_write_json(store.f_inventory(), inv)
    inventory.build(say=lambda *_: None)
    assert {c["added"] for c in store.load_inventory()["clips"]} == {"2020-02-02"}
    shutil.copy(shoot / "B-Roll" / "Desk" / "desk_v.mp4", shoot / "new.mp4")
    ffmpeg("-i", shoot / "new.mp4", "-c", "copy", "-metadata", "comment=fresh", shoot / "fresh.mp4")
    (shoot / "new.mp4").unlink()
    inventory.build(say=lambda *_: None)
    assert rows_by_name()["fresh.mp4"]["added"] == store.today()


def test_descriptions_file_fills_notes(shoot):
    inventory.build(say=lambda *_: None)
    cid = rows_by_name()["silent.mp4"]["id"]
    store.f_descriptions().write_text(json.dumps({cid: {"desc": "a desk, soft light", "flags": "shaky, out_of_focus"}}))
    inventory.build(say=lambda *_: None)
    c = rows_by_name()["silent.mp4"]
    assert (c["desc"], c["flags"]) == ("a desk, soft light", "shaky, out_of_focus")


def test_refuses_to_forget_when_the_footage_folder_is_missing(shoot, ws):
    inventory.build(say=lambda *_: None)
    before = store.f_inventory().read_text()
    shutil.move(str(ws / "Footage"), str(ws / "Footage-unplugged"))
    with pytest.raises(SystemExit) as e:
        inventory.build(say=lambda *_: None)
    assert e.value.code == 2 and store.f_inventory().read_text() == before


def test_refuses_to_forget_when_the_footage_folder_is_empty(shoot, ws):
    inventory.build(say=lambda *_: None)
    before = store.f_inventory().read_text()
    shutil.rmtree(ws / "Footage")
    (ws / "Footage").mkdir()
    with pytest.raises(SystemExit) as e:
        inventory.build(say=lambda *_: None)
    assert e.value.code == 2 and store.f_inventory().read_text() == before
    res = inventory.build(allow_empty=True, say=lambda *_: None)    # on purpose
    assert res["clips"] == 0 and len(res["gone_ids"]) == 4


def test_an_empty_workspace_is_fine(ws):
    res = inventory.build(say=lambda *_: None)
    assert res["clips"] == 0


def test_two_roots_keep_their_paths_apart(ws, clips, tmp_path):
    other = tmp_path / "SSD Footage"
    (other / "2026-02-02 Other Shoot" / "B-Roll").mkdir(parents=True)
    shutil.copy(clips["h"], other / "2026-02-02 Other Shoot" / "B-Roll" / "other.mp4")
    (ws / "Footage" / "2026-01-01 A" / "B-Roll").mkdir(parents=True)
    shutil.copy(clips["v"], ws / "Footage" / "2026-01-01 A" / "B-Roll" / "mine.mp4")
    common.save_config({**common.load_config(), "review": {"roots": [str(ws / "Footage"), str(other)]}})
    inventory.build(say=lambda *_: None)
    r = rows_by_name()
    assert r["other.mp4"]["paths"][0]["root"] == str(other) and r["mine.mp4"]["paths"][0]["root"] == str(ws / "Footage")
    assert store.abs_paths(r["other.mp4"]) == [str(other / "2026-02-02 Other Shoot" / "B-Roll" / "other.mp4")]
