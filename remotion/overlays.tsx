/**
 * overlays.tsx: the layers drawn over any picture: the hook card (and FitAbove, which keeps a top card clear of the speaker's
 * head), the word-page captions, the brand mark and the end card. Edit and Montage both use them.
 */
import React from "react";
import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig } from "remotion";
import {
  BRAND,
  C,
  SOLID,
  SETTLE,
  EASE_OUT,
  HOOK_LOOKS,
  condense,
  hookFamily,
  leave,
  tint,
  negTint,
  clamp01,
} from "./theme";
import { BrandMark, hasMark } from "./mark";
import type { Layout } from "./layout";
import type { EndCardSpec, Face, HookLook, HookSpec, Word } from "./types";

/** "*word*" -> the word in the accent color */
export function rich(text: string, accent: string = C.accent) {
  return String(text ?? "")
    .split(/(\*[^*]+\*)/g)
    .filter(Boolean)
    .map((p, i) =>
      p.startsWith("*") && p.endsWith("*") ? (
        <span key={i} style={{ color: accent }}>
          {p.slice(1, -1)}
        </span>
      ) : (
        <span key={i}>{p}</span>
      ),
    );
}

/** a theme number is a fraction of the frame (0..1) or, above 1, px in a 1080 x 1920 frame */
const along = (v: number, frame: number, ref: number) => (v <= 1 ? v * frame : (v / ref) * frame);

/* ---------------- head clearance ---------------- */
/** top of the head above the face center, in face heights (the face box is forehead to chin) */
export const K_CROWN = 0.76;

export type EditCtx = {
  layout: Layout;
  /** y of the top of the speaker's head at time t (Infinity when the edit carries no face data) */
  crownAt: (t: number) => number;
  /** the highest crown (smallest y) between two times */
  crownMin: (a: number, b: number) => number;
};
export const EditContext = React.createContext<EditCtx | null>(null);
export function useEditCtx(): EditCtx {
  const c = React.useContext(EditContext);
  if (!c) throw new Error("useEditCtx: no edit around this component");
  return c;
}

/** `zoom(t)` says how the picture is scaled at t and about which y: the crown moves with it, so a card fits the head as it plays */
export function makeCrown(
  face: Face | null | undefined,
  track: Record<string, Face> | null | undefined,
  L: Layout,
  zoom?: (t: number) => { z: number; oy: number },
): Pick<EditCtx, "crownAt" | "crownMin"> {
  const raw = (t: number) => {
    for (const d of [0, -1, 1, -2, 2]) {
      const v = track?.[String(Math.floor(t) + d)];
      if (v && v.h) return v.cy - K_CROWN * v.h;
    }
    if (face) return face.cy - K_CROWN * (face.h ?? L.h * 0.1875);
    return Infinity;
  };
  const crownAt = (t: number) => {
    const c = raw(t);
    if (!zoom || !Number.isFinite(c)) return c;
    const { z, oy } = zoom(t);
    return oy + (c - oy) * z;
  };
  const crownMin = (a: number, b: number) => {
    let m = Infinity;
    for (let x = a; x <= b + 1e-6; x += 0.5) m = Math.min(m, crownAt(x));
    return m;
  };
  return { crownAt, crownMin };
}

/** A top-zone card measures its own box and shrinks from its top edge until it ends above `maxBottom` (the head), down to 0.72.
 *  If it still ends below that line it slides up, as far as `minTop` (the platform's top band). A card that starts below that line
 *  is placed on purpose (under the chin, say) and is left alone. Layout size ignores transforms, so an entrance animation never
 *  changes the fit. The first child must be the card's absolutely positioned root. */
export const FitAbove: React.FC<{
  maxBottom: number;
  minTop: number;
  width: number;
  children: React.ReactNode;
}> = ({ maxBottom, minTop, width, children }) => {
  const ref = React.useRef<HTMLDivElement>(null);
  const [box, setBox] = React.useState<{ top: number; h: number } | null>(null);
  React.useLayoutEffect(() => {
    const el = ref.current?.firstElementChild as HTMLElement | null;
    if (!el) return;
    if (!box || box.top !== el.offsetTop || box.h !== el.offsetHeight)
      setBox({ top: el.offsetTop, h: el.offsetHeight });
  });
  let s = 1,
    dy = 0;
  if (box && Number.isFinite(maxBottom) && box.top < maxBottom && box.top + box.h > maxBottom) {
    s = Math.max(0.72, (maxBottom - box.top) / box.h);
    const over = box.top + box.h * s - maxBottom;
    if (over > 0) dy = -Math.min(over, Math.max(0, box.top - minTop));
  }
  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        inset: 0,
        transform: s < 1 || dy ? `translateY(${dy.toFixed(2)}px) scale(${s.toFixed(4)})` : undefined,
        transformOrigin: `${width / 2}px ${box ? box.top : 290}px`,
      }}
    >
      {children}
    </div>
  );
};

/* ---------------- hook card ---------------- */
const hookLines = (hook: HookSpec): string[] =>
  (hook.lines && hook.lines.length ? hook.lines : hook.text ? [hook.text] : []).map((l) =>
    typeof l === "string" ? l : l.text,
  );
export const hookLookOf = (hook: HookSpec): HookLook =>
  HOOK_LOOKS.includes(hook.look as HookLook) ? (hook.look as HookLook) : BRAND.hookLook;
export const hookEnd = (hook: HookSpec) => hook.end_s ?? 4.5;
export const hookTopOf = (hook: HookSpec, L: Layout) =>
  typeof hook.top === "number" ? Math.max(along(hook.top, L.h, 1920), L.hook.minTop) : L.hook.top;

/**
 * The text hook, on screen fully formed from frame 0 and holding to end_s. One look in one place: the theme's hook_look, hook_top
 * and hook_size, unless the hook names its own. The looks:
 *   card     light card (the base color), ink type, accent key words
 *   accent   accent card, on-accent type, ink key words
 *   ink      solid dark card (the theme's surface), base type, accent key words
 *   bigtype  no card: big base-color type straight over the picture on a soft top shade, accent key words
 *   bar      no card: base-color type, the key words sit on an accent highlight bar
 *   mono     no card: big type like bigtype; on turn_s the key words flip to the negative color with a glow and the picture drops
 *            to black and white (Edit draws that part) until the hook leaves
 */
export const Hook: React.FC<{ hook: HookSpec; t: number; L: Layout }> = ({ hook, t, L }) => {
  const end = hookEnd(hook);
  if (t > end + 0.01) return null;
  const look = hookLookOf(hook);
  const lines = hookLines(hook);
  if (!lines.length) return null;
  const k = L.k;
  const longest = Math.max(1, ...lines.map((l) => l.replace(/\*/g, "").length));
  const open = look === "bigtype" || look === "bar" || look === "mono";
  const turned = look === "mono" && t >= (hook.turn_s ?? 0);
  const sizeRaw = typeof hook.size === "number" ? hook.size : BRAND.hookSize;
  const own = sizeRaw == null ? null : sizeRaw <= 1 ? sizeRaw * Math.min(L.w, L.h) : sizeRaw * k;
  const fs =
    own ??
    Math.min(
      (open ? 96 : 80) * k,
      Math.floor(((open ? 0.9375 : 0.906) * L.hook.maxW) / ((BRAND.caps ? 0.66 : 0.56) * longest)),
    );
  const out = leave(t, end, 0.28, 14 * k);
  const color = look === "card" ? C.ink : look === "accent" ? C.onAccent : C.base;
  const accent = look === "accent" ? C.ink : look === "mono" ? (turned ? C.negative : C.base) : C.accent;
  const card: React.CSSProperties =
    look === "card"
      ? { background: C.base, boxShadow: "0 24px 70px rgba(0,0,0,0.38)" }
      : look === "accent"
        ? { background: C.accent, boxShadow: `0 24px 70px ${tint(0.35)}` }
        : look === "ink"
          ? { ...SOLID }
          : {};
  const family = hookFamily(look);
  const ownFace = family !== BRAND.fontFamily; // a face of its own (the running face, the mono face) draws a little lighter and looser
  const keyed = (l: string) =>
    String(l ?? "")
      .split(/(\*[^*]+\*)/g)
      .filter(Boolean)
      .map((p, i) => {
        const key = p.startsWith("*") && p.endsWith("*");
        const txt = key ? p.slice(1, -1) : p;
        if (!key) return <span key={i}>{txt}</span>;
        if (look === "bar")
          return (
            <span
              key={i}
              style={{
                display: "inline-block",
                background: C.accent,
                color: C.onAccent,
                textShadow: "none",
                padding: BRAND.skew ? "0.02em 0.2em 0" : "0 0.18em",
                margin: BRAND.skew ? "0 0.08em" : undefined,
                borderRadius: BRAND.skew ? "0.05em" : "0.12em",
                transform: BRAND.skew ? `skewX(${BRAND.skew}deg)` : undefined,
              }}
            >
              <span
                style={{
                  display: "inline-block",
                  transform: BRAND.skew ? `skewX(${-BRAND.skew}deg)` : undefined,
                }}
              >
                {txt}
              </span>
            </span>
          );
        return (
          <span
            key={i}
            style={{
              color: accent,
              textShadow:
                look === "mono" && turned ? `0 0 26px ${negTint(0.6)}, 0 4px 24px rgba(0,0,0,0.65)` : undefined,
            }}
          >
            {txt}
          </span>
        );
      });
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: hookTopOf(hook, L),
        display: "flex",
        justifyContent: "center",
        ...out.style,
      }}
    >
      {open ? (
        <div
          style={{
            position: "absolute",
            left: -40 * k,
            right: -40 * k,
            top: -120 * k,
            height: 620 * k,
            background: "radial-gradient(70% 60% at 50% 45%, rgba(0,0,0,0.55), rgba(0,0,0,0) 72%)",
          }}
        />
      ) : null}
      <div
        style={{
          position: "relative",
          maxWidth: L.hook.maxW,
          padding: open ? `0 ${60 * k}px` : `${30 * k}px ${40 * k}px ${34 * k}px`,
          borderRadius: 26 * k,
          ...card,
        }}
      >
        {lines.map((l, i) => (
          <div
            key={i}
            style={{
              fontFamily: family,
              fontWeight: ownFace ? 700 : 800,
              fontSize: fs,
              lineHeight: look === "bar" ? 1.16 : ownFace ? 1.14 : 1.04,
              letterSpacing: ownFace ? "-0.01em" : "-0.035em",
              color,
              textAlign: "center",
              textShadow: open ? "0 4px 24px rgba(0,0,0,0.65), 0 2px 4px rgba(0,0,0,0.5)" : "none",
            }}
          >
            {keyed(l)}
          </div>
        ))}
      </div>
    </div>
  );
};

/* ---------------- captions ---------------- */
const SMALL = new Set(
  "the a an to of and or is it its in on at your you we if so but with into that this for by be are was i i'm my our".split(
    " ",
  ),
);
export type Page = { words: Word[]; start_s: number; end_s: number };

/** words -> pages of 1 to maxWords: a page breaks at a pause over maxGapMs, after a sentence end, or when it is full */
export function pages(words: Word[], maxWords = 3, maxGapMs = 420): Page[] {
  const out: Page[] = [];
  let cur: Word[] = [];
  const flush = () => {
    if (cur.length)
      out.push({
        words: cur,
        start_s: cur[0].start_s,
        end_s: cur[cur.length - 1].end_s,
      });
    cur = [];
  };
  for (const w of words) {
    const prev = cur[cur.length - 1];
    const gap = prev ? (w.start_s - prev.end_s) * 1000 : 0;
    const sentence = prev && /[.!?,]$/.test(prev.text);
    if (cur.length >= maxWords || (prev && gap > maxGapMs) || (sentence && cur.length >= 1)) flush();
    cur.push(w);
  }
  flush();
  return out;
}

/** the look knobs of the simple caption pages (the DNA's caption style `feed` block); every key is optional */
export type FeedSpec = {
  /** where the caption box sits: the theme's caption_top (chest), just above the bottom band, or under the top band */
  place?: "chest" | "lower" | "top";
  /** a scale on the theme's caption size (0.7 to 1.5) */
  size?: number;
  /** words a page (1 to 3, never more than the theme's cap_words) */
  words?: number;
  /** how a word arrives: reveal (a quick pop), quiet (a plain fade), soft (a fade out of a little blur) */
  motion?: "reveal" | "quiet" | "soft";
  /** the key words: their look, how much bigger they are (1.3 to 2.8 for the bigger looks), and which word a page puts it on */
  hero?: { look?: "color" | "bigger" | "bigger_bold"; scale?: number; pick?: "accent" | "page" };
};

/** one caption page: its words arrive as they are spoken, at the caption box (or beside the picture-in-picture box while one is up) */
const CapPage: React.FC<{
  page: Page;
  from: number;
  L: Layout;
  bandAt: (t: number) => boolean;
  feed?: FeedSpec | null;
}> = ({ page, from, L, bandAt, feed }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const now = (from + frame) / fps;
  const band = bandAt(now);
  const k = L.k;
  const base = L.caption.size * (feed?.size ?? 1) * (band ? 0.84 : 1);
  const top =
    feed?.place === "lower"
      ? L.h - L.safe.bottom - L.caption.h - 30 * k
      : feed?.place === "top"
        ? L.safe.top + 60 * k
        : L.caption.top;
  const motion = feed?.motion ?? "reveal";
  const hero = {
    look: feed?.hero?.look ?? "color",
    scale: feed?.hero?.scale ?? 1.12,
    pick: feed?.hero?.pick ?? "accent",
  };
  // the page's own key word, when the style picks one a page: its longest word that is not a small one
  const longest = page.words.reduce(
    (best, w, i) => {
      const n = w.text.replace(/[^A-Za-z0-9']/g, "").length;
      return !SMALL.has(w.text.toLowerCase().replace(/[^a-z']/g, "")) && n > best.n ? { i, n } : best;
    },
    { i: -1, n: 0 },
  ).i;
  return (
    <div
      style={{
        position: "absolute",
        left: band ? L.pip.x + L.pip.w + 24 * k : 0,
        right: band ? L.caption.padR : 0,
        top: band ? L.pip.y : top,
        height: band ? L.pip.h : L.caption.h,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: band ? 0 : `0 ${L.caption.padR}px 0 ${L.caption.padL}px`,
      }}
    >
      <div
        style={{
          fontFamily: BRAND.fontFamily,
          fontWeight: 800,
          lineHeight: 1.04,
          letterSpacing: "-0.022em",
          textAlign: "center",
          color: C.base,
          textShadow: "0 3px 16px rgba(0,0,0,0.62), 0 1px 3px rgba(0,0,0,0.7)",
          WebkitTextStroke: BRAND.stroke ? `${BRAND.stroke * k}px ${C.ink}` : undefined,
          paintOrder: "stroke fill",
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          alignItems: "baseline",
          columnGap: 20 * k,
          rowGap: 2 * k,
        }}
      >
        {page.words.map((w, i) => {
          const key = w.text.toLowerCase().replace(/[^a-z']/g, "");
          const isHero = hero.pick === "page" ? i === longest && !w.neg : !!w.accent;
          const mult = w.neg ? 1.16 : isHero ? hero.scale : SMALL.has(key) ? 0.84 : 1;
          const d = motion === "reveal" ? 0.11 : motion === "quiet" ? 0.2 : 0.35;
          const a = (motion === "soft" ? EASE_OUT : SETTLE)(clamp01((now - (w.start_s - 0.03)) / d));
          if (a <= 0) return null;
          return (
            <span
              key={i}
              style={{
                display: "inline-block",
                fontSize: base * mult,
                fontWeight: isHero && hero.look === "bigger_bold" ? 900 : undefined,
                color: w.neg ? C.negative : isHero ? C.accent : undefined,
                textShadow: w.neg
                  ? `0 0 22px ${negTint(0.55)}, 0 3px 16px rgba(0,0,0,0.62), 0 1px 3px rgba(0,0,0,0.7)`
                  : undefined,
                opacity: a,
                filter:
                  motion === "quiet" || a >= 1
                    ? undefined
                    : `blur(${((1 - a) * (motion === "soft" ? 6 : 3)).toFixed(2)}px)`,
                transform:
                  motion === "reveal"
                    ? `translateY(${((1 - a) * 6 * k).toFixed(2)}px) scale(${(1.07 - 0.07 * a).toFixed(4)})`
                    : undefined,
                transformOrigin: "0% 80%",
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

/** every caption page, each held until the next one starts (or 0.4 s after its last word) */
export const Captions: React.FC<{
  words: Word[];
  L: Layout;
  bandAt: (t: number) => boolean;
  feed?: FeedSpec | null;
  /** pages that start inside one of these windows are left out */
  hidden?: [number, number][];
}> = ({ words, L, bandAt, feed, hidden = [] }) => {
  const { fps } = useVideoConfig();
  const perPage = Math.min(Math.max(1, Math.round(feed?.words ?? BRAND.capWords)), BRAND.capWords);
  const pg = React.useMemo(() => pages(words, perPage), [words, perPage]);
  return (
    <>
      {pg.map((p, i) => {
        if (hidden.some(([a, b]) => p.start_s >= a - 0.15 && p.start_s <= b)) return null;
        const from = Math.floor(p.start_s * fps);
        const until = pg[i + 1] ? pg[i + 1].start_s : p.end_s + 0.4;
        return (
          <Sequence key={i} from={from} durationInFrames={Math.max(1, Math.floor(until * fps) - from)}>
            <CapPage page={p} from={from} L={L} bandAt={bandAt} feed={feed} />
          </Sequence>
        );
      })}
    </>
  );
};

/* ---------------- brand mark ---------------- */
/** the mark under the platform's top band (nothing when the theme has none or the edit turned it off) */
export const MarkLayer: React.FC<{
  on: boolean | null | undefined;
  L: Layout;
}> = ({ on, L }) => {
  const want = on ?? BRAND.markOn;
  if (!want || !hasMark(false)) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: L.mark.left,
        top: L.mark.top,
        height: Math.round(L.mark.h * 1.13),
        display: "flex",
        alignItems: "center",
        filter: "drop-shadow(0 2px 8px rgba(0,0,0,0.55))",
      }}
    >
      <BrandMark light={false} h={L.mark.h} />
    </div>
  );
};

/* ---------------- end card ---------------- */
/** a full-frame card in the theme's colors from at_s to the end: the mark (when there is one), the line in big type and the
 *  handle in the accent. The card fades up and each part rises in after the one before, on one long ease-out. */
export const EndCard: React.FC<{ spec: EndCardSpec; t: number; L: Layout }> = ({ spec, t, L }) => {
  const k = L.k;
  const line = spec.line ?? BRAND.endCard?.line ?? "";
  const handle = spec.handle ?? BRAND.endCard?.handle ?? "";
  const at = spec.at_s;
  const bg = EASE_OUT(clamp01((t - at) / 0.5));
  const longest = Math.max(1, ...line.split("\n").map((s) => s.replace(/\*/g, "").length));
  const textW = L.w - 2 * Math.max(L.caption.padL, L.caption.padR);
  const lineSize = Math.min(96 * k, Math.floor(textW / ((BRAND.caps ? 0.66 : 0.56) * longest)));
  const showMark = BRAND.markOn && hasMark(false);
  return (
    <AbsoluteFill style={{ backgroundColor: C.bg, opacity: bg }}>
      <AbsoluteFill
        style={{
          background: `radial-gradient(60% 38% at 50% 44%, ${tint(0.18)}, rgba(0,0,0,0) 72%)`,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: 0,
          bottom: L.safe.bottom,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 40 * k,
          padding: `0 ${Math.max(L.caption.padL, L.caption.padR)}px`,
        }}
      >
        {showMark ? (
          <div style={{ ...condense(t, at + 0.15, 0.8, 24 * k).style }}>
            <BrandMark light={false} h={Math.round(64 * k)} />
          </div>
        ) : null}
        {line
          ? line.split("\n").map((l, i) => (
              <div
                key={i}
                style={{
                  ...condense(t, at + 0.3 + i * 0.08, 0.8, 24 * k).style,
                  fontFamily: BRAND.fontFamily,
                  fontWeight: 800,
                  fontSize: lineSize,
                  lineHeight: 1.06,
                  letterSpacing: "-0.03em",
                  textAlign: "center",
                  color: C.base,
                  marginTop: i ? -28 * k : 0,
                }}
              >
                {rich(l)}
              </div>
            ))
          : null}
        {handle ? (
          <div
            style={{
              ...condense(t, at + 0.5, 0.8, 24 * k).style,
              fontFamily: BRAND.fontFamily,
              fontWeight: 700,
              fontSize: 46 * k,
              letterSpacing: "0.01em",
              color: C.accent,
              textAlign: "center",
            }}
          >
            {handle}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
