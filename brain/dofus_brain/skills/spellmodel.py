"""Spell effect model: targeting, area shape and effects as data, plus the evaluation of
one cast on the current fight (who is hit, damage after resistances, pushes with collision
damage, AP/MP removal, self buffs).

JSON form (config/spells_<class>.json), all fields optional except name/key/pa:
    {"name": "Fleche Glacee", "key": "\\"", "pa": 3,
     "targeting": {"min_range": 1, "max_range": 6, "boostable": true, "line": false,
                   "los": true, "free_cell": false},
     "zone": {"shape": "single" | "cross" | "line" | "circle" | "cone", "size": 0},
     "effects": [{"kind": "damage", "element": "air", "min": 8, "max": 10},
                 {"kind": "push", "value": 2}, {"kind": "ap_remove", "value": 1},
                 {"kind": "mp_remove", "value": 1}, {"kind": "range_buff", "value": 3, "turns": 1},
                 {"kind": "teleport"}],
     "cooldown": 0, "per_turn": 2, "per_target": 1}
Old flat entries (min_range, max_range, los, line, damage) are still read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..game.grid import Grid
from ..game.state import Fighter

ELEMENTS = ("neutral", "earth", "fire", "water", "air")
SHAPES = ("single", "cross", "line", "circle", "cone", "perpendicular")
EFFECT_KINDS = ("damage", "push", "pull", "ap_remove", "mp_remove", "range_buff", "min_range_buff", "retreat",
                "teleport", "heal")


@dataclass(frozen=True)
class Targeting:
    min_range: int = 1
    max_range: int = 1
    boostable: bool = False
    line: bool = False
    los: bool = True
    free_cell: bool = False
    needs_target: bool = False    # "Cible nécessaire": the cell must hold a fighter
    diagonal: bool = False        # "Lancé en diagonale": with line=True, line OR diagonal


@dataclass(frozen=True)
class Zone:
    shape: str = "single"
    size: int = 0


@dataclass(frozen=True)
class Effect:
    kind: str
    element: str | None = None
    min: float = 0.0
    max: float = 0.0
    value: int = 0
    turns: int = 0
    crit_min: float | None = None
    crit_max: float | None = None

    @property
    def avg(self) -> float:
        return (self.min + self.max) / 2

    def expected(self, crit: float) -> float:
        """Average damage including the critical chance (when crit values are known)."""
        if self.crit_min is None or self.crit_max is None:
            return self.avg
        return (1 - crit) * self.avg + crit * (self.crit_min + self.crit_max) / 2


@dataclass
class SpellModel:
    name: str
    key: str
    pa: int
    targeting: Targeting = field(default_factory=Targeting)
    zone: Zone = field(default_factory=Zone)
    effects: list[Effect] = field(default_factory=list)
    cooldown: int = 0
    per_turn: int = 99
    per_target: int = 99
    verified: bool = False
    crit: float = 0.0             # critical chance 0..1
    # "situational": only worth it when it hits 2+ enemies or pushes a close enemy away
    # (keep distance); otherwise the solver prefers the plain ranged spells
    use: str = "any"

    @classmethod
    def from_json(cls, d: dict) -> "SpellModel":
        t = d.get("targeting") or {}
        targeting = Targeting(
            min_range=int(t.get("min_range", d.get("min_range", 1))),
            max_range=int(t.get("max_range", d.get("max_range", d.get("min_range", 1)))),
            boostable=bool(t.get("boostable", d.get("range_modifiable", False))),
            line=bool(t.get("line", d.get("line", False))),
            los=bool(t.get("los", d.get("los", True))),
            free_cell=bool(t.get("free_cell", False)),
            needs_target=bool(t.get("needs_target", False)),
            diagonal=bool(t.get("diagonal", False)))
        z = d.get("zone") or {}
        zone = Zone(str(z.get("shape", "single")), int(z.get("size", 0)))
        if zone.shape not in SHAPES:
            raise ValueError(f"unknown zone shape {zone.shape!r}")
        effects = []
        for e in d.get("effects") or []:
            if e.get("kind") not in EFFECT_KINDS:
                raise ValueError(f"unknown effect kind {e.get('kind')!r}")
            effects.append(Effect(e["kind"], e.get("element"), float(e.get("min", e.get("value", 0))),
                                  float(e.get("max", e.get("min", e.get("value", 0)))), int(e.get("value", 0)),
                                  int(e.get("turns", 0)),
                                  float(e["crit_min"]) if e.get("crit_min") is not None else None,
                                  float(e["crit_max"]) if e.get("crit_max") is not None else None))
        if not effects and float(d.get("damage", 0) or 0) > 0:   # old flat entry
            dmg = float(d["damage"])
            effects.append(Effect("damage", d.get("element", "neutral"), dmg, dmg))
        if d.get("kind") == "teleport" and not any(e.kind == "teleport" for e in effects):
            effects.append(Effect("teleport"))
        crit = float(d.get("crit", 0) or 0)
        return cls(str(d["name"]), str(d["key"]), int(d["pa"]), targeting, zone, effects,
                   int(d.get("cooldown", 0)), int(d.get("per_turn", 99)), int(d.get("per_target", 99)),
                   bool(d.get("verified", False)), crit / 100 if crit > 1 else crit,
                   str(d.get("use", "any")))

    # -- what kind of spell this is
    @property
    def damaging(self) -> bool:
        return any(e.kind == "damage" and e.max > 0 for e in self.effects)

    @property
    def range_buff(self) -> int:
        return sum(e.value for e in self.effects if e.kind == "range_buff")

    @property
    def min_range_buff(self) -> int:
        return sum(e.value for e in self.effects if e.kind == "min_range_buff")

    @property
    def offensive(self) -> bool:
        return any(e.kind in ("damage", "push", "pull", "ap_remove", "mp_remove") for e in self.effects)


# ------------------------------------------------------------------ geometry
def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def direction(grid: Grid, src: int, dst: int) -> tuple[int, int]:
    """Unit step in (u, v) from src towards dst; off-axis targets use the dominant axis."""
    su, sv = grid.uv(src)
    du, dv = grid.uv(dst)
    a, b = du - su, dv - sv
    if a == 0 and b == 0:
        return (0, 0)
    if abs(a) >= abs(b):
        return (_sign(a), 0)
    return (0, _sign(b))


def zone_cells(grid: Grid, caster: int, target: int, zone: Zone) -> list[int]:
    """Cells hit by a cast on `target` (target first)."""
    tu, tv = grid.uv(target)
    n = zone.size
    pts: list[tuple[int, int]] = [(tu, tv)]
    if zone.shape == "single" or n <= 0:
        pass
    elif zone.shape == "circle":
        pts += [(tu + a, tv + b) for a in range(-n, n + 1) for b in range(-n, n + 1)
                if 0 < abs(a) + abs(b) <= n]
    elif zone.shape == "cross":
        pts += [(tu + s * k, tv) for k in range(1, n + 1) for s in (1, -1)]
        pts += [(tu, tv + s * k) for k in range(1, n + 1) for s in (1, -1)]
    elif zone.shape == "line":       # continues away from the caster
        du, dv = direction(grid, caster, target)
        pts += [(tu + du * k, tv + dv * k) for k in range(1, n + 1)]
    elif zone.shape == "perpendicular":   # across the cast direction, n cells each side
        du, dv = direction(grid, caster, target)
        pu, pv = -dv, du
        pts += [(tu + pu * k * s, tv + pv * k * s) for k in range(1, n + 1) for s in (1, -1)]
    elif zone.shape == "cone":       # widens away from the caster
        du, dv = direction(grid, caster, target)
        pu, pv = -dv, du             # perpendicular
        for k in range(1, n + 1):
            for w in range(-k, k + 1):
                pts.append((tu + du * k + pu * w, tv + dv * k + pv * w))
    out = []
    for u, v in pts:
        c = grid.from_uv(u, v)
        if c is not None and c not in out:
            out.append(c)
    return out


def is_diagonal(grid: Grid, src: int, dst: int) -> bool:
    su, sv = grid.uv(src)
    du, dv = grid.uv(dst)
    return abs(du - su) == abs(dv - sv) and src != dst


def can_target(grid: Grid, sp: SpellModel, src: int, dst: int, blockers: set[int], occupied: set[int],
               range_bonus: int = 0, min_range_bonus: int = 0) -> bool:
    """`range_bonus` / `min_range_bonus` come from self buffs (Tirs Éloignés raises both, on
    every offensive spell, whether or not the spell's range is boostable by the PO stat)."""
    t = sp.targeting
    d = grid.distance(src, dst)
    lo = t.min_range + (min_range_bonus if sp.offensive else 0)
    hi = t.max_range + (range_bonus if sp.offensive else 0)
    if not (lo <= d <= max(hi, lo)):
        return False
    if t.line and t.diagonal:
        if not (grid.in_line(src, dst) or is_diagonal(grid, src, dst)):
            return False
    elif t.line and not grid.in_line(src, dst):
        return False
    elif t.diagonal and not is_diagonal(grid, src, dst):
        return False
    if t.free_cell and dst in occupied:
        return False
    if t.needs_target and dst not in occupied:
        return False
    if t.los and not grid.has_los(src, dst, blockers - {src, dst}):
        return False
    return True


# ------------------------------------------------------------------ evaluation
def collision_damage(remaining_cells: int, level: int) -> float:
    """Expected push collision damage (Dofus formula (8 + rand(1..8) * level / 50) per
    remaining cell; expectation of rand is 4.5)."""
    return max(0, remaining_cells) * (8 + 4.5 * max(1, level) / 50)


def push_result(grid: Grid, caster: int, target: int, cells: int, walls: set[int]) -> tuple[int, int]:
    """(final cell, cells NOT travelled because something was in the way)."""
    du, dv = direction(grid, caster, target)
    u, v = grid.uv(target)
    cur = target
    for k in range(cells):
        nxt = grid.from_uv(u + du * (k + 1), v + dv * (k + 1))
        if nxt is None or nxt in walls:
            return cur, cells - k
        cur = nxt
    return cur, 0


@dataclass
class CastValue:
    damage: dict[str, float] = field(default_factory=dict)     # fighter id -> expected damage
    pushed_to: dict[str, int] = field(default_factory=dict)
    ap_removed: dict[str, int] = field(default_factory=dict)
    mp_removed: dict[str, int] = field(default_factory=dict)
    friendly: float = 0.0                                       # damage to allies / self
    unknown_resist: bool = False
    caster_to: int | None = None                                # where a "retreat" effect moves us

    def total_enemy_damage(self) -> float:
        return sum(self.damage.values())


Resist = Callable[[Fighter], dict[str, float | None]]


def evaluate_cast(grid: Grid, sp: SpellModel, caster: Fighter, target: int, fighters: list[Fighter],
                  blocked: set[int], resist: Resist | None = None, level: int = 1) -> CastValue:
    out = CastValue()
    if caster.cell is None:
        return out
    hit = set(zone_cells(grid, caster.cell, target, sp.zone))
    walls = set(blocked) | {f.cell for f in fighters if f.cell is not None}
    for e in sp.effects:
        if e.kind == "retreat":  # the caster moves away from the target (Flèche Vagabonde)
            du, dv = direction(grid, target, caster.cell)
            u, v = grid.uv(caster.cell)
            cur = caster.cell
            for k in range(1, e.value + 1):
                nxt = grid.from_uv(u + du * k, v + dv * k)
                if nxt is None or nxt in walls - {caster.cell}:
                    break
                cur = nxt
            out.caster_to = cur
    for f in fighters:
        if f.cell is None or f.cell not in hit or (f.hp is not None and f.hp <= 0):
            continue
        res = resist(f) if (resist and f.team == "enemy") else {}
        dmg = 0.0
        for e in sp.effects:
            if e.kind == "damage":
                r = res.get(e.element or "neutral")
                if r is None and f.team == "enemy" and resist is not None:
                    out.unknown_resist = True
                dmg += e.expected(sp.crit) * (1 - max(-100.0, min(100.0, float(r or 0))) / 100)
        for e in sp.effects:
            if e.kind == "push" and f.cell == target and not f.is_self:
                final, remaining = push_result(grid, caster.cell, target, e.value, walls - {target})
                out.pushed_to[f.id] = final
                dmg += collision_damage(remaining, level)
            elif e.kind == "ap_remove" and f.team == "enemy":
                out.ap_removed[f.id] = out.ap_removed.get(f.id, 0) + e.value
            elif e.kind == "mp_remove" and f.team == "enemy":
                out.mp_removed[f.id] = out.mp_removed.get(f.id, 0) + e.value
        if f.team == "enemy":
            if dmg:
                out.damage[f.id] = out.damage.get(f.id, 0.0) + dmg
        else:
            out.friendly += dmg
    return out


# ------------------------------------------------------------------ review list
@dataclass
class ReviewItem:
    spell: str
    line: str
    reason: str
