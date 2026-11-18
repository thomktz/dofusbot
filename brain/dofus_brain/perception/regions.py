"""Fractional crop regions (config/regions.json) -> pixel boxes on a capture."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Region:
    x: float
    y: float
    w: float
    h: float

    def box(self, width: int, height: int) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) in pixels of an image of size width x height."""
        x0 = int(round(self.x * width))
        y0 = int(round(self.y * height))
        x1 = int(round((self.x + self.w) * width))
        y1 = int(round((self.y + self.h) * height))
        return (max(0, x0), max(0, y0), min(width, x1), min(height, y1))

    def crop(self, img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        x0, y0, x1, y1 = self.box(w, h)
        return img[y0:y1, x0:x1]


class Regions:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.regions = {k: Region(**v) for k, v in cfg.get("regions", {}).items()}
        self.palette = {k: np.array(v, dtype=np.int16) for k, v in cfg.get("palette", {}).items()}
        self.tolerance = int(cfg.get("color_tolerance", 40))

    def __getitem__(self, name: str) -> Region:
        return self.regions[name]

    def crop(self, img: np.ndarray, name: str) -> np.ndarray:
        return self.regions[name].crop(img)

    def offset(self, img: np.ndarray, name: str) -> tuple[int, int]:
        h, w = img.shape[:2]
        x0, y0, _, _ = self.regions[name].box(w, h)
        return x0, y0

    def color_mask(self, img: np.ndarray, colour: str, tol: int | None = None) -> np.ndarray:
        ref = self.palette[colour]
        t = self.tolerance if tol is None else tol
        diff = np.abs(img.astype(np.int16) - ref).max(axis=-1)
        return diff <= t

    def color_fraction(self, img: np.ndarray, colour: str, tol: int | None = None) -> float:
        if img.size == 0:
            return 0.0
        return float(self.color_mask(img, colour, tol).mean())
