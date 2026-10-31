/**
 * theme.ts: the design tokens and the motion curves.
 *
 * applyTheme() takes the theme dict (see types.ts / README.md), swaps it in before anything reads it, and refuses a theme with
 * no id or no colors.accent. Every color, face and switch the compositions draw comes from here, nothing is hard coded
 * elsewhere. Motion: ease-out curves that settle with no overshoot, a little blur and rise on the way in, exits under 0.3 s.
 */
import React from "react";
import { Easing, staticFile, delayRender, continueRender, cancelRender } from "remotion";
import type { FontFaceSpec, HookLook, Theme } from "./types";

export const HOOK_LOOKS: HookLook[] = ["card", "accent", "ink", "bigtype", "bar", "mono"];

/** a file in the public folder, or a URL / absolute path used as given */
export const res = (p: string) => (/^(https?:|file:|data:|\/)/.test(p ?? "") ? p : staticFile(p));

/* ---------------- colors ---------------- */
const channels = (col: string): [number, number, number] => {
  const m6 = col.match(/^#([0-9a-f]{6})$/i);
  if (m6) {
    const n = parseInt(m6[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const m3 = col.match(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/i);
  if (m3) return [0, 1, 2].map((i) => parseInt(m3[i + 1] + m3[i + 1], 16)) as [number, number, number];
  const r = col.match(/rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/i);
  return r ? [Number(r[1]), Number(r[2]), Number(r[3])] : [128, 128, 128];
};
const luminance = (col: string) => {
  const [r, g, b] = channels(col).map((v) => {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const la = luminance(a),
    lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
};
/** the first of ink, base, white, black that reads on `fill` at 4.5:1 (else whichever reads best) */
export const readableOn = (fill: string) => {
  const cands = [C.ink, C.base, "#ffffff", "#000000"];
  const ok = cands.find((c) => contrast(c, fill) >= 4.5);
  if (ok) return ok;
  return cands.reduce((best, c) => (contrast(c, fill) > contrast(best, fill) ? c : best));
};
const rgba = (col: string, a: number) => {
  const [r, g, b] = channels(col);
  return `rgba(${r},${g},${b},${a})`;
};
/** a color mixed toward another by k (0..1), as #rrggbb */
const mix = (a: string, b: string, k: number) => {
  const x = channels(a),
    y = channels(b);
  return (
    "#" +
    [0, 1, 2]
      .map((i) =>
        Math.round(x[i] + (y[i] - x[i]) * k)
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
};

/** the active colors (applyTheme overwrites every one). These are placeholders. */
export const C = {
  accent: "#2f6bff",
  ink: "#0a0a0a",
  base: "#ffffff",
  onAccent: "#ffffff",
  negative: "#e5484d",
  bg: "#0a0a0a",
  card: "#161616",
};
const DEFAULT_COLORS = { ...C };
/** the accent / negative color at an alpha, for glows and tints */
export const tint = (a: number) => rgba(C.accent, a);
export const negTint = (a: number) => rgba(C.negative, a);

/** the card surface used by the `ink` hook and dark cards */
export const SOLID: React.CSSProperties = {
  background: "rgba(17,17,17,0.94)",
  border: "1px solid rgba(255,255,255,0.08)",
  boxShadow: "0 24px 60px rgba(0,0,0,0.45)",
};
const DEFAULT_SOLID = { ...SOLID };

/* ---------------- the active theme's switches ---------------- */
export const BRAND = {
  id: "",
  name: "",
  caps: false,
  skew: 0,
  capWords: 3,
  stroke: 0,
  /** raw theme numbers: a fraction of the frame (0..1) or px in a 1080 x 1920 frame; null = the default */
  captionTop: null as number | null,
  captionSize: null as number | null,
  hookTop: null as number | null,
  hookSize: null as number | null,
  hookLook: "card" as HookLook,
  /** the CSS font stacks: the display face (everything), the running face and the mono face (null when the theme has none) */
  fontFamily: "ui-sans-serif, system-ui, sans-serif",
  runningFamily: null as string | null,
  monoFamily: null as string | null,
  /** which face the hook card draws in: "display", "running", or a family name */
  hookFont: "display",
  smooth: false,
  brollStyle: "cut",
  /** the mark's files (drawn only when markOn, or when an edit turns the mark on): a lockup, or an emblem and the name */
  lockup: null as { light: string | null; dark: string | null } | null,
  mark: null as { emblem: string; name: string } | null,
  /** the theme's own switch for the mark (lockup_on) */
  markOn: false,
  endCard: null as { on: boolean; line: string; handle: string } | null,
};
const DEFAULT_BRAND = { ...BRAND };

/** a theme for Remotion Studio and for tests of the compositions alone; engine/render.py always passes the real one */
export const PLACEHOLDER_THEME: Theme = {
  id: "placeholder",
  name: "",
  colors: {
    accent: "#2f6bff",
    ink: "#0a0a0a",
    base: "#ffffff",
    on_accent: "#ffffff",
    negative: "#e5484d",
    bg: "#0a0a0a",
    card: "#161616",
  },
};

/* ---------------- fonts ---------------- */
let LOADED: FontFace[] = [];
let FONTS: Promise<void> = Promise.resolve();
const GENERIC = "ui-sans-serif, system-ui, sans-serif";

const loadFaces = (family: string, faces: FontFaceSpec[]) =>
  faces.map((f) =>
    new FontFace(family, `url(${res(f.src)})`, {
      weight: String(f.weight ?? 400),
    })
      .load()
      .then((x) => {
        document.fonts.add(x);
        LOADED.push(x);
      }),
  );

/** one font slot: its faces are registered under `family` (the DNA's generated name, else `alias`) and the CSS stack puts that name first */
function fontSlot(
  family: string | null | undefined,
  faces: FontFaceSpec[] | null | undefined,
  alias: string,
  fallback: string,
) {
  const fs = (faces ?? []).filter((f) => f && f.src);
  const name = fs.length ? String(family || alias).replace(/["\\]/g, "") : null;
  return { faces: fs, name, stack: name ? `"${name}", ${fallback}` : fallback };
}

/** the CSS font stack the hook card draws in: the mono face for the mono look, else the hook font the theme names (display, running or a family) */
export function hookFamily(look: HookLook): string {
  if (look === "mono" && BRAND.monoFamily) return BRAND.monoFamily;
  if (BRAND.hookFont === "running" && BRAND.runningFamily) return BRAND.runningFamily;
  if (BRAND.hookFont && BRAND.hookFont !== "display" && BRAND.hookFont !== "running")
    return `"${BRAND.hookFont.replace(/["\\]/g, "")}", ${BRAND.fontFamily}`;
  return BRAND.fontFamily;
}

/** the page waits for the theme's fonts before it draws or measures anything (a card that fits itself above the speaker's head
 *  would otherwise measure in the fallback face). Call after applyTheme(); render the scene only when it returns true. */
export function useFontsReady(): boolean {
  const [handle] = React.useState(() => delayRender("theme fonts"));
  const [ok, setOk] = React.useState(false);
  React.useEffect(() => {
    FONTS.then(() => setOk(true)).catch((e) => cancelRender(e));
  }, []);
  React.useEffect(() => {
    if (ok) continueRender(handle);
  }, [ok, handle]);
  return ok;
}

let APPLIED = "";
/**
 * Switch the renderer to a theme: colors, card surface, faces, caps, marks, motion profile. Idempotent for the same theme. Starts
 * from the placeholder defaults every time, so nothing a previous theme set survives into this one. Throws on a theme with no id or
 * no colors.accent, and on a hook_look the renderer does not draw. Returns the promise of the theme's faces loading.
 */
export function applyTheme(t: Theme | null | undefined): Promise<void> {
  if (!t || !t.id || !t.colors || !t.colors.accent)
    throw new Error("applyTheme: the theme needs an id and colors.accent");
  if (t.hook_look != null && !HOOK_LOOKS.includes(t.hook_look))
    throw new Error(
      `applyTheme: hook_look must be one of ${HOOK_LOOKS.join(", ")}, got ${JSON.stringify(t.hook_look)}`,
    );
  const key = JSON.stringify(t);
  if (APPLIED === key) return FONTS;
  APPLIED = key;
  const c = t.colors;
  Object.assign(C, DEFAULT_COLORS, {
    accent: c.accent,
    ...(c.ink ? { ink: c.ink } : {}),
    ...(c.base ? { base: c.base } : {}),
    ...(c.negative ? { negative: c.negative } : {}),
  });
  C.bg = c.bg ?? C.ink;
  C.card = c.card ?? mix(C.bg, "#ffffff", 0.07);
  C.onAccent = c.on_accent ?? readableOn(C.accent);
  Object.assign(SOLID, DEFAULT_SOLID, {
    background: rgba(C.card, 0.94),
    ...(t.surface?.background ? { background: t.surface.background } : {}),
    ...(t.surface?.border ? { border: t.surface.border } : {}),
  });
  Object.assign(BRAND, DEFAULT_BRAND);
  BRAND.id = t.id;
  BRAND.name = t.name ?? "";
  BRAND.caps = !!t.caps;
  BRAND.skew = t.skew ?? 0;
  BRAND.capWords = Math.min(6, Math.max(1, Math.round(t.cap_words ?? 3)));
  BRAND.stroke = t.caption_stroke ?? 0;
  BRAND.captionTop = typeof t.caption_top === "number" ? t.caption_top : null;
  BRAND.captionSize = typeof t.caption_size === "number" ? t.caption_size : null;
  BRAND.hookTop = typeof t.hook_top === "number" ? t.hook_top : null;
  BRAND.hookSize = typeof t.hook_size === "number" ? t.hook_size : null;
  BRAND.hookLook = t.hook_look ?? "card";
  BRAND.hookFont = typeof t.hook_font === "string" && t.hook_font ? t.hook_font : "display";
  BRAND.smooth = !!t.smooth_motion;
  BRAND.brollStyle = t.broll_style ?? "cut";
  BRAND.lockup =
    t.lockup_src || t.lockup_dark_src ? { light: t.lockup_src ?? null, dark: t.lockup_dark_src ?? null } : null;
  BRAND.mark = !BRAND.lockup && t.emblem_src ? { emblem: t.emblem_src, name: t.name ?? "" } : null;
  BRAND.markOn = t.lockup_on === true;
  const ec = t.end_card;
  BRAND.endCard = ec ? { on: ec.on !== false, line: ec.line ?? "", handle: ec.handle ?? "" } : null;
  const fallback = t.font_fallback ? `${t.font_fallback}, ${GENERIC}` : GENERIC;
  const display = fontSlot(t.font_family, t.font_faces, "CRDisplay", fallback);
  const running = fontSlot(t.running_family, t.running_faces, "CRRunning", display.stack);
  const mono = fontSlot(t.mono_family, t.mono_faces, "CRMono", display.stack);
  BRAND.fontFamily = display.stack;
  BRAND.runningFamily = running.name ? running.stack : null;
  BRAND.monoFamily = mono.name ? mono.stack : null;
  if (typeof document !== "undefined") {
    LOADED.forEach((f) => document.fonts.delete(f));
    LOADED = [];
    FONTS = Promise.all(
      [display, running, mono].flatMap((slot) => (slot.name ? loadFaces(slot.name, slot.faces) : [])),
    ).then(() => undefined);
    FONTS.catch(() => undefined); // the scene's own wait reports the failure
  }
  return FONTS;
}

/* ---------------- motion ---------------- */
/** entrance: fast then settles, no overshoot */
export const SETTLE = Easing.bezier(0.2, 0.9, 0.3, 1.0);
/** exit: accelerates away */
export const OUT = Easing.bezier(0.6, 0.0, 0.9, 0.5);
export const INOUT = Easing.bezier(0.83, 0.0, 0.17, 1.0);
/** the long smooth ease-out used by every graphic that arrives: no overshoot, a long tail */
export const EASE_OUT = Easing.bezier(0.16, 1, 0.3, 1);
/** the smooth profile (theme smooth_motion): a gentle start and a long settle in, a soft acceleration out, no snap in an in-and-out */
export const SMOOTH_IN = Easing.bezier(0.3, 0.0, 0.1, 1.0);
export const SMOOTH_OUT = Easing.bezier(0.45, 0.0, 0.8, 0.6);
export const SMOOTH_INOUT = Easing.bezier(0.45, 0.0, 0.15, 1.0);
export const CL = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
};
export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

/** 0..1 entrance progress and 0..1 exit progress for a window */
export function env(t: number, start: number, end: number, inD = 0.42, outD = 0.28) {
  if (BRAND.smooth) {
    inD *= 1.45;
    outD *= 1.35;
  }
  const kin = clamp01((t - start) / inD);
  const kout = clamp01((t - (end - outD)) / outD);
  return {
    kin: BRAND.smooth ? SMOOTH_IN(kin) : SETTLE(kin),
    kout: BRAND.smooth ? SMOOTH_OUT(kout) : OUT(kout),
    on: t >= start - 1e-3 && t <= end + 1e-3,
    raw: kin,
  };
}

/** the style of something arriving at `at`: it fades up from a little blur and a little below its place on one long ease-out */
export function condense(t: number, at: number, d = 0.7, dy = 26) {
  if (BRAND.smooth) d *= 1.35;
  const a = EASE_OUT(clamp01((t - at) / d));
  return {
    a,
    style: {
      opacity: a,
      filter: `blur(${((1 - a) * 10).toFixed(2)}px)`,
      transform: `translateY(${((1 - a) * dy).toFixed(2)}px)`,
    } as React.CSSProperties,
  };
}
/** the style of something leaving at `end`: a short fade with a little blur, rising a touch */
export function leave(t: number, end: number, d = 0.28, dy = 14) {
  const x = OUT(clamp01((t - (end - d)) / d));
  return {
    x,
    style: {
      opacity: 1 - x,
      filter: x > 0 ? `blur(${(x * 8).toFixed(2)}px)` : undefined,
      transform: x > 0 ? `translateY(${(-x * dy).toFixed(2)}px)` : undefined,
    } as React.CSSProperties,
  };
}

/** the style of a graphic that is on screen from `start` to `end`: it arrives on one long smooth ease-out (a little blur, a little
 *  rise, no overshoot) and leaves on a short fade. Spread the result into the graphic's root style. */
export function appear(t: number, start: number, end: number, d = 0.8, dy = 28, outD = 0.3) {
  const a = condense(t, start, d, dy);
  const o = leave(t, end, outD, dy * 0.5);
  const blur = (1 - a.a) * 10 + o.x * 8;
  const rise = (1 - a.a) * dy - o.x * dy * 0.5;
  return {
    opacity: a.a * (1 - o.x),
    filter: blur > 0.05 ? `blur(${blur.toFixed(2)}px)` : undefined,
    transform: `translateY(${rise.toFixed(2)}px)`,
  } as React.CSSProperties;
}
