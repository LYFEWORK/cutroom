"""The classify contract: the record every route writes, the words the prompts use, and classify.js run against stubs of the Workflow tool's functions."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import job as jobmod
import merge
from conftest import FOOTAGE

JS = (FOOTAGE / "classify.js").read_text(encoding="utf-8")
BATCH = (FOOTAGE / "prompts" / "classify-batch.md").read_text(encoding="utf-8")
SEGMENT = (FOOTAGE / "prompts" / "segment.md").read_text(encoding="utf-8")

FIELDS = ["clip", "category", "reel", "reel_title", "question_asked", "answer_summary", "key_quote", "on_screen", "shot", "camera", "subject_folder", "subject_other", "people",
          "name", "flags", "usable", "confidence", "notes"]
FLAGS = ["profanity", "confidential", "false_start", "cut_off_early", "shaky", "out_of_focus", "interviewer_audible", "scripted_take", "freestyle_take", "multiple_topics",
         "staff_in_frame", "phone_in_frame", "off_topic_content"]


def js_enum(name):
    return re.search(name + r": \{ type: 'string', enum: \[([^\]]*)\]", JS).group(1).replace("'", "").replace(" ", "").split(",")


def flag_words(text, marker):
    line = next(l for l in text.splitlines() if marker in l)
    body = re.sub(r"\([^)]*\)", "", line.split(":", 1)[1])
    return [w.strip(" `.") for w in body.split(",") if w.strip(" `.")]


def test_the_categories_shots_and_cameras_are_the_same_in_the_script_the_merge_and_the_job():
    assert js_enum("category") == list(jobmod.CATEGORIES)
    assert set(js_enum("shot")) == merge.SHOTS and set(js_enum("camera")) == merge.CAMS
    assert "off_topic" in jobmod.CATEGORIES and "stray_other_client" not in JS + BATCH + SEGMENT


def test_the_script_requires_every_field_of_the_record_and_the_batch_prompt_shows_them_all():
    required = re.search(r"required: \['clip', 'category'[^\]]*\]", JS).group(0)
    assert re.findall(r"'([a-z_]+)'", required) == FIELDS
    example = json.loads(re.search(r"    \{\n(?:      .*\n)+    \}", BATCH).group(0))
    assert list(example) == FIELDS and example["category"] in jobmod.CATEGORIES and isinstance(example["flags"], list) and isinstance(example["usable"], bool)


def test_the_flags_are_the_same_in_the_script_and_the_prompt():
    assert flag_words(JS, "- flags (use [] if none)") == FLAGS
    assert flag_words(BATCH, "- `flags` (use `[]` for none)") == FLAGS


def test_the_prompts_name_every_category_and_where_every_file_goes():
    for c in jobmod.CATEGORIES:
        assert f"`{c}`" in BATCH
    assert "<scratch>/results/<clip>.final.json" in BATCH and "<scratch>/results/<clip>.segments.json" in SEGMENT
    assert "<scratch>/words/<clip>.txt" in SEGMENT and "`clean`, `usable` or `flub`" in SEGMENT and '"kind": "take"' in SEGMENT and '"kind": "blooper"' in SEGMENT


def test_the_segment_prompt_example_is_the_shape_takes_py_reads(tmp_path):
    import takes
    example = re.search(r'\{"clip": "C0001", "segments": \[.*?\]\}', SEGMENT, re.S).group(0)
    p = tmp_path / "C0001.segments.json"
    p.write_text(example)
    got = takes.load_segments(str(p), 60.0)
    assert [s["kind"] for s in got] == ["take", "take", "blooper"] and got[0]["quality"] == "clean" and got[1]["quality"] == "flub" and got[2]["quality"] == ""


def test_no_prompt_or_script_has_an_em_or_en_dash_or_a_hook_openers_block():
    for text in (JS, BATCH, SEGMENT):
        assert "\u2014" not in text and "\u2013" not in text
        assert "OPENERS" not in text and "opener" not in text.lower()


# ---------------------------------------------------------------- classify.js, run with node against stubs
STUB = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8').replace('export const meta =', 'const meta =');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const fn = new AsyncFunction('args', 'phase', 'pipeline', 'agent', 'parallel', 'log', src);
const calls = [], logs = [];
const verdicts = JSON.parse(process.argv[2]);
const args = JSON.parse(process.argv[3]);
const rec = (clip) => ({ clip, category: 'interview', reel: 'Q01', reel_title: 't', question_asked: 'q', answer_summary: 's', key_quote: '', on_screen: 'o', shot: 'wide', camera: 'static',
  subject_folder: 'Other', subject_other: '', people: 'p', name: 'n', flags: [], usable: true, confidence: 0.9, notes: '' });
async function agent(prompt, opts) {
  calls.push({ label: opts.label, prompt, schema: opts.schema });
  const i = opts.label.indexOf(':'), kind = opts.label.slice(0, i), clip = opts.label.slice(i + 1);
  if (kind === 'classify') return rec(clip);
  if (kind === 'verify-visual') return { clip, agree: verdicts[clip].visual, issues: [], corrections: {}, confidence: 0.9 };
  if (kind === 'verify-content') return { clip, agree: verdicts[clip].content, issues: [], corrections: {}, confidence: 0.9 };
  if (kind === 'resolve') return { ...rec(clip), notes: 'judged' };
}
const parallel = (fns) => Promise.all(fns.map(f => f()));
const pipeline = (items, ...stages) => Promise.all(items.map(async (item) => { let v = item; for (const st of stages) v = await st(v, item); return v; }));
fn(args, () => {}, pipeline, agent, parallel, (m) => logs.push(m)).then(res => console.log(JSON.stringify({ res, calls, logs })));
"""


def run_js(tmp_path, verdicts, **over):
    if not shutil.which("node"):
        pytest.skip("needs node")
    args = {"scratch": "/s", "clips": list(verdicts), "context": "ctx", "subjects": ["Desk Work"], "has_questions": True, **over}
    r = subprocess.run(["node", "-e", STUB, str(FOOTAGE / "classify.js"), json.dumps(verdicts), json.dumps(args)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout)


def test_the_script_parses_and_every_clip_gets_a_classifier_and_two_verifiers(tmp_path):
    out = run_js(tmp_path, {"A": {"visual": True, "content": True}, "B": {"visual": True, "content": True}})
    labels = sorted(c["label"] for c in out["calls"])
    assert labels == ["classify:A", "classify:B", "verify-content:A", "verify-content:B", "verify-visual:A", "verify-visual:B"]     # nobody disputed: no judge
    assert out["res"] == {"count": 2, "missing": [], "categories": {"interview": 2}}


def test_a_dispute_in_either_lens_sends_only_that_clip_to_the_judge(tmp_path):
    out = run_js(tmp_path, {"A": {"visual": True, "content": True}, "B": {"visual": False, "content": True}, "C": {"visual": True, "content": False}})
    judged = sorted(c["label"] for c in out["calls"] if c["label"].startswith("resolve"))
    assert judged == ["resolve:B", "resolve:C"] and any("2 went to the judge" in l for l in out["logs"]) and out["res"]["count"] == 3


def test_the_prompts_carry_the_paths_the_rules_and_the_neutral_examples(tmp_path):
    out = run_js(tmp_path, {"A": {"visual": False, "content": True}})
    by = {c["label"]: c for c in out["calls"]}
    first = by["classify:A"]["prompt"]
    assert first.startswith("CONTEXT: ctx") and "/s/ev/A.json" in first and "/s/cheat-sheet.txt" in first and "/s/results/A.classify.json" in first
    assert "off_topic = clearly a different subject or place" in first and "Barista Steaming Milk Close Up" in first and "Why Our Process Takes Three Days" in first
    assert "map interview answers to the question IDs in the cheat sheet by TOPIC" in first
    judge = by["resolve:A"]["prompt"]
    assert "ORIGINAL RECORD:" in judge and "VISUAL VERDICT:" in judge and "CONTENT VERDICT:" in judge and "/s/results/A.final.json" in judge


def test_with_no_question_list_the_rule_changes(tmp_path):
    out = run_js(tmp_path, {"A": {"visual": True, "content": True}}, has_questions=False)
    assert "no question list exists: every interview answer gets reel \"BONUS\"" in out["calls"][0]["prompt"]


def test_the_subject_enum_always_holds_other(tmp_path):
    out = run_js(tmp_path, {"A": {"visual": True, "content": True}}, subjects=["Desk Work", "Other"])
    sf = next(c for c in out["calls"] if c["label"] == "classify:A")["schema"]["properties"]["subject_folder"]["enum"]
    assert sf == ["Desk Work", "Other"]
    out = run_js(tmp_path, {"A": {"visual": True, "content": True}}, subjects=["Desk Work"])
    assert next(c for c in out["calls"] if c["label"] == "classify:A")["schema"]["properties"]["subject_folder"]["enum"] == ["Desk Work", "Other"]
