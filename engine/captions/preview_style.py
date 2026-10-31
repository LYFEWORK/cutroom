#!/usr/bin/env python3
"""preview_style.py <style-id> [--clip <video>] --out <jpg>

Draws one caption style as a still, so a style can be looked at before a take is spent on it. It runs the REAL planner (lookprofile, roomcheck, spec_adapter,
cluster_plan: the code a take runs) on one sample phrase in your look with the style laid over it, and the real composition (CaptionsDynamic, through
`npx remotion still`) draws that plan over a backdrop:

  without --clip   a stand-in speaker: a gray head and shoulders on #1a1a1a, drawn here (and the same bust as the person matte when the style puts the
                   words behind the head). No footage and no key are needed.
  with --clip      a frame of your own video: one face track on that frame gives the planner its face box (so the words sit where they would in a take), and
                   the frame is the backdrop (the person matte of a behind-the-head style is made from it with Apple Vision).

Writes <out> (a JPEG 480 px wide) and, beside it, the same still at full size as <out without .jpg>.png. The sample is "Done in 24 hours.": a numeral hero
(so the hero size a style sets shows), a row before it and a row after it. The soft build draws it as one thought, `Done in | 24 hours. [bar]`: a lead-in and
its numeral payoff on the bar, handed to the planner the way GATE.md hands it a person's lines (SOFT_SAMPLE).

A still is reused, not drawn again, when its fingerprint (the plan, the composition's files, your font files, the backdrop and this file) is the one recorded in
<out's folder>/stills.json. Exit 0 ok, 1 the still failed to draw, 2 setup (no renderer install, no ffmpeg, a style or DNA that does not load).
CUTROOM_REMOTION_DIR points at another Remotion project (one that registers the Captions composition); the default is remotion/ in the repo.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
ENGINE = os.path.dirname(HERE)
REPO = os.path.dirname(ENGINE)
for p in (ENGINE, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from keys import EngineStop  # noqa: E402

REMOTION = os.path.abspath(os.path.expanduser(os.environ.get("CUTROOM_REMOTION_DIR") or os.path.join(REPO, "remotion")))
CAPTIONS = os.path.join(REPO, "remotion", "captions")
COMPOSITION = os.path.join(CAPTIONS, "CaptionsDynamic.tsx")
SOFT_DRAWING = (os.path.join(CAPTIONS, "softthought.tsx"), os.path.join(CAPTIONS, "softmotion.ts"), os.path.join(CAPTIONS, "Icons.tsx"))   # the drawing the composition takes its helpers from
SIDECAR = "stills.json"
RENDER_VERSION = 1                     # bump when the way a still is drawn changes without any file above changing
FPS, CUT_S, FRAMES = 30, 2.5, 75
WIDTH = 480                            # the jpg is this wide (1080 x 1920 scaled down)
JPEG_QUALITY = 86
BG, HEAD, BODY = (0x1A, 0x1A, 0x1A), (0x96, 0x96, 0x9B), (0x6E, 0x6E, 0x74)
# the stand-in speaker's face box (the planner's brow-to-chin box): a head and shoulders shot with 550 px of safe room above the crown, so every style's lockup has the room
# it was made for, and 210 px under the chin, a tight chest band, so a style that sits in front of the speaker is drawn above the head, where the planner puts it on tight footage.
FACE = {"cx": 540, "cy": 1060, "w": 300, "h": 300}
# one phrase, one numeral hero between a row before and a row after: (text, start s, end s)
SAMPLE = (("Done", 0.40, 0.62), ("in", 0.66, 0.78), ("24", 0.82, 1.20), ("hours.", 1.24, 1.70))
# the sample as the soft build lays it out, in the form GATE.md's Thoughts lines take: a lead-in and the numeral payoff on the bar, so the still shows the two sizes,
# the lead-in stepping back, the count and the bar, the soft build's one emphasis (a person's lines with no [bar] draw none). Left to itself the splitter keeps four words
# spoken in 1.3 s on one line, which shows none of that
SOFT_SAMPLE = ("Done in | 24 hours. [bar]",)
INK_MIN = 600                          # pixels that differ from the bare backdrop at full size: below this the still drew no captions


class SetupError(Exception):
    """something the run needs is not there: one plain line, exit 2"""


# ------------------------------------------------------------------ the engine, loaded on demand

def engine():
    """the caption chain's own modules: lookprofile, spec_adapter, cluster_plan, roomcheck, facetrack, personmatte, takectl"""
    try:
        import cluster_plan
        import facetrack
        import lookprofile
        import personmatte
        import roomcheck
        import spec_adapter
        import takectl
    except Exception as e:   # noqa: BLE001
        raise SetupError(f"cannot load the caption chain's scripts from {HERE}: {e}")
    return lookprofile, cluster_plan, roomcheck, spec_adapter, facetrack, personmatte, takectl


def check_setup():
    if not os.path.isfile(os.path.join(REMOTION, "node_modules", ".bin", "remotion")):
        raise SetupError(f"the renderer is not installed: run npm ci in {REMOTION} (bash install.sh does it)")
    for tool in ("ffmpeg", "ffprobe", "npx"):
        if not shutil.which(tool):
            raise SetupError(f"{tool} is not installed")
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise SetupError("Pillow is not installed (pip install pillow)")


# ------------------------------------------------------------------ the sample, the backdrop and the plan

def words():
    return [{"text": t, "start": s, "end": e} for t, s, e in SAMPLE]


def track(face=None):
    """a face track the planner reads like a real one: the same box for every second of the sample"""
    box = dict(face or FACE)
    return {"W": 1080, "H": 1920, "samples": {str(s): dict(box) for s in range(4)}, "median": dict(box), "seconds": 4}


def plan_for(style_id, face=None):
    """-> (plan, tier doc, profile): the sample laid out by the chain's planner in your look with the style laid over it, the way run_captions.sh makes look.json, the
    tier cap, spec.json and plan.json. A soft build style gets the sample's thoughts (SOFT_SAMPLE) the way the gate hands a person's lines to the planner.
    EngineStop carries one plain line."""
    lookprofile, cluster_plan, roomcheck, spec_adapter = engine()[:4]
    profile = lookprofile.load(style_id)
    tier = roomcheck.tier_for(track(face), profile["caption_tier_max"])
    gate = {"overrides": {"devices": {"device_overrides": {"thoughts": list(SOFT_SAMPLE)}}}} if profile.get("soft_build") else None
    spec = spec_adapter.build(tier, profile, gate=gate)
    return cluster_plan.build(words(), track(face), spec, CUT_S, FRAMES), tier, profile


def still_frame(plan):
    """the frame the still is taken at: every word of the first cluster or thought is up (a thought's words arrive one by one, so its last word's own time counts) and the
    numeral has finished counting, before it leaves"""
    e = min(plan["events"], key=lambda ev: ev["start"])
    last = max(round(t * FPS) for r in e["words"] for t in (r.get("ats") or [r["at"]]))
    return int(min(round((e["end"] - 0.25) * FPS), last + 14))


def bust(size, scale=2):
    """-> (the picture, the person alone as RGBA): a head, neck and shoulders in the planner's own measures (crown 0.8 face heights above the face center, chin 0.5 below),
    drawn at `scale` times and brought down for a smooth edge. The RGBA is the person matte for a style that puts words behind the head"""
    from PIL import Image, ImageDraw
    w, h = size
    k = scale
    f = FACE
    crown, chin = f["cy"] - 0.8 * f["h"], f["cy"] + 0.5 * f["h"]
    person = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(person)
    shoulders_top = chin + 0.05 * f["h"]
    d.rounded_rectangle(((f["cx"] - 1.30 * f["h"]) * k, shoulders_top * k, (f["cx"] + 1.30 * f["h"]) * k, (h + 400) * k), radius=int(0.62 * f["h"] * k), fill=BODY + (255,))
    d.rectangle(((f["cx"] - 0.24 * f["h"]) * k, (chin - 0.2 * f["h"]) * k, (f["cx"] + 0.24 * f["h"]) * k, (shoulders_top + 0.15 * f["h"]) * k), fill=HEAD + (255,))
    d.ellipse(((f["cx"] - 0.46 * f["h"]) * k, crown * k, (f["cx"] + 0.46 * f["h"]) * k, chin * k), fill=HEAD + (255,))
    person = person.resize((w, h), Image.LANCZOS)
    picture = Image.new("RGB", (w, h), BG)
    picture.paste(person, (0, 0), person)
    return picture, person


def clip_of(picture_path, out):
    """a 3 s 1080 x 1920 clip of one picture: the source the composition draws over"""
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", picture_path, "-t", str(FRAMES / FPS + 0.5), "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "14", "-g", str(FPS), out], capture_output=True, text=True)
    if r.returncode:
        raise SetupError(f"ffmpeg could not make the backdrop clip: {r.stderr.strip()[-300:]}")


def own_frame(video, out_png):
    """one 1080 x 1920 frame of the user's own video (a vertical one is scaled, any other shape is cropped to fill), taken a third of the way in"""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video], capture_output=True, text=True)
    try:
        t = max(0.0, float(r.stdout.strip()) / 3)
    except ValueError:
        raise SetupError(f"{video} is not a video ffprobe can read")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                        out_png], capture_output=True, text=True)
    if r.returncode or not os.path.isfile(out_png):
        raise SetupError(f"ffmpeg could not take a frame of {video}: {r.stderr.strip()[-300:]}")


def face_of(frame_png, work, facetrack):
    """the face box of one frame (cx, cy, w, h in 1080 x 1920), or None when Apple Vision finds no face in it"""
    one = os.path.join(work, "one-frame.mp4")
    clip_of(frame_png, one)
    track_json = os.path.join(work, "one-frame-face.json")
    t = facetrack.build(one, track_json)
    m = t["median"]
    return None if m.get("fallback") else {k: m[k] for k in ("cx", "cy", "w", "h")}


# ------------------------------------------------------------------ fingerprints: a still is drawn again only when something it is made of changed

def _file_sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    except OSError:
        return "missing"


def fingerprint(plan, profile, backdrop):
    """16 hex digits over what a still is made of: the plan (so any change in a style, your look or the planner), the composition's files, your font files, the
    backdrop (the stand-in, or your clip's path, size and time) and this file"""
    fonts = sorted(set(list((profile.get("font_sources") or {}).values())))
    h = hashlib.sha1()
    for part in (json.dumps(plan, sort_keys=True), "".join(_file_sha(p) for p in (COMPOSITION, *SOFT_DRAWING)), "".join(_file_sha(p) for p in fonts),
                 backdrop, _file_sha(os.path.realpath(__file__)), str(RENDER_VERSION)):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def read_sidecar(out_dir):
    try:
        with open(os.path.join(out_dir, SIDECAR), encoding="utf-8") as fh:
            got = json.load(fh)
        return got["stills"] if isinstance(got, dict) and isinstance(got.get("stills"), dict) else {}
    except (OSError, ValueError, KeyError):
        return {}


def write_sidecar(out_dir, stills):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, SIDECAR)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "stills": dict(sorted(stills.items()))}, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------ one still

def ink(still_png, picture_png):
    """how many pixels of the full size still differ from the bare backdrop: the captions. A still that drew none is an error, never a blank picture"""
    from PIL import Image, ImageChops
    with Image.open(still_png) as a, Image.open(picture_png) as b:
        diff = ImageChops.difference(a.convert("L"), b.convert("L")).point(lambda v: 255 if v > 48 else 0)
        return diff.histogram()[255]


def draw(style_id, plan, look, work, pub, out_jpg, out_png, person, picture_png):
    """one still: the composition draws the plan's frame over the backdrop, the full-size png and the 480 px jpg are saved. -> None, or one plain line of what went wrong"""
    from PIL import Image
    takectl = engine()[6]
    props = json.loads(json.dumps(plan))
    props["src"] = "src.mp4"
    takectl.stage_fonts_for(look, props, pub)
    frame = still_frame(plan)
    windows = (props.get("matte") or {}).get("windows") or []
    if windows:
        mdir = os.path.join(pub, "matte")
        shutil.rmtree(mdir, ignore_errors=True)
        os.makedirs(mdir)
        person.save(os.path.join(mdir, f"f{frame:05d}.webp"), "WEBP", lossless=True)
        props["matte"]["dir"] = "matte"
    pj = os.path.join(work, f"{style_id}-props.json")
    with open(pj, "w", encoding="utf-8") as fh:
        json.dump(props, fh)
    png = os.path.join(work, f"{style_id}.png")
    try:
        r = subprocess.run(["npx", "remotion", "still", "index.ts", "Captions", png, f"--props={pj}", f"--public-dir={pub}", f"--frame={frame}", "--log=error"],
                           cwd=REMOTION, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "the renderer did not finish in 5 minutes"
    if r.returncode or not os.path.isfile(png) or not os.path.getsize(png):
        return (r.stderr or r.stdout).strip()[-400:] or "remotion drew nothing"
    if ink(png, picture_png) < INK_MIN:
        return "the still has no captions in it (the composition drew the bare backdrop)"
    os.makedirs(os.path.dirname(os.path.abspath(out_jpg)), exist_ok=True)
    shutil.copyfile(png, out_png)
    tmp = out_jpg + ".tmp"
    with Image.open(png) as im:
        im.convert("RGB").resize((WIDTH, round(WIDTH * im.height / im.width)), Image.LANCZOS).save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    os.replace(tmp, out_jpg)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="preview_style.py", description="Draw one caption style as a still.")
    ap.add_argument("style", help="a style id (python3 styles.py list)")
    ap.add_argument("--clip", help="a video of yours: its frame is the backdrop and its face the planner's face box (default: a drawn stand-in speaker)")
    ap.add_argument("--out", required=True, help="the 480 px jpg to write (the full-size png goes beside it)")
    ap.add_argument("--force", action="store_true", help="draw again even when the still is current")
    a = ap.parse_args(argv)
    out_jpg = os.path.abspath(os.path.expanduser(a.out))
    out_png = os.path.splitext(out_jpg)[0] + ".png"
    try:
        check_setup()
        if a.clip and not os.path.isfile(os.path.expanduser(a.clip)):
            raise SetupError(f"no video at {a.clip}")
        facetrack, personmatte = engine()[4], engine()[5]
        with tempfile.TemporaryDirectory(prefix="preview-style-") as work:
            face, person, picture_png, backdrop = None, None, os.path.join(work, "backdrop.png"), "stand-in"
            if a.clip:
                clip = os.path.abspath(os.path.expanduser(a.clip))
                own_frame(clip, picture_png)
                face = face_of(picture_png, work, facetrack)
                backdrop = f"clip:{clip}:{os.path.getsize(clip)}:{os.path.getmtime(clip)}"
            else:
                picture, person = bust((1080, 1920))
                picture.save(picture_png)
            plan, _, profile = plan_for(a.style, face)
            fp = fingerprint(plan, profile, backdrop)
            known = read_sidecar(os.path.dirname(out_jpg))
            key = os.path.basename(out_jpg)
            if not a.force and known.get(key) == fp and os.path.isfile(out_jpg) and os.path.isfile(out_png):
                print(f"reused {out_jpg} (current)")
                return 0
            pub = os.path.join(work, "public")
            os.makedirs(pub)
            clip_of(picture_png, os.path.join(pub, "src.mp4"))
            windows = (plan.get("matte") or {}).get("windows") or []
            if windows and person is None:
                # a style that puts words behind the head needs the person matte of the user's own frame: Apple Vision makes it from that frame
                frame = still_frame(plan)
                mdir = os.path.join(work, "own-matte")
                personmatte.build(os.path.join(pub, "src.mp4"), [[frame, frame]], mdir)
                from PIL import Image
                person = Image.open(os.path.join(mdir, f"f{frame:05d}.webp")).convert("RGBA")
            err = draw(a.style, plan, profile, work, pub, out_jpg, out_png, person, picture_png)
            if err:
                print(f"preview_style: {a.style}: {err}", file=sys.stderr)
                return 1
            known[key] = fp
            write_sidecar(os.path.dirname(out_jpg), known)
            print(f"wrote {out_jpg} ({os.path.getsize(out_jpg) // 1024} KB) and {out_png}")
            return 0
    except (SetupError, EngineStop, ValueError) as e:   # ValueError: a style file that does not load (styles.StyleError)
        print(f"STOP: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
