export const meta = {
  name: 'footage-catalog',
  description: 'Catalog raw shoot clips: classify each (interview vs b-roll), match to the question list, name it; two adversarial verifiers per clip; a judge settles disputes',
  phases: [
    { title: 'Classify', detail: 'one agent per clip: transcript + contact sheet -> record' },
    { title: 'Verify', detail: 'visual lens + content lens try to refute each record' },
    { title: 'Resolve', detail: 'judge settles any disputed record' },
  ],
}
// This is the workflow route. Claude Code's Workflow tool runs it; without that tool use prompts/classify-batch.md, which writes the same files.
// args = the output of workflow_args.py: { scratch, clips, context, subjects, has_questions }
const SCR = args.scratch
const OUT = `${SCR}/results`
const CHEAT = `${SCR}/cheat-sheet.txt`
const clips = args.clips
const SUBJECTS = args.subjects.includes('Other') ? args.subjects : [...args.subjects, 'Other']

const REC = {
  type: 'object',
  properties: {
    clip: { type: 'string' },
    category: { type: 'string', enum: ['interview', 'candid_talk', 'broll_with_sound', 'broll_silent', 'off_topic', 'unusable'] },
    reel: { type: 'string' },
    reel_title: { type: 'string' },
    question_asked: { type: 'string' },
    answer_summary: { type: 'string' },
    key_quote: { type: 'string' },
    on_screen: { type: 'string' },
    shot: { type: 'string', enum: ['wide', 'medium', 'close_up', 'mixed'] },
    camera: { type: 'string', enum: ['static', 'handheld', 'pan', 'tilt', 'push_in', 'orbit', 'mixed'] },
    subject_folder: { type: 'string', enum: SUBJECTS },
    subject_other: { type: 'string' },
    people: { type: 'string' },
    name: { type: 'string' },
    flags: { type: 'array', items: { type: 'string' } },
    usable: { type: 'boolean' },
    confidence: { type: 'number' },
    notes: { type: 'string' },
  },
  required: ['clip', 'category', 'reel', 'reel_title', 'question_asked', 'answer_summary', 'key_quote', 'on_screen', 'shot', 'camera',
    'subject_folder', 'subject_other', 'people', 'name', 'flags', 'usable', 'confidence', 'notes'],
}
const VERDICT = {
  type: 'object',
  properties: { clip: { type: 'string' }, agree: { type: 'boolean' }, issues: { type: 'array', items: { type: 'string' } },
    corrections: { type: 'object' }, confidence: { type: 'number' } },
  required: ['clip', 'agree', 'issues', 'corrections', 'confidence'],
}

const CONTEXT = `CONTEXT: ${args.context}`
const RULES = `RULES
- digitally_silent=true means the camera recorded NO audio (max volume below -60 dB). no_speech=true means whisper heard nothing real (hum, room tone). Any text in raw_whisper_discarded is a whisper hallucination ("Thank you.", "Transcription by CastingWords", "."): ignore it completely. Silent clips are broll_silent unless off_topic or unusable; no_speech clips with sound are broll_with_sound unless off_topic or unusable.
- audio_unreadable=true means the file has an audio stream the tools could not decode, so it counts as silent in the evidence but may not be. Classify it from the picture, say so in notes, and lower confidence.
- category: interview = the subject (or anyone) answering a question or speaking to camera with intent, including scripted intros and outros and walk-and-talk explanations. candid_talk = real conversation on camera that is NOT an interview answer (troubleshooting, chit-chat, "keep going", directions). broll_with_sound = visual clip with ambient sound but no meaningful speech. broll_silent = no audio. off_topic = clearly a different subject or place than this shoot is about (another shoot on the same card, a personal clip). unusable = genuinely nothing usable (black, accidental and under 2 seconds, lens cap). Be strict about unusable: almost everything is usable.
- reel: ${args.has_questions
    ? 'map interview answers to the question IDs in the cheat sheet by TOPIC even when the subject freestyles or the interviewer words it differently. An on-camera answer that matches none = "BONUS" with a short reel_title you invent for the topic.'
    : 'no question list exists: every interview answer gets reel "BONUS" and a short reel_title naming its topic (reuse identical wording for clips on the same topic).'} Non-interview clips: reel "NONE", reel_title "".
- question_asked: quote what the interviewer actually asks from the transcript. If the interviewer is not audible, infer it from the answer and append " (inferred)".
- key_quote: a verbatim soundbite from the transcript, max 20 words, "" if none.
- name: 3 to 9 words, Title Case, plain English an editor would search. No clip IDs, no reel IDs. Interview: the topic plus what distinguishes the take ("Why Our Process Takes Three Days", "Welcome Intro Freestyle Take"). B-roll: what is on screen, subject first, specific ("Barista Steaming Milk Close Up", "Pouring Coffee Into Paper Cup"). Never generic names like "Shop Footage" or "B-Roll Clip". Two clips must never get the same name: say what differs.
- on_screen: concrete: who, doing what, which tool or object, where, colors, visible text or logos. Note if a phone, the interviewer, or a cheat sheet is visible.
- shot / camera: judge from the timestamped tiles (framing change across tiles = movement).
- subject_folder: where or what the clip shows, from the fixed list. Interview clips get one too (the place they were filmed). Use Other + subject_other only when nothing fits.
- flags (use [] if none): profanity, confidential (a named person or job that should not air, or someone says it cannot be on camera), false_start, cut_off_early (answer clearly truncated at clip end), shaky, out_of_focus, interviewer_audible, scripted_take, freestyle_take, multiple_topics, staff_in_frame, phone_in_frame, off_topic_content.
- people: who is in frame. "nobody" for hands-only or object-only shots.
- confidence 0 to 1.`

phase('Classify')
const finals = await pipeline(
  clips,
  clip => agent(`${CONTEXT}

TASK: Catalog ONE raw clip so an editor can find it by name. Clip ${clip}.
1. Read the evidence file ${SCR}/ev/${clip}.json: duration, audio stats, digitally_silent, transcript (empty when silent or no_speech), rotation, orientation, sheet path, frames_dir.
2. Read the cheat sheet ${CHEAT}: the question list for this shoot (and the shot list if any).
3. Read the contact sheet image at the "sheet" path from the evidence (each tile is timestamped). If tiles are ambiguous, Read a few individual frames from frames_dir.
4. Produce the record. Write it as JSON to ${OUT}/${clip}.classify.json with the Write tool, then return the same record as your structured output.

${RULES}`, { label: `classify:${clip}`, phase: 'Classify', schema: REC }),

  (rec, clip) => parallel([
    () => agent(`${CONTEXT}

ADVERSARIAL VISUAL CHECK for clip ${clip}. A first-pass catalog record is inline below (also at ${OUT}/${clip}.classify.json). Do NOT trust it. Your job is to REFUTE it on VISUAL grounds only.
1. Read the evidence ${SCR}/ev/${clip}.json for the sheet path and frames_dir. 2. Read the contact sheet image; Read individual frames from frames_dir when a tile is unclear.
Check: Is on_screen accurate and specific (who, action, tool, object, colors, visible text)? Are people, shot, camera, subject_folder right? For b-roll, does the name describe what is ACTUALLY visible, specifically enough that an editor would pick this clip over a similar one? Is category plausible from the visuals (person speaking to camera vs hands-only)? Missed visual flags: shaky, out_of_focus, staff_in_frame, phone_in_frame, off_topic_content, black frames? Is usable right?
Return agree=true ONLY if you found no material error. issues = each concrete problem. corrections = ONLY the fields you would change, with the corrected values (a partial record). Write your verdict JSON to ${OUT}/${clip}.visual.json with the Write tool and return it.

RECORD:
${JSON.stringify(rec)}`, { label: `verify-visual:${clip}`, phase: 'Verify', schema: VERDICT }),

    () => agent(`${CONTEXT}

ADVERSARIAL CONTENT CHECK for clip ${clip}. A first-pass catalog record is inline below (also at ${OUT}/${clip}.classify.json). Do NOT trust it. Your job is to REFUTE it on CONTENT grounds.
1. Read the evidence ${SCR}/ev/${clip}.json (audio stats, digitally_silent, full transcript). 2. Read the cheat sheet ${CHEAT}.
Check: Is category right given the audio (digitally_silent means broll_silent; whisper text on silent clips is hallucination and must not drive anything)? Is the reel mapping the best TOPICAL match: would a different question fit better, or is a BONUS really one of the listed questions? Is question_asked faithful to the transcript (or correctly marked inferred)? Is answer_summary accurate? Is key_quote VERBATIM from the transcript and 20 words or fewer? Flags: profanity present but unflagged (or flagged but absent)? confidential? cut_off_early (last sentence ends mid-thought)? interviewer_audible? scripted vs freestyle? Is the name the phrase an editor would search for, and does it distinguish this take from other takes of the same topic?
Return agree=true ONLY if you found no material error. issues = each concrete problem. corrections = ONLY fields you would change, with corrected values (a partial record). Write your verdict JSON to ${OUT}/${clip}.content.json with the Write tool and return it.

RECORD:
${JSON.stringify(rec)}

${RULES}`, { label: `verify-content:${clip}`, phase: 'Verify', schema: VERDICT }),
  ]).then(([visual, content]) => ({ rec, visual, content })),

  async ({ rec, visual, content }, clip) => {
    const vOk = visual && visual.agree
    const cOk = content && content.agree
    if (vOk && cOk) return { ...rec, verified: 'both_agree', resolver: false }
    const final = await agent(`${CONTEXT}

FINAL JUDGE for clip ${clip}. Two adversarial reviewers checked a first-pass record; at least one disputed it. Settle every disputed field against the EVIDENCE, not by majority.
Inputs: original record, visual verdict, content verdict (all inline below). Re-read only what you need: evidence ${SCR}/ev/${clip}.json (transcript + audio stats), cheat sheet ${CHEAT}, the contact sheet image at the evidence "sheet" path. Read at most 4 individual frames from frames_dir. Do not crop, zoom, or generate new images.
Produce the FINAL complete record (full schema). Accept a correction only if the evidence supports it; keep the original where the reviewer is wrong. Put a one-line rationale for each changed field in notes. Write it to ${OUT}/${clip}.final.json with the Write tool and return it.

ORIGINAL RECORD:
${JSON.stringify(rec)}

VISUAL VERDICT:
${JSON.stringify(visual)}

CONTENT VERDICT:
${JSON.stringify(content)}

${RULES}`, { label: `resolve:${clip}`, phase: 'Resolve', schema: REC })
    return { ...(final || rec), verified: final ? 'resolved' : 'resolver_failed', resolver: true }
  },
)

const ok = finals.filter(Boolean)
log(`${ok.length}/${clips.length} clips cataloged; ${ok.filter(f => f.resolver).length} went to the judge`)
const byCat = {}
for (const f of ok) byCat[f.category] = (byCat[f.category] || 0) + 1
return { count: ok.length, missing: clips.filter((c, i) => !finals[i]), categories: byCat }
