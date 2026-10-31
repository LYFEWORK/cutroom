#!/usr/bin/env python3
"""hear.py <wav> <out.words.json>
The optional hosted transcriber: Deepgram Nova-3 with filler_words=true (keeps um/uh and doubled words, and punctuates). It runs only when your DNA says
`transcribe: deepgram` (or CUTROOM_TRANSCRIBE=deepgram is set) AND DEEPGRAM_API_KEY is in ~/.cutroom/keys.env; otherwise the chain uses hear_local.py, which runs on this Mac
with no key. Output = the words.json contract:
{"source":"deepgram","transcript":str,"words":[{"text","start","end","filler","double","cap"}]}
Every word keeps Deepgram's own stamps. No fallback inside this file: if Deepgram fails the take stops (EngineStop). A transport error (dropped connection,
TLS reset, timeout, a 5xx) is retried 3 times with 1 s, 3 s and 9 s of backoff; a 4xx (bad key, bad request) is never retried. The key is read before the
audio is sent and is never printed."""
import http.client, json, re, socket, ssl, sys, time, urllib.error, urllib.request
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keys import get as key_of, cli, EngineStop
FILLERS = {"um", "uh", "hmm", "mm", "er", "ah", "uhm", "umm"}
def _norm(t): return re.sub(r"[^a-z0-9']", "", t.lower())
LAST_WORD_MAX_DUR = 1.5   # seconds -- see _declamp_trailing_silence
def _declamp_trailing_silence(words, max_dur=LAST_WORD_MAX_DUR):
    """Deepgram Nova-3: when the clip has a long silent/non-speech tail after the last spoken word (dead air, a b-roll cutaway, room tone),
    the API sometimes reports that LAST word's `end` as the end of the audio stream rather than the true end of the word -- a ~0.3s word
    came back with `end` 18.4s after `start`, which then poisoned every downstream consumer that trusts word timing: the QC spoken-span
    (G-FACE measured face presence over 31s of mostly b-roll instead of the real ~13s), the matte/caption windows (built out to cover a
    caption that, per this bad timestamp, was still "on screen" 18 seconds after anyone said anything), and any GATE.md `fix:` that splices
    a replacement phrase evenly across the word's [start, end] span.

    Only the LAST word is at risk this way -- every interior word's `end` is already bounded by the next word's `start`, which Deepgram
    itself keeps sane. So this clamps only `words[-1]`, and only when its duration is wildly out of line with the rest of the clip (> max_dur
    AND > 4x the median duration of every other word) -- a genuinely slow, drawn-out last word (a held note, a trailing "...you know?") is
    never touched, only the runaway-tail case."""
    if len(words) < 2:
        return words
    last = words[-1]
    dur = last["end"] - last["start"]
    if dur <= max_dur:
        return words
    others = [w["end"] - w["start"] for w in words[:-1] if w["end"] > w["start"]]
    median = sorted(others)[len(others) // 2] if others else 0.4
    if dur <= median * 4:
        return words
    words[-1] = dict(last, end=round(last["start"] + max(median, 0.3), 3), raw_end=last["end"])
    return words
def deepgram_words(dg):
    alt = dg["results"]["channels"][0]["alternatives"][0]
    words = [{"text": w.get("punctuated_word", w["word"]), "start": round(float(w["start"]), 3),
              "end": round(float(w["end"]), 3)} for w in alt["words"]]
    return _declamp_trailing_silence(words)
def flag(words):
    """each word with `filler`, `double` and `cap` added (the same three flags engine/words.py writes)"""
    out = []; prev = None
    for w in words:
        n = _norm(w["text"]); f = dict(w)
        f["filler"] = n in FILLERS
        f["double"] = bool(prev and n == prev["n"] and n and (w["start"] - prev["end"]) < 0.5)
        f["cap"] = w["text"][:1].isupper() and not (prev is None or prev["text"].endswith((".", "?", "!")))
        out.append(f); prev = {"n": n, "end": w["end"], "text": w["text"]}
    return out
BACKOFF = (1, 3, 9)    # seconds before retry 1, 2 and 3
TRANSPORT = (urllib.error.URLError, ssl.SSLError, ConnectionError, socket.timeout, TimeoutError, http.client.HTTPException)
def _post(req, timeout=300, _urlopen=urllib.request.urlopen, _sleep=time.sleep):
    """the Deepgram response as a dict: transport errors and 5xx are retried with backoff, a 4xx stops at once"""
    last = None
    for attempt in range(len(BACKOFF) + 1):
        try:
            with _urlopen(req, timeout=timeout) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500: raise EngineStop(f"Deepgram refused the request (HTTP {e.code}): {str(e)[:120]}")
            last = e
        except TRANSPORT as e:
            last = e
        if attempt < len(BACKOFF):
            print(f"Deepgram transport error ({type(last).__name__}); retry {attempt + 1} of {len(BACKOFF)} in {BACKOFF[attempt]} s", file=sys.stderr)
            _sleep(BACKOFF[attempt])
    raise EngineStop(f"Deepgram is down or refused the request after {len(BACKOFF)} retries: {type(last).__name__}: {str(last)[:120]}")
def transcribe(wav, _urlopen=urllib.request.urlopen, _sleep=time.sleep):
    key = key_of("DEEPGRAM_API_KEY")
    url = "https://api.deepgram.com/v1/listen?model=nova-3&filler_words=true&smart_format=true&punctuate=true"
    try:
        wav_bytes = open(wav, "rb").read()
    except OSError as e:
        raise EngineStop(f"could not read wav {wav}: {type(e).__name__}: {e}")
    req = urllib.request.Request(url, data=wav_bytes, method="POST")
    req.add_header("Authorization", "Token " + key); req.add_header("Content-Type", "audio/wav")
    return _post(req, _urlopen=_urlopen, _sleep=_sleep)
def build(wav):
    words = flag(deepgram_words(transcribe(wav)))
    return {"source": "deepgram", "transcript": " ".join(w["text"] for w in words), "words": words}
def _main():
    wav, out = sys.argv[1:3]
    d = build(wav)
    json.dump(d, open(out, "w"), indent=1)
    print(f"{len(d['words'])} words, {sum(w['filler'] for w in d['words'])} fillers, {sum(w['double'] for w in d['words'])} doubles -> {out}")
if __name__ == "__main__":
    cli(_main, "hear.py <wav> <out.words.json>")
