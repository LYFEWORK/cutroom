/**
 * The registry of graphic components an edit plan can use by id: { "component": "LowerThird", "start_s": 2, "end_s": 5, "props": {...} }.
 * The two shipped examples live here. Your own go in ./user/ (see user/index.ts), a folder an update never touches, and are spread in below.
 * The id in a plan is the key.
 */
import type { GraphicComponent } from "../types";
import { LowerThird } from "./LowerThird";
import { TitleCard } from "./TitleCard";
import { USER_GRAPHICS } from "./user";

export const GRAPHICS: Record<string, GraphicComponent> = {
  LowerThird,
  TitleCard,
  ...USER_GRAPHICS,
};
