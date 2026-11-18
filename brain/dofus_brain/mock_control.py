"""MockController: implements the protocol §4 control API on synthetic screenshots.

- capture() returns fixture frames (fixtures/*.png, or re-rendered with the fixture geometry
  so the world screen shows the current mock map coords).
- execute() records every action in `.executed` and makes the mock world react:
    click on a map-edge cell (world)      -> 1 loading frame, then coords move one map
    click on the NPC cell (world)         -> dialog screen
    key "1".."9" or click an option       -> back to world
    key "escape"                          -> back to world
    `mock.start_fight()` / end-turn key   -> fight screen; 3 end turns end the fight
- Kill switch semantics identical to dofus_control: once tripped, execute() raises
  KillSwitchTripped until reset_kill(); on_kill callbacks are called.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .game.grid import Grid
from .mock_render import DEFAULT_DIALOG, FighterSpec, FightSpec, Renderer
from .paths import fixtures_dir


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class WindowInfo:
    window_id: int
    pid: int
    app: str
    title: str
    frame: Rect
    content: Rect


@dataclass
class Frame:
    image: np.ndarray
    scale: float
    ts: float
    window: WindowInfo | None


@dataclass
class PermissionStatus:
    accessibility: bool = True
    screen_recording: bool = True


class KillSwitchTripped(RuntimeError):
    pass


DEFAULT_MARKERS = {"tl": "#FF00FF", "tr": "#00FFFF", "bl": "#FFFF00", "br": "#00FF00"}
TITLE_BAR = 28.0


@dataclass
class MockWorld:
    screen: str = "world"
    coords: tuple[int, int] = (4, -18)
    npc_cell: int = 243
    loading_frames: int = 0
    pending_coords: tuple[int, int] | None = None
    fight_end_turns: int = 0
    fight: FightSpec = field(default_factory=lambda: FightSpec(
        fighters=[FighterSpec(241, "ally", True), FighterSpec(245, "enemy"), FighterSpec(301, "enemy")],
        blocked=[243, 257, 271], highlight=[], my_turn=True))


class MockController:
    def __init__(self, app_name: str = "Dofus", title_contains: str | None = None,
                 fixtures: Path | None = None, action_delay_s: float = 0.0):
        self.app_name = app_name
        self.title_contains = title_contains
        self.dir = Path(fixtures or fixtures_dir())
        meta_p = self.dir / "fixtures.json"
        self.meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
        from .config import load_json

        self.grid_cfg = self.meta.get("grid") or load_json("grid")
        self.regions_cfg = self.meta.get("regions") or load_json("regions")
        self.scale = float(self.meta.get("scale", 1.0))
        wp = self.meta.get("window_points", [1280, 800])
        self.renderer = Renderer(self.grid_cfg, self.regions_cfg, size=(wp[0], wp[1]), scale=self.scale)
        self.grid = Grid.from_config(self.grid_cfg)
        self.world = MockWorld()
        self.executed: list[dict] = []
        self.action_delay_s = action_delay_s
        self._killed = False
        self._kill_cbs: list[Callable[[], None]] = []
        self._lock = threading.RLock()
        self._content = Rect(100.0, 100.0 + TITLE_BAR, float(wp[0]), float(wp[1]))
        self._keep_on_top = False
        self._world_cache: dict[tuple[int, int], np.ndarray] = {}
        self._static: dict[str, np.ndarray] = {}
        self.capture_count = 0

    # ------------------------------------------------------------ permissions/window
    def permissions(self) -> PermissionStatus:
        return PermissionStatus(True, True)

    def request_permissions(self) -> None:
        return None

    def _info(self) -> WindowInfo:
        c = self._content
        return WindowInfo(window_id=4242, pid=4242, app=self.app_name, title=f"{self.app_name} (mock)",
                          frame=Rect(c.x, c.y - TITLE_BAR, c.w, c.h + TITLE_BAR), content=Rect(c.x, c.y, c.w, c.h))

    def find_window(self) -> WindowInfo | None:
        return self._info()

    def align_window(self, target, content: bool = True) -> WindowInfo:
        t = _as_rect(target)
        with self._lock:
            if content:
                self._content = Rect(t.x, t.y, t.w, t.h)
            else:
                self._content = Rect(t.x, t.y + TITLE_BAR, t.w, t.h - TITLE_BAR)
        return self._info()

    def keep_on_top(self, enabled: bool) -> None:
        self._keep_on_top = bool(enabled)

    # ------------------------------------------------------------ capture
    def _static_frame(self, name: str, render: Callable[[], np.ndarray]) -> np.ndarray:
        if name not in self._static:
            p = self.dir / name
            self._static[name] = np.asarray(Image.open(p).convert("RGB")) if p.exists() else render()
        return self._static[name]

    def _frame_image(self) -> np.ndarray:
        w = self.world
        if w.loading_frames > 0:
            w.loading_frames -= 1
            if w.loading_frames == 0 and w.pending_coords is not None:
                w.coords, w.pending_coords = w.pending_coords, None
            return self._static_frame("loading.png", self.renderer.loading)
        if w.screen == "fight":
            return self.renderer.fight(w.fight)
        if w.screen == "dialog":
            d = DEFAULT_DIALOG
            return self._static_frame("dialog.png", lambda: self.renderer.dialog(d["npc"], d["text"], d["options"]))
        if w.coords not in self._world_cache:
            if len(self._world_cache) > 64:
                self._world_cache.clear()
            self._world_cache[w.coords] = self.renderer.world(w.coords)
        return self._world_cache[w.coords]

    def capture(self) -> Frame:
        with self._lock:
            self.capture_count += 1
            img = self._frame_image()
            return Frame(image=img, scale=self.scale, ts=time.time(), window=self._info())

    def capture_holding(self, key: str, settle_ms: float = 250.0) -> Frame:
        """Mock: record the hold (as a key action) and return a normal capture."""
        if self._killed:
            raise KillSwitchTripped("kill switch tripped")
        with self._lock:
            self.executed.append({"kind": "hold", "key": key, "ms": settle_ms})
        return self.capture()

    def capture_display(self, display: int = 0) -> Frame:
        img = np.zeros((900, 1440, 3), dtype=np.uint8)
        return Frame(image=img, scale=1.0, ts=time.time(), window=None)

    def calibrate_slot(self, markers=DEFAULT_MARKERS):
        """Mock: pretend the markers were found exactly where the window content is now."""
        c = self._content
        return Rect(c.x, c.y, c.w, c.h)

    # ------------------------------------------------------------ input
    def execute(self, action: dict) -> None:
        if self._killed:
            raise KillSwitchTripped("kill switch tripped")
        if self.action_delay_s:
            time.sleep(self.action_delay_s)
        if self._killed:
            raise KillSwitchTripped("kill switch tripped")
        with self._lock:
            self.executed.append(dict(action))
            self._react(action)

    def _react(self, a: dict) -> None:
        w = self.world
        kind = a.get("kind")
        if kind == "click" and w.screen == "world" and w.loading_frames == 0:
            cell = self.grid.cell_at(a["x"], a["y"])
            if cell is None:
                return
            if cell == w.npc_cell:
                w.screen = "dialog"
                return
            row, col = self.grid.rc(cell)
            d = None
            if col == self.grid.cols - 1 and row % 2 == 1:
                d = (1, 0)
            elif col == 0 and row % 2 == 0:
                d = (-1, 0)
            elif row == 0:
                d = (0, -1)
            elif row == self.grid.rows - 1:
                d = (0, 1)
            if d is not None:
                w.pending_coords = (w.coords[0] + d[0], w.coords[1] + d[1])
                w.loading_frames = 2
        elif kind == "click" and w.screen == "dialog":
            if a["y"] > 0.69 * self.renderer.size[1]:
                w.screen = "world"
        elif kind == "key":
            k = str(a.get("key", "")).lower()
            if w.screen == "dialog" and (k.isdigit() or k == "escape"):
                w.screen = "world"
            elif w.screen == "fight" and k in ("f1", "space"):
                w.fight_end_turns += 1
                if w.fight_end_turns >= 3:
                    w.screen = "world"
                    w.fight_end_turns = 0
            elif k == "escape":
                w.screen = "world"

    def start_fight(self) -> None:
        with self._lock:
            self.world.screen = "fight"
            self.world.fight_end_turns = 0

    # ------------------------------------------------------------ kill switch
    @property
    def killed(self) -> bool:
        return self._killed

    def kill(self) -> None:
        if self._killed:
            return
        self._killed = True
        for cb in list(self._kill_cbs):
            try:
                cb()
            except Exception:
                pass

    def reset_kill(self) -> None:
        self._killed = False

    def on_kill(self, callback: Callable[[], None]) -> None:
        self._kill_cbs.append(callback)

    def close(self) -> None:
        pass


def _as_rect(r) -> Rect:
    if isinstance(r, dict):
        return Rect(float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"]))
    return Rect(float(r.x), float(r.y), float(r.w), float(r.h))


def make_controller(mock: bool, app_name: str = "Dofus", title_contains: str | None = None, pace: bool = True):
    """Real dofus_control.Controller on macOS (unless mock), else MockController. The real
    one is paced (pacing.py): 1 capture per second at most, 2.5 s after a click."""
    if not mock:
        try:
            from dofus_control import Controller  # type: ignore

            ctl = Controller(app_name=app_name, title_contains=title_contains)
            if pace:
                from .pacing import PacedController

                ctl = PacedController(ctl)
            return ctl, False
        except Exception as e:  # pragma: no cover - depends on control/ being installed
            import logging

            logging.getLogger(__name__).warning("dofus_control unavailable (%s); using MockController", e)
    return MockController(app_name=app_name), True
