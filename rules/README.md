# Rules

The craft rulebook for Cutroom. Each rule is something an editing team learned the hard way, restated as a plain rule that Claude follows while it edits and a gate can enforce on the result.

The rules apply to your own brand. Where a rule says your accent colour, your caption style or your hook spot, it means the ones in your Brand folder, never a fixed colour or font.

## How a skill reads the rules

- Each skill names the rule files it reads at the top of each step, before it does that step.
- The skill opens those files in full and applies every rule in them to the work in that step. It never works from memory of a rule, because the numbers are the point.
- A miss is reported by rule ID, in one plain line that says what failed and where: the second, the frame or the file.
- A rule is never skipped silently. An override names the rule ID and carries a one-line reason, and the Delivery Note lists it (DEL-07).
- A skill never edits these files. Your own taste lives in your Brand folder. It sets the things the rules call yours, and it may retune any number marked default.

## Measurable and judgment rules

- A measurable rule has its number in the sentence. Where a gate exists, qc-gates.md names it and says whether a miss is a FIX or a CHECK. A FIX blocks delivery. A CHECK needs an answer in words, recorded, from a person.
- A judgment rule ends in (judgment). It needs eyes or ears, or an independent judge: whoever placed, cut or wrote a thing never judges it. A judgment rule may quote a number as a guide, but the call is still a person's.
- A rule marked (optional) is a feature you can switch on. Delivery never depends on it.
- A number marked default was tuned on one set of cameras or one typeface. Retune it with your own measurements. A floor is not a default and does not move.
- A rule with no number and no tag is a plain yes or no test, such as a file that exists or a word that matches. A script can answer it.
- A rule with a number but no row in qc-gates.md has no gate. The step that makes the thing checks it by script or by eye.

## Conventions

- Every rule starts with a stable ID such as CUT-01. Other files, gate reports and notes cite it. A gap in the numbering is a rule that was retired or left out. An ID is never reused.
- Pixel numbers assume a 1080 x 1920 frame and scale with frame width (a 4K frame doubles them).

## Index

- cutting.md: where a cut lands, pace, what stays, multi-angle shoots, face lock (CUT)
- hook.md: the hook card, what it says, versions, optional writing rules (HOOK)
- captions.md: the soft build, timing, size and place, colour, platform zones, post captions (CAP)
- broll.md: matching b-roll to words, judging it, where it comes from, how it is treated (BR)
- graphics.md: screen plan, pace, camera moves, cards, numbers, lists, motion (GFX)
- sound.md: loudness, the voice chain, the music bed, sound effects, licences (SND)
- color.md: grade, exposure, skin, sky, tags and export colour (COL)
- delivery.md: file specs, the hand-off folder, posting, photo posts (DEL)
- review.md: approval, review pages, previews, the optional learning loop (REV)
- footage.md: moving, naming, sorting and cutting takes, indexes, keep and cut review (FOO)
- storage.md: cleanup, the Trash, free space, what can leave (STO)
- formats.md: whole-video treatments, each with its structure and needs
- qc-gates.md: every gate, what it checks, its number, FIX or CHECK, and the rules it enforces
- moves.md: the vocabulary of single editing devices, by group
