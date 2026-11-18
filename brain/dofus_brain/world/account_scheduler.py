"""Fair serialized scheduler for independent account jobs.

One local LLM/runtime is shared, but a blocked account never prevents another
idle account from receiving its next safe observation/planning turn.
"""
from __future__ import annotations
from collections import deque


class AccountScheduler:
    def __init__(self) -> None:
        self._queue: deque[int] = deque()
        self._blocked: set[int] = set()

    def request(self, account_id: int) -> None:
        account_id = int(account_id)
        if account_id not in self._queue:
            self._queue.append(account_id)

    def block(self, account_id: int) -> None: self._blocked.add(int(account_id))
    def unblock(self, account_id: int) -> None: self._blocked.discard(int(account_id))

    def next(self) -> int | None:
        for _ in range(len(self._queue)):
            account_id = self._queue.popleft(); self._queue.append(account_id)
            if account_id not in self._blocked:
                return account_id
        return None
