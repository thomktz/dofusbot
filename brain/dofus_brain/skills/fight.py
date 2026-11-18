"""Fight turns: a deterministic move + spell search within PA/PM, executed one action at a
time with verification, and re-planned after every action (tackle, misses, deaths).

No LLM in the turn loop. An optional strategy hook is called once per fight (first own turn)
and may return {"focus": "<enemy name>", "style": "aggressive" | "defensive"}.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Callable

from ..game.grid import Grid
from ..game.state import Fighter, FightState, GameState
from ..planner import PlanContext, PlannedStep, StepResult
from . import Outcome, StepOrOutcome, wait


@dataclass(frozen=True)
class Spell:
    name: str
    key: str
    pa: int
    min_range: int = 1
    max_range: int = 1
    los: bool = True
    line: bool = False
    damage: float = 0.0
    per_turn: int = 99
    per_target: int = 99
    cooldown: int = 0             # turns between casts (0 = none)
    kind: str = "damage"          # damage | teleport (moves us onto the target cell) | other

    @classmethod
    def from_json(cls, d: dict) -> "Spell":
        keys = cls.__dataclass_fields__
        return cls(**{k: d[k] for k in keys if k in d})


def _raw_spells(cfg: dict, character_class: str, config_dir=None) -> tuple[str, list[dict]]:
    from .spells import load_spells_file, spells_path

    fc = cfg["fight"]
    cls_cfg = fc["classes"].get(character_class) or {"role": "melee", "spells": []}
    raw = cls_cfg.get("spells", [])
    if config_dir is None:
        from ..paths import config_dir as _cd

        config_dir = _cd()
    from_file = load_spells_file(spells_path(config_dir, character_class))
    if from_file:
        raw = from_file
    return cls_cfg.get("role", "melee"), [s for s in raw if not s.get("disabled")]


def models_for(cfg: dict, character_class: str, config_dir=None) -> tuple[str, list]:
    """Like spells_for, as SpellModel (targeting, zone, effects) for the turn solver."""
    from .spellmodel import SpellModel

    role, raw = _raw_spells(cfg, character_class, config_dir)
    out = []
    for s in raw:
        try:
            out.append(SpellModel.from_json(s))
        except (TypeError, KeyError, ValueError):
            continue  # incomplete or unknown entry: never guessed into the solver
    return role, out


def spells_for(cfg: dict, character_class: str, config_dir=None) -> tuple[str, list[Spell]]:
    """Role from the class table; spells from config/spells_<class>.json when it exists
    (read from in-game tooltips), else the class table's placeholder spells."""
    role, raw = _raw_spells(cfg, character_class, config_dir)
    spells = []
    for s in raw:
        try:
            spells.append(Spell.from_json(s))
        except (TypeError, KeyError):
            continue  # incomplete entry (e.g. tooltip missing the PA cost): skip it
    return role, spells


@dataclass
class Cast:
    spell: Spell
    target_cell: int
    target_id: str
    damage: float


@dataclass
class TurnPlan:
    move_to: int | None                       # cell to walk to first (None = stay)
    casts: list[Cast] = field(default_factory=list)
    move_after: int | None = None             # cell to walk to after casting (kiting)
    score: float = 0.0
    why: str = ""


class FightSolver:
    def __init__(self, grid: Grid, spells: list[Spell], role: str = "melee", keep_distance: int = 0):
        self.grid = grid
        self.spells = spells
        self.role = role
        self.keep_distance = keep_distance

    def can_cast(self, spell: Spell, src: int, dst: int, blockers: set[int]) -> bool:
        d = self.grid.distance(src, dst)
        if not (spell.min_range <= d <= spell.max_range):
            return False
        if spell.line and not self.grid.in_line(src, dst):
            return False
        if spell.los and not self.grid.has_los(src, dst, blockers - {src, dst}):
            return False
        return True

    def _casts_from(self, src: int, pa: int, enemies: list[Fighter], blockers: set[int],
                    refused: set[tuple[str, int]], focus: str | None, limit: int = 4) -> tuple[list[Cast], float]:
        """Best cast sequence from one cell (small exhaustive search over spell/target
        multisets; few spells and few enemies so this stays tiny)."""
        options = []
        for sp in self.spells:
            if sp.kind != "damage" or sp.damage <= 0:
                continue
            for e in enemies:
                if e.cell is None or (sp.name, e.cell) in refused:
                    continue
                if self.can_cast(sp, src, e.cell, blockers):
                    options.append((sp, e))
        if not options:
            return [], 0.0
        best: tuple[list[Cast], float] = ([], 0.0)
        for n in range(1, limit + 1):
            for combo in itertools.combinations_with_replacement(range(len(options)), n):
                cost = sum(options[i][0].pa for i in combo)
                if cost > pa:
                    continue
                per_turn: dict[str, int] = {}
                per_target: dict[tuple[str, str], int] = {}
                ok = True
                for i in combo:
                    sp, e = options[i]
                    per_turn[sp.name] = per_turn.get(sp.name, 0) + 1
                    per_target[(sp.name, e.id)] = per_target.get((sp.name, e.id), 0) + 1
                    if per_turn[sp.name] > sp.per_turn or per_target[(sp.name, e.id)] > sp.per_target:
                        ok = False
                        break
                if not ok:
                    continue
                score, casts = self._score_casts([options[i] for i in combo], focus)
                if score > best[1] + 1e-9:
                    best = (casts, score)
        return best

    @staticmethod
    def _score_casts(seq: list[tuple[Spell, Fighter]], focus: str | None) -> tuple[float, list[Cast]]:
        hp_left: dict[str, float] = {}
        score = 0.0
        casts = []
        # kill order: lowest HP first, so finishing blows come before spreading damage
        for sp, e in sorted(seq, key=lambda t: (t[1].hp if t[1].hp is not None else 1e9)):
            left = hp_left.get(e.id, float(e.hp) if e.hp is not None else 1e9)
            if left <= 0:
                score -= 1.0  # wasted on a dead target
                casts.append(Cast(sp, e.cell, e.id, 0.0))  # type: ignore[arg-type]
                continue
            dealt = min(sp.damage, left)
            hp_left[e.id] = left - dealt
            gain = dealt + (25.0 if hp_left[e.id] <= 0 else 0.0)
            if focus and focus.casefold() in e.name.casefold():
                gain *= 1.5
            score += gain
            casts.append(Cast(sp, e.cell, e.id, dealt))  # type: ignore[arg-type]
        return score, casts

    def _safety(self, cell: int, enemies: list[Fighter]) -> float:
        """Penalty for ending next to enemies (ranged) / reward for being close (melee)."""
        ds = [self.grid.distance(cell, e.cell) for e in enemies if e.cell is not None]
        if not ds:
            return 0.0
        dmin = min(ds)
        if self.role == "ranged":
            reach = max((s.max_range for s in self.spells if s.kind == "damage"), default=1)
            # stay out of melee, but come within spell range for next turn
            return -4.0 * max(0, self.keep_distance - dmin) - 0.5 * max(0, dmin - reach)
        return -0.5 * dmin

    def plan(self, fight: FightState, pa: int, pm: int, refused: set[tuple[str, int]] = frozenset(),
             focus: str | None = None, style: str = "aggressive") -> TurnPlan:
        me = fight.me()
        if me is None or me.cell is None:
            return TurnPlan(None, why="own fighter not found")
        enemies = [e for e in fight.enemies() if e.cell is not None]
        occupied = {f.cell for f in fight.fighters if f.cell is not None and not f.is_self}
        blockers = set(fight.blocked_cells) | occupied
        reach = self.grid.reachable(me.cell, max(0, pm), fight.blocked_cells, occupied)
        safety_w = 2.0 if style == "defensive" else 1.0
        best = TurnPlan(None, score=float("-inf"))
        for cell, steps in reach.items():
            casts, dmg = self._casts_from(cell, pa, enemies, blockers, set(refused), focus)
            left_pm = pm - steps
            after, after_score = None, self._safety(cell, enemies)
            if self.role == "ranged" and left_pm > 0:
                # kite with the leftover PM after casting
                occ2 = occupied
                for c2, s2 in self.grid.reachable(cell, left_pm, fight.blocked_cells, occ2).items():
                    sc = self._safety(c2, enemies)
                    if sc > after_score + 1e-9:
                        after, after_score = (c2 if c2 != cell else None), sc
            score = dmg + safety_w * after_score - 0.05 * steps
            if score > best.score + 1e-9:
                best = TurnPlan(cell if cell != me.cell else None, casts, after, score)
        if best.score == float("-inf"):
            return TurnPlan(None, why="no reachable cell")
        best.why = (f"{len(best.casts)} cast(s) for {sum(c.damage for c in best.casts):.0f} dmg"
                    + (f", move to {best.move_to}" if best.move_to is not None else "")
                    + (f", then kite to {best.move_after}" if best.move_after is not None else ""))
        return best


def _fold(s: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()


def _new_cast_in_log(before: GameState, now: GameState, spell: str) -> bool:
    """The chat shows one more '<us> lance <spell>' line than before (real client)."""
    from ..perception.fight_real import casts_in_log

    def n(st: GameState) -> int:
        lines = getattr(st.fight, "log", None) if st.fight else None
        return sum(1 for _, sp in casts_in_log(lines or []) if _fold(sp) == _fold(spell))

    return bool(getattr(now.fight, "log", None)) and n(now) > n(before)


def _me(state: GameState) -> Fighter | None:
    return state.fight.me() if state.fight else None


class FightTurnSkill:
    """Plays our turns until the fight ends. One action per step; re-plans every step with
    the FightSession / TurnSolver (spell effect model + threat map)."""

    name = "fight"

    def __init__(self, cfg: dict, character_class: str, strategy_hook: Callable[[GameState], dict] | None = None,
                 clock: Callable[[], float] = time.monotonic, turn_seconds: float = 30.0, config_dir=None,
                 book=None):
        from .threat import MonsterBook

        self.cfg = cfg
        self.role, self.models = models_for(cfg, character_class, config_dir)
        self.keys = cfg["keys"]
        self.budget = float(cfg["fight"].get("turn_budget_frac", 0.7)) * turn_seconds
        self.hook = strategy_hook
        self.book = book if book is not None else MonsterBook()
        self.clock = clock
        self.session = None
        self.in_turn = False
        self.turn_started = 0.0
        self.refused: set[tuple[str, int]] = set()
        self.armed = None
        self.last_plan = None
        fc = cfg.get("fight", {})
        self.max_pa = int(fc.get("max_pa", 6))
        self.max_pm = int(fc.get("max_pm", 3))
        self.pa_left = self.max_pa                 # tracked when the HUD can't be read
        self.pm_left = self.max_pm
        self.end_button = None                     # FIN DE TOUR, when perception sees it
        self.refused_moves: set[int] = set()       # cells a move to failed this turn (hole, crate...)
        self.fails_in_row = 0
        self.max_fails_in_row = int(fc.get("end_turn_after_failures", 2))
        self.end_at_timer_s = float(fc.get("end_turn_at_timer_s", 4.0))

    @property
    def strategy(self) -> dict | None:
        return self.session.strategy if self.session is not None else None

    def _ensure_session(self, state: GameState):
        from .combat import FightSession

        if self.session is None:
            strategy: dict = {}
            if self.hook is not None:  # optional strategist: once per fight, never per action
                try:
                    strategy = dict(self.hook(state) or {})
                except Exception as e:  # the hook is optional; never let it break the fight
                    strategy = {"error": str(e)}
            self.session = FightSession(self.models, self.role, self.book, state.character.level or 1, strategy)

    def _new_turn(self) -> None:
        if not self.in_turn:
            self.in_turn = True
            self.turn_started = self.clock()
            self.refused = set()
            self.refused_moves: set[int] = set()
            self.fails_in_row = 0
            self.armed = None
            self.pa_left, self.pm_left = self.max_pa, self.max_pm
            self.session.start_turn()

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        f = state.fight
        if state.screen != "fight" or f is None:
            return Outcome("done", "not in a fight")
        if f.phase == "ended":
            return Outcome("done", "fight ended")
        if f.phase == "placement":
            return wait("placement phase (placement skill)")
        if f.phase != "my_turn":
            self.armed = None
            self.in_turn = False
            return wait("enemy turn")
        self._ensure_session(state)
        self._new_turn()
        me = f.me()
        if me is None or me.cell is None:
            # the first frames of a turn often lack the rings (turn banner, camera, ring fade-in):
            # keep looking for a few seconds before asking the human
            grace = float(self.cfg.get("fight", {}).get("self_grace_s", 5.0))
            if self.clock() - self.turn_started < grace:
                return wait("looking for our fighter on the grid")
            return Outcome("ask", "cannot find own fighter on the grid")
        b = getattr(f, "button", None)
        self.end_button = b if b is not None and b.kind == "end_turn" else None
        pa = me.pa if me.pa is not None else state.character.pa
        pm = me.pm if me.pm is not None else state.character.pm
        # real client: the PA digit is often unreadable; use what we tracked this turn
        pa = self.pa_left if pa is None else min(pa, self.pa_left)
        pm = self.pm_left if pm is None else pm
        if pa is None or pm is None:
            return Outcome("ask", "PA/PM unreadable")
        if self.clock() - self.turn_started > self.budget:
            return self._end_turn("turn time budget used")
        if f.timer_s is not None and f.timer_s <= self.end_at_timer_s:
            return self._end_turn(f"turn timer at {f.timer_s:.0f}s")
        if self.fails_in_row >= self.max_fails_in_row:
            return self._end_turn(f"{self.fails_in_row} actions in a row did not land")
        grid = ctx.grid
        if self.armed is not None:
            return self._click_target(self.armed, grid, pa)
        if self.refused_moves:
            # plan around cells we failed to walk to (holes, crates the reader doesn't see)
            from dataclasses import replace as _replace
            f = _replace(f, blocked_cells=sorted(set(f.blocked_cells) | self.refused_moves))
        plan = self.session.plan(grid, f, pa, pm, self.refused)
        self.last_plan = plan
        if plan.move_to is not None and pm > 0:
            return self._move(plan.move_to, grid, me.cell, pm, plan.why)
        if plan.casts:
            c = plan.casts[0]
            self.armed = c
            return PlannedStep(
                action={"kind": "key", "key": c.spell.key, "reason": f"select {c.spell.name} ({plan.why})"},
                task=f"fight: select {c.spell.name}",
                verify=lambda before, now: now.screen == "fight" and now.fight is not None and now.fight.phase == "my_turn",
                timeout_s=1.5, meta={"skill": self.name, "op": "select", "spell": c.spell.name})
        if plan.move_after is not None and pm > 0:
            return self._move(plan.move_after, grid, me.cell, pm, "kite: " + plan.why)
        return self._end_turn("nothing useful left: " + (plan.why or "no plan"))

    def _move(self, cell: int, grid: Grid, src: int, pm: int, why: str) -> PlannedStep:
        x, y = grid.center(cell)

        def moved(before: GameState, now: GameState) -> bool:
            m = _me(now)
            return m is not None and (m.cell != src or (m.pm is not None and m.pm < pm))

        return PlannedStep(action={"kind": "click", "x": round(x, 1), "y": round(y, 1), "cell": cell, "reason": why},
                           task=f"fight: move to {cell}", verify=moved, timeout_s=5.0,
                           meta={"skill": self.name, "op": "move", "cell": cell, "from": src,
                                 "steps": grid.distance(src, cell)})

    def _click_target(self, c, grid: Grid, pa: int) -> PlannedStep:
        x, y = grid.center(c.target_cell)

        def cast_ok(before: GameState, now: GameState) -> bool:
            m = _me(now)
            now_pa = (m.pa if m is not None and m.pa is not None else now.character.pa)
            if now_pa is not None and now_pa < pa:
                return True
            if _new_cast_in_log(before, now, c.spell.name):
                return True
            tgt = next((e for e in (now.fight.fighters if now.fight else []) if e.id == c.target_id), None)
            old = next((e for e in (before.fight.fighters if before.fight else []) if e.id == c.target_id), None)
            return tgt is not None and old is not None and tgt.hp is not None and old.hp is not None and tgt.hp < old.hp

        return PlannedStep(action={"kind": "click", "x": round(x, 1), "y": round(y, 1), "cell": c.target_cell,
                                   "reason": f"cast {c.spell.name} on {c.target_id or 'cell ' + str(c.target_cell)}"},
                           # no retry: a second click on the cell, with the spell already spent,
                           # would walk us toward the enemy instead of casting
                           task=f"fight: cast {c.spell.name}", verify=cast_ok, timeout_s=5.0, retries=0,
                           meta={"skill": self.name, "op": "cast", "spell": c.spell.name, "cell": c.target_cell,
                                 "target_id": c.target_id})

    def _end_turn(self, why: str) -> PlannedStep:
        self.armed = None

        def ended(before: GameState, now: GameState) -> bool:
            return now.fight is None or now.fight.phase != "my_turn" or now.screen != "fight"

        b = self.end_button
        if b is not None:   # we can see FIN DE TOUR: click it rather than trust a key binding
            action = {"kind": "click", "x": round(b.x, 1), "y": round(b.y, 1), "reason": why + ": click FIN DE TOUR"}
        else:
            action = {"kind": "key", "key": self.keys["end_turn"], "reason": why}
        return PlannedStep(action=action, task="fight: end turn", verify=ended, timeout_s=5.0,
                           meta={"skill": self.name, "op": "end_turn"})

    def on_result(self, result: StepResult, state: GameState) -> None:
        m = result.step.meta
        if m.get("skill") != self.name:
            return
        op = m.get("op")
        # selecting a spell proves nothing landed: only casts and moves reset the count
        if op in ("cast", "move") or (op == "select" and not result.ok):
            self.fails_in_row = 0 if result.ok else self.fails_in_row + 1
        if op == "move" and not result.ok and m.get("cell") is not None:
            self.refused_moves.add(int(m["cell"]))
        if op == "select" and not result.ok:
            self.armed = None
        elif op == "cast":
            armed, self.armed = self.armed, None
            # real client: PA digit, chat log and HP bars are often unreadable, so a cast that
            # landed still times out. The target was checked in range/LoS by the solver, so
            # count it as spent (PA, per-turn use) instead of refusing the spell and ending the turn.
            assumed = not result.ok and result.reason == "timeout"
            if assumed:
                self.fails_in_row = 0
            if (result.ok or assumed) and self.session is not None:
                sp = armed.spell if armed is not None else next(s for s in self.models if s.name == m["spell"])
                self.session.record_cast(sp, m.get("target_id", ""), m.get("cell"))
                self.pa_left = max(0, self.pa_left - int(getattr(sp, "pa", 0) or 0))
            elif not result.ok:
                self.refused.add((m["spell"], int(m["cell"])))  # out of range / LoS: try something else
        elif op == "move" and result.ok:
            self.pm_left = max(0, self.pm_left - int(m.get("steps", 1)))
        elif op == "end_turn":
            self.armed = None
            if result.ok:
                self.in_turn = False

    def reset_fight(self) -> None:
        self.session = None
        self.in_turn = False
        self.armed = None
