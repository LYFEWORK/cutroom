# Footage

Read before organising a shoot, naming or sorting clips, cutting takes, building an index, or running the keep and cut review.

## Moving and naming

- **FOO-01** Camera originals are moved, never re-encoded or deleted by the tool. Only a same-volume move is allowed, with the plan and manifest written before the first move, and every move is reversible byte for byte. A cross-volume move or an existing destination is refused.
- **FOO-02** Every filename says what the clip is, with the camera's clip ID as the first word. Interview: `<clip> - <Qxx> - <Topic> - Take n`. B-roll: `<clip> - <What Is On Screen>`, with `- No Audio` or `- Candid Talk` added when it applies. Strip # and %, use plain Title Case, no numbered buckets, and nothing deeper than two levels. (judgment)
- **FOO-03** The tree is `Interview/<Question>/`, `B-Roll/<Subject>/`, `Not <Client>/`, `Unusable/` and `Camera Sidecars/`. Ad shoots add `B-Roll/Bloopers/`, `Bad Takes/` and `Raw Clips/` plus a takes manifest, never an `Assembly/` folder. Draft 8 to 13 subject folders from real contact sheets. File b-roll by what is on screen, never by hook, value or mood. (judgment)

## Sorting

- **FOO-04** Category comes from the audio and subject from the picture, one category per clip: interview, candid talk, b-roll with sound, b-roll silent, stray footage of another client, unusable. (judgment)
- **FOO-05** Silence is real. A max volume under -60 dB means nothing was recorded. Transcript text on such a clip (`Thank you.`, `Transcription by ...`) is hallucination, never content. A transcript under 3 letters or digits, or only filler, is no speech.
- **FOO-06** Nothing is filed off a single read: a classifier, then two verifiers (a visual lens and a content lens), then a judge on any dispute. Fix a missing verdict by running the verifiers. (judgment)

## Takes

- **FOO-07** Every attempt becomes its own file, from the first scripted word to the last. A good read after a bad one in the same clip comes out on its own. Each take keeps 5 s of real footage before and after, never black and never tightened. Bloopers are cut the same way.
- **FOO-08** A take is the whole read through to where the speaker stops, the stumble that ends it included. Never split one read into a clean take and a bad tail, never end a take mid-sentence, and treat a complete read with a small stumble as Usable, never Bad. (judgment)
- **FOO-09** Good takes stay in their script line's folder. Every flubbed take goes into one `Bad Takes/` folder at the shoot root, the default on every ad shoot, so the coverage strip counts good takes only. (judgment)
- **FOO-10** Keep each clip at its native resolution: no proxies, no upscale, no slate or overlay, and rotation tags honoured. Take files are re-encoded at 1.3x the camera clip's video bitrate or more (floor 8 Mbit/s, no cap). Verify each is not under the source: re-cut at 1.6x, then at CRF 9, else fail by name.

## Sidecars and indexes

- **FOO-11** A sidecar belongs to the clip whose ID it starts with (followed by `.`, `_`, `-` or `M` plus digits) and moves to `Camera Sidecars/`. The colour step reads the camera's picture profile from it.
- **FOO-12** Every video file gets a text file beside it: what it is, what is said with m:ss times, and what is on screen. A self-contained, phone-friendly index is the first page the user opens, with thumbnail, duration, audio badge, exact filename, question asked, summary, best quote, whole-transcript search and filters. (judgment)
- **FOO-13** Keep and cut review comes before use. Every b-roll clip and finished video plays on a review page with Keep, Cut and trim, and only Keep is placed. Picks live on the device and copy out as a plain list. A wrong label can be corrected on the page and refiles the clip on apply. (judgment)

## Trims and transcripts

- **FOO-14** Trims made by the review step are stream copies that start on the keyframe at or before the kept start. Moments cut from a long clip keep 5 s either side. Each moment is one file named by date, id and what is on screen, and the original long clip stays.
- **FOO-15** Transcripts are keyed by the recording (path, size, modified time, hash), never by file name. Camera names count up from C0001 on every card, so two shoots can share one. Transcript files are never hard-linked or symlinked between batches.
- **FOO-16** Files the cloud sync has evicted from disk (dataless files) hang the media probe, so download them before a batch runs.

## Library

- **FOO-17** A b-roll clip kept on two shoots, the same camera file copied twice, is one library entry keyed by content hash. (judgment)
