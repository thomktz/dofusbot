"""Agent loop: capture -> perceive -> GameState -> planner -> executor (with verification).

Modes (protocol §2.3): observe = perceive + plan, never act (default); assist = propose and
wait for steer.step; auto = act. pause stops actions (perception continues). kill calls
controller.kill() and goes to `stopped` until resume. The controller's own kill switch
(hotkey, mouse slam, user moving the mouse) is wired through controller.on_kill().

The LLM is never called per frame: only the low-confidence screen classifier fallback,
rate-limited by agent.llm_min_interval_s, and whatever a Planner chooses to do.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .bus import Bus
from .corpus import CorpusRecorder
from .executor import Executor, InvalidAction, describe, is_kill_error
from .game.state import MODES, SCREENS, GameState
from .llm import LLMClient, LLMError, load_prompt
from .perception.perceiver import Perceiver
from .planner import NoopPlanner, PlanContext, PlannedStep, Planner, StepResult


class CaptureTimeoutError(RuntimeError):
    """The controller did not return a frame before the configured deadline."""


@dataclass
class Pending:
    step: PlannedStep
    before: GameState
    deadline: float
    attempt: int = 0


@dataclass
class AgentStatusData:
    state: str = "idle"
    mode: str = "observe"
    goal: str | None = None
    task: str | None = None
    proposed: dict | None = None
    loop_hz: float = 0.0
    last_decision_ms: float | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)


class Agent:
    def __init__(self, controller: Any, perceiver: Perceiver, llm: LLMClient, bus: Bus,
                 agent_cfg: dict, planner: Planner | None = None, mock: bool = False,
                 corpus_recorder: CorpusRecorder | None = None):
        self.ctl = controller
        self.perceiver = perceiver
        self.llm = llm
        self.bus = bus
        self.cfg = agent_cfg
        self.planner: Planner = planner or NoopPlanner()
        self.mock = mock
        self.corpus_recorder = corpus_recorder
        self.executor = Executor(controller, bus.log)
        self.status = AgentStatusData()
        self.game = GameState()
        self.last_frame = None
        self._pending: Pending | None = None
        self._proposal: PlannedStep | None = None
        self._step_requested = False
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._vlm_task: asyncio.Task | None = None
        self._last_vlm = 0.0
        self._vlm_hint: tuple[str, float, float] | None = None  # (screen, confidence, monotonic ts)
        self._classification_waiting = False
        # A blocked native screen-capture call cannot be forcibly stopped.  Keep exactly
        # one in flight, rather than starting a new worker thread on every timeout.
        self._capture_task: asyncio.Task | None = None
        self._capture_stalled = False
        self._last_progress = time.monotonic()
        self._last_sig: tuple | None = None
        self._acted_since_progress = False
        self._tick_times: list[float] = []
        self._perm_cache: tuple[float, dict] = (0.0, {})
        self.ticks = 0
        try:
            controller.on_kill(self._on_controller_kill)
        except Exception:
            bus.log("warn", "control", "controller has no on_kill(); kill switch callbacks unavailable")

    # ------------------------------------------------------------------ status
    def _changed(self) -> None:
        self.bus.emit("status", None)

    def _set(self, **kw: Any) -> None:
        changed = False
        for k, v in kw.items():
            if getattr(self.status, k) != v:
                setattr(self.status, k, v)
                changed = True
        if changed:
            self._changed()

    def _permissions(self) -> dict:
        t, cached = self._perm_cache
        if time.monotonic() - t > 5 or not cached:
            try:
                p = self.ctl.permissions()
                cached = {"accessibility": bool(p.accessibility), "screen_recording": bool(p.screen_recording)}
            except Exception:
                cached = {"accessibility": False, "screen_recording": False}
            self._perm_cache = (time.monotonic(), cached)
        return cached

    def status_json(self, window_found: bool | None = None) -> dict:
        s = self.status
        return {
            "state": s.state, "mode": s.mode, "goal": s.goal, "task": s.task, "proposed": s.proposed,
            "loop_hz": round(s.loop_hz, 2),
            "last_decision_ms": None if s.last_decision_ms is None else round(s.last_decision_ms, 1),
            "llm": {"backend": self.llm.backend, "model": self.llm.model, "busy": bool(self.llm.busy)},
            "control": {"connected": bool(window_found) if window_found is not None else self.last_frame is not None,
                        "permissions": self._permissions()},
            "error": s.error,
            "uptime_s": int(time.time() - s.started),
        }

    def _base_state(self) -> str:
        return "running" if self.status.goal else "idle"

    # ------------------------------------------------------------------ steering
    def set_goal(self, text: str | None) -> None:
        text = (text or "").strip() or None
        self.planner.set_goal(text)
        self._pending = None
        self._proposal = None
        st = self.status.state
        new_state = st if st in ("paused", "stopped", "error") else ("running" if text else "idle")
        self._set(goal=text, state=new_state, proposed=None, task=None)
        self.bus.log("info", "user", f"goal: {text!r}")
        self._wake.set()

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self._set(mode=mode)
        self.bus.log("info", "user", f"mode -> {mode}")
        self._wake.set()

    def reset(self) -> None:
        """steer.reset: back to idle, doing nothing. Clears the goal and any fight / NPC state
        in the planner and sets the mode to observe. Sends no input. Leaves a tripped kill
        switch tripped (only Resume resets it)."""
        rst = getattr(self.planner, "reset", None)
        if callable(rst):
            rst()
        else:
            self.planner.set_goal(None)
        self._pending = None
        self._proposal = None
        st = self.status.state
        new_state = "stopped" if st == "stopped" else "idle"
        self._set(goal=None, mode="observe", state=new_state, proposed=None, task=None, error=None)
        self.bus.log("info", "user", "reset: idle, observe, no goal")
        self._wake.set()

    def pause(self) -> None:
        if self.status.state == "stopped":
            return
        self._pending = None
        self._set(state="paused")
        self.bus.log("info", "user", "paused")

    def resume(self) -> None:
        if self.status.state == "stopped":
            try:
                self.ctl.reset_kill()
            except Exception as e:
                self.bus.log("error", "control", f"reset_kill failed: {e}")
                return
        self._set(state=self._base_state(), error=None)
        self.bus.log("info", "user", "resumed")
        self._wake.set()

    def kill(self, source: str = "user") -> None:
        self._pending = None
        self._proposal = None
        try:
            self.ctl.kill()
        except Exception as e:
            self.bus.log("error", "control", f"controller.kill() failed: {e}")
        self._enter_stopped(f"kill switch ({source})")

    def _enter_stopped(self, why: str) -> None:
        if self.status.state != "stopped":
            self._pending = None
            self._proposal = None
            self._set(state="stopped", proposed=None, task=None)
            self.bus.log("warn", "control", f"STOPPED: {why}")

    def _on_controller_kill(self, reason: str | None = None) -> None:  # any thread; KillSwitch passes the reason
        why = f"controller kill switch tripped ({reason})" if reason else "controller kill switch tripped"
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._enter_stopped, why)
        else:
            self.status.state = "stopped"

    async def step(self) -> bool:
        """steer.step: execute the proposed action once (assist mode)."""
        if self.status.mode != "assist":
            raise ValueError("steer.step only works in assist mode")
        if self.status.state in ("paused", "stopped"):
            raise ValueError(f"agent is {self.status.state}")
        if self._proposal is None:
            raise ValueError("nothing proposed")
        await self._execute(self._proposal)
        return True

    # ------------------------------------------------------------------ loop
    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.bus.log("info", "brain", f"agent loop started (mode={self.status.mode}, planner={self.planner.name}, mock={self.mock})")
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                await self.tick()
                if self.status.state == "error":
                    self._set(state=self._base_state(), error=None)
            except Exception as e:  # keep the loop alive; report
                if is_kill_error(e):
                    self._enter_stopped("controller raised KillSwitchTripped")
                else:
                    self.bus.log("error", "brain", f"tick failed: {type(e).__name__}: {e}")
                    if self.status.state not in ("stopped", "paused"):
                        self._set(state="error", error=f"{type(e).__name__}: {e}")
            period = 1.0 / max(0.1, float(self.cfg.get("loop_hz", 4.0)))
            dt = time.monotonic() - t0
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, period - dt))
            except asyncio.TimeoutError:
                pass
        if self._vlm_task:
            self._vlm_task.cancel()

    def _measure_rate(self) -> None:
        now = time.monotonic()
        self._tick_times.append(now)
        self._tick_times = [t for t in self._tick_times if now - t <= 5.0]
        if len(self._tick_times) >= 2:
            hz = (len(self._tick_times) - 1) / (self._tick_times[-1] - self._tick_times[0] + 1e-9)
            if abs(hz - self.status.loop_hz) > 0.2:
                self.status.loop_hz = hz  # sent with the periodic status, not as a "change"

    async def tick(self) -> None:
        self.ticks += 1
        self._measure_rate()
        frame = await self._capture_frame()
        self.last_frame = frame
        self.bus.emit("frame", frame)
        p = self.perceiver.perceive(frame.image, float(frame.scale or 1.0), prev=self.game)
        prev = self.game
        self.game = p.state
        if self.corpus_recorder is not None:
            try:
                saved = await asyncio.to_thread(self.corpus_recorder.record, frame.image,
                                                 float(frame.scale or 1.0), p.classification)
                if saved is not None:
                    self.bus.log("info", "perception", f"saved evaluation sample: {saved.name}")
            except Exception as e:
                self.bus.log("warn", "perception", f"could not save evaluation sample: {e}")
        classification_ready = not p.needs_vlm
        if p.needs_vlm:
            hint = self._vlm_hint
            if hint and time.monotonic() - hint[2] < float(self.cfg.get("vlm_hint_ttl_s", 10.0)) \
                    and hint[1] >= float(self.cfg.get("classifier_vlm_threshold", 0.55)):
                self.game.screen, self.game.confidence = hint[0], hint[1]  # type: ignore[assignment]
                classification_ready = True
            else:
                self._maybe_vlm_classify(frame.image)
        if not classification_ready:
            self._hold_for_classification()
            return
        self._classification_waiting = False
        self._feed_planner(frame)
        if prev.to_json() | {"updated_at": 0} != self.game.to_json() | {"updated_at": 0}:
            self.bus.emit("state", None)
        self._track_progress()

        if self.status.state in ("paused", "stopped", "error"):
            return
        if not self.status.goal:
            return
        if self._pending is not None:
            await self._check_pending()
            return
        await self._plan()

    async def _capture_frame(self):
        """Return one capture, without allowing a hung native call to freeze the loop.

        ``asyncio.to_thread`` cannot cancel a blocking macOS capture after it has begun.
        Shielding the task means a timeout leaves that one call in flight; later ticks wait
        on it instead of accumulating worker threads and stale captures.
        """
        if self._capture_task is None:
            self._capture_task = asyncio.create_task(asyncio.to_thread(self.ctl.capture))
        timeout = max(0.1, float(self.cfg.get("capture_timeout_s", 5.0)))
        try:
            frame = await asyncio.wait_for(asyncio.shield(self._capture_task), timeout=timeout)
        except asyncio.TimeoutError as e:
            if not self._capture_stalled:
                self.bus.log("warn", "control", f"capture stalled for {timeout:.1f}s; waiting for the existing capture")
                self._capture_stalled = True
            raise CaptureTimeoutError(f"capture timed out after {timeout:.1f}s") from e
        finally:
            if self._capture_task is not None and self._capture_task.done():
                self._capture_task = None
        if self._capture_stalled:
            self.bus.log("info", "control", "capture recovered")
            self._capture_stalled = False
        return frame

    def _hold_for_classification(self) -> None:
        """Discard state derived from an ambiguous frame before it can cause input."""
        if self._pending is not None:
            pending = self._pending
            self._pending = None
            self._acted_since_progress = False
            self.planner.on_result(StepResult(pending.step, False, "classification_uncertain"), self.game)
        if self._proposal is not None or self.status.proposed is not None:
            self._proposal = None
            self._set(proposed=None, task=None)
        if not self._classification_waiting:
            self.bus.log("info", "brain", "waiting for confident screen classification before planning")
            self._classification_waiting = True

    async def _plan(self) -> None:
        ctx = PlanContext(goal=self.status.goal, mode=self.status.mode,
                          grid=self.perceiver.grid_for(self._window_w(), self._window_h()), config=self.cfg, llm=self.llm)
        step = await self.planner.propose(self.game, ctx)
        self._proposal = step
        proposed = step.action if step else None
        if proposed != self.status.proposed:
            self._set(proposed=proposed, task=step.task if step else None)
            if step is not None and self.status.mode != "auto":
                self.bus.log("info", "planner", "proposed " + describe(step.action), action=step.action)
        if step is None:
            return
        if self.status.mode == "auto":
            await self._execute(step)

    def _feed_planner(self, frame) -> None:
        """Read-only: hand the raw frame to the planner's pixel readers (placement board,
        terrain preview). Never sends input; a reader error never breaks the tick."""
        scale = float(frame.scale or 1.0)
        before = getattr(self.planner, "preview_status", None)
        for name in ("feed_frame", "observe_frame"):
            fn = getattr(self.planner, name, None)
            if fn is None:
                continue
            try:
                if name == "feed_frame":
                    fn(frame.image, scale, self.game, regions=self.perceiver.regions,
                       grid=self.perceiver.grid_for(self._window_w(), self._window_h()))
                else:
                    fn(frame.image, scale, self.game)
            except Exception as e:
                self.bus.log("warn", "perception", f"planner {name} failed: {e}")
        after = getattr(self.planner, "preview_status", None)
        if after != before:
            if after:
                self.bus.log("info" if after.startswith("preview parsed") else "warn", "perception", after)
            self.bus.emit("status", None)        # push planner.status (preview line) to the dashboard

    def _window_w(self) -> float | None:
        f = self.last_frame
        if f is None:
            return None
        return f.image.shape[1] / float(f.scale or 1.0)

    def _window_h(self) -> float | None:
        f = self.last_frame
        if f is None:
            return None
        return f.image.shape[0] / float(f.scale or 1.0)

    async def _execute(self, step: PlannedStep, attempt: int = 0) -> None:
        if self.status.mode == "observe" or self.status.state in ("paused", "stopped"):
            return  # hard guard: never act in observe
        before = self.game
        try:
            await self.executor.execute(step.action)
        except InvalidAction as e:
            self.bus.log("error", "planner", f"invalid action from planner: {e}")
            self.planner.on_result(StepResult(step, False, f"error: {e}"), self.game)
            return
        except Exception as e:
            if is_kill_error(e):
                self._enter_stopped("controller raised KillSwitchTripped")
                return
            raise
        self._acted_since_progress = True
        self._proposal = None
        self._set(proposed=None, task=step.task)
        if step.verify is None:
            self.planner.on_result(StepResult(step, True, "no_verify"), self.game)
            return
        timeout = step.timeout_s if step.timeout_s is not None else float(self.cfg.get("action_timeout_s", 6.0))
        self._pending = Pending(step, before, time.monotonic() + timeout, attempt)

    async def _check_pending(self) -> None:
        pend = self._pending
        assert pend is not None
        ok = False
        try:
            ok = bool(pend.step.verify(pend.before, self.game))  # type: ignore[misc]
        except Exception as e:
            self.bus.log("warn", "planner", f"verify raised {e}")
        if ok:
            self._pending = None
            self.bus.log("info", "brain", f"verified: {pend.step.task or describe(pend.step.action)}")
            self.planner.on_result(StepResult(pend.step, True, "verified"), self.game)
            return
        if time.monotonic() < pend.deadline:
            return
        retries = pend.step.retries if pend.step.retries is not None else int(self.cfg.get("action_retries", 2))
        self._pending = None
        if pend.attempt < retries and self.status.mode == "auto":
            self.bus.log("warn", "brain", f"not verified after timeout, retry {pend.attempt + 1}/{retries}: "
                         f"{pend.step.task or describe(pend.step.action)}")
            await self._execute(pend.step, attempt=pend.attempt + 1)
        else:
            self.bus.log("error", "brain", f"step failed (timeout, {pend.attempt} retries): "
                         f"{pend.step.task or describe(pend.step.action)}")
            self.planner.on_result(StepResult(pend.step, False, "timeout"), self.game)

    def _track_progress(self) -> None:
        sig = self.game.signature()
        now = time.monotonic()
        if sig != self._last_sig:
            self._last_sig = sig
            self._last_progress = now
            self._acted_since_progress = False
            return
        stuck_after = float(self.cfg.get("stuck_after_s", 30.0))
        if self._acted_since_progress and now - self._last_progress > stuck_after and self.status.state == "running":
            self.bus.log("warn", "brain", f"stuck: no visible change for {stuck_after:.0f}s despite actions")
            self._acted_since_progress = False
            self._last_progress = now
            if self._pending is not None:
                self.planner.on_result(StepResult(self._pending.step, False, "stuck"), self.game)
                self._pending = None

    # ------------------------------------------------------------------ VLM fallback
    def _maybe_vlm_classify(self, image) -> None:
        if self.llm.backend == "mock" and not self.cfg.get("vlm_with_mock", False):
            return
        if self.llm.busy or (self._vlm_task and not self._vlm_task.done()):
            return
        if time.monotonic() - self._last_vlm < float(self.cfg.get("llm_min_interval_s", 10.0)):
            return
        self._last_vlm = time.monotonic()
        self._vlm_task = asyncio.get_running_loop().create_task(self._vlm_classify(image))

    async def _vlm_classify(self, image) -> None:
        def valid(d: Any) -> dict:
            if not isinstance(d, dict) or d.get("screen") not in SCREENS:
                raise ValueError("screen must be one of " + ",".join(SCREENS))
            return d

        try:
            self._changed()
            r = await self.llm.chat_json(load_prompt("classify_screen"), images=[image], validate=valid, max_tokens=120)
        except LLMError as e:
            self.bus.log("warn", "llm", f"screen classification fallback failed: {e}")
            return
        finally:
            self._changed()
        self._set(last_decision_ms=r.timings.total_ms)
        d = r.data
        conf = float(d.get("confidence", 0.6) or 0.6)
        self.bus.log("info", "llm", f"VLM says screen={d['screen']} ({conf:.2f}) in {r.timings.total_ms:.0f} ms: {d.get('reason', '')}")
        self._vlm_hint = (d["screen"], conf, time.monotonic())
        if conf > self.game.confidence:
            self.game.screen = d["screen"]
            self.game.confidence = conf
            self.bus.emit("state", None)
