# DNA: one place for who the user is

The DNA is the one place the user's identity lives: colors, fonts, logo, caption look, hook, formats, motion, music and sound rules, graphics wishlist and folders. The setup wizard writes it and every other module reads it. The few sections that fill in later (`studies`, `pacing`, `graphics`) are written with `save`, which refuses a file that is not well formed. It is two files in the user's `Brand/` folder, plus the files they point at.

| In `Brand/` | What it holds |
|---|---|
| `dna.json` | The values: colors, fonts, logo, caption look, hook, formats, motion, b-roll, music and sound rules, graphics wishlist, output, delivery, storage. |
| `DNA.md` | The judgment, in ten short sections of plain words: who is on camera, the voice, the look, how much motion, captions, b-roll, music and sound, formats, never, where things are. |
| `assets/` | The files `dna.json` points at: `fonts/`, `logos/`, `luts/`. Every path in `fonts`, `logo` and `grade` is relative to `assets/`. |
| `styles/` | Caption styles, one JSON file each. `init` copies the six starters here. |
| `music/` | The music library (`music.library`, relative to `Brand/`). |

`engine/dna.py` is the only reader. It carries no user data: the neutral DNA is `templates/dna.json`, which is also where every omitted optional key gets its value (there is no separate defaults file, so there is one source of truth). The rest of `templates/` is `schema.json`, `DNA.md` (the judgment template) and `styles/` (the starters).

## The rules of the file

- **Closed objects.** A key the schema does not list fails the check: `captions.zoen: not a known field`.
- **TODO fails.** Any string in `dna.json` that contains `TODO`, and any line of `DNA.md` that does, is a problem. `init` writes `TODO` where it could not find an answer (a font, a line of `DNA.md`). `dna.py todo` prints just those, one per line: the wizard's list.
- **Required keys are never filled in.** `id`, `name`, `colors` (all seven), `fonts.display` (`family` and `file`), `output` (`aspect` and `size`). A file that leaves one out is refused by name. Everything else is optional and comes from the template when left out.
- **Types are exact.** An integer field takes an integer: `3.0` fails. A color is `#RRGGBB`. An enum is one of its words.
- **The hook never sits in the top 220 px.** `hook.top_px` is at least 220, because the platform's own header covers that band of a 1080 x 1920 frame.
- **Fields that depend on each other are checked together:** `formats.home` is in `formats.list`, `hook.hold_s` and `broll` holds run short to long, `output.size` has the shape of `output.aspect`, `hook.font` "running" needs `fonts.running`, and ids in `graphics` and `studies` are unique.
- **Files are checked on disk** (`check`, not `show`): every font, logo and LUT exists, a font starts like a font (TTF, OTF, WOFF or WOFF2), a logo is an `.svg` or `.png`, `captions.default_style` names a style file, every file in `Brand/styles/` reads, and `DNA.md` exists with no `TODO` line.

## Using it

```
python3 engine/dna.py check [folder]      one plain line per problem and exit 2, or "ok <id>"   (no folder: the workspace's Brand/)
python3 engine/dna.py show|theme|styles|todo [folder]      captions [folder] [--style id] [--prefix p]
python3 engine/dna.py scan <folder>       what a brand folder proposes; read only
python3 engine/dna.py init <folder> [--name "<Name>"] [--from <folder>] [--accent #hex] [--ink #hex] [--font <file>] [--emblem <file>] [--lockup <file>] [--id x] [--handle @x] [--site url] [--caps]
```

`show`, `theme` and `captions` print what the engines would read (they check the schema, not the files; `check` is the gate). `theme` and `captions` print absolute asset paths; `--prefix brand-<id>` puts that in front of each instead (the renderer's public-folder form).

In code, from a module in `engine/`: `import dna`.

| Call | Gives |
|---|---|
| `load(path=None)` | `dna.json` as a dict with the omitted optional keys filled in (a person's `role` is `""` and `name_tag` is `true` when left out). `path` is a folder or its `dna.json`; `None` is the workspace's `Brand/`. Raises `DnaError` for a missing file, bad JSON or a missing required key. |
| `load_or_exit(path=None, strict=True)` | `load` and `validate`; one plain line on stderr and exit 2 on any problem. Engines call this. `strict=False` skips files, styles and `DNA.md`. |
| `brand_folder(path=None)` | The folder `dna.json` is in. |
| `validate(dna, brand_dir, strict=True)` | The list of problems, `[]` when clean. |
| `save(dna, brand_dir)` | Writes `dna.json` back, atomically. Refuses a DNA that is not well formed; a `TODO` still in it is allowed. |
| `todo_lines(brand_dir)` | The `TODO` items of `dna.json` then of `DNA.md`. |
| `captions_profile(dna, brand_dir, prefix=None)` | The flat dict the caption chain reads (below). |
| `theme(dna, brand_dir, prefix=None)` | The flat dict the Edit renderer takes as its `theme` prop (below). |
| `style(dna, style_id, brand_dir, prefix=None)` | `captions_profile` with a style laid over it. `styles_list(brand_dir)` lists the styles. |
| `resolve(dna, rel, brand_dir)` | The absolute path of a file under `assets/`; `rel` `""` is the folder. |
| `public_prefix(dna)` | `brand-<id>`: the folder name the renderer's public folder uses for the brand's files. |
| `folders(dna)` | `{name: Path}` for the workspace folders; `Archive` is `storage.archive` when set. |
| `music_dir(dna, brand_dir)`, `grade_profile(dna, brand_dir)` | The music folder; `{enabled, lut}` with `lut` absolute or `None`. |
| `font_is_variable(dna, brand_dir, role="display")` | True when the face in that slot (`display`, `running` or `mono`) is one variable file (it has an `fvar` table). |
| `scan(folder)`, `palette_from_image(png)`, `init(brand_dir, name=None, **proposal)` | The wizard's tools (below). `scan` returns `(proposal, notes)`; `init` takes the proposal as keywords. |

## The two views

Both are flat dicts of plain data. Every asset path is absolute, unless a `prefix` is given (the renderer's public folder, normally `public_prefix(dna)`): then it is `prefix/fonts/Name.woff2`. The render step copies the files a theme names into its own public folder, so it wants the absolute form; `font_abs` is always absolute.

### `captions_profile`

| Key | Comes from |
|---|---|
| `id`, `brand`, `handle` | `id`, `name`, `handle` |
| `accent`, `base`, `on_accent` | `colors.accent`, `colors.base`, `colors.on_accent` |
| `font`, `font_prefix` | The display family as written; the family's letters and digits (a letter first) |
| `font_family` | `font_prefix` plus `Dyn`: the CSS name the engines register |
| `fonts_dir` | The folder of `fonts.display.file`, behind the prefix |
| `font_files` | The file for each of `"400"`, `"500"`, `"700"`, `"800"`, `"900"`. One variable or single-weight file answers all five. |
| `font_faces` | `[{weight, src}]`, one entry per distinct file (`"100 900"` for a variable or single file) |
| `font_abs` | The same files as absolute paths, for measuring text |
| `word_font` | `fonts.running` as `{fonts_dir, prefix, family, files, faces, abs}`, or `None` |
| `case` `zone` `width` `top_y0` `hero_max` `row_px` `chin_gap` `chip` `hero_base` `hero_shadow` `hero_stroke` `text_opacity` `text_soften` `whip_frames` `whip_amount` `caption_tier_max` `soft_build` `default_style` `pace_gate` `hero_glow` `hero_tint` `glow_px` `chip_neg` `neg_words` `count_up` `places` `cta_wash` `page_bg` `page_ink` `display_weight` `pill_bg` `pill_ink` `icon_words` `pill_icons` `feel` `look_note` `text_hook` `devices` `master_audio` | Every field of the `captions` section, under its own name. `chip_neg` is `colors.negative` when it is null. `text_hook` is `"off"` whenever `hook.on` is false. |
| `spelling`, `do_not_say` | `words.spelling`, `words.banned` |
| `auto_punches` | `motion.auto_punches` |
| `pace_gate` | `captions.pace_gate` (one of the captions fields above, named here because the caption chain reads it) |
| `dna_file`, `font_fallback`, `font_variable`, `display_font` | Extras: the absolute path of `dna.json`; `fonts.display.fallback`; whether the display file is variable; `{fonts_dir, prefix, family, faces}` again for page and pill text |

`style()` adds two more keys: `style` (the id) and `feed` (the style's feed block, `{}` when it has none).

### `theme`

| Key | Comes from |
|---|---|
| `id`, `name` | `id`, `name` |
| `colors` | `{accent, ink, base, on_accent, negative, bg, card}` from `colors` |
| `surface` | `{background, border}` from `surface` |
| `font_family`, `font_faces` | As in the profile, for the display face |
| `caps`, `skew` | `type.caps`, `type.skew` |
| `cap_words`, `caption_size`, `caption_top`, `caption_stroke` | `feed_captions.words_per_page`, `.size_px`, `.top_px`, `.stroke_px` |
| `hook_look`, `hook_top`, `hook_size`, `hook_font` | `hook.look`, `.top_px`, `.size_px`, `.font` (`"display"` or `"running"`) |
| `lockup_src`, `lockup_dark_src`, `emblem_src` | `logo.lockup`, `.lockup_dark`, `.emblem` (absolute, or behind the prefix), or `None` when unset |
| `lockup_on` | True when any of the three logos is set |
| `broll_style`, `smooth_motion`, `music_level` | `broll.style`, `motion.smooth`, `music.alone_lufs` |
| `end_card` | `{on, line, handle}` from `delivery.end_card`, with `handle` filled from the brand's handle when empty |
| `font_fallback`, `hook_on`, `running_family`, `running_faces`, `mono_family`, `mono_faces` | Extras: `fonts.display.fallback`; `hook.on`; the running and mono faces as a safe family name (letters and digits plus `Dyn`) and a `[{weight, src}]` list, `None` and `[]` when the DNA has none. `hook_font` `"running"` and the mono hook look draw in them. |

## Caption styles

A style is one small JSON file: `Brand/styles/<id>.json` first, then the starters in `templates/styles/`. The id is the file name, in lower case words joined by hyphens (`keyword-slam`); an id spelled in another case is refused even on a disk that ignores case. Required: `id`, `name`, `summary`, `use_when`, `never_when` (the last three are the menu text, one line each) and `set`. Also allowed: `preview` (an image inside the styles folder, or null), `source` (an object or null), `opt_in`, `owner` (an id: the style is private to that DNA), `added` (a date), `added_for` (an id or null), `status` (`built`, or `to build` for a style that waits on the engine and is in no menu), `look` and `feed`.

```json
{"id": "quiet", "name": "Quiet", "summary": "...", "use_when": "...", "never_when": "...", "preview": null,
  "source": {"type": "starter"}, "opt_in": false, "owner": null,
  "set":  {"zone": "chest", "hero_glow": false, "row_px": [84, 100]},
  "look": {"accent": "#FF8800", "font": "running"},
  "feed": {"place": "lower", "size": 0.85, "hero": {"look": "color"}, "motion": "quiet"}}
```

- **`set`** lays fields over the `captions` section for one video. Only these may be set: layout (`case`, `zone`, `width`, `top_y0`, `hero_max`, `row_px`, `chin_gap`), treatment (`chip`, `text_opacity`, `text_soften`, `hero_glow`, `glow_px`), motion (`whip_frames`, `whip_amount`, `count_up`), the tier (`caption_tier_max`) and `soft_build`. Each takes the type and range of the captions field. A color, the display weight or a font key in `set` is refused with a pointer to `look`; a word list or any other field is refused as the brand's. The tier only ever goes down: a cap the brand set lower stays.
- **`look`** is optional, and every key in it is optional (a missing key is the brand's value; `null` is not a value). It lays color and font choices over the profile: `accent`, `text` (the profile's `base`), `on_accent`, `hero_base`, `hero_tint`, `hero_shadow`, `hero_stroke` (`{width, color}`), `chip_neg`, `display_weight`, and `font`. A key not on that list is refused. `font: "running"` swaps the font slot: the font keys (`font`, `font_family`, `font_prefix`, `fonts_dir`, `font_files`, `font_faces`, `font_abs`, `font_fallback`, `font_variable`) read the running face, so the words draw in it; `display_font` (page and pill text) stays the display face and `word_font` stays the running face. It needs `fonts.running`. Starter styles ship with no `look`.
- **`feed`** is for the renderer's simple caption pages: `place` (`chest`, `lower`, `top`), `size` (0.7 to 1.5, a scale of `feed_captions.size_px`), `hero` (`look` `color`, `bigger` or `bigger_bold`; `scale` 1.3 to 2.8; `pick` `accent` or `page`), `words` (1 to 3, never more than `feed_captions.words_per_page`) and `motion` (`reveal`, `quiet`, `soft`). `style()` hands it back as `feed`.

A style whose `owner` is another DNA's id is refused. After a style is laid over a profile, every key outside `set` and `look` is checked to equal the brand's own profile, so a style cannot move the brand's identity. When nobody picks a style, the caller lays `captions.default_style` itself, through `style()`; `captions_profile` on its own is the brand's own preset. `check` reads every file in `Brand/styles/` and names a broken one (`styles/quiet.json: set.hero_base: ...`).

## Gates and automatic motion

- **`captions.pace_gate`** is the pacing check on a finished cut. `report` (the default) runs it and writes the numbers in the notes without blocking anything. `enforce` holds back a cut that fails. `off` skips it. The profile carries it as `pace_gate`.
- **`motion.auto_punches`** lets the editor add automatic 1.12x punch-ins to fill long still stretches. It is `false` unless the user wants it. The profile carries it as `auto_punches`. (`motion.punches` is separate: it allows the hard punch-in at a sentence change.)

## The wizard loop

1. **Look.** `python3 engine/dna.py scan <their brand folder>` shows what a folder proposes: the accent, ink and base from the hex colors in its `.html`, `.css`, `.md`, `.json` and `.txt` files (or from a logo `.png`, when Pillow is installed and no color is written anywhere), the best-weight font, the logos, and the color for text on the accent (white on a deep accent, else the ink when it reads at 4.5:1, else black). The folder is never written to.
2. **Start.** `python3 engine/dna.py init Brand --name "Acme Roofing" [--from <folder>]` writes `dna.json`, copies the fonts and logos into `assets/`, copies the starter styles into `styles/`, writes `DNA.md`, runs the check and prints what is still to do. With no font found it writes `TODO` in the font fields. It never overwrites a `DNA.md` or a style that is already there, and a failed step removes what the call made.
   **Add the look later.** Run `init` again on the same folder with `--accent`, `--ink`, `--font`, `--emblem`, `--lockup`, `--handle`, `--site` or `--caps`: it changes only what it is given. A new ink also works out `colors.bg`, `colors.card` and `surface.background` again, a new ink or accent works out `colors.on_accent` again, and `people`, the styles and `DNA.md` are left alone. With no option it changes nothing and prints what is still to do. The id never changes.
3. **Fill.** `python3 engine/dna.py todo Brand` lists the open items, one per line. Ask for each, write the answer into `dna.json` (`dna.load`, change the dict, `dna.save`) or replace the `TODO` line of `DNA.md` with a few plain sentences.
4. **Check.** `python3 engine/dna.py check Brand`. Repeat 3 and 4 until it prints `ok <id>`.

| The wizard asks about | Lands in |
|---|---|
| name, handle, who is on camera | `name`, `handle`, `people`, `DNA.md` |
| their brand folder | `scan`, then `init` |
| colors (contrast-checked text on the accent) | `colors`, `surface` |
| font and capitals | `fonts`, `type` |
| logo files | `logo` |
| caption look | `captions`, `default_style`, `styles/` |
| hook look and position | `hook` (and `feed_captions`) |
| usual kinds of video | `formats` |
| how much motion, the graphics wishlist | `motion`, `graphics.wishlist` |
| b-roll sources | `broll` |
| music and sound rules | `music`, `sound`, `words` |
| output shape, folders | `output`, `delivery`, `storage`, `cleanup` |
| reference videos | `studies`, `pacing`, `graphics.wishlist` |

## Fields

Read by: **captions** the caption chain (`captions_profile`), **render** the Remotion project (`theme`), **edit** the cutter and edit planner, **sound** the audio mix, **footage** intake and b-roll, **review** the review page, **study** the reference study, **finish** delivery and cleanup, **setup** the wizard (notes), **check** `dna.py` itself.

### Top level

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `version` | integer, always 1 | `1` | check | Version of this file's shape. Always 1. |
| `id` | id | `required` | all | Short name for the brand: lowercase letters, digits and dashes. It names the renderer's public folder, `brand-<id>`. |
| `name` | string | `required` | captions, render, footage | How the brand is written in captions, notes and the pages the footage module builds. |
| `handle` | @name or empty | `""` | captions, render | The brand's @name. Empty is allowed. The end card uses it until it has its own. |
| `site` | string | `""` | setup | The brand's web address. Notes only. |

### people

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `people` | list of objects | `[]` | edit | Everyone who appears on camera. A person's name tag shows once after the hook. |
| `people[].name` | string | `required` | edit | The person's name, spelled the way it goes on screen. |
| `people[].role` | string | `empty when left out` | edit | Their role, shown under the name. Optional. |
| `people[].name_tag` | boolean | `true when left out` | edit | Show the name tag for this person. Optional, true when left out. |

### colors

All seven are required.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `colors.ink` | hex color | `required` | render, footage | The brand's dark color: dark text, and the floor of a dark brand. |
| `colors.base` | hex color | `required` | captions, render | The brand's light color: every caption glyph that is not the accent. |
| `colors.accent` | hex color | `required` | captions, render, footage | The one accent: the hero word, chips, the glow, bars. |
| `colors.on_accent` | hex color | `required` | captions, render | The color of text that sits on the accent. |
| `colors.negative` | hex color | `required` | captions, render | The bad-news color: the box on a negative word, the strike line. |
| `colors.bg` | hex color | `required` | render | The floor color behind cards and pages. |
| `colors.card` | hex color | `required` | render | The card color on that floor. |

### surface

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `surface.background` | string | `"rgba(28,31,38,0.94)"` | render | The card fill over footage, as a CSS color. It may be see-through. |
| `surface.border` | string | `"1px solid rgba(255,255,255,0.08)"` | render | The card border, as a CSS border value. |

### fonts

Paths are relative to Brand/assets/.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `fonts.display.family` | string | `required` | captions, render, footage | The display family's name as written. Engines load it under a safe name made from it: its letters and digits plus `Dyn`. |
| `fonts.display.file` | path under assets/ | `required` | captions, render | The display font file under assets/. A variable or single-weight file serves every weight. |
| `fonts.display.weights` | object: weight 100 to 900, file | `{}` | captions, render | Only for a family shipped as one file per weight: weight (100 to 900) to file. |
| `fonts.display.fallback` | string | `"sans-serif"` | render, footage | A CSS fallback family for places that cannot load the file. |
| `fonts.running` | object like fonts.display, or null | `null` | captions, render | A second face with the same shape as fonts.display, or null. The caption chain draws the non-hero rows in it; hook.font can pick it. |
| `fonts.mono` | object like fonts.display, or null | `null` | render | A mono face with the same shape as fonts.display, or null. The mono hook look draws in it when there is one. |

### type

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `type.caps` | boolean | `false` | render | Headlines and captions in capitals. |
| `type.skew` | number -30 to 30 | `0` | render | Degrees the accent chip leans. |

### logo

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `logo.emblem` | path under assets/ or null | `null` | render | The mark, an .svg or .png under assets/, or null. |
| `logo.lockup` | path under assets/ or null | `null` | render | The full logo for light footage, or null. |
| `logo.lockup_dark` | path under assets/ or null | `null` | render | The full logo for dark footage, or null. |

### captions

Every field of the caption planner's preset, under the same names. A style can set some of them for one video; see Caption styles.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `captions.case` | one of upper, mixed, title | `"mixed"` | captions | The one case every caption renders in: upper, mixed (as spoken) or title (first letter only). |
| `captions.zone` | one of top, chest | `"chest"` | captions | Where captions sit by default: top (behind the head, big heroes) or chest (in front, below the chin). |
| `captions.width` | integer 300 to 1080 | `860` | captions | The widest a caption block may be, in px of a 1080 wide frame. The planner also holds every row inside the safe box. |
| `captions.top_y0` | integer 0 to 1920 | `200` | captions | Top edge of the top zone, in px. |
| `captions.hero_max` | integer 50 to 800 | `250` | captions | The largest hero glyph size, in px. |
| `captions.row_px` | 2 integers, 20 to 400 | `[112, 132]` | captions | Smallest and largest row height of a cluster's non-hero rows, in px. |
| `captions.chin_gap` | integer 0 to 600 | `90` | captions | Clearance kept below the chin before chest text starts, in px. |
| `captions.chip` | boolean | `false` | captions | Draw the accent as a skewed solid chip with base-colored text, instead of accent-colored text. |
| `captions.hero_base` | hex color or null | `null` | captions | Color of hero glyphs when it must differ from the running words. Null: the base color. |
| `captions.hero_shadow` | string | `"0 3px 18px rgba(0,0,0,0.45), 0 0 2px rgba(0,0,0,0.5)"` | captions | Text shadow on hero and lockup glyphs, as a CSS value. |
| `captions.hero_stroke` | {width 0 to 40, color hex}, or null | `null` | captions | Outline on hero glyphs, {width, color}, or null for none. |
| `captions.text_opacity` | number 0 to 1 | `1.0` | captions | Opacity of every text mode. Chips stay solid. |
| `captions.text_soften` | number 0 to 20 | `0` | captions | Edge blur on text, in px. |
| `captions.whip_frames` | integer 0 to 30 | `3` | captions | Length in frames of the whip cuts between clusters. 0 turns them off. |
| `captions.whip_amount` | number 0 to 1 | `1.0` | captions | Strength of the whip cuts, 0 to 1. |
| `captions.caption_tier_max` | one of MINIMAL, REDUCED, FULL | `"FULL"` | captions | Caps how loud the room check lets captions get. A style can lower it, never raise it. |
| `captions.soft_build` | boolean | `false` | captions | Draw captions as the soft build instead of clusters. The soft-build style sets it. |
| `captions.default_style` | style id, or null | `"clean-clusters"` | captions, setup | The style a video gets when nobody picks one: an id from Brand/styles/ or the starters. Null: the brand's own preset. |
| `captions.pace_gate` | one of off, report, enforce | `"report"` | captions | The pacing check on a finished cut. off: not run. report: run, and write the numbers in the notes without blocking (the default). enforce: a cut that fails is held back. |
| `captions.devices` | one of on, off | `"off"` | captions | Whether the caption chain proposes layout events (a call-to-action wash, a page, a frame of pills) for the person to accept at the gate. Off by default: captions only. |
| `captions.master_audio` | one of off, loudnorm | `"off"` | captions | off: the caption chain leaves the cut's mix as it came and only reports its loudness. loudnorm: it normalizes the mix to -16 LUFS, true peak -2 dBTP, and the loudness gate fails outside -16 +/- 1 LUFS. |
| `captions.hero_glow` | boolean | `true` | captions | Master switch for the glow on accent-colored hero rows. |
| `captions.hero_tint` | hex color or null | `null` | captions | Near color of the glow gradient. Null: the accent mixed 55% toward white. |
| `captions.glow_px` | integer 0 to 80 | `12` | captions | Near radius of the glow, in px. |
| `captions.chip_neg` | hex color or null | `null` | captions | Color of the box on a negative word and of the strike line. Null: colors.negative. |
| `captions.neg_words` | list of strings | `[]` | captions | Negative words for this brand, added to the planner's built-in set. |
| `captions.count_up` | boolean | `true` | captions | Numerals from 10 to 99999 (never a bare year) count up when they land. |
| `captions.places` | list of strings | `[]` | captions | Place names (several words allowed) that get the pin and the letter-spaced hero. Empty: none ever. |
| `captions.cta_wash` | hex color or null | `null` | captions | Wash color behind a call-to-action event. Null: the accent. |
| `captions.page_bg` | hex color or null | `null` | captions | Fill of a page event. Null: the accent. |
| `captions.page_ink` | hex color or null | `null` | captions | Text color on a page event. Null: black or the base color, by the fill's brightness. |
| `captions.display_weight` | integer 100 to 900 | `800` | captions | Weight of page-event text, 100 to 900. |
| `captions.pill_bg` | hex color or null | `null` | captions | Fill of a frame event's pills. Null: the accent. |
| `captions.pill_ink` | hex color or null | `null` | captions | Icon and text color on the pills. Null: by the fill's brightness. |
| `captions.icon_words` | list of strings | `[]` | captions | word:icon pairs that put a line icon before a hero row. Added to the planner's own neutral set. |
| `captions.pill_icons` | list of strings | `[]` | captions | Ordered rows word,word:icon for the icons on frame pills. The first row with a word in the pill's text wins. |
| `captions.feel` | string | `""` | setup | Notes only: the brand's voice in a line. |
| `captions.look_note` | string | `""` | setup | Notes only: a decision about the look. |
| `captions.text_hook` | one of on, off | `"on"` | captions | on or off: whether the caption chain plans the text hook. Off whenever hook.on is false. |

### feed_captions

The renderer's simple caption pages. A style's feed block adjusts them for one video.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `feed_captions.words_per_page` | integer 1 to 3 | `3` | render | Words on a caption page, 1 to 3 (the renderer shows at most 3), in its simple caption pages. |
| `feed_captions.size_px` | integer 40 to 140 | `84` | render | Caption size in px of a 1080 wide frame. |
| `feed_captions.stroke_px` | number 0 to 20 | `0` | render | Dark outline under caption words, in px. |
| `feed_captions.top_px` | integer 0 to 1920 | `1190` | render | Top edge of the caption pages, in px from the top of a 1920 tall frame. |

### hook

One hook look in one place on every video.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `hook.on` | boolean | `true` | render, edit | Put a text hook on videos at all. |
| `hook.look` | one of card, accent, ink, bigtype, bar, mono | `"card"` | render | How the hook is drawn: card, accent, ink, bigtype, bar, or mono (big type whose key words flip to the negative color while the picture drops to black and white, in the mono face when there is one). One look in one place on every video. |
| `hook.top_px` | integer 220 to 1600 | `300` | render, edit | Where the hook sits, in px from the top of the frame. At least 220, because the platform's own header covers the top 220 px. |
| `hook.size_px` | integer 40 to 120 or null | `null` | render | Hook text size in px. Null: sized to fit. |
| `hook.font` | one of display, running | `"display"` | render | The face the hook card draws in: display, or running (needs fonts.running). |
| `hook.hold_s` | 2 numbers, 1 to 15 | `[3.0, 6.5]` | edit | Shortest and longest seconds the hook stays on screen. |

### formats

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `formats.home` | id | `"talking-head"` | edit, setup | The usual kind of video. It must be in formats.list. |
| `formats.list` | list of ids | `["talking-head"]` | edit, setup | The kinds of video in use, as ids. |

### motion

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `motion.level` | one of face, light, full | `"light"` | edit | How much of the screen a video may change. face: overlays only, the speaker stays full screen. light: a few short full-screen moments, each on a line that points at something. full: footage leads, with full-screen scenes as the format needs, capped and spaced. |
| `motion.transitions_allowed` | list of strings | `["hard-cut", "punch-in", "zoom-through"]` | edit | Transitions the editor may use. |
| `motion.transitions_banned` | list of strings | `["spin", "glitch", "3d", "shutter"]` | edit | Transitions the editor never uses. |
| `motion.punches` | boolean | `false` | edit | Allow a hard punch-in of about 1.4x at a sentence change. |
| `motion.auto_punches` | boolean | `false` | edit | Let the editor add automatic 1.12x punch-ins to fill long still stretches. Off unless wanted. |
| `motion.smooth` | boolean | `true` | render, edit | Cards, overlays and transitions arrive on one smooth ease, never a bounce. |
| `motion.energy` | integer 1 to 5 | `3` | edit | The overall pace of cuts and motion, 1 (calm) to 5 (fast). |

### graphics

The wishlist is what the user wants built. Built is what exists.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `graphics.wishlist` | list of objects | `[]` | study, setup | The graphics the user wants built, from the studies and the wizard. |
| `graphics.wishlist[].id` | id | `required` | setup | Short id for the graphic. |
| `graphics.wishlist[].name` | string | `required` | setup | Its name. |
| `graphics.wishlist[].what` | string | `optional` | setup | What it does, in a line. |
| `graphics.wishlist[].priority` | one of must, later | `required` | setup | must: build it first. later: build it when the musts are done. |
| `graphics.wishlist[].from_study` | string or null | `optional` | study | The study it came from, or null. |
| `graphics.built` | list of objects | `[]` | render, edit | The graphics that exist as renderer components. |
| `graphics.built[].id` | id | `required` | edit | The id the edit plan uses to ask for it. |
| `graphics.built[].component` | string | `required` | render | The renderer component that draws it. |
| `graphics.built[].since` | date YYYY-MM-DD | `optional` | setup | The date it was built, YYYY-MM-DD. Optional. |

### broll

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `broll.style` | one of cut, dynamic | `"cut"` | render, edit | How b-roll enters and leaves. cut: a clean short dissolve. dynamic: whips and zoom-throughs. |
| `broll.hold_min_s` | number 0.3 to 30 | `1.5` | edit | Shortest a b-roll shot holds, in seconds. |
| `broll.hold_max_s` | number 0.3 to 30 | `4.0` | edit | Longest a b-roll shot holds, in seconds. |
| `broll.own_footage_floor` | number 0 to 1 | `0.6` | edit, footage | The share of b-roll that must be the user's own footage, 0 to 1. |
| `broll.ai` | one of objects-and-places, off | `"off"` | edit | objects-and-places: generated clips are allowed, of objects and places only. off: none. |

### music

The levels are measured on the finished video, not guessed.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `music.on_talking_head` | boolean | `true` | sound, edit | Put music under a talking head. |
| `music.on_montage` | boolean | `true` | sound, edit | Put music under a montage. |
| `music.feel` | string | `""` | sound, setup | What the music should feel like, in a line. |
| `music.alone_lufs` | number -40 to -6 | `-30` | sound | Loudness of the bed where music plays alone (an open before the first word, a long pause, the end card), in LUFS. |
| `music.under_speech_db` | number 0 to 60 | `22` | sound | How far under the speaking voice the bed sits while someone talks, in dB. The voice always leads. |
| `music.alone_db` | number 0 to 60 | `14` | sound | How far under the voice the bed sits where it plays alone (open, end card), in dB. |
| `music.ease_s` | number 0 to 5 | `1.5` | sound | Seconds the bed takes to ease down before a voice. |
| `music.library` | path under Brand/ | `"music"` | sound | The folder of tracks, relative to Brand/. |
| `music.repeat_gap_videos` | integer 0 to 50 | `6` | sound | A track does not come back sooner than this many videos later. |
| `music.banned_md5` | list of md5 strings | `[]` | sound | md5 of tracks that are never used. A renamed copy is refused too. |

### sound

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `sound.sfx` | one of clicks, off | `"clicks"` | sound | clicks: soft clicks and ticks that land with a graphic. off: no sound effects. |
| `sound.sfx_per_min` | integer 0 to 60 | `12` | sound | Most sound effects in a minute. |
| `sound.sfx_under_speech_db` | number 0 to 40 | `14` | sound | How far under the speaking level an effect sits, in dB. |
| `sound.voice_isolation_pct` | integer 0 to 100 | `0` | sound | Percent of voice isolation applied to the voice, 0 (off) to 100. |
| `sound.master_lufs` | number -30 to -6 | `-16` | sound | Loudness of the final mix, in LUFS. |
| `sound.true_peak_dbtp` | number -9 to 0 | `-2.0` | sound | Highest true peak of the final mix, in dBTP. |
| `sound.profanity` | one of spoken, bleep, cut | `"spoken"` | sound, captions | spoken: left as said. bleep: bleeped. cut: cut out. |

### words

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `words.spelling` | object: heard, written | `{}` | captions, edit | Fixes for names the transcriber gets wrong: heard (lowercase, several words allowed) to written. |
| `words.banned` | list of strings | `[]` | captions | Words and phrases no caption may show. |

### transcribe

| field | type | default | read by | what it is |
|---|---|---|---|---|
| `transcribe.engine` | one of local, deepgram | `"local"` | captions, edit | Which transcriber times the words: local (whisper.cpp and WhisperX on this Mac, no key) or deepgram (needs DEEPGRAM_API_KEY in ~/.cutroom/keys.env; falls back to local without it). |
| `transcribe.language` | language code, auto, or null | `null` | captions, edit | The spoken language for the transcriber (en, es, ...), auto to let it decide, or null for config.json's whisper_language. |

### grade

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `grade.enabled` | boolean | `false` | footage, edit | Apply the brand's color grade. |
| `grade.lut` | path under assets/ or null | `null` | footage, edit | A LUT file under assets/, or null for balance only. |

### output

aspect and size are required.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `output.aspect` | one of 9:16, 4:5, 1:1, 16:9 | `required` | edit, render | The main shape: 9:16, 4:5, 1:1 or 16:9. |
| `output.size` | 2 integers, 240 to 4320 | `required` | edit, render | Width and height in px. It must have the shape of output.aspect. |
| `output.fps` | integer 12 to 120 | `30` | edit, render | Frames a second. |
| `output.crf` | integer 8 to 28 | `17` | edit | Encoder quality: lower is cleaner and larger. |
| `output.also` | list of strings | `[]` | edit | Other shapes to deliver as well. |

### delivery

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `delivery.end_card.on` | boolean | `false` | render | Close videos with an end card. |
| `delivery.end_card.line` | string | `""` | render | The one line on the end card. |
| `delivery.end_card.handle` | @name or empty | `""` | render | The @name on the end card. Empty: the brand's handle. |
| `delivery.transcript` | boolean | `true` | finish | Write a transcript beside each finished video. |
| `delivery.caption_file` | boolean | `true` | finish | Write the post caption as a text file beside each finished video. |

### review

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `review.keep_cut_on_intake` | boolean | `true` | review | Show new clips on the review page for a keep or cut when they come in. |
| `review.approve_before_render` | boolean | `true` | review, edit | Ask for a yes on the plan before rendering. |

### cleanup

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `cleanup.after_delivery` | one of ask, auto | `"ask"` | finish | ask: ask before deleting a project's intermediates. auto: delete them. |
| `cleanup.keep` | list of ids | `["plan", "words", "transcript"]` | finish | Names (lowercase words) of what cleanup leaves in place. |

### storage

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `storage.archive` | string | `""` | finish | Where archived shoots go. Empty until set. ~ is allowed. |
| `storage.warn_free_gb` | number 0 to 100000 | `50` | footage, finish | Warn when free space falls under this many GB. |

### pacing

Filled from studies; null until then.

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `pacing.cuts_per_min` | number 0 to 300 or null | `null` | edit, study | Cuts a minute. Null until a study fills it. |
| `pacing.median_shot_s` | number 0 to 120 or null | `null` | edit, study | The median shot length in seconds. Null until a study fills it. |
| `pacing.text_on_screen_pct` | number 0 to 100 or null | `null` | edit, study | The share of seconds with text on screen, 0 to 100. Null until a study fills it. |

### studies

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `studies` | list of objects | `[]` | study | The reference videos the user studied. |
| `studies[].slug` | id | `required` | study | Short id of the study. |
| `studies[].url` | string | `optional` | study | Where the video lives. |
| `studies[].date` | date YYYY-MM-DD | `optional` | study | When it was studied, YYYY-MM-DD. |
| `studies[].liked` | string | `optional` | study | What the user liked, in their words. |
| `studies[].adopted` | list of strings | `optional` | study | What was adopted from it: a style id, a graphic id, a pacing number. |

### notes

| Field | Type | Default | Read by | What it does |
|---|---|---|---|---|
| `notes` | list of strings | `[]` | setup | Free notes: decisions, colors found and not used. |

