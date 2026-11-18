"""NPC context menu (opened by clicking an NPC): OCR a box around the click point and return
the menu entries with their window-point positions.

A menu is considered open when at least one line matches a known entry (talk, buy, sell,
exchange, ...), so random map text near the NPC is not mistaken for a menu.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..ocr.backends import OcrBackend
from ..textnorm import norm

TALK_WORDS = ("parler", "discuter", "talk", "speak")
MENU_WORDS = TALK_WORDS + ("acheter", "vendre", "echanger", "buy", "sell", "trade", "exchange", "defier",
                           "challenge", "fermer", "close", "utiliser", "use", "boutique", "shop")


@dataclass
class MenuOption:
    text: str
    x: float      # window points
    y: float

    @property
    def is_talk(self) -> bool:
        n = norm(self.text)
        return any(n.startswith(w) or f" {w}" in n for w in TALK_WORDS)


class NpcMenuReader:
    """`box_pt` = (dx0, dy0, dx1, dy1) around the click point, in window points: the menu
    opens next to the cursor, usually to the right and below."""

    def __init__(self, ocr: OcrBackend, box_pt: tuple[float, float, float, float] = (-40, -40, 260, 220)):
        self.ocr = ocr
        self.box = box_pt

    def read(self, img: np.ndarray, scale: float, near: tuple[float, float]) -> list[MenuOption]:
        H, W = img.shape[:2]
        x0 = int(max(0, (near[0] + self.box[0]) * scale))
        y0 = int(max(0, (near[1] + self.box[1]) * scale))
        x1 = int(min(W, (near[0] + self.box[2]) * scale))
        y1 = int(min(H, (near[1] + self.box[3]) * scale))
        if x1 <= x0 or y1 <= y0:
            return []
        lines = self.ocr.recognize(img[y0:y1, x0:x1])
        opts = []
        for ln in lines:
            bx0, by0, bx1, by1 = ln.box
            opts.append(MenuOption(ln.text.strip(), (x0 + (bx0 + bx1) / 2) / scale, (y0 + (by0 + by1) / 2) / scale))
        if not any(any(norm(o.text).startswith(w) for w in MENU_WORDS) for o in opts):
            return []
        return [o for o in opts if o.text]

    __call__ = read
