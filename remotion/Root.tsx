import React from "react";
import { Composition } from "remotion";
import { Edit } from "./Edit";
import { Montage } from "./Montage";
import { PLACEHOLDER_THEME } from "./theme";
import { captions } from "./captions/register";
import type { EditProps, MontageProps } from "./types";

/** the size, rate and length of a render come from its props, so one composition serves every output size */
const meta = ({ props }: { props: { durationInFrames?: number; fps?: number; width?: number; height?: number } }) => ({
  durationInFrames: props.durationInFrames ?? 300,
  fps: props.fps ?? 30,
  width: props.width ?? 1080,
  height: props.height ?? 1920,
});

const editDefaults: EditProps = {
  src: "cut.mp4",
  theme: PLACEHOLDER_THEME,
  words: [],
  zooms: [],
  inserts: [],
  graphics: [],
  hook: null,
  endCard: null,
  durationInFrames: 300,
  fps: 30,
  width: 1080,
  height: 1920,
};
const montageDefaults: MontageProps = {
  theme: PLACEHOLDER_THEME,
  clips: [],
  words: [],
  hook: null,
  endCard: null,
  durationInFrames: 300,
  fps: 30,
  width: 1080,
  height: 1920,
};

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="Edit"
      component={Edit}
      durationInFrames={300}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={editDefaults}
      calculateMetadata={meta}
    />
    <Composition
      id="Montage"
      component={Montage}
      durationInFrames={300}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={montageDefaults}
      calculateMetadata={meta}
    />
    <Composition {...captions} />
  </>
);
