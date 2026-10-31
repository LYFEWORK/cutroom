/**
 * register.ts: the Captions composition's configuration, so a project's Root.tsx registers it with one import.
 *
 *   import { Composition } from "remotion";
 *   import { captions } from "./captions/register";
 *   ...
 *   <Composition {...captions} />
 *
 * One composition draws every caption style: 1080x1920 at 30 fps. 4K masters are made by rendering this SAME composition with `--scale=2`
 * (engine/captions/render.sh), never by a second 2160x3840 composition: every pixel constant in CaptionsDynamic.tsx and in the planners is in 1080 space.
 * The defaults are a bare render (no events, no text) over the file named by `src`; a real render passes the take's plan.json as its props
 * (render.sh), and the plan carries the look's font files, its colors and its events. The length of a render comes from `durationInFrames` in the props.
 *
 * Render (a real take, through engine/captions/render.sh):
 *   npx remotion render index.ts Captions <out.mp4> --props=<plan.json> --public-dir=<staging> [--scale=2] --codec=h264 --crf=18 \
 *     --concurrency=3 --color-space=bt709 --image-format=png --log=error
 */
import type { CalculateMetadataFunction } from "remotion";
import { CaptionsDynamic, DynProps } from "./CaptionsDynamic";

export const captionsMetadata: CalculateMetadataFunction<DynProps> = ({ props }) => ({
  durationInFrames: props.durationInFrames ?? 90,
});

export const captions = {
  id: "Captions",
  component: CaptionsDynamic,
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 90,
  defaultProps: {
    src: "src.mp4",
    durationInFrames: 90,
    accent: "#FFFFFF",
    font: { family: "sans-serif", faces: [] },
    events: [],
  } as DynProps,
  calculateMetadata: captionsMetadata,
};
