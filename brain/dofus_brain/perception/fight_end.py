"""After a fight (Thomas's stream of 9 Nov, 21:53): a level-up window ("NIVEAU 11" ... "FERMER")
and then the fight result window ("Combat terminé", "VICTOIRE" ... "FERMER"). Both close with
their FERMER button. Read with OCR on the centre of the screen, only on frames that are
not a fight (perceiver).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from ..ocr.backends import OcrBackend
from ..ocr.helpers import crop
from ..textnorm import norm

CENTER = (0.25, 0.08, 0.50, 0.84)      # x, y, w, h fractions: both windows sit in here
LEVEL_RE = re.compile(r"\bniveau\s+\d+", re.IGNORECASE)


@dataclass
class CloseButton:
    kind: str          # "level_up" | "fight_result" | "other"
    text: str          # all text read in the window area (for the popup rules)
    x: float           # FERMER centre, window points
    y: float


def window_kind(text: str) -> str:
    t = norm(text)
    if "victoire" in t or "defaite" in t or "combat termine" in t:
        return "fight_result"
    if LEVEL_RE.search(t):
        return "level_up"
    return "other"


class CloseButtonReader:
    def __init__(self, ocr: OcrBackend):
        self.ocr = ocr

    def read(self, img: np.ndarray, scale: float = 1.0) -> CloseButton | None:
        H, W = img.shape[:2]
        fx, fy, fw, fh = CENTER
        x0, y0 = int(round(fx * W)), int(round(fy * H))
        lines = self.ocr.recognize(crop(img, (x0, y0, fw * W, fh * H), fractional=False))
        text = " ".join(l.text for l in lines).strip()
        kind = window_kind(text)
        fermer = next((l for l in lines if norm(l.text).strip() == "fermer"), None)
        if fermer is None:
            # no FERMER, no window: a monster tooltip on the map also says "Niveau 7"
            return None
        bx = x0 + (fermer.box[0] + fermer.box[2]) / 2
        by = y0 + (fermer.box[1] + fermer.box[3]) / 2
        return CloseButton(kind, text, bx / scale, by / scale)
