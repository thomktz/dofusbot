"""Enemy threat map and the per-monster knowledge cache.

threat[cell] = expected damage we would take next enemy turn if we end our turn on `cell`:
for each enemy, if it can walk (its MP, around obstacles and fighters) to a cell from which
`cell` is within its attack range (with line of sight when its attack needs it), add its
expected damage. Unknown monsters get a conservative melee default.

brain/data/monsters.json (learned in game; unknown values are null):
    {"monsters": {"Bouftou Nuageux": {"resistances": {"neutral": null, "earth": null, ...},
                                      "ap": null, "mp": null, "attack": {"min_range": 1,
                                      "max_range": 1, "los": true, "damage": null}, "seen": 1}}}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..game.grid import Grid
from ..game.state import Fighter
from ..textnorm import norm
from .spellmodel import ELEMENTS

DEFAULT_ATTACK = {"min_range": 1, "max_range": 1, "los": True, "damage": 12.0}
DEFAULT_MP = 3


def _empty_entry() -> dict:
    return {"resistances": {e: None for e in ELEMENTS}, "ap": None, "mp": None,
            "attack": {"min_range": None, "max_range": None, "los": None, "damage": None}, "seen": 0}


class MonsterBook:
    def __init__(self, data: dict | None = None, path: Path | None = None):
        self.path = path
        self.monsters: dict[str, dict] = dict((data or {}).get("monsters", {}))

    @classmethod
    def load(cls, path: Path) -> "MonsterBook":
        if not path.exists():
            return cls(path=path)
        return cls(json.loads(path.read_text(encoding="utf-8")), path)

    def save(self, path: Path | None = None) -> None:
        p = path or self.path
        if p is None:
            raise ValueError("no path to save the monster book to")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"_comment": "Learned in game. null = not read yet.", "monsters": self.monsters},
                                indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    def _key(self, name: str) -> str | None:
        n = norm(name)
        for k in self.monsters:
            if norm(k) == n:
                return k
        # fighters may carry a level suffix "Bouftou Nuageux (6)"
        base = norm(name.split("(")[0])
        for k in self.monsters:
            if norm(k) == base:
                return k
        return None

    def get(self, name: str) -> dict | None:
        k = self._key(name)
        return self.monsters.get(k) if k else None

    def ensure(self, name: str) -> dict:
        k = self._key(name)
        if k is None:
            k = name.split("(")[0].strip()
            self.monsters[k] = _empty_entry()
        entry = self.monsters[k]
        for key, val in _empty_entry().items():   # fill keys missing from hand-written entries
            entry.setdefault(key, val)
        return entry

    def saw(self, name: str) -> None:
        self.ensure(name)["seen"] = int(self.ensure(name).get("seen", 0)) + 1

    def set_resistances(self, name: str, res: dict[str, float]) -> None:
        r = self.ensure(name)["resistances"]
        for e, v in res.items():
            if e in ELEMENTS:
                r[e] = float(v)

    def resist(self, f: Fighter) -> dict[str, float | None]:
        out: dict[str, float | None] = {e: None for e in ELEMENTS}
        m = self.get(f.name)
        if m:
            out.update(m.get("resistances") or {})
        return out

    def attack(self, name: str) -> dict:
        m = self.get(name) or {}
        a = dict(DEFAULT_ATTACK)
        for k, v in (m.get("attack") or {}).items():
            if v is not None:
                a[k] = v
        return a

    def mp(self, f: Fighter) -> int:
        if f.pm is not None:
            return int(f.pm)
        m = self.get(f.name) or {}
        return int(m.get("mp") or DEFAULT_MP)


@dataclass
class ThreatMap:
    """danger[cell] = total; by_enemy[id][cell] lets the solver drop enemies it kills."""
    danger: dict[int, float]
    by_enemy: dict[str, dict[int, float]]

    def __getitem__(self, cell: int) -> float:
        return self.danger.get(cell, 0.0)

    def without(self, cell: int, dead: set[str]) -> float:
        return sum(m.get(cell, 0.0) for eid, m in self.by_enemy.items() if eid not in dead)

    @classmethod
    def build(cls, grid: Grid, enemies: list[Fighter], blocked: set[int], occupied: set[int],
              book: MonsterBook | None = None, cells: list[int] | None = None) -> "ThreatMap":
        book = book or MonsterBook()
        targets = cells if cells is not None else list(range(grid.n_cells))
        blockers = set(blocked) | occupied
        by_enemy: dict[str, dict[int, float]] = {}
        for e in enemies:
            if e.cell is None or (e.hp is not None and e.hp <= 0):
                continue
            atk = book.attack(e.name)
            lo, hi = int(atk["min_range"]), int(atk["max_range"])
            dmg = float(atk["damage"])
            reach = grid.reachable(e.cell, book.mp(e), blocked, occupied - {e.cell})
            m: dict[int, float] = {}
            for c in targets:
                for r in reach:
                    if lo <= grid.distance(r, c) <= hi and (
                            not atk.get("los", True) or grid.has_los(r, c, blockers - {r, c, e.cell})):
                        m[c] = dmg
                        break
            by_enemy[e.id] = m
        danger: dict[int, float] = {}
        for m in by_enemy.values():
            for c, v in m.items():
                danger[c] = danger.get(c, 0.0) + v
        return cls(danger, by_enemy)
