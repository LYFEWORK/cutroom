"""The soft build's look values: the sizes, weights, tints and line widths both planners and both renderers share, and the font measure
that fits a line to its width. Stdlib only; PIL is imported inside font_measure."""
import re
from collections.abc import Callable

LEAD_PX = 56                                             # lead-in and tail size, px
PAY_PX = 100                                             # payoff size
HERO_PX = 150                                            # a one-word payoff
LINE_GAP = 2                                             # air between two lines
PAY_FLOOR = 60                                           # the smallest a payoff is drawn
LEAD_FLOOR = {"feed": 52, "engine": 44}                  # the smallest a lead-in or tail is drawn, per pipeline
SAFE_W = {"feed": 900, "engine": 850}                    # S, the widest a line may be, per pipeline
CENTER_X = {"feed": 520, "engine": 502}                  # the x a lockup is centerd on, per pipeline
TRACK = {"lead": -0.025, "pay": -0.04, "caps": -0.015}   # letter spacing in em: lead-in and tail, payoff, payoff drawn in caps
ARRIVE_EARLY = 0.05                                      # s: a thought starts to arrive this long before its first word is spoken
ARRIVE_S = 0.34                                          # s: a word's arrival takes this long
OPAQUE_AT = 0.55                                         # and it is fully opaque this share of the way in
LEAVE_LEAD = 0.09                                        # s: it starts to leave this long before the next thought's first word, 0.04 s before that one arrives
LEAVE_S = 0.16                                           # s: and is gone this long after it starts to leave
LINGER_S = 1.0                                           # s: it starts to leave at most this long after its own last word ends, when nothing comes sooner

_ROLES = ("lead", "pay", "tail")
_WEIGHT_FLOOR = 400                                      # no line draws lighter than this
_LEAD_STEP, _PAY_STEP = 200, 100                         # how far under display_weight a variable font draws the lead-in and the payoff
_PAY_INSET, _BAR_INSET = 80, 200                         # how much narrower than S a payoff is, and a payoff on the bar
_DEEP, _LIGHT = 0.42, 0.45                               # how far the accent is mixed toward black, and toward white
_FIT_STEP = 2                                            # px
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def weights(display_weight: int, variable: bool) -> tuple[int, int]:
    """(lead-in and tail weight, payoff weight) from the brand's one display weight. A variable font draws the lead-in 200 under it and
    the payoff 100 under it, never under 400; a static font has its one weight."""
    if not variable:
        return display_weight, display_weight
    return max(_WEIGHT_FLOOR, display_weight - _LEAD_STEP), max(_WEIGHT_FLOOR, display_weight - _PAY_STEP)


def line_case(text: str, role: str, brand_case: str) -> str:
    """The text as a line of this role is drawn. The lead-in and the tail keep the transcript's case; the payoff takes the brand's:
    "upper" is capitals, "title" is sentence case (the first character in capitals, the rest as it was, as cluster_plan.casef has it),
    anything else is as spoken."""
    if role not in _ROLES:
        raise ValueError(f"look: a line's role is lead, pay or tail, not {role!r}")
    if role != "pay":
        return text
    if brand_case == "upper":
        return text.upper()
    if brand_case == "title":
        return text[:1].upper() + text[1:]
    return text


def tints(accent: str) -> tuple[str, str]:
    """(deep, light) of an accent as #RRGGBB: the accent 42% of the way to black, and 45% of the way to white. The channels are rounded with
    Python's round, so an exact half goes to the even number; softmotion.ts must repeat that, not Math.round."""
    return _mix(accent, 0, _DEEP), _mix(accent, 255, _LIGHT)


def _mix(color: str, gray: int, t: float) -> str:
    """The #RRGGBB color t of the way toward a gray (0 black, 255 white): each channel c + (gray - c) * t, rounded."""
    if not _HEX.fullmatch(color):
        raise ValueError(f"look: a color is #RRGGBB, not {color!r}")
    channels = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c + (gray - c) * t):02X}" for c in channels)


def widths(pipeline: str) -> dict[str, int]:
    """The widest a line may be in a pipeline ("feed" or "engine"), by width key: "lead" and "tail" S, "pay" S - 80, and "bar" S - 200, the
    width of a payoff on the bar. "bar" is a width key, not a role: a payoff on the bar is measured with the role "pay" against this width. A
    pipeline that is neither is a ValueError."""
    if pipeline not in SAFE_W:
        raise ValueError(f"look: a pipeline is feed or engine, not {pipeline!r}")
    s = SAFE_W[pipeline]
    return {"lead": s, "pay": s - _PAY_INSET, "bar": s - _BAR_INSET, "tail": s}


def fit(text: str, px: int, maxw: int, floor: int, measure: Callable[[str, int], float]) -> int:
    """The biggest size, from px down in 2 px steps, at which measure(text, size) is within maxw. The floor when none is, and the caller
    splits the line then. measure takes the text and the size only: bind the role first (a lambda or functools.partial)."""
    while px > floor and measure(text, px) > maxw:
        px = max(floor, px - _FIT_STEP)
    return px


def in_capitals(text: str) -> bool:
    """Whether a line is drawn in capitals, which takes the caps tracking on a payoff: it has letters and is its own upper case. The renderers
    read a payoff's text the same way (softthought.tsx), so a planner and a renderer track it alike whatever the brand's case."""
    return any(c.isalpha() for c in text) and text == text.upper()


def font_measure(path: str, display_weight: int, variable: bool, brand_case: str) -> Callable[[str, int, str], float]:
    """measure(text, px, role) -> the width in px of a line of the brand's display font. The line is cased as its role is drawn (line_case),
    set at the role's weight (a variable font only) and tracked: each gap between two characters adds em * px, with em from TRACK (a
    payoff's is the caps one when the payoff, so cased, is in capitals: in_capitals)."""
    from PIL import ImageFont    # here only, so the rest of this file stays stdlib
    lead_weight, pay_weight = weights(display_weight, variable)
    faces = {}                   # (px, weight) -> the opened font

    def face(px, weight):
        got = faces.get((px, weight))
        if got is None:
            try:
                got = ImageFont.truetype(path, px)
                if variable:
                    got.set_variation_by_axes([_axis_value(a, weight) for a in got.get_variation_axes()])
            except OSError as e:
                raise OSError(f"look: cannot read the brand font {path}: {e}") from e
            faces[(px, weight)] = got
        return got

    def measure(text, px, role):
        shown = line_case(text, role, brand_case)    # also refuses a role that is not lead, pay or tail
        paying = role == "pay"
        em = (TRACK["caps"] if in_capitals(shown) else TRACK["pay"]) if paying else TRACK["lead"]
        return face(px, pay_weight if paying else lead_weight).getlength(shown) + em * px * max(len(shown) - 1, 0)

    return measure


def _axis_value(axis: dict, weight: int) -> float:
    """What a variable font's axis is set to: the weight, kept inside the axis's range, on the weight axis and the font's own default
    on any other."""
    name = axis["name"].decode("latin-1") if isinstance(axis["name"], bytes) else str(axis["name"])
    if name.lower() in ("weight", "wght"):
        return max(axis["minimum"], min(axis["maximum"], weight))
    return axis["default"]
