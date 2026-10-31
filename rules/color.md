# Color and exposure

Read before grading, matching cameras, measuring a picture, pulling a still from video, or exporting.

## Base and checks

- **COL-01** One clean base per camera, matched once: exposure on the face, white balance on the garment (never on skin), blacks matched, and a light skin secondary. Then one highlight roll-off per camera and nothing stacked on top: no per-stretch trims, frame holds or soft finish. (judgment)
- **COL-02** Before anyone sees anything, look at one frame per camera and at both sides of every cut between cameras. Settle exposure by eye on a short test cut, because an automatic match can push midday log hot. A gate pass is not proof. (judgment)
- **COL-03** Check color as well as exposure on every location. Firelight turns faces crimson under a day balance, so fire and night shots get their own white-balance trim and lower saturation. An approved look for a location is not a pass for every clip shot there: b-roll and drone clips count too, with the drone's blacks matched to the main camera. (judgment)
- **COL-04** Measure the log before grading a blown area. Where the camera clipped (every channel stopped at one ceiling) there is no detail to bring back, so frame it out and let it end clean white. A highlight knee only recovers skies and clouds that did not clip, and it must not cap whites gray. (judgment)

## Skies, casts and stills

- **COL-05** Measure every sky's hue at every second the edit uses. A natural sky lands at 200 to 210 degrees. A sky clipped in green and blue reads aqua, and a per-channel curve slides a bright blue toward cyan. A sky that is cyan in the camera gets a sky secondary in the base grade, the same on every camera. It turns bright cyan blues toward about 204 degrees and leaves skin, shirts and smoke untouched.
- **COL-06** Never keep a cast because a crop or a later step hides it. Reviewers see full frames. (judgment)
- **COL-07** A still pulled from video for a post or carousel comes from the clip after its full grade, never from a quick balance on a grabbed frame, which cannot see the log. (judgment)
- **COL-08** When the user grades part of a batch by hand, match every other clip in it to their look, never leaving one on the automatic grade beside theirs. Learn the look from graded and ungraded takes of the same footage, with exposure matched on a shared surface. (judgment)

## Pipeline and export

- **COL-09** Every clip, log or not (S-Log3, D-Log, S-Cinetone, HLG), goes through a color-managed grader first, with its camera log named per clip. An automatic grade is for measuring only: it reads log footage gray and breaks the edges of clouds. Set one exposure offset per clip from the face, and let b-roll take the camera's default for its light. Use one look per lighting setup, with fire getting its own. A per-clip trim, such as a drone's color, sits on that clip alone. 8-bit night log is never pushed past +3 stops, or it goes gray and grainy. The one exception is a simple phone talking head (cut down, captions on): it gets no grade at all and plays as shot. (judgment)
- **COL-10** HDR phone footage (HLG or PQ) is tone-mapped to BT.709 SDR. Every export is yuv420p, limited range, with matrix, primaries and transfer all tagged BT.709. Frames render as PNG with an explicit BT.709 conversion, and a camera's super-whites are read, not clipped, before measuring.

## Gates

- **COL-11** Exposure gate. FIX for murky (white point under 80), white-out (over 40% of the frame clipped) or solid black all the way through a shot (white point under 40). CHECK for dark after correction (white point under 185), bright (15% clipped with a median over 160) or a shot 70 or more under the video's usual white point. White point is read on a 0 to 255 scale. Numbers cannot tell a black polo from underexposure, so a person looks at each CHECK and answers it with a recorded reason.
- **COL-12** Skin gate defaults, tuned on one camera set, so retune per camera. The top of the head (95th percentile) is 206 of 255 or less. The lit skin's 90th percentile is 204 or less, with no more than 1% at pure white (235 or more). Skin hue and chroma match across clips: on that set, about 44 to 50 degrees and 18 to 22. A face under 5% of the frame height is not judged and gets a CHECK.
- **COL-13** Your accent color reaches the screen exact. Measured inside flat drawn areas, never footage, it sits within 8 levels of its hex. An untagged sting or logo file decodes on the wrong matrix and goes about 10% dull, so tag it or convert it.
