"""Humanlike input sequencing on top of a Backend (pure logic, screen points).

Every sleep is interruptible by the kill switch; every mouse step checks that the
real cursor is still where we put it (protocol §4 user-movement trigger).
"""
from __future__ import annotations

import time
from typing import Callable

from .backend import Backend
from .human import HumanProfile, plan_move
from .keys import MODIFIER_FLAGS, MODIFIER_KEYCODES, NAMED_KEYS, normalize_modifiers, resolve_key
from .killswitch import KillSwitch

#: Seconds between kill-switch checks while sleeping.
SLEEP_SLICE = 0.02


class InputDriver:
    def __init__(
        self,
        backend: Backend,
        killswitch: KillSwitch,
        profile: HumanProfile,
        sleep: Callable[[float], None] = time.sleep,
        layout: str = "auto",
    ):
        self.b = backend
        self.ks = killswitch
        self.p = profile
        self._sleep_fn = sleep
        self.layout = layout

    # -- primitives -------------------------------------------------------------
    def sleep_ms(self, ms: float) -> None:
        """Sleep ``ms`` milliseconds in slices, raising KillSwitchTripped as soon as tripped."""
        remaining = max(0.0, ms) / 1000.0
        self.ks.check()
        while remaining > 1e-6:
            chunk = min(SLEEP_SLICE, remaining)
            self._sleep_fn(chunk)
            remaining -= chunk
            self.ks.check()

    def _guard_mouse(self) -> None:
        self.ks.check_user_mouse(self.b.mouse_position())
        self.ks.check()

    def _post_mouse(self, kind: str, x: float, y: float, button: str = "left", click_state: int = 1) -> None:
        self._guard_mouse()
        self.ks.set_expected_mouse((x, y))
        self.b.post_mouse(kind, x, y, button, click_state)

    def move_to(self, x: float, y: float, drag_button: str | None = None, overshoot: bool = True) -> None:
        start = self.b.mouse_position()
        self._guard_mouse()
        path = plan_move(start, (x, y), self.p, allow_overshoot=overshoot)
        kind = "drag" if drag_button else "move"
        for px, py, dt in path:
            self.sleep_ms(dt)
            self._post_mouse(kind, px, py, drag_button or "left")

    # -- actions (screen points) ------------------------------------------------------
    def click(self, x: float, y: float, button: str = "left", double: bool = False) -> None:
        self.move_to(x, y)
        self.sleep_ms(self.p.scaled(self.p.uniform((20.0, 60.0))))  # settle before pressing
        clicks = 2 if double else 1
        for n in range(1, clicks + 1):
            self._post_mouse("down", x, y, button, n)
            self.sleep_ms(self.p.hold())
            self._post_mouse("up", x, y, button, n)
            if n < clicks:
                self.sleep_ms(self.p.double_click_gap())

    def move(self, x: float, y: float) -> None:
        self.move_to(x, y)

    def drag(self, x: float, y: float, to_x: float, to_y: float) -> None:
        self.move_to(x, y)
        self.sleep_ms(self.p.drag_settle())
        self._post_mouse("down", x, y, "left", 1)
        self.sleep_ms(self.p.drag_settle())
        self.move_to(to_x, to_y, drag_button="left", overshoot=False)
        self.sleep_ms(self.p.drag_settle())
        self._post_mouse("up", to_x, to_y, "left", 1)

    def scroll(self, x: float, y: float, dy: int) -> None:
        """``dy`` > 0 scrolls DOWN (web/DOM convention), < 0 scrolls up; one event per line."""
        self.move_to(x, y)
        step = -1 if dy > 0 else 1  # CGEvent wheel: positive = up
        for _ in range(abs(int(dy))):
            self._guard_mouse()
            self.b.post_scroll(x, y, step)
            self.sleep_ms(self.p.scaled(self.p.uniform((25.0, 70.0))))

    def key(self, name: str, modifiers=None) -> None:
        spec = resolve_key(name, self.layout)
        mods = list(normalize_modifiers(modifiers))
        if spec.shift and "shift" not in mods:
            mods.append("shift")
        # character only makes sense without command-ish modifiers
        char = spec.char if not any(m in ("cmd", "ctrl", "alt") for m in mods) else None
        flags = 0
        pressed: list[str] = []
        try:
            for m in mods:
                self.ks.check()
                flags |= MODIFIER_FLAGS[m]
                self.b.post_key(MODIFIER_KEYCODES[m], True, flags)
                pressed.append(m)
                self.sleep_ms(self.p.modifier_gap())
            self.ks.check()
            self.b.post_key(spec.keycode, True, flags, char)
            try:
                self.sleep_ms(self.p.key_gap())
            finally:
                self.b.post_key(spec.keycode, False, flags, char)  # never leave a key stuck
        finally:
            for m in reversed(pressed):  # always release modifiers, even when killed
                flags &= ~MODIFIER_FLAGS[m]
                self.b.post_key(MODIFIER_KEYCODES[m], False, flags)

    def hold_key(self, name: str):
        """Context manager: key down on enter, key up on exit (always, even when killed).
        For keys the game only acts on while held (show interactives / show entities)."""
        from contextlib import contextmanager

        @contextmanager
        def _held():
            spec = resolve_key(name, self.layout)
            char = spec.char
            self.ks.check()
            self.b.post_key(spec.keycode, True, 0, char)
            try:
                yield
            finally:
                self.b.post_key(spec.keycode, False, 0, char)  # never leave a key stuck

        return _held()

    def type_text(self, text: str) -> None:
        """Type via the unicode-string API (layout independent). \\n -> Enter, \\t -> Tab."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        for i, ch in enumerate(text):
            self.ks.check()
            if ch == "\n":
                self._tap_named("enter")
            elif ch == "\t":
                self._tap_named("tab")
            else:
                self.b.post_text(ch, True)
                try:
                    self.sleep_ms(self.p.key_gap())
                finally:
                    self.b.post_text(ch, False)
            if i + 1 < len(text):
                self.sleep_ms(self.p.key_gap())

    def _tap_named(self, name: str) -> None:
        code = NAMED_KEYS[name]
        self.b.post_key(code, True, 0)
        try:
            self.sleep_ms(self.p.key_gap())
        finally:
            self.b.post_key(code, False, 0)
