from __future__ import annotations

import common
from review import encode, inventory, posters, store, verify_proxies
from conftest import ffmpeg, ingest_quiet


def top_level_boxes(path) -> list[str]:
    boxes, data = [], open(path, "rb").read()
    i = 0
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8].decode("latin-1")
        boxes.append(kind)
        if size == 1:
            size = int.from_bytes(data[i + 8:i + 16], "big")
        elif size == 0:
            break
        i += max(size, 8)
    return boxes


def proxy_of(clip) -> dict:
    return common.probe(store.d_proxies() / f"{clip['id']}.mp4")


def by_name():
    return {c["name"]: c for c in store.load_inventory()["clips"]}


def test_proxies_are_made_small_and_playable_anywhere(shoot):
    ingest_quiet()
    proxies = store.load_proxies()
    for name, c in by_name().items():
        info = proxy_of(c)
        assert info["codec"] == "h264" and info["pix"] == "yuv420p" and info["fps"] == 24.0, name
        assert info["audio"] is True, name                                         # even a silent source has a track
        assert abs(info["dur"] - c["dur"]) < 0.2, name
        assert min(info["w"], info["h"]) <= 360 and info["vertical"] == (c["h"] >= c["w"]), name
        assert info["w"] % 2 == 0 and info["h"] % 2 == 0
        boxes = top_level_boxes(store.d_proxies() / f"{c['id']}.mp4")
        assert boxes.index("moov") < boxes.index("mdat"), "not faststart: a browser would wait for the whole file"
        entry = proxies[c["id"]]
        assert set(entry) == {"dur", "v", "bytes", "vs", "h", "made"} and entry["h"] == 360
        assert entry["bytes"] == (store.d_proxies() / f"{c['id']}.mp4").stat().st_size


def test_the_short_side_is_proxy_height_and_nothing_is_scaled_up(shoot):
    common.save_config({**common.load_config(), "proxy_height": 120})
    ingest_quiet()
    c = by_name()
    v, h = proxy_of(c["desk_v.mp4"]), proxy_of(c["desk_h.mp4"])
    assert (v["w"], v["h"]) == (120, 212) and (h["w"], h["h"]) == (212, 120)
    common.save_config({**common.load_config(), "proxy_height": 1080})
    ingest_quiet()                                                                    # a changed setting remakes them
    v = proxy_of(by_name()["desk_v.mp4"])
    assert (v["w"], v["h"]) == (216, 384)                                             # the source is smaller than 1080: kept as is


def test_a_silent_source_gets_a_silent_track(shoot):
    ingest_quiet()
    c = by_name()["silent.mp4"]
    assert c["audio"] is False and proxy_of(c)["audio"] is True


def test_the_picture_comes_from_the_probed_stream_not_the_first_one(shoot, tmp_path):
    cover = tmp_path / "cover.jpg"
    ffmpeg("-f", "lavfi", "-i", "color=c=red:size=100x100", "-frames:v", "1", cover)
    out = shoot / "B-Roll" / "Desk" / "with_cover.mp4"
    ffmpeg("-i", cover, "-i", shoot / "B-Roll" / "Desk" / "desk_v.mp4", "-map", "0", "-map", "1", "-c", "copy", out)
    ingest_quiet()
    c = by_name()["with_cover.mp4"]
    info = proxy_of(c)
    assert c["vindex"] == 1
    assert (info["w"], info["h"]) == (216, 384) and abs(info["dur"] - 6.0) < 0.2        # the cover would be 100x100 and a single frame


def test_the_command_maps_the_probed_indexes_never_a_bare_first_stream():
    clip = {"vindex": 3, "aindex": 5, "w": 1920, "h": 1080, "pix": "yuv420p", "audio": True}
    cmd = encode.command(clip, "in.mov", "out.part.mp4", 360, hw=False, silent=False)
    assert "0:3" in cmd and "0:5" in cmd and "0:v:0" not in cmd and "0:a:0" not in cmd
    silent = encode.command({**clip, "audio": False, "aindex": None}, "in.mov", "out.part.mp4", 360, hw=False, silent=True)
    assert "0:3" in silent and "1:a:0" in silent and "anullsrc=r=44100:cl=mono" in silent
    assert "-hwaccel" in encode.command(clip, "in.mov", "o.mp4", 360, hw=True, silent=False)
    assert "-hwaccel" not in cmd


def test_a_sound_track_that_cannot_be_read_does_not_block_the_picture(shoot):
    inventory.build(say=lambda *_: None)
    inv = store.load_inventory()
    for c in inv["clips"]:
        if c["name"] == "desk_v.mp4":
            c["aindex"] = 9                              # no such stream: ffmpeg refuses the map
    common.atomic_write_json(store.f_inventory(), inv)
    res = encode.encode_all(inv["clips"], say=lambda *_: None, hw_enabled=False)
    assert not res["failed"]
    assert proxy_of(by_name()["desk_v.mp4"])["audio"] is True                              # a silent track stands in


def test_second_run_makes_nothing_and_redo_makes_everything(shoot):
    ingest_quiet()
    clips = store.load_inventory()["clips"]
    assert encode.encode_all(clips, say=lambda *_: None)["total"] == 0
    assert encode.encode_all(clips, redo=True, say=lambda *_: None, hw_enabled=False)["total"] == 4


def test_a_clip_that_cannot_be_read_is_reported_and_the_rest_go_on(shoot):
    ingest_quiet()
    inv = store.load_inventory()
    victim = next(c for c in inv["clips"] if c["name"] == "desk_h.mp4")
    (store.d_proxies() / f"{victim['id']}.mp4").unlink()
    (shoot / "B-Roll" / "Desk" / "desk_h.mp4").write_bytes(b"not a video anymore")
    res = encode.encode_all(inv["clips"], say=lambda *_: None, hw_enabled=False)
    assert list(res["failed"]) == [victim["id"]] and not (store.d_proxies() / f"{victim['id']}.part.mp4").exists()


def test_hardware_output_is_trusted_only_when_it_ends_clean():
    assert encode.hw_ok(0, "", 10.0, 10.0)
    assert encode.hw_ok(0, "  \n", 10.02, 10.0)
    assert not encode.hw_ok(0, "hardware accelerator failed to decode picture", 10.0, 10.0)     # exit 0 but frames were dropped
    assert not encode.hw_ok(1, "", 10.0, 10.0)
    assert not encode.hw_ok(0, "", 9.5, 10.0)                                                  # half a second short
    assert encode.sw_ok(0, 10.3, 10.0) and not encode.sw_ok(0, 5.0, 10.0) and not encode.sw_ok(2, 10.0, 10.0)


def test_hardware_decode_is_given_up_after_three_failures(monkeypatch):
    monkeypatch.setattr(encode.sys, "platform", "darwin")
    hw = encode._Hw(True)
    assert hw.ok()
    for _ in range(3):
        hw.failed()
    assert not hw.ok()
    assert not encode._Hw(False).ok()


def test_a_bad_hardware_run_falls_back_to_software(shoot, monkeypatch):
    monkeypatch.setattr(encode.sys, "platform", "darwin")
    real, calls = encode.command, []

    def command(clip, src, tmp, height, hw, silent):
        calls.append(hw)
        if hw:      # exits 0, says nothing useful, writes nothing
            return ["sh", "-c", "echo 'hardware accelerator failed to decode picture' >&2"]
        return real(clip, src, tmp, height, hw, silent)

    monkeypatch.setattr(encode, "command", command)
    inventory.build(say=lambda *_: None)
    res = encode.encode_all(store.load_inventory()["clips"], jobs=1, say=lambda *_: None, hw_enabled=True)
    assert len(res["made"]) == 4 and not res["failed"]
    assert calls.count(True) == 3 and calls.count(False) == 4                                  # three hardware tries, then it stopped trying


def test_prune_removes_derived_files_for_clips_that_are_gone(shoot):
    ingest_quiet()
    inv = store.load_inventory()
    gone = next(c for c in inv["clips"] if c["name"] == "silent.mp4")
    (shoot / "B-Roll" / "Desk" / "silent.mp4").unlink()
    (store.d_proxies() / ("a" * 16 + ".part.mp4")).write_bytes(b"x")                           # a stray half-made file
    inventory.build(say=lambda *_: None)
    n = encode.prune({c["id"] for c in store.load_inventory()["clips"]}, say=lambda *_: None)
    assert n == 4                                                                             # proxy, poster, filmstrip, stray
    assert not (store.d_proxies() / f"{gone['id']}.mp4").exists() and gone["id"] not in store.load_proxies()
    assert len(list(store.d_proxies().glob("*.mp4"))) == 3


def image_size(path) -> str:
    return common.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", path]).stdout.strip()


def test_thumbnails_and_filmstrips(shoot):
    ingest_quiet()
    c = by_name()
    assert image_size(store.d_posters() / f"{c['desk_v.mp4']['id']}.jpg") == "72,128"
    assert image_size(store.d_posters() / f"{c['desk_h.mp4']['id']}.jpg").startswith("144,")
    assert image_size(store.d_strips() / f"{c['desk_v.mp4']['id']}.jpg") == "960,90"          # eight cells of 120 x 90
    assert posters.make_all(store.load_inventory()["clips"], say=lambda *_: None) == {"posters": 0, "strips": 0, "failed": []}     # idempotent


def test_filmstrips_are_optional(shoot):
    inventory.build(say=lambda *_: None)
    encode.encode_all(store.load_inventory()["clips"], say=lambda *_: None)
    res = posters.make_all(store.load_inventory()["clips"], strips=False, say=lambda *_: None)
    assert res["posters"] == 4 and res["strips"] == 0 and not store.d_strips().exists()


def test_previews_match_their_originals(shoot):
    ingest_quiet()
    assert verify_proxies.verify(store.load_inventory()["clips"], say=lambda *_: None) == []


def test_a_preview_of_the_wrong_picture_is_flagged(shoot, tmp_path):
    ingest_quiet()
    victim = by_name()["desk_v.mp4"]
    ffmpeg("-f", "lavfi", "-i", "color=c=white:size=216x384:rate=24:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", tmp_path / "white.mp4")
    (store.d_proxies() / f"{victim['id']}.mp4").write_bytes((tmp_path / "white.mp4").read_bytes())
    said = []
    bad = verify_proxies.verify(store.load_inventory()["clips"], say=said.append)
    assert [b["id"] for b in bad] == [victim["id"]] and "WARN" in said[0] and "desk_v.mp4" in said[0]
    assert verify_proxies.main([]) == 1
