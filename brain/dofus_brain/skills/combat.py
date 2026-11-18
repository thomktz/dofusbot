"""Fight session + turn solver on the spell effect model.

FightSession lives for one fight: monster knowledge, cooldowns, active self buffs (range),
casts per turn/target, the once-per-fight strategy. TurnSolver searches, for every cell we
can reach, a self buff (optional) then up to `max_casts` casts within PA, and scores:

    score = expected damage after resistances (capped by HP) + kill bonus
          + control (push with collision, AP/MP removal)
          - friendly damage
          - danger: threat map of the cell we end on (enemies we kill this turn removed)
          - small move cost (+ approach pull when nothing can be hit)

It is deterministic and fast (no LLM). The skill re-plans after every action.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..game.grid import Grid
from ..game.state import Fighter, FightState
from .spellmodel import CastValue, SpellModel, can_target, evaluate_cast, zone_cells
from .threat import MonsterBook, ThreatMap

WEIGHTS = {"kill": 25.0, "ap": 5.0, "mp": 4.0, "push_cell": 1.5, "friendly": 2.0, "threat": 1.0,
           "move": 0.05, "approach": 0.5, "focus": 1.5,
           # a "situational" spell (Flèche de Barrage) hitting one far enemy loses this much,
           # so the all-direction ranged spell (Flèche Glacée) is the default attack
           "situational": 15.0, "close": 2.0}


@dataclass
class PlannedCast:
    spell: SpellModel
    target_cell: int
    target_id: str          # main target (or "" for a self buff / empty cell)
    damage: float
    value: CastValue | None = None


@dataclass
class TurnPlan:
    move_to: int | None
    casts: list[PlannedCast] = field(default_factory=list)
    move_after: int | None = None
    score: float = 0.0
    why: str = ""
    end_cell: int | None = None
    danger: float = 0.0


class TurnSolver:
    def __init__(self, grid: Grid, spells: list[SpellModel], role: str = "ranged", book: MonsterBook | None = None,
                 level: int = 1, weights: dict | None = None, max_casts: int = 3, options_per_spell: int = 6):
        self.grid = grid
        self.spells = spells
        self.role = role
        self.book = book or MonsterBook()
        self.level = level
        self.w = {**WEIGHTS, **(weights or {})}
        self.max_casts = max_casts
        self.options_per_spell = options_per_spell

    # -- cast options from one cell
    def _options(self, me: Fighter, cell: int, fight: FightState, blocked: set[int], occupied: set[int],
                 range_bonus: int, refused: set, usable: list[SpellModel], min_bonus: int = 0) -> list[PlannedCast]:
        caster = Fighter(me.id, me.name, me.team, True, cell, me.hp, me.hp_max)
        fighters = [caster if f.is_self else f for f in fight.fighters]
        enemies = [f for f in fighters if f.team == "enemy" and f.cell is not None and (f.hp is None or f.hp > 0)]
        blockers = set(blocked) | {f.cell for f in fighters if f.cell is not None and not f.is_self}
        occ = {f.cell for f in fighters if f.cell is not None and not f.is_self}
        out = []
        for sp in usable:
            if not sp.offensive:
                continue
            cand: set[int] = set()
            for e in enemies:
                cand.add(e.cell)  # type: ignore[arg-type]
                if sp.zone.size > 0:  # aim next to enemies to catch several in the area
                    cand |= {c for c in range(self.grid.n_cells)
                             if self.grid.distance(c, e.cell) <= sp.zone.size}  # type: ignore[arg-type]
            opts = []
            for t in cand:
                if (sp.name, t) in refused or not can_target(self.grid, sp, cell, t, blockers, occ, range_bonus, min_bonus):
                    continue
                v = evaluate_cast(self.grid, sp, caster, t, fighters, blocked, self.book.resist, self.level)
                if not v.damage and not v.ap_removed and not v.mp_removed and not v.pushed_to and v.caster_to is None:
                    continue
                main = next((f.id for f in fighters if f.cell == t), "")
                opts.append(PlannedCast(sp, t, main, v.total_enemy_damage(), v))
            opts.sort(key=lambda o: -(o.damage + self.w["mp"] * sum(o.value.mp_removed.values())))  # type: ignore[union-attr]
            out += opts[: self.options_per_spell]
        return out

    def _score(self, seq: list[PlannedCast], fight: FightState, me_cell: int, focus: str | None) -> tuple[float, set[str], list[PlannedCast]]:
        hp: dict[str, float] = {f.id: float(f.hp) if f.hp is not None else 1e9 for f in fight.fighters}
        names = {f.id: f.name for f in fight.fighters}
        cells = {f.id: f.cell for f in fight.fighters}
        dead: set[str] = set()
        score = 0.0
        ordered = sorted(seq, key=lambda c: min((hp.get(i, 1e9) for i in (c.value.damage if c.value else {})), default=1e9))
        for c in ordered:
            v = c.value
            if v is None:
                continue
            gained = 0.0
            for fid, dmg in v.damage.items():
                if fid in dead:
                    gained -= 1.0
                    continue
                dealt = min(dmg, hp[fid])
                hp[fid] -= dealt
                g = dealt + (self.w["kill"] if hp[fid] <= 0 else 0.0)
                if hp[fid] <= 0:
                    dead.add(fid)
                if focus and focus.casefold() in (names.get(fid) or "").casefold():
                    g *= self.w["focus"]
                gained += g
            gained += self.w["ap"] * sum(n for i, n in v.ap_removed.items() if i not in dead)
            gained += self.w["mp"] * sum(n for i, n in v.mp_removed.items() if i not in dead)
            for fid, to in v.pushed_to.items():
                if fid not in dead and cells.get(fid) is not None:
                    gained += self.w["push_cell"] * max(0, self.grid.distance(to, me_cell) - self.grid.distance(cells[fid], me_cell))
            gained -= self.w["friendly"] * v.friendly
            if getattr(c.spell, "use", "any") == "situational":
                hit = [fid for fid, dmg in v.damage.items() if dmg > 0]
                close = self.w["close"]
                pushed_close = any(cells.get(fid) is not None and self.grid.distance(cells[fid], me_cell) <= close
                                   and self.grid.distance(to, me_cell) > self.grid.distance(cells[fid], me_cell)
                                   for fid, to in v.pushed_to.items())
                if len(hit) < 2 and not pushed_close:
                    gained -= self.w["situational"]
            score += gained
        return score, dead, ordered

    def plan(self, fight: FightState, pa: int, pm: int, *, refused: set = frozenset(), used: dict | None = None,
             per_target_used: dict | None = None, range_bonus: int = 0, min_range_bonus: int = 0,
             focus: str | None = None, style: str = "aggressive") -> TurnPlan:
        me = fight.me()
        if me is None or me.cell is None:
            return TurnPlan(None, why="own fighter not found")
        used = used or {}
        per_target_used = per_target_used or {}
        enemies = [e for e in fight.enemies() if e.cell is not None]
        occupied = {f.cell for f in fight.fighters if f.cell is not None and not f.is_self}
        blocked = set(fight.blocked_cells)
        reach = self.grid.reachable(me.cell, max(0, pm), blocked, occupied)
        threat = ThreatMap.build(self.grid, enemies, blocked, occupied, self.book, list(reach))
        tw = self.w["threat"] * (2.0 if style == "defensive" else 1.0)
        usable = [s for s in self.spells if used.get(s.name, 0) < s.per_turn]
        buffs = [s for s in usable if s.range_buff > 0 and s.pa <= pa]
        max_r = max((s.targeting.max_range for s in usable if s.offensive), default=1)
        best = TurnPlan(None, score=float("-inf"))
        for cell, steps in reach.items():
            for buff in [None, *buffs]:
                left_pa = pa - (buff.pa if buff else 0)
                bonus = range_bonus + (buff.range_buff if buff else 0)
                min_bonus = min_range_bonus + (buff.min_range_buff if buff else 0)
                opts = self._options(me, cell, fight, blocked, occupied, bonus, set(refused),
                                     [s for s in usable if s is not buff], min_bonus)
                best_cast: tuple[float, set[str], list[PlannedCast]] = (0.0, set(), [])
                for n in range(1, self.max_casts + 1):
                    for combo in itertools.combinations_with_replacement(range(len(opts)), n):
                        if sum(opts[i].spell.pa for i in combo) > left_pa:
                            continue
                        cnt: dict[str, int] = {}
                        tcnt: dict[tuple, int] = {}
                        ok = True
                        for i in combo:
                            o = opts[i]
                            cnt[o.spell.name] = cnt.get(o.spell.name, 0) + 1
                            k = (o.spell.name, o.target_id or o.target_cell)
                            tcnt[k] = tcnt.get(k, 0) + 1
                            if cnt[o.spell.name] + used.get(o.spell.name, 0) > o.spell.per_turn or \
                                    tcnt[k] + per_target_used.get(k, 0) > o.spell.per_target:
                                ok = False
                                break
                        if not ok:
                            continue
                        sc = self._score([opts[i] for i in combo], fight, cell, focus)
                        if sc[0] > best_cast[0] + 1e-9:
                            best_cast = sc
                cast_score, dead, casts = best_cast
                if buff is not None and not casts:
                    continue  # a range buff only helps if it enables a cast this turn
                left_pm = pm - steps
                end, end_danger = cell, threat.without(cell, dead)
                moved_by = next((c.value.caster_to for c in reversed(casts) if c.value and c.value.caster_to is not None), None)
                if moved_by is not None:   # a recoil spell moved us: we end there (no kiting modelled after it)
                    end, end_danger = moved_by, threat.without(moved_by, dead)
                    left_pm = 0
                if left_pm > 0 and self.role == "ranged":
                    for c2 in self.grid.reachable(cell, left_pm, blocked, occupied):
                        d2 = threat.without(c2, dead)
                        if d2 < end_danger - 1e-9:
                            end, end_danger = c2, d2
                alive = [e for e in enemies if e.id not in dead]
                approach = 0.0
                if alive and not casts:
                    dmin = min(self.grid.distance(end, e.cell) for e in alive)  # type: ignore[arg-type]
                    want = 1 if self.role == "melee" else max_r
                    approach = -self.w["approach"] * max(0, dmin - want)
                score = cast_score - tw * end_danger - self.w["move"] * steps + approach
                if score > best.score + 1e-9:
                    seq = ([PlannedCast(buff, me.cell if cell == me.cell else cell, "", 0.0)] if buff else []) + casts
                    best = TurnPlan(cell if cell != me.cell else None, seq, end if end != cell else None, score,
                                    end_cell=end, danger=end_danger)
        if best.score == float("-inf"):
            return TurnPlan(None, why="no reachable cell")
        dmg = sum(c.damage for c in best.casts)
        best.why = (f"{len([c for c in best.casts if c.damage])} cast(s) for {dmg:.0f} expected dmg, "
                    f"end danger {best.danger:.0f}"
                    + (f", move to {best.move_to}" if best.move_to is not None else "")
                    + (f", then kite to {best.move_after}" if best.move_after is not None else ""))
        return best


class FightSession:
    """One fight: turn counter, cooldowns, buffs, per-turn use counts, strategy, log."""

    def __init__(self, spells: list[SpellModel], role: str, book: MonsterBook | None = None, level: int = 1,
                 strategy: dict | None = None):
        self.spells = spells
        self.role = role
        self.book = book or MonsterBook()
        self.level = level
        self.strategy = strategy or {}
        self.turn = 0
        self.used: dict[str, int] = {}
        self.per_target: dict[tuple, int] = {}
        self.last_cast_turn: dict[str, int] = {}
        self.range_bonus_until: list[tuple[int, int]] = []   # (bonus, last turn it applies)
        self.min_bonus_until: list[tuple[int, int]] = []
        self.log: list[dict] = []

    def start_turn(self) -> None:
        self.turn += 1
        self.used = {}
        self.per_target = {}
        self.range_bonus_until = [(b, t) for b, t in self.range_bonus_until if t >= self.turn]
        self.min_bonus_until = [(b, t) for b, t in self.min_bonus_until if t >= self.turn]

    @property
    def range_bonus(self) -> int:
        return sum(b for b, t in self.range_bonus_until if t >= self.turn)

    @property
    def min_range_bonus(self) -> int:
        return sum(b for b, t in self.min_bonus_until if t >= self.turn)

    def available(self) -> list[SpellModel]:
        return [s for s in self.spells
                if self.used.get(s.name, 0) < s.per_turn and self.turn - self.last_cast_turn.get(s.name, -99) >= s.cooldown]

    def record_cast(self, spell: SpellModel, target_id: str = "", target_cell: int | None = None) -> None:
        self.used[spell.name] = self.used.get(spell.name, 0) + 1
        k = (spell.name, target_id or target_cell)
        self.per_target[k] = self.per_target.get(k, 0) + 1
        self.last_cast_turn[spell.name] = self.turn
        for e in spell.effects:
            if e.kind == "range_buff":
                self.range_bonus_until.append((e.value, self.turn + max(0, e.turns - 1)))
            elif e.kind == "min_range_buff":
                self.min_bonus_until.append((e.value, self.turn + max(0, e.turns - 1)))
        self.log.append({"turn": self.turn, "spell": spell.name, "target": target_id, "cell": target_cell})

    def solver(self, grid: Grid) -> TurnSolver:
        return TurnSolver(grid, self.available(), self.role, self.book, self.level)

    def plan(self, grid: Grid, fight: FightState, pa: int, pm: int, refused: set = frozenset()) -> TurnPlan:
        for f in fight.enemies():
            if f.name and not f.name.startswith("enemy"):  # placeholder names from the grid reader
                self.book.ensure(f.name)
        return self.solver(grid).plan(fight, pa, pm, refused=refused, used=self.used, per_target_used=self.per_target,
                                      range_bonus=self.range_bonus, min_range_bonus=self.min_range_bonus,
                                      focus=self.strategy.get("focus"),
                                      style=self.strategy.get("style", "aggressive"))


__all__ = ["FightSession", "PlannedCast", "TurnPlan", "TurnSolver", "zone_cells"]
