/**
 * softmotion.ts: the motion maths of the soft build captions, as pure functions of time and nothing else.
 * The caption composition (CaptionsDynamic.tsx) draws with these numbers, and softbuild/look.py makes the same tints in Python: tests/captions/softbuild/test_softmotion.py
 * fails when the two differ, so change one and change the other. It imports nothing, so any project takes it as it is.
 * Times are seconds on the clip's own clock (frame / fps). `at` and `payAt` are a word's spoken start, as the plan has it: the arrival begins
 * SOFT.arrive.early before it. `exitAt` is the second a thought begins to leave, which the plan has already put 0.04 s ahead of the next
 * thought's arrival.
 * Colours come in as #RRGGBB. settle and sweep answer in rgb(r, g, b), which is what a CSS gradient draws; mix and tints answer in #RRGGBB, which
 * is what the plan keeps. A half rounds to the even number, as Python's round does, because softbuild/look.py makes the same tints and the two
 * must agree on every channel; Math.round would send a half up.
 * The numbers are the soft build's defaults (README, "The soft build").
 */

/** Every number the soft build's motion uses. Seconds, px, shares of a run (0 to 1), and em (a multiple of the line's drawn size). */
export const SOFT = {
  arrive: {
    early: 0.05, // a word starts to arrive this long before its spoken start
    dur: 0.34, // and takes this long
    opaqueAt: 0.55, // fully opaque at this share of the way (cubic out)
    clearAt: 0.8, // fully in focus at this share (cubic out)
    blur: 9, // px of blur it starts with
    rise: 7, // px under its place it starts (quint out)
    toBlack: 0.42, // deep: the accent this far toward black
    toWhite: 0.45, // light: the accent this far toward white
    split: 0.42, // the settle runs deep to light over this share of its run, then light to the end colour over the rest
    sweepRate: 1.45, // a column u across the word (0 its left edge, 1 its right) is at progress sweepRate * p - sweepLag * u
    sweepLag: 0.45,
  },
  dim: { to: 0.72, dur: 0.3 }, // the lead-in's opacity once the payoff has started to arrive, and the time it takes to get there
  leave: { lead: 0.04, dur: 0.16, blur: 6 }, // a thought starts to leave `lead` before the next one starts to arrive (the plan sets exitAt), over `dur`, to `blur` px
  bar: { lead: 0.04, dur: 0.3, padX: 0.2, padTop: 0.13, padBottom: 0.11 }, // opens `lead` before the payoff starts to arrive, over `dur` (quint out); padding in em
  track: { lead: -0.025, pay: -0.04, caps: -0.015 }, // letter spacing in em: a lead-in or a tail, a payoff, a payoff drawn in capitals (look.py TRACK)
  gap: 2, // px between two lines of a thought (look.py LINE_GAP)
} as const;

const clamp01 = (x: number): number => (x < 0 ? 0 : x > 1 ? 1 : x);

/** Fast, then slow: 0 at 0, 1 at 1, and x is held to 0..1. */
export const outCubic = (x: number): number => 1 - (1 - clamp01(x)) ** 3;

/** Faster, then slower still: the same shape as outCubic with a steeper start. */
export const outQuint = (x: number): number => 1 - (1 - clamp01(x)) ** 5;

/** Slow, fast, slow (smoothstep): 0 at 0, 1 at 1, and x is held to 0..1. */
export const smooth = (x: number): number => {
  const t = clamp01(x);
  return t * t * (3 - 2 * t);
};

/** A word arriving, `at` being its spoken start. p is how far along it is (0 to 1); opacity, blur (px) and rise (px still to climb) are what to draw. */
export function arrive(
  now: number,
  at: number,
): { p: number; opacity: number; blur: number; rise: number } {
  const a = SOFT.arrive;
  const p = clamp01((now - (at - a.early)) / a.dur);
  return {
    p,
    opacity: outCubic(p / a.opaqueAt),
    blur: a.blur * (1 - outCubic(p / a.clearAt)),
    rise: a.rise * (1 - outQuint(p)),
  };
}

const HEX = /^#[0-9A-Fa-f]{6}$/;

type Rgb = number[]; // red, green, blue: each 0 to 255

function parse(colour: string): Rgb {
  if (!HEX.test(colour))
    throw new Error(
      `softmotion: a colour is #RRGGBB, not ${JSON.stringify(colour)}`,
    );
  return [1, 3, 5].map((i) => parseInt(colour.slice(i, i + 2), 16));
}

/** Python's round: a half goes to the even number, where Math.round sends it up. look.tints rounds this way, so these must too. */
function roundEven(x: number): number {
  const down = Math.floor(x);
  const rest = x - down;
  if (rest !== 0.5) return rest < 0.5 ? down : down + 1;
  return down % 2 === 0 ? down : down + 1;
}

/** Each channel c + (to - c) * t, rounded, with t held to 0..1 so the answer is always a colour. */
function blend(from: Rgb, to: Rgb, t: number): Rgb {
  const k = clamp01(t);
  return from.map((c, i) => roundEven(c + (to[i] - c) * k));
}

const hex = (c: Rgb): string =>
  "#" + c.map((v) => v.toString(16).toUpperCase().padStart(2, "0")).join("");

const css = (c: Rgb): string => `rgb(${c[0]}, ${c[1]}, ${c[2]})`;

/** The colour t of the way from a to b, as #RRGGBB in upper case. */
export function mix(a: string, b: string, t: number): string {
  return hex(blend(parse(a), parse(b), t));
}

/** (deep, light) of an accent, as look.tints has them: the accent 42% of the way to black and 45% of the way to white. */
export function tints(accent: string): { deep: string; light: string } {
  return {
    deep: mix(accent, "#000000", SOFT.arrive.toBlack),
    light: mix(accent, "#FFFFFF", SOFT.arrive.toWhite),
  };
}

/**
 * The colour of a word at settle progress q (0 to 1), as rgb(r, g, b): the first 42% of q runs deep to light, the rest light to the end colour,
 * each segment eased with smooth. All three colours are checked whichever segment q is in.
 */
export function settle(
  q: number,
  deep: string,
  light: string,
  end: string,
): string {
  const d = parse(deep);
  const l = parse(light);
  const e = parse(end);
  const s = SOFT.arrive.split;
  const x = clamp01(q);
  return css(
    x <= s
      ? blend(d, l, smooth(x / s))
      : blend(l, e, smooth((x - s) / (1 - s))),
  );
}

const COLUMNS = [0, 0.25, 0.5, 0.75, 1]; // where a sweep takes a colour: shares of the word's width

/**
 * A word's colour across its width at arrival progress p: a CSS linear-gradient(90deg, ...) with a stop at each of the five columns, the
 * colour at column u being settle(1.45 p - 0.45 u), so the left side whitens first. A face padded out by `inset` em on each side passes the
 * inset, and the stops sit at calc(inset + u * (100% - 2 * inset)), so u runs across the word and not across its padding.
 */
export function sweep(
  p: number,
  deep: string,
  light: string,
  end: string,
  inset = 0,
): string {
  const a = SOFT.arrive;
  const stops = COLUMNS.map((u) => {
    const at = inset
      ? `calc(${inset}em + ${u} * (100% - ${2 * inset}em))`
      : `${u * 100}%`;
    return `${settle(a.sweepRate * p - a.sweepLag * u, deep, light, end)} ${at}`;
  });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

/** The lead-in's opacity: 1 until the payoff's first word starts to arrive (0.05 s before payAt), then down to 72% over 0.3 s. The tail is not dimmed. */
export function dim(now: number, payAt: number): number {
  return (
    1 -
    (1 - SOFT.dim.to) * smooth((now - payAt + SOFT.arrive.early) / SOFT.dim.dur)
  );
}

/** How far the bar is open, 0 to 1: it starts 0.04 s before the payoff's first word starts to arrive (payAt - 0.09) and takes 0.30 s, quint out. */
export function barOpen(now: number, payAt: number): number {
  return outQuint(
    (now - (payAt - SOFT.arrive.early - SOFT.bar.lead)) / SOFT.bar.dur,
  );
}

/** A thought leaving, from exitAt: over 0.16 s its opacity goes to 0 as a 6 px blur comes in, both smoothstep. */
export function leave(
  now: number,
  exitAt: number,
): { opacity: number; blur: number } {
  const e = smooth((now - exitAt) / SOFT.leave.dur);
  return { opacity: 1 - e, blur: SOFT.leave.blur * e };
}
