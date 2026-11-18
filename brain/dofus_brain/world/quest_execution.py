"""Bounded execution bridge from a verified quest plan to one world intent."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from .orchestrator import WorldIntent


@dataclass(frozen=True)
class QuestStep:
    state: str
    reason: str
    intent: WorldIntent | None = None


class QuestExecutor:
    """Never infer quest instructions beyond live/local evidence."""
    def next_step(self, plan: dict[str, Any], state: Any) -> QuestStep:
        active = list(plan.get("active_quest_plans") or [])
        if not active:
            return QuestStep("idle", "no active protocol-confirmed quest")
        quest = active[0]
        if quest.get("missing"):
            return QuestStep("needs_preparation", "quest preparation item is missing")
        guide = quest.get("guide") or {}
        if not guide.get("found"):
            return QuestStep("needs_guide", "no local human guide is available")
        objectives = [item for item in quest.get("objectives", ()) if not item.get("reached")]
        if not objectives:
            return QuestStep("waiting_for_server", "all known objectives are complete; await quest update")
        objective = objectives[0]
        target_map = objective.get("map_id")
        if target_map is not None and getattr(state, "map_id", None) != int(target_map):
            return QuestStep("navigate", "objective has a verified target map", WorldIntent("navigate_to_map", {"map_id": int(target_map)}, "quest objective"))
        return QuestStep("needs_human_or_guide", "objective needs a specific dialogue/resource mapping")
