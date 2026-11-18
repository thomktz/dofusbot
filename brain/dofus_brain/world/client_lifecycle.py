"""Per-profile local client lifecycle records.

This is intentionally process-agnostic: server code supplies the actual
launcher once a user presses Launch.  It prevents the old singleton Zaap port
and PID fields from being accidentally shared by two profiles.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClientLaunch:
    profile_id: str
    zaap_port: int
    state: str = "idle"
    pid: int | None = None
    error: str | None = None

    def public(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "zaap_port": self.zaap_port,
                "state": self.state, "pid": self.pid, "error": self.error}


class ClientLifecycleManager:
    """Reserves a deterministic, non-overlapping loopback Zaap port per profile."""
    def __init__(self, first_port: int = 26116):
        self.first_port = int(first_port)
        self._clients: dict[str, ClientLaunch] = {}

    def get_or_create(self, profile_id: str) -> ClientLaunch:
        profile_id = str(profile_id)
        if not profile_id:
            raise ValueError("profile id is required")
        if profile_id not in self._clients:
            self._clients[profile_id] = ClientLaunch(profile_id, self.first_port + len(self._clients))
        return self._clients[profile_id]

    def mark_launched(self, profile_id: str, pid: int) -> ClientLaunch:
        record = self.get_or_create(profile_id)
        if record.state in {"launching", "launched"}:
            raise ValueError("that account client is already launching or running")
        record.state, record.pid, record.error = "launched", int(pid), None
        return record

    def mark_error(self, profile_id: str, error: str) -> ClientLaunch:
        record = self.get_or_create(profile_id)
        record.state, record.error = "error", str(error)
        return record

    def status(self) -> list[dict[str, object]]:
        return [self._clients[key].public() for key in sorted(self._clients)]
