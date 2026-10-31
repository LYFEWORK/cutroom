import json
import pytest
import cut
SRC = [{"text": "the", "start": 10.0, "end": 10.2}, {"text": "welcome", "start": 10.3, "end": 10.8}, {"text": "video", "start": 10.9, "end": 11.3}]
def test_first_word_after_joint():
    assert cut.first_word_after(SRC, 10.25)["text"] == "welcome"
    assert cut.first_word_after(SRC, 11.5) is None
def test_ratio_finds_output_word_near_joint():
    out = [{"text": "welcome", "start": 4.02, "end": 4.40}]           # 0.38 vs 0.50 source
    assert abs(cut.ratio(out, 4.0, SRC[1]) - 0.76) < 0.01
    assert cut.ratio([], 4.0, SRC[1]) == 0.0

# Geometry decides. The speech-engine ratio alone was proven wrong on real footage: it passed genuinely clipped joints and failed clean ones on
# short-word variance. The clip below is 0.12 s, past the 0.08 s stamp tolerance, so geometry fails it whatever the output ratio says, and a clean
# output ratio cannot rescue it. The reported word is "video" because first_word_after only looks 0.02 s back from c0: any clip deeper than that
# reports on the NEXT word (the same effect test_geometry_head_clip_fails_even_with_perfect_asr_ratio shows below).
def test_check_fails_a_clipped_first_word():
    edl = {"pieces": [{"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0}, {"seg": 1, "c0": 10.42, "c1": 11.3, "out_in": 3.0, "out_out": 4.05}]}
    out_words = [{"text": "welcome", "start": 3.02, "end": 3.30}]      # 0.28 / 0.50 = 0.56 -> ASR alone would call this clipped too
    r = cut.check_joints(edl, SRC, out_words)
    assert r["verdict"] == "FAIL" and r["failed"] == 1 and r["joints"][0]["word"] == "video" and r["joints"][0]["ok"] is False
    assert r["joints"][0]["reason"] == "clipped_head" and r["joints"][0]["head"] == -0.12
    assert r["joints"][0]["fixable_by"] == "head_pad"
    # a clean output ratio does NOT rescue a geometrically clipped input -- geometry is primary now.
    still_clipped = cut.check_joints(edl, SRC, [{"text": "welcome", "start": 3.02, "end": 3.50}])
    assert still_clipped["verdict"] == "FAIL" and still_clipped["joints"][0]["reason"] == "clipped_head"

# --- geometry() and check_joints(): the geometry-first verdict rule ---

def test_geometry_head_clip_fails_even_with_perfect_asr_ratio():
    """Piece c0 14.567 against "because" starting at 14.480 gives head -0.087. The clip exceeds first_word_after's 0.02 s tolerance, so the ASR
    machinery reports on the NEXT word ("your") entirely, and even there we hand it a perfect 1.0 ratio. Geometry must fail the joint anyway; it
    never looks at the output."""
    src = [{"text": "because", "start": 14.480, "end": 15.065}, {"text": "your", "start": 15.1, "end": 15.4}]
    edl = {"pieces": [
        {"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0},
        {"seg": 1, "c0": 14.567, "c1": 15.4, "out_in": 3.0, "out_out": 3.833},
    ]}
    geo = cut.geometry(edl["pieces"], src)
    assert geo[1]["first"] == "because" and geo[1]["head"] == -0.087 and geo[1]["clipped"] is True
    out_words = [{"text": "your", "start": 3.0, "end": 3.3}]           # your: 0.3s duration -> ratio 1.0, perfect
    r = cut.check_joints(edl, src, out_words)
    assert r["joints"][0]["ratio"] == 1.0
    assert r["verdict"] == "FAIL"
    assert r["joints"][0]["reason"] == "clipped_head" and r["joints"][0]["head"] == -0.087

def test_clean_geometry_with_bad_asr_ratio_is_a_warning_not_a_failure():
    """A word with a clean 0.06 s head whose output word is short (ratio 0.62, under the 0.8 floor) is a warning, not a failure: the geometry is clean."""
    src = [{"text": "show", "start": 10.06, "end": 10.576}]            # 0.516s duration
    edl = {"pieces": [
        {"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0},
        {"seg": 1, "c0": 10.0, "c1": 10.9, "out_in": 3.0, "out_out": 3.8},   # c0 is 0.06s BEFORE "show" starts
    ]}
    out_words = [{"text": "show", "start": 3.0, "end": 3.32}]          # 0.32 / 0.516 = 0.620 -> below the 0.8 floor
    r = cut.check_joints(edl, src, out_words)
    assert r["joints"][0]["head"] == 0.06 and r["joints"][0]["clipped"] is False
    assert abs(r["joints"][0]["ratio"] - 0.62) < 0.01
    assert r["verdict"] == "PASS"
    assert r["joints"][0]["reason"] == "asr_variance" and r["joints"][0]["ok"] is True
    assert len(r["warnings"]) == 1 and r["warnings"][0]["word"] == "show"

def test_word_missing_from_output_transcript_is_a_warning_not_a_failure():
    """A word the cut's own transcript merged away (ratio 0.0) with a clean 0.1 s head is a warning, not a failure."""
    src = [{"text": "the", "start": 20.1, "end": 20.583}]              # 0.483s duration
    edl = {"pieces": [
        {"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0},
        {"seg": 1, "c0": 20.0, "c1": 20.9, "out_in": 3.0, "out_out": 3.9},   # 0.1s clean pad before "the"
    ]}
    out_words = []                                                     # "the" is simply absent from the output transcript
    r = cut.check_joints(edl, src, out_words)
    assert r["joints"][0]["head"] == 0.1 and r["joints"][0]["clipped"] is False
    assert r["joints"][0]["ratio"] == 0.0
    assert r["verdict"] == "PASS"
    assert r["joints"][0]["reason"] == "asr_variance" and r["joints"][0]["ok"] is True
    assert len(r["warnings"]) == 1

def test_last_piece_tail_clip_fails_even_though_no_later_joint_would_catch_it():
    """A piece's own tail (does IT end inside ITS last word) is only ever visible to the NEXT piece's joint entry; there is no next piece for the
    LAST one, so it needs its own check, and it has to surface as its own reason (clipped_tail), not get silently swallowed."""
    src = [{"text": "wrap", "start": 5.0, "end": 5.4}, {"text": "it", "start": 5.42, "end": 5.6}, {"text": "up", "start": 5.62, "end": 6.0}]
    edl = {"pieces": [
        {"seg": 0, "c0": 0.0, "c1": 1.0, "out_in": 0.0, "out_out": 1.0},              # no words, clean
        {"seg": 1, "c0": 4.9, "c1": 5.85, "out_in": 1.0, "out_out": 1.95},            # ends 0.15s inside "up" (up ends 6.0)
    ]}
    out_words = [{"text": "wrap", "start": 1.0, "end": 1.4}, {"text": "it", "start": 1.42, "end": 1.6}, {"text": "up", "start": 1.62, "end": 1.85}]
    r = cut.check_joints(edl, src, out_words)
    assert r["verdict"] == "FAIL"
    tail_fail = [j for j in r["joints"] if j["reason"] == "clipped_tail"]
    assert len(tail_fail) == 1 and tail_fail[0]["tail"] == -0.15 and tail_fail[0]["ok"] is False

def test_geometry_piece_with_no_words_is_not_clipped():
    src = [{"text": "hello", "start": 50.0, "end": 50.3}]
    pieces = [{"seg": 0, "c0": 0.0, "c1": 5.0, "out_in": 0.0, "out_out": 5.0}]        # pure b-roll, no words in range
    geo = cut.geometry(pieces, src)
    assert geo[0]["clipped"] is False
    assert geo[0]["first"] is None and geo[0]["last"] is None
    assert geo[0]["head"] is None and geo[0]["tail"] is None

# --- stamp tolerance, the aligned sidecar's preference, and an actionable pad ---

def test_head_clip_inside_the_stamp_tolerance_passes():
    """Heads of -0.027 and -0.033 s read as clips by the stamps, while the waveform (room floor -51 dBFS, threshold -41 dBFS, 5 ms windows) shows
    lead-ins of +0.025 and +0.055 s: the cut was clean and the aligner had simply stamped the sentence-initial word early. Anything inside
    STAMP_TOL is stamp jitter, not a clipped word."""
    src = [{"text": "are", "start": 9.973, "end": 10.055}, {"text": "you", "start": 10.1, "end": 10.4},
           {"text": "check", "start": 19.967, "end": 20.129}, {"text": "it", "start": 20.2, "end": 20.5}]
    edl = {"pieces": [
        {"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0},
        {"seg": 1, "c0": 10.0, "c1": 10.6, "out_in": 3.0, "out_out": 3.6},      # head -0.027
        {"seg": 2, "c0": 20.0, "c1": 20.7, "out_in": 3.6, "out_out": 4.3},      # head -0.033
    ]}
    out_words = [{"text": "are", "start": 3.0, "end": 3.24}, {"text": "check", "start": 3.6, "end": 3.92}]
    r = cut.check_joints(edl, src, out_words)
    assert [j["head"] for j in r["joints"]] == [-0.027, -0.033]      # raw stamps recorded verbatim
    assert [j["head_adj"] for j in r["joints"]] == [0.053, 0.047]    # ... and what the verdict saw
    assert r["verdict"] == "PASS" and r["failed"] == 0
    assert all(j["clipped"] is False and j["fixable_by"] is None for j in r["joints"])
    assert r["stamp_tol"] == cut.STAMP_TOL == 0.08

def test_head_clip_beyond_the_stamp_tolerance_still_fails_on_the_stamps():
    """A head of -0.160 s where the waveform showed a real +0.075 s lead-in is a false positive, and it STILL fails here. That is the honest call,
    not an oversight: a tolerance wide enough to swallow 0.160 s would be wider than whole words ("are" 0.082 s, "see" 0.121 s), and the guard
    would stop catching genuine clips. 0.08 s is what the jitter measures; the fix for a 0.160 s disagreement is an aligned sidecar or a waveform
    onset, not a tolerance that hides a whole word. Failing it costs one re-cut at a wider head pad; hiding it would cost a chopped word."""
    src = [{"text": "see", "start": 29.84, "end": 30.2}, {"text": "how", "start": 30.25, "end": 30.5}]
    edl = {"pieces": [
        {"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0},
        {"seg": 1, "c0": 30.0, "c1": 30.6, "out_in": 3.0, "out_out": 3.6},
    ]}
    r = cut.check_joints(edl, src, [{"text": "how", "start": 3.0, "end": 3.25}])
    assert r["verdict"] == "FAIL" and r["failed"] == 1
    j = r["joints"][0]
    assert j["head"] == -0.16 and j["head_adj"] == -0.08
    assert j["reason"] == "clipped_head" and j["fixable_by"] == "head_pad"
    assert cut.needed_pads(r) == "head"

def test_tail_clip_fails_with_fixable_by_tail_pad():
    """A set of failures that were all tails, each reported as "ends inside its last word", must be fixed by the pad that CAN move them: a tail
    failure has to name the tail pad, not the head pad."""
    src = [{"text": "so", "start": 5.0, "end": 5.3}, {"text": "right", "start": 5.35, "end": 5.9},
           {"text": "next", "start": 8.0, "end": 8.4}]
    edl = {"pieces": [
        {"seg": 0, "c0": 4.9, "c1": 5.5, "out_in": 0.0, "out_out": 0.6},      # ends 0.4s inside "right"
        {"seg": 1, "c0": 7.94, "c1": 8.5, "out_in": 0.6, "out_out": 1.16},    # head +0.06, clean
    ]}
    out_words = [{"text": "next", "start": 0.6, "end": 1.0}]
    r = cut.check_joints(edl, src, out_words)
    assert r["verdict"] == "FAIL" and r["failed"] == 1
    j = r["joints"][0]
    assert j["head"] == 0.06 and j["tail"] == -0.4 and j["tail_adj"] == -0.32
    assert j["reason"] == "clipped_tail" and j["fixable_by"] == "tail_pad"
    assert cut.needed_pads(r) == "tail"

def test_needed_pads_reports_both_when_head_and_tail_both_fail():
    """A mixed set (head clips AND a tail clip): widening only one pad leaves the other failing forever, so the ladder has to widen both."""
    src = [{"text": "taking", "start": 5.0, "end": 5.9}, {"text": "so", "start": 10.0, "end": 10.3},
           {"text": "we", "start": 10.35, "end": 10.6}, {"text": "shape", "start": 20.6, "end": 21.0},
           {"text": "up", "start": 21.05, "end": 21.4}]
    edl = {"pieces": [
        {"seg": 0, "c0": 4.9, "c1": 5.5, "out_in": 0.0, "out_out": 0.6},      # tail -0.4 inside "taking"
        {"seg": 1, "c0": 9.94, "c1": 10.7, "out_in": 0.6, "out_out": 1.36},   # clean both edges
        {"seg": 2, "c0": 20.75, "c1": 21.5, "out_in": 1.36, "out_out": 2.11}, # head -0.15 inside "shape"
    ]}
    r = cut.check_joints(edl, src, [])
    assert r["verdict"] == "FAIL" and r["failed"] == 2
    assert sorted(j["fixable_by"] for j in r["joints"] if not j["ok"]) == ["head_pad", "tail_pad"]
    assert cut.needed_pads(r) == "both"

def test_needed_pads_reads_both_edges_of_a_joint_clipped_at_both_ends():
    """A joint reports one reason, and head wins -- but widening only pad_pre would leave the tail
    clipped and burn a second try to discover it. needed_pads reads the adjusted numbers, so one
    joint clipped at both ends asks for both pads at once."""
    r = {"joints": [{"ok": False, "reason": "clipped_head", "fixable_by": "head_pad",
                     "head_adj": -0.07, "tail_adj": -0.32}]}
    assert cut.needed_pads(r) == "both"

def test_needed_pads_is_none_on_a_pass():
    r = {"joints": [{"ok": True, "fixable_by": None, "head_adj": -0.5, "tail_adj": -0.5}], "verdict": "PASS"}
    assert cut.needed_pads(r) == "none"

def test_load_src_words_prefers_the_wx_sidecar_when_the_two_disagree(tmp_path):
    """The case the tolerance alone cannot cover: words.json stamps the word 0.2 s early (head -0.2, past STAMP_TOL, a FAIL) while the aligned
    sidecar puts the onset where the waveform does (head +0.1, a PASS). The sidecar is `words.wx.json` beside `words.json`, so the joint check reads
    it from the same directory."""
    words_p = tmp_path / "words.json"
    words_p.write_text(json.dumps({"words": [{"text": "Because", "start": 14.3, "end": 15.065},
                                             {"text": "your", "start": 15.1, "end": 15.4}]}))
    src_dg, stamps_dg = cut.load_src_words(str(words_p))
    assert stamps_dg.startswith("words.json") and "words.wx.json" in stamps_dg
    edl = {"pieces": [{"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0},
                      {"seg": 1, "c0": 14.5, "c1": 15.4, "out_in": 3.0, "out_out": 3.9}]}
    assert cut.check_joints(edl, src_dg, [])["verdict"] == "FAIL"          # on words.json's own stamps

    (tmp_path / "words.wx.json").write_text(json.dumps({"segments": [{"words": [
        {"word": "Because", "start": 14.6, "end": 15.065}, {"word": "your", "start": 15.1, "end": 15.4}]}]}))
    src_wx, stamps_wx = cut.load_src_words(str(words_p))
    assert stamps_wx.startswith("whisperx")
    assert src_wx[0] == {"text": "Because", "start": 14.6, "end": 15.065}   # normalized to the words.json shape
    r = cut.check_joints(edl, src_wx, [], stamps=stamps_wx)
    assert r["verdict"] == "PASS" and r["joints"][0]["head"] == 0.1
    assert r["stamps"].startswith("whisperx")

def test_an_empty_wx_sidecar_falls_back_to_words_json_and_says_so(tmp_path):
    """An empty sidecar must not silently erase every source word (that would make geometry find
    nothing inside any piece and PASS a genuinely clipped cut)."""
    words_p = tmp_path / "words.json"
    words_p.write_text(json.dumps({"words": [{"text": "hi", "start": 1.0, "end": 1.2}]}))
    (tmp_path / "words.wx.json").write_text(json.dumps({"segments": [{"words": []}]}))
    src, stamps = cut.load_src_words(str(words_p))
    assert src == [{"text": "hi", "start": 1.0, "end": 1.2}]
    assert stamps.startswith("words.json") and "no words" in stamps

def test_a_malformed_wx_sidecar_stops_instead_of_reverting_silently(tmp_path):
    words_p = tmp_path / "words.json"
    words_p.write_text(json.dumps({"words": [{"text": "hi", "start": 1.0, "end": 1.2}]}))
    (tmp_path / "words.wx.json").write_text("not json")
    with pytest.raises(SystemExit):
        cut.load_src_words(str(words_p))

# A missing or corrupt cut, a missing ffmpeg binary or a malformed or missing edl.json / words.json is one plain "STOP: ..." line and exit 2, never
# a traceback. These tests never reach the speech engine (each fails before it would be called).
def test_run_stops_on_a_missing_edl_json(tmp_path):
    with pytest.raises(SystemExit):
        cut.joints_run("ignored.mp4", str(tmp_path / "no-edl.json"), str(tmp_path / "no-words.json"), str(tmp_path / "out.json"))
def test_run_stops_on_a_malformed_words_json(tmp_path):
    edl_p = tmp_path / "edl.json"; edl_p.write_text(json.dumps({"pieces": []}))
    words_p = tmp_path / "words.json"; words_p.write_text("not json")
    with pytest.raises(SystemExit):
        cut.joints_run("ignored.mp4", str(edl_p), str(words_p), str(tmp_path / "out.json"))
def test_run_stops_on_a_words_json_missing_its_words_key(tmp_path):
    edl_p = tmp_path / "edl.json"; edl_p.write_text(json.dumps({"pieces": []}))
    words_p = tmp_path / "words.json"; words_p.write_text(json.dumps({"not_words": []}))
    with pytest.raises(SystemExit):
        cut.joints_run("ignored.mp4", str(edl_p), str(words_p), str(tmp_path / "out.json"))
def test_run_stops_when_ffmpeg_cannot_read_the_cut(tmp_path):
    edl_p = tmp_path / "edl.json"; edl_p.write_text(json.dumps({"pieces": []}))
    words_p = tmp_path / "words.json"; words_p.write_text(json.dumps({"words": []}))
    with pytest.raises(SystemExit):
        cut.joints_run(str(tmp_path / "no-such-cut.mp4"), str(edl_p), str(words_p), str(tmp_path / "out.json"))

# --- JOINTS_ASR: the speech-engine corroboration pass is not the verdict ---------------------------------------------------------
# Geometry decides. The ratio pass costs a whole extra transcription per attempt, up to three attempts per take, to produce a WARNING. With an
# aligned sidecar on disk the stamps it corroborates are no better than the ones the verdict uses, so `off` is the default there.

def test_asr_is_off_by_default_when_a_wx_sidecar_exists(tmp_path):
    words_p = tmp_path / "words.json"; words_p.write_text(json.dumps({"words": []}))
    assert cut.asr_enabled(str(words_p), env={}) is True
    (tmp_path / "words.wx.json").write_text(json.dumps({"segments": []}))
    assert cut.asr_enabled(str(words_p), env={}) is False

def test_joints_asr_env_overrides_the_default_both_ways(tmp_path):
    words_p = tmp_path / "words.json"; words_p.write_text(json.dumps({"words": []}))
    (tmp_path / "words.wx.json").write_text(json.dumps({"segments": []}))
    assert cut.asr_enabled(str(words_p), env={"JOINTS_ASR": "on"}) is True
    assert cut.asr_enabled(str(words_p), env={"JOINTS_ASR": "off"}) is False
    (tmp_path / "words.wx.json").unlink()
    assert cut.asr_enabled(str(words_p), env={"JOINTS_ASR": "off"}) is False

def test_check_with_asr_off_never_invents_a_ratio_verdict():
    """With no output transcript, ratio() returns 0.0 for every joint -- which would label a clean
    take `asr_corroborated` (a FAIL) on marginal geometry and `asr_variance` everywhere else. The
    ratio machinery has to be switched off, not fed an empty transcript."""
    src = [{"text": "why", "start": 10.0, "end": 10.3}, {"text": "now", "start": 20.01, "end": 20.4}]
    edl = {"pieces": [{"seg": 0, "c0": 9.98, "c1": 10.4, "out_in": 0.0, "out_out": 0.42},
                      {"seg": 1, "c0": 20.0, "c1": 20.5, "out_in": 0.42, "out_out": 0.92}]}
    fed_empty = cut.check_joints(edl, src, [])
    assert fed_empty["verdict"] == "FAIL" and fed_empty["joints"][0]["reason"] == "asr_corroborated"
    off = cut.check_joints(edl, src, [], asr=False)
    assert off["verdict"] == "PASS" and off["asr"] is False
    assert off["joints"][0]["reason"] == "ok" and off["joints"][0]["ratio"] is None
    assert off["warnings"] == []

def test_check_with_asr_off_still_fails_real_geometry():
    """`your` is in the fixture because first_word_after only looks 0.02 s back from c0: a clip deeper than that reports on the NEXT word, and with
    no next word check_joints() emits no joint at all. The QC gate calls geometry() directly for exactly that reason."""
    src = [{"text": "because", "start": 14.480, "end": 15.065}, {"text": "your", "start": 15.1, "end": 15.4}]
    edl = {"pieces": [{"seg": 0, "c0": 0.0, "c1": 3.0, "out_in": 0.0, "out_out": 3.0},
                      {"seg": 1, "c0": 14.567, "c1": 15.4, "out_in": 3.0, "out_out": 3.833}]}
    r = cut.check_joints(edl, src, [], asr=False)
    assert r["verdict"] == "FAIL" and r["joints"][0]["reason"] == "clipped_head"
    assert r["joints"][0]["ratio"] is None

def test_run_with_asr_off_never_touches_ffmpeg_or_the_speech_engine(tmp_path, monkeypatch):
    """The whole point: no wav extraction and no transcription. joints_run() must reach a verdict from the EDL and the sidecar alone."""
    import words
    def boom(*a, **k): raise AssertionError("the ASR pass ran with JOINTS_ASR=off")
    monkeypatch.setattr(words, "transcribe", boom)
    monkeypatch.setattr(cut.subprocess, "run", boom)
    edl_p = tmp_path / "edl.json"
    edl_p.write_text(json.dumps({"pieces": [{"seg": 0, "c0": 10.0, "c1": 10.4, "out_in": 0.0, "out_out": 0.4},
                                            {"seg": 1, "c0": 20.0, "c1": 20.5, "out_in": 0.4, "out_out": 0.9}]}))
    words_p = tmp_path / "words.json"
    words_p.write_text(json.dumps({"words": [{"text": "why", "start": 10.05, "end": 10.3}]}))
    (tmp_path / "words.wx.json").write_text(json.dumps(
        {"segments": [{"words": [{"word": "why", "start": 10.05, "end": 10.3},
                                 {"word": "now", "start": 20.05, "end": 20.4}]}]}))
    out_p = tmp_path / "joints.json"
    r = cut.joints_run("no-such-cut.mp4", str(edl_p), str(words_p), str(out_p))
    assert r["verdict"] == "PASS" and r["asr"] is False and "whisperx" in r["stamps"]
    assert json.load(open(out_p))["asr"] is False


# --- edges read off the waveform, shared edges are not cuts ----------------------------------------------------------------
import subprocess, tempfile

def _wav(path, expr, secs=2.0):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc=" + expr.replace(",", "\\,") + f":s=16000:d={secs}", "-ac", "1", path], check=True)
    return cut.Audio(path)

def _words(*ws): return [{"text": t, "start": s, "end": e} for t, s, e in ws]
def _piece(c0, c1, out_in=0.0): return {"seg": 0, "c0": c0, "c1": c1, "out_in": out_in, "out_out": out_in + c1 - c0}

def test_a_stamp_stretched_into_silence_is_not_a_clipped_head(tmp_path):
    """The aligner stamped 'a' 0.2 s before the sound; the waveform is silent until 0.55 s. Cut at 0.5 s."""
    audio = _wav(str(tmp_path / "a.wav"), "0.3*sin(2*PI*220*t)*gte(t,0.55)")
    words = _words(("a", 0.30, 0.90), ("decent", 0.88, 1.40))
    g = cut.geometry([_piece(0.5, 1.5)], words)[0]
    assert g["clipped"] is False                     # without audio the function-word cap already takes the stretch out (a is 0.6 s long)
    stretched = _words(("decent", 0.30, 0.90))       # a content word stretched the same way: only the waveform can say
    assert cut.geometry([_piece(0.5, 1.5)], stretched)[0]["clipped"] is True
    g = cut.geometry([_piece(0.5, 1.5)], stretched, audio=audio)[0]
    assert g["clipped"] is False and g["head_stamp"] < -0.15 and g["head"] >= -0.08

def test_a_real_clip_is_still_a_clip_with_audio(tmp_path):
    audio = _wav(str(tmp_path / "a.wav"), "0.3*sin(2*PI*220*t)*gte(t,0.2)")      # sound from 0.2 s; the piece starts at 0.5 s inside the word
    g = cut.geometry([_piece(0.5, 1.5)], _words(("decent", 0.2, 1.0)), audio=audio)[0]
    assert g["clipped"] is True and g["head"] < -0.2

def test_a_word_whose_sound_is_over_before_the_piece_is_not_played_by_it(tmp_path):
    audio = _wav(str(tmp_path / "a.wav"), "0.3*sin(2*PI*220*t)*lt(t,0.4) + 0.3*sin(2*PI*330*t)*gte(t,1.0)")
    words = _words(("digitized", 0.1, 0.9), ("so", 1.0, 1.4))     # the stamp runs to 0.9; the sound stops at 0.4
    g = cut.geometry([_piece(0.6, 1.5)], words, audio=audio)[0]
    assert g["first"] == "so" and g["clipped"] is False

def test_a_shared_edge_between_adjacent_pieces_is_not_a_cut():
    words = _words(("digitized", 0.0, 1.0), ("so", 1.1, 1.5))
    pieces = [_piece(0.0, 0.5, 0.0), _piece(0.5, 1.6, 0.5)]        # one continuous stretch cut in two at 0.5 s, mid-word
    geo = cut.geometry(pieces, words)
    assert [g["clipped"] for g in geo] == [False, False]
    assert geo[0]["tail_splice"] is False and geo[1]["head_splice"] is False
    r = cut.check_joints({"pieces": pieces}, words, [], asr=False)
    assert r["verdict"] == "PASS"
    gap = [_piece(0.0, 0.5, 0.0), _piece(0.56, 1.6, 0.5)]           # 60 ms removed between them: a real cut through the word
    assert any(g["clipped"] for g in cut.geometry(gap, words))

def test_overlapped_stamps_are_split_at_the_dip_in_the_sound(tmp_path):
    """'There's' stamped 0.3 s before it begins, overlapping 'etcetera'; the real break is the dip at 1.0-1.1 s"""
    audio = _wav(str(tmp_path / "a.wav"), "0.3*sin(2*PI*220*t)*(lt(t,1.0)+gte(t,1.12))", secs=2.0)
    words = _words(("etcetera", 0.2, 1.0), ("there's", 0.7, 1.6), ("a", 1.7, 1.8))
    geo = cut.geometry([_piece(0.1, 0.99, 0.0), _piece(1.03, 1.9, 0.9)], words, audio=audio)
    assert [g["clipped"] for g in geo] == [False, False], geo
    assert geo[0]["last"] == "etcetera" and geo[1]["first"] == "there's"

def test_a_stamp_that_starts_inside_the_previous_words_tail_is_read_from_the_rise_after_the_dip(tmp_path):
    """'to' stamped over the previous word's decaying tail, silent for 0.26 s, spoken later. The first hop above the threshold is the previous
    word's; the cut at 0.7 s is inside real silence, not inside the word"""
    audio = _wav(str(tmp_path / "a.wav"), "0.3*sin(2*PI*220*t)*(lt(t,0.55)+gte(t,0.95))")
    assert audio.onset(0.52, 1.4) == pytest.approx(0.95, abs=0.011)
    words = _words(("decent", 0.52, 1.4))
    assert cut.geometry([_piece(0.7, 1.5)], words)[0]["clipped"] is True          # by the stamp alone it reads as a clip
    g = cut.geometry([_piece(0.7, 1.5)], words, audio=audio)[0]
    assert g["clipped"] is False and g["head"] > 0.2

def test_running_speech_with_no_dip_keeps_the_window_start_as_the_onset(tmp_path):
    audio = _wav(str(tmp_path / "a.wav"), "0.3*sin(2*PI*220*t)")
    assert audio.onset(0.5, 1.2) == pytest.approx(0.5, abs=0.011)
    gone = _wav(str(tmp_path / "b.wav"), "0.3*sin(2*PI*220*t)*lt(t,0.9)")                  # a long loud run is a word, not a tail: no skipping to what follows
    assert gone.onset(0.2, 1.4) == pytest.approx(0.2, abs=0.011)


# --- a struck word is not a played word, a cut inside a pause is not a clip, and the ladder moves edges ---------------------------
# The numbers are a real take the chain stopped on after three identical re-cuts.
import wave
import numpy as np

SR = 16000


def _sound(tmp_path, tones, secs=2.0, noise=0.002, name="d32.wav"):
    """a mono wav: room noise and 220 Hz tones [(t0, t1, amp)]; returns the cut.Audio of it"""
    t = np.arange(int(secs * SR)) / SR
    y = np.random.default_rng(5).normal(0, noise, len(t))
    for t0, t1, amp in tones:
        m = (t >= t0) & (t < t1)
        y[m] += amp * np.sin(2 * np.pi * 220 * t[m])
    path = str(tmp_path / name)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    return cut.Audio(path)


def test_a_struck_word_is_not_the_first_word_a_piece_plays():
    """the piece starts at 1.1667 s, in the gap after a struck "uh" (stamped to 1.20 s) and before "every" (stamped from 1.245 s): by the stamps
    the cut is 0.2 s inside the uh, which the plan removed"""
    words = _words(("hand,", 0.362, 0.703), ("uh,", 0.96, 1.20), ("every", 1.245, 1.446), ("single", 1.486, 1.727))
    pieces = [_piece(0.033, 0.8), _piece(1.1667, 1.9, out_in=0.767)]
    plain = cut.geometry(pieces, words)[1]
    assert plain["first"] == "uh," and plain["clipped"] is True
    struck = [{"text": "uh,", "start": 0.96, "end": 1.2}]
    geo = cut.geometry(pieces, words, dropped=struck)[1]
    assert geo["first"] == "every" and geo["clipped"] is False and geo["head"] == pytest.approx(0.078, abs=0.001)
    r = cut.check_joints({"pieces": pieces, "dropped": struck}, words, [], asr=False)
    assert r["verdict"] == "PASS" and r["joints"][0]["word"] == "every"
    assert cut.check_joints({"pieces": pieces}, words, [], asr=False)["verdict"] == "FAIL"      # an EDL with no list reads as before


def test_a_struck_word_only_matches_by_its_text_and_start():
    words = _words(("uh,", 0.96, 1.20), ("uh,", 5.0, 5.2), ("every", 1.245, 1.446))
    assert cut.dropped_indices(words, [{"text": "uh,", "start": 5.0, "end": 5.2}]) == {1}
    assert cut.dropped_indices(words, [{"text": "uh,", "start": 3.0, "end": 3.2}]) == set()
    assert cut.dropped_indices(words, None) == set()


def test_a_cut_inside_a_pause_is_not_a_clip_whatever_the_stamps_say(tmp_path):
    """"Um" to 0.63 s, 120 ms of silence, "it's" from 0.75 s; the aligner stamped "it's" 0.60 to 0.74 s, entirely over the pause and the tail of
    the Um, and the piece starts at 0.69 s: 90 ms inside the word by the stamps (past the 0.08 s tolerance) with no sound at the cut."""
    audio = _sound(tmp_path, [(0.30, 0.63, 0.3), (0.75, 1.2, 0.3)])
    words = _words(("um", 0.30, 0.55), ("it's", 0.60, 0.74), ("fine", 0.9, 1.1))
    piece = [_piece(0.69, 1.3)]
    assert cut.geometry(piece, words)[0]["clipped"] is True                     # the stamps alone read it as a clip
    g = cut.geometry(piece, words, audio=audio)[0]
    assert g["clipped"] is False and g["head"] == pytest.approx(0.06, abs=0.011), g
    assert g["head_stamp"] == pytest.approx(-0.09, abs=0.001) and g["head_pause"] == pytest.approx([0.63, 0.75], abs=0.011)


def test_a_cut_through_a_sound_is_still_a_clip_with_audio(tmp_path):
    audio = _sound(tmp_path, [(0.75, 1.2, 0.3)])
    g = cut.geometry([_piece(0.90, 1.3)], _words(("decent", 0.75, 1.2)), audio=audio)[0]
    assert g["clipped"] is True and "head_pause" not in g


def test_a_closure_inside_a_word_is_not_a_pause(tmp_path):
    """60 ms of near-silence inside one word (a stop closure) is under PAUSE_MIN: a cut in it still reads as a clip"""
    audio = _sound(tmp_path, [(0.30, 0.60, 0.3), (0.66, 1.0, 0.3)])
    g = cut.geometry([_piece(0.63, 1.2)], _words(("stitch", 0.30, 1.0)), audio=audio)[0]
    assert audio.pause_at(0.63) is None
    assert g["clipped"] is True


def test_the_tail_of_a_piece_inside_a_pause_is_not_a_clip_either(tmp_path):
    """the last word is stamped to 0.70 s over the start of the next word (sound from 0.62 s); the piece ends at 0.55 s in the 170 ms between them"""
    audio = _sound(tmp_path, [(0.20, 0.45, 0.3), (0.62, 0.9, 0.3)])
    words = _words(("alpha", 0.20, 0.70), ("bravo", 0.75, 0.9))
    piece = [_piece(0.1, 0.55)]
    assert cut.geometry(piece, words)[0]["clipped"] is True
    g = cut.geometry(piece, words, audio=audio)[0]
    assert g["clipped"] is False and g["tail"] == pytest.approx(0.10, abs=0.011) and g["tail_pause"] == pytest.approx([0.45, 0.62], abs=0.011)


def test_pause_at_wants_a_long_pause_and_room_on_both_sides(tmp_path):
    audio = _sound(tmp_path, [(0.30, 0.60, 0.3), (0.72, 1.0, 0.3)])                  # 120 ms pause from 0.60 to 0.72
    assert audio.pause_at(0.66) == pytest.approx((0.60, 0.72), abs=0.011)
    assert audio.pause_at(0.61) is None and audio.pause_at(0.71) is None            # at the rim: inside the pause by less than PAUSE_EDGE
    assert audio.pause_at(0.45) is None                                              # on speech
    short = _sound(tmp_path, [(0.30, 0.60, 0.3), (0.66, 1.0, 0.3)], name="short.wav")
    assert short.pause_at(0.63) is None                                              # 60 ms is a closure, not a pause


# --- the retry ladder ------------------------------------------------------------------------------------------------------

HEAD = {"joints": [{"ok": False, "fixable_by": "head_pad", "head_adj": -0.02, "tail_adj": 0.1}]}
TAIL = {"joints": [{"ok": False, "fixable_by": "tail_pad", "head_adj": 0.1, "tail_adj": -0.02}]}
EDL_A = {"pieces": [{"c0": 1.0, "c1": 2.0}, {"c0": 3.0, "c1": 4.0}]}
EDL_B = {"pieces": [{"c0": 0.96, "c1": 2.0}, {"c0": 3.0, "c1": 4.0}]}


def test_the_ladder_widens_the_head_edge_one_step_at_a_time_up_to_its_limit():
    plan = {}
    seen = []
    for _ in range(5):
        ok, msg = cut.ladder(plan, HEAD, EDL_A)
        seen.append((ok, plan.get("lead_extra"), plan.get("tail_extra")))
    assert seen[:3] == [(True, 0.04, 0.0), (True, 0.08, 0.0), (True, 0.12, 0.0)]
    assert seen[3][0] is False and "maximum" in msg and plan["lead_extra"] == 0.12


def test_the_ladder_widens_the_side_that_failed():
    plan = {}
    assert cut.ladder(plan, TAIL, EDL_A)[0] and (plan["lead_extra"], plan["tail_extra"]) == (0.0, 0.04)
    both = {"joints": HEAD["joints"] + TAIL["joints"]}
    plan = {"lead_extra": 0.04}
    assert cut.ladder(plan, both, EDL_A)[0] and (plan["lead_extra"], plan["tail_extra"]) == (0.08, 0.04)


def test_the_ladder_stops_at_once_when_no_pad_can_move_the_failure():
    ok, msg = cut.ladder({}, {"joints": [{"ok": False, "reason": "asr_variance", "fixable_by": None, "head_adj": 0.1, "tail_adj": 0.1}]}, EDL_A)
    assert ok is False and "no pad can move" in msg


def test_the_ladder_stops_when_the_last_retry_moved_no_edge():
    plan = {"lead_extra": 0.04}
    ok, msg = cut.ladder(plan, HEAD, EDL_A, prev_edl=EDL_A)
    assert ok is False and "moved no clipped edge" in msg and plan == {"lead_extra": 0.04}
    assert cut.ladder(plan, HEAD, EDL_A, prev_edl=EDL_B)[0] is True        # an edge that moved is a retry that did something


def test_the_ladder_judges_a_retry_by_the_edge_that_failed_not_by_any_edge():
    """the retry widened every other piece, but the clipped edge (the head of the piece that starts at out time 3.0) is a hand-set span that cannot
    move: only that edge counts"""
    before = {"pieces": [{"c0": 1.0, "c1": 2.0, "out_in": 0.0, "out_out": 1.0}, {"c0": 5.0, "c1": 6.0, "out_in": 1.0, "out_out": 2.0},
                         {"c0": 7.0, "c1": 8.0, "out_in": 2.0, "out_out": 3.0}]}
    after = {"pieces": [{"c0": 0.96, "c1": 2.04, "out_in": 0.0, "out_out": 1.0}, {"c0": 4.96, "c1": 6.04, "out_in": 1.0, "out_out": 2.0},
                        {"c0": 7.0, "c1": 8.04, "out_in": 2.0, "out_out": 3.0}]}
    head = {"joints": [{"t": 2.0, "ok": False, "fixable_by": "head_pad", "head_adj": -0.05, "tail_adj": 0.1}]}
    assert cut.failing_edges(head, after) == [(2, "c0")]
    ok, msg = cut.ladder({"lead_extra": 0.04}, head, after, prev_edl=before)
    assert ok is False and "moved no clipped edge" in msg
    tail = {"joints": [{"t": 2.0, "ok": False, "fixable_by": "tail_pad", "head_adj": 0.1, "tail_adj": -0.05}]}
    assert cut.failing_edges(tail, after) == [(1, "c1")]                  # a tail clip is the previous piece's end
    assert cut.ladder({"tail_extra": 0.04}, tail, after, prev_edl=before)[0] is True      # that edge moved (6.0 to 6.04)
    last = {"joints": [{"t": 3.0, "ok": False, "fixable_by": "tail_pad", "head_adj": None, "tail_adj": -0.05}]}
    assert cut.failing_edges(last, after) == [(2, "c1")]                  # the final joint is the last piece's own end
    assert cut.retry_moved_an_edge(head, {"pieces": after["pieces"][:2]}, before) is True      # a different piece count is a different cut


def test_ladder_cli_rewrites_the_plan_and_exits_3_when_it_cannot_help(tmp_path):
    import os, subprocess, sys
    def write(name, obj):
        (tmp_path / name).write_text(json.dumps(obj)); return str(tmp_path / name)
    plan_p, joints_p, edl_p = write("plan.json", {"pad_pre": 0.06}), write("joints.json", HEAD), write("edl.json", EDL_A)
    script = os.path.join(os.path.dirname(os.path.abspath(cut.__file__)), "cut.py")
    r = subprocess.run([sys.executable, script, "ladder", plan_p, joints_p, edl_p], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("LADDER:"), r.stdout + r.stderr
    assert json.load(open(plan_p)) == {"pad_pre": 0.06, "lead_extra": 0.04, "tail_extra": 0.0}
    r = subprocess.run([sys.executable, script, "ladder", plan_p, joints_p, edl_p, edl_p], capture_output=True, text=True)
    assert r.returncode == 3 and r.stdout.startswith("LADDER STOP:") and "moved no clipped edge" in r.stdout, r.stdout + r.stderr
    assert json.load(open(plan_p))["lead_extra"] == 0.04                              # a stop leaves the plan as it was
    r = subprocess.run([sys.executable, script, "ladder", plan_p, str(tmp_path / "missing.json"), edl_p], capture_output=True, text=True)
    assert r.returncode == 2 and r.stderr.startswith("STOP:")
