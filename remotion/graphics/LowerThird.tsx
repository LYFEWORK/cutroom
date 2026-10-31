/**
 * LowerThird: a name on an accent chip with the role beside it, once. It sits in the lower-third slot (just above the platform's
 * bottom band), arrives on one long smooth ease-out with a little blur and rise, and leaves on a short fade.
 *
 * props: { name: string, role?: string }
 * Uses only theme colours: the accent for the name chip, the card colour for the role. A role that does not fit beside the name
 * wraps under it.
 */
import React from "react";
import { BRAND, C, appear } from "../theme";
import type { GraphicProps } from "../types";

export const LowerThird: React.FC<GraphicProps<{ name: string; role?: string }>> = ({
  props,
  start_s,
  end_s,
  t,
  layout: L,
}) => {
  const k = L.k;
  const skew = BRAND.skew;
  return (
    <div
      style={{
        position: "absolute",
        left: L.lower.left,
        bottom: L.h - L.lower.bottom,
        maxWidth: L.w - 2 * L.lower.left,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 10 * k,
        fontFamily: BRAND.fontFamily,
        ...appear(t, start_s, end_s, 0.8, 26 * k),
      }}
    >
      <div
        style={{
          background: C.accent,
          color: C.onAccent,
          fontWeight: 800,
          fontSize: 48 * k,
          letterSpacing: "-0.01em",
          lineHeight: 1.05,
          padding: `${6 * k}px ${22 * k}px ${8 * k}px`,
          borderRadius: skew ? 4 * k : 8 * k,
          transform: skew ? `skewX(${skew}deg)` : undefined,
          boxShadow: "0 10px 24px rgba(0,0,0,0.35)",
        }}
      >
        <span style={{ display: "inline-block", transform: skew ? `skewX(${-skew}deg)` : undefined }}>
          {props.name}
        </span>
      </div>
      {props.role ? (
        <div
          style={{
            background: C.card,
            color: C.base,
            fontWeight: 600,
            fontSize: 36 * k,
            letterSpacing: "0.01em",
            lineHeight: 1.1,
            padding: `${7 * k}px ${18 * k}px ${8 * k}px`,
            borderRadius: 8 * k,
            boxShadow: "0 8px 20px rgba(0,0,0,0.3)",
          }}
        >
          {props.role}
        </div>
      ) : null}
    </div>
  );
};
