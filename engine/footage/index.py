#!/usr/bin/env python3
"""Build the self-contained "START HERE.html" next to the organized footage.

   python3 index.py --job JOB.json [--out PATH]

Reads <scratch>/plan.json (written by organize.py), <scratch>/thumbs/<clip>.jpg and, once takes.py has run, Takes Manifest.csv: one card per cut take and
per blooper under its Interview/<line> or B-Roll/Bloopers folder, and the coverage strip counts takes per script line.
One file, no network: thumbnails are base64 JPEGs, fonts are the system's, so it opens the same offline and on a phone. Links are relative and URL-encoded.
Dark by default, light when the reader's system asks for it (prefers-color-scheme); both are checked to read at WCAG AA. job.brand = {accent, ink, font_display}
changes the colours and the heading font, nothing else. A thumbnail is shown in the clip's own shape: vertical, horizontal or square.
"""
import argparse
import base64
import html
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
for _p in (str(HERE.parent), str(HERE)):   # engine/ for common, then this folder in front of it: a module here wins over one of the same name in engine/
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)
import common  # noqa: E402
from common import stop  # noqa: E402
import job as jobmod  # noqa: E402
from job import cut_id, load_job, read_csv, read_plan, resolve_brand  # noqa: E402


# ---------------------------------------------------------------- colour
def _hex(c):
    c = c.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in rgb)


def mix(a, b, t):
    return _to_hex(tuple(x + (y - x) * t for x, y in zip(_hex(a), _hex(b))))


def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in _hex(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def readable(color, ground, toward, ratio=4.5):
    """color itself when it reads on ground at `ratio`, else color moved toward `toward` in 5% steps until it does"""
    for i in range(0, 21):
        c = mix(color, toward, i / 20) if i else color
        if contrast(c, ground) >= ratio:
            return c
    return toward


def text_on(bg, prefer):
    """the text colour for a filled badge: the first of prefer, white, black that reads on bg at 4.5:1, else whichever reads best"""
    for c in list(prefer) + ["#ffffff", "#000000"]:
        if contrast(bg, c) >= 4.5:
            return c
    return max(list(prefer) + ["#ffffff", "#000000"], key=lambda c: contrast(bg, c))


def tokens(brand):
    """the colours of both themes, derived from the brand's accent and ink. Every text colour is checked against the surface it sits on (4.5:1)."""
    ink, acc = brand["ink"], brand["accent"]
    dark = {"ink": ink, "ink2": mix(ink, "#c8ced6", .055), "ink3": mix(ink, "#c8ced6", .12), "line": mix(ink, "#c8ced6", .22), "ink_top": mix(ink, "#c8ced6", .02),
            "txt": "#f2f4f6", "txt2": "#d6dbe0", "mut": "#a3acb6", "amber": "#ffb020", "blue": "#5ab0ff"}
    for k in ("txt", "txt2", "mut"):   # a grey ink (the lightest the brand allows) brightens the text until it reads on the lightest surface
        dark[k] = dark[k] if contrast(dark[k], dark["ink3"]) >= 4.5 else readable(dark[k], dark["ink3"], "#ffffff")
    dark["accent"] = acc
    dark["accent_text"] = readable(acc, dark["ink3"], "#ffffff")
    dark["amber_text"] = readable(dark["amber"], dark["ink3"], "#ffffff")
    dark["on_accent"] = text_on(acc, [ink])
    light = {"ink": mix(ink, "#ffffff", .965), "ink2": "#ffffff", "ink3": mix(ink, "#ffffff", .925), "line": mix(ink, "#ffffff", .80), "ink_top": "#ffffff",
             "txt": "#14161b", "txt2": "#2a2f37", "mut": "#58606b", "amber": "#ffb020", "blue": "#5ab0ff"}
    light["accent"] = acc
    light["accent_text"] = readable(acc, light["ink3"], "#000000")
    light["amber_text"] = readable("#a35f00", light["ink3"], "#000000")
    light["on_accent"] = dark["on_accent"]
    return dark, light


def css_vars(t):
    return ("--ink:{ink};--ink2:{ink2};--ink3:{ink3};--line:{line};--ink-top:{ink_top};--accent:{accent};--accent-text:{accent_text};--on-accent:{on_accent};"
            "--txt:{txt};--txt2:{txt2};--mut:{mut};--amber:{amber};--amber-text:{amber_text};--blue:{blue}").format(**t)


# ---------------------------------------------------------------- the page
def esc(s):
    return html.escape(str(s if s is not None else ""))


def dur(s):
    s = float(s)
    if s >= 3600:
        return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}"
    return f"{int(s // 60)}:{int(s % 60):02d}" if s >= 60 else f"{s:.1f}s"


def build(job, out=None, quiet=False):
    SCR, ROOT = job["scratch"], job["root"]
    OUT = out or os.path.join(ROOT, "START HERE.html")
    P = read_plan(job)
    if P is None:
        stop("no plan.json in the scratch folder or in Edit/: organize the shoot first (intake.py apply)")
    recs = {r["clip"]: r for r in P["records"]}
    moves = P["moves"]
    B = resolve_brand(job)
    dark, light = tokens(B)
    NAME, DATE, CAM = job["name"], job.get("shoot_date", ""), job.get("camera_label", "")
    ORGANIZED = P.get("organized_at") or jobmod.today()
    COPIED = P.get("mode") == "copy"
    QS = OrderedDict((q["id"], q["title"]) for q in job["questions"])
    QTEXT = {q["id"]: q.get("question", "") for q in job["questions"]}
    CUTS = read_csv(os.path.join(ROOT, "Takes Manifest.csv"))

    def thumb(clip):
        p = f"{SCR}/thumbs/{clip}.jpg"
        if not os.path.exists(p):
            return ""
        with open(p, "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()

    def words_between(clip, t0, t1):
        p = f"{SCR}/words/{clip}.txt"
        out = []
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                for l in fh:
                    m = re.match(r"t=([\d.]+)\s+(.+)", l.strip())
                    if m and t0 <= float(m.group(1)) <= t1:
                        out.append(m.group(2))
        return " ".join(out)

    def raw_item(m):
        r = recs[m["clip"]]
        fname = os.path.basename(m["rel"])
        acls = "silent" if r.get("silent") else ("candid" if r.get("category") == "candid_talk" else "audio")
        flags = [f for f in (r.get("flags") or []) if f and f != "none"]
        q = r.get("question_asked", "") if r.get("category") == "interview" else ""
        summary = r.get("answer_summary", "") if r.get("category") in ("interview", "candid_talk") else r.get("on_screen", "")
        quote_ = r.get("key_quote", "") if r.get("category") == "interview" else ""
        search = " ".join([fname, q, summary, quote_, r.get("on_screen", ""), " ".join(flags), r.get("people", ""), r.get("transcript", "")]).lower()
        return dict(rel=m["rel"], fname=fname, thumb=thumb(m["clip"]), dur=r["duration_s"], acls=acls, shape=r.get("orientation", "vertical"),
                    label={"silent": "NO AUDIO", "candid": "CANDID TALK", "audio": "AUDIO"}[acls], q=q, summary=summary, quote=quote_,
                    meta=" · ".join(x for x in (esc(r.get("shot", "")), esc(r.get("camera", "")), esc(r.get("people", ""))) if x), flags=flags, search=search, clip=m["clip"])

    def cut_item(c):
        r = recs.get(c["source_clip"], {})
        fname = os.path.basename(c["file"])
        cid = cut_id(c)
        t0, t1 = float(c["in_s"]), float(c["out_s"])
        said = words_between(c["source_clip"], float(c["spoken_from_s"]), float(c["spoken_to_s"]))
        is_take = c["kind"] == "take"
        label = (f"TAKE {c['take']} · {c['quality'].upper()}" if is_take else "BLOOPER")
        line = c["line"]
        q = (QTEXT.get(line) or QS.get(line, "")) if line else ""
        summary = " · ".join(x for x in (c["quality"].capitalize() if is_take else "Blooper", c["note"]) if x)
        quote_ = " ".join(said.split()[:30]) + (" ..." if len(said.split()) > 30 else "")
        return dict(rel=c["file"], fname=fname, thumb=thumb(cid) or thumb(c["source_clip"]), dur=t1 - t0, acls="audio", shape=r.get("orientation", "vertical"),
                    label=label, q=q, summary=summary, quote=quote_, meta=" · ".join(x for x in (f"cut from {esc(c['source_clip'])}", esc(r.get("people", ""))) if x),
                    flags=[], clip=c["source_clip"], search=" ".join([fname, q, summary, said, c["note"], c["quality"], c["kind"]]).lower())

    def card(it):
        shape = {"vertical": "v", "horizontal": "h", "square": "s"}.get(it["shape"], "v")
        flags = (" · " + " ".join(f'<span class="flag">{esc(f)}</span>' for f in it["flags"])) if it["flags"] else ""
        return f"""
<article class="card {shape}" data-search="{esc(it['search'])}" data-audio="{it['acls']}">
  <a class="thumb" href="{esc(quote(it['rel'], safe='/'))}" aria-label="Open {esc(it['fname'])}"><img src="{it['thumb']}" alt=""><span class="dur">{dur(it['dur'])}</span><span class="badge {it['acls']}">{esc(it['label'])}</span></a>
  <div class="body"><h4>{esc(it['fname'])}</h4>
    {f'<p class="q"><b>Asked:</b> {esc(it["q"])}</p>' if it['q'] else ''}<p class="s">{esc(it['summary'])}</p>
    {f'<p class="quote">&ldquo;{esc(it["quote"])}&rdquo;</p>' if it['quote'] else ''}
    <p class="meta">{it['meta']}{flags}</p>
  </div></article>"""

    items = [raw_item(m) for m in moves] + [cut_item(c) for c in CUTS]
    groups = OrderedDict()
    for it in items:
        groups.setdefault("/".join(it["rel"].split("/")[:-1]), []).append(it)

    def section(title_, keys, note=""):
        if not any(groups.get(k) for k in keys):
            return ""   # nothing of this kind on the shoot: no empty heading
        out = [f'<section class="sec"><h2>{esc(title_)}</h2>{f"<p class=note>{esc(note)}</p>" if note else ""}']
        for k in keys:
            its = groups.get(k, [])
            if not its:
                continue
            total = sum(float(it["dur"]) for it in its)
            out.append(f'<h3>{esc(k.split("/")[-1])} <span class="cnt">{len(its)} clip{"s" if len(its) != 1 else ""} · {dur(total)}</span></h3><div class="grid">')
            out += [card(it) for it in its]
            out.append("</div>")
        out.append("</section>")
        return "\n".join(out)

    def qid_of(k):
        parts = k.split("/")
        return parts[1].split(" ")[0] if len(parts) > 1 else ""
    int_keys = sorted((k for k in groups if k.startswith("Interview/")), key=lambda k: (0, list(QS).index(qid_of(k)), k) if qid_of(k) in QS else (1, 0, k))
    broll_keys = sorted(k for k in groups if k.startswith("B-Roll/"))
    other_keys = sorted(k for k in groups if not k.startswith("Interview/") and not k.startswith("B-Roll/"))

    cov = ""
    if QS:
        cells = []
        for rid, t in QS.items():
            n = sum(len(v) for k, v in groups.items() if k.startswith(f"Interview/{rid} "))
            cells.append(f'<div class="cov {"hit" if n else "miss"}"><b>{esc(rid)}</b><span>{esc(t)}</span><em>{n} take{"s" if n != 1 else ""}</em></div>')
        bn = len(groups.get("Interview/Bonus Answers", []))
        cells.append(f'<div class="cov {"hit" if bn else "miss"}"><b>Bonus</b><span>Answers not on the list</span><em>{bn} clip{"s" if bn != 1 else ""}</em></div>')
        cov = f'<div class="covwrap"><h2>Coverage: what was answered on camera</h2><div class="covgrid">{"".join(cells)}</div></div>'

    n_clips = len(moves)
    total_s = sum(float(recs[m["clip"]]["duration_s"]) for m in moves)
    n_int = sum(1 for m in moves if recs[m["clip"]].get("category") == "interview")
    n_broll = sum(1 for m in moves if m["rel"].startswith("B-Roll/"))
    n_silent = sum(1 for m in moves if recs[m["clip"]].get("silent"))
    n_takes = sum(1 for c in CUTS if c["kind"] == "take")
    n_blo = len(CUTS) - n_takes
    cut_stats = f'<div><b>{n_takes}</b>takes cut</div><div><b>{n_blo}</b>bloopers cut</div>' if CUTS else ""
    display = B["font_display"]
    how = ("copied from the source folder, which was left as it was" if COPIED else "moved (not copied) out of the source folder")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light">
<link rel="icon" href="data:,">
<title>{esc(NAME)} Start Here{(' · Shoot ' + esc(DATE)) if DATE else ''}</title>
<style>
:root{{color-scheme:dark light;{css_vars(dark)}}}
@media (prefers-color-scheme:light){{:root{{{css_vars(light)}}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--ink);color:var(--txt);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
h1,h2,h3{{font-family:"{display}",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;letter-spacing:-.01em;margin:0}}
a{{color:inherit}}:focus-visible{{outline:2px solid var(--accent-text);outline-offset:2px}}
header{{padding:36px 32px 20px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,var(--ink-top),var(--ink))}}
header h1{{font-size:40px;font-weight:800;line-height:1.05;overflow-wrap:anywhere}}header h1 span{{color:var(--accent-text)}}
.stats{{display:flex;gap:28px;flex-wrap:wrap;margin-top:14px;color:var(--mut)}}.stats b{{color:var(--txt);font-size:20px;display:block;font-family:"{display}",system-ui,sans-serif}}
.tools{{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}}
input[type=search]{{flex:1;min-width:240px;background:var(--ink2);border:1px solid var(--line);color:var(--txt);padding:10px 14px;border-radius:8px;font:inherit}}
input[type=search]::placeholder{{color:var(--mut);opacity:1}}
.tools button{{background:var(--ink2);border:1px solid var(--line);color:var(--txt);padding:10px 14px;border-radius:8px;font:inherit;cursor:pointer}}
.tools button[aria-pressed=true]{{background:var(--accent);color:var(--on-accent);border-color:var(--accent);font-weight:600}}
.shown{{margin:10px 0 0;color:var(--mut);font-size:12.5px}}
.covwrap{{padding:20px 32px;border-bottom:1px solid var(--line)}}.covwrap h2{{font-size:14px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px}}
.covgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}}
.cov{{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--ink2)}}.cov b{{font-family:"{display}",system-ui,sans-serif;font-size:15px;display:block}}.cov span{{display:block;color:var(--mut);font-size:12px;min-height:32px}}.cov em{{font-style:normal;font-size:12px;font-weight:600}}
.cov.hit{{border-color:var(--accent-text)}}.cov.hit em{{color:var(--accent-text)}}.cov.miss em{{color:var(--amber-text)}}.cov.miss b,.cov.miss span{{opacity:.85}}
.sec{{padding:28px 32px;border-bottom:1px solid var(--line)}}.sec h2{{font-size:28px;font-weight:800;margin-bottom:6px}}.sec h2::before{{content:"";display:inline-block;width:10px;height:10px;background:var(--accent);margin-right:10px;border-radius:2px}}
.note{{color:var(--mut);margin:0 0 10px}}.sec h3{{font-size:17px;margin:22px 0 10px}}.cnt{{color:var(--mut);font-weight:500;font-size:13px;margin-left:8px;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
.card{{display:flex;gap:12px;background:var(--ink2);border:1px solid var(--line);border-radius:10px;overflow:hidden}}.card.hide{{display:none}}
.thumb{{position:relative;flex:0 0 110px;min-height:196px;background:#000;display:block;overflow:hidden}}.thumb img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}}
.card.s{{align-self:start}}.card.s .thumb{{flex-basis:150px;min-height:150px}}
.card.h{{flex-direction:column;gap:0}}.card.h .thumb{{flex:none;width:100%;min-height:0;aspect-ratio:16/9}}.card.h .body{{padding:10px 12px}}
.dur{{color:#fff;position:absolute;left:6px;bottom:6px;background:rgba(0,0,0,.78);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600}}
.badge{{position:absolute;left:6px;top:6px;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700;letter-spacing:.04em}}.badge.audio{{background:var(--accent);color:var(--on-accent)}}.badge.silent{{background:var(--amber);color:#14161b}}.badge.candid{{background:var(--blue);color:#14161b}}
.body{{padding:10px 12px 10px 0;min-width:0}}.body h4{{margin:0 0 6px;font:600 12.5px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;overflow-wrap:anywhere;color:var(--accent-text)}}
.q{{margin:0 0 4px;font-size:12.5px}}.s{{margin:0 0 4px;font-size:12.5px;color:var(--txt2)}}.quote{{margin:0 0 4px;font-size:12px;color:var(--mut);font-style:italic}}
.meta{{margin:0;font-size:11px;color:var(--mut)}}.flag{{display:inline-block;background:var(--ink3);border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--amber-text);font-size:10px;margin-left:2px}}
footer{{padding:24px 32px 48px;color:var(--mut);font-size:12.5px}}footer code{{color:var(--txt)}}
@media(max-width:640px){{header,.sec,.covwrap,footer{{padding-left:16px;padding-right:16px}}header h1{{font-size:30px}}.grid{{grid-template-columns:1fr}}.stats{{gap:18px}}}}
</style></head><body>
<header><h1>{esc(NAME)} <span>Start Here</span></h1>
  <div class="stats"><div><b>{n_clips}</b>clips</div><div><b>{dur(total_s)}</b>total</div><div><b>{n_int}</b>interview answers</div><div><b>{n_broll}</b>b-roll clips</div><div><b>{n_silent}</b>silent clips</div>{cut_stats}<div><b>{esc(CAM) or '-'}</b>camera</div><div><b>{esc(DATE) or '-'}</b>shoot</div></div>
  <div class="tools"><input type="search" id="q" aria-label="Search the footage" placeholder="Search anything said or seen"><button type="button" data-f="all" aria-pressed="true">All</button><button type="button" data-f="audio" aria-pressed="false">With audio</button><button type="button" data-f="silent" aria-pressed="false">No audio</button><button type="button" data-f="candid" aria-pressed="false">Candid talk</button></div>
  <p class="shown" id="shown" aria-live="polite"></p>
</header>
{cov}
{section("Interview: on-camera answers", int_keys, "Grouped by question. Take numbers follow shoot order. Click a thumbnail to open the clip.")}
{section("B-Roll: by what is on screen", broll_keys, "Amber badge = the camera recorded no audio. Blue badge = real conversation on the clip (not an interview answer).")}
{section("Everything else", other_keys)}
<footer>{esc(NAME)}{(', shoot ' + esc(DATE)) if DATE else ''}. Organized {esc(ORGANIZED)}. Files were {how}; every original name is kept as the clip prefix. <code>Footage Manifest.csv</code> maps each file back to its original and carries the full notes. Camera sidecars sit in <code>Camera Sidecars/</code>.</footer>
<script>
const q=document.getElementById('q'),cards=[...document.querySelectorAll('.card')],btns=[...document.querySelectorAll('.tools button')],shown=document.getElementById('shown');let f='all';
function apply(){{const t=q.value.trim().toLowerCase();let n=0;cards.forEach(c=>{{const ok=(!t||c.dataset.search.includes(t))&&(f==='all'||c.dataset.audio===f);c.classList.toggle('hide',!ok);if(ok)n++}});document.querySelectorAll('.sec h3').forEach(h=>{{const g=h.nextElementSibling;const any=[...g.children].some(c=>!c.classList.contains('hide'));h.style.display=any?'':'none';g.style.display=any?'':'none'}});shown.textContent=(t||f!=='all')?`Showing ${{n}} of ${{cards.length}}`:''}}
q.addEventListener('input',apply);btns.forEach(b=>b.addEventListener('click',()=>{{btns.forEach(x=>x.setAttribute('aria-pressed','false'));b.setAttribute('aria-pressed','true');f=b.dataset.f;apply()}}));
</script></body></html>"""
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(page)
    if not quiet:
        print("index ->", OUT, f"({os.path.getsize(OUT) // 1024} KB)")
    return OUT


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    return build(load_job(a.job), a.out)


if __name__ == "__main__":
    main()
