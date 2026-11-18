"""Questing: parse quest tracker lines into objectives, turn an objective into goals for the
other skills, and choose dialog options (deterministic first, LLM only when that is not
enough, pause-and-ask when still unsure).

Locations come from a LOCAL JSON file only (data/locations.json, optional):
    {"npcs": {"Ganymede": [4, -18]}, "monsters": {"Bouftou": [[5, -18]]}, "resources": {"Ble": [[3, -4]]}}
plus whatever the Z-key scans learn while playing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..game.state import Dialog, GameState, Quest
from ..planner import PlanContext, PlannedStep, StepResult
from ..textnorm import norm, tokens  # noqa: F401  (re-exported)
from . import Outcome, StepOrOutcome, wait

Coords = tuple[int, int]


# ------------------------------------------------------------------ objectives
@dataclass
class Objective:
    kind: str                       # talk | kill | gather | bring | goto | unknown
    target: str | None = None
    count: int | None = None        # required
    progress: int | None = None     # done so far
    coords: Coords | None = None
    to: str | None = None           # bring X to <to>
    text: str = ""

    @property
    def complete(self) -> bool:
        return self.count is not None and self.progress is not None and self.progress >= self.count


PROGRESS_RE = re.compile(r"\(?\s*(\d{1,4})\s*/\s*(\d{1,4})\s*\)?\s*$")
COORDS_RE = re.compile(r"\[?\s*(-?\d{1,3})\s*[,;]\s*(-?\d{1,3})\s*\]?")
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("talk", re.compile(r"^(?:parler a|parler avec|aller voir|voir|talk to|speak to)\s+(.+)$", re.I)),
    ("goto", re.compile(r"^(?:se rendre|aller|rejoindre|go to|travel to)\s+(?:en|a|au|à|to)?\s*(.+)$", re.I)),
    ("kill", re.compile(r"^(?:vaincre|tuer|combattre|eliminer|defeat|kill)\s+(?:(\d+)\s+)?(.+)$", re.I)),
    ("bring", re.compile(r"^(?:ramener|apporter|rapporter|bring)\s+(?:(\d+)\s+)?(.+?)\s+(?:a|à|to)\s+(.+)$", re.I)),
    ("gather", re.compile(r"^(?:recolter|ramasser|cueillir|couper|gather|collect|harvest)\s+(?:(\d+)\s+)?(.+)$", re.I)),
]


def parse_objective(text: str) -> Objective:
    raw = " ".join(text.split())
    t = norm(raw).lstrip("-•* ")
    prog = cnt = None
    m = PROGRESS_RE.search(t)
    if m:
        prog, cnt = int(m.group(1)), int(m.group(2))
        t = t[:m.start()].strip()
    for kind, rx in PATTERNS:
        m = rx.match(t)
        if not m:
            continue
        if kind == "goto":
            c = COORDS_RE.search(m.group(1))
            if c:
                return Objective("goto", coords=(int(c.group(1)), int(c.group(2))), text=raw)
            return Objective("goto", target=m.group(1).strip(), text=raw)
        if kind == "talk":
            return Objective("talk", target=m.group(1).strip(), text=raw)
        if kind == "bring":
            n = int(m.group(1)) if m.group(1) else cnt
            return Objective("bring", target=m.group(2).strip(), count=n, progress=prog, to=m.group(3).strip(), text=raw)
        n = int(m.group(1)) if m.group(1) else cnt
        return Objective(kind, target=m.group(2).strip(), count=n or cnt, progress=prog, text=raw)
    return Objective("unknown", text=raw)


def parse_tracker(lines: list[str]) -> tuple[Quest | None, list[Objective]]:
    """Tracker text: first line = quest name, optional 'Etape: ...' line, then objectives
    (lines that parse to a known kind or carry an n/m progress)."""
    clean = [" ".join(l.split()) for l in lines if l and l.strip()]
    if not clean:
        return None, []
    name = clean[0]
    step = None
    objs: list[Objective] = []
    for ln in clean[1:]:
        n = norm(ln)
        if n.startswith(("etape", "step")):
            step = ln.split(":", 1)[-1].strip() if ":" in ln else ln
            continue
        o = parse_objective(ln)
        if o.kind != "unknown" or PROGRESS_RE.search(n):
            objs.append(o)
    current = next((o for o in objs if not o.complete), None)
    return Quest(name=name, step=step, objective=current.text if current else None), objs


# ------------------------------------------------------------------ locations + goals
class LocationBook:
    def __init__(self, data: dict | None = None):
        d = data or {}
        self.npcs: dict[str, Coords] = {norm(k): (int(v[0]), int(v[1])) for k, v in d.get("npcs", {}).items()}
        self.monsters: dict[str, list[Coords]] = {norm(k): [(int(c[0]), int(c[1])) for c in v]
                                                  for k, v in d.get("monsters", {}).items()}
        self.resources: dict[str, list[Coords]] = {norm(k): [(int(c[0]), int(c[1])) for c in v]
                                                   for k, v in d.get("resources", {}).items()}

    @classmethod
    def load(cls, path: Path) -> "LocationBook":
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _lookup(table: dict, name: str):
        n = norm(name)
        if n in table:
            return table[n]
        for k, v in table.items():  # partial: "Ganymede le sage" vs "Ganymede"
            if k in n or n in k:
                return v
        return None

    def npc(self, name: str) -> Coords | None:
        return self._lookup(self.npcs, name)

    def monster_maps(self, name: str) -> list[Coords]:
        return self._lookup(self.monsters, name) or []

    def resource_maps(self, name: str) -> list[Coords]:
        return self._lookup(self.resources, name) or []

    def learn_npc(self, name: str, coords: Coords) -> None:
        self.npcs[norm(name)] = (int(coords[0]), int(coords[1]))

    def learn_monster(self, name: str, coords: Coords) -> None:
        lst = self.monsters.setdefault(norm(name), [])
        if tuple(coords) not in lst:
            lst.append((int(coords[0]), int(coords[1])))


@dataclass
class Goal:
    kind: str                       # travel | talk | fight | harvest | ask
    params: dict = field(default_factory=dict)
    why: str = ""


def goals_for(obj: Objective, book: LocationBook, here: Coords | None) -> list[Goal]:
    """Objective -> ordered goals. Unknown locations -> an 'ask' goal (pause and ask)."""
    def nearest(maps: list[Coords]) -> Coords | None:
        if not maps:
            return None
        if here is None:
            return maps[0]
        return min(maps, key=lambda c: abs(c[0] - here[0]) + abs(c[1] - here[1]))

    if obj.complete:
        return []
    if obj.kind == "goto":
        if obj.coords is not None:
            return [Goal("travel", {"coords": obj.coords}, obj.text)]
        return [Goal("ask", {"question": f"Where is '{obj.target}'?"}, obj.text)]
    if obj.kind == "talk":
        c = book.npc(obj.target or "")
        if c is None:
            return [Goal("ask", {"question": f"Where is NPC '{obj.target}'?"}, obj.text)]
        return [Goal("travel", {"coords": c}, obj.text), Goal("talk", {"npc": obj.target}, obj.text)]
    if obj.kind == "kill":
        c = nearest(book.monster_maps(obj.target or ""))
        if c is None:
            return [Goal("ask", {"question": f"Where can I find '{obj.target}'?"}, obj.text)]
        left = (obj.count or 1) - (obj.progress or 0)
        return [Goal("travel", {"coords": c}, obj.text), Goal("fight", {"monster": obj.target, "count": left}, obj.text)]
    if obj.kind in ("gather", "bring"):
        c = nearest(book.resource_maps(obj.target or ""))
        goals = []
        if not (obj.kind == "bring" and obj.progress is not None and obj.count is not None and obj.progress >= obj.count):
            if c is None:
                return [Goal("ask", {"question": f"Where can I gather '{obj.target}'?"}, obj.text)]
            left = (obj.count or 1) - (obj.progress or 0)
            goals += [Goal("travel", {"coords": c}, obj.text), Goal("harvest", {"item": obj.target, "count": left}, obj.text)]
        if obj.kind == "bring" and obj.to:
            n = book.npc(obj.to)
            goals += ([Goal("travel", {"coords": n}, obj.text)] if n else []) + [Goal("talk", {"npc": obj.to}, obj.text)]
        return goals
    return [Goal("ask", {"question": f"I don't understand the objective '{obj.text}'"}, obj.text)]


# ------------------------------------------------------------------ dialogs
@dataclass
class Choice:
    index: int | None               # None = ask the human
    why: str
    source: str                     # rule | llm | ask


class DialogChooser:
    def __init__(self, cfg: dict):
        qc = cfg["quest"]
        self.forbidden = [norm(w) for w in qc.get("forbidden_option_words", [])]
        self.continue_words = [norm(w) for w in qc.get("continue_words", [])]
        self.min_score = float(qc.get("min_match_score", 0.34))
        self.use_llm = bool(qc.get("llm_dialog", True))

    def allowed(self, dialog: Dialog) -> list:
        return [o for o in dialog.options if not any(re.search(rf"\b{re.escape(w)}", norm(o.text)) for w in self.forbidden)]

    def choose(self, dialog: Dialog, quest: Quest | None, objective: Objective | None = None) -> Choice:
        opts = self.allowed(dialog)
        if not dialog.options:
            return Choice(None, "dialog has no options", "ask")
        if not opts:
            return Choice(None, "every option is forbidden (buy/sell/abandon...)", "ask")
        context = " ".join(x for x in (quest.name if quest else "", quest.objective if quest and quest.objective else "",
                                       objective.text if objective else "", objective.target or "" if objective else "") if x)
        ctx_tok = tokens(context)
        if ctx_tok:
            scored = []
            for o in opts:
                ot = tokens(o.text)
                s = len(ot & ctx_tok) / max(1, min(len(ot), len(ctx_tok)))
                scored.append((s, o))
            scored.sort(key=lambda t: -t[0])
            if scored[0][0] >= self.min_score and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                return Choice(scored[0][1].index, f"matches quest ({scored[0][0]:.2f}): {scored[0][1].text}", "rule")
        for o in opts:
            n = norm(o.text)
            if any(n.startswith(w) or n == w for w in self.continue_words):
                return Choice(o.index, f"continue: {o.text}", "rule")
        if len(opts) == 1:
            return Choice(opts[0].index, f"only allowed option: {opts[0].text}", "rule")
        return Choice(None, "several options and none clearly matches the quest", "ask")

    async def choose_with_llm(self, dialog: Dialog, quest: Quest | None, llm, objective: Objective | None = None) -> Choice:
        """Deterministic choice first; the LLM only when that would ask, and its answer must
        be an allowed option index with confidence >= 0.6, else we still ask."""
        c = self.choose(dialog, quest, objective)
        if c.index is not None or llm is None or not self.use_llm:
            return c
        opts = self.allowed(dialog)
        if not opts:
            return c
        prompt = ("Tu joues à Dofus. Choisis l'option de dialogue qui fait avancer la quête.\n"
                  f"Quête: {quest.name if quest else 'aucune'} / objectif: {quest.objective if quest else '-'}\n"
                  f"PNJ: {dialog.npc or '?'}\nTexte: {dialog.text}\nOptions:\n"
                  + "\n".join(f"{o.index}. {o.text}" for o in opts)
                  + "\nRéponds {\"index\": <numéro>, \"confidence\": <0..1>, \"why\": \"...\"}.")
        schema = {"type": "object", "properties": {"index": {"type": "integer"}, "confidence": {"type": "number"},
                                                   "why": {"type": "string"}}, "required": ["index", "confidence"]}
        try:
            resp = await llm.chat_json(prompt, schema=schema, max_tokens=200)
            data = resp.data or {}
            idx, conf = int(data.get("index", -1)), float(data.get("confidence", 0))
        except Exception as e:
            return Choice(None, f"LLM failed ({e}); asking", "ask")
        if idx in {o.index for o in opts} and conf >= 0.6:
            return Choice(idx, f"LLM ({conf:.2f}): {data.get('why', '')}".strip(), "llm")
        return Choice(None, f"LLM unsure (index {idx}, confidence {conf:.2f})", "ask")


class DialogSkill:
    """Answer the dialog on screen. `prepare()` (async) may consult the LLM once per dialog
    text; `step()` then clicks the chosen option and verifies the dialog moved on."""

    name = "dialog"

    def __init__(self, cfg: dict, objective: Objective | None = None):
        self.chooser = DialogChooser(cfg)
        self.dismiss_key = cfg["keys"]["dismiss"]
        self.objective = objective
        self.choice: Choice | None = None
        self._for: tuple | None = None
        self._llm_for: tuple | None = None

    def _sig(self, d: Dialog) -> tuple:
        return (d.npc, d.text, tuple((o.index, o.text) for o in d.options))

    async def prepare(self, state: GameState, llm=None) -> None:
        """Consult the LLM at most once per dialog text, and only if the rules would ask."""
        if state.screen != "dialog" or state.dialog is None:
            return
        sig = self._sig(state.dialog)
        if sig == self._for and (self.choice is None or self.choice.index is not None or sig == self._llm_for):
            return
        self._for = self._llm_for = sig
        self.choice = await self.chooser.choose_with_llm(state.dialog, state.quest, llm, self.objective)

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        if state.screen != "dialog" or state.dialog is None:
            return Outcome("done", "no dialog")
        d = state.dialog
        if self._sig(d) != self._for:
            self._for = self._sig(d)
            self.choice = self.chooser.choose(d, state.quest, self.objective)
        c = self.choice
        if c is None or c.index is None:
            return Outcome("ask", f"dialog with {d.npc or 'NPC'}: {c.why if c else 'no choice'}",
                           data={"options": [o.to_json() for o in d.options]})
        pt = d.option_points.get(c.index)
        if pt is None:
            return Outcome("ask", f"option {c.index} chosen but its position on screen is unknown")
        sig = self._sig(d)

        def moved_on(before: GameState, now: GameState) -> bool:
            return now.screen != "dialog" or now.dialog is None or self._sig(now.dialog) != sig

        return PlannedStep(action={"kind": "click", "x": round(pt[0], 1), "y": round(pt[1], 1),
                                   "reason": f"dialog option {c.index} ({c.source}): {c.why}"},
                           task=f"dialog: option {c.index}", verify=moved_on, timeout_s=3.0,
                           meta={"skill": self.name, "index": c.index, "source": c.source})

    def on_result(self, result: StepResult, state: GameState) -> None:
        if result.step.meta.get("skill") == self.name and not result.ok:
            self._for = None  # re-read and re-choose


class TalkSkill:
    """Talk to the NPC whose Z-key plate matches `npc`:
        1. click the NPC (slightly below its plate, on the sprite)
        2. verify its context menu opened (menu_reader finds known entries near the click;
           some NPCs open the dialog directly, which also counts)
        3. click the "Parler" / "Talk" entry (French and English)
        4. verify a dialog opened
    Plates come from `set_plates` (Z scan), frames from `feed_frame`. Without a menu_reader
    the skill can only handle NPCs that open the dialog directly."""

    name = "talk"

    def __init__(self, npc: str, below_pt: float = 30.0, menu_reader=None, dismiss_key: str = "escape"):
        self.npc = npc
        self.below = below_pt
        self.menu_reader = menu_reader      # (img, scale, near_xy) -> list[MenuOption]
        self.dismiss_key = dismiss_key
        self.plates: list = []
        self.tries = 0
        self.frame = None
        self.scale = 1.0
        self.clicked_at: tuple[float, float] | None = None
        self.menu: list = []
        self.phase = "npc"                  # npc -> menu -> (dialog)

    def set_plates(self, plates: list) -> None:
        self.plates = list(plates)

    def feed_frame(self, img, scale: float = 1.0) -> None:
        self.frame, self.scale = img, scale
        if self.phase == "menu_wait" and self.menu_reader is not None and self.clicked_at is not None:
            self.menu = self.menu_reader(img, scale, self.clicked_at)

    def _match(self):
        want = tokens(self.npc)
        return [p for p in self.plates if (want and want <= tokens(p.name)) or norm(self.npc) in norm(p.name)]

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        if state.screen == "dialog":
            return Outcome("done", f"talking to {self.npc}")
        if state.screen not in ("world", "menu", "popup"):
            return wait(f"screen {state.screen}")
        if self.tries >= 2:
            return Outcome("ask", f"could not open a dialog with '{self.npc}'")
        if self.phase == "menu":
            talk = next((o for o in self.menu if o.is_talk), None)
            if talk is None:
                entries = ", ".join(o.text for o in self.menu) or "nothing readable"
                return Outcome("ask", f"menu of '{self.npc}' has no talk entry ({entries})")
            return PlannedStep(action={"kind": "click", "x": round(talk.x, 1), "y": round(talk.y, 1),
                                       "reason": f"menu: {talk.text}"},
                               task=f"talk to {self.npc}: {talk.text}", verify=lambda b, n: n.screen == "dialog",
                               timeout_s=4.0, retries=0, meta={"skill": self.name, "op": "menu", "target": "npc"})
        if state.screen != "world":
            return wait(f"screen {state.screen}")
        match = self._match()
        if not match:
            return Outcome("ask", f"NPC '{self.npc}' not seen on this map (scan with the entity key first)")
        p = match[0]
        pt = (round(p.x, 1), round(p.y + self.below, 1))
        self.clicked_at = pt
        self.menu = []
        self.phase = "menu_wait"

        def opened(before: GameState, now: GameState) -> bool:
            return now.screen == "dialog" or bool(self.menu)

        return PlannedStep(action={"kind": "click", "x": pt[0], "y": pt[1], "reason": f"open menu of {p.name}"},
                           task=f"talk to {p.name}: open menu", verify=opened, timeout_s=3.0, retries=0,
                           meta={"skill": self.name, "op": "npc", "target": "npc"})

    def on_result(self, result: StepResult, state: GameState) -> None:
        m = result.step.meta
        if m.get("skill") != self.name:
            return
        if m.get("op") == "npc":
            if result.ok and state.screen != "dialog":
                self.phase = "menu"
            elif not result.ok:
                self.tries += 1
                self.phase = "npc"
        elif m.get("op") == "menu":
            if not result.ok:
                self.tries += 1
                self.phase = "npc"
