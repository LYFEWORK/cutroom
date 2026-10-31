/**
 * The registry of graphic components an edit plan can use by id: { "component": "LowerThird", "start_s": 2, "end_s": 5, "props": {...} }.
 * To add one: put a file in this folder, export a component typed GraphicProps, import it here and add it to GRAPHICS.
 * The id in a plan is the key below.
 */
import type { GraphicComponent } from "../types";
import { LowerThird } from "./LowerThird";
import { TitleCard } from "./TitleCard";

export const GRAPHICS: Record<string, GraphicComponent> = {
  LowerThird,
  TitleCard,
};
