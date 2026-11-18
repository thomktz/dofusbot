"""Perceiver: one capture -> one GameState (deterministic). VLM fallback for low confidence
screens is requested through `needs_vlm` and done by the agent (rate-limited, async)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..game.grid import Grid
from ..game.state import Character, GameState, MapInfo
from ..ocr.backends import OcrBackend
from ..util import now_ms
from .classifier import Classification, ScreenClassifier
from .dialog import DialogReader
from .fight import FightReader
from .fight_button import FightButtonReader
from .fight_end import CloseButtonReader
from .fight_real import RealFightReader
from .hud import HudReader
from .regions import Regions


def _dark(img: np.ndarray) -> bool:
    """Mostly black game area (the chat box bottom-left is ignored)."""
    H, W = img.shape[:2]
    area = img[int(0.08 * H):int(0.72 * H):4, int(0.20 * W):int(0.85 * W):4]
    return area.size > 0 and float(area.mean()) < 25 and float((area.max(axis=-1) > 60).mean()) < 0.05


@dataclass
class Perception:
    state: GameState
    classification: Classification
    needs_vlm: bool = False
    timings_ms: dict[str, float] = field(default_factory=dict)


class Perceiver:
    def __init__(self, grid_cfg: dict, regions_cfg: dict, ocr: OcrBackend, vlm_threshold: float = 0.55,
                 max_pa: int = 6, max_pm: int = 3):
        self.grid_cfg = grid_cfg
        self.base_grid = Grid.from_config(grid_cfg)
        self.ref_w = float((grid_cfg.get("window_points") or [1280, 800])[0])
        self.regions = Regions(regions_cfg)
        self.ocr = ocr
        self.classifier = ScreenClassifier(self.regions)
        self.hud = HudReader(ocr, self.regions)
        self.fight = FightReader(self.regions)
        self.dialog = DialogReader(ocr, self.regions)
        self.button = FightButtonReader(ocr, self.regions)
        self.real_fight = RealFightReader(ocr, max_pa=max_pa, max_pm=max_pm)
        self.closer = CloseButtonReader(ocr)
        self.vlm_threshold = vlm_threshold
        self.last_coords: tuple[int, int] | None = None

    def grid_for(self, window_w_points: float | None, window_h_points: float | None = None) -> Grid:
        """Scale the calibrated grid to the current Dofus content dimensions.

        Dofus can run at a different aspect ratio, so width-only scaling puts
        clicks on the wrong row. Keep independent x/y scale factors when height
        is available; callers that only have width retain the old behavior.
        """
        ref_h = float((self.grid_cfg.get("window_points") or [1280, 800])[1])
        if not window_w_points and not window_h_points:
            return self.base_grid
        # The tactical board scales uniformly. Independent x/y scaling turns
        # small measurement noise into a visibly sheared/compressed grid.
        kx = (window_w_points / self.ref_w) if window_w_points else 1.0
        ky = kx
        g = self.base_grid
        if abs(kx - 1.0) < 0.0005 and abs(ky - 1.0) < 0.0005:
            return g
        return Grid(ox=g.ox * kx, oy=g.oy * ky, cw=g.cw * kx, ch=g.ch * ky, rows=g.rows, cols=g.cols)

    def perceive(self, img: np.ndarray, scale: float = 1.0, prev: GameState | None = None) -> Perception:
        import time

        t0 = time.perf_counter()
        cls = self.classifier.classify(img)
        button = None
        if cls.screen in ("fight", "unknown"):
            # the fight button's label beats the colour heuristics on the real client
            button = self.button.read(img, scale)
            if button is not None and cls.screen != "fight":
                cls = Classification("fight", max(cls.confidence, 0.85), {**cls.scores, "fight_button": 1.0})
        close = None
        if button is None and (cls.screen in ("unknown", "popup", "loading") or cls.confidence < 0.6):
            # after a fight: level-up / result window with FERMER (often over a black background)
            close = self.closer.read(img, scale)
            if close is not None:
                cls = Classification("popup", max(cls.confidence, 0.9), {**cls.scores, "close_button": 1.0})
            elif cls.screen == "unknown" and _dark(img):
                # black loading screen; the chat text on it defeats the classifier's std test
                cls = Classification("loading", 0.8, {**cls.scores, "dark": 1.0})
            elif cls.screen == "unknown" and self.real_fight.hp(img, scale) is not None:
                # real client on the map: the HUD heart shows HP and there is no fight button
                cls = Classification("world", 0.7, {**cls.scores, "hud_hp": 1.0})
        t1 = time.perf_counter()
        st = GameState(screen=cls.screen, confidence=cls.confidence, updated_at=now_ms())  # type: ignore[arg-type]
        st.close_button = close
        if prev is not None:
            st.character = Character(**prev.character.to_json())
            st.map = MapInfo(prev.map.coords, prev.map.area, prev.map.sub_area)
            st.quest = prev.quest
        if cls.screen in ("world", "dialog", "popup"):
            coords = self.hud.coords(img)
            if coords is not None:
                st.map.coords = coords
                self.last_coords = coords
                area = self.hud.area(img)
                if area:
                    st.map.area = area
        if cls.screen in ("world", "fight", "dialog") and button is None:   # real fight HUD: below
            pa, pm, hp = self.hud.pa(img), self.hud.pm(img), self.hud.hp(img)
            if pa is not None:
                st.character.pa = pa
            if pm is not None:
                st.character.pm = pm
            if hp is not None:
                st.character.hp, st.character.hp_max = hp
        t2 = time.perf_counter()
        if cls.screen == "fight":
            grid = self.grid_for(img.shape[1] / scale, img.shape[0] / scale)
            if button is not None and button.kind == "end_turn":
                # real client, fight in progress: rings, FIN DE TOUR colour, HUD spots, combat log
                rr = self.real_fight.read(img, grid, scale)
                st.fight = rr.fight
                st.fight.log = rr.log
                if rr.hp is not None:
                    st.character.hp = rr.hp
                # PA/PM only when read this frame (the fight skill tracks them otherwise)
                st.character.pa, st.character.pm = rr.pa, rr.pm
            else:
                st.fight = self.fight.read(img, grid, scale)
            st.fight.button = button
            if button is not None and button.kind in ("ready", "challenges"):
                st.fight.phase = "placement"
            me = st.fight.me()
            if me is not None:
                me.pa, me.pm, me.hp, me.hp_max = st.character.pa, st.character.pm, st.character.hp, st.character.hp_max
            if prev is not None and prev.fight is not None:
                st.fight.turn = prev.fight.turn + (1 if prev.fight.phase != "my_turn" and st.fight.phase == "my_turn" else 0)
            elif st.fight.phase == "my_turn":
                st.fight.turn = 1
        elif cls.screen == "dialog":
            st.dialog = self.dialog.read(img, scale)
        t3 = time.perf_counter()
        return Perception(st, cls, needs_vlm=cls.confidence < self.vlm_threshold,
                          timings_ms={"classify": (t1 - t0) * 1e3, "hud": (t2 - t1) * 1e3, "readers": (t3 - t2) * 1e3})
