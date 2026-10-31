#!/usr/bin/env python3
"""store.py: where the review module keeps its files, and the one reader and writer of decisions.json.

Everything lives under the workspace's Review/ folder:

    inventory.json            every clip found (inventory.py)
    decisions.json            the saved keep, cut and trim choices (+ decisions.json.bak, the one before it)
    decisions.log.jsonl       every accepted change, appended, never rewritten
    proxies/<id>.mp4          small playable copies (encode.py), proxies.json describes them
    posters/<id>.jpg          one thumbnail per clip (posters.py)
    filmstrips/<id>.jpg       eight frames in a row, drawn behind the trim bar
    site/index.html, data.js  the review page (build_site.py)
    apply-plan.json           what `apply plan` wrote and `apply go` will run
    logs/apply.jsonl          what apply did

A decision is {"s": "keep" | "cut" | "none", "r": [[start, end], ...] or null, "at": epoch ms}. "none" is a draft: a trim
was set but the clip is not marked yet. A kept clip with no "r" is kept whole. "r" is in seconds of the clip's own time.
Every reader that enforces anything (approved, gate, apply) calls load_decisions(), which hides drafts.

Writers go through merge_decisions(): last write wins by "at", under a file lock, replaced atomically, one backup kept.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import common  # noqa: E402

try:
    import fcntl
except ImportError:  # no flock here: the file is still replaced atomically, only the cross-process lock is skipped
    fcntl = None

ID_RE = re.compile(r"^[0-9a-f]{16}$")
KINDS = ("take", "broll", "bad", "other")
STATES = ("keep", "cut", "none")
MAX_PARTS = 64
FUTURE_SLACK_MS = 24 * 3600 * 1000      # a decision stamped further ahead than this is refused, so a bad clock cannot win forever

_thread_lock = threading.RLock()


class StoreError(Exception):
    """A file the module depends on is unreadable. CLIs turn this into a STOP line; the server turns it into a 500."""


# ---------------------------------------------------------------- paths

def home() -> Path:
    return common.ws("Review")


def f_inventory() -> Path: return home() / "inventory.json"
def f_decisions() -> Path: return home() / "decisions.json"
def f_decisions_log() -> Path: return home() / "decisions.log.jsonl"
def f_proxies() -> Path: return home() / "proxies.json"
def f_plan() -> Path: return home() / "apply-plan.json"
def f_descriptions() -> Path: return home() / "descriptions.json"
def f_apply_log() -> Path: return home() / "logs" / "apply.jsonl"
def d_proxies() -> Path: return home() / "proxies"
def d_posters() -> Path: return home() / "posters"
def d_strips() -> Path: return home() / "filmstrips"
def d_site() -> Path: return home() / "site"


def now_ms() -> int:
    return int(time.time() * 1000)


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def today() -> str:
    return time.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- config

def roots(cfg: dict | None = None) -> list[Path]:
    """Folders to inventory: review.roots in the config (absolute, ~, or relative to the workspace), default [Footage]."""
    cfg = cfg if cfg is not None else common.load_config()
    raw = None
    section = cfg.get("review")
    if isinstance(section, dict):
        raw = section.get("roots")
    if raw is None:
        raw = cfg.get("review.roots")
    if not raw:
        return [common.ws("Footage")]
    if isinstance(raw, str):
        raw = [raw]
    out: list[Path] = []
    for r in raw:
        p = Path(str(r)).expanduser()
        if not p.is_absolute():
            p = common.workspace() / p
        if p not in out:
            out.append(p)
    return out


def proxy_height(cfg: dict | None = None) -> int:
    cfg = cfg if cfg is not None else common.load_config()
    try:
        h = int(cfg.get("proxy_height", 360))
    except (TypeError, ValueError):
        h = 360
    return max(120, min(1080, h))


def port(cfg: dict | None = None) -> int:
    cfg = cfg if cfg is not None else common.load_config()
    try:
        return int(cfg.get("review_port", 8765))
    except (TypeError, ValueError):
        return 8765


# ---------------------------------------------------------------- reading

def read_json(path: Path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise StoreError(f"{path} could not be read ({e}). If it is damaged, the previous copy may be {path.name}.bak") from e


def load_inventory() -> dict:
    inv = read_json(f_inventory(), None)
    if not isinstance(inv, dict) or not isinstance(inv.get("clips"), list):
        return {"version": 1, "built": "", "roots": [], "clips": []}
    return inv


def load_proxies() -> dict:
    d = read_json(f_proxies(), {})
    return d if isinstance(d, dict) else {}


def load_decisions(drafts: bool = False) -> dict:
    """id -> decision. Without drafts=True only keep and cut come back, which is what every enforcing reader wants."""
    data = read_json(f_decisions(), {})
    decs = data.get("decisions", {}) if isinstance(data, dict) else {}
    if not isinstance(decs, dict):
        return {}
    if drafts:
        return {i: d for i, d in decs.items() if isinstance(d, dict)}
    return {i: d for i, d in decs.items() if isinstance(d, dict) and d.get("s") in ("keep", "cut")}


# ---------------------------------------------------------------- writing decisions

@contextmanager
def locked():
    """One writer at a time, across threads and across processes (the page server and `apply go` both write)."""
    lock = home() / ".decisions.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock:
        with open(lock, "a+") as fh:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(fh, fcntl.LOCK_UN)


def _dump_decisions(decs: dict) -> str:
    """One decision per line, so the file reads and diffs well."""
    head = '{"version":1,"saved":%s,"decisions":{' % json.dumps(stamp())
    rows = [json.dumps(i, ensure_ascii=False) + ":" + json.dumps(d, ensure_ascii=False, separators=(",", ":")) for i, d in sorted(decs.items())]
    return head + ("\n" + ",\n".join(rows) + "\n" if rows else "") + "}}\n"


def _write_decisions(decs: dict) -> None:
    f = f_decisions()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_dump_decisions(decs))
        fh.flush()
        os.fsync(fh.fileno())
    if f.exists():
        shutil.copy2(f, f.with_name(f.name + ".bak"))      # rolling: always the one before this save
    os.replace(tmp, f)


def save_decisions(decs: dict) -> None:
    """Replace the whole file (atomic, one rolling .bak). Prefer merge_decisions for anything that is not a full rewrite."""
    with locked():
        _write_decisions(decs)


def _log_decisions(rows: list[dict]) -> None:
    if not rows:
        return
    f = f_decisions_log()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def merge_decisions(docs: dict, source: str = "page") -> dict:
    """Merge decisions into decisions.json, last write wins by "at". Returns {"saved": [ids], "stale": {id: the newer doc}}.

    A doc replaces the stored one when its "at" is the same or newer. Keys the new doc does not carry (like from_id) are kept.
    The caller has already checked the docs (validate_doc for page posts, apply builds its own)."""
    saved: list[str] = []
    stale: dict = {}
    with locked():
        cur = load_decisions(drafts=True)
        for cid, doc in docs.items():
            have = cur.get(cid)
            if have and have.get("at", 0) > doc.get("at", 0):
                stale[cid] = have
                continue
            merged = dict(have or {})
            merged.update(doc)
            cur[cid] = merged
            saved.append(cid)
        if saved:
            _write_decisions(cur)
            _log_decisions([{"t": stamp(), "id": c, "s": cur[c].get("s"), "r": cur[c].get("r"), "at": cur[c].get("at"), "src": source} for c in saved])
    return {"saved": saved, "stale": stale}


def clean_ranges(r, dur: float):
    """(ranges, None) normalized and sorted, or (None, reason). None in means whole clip."""
    if r is None:
        return None, None
    if not isinstance(r, (list, tuple)) or not 1 <= len(r) <= MAX_PARTS:
        return None, f"r must be a list of 1 to {MAX_PARTS} parts"
    out = []
    for pr in r:
        if not isinstance(pr, (list, tuple)) or len(pr) != 2:
            return None, "each part is [start, end]"
        a, b = pr
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (a, b)):
            return None, "part times must be finite numbers"
        if a < 0 or not a < b or b > dur + 0.5:
            return None, f"part {a}-{b} is outside the clip (0 to {dur:.2f}s)"
        a, b = round(float(a), 3), round(min(float(b), float(dur)), 3)
        if not a < b:
            return None, "a part is empty after the clip's end"
        out.append([a, b])
    out.sort()
    for i in range(1, len(out)):
        if out[i][0] < out[i - 1][1]:
            return None, "parts overlap"
    return out, None


def validate_doc(cid, raw, clip, at_limit_ms: int | None = None):
    """Check one posted decision against the inventory. Returns (doc, None) or (None, reason).
    The descriptive fields (p, b, k, n) come from the inventory, never from the client."""
    if not isinstance(cid, str) or not ID_RE.match(cid):
        return None, "not a clip id"
    if clip is None:
        return None, "not in the inventory (run ingest, then reload the page)"
    if not isinstance(raw, dict):
        return None, "a decision is an object"
    s = raw.get("s")
    if s not in STATES:
        return None, "s must be keep, cut or none"
    at = raw.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or at <= 0:
        return None, "at must be a time in milliseconds"
    limit = at_limit_ms if at_limit_ms is not None else now_ms() + FUTURE_SLACK_MS
    if at > limit:
        return None, "at is more than a day in the future"
    r, why = clean_ranges(raw.get("r"), float(clip.get("dur") or 0))
    if why:
        return None, why
    return {"s": s, "r": r, "at": int(at), "p": clip.get("rel", ""), "b": clip.get("project", ""), "k": clip.get("kind", ""), "n": clip.get("name", "")}, None


# ---------------------------------------------------------------- the inventory as a lookup

def abs_of(path_entry: dict) -> str:
    return os.path.normpath(os.path.join(path_entry["root"], path_entry["rel"]))


def abs_paths(clip: dict) -> list[str]:
    return [abs_of(p) for p in clip.get("paths", [])]


def norm(p) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(p))))


def verdict(clip: dict, decs: dict) -> str:
    """keep, cut, or todo (never reviewed). decs must come from load_decisions() so drafts do not count."""
    d = decs.get(clip["id"])
    return d["s"] if d and d.get("s") in ("keep", "cut") else "todo"


def kept_ranges(clip: dict, dec: dict | None) -> list[list[float]]:
    """The parts of a kept clip that may be used. Kept whole means one part, the whole clip."""
    r = (dec or {}).get("r")
    if r:
        return [[float(a), float(b)] for a, b in r]
    return [[0.0, float(clip.get("dur") or 0.0)]]


class Index:
    """The inventory as lookups: by id, by path, by file name. lookup_file() also resolves a copy by its content."""

    def __init__(self, inv: dict | None = None):
        inv = inv if inv is not None else load_inventory()
        self.clips = inv.get("clips", [])
        self.by_id = {c["id"]: c for c in self.clips}
        self._by_abs: dict[str, tuple] = {}
        self._by_name: dict[str, set] = {}
        for c in self.clips:
            for p in c.get("paths", []):
                for a in {norm(os.path.join(p["root"], p["rel"])), os.path.join(os.path.realpath(p["root"]), p["rel"])}:
                    self._by_abs[a] = (c, p)
                self._by_name.setdefault(os.path.basename(p["rel"]).lower(), set()).add(c["id"])

    def lookup_path(self, path):
        """(clip, path entry) for a path the inventory lists, else None. Compares the written path and the resolved one."""
        for cand in (norm(path), os.path.realpath(os.path.expanduser(str(path)))):
            hit = self._by_abs.get(cand)
            if hit:
                return hit
        return None

    def lookup_file(self, path):
        """(clip, how) for a file on disk. how is "path" when it is where the inventory saw it, unchanged, or "content" when
        it is found by its fingerprint (a copy, a move, a rename). (None, reason) when it is not a reviewed clip."""
        try:
            st = os.stat(path)
        except OSError:
            return None, "not on disk"
        hit = self.lookup_path(path)
        if hit and hit[1].get("size") == st.st_size and hit[1].get("mtime") == int(st.st_mtime):
            return hit[0], "path"
        try:
            c = self.by_id.get(common.fingerprint(path))
        except OSError:
            return None, "unreadable"
        if c:
            return c, "content"
        return None, "changed since the last ingest" if hit else "not in the inventory"

    def by_suffix(self, ref: str) -> list[dict]:
        """Clips with a path that ends with ref on a folder boundary. ref is a relative path or a bare file name."""
        ref = ref.replace("\\", "/").strip("/").lower()
        if not ref:
            return []
        out: dict[str, dict] = {}
        want = "/" + ref
        for c in self.clips:
            for p in c.get("paths", []):
                full = "/" + abs_of(p).replace("\\", "/").strip("/").lower()
                if full.endswith(want):
                    out[c["id"]] = c
        return list(out.values())


def log_apply(**kw) -> None:
    f = f_apply_log()
    f.parent.mkdir(parents=True, exist_ok=True)
    kw["t"] = stamp()
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(kw, ensure_ascii=False) + "\n")


def run_cli(main) -> None:
    """The one wrapper every command line entry uses: a damaged file becomes a plain STOP line, never a stack trace."""
    try:
        sys.exit(main())
    except StoreError as e:
        common.stop(str(e))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        sys.exit(130)
