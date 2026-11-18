from dofus_brain.game.state import GameState
from dofus_brain.planner import PlannedStep, StepResult
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.safety import SafetyGuard


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def ok(before, now):
    return True


def guard():
    clock = Clock()
    halts = []
    g = SafetyGuard(load_phases_config(), clock=clock, on_halt=halts.append)
    return g, clock, halts


def test_requires_verify_and_known_keys():
    g, _, _ = guard()
    assert g.check(PlannedStep({"kind": "click", "x": 1, "y": 1})) is not None
    assert g.check(PlannedStep({"kind": "click", "x": 1, "y": 1}, verify=ok)) is None
    assert g.check(PlannedStep({"kind": "wait", "ms": 100})) is None
    assert g.check(PlannedStep({"kind": "key", "key": "f1"}, verify=ok)) is None
    assert g.check(PlannedStep({"kind": "key", "key": "escape"}, verify=ok)) is None
    assert "not a configured key" in g.check(PlannedStep({"kind": "key", "key": "q"}, verify=ok))
    assert g.check(PlannedStep({"kind": "type", "text": "hi"}, verify=ok)) is not None
    assert g.check(PlannedStep({"kind": "key", "key": "f1", "modifiers": ["cmd"]}, verify=ok)) is not None


def test_unknown_screen_halts_after_timeout():
    g, clock, halts = guard()
    g.observe(GameState(screen="unknown", confidence=0.1))
    clock.t = 5
    g.observe(GameState(screen="unknown", confidence=0.1))
    assert g.halted is None
    clock.t = 9
    g.observe(GameState(screen="unknown", confidence=0.1))
    assert g.halted and halts
    assert g.check(PlannedStep({"kind": "click", "x": 1, "y": 1}, verify=ok)).startswith("halted")
    g.reset()
    assert g.halted is None


def test_known_screen_clears_timer_and_loading_is_fine():
    g, clock, _ = guard()
    g.observe(GameState(screen="unknown", confidence=0.1))
    clock.t = 7
    g.observe(GameState(screen="world", confidence=0.9))
    clock.t = 30
    g.observe(GameState(screen="loading", confidence=0.9))
    assert g.halted is None and g.screen_ok()


def test_consecutive_failures_halt():
    g, _, _ = guard()
    s = PlannedStep({"kind": "click", "x": 1, "y": 1}, verify=ok, task="t")
    g.on_result(StepResult(s, False, "timeout"))
    g.on_result(StepResult(s, True, "verified"))
    g.on_result(StepResult(s, False, "timeout"))
    g.on_result(StepResult(s, False, "timeout"))
    assert g.halted is None
    g.on_result(StepResult(s, False, "timeout"))
    assert "3 failed steps" in g.halted


def test_kill():
    g, _, _ = guard()
    g.kill()
    assert g.halted == "kill switch"
