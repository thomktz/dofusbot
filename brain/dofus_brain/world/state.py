"""Stable world model for protocol events.

The network decoder remains responsible for wire-format details. This module owns
the state shape consumed by the LLM, UI, and deterministic planners, so those
consumers do not depend on obfuscated message aliases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class MapCell:
    id: int
    movable: bool | None = None
    line_of_sight: bool | None = None
    elevation: int | None = None
    start_team: str | None = None
    obstacle: bool = False


@dataclass
class Actor:
    id: int
    kind: str  # player | npc | monster_group | monster | summon | unknown
    name: str | None = None
    cell: int | None = None
    map_id: int | None = None
    team: int | None = None
    level: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    alive: bool = True


@dataclass
class Interactive:
    id: int
    cell: int | None = None
    name: str | None = None
    gfx_id: int | None = None
    available: bool | None = None
    skills: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorldState:
    map_id: int | None = None
    coords: tuple[int, int] | None = None
    subarea_id: int | None = None
    cells: dict[int, MapCell] = field(default_factory=dict)
    actors: dict[int, Actor] = field(default_factory=dict)
    interactives: dict[int, Interactive] = field(default_factory=dict)
    movement_paths: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    # Fight state is driven exclusively by normalized protocol events.  It is
    # intentionally separate from role-play actors/map data.
    fight_phase: str = "out_of_fight"
    fight_turn: int | None = None
    fight_actor_id: int | None = None
    legal_placement_cells: set[int] = field(default_factory=set)
    pending_action: dict[str, Any] | None = None
    character: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, Any] = field(default_factory=dict)
    dialog: dict[str, Any] | None = None
    spells: list[dict[str, Any]] = field(default_factory=list)
    shortcuts: list[dict[str, Any]] = field(default_factory=list)
    quests: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "WorldState":
        return cls(cells={i: MapCell(i) for i in range(560)})

    @classmethod
    def from_scan(cls, result: dict[str, Any]) -> "WorldState":
        """Build a canonical snapshot from the current bounded scan format."""
        state = cls.empty()
        fight_snapshot = result.get("fight") or {}
        if "fight" in result:
            fight_active = bool(fight_snapshot.get("active"))
        else:
            # Keep compatibility with older callers that only supplied the
            # normalized fight event stream.
            fight_active = False
            for event in result.get("fight_events", ()):
                if event.get("kind") in {"fighter_show", "placement_update", "turn_marker", "spell_cast"}:
                    fight_active = True
                elif event.get("kind") == "fight_result":
                    fight_active = False
        # A fight has its own map and actor set. Never let the role-play map
        # leak into the combat view while that snapshot is active.
        state.map_id = (fight_snapshot.get("map_id") or result.get("fight_map_id")) if fight_active else (result.get("map_id") or result.get("fight_map_id"))
        position = result.get("map_position") or {}
        if position.get("x") is not None and position.get("y") is not None:
            state.coords = (int(position["x"]), int(position["y"]))
        state.subarea_id = position.get("sub_area_id")
        state.character = dict(result.get("character") or {})
        state.inventory = dict(result.get("inventory") or {})
        state.dialog = dict(result["dialog"]) if isinstance(result.get("dialog"), dict) else None
        state.spells = [dict(spell) for spell in result.get("spells", ())]
        state.shortcuts = [dict(shortcut) for shortcut in result.get("shortcuts", ())]
        state.quests = dict(result.get("quests") or {})
        terrain = result.get("terrain") or {}
        blocked = set(terrain.get("blocked", ())) | set(terrain.get("holes", ()))
        red = set(terrain.get("red", ()))
        blue = set(terrain.get("blue", ()))
        for cell_id, cell in state.cells.items():
            cell.obstacle = cell_id in blocked
            cell.movable = None if cell_id in terrain.get("holes", ()) else cell_id not in blocked
            cell.start_team = "red" if cell_id in red else "blue" if cell_id in blue else None

        for group in (() if fight_active else result.get("groups", ())):
            members = group.get("members", [])
            state.actors[int(group["entity_id"])] = Actor(
                int(group["entity_id"]), "monster_group", cell=group.get("cell"),
                metadata={"members": members},
            )
        for player in (() if fight_active else result.get("players", ())):
            actor_id = int(player["entity_id"])
            state.actors[actor_id] = Actor(actor_id, "player", player.get("name"), player.get("cell"))
        for item in (() if fight_active else result.get("interactives", ())):
            item_id = item.get("id")
            if item_id is not None:
                state.interactives[int(item_id)] = Interactive(
                    int(item_id), item.get("cell"), item.get("name"), item.get("gfx_id"),
                    item.get("available"), item.get("skills", []), item.get("metadata", {}),
                    {k: v for k, v in item.items() if k not in {"id", "cell", "name", "gfx_id", "available", "skills", "metadata"}})
        map_events = () if fight_active else result.get("map_events", ())
        latest_snapshot_offset = max(
            (int(event.get("offset", -1)) for event in map_events if event.get("kind") == "map_snapshot"),
            default=-1,
        )
        for event in map_events:
            if event.get("kind") == "map_snapshot":
                # MapComplementaryInformation is authoritative for a new
                # role-play map. Negative role-play IDs are reused between
                # maps, so carrying metadata forward can turn Berb Nhin into
                # a perfectly valid but unrelated guard from an older map.
                state.actors.clear()
                for actor_data in event.get("actors", ()):
                    actor_id = actor_data.get("entity_id")
                    if actor_id is None:
                        continue
                    actor_id = int(actor_id)
                    state.actors[actor_id] = Actor(
                        actor_id, actor_data.get("kind", "unknown"), actor_data.get("name"),
                        actor_data.get("cell"), state.map_id,
                        metadata=actor_data.get("metadata", {}),
                        alive=True)
            elif event.get("kind") == "actors_snapshot":
                for actor_data in event.get("actors", ()):
                    actor_id = actor_data.get("entity_id")
                    if actor_id is not None:
                        actor_id = int(actor_id)
                        previous = state.actors.get(actor_id)
                        state.actors[actor_id] = Actor(
                            actor_id, previous.kind if previous else actor_data.get("kind", "unknown"),
                            actor_data.get("name") or (previous.name if previous else None),
                            actor_data.get("cell"), state.map_id,
                            metadata=(previous.metadata if previous and previous.metadata else actor_data.get("metadata", {})),
                            alive=previous.alive if previous else True)
            elif event.get("kind") == "movement":
                if int(event.get("offset", -1)) < latest_snapshot_offset:
                    continue
                if event.get("actor_id") not in state.actors:
                    continue
                state.apply_event(event)
            elif event.get("kind") == "element_removed":
                state.interactives.pop(int(event["element_id"]), None)
                state.apply_event(event)
            elif event.get("kind") in {"interactive_used", "interactive_ended", "interactive_error",
                                        "interactive_update", "stated_update"}:
                # Preserve role-play interaction lifecycle acknowledgements for
                # the executor.  They are not tactical/fight events, but are
                # exactly what confirms a resource use request.
                state.apply_event(event)
        fight_events = result.get("fight_events", ())
        if fight_active:
            for fighter in fight_snapshot.get("fighters", ()):
                actor_id = fighter.get("id")
                if actor_id is None:
                    continue
                is_enemy = int(actor_id) < 0 or fighter.get("team") == 1
                state.actors[int(actor_id)] = Actor(
                    int(actor_id), "monster" if is_enemy else "player",
                    fighter.get("name"), fighter.get("cell"), state.map_id,
                    team=fighter.get("team"), level=fighter.get("level"),
                    metadata={k: fighter[k] for k in ("monster_id", "grade", "hp", "hp_max", "pa", "pm") if k in fighter},
                    alive=fighter.get("alive", True))
            for event in fight_events:
                state.apply_event(event)
        return state

    def apply_event(self, event: dict[str, Any]) -> None:
        """Apply a normalized event; raw protocol aliases stay outside this class."""
        self.events.append(dict(event))
        actor_id = event.get("actor_id")
        if event.get("kind") == "movement" and actor_id is not None:
            actor = self.actors.setdefault(int(actor_id), Actor(int(actor_id), "unknown"))
            path = event.get("path_cells") or []
            actor.cell = event.get("end_cell") or event.get("cell") or (path[-1] if path else actor.cell)
            self.movement_paths.append({"actor_id": int(actor_id), "cells": list(path), "turn": event.get("turn")})
        kind = event.get("kind")
        if kind == "fighter_show":
            self.fight_phase = "placement"
        elif kind == "placement_possible":
            cells = event.get("cells") or event.get("legal_cells") or []
            self.legal_placement_cells = {int(c) for c in cells if 0 <= int(c) < 560}
        elif kind == "placement_update":
            self.fight_phase = "placement"
            if actor_id is not None:
                actor = self.actors.setdefault(int(actor_id), Actor(int(actor_id), "unknown"))
                actor.cell = event.get("cell", actor.cell)
            self.pending_action = None
        elif kind == "turn_marker":
            self.fight_phase = "turn"
            self.fight_turn = event.get("turn", self.fight_turn)
            self.fight_actor_id = actor_id
        elif kind == "turn_end":
            self.fight_phase = "turn_end"
            self.fight_actor_id = actor_id or self.fight_actor_id
        elif kind in {"spell_cast", "movement", "action_effect", "points_change", "hp_loss", "slide"}:
            # A protocol effect is an acknowledgement of the currently
            # executing action; do not leave the planner believing it is
            # still pending once the server has answered.
            if self.fight_phase in {"turn", "turn_end"}:
                self.pending_action = None
        elif kind == "fight_result":
            self.fight_phase = "ended"
            self.fight_actor_id = None
            self.pending_action = None
        if event.get("kind") == "death" and event.get("dead_id") is not None:
            actor = self.actors.setdefault(int(event["dead_id"]), Actor(int(event["dead_id"]), "unknown"))
            actor.alive = False
        if event.get("kind") == "fighter_show" and actor_id is not None:
            actor = self.actors.setdefault(int(actor_id), Actor(int(actor_id), "unknown"))
            actor.cell = event.get("cell", actor.cell)
            actor.team = event.get("team", actor.team)

    def actor_ids_at(self, cell: int) -> list[int]:
        return [actor.id for actor in self.actors.values() if actor.cell == cell and actor.alive]

    def affordances(self) -> list[dict[str, Any]]:
        """Safe action candidates derived from the canonical state.

        These are *not* commands and have no side effects.  They give a future
        LLM/planner a small, stable vocabulary (talk/attack/use/reply) instead
        of asking it to reinterpret protocol-specific actor records.
        """
        actions: list[dict[str, Any]] = []
        if self.fight_phase == "out_of_fight":
            for actor in self.actors.values():
                if not actor.alive or actor.cell is None:
                    continue
                if actor.kind == "npc":
                    actions.append({"kind": "talk", "entity_id": actor.id, "cell": actor.cell,
                                    "label": actor.name or f"NPC {actor.id}"})
                elif actor.kind == "monster_group":
                    members = actor.metadata.get("members", [])
                    named_members = []
                    total_level = 0
                    total_xp = 0
                    for member in members:
                        level = member.get("level") or member.get("level_override")
                        if level is not None:
                            total_level += int(level)
                        xp = member.get("xp")
                        if xp is not None:
                            total_xp += int(xp)
                        name = member.get("name") or f"Monster {member.get('monster_id', '?')}"
                        named_members.append({"name": name, "level": level, "monster_id": member.get("monster_id")})
                    label = " · ".join(member["name"] for member in named_members[:3]) or "Monster group"
                    if len(named_members) > 3:
                        label += f" +{len(named_members) - 3}"
                    actions.append({"kind": "attack", "entity_id": actor.id, "cell": actor.cell,
                                    "label": actor.name or label, "members": named_members,
                                    "total_level": total_level or None, "xp": total_xp or None})
            for item in self.interactives.values():
                if item.cell is None or item.available is False:
                    continue
                actions.append({"kind": "use", "element_id": item.id, "cell": item.cell,
                                "label": item.name or f"Interactive {item.id}", "skills": item.skills,
                                "details": item.details})
        if self.dialog is not None:
            stages = self.dialog.get("stages") or []
            current = stages[-1] if stages else self.dialog
            for option in current.get("options") or []:
                reply_id = option.get("reply_id")
                if reply_id is not None:
                    actions.append({"kind": "dialog_reply", "reply_id": reply_id,
                                    "label": option.get("text") or f"Reply {reply_id}",
                                    "stage": len(stages) or 1})
        return actions

    def agent_context(self) -> dict[str, Any]:
        """Complete *actionable* protocol state for the local LLM.

        Packet blobs and the unbounded event trace are implementation detail,
        but every current fact the agent can reason about or act on is here:
        terrain, actors, resources, inventory, quests, dialogue and fight
        state.  This makes the model a client of the same canonical state as
        the dashboard, never a reader of screenshots or stale UI text.
        """
        terrain = {
            "walkable_cells": [cell_id for cell_id, cell in self.cells.items() if cell.movable is True],
            "blocked_cells": [cell_id for cell_id, cell in self.cells.items() if cell.obstacle],
            "line_of_sight_cells": [cell_id for cell_id, cell in self.cells.items() if cell.line_of_sight is True],
        }
        actors = [{
            "entity_id": actor.id, "kind": actor.kind, "name": actor.name,
            "cell": actor.cell, "level": actor.level, "team": actor.team,
            "alive": actor.alive, "metadata": actor.metadata,
        } for actor in sorted(self.actors.values(), key=lambda actor: actor.id)]
        interactives = [{
            "element_id": item.id, "cell": item.cell, "name": item.name,
            "available": item.available, "skills": item.skills,
            "details": item.details,
        } for item in sorted(self.interactives.values(), key=lambda item: item.id)]
        return {
            "map": {"map_id": self.map_id, "coords": list(self.coords) if self.coords else None,
                    "subarea_id": self.subarea_id, "in_fight": self.fight_phase != "out_of_fight",
                    "terrain": terrain},
            "character": self.character,
            "actors": actors,
            "interactives": interactives,
            # These views deliberately omit packet-only IDs, raw blobs and
            # duplicate stack records.  The full decoded state remains in
            # ``to_json`` for deterministic tools; an LLM gets facts it can
            # reason about without having to reverse the protocol itself.
            "inventory": self._agent_inventory(),
            "spells": self._agent_spells(),
            "quests": self._agent_quests(),
            "dialog": self._agent_dialog(),
            "affordances": self.affordances(),
            "fight": {"phase": self.fight_phase, "turn": self.fight_turn,
                      "actor_id": self.fight_actor_id,
                      "placement_cells": sorted(self.legal_placement_cells)} if self.fight_phase != "out_of_fight" else None,
        }

    def _agent_inventory(self) -> dict[str, Any]:
        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in self.inventory.get("objects", ()):
            # A stack is identified by static item, container and (for worn
            # equipment) its slot.  Quantity is nullable in partial captures;
            # do not turn unknown into zero and accidentally tell the agent an
            # item is absent.
            position = item.get("position")
            key = (item.get("gid"), item.get("name"), item.get("container"),
                   position if item.get("container") == "equipped" else None)
            current = grouped.setdefault(key, {
                "name": item.get("name") or f"Item {item.get('gid', '?')}",
                "item_id": item.get("gid"), "level": item.get("level"),
                "container": item.get("container") or "unknown",
                "slot": item.get("position_name") if item.get("container") == "equipped" else None,
                "quantity": None,
                # Opaque live handles needed only by validated inventory
                # actions. They are not static item IDs and must never be
                # persisted across captures/maps.
                "object_uids": [],
            })
            if item.get("uid") is not None:
                current["object_uids"].append(int(item["uid"]))
            quantity = item.get("quantity")
            if quantity is not None:
                current["quantity"] = int(quantity) + int(current["quantity"] or 0)
        items = sorted(grouped.values(), key=lambda item: (
            item["container"] != "equipped", str(item["name"]).casefold(), int(item["item_id"] or -1)))
        for item in items:
            item["object_uids"].sort()
        weight, maximum = self.inventory.get("weight"), self.inventory.get("weight_max")
        return {
            "kamas": self.inventory.get("kamas"),
            "pods": {"used": weight, "max": maximum,
                     "percent": round(100 * float(weight) / float(maximum), 1)
                     if isinstance(weight, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0 else None},
            "items": items,
        }

    def _agent_spells(self) -> list[dict[str, Any]]:
        slots = {entry.get("spell_id"): entry.get("slot") for entry in self.shortcuts
                 if entry.get("kind") == "spell" and entry.get("spell_id") is not None}
        spells = []
        for spell in self.spells:
            record = {key: spell.get(key) for key in (
                "spell_id", "name", "level", "available", "ap_cost", "min_range", "range",
                "max_cast_turn", "max_cast_target", "min_cast_interval", "initial_cooldown",
                "global_cooldown", "effects") if spell.get(key) is not None}
            record["slot"] = slots.get(spell.get("spell_id"))
            spells.append(record)
        return sorted(spells, key=lambda spell: (spell.get("slot") is None, spell.get("slot") or 999, str(spell.get("name") or "")))

    def _agent_quests(self) -> dict[str, Any]:
        active = []
        for quest in self.quests.get("active", ()):
            active.append({
                "quest_id": quest.get("quest_id"), "name": quest.get("name"),
                "step_id": quest.get("step_id"), "step": quest.get("step_name"),
                "objectives": [{key: objective.get(key) for key in (
                    "objective_id", "reached", "description", "type_name", "completion", "coords", "map_id")
                } for objective in quest.get("objectives", ())],
            })
        return {"active": active, "completed_count": len(self.quests.get("completed", ()))}

    def _agent_dialog(self) -> dict[str, Any] | None:
        if self.dialog is None:
            return None
        stages = self.dialog.get("stages") or [self.dialog]
        def view(stage: dict[str, Any]) -> dict[str, Any]:
            return {"message_id": stage.get("message_id"), "text": stage.get("text"),
                    "options": [{"reply_id": option.get("reply_id"), "text": option.get("text")}
                                for option in stage.get("options", ())]}
        return {"npc": self.dialog.get("npc_name"), "npc_id": self.dialog.get("npc_id"),
                "actor_id": self.dialog.get("actor_id"), "stage": len(stages),
                "history": [view(stage) for stage in stages[:-1]], "current": view(stages[-1])}

    def to_json(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "coords": list(self.coords) if self.coords else None,
            "subarea_id": self.subarea_id,
            "character": self.character,
            "inventory": self.inventory,
            "dialog": self.dialog,
            "spells": self.spells,
            "shortcuts": self.shortcuts,
            "quests": self.quests,
            "cells": {str(k): vars(v) for k, v in self.cells.items()},
            "actors": {str(k): vars(v) for k, v in self.actors.items()},
            "interactives": {str(k): vars(v) for k, v in self.interactives.items()},
            "affordances": self.affordances(),
            "agent_context": self.agent_context(),
            "movement_paths": list(self.movement_paths),
            "events": list(self.events),
            "fight": {
                "phase": self.fight_phase,
                "turn": self.fight_turn,
                "actor_id": self.fight_actor_id,
                "legal_placement_cells": sorted(self.legal_placement_cells),
                "pending_action": self.pending_action,
            },
        }
