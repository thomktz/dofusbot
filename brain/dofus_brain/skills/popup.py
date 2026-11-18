"""Popups: known harmless ones (level up, achievement, tip...) are closed with the dismiss
key, once per popup text; invitations / messages from other players and anything unknown
pause and ask. Never answers chat, never accepts anything."""
from __future__ import annotations

import re

import numpy as np

from ..game.state import GameState
from ..ocr.backends import OcrBackend, text_of
from ..perception.regions import Regions
from ..planner import PlanContext, PlannedStep, StepResult
from . import Outcome, StepOrOutcome
from ..textnorm import norm


def classify_popup(text: str, cfg: dict) -> str:
    """'ask' (other players, trades...) wins over 'dismiss'; neither = 'unknown'."""
    t = norm(text)
    if not t:
        return "unknown"
    pc = cfg["popups"]
    if any(re.search(rf"\b{re.escape(norm(w))}", t) for w in pc.get("ask", [])):
        return "ask"
    if any(re.search(rf"\b{re.escape(norm(w))}", t) for w in pc.get("dismiss", [])):
        return "dismiss"
    return "unknown"


class PopupReader:
    def __init__(self, ocr: OcrBackend, regions: Regions, region: str = "popup_center"):
        self.ocr = ocr
        self.r = regions
        self.region = region

    def read(self, img: np.ndarray) -> str:
        return text_of(self.ocr.recognize(self.r.crop(img, self.region))).strip()


class PopupSkill:
    name = "popup"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.key = cfg["keys"]["dismiss"]
        self.text = ""
        self.tried: set[str] = set()
        self.fermer_tries: dict[str, int] = {}

    def set_text(self, text: str) -> None:
        self.text = text or ""

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        if state.screen != "popup":
            return Outcome("done", "no popup")
        close = getattr(state, "close_button", None)
        if close is not None:
            self.text = close.text
        kind = classify_popup(self.text, self.cfg)
        if kind == "ask":
            return Outcome("ask", f"another player or a request needs you: {self.text[:120]!r}")
        if kind == "unknown":
            return Outcome("ask", f"unknown popup: {self.text[:120]!r}" if self.text else "unreadable popup")
        if close is not None:
            # FERMER clicks may miss (window still animating): two tries per window kind
            n = self.fermer_tries.get(close.kind, 0)
            if n >= 2:
                return Outcome("ask", f"{close.kind} window did not close with FERMER: {self.text[:120]!r}")
            self.fermer_tries[close.kind] = n + 1
        else:
            key = norm(self.text)
            if key in self.tried:
                return Outcome("ask", f"popup did not close with {self.key}: {self.text[:120]!r}")
            self.tried.add(key)

        def closed(before: GameState, now: GameState) -> bool:
            nb = getattr(now, "close_button", None)
            return now.screen != "popup" or (close is not None and nb is not None and nb.kind != close.kind)

        if close is not None:
            # level-up / fight result window: click its FERMER button (Thomas, 9 Nov)
            return PlannedStep(action={"kind": "click", "x": round(close.x, 1), "y": round(close.y, 1),
                                       "reason": f"close {close.kind} window: click FERMER"},
                               task=f"close {close.kind} window", verify=closed, timeout_s=5.0, retries=0,
                               meta={"skill": self.name, "via": "fermer"})
        return PlannedStep(action={"kind": "key", "key": self.key, "reason": f"close popup: {self.text[:60]}"},
                           task="close popup", verify=closed, timeout_s=2.5, retries=0,
                           meta={"skill": self.name})

    def on_result(self, result: StepResult, state: GameState) -> None:
        if result.step.meta.get("skill") == self.name and result.ok:
            self.tried.clear()  # the next popup with the same text (next level up) may be closed again
            self.fermer_tries.clear()
