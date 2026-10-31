/**
 * mark.tsx: the brand's mark on a video. A theme with a lockup file draws it: lockup_dark_src over footage and dark grounds,
 * lockup_src on a light ground (each falls back to the other when only one is given). A theme with no lockup file but an emblem
 * draws the emblem and its name in its own face, flat in the ink on a light ground and in the base colour over footage. Whether the
 * mark is drawn at all is the theme's lockup_on, or the edit's own `mark`.
 */
import React from "react";
import { Img } from "remotion";
import { BRAND, C, res } from "./theme";
import { SvgIcon } from "./icon";

/** is there a mark to draw on this ground? `light`: a light panel is covering the footage */
export const hasMark = (light: boolean): boolean =>
  BRAND.lockup
    ? !!(light ? (BRAND.lockup.light ?? BRAND.lockup.dark) : (BRAND.lockup.dark ?? BRAND.lockup.light))
    : !!BRAND.mark;

/** the mark, `h` px high. Call only when hasMark(light) */
export const BrandMark: React.FC<{ light: boolean; h: number }> = ({ light, h }) => {
  if (BRAND.lockup) {
    const src = light ? (BRAND.lockup.light ?? BRAND.lockup.dark) : (BRAND.lockup.dark ?? BRAND.lockup.light);
    return src ? <Img src={res(src)} style={{ height: h }} /> : null;
  }
  const m = BRAND.mark;
  if (!m) return null;
  const color = light ? C.ink : C.base;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: Math.round(h * 0.3),
        maxWidth: h * 26,
        whiteSpace: "nowrap",
      }}
    >
      {/\.svg$/i.test(m.emblem) ? (
        <SvgIcon src={m.emblem} size={h} color={color} />
      ) : (
        <Img
          src={res(m.emblem)}
          style={{
            height: h,
            filter: light ? "brightness(0)" : "brightness(0) invert(1)",
          }}
        />
      )}
      <span
        style={{
          fontFamily: BRAND.fontFamily,
          fontWeight: 800,
          fontSize: Math.round(h * 0.95),
          lineHeight: 1,
          letterSpacing: "-0.02em",
          color,
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        {BRAND.caps ? m.name.toUpperCase() : m.name}
      </span>
    </div>
  );
};
