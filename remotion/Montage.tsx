/**
 * Montage.tsx: a b-roll edit with no talking head (a voiceover piece, a machine-only piece). Layered clips that cut, dissolve or push
 * into one another and drift in slowly, with the same hook card, caption pages, brand mark and end card as Edit.
 *
 * Hard cuts on the voice by default, a dissolve only where time changes, a push only to carry a camera move through the cut, and
 * every shot drifts in slowly. Sound is the caller's: by default nothing plays inside the composition, and engine/render.py mixes the
 * voiceover lines, music and effects afterwards. Pass `audio: true` to hear the clips' own sound, beds, voiceover and music in
 * Remotion Studio (they are the layers a render would mix).
 */
import React from "react";
import { AbsoluteFill, Audio, OffthreadVideo, Sequence, useCurrentFrame, useVideoConfig } from "remotion";
import { BRAND, INOUT, applyTheme, clamp01, res, useFontsReady } from "./theme";
import { useLayout } from "./layout";
import { HBlur } from "./trans";
import { Captions, EndCard, Hook, MarkLayer } from "./overlays";
import type { MontageProps } from "./types";

const MontageBody: React.FC<MontageProps> = ({
  clips = [],
  words = [],
  captions_mode = "feed",
  feed = null,
  hook = null,
  mark = null,
  endCard = null,
  safe = null,
  audio = false,
  beds = [],
  vo = [],
  music = null,
  sfx = [],
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const L = useLayout(safe);
  const t = frame / fps;
  // 1 when nobody is talking, `to` while a voiceover line plays (0.15 s ramps either side)
  const spans: [number, number][] = vo.map((v) => [
    v.at + (v.words?.[0]?.start_s ?? 0),
    v.at + (v.words?.[v.words.length - 1]?.end_s ?? 2),
  ]);
  const duck = (tt: number, to: number) => {
    let k = 0;
    for (const [a, b] of spans)
      k = Math.max(k, Math.min(clamp01((tt - (a - 0.25)) / 0.15), clamp01((b + 0.25 - tt) / 0.15)));
    return 1 - k * (1 - to);
  };
  return (
    <AbsoluteFill style={{ backgroundColor: "#000", textTransform: BRAND.caps ? "uppercase" : undefined }}>
      {clips.map((c, i) => {
        const nx = clips[i + 1];
        const tail = nx && nx.in && nx.in !== "cut" ? (nx.inDur ?? 0.35) : 0; // the outgoing shot runs under the incoming transition
        const from = Math.round(c.at * fps);
        const len = Math.max(1, Math.round((c.dur + tail) * fps));
        const lt = t - c.at;
        const inDur = c.inDur ?? 0.35;
        const kin = c.in === "dissolve" || c.in === "push" ? INOUT(clamp01(lt / inDur)) : 1;
        const kout = nx && nx.in === "push" ? INOUT(clamp01((t - nx.at) / (nx.inDur ?? 0.35))) : 0;
        const dirIn = c.dir === "left" ? -1 : 1;
        const dirOut = nx?.dir === "left" ? -1 : 1;
        // a push travels the frame width; the blur follows the speed so it reads as camera movement, not a slide
        const x = c.in === "push" ? (1 - kin) * L.w * dirIn : 0;
        const xo = -kout * L.w * dirOut;
        const speed =
          c.in === "push" && kin < 1 ? Math.sin(kin * Math.PI) : kout > 0 && kout < 1 ? Math.sin(kout * Math.PI) : 0;
        const [z0, z1] = c.z ?? [1.0, 1.04];
        const z = z0 + (z1 - z0) * clamp01(lt / Math.max(0.5, c.dur));
        const op = c.in === "dissolve" ? kin : 1;
        return (
          <Sequence key={i} from={from} durationInFrames={len}>
            <HBlur id={`cr-m${i}`} amount={speed * 32} />
            <AbsoluteFill
              style={{
                opacity: op,
                transform: `translateX(${(x + xo).toFixed(1)}px)`,
                filter: speed > 0.02 ? `url(#cr-m${i})` : undefined,
                overflow: "hidden",
              }}
            >
              <OffthreadVideo
                src={res(c.src)}
                muted={!audio}
                trimBefore={Math.round((c.from_s ?? 0) * fps)}
                playbackRate={c.rate ?? 1}
                volume={(f: number) => {
                  const tt = c.at + f / fps;
                  const edge = Math.min(clamp01((tt - c.at) / 0.08), clamp01((c.at + c.dur + tail - tt) / 0.12));
                  return (c.vol ?? 0) * edge * duck(tt, 0.45);
                }}
                style={{
                  width: L.w,
                  height: L.h,
                  objectFit: "cover",
                  objectPosition: c.pos ?? "50% 50%",
                  transform: `scale(${z.toFixed(4)})`,
                  transformOrigin: c.origin ?? "50% 50%",
                }}
              />
            </AbsoluteFill>
          </Sequence>
        );
      })}

      {hook ? <Hook hook={hook} t={t} L={L} /> : null}
      <MarkLayer on={mark} L={L} />
      {captions_mode === "feed" && words.length ? (
        <Captions words={words} L={L} bandAt={() => false} feed={feed} />
      ) : null}
      {endCard ? (
        <Sequence from={Math.max(0, Math.round(endCard.at_s * fps))} layout="none">
          <EndCard spec={endCard} t={t} L={L} />
        </Sequence>
      ) : null}

      {audio ? (
        <>
          {beds.map((b, i) => (
            <Sequence
              key={"b" + i}
              from={Math.round(b.at * fps)}
              durationInFrames={Math.max(1, Math.round(b.dur * fps))}
            >
              <Audio
                src={res(b.src)}
                trimBefore={Math.round((b.from_s ?? 0) * fps)}
                volume={(f: number) => {
                  const tt = b.at + f / fps;
                  return (
                    (b.vol ?? 0.3) *
                    Math.min(clamp01((tt - b.at) / 0.3), clamp01((b.at + b.dur - tt) / 0.3)) *
                    duck(tt, 0.5)
                  );
                }}
              />
            </Sequence>
          ))}
          {music ? (
            <Audio
              src={res(music.src)}
              trimBefore={Math.round((music.from_s ?? 0) * fps)}
              volume={(f: number) => {
                const tt = f / fps;
                const end = music.end ?? 1e9;
                const fin = clamp01(tt / (music.fadeIn ?? 0.3));
                const fout = clamp01((end - tt) / (music.fadeOut ?? 1.5));
                return music.vol * fin * fout * duck(tt, music.duckTo ?? 0.42);
              }}
            />
          ) : null}
          {sfx.map((x, i) => (
            <Sequence key={"x" + i} from={Math.max(0, Math.round(x.at * fps))}>
              <Audio src={res(x.src)} volume={x.vol ?? 0.5} />
            </Sequence>
          ))}
          {vo.map((v, i) => (
            <Sequence key={"v" + i} from={Math.round(v.at * fps)}>
              <Audio src={res(v.src)} volume={v.vol ?? 1} />
            </Sequence>
          ))}
        </>
      ) : null}
    </AbsoluteFill>
  );
};

export const Montage: React.FC<MontageProps> = (props) => {
  applyTheme(props.theme);
  const ready = useFontsReady();
  return ready ? <MontageBody {...props} /> : <AbsoluteFill />;
};
