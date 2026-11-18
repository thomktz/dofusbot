"""Exceptions raised by dofus_control."""
from __future__ import annotations


class ControlError(RuntimeError):
    """Base class for every error raised by dofus_control."""


class PlatformNotSupported(ControlError):
    """The real controller only runs on macOS (use dofus_brain.mock_control elsewhere)."""


class PermissionMissing(ControlError):
    """A macOS privacy permission (Accessibility / Screen Recording / Input Monitoring) is missing."""

    def __init__(self, permission: str, hint: str | None = None):
        self.permission = permission
        msg = (
            f"macOS permission missing: {permission}. Grant it in System Settings -> "
            f"Privacy & Security -> {_PANE.get(permission, permission)} to the app that runs "
            "Python (Terminal, iTerm, VS Code...), then quit and reopen that app."
        )
        if hint:
            msg += f" ({hint})"
        super().__init__(msg)


_PANE = {
    "accessibility": "Accessibility",
    "screen_recording": "Screen & System Audio Recording",
    "input_monitoring": "Input Monitoring",
}


class WindowNotFound(ControlError):
    """No matching on-screen window."""


class WindowMoved(ControlError):
    """The target window is not where align_window() put it; actions are refused."""


class WindowNotFrontmost(ControlError):
    """The target app is not frontmost; input would go to another app."""


class WindowCovered(ControlError):
    """Another window covers the point we were about to click."""


class AlignmentFailed(ControlError):
    """The window could not be moved/resized onto the target rect."""


class CaptureFailed(ControlError):
    """Screen capture failed."""


class CalibrationFailed(ControlError):
    """The slot corner markers could not be located (protocol §3)."""


class InvalidAction(ControlError, ValueError):
    """The action dict does not follow protocol §2.3 or targets an invalid point."""


class KillSwitchTripped(ControlError):
    """The kill switch is tripped; no input is sent until reset_kill()."""

    def __init__(self, reason: str = "kill switch tripped"):
        self.reason = reason
        super().__init__(f"kill switch tripped: {reason}")
