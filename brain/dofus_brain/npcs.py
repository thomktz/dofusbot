"""list_npcs(): hold the entity key (Z), capture, OCR the name plates, return NPCs and
monsters with their positions.

Only plates that APPEAR while the key is held are kept (diff against a plain capture taken
just before), so chat lines, the HUD and map text are ignored. The only input sent is one
hold and release of the entity key (Controller.capture_holding).

    python -m dofus_brain.npcs [--out docs/list_npcs_output.txt] [--frame docs/list_npcs_frame.png]
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass

import numpy as np

from .game.grid import Grid
from .ocr.backends import OcrBackend, OcrLine
from .perception.entities import parse_plate
from .perception.regions import Regions


@dataclass
class Entity:
    name: str
    kind: str                   # npc | monster
    level: int | None
    group_size: int | None
    x: float                    # window points, centre of the plate
    y: float
    cell: int | None            # only when the grid is calibrated
    conf: float
    raw: str
    xp: int | None = None       # monster groups: XP shown on the plate
    members: list | None = None  # monster groups: [{"name", "level"}]


def _changed(plain: np.ndarray, held: np.ndarray, box: tuple[int, int, int, int], threshold: int = 40,
             frac: float = 0.08) -> bool:
    x0, y0, x1, y1 = (int(v) for v in box)
    a, b = plain[y0:y1, x0:x1], held[y0:y1, x0:x1]
    if a.size == 0:
        return False
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > threshold
    return float(d.mean()) > frac


LEVEL_LINE_RE = re.compile(r"^(?:niveau|niv\.?|level|lvl)\s*(\d{1,4})$", re.I)
XP_LINE_RE = re.compile(r"^(\d[\d\s]*)\s*[XХKК]\s*[PР]$", re.I)   # OCR mixes Latin/Cyrillic X/P
MEMBER_RE = re.compile(r"^(.+?)\s*\((\d{1,3})\)$")


@dataclass
class _Line:
    text: str
    x: float
    y: float
    h: float
    conf: float


def _cluster(lines: list[_Line], dx: float = 70.0, gap: float = 1.2) -> list[list[_Line]]:
    """Group lines of one plate box: horizontally centred together and vertically adjacent
    (next line starts within `gap` line heights)."""
    groups: list[list[_Line]] = []
    for ln in sorted(lines, key=lambda l: (l.y, l.x)):
        for g in groups:
            last = g[-1]
            if abs(ln.x - last.x) <= dx and 0 <= ln.y - last.y <= gap * max(last.h, ln.h) + last.h:
                g.append(ln)
                break
        else:
            groups.append([ln])
    return groups


def entities_from_ocr(lines: list[OcrLine], scale: float, offset_px: tuple[int, int] = (0, 0),
                      plain: np.ndarray | None = None, held: np.ndarray | None = None,
                      grid: Grid | None = None, below_pt: float = 30.0) -> list[Entity]:
    """OCR lines of the held frame (boxes relative to a crop at offset_px) -> entities.
    With both frames given, lines whose box did not change are dropped (not a plate).

    Dofus plates: an NPC is one line (its name). A monster group is a box of lines:
    "Niveau <total>", "<n> XP", then one "<Name> (<level>)" line per member."""
    kept: list[_Line] = []
    for ln in lines:
        x0, y0, x1, y1 = ln.box
        abs_box = (x0 + offset_px[0], y0 + offset_px[1], x1 + offset_px[0], y1 + offset_px[1])
        if plain is not None and held is not None and not _changed(plain, held, abs_box):
            continue
        t = " ".join(ln.text.split())
        if not t:
            continue
        kept.append(_Line(t, (abs_box[0] + abs_box[2]) / 2 / scale, (abs_box[1] + abs_box[3]) / 2 / scale,
                          (abs_box[3] - abs_box[1]) / scale, float(ln.conf)))
    out = []
    for g in _cluster(kept):
        level = xp = None
        members: list[tuple[str, int]] = []
        names: list[_Line] = []
        for ln in g:
            if (m := LEVEL_LINE_RE.match(ln.text)):
                level = int(m.group(1))
            elif (m := XP_LINE_RE.match(ln.text)):
                xp = int(m.group(1).replace(" ", ""))
            elif (m := MEMBER_RE.match(ln.text)):
                members.append((m.group(1).strip(), int(m.group(2))))
            else:
                names.append(ln)
        x = round(sum(l.x for l in g) / len(g), 1)
        top = g[0]
        cell = grid.cell_at(x, g[-1].y + below_pt) if grid is not None else None
        conf = round(min(l.conf for l in g), 2)
        if level is not None or xp is not None or members:
            label = ", ".join(f"{n} ({lv})" for n, lv in members) or "monster group"
            out.append(Entity(label, "monster", level, len(members) or None, x, round(top.y, 1), cell, conf,
                              " | ".join(l.text for l in g), xp, [{"name": n, "level": lv} for n, lv in members]))
            continue
        for ln in names:  # NPC name plates (one line each); parse_plate drops noise
            p = parse_plate(ln.text)
            if p is None:
                continue
            monster = p.level is not None or p.group_size is not None
            c = grid.cell_at(ln.x, ln.y + below_pt) if grid is not None else None
            out.append(Entity(p.name, "monster" if monster else "npc", p.level, p.group_size, round(ln.x, 1),
                              round(ln.y, 1), c, round(ln.conf, 2), ln.text))
    return sorted(out, key=lambda e: (e.kind != "npc", e.y, e.x))


def list_npcs(ctl, ocr: OcrBackend, regions: Regions, grid: Grid | None = None, key: str = "z",
              settle_ms: float = 300.0):
    """Returns (entities, held_frame). `grid` only if calibrated (else cells are None)."""
    plain = ctl.capture()                        # read only
    held = ctl.capture_holding(key, settle_ms)    # the one input: hold key, capture, release
    a, b = plain.image, held.image
    if a.shape != b.shape:
        a = None  # window changed between the two captures: keep every parsed line
    crop = regions.crop(b, "game_area")
    off = regions.offset(b, "game_area")
    lines = ocr.recognize(crop)
    return entities_from_ocr(lines, held.scale, off, a, b, grid), held


def format_entities(ents: list[Entity]) -> str:
    if not ents:
        return "no name plates found"
    rows = [f"{'kind':8} {'name':60} {'lvl':>4} {'xp':>5} {'x':>7} {'y':>7} {'cell':>5} {'conf':>5}"]
    for e in ents:
        rows.append(f"{e.kind:8} {e.name[:60]:60} {e.level if e.level is not None else '-':>4} "
                    f"{e.xp if e.xp is not None else '-':>5} {e.x:7.1f} {e.y:7.1f} "
                    f"{e.cell if e.cell is not None else '-':>5} {e.conf:5.2f}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    from dofus_control.controller import Controller

    from .config import load_json
    from .ocr import make_ocr
    from dofus_control.png import write_png

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default="Dofus")
    ap.add_argument("--key", default=None, help="entity key (default: phases.json keys.show_entities)")
    ap.add_argument("--out")
    ap.add_argument("--frame")
    a = ap.parse_args(argv)
    from .skills.config import load_phases_config

    key = a.key or load_phases_config()["keys"]["show_entities"]
    regions = Regions(load_json("regions"))
    gcfg = load_json("grid")
    grid = Grid.from_config(gcfg) if gcfg.get("calibrated") else None
    ocr = make_ocr(load_json("regions").get("ocr", {}))
    ctl = Controller(app_name=a.app)   # require_frontmost: refuses if Dofus is not in front
    try:
        ents, frame = list_npcs(ctl, ocr, regions, grid, key)
    finally:
        ctl.close()
    text = format_entities(ents)
    print(text)
    print(json.dumps([asdict(e) for e in ents], ensure_ascii=False, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n\n" + json.dumps([asdict(e) for e in ents], ensure_ascii=False, indent=1) + "\n")
    if a.frame:
        write_png(a.frame, frame.image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
