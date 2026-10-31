"""takectl.py: the small JSON and file checks run_captions.sh makes between stages (paths always arrive as arguments): the take's status, its cut length, the name it delivers
under and the folder it delivers to, the caption style it asks for and records, the font files its render stages, and the guard that keeps finished videos out of the install."""
import filecmp
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

import cap_helpers
import dna
import lookprofile
import takectl
from golden_pipeline import outputs
from keys import EngineStop

SCRIPT = os.path.join(cap_helpers.REPO, "engine", "captions", "takectl.py")


def run(*args, env=None):
    return subprocess.run([sys.executable, "-B", SCRIPT, *map(str, args)], capture_output=True, text=True, env=dict(os.environ, **(env or {})))


def write(path, obj):
    json.dump(obj, open(path, "w"))
    return str(path)


def video(path, frames=60, size="216x384"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={frames / 30}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


@pytest.fixture
def put_brand(workspace):
    """put_brand(id) installs a fixture Brand/ into the shared workspace; the workspace has no Brand/ again afterwards"""
    yield lambda brand_id, edit=None: cap_helpers.install_brand(workspace, brand_id, edit)
    shutil.rmtree(os.path.join(workspace, "Brand"), ignore_errors=True)


@pytest.fixture
def no_brand(workspace):
    shutil.rmtree(os.path.join(workspace, "Brand"), ignore_errors=True)


def test_status_writes_the_documented_shape(tmp_path):
    assert run("status", tmp_path, "gate", "false", "Open it").returncode == 0
    d = json.load(open(tmp_path / "status.json"))
    assert d["stage"] == "gate" and d["ok"] is False and d["message"] == "Open it" and d["updated"].startswith("20")
    run("status", tmp_path, "deliver", "true", "ok")
    assert json.load(open(tmp_path / "status.json"))["ok"] is True


def test_a_message_with_quotes_and_apostrophes_survives(tmp_path):
    msg = "Sam's \"Acme\" take, $HOME `x`"
    run("status", tmp_path, "look", "false", msg)
    assert json.load(open(tmp_path / "status.json"))["message"] == msg


def test_valid_is_true_only_for_a_non_empty_json_file(tmp_path):
    good, empty, text = write(tmp_path / "g.json", {"a": 1}), tmp_path / "e.json", tmp_path / "t.json"
    empty.write_text("")
    text.write_text("STOP: a half written failure")
    assert run("valid", good).returncode == 0
    assert run("valid", empty).returncode == 1 and run("valid", text).returncode == 1 and run("valid", tmp_path / "nope.json").returncode == 1


def test_get_reads_dotted_keys_and_list_indexes_and_falls_back_to_the_default(tmp_path):
    p = write(tmp_path / "d.json", {"a": {"b": "x y"}, "n": [2160, 3840], "flag": True, "e": ""})
    assert run("get", p, "a.b").stdout == "x y\n"
    assert run("get", p, "n.0").stdout == "2160\n" and run("get", p, "n.1").stdout == "3840\n"
    assert run("get", p, "flag").stdout == "true\n"
    assert run("get", p, "missing", "dflt").stdout == "dflt\n"
    assert run("get", p, "missing", "").stdout == "\n"
    r = run("get", p, "missing")
    assert r.returncode == 2 and r.stderr.startswith("STOP:") and r.stdout == ""


def test_nwords_counts(tmp_path):
    assert run("nwords", write(tmp_path / "w.json", {"words": [{}, {}, {}]})).stdout == "3\n"
    assert run("nwords", write(tmp_path / "z.json", {"words": []})).stdout == "0\n"


def test_cut_and_frames_never_run_past_the_mezzanine(tmp_path):
    video(tmp_path / "mez.mp4", frames=60)                       # 2.0 s
    write(tmp_path / "words.json", {"words": [{"text": "hi", "start": 0.2, "end": 1.0}]})
    assert takectl.cut_and_frames(str(tmp_path)) == (1.3, 60)    # last word end + 0.3
    write(tmp_path / "words.json", {"words": [{"text": "hi", "start": 0.2, "end": 1.9}]})
    cut, frames = takectl.cut_and_frames(str(tmp_path))          # speech ends 0.1 s before the clip: 0.3 s of tail would overrun
    assert frames == 60 and cut == 2.0
    assert run("cutframes", tmp_path).stdout == "2.0 60\n"


def test_a_4k_takes_1080_mezzanine_is_the_one_the_frames_are_counted_on(tmp_path):
    video(tmp_path / "mez.mp4", frames=90)
    video(tmp_path / "mez1080.mp4", frames=60)
    write(tmp_path / "words.json", {"words": [{"text": "hi", "start": 0.2, "end": 1.0}]})
    assert takectl.cut_and_frames(str(tmp_path)) == (1.3, 60)


def test_cut_and_frames_stops_when_nobody_spoke_or_there_is_no_mezzanine(tmp_path):
    write(tmp_path / "words.json", {"words": [{"text": "hi", "start": 0.2, "end": 1.0}]})
    with pytest.raises(EngineStop, match="mezzanine"):
        takectl.cut_and_frames(str(tmp_path))
    video(tmp_path / "mez.mp4")
    write(tmp_path / "words.json", {"words": []})
    with pytest.raises(EngineStop, match="no speech"):
        takectl.cut_and_frames(str(tmp_path))
    r = run("cutframes", tmp_path)
    assert r.returncode == 2 and r.stderr.startswith("STOP: no speech detected") and r.stdout == ""


def test_same_source_by_path_by_link_and_by_content(tmp_path):
    vid = video(tmp_path / "a.mp4")
    sha = hashlib.sha256(open(vid, "rb").read()[:64 * 1024 * 1024]).hexdigest()
    take = tmp_path / "take"
    take.mkdir()
    write(take / "intake.json", {"source_original": vid, "size_bytes": os.path.getsize(vid), "sha256_64mb": sha})
    assert run("same-source", vid, take).returncode == 0
    link = tmp_path / "link.mp4"
    os.symlink(vid, link)
    assert run("same-source", link, take).returncode == 0                       # a symlinked spelling
    twin = tmp_path / "twin.mp4"
    twin.write_bytes(open(vid, "rb").read())
    assert run("same-source", twin, take).returncode == 0                       # the same bytes elsewhere
    other = video(tmp_path / "b.mp4", frames=45)
    r = run("same-source", other, take)
    assert r.returncode == 1 and r.stdout.strip() == "other"
    assert run("same-source", tmp_path / "gone.mp4", take).returncode == 1


def test_same_path_with_new_bytes_is_an_edited_source_not_the_same_one(tmp_path):
    """A cut re-exported over the file the take was built from."""
    vid = video(tmp_path / "a.mp4")
    sha = hashlib.sha256(open(vid, "rb").read()).hexdigest()
    take = tmp_path / "take"
    take.mkdir()
    write(take / "intake.json", {"source_original": vid, "size_bytes": os.path.getsize(vid), "sha256_64mb": sha})
    assert run("same-source", vid, take).returncode == 0
    video(tmp_path / "a.mp4", frames=45)                                        # same path, different picture
    r = run("same-source", vid, take)
    assert r.returncode == 1 and r.stdout.strip() == "edited"
    link = tmp_path / "link.mp4"
    os.symlink(vid, link)
    assert run("same-source", link, take).stdout.strip() == "edited"            # a symlinked spelling of the edited file too


def _delivered(dest, stem, take_dir):
    """A delivery as deliver.sh leaves it: the Build folder with its delivery.json, and the video beside it."""
    build = dest / "Build" / stem
    build.mkdir(parents=True)
    assert run("delivery-record", build, stem, take_dir).returncode == 0
    (dest / f"{stem}.mp4").write_bytes(b"video")


def test_a_delivered_name_is_the_title_case_name_with_no_date(tmp_path):
    assert takectl.delivery_name(str(tmp_path / "finals"), str(tmp_path / "work" / "sam-intro"), "sam-intro") == "Sam Intro"
    assert takectl.title_of("my-take_two") == "My Take Two" and takectl.title_of("ACME intro Q3") == "ACME Intro Q3"     # capitals and digits are kept
    assert takectl.title_of("my iPhone clip") == "My iPhone Clip" and takectl.title_of("-a--b__c-") == "A B C"
    r = run("delivery-name", tmp_path / "finals", tmp_path / "work" / "sam-intro", "sam-intro")
    assert r.returncode == 0 and r.stdout == "Sam Intro\n"


def test_two_take_folders_with_one_name_get_two_delivered_names_and_each_keeps_its_own(tmp_path):
    dest = tmp_path / "finals"
    a, b, c = tmp_path / "a" / "intro", tmp_path / "b" / "intro", tmp_path / "c" / "intro"
    for d in (a, b, c):
        d.mkdir(parents=True)
    assert takectl.delivery_name(str(dest), str(a), "intro") == "Intro"
    _delivered(dest, "Intro", a)
    assert takectl.delivery_name(str(dest), str(b), "intro") == "Intro Take 2"
    _delivered(dest, "Intro Take 2", b)
    assert takectl.delivery_name(str(dest), str(c), "intro") == "Intro Take 3"
    # delivering again is the same name for each take, whichever name came first
    assert takectl.delivery_name(str(dest), str(a), "intro") == "Intro"
    assert takectl.delivery_name(str(dest), str(b), "intro") == "Intro Take 2"
    assert run("delivery-name", dest, b, "intro").stdout.strip() == "Intro Take 2"
    # a take that has not delivered yet takes the first free name
    assert run("delivery-name", dest, c, "intro").stdout.strip() == "Intro Take 3"


def test_a_name_is_taken_in_any_case_and_by_a_video_nobody_recorded(tmp_path):
    dest = tmp_path / "finals"
    take = tmp_path / "work" / "x"
    take.mkdir(parents=True)
    dest.mkdir()
    (dest / "intro.MP4").write_bytes(b"someone else's file")                              # the disk is case-insensitive
    assert takectl.delivery_name(str(dest), str(take), "intro") == "Intro Take 2"
    (dest / "Build" / "INTRO TAKE 2").mkdir(parents=True)                                 # a Build folder of unknown origin is taken too
    assert takectl.delivery_name(str(dest), str(take), "intro") == "Intro Take 3"


def test_only_a_video_or_a_build_folder_takes_a_name(tmp_path):
    """the sidecars beside a video are named after it but are not videos: they never take a name on their own"""
    dest = tmp_path / "finals"
    take = tmp_path / "work" / "intro"
    take.mkdir(parents=True)
    dest.mkdir()
    (dest / "Intro - Transcript.md").write_text("x")
    (dest / "Intro - Caption.txt").write_text("x")
    (dest / "Intro.mov").write_bytes(b"x")
    assert takectl.delivery_name(str(dest), str(take), "intro") == "Intro"


def test_delivery_free_stops_on_another_takes_name_and_allows_its_own_and_a_free_one(tmp_path):
    dest = tmp_path / "finals"
    a, b = tmp_path / "a" / "intro", tmp_path / "b" / "intro"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    _delivered(dest, "Intro", a)
    assert run("delivery-free", dest, "Intro", a).returncode == 0                # the same take again
    r = run("delivery-free", dest, "Intro", b)
    assert r.returncode == 2 and "already used" in r.stderr and str(a) in r.stderr and r.stderr.startswith("STOP:") and r.stdout == ""
    assert run("delivery-free", dest, "intro", b).returncode == 2               # in any case
    assert run("delivery-free", dest, "Intro Take 2", b).returncode == 0         # a free name
    assert run("delivery-free", tmp_path / "nowhere", "Intro", b).returncode == 0   # no finished folder yet
    (dest / "Loose.mp4").write_bytes(b"x")                                      # a video with no record is somebody's
    assert run("delivery-free", dest, "Loose", b).returncode == 2


def test_delivery_record_names_the_take_and_its_real_folder(tmp_path):
    take = tmp_path / "work" / "my-take"
    take.mkdir(parents=True)
    link = tmp_path / "link"
    os.symlink(take, link)
    build = tmp_path / "Build" / "My Take"
    build.mkdir(parents=True)
    assert run("delivery-record", build, "My Take", link).returncode == 0
    rec = json.load(open(build / "delivery.json"))
    assert rec["name"] == "My Take" and rec["take"] == "link" and rec["take_dir"] == os.path.realpath(take)
    assert rec["delivered"].startswith("20") and rec["caption_style"] is None
    assert set(rec) == {"name", "take", "take_dir", "caption_style", "delivered"}, "a delivery record has no grade key"
    for spelling in (link, take):                                                          # a symlinked spelling of the folder is the same take
        assert takectl.delivery_name(str(tmp_path), str(spelling), "my-take") == "My Take"


def test_delivery_record_carries_the_caption_style_the_looks_was_made_with(tmp_path):
    take = tmp_path / "work" / "my-take"
    take.mkdir(parents=True)
    write(take / "look.json", cap_helpers.profile("varsans", "keyword-slam"))
    build = tmp_path / "Build" / "My Take"
    build.mkdir(parents=True)
    assert run("delivery-record", build, "My Take", take).returncode == 0
    assert json.load(open(build / "delivery.json"))["caption_style"] == "keyword-slam"


def test_render_key_follows_the_plan_the_audio_setting_and_the_font_bytes(tmp_path):
    font = tmp_path / "f.woff2"
    font.write_bytes(b"font one")
    write(tmp_path / "plan.json", {"events": []})
    write(tmp_path / "look.json", {"font_abs": {"400": str(font)}, "word_font": {"abs": {}}})
    k = run("render-key", tmp_path, "off").stdout
    assert k == run("render-key", tmp_path, "off").stdout
    assert run("render-key", tmp_path, "loudnorm").stdout != k
    font.write_bytes(b"font two")
    assert run("render-key", tmp_path, "off").stdout != k
    write(tmp_path / "plan.json", {"events": [1]})
    assert run("render-key", tmp_path, "off").stdout != run("render-key", tmp_path, "loudnorm").stdout


def test_render_key_follows_every_font_file_the_look_can_draw_with(tmp_path):
    """the display face, the running face and a face only font_sources lists (the one a style swapped out) all count; the same file named twice counts once"""
    files = {n: tmp_path / f"{n}.ttf" for n in ("display", "running", "swapped")}
    for n, p in files.items():
        p.write_bytes(n.encode())
    write(tmp_path / "plan.json", {"events": []})
    write(tmp_path / "look.json", {"font_abs": {"400": str(files["display"]), "800": str(files["display"])}, "word_font": {"abs": {"400": str(files["running"])}},
                                   "font_sources": {"brand-x/fonts/swapped.ttf": str(files["swapped"]), "brand-x/fonts/display.ttf": str(files["display"])}})
    before = run("render-key", tmp_path, "off").stdout
    for n, p in files.items():
        p.write_bytes(n.encode() + b" changed")
        after = run("render-key", tmp_path, "off").stdout
        assert after != before, f"the {n} face's bytes do not move the render key"
        p.write_bytes(n.encode())
        assert run("render-key", tmp_path, "off").stdout == before


def test_render_key_of_a_look_made_with_a_swapping_style_follows_the_face_the_style_swapped_out(tmp_path):
    """rose-running draws the words in the running face; the display face is only in font_sources, and the page text still uses it"""
    bd = cap_helpers.materialize("upstatic", str(tmp_path / "Brand"))
    look = lookprofile.build(dna.load(bd), bd, "rose-running")
    assert look["font_family"] == "InterRunDyn" and {os.path.basename(v) for v in look["font_abs"].values()} == {"Inter-Variable.ttf"}
    write(tmp_path / "look.json", look)
    write(tmp_path / "plan.json", {"events": []})
    before = run("render-key", tmp_path, "off").stdout
    with open(os.path.join(bd, "assets", "fonts", "Inter-ExtraBold.ttf"), "ab") as f:
        f.write(b"\0")
    assert run("render-key", tmp_path, "off").stdout != before


def test_render_key_survives_a_font_file_that_is_missing_and_changes_when_it_appears(tmp_path):
    font = tmp_path / "late.ttf"
    write(tmp_path / "plan.json", {"events": []})
    write(tmp_path / "look.json", {"font_abs": {"400": str(font)}})
    gone = run("render-key", tmp_path, "off")
    assert gone.returncode == 0 and gone.stdout.strip()
    font.write_bytes(b"here now")
    assert run("render-key", tmp_path, "off").stdout != gone.stdout


def test_loudnorm_filter_is_a_measured_linear_second_pass(tmp_path):
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=4", "-af", "volume=-12dB", str(wav)], check=True)
    out = run("loudnorm-filter", wav).stdout.strip()
    assert out.startswith("loudnorm=I=-16:TP=-2:LRA=8:measured_I=") and out.endswith(":linear=true")
    silent = tmp_path / "s.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "2", str(silent)], check=True)
    assert run("loudnorm-filter", silent).stdout.strip() == "loudnorm=I=-16:TP=-2:LRA=8"       # nothing to measure: single pass


def test_needs_matte_and_matte_sync(tmp_path):
    plan = write(tmp_path / "plan.json", {"matte": {"windows": [[10, 12]]}})
    assert run("needs-matte", plan).stdout == "1\n"
    assert run("needs-matte", write(tmp_path / "p2.json", {"matte": {"windows": []}})).stdout == "0\n"
    assert run("needs-matte", write(tmp_path / "p3.json", {})).stdout == "0\n"
    windows, matte = tmp_path / "w.json", tmp_path / "matte"
    assert run("matte-sync", plan, windows, matte).returncode == 1              # nothing built yet
    matte.mkdir()
    for n in (10, 11):
        (matte / f"f{n:05d}.webp").write_bytes(b"x")
    assert run("matte-sync", plan, windows, matte).returncode == 1              # frame 12 missing: a partial matte is removed
    assert not matte.exists()
    matte.mkdir()
    for n in (10, 11, 12):
        (matte / f"f{n:05d}.webp").write_bytes(b"x")
    assert run("matte-sync", plan, windows, matte).returncode == 0              # complete, same windows: kept
    assert matte.exists() and json.load(open(windows)) == [[10, 12]]
    plan2 = write(tmp_path / "plan2.json", {"matte": {"windows": [[10, 14]]}})
    assert run("matte-sync", plan2, windows, matte).returncode == 1             # windows changed: rebuilt
    assert not matte.exists() and json.load(open(windows)) == [[10, 14]]


def test_failed_gates_plan_sha_approved(tmp_path):
    card = write(tmp_path / "qc.json", {"gates": {"G-FACE": {"pass": True}, "G4": {"pass": False}, "G-ACCENT": {"pass": None}, "G10": {"pass": None, "skipped": True},
                                                  "G9": {"pass": False}}})
    assert run("failed-gates", card).stdout == "G4, G9\n"                       # a gate that was skipped or not measured did not fail
    a, b = write(tmp_path / "a.json", {"x": 1, "y": [1, 2]}), write(tmp_path / "b.json", {"y": [1, 2], "x": 1})
    assert run("plan-sha", a).stdout == run("plan-sha", b).stdout                # key order does not matter
    assert run("plan-sha", a).stdout != run("plan-sha", write(tmp_path / "c.json", {"x": 2, "y": [1, 2]})).stdout
    assert run("approved", write(tmp_path / "g.json", {"approved": True})).returncode == 0
    assert run("approved", write(tmp_path / "g2.json", {"approved": False})).returncode == 1
    assert run("approved", tmp_path / "none.json").returncode == 1


def test_guard_dest_refuses_the_install_and_allows_everything_else(tmp_path):
    repo = takectl.REPO
    assert os.path.realpath(repo) == os.path.realpath(cap_helpers.REPO)
    for inside in (repo, os.path.join(repo, "Finished", "Sam Intro"), os.path.join(repo, "engine", "captions")):
        r = run("guard-dest", inside)
        assert r.returncode == 2 and r.stderr.startswith("STOP: refusing to write finished videos inside the Cutroom install") and "--dest" in r.stderr and r.stdout == ""
    assert run("guard-dest", tmp_path / "finals").returncode == 0
    assert run("guard-dest", repo + "-elsewhere").returncode == 0               # a sibling with the same prefix is not inside it
    link = tmp_path / "sneaky"
    os.symlink(os.path.join(repo, "engine"), link)
    assert run("guard-dest", link).returncode == 2                              # a link into the install is the install


def test_guard_dest_reads_a_tilde_as_the_home_folder(tmp_path):
    assert run("guard-dest", "~/Finished", env={"HOME": takectl.REPO}).returncode == 2
    assert run("guard-dest", "~/Finished", env={"HOME": str(tmp_path)}).returncode == 0


def test_unknown_command_and_no_arguments_are_usage_stops():
    for r in (run(), run("frobnicate")):
        assert r.returncode == 2 and r.stderr.startswith("STOP: usage: takectl.py status|valid|get|") and r.stdout == "" and "Traceback" not in r.stderr
    for command in ("finished-dir", "stage-fonts", "style-set", "style-record"):
        assert command in run().stderr


# --- finished-dir: where a take delivers when no folder is given --------------------------------------

def test_finished_dir_is_the_workspaces_finished_folder_and_the_takes_title(workspace):
    r = run("finished-dir", "sam-intro")
    assert r.returncode == 0 and r.stdout == os.path.join(workspace, "Finished", "Sam Intro") + "\n"
    assert takectl.finished_dir("sam-intro") == os.path.join(workspace, "Finished", "Sam Intro")
    assert run("finished-dir", "ACME_intro-Q3").stdout.strip() == os.path.join(workspace, "Finished", "ACME Intro Q3")


def test_finished_dir_only_names_the_folder_it_never_makes_it(workspace):
    path = run("finished-dir", "never-made").stdout.strip()
    assert path.endswith(os.path.join("Finished", "Never Made")) and not os.path.exists(path)


def test_finished_dir_follows_the_workspace_in_the_cutroom_home_config(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write(home / "config.json", {"workspace": str(tmp_path / "other-workspace")})
    r = run("finished-dir", "sam-intro", env={"CUTROOM_HOME": str(home)})
    assert r.returncode == 0 and r.stdout == str(tmp_path / "other-workspace" / "Finished" / "Sam Intro") + "\n"


def test_finished_dir_with_no_workspace_set_is_one_plain_stop(tmp_path):
    home = tmp_path / "empty-home"
    home.mkdir()
    r = run("finished-dir", "sam-intro", env={"CUTROOM_HOME": str(home)})
    assert r.returncode == 2 and r.stderr.startswith("STOP: no workspace is set") and r.stdout == "" and r.stderr.count("\n") == 1


# --- stage-fonts: the font files the plan loads, copied to the paths the plan names ----------------------

def planned_take(tmp_path, brand_id, style=None):
    """(take dir, look, plan) for a fixture look: look.json is the profile, plan.json the real plan the planner makes from the frozen golden transcript"""
    look = cap_helpers.profile(brand_id, style)
    take = tmp_path / "take"
    take.mkdir()
    write(take / "look.json", look)
    _, text = outputs(look)
    (take / "plan.json").write_text(text)
    return take, look, json.loads(text)


def staged(stage):
    """every file under the staging folder, as a sorted list of paths relative to it"""
    return sorted(os.path.relpath(os.path.join(d, f), stage) for d, _, files in os.walk(stage) for f in files)


def edit_plan(take, fn):
    plan = json.load(open(take / "plan.json"))
    fn(plan)
    write(take / "plan.json", plan)


VARIABLE, EXTRABOLD = "brand-upstatic/fonts/Inter-Variable.ttf", "brand-upstatic/fonts/Inter-ExtraBold.ttf"


def test_stage_fonts_copies_exactly_the_files_the_plan_names_at_the_paths_it_names(tmp_path):
    take, look, plan = planned_take(tmp_path, "upstatic")
    assert plan["font"]["faces"][0]["src"] == EXTRABOLD and plan["wordFont"]["faces"][0]["src"] == VARIABLE
    stage = tmp_path / "stage"
    r = run("stage-fonts", take, stage)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r.stderr
    assert staged(stage) == [EXTRABOLD, VARIABLE]
    for src in (EXTRABOLD, VARIABLE):
        got = stage / src
        assert not got.is_symlink() and filecmp.cmp(got, look["font_sources"][src], shallow=False), f"{src} is not a real copy of the look's file"


def test_stage_fonts_for_a_look_with_one_face_stages_that_one_file(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    assert "wordFont" not in plan or not plan["wordFont"]
    stage = tmp_path / "stage"
    assert run("stage-fonts", take, stage).returncode == 0
    assert staged(stage) == ["brand-varsans/fonts/Inter-Variable.ttf"]


def test_stage_fonts_stages_the_page_face_too_when_a_style_swaps_the_words_into_the_running_face(tmp_path):
    take, look, plan = planned_take(tmp_path, "upstatic", "rose-running")
    assert plan["font"]["faces"][0]["src"] == VARIABLE and plan["displayFont"]["faces"][0]["src"] == EXTRABOLD
    stage = tmp_path / "stage"
    assert run("stage-fonts", take, stage).returncode == 0
    assert staged(stage) == [EXTRABOLD, VARIABLE]


def test_stage_fonts_leaves_out_a_file_the_look_lists_and_the_plan_does_not_load(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    look["font_sources"]["brand-varsans/fonts/Unused.ttf"] = look["font_sources"]["brand-varsans/fonts/Inter-Variable.ttf"]
    write(take / "look.json", look)
    stage = tmp_path / "stage"
    assert run("stage-fonts", take, stage).returncode == 0
    assert staged(stage) == ["brand-varsans/fonts/Inter-Variable.ttf"]
    edit_plan(take, lambda p: p.pop("wordFont", None))                            # a plan with no running face loads one file fewer
    shutil.rmtree(stage)
    planned = tmp_path / "upstatic"
    planned.mkdir()
    take2, _, _ = planned_take(planned, "upstatic")
    edit_plan(take2, lambda p: p.pop("wordFont"))
    assert run("stage-fonts", take2, stage).returncode == 0
    assert staged(stage) == [EXTRABOLD]


def test_stage_fonts_finds_a_file_the_look_lists_under_another_folder_by_its_name(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    edit_plan(take, lambda p: p["font"]["faces"].__setitem__(0, {"weight": "100 900", "src": "elsewhere/fonts/Inter-Variable.ttf"}))
    stage = tmp_path / "stage"
    assert run("stage-fonts", take, stage).returncode == 0
    assert staged(stage) == ["elsewhere/fonts/Inter-Variable.ttf"]
    assert filecmp.cmp(stage / "elsewhere" / "fonts" / "Inter-Variable.ttf", look["font_sources"]["brand-varsans/fonts/Inter-Variable.ttf"], shallow=False)


def test_stage_fonts_makes_the_staging_folder_and_restaging_replaces_a_stale_copy(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    stage = tmp_path / "does" / "not" / "exist" / "yet"
    assert run("stage-fonts", take, stage).returncode == 0
    staged_file = stage / "brand-varsans" / "fonts" / "Inter-Variable.ttf"
    staged_file.write_bytes(b"a stale copy")
    assert run("stage-fonts", take, stage).returncode == 0
    assert filecmp.cmp(staged_file, look["font_sources"]["brand-varsans/fonts/Inter-Variable.ttf"], shallow=False)


@pytest.mark.parametrize("src", ["../escape/Inter-Variable.ttf", "brand-varsans/fonts/../../../escape.ttf", "/abs/path/Inter-Variable.ttf"])
def test_stage_fonts_stops_on_a_path_that_leaves_the_staging_folder_and_writes_nothing(tmp_path, src):
    take, look, plan = planned_take(tmp_path, "varsans")
    edit_plan(take, lambda p: p["font"]["faces"].__setitem__(0, {"weight": "100 900", "src": src}))
    stage = tmp_path / "work" / "stage"
    r = run("stage-fonts", take, stage)
    assert r.returncode == 2 and r.stderr.startswith(f"STOP: the plan names the font {src}, which is not a path inside the render's folder") and r.stderr.count("\n") == 1
    assert r.stdout == "" and "Traceback" not in r.stderr
    assert not stage.exists() and not (tmp_path / "escape").exists() and not (tmp_path / "escape.ttf").exists() and not os.path.exists("/abs")


def test_stage_fonts_stops_on_a_face_the_look_has_no_file_for(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    edit_plan(take, lambda p: p["font"]["faces"].__setitem__(0, {"weight": "100 900", "src": "brand-varsans/fonts/Missing.ttf"}))
    r = run("stage-fonts", take, tmp_path / "stage")
    assert r.returncode == 2 and r.stdout == "" and r.stderr.count("\n") == 1
    assert r.stderr.startswith("STOP: the plan loads the font brand-varsans/fonts/Missing.ttf, but the look has no file on disk for it; run lookprofile.py again")


def test_stage_fonts_stops_on_a_face_whose_file_is_gone_from_the_disk(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    look["font_sources"]["brand-varsans/fonts/Inter-Variable.ttf"] = str(tmp_path / "moved.ttf")
    write(take / "look.json", look)
    r = run("stage-fonts", take, tmp_path / "stage")
    assert r.returncode == 2 and r.stderr.startswith("STOP: the plan loads the font brand-varsans/fonts/Inter-Variable.ttf, but the look has no file on disk for it")
    assert not (tmp_path / "stage" / "brand-varsans" / "fonts" / "Inter-Variable.ttf").exists()


def test_stage_fonts_of_a_plan_that_loads_no_font_stages_nothing(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    write(take / "plan.json", {"events": []})
    stage = tmp_path / "stage"
    assert run("stage-fonts", take, stage).returncode == 0 and not stage.exists()


def test_stage_fonts_with_no_plan_json_is_one_plain_stop(tmp_path):
    take, look, plan = planned_take(tmp_path, "varsans")
    os.remove(take / "plan.json")
    r = run("stage-fonts", take, tmp_path / "stage")
    assert r.returncode == 2 and r.stderr.startswith("STOP: bad input") and "plan.json" in r.stderr and "Traceback" not in r.stderr


def test_stage_fonts_for_works_from_a_look_and_a_plan_already_in_hand(tmp_path):
    """preview_style.py has no take folder: it hands the look and the plan over as dicts"""
    look = cap_helpers.profile("upstatic")
    plan = {"font": {"family": "InterStaticDyn", "faces": [{"weight": "100 900", "src": EXTRABOLD}]},
            "wordFont": {"family": "InterRunDyn", "faces": [{"weight": "100 900", "src": VARIABLE}]}}
    stage = tmp_path / "stage"
    takectl.stage_fonts_for(look, plan, str(stage))
    assert staged(stage) == [EXTRABOLD, VARIABLE]
    assert filecmp.cmp(stage / EXTRABOLD, look["font_sources"][EXTRABOLD], shallow=False)
    plan["displayFont"] = {"faces": [{"src": "../up/Inter-Variable.ttf"}]}
    with pytest.raises(EngineStop, match="not a path inside the render's folder"):
        takectl.stage_fonts_for(look, plan, str(tmp_path / "stage2"))
    with pytest.raises(EngineStop, match="no file on disk for it"):
        takectl.stage_fonts_for({"font_sources": {}}, {"font": {"faces": [{"src": "x/y.ttf"}]}}, str(tmp_path / "stage3"))
    takectl.stage_fonts_for({}, {}, str(tmp_path / "stage4"))
    assert not (tmp_path / "stage4").exists()


# --- style-set: the take's own pick, checked against the library before it is written ------------------------

def test_style_set_writes_the_takes_pick_for_a_style_the_library_has(tmp_path, put_brand):
    put_brand("varsans")
    take = tmp_path / "take"
    take.mkdir()
    r = run("style-set", take, "keyword-slam")
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r.stderr
    assert (take / "caption-style.txt").read_text() == "keyword-slam\n"
    assert takectl.style_in_file(str(take)) == "keyword-slam"
    assert run("style", take).stdout == "keyword-slam\n", "from then on the take asks for it"


@pytest.mark.parametrize("brand_id,sid", [("varsans", "soft-build"), ("varsans", "clean-clusters"), ("upstatic", "rose-running")])
def test_style_set_takes_a_style_of_the_brand_a_starter_and_a_style_with_its_own_look(tmp_path, put_brand, brand_id, sid):
    put_brand(brand_id)
    take = tmp_path / "take"
    take.mkdir()
    assert run("style-set", take, sid).returncode == 0
    assert (take / "caption-style.txt").read_text() == f"{sid}\n"


def test_style_set_replaces_an_earlier_pick(tmp_path, put_brand):
    put_brand("varsans")
    take = tmp_path / "take"
    take.mkdir()
    for sid in ("keyword-slam", "quiet-cluster"):
        assert run("style-set", take, sid).returncode == 0
        assert (take / "caption-style.txt").read_text() == f"{sid}\n"


def test_style_set_with_an_unknown_id_is_one_plain_stop_that_names_the_known_styles_and_writes_nothing(tmp_path, put_brand):
    put_brand("varsans")
    take = tmp_path / "take"
    take.mkdir()
    r = run("style-set", take, "no-such-style")
    assert r.returncode == 2 and r.stdout == "" and r.stderr.count("\n") == 1 and "Traceback" not in r.stderr
    assert r.stderr.startswith("STOP: style: unknown style 'no-such-style' (known: ") and "keyword-slam" in r.stderr and "soft-build" in r.stderr
    assert os.listdir(take) == []


@pytest.mark.parametrize("sid", ["Keyword-Slam", "../keyword-slam", "keyword slam"])
def test_style_set_refuses_an_id_that_is_not_written_like_a_style_id_and_keeps_the_earlier_pick(tmp_path, put_brand, sid):
    put_brand("varsans")
    take = tmp_path / "take"
    take.mkdir()
    (take / "caption-style.txt").write_text("quiet-cluster\n")
    r = run("style-set", take, sid)
    assert r.returncode == 2 and r.stderr.startswith("STOP: style:") and r.stdout == "" and "Traceback" not in r.stderr
    assert (take / "caption-style.txt").read_text() == "quiet-cluster\n"
    assert os.listdir(take) == ["caption-style.txt"]


def test_style_set_refuses_a_style_that_asks_for_a_running_face_the_look_does_not_have(tmp_path, put_brand):
    bd = put_brand("varsans")
    shutil.copyfile(os.path.join(cap_helpers.FIXTURES, "styles", "rose-running.json"), os.path.join(bd, "styles", "rose-running.json"))
    take = tmp_path / "take"
    take.mkdir()
    r = run("style-set", take, "rose-running")
    assert r.returncode == 2 and r.stdout == "" and r.stderr.count("\n") == 1 and "Traceback" not in r.stderr
    assert r.stderr.startswith("dna: cannot be used:") and "rose-running.json" in r.stderr and "running" in r.stderr   # the DNA check names the style file
    assert os.listdir(take) == []


def test_style_set_with_no_dna_yet_is_one_plain_line_and_writes_nothing(tmp_path, no_brand):
    take = tmp_path / "take"
    take.mkdir()
    r = run("style-set", take, "keyword-slam")
    assert r.returncode == 2 and r.stdout == "" and r.stderr.count("\n") == 1 and "dna.json" in r.stderr and "Traceback" not in r.stderr
    assert os.listdir(take) == []


def test_style_set_in_a_take_folder_that_is_not_there_is_one_plain_stop(tmp_path, put_brand):
    put_brand("varsans")
    r = run("style-set", tmp_path / "no-take", "keyword-slam")
    assert r.returncode == 2 and r.stderr.startswith("STOP: bad input") and "Traceback" not in r.stderr
    assert not (tmp_path / "no-take").exists()


def test_a_gate_md_style_line_beats_the_pick_style_set_wrote(tmp_path, put_brand):
    put_brand("varsans")
    take = tmp_path / "take"
    take.mkdir()
    assert run("style-set", take, "keyword-slam").returncode == 0
    (take / "GATE.md").write_text("approved: no\n\n## Overrides\n\nstyle: quiet-cluster\n")
    assert run("style", take).stdout == "quiet-cluster\n"
    assert (take / "caption-style.txt").read_text() == "keyword-slam\n"


# --- style-record: what a delivery records as its caption style ------------------------------------------------

def test_style_record_is_the_style_look_json_was_made_with_and_otherwise_nothing(tmp_path):
    assert takectl.style_record(str(tmp_path)) == "" and run("style-record", tmp_path).stdout == "\n"       # no look.json: nothing to record
    write(tmp_path / "look.json", cap_helpers.profile("varsans"))
    assert takectl.style_record(str(tmp_path)) == "" and run("style-record", tmp_path).stdout == "\n"       # a look made with no style
    write(tmp_path / "look.json", cap_helpers.profile("varsans", "keyword-slam"))
    assert takectl.style_record(str(tmp_path)) == "keyword-slam" and run("style-record", tmp_path).stdout == "keyword-slam\n"
    write(tmp_path / "look.json", {"caption_style": None})
    assert takectl.style_record(str(tmp_path)) == ""
    (tmp_path / "look.json").write_text("{not json")
    assert takectl.style_record(str(tmp_path)) == "" and run("style-record", tmp_path).returncode == 0


def test_style_record_ignores_the_default_style_and_the_picks_files(tmp_path):
    """only the look the take was actually made with is recorded: a pick that look.json does not carry (yet) is not a record"""
    write(tmp_path / "look.json", cap_helpers.profile("varsans"))
    assert cap_helpers.profile("varsans")["default_style"] == "soft-build"
    (tmp_path / "caption-style.txt").write_text("keyword-slam\n")
    (tmp_path / "GATE.md").write_text("approved: yes\n\n## Overrides\n\nstyle: quiet-cluster\n")
    assert run("style-record", tmp_path).stdout == "\n"
