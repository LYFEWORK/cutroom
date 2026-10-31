from __future__ import annotations

import json
import math

import pytest

import common
from review import store


def doc(s="keep", r=None, at=1000):
    return {"s": s, "r": r, "at": at}


def test_merge_last_write_wins_by_at(ws):
    cid = "a" * 16
    assert store.merge_decisions({cid: doc("keep", at=100)})["saved"] == [cid]
    res = store.merge_decisions({cid: doc("cut", at=50)})
    assert res["saved"] == [] and cid in res["stale"] and res["stale"][cid]["s"] == "keep"
    assert store.load_decisions()[cid]["s"] == "keep"
    assert store.merge_decisions({cid: doc("cut", at=200)})["saved"] == [cid]
    assert store.load_decisions()[cid]["s"] == "cut"
    assert store.merge_decisions({cid: doc("keep", at=200)})["saved"] == [cid]      # the same instant is the same write: accepted


def test_merge_keeps_keys_the_new_doc_does_not_carry(ws):
    cid = "b" * 16
    store.merge_decisions({cid: {**doc("keep", at=1), "from_id": "c" * 16}})
    store.merge_decisions({cid: doc("keep", [[1.0, 2.0]], at=2)})
    d = store.load_decisions()[cid]
    assert d["from_id"] == "c" * 16 and d["r"] == [[1.0, 2.0]]


def test_drafts_are_hidden_unless_asked_for(ws):
    store.merge_decisions({"a" * 16: doc("none", [[1.0, 2.0]], 1), "b" * 16: doc("keep", None, 1), "c" * 16: doc("cut", None, 1)})
    assert set(store.load_decisions()) == {"b" * 16, "c" * 16}
    assert set(store.load_decisions(drafts=True)) == {"a" * 16, "b" * 16, "c" * 16}


def test_rolling_backup_holds_the_save_before(ws):
    store.merge_decisions({"a" * 16: doc("keep", at=1)})
    assert not (store.f_decisions().with_name("decisions.json.bak")).exists()       # nothing before the first save
    store.merge_decisions({"b" * 16: doc("cut", at=2)})
    bak = json.loads(store.f_decisions().with_name("decisions.json.bak").read_text())["decisions"]
    assert set(bak) == {"a" * 16}
    store.merge_decisions({"c" * 16: doc("cut", at=3)})
    bak = json.loads(store.f_decisions().with_name("decisions.json.bak").read_text())["decisions"]
    assert set(bak) == {"a" * 16, "b" * 16}


def test_every_accepted_change_is_logged(ws):
    store.merge_decisions({"a" * 16: doc("keep", at=1)}, source="page")
    store.merge_decisions({"a" * 16: doc("keep", at=0)}, source="page")           # stale: not logged
    rows = [json.loads(x) for x in store.f_decisions_log().read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["id"] == "a" * 16 and rows[0]["src"] == "page"


def test_file_is_valid_json_one_decision_per_line(ws):
    store.merge_decisions({"a" * 16: doc("keep", at=1), "b" * 16: doc("cut", at=1)})
    text = store.f_decisions().read_text()
    assert json.loads(text)["version"] == 1
    assert len([ln for ln in text.splitlines() if ln.startswith('"')]) == 2


def test_a_damaged_file_is_reported_not_guessed(ws):
    store.f_decisions().parent.mkdir(parents=True, exist_ok=True)
    store.f_decisions().write_text("{not json")
    with pytest.raises(store.StoreError, match="decisions.json"):
        store.load_decisions()


CLIP = {"id": "d" * 16, "dur": 10.0, "rel": "S/B-Roll/x.mp4", "project": "S", "kind": "broll", "name": "x.mp4"}


@pytest.mark.parametrize("cid,raw,why", [
    ("zz", doc(), "not a clip id"),
    ("e" * 16, doc(), "not in the inventory"),                                  # valid id, but the clip is None below
    ("d" * 16, "keep", "an object"),
    ("d" * 16, doc("maybe"), "keep, cut or none"),
    ("d" * 16, {"s": "keep", "r": None}, "milliseconds"),
    ("d" * 16, {"s": "keep", "r": None, "at": -5}, "milliseconds"),
    ("d" * 16, {"s": "keep", "r": None, "at": float("nan")}, "milliseconds"),
    ("d" * 16, {"s": "keep", "r": None, "at": True}, "milliseconds"),
    ("d" * 16, {"s": "keep", "r": None, "at": 10 ** 15}, "future"),
    ("d" * 16, doc("keep", [[2.0, 1.0]]), "outside the clip"),
    ("d" * 16, doc("keep", [[-1.0, 1.0]]), "outside the clip"),
    ("d" * 16, doc("keep", [[0.0, 11.0]]), "outside the clip"),
    ("d" * 16, doc("keep", [[0.0, float("inf")]]), "finite"),
    ("d" * 16, doc("keep", [[0, "2"]]), "finite"),
    ("d" * 16, doc("keep", [[1, 2, 3]]), "[start, end]"),
    ("d" * 16, doc("keep", []), "1 to 64"),
    ("d" * 16, doc("keep", [[0.0, 3.0], [2.0, 4.0]]), "overlap"),
    ("d" * 16, doc("keep", [[i, i + 0.5] for i in range(65)]), "1 to 64"),
])
def test_validate_doc_rejects(cid, raw, why):
    clip = None if cid == "e" * 16 else CLIP
    d, reason = store.validate_doc(cid, raw, clip)
    assert d is None and why in reason


def test_validate_doc_normalizes_and_takes_the_description_from_the_inventory():
    d, why = store.validate_doc("d" * 16, {"s": "keep", "r": [[5.0, 7.123456], [1.0, 2.0]], "at": 12345, "p": "hacked", "b": "hacked"}, CLIP)
    assert why is None
    assert d["r"] == [[1.0, 2.0], [5.0, 7.123]] and d["at"] == 12345
    assert (d["p"], d["b"], d["k"], d["n"]) == ("S/B-Roll/x.mp4", "S", "broll", "x.mp4")
    d, _ = store.validate_doc("d" * 16, doc("keep", [[9.0, 10.3]]), CLIP)           # an end a little past the clip is clamped to it
    assert d["r"] == [[9.0, 10.0]]


def test_validate_doc_accepts_a_draft_trim():
    d, why = store.validate_doc("d" * 16, doc("none", [[1.0, 2.0]], at=5), CLIP)
    assert why is None and d["s"] == "none"


def test_roots_default_and_config(ws):
    assert store.roots() == [ws / "Footage"]
    cfg = {**common.load_config(), "review": {"roots": ["Footage", "Drop", str(ws / "Footage")]}}
    common.save_config(cfg)
    assert store.roots() == [ws / "Footage", ws / "Drop"]                           # relative means inside the workspace, duplicates dropped
    common.save_config({**common.load_config(), "review": {"roots": "/elsewhere"}})
    assert [str(r) for r in store.roots()] == ["/elsewhere"]


def test_proxy_height_and_port_have_sane_bounds(ws):
    assert store.proxy_height() == 360 and store.port() == 8765
    common.save_config({**common.load_config(), "proxy_height": 99999, "review_port": "9001"})
    assert store.proxy_height() == 1080 and store.port() == 9001
    common.save_config({**common.load_config(), "proxy_height": "junk", "review_port": None})
    assert store.proxy_height() == 360 and store.port() == 8765


def test_kept_ranges():
    assert store.kept_ranges({"dur": 8.0}, {"s": "keep", "r": None}) == [[0.0, 8.0]]
    assert store.kept_ranges({"dur": 8.0}, {"s": "keep", "r": [[1, 2], [3, 4]]}) == [[1.0, 2.0], [3.0, 4.0]]
    assert not math.isnan(store.kept_ranges({"dur": 0}, None)[0][1])
