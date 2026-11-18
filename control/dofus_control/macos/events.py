"""CGEvent input posting and the kill-switch hotkey event tap. macOS only.

All events are posted to kCGHIDEventTap (they behave like hardware input, so
they go to the frontmost app / the window under the cursor) and carry
``EVENT_MAGIC`` in kCGEventSourceUserData so they can be told apart from real
user input.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

import Quartz

from ..keys import KEYCODE_K, is_kill_hotkey, utf16_len

log = logging.getLogger(__name__)

EVENT_MAGIC = 0x0D0F_A6E7  # tag on every event we post

_MOUSE_TYPES = {
    ("move", "left"): Quartz.kCGEventMouseMoved,
    ("move", "right"): Quartz.kCGEventMouseMoved,
    ("down", "left"): Quartz.kCGEventLeftMouseDown,
    ("up", "left"): Quartz.kCGEventLeftMouseUp,
    ("down", "right"): Quartz.kCGEventRightMouseDown,
    ("up", "right"): Quartz.kCGEventRightMouseUp,
    ("drag", "left"): Quartz.kCGEventLeftMouseDragged,
    ("drag", "right"): Quartz.kCGEventRightMouseDragged,
}
_BUTTONS = {"left": Quartz.kCGMouseButtonLeft, "right": Quartz.kCGMouseButtonRight}

_source_lock = threading.Lock()
_source = None


def _src():
    global _source
    with _source_lock:
        if _source is None:
            _source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        return _source


def _tag_and_post(ev) -> None:
    Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGEventSourceUserData, EVENT_MAGIC)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def mouse_position() -> tuple[float, float]:
    ev = Quartz.CGEventCreate(None)
    p = Quartz.CGEventGetLocation(ev)
    return (float(p.x), float(p.y))


def post_mouse(kind: str, x: float, y: float, button: str = "left", click_state: int = 1) -> None:
    etype = _MOUSE_TYPES[(kind, button)]
    ev = Quartz.CGEventCreateMouseEvent(_src(), etype, Quartz.CGPointMake(float(x), float(y)), _BUTTONS[button])
    if ev is None:
        raise RuntimeError("CGEventCreateMouseEvent failed")
    Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, int(click_state))
    Quartz.CGEventSetFlags(ev, 0)  # don't inherit modifiers the user may be holding
    _tag_and_post(ev)


def post_key(keycode: int, down: bool, flags: int = 0, char: str | None = None) -> None:
    ev = Quartz.CGEventCreateKeyboardEvent(_src(), int(keycode), bool(down))
    if ev is None:
        raise RuntimeError("CGEventCreateKeyboardEvent failed")
    Quartz.CGEventSetFlags(ev, int(flags))
    if char:
        Quartz.CGEventKeyboardSetUnicodeString(ev, utf16_len(char), char)
    _tag_and_post(ev)


def post_text(text: str, down: bool) -> None:
    """Type ``text`` (usually one character) via the unicode-string API: layout independent."""
    ev = Quartz.CGEventCreateKeyboardEvent(_src(), 0, bool(down))
    if ev is None:
        raise RuntimeError("CGEventCreateKeyboardEvent failed")
    Quartz.CGEventSetFlags(ev, 0)
    Quartz.CGEventKeyboardSetUnicodeString(ev, utf16_len(text), text)
    _tag_and_post(ev)


def post_scroll(x: float, y: float, lines: int) -> None:
    """Scroll wheel event; ``lines`` > 0 = wheel up (CG convention)."""
    if hasattr(Quartz, "CGEventCreateScrollWheelEvent2"):
        ev = Quartz.CGEventCreateScrollWheelEvent2(_src(), Quartz.kCGScrollEventUnitLine, 1, int(lines), 0, 0)
    else:
        ev = Quartz.CGEventCreateScrollWheelEvent(_src(), Quartz.kCGScrollEventUnitLine, 1, int(lines))
    if ev is None:
        raise RuntimeError("CGEventCreateScrollWheelEvent failed")
    Quartz.CGEventSetLocation(ev, Quartz.CGPointMake(float(x), float(y)))
    Quartz.CGEventSetFlags(ev, 0)
    _tag_and_post(ev)


def seconds_since_keyboard_input() -> float:
    return float(
        Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateHIDSystemState, Quartz.kCGEventKeyDown
        )
    )


def kill_hotkey_down() -> bool:
    """Polling fallback: is ctrl+alt+cmd+K held right now?"""
    st = Quartz.kCGEventSourceStateHIDSystemState
    if not Quartz.CGEventSourceKeyState(st, KEYCODE_K):
        return False
    return is_kill_hotkey(KEYCODE_K, int(Quartz.CGEventSourceFlagsState(st)))


class HotkeyTap:
    """Listen-only CGEventTap on its own thread + CFRunLoop, calling ``on_hotkey``.

    Needs Input Monitoring (or Accessibility on some versions); ``start()`` returns
    False if the tap can't be created.
    """

    def __init__(self, on_hotkey: Callable[[], None]):
        self.on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._tap = None
        self._loop = None
        self.error: str | None = None

    def _callback(self, proxy, etype, event, refcon):
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event
            if etype == Quartz.kCGEventKeyDown:
                kc = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                flags = Quartz.CGEventGetFlags(event)
                if is_kill_hotkey(kc, flags):
                    threading.Thread(target=self.on_hotkey, name="kill-hotkey", daemon=True).start()
        except Exception:  # an exception here could disable the tap
            log.exception("hotkey tap callback failed")
        return event

    def _run(self) -> None:
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._callback,
            None,
        )
        if self._tap is None:
            self.error = "CGEventTapCreate returned NULL (grant Input Monitoring to your terminal)"
            self._ready.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        self._loop = loop
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self._ready.set()
        while not self._stop.is_set():
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.25, False)
        Quartz.CGEventTapEnable(self._tap, False)
        Quartz.CFRunLoopRemoveSource(loop, source, Quartz.kCFRunLoopCommonModes)

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, name="kill-hotkey-tap", daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        return self._tap is not None and self.error is None

    def stop(self) -> None:
        """Idempotent."""
        self._stop.set()
        loop, self._loop = self._loop, None
        if loop is not None:
            Quartz.CFRunLoopStop(loop)
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=1.0)
