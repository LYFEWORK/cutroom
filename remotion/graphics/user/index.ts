/**
 * Your own graphics live in this folder. Put a component file beside this one (QuoteCard.tsx, ProgressBar.tsx ...), typed GraphicProps
 * like the two examples in ../LowerThird.tsx and ../TitleCard.tsx, import it here and add it to USER_GRAPHICS. The key is the id an edit
 * plan uses ({ "component": "QuoteCard", ... }), and the id you list in your DNA's graphics.built.
 *
 * An update (git pull) never touches this folder, so nothing you add here is overwritten or ends in a merge conflict.
 */
import type { GraphicComponent } from "../../types";

export const USER_GRAPHICS: Record<string, GraphicComponent> = {};
