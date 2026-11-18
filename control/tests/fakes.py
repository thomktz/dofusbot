"""In-memory Backend used by the tests (simulates a tiny window server)."""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from dofus_control.types import DisplayInfo, PermissionStatus, RawWindow, Rect


@dataclass
class FakeWin:
    raw: RawWindow
    min_w: float = 0
    min_h: float = 0
    tb: float | None = 28.0


class FakeBackend:
    def __init__(self, scale: float = 2.0):
        self.scale = scale
        self.display = DisplayInfo(0, 1, Rect(0, 0, 1728, 1117), scale, True)
        self.wins: list[FakeWin] = []      # front to back
        self.front: int | None = None
        self.mouse = (500.0, 500.0)
        self.events: list[tuple] = []
        self.perms = PermissionStatus(True, True, True)
        self.menubar = 25.0
        self.hidden: set[int] = set()
        self.activations: list[int] = []
        self.kb_idle = 100.0
        self.display_image: np.ndarray | None = None
        self.mouse_hook = None   # called after each post_mouse (simulate user)
        self.frozen_frames = False

    # helpers
    def add_window(self, wid, pid, owner, bounds, title=None, layer=0, front=True, **kw) -> FakeWin:
        w = FakeWin(RawWindow(wid, pid, owner, title, layer, bounds), **kw)
        if front:
            self.wins.insert(0, w)
        else:
            self.wins.append(w)
        return w

    def get(self, wid) -> FakeWin:
        return next(w for w in self.wins if w.raw.window_id == wid)

    # permissions
    def permissions(self):
        return self.perms

    def request_permissions(self):
        self.events.append(("request_permissions",))

    # windows
    def list_windows(self, include_offscreen=False):
        return [w.raw for w in self.wins if w.raw.pid not in self.hidden]

    def ax_title(self, pid, frame):
        return "AX title"

    def ax_window(self, pid, frame):
        for w in self.wins:
            if w.raw.pid == pid and w.raw.bounds.approx_equal(frame, 2):
                return w.raw.window_id
        return None

    def ax_get_frame(self, handle):
        try:
            return self.get(handle).raw.bounds
        except StopIteration:
            return None

    def ax_set_position(self, handle, x, y):
        if self.frozen_frames:
            return True
        w = self.get(handle)
        y = max(y, self.menubar)
        b = w.raw.bounds
        w.raw = replace(w.raw, bounds=Rect(x, y, b.w, b.h))
        return True

    def ax_set_size(self, handle, ww, hh):
        if self.frozen_frames:
            return True
        w = self.get(handle)
        b = w.raw.bounds
        w.raw = replace(w.raw, bounds=Rect(b.x, b.y, max(ww, w.min_w), max(hh, w.min_h)))
        return True

    def title_bar_height(self, handle, frame):
        return self.get(handle).tb

    def frontmost_pid(self):
        return self.front

    def activate(self, pid, handle=None):
        self.activations.append(pid)
        self.front = pid
        # bring its windows to the front
        mine = [w for w in self.wins if w.raw.pid == pid]
        others = [w for w in self.wins if w.raw.pid != pid]
        self.wins = mine + others
        return True

    def hide_app(self, pid):
        self.hidden.add(pid)
        self.events.append(("hide", pid))
        return True

    def unhide_app(self, pid):
        self.hidden.discard(pid)
        self.events.append(("unhide", pid))
        return True

    # displays / capture
    def displays(self):
        return [self.display]

    def capture_window(self, window_id):
        b = self.get(window_id).raw.bounds
        h, w = int(b.h * self.scale), int(b.w * self.scale)
        img = np.zeros((h, w, 3), np.uint8)
        tb = int((self.get(window_id).tb or 0) * self.scale)
        img[:tb] = (200, 200, 200)   # title bar grey
        img[tb:] = (10, 20, 30)
        return img

    def capture_display(self, index, exclude_window_ids=()):
        self.events.append(("capture_display", index, tuple(exclude_window_ids), tuple(sorted(self.hidden))))
        if self.display_image is not None:
            return self.display_image, self.display
        b = self.display.bounds
        return np.zeros((int(b.h * self.scale), int(b.w * self.scale), 3), np.uint8), self.display

    # input
    def mouse_position(self):
        return self.mouse

    def post_mouse(self, kind, x, y, button="left", click_state=1):
        self.events.append(("mouse", kind, round(x, 2), round(y, 2), button, click_state))
        self.mouse = (x, y)
        if self.mouse_hook:
            self.mouse_hook(self)

    def post_key(self, keycode, down, flags=0, char=None):
        self.events.append(("key", keycode, down, flags, char))

    def post_text(self, text, down):
        self.events.append(("text", text, down))

    def post_scroll(self, x, y, lines):
        self.events.append(("scroll", x, y, lines))

    def seconds_since_keyboard_input(self):
        return self.kb_idle

    def kill_hotkey_down(self):
        return False

    def start_hotkey_listener(self, on_hotkey):
        self.hotkey = on_hotkey
        return lambda: None

    def close(self):
        pass

    # filters
    def kinds(self, kind):
        return [e for e in self.events if e[0] == kind]
