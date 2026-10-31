/**
 * softthought.tsx: the soft build's drawing of one thought (README, "The soft build"). A thought is a
 * lead-in, a payoff and an optional tail, laid out once and never re-centred. Every word is in its place from the start (one not yet arrived
 * is drawn at opacity 0) and arrives on its own spoken time, blurred, see-through and in the accent's deep tint, settling through its light
 * tint to its line's end colour (the brand's own text colour, the bar's ink on the bar, the red on a red payoff), left side first. The lead-in
 * steps back when the payoff starts to arrive, one payoff a reel opens a bar of the accent from its centre, and the whole thought leaves soft
 * from its exitAt. The motion and its numbers are softmotion.ts's.
 * Every renderer that draws the soft build carries this file byte for byte, as it carries softmotion.ts. It imports only react, remotion and
 * ./softmotion; what a host decides comes in through props: its display face, its accent and soft colours, when that face is loaded, how far a
 * spaced line spreads, how it draws a pin, a logo or an icon, and the frame its Sequence starts at when it mounts the thought in one. It also
 * holds the small helpers a host shares with it (SNAP, countText, and WHITE, which only the caption engine's cluster drawing still draws in),
 * so neither keeps a copy. A line is drawn in the case its words were planned in: a host's own text-transform never reaches it.
 */
import React from "react";
import {
  AbsoluteFill,
  Easing,
  cancelRender,
  continueRender,
  delayRender,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {
  SOFT,
  arrive,
  barOpen,
  dim,
  leave,
  outCubic,
  settle,
  sweep,
} from "./softmotion";

export const WHITE = "#FFFFFF";
export const SNAP = Easing.bezier(0.1, 0.9, 0.2, 1);

// The planner passes a count's FINAL string as `text` and the integer as `count`; keep whatever wraps the digits ("$1,200" is "$" and the
// number, "+50" is "+" and the number, "11" is the number) and group thousands again only when the planned string was grouped.
export const countText = (text: string, value: number) => {
  const m = text.match(/\d[\d,]*\d|\d/);
  if (!m) return String(value);
  const at = m.index ?? 0;
  const grouped = m[0].includes(",");
  return (
    text.slice(0, at) +
    (grouped ? value.toLocaleString("en-US") : String(value)) +
    text.slice(at + m[0].length)
  );
};

/**
 * The colours a thought settles through: the accent's deep and light tints, the brand's own text colour (its colors.white) an ordinary line settles
 * to, the ink a payoff on the bar settles to, and the negative red.
 */
export type SoftColours = {
  deep: string;
  light: string;
  text: string;
  barInk: string;
  neg: string;
};
/** One line of a thought, as a planner writes it. */
export type SoftRow = {
  text: string; // the line as it is drawn: case and punctuation as planned, its words split on single spaces
  at: number; // the spoken start of its first word
  ats?: number[]; // the spoken start of each of its words, one to one
  role?: "lead" | "pay" | "tail";
  size?: number; // px
  weight?: number;
  dx?: number; // px the line's centre sits from the frame's centre
  bar?: boolean; // the one payoff a reel that opens the bar
  neg?: boolean; // a red payoff: it settles to the negative red
  count?: number; // the number its digit word counts up to while it arrives
  strike?: boolean; // a struck payoff: a line wipes across it once it has landed
  spaced?: boolean; // a spread place name
  pin?: boolean; // the line leads with a mark: a pin, else a logo, else an icon
  logo?: string;
  icon?: string;
};
/** A thought: when it starts to arrive, when it starts to leave, when it is gone, its top, its shadow, and its lines. */
export type SoftEvent<R extends SoftRow = SoftRow> = {
  start: number;
  end: number;
  exitAt?: number;
  y: number;
  shadow?: "soft" | "deep"; // "deep" over a bright picture (the planner's legibility pass): both shadow layers at DEEP times their opacity; absent is soft
  words: R[];
};
/** How a host draws a line's mark (the line's pin, else its logo, else its icon), in the colour and style the thought gives it. */
export type SoftMark<R extends SoftRow = SoftRow> = (
  row: R,
  colour: string,
  style: React.CSSProperties,
) => React.ReactNode;

type Drawn<R extends SoftRow> = R & {
  size: number;
  weight: number;
  ats: number[];
};
type Box = { x: number; y: number; w: number; h: number };

// A line the planner finished: it has its size and weight, and one spoken time for each of its words.
const drawn = <R extends SoftRow>(w: R): w is Drawn<R> =>
  w.size !== undefined &&
  w.weight !== undefined &&
  w.ats !== undefined &&
  w.ats.length === w.text.split(" ").length;
// What stops a thought from being drawn, in one line, or null: nothing is drawn at a guessed size, weight, time or colour (a plan made before the soft
// colours carried the brand's text colour has none), and a bar line carries no mark (the bar's box is its payoff's text alone, so the planner never
// sends one).
function unfinished(e: SoftEvent, c: SoftColours): string | null {
  if (!c.text)
    return "a soft thought needs the colour its lines settle to, the plan's soft text (the brand's colors.white): plan it again";
  if (e.exitAt === undefined)
    return `a soft thought needs its exitAt, the second it starts to leave (the thought from ${e.start} s)`;
  const w = e.words.find((r) => !drawn(r));
  if (w) {
    if (w.size === undefined || w.weight === undefined)
      return `a soft line needs its size and its weight: "${w.text}"`;
    return `a soft line needs one spoken time a word (ats): "${w.text}"`;
  }
  const marked = e.words.find((r) => r.bar && (r.pin || r.logo || r.icon));
  if (marked)
    return `a soft bar line draws no mark: "${marked.text}" carries ${marked.pin ? "a pin" : marked.logo ? "a logo" : "an icon"}`;
  return null;
}

// One soft shadow under every glyph, the opacity of its two layers: a 9 px blur 6 px down and a 2.2 px blur 2 px down; lighter on the bar. A deep
// shadow draws both layers at DEEP times their opacity, except on the bar: its payoff's ink sits on the flat accent, which the picture never
// reaches, so a deeper shadow there only dulls the accent colour (a bar that should read #FC7007 came out (243, 108, 4), and the accent
// check refused it).
const SHADOW = { plain: [0.42, 0.22], bar: [0.22, 0.12] } as const;
const DEEP = 1.5;
const shadow = ([wide, near]: readonly [number, number], k: number) =>
  `drop-shadow(0 6px 18px rgba(0,0,0,${wide * k})) drop-shadow(0 2px 4.4px rgba(0,0,0,${near * k}))`;
// A word's face is padded out PAD_Y em above and below and PAD_X em at the sides, and a margin takes the padding back (softFace says why).
const PAD_Y = 0.25;
const PAD_X = 0.1;

// A payoff is drawn in capitals when its text has letters and is its own upper case (look.in_capitals, which the planners measure with).
const inCapitals = (text: string) =>
  /\p{L}/u.test(text) && text === text.toUpperCase();
// A line's letter spacing in em: the caps tracking on a payoff in capitals, the payoff's on any other, the lead-in's on a lead-in or a tail;
// a spaced line adds the host's spread a character on top, as the planner's refit counts it.
const spacing = (w: SoftRow, spread: number) =>
  (w.role !== "pay"
    ? SOFT.track.lead
    : inCapitals(w.text)
      ? SOFT.track.caps
      : SOFT.track.pay) + (w.spaced ? spread : 0);
// The colour a line's words settle to: the negative red on a red payoff, the bar's ink on the bar, the brand's own text colour anywhere else.
const endColour = (w: SoftRow, c: SoftColours) =>
  w.neg ? c.neg : w.bar ? c.barInk : c.text;
// A word's face: the sweep through background-clip: text over a transparent fill. That paints only inside the word's own box, and at a line
// height of 1.0 descenders and accents reach outside it, as does a glyph wider than its own advance (the hook of a j), so the box is padded
// out and a margin takes the padding back, which leaves the layout as it was. The sweep is inset by the side padding, so its columns run
// across the word itself.
const softFace = (
  p: number,
  c: SoftColours,
  end: string,
): React.CSSProperties => ({
  backgroundImage: sweep(p, c.deep, c.light, end, PAD_X),
  WebkitBackgroundClip: "text",
  backgroundClip: "text",
  color: "transparent",
  WebkitTextFillColor: "transparent",
  padding: `${PAD_Y}em ${PAD_X}em`,
  margin: `-${PAD_Y}em -${PAD_X}em`,
});

// The bar's box in the payoff line's own px: the line's ink box as the browser draws it, measured with canvas measureText in the line's face,
// weight, size and letter spacing, padded SOFT.bar.padX em left and right, padTop em above the tallest glyph and padBottom em below the lowest.
// At line height 1.0 the baseline sits at the line's top + (size - (ascent + descent)) / 2 + ascent. The text is all it measures: the planner
// never puts the bar on a line with a mark, and a bar line draws none.
function barBox(w: Drawn<SoftRow>, family: string, spread: number): Box {
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx)
    throw new Error("the soft bar has no 2D canvas to measure its payoff with");
  const size = w.size;
  ctx.font = `${w.weight} ${size}px ${family}`;
  ctx.letterSpacing = `${spacing(w, spread) * size}px`;
  const m = ctx.measureText(w.text);
  const baseline =
    (size - (m.fontBoundingBoxAscent + m.fontBoundingBoxDescent)) / 2 +
    m.fontBoundingBoxAscent;
  const start = w.spaced ? spread * size : 0; // a spaced line's left padding
  const { padX, padTop, padBottom } = SOFT.bar;
  return {
    x: start - m.actualBoundingBoxLeft - padX * size,
    y: baseline - m.actualBoundingBoxAscent - padTop * size,
    w: m.actualBoundingBoxLeft + m.actualBoundingBoxRight + 2 * padX * size,
    h:
      m.actualBoundingBoxAscent +
      m.actualBoundingBoxDescent +
      (padTop + padBottom) * size,
  };
}
// The bar's box once the display face is in (measured before, it would be a fallback face's): the render waits for it.
function useBarBox(
  w: Drawn<SoftRow> | undefined,
  family: string,
  fontReady: Promise<void>,
  spread: number,
): Box | null {
  const [box, setBox] = React.useState<Box | null>(null);
  const [handle] = React.useState(() =>
    w ? delayRender("the soft bar measures its payoff") : null,
  );
  React.useEffect(() => {
    if (!w) return;
    fontReady
      .then(() => setBox(barBox(w, family, spread)))
      .catch((err: unknown) => cancelRender(err));
  }, [w, family, fontReady, spread]);
  React.useEffect(() => {
    if (box && handle !== null) continueRender(handle);
  }, [box, handle]);
  return box;
}
// The bar line's clip: the open part of the bar, grown from the bar's centre. It is inset(0 X% 0 X%) of the bar's own box with
// X = (1 - open) * 50, written against the line's box, which holds the bar.
const barClip = (b: Box, open: number) => {
  const side = ((1 - open) * b.w) / 2;
  return `inset(${b.y}px calc(100% - ${b.x + b.w - side}px) calc(100% - ${b.y + b.h}px) ${b.x + side}px)`;
};

export type SoftThoughtProps<R extends SoftRow> = {
  e: SoftEvent<R>;
  family: string; // the display face, as a CSS font-family
  accent: string; // #RRGGBB
  colours: SoftColours;
  fontReady: Promise<void>; // settles once the display face is in document.fonts
  spread: number; // em a character a spaced line adds to its tracking
  mark?: SoftMark<R>; // draws a line's pin, logo or icon; without it no mark is drawn
  from?: number; // the frame the host's Sequence starts at, when it mounts the thought in one; absent, 0
};

export function SoftThought<R extends SoftRow>({
  e,
  family,
  accent,
  colours,
  fontReady,
  spread,
  mark,
  from = 0,
}: SoftThoughtProps<R>): React.ReactElement | null {
  // the clip's own frame: a thought's times are seconds on the clip's clock, wherever the host mounts it
  const frame = useCurrentFrame() + from;
  const { fps } = useVideoConfig();
  const now = frame / fps;
  const rows = e.words;
  const barRow = rows.find((w) => w.bar);
  const box = useBarBox(
    barRow && drawn(barRow) ? barRow : undefined,
    family,
    fontReady,
    spread,
  );
  const exitAt = e.exitAt;
  const why = unfinished(e, colours);
  if (exitAt === undefined || !rows.every(drawn) || why !== null) {
    cancelRender(new Error(why ?? "a soft thought cannot be drawn"));
    return null;
  }
  if (now < e.start || now > e.end) return null;
  const pay = rows.find((w) => w.role === "pay");
  const out = leave(now, exitAt);
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <div
        style={{
          position: "absolute",
          top: e.y,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: SOFT.gap,
          opacity: out.opacity,
          filter: out.blur > 0 ? `blur(${out.blur}px)` : undefined,
        }}
      >
        {rows.map((w, i) => {
          const end = endColour(w, colours);
          const shade = shadow(
            w.bar ? SHADOW.bar : SHADOW.plain,
            e.shadow === "deep" && !w.bar ? DEEP : 1,
          );
          const texts = w.text.split(" ");
          const filterOf = (a: { blur: number }) =>
            `${a.blur > 0 ? `blur(${a.blur}px) ` : ""}${shade}`;
          // a mark arrives with its line's first word: its opacity, its blur, and its colour, the word's at its left column settling to the
          // mark's own end colour (a pin's is the accent, or the red on a red payoff; a logo's or an icon's is its line's). A bar line has none:
          // unfinished stopped the render on one that does.
          const first = arrive(now, w.ats[0]);
          const marked =
            mark && (w.pin || w.logo || w.icon)
              ? mark(
                  w,
                  settle(
                    SOFT.arrive.sweepRate * first.p,
                    colours.deep,
                    colours.light,
                    w.pin && !w.neg ? accent : end,
                  ),
                  { opacity: first.opacity, filter: filterOf(first) },
                )
              : null;
          // the strike: today's wipe in the negative red, 4 frames after the line's last word is spoken, over 6; the line dims under it
          const landed = Math.round(w.ats[w.ats.length - 1] * fps);
          const struck = w.strike
            ? interpolate(frame, [landed + 4, landed + 10], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
                easing: SNAP,
              })
            : 0;
          const counting =
            w.count === undefined ? -1 : texts.findIndex((t) => /\d/.test(t));
          return (
            <div
              key={i}
              style={{
                position: "relative",
                height: w.size,
                lineHeight: 1,
                whiteSpace: "pre",
                textTransform: "none", // the planner cased the words; a host's capitals stay out
                fontFamily: family,
                fontWeight: w.weight,
                fontSize: w.size,
                letterSpacing: `${spacing(w, spread)}em`,
                // a spaced line's last letter carries its spread too, which would push the line left: pad it back
                paddingLeft: w.spaced ? `${spread}em` : undefined,
                opacity:
                  (w.role === "lead" && pay ? dim(now, pay.at) : 1) *
                  (1 - 0.2 * struck),
                transform: `translateX(${w.dx ?? 0}px)`,
                clipPath: w.bar
                  ? box
                    ? barClip(box, barOpen(now, w.at))
                    : "inset(0 50%)"
                  : undefined,
              }}
            >
              {w.bar && box ? (
                <div
                  style={{
                    position: "absolute",
                    left: box.x,
                    top: box.y,
                    width: box.w,
                    height: box.h,
                    background: accent,
                  }}
                />
              ) : null}
              {marked}
              {texts.map((t, k) => {
                const a = arrive(now, w.ats[k]);
                const word: React.CSSProperties = {
                  display: "inline-block",
                  opacity: a.opacity,
                  transform: `translateY(${a.rise}px)`,
                  filter: filterOf(a),
                };
                const face = softFace(a.p, colours, end);
                return (
                  <React.Fragment key={k}>
                    {k > 0 ? " " : null}
                    {k === counting ? (
                      // a count keeps its landed width while it runs, so nothing after it moves: the running number is drawn over its own
                      // final text, right aligned, and lands in its line's end colour
                      <span style={{ ...word, position: "relative" }}>
                        <span style={{ visibility: "hidden" }}>{t}</span>
                        <span
                          style={{
                            ...face,
                            position: "absolute",
                            top: 0,
                            right: 0,
                          }}
                        >
                          {countText(
                            t,
                            Math.round((w.count ?? 0) * outCubic(a.p)),
                          )}
                        </span>
                      </span>
                    ) : (
                      <span style={{ ...word, ...face }}>{t}</span>
                    )}
                  </React.Fragment>
                );
              })}
              {w.strike ? (
                <span
                  style={{
                    position: "absolute",
                    left: 0,
                    top: "52%",
                    height: 6,
                    width: `${struck * 100}%`,
                    background: colours.neg,
                    borderRadius: 3,
                    transform: "translateY(-50%)",
                  }}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
}
