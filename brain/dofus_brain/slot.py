"""Dofus slot logic (protocol §3): choose the target rect (calibrated if still valid, else the
estimate), align the Dofus window onto it, keep it aligned, persist calibration in
config/slot.json."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .bus import Bus

ALIGN_TOL = 1.0


def rect_dict(r: Any) -> dict | None:
    if r is None:
        return None
    if isinstance(r, dict):
        return {k: float(r[k]) for k in ("x", "y", "w", "h")}
    return {"x": float(r.x), "y": float(r.y), "w": float(r.w), "h": float(r.h)}


def estimate_from_report(rep: dict) -> dict:
    """§3 estimate: screen_x = window.screen_x + (outer_w - inner_w)/2 + rect_css.x,
    screen_y = window.screen_y + (outer_h - inner_h) + rect_css.y, w/h = rect_css (× zoom_hint)."""
    w = rep["window"]
    r = rep["rect_css"]
    z = float(rep.get("zoom_hint", 1) or 1)
    return {
        "x": float(w["screen_x"]) + (float(w["outer_w"]) - float(w["inner_w"])) / 2 + float(r["x"]) * z,
        "y": float(w["screen_y"]) + (float(w["outer_h"]) - float(w["inner_h"])) + float(r["y"]) * z,
        "w": float(r["w"]) * z,
        "h": float(r["h"]) * z,
    }


def rects_close(a: dict | None, b: dict | None, tol: float = ALIGN_TOL) -> bool:
    if a is None or b is None:
        return False
    return (abs(a["x"] - b["x"]) <= tol and abs(a["y"] - b["y"]) <= tol
            and abs((a["x"] + a["w"]) - (b["x"] + b["w"])) <= tol and abs((a["y"] + a["h"]) - (b["y"] + b["h"])) <= tol)


def _browser_key(rep: dict) -> dict:
    w = rep["window"]
    return {"screen_x": w["screen_x"], "screen_y": w["screen_y"], "outer_w": w["outer_w"], "outer_h": w["outer_h"],
            "inner_w": w["inner_w"], "inner_h": w["inner_h"], "rect_css": rect_dict(rep["rect_css"]),
            "dpr": rep.get("dpr"), "zoom_hint": rep.get("zoom_hint", 1)}


class SlotManager:
    def __init__(self, controller: Any, bus: Bus, config_dir: Path, keep_on_top: bool = True):
        self.ctl = controller
        self.bus = bus
        self.path = Path(config_dir) / "slot.json"
        self.keep_on_top = keep_on_top
        self.suspended: str | None = None      # kill switch: no keep-on-top, no auto-align
        self.report: dict | None = None
        self.calibrated: dict | None = None
        self.calib_key: dict | None = None
        self.window: dict = {"found": False, "title": None, "pid": None, "frame": None}
        self._last_status: dict | None = None
        self._lock = asyncio.Lock()
        self._load()
        try:
            controller.keep_on_top(keep_on_top)
        except Exception:
            pass

    def _load(self) -> None:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text())
                self.calibrated = rect_dict(d.get("rect"))
                self.calib_key = d.get("browser")
            except Exception as e:
                self.bus.log("warn", "server", f"ignoring bad {self.path.name}: {e}")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "_comment": "Written by dofus_brain on overlay.calibrate (protocol §3). rect = slot in screen points; "
                        "browser = webapp window geometry at calibration time (calibration is dropped when it changes).",
            "rect": self.calibrated, "browser": self.calib_key, "ts": int(time.time() * 1000)}, indent=2) + "\n")

    # ------------------------------------------------------------------ target
    def calibration_valid(self) -> bool:
        if self.calibrated is None:
            return False
        if self.report is None or self.calib_key is None:
            return True  # nothing says the browser moved
        return _browser_key(self.report) == self.calib_key

    def target(self) -> dict | None:
        if self.calibration_valid():
            return self.calibrated
        if self.report is None:
            return None
        return estimate_from_report(self.report)

    def _rect_obj(self, d: dict) -> Any:
        try:
            from dofus_control import Rect  # type: ignore

            ctl = getattr(self.ctl, "inner", self.ctl)     # PacedController wraps the real one
            if type(ctl).__module__.startswith("dofus_control"):
                return Rect(d["x"], d["y"], d["w"], d["h"])
        except Exception:
            pass
        from .mock_control import Rect

        return Rect(d["x"], d["y"], d["w"], d["h"])

    # ------------------------------------------------------------------ status
    def refresh_window(self) -> None:
        try:
            info = self.ctl.find_window()
        except Exception as e:
            info = None
            self.bus.log("warn", "control", f"find_window failed: {e}")
        if info is None:
            self.window = {"found": False, "title": None, "pid": None, "frame": None}
        else:
            self.window = {"found": True, "title": getattr(info, "title", None), "pid": getattr(info, "pid", None),
                           "frame": rect_dict(getattr(info, "content", None) or getattr(info, "frame", None))}

    def status(self) -> dict:
        t = self.target()
        return {"dofus_window": dict(self.window), "slot": t,
                "aligned": rects_close(self.window.get("frame"), t),
                "keep_on_top": self.keep_on_top and self.suspended is None, "calibrated": self.calibration_valid()}

    def suspend(self, why: str) -> None:
        """Kill switch: stop pulling Dofus to the front and stop re-aligning it, until restore()."""
        first = self.suspended is None
        self.suspended = why
        try:
            self.ctl.keep_on_top(False)
        except Exception as e:
            self.bus.log("warn", "control", f"keep_on_top(False) failed: {e}")
        if first:
            self.bus.log("warn", "control", f"window left alone (no keep-on-top, no auto-align): {why}")
        self._maybe_emit()

    def restore(self) -> None:
        if self.suspended is None:
            return
        self.suspended = None
        try:
            self.ctl.keep_on_top(self.keep_on_top)
        except Exception as e:
            self.bus.log("warn", "control", f"keep_on_top({self.keep_on_top}) failed: {e}")
        self.bus.log("info", "control", "window management back on")
        self._maybe_emit()

    def _maybe_emit(self) -> None:
        s = self.status()
        if s != self._last_status:
            self._last_status = s
            self.bus.emit("overlay", s)

    # ------------------------------------------------------------------ actions
    async def on_report(self, rep: dict) -> None:
        for k in ("rect_css", "window", "estimate_screen"):
            if k not in rep:
                raise ValueError(f"slot.report missing {k}")
        old_target = self.target()
        self.report = rep
        est = estimate_from_report(rep)
        if not rects_close(est, rect_dict(rep["estimate_screen"]), 2.0):
            self.bus.log("debug", "server", f"webapp estimate {rep['estimate_screen']} differs from §3 formula {est}")
        if self.calibrated is not None and not self.calibration_valid():
            self.bus.log("info", "server", "browser window moved: calibration dropped, using estimate")
            self.calibrated = None
        new_target = self.target()
        if not rects_close(old_target, new_target, 0.5) and self.suspended is None:
            await self.align()
        self._maybe_emit()

    async def align(self) -> dict:
        if self.suspended is not None:
            raise ValueError(f"window management is off after the kill switch ({self.suspended}); resume first")
        t = self.target()
        if t is None:
            raise ValueError("no slot known yet (waiting for slot.report)")
        async with self._lock:
            try:
                await asyncio.to_thread(self.ctl.align_window, self._rect_obj(t), True)
            except Exception as e:
                self.bus.log("error", "control", f"align_window failed: {e}")
                raise
            await asyncio.to_thread(self.refresh_window)
        self.bus.log("info", "control", f"aligned Dofus onto slot {t}")
        self._maybe_emit()
        return self.status()

    async def calibrate(self) -> dict:
        async with self._lock:
            r = await asyncio.to_thread(self.ctl.calibrate_slot)
        self.calibrated = rect_dict(r)
        self.calib_key = _browser_key(self.report) if self.report else None
        self._save()
        self.bus.log("info", "control", f"slot calibrated: {self.calibrated}")
        return await self.align()

    async def adopt_current(self) -> dict:
        """Persist the current Dofus content rect as the default slot.

        Unlike marker calibration this does not move or resize either window.
        """
        await asyncio.to_thread(self.refresh_window)
        frame = self.window.get("frame")
        if frame is None:
            raise ValueError("Dofus window not found")
        self.calibrated = dict(frame)
        self.calib_key = None
        self._save()
        self.bus.log("info", "control", f"saved current Dofus window as default slot: {self.calibrated}")
        self._maybe_emit()
        return self.status()

    async def periodic(self) -> None:
        await asyncio.to_thread(self.refresh_window)
        st = self.status()
        if self.keep_on_top and self.suspended is None and st["slot"] is not None and not st["aligned"]:
            try:
                await self.align()
            except Exception:
                pass
        self._maybe_emit()
