"""Account-scoped protocol sessions.

One Dofus client connection must never share world state or a writable
transport with another account. This registry is deliberately free of
credentials and UI concerns: the launcher/auth layer owns secrets, while the
dashboard and planner address a stable account/session scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any

from ..managed_transport import ManagedTransport


@dataclass
class AccountSession:
    account_id: int
    session_id: str | None = None
    profile_id: str | None = None
    transport: ManagedTransport = field(default_factory=ManagedTransport)
    scan: dict[str, Any] | None = None
    updated_at_ms: int | None = None

    def update_scan(self, scan: dict[str, Any]) -> None:
        self.scan = dict(scan)
        self.updated_at_ms = int(time() * 1000)

    def to_json(self) -> dict[str, Any]:
        character = (self.scan or {}).get("character") or {}
        return {
            "account_id": self.account_id,
            "session_id": self.session_id,
            "profile_id": self.profile_id,
            "updated_at_ms": self.updated_at_ms,
            "character": {key: character.get(key) for key in ("id", "name", "level")},
            "map_id": (self.scan or {}).get("map_id"),
            "transport": self.transport.status(),
        }


class AccountSessionRegistry:
    """Owns isolated state/transport containers keyed by account id."""

    def __init__(self) -> None:
        self._sessions: dict[int, AccountSession] = {}

    def get_or_create(self, account_id: int) -> AccountSession:
        account_id = int(account_id)
        return self._sessions.setdefault(account_id, AccountSession(account_id))

    def bind_transport(self, account_id: int, session_id: str, transport: ManagedTransport, profile_id: str | None = None) -> AccountSession:
        record = self.get_or_create(account_id)
        for other_id, other in self._sessions.items():
            if other_id != record.account_id and other.session_id == session_id:
                raise ValueError("managed session is already bound to another account")
        record.session_id, record.transport = str(session_id), transport
        if profile_id is not None:
            record.profile_id = str(profile_id)
        return record

    def update_scan(self, account_id: int, scan: dict[str, Any]) -> AccountSession:
        record = self.get_or_create(account_id)
        record.update_scan(scan)
        return record

    def get(self, account_id: int) -> AccountSession | None:
        return self._sessions.get(int(account_id))

    def list(self) -> list[dict[str, Any]]:
        return [self._sessions[key].to_json() for key in sorted(self._sessions)]
