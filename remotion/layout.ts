/**
 * layout.ts: where things sit, derived from the composition's own size so 1080x1920, 1920x1080 and 1080x1350 all work.
 *
 * Everything is a fraction of the frame, or a size scaled by `k` (the short side over 1080). At 1080x1920 the numbers come
 * out as: caption box top 1190, hook top 300, picture-in-picture box at x 66 y 1180 (300 x 380), brand mark at y 236,
 * platform top band 220 and bottom band 270.
 *
 * Platform UI to stay out of (`Safe`): a top band (the handle row), a bottom band (caption and audio row) and a right rail
 * (like / comment / share) that starts partway down. These are defaults for a full-screen vertical feed, a feed post and a
 * landscape player; pass `safe` on a composition to override them.
 */
import { useVideoConfig } from "remotion";
import { BRAND } from "./theme";
import type { Safe } from "./types";

export type Orientation = "story" | "feed" | "wide";

const ZONES: Record<Orientation, Safe> = {
  story: {
    top: 220 / 1920,
    bottom: 270 / 1920,
    rail: 100 / 1080,
    railTop: 1100 / 1920,
  },
  feed: { top: 0.04, bottom: 0.05, rail: 0.03, railTop: 0.5 },
  wide: { top: 0.05, bottom: 0.09, rail: 0, railTop: 1 },
};

export type Layout = {
  w: number;
  h: number;
  fps: number;
  /** type and spacing scale: the short side over 1080 */
  k: number;
  orientation: Orientation;
  /** the platform UI, in px: bands from the top and bottom edges, the right rail's width and where it starts */
  safe: { top: number; bottom: number; rail: number; railTop: number };
  caption: { top: number; h: number; size: number; padL: number; padR: number };
  hook: { top: number; minTop: number; maxW: number };
  pip: { x: number; y: number; w: number; h: number };
  mark: { left: number; top: number; h: number };
  window: { top: number; margin: number; w: number };
  /** the lower-third slot: its left edge and the y of its bottom edge */
  lower: { left: number; bottom: number };
  /** the clearance a card keeps above the speaker's head */
  headGap: number;
};

/** a theme number is a fraction of the frame (0..1) or, above 1, px in a 1080 x 1920 frame */
const along = (v: number, frame: number, ref: number) => (v <= 1 ? v * frame : (v / ref) * frame);

export function orientationOf(w: number, h: number): Orientation {
  const a = w / h;
  return a < 0.7 ? "story" : a <= 1.2 ? "feed" : "wide";
}

export function layoutFor(w: number, h: number, fps = 30, safe?: Partial<Safe> | null): Layout {
  const short = Math.min(w, h);
  const k = short / 1080;
  const orientation = orientationOf(w, h);
  const z = { ...ZONES[orientation], ...(safe ?? {}) };
  const sp = {
    top: Math.round(z.top * h),
    bottom: Math.round(z.bottom * h),
    rail: Math.round(z.rail * w),
    railTop: Math.round(z.railTop * h),
  };
  const capH = 300 * k;
  const capTop = Math.min(along(BRAND.captionTop ?? 1190, h, 1920), h - sp.bottom - capH);
  const capSize =
    BRAND.captionSize == null ? 84 * k : BRAND.captionSize <= 1 ? BRAND.captionSize * short : BRAND.captionSize * k;
  const pipW = 300 * k,
    pipH = 380 * k;
  const margin = 66 * k;
  return {
    w,
    h,
    fps,
    k,
    orientation,
    safe: sp,
    caption: {
      top: Math.round(capTop),
      h: Math.round(capH),
      size: capSize,
      padL: Math.round(70 * k),
      padR: Math.round(Math.max(70 * k, sp.rail + 10 * k)),
    },
    hook: {
      top: Math.round(Math.max(along(BRAND.hookTop ?? 300, h, 1920), sp.top + 8 * k)),
      minTop: Math.round(sp.top + 8 * k),
      maxW: Math.round(Math.min(w * 0.889, short * 1.1)),
    },
    pip: {
      x: Math.round(margin),
      y: Math.round(h - sp.bottom - pipH - 90 * k),
      w: Math.round(pipW),
      h: Math.round(pipH),
    },
    mark: {
      left: Math.round(margin),
      top: Math.round(sp.top + 16 * k),
      h: Math.round(30 * k),
    },
    window: {
      top: Math.round(sp.top + 100 * k),
      margin: Math.round(40 * k),
      w: Math.round(320 * k),
    },
    lower: {
      left: Math.round(margin),
      bottom: Math.round(h - sp.bottom - 20 * k),
    },
    headGap: Math.round(22 * k),
  };
}

/** the layout of the current composition (call after applyTheme) */
export function useLayout(safe?: Partial<Safe> | null): Layout {
  const { width, height, fps } = useVideoConfig();
  return layoutFor(width, height, fps, safe);
}
