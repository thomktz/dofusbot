"""A macOS-safe encoder based on AstraBot's shipped protocol data.

The upstream AstraBot runtime imports its Windows launcher at package import
time, so importing its convenient codec helper is not an option here.  This
module keeps the useful part: its generated protobuf descriptors and current
clear-to-obfuscated mapping.  It turns a typed, clear request into the exact
obfuscated ``GameMessage`` envelope used by the game socket.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from google.protobuf import any_pb2
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.descriptor_pool import Default
from google.protobuf.message_factory import GetMessageClass

from .managed_transport import GameIntent


class AstraRuntimeUnavailable(RuntimeError):
    pass


class AstraCodec:
    """Encode supported intents through AstraBot's current proto mapping."""

    def __init__(self, root: str | Path | None = None):
        configured = root or os.environ.get("ASTRABOT_HOME") or "/private/tmp/AstraBot"
        self.root = Path(configured).expanduser()
        self._ready = False
        self._mappings: dict[str, dict[str, Any]] = {}

    def _load(self) -> None:
        if self._ready:
            return
        proto_root = self.root / "DBDofusUnity" / "datas" / "protos"
        clear_root, obf_root = proto_root / "non_obf" / "game", proto_root / "obf" / "game"
        mapping_path = proto_root / "game_mappings.json"
        if not clear_root.is_dir() or not obf_root.is_dir() or not mapping_path.is_file():
            raise AstraRuntimeUnavailable(f"Astra protocol data not found under {self.root}")
        for entry in (clear_root, obf_root):
            text = str(entry)
            if text not in sys.path:
                sys.path.insert(0, text)
        try:
            self._npc = importlib.import_module("npc_pb2")
            self._dialog = importlib.import_module("dialog_pb2")
            self._gamemap = importlib.import_module("gamemap_pb2")
            self._interactive = importlib.import_module("interactive_element_pb2")
            self._inventory = importlib.import_module("inventory_pb2")
            self._roleplay = importlib.import_module("roleplay_pb2")
            self._game_action = importlib.import_module("game_action_pb2")
            self._fight = importlib.import_module("fight_pb2")
            self._party = importlib.import_module("party_pb2")
            importlib.import_module("game_message_pb2")
            importlib.import_module("game_messages_pb2")
            self._mappings = json.loads(mapping_path.read_text())
        except Exception as exc:  # pragma: no cover - depends on external generated runtime
            raise AstraRuntimeUnavailable(f"Astra protocol runtime is unavailable: {exc}") from exc
        self._ready = True

    @staticmethod
    def _clear_name(message: Any) -> str:
        return f".{message.DESCRIPTOR.full_name}"

    def _obfuscated(self, clear: Any) -> Any:
        """Recursively translate one clear protobuf message with Astra mappings."""
        mapping = self._mappings.get(self._clear_name(clear))
        if not mapping:
            raise AstraRuntimeUnavailable(f"Astra mapping missing for {clear.DESCRIPTOR.full_name}")
        obf = GetMessageClass(Default().FindMessageTypeByName(mapping["obf_msg_namespace"]))()
        # Astra's JSON maps obfuscated field name -> clear field name.
        clear_to_obf = {clear_name: obf_name for obf_name, clear_name in mapping["field_mapping"].items()}
        for field, value in clear.ListFields():
            obf_name = clear_to_obf.get(field.name)
            if not obf_name:
                raise AstraRuntimeUnavailable(f"Astra field mapping missing for {clear.DESCRIPTOR.full_name}.{field.name}")
            target = getattr(obf, obf_name)
            if field.label == FieldDescriptor.LABEL_REPEATED:
                if field.type == FieldDescriptor.TYPE_MESSAGE:
                    for item in value:
                        target.add().CopyFrom(self._obfuscated(item))
                else:
                    target.extend(value)
            elif field.type == FieldDescriptor.TYPE_MESSAGE:
                target.CopyFrom(self._obfuscated(value))
            else:
                setattr(obf, obf_name, value)
        return obf

    def _message(self, intent: GameIntent) -> Any:
        p = intent.payload
        if intent.kind == "npc_dialog_reply":
            return self._npc.NpcDialogReplyRequest(reply_id=int(p["reply_id"]))
        if intent.kind == "dialog_leave":
            return self._dialog.DialogLeaveRequest()
        if intent.kind == "party_invitation_refuse":
            return self._party.PartyInvitationRefuseRequest(party_id=int(p["party_id"]))
        if intent.kind == "friendly_fight_answer":
            return self._roleplay.PlayerFightFriendlyAnswerRequest(fight_id=int(p["fight_id"]), accept=bool(p["accept"]))
        if intent.kind == "npc_action":
            return self._npc.NpcGenericActionRequest(npc_id=int(p["npc_id"]), npc_action_id=int(p["npc_action_id"]), npc_map_id=int(p["npc_map_id"]))
        if intent.kind == "attack_monster":
            return self._roleplay.AttackMonsterRequest(monster_group_id=int(p["monster_group_id"]))
        if intent.kind == "object_use":
            return self._inventory.ObjectUseRequest(object_uid=int(p["object_uid"]))
        if intent.kind == "object_set_position":
            return self._inventory.ObjectSetPositionRequest(object_uid=int(p["object_uid"]), position=int(p["position"]), quantity=int(p["quantity"]))
        if intent.kind == "map_movement":
            return self._gamemap.MapMovementRequest(map_id=int(p["map_id"]), key_cells=list(p["key_cells"]))
        if intent.kind == "map_change":
            return self._gamemap.MapChangeRequest(map_id=int(p["map_id"]), auto_pilot=bool(p.get("auto_pilot", False)))
        if intent.kind == "interactive_use":
            return self._interactive.InteractiveUseRequest(element_id=int(p["element_id"]), skill_instance_uid=int(p["skill_instance_uid"]))
        if intent.kind == "fight_cast":
            return self._game_action.GameActionFightCastRequest(spell_id=int(p["spell_id"]), cell=int(p["cell"]))
        if intent.kind == "fight_turn_finish":
            return self._fight.FightTurnFinishRequest()
        raise ValueError(f"unsupported managed game intent: {intent.kind}")

    def request_uid(self, intent: GameIntent, next_uid: int) -> int:
        """Return Astra's envelope UID for an injected request.

        AstraBot sends a real incrementing UID only for connection/control
        requests (client-id, challenge, characteristic upgrade, latency).
        Every gameplay request exposed by this codec is deliberately sent with
        ``-1``.  This is observable in AstraBot's ``GameProxy`` and avoids
        advancing the live client's UID sequence for an action the game does
        not correlate with a response.
        """
        del intent, next_uid
        return -1

    def encode(self, intent: GameIntent, uid: int) -> bytes:
        self._load()
        payload = self._obfuscated(self._message(intent))
        packed = any_pb2.Any()
        packed.Pack(payload, type_url_prefix="type.ankama.com")
        request_mapping = self._mappings[".com.ankama.dofus.server.game.protocol.Request"]
        request = GetMessageClass(Default().FindMessageTypeByName(request_mapping["obf_msg_namespace"]))()
        setattr(request, next(name for name, clear in request_mapping["field_mapping"].items() if clear == "uid"), uid)
        getattr(request, next(name for name, clear in request_mapping["field_mapping"].items() if clear == "content")).CopyFrom(packed)
        envelope_mapping = self._mappings[".com.ankama.dofus.server.game.protocol.GameMessage"]
        envelope = GetMessageClass(Default().FindMessageTypeByName(envelope_mapping["obf_msg_namespace"]))()
        getattr(envelope, next(name for name, clear in envelope_mapping["field_mapping"].items() if clear == "request")).CopyFrom(request)
        return envelope.SerializeToString()

    def client_uid(self, payload: bytes) -> int | None:
        """Read the latest real client request UID from an obfuscated frame."""
        self._load()
        envelope_mapping = self._mappings[".com.ankama.dofus.server.game.protocol.GameMessage"]
        envelope = GetMessageClass(Default().FindMessageTypeByName(envelope_mapping["obf_msg_namespace"]))()
        envelope.ParseFromString(payload)
        request_field = next(name for name, clear in envelope_mapping["field_mapping"].items() if clear == "request")
        if not envelope.HasField(request_field):
            return None
        request_mapping = self._mappings[".com.ankama.dofus.server.game.protocol.Request"]
        request = getattr(envelope, request_field)
        uid_field = next(name for name, clear in request_mapping["field_mapping"].items() if clear == "uid")
        uid = int(getattr(request, uid_field))
        return uid if uid > 0 else None
