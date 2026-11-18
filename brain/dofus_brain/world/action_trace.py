"""Bounded, account-local evidence for protocol action decisions."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

from .state import WorldState


class ActionTrace:
    """Retain the small decision chain needed to diagnose an action safely."""

    def __init__(self, limit: int = 120) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=limit)

    def record(self, category: str, detail: dict[str, Any], state: WorldState | None = None) -> None:
        entry: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "category": category,
            "detail": dict(detail),
        }
        if state is not None:
            entry["snapshot"] = {
                "map_id": state.map_id, "cell": state.character.get("cell"),
                "fight_phase": state.fight_phase, "hp": state.character.get("hp"),
                "hp_max": state.character.get("hp_max"), "dialogue_open": state.dialog is not None,
            }
        self._entries.append(entry)

    def payload(self, limit: int = 40) -> dict[str, Any]:
        return {"entries": list(self._entries)[-max(1, min(int(limit), 120)):], "retained": len(self._entries)}
