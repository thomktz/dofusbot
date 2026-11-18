"""The fight's main button, bottom right next to the spell bar. Its label tells the fight phase
on the real client far more reliably than colour heuristics:

- "CHOISIR LES CHALLENGES": placement, the challenge step is offered (we ignore challenges)
- "PRÊT": placement, ready to be clicked
- "FIN DE TOUR": the fight has started

Default crop measured on Thomas's 1280x800 client (docs: combat shots 01-04); a
`fight_main_button` entry in regions.json overrides it.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

import numpy as np

from ..ocr.backends import OcrBackend
from ..ocr.helpers import crop, upscale
from .regions import Regions

ButtonKind = Literal["ready", "challenges", "end_turn"]
DEFAULT_RECT = (0.650, 0.905, 0.130, 0.075)   # x, y, w, h fractions of the content area
UPSCALE = 2.0


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    return " ".join("".join(ch for ch in t if not unicodedata.combining(ch)).upper().split())


def classify_label(text: str) -> ButtonKind | None:
    t = _fold(text)
    if not t:
        return None
    if "CHALLENGE" in t:
        return "challenges"
    if "FIN" in t and "TOUR" in t:
        return "end_turn"
    if "PRET" in t.replace(" ", "") or t in ("PRE", "RET"):
        return "ready"
    return None


@dataclass
class FightButton:
    kind: ButtonKind
    text: str
    x: float          # centre of the label, window points
    y: float

    def to_json(self) -> dict:
        return {"kind": self.kind, "text": self.text, "x": round(self.x, 1), "y": round(self.y, 1)}


class FightButtonReader:
    def __init__(self, ocr: OcrBackend, regions: Regions | None = None):
        self.ocr = ocr
        r = regions.regions.get("fight_main_button") if regions is not None else None
        self.rect = (r.x, r.y, r.w, r.h) if r is not None else DEFAULT_RECT

    def read(self, img: np.ndarray, scale: float = 1.0) -> FightButton | None:
        H, W = img.shape[:2]
        fx, fy, fw, fh = self.rect
        x0, y0 = max(0, int(round(fx * W))), max(0, int(round(fy * H)))
        c = crop(img, (x0, y0, fw * W, fh * H), fractional=False)
        if c.size == 0:
            return None
        lines = self.ocr.recognize(upscale(c, UPSCALE))
        text = " ".join(l.text for l in lines).strip()
        kind = classify_label(text)
        if kind is None:
            return None
        bx0 = min(l.box[0] for l in lines); by0 = min(l.box[1] for l in lines)
        bx1 = max(l.box[2] for l in lines); by1 = max(l.box[3] for l in lines)
        cx = x0 + (bx0 + bx1) / 2 / UPSCALE
        cy = y0 + (by0 + by1) / 2 / UPSCALE
        return FightButton(kind, text, cx / scale, cy / scale)
