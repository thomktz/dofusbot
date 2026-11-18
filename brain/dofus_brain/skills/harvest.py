"""Harvest / interact: scan the map with the highlight key (Y), click the nearest highlighted
element, confirm by a count change (pods / item count) or the element changing on screen.

The skill needs raw frames, which GameState does not carry: the caller passes each new
capture through `feed_frame(img, scale)` before `step()`. Sequence per element:
    hold mode (Dofus default): remember plain frame -> Outcome("scan", key) -> the caller
    captures while holding the key and calls `on_scan(img)` -> diff
    toggle mode: remember plain frame -> press key -> next frame = highlighted -> diff -> press key
    then: click nearest blob -> wait for confirmation -> repeat until `target_count`.
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np

from ..game.state import GameState
from ..perception.highlight import Blob, highlight_blobs, region_changed
from ..planner import PlanContext, PlannedStep, StepResult
from . import Outcome, StepOrOutcome, wait


class HarvestSkill:
    name = "harvest"

    def __init__(self, cfg: dict, target_count: int = 1, count_fn: Callable[[GameState], int | None] | None = None,
                 exclude_px: list[tuple[int, int, int, int]] | None = None, clock: Callable[[], float] = time.monotonic,
                 origin: Callable[[GameState], tuple[float, float] | None] | None = None):
        hc = cfg["harvest"]
        self.key = cfg["keys"]["highlight_interactives"]
        self.toggle = cfg.get("key_mode", "toggle") == "toggle"
        self.threshold = int(hc.get("diff_threshold", 40))
        self.min_px = int(hc.get("min_blob_px", 40))
        self.timeout = float(hc.get("progress_timeout_s", 8.0))
        self.target = target_count
        self.count_fn = count_fn or (lambda st: st.character.pods)
        self.exclude = exclude_px or []
        self.clock = clock
        self.origin = origin
        self.frame: np.ndarray | None = None
        self.scale = 1.0
        self.frame_no = 0
        self.phase = "idle"            # idle -> shown -> hidden -> clicked
        self.plain: np.ndarray | None = None
        self.blobs: list[Blob] = []
        self.skipped: list[Blob] = []
        self.done = 0
        self._mark = 0
        self._click_frame: np.ndarray | None = None
        self._click_count: int | None = None
        self._clicked: Blob | None = None

    def feed_frame(self, img: np.ndarray, scale: float = 1.0) -> None:
        self.frame = img
        self.scale = scale
        self.frame_no += 1

    def _new_frame_since(self, mark: int) -> Callable[[GameState, GameState], bool]:
        return lambda before, now: self.frame_no > mark

    def _key(self, op: str, why: str) -> PlannedStep:
        self._mark = self.frame_no
        return PlannedStep(action={"kind": "key", "key": self.key, "reason": why}, task=f"harvest: {why}",
                           verify=self._new_frame_since(self.frame_no), timeout_s=2.0,
                           meta={"skill": self.name, "op": op})

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        if self.done >= self.target:
            return Outcome("done", f"harvested {self.done}")
        if state.screen != "world":
            return wait(f"screen {state.screen}")
        if self.frame is None:
            return wait("no frame yet")
        if self.phase == "idle":
            self.plain = self.frame
            if not self.toggle:
                self.phase = "scanning"
                return Outcome("scan", "hold the highlight key and capture", data={"key": self.key})
            return self._key("show", "show interactive elements")
        if self.phase == "scanning":
            return Outcome("scan", "waiting for the highlighted capture", data={"key": self.key})
        if self.phase == "shown":
            if self.frame_no <= self._mark:
                return wait("waiting for highlighted frame")
            assert self.plain is not None
            self.blobs = [b for b in highlight_blobs(self.plain, self.frame, self.scale, self.threshold, self.min_px,
                                                     exclude=self.exclude)
                          if not any(b.contains(s.x, s.y) for s in self.skipped)]
            if self.toggle:
                return self._key("hide", "hide interactive elements")
            self.phase = "hidden"
        if self.phase == "hidden":
            if not self.blobs:
                return Outcome("failed", "no highlighted element on this map")
            ox, oy = (self.origin(state) if self.origin else None) or (ctx.grid.ox + ctx.grid.cw * 6.5,
                                                                       ctx.grid.oy + ctx.grid.ch * 10)
            b = min(self.blobs, key=lambda b: (b.x - ox) ** 2 + (b.y - oy) ** 2)
            self._clicked = b
            self._click_frame = self.frame
            self._click_count = self.count_fn(state)
            started = self.clock()

            def confirmed(before: GameState, now: GameState) -> bool:
                c = self.count_fn(now)
                if c is not None and self._click_count is not None and c > self._click_count:
                    return True
                if self.frame is not None and self._click_frame is not None and self.frame is not self._click_frame \
                        and self.clock() - started > 0.5:
                    return region_changed(self._click_frame, self.frame, b, self.scale, self.threshold)
                return False

            return PlannedStep(action={"kind": "click", "x": round(b.x, 1), "y": round(b.y, 1),
                                       "reason": f"interact with highlighted element at ({b.x:.0f},{b.y:.0f})"},
                               task="harvest: interact", verify=confirmed, timeout_s=self.timeout, retries=0,
                               meta={"skill": self.name, "op": "click"})
        return wait(self.phase)

    def on_scan(self, img: np.ndarray, scale: float = 1.0) -> None:
        """Frame captured while the highlight key was held (hold mode)."""
        if self.phase != "scanning" or self.plain is None:
            return
        if img.shape != self.plain.shape:  # window resized between frames: start over
            self.phase = "idle"
            return
        self.blobs = [b for b in highlight_blobs(self.plain, img, scale, self.threshold, self.min_px, exclude=self.exclude)
                      if not any(b.contains(s.x, s.y) for s in self.skipped)]
        self.phase = "hidden"

    def on_result(self, result: StepResult, state: GameState) -> None:
        m = result.step.meta
        if m.get("skill") != self.name:
            return
        op = m["op"]
        if op == "show":
            self.phase = "shown" if result.ok else "idle"
        elif op == "hide":
            self.phase = "hidden"
        elif op == "click":
            if result.ok:
                self.done += 1
            elif self._clicked is not None:
                self.skipped.append(self._clicked)  # not usable (or not ours): skip it next scan
            self.phase = "idle"
            self.blobs = []
