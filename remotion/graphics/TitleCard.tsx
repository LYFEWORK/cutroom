/**
 * TitleCard: one line of big type on a card. It arrives on one long smooth ease-out with a little blur and rise (no spring, no
 * overshoot) and leaves on a short fade.
 *
 * props: { text: string, place?: "top" | "low" }   "*word*" puts a word in the accent color
 * Placement: "top" is the top zone above the speaker's head (FitAbove keeps it clear of the head); "low" is the caption zone below
 * the chin. With no place given it picks "top" when the head leaves the room it needs and "low" when it does not (a close-up).
 * The type never drops under 52 px (a line that needs less wraps instead).
 * Uses only theme colors: the theme's card surface, the base color for the type, the accent for the bar and key words.
 */
import React from "react";
import { BRAND, C, SOLID, appear } from "../theme";
import { FitAbove, rich, useEditCtx } from "../overlays";
import type { GraphicProps } from "../types";

export const TitleCard: React.FC<GraphicProps<{ text: string; place?: "top" | "low" }>> = ({
  props,
  start_s,
  end_s,
  t,
  layout: L,
}) => {
  const { crownMin } = useEditCtx();
  const k = L.k;
  const text = String(props.text ?? "");
  const chars = Math.max(1, text.replace(/\*/g, "").length);
  const charW = BRAND.caps ? 0.66 : 0.56; // an average glyph, in em
  const textW = L.hook.maxW - 2 * 44 * k - 12 * k;
  const size = Math.max(52 * k, Math.min(84 * k, Math.floor(textW / (charW * chars))));
  const lines = Math.max(1, Math.ceil((chars * charW * size) / textW));
  const maxBottom = crownMin(start_s, end_s) - L.headGap;
  const need = (size * 1.04 * lines + 64 * k) * 0.85; // a card that has to shrink under 85% is better placed below the chin
  const place = props.place ?? (maxBottom - L.hook.minTop >= need ? "top" : "low");
  const card = (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: place === "top" ? L.hook.top : L.caption.top,
        display: "flex",
        justifyContent: "center",
        ...appear(t, start_s, end_s, 0.8, 30 * k),
      }}
    >
      <div
        style={{
          position: "relative",
          maxWidth: L.hook.maxW,
          padding: `${30 * k}px ${44 * k}px ${34 * k}px ${56 * k}px`,
          borderRadius: 26 * k,
          ...SOLID,
        }}
      >
        <div
          style={{
            position: "absolute",
            left: 22 * k,
            top: 26 * k,
            bottom: 26 * k,
            width: 8 * k,
            borderRadius: 4 * k,
            background: C.accent,
          }}
        />
        <div
          style={{
            fontFamily: BRAND.fontFamily,
            fontWeight: 800,
            fontSize: size,
            lineHeight: 1.04,
            letterSpacing: "-0.035em",
            color: C.base,
            textAlign: "left",
          }}
        >
          {rich(text)}
        </div>
      </div>
    </div>
  );
  return place === "top" ? (
    <FitAbove maxBottom={maxBottom} minTop={L.hook.minTop} width={L.w}>
      {card}
    </FitAbove>
  ) : (
    card
  );
};
