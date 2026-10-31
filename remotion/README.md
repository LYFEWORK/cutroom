# Cutroom renderer

One Remotion project that draws an edit over a finished cut: the hook card, caption pages, b-roll inserts, graphics, the brand mark and an end card, in the user's own look. It plays one cut file and layers things over it. It never cuts and never touches sound: the cut's own audio is the voice, and `engine/render.py` mixes the music and effects afterwards.

Two compositions are this project's own: **`Edit`** (a cut with layers over it) and **`Montage`** (b-roll and voiceover with no talking head). Their size, rate and length come from their props, so one composition serves 1080x1920, 1920x1080 and 1080x1350. `Root.tsx` also registers the caption engine's `Captions` composition (from `captions/register.ts`), which is not covered here.

```bash
npm ci                  # install (about 250 MB, Remotion 4)
npm run typecheck       # tsc --noEmit
npm run studio          # Remotion Studio, to look at a props file
```

You almost never call Remotion yourself. `python3 engine/render.py edit.json --out final.mp4` turns an edit plan into these props (conforms the inserts, links the theme's files into the public folder, converts the face boxes), renders video-only, mixes the sound and checks the result. To look at a plan in Studio:

```bash
python3 engine/render.py edit.json --out final.mp4 --work /tmp/w --prep-only
cd remotion && npx remotion studio --props=/tmp/w/props.json --public-dir=/tmp/w/public
```

## `Edit` props

Times are seconds on the cut's clock. Positions are pixels in the output frame (`render.py` converts the plan's face boxes from the cut's frame).

| key | type | default | what it does |
|---|---|---|---|
| `src` | string | `"cut.mp4"` | the cut, a file in the public folder. Its audio is played (Studio) and dropped by `render.py` (`--muted`). |
| `theme` | Theme | required | the look (below). A theme with no `id` or no `colors.accent` is refused. |
| `words` | `{text, start_s, end_s, accent?, neg?}[]` | `[]` | the spoken words. `accent` draws a word in the accent colour, `neg` in the negative colour with a glow. |
| `captions_mode` | `"feed"` / `"none"` / `"external"` | `"feed"` | `feed` draws the simple caption pages (up to the theme's `cap_words` words a page, 3 by default, at its `caption_top` and `caption_size`). `none` and `external` draw none (an external caption chain burns them later). |
| `feed` | `{place?, size?, words?, motion?, hero?}` | `null` | look knobs for the pages. `place`: `chest` (theme default), `lower`, `top`. `size`: a scale on the caption size (0.7 to 1.5). `words`: words a page, never more than the theme's `cap_words`. `motion`: `reveal` (a quick pop), `quiet` (a fade), `soft` (a fade out of a little blur). `hero`: `{look: color / bigger / bigger_bold, scale: 1.3 to 2.8, pick: accent / page}`, how the key words look and which word a page uses. |
| `zooms` | `{start_s, end_s, z, cx?, cy?, ease_s?}[]` | `[]` | eased emphasis zooms: `z` is the peak scale (1.12 is a gentle push), `cx, cy` the point it pushes toward (default the face), `ease_s` seconds in and out (default 0.4). A zoom moves the head, and a card placed above the head is fitted against the zoomed head. |
| `inserts` | `Insert[]` | `[]` | b-roll, see below. |
| `hook` | `{lines, end_s?, look?, top?, size?, turn_s?}` | `null` | the hook card, on screen fully formed from frame 0 and holding to `end_s` (default 4.5). `lines`: up to 3 strings, `*word*` is an accent word (`text` is one line instead). `look`: `card`, `accent`, `ink`, `bigtype`, `bar`, `mono` (default: the theme's `hook_look`). `top`, `size`: this hook's own spot and size (fractions of the frame, or px in a 1080 x 1920 frame). `turn_s` (the mono look): the second the key words turn to the negative colour and the picture drops to black and white until the hook leaves. |
| `face` | `{cx, cy, h?}` | `null` | the speaker's face box (forehead to chin) in output pixels. The crown is taken as `cy - 0.76 h`. |
| `track` | `{"0": {cx, cy, h}, ...}` | `null` | the face box each second. Better than one `face`. |
| `graphics` | `{component, start_s, end_s, props?, captions?}[]` | `[]` | your graphics, by id from `graphics/index.ts`. `captions: "hide"` leaves the caption pages out while it is up. |
| `mark` | boolean or null | `null` | draw the brand mark under the platform's top band. `null`: the theme's `lockup_on`. |
| `endCard` | `{at_s, line?, handle?}` | `null` | a full-frame card in the theme's colours from `at_s` to the end: the mark, the line in big type, the handle in the accent. `line` and `handle` default to the theme's `end_card`. |
| `safe` | `{top, bottom, rail, railTop}` | `null` | platform UI to stay out of, as fractions of the frame (a partial object overrides the defaults below). |
| `durationInFrames`, `fps`, `width`, `height` | number | 300, 30, 1080, 1920 | the render's length, rate and size. |

**`Insert`**: `{src, start_s, end_s, mode?, transition?, from_s?, rate?, side?, w?, zoom?, origin?}`.

- `src` is a file in the public folder (`render.py` conforms the clip: cover-cropped to the frame, constant rate, silent, a second of handle).
- `mode`: `full` fills the frame (default); `window` floats in a rounded window on the side away from the face (`side: "left" | "right"` to choose, `w` its width in px at 1080 wide); `pip` fills the frame and puts the speaker in a small box at lower left (about x 66, y 1180, 300 x 380 at 1080 x 1920) while captions move beside it.
- `transition` (full and pip): `cut` (a 4-frame dissolve), `whip-l`, `whip-r` (a sideways whip pan, both pictures move), `zoom` (a zoom-through), `soft` (a dissolve-push) or `band` (a diagonal band wipe with an accent edge). With none given the theme's `broll_style` decides: `cut` (default), `dynamic` (whip-l, zoom, whip-r in turn, or `soft` when `smooth_motion` is on), `soft`, `band`. Two inserts that meet push straight into each other.
- `from_s`, `rate`: where in the clip it starts and how fast it plays. `zoom`, `origin`: a slow extra push into the shot and where it pushes from (`"40% 55%"`).

## `Montage` props

The same `theme`, `words`, `captions_mode`, `feed`, `hook`, `mark`, `endCard`, `safe`, `durationInFrames`, `fps`, `width`, `height`, plus:

| key | type | default | what it does |
|---|---|---|---|
| `clips` | `{src, at, dur, from_s?, rate?, z?, pos?, origin?, in?, inDur?, dir?, vol?}[]` | `[]` | layered shots. `at`: when it starts, `dur`: how long it plays. `in`: `cut` (default), `dissolve` or `push` (a sideways push with real motion blur, `dir: "left" | "right"`); `inDur` seconds (default 0.35). `z: [z0, z1]` is the slow drift over the shot (default 1.0 to 1.04). `pos`, `origin`: where the crop sits and pushes from. |
| `audio` | boolean | `false` | play the clips' own sound, `beds`, `vo`, `music` and `sfx` inside the composition (for Studio). A render leaves it off: `render.py` mixes the voiceover, the music and the effects after. |
| `beds`, `vo`, `music`, `sfx` | see `types.ts` | | the sound layers `audio: true` plays. |

## The theme

The design theme is the DNA's `theme` dict (`python3 engine/dna.py theme`). `applyTheme()` takes it as is. Only `id` and `colors.accent` are required; anything else left out takes the default shown.

| key | default | what it does |
|---|---|---|
| `id`, `name` | required, `""` | the theme's id; the name is drawn next to an emblem when the mark has no lockup file |
| `colors.accent` | required | key words, the accent hook looks, chips, glows |
| `colors.ink` | `#0a0a0a` | type on a light card |
| `colors.base` | `#ffffff` | the light colour: caption type, the light hook card |
| `colors.on_accent` | the readable one of ink, base, white, black | type on the accent |
| `colors.negative` | `#e5484d` | negative caption words and the mono hook's turn |
| `colors.bg` | `colors.ink` | the end card's floor |
| `colors.card` | `bg` lightened 7% | dark cards |
| `surface` `{background, border}` | the card colour at 94%, a faint light border | the dark card surface (the `ink` hook, `TitleCard`) |
| `font_family`, `font_faces` `[{weight, src}]`, `font_fallback` | system sans | the display face. Faces are registered under `font_family` (a weight can be a range, `"100 900"`) and the page waits for them before it draws. `src` is a file in the public folder (`render.py` puts the theme's files there). |
| `running_family`, `running_faces`, `mono_family`, `mono_faces` | none | a second and a mono face, same shape. The `mono` hook look draws in the mono face. |
| `caps` | `false` | everything in capitals |
| `skew` | `0` | degrees the accent chip leans (the `bar` hook, the name chip) |
| `cap_words` | `3` | words a caption page (1 to 6) |
| `caption_size`, `caption_top`, `caption_stroke` | `84`, `1190`, `0` | caption size (px of a 1080 wide frame), the top of the caption box (px from the top of a 1920 tall frame, or a 0 to 1 fraction), the dark outline under the words (px) |
| `hook_look` | `card` | `card`, `accent`, `ink`, `bigtype`, `bar`, `mono`. Anything else is refused. |
| `hook_top`, `hook_size` | `300`, `null` | the card's top (px from the top of a 1920 tall frame, or a 0 to 1 fraction) and its type size in px (`null`: fitted to the longest line) |
| `hook_font` | `null` | `display` (default), `running` (the running face) or a family name |
| `hook_on` | `true` | `false` turns text hooks off (`render.py` then draws none) |
| `lockup_src`, `lockup_dark_src`, `emblem_src`, `lockup_on` | none, `false` | the mark: the dark lockup is used over footage, the light one on a light ground; with no lockup the emblem and the name. Nothing draws unless `lockup_on` (or the edit's `mark`) is on. |
| `broll_style` | `cut` | how inserts enter and leave when they name no transition |
| `smooth_motion` | `false` | slower, softer easing on entrances, zooms and inserts; `soft` transitions |
| `music_level` | | accepted and not read: the bed follows the voice (22 dB under it through the talk, 14 dB where the voice is absent) |
| `end_card` `{on, line, handle}` | | defaults for the edit's `endCard` |

Motion everywhere is one long smooth ease-out with a little blur and a little rise: nothing springs, nothing overshoots, exits take 0.3 s or less. The hook is the one thing with no entrance.

## Layout

`layout.ts` derives every position from the composition's size: fractions of the frame, and sizes scaled by `k`, the short side over 1080. At 1080 x 1920 that gives a caption box top of 1190, a hook top of 300, the picture-in-picture box at x 66, y 1180, the mark at y 236 and the lower-third slot just above y 1630.

The platform UI to stay out of (`safe`) has three defaults, picked by shape: a full-screen vertical frame (a top band of 220 px, a bottom band of 270 px, a right rail 100 px wide from y 1100), a feed post (4:5 and square: 4% and 5%, a thin rail) and a landscape player (5% and 9%, no rail). Pass `safe` to change them. Captions, the hook, the mark and the picture-in-picture box all keep to them.

**Head clearance.** A card in the top zone (the hook, `TitleCard` placed on top) measures itself and shrinks from its top edge until it ends 22 px above the crown at the highest it gets during its window, down to 72% of its size; if it still ends too low it slides up as far as the platform's top band. A card whose top is already below that line (a hook set under the chin with `hook.top`) is left where it is. With no `face` or `track` nothing is fitted. When the head leaves no room at all, the card still overlaps it: move that video's hook (`hook.top`), shorten it, or drop it.

## Add a graphic

A graphic is a React component the plan asks for by id, with props, for a window of time.

1. Write `graphics/MyCard.tsx` and export a component typed `GraphicProps`:

   ```tsx
   import React from "react";
   import { BRAND, C, SOLID, appear } from "../theme";
   import type { GraphicProps } from "../types";

   export const MyCard: React.FC<GraphicProps<{ text: string }>> = ({ props, start_s, end_s, t, layout: L }) => (
     <div style={{ position: "absolute", left: L.lower.left, bottom: L.h - L.lower.bottom, ...SOLID, padding: 24 * L.k,
                   color: C.base, fontFamily: BRAND.fontFamily, fontSize: 52 * L.k, ...appear(t, start_s, end_s) }}>
       {props.text}
     </div>
   );
   ```

2. Register it in `graphics/index.ts`: import it and add it to `GRAPHICS` (the key is the id the plan uses).
3. List it in the DNA's `graphics.built` (`{id, component: "MyCard"}`) so the edit skill knows it exists.
4. Ask for it in an edit plan: `{"component": "MyCard", "start_s": 2.0, "end_s": 4.5, "props": {"text": "..."}}` (a DNA `graphics.built` id works as `component` too). `render.py` refuses an id that is not registered before it starts a render.
5. Look at it: `python3 engine/render.py edit.json --out /tmp/x.mp4 --work /tmp/w --prep-only`, then Studio (above), or render a few seconds.

What a graphic receives: `props` (the plan's), `start_s` and `end_s`, `t` (the composition's clock in seconds, the same on every frame of the graphic), and `layout` (positions and sizes: `layout.k`, `layout.safe`, `layout.hook`, `layout.caption`, `layout.lower`, `layout.pip`, `layout.mark`). The component sits in a `Sequence` for its window, so it mounts at `start_s` and goes at `end_s`; it draws its own entrance and exit (`appear(t, start_s, end_s)` from `theme.ts` is the long ease-out in and a short fade out).

Helpers: from `theme.ts` the colours `C` (`accent`, `ink`, `base`, `onAccent`, `negative`, `bg`, `card`), `BRAND` (`fontFamily`, `caps`, `skew`, `smooth`), `SOLID` (the dark card surface), `appear`, `condense`, `leave`, `tint`, `negTint`, the easings, and `res()` for a public-folder file; from `overlays.tsx` `rich()` (`*word*` in the accent colour), `FitAbove` and `useEditCtx()` (`crownAt`, `crownMin`: where the head is) for a card that must clear it. Look at `TitleCard.tsx` for a card that does both.

Rules a graphic keeps:

- Theme colours and the theme's face only, so it matches every video of the brand. No hard-coded colours or fonts.
- Real content: text, stills and footage from the user's library. Never placeholder bars or empty fields.
- One long smooth ease-out with a little blur and rise in; no spring, no overshoot; out in 0.3 s or less.
- Type at least 52 px for body text, 36 px for labels (at 1080 wide: multiply by `layout.k`).
- Out of the platform's UI (`layout.safe`) and off the speaker's face.
- Files a graphic shows (an image, a logo): put an absolute path in its `props` and `render.py` links the file into the public folder and replaces the path with its public name, which you give to `staticFile` (or `res`).

Two examples ship: `LowerThird` (`{name, role?}`: a name on an accent chip with the role beside it, once) and `TitleCard` (`{text, place?}`: one line of big type on a card, above the head or, when a close-up leaves no room, below the chin).
