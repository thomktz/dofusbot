"""Humanlike timing and mouse paths (protocol §4), all driven by a seeded RNG.

Pure Python; unit-tested on Linux.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

Point = tuple[float, float]


@dataclass
class HumanProfile:
    """Timing knobs. All durations in milliseconds, distances in points.

    Defaults follow protocol §4. ``speed`` multiplies every duration (0 = no
    sleeping at all, handy for tests; 0.5 = twice as fast).
    """

    seed: int | None = None
    move_ms: tuple[float, float] = (120.0, 450.0)
    far_distance_pt: float = 900.0         # distance that maps to move_ms[1]
    move_ms_variation: float = 0.15        # +-15 % random variation of the duration
    jitter_pt: float = 2.0                 # +- jitter on targets unless exact=True
    hold_ms: tuple[float, float] = (40.0, 110.0)
    key_gap_ms: tuple[float, float] = (60.0, 180.0)
    post_pause_ms: tuple[float, float] = (150.0, 600.0)
    double_click_gap_ms: tuple[float, float] = (70.0, 140.0)
    drag_settle_ms: tuple[float, float] = (60.0, 140.0)
    modifier_gap_ms: tuple[float, float] = (20.0, 60.0)
    overshoot_prob: float = 0.15
    overshoot_pt: tuple[float, float] = (3.0, 9.0)
    overshoot_min_distance: float = 120.0
    correction_ms: tuple[float, float] = (60.0, 120.0)
    curvature: float = 0.18                # control point offset as a fraction of distance
    step_ms: float = 8.0                   # spacing of intermediate mouse events
    min_steps: int = 4
    speed: float = 1.0
    _rng: random.Random | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def rng(self) -> random.Random:
        if self._rng is None:
            self._rng = random.Random(self.seed)
        return self._rng

    def reseed(self, seed: int | None) -> None:
        self.seed = seed
        self._rng = random.Random(seed)

    # -- samplers -------------------------------------------------------------
    def uniform(self, rng_range: tuple[float, float]) -> float:
        lo, hi = rng_range
        return self.rng.uniform(lo, hi)

    def scaled(self, ms: float) -> float:
        return max(0.0, ms * self.speed)

    def hold(self) -> float:
        return self.scaled(self.uniform(self.hold_ms))

    def key_gap(self) -> float:
        return self.scaled(self.uniform(self.key_gap_ms))

    def post_pause(self) -> float:
        return self.scaled(self.uniform(self.post_pause_ms))

    def double_click_gap(self) -> float:
        return self.scaled(self.uniform(self.double_click_gap_ms))

    def drag_settle(self) -> float:
        return self.scaled(self.uniform(self.drag_settle_ms))

    def modifier_gap(self) -> float:
        return self.scaled(self.uniform(self.modifier_gap_ms))

    def move_duration(self, dist: float) -> float:
        """120..450 ms (defaults) growing with distance, +-15 %, clamped to the range."""
        lo, hi = self.move_ms
        frac = min(1.0, max(0.0, dist / max(1.0, self.far_distance_pt)))
        # sqrt: short moves already take a while, long moves saturate (Fitts-like)
        base = lo + (hi - lo) * math.sqrt(frac)
        base *= 1.0 + self.rng.uniform(-self.move_ms_variation, self.move_ms_variation)
        return min(hi, max(lo, base))

    def jitter(self, x: float, y: float, exact: bool = False) -> Point:
        if exact or self.jitter_pt <= 0:
            return (x, y)
        j = self.jitter_pt
        return (x + self.rng.uniform(-j, j), y + self.rng.uniform(-j, j))


# ------------------------------------------------------------------------- curves
def ease_in_out(t: float) -> float:
    """Sinusoidal ease-in-out, 0 -> 0, 1 -> 1, symmetric."""
    t = min(1.0, max(0.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (
        a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    )


def _control_points(start: Point, end: Point, profile: HumanProfile) -> tuple[Point, Point]:
    rng = profile.rng
    dx, dy = end[0] - start[0], end[1] - start[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return start, end
    # unit normal
    nx, ny = -dy / dist, dx / dist
    side = 1.0 if rng.random() < 0.5 else -1.0
    off1 = side * dist * profile.curvature * rng.uniform(0.3, 1.0)
    off2 = side * dist * profile.curvature * rng.uniform(0.0, 0.7)
    t1, t2 = rng.uniform(0.2, 0.4), rng.uniform(0.6, 0.8)
    p1 = (start[0] + dx * t1 + nx * off1, start[1] + dy * t1 + ny * off1)
    p2 = (start[0] + dx * t2 + nx * off2, start[1] + dy * t2 + ny * off2)
    return p1, p2


def _segment(start: Point, end: Point, duration_ms: float, profile: HumanProfile) -> list[tuple[float, float, float]]:
    """Points along one Bezier segment with ease-in-out timing.

    Returns ``[(x, y, delay_ms_before_this_point), ...]``; the last point is ``end`` exactly.
    """
    p1, p2 = _control_points(start, end, profile)
    step = max(1.0, profile.step_ms)
    n = max(profile.min_steps, int(math.ceil(duration_ms / step)))
    dt = profile.scaled(duration_ms) / n
    out = []
    for i in range(1, n + 1):
        t = ease_in_out(i / n)
        x, y = cubic_bezier(start, p1, p2, end, t)
        out.append((x, y, dt))
    out[-1] = (end[0], end[1], dt)
    return out


def plan_move(
    start: Point,
    end: Point,
    profile: HumanProfile,
    allow_overshoot: bool = True,
) -> list[tuple[float, float, float]]:
    """Full humanlike path from ``start`` to ``end`` (both screen points).

    Optionally overshoots the target by a few points along the movement direction
    and corrects back. The final point is always exactly ``end``.
    """
    dist = math.hypot(end[0] - start[0], end[1] - start[1])
    if dist < 0.5:
        return [(end[0], end[1], profile.scaled(profile.step_ms))]
    duration = profile.move_duration(dist)
    rng = profile.rng
    if (
        allow_overshoot
        and dist >= profile.overshoot_min_distance
        and rng.random() < profile.overshoot_prob
    ):
        over = profile.uniform(profile.overshoot_pt)
        ux, uy = (end[0] - start[0]) / dist, (end[1] - start[1]) / dist
        mid = (end[0] + ux * over, end[1] + uy * over)
        corr = profile.uniform(profile.correction_ms)
        main = _segment(start, mid, max(profile.move_ms[0], duration - corr), profile)
        back = _segment(mid, end, corr, profile)
        return main + back
    return _segment(start, end, duration, profile)


def path_duration_ms(path: list[tuple[float, float, float]]) -> float:
    return sum(p[2] for p in path)
