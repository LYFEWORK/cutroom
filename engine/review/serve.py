#!/usr/bin/env python3
"""serve.py: the local review server. Serves the page and the previews, and saves decisions to decisions.json.

    python3 serve.py [--port N] [--open] [--verbose]

Binds 127.0.0.1 only, on review_port from the config (default 8765). Standard library only.

    GET  /                       the page (Review/site/index.html), and /data.js
    GET  /proxies/<id>.mp4       the preview, with full byte-range support (Safari will not play video without it)
    GET  /posters/<id>.jpg       thumbnail          GET /filmstrips/<id>.jpg   trim bar frames
    GET  /api/decisions          every saved decision, drafts included
    POST /api/decide             {"docs": {id: {"s", "r", "at"}}}: checked against the inventory, last write wins by "at"
    GET  /api/ping               {"app": "cutroom-review"}, so ingest can tell whether a server is already running

Only those paths are served, by exact pattern, so there is no way to ask for another file. Every request must carry a Host of
127.0.0.1 or localhost on this port (a page on another site cannot reach it by DNS tricks), a POST must be application/json
from this same origin, and a request body is capped at 2 MB.
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import threading
import webbrowser
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402
from review import store  # noqa: E402

APP = "cutroom-review"
MAX_BODY = 2 * 1024 * 1024
MAX_DOCS = 5000
CHUNK = 256 * 1024
HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MEDIA_RE = re.compile(r"^/(proxies|posters|filmstrips)/([0-9a-f]{16})\.(mp4|jpg)$")
MEDIA_TYPES = {"mp4": "video/mp4", "jpg": "image/jpeg"}
RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.I)


def parse_range(header, size: int):
    """None: no usable Range header (send the whole file). "bad": cannot be satisfied (416). (start, end): inclusive bytes.
    Handles bytes=S-E, bytes=S-, bytes=-N and Safari's bytes=0-1 probe. A list of ranges is ignored, which HTTP allows."""
    if not header:
        return None
    m = RANGE_RE.match(header)
    if not m:
        return None
    a, b = m.groups()
    if a == "" and b == "":
        return None
    if a == "":                                 # the last N bytes
        n = int(b)
        if n == 0 or size == 0:
            return "bad"
        return (max(0, size - n), size - 1)
    start = int(a)
    end = int(b) if b != "" else size - 1
    if start >= size:
        return "bad"
    if end < start:
        return None
    return (start, min(end, size - 1))


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64
    verbose = False

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self._lock = threading.Lock()
        self._inv_key = None
        self._inv: dict = {}
        self._built = ""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            return                          # a browser dropping a video request halfway is normal
        super().handle_error(request, client_address)

    def inventory(self):
        """({id: clip}, built stamp), re-read only when inventory.json changes, so a new ingest shows up without a restart."""
        f = store.f_inventory()
        try:
            st = f.stat()
        except OSError:
            return {}, ""
        key = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if key != self._inv_key:
                inv = store.load_inventory()
                self._inv = {c["id"]: c for c in inv["clips"]}
                self._built = inv.get("built", "")
                self._inv_key = key
            return self._inv, self._built


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = APP
    sys_version = ""
    timeout = 60

    # ---- plumbing
    def log_request(self, code="-", size="-"):
        if self.server.verbose:
            print(f"{self.command} {self.path} {code} range={self.headers.get('Range') or '-'}", file=sys.stderr, flush=True)

    def log_error(self, *args):
        pass

    def version_string(self):
        return APP

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, code, obj, headers=None):
        h = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
        h.update(headers or {})
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), h)

    def _error(self, code, msg):
        self._json(code, {"error": msg})

    def do_GET(self): self._route()
    def do_HEAD(self): self._route()
    def do_POST(self): self._route()

    def do_OPTIONS(self):
        # no cross-origin use, so no preflight is ever answered
        self.close_connection = True
        self._json(405, {"error": "not allowed"}, {"Allow": "GET, HEAD, POST"})

    def _route(self):
        try:
            if not self._host_ok():
                self.close_connection = True
                return self._error(403, "this server only answers to 127.0.0.1 or localhost")
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                if not self._origin_ok():
                    self.close_connection = True
                    return self._error(403, "this request did not come from the review page")
                return self._api(path)
            if self.command == "POST":
                self.close_connection = True
                return self._error(405, "not allowed")
            return self._static(path)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            self.close_connection = True
        except store.StoreError as e:
            self._error(500, str(e))
        except SystemExit:          # common.stop() inside a helper (for example the workspace is not set up)
            self.close_connection = True
            self._error(500, "the workspace is not set up")

    def _host_ok(self) -> bool:
        h = (self.headers.get("Host") or "").strip().lower()
        if h.startswith("["):
            host, _, rest = h[1:].partition("]")
            host, port = "[" + host + "]", rest[1:] if rest.startswith(":") else ""
        else:
            host, _, port = h.partition(":")
        return host in HOSTS and port == str(self.server.server_port)

    def _origin_ok(self) -> bool:
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return False
        o = self.headers.get("Origin")
        if o is None:
            return True                     # not a browser (a script, curl): the Host check above still applies
        if o == "null":
            return False
        u = urlsplit(o)
        return u.scheme == "http" and (u.hostname or "").lower() in ("127.0.0.1", "localhost", "::1") and u.port == self.server.server_port

    # ---- the page and the media
    def _static(self, path):
        if path in ("/", "/index.html"):
            f, ctype, cache = store.d_site() / "index.html", "text/html; charset=utf-8", "no-store"
        elif path == "/data.js":
            f, ctype, cache = store.d_site() / "data.js", "text/javascript; charset=utf-8", "no-store"
        elif path == "/favicon.ico":
            return self._send(204)
        else:
            m = MEDIA_RE.match(path)
            if not m or (m.group(1) == "proxies") != (m.group(3) == "mp4"):
                return self._error(404, "not found")
            folder = {"proxies": store.d_proxies, "posters": store.d_posters, "filmstrips": store.d_strips}[m.group(1)]()
            f, ctype, cache = folder / f"{m.group(2)}.{m.group(3)}", MEDIA_TYPES[m.group(3)], "no-cache"
        self._file(f, ctype, cache)

    def _file(self, f: Path, ctype: str, cache: str):
        try:
            st = f.stat()
            if not f.is_file():
                raise OSError
        except OSError:
            msg = "The review page has not been built yet. Run ingest." if f.name in ("index.html", "data.js") else "not found"
            return self._error(404, msg)
        size = st.st_size
        etag = '"%x-%x"' % (size, st.st_mtime_ns)
        lastmod = formatdate(st.st_mtime, usegmt=True)
        hdr = {"Content-Type": ctype, "Accept-Ranges": "bytes", "ETag": etag, "Last-Modified": lastmod, "Cache-Control": cache}
        rng_h = self.headers.get("Range")
        if rng_h and (self.headers.get("If-Range") or "").strip() not in ("", etag, lastmod):
            rng_h = None                    # the file changed since the client's partial copy: send all of it
        if not rng_h and etag in [x.strip() for x in (self.headers.get("If-None-Match") or "").split(",")]:
            self.send_response(304)
            for k in ("ETag", "Cache-Control", "Last-Modified"):
                self.send_header(k, hdr[k])
            self.end_headers()
            return
        rng = parse_range(rng_h, size)
        if rng == "bad":
            hdr["Content-Range"] = f"bytes */{size}"
            return self._send(416, b"", hdr)
        if rng:
            start, end = rng
            code = 206
            hdr["Content-Range"] = f"bytes {start}-{end}/{size}"
        else:
            start, end, code = 0, size - 1, 200
        length = max(0, end - start + 1)
        hdr["Content-Length"] = str(length)
        self.send_response(code)
        for k, v in hdr.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD" or not length:
            return
        with open(f, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(CHUNK, left))
                if not chunk:
                    self.close_connection = True        # the file shrank under us: do not leave the client waiting for the rest
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # ---- the api
    def _api(self, path):
        if path == "/api/ping" and self.command in ("GET", "HEAD"):
            _, built = self.server.inventory()
            return self._json(200, {"app": APP, "built": built})
        if path == "/api/decisions" and self.command in ("GET", "HEAD"):
            _, built = self.server.inventory()
            return self._json(200, {"decisions": store.load_decisions(drafts=True), "built": built, "saved": store.stamp()})
        if path == "/api/decide" and self.command == "POST":
            return self._decide()
        self.close_connection = True
        return self._error(404 if self.command != "POST" else 405, "not found")

    def _decide(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.close_connection = True
            return self._error(415, "send application/json")
        n = self.headers.get("Content-Length")
        if n is None or not n.strip().isdigit():
            self.close_connection = True
            return self._error(411, "Content-Length is required")
        n = int(n)
        if n > MAX_BODY:
            self.close_connection = True
            return self._error(413, "that is too much to save at once")
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw)
        except ValueError:
            return self._error(400, "the body is not JSON")
        docs = body.get("docs") if isinstance(body, dict) else None
        if not isinstance(docs, dict):
            return self._error(400, 'expected {"docs": {id: decision}}')
        if len(docs) > MAX_DOCS:
            return self._error(413, f"at most {MAX_DOCS} decisions at once")
        inv, _ = self.server.inventory()
        good, rejected = {}, {}
        for cid, raw_doc in docs.items():
            doc, why = store.validate_doc(cid, raw_doc, inv.get(cid))
            if why:
                rejected[str(cid)[:64]] = why
            else:
                good[cid] = doc
        res = store.merge_decisions(good, source="page") if good else {"saved": [], "stale": {}}
        newer = {i: {"s": d.get("s"), "r": d.get("r"), "at": d.get("at")} for i, d in res["stale"].items()}
        out = {"saved": len(res["saved"]), "stale": len(res["stale"]), "rejected": rejected, "newer": newer}
        self._json(400 if rejected and not res["saved"] and not res["stale"] else 200, out)


def make_server(port=None, verbose=False) -> ReviewServer:
    """A server bound to 127.0.0.1. port=None uses review_port from the config, port=0 picks a free one (tests)."""
    store.home().mkdir(parents=True, exist_ok=True)
    srv = ReviewServer(("127.0.0.1", store.port() if port is None else port), Handler)
    srv.verbose = verbose
    return srv


def url(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def ping(port: int, timeout: float = 1.0):
    """The server's /api/ping answer when one of ours is on that port, else None."""
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("GET", "/api/ping")
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        c.close()
        return data if data.get("app") == APP else None
    except (OSError, ValueError, http.client.HTTPException):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Serve the review page on this computer.")
    ap.add_argument("--port", type=int, help="override review_port from the config")
    ap.add_argument("--open", action="store_true", help="open the page in the browser")
    ap.add_argument("--verbose", action="store_true", help="print every request")
    a = ap.parse_args(argv)
    port = a.port if a.port is not None else store.port()
    try:
        srv = make_server(port, verbose=a.verbose)
    except OSError as e:
        if ping(port):
            print(f"Already running: {url(port)}")
            if a.open:
                webbrowser.open(url(port))
            return 0
        common.stop(f"port {port} is in use by something else ({e.strerror or e}). Set review_port in the config to another number.")
    print(f"Review page: {url(srv.server_port)}   (Ctrl-C to stop)", flush=True)
    if a.open:
        webbrowser.open(url(srv.server_port))
    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    store.run_cli(main)
