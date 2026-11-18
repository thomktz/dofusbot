"""PhasePlanner: runs the phase skills behind the safety layer (docs/agent_phases.md).

Interrupts win over the goal: popup > fight (placement, then turns) > dialog > loading >
world goal. Goals (set from the dashboard):
    "goto 5,-18" | "travel 5 -18"     travel to map coordinates
    "fight"                           engage a group the aggro rules allow on this map
    "harvest [n]"                     interact with the nearest highlighted element n times
    "talk <npc>"                      talk to an NPC seen with the entity key
    "quest"                           follow the current quest objective (tracker text)
An Outcome "ask" (or anything the safety layer refuses) halts the planner until the human
resets it with a new goal. Frames for the image-based skills come via `feed_frame()`.
The overlay keys (Y interactives, Z entities) are HOLD keys: the planner never presses them
as an action; it sets `scan_request` and the agent captures with the key held
(dofus_control Controller.capture_holding), then calls `on_scan()`. A Z scan runs on every
new map (and again after entity_scan_ttl_s) before the goal acts.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

from ..game.state import GameState
from ..paths import brain_home
from ..perception.entities import Plate
from ..perception.placement import PlacementReader, PlacementView
from ..perception.terrain import TerrainPreviewReader, TerrainReplica
from ..skills import Outcome
from ..skills.aggro import AggroMap, DangerRules, EngageSkill, detect_aggro, groups_from_plates, on_aggro
from ..skills.config import load_phases_config
from ..skills.fight import FightTurnSkill
from ..skills.harvest import HarvestSkill
from ..skills.attack import AttackSkill, FightSequenceSkill
from ..skills.placement import PlacementSkill
from ..skills.popup import PopupSkill
from ..skills.quest import DialogSkill, LocationBook, Objective, TalkSkill, goals_for, parse_objective
from ..skills.safety import SafetyGuard
from ..skills.travel import MapGraph, TravelSkill
from . import PlanContext, PlannedStep, StepResult

log = logging.getLogger("dofus_brain.phases")
GOTO_RE = re.compile(r"^(?:goto|go|travel|aller)\s+(-?\d+)\s*[, ]\s*(-?\d+)$", re.I)


class PhasePlanner:
    name = "phases"

    def __init__(self, cfg: dict | None = None, character_class: str = "cra", graph: MapGraph | None = None,
                 book: LocationBook | None = None, strategy_hook=None, clock=None, maps_path: Path | None = None,
                 entity_reader=None, menu_reader=None):
        import time

        self.cfg = cfg or load_phases_config()
        self.clock = clock or time.monotonic
        self.character_class = self.cfg["fight"].get("character_class") or character_class
        self.role = self.cfg["fight"]["classes"].get(self.character_class, {}).get("role", "melee")
        self.maps_path = maps_path or brain_home() / self.cfg["travel"].get("maps_file", "data/maps.json")
        self.graph = graph if graph is not None else MapGraph.load(self.maps_path)
        self.book = book if book is not None else LocationBook.load(brain_home() / "data" / "locations.json")
        self.safety = SafetyGuard(self.cfg, clock=self.clock)
        self.aggro = AggroMap(DangerRules(self.cfg), clock=self.clock)
        self.popup = PopupSkill(self.cfg)
        self.dialog = DialogSkill(self.cfg)
        self.placement: PlacementSkill | None = None
        self.fight: FightTurnSkill | None = None
        self.strategy_hook = strategy_hook
        self.goal: str | None = None
        self.goal_skills: list[Any] = []    # queue for the current goal
        self.attention: str | None = None   # why we stopped and need the human
        self.last_outcome: Outcome | None = None
        self.prev: GameState | None = None
        self.last_action: dict | None = None
        self.last_meta: dict | None = None
        self.placement_view: PlacementView | None = None
        self.placement_reader: PlacementReader | None = None
        self.plates: list[Plate] = []
        self.objective: Objective | None = None
        self._grid = None
        # hold-to-show scans: the agent captures while holding scan_request["key"] (controller
        # capture_holding) and hands the frame back through on_scan()
        self.scan_request: dict | None = None
        self.attack_target = None      # npcs.Entity picked in the dashboard (npcs.attack)
        self.entity_reader = entity_reader     # (img, scale) -> list[Plate], e.g. EntityScanner
        self.menu_reader = menu_reader         # (img, scale, near_xy) -> list[MenuOption], NpcMenuReader
        self.scanned_maps: dict[tuple, float] = {}
        self.entity_scan_ttl_s = float(self.cfg.get("entity_scan_ttl_s", 60.0))
        # terrain preview (top-left, placement only): replica of the board, frozen at fight start
        self.terrain_cfg = dict(self.cfg.get("terrain") or {})
        self.terrain_reader = TerrainPreviewReader(self.terrain_cfg)
        self.terrain: TerrainReplica | None = None
        self.terrain_frozen = False
        self._terrain_saved = False
        self.preview_status: str | None = None   # dashboard line: parsed/ready or why not
        self._terrain_ts = 0.0

    # ------------------------------------------------------------------ inputs
    def set_goal(self, goal: str | None) -> None:
        self.goal = goal.strip() if goal else None
        self.safety.reset()
        self.attention = None
        self.goal_skills = []
        self.objective = None

    def reset(self) -> None:
        """Dashboard Reset: back to idle. Drops the goal, the picked NPC, any fight/placement
        skill and pending scan, so the planner proposes nothing."""
        self.set_goal(None)
        self.attack_target = None
        self.fight = None
        self.placement = None
        self.scan_request = None
        self.last_outcome = None
        self.last_action = None
        self.last_meta = None
        self._drop_terrain()

    # ------------------------------------------------------------------ terrain replica
    def _drop_terrain(self) -> None:
        self.preview_status = None
        self.terrain = None
        self.terrain_frozen = False
        self._terrain_saved = False

    def observe_frame(self, img: np.ndarray, scale: float, state: GameState) -> None:
        """Every tick, read-only. The terrain preview opens BEFORE the fight (on the map) and
        closes during placement: read it outside fights (and in early placement while it is
        still open), keep the best recent read, freeze it when the fight proper starts."""
        f = state.fight
        in_fight = state.screen == "fight" and f is not None
        if not in_fight and self.terrain_frozen:
            self._drop_terrain()                     # that fight is over: next fight, next map
        v = self.placement_view
        placing = in_fight and (f.phase == "placement" or (v is not None and v.is_placement))
        if (in_fight and not placing) or self.terrain_frozen:
            return
        max_age = float(self.terrain_cfg.get("max_age_s", 120.0))
        stale = self.terrain is not None and self.clock() - self._terrain_ts > max_age
        if stale:                                    # an old preview from another group/map
            self.terrain = None
            self._terrain_saved = False
        try:
            r = self.terrain_reader.read(img)
        except Exception as e:                       # never let the preview break a tick
            log.warning("terrain preview read failed: %s", e)
            if self.terrain is None:
                self.preview_status = f"preview could not be parsed ({e}) - not ready"
            return
        if r is None or r.present == 0:
            if self.terrain is None:
                self.preview_status = ("no preview read before the fight - not ready" if placing
                                       else "waiting for the preview (opens before the fight)")
            return
        if self.terrain is None or r.present >= self.terrain.present:
            first = self.terrain is None
            self.terrain = r
            self._terrain_ts = self.clock()
            sm = r.summary()
            self.preview_status = (f"preview parsed, ready ({sm['walkable']} walkable, "
                                   f"{sm['blocked']} blocked, {sm['red']} red / {sm['blue']} blue)")
            if first:
                log.info("terrain preview read (%s): %s", "placement" if placing else "before fight", sm)
            if not self._terrain_saved and self.terrain_cfg.get("save_crops", True):
                self._terrain_saved = True
                self._save_terrain_crop(img, r)

    def _save_terrain_crop(self, img: np.ndarray, r: TerrainReplica) -> None:
        """Keep the preview crop + the replica, to measure the real colours later."""
        try:
            import json
            import time

            from PIL import Image

            d = brain_home() / "data" / "terrain-preview"
            d.mkdir(parents=True, exist_ok=True)
            stem = d / time.strftime("%Y%m%d-%H%M%S")
            x, y, w, h = r.crop_rect
            Image.fromarray(np.ascontiguousarray(img[y:y + h, x:x + w, :3])).save(f"{stem}.png")
            Path(f"{stem}.json").write_text(json.dumps({**r.summary(), "crop_rect": list(r.crop_rect),
                                                        "mini": list(r.mini), "blocked": r.blocked,
                                                        "holes": r.holes, "red": r.red, "blue": r.blue}))
        except Exception as e:
            log.warning("could not save the terrain preview crop: %s", e)

    def _apply_terrain(self, state: GameState) -> None:
        """Fight running: freeze the replica and add its obstacles to the live blocked cells."""
        f = state.fight
        if f is None or self.terrain is None or not self.terrain_cfg.get("use_in_fight", True):
            return
        if not self.terrain_frozen:
            self.terrain_frozen = True
            bad = {x.cell for x in f.fighters if x.cell is not None} & set(self.terrain.not_walkable())
            if bad:                                  # a fighter on a "wall": the read is wrong
                log.warning("terrain replica dropped: fighters on cells it marks blocked %s", sorted(bad))
                self.terrain = None
                return
            log.info("terrain replica frozen for this fight: %s", self.terrain.summary())
        f.blocked_cells = sorted(set(f.blocked_cells) | set(self.terrain.not_walkable()))

    def feed_frame(self, img: np.ndarray, scale: float, state: GameState, regions=None, grid=None) -> None:
        """Raw frame for the image-based skills (harvest diff, placement reader)."""
        for sk in self.goal_skills:
            if isinstance(sk, (HarvestSkill, TalkSkill)):
                sk.feed_frame(img, scale)
        if state.screen == "fight" and regions is not None and grid is not None:
            if self.placement_reader is None:
                self.placement_reader = PlacementReader(regions, self.cfg.get("placement", {}).get("palette"))
            self.placement_view = self.placement_reader.read(img, grid, scale)

    def status(self) -> dict:
        """What the planner is doing, for the dashboard (server `planner.status`)."""
        sk = self.goal_skills[0] if self.goal_skills else None
        lo = getattr(self, "last_outcome", None)
        f = self.fight
        return {"goal": self.goal, "skill": getattr(sk, "name", None),
                "attention": self.attention, "halted": self.safety.halted,
                "outcome": ({"status": lo.status, "reason": lo.reason} if lo is not None else None),
                "fight": None if f is None else {
                    "in_turn": bool(getattr(f, "in_turn", False)),
                    "pa_left": getattr(f, "pa_left", None), "pm_left": getattr(f, "pm_left", None),
                    "plan": getattr(getattr(f, "last_plan", None), "why", None)},
                "attack_target": None if self.attack_target is None else self.attack_target.name,
                "preview": self.preview_status,
                "terrain": None if self.terrain is None else {**self.terrain.summary(), "frozen": self.terrain_frozen}}

    def set_plates(self, plates: list[Plate], coords) -> None:
        """Result of an entity-key scan on the current map."""
        self.plates = list(plates)
        if coords is not None:
            self.aggro.update(tuple(coords), groups_from_plates(plates, tuple(coords), self.clock()))
            for p in plates:
                if p.level is None and p.group_size is None:
                    self.book.learn_npc(p.name, tuple(coords))
        for sk in self.goal_skills:
            if isinstance(sk, TalkSkill):
                sk.set_plates(plates)

    def on_scan(self, key: str, img: np.ndarray, scale: float, state: GameState | None = None) -> None:
        """Frame captured while `key` was held (answer to scan_request)."""
        req, self.scan_request = self.scan_request, None
        keys = self.cfg["keys"]
        if key == keys["show_entities"]:
            coords = (state.map.coords if state is not None else None) or (req or {}).get("coords")
            if coords is not None:
                self.scanned_maps[tuple(coords)] = self.clock()
            if self.entity_reader is not None:
                self.set_plates(self.entity_reader(img, scale), coords)
        elif key == keys["highlight_interactives"]:
            for sk in self.goal_skills:
                if isinstance(sk, HarvestSkill):
                    sk.on_scan(img, scale)

    def _request_scan(self, key: str, purpose: str, coords=None) -> None:
        if str(key).lower() not in self.safety.keys:
            self._ask(f"scan key {key!r} is not a configured key")
            return
        self.scan_request = {"key": key, "purpose": purpose, "coords": coords,
                             "settle_ms": float(self.cfg.get("scan_settle_ms", 250))}

    def _needs_entity_scan(self, state: GameState) -> bool:
        c = state.map.coords
        if c is None:
            return False
        t = self.scanned_maps.get(tuple(c))
        return t is None or self.clock() - t > self.entity_scan_ttl_s

    def set_popup_text(self, text: str) -> None:
        self.popup.set_text(text)

    # ------------------------------------------------------------------ goals
    def _build_goal(self, state: GameState) -> list[Any] | str:
        g = (self.goal or "").strip()
        if not g:
            return []
        m = GOTO_RE.match(g)
        if m:
            return [self._travel((int(m.group(1)), int(m.group(2))), state)]
        word, _, rest = g.partition(" ")
        word = word.lower()
        if g.lower() in ("fight sequence", "fight now", "combat"):
            # Thomas starts this during placement: the fight interrupts do the work
            return [FightSequenceSkill(self.clock)]
        if word == "attack":
            t = self.attack_target
            if t is None or (rest.strip() and rest.strip().casefold() != t.name.casefold()):
                return f"attack {rest.strip()!r}: locate NPCs first and pick a monster from the list"
            return [AttackSkill(t, self.clock)]
        if word == "fight":
            return [EngageSkill(self.aggro)]
        if word == "harvest":
            n = int(rest) if rest.strip().isdigit() else 1
            return [HarvestSkill(self.cfg, target_count=n, clock=self.clock)]
        if word == "talk" and rest.strip():
            t = self._talk(rest.strip())
            t.set_plates(self.plates)
            return [t]
        if word == "quest":
            if state.quest is None or not state.quest.objective:
                return "no quest objective visible (open the quest tracker)"
            self.objective = parse_objective(state.quest.objective)
            self.dialog.objective = self.objective
            out: list[Any] = []
            for goal in goals_for(self.objective, self.book, state.map.coords):
                if goal.kind == "ask":
                    return goal.params["question"]
                if goal.kind == "travel":
                    out.append(self._travel(goal.params["coords"], state))
                elif goal.kind == "talk":
                    t = self._talk(goal.params["npc"])
                    t.set_plates(self.plates)
                    out.append(t)
                elif goal.kind == "fight":
                    out.append(EngageSkill(self.aggro))
                elif goal.kind == "harvest":
                    out.append(HarvestSkill(self.cfg, target_count=int(goal.params.get("count") or 1), clock=self.clock))
            return out or "objective already complete"
        return f"unknown goal {g!r}"

    def _talk(self, npc: str) -> TalkSkill:
        return TalkSkill(npc, menu_reader=self.menu_reader, dismiss_key=self.cfg["keys"]["dismiss"])

    def _talk_menu_open(self) -> bool:
        return bool(self.goal_skills) and isinstance(self.goal_skills[0], TalkSkill) \
            and self.goal_skills[0].phase in ("menu_wait", "menu")

    def _travel(self, coords, state: GameState) -> TravelSkill:
        lvl = state.character.level
        here = state.map.coords

        def avoid(cell: int, _here=here) -> bool:
            return False if _here is None else self.aggro.avoid_cells(_here, self._grid, lvl)(cell)

        return TravelSkill(tuple(coords), self.graph, self.cfg, danger=self.aggro.danger_cost(lvl), avoid_cells=avoid)

    # ------------------------------------------------------------------ main
    def _ask(self, reason: str) -> None:
        self.attention = reason
        self.safety.halt(reason)
        log.warning("needs you: %s", reason)

    def _checked(self, step: PlannedStep | None) -> PlannedStep | None:
        if step is None:
            return None
        why = self.safety.check(step)
        if why is not None:
            self._ask(f"refused {step.task or step.action.get('kind')}: {why}")
            return None
        return step

    def _run(self, skill, state: GameState, ctx: PlanContext) -> PlannedStep | Outcome:
        r = skill.step(state, ctx)
        if isinstance(r, Outcome):
            self.last_outcome = r
            if r.status == "ask":
                self._ask(f"{skill.name}: {r.reason}")
        return r

    async def propose(self, state: GameState, ctx: PlanContext) -> PlannedStep | None:
        self._grid = ctx.grid
        prev, self.prev = self.prev, state
        self.safety.observe(state)
        for sk in self.goal_skills:               # goal skills that follow a fight they don't play
            if hasattr(sk, "note"):
                sk.note(state)
        # aggro: world -> fight without engaging
        if detect_aggro(prev, state, self.last_meta):
            names = on_aggro(self.aggro, prev.map.coords if prev else None, None, ctx.grid)
            log.info("aggroed%s", f" by {', '.join(names)}" if names else "")
        if self.safety.halted:
            self.attention = self.attention or self.safety.halted
            return None
        if not self.safety.screen_ok():
            return None
        s = state.screen
        if s in ("popup", "menu") and self._talk_menu_open():
            s = "world"                       # the NPC menu we just opened, not a real popup
        if s == "popup":
            r = self._run(self.popup, state, ctx)
            return self._checked(r) if isinstance(r, PlannedStep) else None
        if s == "fight" and state.fight is not None:
            v = self.placement_view
            if v is not None and v.is_placement and state.fight.phase != "my_turn":
                state.fight.phase = "placement"
            if state.fight.phase == "placement":
                if self.placement is None:
                    self.placement = PlacementSkill(self.cfg, self.role, clock=self.clock)
                self.placement.set_view(v)
                r = self._run(self.placement, state, ctx)
                return self._checked(r) if isinstance(r, PlannedStep) else None
            self._apply_terrain(state)
            if self.fight is None:
                self.fight = FightTurnSkill(self.cfg, self.character_class, self.strategy_hook, clock=self.clock)
            r = self._run(self.fight, state, ctx)
            return self._checked(r) if isinstance(r, PlannedStep) else None
        if s != "fight" and (self.fight is not None or self.placement is not None):
            self.fight = self.placement = None   # fight over: fresh skills next time
            self.placement_view = None
        # the replica is dropped in observe_frame once the fight is over (it is read before
        # the fight, so it must survive the map screen until then)
        if s == "dialog":
            await self.dialog.prepare(state, ctx.llm)
            r = self._run(self.dialog, state, ctx)
            return self._checked(r) if isinstance(r, PlannedStep) else None
        if s != "world":
            return None
        if not self.goal:
            return None
        if self._talk_menu_open():
            r = self._run(self.goal_skills[0], state, ctx)
            return self._checked(r) if isinstance(r, PlannedStep) else None
        if self.scan_request is not None:
            return None                       # waiting for the held-key capture
        dashboard_goal = (self.goal or "").lower().startswith(("attack", "fight sequence", "fight now", "combat"))
        if self._needs_entity_scan(state) and not dashboard_goal:   # the dashboard did its own Z scan
            # every map arrival: see monsters (aggro) and NPCs before acting
            self._request_scan(self.cfg["keys"]["show_entities"], "entities", state.map.coords)
            return None
        if not self.goal_skills:
            built = self._build_goal(state)
            if isinstance(built, str):
                self._ask(built)
                return None
            if not built:
                return None
            self.goal_skills = built
        while self.goal_skills:
            sk = self.goal_skills[0]
            r = self._run(sk, state, ctx)
            if isinstance(r, PlannedStep):
                return self._checked(r)
            if r.status == "done":
                self.goal_skills.pop(0)
                if not self.goal_skills:
                    log.info("goal %r done: %s", self.goal, r.reason)
                    self.goal = None
                    return None
                continue
            if r.status == "failed":
                self._ask(f"{sk.name} failed: {r.reason}")
            elif r.status == "scan":
                self._request_scan(r.data["key"], sk.name, state.map.coords)
            return None
        return None

    def on_result(self, result: StepResult, state: GameState) -> None:
        self.last_action = result.step.action
        self.last_meta = result.step.meta
        self.safety.on_result(result)
        skill = result.step.meta.get("skill")
        for sk in (self.popup, self.dialog, self.placement, self.fight, *self.goal_skills):
            if sk is not None and getattr(sk, "name", None) == skill:
                sk.on_result(result, state)
        if skill == "travel":
            try:
                self.graph.save(self.maps_path)
            except OSError as e:  # never let persistence break the loop
                log.warning("could not save map graph: %s", e)
