# QC gates

Every gate here enforces one or more rules. FIX blocks delivery. CHECK needs an answer in words, recorded with the video, from a person. Advisory is read by a person and never blocks. A gate marked (retune) carries defaults tuned on one set of cameras: replace them with your own measurements.

Pixel numbers are for a 1080 x 1920 frame. Where the platform (Instagram or Facebook Reels) draws its own interface over the video, the zones are measured on that frame.

| Gate | Checks | Number | Result | Rules |
|---|---|---|---|---|
| Speech-only pieces | dead footage at the ends of each piece, words in every piece, the speaker's face | lead-in 0.35 s or less, tail 0.40 s or less, face 95% or more | FIX | CUT-13 |
| Subject on camera | share of the spoken span that shows the subject | 90% or more, advisory when nobody is on camera | FIX | CUT-13 |
| Clipped word | every cut edge against the voice's own onset and decay | within 0.02 s of the voice (0.08 s against aligner times); a cut inside a pause of 90 ms or more is not a clip; retry ladder +0.04 s up to 0.12 s, 3 re-cuts | FIX | CUT-01, CUT-02, CUT-05 |
| Joint heard | words the cut's own transcript hears differently near a joint | within 0.6 s of a joint, each listed for an independent listener | CHECK | CUT-04 |
| Residual pause | silence left after the cut, scanned at -30 dB | review from 0.18 s, fail from 0.30 s (0.22 s on ads) | CHECK, FIX | CUT-09 |
| Piece overlap | adjacent pieces that overlap in source time | none | FIX | CUT-07 |
| Picture equals sound | freezes, frame match to the source, audio lag | freeze 0.5 s or less, frame match 0.9 or more, lag 60 ms or less over the first 10 s (and the last 10 s of a video of 20 s or more) and growing 20 ms or less | FIX | DEL-17 |
| Audio origin | the video's audio starts at t = 0 and matches the master | exact | FIX | DEL-17 |
| Loudness | integrated loudness and true peak of the final | -16 plus or minus 1 LUFS, true peak -1.5 dBTP or lower (target -2.0) | FIX | SND-01 |
| Bed and effects levels | music against the talk and against the voice alone, effects against speech and where they land | about 22 dB under the talk and about 14 dB under the voice alone, effects 12 to 16 dB under speech, 1.0 s apart, none in the 60 ms after a word | CHECK | SND-04, SND-10, SND-11 |
| Captions in the box | caption ink outside the safe box, on the face, above the crown | box x 65 to 940, y 270 to 1420, face box grown 6% | FIX | CAP-08 |
| Caption events | overlaps, minimum length, order, word coverage | 3 frames or more, onset within 1 frame | FIX | CAP-01, CAP-02 |
| Platform zones | text over the platform's own interface, read by OCR on every final frame, every 0.5 s | organic: top 220 px, below y 1650, x 980 or more below y 1100. Ads: top 269 px, below y 1248, 65 px sides. A hit counts on two samples in a row | FIX | CAP-17, CAP-18 |
| Do-not-say words | caption words against your do-not-say list | none, unless an override carries a reason | FIX | CAP-19 |
| Text size floor | size of all graphic text | body 52 px, label 36 px, payoff 60 px | FIX | CAP-07 |
| Head room | card height against the room above the crown | card ends 22 px above the crown, shrink floor 0.72, room under 0.85 of the need means split | FIX | HOOK-12, GFX-12 |
| Hook fit | lines, characters, words and hold of the hook card | 3 lines of 27 characters, 14 words, hold 3.0 to 6.5 s | FIX | HOOK-01, HOOK-05 |
| Hook triple | hook words against the words spoken in the first 5 s, plus a sheet of the first 6 s at 4 fps | a card word nobody says is FIX, a different order or a first spoken word after 1.5 s is CHECK | FIX, CHECK | HOOK-03, HOOK-04 |
| Text content | filler-only labels, dangling endings, bare numbers, invented numbers, list items shown early | an item more than 1.5 s before it is said, any number the speaker never says | FIX | GFX-09, GFX-10, GFX-11 |
| Color tags and pixels | pixel format, range, matrix, primaries, transfer, and pixels against the cut | yuv420p, limited range, BT.709 on all three tags, median per-channel difference 4 levels or less | FIX | COL-10 |
| Grade verdict | solid black, white-out, crushed shadows, blown highlights, accent moved | accent moved over 2 levels is FIX, any other CHECK passes with its lines on the card | FIX | COL-11, COL-13 |
| Exposure | each shot's picture | FIX: white point under 80, over 40% clipped, solid black (white point under 40). CHECK: under 185 after correction, 15% clipped with a median over 160, 70 or more under the video's usual white point | FIX, CHECK | COL-11 |
| Skin (retune) | blown, washed-out, too bright or too dark skin per piece, and skin color across clips | head 206 or less, lit-skin 90th percentile 204 or less, 1% pure white or less, hue and chroma matched across clips | FIX (CHECK when there is no face to judge) | COL-12 |
| Accent exact | your accent color inside flat drawn areas | within 8 levels of its hex | FIX | COL-13 |
| Pace | gaps between visible changes in the rendered video | more than 2.5 s with nothing new fails, a camera move never counts, videos under 8 s are only reported | FIX | GFX-04 |
| Full-screen cap | scenes against the video's screen plan | full mode: the larger of 3 and one per 5 s, 1.0 s of speaker between scenes (0.75 s footage-led) | FIX, CHECK | GFX-01, GFX-02 |
| B-roll | every cutaway judged PASS by a separate judge, shake and reuse | jitter over 9 px, jolt over 60 px, first 0.6 s moving 3.5x the rest, a quarter of frames out of focus, one shot per batch | FIX (CHECK for a different moment of a long take) | BR-02, BR-08, BR-09 |
| Photo edges | faces cut by an edge on photo slides | FAIL: face box past 1% of a side or 0.5% of the top, head point within 2.5% of a side or 3.5% of the top. WARN: within 6% | FIX (FAIL), CHECK (WARN) | DEL-11 |
| Delivered file | duration, size, audio, black frames, bitrate | cut length within 0.1 s, 1080 x 1920, audio present, first and last frame not black, 24 Mbps or less | FIX | DEL-01, DEL-03 |
| Sidecars | transcript and caption beside the video | both present, caption not a placeholder, transcript not empty | FIX | DEL-04 |
| Post caption copy | caption text against your copy rules | withheld on a FIX | FIX | CAP-20, CAP-21 |
| Working file | the video's spec file: schema, edge off the voice, edge in the voice, hook fit, hook words, unreadable source, unjudged b-roll | an edge off the voice by more than 1 frame is FIX | FIX or CHECK per code | CUT-01, HOOK-05, BR-02 |
| Visual read | one frame a second as a strip, plus an annotated contact sheet | a person reads it | advisory | REV-05 |
