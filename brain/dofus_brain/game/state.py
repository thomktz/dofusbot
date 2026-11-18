"""Dataclasses mirroring protocol §2.3 exactly. `to_json()` emits the protocol field names."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ..util import now_ms

Screen = Literal["unknown", "loading", "world", "fight", "dialog", "popup", "menu"]
SCREENS: tuple[str, ...] = ("unknown", "loading", "world", "fight", "dialog", "popup", "menu")
Mode = Literal["observe", "assist", "auto"]
MODES: tuple[str, ...] = ("observe", "assist", "auto")
AgentStateName = Literal["idle", "running", "paused", "stopped", "error"]
POI_KINDS = ("npc", "zaap", "zaapi", "bank", "workshop", "resource", "monster", "door", "other")


def _coords(v: Any) -> tuple[int, int] | None:
    if v is None:
        return None
    return (int(v[0]), int(v[1]))


@dataclass
class MapInfo:
    coords: tuple[int, int] | None = None
    area: str | None = None
    sub_area: str | None = None

    def to_json(self) -> dict:
        return {"coords": list(self.coords) if self.coords is not None else None,
                "area": self.area, "sub_area": self.sub_area}

    @classmethod
    def from_json(cls, d: dict) -> "MapInfo":
        return cls(coords=_coords(d.get("coords")), area=d.get("area"), sub_area=d.get("sub_area"))


@dataclass
class Character:
    name: str | None = None
    level: int | None = None
    hp: int | None = None
    hp_max: int | None = None
    pa: int | None = None
    pm: int | None = None
    pods: int | None = None
    pods_max: int | None = None
    kamas: int | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Character":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class Fighter:
    id: str
    name: str
    team: Literal["ally", "enemy"]
    is_self: bool = False
    cell: int | None = None
    hp: int | None = None
    hp_max: int | None = None
    pa: int | None = None
    pm: int | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Fighter":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class FightState:
    phase: Literal["placement", "my_turn", "enemy_turn", "ended"] = "placement"
    turn: int = 0
    timer_s: float | None = None
    fighters: list[Fighter] = field(default_factory=list)
    blocked_cells: list[int] = field(default_factory=list)
    reachable_cells: list[int] = field(default_factory=list)
    # the main button's label (perception.fight_button.FightButton), in-process only, not in JSON
    button: object | None = field(default=None, compare=False, repr=False)
    # last combat-log lines from the chat (real client, our turn only), in-process only
    log: list[str] = field(default_factory=list, compare=False, repr=False)

    def me(self) -> Fighter | None:
        return next((f for f in self.fighters if f.is_self), None)

    def enemies(self) -> list[Fighter]:
        return [f for f in self.fighters if f.team == "enemy" and (f.hp is None or f.hp > 0)]

    def allies(self) -> list[Fighter]:
        return [f for f in self.fighters if f.team == "ally"]

    def to_json(self) -> dict:
        return {"phase": self.phase, "turn": self.turn, "timer_s": self.timer_s,
                "fighters": [f.to_json() for f in self.fighters],
                "blocked_cells": list(self.blocked_cells), "reachable_cells": list(self.reachable_cells)}

    @classmethod
    def from_json(cls, d: dict) -> "FightState":
        return cls(phase=d.get("phase", "placement"), turn=d.get("turn", 0), timer_s=d.get("timer_s"),
                   fighters=[Fighter.from_json(f) for f in d.get("fighters", [])],
                   blocked_cells=list(d.get("blocked_cells", [])),
                   reachable_cells=list(d.get("reachable_cells", [])))


@dataclass
class DialogOption:
    index: int
    text: str

    def to_json(self) -> dict:
        return {"index": self.index, "text": self.text}


@dataclass
class Dialog:
    npc: str | None = None
    text: str = ""
    options: list[DialogOption] = field(default_factory=list)
    # not in protocol: window-point centres of each option, used by the executor
    option_points: dict[int, tuple[float, float]] = field(default_factory=dict, compare=False)

    def to_json(self) -> dict:
        return {"npc": self.npc, "text": self.text, "options": [o.to_json() for o in self.options]}

    @classmethod
    def from_json(cls, d: dict) -> "Dialog":
        return cls(npc=d.get("npc"), text=d.get("text", ""),
                   options=[DialogOption(int(o["index"]), o["text"]) for o in d.get("options", [])])


@dataclass
class Quest:
    name: str
    step: str | None = None
    objective: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Poi:
    kind: str
    name: str
    coords: tuple[int, int] | None = None
    cell: int | None = None

    def to_json(self) -> dict:
        return {"kind": self.kind, "name": self.name,
                "coords": list(self.coords) if self.coords is not None else None, "cell": self.cell}

    @classmethod
    def from_json(cls, d: dict) -> "Poi":
        return cls(kind=d["kind"], name=d["name"], coords=_coords(d.get("coords")), cell=d.get("cell"))


@dataclass
class GameState:
    screen: Screen = "unknown"
    confidence: float = 0.0
    map: MapInfo = field(default_factory=MapInfo)
    character: Character = field(default_factory=Character)
    fight: FightState | None = None
    dialog: Dialog | None = None
    quest: Quest | None = None
    pois: list[Poi] = field(default_factory=list)
    updated_at: int = field(default_factory=now_ms)
    # FERMER button of an after-fight window (perception.fight_end.CloseButton), in-process only
    close_button: object | None = field(default=None, compare=False, repr=False)

    def to_json(self) -> dict:
        return {
            "screen": self.screen,
            "confidence": round(float(self.confidence), 3),
            "map": self.map.to_json(),
            "character": self.character.to_json(),
            "fight": self.fight.to_json() if self.fight is not None else None,
            "dialog": self.dialog.to_json() if self.dialog is not None else None,
            "quest": self.quest.to_json() if self.quest is not None else None,
            "pois": [p.to_json() for p in self.pois],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, d: dict) -> "GameState":
        return cls(
            screen=d.get("screen", "unknown"), confidence=float(d.get("confidence", 0.0)),
            map=MapInfo.from_json(d.get("map") or {}),
            character=Character.from_json(d.get("character") or {}),
            fight=FightState.from_json(d["fight"]) if d.get("fight") else None,
            dialog=Dialog.from_json(d["dialog"]) if d.get("dialog") else None,
            quest=Quest(**d["quest"]) if d.get("quest") else None,
            pois=[Poi.from_json(p) for p in d.get("pois", [])],
            updated_at=int(d.get("updated_at", now_ms())),
        )

    def signature(self) -> tuple:
        """What counts as 'progress' for stuck detection (ignores timestamps)."""
        f = self.fight
        return (self.screen, self.map.coords, self.character.pa, self.character.pm,
                None if f is None else (f.phase, f.turn, tuple((x.id, x.cell, x.hp) for x in f.fighters)),
                None if self.dialog is None else (self.dialog.text, len(self.dialog.options)))

    def summary(self) -> str:
        """Compact French/English text of the state, given to the LLM."""
        c = self.character
        lines = [f"screen={self.screen} (conf {self.confidence:.2f})",
                 f"map={self.map.coords} area={self.map.area} sub_area={self.map.sub_area}",
                 f"character: level={c.level} hp={c.hp}/{c.hp_max} pa={c.pa} pm={c.pm} pods={c.pods}/{c.pods_max} kamas={c.kamas}"]
        if self.fight:
            lines.append(f"fight: phase={self.fight.phase} turn={self.fight.turn} fighters="
                         + ", ".join(f"{x.name}[{x.team}{'*' if x.is_self else ''}]@{x.cell} hp={x.hp}" for x in self.fight.fighters))
        if self.dialog:
            lines.append(f"dialog npc={self.dialog.npc}: {self.dialog.text}")
            for o in self.dialog.options:
                lines.append(f"  option {o.index}: {o.text}")
        if self.quest:
            lines.append(f"quest: {self.quest.name} / {self.quest.step} / {self.quest.objective}")
        if self.pois:
            lines.append("pois: " + "; ".join(f"{p.kind}:{p.name}@{p.coords} cell {p.cell}" for p in self.pois))
        return "\n".join(lines)
