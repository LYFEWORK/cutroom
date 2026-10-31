/**
 * Icons.tsx — the inline SVG glyph set the layout events need (README, Devices).
 *
 * WHY inline and not a font/library: the icons are stroked line marks at ONE size
 * (24 px pills, §2c; 30 px post chrome, §2d) and their stroke must take the event's `ink` colour,
 * which a webfont cannot do. No dependency is added — every mark is a 24-box path.
 *
 *  - Icon        — §2c pill icons (play|grid|search|phone|chat|calendar|dollar|check|pin|star)
 *                  and §2d post-chrome marks (heart|comment|share|bookmark). Stroked, fill none.
 *  - PinGlyph    — §1e word device: a FILLED teardrop-with-hole (evenodd), sized in `em` so it
 *                  scales with the row it sits in and sits on the baseline = the cap height.
 *  - LogoGlyph   — §2: a platform logo in the same leading slot, FILLED in the row's colour. The marks
 *                  are OPTIONAL (see LOGO_PATHS): a name with no entry draws no mark.
 *  - RowIcon     — §2: that slot in line-icon form — the Icon vocabulary sized in `em`, stroked in
 *                  the row's colour.
 */
import React from "react";

// §2c: the icon vocabulary the planner may name on a pill.
export type IconName =
  | "play"
  | "grid"
  | "search"
  | "phone"
  | "chat"
  | "calendar"
  | "dollar"
  | "check"
  | "pin"
  | "star";
// §2d: the post chrome (never planner-facing, the igpost card draws them itself).
export type PostIconName = "heart" | "comment" | "share" | "bookmark";
export type AnyIconName = IconName | PostIconName;

const PATHS: Record<AnyIconName, React.ReactNode> = {
  play: <path d="M7 4.5 19 12 7 19.5z" />,
  grid: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.2" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.2" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.2" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M15.3 15.3 21 21" />
    </>
  ),
  phone: (
    <path d="M21 16.9v2.6a1.7 1.7 0 0 1-1.9 1.7 17 17 0 0 1-7.4-2.6 16.7 16.7 0 0 1-5.1-5.1A17 17 0 0 1 4 6a1.7 1.7 0 0 1 1.7-1.9h2.6a1.7 1.7 0 0 1 1.7 1.5c.1.9.3 1.7.6 2.5a1.7 1.7 0 0 1-.4 1.8l-1.1 1.1a13.6 13.6 0 0 0 5.1 5.1l1.1-1.1a1.7 1.7 0 0 1 1.8-.4c.8.3 1.6.5 2.5.6a1.7 1.7 0 0 1 1.4 1.7z" />
  ),
  chat: (
    <path d="M21 12.5a7.5 7.5 0 0 1-10.8 6.7L4 21l1.8-6.2A7.5 7.5 0 1 1 21 12.5z" />
  ),
  calendar: (
    <>
      <rect x="3.5" y="5.5" width="17" height="15" rx="2" />
      <path d="M3.5 10.5h17M8 3.5v4M16 3.5v4" />
    </>
  ),
  dollar: (
    <path d="M12 2.5v19M16.5 6.5H9.8a3.3 3.3 0 0 0 0 6.6h4.4a3.3 3.3 0 0 1 0 6.6H7" />
  ),
  check: <path d="M4.5 12.8 9.5 18 19.5 6" />,
  pin: (
    <>
      <path d="M20 10c0 6.5-8 12.5-8 12.5S4 16.5 4 10a8 8 0 0 1 16 0z" />
      <circle cx="12" cy="10" r="2.9" />
    </>
  ),
  star: (
    <path d="m12 3.2 2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.7l6.1-.9z" />
  ),
  heart: (
    <path d="M12 20.5S3.5 15.2 3.5 9.4A4.9 4.9 0 0 1 12 6.1a4.9 4.9 0 0 1 8.5 3.3c0 5.8-8.5 11.1-8.5 11.1z" />
  ),
  comment: (
    <path d="M21 11.9a7.4 7.4 0 0 1-10.7 6.6L4 20.4l1.9-6A7.4 7.4 0 1 1 21 11.9z" />
  ),
  share: <path d="M21.5 2.8 2.8 10.2l7.6 3.1 3.1 7.6z M21.5 2.8 10.4 13.3" />,
  bookmark: <path d="M6 3.4h12v18l-6-4.6-6 4.6z" />,
};

export const Icon: React.FC<{
  name: AnyIconName;
  size: number;
  color: string;
  strokeWidth?: number;
}> = ({ name, size, color, strokeWidth = 2 }) => (
  <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    fill="none"
    stroke={color}
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{ display: "block", flex: "0 0 auto" }}
  >
    {PATHS[name]}
  </svg>
);

// §1e: teardrop with a hole punched through it (fillRule evenodd), 0.72em tall, accent fill,
// 0.18em of air before the row. `display:inline-block` on the baseline puts its bottom on the
// baseline, which is exactly "vertically centred on the cap height" for a 0.72em mark.
export const PinGlyph: React.FC<{
  color: string;
  em?: number;
  style?: React.CSSProperties;
}> = ({ color, em = 0.72, style }) => (
  <svg
    viewBox="0 0 24 24"
    style={{
      width: `${em}em`,
      height: `${em}em`,
      display: "inline-block",
      marginRight: "0.18em",
      ...style,
    }}
  >
    <path
      fill={color}
      fillRule="evenodd"
      clipRule="evenodd"
      d="M12 1.4c-3.9 0-7.1 3.2-7.1 7.1 0 5.3 7.1 14.1 7.1 14.1s7.1-8.8 7.1-14.1c0-3.9-3.2-7.1-7.1-7.1zm0 4.5a2.7 2.7 0 1 1 0 5.4 2.7 2.7 0 0 1 0-5.4z"
    />
  </svg>
);

// ---------------------------------------------------------------------------
// §2 — row marks (a logo / icon that pops before a cluster row)
// ---------------------------------------------------------------------------

// §2: the platform marks a row may carry.
export type LogoName =
  | "instagram"
  | "facebook"
  | "tiktok"
  | "youtube"
  | "google"
  | "meta"
  | "x"
  | "whatsapp"
  | "gmail";

// LOGO_PATHS: nine platform marks (simple-icons path data, CC0, viewBox 0 0 24 24, one glyph path each). The path data is CC0; the
// marks themselves are other companies' trademarks, so they are OPTIONAL: delete any entry, or all of them, and nothing else changes. A row
// whose name has no entry draws without a mark, and cluster_plan.py (which reads the names in this block, one `name:` a line, to know which
// platform words it may tag) stops tagging that name, so no room is held for a mark that is not drawn. WHY inline paths and not an image: a
// row mark takes the ROW's colour, and a CSS `filter` cannot retint a white glyph to an arbitrary hex (a hue-rotate chain only
// approximates it) — an inline `<path fill={color}>` is exact.
const LOGO_PATHS: Partial<Record<LogoName, string>> = {
  instagram:
    "M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077",
  facebook:
    "M9.101 23.691v-7.98H6.627v-3.667h2.474v-1.58c0-4.085 1.848-5.978 5.858-5.978.401 0 .955.042 1.468.103a8.68 8.68 0 0 1 1.141.195v3.325a8.623 8.623 0 0 0-.653-.036 26.805 26.805 0 0 0-.733-.009c-.707 0-1.259.096-1.675.309a1.686 1.686 0 0 0-.679.622c-.258.42-.374.995-.374 1.752v1.297h3.919l-.386 2.103-.287 1.564h-3.246v8.245C19.396 23.238 24 18.179 24 12.044c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.628 3.874 10.35 9.101 11.647Z",
  tiktok:
    "M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z",
  youtube:
    "M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z",
  google:
    "M12.48 10.92v3.28h7.84c-.24 1.84-.853 3.187-1.787 4.133-1.147 1.147-2.933 2.4-6.053 2.4-4.827 0-8.6-3.893-8.6-8.72s3.773-8.72 8.6-8.72c2.6 0 4.507 1.027 5.907 2.347l2.307-2.307C18.747 1.44 16.133 0 12.48 0 5.867 0 .307 5.387.307 12s5.56 12 12.173 12c3.573 0 6.267-1.173 8.373-3.36 2.16-2.16 2.84-5.213 2.84-7.667 0-.76-.053-1.467-.173-2.053H12.48z",
  meta: "M6.915 4.03c-1.968 0-3.683 1.28-4.871 3.113C.704 9.208 0 11.883 0 14.449c0 .706.07 1.369.21 1.973a6.624 6.624 0 0 0 .265.86 5.297 5.297 0 0 0 .371.761c.696 1.159 1.818 1.927 3.593 1.927 1.497 0 2.633-.671 3.965-2.444.76-1.012 1.144-1.626 2.663-4.32l.756-1.339.186-.325c.061.1.121.196.183.3l2.152 3.595c.724 1.21 1.665 2.556 2.47 3.314 1.046.987 1.992 1.22 3.06 1.22 1.075 0 1.876-.355 2.455-.843a3.743 3.743 0 0 0 .81-.973c.542-.939.861-2.127.861-3.745 0-2.72-.681-5.357-2.084-7.45-1.282-1.912-2.957-2.93-4.716-2.93-1.047 0-2.088.467-3.053 1.308-.652.57-1.257 1.29-1.82 2.05-.69-.875-1.335-1.547-1.958-2.056-1.182-.966-2.315-1.303-3.454-1.303zm10.16 2.053c1.147 0 2.188.758 2.992 1.999 1.132 1.748 1.647 4.195 1.647 6.4 0 1.548-.368 2.9-1.839 2.9-.58 0-1.027-.23-1.664-1.004-.496-.601-1.343-1.878-2.832-4.358l-.617-1.028a44.908 44.908 0 0 0-1.255-1.98c.07-.109.141-.224.211-.327 1.12-1.667 2.118-2.602 3.358-2.602zm-10.201.553c1.265 0 2.058.791 2.675 1.446.307.327.737.871 1.234 1.579l-1.02 1.566c-.757 1.163-1.882 3.017-2.837 4.338-1.191 1.649-1.81 1.817-2.486 1.817-.524 0-1.038-.237-1.383-.794-.263-.426-.464-1.13-.464-2.046 0-2.221.63-4.535 1.66-6.088.454-.687.964-1.226 1.533-1.533a2.264 2.264 0 0 1 1.088-.285z",
  x: "M14.234 10.162 22.977 0h-2.072l-7.591 8.824L7.251 0H.258l9.168 13.343L.258 24H2.33l8.016-9.318L16.749 24h6.993zm-2.837 3.299-.929-1.329L3.076 1.56h3.182l5.965 8.532.929 1.329 7.754 11.09h-3.182z",
  whatsapp:
    "M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z",
  gmail:
    "M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z",
};

// Both marks sit on the row's CAP HEIGHT. PinGlyph's 0.72em is the cap-height stand-in (a
// 0.72em box with its bottom on the baseline is centred on the caps), so a mark of any other height
// is lowered by half the difference to land on that same centre.
const CAP_EM = 0.72;
const capAlign = (em: number) => `${(-(em - CAP_EM) / 2).toFixed(4)}em`;

// §2: the platform logo (when it has an entry above) — filled glyph, 0.9em, 0.18em of air before the row's text.
export const LogoGlyph: React.FC<{
  name: LogoName;
  color: string;
  em?: number;
  style?: React.CSSProperties;
}> = ({ name, color, em = 0.9, style }) =>
  LOGO_PATHS[name] ? (
    <svg
      viewBox="0 0 24 24"
      style={{
        width: `${em}em`,
        height: `${em}em`,
        display: "inline-block",
        verticalAlign: capAlign(em),
        marginRight: "0.18em",
        ...style,
      }}
    >
      <path fill={color} d={LOGO_PATHS[name]} />
    </svg>
  ) : null;

// §2: the line icon in the same slot — the §2c vocabulary at 0.8em, stroked in the row's colour.
// Stroke stays 2/24 of the box so it matches a 24 px pill icon at 2 px.
export const RowIcon: React.FC<{
  name: IconName;
  color: string;
  em?: number;
  strokeWidth?: number;
  style?: React.CSSProperties;
}> = ({ name, color, em = 0.8, strokeWidth = 2, style }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke={color}
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{
      width: `${em}em`,
      height: `${em}em`,
      display: "inline-block",
      verticalAlign: capAlign(em),
      marginRight: "0.18em",
      ...style,
    }}
  >
    {PATHS[name]}
  </svg>
);
