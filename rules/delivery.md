# Delivery

Read before rendering a final, exporting, filing, naming, uploading or scheduling anything. In this file the platform means Instagram or Facebook Reels.

## Formats

- **DEL-01** Vertical video is 1080 x 1920, 30 fps, H.264 yuv420p, BT.709. Author in 1080 space and render at 2x for a 4K source. Never upscale a 1080 source.
- **DEL-02** Photo and carousel posts are 4:5 at 1080 x 1350 JPEG, the only post size. The platform's publishing API accepts 4:5 to 1.91:1 and refuses 3:4. Stories stay 9:16.

## The file

- **DEL-03** Video bitrate is 24 Mbps or less (the platform cap is 25), yet never under its master's bitrate below that cap: ask the encoder for about 1.06x the master, then verify. The duration equals the cut within 0.1 s. Audio is present, and the first and last frame are not black.
- **DEL-04** Every export ships with a timestamped transcript of that file's own words and a plain-text, paste-ready post caption. A cutdown gets its own transcript. A placeholder caption blocks delivery.

## The hand-off folder

- **DEL-05** The hand-off folder holds only deliverables: one folder per video with `<Title>.mp4`, `<Title> - Caption.txt` and `<Title> - Transcript.md`. Everything else (the No Text cut, timeline, QC card, review page) stays in Working Files. Only files that passed QC and have a caption go in, and a video that must not post yet is held out. A failed re-render moves to `_failed/`, so export can never ship it. Export keeps a manifest of what it made and removes only files listed in it, so hand-staged folders are left alone (STO-05). Keep Working Files and `_failed/` out of reach of any scheduler or uploader. On the first delivery, tell the user the folder holds three files per video and why: the transcript and caption travel with the video.
- **DEL-06** Use plain Title Case names, with no # or % and no dates in file names. Titles are unique per batch: two videos with the same title stop the run. Repost versions get their own folder named `<Title> - <Version>`. (judgment)
- **DEL-07** One Delivery Note per batch, shown before anything is approved. It lists every open CHECK and every gate loosened (grade off, QA skipped, overrides), and shows as a banner on the review page. A clean batch reads `no open CHECKs, no gates changed`.
- **DEL-08** Nothing ships on a failed gate. A FIX stops delivery with one plain line per failure that names the second. A CHECK needs an answer in words. (judgment)

The layout, in one project folder:

```
<Project>/
  Ready To Post/<Title>/              <Title>.mp4, <Title> - Caption.txt, <Title> - Transcript.md
  Ready To Post/<Title> - <Version>/  the same three files for each hook or repost version
  Working Files/<Title>/              hooked and No Text cuts, Timeline.json, QC card, Sound Off sheet, your B-Roll drops
  Working Files/                      Review.html, Delivery Note.md, QC Failures.json, _failed/, Log.csv
```

## Posting

- **DEL-09** Scheduling happens only on request (REV-01). Upload the full-resolution master (1080 x 1920 video, 1080 x 1350 post), never a preview, a phone copy or a re-encode. Probe the uploaded media before calling a post scheduled: its resolution, and its byte size against the master when the tool did not convert it. Write the schedule file with every post's id.

## Photo posts

- **DEL-10** Photo posts use one photo per slide. Never split a horizontal across slides, and drop a wide that only works wide. Count a designed slide's photo-panel edges as frame edges. (judgment)
- **DEL-11** No face is ever cut by an edge. Run the face check on every slide. FAIL on any of these: a face box past 1% of a side or 0.5% of the top; a head point within 2.5% of a side or 3.5% of the top; someone near the camera at 25% of the frame height or more whose head leaves the top. WARN on anything tight within 6%. Open every FAIL and WARN at full size and look at every edge by eye, because the detector misses blurred foreground people.
- **DEL-12** Know who is in a photo before writing about it. Roles come from your people file. (judgment)

## Covers and static formats

- **DEL-15** Do not design covers: the platform's own frame pick stands. Only when the user asks for covers, score real frames, offer three, and keep any title inside the grid crop. (judgment)
- **DEL-16** Any repeatable static format shipping more than about 6 times gets at least three rotating treatments in one brand system (the same chrome, mark, accent and type). Assign by content: a strong photo picks hero or framed, no photo picks typographic. No two consecutive posts share a treatment, checked on a contact sheet in posting order. (judgment)

## Picture and sound

- **DEL-17** Picture and sound agree. No freeze lasts over 0.5 s, and the frame match to the source is 0.9 or more. Audio lag is 60 ms or less over the first 10 s (and the last 10 s of a video of 20 s or more), and it grows by 20 ms or less. The video's own audio starts at t = 0 and matches the master.
