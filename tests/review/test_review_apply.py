from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import common
from review import apply, inventory, store
from conftest import HAVE_X265, SHOOT, clip_by_name, decide, make_clip


def quiet(*_):
    pass


@pytest.fixture()
def lib(shoot, clips, fake_trash):
    """The shoot, ingested. desk_v is 6 s with a keyframe every second; common.trash is replaced so nothing reaches the real Trash."""
    if HAVE_X265:
        shutil.copy(clips["hevc"], shoot / "B-Roll" / "Desk" / "hevc.mov")
    inventory.build(say=quiet)
    return shoot


def orig_path(name) -> Path:
    return Path(store.abs_paths(clip_by_name(name))[0])


def plan_of(**kw):
    return apply.make_plan(say=quiet, **kw)


def go(**kw):
    return apply.go(refresh=False, say=quiet, **kw)


def only_step(plan):
    assert len(plan["steps"]) == 1, plan
    return plan["steps"][0]


HALF_FRAME = 0.5 / 24      # the test clips are 24 fps: sampling mid-frame keeps a frame boundary from deciding the result


def picture_gap(src, t_src, part, t_part):
    return apply.gap(apply.gray(src, t_src + HALF_FRAME, "0:0"), apply.gray(part, t_part + HALF_FRAME, "0:v:0"))


# ---------------------------------------------------------------- the plan

def test_what_is_and_is_not_planned(lib):
    decide(clip_by_name("desk_v.mp4")["id"], "keep")                                    # kept whole: nothing to do
    decide(clip_by_name("desk_h.mp4")["id"], "cut")
    decide(clip_by_name("silent.mp4")["id"], "none", [[1.0, 2.0]])                      # a draft is not a decision
    plan = plan_of()                                                                    # take1 is not reviewed at all
    assert [(s["do"], s["rel"].split("/")[-1]) for s in plan["steps"]] == [("trash", "desk_h.mp4")]
    assert json.loads(store.f_plan().read_text())["steps"][0]["id"] == clip_by_name("desk_h.mp4")["id"]


def test_a_trim_is_planned_with_the_keyframe_it_will_start_on(lib):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    step = only_step(plan_of())
    assert step["do"] == "trim" and step["keep_s"] == 2.5
    assert step["parts"][0]["a"] == 1.5 and step["parts"][0]["b"] == 4.0 and abs(step["parts"][0]["kf"] - 1.0) < 0.05
    assert step["parts"][0]["out"].endswith("B-Roll/Desk/desk_v - Trimmed.mp4")
    assert (step["size"], step["mtime"]) == (orig_path("desk_v.mp4").stat().st_size, int(orig_path("desk_v.mp4").stat().st_mtime))


@pytest.mark.parametrize("r,planned", [
    ([[0.0, 6.0]], False),             # the whole clip
    ([[0.2, 5.9]], False),             # a stream copy could take off under a second: stays a kept range
    ([[0.0, 4.0]], True),              # two seconds off the end
    ([[2.5, 6.0]], True),              # keyframe at 2.0, so a second and a half off the front
    ([[0.5, 1.5], [3.5, 5.5]], True),  # several parts are always a real split
])
def test_only_trims_that_remove_enough_are_planned(lib, r, planned):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", r)
    plan = plan_of()
    assert bool(plan["steps"]) is planned
    if not planned:
        assert "stream copy could only remove" in plan["skipped"][0]["why"] or "whole" in plan["skipped"][0]["why"]


def test_several_parts_each_get_their_own_file(lib):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[0.5, 1.8], [3.2, 5.0]])
    step = only_step(plan_of())
    assert [p["out"].split("/")[-1] for p in step["parts"]] == ["desk_v - Part 1.mp4", "desk_v - Part 2.mp4"]


def test_a_name_already_taken_gets_a_number(lib):
    (orig_path("desk_v.mp4").with_name("desk_v - Trimmed.mp4")).write_bytes(b"already here")
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    assert only_step(plan_of())["parts"][0]["out"].endswith("desk_v - Trimmed 2.mp4")


def test_a_copy_of_a_clip_gets_the_same_treatment(lib):
    (lib / "B-Roll" / "Copy").mkdir()
    shutil.copy(orig_path("desk_h.mp4"), lib / "B-Roll" / "Copy" / "desk_h copy.mp4")
    inventory.build(say=quiet)
    both = next(c for c in store.load_inventory()["clips"] if len(c["paths"]) == 2)
    decide(both["id"], "cut")
    assert sorted(s["rel"].split("/")[-1] for s in plan_of()["steps"]) == ["desk_h copy.mp4", "desk_h.mp4"]


def test_the_plan_can_be_limited_to_one_shoot(lib, ws, clips):
    other = ws / "Footage" / "2026-02-02 Other"
    (other / "B-Roll").mkdir(parents=True)
    make_clip(other / "B-Roll" / "o.mp4", "216x384", 3, freq=999)
    inventory.build(say=quiet)
    decide(clip_by_name("o.mp4")["id"], "cut")
    decide(clip_by_name("silent.mp4")["id"], "cut")
    assert [s["rel"].split("/")[-1] for s in plan_of(shoot="2026-02-02 other")["steps"]] == ["o.mp4"]
    assert len(plan_of()["steps"]) == 2


def test_a_file_changed_since_the_last_ingest_is_left_out_of_the_plan(lib):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    p = orig_path("silent.mp4")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 100))
    plan = plan_of()
    assert plan["steps"] == [] and "changed since the last ingest" in plan["skipped"][0]["why"]


def test_the_plan_warns_about_clips_a_project_names(lib, ws):
    proj = ws / "Projects" / "demo"
    proj.mkdir(parents=True)
    (proj / "edit.json").write_text(json.dumps({"layers": [{"src": "footage/silent.mp4"}]}))
    decide(clip_by_name("silent.mp4")["id"], "cut")
    said = []
    apply.make_plan(say=said.append)
    text = "\n".join(said)
    assert "HEADS UP" in text and "edit.json" in text and "TRASH" in text


def test_the_plan_says_when_there_is_nothing_to_do(lib):
    said = []
    apply.make_plan(say=said.append)
    assert "Nothing to apply" in said[0]


# ---------------------------------------------------------------- go: the happy paths

def test_a_trim_becomes_a_verified_part_and_the_original_goes_to_the_trash(lib, fake_trash):
    v = clip_by_name("desk_v.mp4")
    orig = orig_path("desk_v.mp4")
    decide(v["id"], "keep", [[1.5, 4.0]])
    plan_of()
    res = go()
    part = orig.with_name("desk_v - Trimmed.mp4")
    assert [r["result"] for r in res["results"]] == ["done"]
    assert part.exists() and not orig.exists() and fake_trash.calls == [str(orig)]
    assert apply.decode_clean(part) == (True, "")
    assert not list(orig.parent.glob(".*cutroom-part*")), "no half-made file is left behind"
    new_id = common.fingerprint(part)
    d = store.load_decisions()[new_id]
    assert (d["s"], d["from_id"], d["n"], d["b"], d["k"]) == ("keep", v["id"], part.name, SHOOT, "broll") and d["p"].endswith("B-Roll/Desk/desk_v - Trimmed.mp4")
    head, end = d["r"][0]
    assert 0.45 < head < 0.55 and 2.9 < end < 3.1                                          # 1.5 s in the original, 1 s of lead, 4.0 s ends at 3.0
    original = fake_trash.dir / "desk_v.mp4"
    for t in (1.5, 2.5, 3.9):                                                              # the saved lead puts the part on the original's picture
        assert picture_gap(original, t, part, t + (head - 1.5)) < 1.0, t
    assert abs(common.probe(part)["dur"] - 3.0) < 0.3


def test_a_cut_clip_goes_to_the_trash_through_common_trash(lib, fake_trash):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    p = orig_path("silent.mp4")
    plan_of()
    res = go()
    assert res["results"][0]["result"] == "done" and not p.exists() and fake_trash.calls == [str(p)] and (fake_trash.dir / "silent.mp4").exists()


def test_several_parts_are_made_and_each_is_checked(lib, fake_trash):
    v = clip_by_name("desk_v.mp4")
    orig = orig_path("desk_v.mp4")
    decide(v["id"], "keep", [[0.5, 1.8], [3.2, 5.0]])
    plan_of()
    res = go()
    assert res["results"][0]["result"] == "done" and not orig.exists()
    parts = [orig.with_name("desk_v - Part 1.mp4"), orig.with_name("desk_v - Part 2.mp4")]
    decs = store.load_decisions()
    for part, (a, b) in zip(parts, [(0.5, 1.8), (3.2, 5.0)]):
        assert apply.decode_clean(part)[0]
        d = decs[common.fingerprint(part)]
        head = d["r"][0][0] if d["r"] else 0.0
        assert picture_gap(fake_trash.dir / "desk_v.mp4", a + 0.2, part, a + 0.2 + head - a) < 1.0
        assert d["from_id"] == v["id"]


@pytest.mark.skipif(not HAVE_X265, reason="no libx265 in this ffmpeg")
def test_hevc_parts_open_in_quicktime_and_safari(lib, fake_trash):
    decide(clip_by_name("hevc.mov")["id"], "keep", [[2.2, 4.5]])
    plan_of()
    go()
    part = orig_path("desk_v.mp4").with_name("hevc - Trimmed.mov")
    assert part.exists() and apply.decode_clean(part)[0]
    tag = common.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_tag_string,codec_name", "-of", "csv=p=0", part]).stdout.strip()
    assert tag == "hevc,hvc1"                                                                # without the tag QuickTime will not open it
    head = store.load_decisions()[common.fingerprint(part)]["r"][0][0]
    assert picture_gap(fake_trash.dir / "hevc.mov", 2.2, part, head) < 3.0


def test_mp4_parts_are_faststart(lib):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    plan_of()
    go()
    data = orig_path("desk_v.mp4").with_name("desk_v - Trimmed.mp4").read_bytes()
    assert 0 <= data.find(b"moov") < data.find(b"mdat")


def test_a_part_that_starts_on_a_keyframe_needs_no_lead(lib, fake_trash):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[2.0, 4.0]])
    plan_of()
    go()
    d = store.load_decisions()[common.fingerprint(orig_path("desk_v.mp4").with_name("desk_v - Trimmed.mp4"))]
    assert d["r"] is None                                                                  # kept whole: it starts exactly where it was set


def test_the_new_part_is_known_to_the_review_after_a_refresh(lib, fake_trash):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    apply.go(say=quiet)                                                                    # with the refresh
    names = {c["name"] for c in store.load_inventory()["clips"]}
    assert names == {"desk_h.mp4", "take1.mp4", "desk_v - Trimmed.mp4"} | ({"hevc.mov"} if HAVE_X265 else set())
    part = next(c for c in store.load_inventory()["clips"] if c["name"].endswith("Trimmed.mp4"))
    assert (store.d_proxies() / f"{part['id']}.mp4").exists()
    from review import approved
    assert approved.check(store.abs_paths(part)[0])[0] == 0                                # kept, and enforceable at once
    assert part["id"] in store.load_decisions()


def test_every_step_is_logged(lib):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    go()
    rows = [json.loads(x) for x in store.f_apply_log().read_text().splitlines()]
    assert rows[0]["do"] == "trash" and rows[0]["result"] == "done" and rows[0]["t"]


def test_the_plan_is_marked_as_run_and_will_not_run_twice(lib):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    go()
    saved = json.loads(store.f_plan().read_text())
    assert saved["ran"] and saved["results"][0]["result"] == "done"
    with pytest.raises(SystemExit) as e:
        go()
    assert e.value.code == 2


def test_go_without_a_plan_stops(lib):
    with pytest.raises(SystemExit) as e:
        go()
    assert e.value.code == 2


def test_sidecar_notes_only_when_asked_for(lib):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    plan_of()
    go()
    assert not list(lib.rglob("*.txt"))
    decide(clip_by_name("desk_h.mp4")["id"], "keep", [[1.2, 4.0]])
    plan_of()
    go(sidecars=True)
    note = (orig_path("desk_h.mp4").parent / "desk_h - Trimmed.txt").read_text()
    assert "TRIMMED FROM  desk_h.mp4" in note and "STARTS EARLY" in note


def test_a_mac_stub_file_beside_a_new_part_is_removed(lib):
    stub = orig_path("desk_v.mp4").with_name("._desk_v - Trimmed.mp4")
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    plan_of()
    stub.write_bytes(b"stub")
    go()
    assert not stub.exists()


def test_files_beside_a_clip_are_never_touched(lib):
    note = orig_path("silent.mp4").with_suffix(".txt")
    note.write_text("transcript")
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    go()
    assert note.read_text() == "transcript"


def test_the_real_common_trash_falls_back_to_a_holding_folder(shoot, ws, monkeypatch):
    """Without the fake: no /usr/bin/trash means common.trash moves the original into Review/trash/<date>/."""
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda p: False if str(p) == "/usr/bin/trash" else real_exists(p))
    inventory.build(say=quiet)
    p = orig_path("silent.mp4")
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    go()
    held = list((ws / "Review" / "trash").rglob("silent.mp4"))
    assert not p.exists() and len(held) == 1


# ---------------------------------------------------------------- go: nothing is trusted

def test_a_file_changed_since_the_plan_is_skipped(lib, fake_trash):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    p = orig_path("silent.mp4")
    with open(p, "ab") as f:
        f.write(b"\0")
    res = go()
    assert res["results"][0]["result"] == "skipped" and "changed since the plan" in res["results"][0]["detail"]
    assert p.exists() and fake_trash.calls == []


def test_a_file_with_the_same_size_but_other_content_is_skipped(lib, fake_trash):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    p = orig_path("silent.mp4")
    st = p.stat()
    data = bytearray(p.read_bytes())
    data[100] ^= 0xFF                                       # one flipped byte in the first 512 KiB, same size
    p.write_bytes(bytes(data))
    os.utime(p, (st.st_atime, st.st_mtime))                 # and the same modification time
    res = go()
    assert res["results"][0]["result"] == "skipped" and "content" in res["results"][0]["detail"] and p.exists()


def test_a_decision_changed_since_the_plan_is_skipped(lib, fake_trash):
    s = clip_by_name("silent.mp4")
    decide(s["id"], "cut")
    plan_of()
    decide(s["id"], "keep")                                 # the user changed their mind before saying go
    res = go()
    assert res["results"][0]["result"] == "skipped" and "decision changed" in res["results"][0]["detail"]
    assert orig_path("silent.mp4").exists() and fake_trash.calls == []


def test_a_trim_changed_since_the_plan_is_skipped(lib, fake_trash):
    v = clip_by_name("desk_v.mp4")
    decide(v["id"], "keep", [[1.5, 4.0]])
    plan_of()
    decide(v["id"], "keep", [[2.5, 4.0]], at=store.now_ms() + 10)
    res = go()
    assert res["results"][0]["result"] == "skipped" and orig_path("desk_v.mp4").exists()


def test_a_missing_file_is_skipped_quietly(lib):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    orig_path("silent.mp4").unlink()
    assert go()["results"][0] == {"rel": f"{SHOOT}/B-Roll/Desk/silent.mp4", "do": "trash", "result": "skipped", "detail": "already gone"}


def _failing(monkeypatch, name, value):
    monkeypatch.setattr(apply, name, lambda *a, **k: value)


@pytest.mark.parametrize("hook,value,why", [
    ("decode_clean", (False, "Invalid data found when processing input"), "does not play through cleanly"),
    ("verify_cut", (False, "the part's picture does not match the original"), "does not match"),
    ("align_offset", None, "could not line the part up"),
])
def test_a_part_that_fails_a_check_leaves_the_original_alone(lib, fake_trash, monkeypatch, hook, value, why):
    v = clip_by_name("desk_v.mp4")
    orig = orig_path("desk_v.mp4")
    before = orig.read_bytes()
    decide(v["id"], "keep", [[0.5, 1.8], [3.2, 5.0]])
    plan_of()
    _failing(monkeypatch, hook, value)
    res = go()
    assert res["results"][0]["result"] == "failed" and why in res["results"][0]["detail"]
    assert orig.read_bytes() == before and fake_trash.calls == []
    assert not [x for x in orig.parent.iterdir() if "Part" in x.name or "Trimmed" in x.name or "cutroom-part" in x.name]
    assert set(store.load_decisions()) == {v["id"]}                                       # no part was recorded


def test_a_second_part_failing_removes_the_first_as_well(lib, fake_trash, monkeypatch):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[0.5, 1.8], [3.2, 5.0]])
    plan_of()
    real, calls = apply.verify_cut, []

    def second_fails(*a, **k):
        calls.append(1)
        return (False, "mismatch") if len(calls) == 2 else real(*a, **k)

    monkeypatch.setattr(apply, "verify_cut", second_fails)
    res = go()
    assert res["results"][0]["result"] == "failed" and len(calls) == 2
    assert orig_path("desk_v.mp4").exists() and not [x for x in orig_path("desk_v.mp4").parent.iterdir() if "Part" in x.name or "cutroom-part" in x.name]


def test_ffmpeg_failing_to_cut_leaves_the_original_alone(lib, fake_trash, monkeypatch):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    plan_of()
    monkeypatch.setattr(apply, "cut_command", lambda *a, **k: ["sh", "-c", "echo boom >&2; exit 3"])
    res = go()
    assert res["results"][0]["result"] == "failed" and "boom" in res["results"][0]["detail"] and orig_path("desk_v.mp4").exists()


def test_when_the_trash_refuses_the_new_parts_are_taken_back(lib, monkeypatch):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    plan_of()
    monkeypatch.setattr(common, "trash", lambda p: "trash")                               # says yes, moves nothing
    orig = orig_path("desk_v.mp4")
    res = go()
    assert res["results"][0]["result"] == "failed" and "could not be moved to the Trash" in res["results"][0]["detail"]
    assert orig.exists() and not orig.with_name("desk_v - Trimmed.mp4").exists()
    assert set(store.load_decisions()) == {clip_by_name("desk_v.mp4")["id"]}


def test_a_trash_that_raises_is_a_failure_that_keeps_the_file(lib, monkeypatch):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()

    def boom(p):
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(common, "trash", boom)
    res = go()
    assert res["results"][0]["result"] == "failed" and "operation not permitted" in res["results"][0]["detail"] and orig_path("silent.mp4").exists()


def test_a_trash_that_moves_the_file_then_complains_still_counts_as_done(lib, monkeypatch, tmp_path):
    v = clip_by_name("desk_v.mp4")
    decide(v["id"], "keep", [[1.5, 4.0]])
    plan_of()
    orig = orig_path("desk_v.mp4")

    def moves_then_raises(p):
        shutil.move(str(p), str(tmp_path / "gone.mp4"))
        raise OSError("could not tidy up")

    monkeypatch.setattr(common, "trash", moves_then_raises)
    res = go()
    assert res["results"][0]["result"] == "done" and not orig.exists()
    assert orig.with_name("desk_v - Trimmed.mp4").exists()                                # the parts stay: the original really is gone


def test_a_name_taken_after_the_plan_stops_that_step_only(lib, fake_trash):
    decide(clip_by_name("desk_v.mp4")["id"], "keep", [[1.5, 4.0]])
    decide(clip_by_name("silent.mp4")["id"], "cut")
    plan_of()
    taken = orig_path("desk_v.mp4").with_name("desk_v - Trimmed.mp4")
    taken.write_bytes(b"someone else's file")
    res = go()
    got = {r["rel"].split("/")[-1]: r for r in res["results"]}
    assert got["desk_v.mp4"]["result"] == "failed" and "already there" in got["desk_v.mp4"]["detail"]
    assert got["silent.mp4"]["result"] == "done"                                          # one bad step does not stop the rest
    assert taken.read_bytes() == b"someone else's file" and orig_path("desk_v.mp4").exists()


def test_the_command_exit_code_says_whether_everything_was_done(lib, fake_trash, capsys):
    decide(clip_by_name("silent.mp4")["id"], "cut")
    assert apply.main(["plan"]) == 0
    assert apply.main(["go", "--no-refresh"]) == 0
    assert "DONE: 1 of 1 steps" in capsys.readouterr().out
    decide(clip_by_name("desk_h.mp4")["id"], "cut")
    apply.main(["plan"])
    decide(clip_by_name("desk_h.mp4")["id"], "keep")
    assert apply.main(["go", "--no-refresh"]) == 1


# ---------------------------------------------------------------- the pieces that make a trim trustworthy

def test_only_sound_ffmpeg_can_decode_is_copied():
    streams = [{"index": 1, "codec_type": "audio", "codec_name": "aac"}, {"index": 2, "codec_type": "audio", "codec_name": "apple_apac"},
               {"index": 3, "codec_type": "audio", "codec_name": "pcm_s16be"}, {"index": 4, "codec_type": "audio", "codec_name": "no_such_codec_xyz"},
               {"index": 0, "codec_type": "video", "codec_name": "hevc"}, {"index": 5, "codec_type": "data", "codec_name": "none"}]
    assert apply.copyable_audio(streams) == [1, 3]


def test_the_cut_command_tags_hevc_and_only_asks_mp4_for_faststart():
    mov = apply.cut_command("in.mov", 2, [3, 4], "hevc", 1.5, 2.5, Path("/x/out.mov"))
    assert mov[mov.index("-tag:v") + 1] == "hvc1" and "+faststart" in mov and mov.count("-map") == 3 and "0:2" in mov and "0:v:0" not in mov
    mkv = apply.cut_command("in.mkv", 0, [1], "hevc", 1.5, 2.5, Path("/x/out.mkv"))
    assert "-tag:v" not in mkv and "-movflags" not in mkv
    h264 = apply.cut_command("in.mp4", 0, [1], "h264", 0, 3, Path("/x/out.mp4"))
    assert "-tag:v" not in h264 and h264[h264.index("-ss") + 1] == "0.000000" and h264[-1] == "/x/out.mp4"


@pytest.mark.parametrize("hevc", [False, True])
@pytest.mark.parametrize("a,b", [(0.3, 2.0), (1.5, 4.0), (2.0, 3.5), (3.7, 5.9)])
def test_the_measured_lead_puts_every_frame_of_the_part_on_the_original(lib, tmp_path, hevc, a, b):
    if hevc and not HAVE_X265:
        pytest.skip("no libx265")
    src = orig_path("hevc.mov" if hevc else "desk_v.mp4")
    clip = clip_by_name(src.name)
    part = tmp_path / ("part.mov" if hevc else "part.mp4")
    audio = apply.copyable_audio(apply.streams_of(src))
    r = common.run(apply.cut_command(src, clip["vindex"], audio, clip["codec"], a, b - a, part), check=False)
    assert r.returncode == 0
    offset, first, hits = apply.align_offset(src, clip["vindex"], part, a)
    assert hits == 1 and first <= a + 0.001                                               # it started at or before the chosen start
    for t in (a, (a + b) / 2, b - 0.2):
        assert picture_gap(src, t, part, t + offset) < 1.0, (t, offset)
    assert apply.verify_cut(src, clip["vindex"], part, a, b, offset, clip["fps"])[0]
    assert not apply.verify_cut(src, clip["vindex"], part, a, b, offset + 0.5, clip["fps"])[0]     # and a lead that is wrong by half a second is caught


def test_the_decode_check_catches_a_damaged_file_and_passes_a_good_one(lib, tmp_path):
    good = orig_path("desk_v.mp4")
    assert apply.decode_clean(good) == (True, "")
    data = bytearray(good.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 4000] = os.urandom(4000)
    bad = tmp_path / "damaged.mp4"
    bad.write_bytes(bytes(data))
    ok, why = apply.decode_clean(bad)
    assert not ok and why


def test_keyframe_estimate_widens_until_it_finds_one(lib):
    p = orig_path("desk_v.mp4")
    vindex = clip_by_name("desk_v.mp4")["vindex"]
    assert apply.keyframe_estimate(p, vindex, 0.0) == 0.0
    assert abs(apply.keyframe_estimate(p, vindex, 3.4) - 3.0) < 0.05
    assert abs(apply.keyframe_estimate(p, vindex, 3.0) - 3.0) < 0.05
