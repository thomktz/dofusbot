"""Screen classifier: cheap colour heuristics on fixed regions first; the caller may ask the
VLM when confidence is below `classifier_vlm_threshold` (see Perceiver).

Every heuristic is a score in 0..1; the best one wins and its score is the confidence.
Thresholds/colours are placeholders: re-sample the palette in config/regions.json on the
real client (tactical mode ON makes fights trivially recognisable: flat grey cells).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .regions import Regions


@dataclass
class Classification:
    screen: str
    confidence: float
    scores: dict[str, float]


def _ramp(v: float, lo: float, hi: float) -> float:
    if v <= lo:
        return 0.0
    if v >= hi:
        return 1.0
    return (v - lo) / (hi - lo)


class ScreenClassifier:
    def __init__(self, regions: Regions):
        self.r = regions

    def features(self, img: np.ndarray) -> dict[str, float]:
        r = self.r
        small = img[::4, ::4]
        game = r.crop(img, "game_area")[::4, ::4]
        tactical = sum(r.color_fraction(game, c, 12) for c in
                       ("tactical_cell", "tactical_cell_alt", "blocked_cell", "move_highlight"))
        return {
            "brightness": float(small.mean()),
            "std": float(small.std()),
            "tactical": tactical,
            "dialog_bg": r.color_fraction(r.crop(img, "dialog_box")[::2, ::2], "dialog_bg", 14),
            "popup_bg": r.color_fraction(r.crop(img, "popup_center")[::2, ::2], "popup_bg", 14),
            "hud_bg": r.color_fraction(r.crop(img, "hud_bar")[::2, ::2], "hud_bg", 14),
        }

    def classify(self, img: np.ndarray) -> Classification:
        f = self.features(img)
        hud = _ramp(f["hud_bg"], 0.2, 0.6)
        scores = {
            "loading": _ramp(40 - f["brightness"], 0, 25) * _ramp(20 - f["std"], 0, 15),
            "fight": _ramp(f["tactical"], 0.2, 0.55),
            "dialog": _ramp(f["dialog_bg"], 0.3, 0.75) * (0.5 + 0.5 * hud),
            "popup": _ramp(f["popup_bg"], 0.3, 0.75) * 0.95,
            "world": hud * 0.9,
        }
        # overlays win over the world underneath them
        if scores["dialog"] > 0.5 or scores["popup"] > 0.5 or scores["fight"] > 0.5:
            scores["world"] *= 0.5
        screen = max(scores, key=scores.get)  # type: ignore[arg-type]
        conf = scores[screen]
        if conf < 0.2:
            screen = "unknown"
        return Classification(screen, round(conf, 3), {k: round(v, 3) for k, v in scores.items()})
