"""Protocol-derived pod decisions; bank actions remain explicitly non-automatic."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PodDecision:
    state: str
    percent: float | None
    reason: str


@dataclass
class InventoryPolicy:
    caution_pct: float = 70.0
    bank_pct: float = 85.0
    emergency_pct: float = 95.0

    def decide(self, inventory: dict[str, Any]) -> PodDecision:
        pods, maximum = inventory.get("weight"), inventory.get("weight_max")
        if pods is None or maximum is None or int(maximum) <= 0:
            return PodDecision("unknown", None, "pods are not protocol-confirmed")
        pct = round(100 * int(pods) / int(maximum), 1)
        if pct >= self.emergency_pct:
            return PodDecision("bank_required", pct, "pods are critically full; stop current work and route to bank")
        if pct >= self.bank_pct:
            return PodDecision("bank_after_safe_action", pct, "finish only the current confirmed action, then route to bank")
        if pct >= self.caution_pct:
            return PodDecision("caution", pct, "avoid long gathering/fight detours")
        return PodDecision("continue", pct, "pods are within policy")
