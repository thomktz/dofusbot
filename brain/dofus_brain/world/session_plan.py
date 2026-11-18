"""Durable, account-scoped session plans and conservative checkpoints."""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from time import time
from typing import Any


@dataclass
class SessionPlan:
    account_id: int
    duration_minutes: int = 180
    jobs: list[dict[str, Any]] = field(default_factory=list)
    stop_on_full_pods: bool = True
    started_at: int | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)

    def status(self) -> dict[str, Any]:
        elapsed = 0 if self.started_at is None else max(0, int(time()) - self.started_at)
        return {"account_id": self.account_id, "duration_minutes": self.duration_minutes,
                "elapsed_seconds": elapsed, "remaining_seconds": max(0, self.duration_minutes * 60 - elapsed),
                "jobs": self.jobs, "checkpoint": self.checkpoint,
                "expired": elapsed >= self.duration_minutes * 60}


class SessionPlanStore:
    def __init__(self, root: str | Path): self.root = Path(root)
    def path(self, account_id: int) -> Path: return self.root / f"account-{int(account_id)}.json"
    def save(self, plan: SessionPlan) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path(plan.account_id).write_text(json.dumps(asdict(plan), indent=2) + "\n")
    def load(self, account_id: int) -> SessionPlan | None:
        try: return SessionPlan(**json.loads(self.path(account_id).read_text()))
        except (OSError, TypeError, ValueError): return None
