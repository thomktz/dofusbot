"""LLM-facing world tools and proposal boundary.

The local model may inspect canonical state and propose an intent. It cannot
press keys or mutate the game directly; a controller will validate/execute a
proposal in a later, explicitly enabled mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..llm.base import LLMClient
from ..llm.coordinator import SharedLLMCoordinator
from ..managed_transport import GameIntent
from ..game.grid import Grid
from .state import WorldState


@dataclass(frozen=True)
class WorldIntent:
    action: str
    args: dict[str, Any]
    reason: str = ""


class WorldToolRegistry:
    """Typed proposal surface over one protocol-derived world snapshot.

    This class has no socket/controller dependency.  It validates model output
    against the current snapshot and can turn a validated proposal into a
    ``GameIntent`` for the managed transport later.
    """

    def __init__(self, state: WorldState):
        self.state = state

    def observe(self) -> dict[str, Any]:
        return self.state.agent_context()

    def schema(self) -> list[dict[str, Any]]:
        return [
            {"name": "observe_world", "description": "Read the full actionable protocol-derived world state.",
                            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "wait_for_update", "description": "Wait for the next authoritative protocol update when no safe action is available.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "finish_goal", "description": "Finish the current goal only when its requested outcome is confirmed.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "talk_to_npc", "description": "Open the default dialogue with a visible NPC.",
             "parameters": {"type": "object", "properties": {"entity_id": {"type": "integer"}},
                            "required": ["entity_id"], "additionalProperties": False}},
            {"name": "attack_monster_group", "description": "Engage a visible monster group.",
             "parameters": {"type": "object", "properties": {"entity_id": {"type": "integer"}},
                            "required": ["entity_id"], "additionalProperties": False}},
            {"name": "choose_dialogue_reply", "description": "Choose one currently visible NPC reply.",
             "parameters": {"type": "object", "properties": {"reply_id": {"type": "integer"}},
                            "required": ["reply_id"], "additionalProperties": False}},
            {"name": "leave_dialogue", "description": "Close the current NPC dialogue.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "use_interactive", "description": "Use a visible enabled resource/interactive element.",
             "parameters": {"type": "object", "properties": {"element_id": {"type": "integer"}},
                            "required": ["element_id"], "additionalProperties": False}},
            {"name": "navigate_to_map", "description": "Use the game's native Go To route to a verified destination map ID; do not manually cross borders.",
             "parameters": {"type": "object", "properties": {"map_id": {"type": "integer"}},
                            "required": ["map_id"], "additionalProperties": False}},
            {"name": "inspect_active_quests", "description": "Read local plans, prerequisites and preparation gaps for active quests.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "get_active_quest_guide", "description": "Read compact human-guide metadata for one active quest.",
             "parameters": {"type": "object", "properties": {"quest_id": {"type": "integer"}},
                            "required": ["quest_id"], "additionalProperties": False}},
            {"name": "use_inventory_item", "description": "Use one current inventory object by its live object UID.",
             "parameters": {"type": "object", "properties": {"object_uid": {"type": "integer"}},
                            "required": ["object_uid"], "additionalProperties": False}},
            {"name": "equip_inventory_item", "description": "Move one current inventory object to a valid equipment position.",
             "parameters": {"type": "object", "properties": {"object_uid": {"type": "integer"}, "position": {"type": "integer"}},
                            "required": ["object_uid", "position"], "additionalProperties": False}},
            {"name": "cast_spell", "description": "Cast an available spell in the current player turn.",
             "parameters": {"type": "object", "properties": {"spell_id": {"type": "integer"}, "cell": {"type": "integer"}},
                            "required": ["spell_id", "cell"], "additionalProperties": False}},
            {"name": "end_turn", "description": "End the current player turn.",
             "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
        ]

    def validate(self, intent: WorldIntent) -> WorldIntent:
        if intent.action in {"observe_world", "wait_for_update", "finish_goal"}:
            return intent
        if intent.action == "talk_to_npc":
            actor = self.state.actors.get(int(intent.args.get("entity_id", 0)))
            npc_id = (actor.metadata if actor else {}).get("npc_id")
            if actor is None or actor.kind != "npc" or not actor.alive or npc_id is None or self.state.map_id is None:
                raise ValueError("NPC is not a current protocol-identified dialogue target")
        elif intent.action == "attack_monster_group":
            actor = self.state.actors.get(int(intent.args.get("entity_id", 0)))
            if actor is None or actor.kind != "monster_group" or not actor.alive or self.state.fight_phase != "out_of_fight":
                raise ValueError("target is not a live monster group on the current map")
        elif intent.action == "choose_dialogue_reply":
            current = (self.state._agent_dialog() or {}).get("current", {})
            replies = {option.get("reply_id") for option in current.get("options", ())}
            if int(intent.args.get("reply_id", -1)) not in replies:
                raise ValueError("reply is not available in the current dialogue stage")
        elif intent.action == "leave_dialogue":
            if self.state.dialog is None:
                raise ValueError("there is no current dialogue")
        elif intent.action == "use_interactive":
            item = self.state.interactives.get(int(intent.args.get("element_id", 0)))
            instances = item.details.get("skill_instances", ()) if item else ()
            if item is None or item.available is False or not any(x.get("skill_instance_uid") is not None for x in instances):
                raise ValueError("interactive has no enabled protocol skill instance")
        elif intent.action == "navigate_to_map":
            target = int(intent.args.get("map_id", 0))
            if self.state.map_id is None or target <= 0:
                raise ValueError("current and target map IDs must be protocol-confirmed")
            if target == self.state.map_id:
                raise ValueError("already on requested map")
        elif intent.action == "inspect_active_quests":
            pass
        elif intent.action == "get_active_quest_guide":
            quest_id = int(intent.args.get("quest_id", -1))
            active = {int(quest.get("quest_id")) for quest in self.state.quests.get("active", ())
                      if quest.get("quest_id") is not None}
            if quest_id not in active:
                raise ValueError("quest is not active in the current protocol state")
        elif intent.action == "use_inventory_item":
            uid = int(intent.args.get("object_uid", -1))
            if not any(item.get("uid") == uid for item in self.state.inventory.get("objects", ())):
                raise ValueError("object UID is not present in the current inventory")
        elif intent.action == "equip_inventory_item":
            uid, position = int(intent.args.get("object_uid", -1)), int(intent.args.get("position", -1))
            if not any(item.get("uid") == uid for item in self.state.inventory.get("objects", ())):
                raise ValueError("object UID is not present in the current inventory")
            if position not in set(range(0, 17)) | set(range(20, 41)):
                raise ValueError("inventory position is not a valid equipment position")
        elif intent.action == "cast_spell":
            spell_id, cell = int(intent.args.get("spell_id", -1)), int(intent.args.get("cell", -1))
            spell = next((spell for spell in self.state.spells if spell.get("spell_id") == spell_id), None)
            if self.state.fight_phase != "turn" or spell is None or spell.get("available") is False or cell not in self.state.cells:
                raise ValueError("spell/cell is not valid for the current fight turn")
            caster = self.state.character.get("cell")
            if caster is None and self.state.character.get("entity_id") is not None:
                actor = self.state.actors.get(int(self.state.character["entity_id"]))
                caster = actor.cell if actor else None
            lo, hi = spell.get("min_range"), spell.get("range")
            if caster is not None and (lo is not None or hi is not None):
                grid = Grid()
                distance = grid.distance(int(caster), cell)
                if (lo is not None and distance < int(lo)) or (hi is not None and distance > int(hi)):
                    raise ValueError(f"target cell is outside known spell range ({distance}, expected {lo or 0}–{hi or '∞'})")
                if spell.get("cast_in_line") and not grid.in_line(int(caster), cell):
                    raise ValueError("spell must be cast in a straight line")
                if spell.get("requires_los"):
                    blockers = {cell_id for cell_id, state_cell in self.state.cells.items() if state_cell.obstacle}
                    if not grid.has_los(int(caster), cell, blockers):
                        raise ValueError("spell target has no known line of sight")
        elif intent.action == "end_turn":
            if self.state.fight_phase != "turn":
                raise ValueError("cannot end turn outside a current fight turn")
        else:
            raise ValueError(f"unsupported world action: {intent.action}")
        return intent

    def game_intent(self, intent: WorldIntent) -> GameIntent | None:
        """Translate a validated proposal to the transport's narrow wire API."""
        intent = self.validate(intent)
        if intent.action in {"observe_world", "wait_for_update", "finish_goal"}:
            return None
        if intent.action == "talk_to_npc":
            actor = self.state.actors[int(intent.args["entity_id"])]
            return GameIntent("npc_action", {"npc_id": int(actor.metadata["npc_id"]), "npc_action_id": 3,
                                               "npc_map_id": int(self.state.map_id)})
        if intent.action == "attack_monster_group":
            return GameIntent("attack_monster", {"monster_group_id": int(intent.args["entity_id"])})
        if intent.action == "choose_dialogue_reply":
            return GameIntent("npc_dialog_reply", {"reply_id": int(intent.args["reply_id"])})
        if intent.action == "leave_dialogue":
            return GameIntent("dialog_leave", {})
        if intent.action == "use_interactive":
            item = self.state.interactives[int(intent.args["element_id"])]
            skill = next(x for x in item.details["skill_instances"] if x.get("skill_instance_uid") is not None)
            return GameIntent("interactive_use", {"element_id": item.id, "skill_instance_uid": int(skill["skill_instance_uid"])})
        if intent.action == "navigate_to_map":
            # The Hub expands this high-level request against its installed
            # world graph; it has no single wire request of its own.
            return None
        if intent.action in {"inspect_active_quests", "get_active_quest_guide"}:
            return None
        if intent.action == "use_inventory_item":
            return GameIntent("object_use", {"object_uid": int(intent.args["object_uid"])})
        if intent.action == "equip_inventory_item":
            return GameIntent("object_set_position", {"object_uid": int(intent.args["object_uid"]),
                                                        "position": int(intent.args["position"]), "quantity": 1})
        if intent.action == "cast_spell":
            return GameIntent("fight_cast", {"spell_id": int(intent.args["spell_id"]), "cell": int(intent.args["cell"])})
        if intent.action == "end_turn":
            return GameIntent("fight_turn_finish", {})
        raise AssertionError(f"validated but untranslatable intent: {intent.action}")


class LocalWorldOrchestrator:
    def __init__(self, llm: LLMClient | SharedLLMCoordinator, account_id: int = 0):
        """Create an account-scoped planner.

        Supplying ``SharedLLMCoordinator`` keeps a single local model loaded
        while retaining account-separated prompts.  A raw client remains
        supported for the single-account/dev path.
        """
        self.llm = llm
        self.account_id = int(account_id)

    async def propose(self, state: WorldState, goal: str, knowledge: dict[str, Any] | None = None) -> tuple[WorldIntent, dict[str, Any], str]:
        tools = WorldToolRegistry(state)
        prompt = (
            "You are the Dofus local world planner. Inspect the canonical state and propose exactly "
            "one safe intent. Do not invent cells or entities. The state below is the complete "
            "actionable protocol view: use it directly. Return JSON with action, args, reason and a "
            "short user-facing narration of what you observed, decided, or just accomplished.\n"
            f"Goal: {goal}\nState: {tools.observe()}\n"
            f"Quest, success and guide knowledge: {knowledge or {}}\nTools: {tools.schema()}"
        )
        schema = {
            "type": "object", "properties": {"action": {"type": "string"}, "args": {"type": "object"},
                                                 "reason": {"type": "string"}, "narration": {"type": "string"}},
            "required": ["action", "args"], "additionalProperties": False}
        if isinstance(self.llm, SharedLLMCoordinator):
            response = await self.llm.chat_json(self.account_id, prompt, schema=schema)
        else:
            response = await self.llm.chat_json(prompt, schema=schema)
        data = response.data
        intent = tools.validate(WorldIntent(str(data["action"]), dict(data.get("args", {})), str(data.get("reason", ""))))
        return intent, response.timings.as_dict(), str(data.get("narration") or data.get("reason") or "")
