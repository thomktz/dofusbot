"""Central multi-account ownership model, following AstraBot's BotManager."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..managed_transport import ManagedTransport
from .sessions import AccountSessionRegistry


@dataclass
class AccountManager:
    """One process owns all account sessions; no terminal-per-account model."""
    sessions: AccountSessionRegistry = field(default_factory=AccountSessionRegistry)
    active_account_id: int = 0
    legacy_observation_account_id: int = 0

    def primary_transport(self) -> ManagedTransport:
        return self.sessions.get_or_create(self.active_account_id).transport

    def transport_for_identified_account(self, account_id: int) -> ManagedTransport:
        """Reserve the isolated transport container for a login-identified account.

        This is intentionally allowed to create a session: unlike dashboard
        selection, the account id came from Ankama's authoritative connection
        response rather than user supplied UI input.
        """
        return self.sessions.get_or_create(int(account_id)).transport

    def select(self, account_id: int) -> None:
        """Make one already-known account the explicit command scope.

        Selection is deliberately not creation: a dashboard cannot fabricate an
        account/session pair, and it can never silently fall back to account 0
        when a requested account has not connected yet.
        """
        account_id = int(account_id)
        if self.sessions.get(account_id) is None:
            raise ValueError(f"account {account_id} is not connected")
        self.active_account_id = account_id

    def transport(self, account_id: int | None = None) -> ManagedTransport:
        selected = self.active_account_id if account_id is None else int(account_id)
        session = self.sessions.get(selected)
        if session is None:
            raise ValueError(f"account {selected} is not connected")
        return session.transport

    def scan(self, account_id: int | None = None) -> dict[str, Any] | None:
        selected = self.active_account_id if account_id is None else int(account_id)
        session = self.sessions.get(selected)
        return None if session is None or session.scan is None else dict(session.scan)

    def observe_primary(self, scan: dict[str, Any]) -> None:
        """Attach the old one-listener scan to its fixed source account.

        Dashboard selection only changes the command scope.  Until the proxy
        listener has independently identified multiple game connections, it
        must never relabel the sole observed snapshot as another account.
        """
        self.sessions.update_scan(self.legacy_observation_account_id, scan)

    def observe(self, account_id: int, scan: dict[str, Any]) -> None:
        self.sessions.update_scan(account_id, scan)

    def bind_identified_transport(self, account_id: int, session_id: str, transport: ManagedTransport, profile_id: str | None = None) -> None:
        """Atomically bind a proxy-identified connection to its account.

        The proxy layer calls this only after the authoritative login/game
        identification response.  It is the handoff point that replaces the
        temporary legacy account-0 *pcap* stream. Passive captures remain
        account 0; authoritative proxy frames are attached separately by the
        proxy bridge and must never be overwritten by an unrelated capture.
        """
        account_id = int(account_id)
        self.sessions.bind_transport(account_id, session_id, transport, profile_id)

    def payload(self) -> dict[str, Any]:
        return {"active_account_id": self.active_account_id, "accounts": self.sessions.list(),
                "mode": "independent", "model_runtime": "shared"}
