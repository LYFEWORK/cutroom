/**
 * Edit.tsx: plays ONE cut file and lays the edit over it.
 *
 * Layers, bottom to top: the cut (with eased emphasis zooms) -> inserts (b-roll: full, window or pip) -> the speaker's
 * picture-in-picture box while a pip insert holds -> the graphics slots -> the hook card -> the brand mark -> caption pages -> the
 * end card. Audio is the cut's own track; music and sound effects are mixed afterwards by engine/render.py.
 *
 * When the end card runs past the cut (render.py sets `holdFrom`), the picture holds the cut's last frame from the frame after `holdFrom`
 * on, so the card fades up over a still of the speaker instead of black, and never has to sit over the speaker's words.
 *
 * Everything that sits somewhere comes from layout.ts (fractions of the frame, scaled by the short side), so the same props work
 * at 1080x1920, 1920x1080 and 1080x1350. See README.md for the props.
 */
import React from "react";
import { AbsoluteFill, Freeze, OffthreadVideo, Sequence, useCurrentFrame, useVideoConfig } from "remotion";
import { BRAND, INOUT, OUT, SETTLE, SMOOTH_INOUT, applyTheme, clamp01, env, res, useFontsReady } from "./theme";
import { useLayout } from "./layout";
import type { Layout } from "./layout";
import {
  BandEdge,
  HBlur,
  baseLook,
  bandWipe,
  brollLook,
  brollPlan,
  brollUntil,
  lookStyle,
  isDynamic,
  resolveInserts,
} from "./trans";
import type { PlanEntry, RInsert } from "./trans";
import { Captions, EditContext, EndCard, FitAbove, Hook, MarkLayer, hookEnd, hookLookOf, makeCrown } from "./overlays";
import { GRAPHICS } from "./graphics";
import type { EditProps, Zoom } from "./types";

/* ---------------- the picture ---------------- */
/** the zoom in force at time t: its scale, its own pivot (if it names one) and how far in it is (0..1) */
function emphAt(t: number, zooms: Zoom[]) {
  for (const z of zooms) {
    if (t < z.start_s || t > z.end_s) continue;
    const span = Math.max(0.01, z.end_s - z.start_s);
    if (BRAND.smooth) {
      // at least 0.8 s in and out on a curve with no snap in its middle; while it holds it keeps creeping in, so the camera never sits dead
      const e = Math.min(Math.max(z.ease_s ?? 0.4, 0.8), span / 2);
      const k = Math.min(SMOOTH_INOUT(Math.min(1, (t - z.start_s) / e)), SMOOTH_INOUT(Math.min(1, (z.end_s - t) / e)));
      const drift = (z.z - 1) * 0.25 * Math.min(1, (t - z.start_s) / span);
      return { z: 1 + (z.z - 1 + drift) * k, cx: z.cx, cy: z.cy, k };
    }
    const e = Math.min(z.ease_s ?? 0.4, span / 2);
    const k = Math.min(INOUT(Math.min(1, (t - z.start_s) / e)), INOUT(Math.min(1, (z.end_s - t) / e)));
    return { z: 1 + (z.z - 1) * k, cx: z.cx, cy: z.cy, k };
  }
  return { z: 1, cx: undefined as number | undefined, cy: undefined as number | undefined, k: 0 };
}

/* ---------------- inserts ---------------- */
/** an insert that floats over the speaker in a rounded window, on the side away from the face unless it names one */
const InsertWindow: React.FC<{ o: RInsert; t: number; fps: number; L: Layout; autoSide: "left" | "right" }> = ({
  o,
  t,
  fps,
  L,
  autoSide,
}) => {
  const e = env(t, o.start_s, o.end_s, 0.5, 0.28);
  if (!e.on) return null;
  const k = L.k;
  const right = (o.side ?? autoSide) !== "left";
  const w = (o.w ?? 320) * k,
    h = Math.round(w * (4 / 3));
  const left = right ? L.w - L.window.margin - w : L.window.margin;
  const life = Math.max(0.01, o.end_s - o.start_s),
    u = clamp01((t - o.start_s) / life);
  const dy = ((1 - e.kin) * 46 - u * 10 - e.kout * 18) * k;
  const sc = (0.92 + 0.08 * e.kin) * (1 + 0.02 * u) * (1 - 0.03 * e.kout);
  const rot = (1 - e.kin) * (right ? 2.2 : -2.2);
  return (
    <div
      style={{
        position: "absolute",
        left,
        top: L.window.top,
        width: w,
        height: h,
        borderRadius: 28 * k,
        overflow: "hidden",
        border: `${3 * k}px solid rgba(255,255,255,0.88)`,
        boxShadow: "0 30px 70px rgba(0,0,0,0.38), 0 6px 18px rgba(0,0,0,0.22)",
        background: "#000",
        opacity: e.kin * (1 - e.kout),
        filter: `blur(${((1 - e.kin) * 8 + e.kout * 6).toFixed(2)}px)`,
        transform: `translateY(${dy.toFixed(2)}px) rotate(${rot.toFixed(3)}deg) scale(${sc.toFixed(4)})`,
        transformOrigin: right ? "70% 100%" : "30% 100%",
      }}
    >
      <OffthreadVideo
        src={res(o.src)}
        muted
        trimBefore={Math.round((o.from_s ?? 0) * fps)}
        playbackRate={o.rate ?? 1}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${(1.04 + 0.04 * u).toFixed(4)})`,
        }}
      />
    </div>
  );
};

/** an insert that fills the frame (mode full, or pip while the speaker shows in a box) with its transition */
const InsertScene: React.FC<{ o: RInsert; t: number; fps: number; plan: PlanEntry[]; L: Layout }> = ({
  o,
  t,
  fps,
  plan,
  L,
}) => {
  const pe = plan.find((p) => p.s === o);
  const until = pe ? brollUntil(pe) : o.end_s;
  if (t < o.start_s - 0.01 || t > until + 0.01) return null;
  const tl = pe && isDynamic(o.kind) ? brollLook(pe, t, L.w) : null;
  const fid = `cr-in-${Math.round(o.start_s * 100)}`;
  const band = o.kind === "band" ? bandWipe(t, o.start_s, o.end_s, L.w, L.h, 0.45, 0.38) : null;
  // a clean cut is a 4-frame dissolve each way, unless the insert meets another one
  const dis =
    o.kind === "cut" ? Math.min(1, pe?.joinIn ? 1 : (t - o.start_s) / 0.13, pe?.joinOut ? 1 : (o.end_s - t) / 0.13) : 1;
  const z = (1.02 + Math.min(0.06, Math.max(0, t - o.start_s) * 0.018)) * (o.zoom ?? 1);
  return (
    <>
      {tl ? <HBlur id={fid} amount={tl.hblur} /> : null}
      <div
        style={{
          position: "absolute",
          inset: 0,
          clipPath: band?.clip,
          background: "#000",
          overflow: "hidden",
          opacity: Math.max(0, dis),
          ...(tl ? lookStyle(tl, fid) : {}),
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            transform: `scale(${z.toFixed(4)})${band ? ` translateX(${((1 - band.p) * 70 - band.q * 70).toFixed(2)}px)` : ""}`,
            transformOrigin: o.origin ?? "50% 50%",
          }}
        >
          <OffthreadVideo
            src={res(o.src)}
            muted
            trimBefore={Math.round((o.from_s ?? 0) * fps)}
            playbackRate={o.rate ?? 1}
            style={{ width: L.w, height: L.h, objectFit: "cover" }}
          />
        </div>
      </div>
      {band ? <BandEdge x={band.edge} slant={band.slant} W={L.w} H={L.h} /> : null}
    </>
  );
};

/** the speaker in a small box while a pip insert fills the frame: a window of the cut around the face */
const Pip: React.FC<{ src: string; vis: number; face: { cx: number; cy: number }; L: Layout }> = ({
  src,
  vis,
  face,
  L,
}) => {
  if (vis <= 0.001) return null;
  const s = 0.5; // the box shows a window twice its size, around the face
  const tx = L.pip.w / 2 - face.cx * s,
    ty = L.pip.h * 0.46 - face.cy * s;
  return (
    <div
      style={{
        position: "absolute",
        left: L.pip.x,
        top: L.pip.y,
        width: L.pip.w,
        height: L.pip.h,
        borderRadius: 30 * L.k,
        overflow: "hidden",
        border: `${4 * L.k}px solid #fff`,
        boxShadow: "0 18px 50px rgba(0,0,0,0.38)",
        background: "#000",
        opacity: vis,
        transform: `scale(${(0.88 + 0.12 * vis).toFixed(4)})`,
        transformOrigin: "0% 100%",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: L.w,
          height: L.h,
          transform: `translate(${tx.toFixed(2)}px, ${ty.toFixed(2)}px) scale(${s})`,
          transformOrigin: "0 0",
        }}
      >
        <OffthreadVideo src={res(src)} muted style={{ width: L.w, height: L.h, objectFit: "cover" }} />
      </div>
    </div>
  );
};

/* ---------------- the composition ---------------- */
const EditBody: React.FC<EditProps> = ({
  src,
  words = [],
  captions_mode = "feed",
  feed = null,
  zooms = [],
  inserts = [],
  hook = null,
  face = null,
  track = null,
  graphics = [],
  mark = null,
  endCard = null,
  safe = null,
  holdFrom = null,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const L = useLayout(safe);
  const t = frame / fps;
  const fc = face ?? { cx: L.w / 2, cy: L.h / 2 };

  const em = emphAt(t, zooms);
  const ox = em.cx != null ? fc.cx + (em.cx - fc.cx) * em.k : fc.cx;
  const oy = em.cy != null ? fc.cy + (em.cy - fc.cy) * em.k : fc.cy;

  const rins = React.useMemo(() => resolveInserts(inserts), [inserts]);
  const plan = React.useMemo(() => brollPlan(rins), [rins]);
  const bl = plan.some((p) => isDynamic(p.kind)) ? baseLook(plan, t, L.w) : null;

  // the picture-in-picture box: in after the pip insert has settled (0.3 s), out 0.5 s before it ends
  const pips = rins.filter((s) => s.mode === "pip");
  const pipAt = (tt: number) =>
    pips.reduce(
      (m, s) =>
        tt < s.start_s || tt > s.end_s
          ? m
          : Math.max(
              m,
              Math.min(SETTLE(clamp01((tt - s.start_s - 0.3) / 0.3)), 1 - OUT(clamp01((tt - (s.end_s - 0.5)) / 0.25))),
            ),
      0,
    );
  const bandAt = (tt: number) => pips.some((s) => tt >= s.start_s + 0.3 && tt <= s.end_s - 0.3);

  // the mono hook look: from turn_s until the hook leaves the picture drops to black and white
  const look = hook ? hookLookOf(hook) : BRAND.hookLook;
  const monoOn = !!hook && look === "mono" && hook.turn_s != null && t >= hook.turn_s && t <= hookEnd(hook);

  // the zoom moves the head: the crown the cards fit above is read off the picture that plays
  const crown = makeCrown(face, track, L, (tt) => {
    const e = emphAt(tt, zooms);
    return { z: e.z, oy: e.cy != null ? fc.cy + (e.cy - fc.cy) * e.k : fc.cy };
  });
  const hidden: [number, number][] = graphics.filter((g) => g.captions === "hide").map((g) => [g.start_s, g.end_s]);

  return (
    <EditContext.Provider value={{ layout: L, ...crown }}>
      <AbsoluteFill style={{ backgroundColor: "#000", textTransform: BRAND.caps ? "uppercase" : undefined }}>
        {bl ? <HBlur id="cr-base" amount={bl.hblur} /> : null}
        <div
          style={{
            position: "absolute",
            inset: 0,
            ...(bl ? { ...lookStyle(bl, "cr-base"), transformOrigin: `${fc.cx}px ${fc.cy}px` } : {}),
          }}
        >
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: L.w,
              height: L.h,
              transform: `scale(${em.z.toFixed(4)})`,
              transformOrigin: `${ox}px ${oy}px`,
              filter: monoOn ? "grayscale(1) contrast(1.3) brightness(0.92)" : undefined,
            }}
          >
            {holdFrom != null ? (
              <Freeze frame={holdFrom} active={(f) => f > holdFrom}>
                <OffthreadVideo src={res(src)} style={{ width: L.w, height: L.h, objectFit: "cover" }} />
              </Freeze>
            ) : (
              <OffthreadVideo src={res(src)} style={{ width: L.w, height: L.h, objectFit: "cover" }} />
            )}
          </div>
        </div>
        {monoOn ? (
          <AbsoluteFill
            style={{
              background: "radial-gradient(ellipse 82% 64% at 50% 44%, rgba(0,0,0,0) 46%, rgba(0,0,0,0.62) 100%)",
            }}
          />
        ) : null}

        {rins.map((s, i) => {
          const pe = plan.find((p) => p.s === s);
          const until = pe ? brollUntil(pe) : s.end_s;
          const from = Math.max(0, Math.floor(s.start_s * fps));
          const dur = Math.max(1, Math.ceil((until - s.start_s) * fps) + 1);
          return (
            <Sequence key={i} from={from} durationInFrames={dur} layout="none">
              {s.mode === "window" ? (
                <InsertWindow o={s} t={t} fps={fps} L={L} autoSide={fc.cx < L.w / 2 ? "right" : "left"} />
              ) : (
                <InsertScene o={s} t={t} fps={fps} plan={plan} L={L} />
              )}
            </Sequence>
          );
        })}
        <Pip src={src} vis={pipAt(t)} face={fc} L={L} />

        {graphics.map((g, i) => {
          const Comp = GRAPHICS[g.component];
          if (!Comp)
            throw new Error(
              `graphics: no component "${g.component}" in remotion/graphics/index.ts (registered: ${Object.keys(GRAPHICS).join(", ")})`,
            );
          const from = Math.max(0, Math.floor(g.start_s * fps));
          const dur = Math.max(1, Math.ceil((g.end_s - g.start_s) * fps) + 1);
          return (
            <Sequence key={i} from={from} durationInFrames={dur} layout="none">
              <Comp props={g.props ?? {}} start_s={g.start_s} end_s={g.end_s} t={t} layout={L} />
            </Sequence>
          );
        })}

        {hook ? (
          <FitAbove maxBottom={crown.crownMin(0, hookEnd(hook)) - L.headGap} minTop={L.hook.minTop} width={L.w}>
            <Hook hook={hook} t={t} L={L} />
          </FitAbove>
        ) : null}
        <MarkLayer on={mark} L={L} />
        {captions_mode === "feed" && words.length ? (
          <Captions words={words} L={L} bandAt={bandAt} feed={feed} hidden={hidden} />
        ) : null}
        {endCard ? (
          <Sequence from={Math.max(0, Math.round(endCard.at_s * fps))} layout="none">
            <EndCard spec={endCard} t={t} L={L} />
          </Sequence>
        ) : null}
      </AbsoluteFill>
    </EditContext.Provider>
  );
};

export const Edit: React.FC<EditProps> = (props) => {
  applyTheme(props.theme);
  const ready = useFontsReady();
  return ready ? <EditBody {...props} /> : <AbsoluteFill />;
};
