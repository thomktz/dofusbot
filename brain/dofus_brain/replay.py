"""Replay a folder of captured Dofus frames as a fake live screen, for dry runs.

`ReplayController` speaks the control API the agent uses (protocol §4, same as
MockController). capture() returns the frame whose timestamp is the latest one at or before
the replay clock (frames are named HHMMSS_mmm.png by the capture loop, or listed in a
labels.json). execute() never touches the game: it only records what the bot would have
done, together with the frame on screen at that moment.

    python -m dofus_brain.replay <frames dir> [--speed 1.0] [--goal fight]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from .mock_control import Frame, PermissionStatus, Rect, WindowInfo

NAME_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})(?:_(\d{1,3}))?")


def frame_time(name: str) -> float | None:
    """'215228_045.png' -> seconds since midnight (21*3600 + 52*60 + 28.045)."""
    m = NAME_RE.match(Path(name).name)
    if not m:
        return None
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + (int(ms.ljust(3, "0")) / 1000 if ms else 0.0)


@dataclass
class ReplayFrame:
    path: Path
    t: float              # capture time, seconds since midnight
    label: str = ""


def load_frames(directory: Path) -> list[ReplayFrame]:
    directory = Path(directory)
    labels: dict[str, str] = {}
    lj = directory / "labels.json"
    if lj.exists():
        for e in json.loads(lj.read_text()):
            labels[e["file"]] = e.get("label", "")
            if e.get("captured"):
                labels.setdefault(e["captured"], e.get("label", ""))
    out = []
    for p in sorted(directory.iterdir()):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        t = frame_time(p.name)
        if t is None:
            # fixtures named NN_label.jpg: use the capture time stored in labels.json
            cap = next((e.get("captured") for e in (json.loads(lj.read_text()) if lj.exists() else [])
                        if e["file"] == p.name), None)
            t = frame_time(cap) if cap else None
        if t is None:
            continue
        out.append(ReplayFrame(p, t, labels.get(p.name, "")))
    out.sort(key=lambda f: f.t)
    if not out:
        raise ValueError(f"no timestamped frames in {directory}")
    return out


class ReplayController:
    """Fake live screen from captured frames. Never sends input anywhere."""

    def __init__(self, frames: list[ReplayFrame], speed: float = 1.0, clock: Callable[[], float] = time.monotonic,
                 grid_cfg: dict | None = None, regions_cfg: dict | None = None):
        from .config import load_json

        self.frames = frames
        self.speed = float(speed)
        self.tail_s = 6.0
        self.clock = clock
        self.t0 = frames[0].t
        self.start: float | None = None
        self.grid_cfg = grid_cfg or load_json("grid")
        self.regions_cfg = regions_cfg or load_json("regions")
        self.executed: list[dict] = []          # what the bot would have done
        self.capture_count = 0
        self._cache: dict[Path, np.ndarray] = {}
        self._lock = threading.Lock()
        self._kill_cbs: list[Callable[[], None]] = []
        self._killed = False
        self.content = Rect(16, 82, 1280, 800)

    # ------------------------------------------------------------ time
    def now(self) -> float:
        """Replay time, in the frames' clock."""
        if self.start is None:
            self.start = self.clock()
        return self.t0 + (self.clock() - self.start) * self.speed

    @property
    def finished(self) -> bool:
        # the last frame stays on screen a little, like a live screen would
        return self.start is not None and self.now() > self.frames[-1].t + self.tail_s

    def current(self) -> ReplayFrame:
        t = self.now()
        cur = self.frames[0]
        for f in self.frames:
            if f.t <= t:
                cur = f
            else:
                break
        return cur

    # ------------------------------------------------------------ control API
    def permissions(self) -> PermissionStatus:
        return PermissionStatus(True, True)

    def request_permissions(self) -> None:
        pass

    def _info(self) -> WindowInfo:
        c = self.content
        return WindowInfo(0, 0, "Dofus (replay)", "replay", Rect(c.x, c.y - 28, c.w, c.h + 28), c)

    def find_window(self) -> WindowInfo | None:
        return self._info()

    def align_window(self, target, content: bool = True) -> WindowInfo:
        return self._info()

    def keep_on_top(self, enabled: bool) -> None:
        pass

    def capture(self) -> Frame:
        with self._lock:
            self.capture_count += 1
            f = self.current()
            img = self._cache.get(f.path)
            if img is None:
                img = np.asarray(Image.open(f.path).convert("RGB"))
                self._cache = {f.path: img}      # one frame in memory is enough
            return Frame(image=img, scale=1.0, ts=f.t, window=self._info())

    def capture_display(self, display: int = 0) -> Frame:
        return self.capture()

    def capture_holding(self, key: str, settle_ms: float = 250.0) -> Frame:
        """Dry run: the key hold is only recorded; the frame on screen is returned."""
        with self._lock:
            f = self.current()
            self.executed.append({"t": round(self.now() - self.t0, 2), "frame": f.path.name, "label": f.label,
                                  "action": {"kind": "hold", "key": key, "ms": settle_ms}})
        return self.capture()

    def calibrate_slot(self, markers=None):
        return self.content

    def execute(self, action: dict) -> None:
        """Dry run: record only. Nothing is sent to Dofus or to the OS."""
        from .mock_control import KillSwitchTripped

        if self._killed:
            raise KillSwitchTripped("kill switch tripped")
        f = self.current()
        with self._lock:
            self.executed.append({"t": round(self.now() - self.t0, 2), "frame": f.path.name,
                                  "label": f.label, "action": dict(action)})

    def killed(self) -> bool:
        return self._killed

    def kill(self) -> None:
        self._killed = True
        for cb in self._kill_cbs:
            cb()

    def reset_kill(self) -> None:
        self._killed = False

    def on_kill(self, callback: Callable[[], None]) -> None:
        self._kill_cbs.append(callback)

    def close(self) -> None:
        pass


def describe_action(a: dict) -> str:
    k = a.get("kind")
    why = a.get("reason", "")
    if k == "click":
        where = f"cell {a['cell']}" if "cell" in a else f"({a['x']:.0f},{a['y']:.0f})"
        return f"click {where}: {why}"
    if k == "key":
        return f"key {a['key']!r}: {why}"
    return f"{k}: {why}"


async def dry_run(frames_dir: Path, speed: float = 1.0, goal: str = "fight", loop_hz: float = 4.0,
                  ocr=None, log: Callable[[str], None] = print,
                  clock: Callable[[], float] | None = None, tick_s: float | None = None,
                  pace: bool = True, max_failures: int | None = None) -> tuple[list[dict], list[dict]]:
    """Run the real agent loop (perceiver + PhasePlanner, auto mode) over the frames.
    Returns (would-be actions, bus log). `clock` + `tick_s` (tests): a fake clock advanced
    by tick_s after every tick, no sleeping."""
    from .agent import Agent
    from .bus import Bus
    from .config import load_json
    from .llm import MockLLM
    from .ocr.backends import make_ocr
    from .perception.perceiver import Perceiver
    from .planner.phases import PhasePlanner
    from .skills.config import load_phases_config

    frames = load_frames(frames_dir)
    ctl = ReplayController(frames, speed=speed, clock=clock or time.monotonic)
    # same pacing as live (1 fps max, settle after clicks), on the replay clock: frames
    # captured live during a wait are skipped, as the live loop would skip them
    paced = None
    if pace:
        from .pacing import PacedController

        if clock is not None and hasattr(clock, "advance"):
            sleep = clock.advance
        else:
            sleep = lambda s: time.sleep(s / max(speed, 1e-6))   # noqa: E731
        paced = PacedController(ctl, clock=lambda: ctl.now(), sleep=sleep)
    regions = load_json("regions")
    ocr = ocr or make_ocr(regions.get("ocr", {}))
    pcfg = load_phases_config()
    if max_failures is not None:
        # replayed frames never react to the bot, so its checks fail whenever it chose
        # differently from Thomas; raise the live safety stop to see the whole sequence
        pcfg["safety"]["max_consecutive_failures"] = int(max_failures)
    perceiver = Perceiver(load_json("grid"), regions, ocr,
                          max_pa=int(pcfg["fight"].get("max_pa", 6)), max_pm=int(pcfg["fight"].get("max_pm", 3)))
    bus = Bus()
    cfg = {"loop_hz": loop_hz, "action_timeout_s": 3.0, "action_retries": 0, "stuck_after_s": 60,
           "llm_min_interval_s": 0, "capture_timeout_s": 5.0, "classifier_vlm_threshold": 0.0}
    agent = Agent(paced or ctl, perceiver, MockLLM(), bus, cfg, planner=PhasePlanner(pcfg, clock=clock), mock=True)
    agent.set_goal(goal)
    agent.set_mode("auto")
    seen = 0
    last_frame = None
    while not ctl.finished:
        t = time.monotonic()
        await agent.tick()
        shown = next(f for f in frames if agent.last_frame is not None and f.t == agent.last_frame.ts)
        if shown.path.name != last_frame:
            last_frame = shown.path.name
            st = agent.game
            ph = st.fight.phase if st.fight else "-"
            log(f"[{shown.path.name}] {shown.label or ''} -> screen={st.screen} phase={ph}")
        while seen < len(ctl.executed):
            e = ctl.executed[seen]
            seen += 1
            log(f"    would {describe_action(e['action'])}")
        if tick_s is not None and clock is not None and hasattr(clock, "advance"):
            clock.advance(tick_s)
        else:
            await asyncio.sleep(max(0.0, 1.0 / loop_hz - (time.monotonic() - t)))
    return ctl.executed, list(bus.history)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Dry-run the bot on captured frames (no input is sent)")
    ap.add_argument("frames", type=Path)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--goal", default="fight")
    ap.add_argument("--hz", type=float, default=4.0)
    ap.add_argument("--no-pace", action="store_true", help="capture every tick (no 1 fps cap / settle wait)")
    ap.add_argument("--max-failures", type=int, default=None,
                    help="override the safety stop after N failed actions in a row (live default: 3)")
    a = ap.parse_args(argv)
    acts, history = asyncio.run(dry_run(a.frames, a.speed, a.goal, a.hz, pace=not a.no_pace,
                                        max_failures=a.max_failures))
    print(f"\n{len(acts)} action(s) would have been sent; none were.")
    for e in history:
        if e.get("level") in ("warn", "error") or "halt" in str(e.get("message", "")):
            print(f"  [{e.get('level')}] {e.get('source', '')}: {e.get('message')}")


if __name__ == "__main__":
    main()
