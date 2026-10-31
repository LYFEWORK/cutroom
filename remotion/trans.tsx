/**
 * trans.tsx: how an insert (b-roll) comes in and goes out.
 *
 *   cut      a 4-frame dissolve each way, nothing else moves
 *   whip-l   both layers travel together sideways with real horizontal motion blur (a whip pan)
 *   whip-r   the same, the other way
 *   zoom     the speaker pushes in fast while the shot settles in from a zoom (a zoom-through); it reverses on the way out
 *   soft     a dissolve-push: the shot fades up from a slight push while the speaker drifts in and softens
 *   band     a diagonal band wipe with an accent edge
 *
 * Two inserts that meet push straight into each other. The speaker layer and the insert layer both read the same plan so their
 * motion lines up frame for frame. Which kind an insert gets: its own `transition`, else the theme's broll_style
 * (cut | dynamic | soft | band; dynamic rotates whip-l, zoom, whip-r, or soft when smooth_motion is on).
 */
import React from "react";
import { INOUT, SMOOTH_INOUT, BRAND, C, clamp01 } from "./theme";
import type { Insert, InsertMode, Transition } from "./types";

export const TD = 0.24; // seconds a dynamic transition in takes
export const TDO = 0.2; // seconds the way back out takes
export const td = () => (BRAND.smooth ? 0.55 : TD);
export const tdo = () => (BRAND.smooth ? 0.5 : TDO);
const ease = (x: number) => (BRAND.smooth ? SMOOTH_INOUT(x) : INOUT(x));
const ROTATE: Transition[] = ["whip-l", "zoom", "whip-r"];

export const isDynamic = (k: Transition) => k === "whip-l" || k === "whip-r" || k === "zoom" || k === "soft";
/** seconds a kind takes to cover the picture beneath it */
export const entrance = (k: Transition) => (isDynamic(k) ? td() : k === "band" ? 0.45 : 0.13);

export type RInsert = Insert & { mode: InsertMode; kind: Transition };

/** the inserts in time order, each with its mode and the transition it will use */
export function resolveInserts(inserts: Insert[]): RInsert[] {
  let n = 0;
  return [...inserts]
    .sort((a, b) => a.start_s - b.start_s)
    .map((s) => {
      const mode: InsertMode = s.mode ?? "full";
      let kind: Transition | undefined = s.transition;
      if (!kind) {
        const style = BRAND.brollStyle;
        kind =
          mode === "window"
            ? "cut"
            : style === "dynamic"
              ? BRAND.smooth
                ? "soft"
                : ROTATE[n % ROTATE.length]
              : style === "soft"
                ? "soft"
                : style === "band"
                  ? "band"
                  : "cut";
      }
      if (mode !== "window") n++;
      return { ...s, mode, kind };
    });
}

export type Look = {
  tx: number;
  scale: number;
  hblur: number;
  blur: number;
  opacity: number;
};
const IDENT: Look = { tx: 0, scale: 1, hblur: 0, blur: 0, opacity: 1 };

export type PlanEntry = {
  s: RInsert;
  kind: Transition;
  joinIn: boolean;
  joinOut: boolean;
  nextKind: Transition;
  nextStart: number;
};

/** the full-frame inserts (full and pip) in order, with whether each meets the shot before and after it */
export const brollPlan = (inserts: RInsert[]): PlanEntry[] => {
  const br = inserts.filter((s) => s.mode !== "window").sort((a, b) => a.start_s - b.start_s);
  return br.map((s, i) => {
    const prev = br[i - 1],
      next = br[i + 1];
    return {
      s,
      kind: s.kind,
      joinIn: !!prev && Math.abs(prev.end_s - s.start_s) < 0.15,
      joinOut: !!next && Math.abs(next.start_s - s.end_s) < 0.15,
      nextKind: next ? next.kind : s.kind,
      nextStart: next ? next.start_s : s.end_s,
    };
  });
};

/** when an insert leaves the screen: one that pushes into the next stays up until the next one has covered it, so the speaker
 *  never shows between two inserts */
export const brollUntil = (p: PlanEntry) =>
  p.joinOut ? Math.max(p.s.end_s, p.nextStart + entrance(p.nextKind)) : p.s.end_s;

const dirOf = (k: Transition) => (k === "whip-r" ? -1 : 1); // +1: the picture travels left (the new shot comes from the right)

/** how an insert looks at time t (entering, holding, leaving); W is the frame width */
export const brollLook = (p: PlanEntry, t: number, W: number): Look => {
  const { s, kind, joinOut, nextKind } = p;
  const L = { ...IDENT };
  if (!isDynamic(kind)) return L;
  const e = ease(clamp01((t - s.start_s) / td()));
  const xw = joinOut ? clamp01((t - p.nextStart) / td()) : clamp01((t - (s.end_s - tdo())) / tdo());
  const x = ease(xw);
  if (e < 1) {
    if (kind === "soft") {
      L.scale = 1.06 - 0.06 * e;
      L.opacity = e;
      L.blur = (1 - e) * 6;
    } else if (kind === "zoom") {
      L.scale = 1.16 - 0.16 * e;
      L.opacity = clamp01(e * 1.7);
      L.blur = (1 - e) * 10;
    } else {
      L.tx = dirOf(kind) * W * (1 - e);
      L.hblur = Math.sin(Math.PI * clamp01((t - s.start_s) / td())) * 30;
    }
  }
  if (x > 0) {
    const k = joinOut ? nextKind : kind; // into the next shot: move with its entrance; back to the speaker: our own move
    if (k === "soft") {
      L.scale *= 1 + 0.03 * x;
      if (!joinOut) {
        L.opacity *= 1 - x;
        L.blur += x * 4;
      }
    } else if (k === "zoom") {
      L.scale *= 1 + 0.15 * x;
      L.opacity *= joinOut ? 1 : 1 - clamp01((x - 0.25) / 0.75);
      L.blur += x * 8;
    } else if (k === "whip-l" || k === "whip-r") {
      L.tx += -dirOf(k) * W * x;
      L.hblur += Math.sin(Math.PI * xw) * (joinOut ? 30 : 24);
    }
  }
  return L;
};

/** how the speaker's own picture moves while an insert comes in or goes out (never during a join) */
export const baseLook = (plan: PlanEntry[], t: number, W: number): Look => {
  const L = { ...IDENT };
  for (const p of plan) {
    const { s, kind } = p;
    if (!isDynamic(kind)) continue;
    if (!p.joinIn && t >= s.start_s - 0.01 && t <= s.start_s + td()) {
      const e = ease(clamp01((t - s.start_s) / td()));
      if (kind === "soft") {
        L.scale = 1 + 0.05 * e;
        L.blur = e * 5;
      } else if (kind === "zoom") {
        L.scale = 1 + 0.2 * e;
        L.blur = e * 8;
      } else {
        L.tx = -dirOf(kind) * W * 0.3 * e;
        L.hblur = Math.sin(Math.PI * clamp01((t - s.start_s) / td())) * 30;
      }
    }
    if (!p.joinOut && t >= s.end_s - tdo() && t <= s.end_s + 0.01) {
      const x = ease(clamp01((t - (s.end_s - tdo())) / tdo()));
      if (kind === "soft") {
        L.scale = 1.05 - 0.05 * x;
        L.blur = (1 - x) * 5;
      } else if (kind === "zoom") {
        L.scale = 1.2 - 0.2 * x;
        L.blur = (1 - x) * 8;
      } else {
        L.tx = dirOf(kind) * W * 0.3 * (1 - x);
        L.hblur = Math.sin(Math.PI * clamp01((t - (s.end_s - tdo())) / tdo())) * 24;
      }
    }
  }
  return L;
};

/** an SVG filter for horizontal motion blur (CSS blur() is round; a whip only smears sideways) */
export const HBlur: React.FC<{ id: string; amount: number }> = ({ id, amount }) => (
  <svg style={{ position: "absolute", width: 0, height: 0 }} aria-hidden>
    <defs>
      <filter id={id} x="-20%" y="0%" width="140%" height="100%" colorInterpolationFilters="sRGB">
        <feGaussianBlur stdDeviation={`${amount.toFixed(1)} 0`} />
      </filter>
    </defs>
  </svg>
);

/** the CSS for a Look (motion blur through the matching HBlur filter id) */
export const lookStyle = (L: Look, filterId: string): React.CSSProperties => {
  const f: string[] = [];
  if (L.hblur > 0.6) f.push(`url(#${filterId})`);
  if (L.blur > 0.4) f.push(`blur(${L.blur.toFixed(1)}px)`);
  return {
    transform: `translateX(${L.tx.toFixed(1)}px) scale(${L.scale.toFixed(4)})`,
    filter: f.length ? f.join(" ") : undefined,
    opacity: L.opacity,
  };
};

/* ---------------- the band wipe ---------------- */
/** diagonal band wipe: the entry edge and the exit edge both sweep left. slant is the band's lean in px */
export function bandWipe(t: number, s: number, e: number, W: number, H: number, inD = 0.45, outD = 0.38) {
  const slant = W * 0.24;
  const p = INOUT(clamp01((t - s) / inD));
  const q = INOUT(clamp01((t - (e - outD)) / outD));
  const Lin = (1 - p) * (W + slant) - slant;
  const Lout = (1 - q) * (W + slant) - slant;
  const clip = `polygon(${Lin + slant}px 0px, ${Lout + slant}px 0px, ${Lout}px ${H}px, ${Lin}px ${H}px)`;
  const edge = p < 1 ? Lin : q > 0 ? Lout : null;
  return { p, q, clip, edge, slant };
}
export const BandEdge: React.FC<{
  x: number | null;
  slant: number;
  W: number;
  H: number;
}> = ({ x, slant, W, H }) =>
  x === null ? null : (
    <svg width={W} height={H} style={{ position: "absolute", inset: 0 }}>
      <line x1={x + slant} y1={0} x2={x} y2={H} stroke={C.accent} strokeWidth={10} />
    </svg>
  );
