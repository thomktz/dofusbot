"""TCC permission checks / prompts. macOS only.

Permissions are granted to the *responsible* app (Terminal, iTerm2, VS Code,
PyCharm...) rather than to python itself. After granting, quit and reopen that
app: the running process keeps the old answer.
"""
from __future__ import annotations

import logging

import HIServices
import Quartz

from ..types import PermissionStatus

log = logging.getLogger(__name__)


def _call(name: str, default):
    fn = getattr(Quartz, name, None)
    if fn is None:
        return default
    try:
        return bool(fn())
    except Exception:
        log.debug("%s failed", name, exc_info=True)
        return default


def status() -> PermissionStatus:
    return PermissionStatus(
        accessibility=bool(HIServices.AXIsProcessTrusted()),
        screen_recording=_call("CGPreflightScreenCaptureAccess", False),
        input_monitoring=_call("CGPreflightListenEventAccess", None),
    )


def request() -> None:
    """Show the system prompts (each only once per app; afterwards use System Settings)."""
    prompt_key = getattr(HIServices, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
    try:
        HIServices.AXIsProcessTrustedWithOptions({prompt_key: True})
    except Exception:
        log.exception("AXIsProcessTrustedWithOptions failed")
    _call("CGRequestScreenCaptureAccess", False)
    _call("CGRequestListenEventAccess", False)
