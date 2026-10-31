/**
 * icon.tsx: a monochrome SVG file drawn inline in any colour. Inline, not a CSS mask: the frame waits (delayRender) until the SVG
 * text has loaded, so an icon can never be missing from a rendered frame.
 */
import React, { useEffect, useState } from "react";
import { continueRender, delayRender } from "remotion";
import { res } from "./theme";

const cache: Record<string, string> = {};

export const SvgIcon: React.FC<{
  src: string;
  size: number;
  color: string;
  style?: React.CSSProperties;
}> = ({ src, size, color, style }) => {
  const [svg, setSvg] = useState<string | null>(cache[src] ?? null);
  const [handle] = useState(() => (cache[src] ? null : delayRender(`icon ${src}`)));
  useEffect(() => {
    if (cache[src]) {
      if (handle !== null) continueRender(handle);
      return;
    }
    fetch(res(src))
      .then((r) => {
        if (!r.ok) throw new Error(`icon ${src}: ${r.status}`); // a wrong path draws nothing, never an error page
        return r.text();
      })
      .then((txt) => {
        cache[src] = txt;
        setSvg(txt);
      })
      .catch(() => undefined)
      .finally(() => {
        if (handle !== null) continueRender(handle);
      });
  }, [src, handle]);
  const box: React.CSSProperties = {
    width: size,
    height: size,
    flexShrink: 0,
    display: "inline-block",
    ...style,
  };
  if (!svg) return <div style={box} />;
  // a file that carries no fill is black by default: give the root our colour, and strip anything executable
  let out = svg
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*')/gi, "")
    .replace(/currentColor|#000000\b|#000\b/gi, color);
  out = out.replace(/<svg\b/, `<svg width="${size}" height="${size}"`);
  if (!/<svg[^>]*\sfill=/.test(out)) out = out.replace(/<svg\b/, `<svg fill="${color}"`);
  return <div style={box} dangerouslySetInnerHTML={{ __html: out }} />;
};
