"""Pure, conservative planning facts derived from decoded protocol snapshots.

These helpers never send a packet.  They give the LLM evidence for goals such
as farming or choosing a monster pack while making uncertainty explicit.
"""
from __future__ import annotations

from typing import Any


def farming_candidates(result: dict[str, Any], resource: str | None = None) -> dict[str, Any]:
    """Rank currently visible, usable resources; never claim off-map knowledge."""
    wanted = (resource or "").strip().casefold()
    candidates = []
    for item in result.get("interactives") or ():
        if item.get("available") is False or item.get("cell") is None:
            continue
        name = str(item.get("resource_name") or item.get("name") or "")
        if wanted and wanted not in name.casefold():
            continue
        skills = [entry for entry in item.get("skill_instances") or () if entry.get("skill_instance_uid") is not None]
        if not skills:
            continue
        candidates.append({"element_id": item.get("id"), "cell": item.get("cell"), "name": name or None,
                           "job": item.get("job_name"), "level_min": item.get("level_min"),
                           "skills": [{"skill_id": entry.get("skill_id"), "name": entry.get("name_id")}
                                      for entry in skills]})
    candidates.sort(key=lambda item: (str(item.get("name") or "").casefold(), int(item["cell"])))
    return {"scope": "current_protocol_map", "requested_resource": resource, "candidates": candidates,
            "reason": None if candidates else "no enabled matching resource is visible on the current map"}


def quest_preparation(quest_plan: dict[str, Any]) -> dict[str, Any]:
    """Flatten verified quest needs into a safe, compact planner view."""
    actions: list[dict[str, Any]] = []
    for quest in quest_plan.get("active_quest_plans") or ():
        missing = list(quest.get("missing") or ())
        if missing:
            actions.append({"kind": "obtain_items", "quest_id": quest.get("quest_id"),
                            "quest": quest.get("quest"), "missing": missing})
            continue
        for objective in quest.get("objectives") or ():
            if objective.get("reached"):
                continue
            actions.append({"kind": "objective", "quest_id": quest.get("quest_id"), "quest": quest.get("quest"),
                            "objective": objective.get("description"), "map_id": objective.get("map_id"),
                            "coords": objective.get("coords"), "type": objective.get("type_name")})
            break
    return {"source": quest_plan.get("source"), "actions": actions}


def pack_assessment(character: dict[str, Any] | None, group: dict[str, Any]) -> dict[str, Any]:
    """Report transparent pack facts and only a coarse risk tier.

    Without monster spells/resistances or an in-fight snapshot, a claim that a
    pack is beatable would be fiction.  ``unknown`` is deliberate.
    """
    character = character or {}
    members = list(group.get("members") or ())
    levels = [int(member["level"]) for member in members if member.get("level") is not None]
    total = sum(levels) if levels else None
    own_level = character.get("level")
    hp, hp_max = character.get("hp"), character.get("hp_max")
    hp_ratio = round(float(hp) / float(hp_max), 3) if isinstance(hp, (int, float)) and isinstance(hp_max, (int, float)) and hp_max > 0 else None
    tier, reason = "unknown", "combat stats/spells are incomplete"
    if own_level is not None and total is not None:
        # This is a gate for human/LLM attention, not a win prediction.
        ratio = total / max(1, int(own_level))
        if hp_ratio is not None and hp_ratio < 0.45:
            tier, reason = "high_risk", "current health is below 45%"
        elif ratio > 2.0:
            tier, reason = "high_risk", "combined monster level exceeds twice character level"
        elif ratio <= 1.25 and (hp_ratio is None or hp_ratio >= 0.75):
            tier, reason = "review", "level/health gate is favourable; inspect live fight stats before attacking"
        else:
            tier, reason = "review", "requires a live fight-stat assessment"
    full_health = (isinstance(hp, (int, float)) and isinstance(hp_max, (int, float)) and hp_max > 0 and hp >= hp_max)
    health_known = isinstance(hp, (int, float)) and isinstance(hp_max, (int, float)) and hp_max > 0
    return {"entity_id": group.get("entity_id"), "cell": group.get("cell"), "members": len(members),
            "member_levels": levels, "total_level": total, "character_level": own_level,
            "health_ratio": hp_ratio, "full_health_required": True, "fight_ready": full_health,
            "fight_readiness_reason": "health is protocol-confirmed at full" if full_health else (
                f"wait for full health ({int(hp)}/{int(hp_max)}) before initiating a fight" if health_known
                else "health is not protocol-confirmed; do not initiate a fight"),
            "risk": tier, "reason": reason}
