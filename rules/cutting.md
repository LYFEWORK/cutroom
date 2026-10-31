# Cutting

Read before any step that selects, trims or joins takes, and before any clipped-word or pause check.

## Where a cut lands

- **CUT-01** A cut never clips a word. Every edge sits on the voice's own onset (start) or decay (end), read on a 10 ms level plus the 2.5 to 7 kHz band. Never trust aligner word times: ends run early on soft endings, starts sit 25 to 40 ms into the word, and stamps drift 60 to 135 ms either way.
- **CUT-02** Edge margins: an end sits 0.05 s after the last word's voice has decayed, and a start sits 0.04 s before the first word's onset. An edge moves at most 0.5 s to reach the voice. The check tolerance is 0.02 s.
- **CUT-03** Pads at joints between takes: head 0.06 to 0.07 s, tail 0.07 s (0.14 s after a word ending in s, z or f, which sit near -26 dB). Add a 15 ms audio micro-fade at every joint.
- **CUT-04** Flag every edge still inside the voice, and every word the cut's own transcript hears differently within 0.6 s of a joint. An independent listener, a different agent or person than the cutter, confirms each flag before anything ships. An edge a listener has pinned is never moved again.
- **CUT-05** Clipped-word retry ladder: widen the head or tail pad 0.04 s a step, up to 0.12 s, with at most three re-cuts and never into the next speech. Then stop and flag it.
- **CUT-06** Re-transcribe every final and compare it word for word with the intended text. A word the cut or the mix buries fails like a clipped one.
- **CUT-07** A joint never replays audio: adjacent pieces may not overlap in source time.

## What stays, and pace

- **CUT-08** The transcript decides what stays, and measured silence decides where cuts land. On phone takes scan silence at -30 dB, since breaths and room tone sit near -35 dB. Give a hard spot an explicit span found by probing. (judgment)
- **CUT-09** Talking-head pace: every pause of 0.25 s or more collapses to a joint of about 0.12 s, and joints at cuts run 0.0 to 0.10 s. QC reviews a remaining pause from 0.18 s and fails it from 0.30 s (0.22 s on ads).
- **CUT-10** Screen-walkthrough pace: interior pauses collapse only at 0.40 s or more, and joints run 0.10 to 0.15 s.
- **CUT-11** Tight preview cut: a pause of 0.45 s or more inside a span is jump-cut out, keeping 0.12 s after the voice and 0.08 s before the next word.
- **CUT-12** A deliberate pause inside one phrase, up to about 0.5 s, stays, because a mid-phrase cut in one shot reads as a glitch. Longer thinking pauses collapse to the target, never to zero. (judgment)
- **CUT-13** A video opens with 0.35 s or less of dead footage before the first word and ends 0.40 s or less after the last word. Every kept piece holds words. The speaker's face is on screen for 95% or more of each kept piece, and for 90% or more of the whole spoken span (advisory when nobody is on camera).
- **CUT-14** One delivery per beat. Where a line is said twice, keep the single best: clean over usable over flubbed, and the later, more confident of two clean reads. Never keep both halves of a re-say. (judgment)
- **CUT-15** Cut restarts, false starts, trailing-off fragments, self-talk, prompter glances, `um`, `uh` and clause-final `right?` tags. Keep two to four of those tags per video where the beat or a risky joint needs one. Keep content uses such as `all right` or `the right data`. (judgment)
- **CUT-16** Self-introductions and filler openers (`all right`, `so`, `now`) go. Never invent a call to action: ending without one is fine. (judgment)
- **CUT-17** Coaching audio (`one more time`, prompter talk) never plays. A read the director praises right after outranks the earlier reads. (judgment)
- **CUT-18** Every line in the cut is the on-camera person's own voice. Confirm it by pitch and by level on their mic, never by the take's label or the transcript. An off-camera example read sits 4 to 15 dB under them and in another pitch range. (judgment)
- **CUT-19** A video cut from a longer talk starts and ends on whole sentences that stand alone, and makes sense to someone who never heard the talk. If it leans on an earlier line, add that setup as its own section, or start on the first sentence that stands alone. (judgment)
- **CUT-20** Numbers and names stay exactly as said. Spelling fixes happen later, in captions. (judgment)
- **CUT-21** A cut built from approved lines never uses a struck line's words: 6 or more consecutive words from a struck line fail. An approved line that appears with fewer than 4 consecutive words is flagged as broken.

## Moves inside the picture

- **CUT-22** A jump cut gets no snap of its own. Camera moves live inside one shot, so the next cut hides the jump back (GFX-05). (judgment)
- **CUT-23** A blend between two takes of a person lands mid-movement, so the second take carries the move on. Neither take plays a beat of its own across the join, such as a look at the lens, a held stare or a silent walk-in. Pin the alignment on the head when the bodies differ. (judgment)
- **CUT-24** Multi-angle shoots: plan one setup per line. For example a walk-in open on line one, a seated medium, a tight shot from a second spot (about 1.5x), a wide, and a close held still for a push. Rotate so no two lines in a row share a setup. Film in 4K, lock exposure per setup, and cut angle to angle: a real angle change beats a zoom faked in the edit. (judgment)
- **CUT-25** Face lock on a walking talking head: lock each shot at full strength (1.0) before captions and graphics are placed, so they sit on a picture that holds still. Use one shared punch-in per video, the smallest that keeps frame edges out, 15% at most. When the person walks into or out of frame on purpose, the camera eases to a stop and lets them go.
- **CUT-26** Faked background softening (a cut-out person over a blurred ground) stays near 2 px on a 1080 frame. Keep the ground at the feet sharp, and only slightly softer the farther back it goes. On anyone who is moving it is off, because the matte trails the head. Never relight a person with a tracked mask.
- **CUT-27** Triage each take before editing. Structured (a hook and a point): full treatment. Hook without a payoff: tight cut and light captions. Loose talk (just clean it up): tight cut only, no graphics. Hand edit (a creative brief): package the footage for a human editor. (judgment)
