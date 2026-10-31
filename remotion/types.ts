/**
 * types.ts: the shapes the compositions read. Times are seconds on the composition's own clock.
 * Frame positions (face, track) are pixels in the OUTPUT frame; engine/render.py converts them from the cut's frame.
 */
import type React from "react";
import type { Layout } from "./layout";
import type { FeedSpec } from "./overlays";

export type HookLook = "card" | "accent" | "ink" | "bigtype" | "bar" | "mono";
export type Transition = "cut" | "whip-l" | "whip-r" | "zoom" | "soft" | "band";
export type InsertMode = "full" | "window" | "pip";
export type CaptionsMode = "feed" | "none" | "external";

export type FontFaceSpec = { weight?: string | number; src: string };

/** the design theme (the DNA's `theme` dict). Only `id` and `colors.accent` are required; everything else has a default. */
export type Theme = {
  id: string;
  name?: string;
  colors: {
    accent: string;
    ink?: string;
    base?: string;
    on_accent?: string;
    negative?: string;
    bg?: string;
    card?: string;
  };
  surface?: { background?: string; border?: string } | null;
  /** the display face: its files, the name they are registered under, and a CSS fallback list */
  font_family?: string | null;
  font_faces?: FontFaceSpec[] | null;
  font_fallback?: string | null;
  /** a second (running) face and a mono face, each like the display one; either may be absent */
  running_family?: string | null;
  running_faces?: FontFaceSpec[] | null;
  mono_family?: string | null;
  mono_faces?: FontFaceSpec[] | null;
  caps?: boolean;
  skew?: number;
  cap_words?: number;
  caption_size?: number | null;
  caption_top?: number | null;
  caption_stroke?: number | null;
  hook_look?: HookLook | null;
  hook_top?: number | null;
  hook_size?: number | null;
  /** the face the hook card draws in: "display" (default), "running", or a family name. The mono look draws in the mono face. */
  hook_font?: string | boolean | null;
  /** false: the theme turns text hooks off (engine/render.py then draws none) */
  hook_on?: boolean;
  lockup_src?: string | null;
  lockup_dark_src?: string | null;
  emblem_src?: string | null;
  lockup_on?: boolean;
  broll_style?: string | null;
  smooth_motion?: boolean;
  music_level?: number | null;
  end_card?: { on?: boolean; line?: string; handle?: string } | null;
};

export type Word = {
  text: string;
  start_s: number;
  end_s: number;
  accent?: boolean;
  neg?: boolean;
};

export type Zoom = {
  start_s: number;
  end_s: number;
  /** scale at the peak, 1.0 = none (1.12 is a gentle push) */
  z: number;
  /** the point the picture pushes toward, frame px (default: the face) */
  cx?: number;
  cy?: number;
  /** seconds to ease in and out (default 0.4) */
  ease_s?: number;
};

export type Insert = {
  /** a file in the public folder (engine/render.py conforms and links it) */
  src: string;
  start_s: number;
  end_s: number;
  /** full: fills the frame; window: floats over the speaker; pip: fills the frame and the speaker shows in a small box */
  mode?: InsertMode;
  transition?: Transition;
  /** where in the clip it starts (render.py bakes this in when it conforms) */
  from_s?: number;
  rate?: number;
  /** window mode: which side, and its width in px at 1080 wide */
  side?: "left" | "right";
  w?: number;
  /** a slow extra push into the shot, and where it pushes from ("40% 55%") */
  zoom?: number;
  origin?: string;
};

export type HookSpec = {
  lines?: (string | { text: string })[];
  /** one line instead of `lines` */
  text?: string;
  /** when the card leaves, seconds (default 4.5) */
  end_s?: number;
  look?: HookLook;
  /** a top / size override for this hook: fractions of the frame (0..1) or px in a 1080 x 1920 frame */
  top?: number;
  size?: number;
  /** the mono look: the second the key words turn to the negative color and the picture drops to black and white */
  turn_s?: number;
};

export type Face = { cx: number; cy: number; h?: number; w?: number };

export type GraphicSpec = {
  /** an id registered in remotion/graphics/index.ts */
  component: string;
  start_s: number;
  end_s: number;
  props?: Record<string, any>;
  /** hide the caption pages while this graphic is on screen */
  captions?: "show" | "hide";
};

export type EndCardSpec = { at_s: number; line?: string; handle?: string };

/** what a graphic component receives */
export type GraphicProps<P = Record<string, any>> = {
  props: P;
  start_s: number;
  end_s: number;
  /** the composition's clock in seconds (frame / fps) */
  t: number;
  layout: Layout;
};
export type GraphicComponent = React.FC<GraphicProps<any>>;

/** platform UI to stay out of, as fractions: top and bottom of the frame height, the right rail as a fraction of the width
 *  starting at railTop (a fraction of the height) */
export type Safe = {
  top: number;
  bottom: number;
  rail: number;
  railTop: number;
};

export type EditProps = {
  /** the cut file, relative to the public folder */
  src: string;
  theme: Theme;
  words?: Word[];
  captions_mode?: CaptionsMode;
  /** the look knobs of the simple caption pages (a DNA caption style's `feed` block) */
  feed?: FeedSpec | null;
  zooms?: Zoom[];
  inserts?: Insert[];
  hook?: HookSpec | null;
  face?: Face | null;
  /** the face box each second: { "0": {cx, cy, h}, "1": ... } */
  track?: Record<string, Face> | null;
  graphics?: GraphicSpec[];
  /** draw the brand mark (default: the theme's lockup_on) */
  mark?: boolean | null;
  endCard?: EndCardSpec | null;
  safe?: Partial<Safe> | null;
  durationInFrames?: number;
  fps?: number;
  width?: number;
  height?: number;
};

export type MontageClip = {
  src: string;
  from_s?: number;
  /** when it starts on the montage clock, and how long it plays */
  at: number;
  dur: number;
  rate?: number;
  /** drift: scale at the start and the end of the shot */
  z?: [number, number];
  pos?: string;
  origin?: string;
  in?: "cut" | "dissolve" | "push";
  inDur?: number;
  dir?: "left" | "right";
  vol?: number;
};

export type MontageProps = {
  theme: Theme;
  clips: MontageClip[];
  words?: Word[];
  captions_mode?: CaptionsMode;
  feed?: FeedSpec | null;
  hook?: HookSpec | null;
  mark?: boolean | null;
  endCard?: EndCardSpec | null;
  safe?: Partial<Safe> | null;
  /** play the clips' own sound, beds, voiceover lines and music inside the composition (default false: sound is mixed after the render) */
  audio?: boolean;
  beds?: {
    src: string;
    from_s?: number;
    at: number;
    dur: number;
    vol?: number;
  }[];
  vo?: {
    src: string;
    at: number;
    vol?: number;
    words?: { start_s: number; end_s: number }[];
  }[];
  music?: {
    src: string;
    from_s?: number;
    vol: number;
    duckTo?: number;
    fadeIn?: number;
    fadeOut?: number;
    end?: number;
  } | null;
  sfx?: { src: string; at: number; vol?: number }[];
  durationInFrames?: number;
  fps?: number;
  width?: number;
  height?: number;
};
