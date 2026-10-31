/**
 * CaptionsDynamic.tsx — the dynamic caption composition: cluster captions and the soft build.
 * One font family (the plan's `font`), a base color + one accent, content decides the mode.
 *   behind  — huge word(s) above/behind the head; the person matte (RGBA webp sequence) sits over them.
 *   front   — a stacked lockup in front (the payoff), word by word, last line in the accent.
 *             A front event with `soft` is a soft build thought instead, drawn by SoftThought (softthought.tsx, by its own rules).
 * Layout events (README, Devices) take the frame for a beat instead:
 *   cta     — wash + a two-row call to action + pulsing chevrons (the video stays under it).
 *   page    — a full-frame solid color page with one display word sliding in.
 *   frame   — the video shrinks into a rounded card, spoken-list pills drop in above it.
 *   igpost  — the video inside a mock post card, slow zoom-out, optional mosaic.
 * Layers: video → behind → matte → front → cta → page/frame/igpost.
 * Footage moves (README, Pacing): `punches` scale the full-frame clip AND the matte
 * about a fixed origin (snap in, hold, ease back to 1); `whips` blur + push the clip on top of that.
 * Row marks (§2): a behind/front row may lead with ONE of pin / logo / icon, dropping in on the
 * pin's 4-frame curve in the row's own color.
 *
 * props: {
 *   src, font, durationInFrames, accent?,
 *   matte?: {dir, windows:[[startFrame,endFrame],...]}   // files `${dir}/f${frame padded 5}.webp`
 *   punches?: [{at, scale?, frames?, hold?, cx?, cy?}]   // §1, `at` in FRAMES like whips
 *   events: [
 *     {mode:"behind", words:[{text,at,size}], start, end, y, stack:"column"|"row"},
 *     {mode:"front",  words:[{text,at,size,accent,pin|logo|icon}], start, end, y},
 *     {mode:"front", soft:true, words:[{text,at,ats,role,size,weight,dx,bar,devices}], start, end, exitAt, y, shadow?},  // with the plan's `soft` colors
 *     {mode:"cta", rows, start, end, chevrons?, wash?, y?},
 *     {mode:"page", text, start, end, color?, ink?, font?, size?, weight?, slide?},
 *     {mode:"frame", list, start, end, bg?, card?, pill?},
 *     {mode:"igpost", start, end, pixelate?, scaleFrom?, scaleTo?, rows?}
 *   ]
 * }
 */
import React from "react";
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  cancelRender,
  continueRender,
  delayRender,
  interpolate,
  interpolateColors,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  Easing,
} from "remotion";
import {
  Icon,
  IconName,
  LogoGlyph,
  LogoName,
  PinGlyph,
  RowIcon,
} from "./Icons";
import {
  SNAP,
  SoftColors,
  SoftThought,
  WHITE,
  countText,
} from "./softthought";

// Brand fonts come from the plan, never from this file: `font` (and `wordFont`, `displayFont`) is
// {family, faces: [{src, weight}]}. `src` is a file under the render's public folder (brand-<id>/fonts/...,
// copied there by render.sh from your DNA's font files) and `weight` the CSS weight it covers: "100 900" for a variable or
// single-weight file, "800" or "700 800" for one file per weight. Every declared file is loaded before the first
// frame renders; a file that fails to load stops the render (cancelRender) and names the file, so a missing or
// broken font can never ship as a silent system-font fallback.
type FontFaceSpec = { src: string; weight: string };
type FontSpec = { family: string; faces: FontFaceSpec[] };
const fontStack = (family: string) =>
  `${family}, ui-sans-serif, system-ui, sans-serif`;
const FORMATS: Record<string, string> = {
  woff2: "woff2",
  woff: "woff",
  ttf: "truetype",
  otf: "opentype",
};
// Each font's load, started once and kept by its family and files. loadFonts answers it, so a drawing that measures glyphs in the browser
// (the soft bar) waits for the face to be in document.fonts: it settles once the faces are added, or once the render is canceled.
const loaded: Record<string, Promise<void>> = {};
const fontKey = (font: FontSpec) =>
  `${font.family}|${font.faces.map((f) => `${f.src}@${f.weight}`).join(",")}`;
function loadFonts(font: FontSpec | undefined, what: string): Promise<void> {
  if (!font || !font.family || !font.faces || font.faces.length === 0) {
    cancelRender(
      new Error(
        `${what} has no font: the plan must carry {family, faces:[{src, weight}]} (build it with spec_adapter.py)`,
      ),
    );
    return Promise.resolve();
  }
  const key = fontKey(font);
  if (typeof document === "undefined" || key in loaded)
    return loaded[key] ?? Promise.resolve();
  const h = delayRender(`font ${font.family}`);
  loaded[key] = Promise.all(
    font.faces.map((face) => {
      const ext = (face.src.split(".").pop() ?? "").toLowerCase();
      return new FontFace(
        font.family,
        `url(${staticFile(face.src)}) format('${FORMATS[ext] ?? "truetype"}')`,
        { weight: face.weight },
      )
        .load()
        .catch((err: unknown) => {
          throw new Error(
            `font file ${face.src} (${font.family}, weight ${face.weight}) failed to load: ${String(err)}`,
          );
        });
    }),
  )
    .then((fs) => {
      const set = document.fonts as unknown as { add(f: FontFace): void };
      fs.forEach((f) => set.add(f));
      continueRender(h);
    })
    .catch((err: unknown) => cancelRender(err));
  return loaded[key];
}

type LWord = {
  text: string;
  at: number;
  size?: number;
  accent?: boolean;
  color?: string; // color = explicit fill (a lime hero word that is NOT a chip)
  dx?: number;
  weight?: number;
  hero?: boolean; // the cluster's hero row keeps the display face; the other rows take the brand's running face (wordFont)
  // Word devices (README, Devices) — all optional, all default off, so a plan that
  // sets none of them renders exactly as it did before they existed.
  glow?: boolean; // §1a hero "light refraction": diagonal gradient fill + twin outer glow
  neg?: boolean; // §1b negative word: solid chipNeg box, WHITE text, left→right clip wipe
  count?: number; // §1c numeral counts 0→count, lands in accent, settles to base
  countFrames?: number; // §1c count length (default 8)
  strike?: boolean; // §1d chipNeg line wipes through the row (a rejected idea)
  pin?: boolean; // §1e map-pin glyph before the row (place names)
  spaced?: boolean; // §1f 0.28em tracking + blur reveal, accent→base (place-name hero)
  // Row pops (README, Devices): a mark that drops in before the row's text, in the
  // row's own color, with the pin's entrance. A row carries AT MOST ONE — pin > logo > icon.
  logo?: LogoName; // §2 platform logo, 0.9em (public/logos/<name>.svg, filled)
  icon?: IconName; // §2 line icon, 0.8em (the §2c vocabulary, stroked)
  // A line of a soft build thought (a front event with `soft`, SoftThought):
  ats?: number[]; // the spoken start of each word of `text`, split on single spaces, one to one
  role?: "lead" | "pay" | "tail"; // the lead-in, the payoff or the tail
  bar?: boolean; // the one payoff a reel that opens the bar
}; // cluster rows: horizontal stagger (px) and per-row weight
// Cluster captions: a phrase is ONE tight lockup — rows overlap
// (`leading` < 1), stagger left/right (`dx`), each row blurs in with a small overshoot, the cluster leaves as a
// whole with a scale-away + blur. Plain behind/front lockups keep their old 4-frame pop when `cluster` is unset.
type ClusterOpts = {
  cluster?: boolean;
  leading?: number; // row height as a fraction of font size (default 0.82 = rows overlap)
  tracking?: string; // letter-spacing (default "-0.04em")
  enterFrames?: number; // blur-in length per row (default 5)
  exit?: "fade" | "blur"; // default "blur" for clusters
  exitFrames?: number; // default 5
};
type Ev =
  | ({
      mode: "behind";
      words: LWord[];
      start: number;
      end: number;
      y: number;
      stack?: "column" | "row";
      maxW?: number; // behind text may run wider than the caption safe box (about 1000 px), it sits behind the person
      fade?: number; // seconds of fade before `end` (default 0.2); 0 = leaves on the cut
    } & ClusterOpts)
  | ({
      mode: "front";
      words: LWord[];
      start: number;
      end: number;
      y: number;
      maxW?: number;
      fade?: number;
      soft?: boolean; // a soft build thought: SoftThought draws it, by its own rules
      exitAt?: number; // the soft build: the second the thought starts to leave
    } & ClusterOpts)
  // ---- layout events (README, Devices). `start`/`end` are seconds like every other event; the planner
  // clamps overlapping clusters so these own their beat outright.
  | {
      mode: "cta"; // §2a
      start: number;
      end: number;
      rows: { text: string; size?: number; weight?: number }[];
      chevrons?: boolean; // default true
      wash?: string; // default ctaWash ?? accent
      y?: number; // default 700
      maxW?: number; // width budget for the rows (default 960: a CTA is centerd on the canvas, not the caption safe box)
    }
  | {
      mode: "page"; // §2b
      start: number;
      end: number;
      text: string;
      color?: string; // default pageBg ?? accent
      ink?: string; // default pageInk ?? (luma rule)
      font?: "display" | "base"; // default display
      size?: number; // default 150
      weight?: number; // brand display_weight, default 800
      slide?: "right" | "left" | "none"; // default right
    }
  | {
      mode: "frame"; // §2c
      start: number;
      end: number;
      bg?: string; // default #FFFFFF
      card?: { x: number; y: number; w: number; h: number; radius: number };
      list: { text: string; at: number; icon?: IconName }[];
      pill?: {
        w?: number;
        h?: number;
        radius?: number;
        gap?: number;
        y?: number;
        bg?: string;
        ink?: string;
        size?: number;
      };
    }
  | {
      mode: "igpost"; // §2d
      start: number;
      end: number;
      pixelate?: number; // mosaic block px, default 0 = off
      scaleFrom?: number; // default 1.15
      scaleTo?: number; // default 0.78
      rows?: LWord[]; // captions inside the image area
    };

export type DynProps = {
  src: string;
  durationInFrames: number;
  accent: string;
  font: FontSpec;
  matte?: { dir: string; windows: [number, number][] };
  // Color deployment: `base` is the color of every non-accent glyph (default white); `chip` renders the
  // accent as a chip device — accent block, base-colored text, skewed — instead of accent text. Both
  // default to a white-text, accent-hero look, so a plan that sets neither renders as it always did.
  base?: string;
  chip?: boolean;
  // `heroBase` colors the behind/front lockup glyphs (default = base), for a look whose running text and heroes differ.
  heroBase?: string;
  // A second face for the RUNNING words only (heroes, lockups, chips, hook keep `font`): e.g. a condensed cut for
  // the running words while the big words stay normal width.
  wordFont?: FontSpec;
  // Ink outline on hero / lockup glyphs (not chips): solid dark heroes can blend into a dark background; an
  // outlined fill separates from both a light wall and a dark one.
  heroStroke?: { width: number; color: string };
  // Ink opacity for TEXT modes (running words, heroes, lockup rows) — chips stay solid. White type on a bright
  // scene reads softer knocked back to ~0.92 than at full white.
  textOpacity?: number;
  // Shadow for hero / lockup glyphs (default: a soft shadow on front lockups, a light one behind). A soft halo
  // can carry the separation that an outline would.
  heroShadow?: string;
  // Sub-pixel feather on the glyph edges (CSS blur, px at 1080 wide) for TEXT modes; chips stay crisp.
  // Crisp vector type over softer video reads hard-edged even with a halo; ~0.8 px matches the footage.
  textSoften?: number;
  // Your look's measured width factors (see GLYPH_CAPS below). Absent = the renderer's own numbers below.
  glyph?: Glyph;
  // Whip cuts on the footage between clusters: a few frames of blur + push around `at` (frame index).
  whips?: { at: number; frames?: number; amount?: number }[];
  // Footage punch-ins (README, Pacing) — see `Punch` above. Empty = the pre-punch
  // look, unchanged. A whip that lands inside a punch composes on top of it (scales multiply).
  punches?: Punch[];
  // ---- device props. Every one is optional and every default reproduces a plan that sets none of them.
  chipNeg?: string; // §1b/§1d box + strike color (default #E3161B)
  heroGlow?: boolean; // §1a master switch for the `glow` word device
  heroTint?: string; // §1a gradient start (default = accent mixed 55% toward white)
  glowPx?: number; // §1a near drop-shadow radius (default 14; the far one scales with it)
  ctaWash?: string; // §2a wash color (default accent)
  pageBg?: string; // §2b page fill (default accent)
  pageInk?: string; // §2b page text (default by the luma rule)
  displayFont?: FontSpec; // §2b page face (default = the caption font)
  displayWeight?: number; // brand display_weight: the page word's weight unless the event sets its own
  pillBg?: string; // §2c pill fill (default accent)
  pillInk?: string; // §2c pill icon + text (default by the luma rule)
  // The soft build's colors (cluster_plan.soft_plan), read by soft events only: the accent's deep and light tints a word settles through,
  // text (the brand's colors.white, what every other line settles to), barInk (what the payoff on the bar settles to) and neg (what a red payoff
  // settles to, and a strike's color).
  soft?: SoftColors;
  events: Ev[];
};

// ONE width budget for every mode (cluster_plan.MAXW). Lines are centerd on canvas x=540 and the
// safe box (x 65..940) is not symmetric around that point, so the binding limit is the tighter
// side: 2*(940-540) = 800 px of total text width. GLYPH_CAPS/GLYPH_RUN are the reference face's measured
// px per pt per char for heavy uppercase and for the running words (glyph.py holds the same two numbers). A look whose
// faces are narrower or wider carries its own measured numbers in the `glyph` prop (spec_adapter.py measures its font
// files through glyph.glyph_factors), one per kind of text this file caps: `caps` the display
// face in capitals (a hero row, the CTA's first row), `word` the rows that are not the hero, `hook` the
// display face in the look's own case (the CTA's other rows, the page word). With no `glyph` prop the
// numbers below apply as they are.
const MAXW = 800;
const GLYPH_CAPS = 0.75;
const GLYPH_RUN = 0.58;
type Glyph = { caps: number; word: number; hook: number };
// The narrowest a text is ever estimated at, in em: FLOOR_LETTERS letters of the caps factor (3 em at the
// renderer's own factor), so a word of one or two letters is not handed a cap bigger than a heavy word would get.
const FLOOR_LETTERS = 4;
const floorEm = (g?: Glyph) => FLOOR_LETTERS * (g?.caps ?? GLYPH_CAPS);
// What a spaced row (tracking) and a marked row (its glyph + gap) add to the width a cluster row is capped by, in em per
// letter and em per row. scripts/cluster_plan.py reads GLYPH_CAPS, FLOOR_LETTERS and these five out of THIS file (one
// `const NAME = <number>;` line each), so the planner's block heights are the ones that render: keep them in that shape.
const SPACED_EM = 0.28;
const MARK_PIN_EM = 0.9;
const MARK_LOGO_EM = 1.1;
const MARK_ICON_EM = 1.0;

const OVERSHOOT = Easing.bezier(0.2, 1.35, 0.4, 1); // lands past 1 then settles (cluster entrances)
const shadow = "0 3px 18px rgba(0,0,0,0.45), 0 0 2px rgba(0,0,0,0.5)";
// The chip: an accent block with base-colored text, skewed -8deg with the glyphs un-skewed inside
// Width grows by ~0.44em of padding — the planner/hand pass must budget for it (cluster_plan chip-aware sizes).
const CHIP_SKEW = "skewX(-8deg)";
const chipBox = (accent: string, base: string): React.CSSProperties => ({
  background: accent,
  color: base,
  padding: "0.04em 0.22em 0.06em",
  letterSpacing: "0.01em",
  textShadow: "none",
});
const Unskew: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ display: "inline-block", transform: "skewX(8deg)" }}>
    {children}
  </span>
);

// ---------------------------------------------------------------------------
// device helpers
// ---------------------------------------------------------------------------
const CHIP_NEG = "#E3161B"; // §3 chip_neg default
const GLOW_PX = 14; // §1a near radius default
const GLOW_FAR = 40 / 14; // §1a: the far shadow was measured at 40 px against a 14 px near one

const hexRgb = (hex: string): [number, number, number] => {
  const h = hex.replace("#", "").trim();
  const s =
    h.length === 3
      ? h
          .split("")
          .map((c) => c + c)
          .join("")
      : h.slice(0, 6);
  const n = parseInt(s, 16);
  return Number.isNaN(n)
    ? [255, 255, 255]
    : [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const rgba = (hex: string, a: number) => {
  const [r, g, b] = hexRgb(hex);
  return `rgba(${r},${g},${b},${a})`;
};
// WCAG relative luminance. §3's ink rule: a bright surface (> 0.6) takes black ink, anything else
// takes the look's base — so a lime page gets black type and a deep orange page keeps white.
const luma = (hex: string) => {
  const [r, g, b] = hexRgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const inkFor = (bg: string, base: string) =>
  luma(bg) > 0.6 ? "#000000" : base;
// §1a: the gradient start defaults to the accent mixed 55% toward white.
const tintFor = (accent: string, tint?: string) =>
  tint ?? interpolateColors(0.55, [0, 1], [accent, WHITE]);
// §1a: gradient clipped to the glyphs + TWO drop-shadows (tight + wide). background-clip:text kills
// text-shadow, so this REPLACES the row's shadow; heroStroke is dropped on these spans (stroke does
// not composite with clip-text).
const glowFill = (accent: string, tint?: string): React.CSSProperties => ({
  backgroundImage: `linear-gradient(135deg, ${tintFor(accent, tint)} 0%, ${accent} 70%)`,
  WebkitBackgroundClip: "text",
  backgroundClip: "text",
  color: "transparent",
  WebkitTextFillColor: "transparent",
  textShadow: "none",
});
const glowShadows = (accent: string, px = GLOW_PX) =>
  `drop-shadow(0 0 ${px}px ${rgba(accent, 0.55)}) drop-shadow(0 0 ${Math.round(px * GLOW_FAR)}px ${rgba(accent, 0.25)})`;

function hit(frame: number, atFrame: number, dur: number, from: number) {
  const p = interpolate(frame, [atFrame, atFrame + dur], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: SNAP,
  });
  return { opacity: Math.min(1, p * 1.6), scale: from - (from - 1) * p };
}
function fadeOut(t: number, end: number, len: number) {
  if (len <= 0) return 1; // fade 0 = the event leaves on the cut (interpolate needs a strictly increasing range)
  return interpolate(t, [end - len, end], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

// ---------------------------------------------------------------------------
// footage punch-ins (README, Pacing)
// ---------------------------------------------------------------------------
// A punch scales the FULL-FRAME clip about a fixed origin so the face stays put: snap in over
// `frames`, hold, ease back to 1 over PUNCH_OUT. `at` is a FRAME index (whips are too), not seconds
// like the events. The person matte takes the same scale so a behind cluster stays hidden by the
// person. Each punch eases back to 1.0 after its hold, so consecutive punches breathe in and out.
export type Punch = {
  at: number;
  scale?: number; // default 1.12, the planner's own (cadence.py PUNCH_SCALE)
  frames?: number; // snap-in length, default 3
  hold?: number; // frames at `scale` before the return; default = up to the next punch, capped at 24
  cx?: number; // zoom origin in 1080x1920 px; default = canvas center
  cy?: number;
};
const PUNCH_SCALE = 1.12;
const PUNCH_IN = 3;
const PUNCH_HOLD_MAX = 24;
const PUNCH_OUT = 6;
// SNAP is the way in and the way out is "eases back to 1 over 6 frames",
// so the return runs the same curve reversed. `punches` must be ascending by `at` (the caller sorts).
function punchAt(frame: number, punches: Punch[]) {
  let cur: Punch | undefined;
  let nxt: Punch | undefined;
  for (let i = 0; i < punches.length; i++) {
    if (punches[i].at <= frame) {
      cur = punches[i];
      nxt = punches[i + 1];
    }
  }
  if (!cur) return null;
  const inN = Math.max(1, cur.frames ?? PUNCH_IN);
  const to = cur.scale ?? PUNCH_SCALE;
  const hold = Math.max(
    0,
    cur.hold ??
      Math.min(PUNCH_HOLD_MAX, nxt ? nxt.at - (cur.at + inN) : PUNCH_HOLD_MAX),
  );
  const f0 = cur.at;
  const f1 = f0 + inN;
  const f2 = f1 + hold;
  const f3 = f2 + PUNCH_OUT;
  if (frame >= f3) return null; // back at 1 — no transform at all, so no needless composite layer
  const scale =
    frame <= f1
      ? interpolate(frame, [f0, f1], [1, to], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: SNAP,
        })
      : frame >= f2
        ? interpolate(frame, [f2, f3], [to, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: SNAP,
          })
        : to;
  return { scale, cx: cur.cx ?? 540, cy: cur.cy ?? 960 };
}

const Lockup: React.FC<{
  e: Extract<Ev, { mode: "behind" | "front" }>;
  accent: string;
  weight: number;
  lead: number;
  font: string;
  runFont?: string; // the brand's running face, for the rows that are not the hero
  base: string;
  chip: boolean;
  heroStroke?: { width: number; color: string };
  textOpacity?: number;
  heroShadow?: string;
  textSoften?: number;
  // word devices — off unless the plan sets them
  chipNeg?: string;
  heroGlow?: boolean;
  heroTint?: string;
  glowPx?: number;
  glyph?: Glyph;
}> = ({
  e,
  accent,
  weight,
  lead,
  font,
  runFont,
  base,
  chip,
  heroStroke,
  textOpacity = 1,
  heroShadow,
  textSoften = 0,
  chipNeg = CHIP_NEG,
  heroGlow = false,
  heroTint,
  glowPx = GLOW_PX,
  glyph,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t < e.start || t > e.end) return null;
  const row = e.mode === "behind" && e.stack === "row";
  const cl = !!e.cluster;
  const endFrame = Math.round(e.end * fps);
  // cluster exit: the whole lockup scales away + blurs over the last exitFrames (fade otherwise)
  const exitN = e.exitFrames ?? 5;
  const ex =
    cl && (e.exit ?? "blur") === "blur"
      ? interpolate(frame, [endFrame - exitN, endFrame], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
          easing: Easing.in(Easing.quad),
        })
      : 0;
  const fo = cl ? 1 - ex : fadeOut(t, e.end, e.fade ?? 0.2);
  const leading = e.leading ?? 0.82;
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <div
        style={{
          position: "absolute",
          top: e.y,
          display: "flex",
          flexDirection: row ? "row" : "column",
          alignItems: "center",
          gap: row ? 36 : 0,
          opacity: fo,
          transform: cl ? `scale(${1 - 0.08 * ex})` : undefined,
          filter: cl && ex > 0 ? `blur(${10 * ex}px)` : undefined,
        }}
      >
        {e.words.map((w, i) => {
          // §1b: a neg row IS the chip of its phrase — the accent chip device stands down for it.
          const isChip = chip && !!w.accent && !w.neg;
          const skewed = isChip || (!!w.neg && chip);
          const atF = Math.round(w.at * fps);
          // entrance: plain lockups pop (4 frames from 1.08); cluster rows blur in with an overshoot
          let op = 1,
            sc = 1,
            bl = 0,
            tx = 0;
          if (cl) {
            const n = e.enterFrames ?? 5;
            const p = interpolate(frame, [atF, atF + n], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            });
            const po = interpolate(frame, [atF, atF + n + 2], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: OVERSHOOT,
            });
            op = Math.min(1, p * 1.5);
            bl = 14 * (1 - p);
            sc = 0.86 + 0.14 * po;
            tx = (i % 2 === 0 ? -1 : 1) * 36 * (1 - p);
          } else {
            const h = hit(frame, atF, 4, 1.08);
            op = h.opacity;
            sc = h.scale;
          }
          // §1b: the glyphs are already in place and the BOX wipes on — no pop, no stagger.
          if (w.neg) {
            op = 1;
            sc = 1;
            bl = 0;
            tx = 0;
          }
          // §1f: the spaced reveal (blur 12→0 + opacity over 6 frames) replaces the row's own
          // blur-in; the cluster's scale/stagger stay so the row still belongs to the lockup.
          if (w.spaced) {
            const ps = interpolate(frame, [atF, atF + 6], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: SNAP,
            });
            op = ps;
            bl = 12 * (1 - ps);
          }
          // README, Devices: a row carries AT MOST ONE leading mark, pin > logo >
          // icon. Resolved once so the width cap and the render agree.
          const logoName = w.pin ? undefined : w.logo;
          const iconName = w.pin || w.logo ? undefined : w.icon;
          // Width cap: a spaced row carries SPACED_EM of tracking per char and a marked row its glyph +
          // gap (MARK_PIN_EM, MARK_LOGO_EM, MARK_ICON_EM), so all of them widen the row and must widen
          // its divisor (C1 safe-box law).
          const per =
            (glyph ? (w.hero ? glyph.caps : glyph.word) : GLYPH_CAPS) +
            (w.spaced ? SPACED_EM : 0);
          const markEm = w.pin
            ? MARK_PIN_EM
            : logoName
              ? MARK_LOGO_EM
              : iconName
                ? MARK_ICON_EM
                : 0;
          const size = Math.min(
            w.size ?? 220,
            Math.floor(
              (e.maxW ?? MAXW) /
                Math.max(floorEm(glyph), w.text.length * per + markEm),
            ),
          );
          // §1c: base while counting → accent for 6 frames on landing → base.
          const isCount = w.count !== undefined;
          const cFrames = w.countFrames ?? 8;
          const landed =
            isCount && frame >= atF + cFrames && frame < atF + cFrames + 6;
          // §1a: glow is a hero-row device only — never on a chip, never on a neg box. On a count
          // row it is held back to the accent landing (§1c), which is the only accent moment there.
          const useGlow =
            !!w.glow && heroGlow && !isChip && !w.neg && (!isCount || landed);
          const countVal =
            w.count === undefined
              ? 0
              : Math.round(
                  w.count *
                    interpolate(frame, [atF, atF + cFrames], [0, 1], {
                      extrapolateLeft: "clamp",
                      extrapolateRight: "clamp",
                      easing: Easing.out(Easing.cubic),
                    }),
                );
          const normal = w.color ?? (w.accent ? accent : base);
          let ink = normal;
          if (w.neg)
            ink = WHITE; // §1b: a red box wants white type, not the brand base
          else if (isCount) ink = landed ? accent : (w.color ?? base);
          else if (w.spaced)
            // §1f: blue on arrival, base by frame 10
            ink = interpolateColors(
              interpolate(frame, [atF, atF + 10], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
              [0, 1],
              [accent, normal],
            );
          const blurPx = isChip || w.neg ? 0 : textSoften + bl;
          const filters: string[] = [];
          if (blurPx > 0) filters.push(`blur(${blurPx}px)`);
          if (useGlow) filters.push(glowShadows(accent, glowPx));
          // §1b: 5-frame left→right clip wipe over a box that is already laid out.
          const negP = interpolate(frame, [atF, atF + 5], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: SNAP,
          });
          // §1d: the line starts 4 frames after the row lands and takes 6 to cross it.
          const strikeP = interpolate(frame, [atF + 4, atF + 10], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: SNAP,
          });
          // §1e/§2: the row's leading mark drops in over 4 frames from the row's `at`
          // (translateY -0.25em → 0, scale 1.25 → 1) — the pin's curve, shared by logo and icon.
          const pinP = interpolate(frame, [atF, atF + 4], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: SNAP,
          });
          const markStyle: React.CSSProperties = {
            transform: `translateY(${-0.25 * size * (1 - pinP)}px) scale(${1.25 - 0.25 * pinP})`,
          };
          const body = isCount ? countText(w.text, countVal) : w.text;
          return (
            <span
              key={i}
              style={{
                fontFamily: runFont && !w.hero ? runFont : font,
                fontWeight: w.weight ?? weight,
                fontSize: size,
                color: ink,
                letterSpacing: w.spaced
                  ? `${SPACED_EM}em`
                  : cl
                    ? (e.tracking ?? "-0.04em")
                    : "-0.045em",
                wordSpacing: cl ? "0.06em" : "0.14em",
                lineHeight: cl ? 1 : 0.95,
                marginTop:
                  cl && i > 0 ? `${-(1 - leading) * size}px` : undefined,
                whiteSpace: "nowrap",
                opacity:
                  op *
                  (isChip || w.neg ? 1 : textOpacity) *
                  // §1d: the struck row dims as the line completes
                  (w.strike ? 1 - 0.2 * strikeP : 1),
                transform: `translateX(${(w.dx ?? 0) + tx}px) scale(${sc})${skewed ? " " + CHIP_SKEW : ""}`,
                textShadow:
                  heroShadow ??
                  (e.mode === "front" ? shadow : "0 2px 10px rgba(0,0,0,0.3)"),
                filter: filters.length ? filters.join(" ") : undefined,
                ...(isChip
                  ? { ...chipBox(accent, base), marginTop: "0.08em" }
                  : {}),
                ...(w.neg
                  ? {
                      // §1b: solid chipNeg box, square corners, no shadow, wiping on from the left
                      background: chipNeg,
                      padding: "0.06em 0.22em 0.08em",
                      borderRadius: 0,
                      letterSpacing: "0.01em",
                      textShadow: "none",
                      clipPath: `inset(0 ${(1 - negP) * 100}% 0 0)`,
                      marginTop:
                        cl && i > 0 ? `${-(1 - leading) * size}px` : undefined,
                    }
                  : {}),
                ...(w.spaced
                  ? {
                      // trailing letter-space would push the row left; pad it back
                      paddingLeft: `${SPACED_EM}em`,
                      textTransform: "uppercase" as const,
                    }
                  : {}),
                ...(useGlow ? glowFill(accent, heroTint) : {}),
                ...(w.strike ? { position: "relative" as const } : {}),
                ...(!isChip && !w.neg && !useGlow && heroStroke
                  ? {
                      WebkitTextStroke: `${heroStroke.width}px ${heroStroke.color}`,
                      paintOrder: "stroke fill",
                    }
                  : {}),
              }}
            >
              {/* §1e/§2: the leading mark sits inline before the row, centerd on the cap height.
                  The pin keeps its accent fill; a logo/icon takes the ROW's color (`ink`), which is
                  base on a plain row and the accent on an accent one. */}
              {w.pin ? (
                <PinGlyph color={accent} style={markStyle} />
              ) : logoName ? (
                <LogoGlyph
                  name={logoName}
                  color={ink}
                  em={0.9}
                  style={markStyle}
                />
              ) : iconName ? (
                <RowIcon
                  name={iconName}
                  color={ink}
                  em={0.8}
                  style={markStyle}
                />
              ) : null}
              {skewed ? <Unskew>{body}</Unskew> : body}
              {/* §1d: 6 px chipNeg rule at 52% of the em box, wiping left→right */}
              {w.strike ? (
                <span
                  style={{
                    position: "absolute",
                    left: 0,
                    top: "52%",
                    height: 6,
                    width: `${strikeP * 100}%`,
                    background: chipNeg,
                    borderRadius: 3,
                    transform: "translateY(-50%)",
                  }}
                />
              ) : null}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// ===========================================================================
// Layout events (README, Devices). Each one owns its beat: the planner clamps any
// cluster that would overlap it. One component per mode so each stays readable.
// ===========================================================================

// §2a — cta: a wash up from the bottom, a two-row lockup, three pulsing chevrons under it.
// The video stays; only the wash sits between it and the rows.
const Cta: React.FC<{
  e: Extract<Ev, { mode: "cta" }>;
  font: string;
  base: string;
  wash: string;
  glyph?: Glyph;
}> = ({ e, font, base, wash, glyph }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t < e.start || t > e.end) return null;
  const s = Math.round(e.start * fps);
  const en = Math.round(e.end * fps);
  // 6 frames in, 6 out — the wash is the only thing that fades; the rows cut with it.
  const washOp = Math.min(
    interpolate(frame, [s, s + 6], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
    interpolate(frame, [en - 6, en], [1, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    }),
  );
  // row 1 lands 4 frames after row 0; both blur in over 5 like a cluster row.
  const enter = (k: number) => {
    const at = s + k * 4;
    const p = interpolate(frame, [at, at + 5], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
    const po = interpolate(frame, [at, at + 7], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: OVERSHOOT,
    });
    return {
      opacity: Math.min(1, p * 1.5),
      blur: 14 * (1 - p),
      scale: 0.86 + 0.14 * po,
    };
  };
  const last = enter(Math.max(0, e.rows.length - 1));
  // chevrons: opacity marches down the stack every 6 frames, the block drifts on a 24-frame sine
  const lit = Math.floor(frame / 6) % 3;
  const drift = 6 - 6 * Math.cos((2 * Math.PI * frame) / 24);
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <AbsoluteFill
        style={{
          background: `linear-gradient(to bottom, ${rgba(wash, 0)} 40%, ${rgba(wash, 0.78)} 100%)`,
          opacity: washOp,
        }}
      />
      <div
        style={{
          position: "absolute",
          top: e.y ?? 700,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        {e.rows.map((r, k) => {
          const caps = k === 0; // §2a: row 0 is the 800-weight caps slam
          const en2 = enter(k);
          // the reference "FILL OUT" is ~190 px; the caption safe box (MAXW 800) would cap it at 133, so a
          // CTA carries its own budget (default 960 = the top-zone width) — centerd, it stays on canvas.
          const budget = e.maxW ?? 960;
          const size = Math.min(
            r.size ?? (caps ? 190 : 74),
            Math.floor(
              budget /
                Math.max(
                  floorEm(glyph),
                  r.text.length *
                    (caps
                      ? (glyph?.caps ?? GLYPH_CAPS)
                      : (glyph?.hook ?? GLYPH_RUN)),
                ),
            ),
          );
          return (
            <div
              key={k}
              style={{
                fontFamily: font,
                fontWeight: r.weight ?? (caps ? 800 : 600),
                fontSize: size,
                color: base,
                textTransform: caps ? "uppercase" : undefined,
                letterSpacing: caps ? "-0.03em" : "-0.01em",
                lineHeight: 1.05,
                whiteSpace: "nowrap",
                opacity: en2.opacity,
                transform: `scale(${en2.scale})`,
                filter: en2.blur > 0 ? `blur(${en2.blur}px)` : undefined,
                textShadow: shadow,
              }}
            >
              {r.text}
            </div>
          );
        })}
        {(e.chevrons ?? true) && (
          <svg
            width={120}
            height={80}
            viewBox="0 0 120 80"
            fill="none"
            style={{
              marginTop: 36,
              opacity: last.opacity,
              transform: `translateY(${drift}px)`,
              filter: last.blur > 0 ? `blur(${last.blur}px)` : undefined,
            }}
          >
            {[0, 1, 2].map((k) => (
              <polyline
                key={k}
                points={`6,${6 + k * 22} 60,${34 + k * 22} 114,${6 + k * 22}`}
                stroke={base}
                strokeWidth={6}
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity={lit === k ? 1 : 0.35}
              />
            ))}
          </svg>
        )}
      </div>
    </AbsoluteFill>
  );
};

// §2b — page: a full-frame solid page, one display word sliding in behind a motion blur, hard cut out.
// PAGE_MAXW is the full safe width (not MAXW): a page has no video under it, so the caption safe box
// does not apply — but the word still must not run off canvas.
const PAGE_MAXW = 950;
const PageEvent: React.FC<{
  e: Extract<Ev, { mode: "page" }>;
  displayFont: string;
  font: string;
  accent: string;
  base: string;
  pageBg?: string;
  pageInk?: string;
  displayWeight?: number;
  glyph?: Glyph;
}> = ({
  e,
  displayFont,
  font,
  accent,
  base,
  pageBg,
  pageInk,
  displayWeight,
  glyph,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t < e.start || t > e.end) return null;
  const s = Math.round(e.start * fps);
  const bg = e.color ?? pageBg ?? accent;
  const ink = e.ink ?? pageInk ?? inkFor(bg, base);
  // Chrome has no directional blur — a plain 20→0 blur over the slide is what the reference reads as.
  const p = interpolate(frame, [s, s + 7], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: SNAP,
  });
  const dx = e.slide === "none" ? 0 : e.slide === "left" ? -600 : 600;
  const size = Math.min(
    e.size ?? 150,
    Math.floor(
      PAGE_MAXW /
        Math.max(floorEm(glyph), e.text.length * (glyph?.hook ?? GLYPH_RUN)),
    ),
  );
  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg,
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          fontFamily: e.font === "base" ? font : displayFont,
          fontWeight: e.weight ?? displayWeight ?? 800,
          fontSize: size,
          color: ink,
          letterSpacing: "-0.03em",
          lineHeight: 1,
          whiteSpace: "nowrap",
          transform: `translateX(${dx * (1 - p)}px)`,
          filter: p < 1 ? `blur(${20 * (1 - p)}px)` : undefined,
        }}
      >
        {e.text}
      </div>
    </AbsoluteFill>
  );
};

// §2c — frame: the video shrinks into a rounded card over 8 frames while a flat bg fades in behind
// it, spoken-list pills drop in above, then it scales back to full over 6.
const CARD = { x: 0, y: 640, w: 1080, h: 1280, radius: 40 };
const FrameEvent: React.FC<{
  e: Extract<Ev, { mode: "frame" }>;
  src: string;
  font: string;
  accent: string;
  base: string;
  pillBg?: string;
  pillInk?: string;
}> = ({ e, src, font, accent, base, pillBg, pillInk }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t < e.start || t > e.end) return null;
  const s = Math.round(e.start * fps);
  const en = Math.round(e.end * fps);
  const c = e.card ?? CARD;
  const p = Math.min(
    interpolate(frame, [s, s + 8], [0, 1], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: SNAP,
    }),
    interpolate(frame, [en - 6, en], [1, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: SNAP,
    }),
  );
  const mix = (a: number, b: number) => a + (b - a) * p;
  const pb = pillBg ?? accent;
  const pi = pillInk ?? inkFor(pb, base);
  const pw = e.pill?.w ?? 620;
  const ph = e.pill?.h ?? 90;
  const gap = e.pill?.gap ?? 24;
  return (
    <AbsoluteFill>
      <AbsoluteFill
        style={{ backgroundColor: e.bg ?? "#FFFFFF", opacity: p }}
      />
      <div
        style={{
          position: "absolute",
          left: mix(0, c.x),
          top: mix(0, c.y),
          width: mix(1080, c.w),
          height: mix(1920, c.h),
          borderRadius: mix(0, c.radius),
          overflow: "hidden",
          boxShadow: `0 30px 60px rgba(0,0,0,${0.18 * p})`,
        }}
      >
        {/* the SAME clip, drawn a second time inside the card: full-frame box translated to the
            card center and scaled by card.w/1080, so the shrink reads as the video moving in. */}
        <div
          style={{
            position: "absolute",
            left: "50%",
            top: "50%",
            width: 1080,
            height: 1920,
            transform: `translate(-50%, -50%) scale(${mix(1, c.w / 1080)})`,
          }}
        >
          <OffthreadVideo
            src={staticFile(src)}
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        </div>
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: e.pill?.y ?? 170,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap,
        }}
      >
        {e.list.map((it, k) => {
          const at = Math.round(it.at * fps);
          const q = interpolate(frame, [at, at + 5], [0, 1], {
            extrapolateLeft: "clamp",
            extrapolateRight: "clamp",
            easing: SNAP,
          });
          return (
            <div
              key={k}
              style={{
                width: pw,
                height: ph,
                borderRadius: e.pill?.radius ?? 18,
                background: pb,
                display: "flex",
                alignItems: "center",
                gap: 18,
                paddingLeft: 26,
                boxSizing: "border-box",
                opacity: q,
                transform: `translateY(${-30 * (1 - q)}px)`,
              }}
            >
              {it.icon ? <Icon name={it.icon} size={24} color={pi} /> : null}
              <span
                style={{
                  fontFamily: font,
                  fontWeight: 700,
                  fontSize: e.pill?.size ?? 46,
                  color: pi,
                  letterSpacing: "-0.01em",
                  whiteSpace: "nowrap",
                }}
              >
                {it.text}
              </span>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// §2d — igpost: the clip inside a mock post card on a white page, slow zoom-out + left drift,
// optional mosaic over the image area.
const POST = { w: 760, h: 900, img: 760, top: 510, head: 70 };
const IgPost: React.FC<{
  e: Extract<Ev, { mode: "igpost" }>;
  src: string;
  font: string;
  base: string;
  id: string;
}> = ({ e, src, font, base, id }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  if (t < e.start || t > e.end) return null;
  const s = Math.round(e.start * fps);
  const en = Math.round(e.end * fps);
  const q = interpolate(frame, [s, Math.max(s + 1, en)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const sc =
    (e.scaleFrom ?? 1.15) + ((e.scaleTo ?? 0.78) - (e.scaleFrom ?? 1.15)) * q;
  const px = e.pixelate ?? 0;
  // §2d mosaic: sample a small dot per block (feFlood + feTile) and dilate it back to block size.
  // A CSS scale-down/up cannot do this — Chrome re-rasterises at the composited scale, so the
  // downscale is thrown away. feMorphology is the other route.
  const dot = Math.max(2, Math.round(px / 6));
  const fid = `mosaic-${id}`;
  return (
    <AbsoluteFill style={{ backgroundColor: "#FFFFFF" }}>
      {px > 0 && (
        <svg width={0} height={0} style={{ position: "absolute" }}>
          <filter
            id={fid}
            x="0"
            y="0"
            width={POST.img}
            height={POST.img}
            filterUnits="userSpaceOnUse"
          >
            <feFlood
              x={Math.round(px / 2 - dot / 2)}
              y={Math.round(px / 2 - dot / 2)}
              width={dot}
              height={dot}
            />
            <feComposite width={px} height={px} />
            <feTile result="a" />
            <feComposite in="SourceGraphic" in2="a" operator="in" />
            <feMorphology operator="dilate" radius={px / 2} />
          </filter>
        </svg>
      )}
      <div
        style={{
          position: "absolute",
          left: (1080 - POST.w) / 2,
          top: POST.top,
          width: POST.w,
          height: POST.h,
          background: "#FFFFFF",
          borderRadius: 8,
          boxShadow: "0 20px 50px rgba(0,0,0,0.12)",
          overflow: "hidden",
          transform: `translateX(${-40 * q}px) scale(${sc})`,
        }}
      >
        <div
          style={{
            height: POST.head,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 20px",
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 18,
              border: "2px solid #222",
            }}
          />
          <div
            style={{
              color: "#222",
              fontFamily: font,
              fontWeight: 800,
              fontSize: 30,
              letterSpacing: "0.08em",
              lineHeight: 0.6,
            }}
          >
            •••
          </div>
        </div>
        <div
          style={{
            position: "relative",
            width: POST.img,
            height: POST.img,
            overflow: "hidden",
            background: "#000",
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: 0,
              filter: px > 0 ? `url(#${fid})` : undefined,
            }}
          >
            <OffthreadVideo
              src={staticFile(src)}
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          </div>
          {/* captions live in the image area's bottom third, running-word style */}
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: "62%",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
            }}
          >
            {/* running-word style = ONE word at a time: only the latest row whose onset has passed is drawn */}
            {(e.rows ?? [])
              .filter((w, k, arr) => {
                const nxt = arr[k + 1];
                return (
                  frame >= Math.round(w.at * fps) &&
                  (!nxt || frame < Math.round(nxt.at * fps))
                );
              })
              .map((w, k) => {
                const h = hit(frame, Math.round(w.at * fps), 3, 1.12);
                return (
                  <div
                    key={k}
                    style={{
                      fontFamily: font,
                      fontWeight: w.weight ?? 700,
                      fontSize: w.size ?? 56,
                      color: w.color ?? base,
                      letterSpacing: "-0.02em",
                      lineHeight: 1.15,
                      whiteSpace: "nowrap",
                      opacity: h.opacity,
                      transform: `scale(${h.scale})`,
                      textShadow: shadow,
                    }}
                  >
                    {w.text}
                  </div>
                );
              })}
          </div>
        </div>
        <div
          style={{
            height: POST.h - POST.head - POST.img,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 22px",
          }}
        >
          <div style={{ display: "flex", gap: 22 }}>
            <Icon name="heart" size={30} color="#111" />
            <Icon name="comment" size={30} color="#111" />
            <Icon name="share" size={30} color="#111" />
          </div>
          <Icon name="bookmark" size={30} color="#111" />
        </div>
      </div>
    </AbsoluteFill>
  );
};

// A soft build line's mark (SoftThought draws the line): its pin, else its logo, else its icon, with this file's glyphs at their row-mark
// sizes, in the color and the style the thought gives it as it arrives.
const softMark = (w: LWord, color: string, style: React.CSSProperties) =>
  w.pin ? (
    <PinGlyph color={color} style={style} />
  ) : w.logo ? (
    <LogoGlyph name={w.logo} color={color} em={0.9} style={style} />
  ) : w.icon ? (
    <RowIcon name={w.icon} color={color} em={0.8} style={style} />
  ) : null;

export const CaptionsDynamic: React.FC<DynProps> = ({
  src,
  accent,
  font: fontSpec,
  matte,
  events,
  base = WHITE,
  chip = false,
  heroBase,
  wordFont,
  heroStroke,
  textOpacity = 1,
  heroShadow,
  textSoften = 0,
  whips = [],
  punches = [],
  chipNeg = CHIP_NEG,
  heroGlow = false,
  heroTint,
  glowPx = GLOW_PX,
  ctaWash,
  pageBg,
  pageInk,
  displayFont,
  displayWeight,
  pillBg,
  pillInk,
  glyph,
  soft,
}) => {
  const heroColor = heroBase ?? base;
  // a plan with no caption events draws no text (the bare smoke render), so it needs no font; any plan that does must name one
  const fontReady =
    events.length > 0
      ? loadFonts(fontSpec, "the plan's font")
      : Promise.resolve();
  const font = fontStack(fontSpec?.family ?? "sans-serif");
  // the brand's running face: for the non-hero rows of a cluster and for single running words
  if (wordFont) loadFonts(wordFont, "the plan's wordFont");
  const runFont = wordFont ? fontStack(wordFont.family) : undefined;
  // §2b: the page word may run in a second (display) face; with no displayFont it IS the caption face.
  if (displayFont) loadFonts(displayFont, "the plan's displayFont");
  const dispFont = displayFont ? fontStack(displayFont.family) : font;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const behinds = events.filter((e) => e.mode === "behind") as Extract<
    Ev,
    { mode: "behind" }
  >[];
  const fronts = events.filter((e) => e.mode === "front") as Extract<
    Ev,
    { mode: "front" }
  >[];
  if (!soft && fronts.some((e) => e.soft))
    cancelRender(
      new Error(
        "a soft event needs the plan's soft colors {deep, light, text, barInk, neg}: plan the take with cluster_plan.py",
      ),
    );
  const ctas = events.filter((e) => e.mode === "cta") as Extract<
    Ev,
    { mode: "cta" }
  >[];
  const pages = events.filter((e) => e.mode === "page") as Extract<
    Ev,
    { mode: "page" }
  >[];
  const cards = events.filter((e) => e.mode === "frame") as Extract<
    Ev,
    { mode: "frame" }
  >[];
  const posts = events.filter((e) => e.mode === "igpost") as Extract<
    Ev,
    { mode: "igpost" }
  >[];
  // §2: page/frame/igpost OWN the frame — the full-frame clip and the matte stand down for their
  // span (each draws the clip it needs itself). `cta` is not exclusive: the video stays under it.
  const now = frame / fps; // every event's start/end is in seconds
  const owned = [...pages, ...cards, ...posts].some(
    (e) => now >= e.start && now <= e.end,
  );
  const inWindow =
    !owned &&
    !!matte &&
    matte.windows.some(([s, e]) => frame >= s && frame <= e);
  // whip: strongest AT the cut frame, ramping in/out over `frames` on each side
  let whip = 0;
  for (const w of whips) {
    const n = w.frames ?? 4;
    const d = Math.abs(frame - w.at);
    if (d <= n) whip = Math.max(whip, (w.amount ?? 1) * (1 - d / (n + 1)));
  }
  // §1: the punch — sorted so `punchAt` can take the last one that has started. The clip carries
  // punch × whip about the punch's origin (with no punch, origin = the canvas center, so a lone whip
  // renders exactly as it did before punches existed); the matte carries the punch alone, since the
  // whip has never moved it and that does not change here.
  const punchList = React.useMemo(
    () => [...punches].sort((a, b) => a.at - b.at),
    [punches],
  );
  const punch = punchAt(frame, punchList);
  const punchScale = punch?.scale ?? 1;
  const origin = punch ? `${punch.cx}px ${punch.cy}px` : undefined;
  const camScale = punchScale * (whip > 0 ? 1 + 0.08 * whip : 1);
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {!owned && (
        <OffthreadVideo
          src={staticFile(src)}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            filter: whip > 0 ? `blur(${18 * whip}px)` : undefined,
            transform: camScale !== 1 ? `scale(${camScale})` : undefined,
            transformOrigin: origin,
          }}
        />
      )}
      {behinds.map((e, i) => (
        <Lockup
          key={`b${i}`}
          e={e}
          accent={accent}
          weight={900}
          lead={0}
          font={font}
          runFont={runFont}
          base={heroColor}
          chip={chip}
          heroStroke={heroStroke}
          textOpacity={textOpacity}
          heroShadow={heroShadow}
          textSoften={textSoften}
          chipNeg={chipNeg}
          heroGlow={heroGlow}
          heroTint={heroTint}
          glowPx={glowPx}
          glyph={glyph}
        />
      ))}
      {inWindow && (
        <Img
          src={staticFile(
            `${matte!.dir}/f${String(frame).padStart(5, "0")}.webp`,
          )}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            // §1: the same punch transform as the clip — a behind cluster must stay hidden by the
            // person while the frame pushes in.
            transform: punchScale !== 1 ? `scale(${punchScale})` : undefined,
            transformOrigin: origin,
          }}
        />
      )}
      {fronts.map((e, i) =>
        e.soft ? (
          soft ? (
            <SoftThought
              key={`f${i}`}
              e={e}
              family={font}
              accent={accent}
              colors={soft}
              fontReady={fontReady}
              spread={SPACED_EM}
              mark={softMark}
            />
          ) : null
        ) : (
          <Lockup
            key={`f${i}`}
            e={e}
            accent={accent}
            weight={900}
            lead={0}
            font={font}
            runFont={runFont}
            base={heroColor}
            chip={chip}
            heroStroke={heroStroke}
            textOpacity={textOpacity}
            heroShadow={heroShadow}
            textSoften={textSoften}
            chipNeg={chipNeg}
            heroGlow={heroGlow}
            heroTint={heroTint}
            glowPx={glowPx}
            glyph={glyph}
          />
        ),
      )}
      {/* layout events draw last — over the matte, and `cta`'s wash
          over the video. page/frame/igpost already replaced the frame above. */}
      {ctas.map((e, i) => (
        <Cta
          key={`c${i}`}
          e={e}
          font={font}
          base={base}
          wash={e.wash ?? ctaWash ?? accent}
          glyph={glyph}
        />
      ))}
      {pages.map((e, i) => (
        <PageEvent
          key={`p${i}`}
          e={e}
          displayFont={dispFont}
          font={font}
          accent={accent}
          base={base}
          pageBg={pageBg}
          pageInk={pageInk}
          displayWeight={displayWeight}
          glyph={glyph}
        />
      ))}
      {cards.map((e, i) => (
        <FrameEvent
          key={`fr${i}`}
          e={e}
          src={src}
          font={font}
          accent={accent}
          base={base}
          pillBg={pillBg}
          pillInk={pillInk}
        />
      ))}
      {posts.map((e, i) => (
        <IgPost
          key={`ig${i}`}
          e={e}
          id={String(i)}
          src={src}
          font={font}
          base={base}
        />
      ))}
    </AbsoluteFill>
  );
};
