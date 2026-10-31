from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

import common
from review import build_site, serve, store
from conftest import SHOOT, ingest_quiet, make_clip, make_shoot


class Client:
    def __init__(self, port):
        self.port = port

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def decide(self, docs, headers=None, raw=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        status, hdr, body = self.req("POST", "/api/decide", body=raw if raw is not None else json.dumps({"docs": docs}), headers=h)
        return status, (json.loads(body) if body else None)


def _start(ws_root, clips_dict):
    make_shoot(ws_root, clips_dict)
    ingest_quiet()
    server = serve.make_server(port=0)
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    c = Client(server.server_port)
    c.clips = {x["name"]: x for x in store.load_inventory()["clips"]}
    return server, t, c


def _stop(server, t):
    server.shutdown()
    server.server_close()
    t.join(5)


@pytest.fixture(scope="module")
def served(tmp_path_factory, clips):
    """One workspace, one ingest and one server for the whole module. Tests that change footage use `fresh` instead."""
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("served")
    (tmp / "home").mkdir()
    mp.setattr(common, "CONF_DIR", tmp / "home")
    mp.setattr(common, "CONF", tmp / "home" / "config.json")
    mp.setenv("CUTROOM_HOME", str(tmp / "home"))
    root = common.make_workspace(tmp / "ws")
    common.save_config({**common.DEFAULTS, "workspace": str(root)})
    server, t, c = _start(root, clips)
    yield c
    _stop(server, t)
    mp.undo()


@pytest.fixture()
def srv(served):
    for name in ("decisions.json", "decisions.json.bak", "decisions.log.jsonl"):
        (store.home() / name).unlink(missing_ok=True)
    return served


@pytest.fixture()
def fresh(ws, clips):
    server, t, c = _start(ws, clips)
    c.shoot = ws / "Footage" / SHOOT
    yield c
    _stop(server, t)


def proxy_path(c, name="desk_v.mp4"):
    cid = c.clips[name]["id"]
    return f"/proxies/{cid}.mp4", store.d_proxies() / f"{cid}.mp4"


# ---------------------------------------------------------------- the page and the files

def test_index_and_data_are_served(srv):
    status, h, body = srv.get("/")
    assert status == 200 and h["content-type"].startswith("text/html") and body.startswith(b"<!doctype html>") and h["cache-control"] == "no-store"
    assert srv.get("/index.html")[0] == 200
    status, h, body = srv.get("/data.js?v=123")
    assert status == 200 and h["content-type"].startswith("text/javascript") and body.startswith(b"window.REVIEW_DATA=")
    assert srv.get("/favicon.ico")[0] == 204


def test_a_missing_page_says_what_to_do(srv):
    (store.d_site() / "index.html").unlink()
    try:
        status, _, body = srv.get("/")
        assert status == 404 and b"Run ingest" in body
    finally:
        build_site.build(say=lambda *_: None)


def test_whole_file_without_a_range(srv):
    url, f = proxy_path(srv)
    status, h, body = srv.get(url)
    assert status == 200 and h["content-type"] == "video/mp4" and h["accept-ranges"] == "bytes"
    assert int(h["content-length"]) == f.stat().st_size and body == f.read_bytes() and "etag" in h


@pytest.mark.parametrize("rng,start,end", [
    ("bytes=0-1", 0, 1),                  # the probe Safari sends first
    ("bytes=0-", 0, None),
    ("bytes=1000-", 1000, None),
    ("bytes=10-19", 10, 19),
    ("bytes=-100", None, None),           # the last 100 bytes
    ("bytes=5-999999999", 5, None),       # an end past the file is clipped to it
    ("bytes = 3 - 6", 3, 6),
])
def test_byte_ranges_answer_206_with_the_right_bytes(srv, rng, start, end):
    url, f = proxy_path(srv)
    data = f.read_bytes()
    size = len(data)
    if rng == "bytes=-100":
        start, end = size - 100, size - 1
    end = size - 1 if end is None else end
    status, h, body = srv.get(url, headers={"Range": rng})
    assert status == 206
    assert h["content-range"] == f"bytes {start}-{end}/{size}"
    assert int(h["content-length"]) == end - start + 1 and body == data[start:end + 1]
    assert h["accept-ranges"] == "bytes" and h["content-type"] == "video/mp4"


@pytest.mark.parametrize("rng", ["bytes=99999999-", "bytes=-0", "bytes=99999999-100000000"])
def test_an_unsatisfiable_range_is_416(srv, rng):
    url, f = proxy_path(srv)
    status, h, _ = srv.get(url, headers={"Range": rng})
    assert status == 416 and h["content-range"] == f"bytes */{f.stat().st_size}"


@pytest.mark.parametrize("rng", ["bytes=0-1,5-6", "items=0-1", "bytes=9-3", "garbage", "bytes=-"])
def test_a_range_it_will_not_honour_gets_the_whole_file(srv, rng):
    url, f = proxy_path(srv)
    status, _, body = srv.get(url, headers={"Range": rng})
    assert status == 200 and body == f.read_bytes()


def test_if_range_with_a_stale_validator_sends_everything(srv):
    url, f = proxy_path(srv)
    _, h, _ = srv.get(url)
    status, _, body = srv.get(url, headers={"Range": "bytes=0-1", "If-Range": h["etag"]})
    assert status == 206 and len(body) == 2
    status, _, body = srv.get(url, headers={"Range": "bytes=0-1", "If-Range": '"stale"'})
    assert status == 200 and body == f.read_bytes()


def test_head_and_conditional_requests(srv):
    url, f = proxy_path(srv)
    status, h, body = srv.req("HEAD", url)
    assert status == 200 and body == b"" and int(h["content-length"]) == f.stat().st_size
    status, h, body = srv.req("HEAD", url, headers={"Range": "bytes=0-9"})
    assert status == 206 and body == b"" and h["content-range"].startswith("bytes 0-9/")
    _, h, _ = srv.get(url)
    status, _, body = srv.get(url, headers={"If-None-Match": h["etag"]})
    assert status == 304 and body == b""


def test_posters_and_filmstrips_are_served_as_images(srv):
    cid = srv.clips["desk_v.mp4"]["id"]
    for kind in ("posters", "filmstrips"):
        status, h, body = srv.get(f"/{kind}/{cid}.jpg")
        assert status == 200 and h["content-type"] == "image/jpeg" and body[:2] == b"\xff\xd8"


def test_the_connection_survives_many_requests(srv):
    c = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    url, f = proxy_path(srv)
    for i in range(5):
        c.request("GET", url, headers={"Range": f"bytes={i}-{i + 3}"})
        r = c.getresponse()
        assert r.status == 206 and len(r.read()) == 4
    c.close()


@pytest.mark.parametrize("path", [
    "/proxies/../inventory.json", "/proxies/%2e%2e/inventory.json", "/proxies/..%2finventory.json", "/../inventory.json", "/inventory.json",
    "/decisions.json", "/site/index.html", "/proxies/", "/proxies", "/proxies/zzzz.mp4", "/proxies/ABCDEF0123456789.mp4",
    "/proxies/" + "a" * 16 + ".mp4", "/proxies/" + "a" * 16 + ".jpg", "/posters/" + "a" * 16 + ".mp4", "/api/nothing", "/etc/passwd",
])
def test_only_exact_paths_are_served(srv, path):
    assert srv.get(path)[0] in (400, 404)


def test_a_poster_path_cannot_ask_for_a_video_or_the_other_way_round(srv):
    cid = srv.clips["desk_v.mp4"]["id"]
    assert srv.get(f"/posters/{cid}.mp4")[0] == 404 and srv.get(f"/proxies/{cid}.jpg")[0] == 404


# ---------------------------------------------------------------- saving decisions

def test_a_decision_is_saved_and_read_back(srv):
    cid = srv.clips["desk_v.mp4"]["id"]
    at = store.now_ms()
    status, res = srv.decide({cid: {"s": "keep", "r": [[1.0, 3.5]], "at": at}})
    assert status == 200 and res["saved"] == 1 and res["rejected"] == {}
    saved = store.load_decisions()[cid]
    assert saved["s"] == "keep" and saved["r"] == [[1.0, 3.5]] and saved["at"] == at and saved["n"] == "desk_v.mp4"
    status, _, body = srv.get("/api/decisions")
    assert status == 200 and json.loads(body)["decisions"][cid]["r"] == [[1.0, 3.5]]
    assert len(store.f_decisions_log().read_text().splitlines()) == 1


def test_drafts_are_kept_for_the_page_but_hidden_from_enforcement(srv):
    cid = srv.clips["desk_h.mp4"]["id"]
    assert srv.decide({cid: {"s": "none", "r": [[1.0, 2.0]], "at": store.now_ms()}})[0] == 200
    assert cid not in store.load_decisions() and cid in store.load_decisions(drafts=True)
    assert cid in json.loads(srv.get("/api/decisions")[2])["decisions"]


def test_a_batch_is_one_request(srv):
    ids = [c["id"] for c in srv.clips.values()]
    status, res = srv.decide({i: {"s": "cut", "r": None, "at": store.now_ms()} for i in ids})
    assert status == 200 and res["saved"] == 4 and len(store.load_decisions()) == 4


@pytest.mark.parametrize("doc,why", [
    ({"s": "maybe", "r": None, "at": 1}, "keep, cut or none"),
    ({"s": "keep", "r": [[2, 1]], "at": 1}, "outside the clip"),
    ({"s": "keep", "r": [[0, 999]], "at": 1}, "outside the clip"),
    ({"s": "keep", "r": [[0, 1], [0.5, 2]], "at": 1}, "overlap"),
    ({"s": "keep", "r": None, "at": "now"}, "milliseconds"),
    ({"s": "keep", "r": None, "at": 10 ** 15}, "future"),
    ("keep", "an object"),
])
def test_a_bad_decision_is_rejected_and_nothing_is_saved(srv, doc, why):
    cid = srv.clips["desk_v.mp4"]["id"]
    status, res = srv.decide({cid: doc})
    assert status == 400 and why in res["rejected"][cid] and res["saved"] == 0
    assert store.load_decisions(drafts=True) == {}


def test_an_id_that_is_not_in_the_inventory_is_rejected(srv):
    status, res = srv.decide({"f" * 16: {"s": "keep", "r": None, "at": store.now_ms()}, "not-an-id": {"s": "keep", "r": None, "at": 1}})
    assert status == 400 and set(res["rejected"]) == {"f" * 16, "not-an-id"}
    assert "inventory" in res["rejected"]["f" * 16] and "clip id" in res["rejected"]["not-an-id"]
    assert not store.f_decisions().exists()


def test_a_good_decision_goes_through_next_to_a_bad_one(srv):
    good, bad = srv.clips["desk_v.mp4"]["id"], srv.clips["desk_h.mp4"]["id"]
    status, res = srv.decide({good: {"s": "keep", "r": None, "at": store.now_ms()}, bad: {"s": "zzz", "r": None, "at": 1}})
    assert status == 200 and res["saved"] == 1 and list(res["rejected"]) == [bad]
    assert list(store.load_decisions()) == [good]


def test_last_write_wins_by_at_and_the_page_is_told_what_won(srv):
    cid = srv.clips["desk_v.mp4"]["id"]
    now = store.now_ms()
    assert srv.decide({cid: {"s": "keep", "r": None, "at": now}})[1]["saved"] == 1
    status, res = srv.decide({cid: {"s": "cut", "r": None, "at": now - 5000}})            # an older tap arriving late
    assert status == 200 and res["saved"] == 0 and res["stale"] == 1 and res["newer"][cid]["s"] == "keep"
    assert store.load_decisions()[cid]["s"] == "keep"
    assert srv.decide({cid: {"s": "cut", "r": None, "at": now + 5000}})[1]["saved"] == 1
    assert store.load_decisions()[cid]["s"] == "cut"


def test_a_far_future_clock_cannot_lock_a_clip(srv):
    cid = srv.clips["desk_v.mp4"]["id"]
    assert srv.decide({cid: {"s": "keep", "r": None, "at": 9_999_999_999_999}})[0] == 400
    assert srv.decide({cid: {"s": "cut", "r": None, "at": store.now_ms()}})[1]["saved"] == 1


def test_a_new_ingest_shows_up_without_restarting_the_server(fresh):
    make_clip(fresh.shoot / "B-Roll" / "Late" / "late.mp4", "216x384", 3, freq=777)
    ingest_quiet()
    late = next(c for c in store.load_inventory()["clips"] if c["name"] == "late.mp4")
    assert fresh.decide({late["id"]: {"s": "keep", "r": None, "at": store.now_ms()}})[0] == 200
    assert fresh.get(f"/proxies/{late['id']}.mp4")[0] == 200


# ---------------------------------------------------------------- who may talk to it

def test_a_foreign_origin_cannot_save(srv):
    cid = srv.clips["desk_v.mp4"]["id"]
    good = {cid: {"s": "keep", "r": None, "at": store.now_ms()}}
    for origin in ("http://evil.example", "https://127.0.0.1:%d" % srv.port, "http://127.0.0.1:1", "http://localhost:1", "null", "http://127.0.0.1.evil.example:%d" % srv.port):
        status, res = srv.decide(good, headers={"Origin": origin})
        assert status == 403, origin
    assert not store.f_decisions().exists()
    assert srv.decide(good, headers={"Origin": f"http://127.0.0.1:{srv.port}"})[0] == 200          # the page's own origin
    assert srv.decide(good, headers={"Origin": f"http://localhost:{srv.port}"})[0] == 200
    assert srv.decide(good, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert srv.decide(good, headers={"Sec-Fetch-Site": "same-origin"})[0] == 200


def test_a_foreign_origin_cannot_read_decisions_either(srv):
    assert srv.get("/api/decisions", headers={"Origin": "http://evil.example"})[0] == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:%PORT%", "127.0.0.1", "127.0.0.1:1", "localhost", "0.0.0.0:%PORT%", "127.0.0.1.evil.example:%PORT%", ""])
def test_a_wrong_host_header_is_refused_everywhere(srv, host):
    host = host.replace("%PORT%", str(srv.port))
    assert srv.get("/", headers={"Host": host})[0] == 403                 # DNS rebinding: a page on another name reaching this port
    assert srv.get("/api/decisions", headers={"Host": host})[0] == 403
    url, _ = proxy_path(srv)
    assert srv.get(url, headers={"Host": host})[0] == 403


def test_localhost_by_name_works(srv):
    assert srv.get("/", headers={"Host": f"localhost:{srv.port}"})[0] == 200


def test_the_body_must_be_json_and_not_huge(srv):
    cid = srv.clips["desk_v.mp4"]["id"]
    body = json.dumps({"docs": {cid: {"s": "keep", "r": None, "at": store.now_ms()}}})
    assert srv.req("POST", "/api/decide", body=body, headers={"Content-Type": "text/plain"})[0] == 415            # a "simple" cross-site form post
    assert srv.req("POST", "/api/decide", body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 415
    assert srv.req("POST", "/api/decide", body="{nope", headers={"Content-Type": "application/json"})[0] == 400
    assert srv.req("POST", "/api/decide", body="[]", headers={"Content-Type": "application/json"})[0] == 400
    assert srv.req("POST", "/api/decide", body=json.dumps({"docs": [1]}), headers={"Content-Type": "application/json"})[0] == 400
    big = json.dumps({"docs": {}, "pad": "x" * (serve.MAX_BODY + 10)})
    try:
        assert srv.req("POST", "/api/decide", body=big, headers={"Content-Type": "application/json"})[0] == 413
    except (ConnectionError, http.client.HTTPException):
        pass                                # the server may hang up before the client has finished sending: also a refusal
    many = {("%016x" % i): {"s": "keep", "r": None, "at": 1} for i in range(serve.MAX_DOCS + 1)}
    assert srv.decide(many)[0] == 413
    assert not store.f_decisions().exists()
    assert srv.req("POST", "/api/decide", body=body, headers={"Content-Type": "application/json; charset=utf-8"})[0] == 200


def test_other_methods_and_paths_are_refused(srv):
    assert srv.req("OPTIONS", "/api/decide")[0] == 405                    # no preflight is ever answered, so no other site can post
    assert srv.req("POST", "/")[0] == 405
    assert srv.req("POST", "/api/decisions", body="{}", headers={"Content-Type": "application/json"})[0] in (404, 405)
    assert srv.req("GET", "/api/decide")[0] in (404, 405)
    assert srv.req("DELETE", "/api/decisions")[0] in (404, 405, 501)
    assert srv.req("PUT", "/proxies/" + "a" * 16 + ".mp4", body="x")[0] in (404, 405, 501)


def test_post_without_a_length_is_refused(srv):
    import socket
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    s.sendall(f"POST /api/decide HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n".encode())
    reply = s.recv(4096).decode()
    s.close()
    assert reply.startswith("HTTP/1.1 411")


def test_ping_identifies_the_server(srv):
    assert serve.ping(srv.port)["app"] == serve.APP
    assert serve.ping(1) is None


def test_concurrent_saves_do_not_lose_decisions(srv):
    ids = [c["id"] for c in srv.clips.values()]
    errors = []

    def worker(cid):
        try:
            for k in range(8):
                s, r = srv.decide({cid: {"s": "keep" if k % 2 else "cut", "r": None, "at": store.now_ms() + k}})
                assert s == 200
        except Exception as e:      # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in ids]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not errors and set(store.load_decisions()) == set(ids)
    assert all(d["s"] == "keep" for d in store.load_decisions().values())      # each clip's last write (k = 7) won


def test_a_damaged_decisions_file_is_a_500_not_a_hang(srv):
    store.f_decisions().write_text("{broken")
    status, _, body = srv.get("/api/decisions")
    assert status == 500 and b"decisions.json" in body
