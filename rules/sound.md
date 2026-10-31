# Sound

Read before cleaning a voice, choosing or mixing music, placing any sound effect, or exporting a final.

## Loudness and the voice

- **SND-01** The final measures -16 LUFS integrated (plus or minus 1). The true-peak target is -2.0 dBTP, and anything above -1.5 dBTP fails. Use two-pass loudness normalization with the limiter oversampled 4x before the AAC encode. Export AAC at 256 kbps and 48 kHz, and trim loudness after the final mix.
- **SND-02** Voice chain on every talking head: high-pass near 80 Hz, denoise, about -2 dB at 250 Hz, +1.5 dB at 3.2 kHz, de-ess, gentle compression (about 2.5:1), then loudness normalization. Voice isolation takes machines and clatter down, so pick takes away from people talking.
- **SND-03** Static (crackle, hiss) is measured and cleaned on the voice before anything is shown.

## Music

- **SND-04** Music is one steady bed that never pops up between lines. Where it plays alone (the open, the end card) it sits about 14 dB under the voice, set near -30 LUFS. Through the talk it drops a further 8 dB, about 22 dB under the talk. Measure both on the final before anything ships.
- **SND-05** The bed holds steady under a stretch of talk. A stretch is any run of words with less than 4 s of silence between them. It starts in the gap before its first word and ends after its last word's decay, read on the voice. When the voice cannot say, it starts 0.15 s before the first word and ends 0.45 s after the last, and an edge never moves more than 0.6 s from the aligned time. The bed eases down over about 1.5 s before a voice that music alone leads into, and back up over as long after the last word. It fades in about 0.25 s under the hook, and the end fade runs about 1.6 s.
- **SND-06** A bed shorter than the video loops with 1.5 s equal-power crossfades on its sounding body only. Drop the near-silent intro and fade-out, which otherwise leave a hole of 6 to 7 s.
- **SND-07** A stretch of music alone (an open, a montage, the end card) holds one steady level, and the talk holds one lower level, as SND-04 sets. Measure both on the final. The bed changes level only in the gap before a voice's first word and after its last word's decay, never on a word.
- **SND-08** A video meant to engage carries a bed unless the user says no music. The music plays through the end card and fades out with it. (judgment)
- **SND-09** Music changes from video to video and fits the moment: banter gets something light, range or action gets energy, a testimony or prayer can go reflective. Soft is one mood among many, never the default. Counting every video in posting order, the same song comes back no sooner than 6 to 7 videos later, and one default track never goes on a batch. A track lives with its piece, so an ad's or long video's track never goes under anything else. Nothing picks a bed by rotation, and a track the user has banned is refused by its checksum. (judgment)

## Sound effects

- **SND-10** Effects are a budget, not a sound on every move: about 12 a minute (16 on graphic-led formats, with a floor of 3 under 15 s), never two within 1.0 s. When the budget forces a choice, spend in this order: hard punch-in cuts, full-screen entrances and big type, a graphic arriving (one per card, not per row), what lands on a graphic, then rows and labels. Never use the same sample twice in a row. Stay silent for plain jump cuts, captions, emphasis zooms, exits and the return from b-roll.
- **SND-11** Effects are extremely subtle and always heard. They sit 12 to 16 dB under the speaking level, with the loud part in a pause between words and never on a word. A sharp sound never lands in the 60 ms after a word, because the transcript hears it as part of the word. Place effects off-center and alternate sides so the voice keeps the middle.
- **SND-12** Sound effects in an edit are clicks, ticks and blinks that land with their graphic. Reveal, swell, aha and transition sounds (whooshes, risers) read cheap. (judgment)
- **SND-14** An effect lands only where the eye also sees something happen, such as a card arriving or a value ticking. No effect plays in the first 0.5 s or the last 0.3 s of a video. (judgment)

## Licenses

- **SND-15** Keep each licensed track's license ID and the piece it was picked for. Download only while your license is active, and never put a preview file in a final. (judgment)
