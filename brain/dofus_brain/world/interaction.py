"""Read-only, protocol-derived interaction plans for resources and doors."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from ..game.grid import Grid
from .roleplay_path import find_roleplay_path, roleplay_neighbours
from .state import WorldState


def plan_interactive(state: WorldState, element_id: int, exit_cells: Iterable[int] = (),
                     grid: Grid | None = None) -> dict[str, Any]:
    """Plan approach then use; it emits no movement or game request itself."""
    grid = grid or Grid()
    item = state.interactives.get(int(element_id))
    if item is None:
        return {"ready": False, "reason": "interactive is not present in current protocol state"}
    if item.available is False:
        return {"ready": False, "reason": "interactive is not currently available"}
    if item.cell is None or not grid.is_valid(int(item.cell)):
        return {"ready": False, "reason": "interactive has no current map cell"}
    if int(item.cell) in {int(cell) for cell in exit_cells}:
        return {"ready": False, "reason": "interactive cell is a world-map transition; route it instead"}
    player_cell = state.character.get("cell")
    if player_cell is None or not grid.is_valid(int(player_cell)):
        return {"ready": False, "reason": "current character cell is not protocol-confirmed"}
    instances = [entry for entry in item.details.get("skill_instances", ()) if entry.get("skill_instance_uid") is not None]
    if not instances:
        return {"ready": False, "reason": "interactive has no live usable skill instance"}
    blocked = {cell_id for cell_id, cell in state.cells.items() if cell.movable is False}
    # Resources often occupy a non-walkable cell. Prefer an adjacent reachable
    # cell, then only use its own cell when the asset explicitly allows it.
    candidates = roleplay_neighbours(grid, int(item.cell))
    if int(item.cell) not in blocked:
        candidates.append(int(item.cell))
    paths = [path for target in candidates if (path := find_roleplay_path(int(player_cell), target, blocked, grid)) is not None]
    if not paths:
        return {"ready": False, "reason": "no reachable approach cell for interactive"}
    path = min(paths, key=lambda value: (len(value.cells), value.cells[-1]))
    skill = instances[0]
    return {
        "ready": True, "kind": "move_then_interact",
        "element": {"id": item.id, "name": item.name, "cell": item.cell, "skills": item.skills},
        "approach": {"cell": path.cells[-1], "cells": list(path.cells), "key_cells": list(path.key_cells)},
        "use": {"element_id": item.id, "skill_instance_uid": int(skill["skill_instance_uid"]),
                "skill_id": skill.get("skill_id")},
        "confirmation": "wait for this character's movement event before InteractiveUseRequest",
    }
