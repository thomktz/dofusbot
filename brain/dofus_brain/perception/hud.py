"""HUD readers: map coords, area name, PA/PM/HP via OCR on fixed crops (config/regions.json)."""
from __future__ import annotations

import re

import numpy as np

from ..ocr.backends import OcrBackend, text_of
from ..ocr.helpers import upscale
from .regions import Regions

COORDS_RE = re.compile(r"(-?\d{1,3})\s*[,.;]\s*(-?\d{1,3})")
INT_RE = re.compile(r"-?\d+")
FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
# OCR often confuses these on small game fonts
_FIXES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8", "—": "-", "–": "-", "−": "-"})


def parse_coords(text: str) -> tuple[int, int] | None:
    """'4,-18' / '4, -18' / 'Astrub (4,-18)' -> (4, -18)."""
    if not text:
        return None
    for candidate in (text, text.translate(_FIXES)):
        m = COORDS_RE.search(candidate)
        if m:
            x, y = int(m.group(1)), int(m.group(2))
            if -200 <= x <= 200 and -200 <= y <= 200:
                return (x, y)
    return None


def parse_int(text: str) -> int | None:
    m = INT_RE.search(text.translate(_FIXES)) if text else None
    return int(m.group(0)) if m else None


def parse_fraction(text: str) -> tuple[int, int] | None:
    m = FRACTION_RE.search(text.translate(_FIXES)) if text else None
    return (int(m.group(1)), int(m.group(2))) if m else None


class HudReader:
    def __init__(self, ocr: OcrBackend, regions: Regions, upscale_factor: float = 1.0):
        self.ocr = ocr
        self.r = regions
        self.up = upscale_factor

    def _text(self, img: np.ndarray, region: str) -> str:
        if region not in self.r.regions:
            return ""
        crop = self.r.crop(img, region)
        return text_of(self.ocr.recognize(upscale(crop, self.up)))

    def coords(self, img: np.ndarray) -> tuple[int, int] | None:
        return parse_coords(self._text(img, "map_coords"))

    def area(self, img: np.ndarray) -> str | None:
        t = self._text(img, "area_name").strip()
        return t or None

    def pa(self, img: np.ndarray) -> int | None:
        return parse_int(self._text(img, "pa"))

    def pm(self, img: np.ndarray) -> int | None:
        return parse_int(self._text(img, "pm"))

    def hp(self, img: np.ndarray) -> tuple[int, int] | None:
        return parse_fraction(self._text(img, "hp"))
