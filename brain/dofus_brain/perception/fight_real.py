"""Fight reader for Thomas's real client (creature mode, 1280x800 content, measured on combat
shots 03/04 of 9 Nov). Used once the main button reads "FIN DE TOUR" (perception.fight_button).

- Fighters: every fighter stands on a coloured ring drawn around its cell centre: red for
  the enemy team, blue for ours. Our own cell also gets a white dashed outline.
- Whose turn: the FIN DE TOUR button is bright yellow-green while it is our turn. Turn
  detection was only checked on our turn (no enemy-turn shot yet). Anything that is not
  clearly our turn is read as the enemy's turn, so the bot never acts on a doubtful frame.
- HUD: HP, PM and the turn timer read with OCR at fixed spots. The PA digit on the blue
  star is too small for Apple Vision (it reads 6 as 9), so values are only accepted within
  0..max, and the fight skill tracks PA/PM itself when they are missing.
- Combat log: the chat's last lines ("Chern-Simons lance Flèche Glacée.") verify our casts.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..game.grid import Grid
from ..game.state import Fighter, FightState
from ..ocr.backends import OcrBackend
from ..ocr.helpers import crop, upscale

# fixed spots on the 1280x800 content area, pixels at scale 1 (scaled with the capture)
REF_W, REF_H = 1280.0, 800.0
HP_RECT = (350, 712, 60, 38)
PA_RECT = (340, 758, 36, 32)
PM_RECT = (382, 758, 36, 32)
TIMER_RECT = (890, 700, 60, 30)
CHAT_RECT = (0, 600, 228, 185)
BUTTON_RECT = (860, 738, 122, 34)
# where fighters can stand: the board, minus the HUD, chat, minimap and timeline
BOARD = (240, 80, 1090, 700)

LANCE_RE = re.compile(r"(?P<who>[\w\-' ]+?)\s+lance\s+(?P<spell>.+?)\.?$", re.IGNORECASE)


def _red(px: np.ndarray) -> np.ndarray:
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    return (r > 165) & (g < 115) & (b < 85) & (r - g > 85)


def _blue(px: np.ndarray) -> np.ndarray:
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    return (b > 175) & (r < 115) & (b - r > 85)


def _white(px: np.ndarray) -> np.ndarray:
    return px.min(axis=-1) > 215


@dataclass
class RealFightRead:
    fight: FightState
    my_turn: bool
    hp: int | None = None
    pa: int | None = None
    pm: int | None = None
    timer_s: int | None = None
    log: list[str] = field(default_factory=list)


class RealFightReader:
    def __init__(self, ocr: OcrBackend | None, max_pa: int = 6, max_pm: int = 3, max_hp: int = 10000):
        self.ocr = ocr
        self.max_pa, self.max_pm, self.max_hp = max_pa, max_pm, max_hp

    # ------------------------------------------------------------------ fighters
    def _ring_score(self, img: np.ndarray, x: float, y: float, rx: float, ry: float, test) -> tuple[float, float]:
        """(ring, inside): fraction of points on the ring's lower arc / just inside it that
        match the colour. Rings are hollow (filled start cells are not) and show on both the
        left and the right side of the cell (a red object off to one side does not), so
        `ring` is the weaker of the two sides."""
        H, W = img.shape[:2]
        side = {True: [0, 0], False: [0, 0]}      # right side?: [hits, points]
        inside = n = 0
        for deg in range(-20, 201, 15):            # lower arc (screen y grows downwards)
            a = math.radians(deg)
            px, py = int(round(x + rx * math.cos(a))), int(round(y + ry * math.sin(a)))
            ix, iy = int(round(x + 0.45 * rx * math.cos(a))), int(round(y + 0.45 * ry * math.sin(a)))
            if not (1 <= px < W - 1 and 1 <= py < H - 1):
                continue
            n += 1
            s = side[deg < 90]
            s[1] += 1
            s[0] += bool(test(img[py - 1:py + 2, px - 1:px + 2]).any())
            inside += bool(test(img[iy:iy + 1, ix:ix + 1]).any())
        if not n or not side[True][1] or not side[False][1]:
            return 0.0, 0.0, 0.0
        both = min(side[True][0] / side[True][1], side[False][0] / side[False][1])
        return both, (side[True][0] + side[False][0]) / n, inside / n

    @staticmethod
    def _pawn(img: np.ndarray, x: float, y: float, cw: float, ch: float) -> float:
        """Creature mode draws monsters as dark grey pawns standing on the cell: fraction of
        dark pixels in a box just above the cell centre."""
        x0, x1 = int(x - 0.15 * cw), int(x + 0.15 * cw)
        y0, y1 = int(y - 0.75 * ch), int(y)
        box = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)].astype(int)
        if box.size == 0:
            return 0.0
        mx, mn = box.max(axis=-1), box.min(axis=-1)
        return float(((mx < 110) & (mn > 25) & (mx - mn < 45)).mean())

    def _self_marker(self, img: np.ndarray, x: float, y: float, cw: float, ch: float) -> float:
        """White dashed outline of our cell: fraction of points along the diamond edge."""
        H, W = img.shape[:2]
        hits = n = 0
        for t in np.linspace(0, 1, 9, endpoint=False):
            for (x0, y0), (x1, y1) in (((0, -1), (1, 0)), ((1, 0), (0, 1)), ((0, 1), (-1, 0)), ((-1, 0), (0, -1))):
                px = int(round(x + 0.47 * cw * (x0 + (x1 - x0) * t)))
                py = int(round(y + 0.47 * ch * (y0 + (y1 - y0) * t)))
                if 1 <= px < W - 1 and 1 <= py < H - 1:
                    n += 1
                    hits += bool(_white(img[py - 1:py + 2, px - 1:px + 2]).any())
        return hits / n if n else 0.0

    def fighters(self, img: np.ndarray, grid: Grid, scale: float = 1.0) -> list[Fighter]:
        H, W = img.shape[:2]
        k = W / (REF_W * scale) if W else 1.0     # capture px per reference px, beyond `scale`
        bx0, by0, bx1, by1 = (v * scale * k for v in BOARD)
        rx, ry = 0.30 * grid.cw * scale, 0.30 * grid.ch * scale
        found: list[tuple[str, int, float, float]] = []
        for c in range(grid.n_cells):
            x, y = grid.center_px(c, scale)
            if not (bx0 <= x < bx1 and by0 <= y < by1):
                continue
            for team, test in (("enemy", _red), ("ally", _blue)):
                both, total, inside = self._ring_score(img, x, y, rx, ry, test)
                ring = both
                if inside > 0.3:
                    continue
                if both < 0.25 and team == "enemy" and total >= 0.33:
                    # one side hidden (crate, wall): accept when a pawn stands on the cell
                    if self._pawn(img, x, y, grid.cw * scale, grid.ch * scale) >= 0.4:
                        ring = total
                if ring >= 0.25:
                    mark = self._self_marker(img, x, y, grid.cw * scale, grid.ch * scale) if team == "ally" else 0.0
                    found.append((team, c, ring, mark))
        # our Cra is a red creature on a blue ring: a cell matching both teams is an ally
        ally_cells = {f[1] for f in found if f[0] == "ally"}
        found = [f for f in found if not (f[0] == "enemy" and f[1] in ally_cells)]
        out: list[Fighter] = []
        allies = [f for f in found if f[0] == "ally"]
        self_cell = max(allies, key=lambda f: (f[3], f[2]))[1] if allies else None
        n_e = n_a = 0
        for team, c, _, _ in sorted(found, key=lambda f: f[1]):
            if c == self_cell:
                out.append(Fighter(id="self", name="self", team="ally", is_self=True, cell=c))
            elif team == "ally":
                n_a += 1
                out.append(Fighter(id=f"ally{n_a}", name=f"ally{n_a}", team="ally", cell=c))
            else:
                n_e += 1
                out.append(Fighter(id=f"enemy{n_e}", name=f"enemy{n_e}", team="enemy", cell=c))
        return out

    # ------------------------------------------------------------------ turn, HUD
    @staticmethod
    def _rect(img: np.ndarray, rect, scale: float) -> np.ndarray:
        H, W = img.shape[:2]
        k = W / REF_W
        x, y, w, h = rect
        return crop(img, (x * k, y * k, w * k, h * k), fractional=False)

    def is_my_turn(self, img: np.ndarray, scale: float = 1.0) -> bool:
        c = self._rect(img, BUTTON_RECT, scale).astype(int)
        if c.size == 0:
            return False
        r, g, b = c[..., 0], c[..., 1], c[..., 2]
        lime = (g > 130) & (r > 100) & (b < 95) & (g - b > 70)
        return float(lime.mean()) > 0.35

    def _int(self, img, rect, scale, hi: int) -> int | None:
        if self.ocr is None:
            return None
        lines = self.ocr.recognize(upscale(self._rect(img, rect, scale), 3))
        m = re.search(r"\d+", " ".join(l.text for l in lines))
        if not m:
            return None
        v = int(m.group(0))
        return v if 0 <= v <= hi else None    # e.g. PA "6" misread as "9": reject, track instead

    def hp(self, img: np.ndarray, scale: float = 1.0) -> int | None:
        """HP in the HUD heart (also shown on the map, outside fights)."""
        return self._int(img, HP_RECT, scale, self.max_hp)

    def log_lines(self, img: np.ndarray, scale: float = 1.0) -> list[str]:
        if self.ocr is None:
            return []
        lines = self.ocr.recognize(upscale(self._rect(img, CHAT_RECT, scale), 2))
        return [l.text.strip() for l in sorted(lines, key=lambda l: l.box[1]) if l.text.strip()]

    def read(self, img: np.ndarray, grid: Grid, scale: float = 1.0, with_log: bool = True) -> RealFightRead:
        my_turn = self.is_my_turn(img, scale)
        fs = FightState(phase="my_turn" if my_turn else "enemy_turn", fighters=self.fighters(img, grid, scale))
        hp = self._int(img, HP_RECT, scale, self.max_hp)
        pa = self._int(img, PA_RECT, scale, self.max_pa)
        pm = self._int(img, PM_RECT, scale, self.max_pm)
        timer = self._int(img, TIMER_RECT, scale, 300)
        fs.timer_s = float(timer) if timer is not None else None
        log = self.log_lines(img, scale) if (with_log and my_turn) else []
        return RealFightRead(fs, my_turn, hp, pa, pm, timer, log)


def casts_in_log(lines: list[str], caster: str | None = None) -> list[tuple[str, str]]:
    """[(who, spell)] for every 'X lance Spell.' line (oldest first)."""
    out = []
    for ln in lines:
        t = re.sub(r"^\[\d{1,2}:\d{2}\]\s*", "", ln.strip())
        m = LANCE_RE.search(t)
        if m and (caster is None or caster.casefold() in m.group("who").casefold()):
            out.append((m.group("who").strip(), m.group("spell").strip().rstrip(".")))
    return out
