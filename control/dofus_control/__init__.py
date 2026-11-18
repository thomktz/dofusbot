"""dofus_control: macOS window / capture / input / kill-switch layer (protocol §4).

Importing this package never imports PyObjC; the macOS backend is loaded lazily
when a Controller is created on macOS.
"""
from .controller import Controller
from .errors import (
    AlignmentFailed,
    CalibrationFailed,
    CaptureFailed,
    ControlError,
    InvalidAction,
    KillSwitchTripped,
    PermissionMissing,
    PlatformNotSupported,
    WindowCovered,
    WindowMoved,
    WindowNotFound,
    WindowNotFrontmost,
)
from .human import HumanProfile
from .types import (
    DEFAULT_MARKERS,
    DisplayInfo,
    Frame,
    MarkerSpec,
    PermissionStatus,
    RawWindow,
    Rect,
    WindowInfo,
)

__version__ = "0.1.0"

__all__ = [
    "Controller", "Rect", "WindowInfo", "Frame", "PermissionStatus", "KillSwitchTripped",
    "DEFAULT_MARKERS", "MarkerSpec", "HumanProfile", "DisplayInfo", "RawWindow",
    "ControlError", "PermissionMissing", "WindowNotFound", "AlignmentFailed", "CaptureFailed",
    "CalibrationFailed", "InvalidAction", "PlatformNotSupported", "WindowMoved",
    "WindowNotFrontmost", "WindowCovered",
]
