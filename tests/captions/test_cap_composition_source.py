"""The caption composition (remotion/captions/): what its source holds, that it type checks, and what it really draws.

The source tests read the composition's files. The type check, the registered composition, the font failure and the stills run the real TypeScript compiler and the real
renderer in a throwaway project (cap_remotion.project), so they skip, with one plain reason, where Remotion or tsc is not installed. The stills draw rows in the upstatic
fixture look's own faces over a black picture and measure the ink against that bare backdrop (the technique preview_style.ink uses): the width a row is drawn at must follow
the renderer's width cap, floor(budget / (letters x factor)), checked against the face's own advance, with the factors the plan carries in `glyph` and with the renderer's own
when it carries none. A tagged logo whose mark the composition does not ship must draw the row without the mark and not stop the render.
"""
import json
import os
import re
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageFont

import cap_helpers
import cap_remotion
import cluster_plan
import glyph
import takectl
from cap_remotion import needs_remotion, needs_tsc

SOURCE = os.path.join(cap_helpers.REPO, "remotion", "captions")
FILES = ("CaptionsDynamic.tsx", "register.ts", "softthought.tsx", "softmotion.ts", "Icons.tsx")


def src(name):
    with open(os.path.join(SOURCE, name), encoding="utf-8") as fh:
        return fh.read()


def code_only(text):
    """The source without // and /* */ comments."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)(?<!:)//.*$", "", text)


def clean_room_pattern():
    """The engine's own clean-room word list, read out of check/clean.sh when the test runs: the words live in that one place and are never repeated here."""
    with open(os.path.join(cap_helpers.REPO, "check", "clean.sh"), encoding="utf-8") as fh:
        found = re.search(r'(?m)^PATTERN = re\.compile\(r"(.+)"\)\s*$', fh.read())
    assert found, 'check/clean.sh no longer has a PATTERN = re.compile(r"...") line to read the banned words from'
    assert found.group(1).count("|") >= 5, "the banned word list in check/clean.sh looks empty"
    return re.compile(found.group(1))


def fixture_brand_marks():
    """(names, colors) that identify the two fixture brands: their ids, names, handles and font families, and every color of their DNA that is not also one of the
    engine's own neutral defaults (templates/dna.json: white, the default negative red, ...)."""
    with open(os.path.join(cap_helpers.REPO, "templates", "dna.json"), encoding="utf-8") as fh:
        neutral = {c.lower() for c in json.load(fh)["colors"].values()}
    names, colors = set(), set()
    for brand in cap_helpers.BRANDS:
        with open(os.path.join(cap_helpers.FIXTURES, "brands", brand, "dna.json"), encoding="utf-8") as fh:
            dna = json.load(fh)
        names |= {dna["id"], dna["name"], dna["handle"].lstrip("@")} | {f["family"] for f in dna["fonts"].values() if f}
        colors |= {c.lower() for c in dna["colors"].values()} - neutral
    return names, colors


# --- the source -------------------------------------------------------------------------------------------------------------------------------
def test_no_house_word_brand_name_or_brand_color_is_written_in_the_composition_source():
    """Fonts, colors and names reach the composition only through the plan. Every file of it is held to the engine's clean-room word list (check/clean.sh), and its code
    (comments aside) names neither of the two fixture brands, nor one of their fonts, nor hard-codes one of their colors."""
    pattern = clean_room_pattern()
    names, colors = fixture_brand_marks()
    assert names and colors
    for name in FILES:
        text = src(name)
        hit = pattern.search(text)
        assert not hit, f"{name} names {hit.group(0)!r}, which check/clean.sh bans"
        code = code_only(text)
        for token in sorted(names):
            assert not re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", code, re.I), f"{name} names the fixture brand's {token!r}"
        for color in sorted(colors):
            assert color not in code.lower(), f"{name} hard-codes the fixture brand color {color}"
    assert "fontsDir" not in code_only(src("CaptionsDynamic.tsx"))


def test_the_soft_drawing_imports_only_react_remotion_and_softmotion():
    """softthought.tsx takes nothing from its host but its props"""
    code = code_only(src("softthought.tsx"))
    froms = re.findall(r"""(?m)^\s*(?:import|export)\b[^;]*?\bfrom\s+["']([^"']+)["']""", code)
    assert froms and set(froms) <= {"react", "remotion", "./softmotion"}, froms
    assert not re.search(r"\brequire\(|\bimport\(", code)


def test_a_failed_font_load_cancels_the_render_instead_of_continuing():
    text = src("CaptionsDynamic.tsx")
    body = text[text.index("function loadFonts"):text.index("type LWord")]
    assert "cancelRender" in body and ".catch((err: unknown) => cancelRender(err))" in body
    assert "continueRender(h)" in body and body.count("continueRender") == 1       # only on success
    assert "failed to load" in body and "face.src" in body                          # the message names the file


def test_the_running_face_reaches_cluster_rows_not_just_single_words():
    text = src("CaptionsDynamic.tsx")
    assert re.search(r"fontFamily: runFont && !w\.hero \? runFont : font", text)
    assert text.count("runFont={runFont}") == 2                                      # behind and front lockups


def test_register_exports_one_composition_called_captions_at_1080_by_1920_and_30_fps():
    """register.ts is the one import a project's Root makes. Its config is the composition every render names: id Captions, 1080 x 1920, 30 fps, and a length that
    comes from the plan's durationInFrames."""
    code = code_only(src("register.ts"))
    config = code[code.index("export const captions"):]
    for key, value in (("id", '"Captions"'), ("width", "1080"), ("height", "1920"), ("fps", "30")):
        assert re.search(rf"(?m)^\s*{key}: {re.escape(value)},", config), f"register.ts does not set {key}: {value}"
    assert re.search(r"durationInFrames: props\.durationInFrames \?\? \d+", code), "the length of a render must come from the plan's durationInFrames"
    assert "calculateMetadata: captionsMetadata" in config


# --- the compiler and the renderer's own listing -----------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """the throwaway Remotion project: the composition copied out of remotion/captions/, the renderer's node_modules linked in (skips where Remotion is not installed)"""
    return cap_remotion.project(tmp_path_factory.mktemp("project"))


@needs_tsc
def test_the_composition_type_checks(project):
    """register.ts, CaptionsDynamic.tsx, Icons.tsx, softthought.tsx and softmotion.ts, with the project's own Root and index, through the real compiler"""
    r = subprocess.run([cap_remotion.TSC_BIN, "--noEmit", "-p", project], capture_output=True, text=True, cwd=project, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_tsc
def test_the_type_check_really_reads_the_composition(project, tmp_path):
    """a type error planted in a copy of register.ts is reported by name, so a pass above is not a compiler that looked at nothing"""
    copy = cap_remotion.project(tmp_path / "planted")
    with open(os.path.join(copy, "captions", "register.ts"), "a", encoding="utf-8") as fh:
        fh.write('\nexport const planted: number = "not a number";\n')
    r = subprocess.run([cap_remotion.TSC_BIN, "--noEmit", "-p", copy], capture_output=True, text=True, cwd=copy, timeout=300)
    assert r.returncode != 0 and "register.ts" in r.stdout + r.stderr, r.stdout + r.stderr


@needs_remotion
def test_remotion_lists_the_captions_composition_at_1080_by_1920_and_30_fps(project):
    r = subprocess.run([cap_remotion.REMOTION_BIN, "compositions", "index.ts"], capture_output=True, text=True, cwd=project, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    listed = re.findall(r"(?m)^(\S+)\s+(\d+)\s+(\d+)x(\d+)\s+\d+ \(", r.stdout + r.stderr)
    assert listed == [("Captions", "30", "1080", "1920")], r.stdout[-300:]


def ffmpeg_clip(path, color="black", seconds=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={color}:size=270x480:rate=30:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


@needs_remotion
def test_a_render_with_a_missing_font_file_fails_and_names_the_file(project, tmp_path):
    """the plan loads a font file the render's public folder does not hold (nothing was staged): the composition cancels the render and names the file, so a missing font can
    never ship as a silent system-font fallback"""
    spec = cap_helpers.spec("varsans")
    missing = spec["font"]["faces"][0]["src"]
    pub = tmp_path / "public"
    pub.mkdir()
    ffmpeg_clip(pub / "src.mp4", seconds=1)
    props = {"src": "src.mp4", "durationInFrames": 30, "accent": spec["accent"], "font": {"family": spec["font"]["family"], "faces": spec["font"]["faces"]},
             "events": [{"mode": "front", "cluster": True, "start": 0, "end": 1, "y": 900, "leading": 0.84,
                         "words": [{"text": "hello", "at": 0, "size": 120, "color": "#FFFFFF", "dx": 0, "weight": 800}]}]}
    (tmp_path / "props.json").write_text(json.dumps(props), encoding="utf-8")
    out = tmp_path / "out.mp4"
    r = subprocess.run([cap_remotion.REMOTION_BIN, "render", "index.ts", "Captions", str(out), f"--props={tmp_path / 'props.json'}", f"--public-dir={pub}",
                        "--frames=0-5", "--log=error"], capture_output=True, text=True, cwd=project, timeout=300)
    said = r.stdout + r.stderr
    assert r.returncode != 0 and not out.exists(), "the render went on without the font"
    assert os.path.basename(missing) in said and "failed to load" in said, said[-600:]


# --- the width cap: the plan's measured factors, and the renderer's own without them ------------------------------------------------------------
# The composition caps every cluster row's size at floor(budget / max(floor, letters x factor + marks)). The planner measures cluster rows itself, but the CTA and the page word it
# does not, and the cap is the only fit they get. These render real stills of the upstatic look's own faces (a static display face, a variable running face) over a black picture and
# measure the ink: the width must follow the formula, checked against the face's own advance, with the plan's `glyph` factors and with the renderer's own when the plan has none.
BUDGET = 960
PAGE_BUDGET = 950                                        # the page word's own budget (PAGE_MAXW in the composition: a page has no video under it)
# (text, planned size, hero, color): every row is planned far over its cap, so the cap sets the size drawn. A hero is drawn in the display face, the other row in the running face.
ROWS = [("RESPONSIBILITY", 400, True, (255, 255, 255)), ("ADS", 500, True, (0, 0, 255)), ("WHAT'S THE WHAT'S THE", 300, False, (255, 0, 0))]


def row_events(rows=ROWS):
    return [{"mode": "front", "cluster": True, "start": 0, "end": 2, "y": 300, "leading": 1.5, "maxW": BUDGET, "exit": "fade",
             "words": [{"text": t, "at": 0, "size": s, "color": "#%02X%02X%02X" % c, "dx": 0, "weight": 800, "hero": h} for t, s, h, c in rows]}]


def renderer_numbers():
    """the renderer's own width numbers, read out of the composition's `const` lines: GLYPH_CAPS and GLYPH_RUN (the factors a plan without `glyph` gets) and FLOOR_LETTERS"""
    return cluster_plan.renderer_numbers(names=("GLYPH_CAPS", "GLYPH_RUN", "FLOOR_LETTERS", "MARK_LOGO_EM"))


def cap(budget, text, per, floor_em):
    return int(budget // max(floor_em, len(text) * per))


def browser_axis(axis, size, weight):
    """what the browser sets one variation axis of a font to: the weight on the weight axis, the size in px on the optical size axis (font-optical-sizing is auto, so a big
    row is drawn with the font's display cut, kept inside the axis range), the font's own default on any other"""
    name = (axis["name"].decode("latin-1") if isinstance(axis["name"], bytes) else str(axis["name"])).lower()
    if "optical" in name or name == "opsz":
        return max(axis["minimum"], min(axis["maximum"], size))
    return glyph.axis_value(axis, weight)


def model(face, text, size, weight, tracking, word_spacing=0.0):
    """the width the browser draws: the face's advance (PIL, at the weight and the optical size) plus the composition's letter-spacing and word-spacing in em"""
    f = ImageFont.truetype(face, size)
    try:
        f.set_variation_by_axes([browser_axis(a, size, weight) for a in f.get_variation_axes()])
    except OSError:
        pass                                                      # a static font: every weight is the one file
    return f.getlength(text) + (tracking * len(text) + word_spacing * text.count(" ")) * size


def ink_width(mask):
    xs = np.where(mask.any(axis=0))[0]
    return int(xs.max() - xs.min())


def bands(mask, gap=8):
    ys = np.where(mask.any(axis=1))[0]
    return [(int(a[0]), int(a[-1])) for a in np.split(ys, np.where(np.diff(ys) > gap)[0] + 1) if len(a)]


def masks(im):
    r, g, b = im[:, :, 0], im[:, :, 1], im[:, :, 2]
    return {(255, 255, 255): (r > 200) & (g > 200) & (b > 200), (0, 0, 255): (b > 200) & (r < 60) & (g < 60), (255, 0, 0): (r > 200) & (g < 60) & (b < 60)}


def glyph_variants(spec):
    """{label: the plan's `glyph` prop}: none (the renderer's own factors), the look's measured factors, and a face a fifth narrower than the look's, so its rows come out clearly bigger"""
    measured = spec["glyph"]
    return {"renderer's own": None, "measured": measured, "narrower": {k: round(v * 0.8, 4) for k, v in measured.items()}}


@pytest.fixture(scope="module")
def stills(tmp_path_factory, project):
    """(still, spec): still(name, events, frame, factors=None, project=None, **props) is that frame of the Captions composition over a black picture, in the upstatic look's
    faces (staged into a public folder the way a render stages them, takectl.stage_fonts_for), as an RGB array. Each distinct plan and frame is drawn once for the module."""
    root = tmp_path_factory.mktemp("stills")
    pub = root / "public"
    pub.mkdir()
    ffmpeg_clip(pub / "src.mp4")
    look, spec = cap_helpers.profile("upstatic"), cap_helpers.spec("upstatic")
    faces = {"font": {"family": spec["font"]["family"], "faces": spec["font"]["faces"]},
             "wordFont": {"family": spec["look"]["word_font"]["family"], "faces": spec["look"]["word_font"]["faces"]}}
    takectl.stage_fonts_for(look, faces, str(pub))
    done = {}

    def still(name, events, frame, factors=None, project=project, **extra):
        props = {"src": "src.mp4", "durationInFrames": 60, "accent": spec["accent"], "base": "#FFFFFF", **faces, "events": events, **extra}
        if factors:
            props["glyph"] = factors
        key = (project, frame, json.dumps(props, sort_keys=True))
        if key not in done:
            path, out = root / f"{name}-{len(done)}.json", root / f"{name}-{len(done)}.png"
            path.write_text(json.dumps(props), encoding="utf-8")
            r = subprocess.run([cap_remotion.REMOTION_BIN, "still", "index.ts", "Captions", str(out), f"--frame={frame}", f"--props={path}", f"--public-dir={pub}",
                                "--image-format=png", "--log=error"], capture_output=True, text=True, cwd=project, timeout=300)
            assert r.returncode == 0 and out.exists(), r.stdout + r.stderr
            im = np.array(Image.open(out).convert("RGB")).astype(int)
            assert im.shape == (1920, 1080, 3), "the composition draws 1080 x 1920"
            done[key] = im
        return done[key]

    return still, spec


@needs_remotion
def test_a_cluster_rows_width_cap_follows_the_plans_factors_and_the_renderers_own_without_them(stills):
    still, spec = stills
    n = renderer_numbers()
    display, running = spec["font"]["measure"], spec["look"]["word_font"]["measure"]
    drawn = {}
    for label, factors in glyph_variants(spec).items():
        found = masks(still("rows", row_events(), 20, factors))
        floor_em = n["FLOOR_LETTERS"] * (factors["caps"] if factors else n["GLYPH_CAPS"])
        for text, planned, hero, color in ROWS:
            per = (factors["caps"] if hero else factors["word"]) if factors else n["GLYPH_CAPS"]
            size = min(planned, cap(BUDGET, text, per, floor_em))
            want = model(display if hero else running, text, size, 800, -0.04, 0.06)
            got = ink_width(found[color])
            assert abs(got / want - 1) < 0.03, f"{text!r} with the {label} factors: cap size {size}, model {want:.0f} px, drawn {got} px"
            if label == "measured":
                assert got <= BUDGET, f"{text!r} is drawn {got} px wide with the look's own measured factors, over its {BUDGET} px budget"
            drawn[(text, label)] = got
    for text, *_ in ROWS:
        assert drawn[(text, "narrower")] > 1.1 * drawn[(text, "renderer's own")], f"{text!r} did not get bigger on a narrower face's factors"


@needs_remotion
def test_the_planners_row_cap_is_the_size_the_composition_really_draws(stills):
    """cluster_plan reads its cap out of this composition (renderer_cap, from the `const` lines) and lays its blocks out by it. The judge here is the renderer: three rows planned far
    over the cap are drawn in the look's real faces on a black picture, and the ink must be as wide as the size the planner says they are drawn at, with the renderer's own factors and
    with the plan's. A short hero is held to the short-hero cap however big the plan asks."""
    still, spec = stills
    display, running = spec["font"]["measure"], spec["look"]["word_font"]["measure"]
    for label, factors in glyph_variants(spec).items():
        found = masks(still("rows", row_events(), 20, factors))
        for text, planned, hero, color in ROWS:
            size = cluster_plan.row_drawn({"text": text, "size": planned, "hero": hero}, BUDGET, factors)
            assert size < planned, f"{text!r} is planned over the cap"
            if text == "ADS":
                assert size == cluster_plan.short_hero_cap(BUDGET, factors)
            want = model(display if hero else running, text, size, 800, -0.04, 0.06)
            got = ink_width(found[color])
            assert abs(got / want - 1) < 0.03, f"{text!r} with the {label} factors: the planner says {size} px (model {want:.0f} px wide), drawn {got} px"


@needs_remotion
def test_the_ctas_rows_and_the_page_word_take_their_width_cap_from_the_plans_factors_too(stills):
    """With a plan whose three factors all differ: the CTA's first row reads `caps`, its other rows and the page word read `hook`. Without a `glyph` they take the renderer's own.
    The three differing factors are the look's measured ones, each made a different amount wider (1.1, 1.2 and 1.3 times), so every row is drawn a little smaller than its face needs
    and must fit: the renderer's own factors promise a fit only for a face that measures like their reference, so no fit is asked of that leg."""
    still, spec = stills
    n = renderer_numbers()
    display = spec["font"]["measure"]
    wider = {k: round(v * m, 4) for (k, v), m in zip(spec["glyph"].items(), (1.1, 1.2, 1.3))}
    assert len(set(wider.values())) == 3
    cta = [{"mode": "cta", "start": 0, "end": 2, "y": 300, "maxW": BUDGET, "chevrons": False,
            "rows": [{"text": "FILL OUT THE FORM TODAY"}, {"text": "NO COST NO OBLIGATION NO CATCH"}]}]
    page = [{"mode": "page", "start": 0, "end": 2, "text": "RESPONSIBILITY", "slide": "none"}]
    for factors in (None, wider):
        label = "with" if factors else "without"
        caps, hook = (factors["caps"], factors["hook"]) if factors else (n["GLYPH_CAPS"], n["GLYPH_RUN"])
        floor_em = n["FLOOR_LETTERS"] * caps
        white = masks(still("cta", cta, 30, factors))[(255, 255, 255)]
        found = bands(white)
        assert len(found) == 2, found
        for (text, planned, weight, tracking, per), (y0, y1) in zip((("FILL OUT THE FORM TODAY", 190, 800, -0.03, caps), ("NO COST NO OBLIGATION NO CATCH", 74, 600, -0.01, hook)), found):
            size = min(planned, cap(BUDGET, text, per, floor_em))
            want = model(display, text, size, weight, tracking)
            got = ink_width(white[y0:y1 + 1])
            assert abs(got / want - 1) < 0.03, f"CTA {text!r} {label} a glyph prop: cap size {size}, model {want:.0f} px, drawn {got} px"
            if factors:
                assert got <= BUDGET, f"CTA {text!r} is drawn {got} px wide, over its {BUDGET} px budget"
        im = still("page", page, 30, factors, pageInk="#000000")        # black ink on the accent page: the page word is every pixel that is dark in all three channels
        size = min(150, cap(PAGE_BUDGET, "RESPONSIBILITY", hook, floor_em))
        want = model(display, "RESPONSIBILITY", size, 800, -0.03)
        got = ink_width((im[:, :, 0] < 60) & (im[:, :, 1] < 60) & (im[:, :, 2] < 60))
        assert abs(got / want - 1) < 0.03, f"page word {label} a glyph prop: cap size {size}, model {want:.0f} px, drawn {got} px"
        if factors:
            assert got <= PAGE_BUDGET, f"the page word is drawn {got} px wide, over its {PAGE_BUDGET} px budget"


# --- a mark the composition does not ship ------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def project_without_the_instagram_mark(tmp_path_factory):
    """a second throwaway project whose copy of Icons.tsx ships no `instagram` mark: the entry is deleted from its LOGO_PATHS (the repo's file is never touched)"""
    dest = cap_remotion.project(tmp_path_factory.mktemp("no-mark"))
    path = os.path.join(dest, "captions", "Icons.tsx")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    cut, hits = re.subn(r'(?m)^[ \t]*instagram:\s*"[^"]*",\n', "", text, count=1)
    assert hits == 1, "Icons.tsx has no `instagram:` entry in LOGO_PATHS for this test to take away"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cut)
    return dest


@needs_remotion
def test_a_row_tagged_with_a_logo_the_composition_does_not_ship_is_drawn_without_the_mark(stills, project_without_the_instagram_mark):
    """Icons.tsx lists the platform marks the composition draws, and a name with no entry draws no mark. A plan that still tags a row with that name (an older plan, a hand-written
    one) is drawn without the mark and the render does not stop: the row's own text is on screen at the size the cap gives it, and the same plan in a project that ships the mark is
    wider by about one mark (0.9 em and 0.18 em of air), so the missing mark is the entry's absence and not a plan that never draws one."""
    still, spec = stills
    display, factors = spec["font"]["measure"], spec["glyph"]
    row = {"text": "INSTAGRAM", "at": 0, "size": 400, "color": "#FFFFFF", "dx": 0, "weight": 800, "hero": True, "logo": "instagram"}
    events = [{"mode": "front", "cluster": True, "start": 0, "end": 2, "y": 300, "leading": 1.5, "maxW": BUDGET, "exit": "fade", "words": [row]}]
    size = cluster_plan.row_drawn(row, BUDGET, factors)
    assert 0 < size < row["size"]
    white = (255, 255, 255)
    with_mark = ink_width(masks(still("logo", events, 20, factors))[white])
    without_mark = ink_width(masks(still("logo", events, 20, factors, project=project_without_the_instagram_mark))[white])
    want = model(display, row["text"], size, 800, -0.04, 0.06)
    assert abs(without_mark / want - 1) < 0.03, f"the row's text is drawn {without_mark} px wide, the model says {want:.0f} px"
    assert 0.95 * size < with_mark - without_mark < 1.25 * size, f"the mark adds {with_mark - without_mark} px to a row drawn at {size} px"
