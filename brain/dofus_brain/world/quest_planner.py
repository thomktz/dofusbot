"""Read-only quest planning over live protocol state and local guide metadata."""
from __future__ import annotations

import re
from typing import Any


def _name_key(value: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9àâçéèêëîïôûùüÿñæœ]+", " ", (value or "").casefold()).split())


class QuestPlanner:
    """Produce a compact, non-mutating plan for currently active quests.

    Routing and acquisition are deliberately left as named subgoals until the
    movement/shop protocol layers exist. This prevents a high-level planner
    from pretending that a missing item is already actionable.
    """

    def __init__(self, game_data: Any, guides: Any):
        self.game_data, self.guides = game_data, guides

    @staticmethod
    def _inventory(result: dict[str, Any]) -> dict[str, int]:
        quantities: dict[str, int] = {}
        for item in (result.get("inventory") or {}).get("objects", []):
            name = item.get("name")
            quantity = item.get("quantity")
            if name is None or quantity is None:
                continue
            key = _name_key(str(name))
            quantities[key] = quantities.get(key, 0) + max(0, int(quantity))
        return quantities

    def plan(self, result: dict[str, Any]) -> dict[str, Any]:
        inventory = self._inventory(result)
        plans: list[dict[str, Any]] = []
        for live in (result.get("quests") or {}).get("active", []):
            quest_id = live.get("quest_id")
            if quest_id is None:
                continue
            quest_id = int(quest_id)
            info = self.game_data.quest_info(quest_id, live.get("step_id"))
            graph = self.game_data.quest_graph(quest_id)
            guide = self.guides.resolve(quest_id, info.get("name"))
            success_preflight = []
            for group in graph.get("success_groups", []):
                success = self.guides.resolve_success(int(group["achievement_id"]))
                if success.get("found"):
                    success_preflight.append(success)
            requirements: dict[str, int] = {}
            for success in success_preflight:
                for item in (success.get("preparation") or {}).get("items", []):
                    if isinstance(item, list) and len(item) == 2:
                        requirements[str(item[0])] = max(requirements.get(str(item[0]), 0), int(item[1]))
            checklist = [
                {"item": item, "needed": needed, "have": inventory.get(_name_key(item), 0),
                 "missing": max(0, needed - inventory.get(_name_key(item), 0))}
                for item, needed in sorted(requirements.items(), key=lambda pair: _name_key(pair[0]))
            ]
            missing = [item for item in checklist if item["missing"] > 0]
            objectives = []
            for objective in live.get("objectives", []):
                objectives.append({key: objective.get(key) for key in (
                    "objective_id", "description", "type_name", "reached", "map_id", "coords", "parameter_labels")})
            plans.append({
                "quest_id": quest_id, "quest": info.get("name"),
                "step_id": info.get("step_id"), "step": info.get("step_name"),
                "step_description": info.get("step_description"), "objectives": objectives,
                "status": "needs_preparation" if missing else "ready_for_next_objective",
                "checklist": checklist, "missing": missing,
                "guide": guide,
                "success_preflight": success_preflight,
                "prerequisites": graph.get("prerequisites", []),
                "unlocks": graph.get("unlocks", []),
            })
        return {"source": "dofus-protocol + installed-assets + on-demand-guide-index",
                "active_quest_plans": plans, "inventory_items_observed": len(inventory)}
