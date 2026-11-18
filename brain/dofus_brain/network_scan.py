"""Small, dependency-free reader for a bounded Dofus TCP capture.

This is deliberately read-only: it parses an already-created pcap and never
replays packets or sends anything to the game.
"""
from __future__ import annotations

import re
import struct
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NetworkMember:
    monster_id: int
    raw_field_4: int | None = None
    name: str | None = None
    levels: list[int] = field(default_factory=list)
    level_override: int | None = None
    xp_levels: list[int] = field(default_factory=list)
    cell: int | None = None

    @property
    def grade(self) -> int | None:
        return self.raw_field_4

    @property
    def level(self) -> int | None:
        if self.level_override is not None:
            return self.level_override
        if self.grade is None or not self.levels or self.grade < 1:
            return None
        return self.levels[min(self.grade - 1, len(self.levels) - 1)]

    @property
    def xp(self) -> int | None:
        if self.grade is None or not self.xp_levels or self.grade < 1:
            return None
        return self.xp_levels[min(self.grade - 1, len(self.xp_levels) - 1)]


@dataclass
class NetworkGroup:
    entity_id: int
    cell: int | None
    members: list[NetworkMember] = field(default_factory=list)


@dataclass
class NetworkPlayer:
    entity_id: int
    name: str | None
    cell: int | None


@dataclass
class NetworkCharacter:
    """Character values decoded from CharacterCharacteristicsEvent.

    The client build uses an obfuscated Any alias (``krg``).  The surrounding
    record is stable: XP bounds and life are scalar fields, while AP/MP are
    entries in the characteristic list.  Keep unknown values absent rather
    than guessing from screenshots.
    """
    experience: int | None = None
    experience_next: int | None = None
    hp: int | None = None
    hp_max: int | None = None
    pa: int | None = None
    pm: int | None = None
    level: int | None = None
    name: str | None = None
    entity_id: int | None = None
    cell: int | None = None


@dataclass
class NetworkProtocolEvent:
    """One server Any message, retained even when its alias is not decoded."""
    offset: int
    alias: str
    size: int
    scalar_fields: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class NetworkInventory:
    """Inventory reducer based on AstraBot's InventoryContent/Object events."""
    kamas: int | None = None
    weight: int | None = None
    weight_max: int | None = None
    objects: list[dict[str, int | None]] = field(default_factory=list)


@dataclass
class NetworkJob:
    job_id: int | None = None
    level: int | None = None
    xp: int | None = None
    xp_floor: int | None = None
    xp_next: int | None = None


@dataclass
class NetworkDialog:
    actor_id: int | None = None
    message_id: int | None = None
    params: list[str] = field(default_factory=list)
    replies: list[int] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)


def _quest_objective(blob: bytes) -> dict[str, Any] | None:
    """Decode an obfuscated QuestObjective conservatively.

    Field *numbers* are shuffled by the current build, but the nested message
    shape is stable: one large objective ID, a boolean reached flag, optional
    text parameters, and a two-number completion record.  Keep only values we
    can identify from that shape; the hub validates/enriches IDs using the
    installed game data before presenting them to a planner.
    """
    fields = _fields(blob)
    scalars = [int(v) for _, wire, v in fields if wire == 0 and int(v) >= 0]
    if not scalars:
        return None
    # Objective IDs in the local data set are positive game IDs; completion
    # counters and booleans are small.  This intentionally avoids inventing
    # an ID when a message only contains progress counters.
    identifiers = [v for v in scalars if v > 255]
    objective_id = max(identifiers, default=None)
    if objective_id is None:
        return None
    completion: dict[str, int] | None = None
    for _, wire, value in fields:
        if wire != 2:
            continue
        nested = [int(v) for _, nested_wire, v in _fields(value) if nested_wire == 0 and int(v) >= 0]
        if len(nested) == 2:
            completion = {"current": nested[0], "max": nested[1]}
            break
    params: list[str] = []
    for _, wire, value in fields:
        if wire != 2:
            continue
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if text and text.isprintable():
            params.append(text)
    return {"objective_id": objective_id, "reached": 1 in scalars,
            "params": params, "completion": completion}


def _quest_active(blob: bytes) -> dict[str, Any] | None:
    """Decode one active-quest record by its nested Details/objectives shape."""
    fields = _fields(blob)
    scalars = [int(v) for _, wire, v in fields if wire == 0 and int(v) > 0]
    if not scalars:
        return None
    for _, wire, details_blob in fields:
        if wire != 2:
            continue
        details = _fields(details_blob)
        objective_blobs = [value for _, inner_wire, value in details if inner_wire == 2]
        objectives = [objective for value in objective_blobs if (objective := _quest_objective(value)) is not None]
        if not objectives and not objective_blobs:
            continue
        step_values = [int(v) for _, inner_wire, v in details if inner_wire == 0 and int(v) > 0]
        # Quest IDs are carried at the record level and are normally the only
        # positive scalar there.  If future builds add another scalar, prefer
        # the first field in wire order over a numerical guess.
        return {"quest_id": scalars[0], "step_id": step_values[0] if step_values else None,
                "objectives": objectives}
    return None


def _quests(stream: bytes) -> dict[str, list[dict[str, Any]]]:
    """Keep full quest state in protocol IDs; local data joins happen in the hub.

    AstraBot supplies the current aliases, but current Dofus builds reorder
    protobuf fields.  Active snapshots are therefore recognized from the
    stable nested QuestActive → Details → Objective message shape rather than
    hard-coding a field number.
    """
    active: dict[int, dict[str, Any]] = {}
    completed: set[int] = set()
    for _, typ, value in _any_events(stream):
        fs = _fields(value)
        if typ == "ica":  # QuestsEvent snapshot
            # A snapshot replaces (rather than appends to) active state.
            active.clear()
            for _, wire, blob in (item for item in fs if item[1] == 2):
                rec = _fields(blob)
                # Current finished records are two scalar fields (count, ID).
                # Active records have a nested Details message and are handled
                # separately below.
                quest_id = next((int(v) for n, w, v in rec if n == 2 and w == 0), None) if not any(w == 2 for _, w, _ in rec) else None
                if quest_id is not None and quest_id > 0:
                    completed.add(quest_id)
                    active.pop(quest_id, None)
                elif decoded := _quest_active(blob):
                    active[int(decoded["quest_id"])] = decoded
        elif typ == "icb":  # QuestStartedEvent
            quest_id = next((int(v) for _, w, v in fs if w == 0 and v > 0), None)
            if quest_id is not None:
                active.setdefault(quest_id, {"quest_id": quest_id, "objectives": []})
        elif typ == "ibc":  # QuestValidatedEvent
            quest_id = next((int(v) for _, w, v in fs if w == 0 and v > 0), None)
            if quest_id is not None:
                completed.add(quest_id); active.pop(quest_id, None)
        elif typ == "ici":  # QuestStepStartedEvent
            vals = [int(v) for _, w, v in fs if w == 0 and v > 0]
            if len(vals) >= 2:
                # Prefer a value already known as a quest ID.  Obfuscated
                # field ordering is not a safe semantic contract by itself.
                quest_id = next((value for value in vals if value in active), vals[0])
                step_id = next((value for value in vals if value != quest_id), vals[1])
                active.setdefault(quest_id, {"quest_id": quest_id, "objectives": []})["step_id"] = step_id
        elif typ == "ibe":  # QuestStepValidatedEvent
            vals = [int(v) for _, w, v in fs if w == 0 and v > 0]
            if len(vals) >= 2:
                quest_id = next((value for value in vals if value in active), vals[0])
                step_id = next((value for value in vals if value != quest_id), vals[1])
                active.setdefault(quest_id, {"quest_id": quest_id, "objectives": []})["validated_step_id"] = step_id
        elif typ == "icj":  # QuestStepInformationEvent, one nested QuestActive
            for _, wire, blob in fs:
                if wire == 2 and (decoded := _quest_active(blob)):
                    active[int(decoded["quest_id"])] = decoded
        elif typ == "ibs":  # QuestsFollowedEvent, repeated QuestActive
            for _, wire, blob in fs:
                if wire == 2 and (decoded := _quest_active(blob)):
                    active[int(decoded["quest_id"])] = decoded
        elif typ == "ibg":  # QuestObjectiveValidatedEvent
            vals = [int(v) for _, w, v in fs if w == 0 and v > 0]
            if len(vals) >= 2:
                quest_id = next((value for value in vals if value in active), vals[0])
                objective_id = next((value for value in vals if value != quest_id), vals[1])
                for objective in active.get(quest_id, {}).get("objectives", []):
                    if objective.get("objective_id") == objective_id:
                        objective["reached"] = True
    return {"active": list(active.values()), "completed": [{"quest_id": q} for q in sorted(completed)]}


def _achievements(stream: bytes) -> dict[str, list[dict[str, Any]]]:
    """Decode achievement aliases verified in Astra's current protobuf map.

    ``mdo`` is ``AchievementsDetailedEvent`` and ``mdp`` is the completion
    event.  Unknown achievement-shaped packets are deliberately ignored.
    """
    detailed: dict[int, dict[str, Any]] = {}
    completed: set[int] = set()
    for _, typ, value in _any_events(stream):
        if typ == "mdo":  # AchievementsDetailedEvent.achievements
            for _, wire, blob in _fields(value):
                if wire != 2:
                    continue
                fields = _fields(blob)
                achievement_id = next((int(raw) for number, kind, raw in fields if number == 1 and kind == 0), None)
                if achievement_id is None:
                    continue
                objectives = []
                for number, kind, objective_blob in fields:
                    if number != 2 or kind != 2:
                        continue
                    values = {field: raw for field, obj_wire, raw in _fields(objective_blob) if obj_wire == 0}
                    if 1 in values:
                        objectives.append({"objective_id": int(values[1]), "max": int(values.get(2, 0)),
                                           "current": int(values[3]) if 3 in values else None})
                detailed[achievement_id] = {"achievement_id": achievement_id, "objectives": objectives}
        elif typ == "mdp":  # AchievementsEvent.achieved_achievements
            for _, wire, blob in _fields(value):
                if wire != 2:
                    continue
                achievement_id = next((int(raw) for number, kind, raw in _fields(blob) if number == 1 and kind == 0), None)
                if achievement_id is not None:
                    completed.add(achievement_id)
    return {"detailed": [detailed[key] for key in sorted(detailed)],
            "completed": [{"achievement_id": key} for key in sorted(completed)]}


@dataclass
class NetworkScan:
    groups: list[NetworkGroup]
    players: list[NetworkPlayer]
    packets: int
    bytes: int
    fight_monsters: list[NetworkMember] = field(default_factory=list)
    fight_players: list[NetworkPlayer] = field(default_factory=list)
    placement_moves: list[int] = field(default_factory=list)
    fight_map_id: int | None = None
    fight_events: list[dict[str, Any]] = field(default_factory=list)
    map_id: int | None = None
    map_events: list[dict[str, Any]] = field(default_factory=list)
    interactives: list[dict[str, Any]] = field(default_factory=list)
    stated_elements: list[dict[str, Any]] = field(default_factory=list)
    character: NetworkCharacter = field(default_factory=NetworkCharacter)
    protocol_events: list[NetworkProtocolEvent] = field(default_factory=list)
    inventory: NetworkInventory = field(default_factory=NetworkInventory)
    jobs: list[NetworkJob] = field(default_factory=list)
    dialog: NetworkDialog | None = None
    spells: list[dict[str, Any]] = field(default_factory=list)
    shortcuts: list[dict[str, Any]] = field(default_factory=list)
    quests: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"active": [], "completed": []})
    achievements: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"detailed": [], "completed": []})


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, i
        shift += 7
    raise ValueError("truncated varint")


def _fields(buf: bytes) -> list[tuple[int, int, Any]]:
    i, out = 0, []
    while i < len(buf):
        try:
            key, i = _varint(buf, i)
        except ValueError:
            # TCP captures can end in the middle of an application frame.
            # Keep complete fields already decoded instead of failing the scan.
            break
        number, wire = key >> 3, key & 7
        try:
            if wire == 0:
                value, i = _varint(buf, i)
            elif wire == 1:
                if i + 8 > len(buf): break
                value, i = buf[i:i + 8], i + 8
            elif wire == 2:
                size, i = _varint(buf, i)
                if i + size > len(buf): break
                value, i = buf[i:i + size], i + size
            elif wire == 5:
                if i + 4 > len(buf): break
                value, i = buf[i:i + 4], i + 4
            else:
                break
        except ValueError:
            break
        out.append((number, wire, value))
    return out


def _packed_varints(buf: bytes) -> list[int]:
    """Decode a protobuf packed uint32/int32 field (used by fight paths)."""
    out: list[int] = []
    i = 0
    while i < len(buf):
        value, i = _varint(buf, i)
        out.append(_signed(value))
    return out


def _signed(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


def _pcap_streams(path: Path) -> tuple[bytes, bytes, int]:
    raw = path.read_bytes()
    if len(raw) < 24:
        raise ValueError("pcap header is truncated")
    magic = struct.unpack_from("<I", raw)[0]
    endian = "<" if magic == 0xA1B2C3D4 else ">" if magic == 0xD4C3B2A1 else None
    if endian is None:
        raise ValueError("unsupported pcap format")
    off, packets, segments = 24, 0, {"server": [], "client": []}
    while off + 16 <= len(raw):
        _, _, caplen, _ = struct.unpack_from(endian + "IIII", raw, off)
        off += 16
        packet = raw[off:off + caplen]
        off += caplen
        packets += 1
        if len(packet) < 54:
            continue
        ethertype = struct.unpack_from("!H", packet, 12)[0]
        ip = packet[14:]
        if ethertype == 0x0800 and ip[0] >> 4 == 4:
            if ip[9] != 6:
                continue
            ihl = (ip[0] & 15) * 4
        elif ethertype == 0x86DD and ip[0] >> 4 == 6:
            # Dofus currently uses IPv4 for the game socket, but the UI
            # capture is port-scoped, so accept the IPv6 form too.
            if ip[6] != 6:
                continue
            ihl = 40
        else:
            continue
        tcp = ip[ihl:]
        if len(tcp) < 20:
            continue
        sport, dport, seq, _, offset_flags, _ = struct.unpack_from("!HHIIBB", tcp, 0)
        if sport != 5555 and dport != 5555:
            continue
        payload = tcp[((offset_flags >> 4) & 15) * 4:]
        if payload:
            segments["server" if sport == 5555 else "client"].append((seq, payload))
    streams = []
    for direction in ("server", "client"):
        chunks = sorted(segments[direction]); stream, end = bytearray(), None
        for seq, payload in chunks:
            if end is None:
                stream.extend(payload); end = seq + len(payload)
            elif seq >= end:
                stream.extend(payload); end = seq + len(payload)
            elif seq + len(payload) > end:
                stream.extend(payload[end - seq:]); end = seq + len(payload)
        streams.append(bytes(stream))
    return streams[0], streams[1], packets


def _pcap_payload(path: Path) -> tuple[bytes, int]:
    stream, _, packets = _pcap_streams(path)
    return stream, packets


def _any_values(stream: bytes, type_name: bytes) -> list[bytes]:
    values = []
    for match in re.finditer(rb"type\.ankama\.com/" + type_name, stream):
        start = stream.rfind(b"\x0a", 0, match.start())
        if start < 0:
            continue
        try:
            type_len, cursor = _varint(stream, start + 1)
            cursor += type_len
            if stream[cursor] != 0x12:
                continue
            size, cursor = _varint(stream, cursor + 1)
            values.append(stream[cursor:cursor + size])
        except (IndexError, ValueError):
            continue
    return values


def _text(buf: bytes) -> list[str]:
    return [m.decode("utf-8", "replace") for m in re.findall(rb"[ -~]{4,}", buf)]


def _monster_members(buf: bytes) -> list[NetworkMember]:
    """Find nested MonsterInGroupInformation-shaped records.

    The generated client uses compact type aliases, so field names are not
    available in the wire data. A member has a plausible monster id in field
    1 and the still-unverified field 4. Walking nested messages keeps this
    parser independent of the exact generated class names.
    """
    found: list[NetworkMember] = []

    def walk(blob: bytes) -> None:
        fs = _fields(blob)
        # AstraBot's kzi mapping: wire 5=gid, wire 2=grade, wire 4=level.
        # The old parser treated wire 1/4 as id/grade, which explains the
        # bogus levels shown in the map UI.
        mid = next((_signed(v) for n, w, v in fs if n == 5 and w == 0), None)
        grade = next((_signed(v) for n, w, v in fs if n == 2 and w == 0), None)
        level = next((_signed(v) for n, w, v in fs if n == 4 and w == 0), None)
        if mid is not None and 10 <= mid <= 100000:
            meta = _MONSTER_CATALOG.get(mid, {})
            found.append(NetworkMember(mid, grade, meta.get("name"), meta.get("levels", []), level,
                                       _MONSTER_XP.get(mid, [])))
        for n, w, value in fs:
            if w == 2 and isinstance(value, bytes):
                walk(value)

    walk(buf)
    # Do not deduplicate: repeated identical members are the group size.
    return found


def _load_catalog() -> dict[int, dict[str, Any]]:
    path = Path(__file__).resolve().parents[1] / "data" / "monster_catalog.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {int(m["id"]): m for m in raw.get("monsters", [])}
    except (OSError, ValueError, TypeError):
        return {}


_MONSTER_CATALOG = _load_catalog()


def _load_monster_xp() -> dict[int, list[int]]:
    """Read grade XP from the installed MonsterData asset, without OCR."""
    try:
        import UnityPy
        UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0f1"
        bundle = Path("/Applications/Ankama/Dofus-dofus3/DofusContent/Content/Data/data_assets_monstersdataroot.asset.bundle")
        if not bundle.exists():
            return {}
        root = next(o for o in UnityPy.load(str(bundle)).objects if o.type.name == "MonoBehaviour").read_typetree()
        refs = {item["rid"]: item.get("data", {}) for item in root.get("references", {}).get("RefIds", [])}
        keys = root.get("objectsById", {}).get("m_keys", [])
        values = root.get("objectsById", {}).get("m_values", [])
        output: dict[int, list[int]] = {}
        for monster_id, value in zip(keys, values):
            data = refs.get(value.get("rid"), {})
            xp = [int(grade.get("xp", 0)) for grade in data.get("grades", [])]
            if any(xp):
                output[int(monster_id)] = xp
        return output
    except (ImportError, OSError, StopIteration, KeyError, TypeError, ValueError):
        return {}


_MONSTER_XP = _load_monster_xp()


def _fight_monsters(stream: bytes) -> list[NetworkMember]:
    """Extract monster fighter identity/grade from fight-show messages (jwj).

    The obfuscated type name is build-specific, but this capture's fight-show
    payload has a nested record with field 1 = monster template ID and field 4
    = one-based creature grade.
    """
    found: list[NetworkMember] = []
    def first_level(blob: bytes) -> int | None:
        for fn, fw, value in _fields(blob):
            if fn == 2 and fw == 0 and 1 <= value <= 300:
                return value
            if fw == 2 and isinstance(value, bytes):
                nested = first_level(value)
                if nested is not None:
                    return nested
        return None

    for value in _any_values(stream, b"jwj"):
        # FighterShow wraps the actor in field 1. Its field 2 is the
        # disposition (cell, direction, ...); field 3 is the fighter info.
        # In this capture disposition.field3 is the actual map cell.
        outer = _fields(value)
        actor_wrapper = next((v for n, w, v in outer if n == 1 and w == 2), b"")
        disposition = next((v for n, w, v in _fields(actor_wrapper) if n == 2 and w == 2), b"")
        fight_cell = next((v for n, w, v in _fields(disposition) if n == 3 and w == 0 and 0 <= v <= 559), None)
        def walk(blob: bytes, level_hint: int | None = None) -> None:
            fs = _fields(blob)
            mid = next((v for n, w, v in fs if n == 1 and w == 0 and 1000 <= v <= 100000), None)
            grade = next((v for n, w, v in fs if n == 4 and w == 0 and 1 <= v <= 10), None)
            if mid is not None and grade is not None:
                meta = _MONSTER_CATALOG.get(mid, {})
                found.append(NetworkMember(mid, grade, meta.get("name"), meta.get("levels", []), level_hint, fight_cell))
            sibling_level = level_hint
            for n, w, nested in fs:
                if n in (4, 6) and w == 2 and isinstance(nested, bytes):
                    candidate = first_level(nested)
                    if candidate is not None:
                        sibling_level = candidate
            for n, w, nested in fs:
                if w == 2 and isinstance(nested, bytes):
                    walk(nested, sibling_level)
        walk(value)
    # Keep individual fighters: a group can contain multiple monsters with the
    # same template and grade, but different cells (and sometimes levels).
    unique: dict[tuple[int, int | None, int | None, int | None], NetworkMember] = {}
    for member in found:
        unique[(member.monster_id, member.grade, member.cell, member.level_override)] = member
    return list(unique.values())


def _placement_moves(stream: bytes) -> list[int]:
    """Read client placement-cell requests (obfuscated alias jxb in this build)."""
    cells: list[int] = []
    for value in _any_values(stream, b"jxb"):
        cell = next((v for n, w, v in _fields(value) if n == 1 and w == 0 and 0 <= v <= 559), None)
        if cell is not None:
            cells.append(cell)
    return cells


def _fight_players(stream: bytes) -> list[NetworkPlayer]:
    """Extract positive-id player fighters from the same FighterShow messages."""
    out: list[NetworkPlayer] = []
    for value in _any_values(stream, b"jwj"):
        outer = _fields(value)
        actor = next((v for n, w, v in outer if n == 1 and w == 2), b"")
        af = _fields(actor)
        raw_id = next((v for n, w, v in af if n == 1 and w == 0), None)
        if raw_id is None:
            continue
        entity_id = _signed(raw_id)
        if entity_id <= 0:
            continue
        disposition = next((v for n, w, v in af if n == 2 and w == 2), b"")
        cell = next((v for n, w, v in _fields(disposition) if n == 3 and w == 0 and 0 <= v <= 559), None)
        texts = [t for t in _text(actor) if not t.startswith("type.ankama.com")]
        name = texts[0].rstrip("0") if texts else None
        out.append(NetworkPlayer(entity_id, name, cell))
    return out


def _fight_map_id(stream: bytes) -> int | None:
    # ContextReadyRequest's build-specific alias is kjt in these captures;
    # its field 1 is the map id used to load the tactical map asset.
    for value in _any_values(stream, b"kjt"):
        found = next((v for n, w, v in _fields(value) if n == 1 and w == 0 and v > 1000000), None)
        if found is not None:
            return found
    return None


def _character_stats(stream: bytes) -> NetworkCharacter:
    """Decode the latest role-play CharacterCharacteristicsEvent.

    ``krg`` is the current build's alias.  Its nested characteristic records
    identify AP as stat 1 and MP as stat 23; those IDs are also what the game
    uses in the fight characteristic list.  Scalar field numbers below are
    observed in the current 3.7 capture and deliberately remain isolated here
    so a build change cannot silently corrupt the dashboard.
    """
    latest: bytes | None = None
    for _, typ, value in _any_events(stream):
        if typ == "krg":
            latest = value
    if latest is None:
        return NetworkCharacter()
    outer = _fields(latest)
    # The event wraps CharacterCharacteristicsInformations in field 1.
    fields = _fields(next((v for n, w, v in outer if n == 1 and w == 2), b""))
    ints = {n: _signed(v) for n, w, v in fields if w == 0}
    result = NetworkCharacter(
        # In this build field 8 is current XP and field 2 is the next level
        # floor.  The latter is more useful than a guessed level number.
        experience=ints.get(8),
        experience_next=ints.get(2),
    )
    # AstraBot's current mapping names the selection envelope ksb.  The
    # Success payload is field 1, then character id=3 and details=4.  (ksc
    # was an old alias and made every current character look anonymous.)
    for _, typ, value in _any_events(stream):
        if typ != "ksb":
            continue
        success = next((v for n, w, v in _fields(value) if n == 1 and w == 2), None)
        selected = next((v for n, w, v in _fields(success) if n == 1 and w == 2), None) if success else None
        if selected is None:
            continue
        sf = _fields(selected)
        result.entity_id = next((_signed(v) for n, w, v in sf if n == 3 and w == 0), result.entity_id)
        details = next((v for n, w, v in sf if n == 4 and w == 2), b"")
        df = _fields(details)
        result.level = next((_signed(v) for n, w, v in df if n == 3 and w == 0), result.level)
        names = [text for text in _text(details) if text and not text.startswith("type.ankama.com")]
        if names:
            result.name = names[-1].rstrip("0")
    # UpdateLifePointsEvent is a separate event, not fields 5/6 of the
    # characteristics packet (those are unrelated account values).  In the
    # observed current build kqo uses fields 1=current life and 3=max life;
    # field 4 is a transient update value, not max HP.
    for _, typ, value in _any_events(stream):
        if typ == "kqo":
            life = {n: _signed(v) for n, w, v in _fields(value) if w == 0}
            result.hp = life.get(1, result.hp)
            result.hp_max = life.get(3, result.hp_max)
        elif typ == "kqp":
            result.level = next((_signed(v) for n, w, v in _fields(value) if n == 1 and w == 0), result.level)
    for n, w, record in fields:
        if n != 1 or w != 2:
            continue
        rf = _fields(record)
        stat_id = next((int(v) for a, b, v in rf if a == 2 and b == 0), None)
        if stat_id not in (1, 23):
            continue
        # CharacterCharacteristic.value is nested field 1, field 4 in this
        # build.  A few records also carry a bonus in field 5; AP/MP use the
        # base value, so read the field-1 value only.
        value_blob = next((v for a, b, v in rf if a == 1 and b == 2), b"")
        value = next((int(v) for a, b, v in _fields(value_blob) if a == 4 and b == 0), None)
        if stat_id == 1:
            result.pa = value
        else:
            result.pm = value
    return result


def _inventory_and_jobs(stream: bytes) -> tuple[NetworkInventory, list[NetworkJob]]:
    """Reduce inventory/jobs using AstraBot's current Dofus 3 mappings.

    These aliases are build-specific and intentionally isolated here.  The
    complete raw protocol event list remains available when Ankama changes a
    mapping, while the common fields are decoded without screenshots.
    """
    inventory = NetworkInventory()
    jobs_by_id: dict[int, NetworkJob] = {}
    for _, typ, value in _any_events(stream):
        fields = _fields(value)
        if typ == "irl":  # InventoryContentEvent, current build: objects=2, kamas=4
            inventory.kamas = next((_signed(v) for n, w, v in fields if n == 4 and w == 0), inventory.kamas)
            inventory.objects = []
            for n, w, blob in fields:
                if n != 2 or w != 2:
                    continue
                # Current object wrapper: item=1, position=4.  The embedded
                # ObjectItem uses quantity=3, gid=4, uid=5 (verified against
                # the local item table: 2476 = Bottes de l'Aventurier).
                outer = _fields(blob)
                item_blob = next((v for a, b, v in outer if a == 1 and b == 2), b"")
                item = _fields(item_blob)
                inventory.objects.append({
                    "uid": next((_signed(v) for a, b, v in item if a == 5 and b == 0), None),
                    "quantity": next((_signed(v) for a, b, v in item if a == 3 and b == 0), None),
                    "gid": next((_signed(v) for a, b, v in item if a == 4 and b == 0), None),
                    "position": next((_signed(v) for a, b, v in outer if a == 4 and b == 0), None),
                })
        elif typ == "iqq":  # InventoryWeightEvent
            inventory.weight = next((_signed(v) for n, w, v in fields if n == 1 and w == 0), inventory.weight)
            inventory.weight_max = next((_signed(v) for n, w, v in fields if n == 2 and w == 0), inventory.weight_max)
        elif typ == "ith":  # KamasUpdateEvent
            inventory.kamas = next((_signed(v) for n, w, v in fields if n == 1 and w == 0), inventory.kamas)
        elif typ == "iqx":  # ObjectAddedEvent, same nested object layout
            item_blob = next((v for n, w, v in fields if n == 2 and w == 2), b"")
            item = _fields(item_blob)
            if item:
                inventory.objects.append({
                    "uid": next((_signed(v) for a, b, v in item if a == 1 and b == 0), None),
                    "quantity": next((_signed(v) for a, b, v in item if a == 2 and b == 0), None),
                    "gid": next((_signed(v) for a, b, v in item if a == 3 and b == 0), None),
                    "position": None,
                })
        elif typ == "iqm":  # ObjectDeletedEvent
            uid = next((_signed(v) for n, w, v in fields if n == 1 and w == 0), None)
            if uid is not None:
                inventory.objects = [obj for obj in inventory.objects if obj.get("uid") != uid]
        elif typ == "ipz":  # JobExperiencesUpdateEvent: repeated JobExperience
            for n, w, blob in fields:
                if n != 1 or w != 2:
                    continue
                jf = _fields(blob)
                job = NetworkJob(
                    job_id=next((_signed(v) for a, b, v in jf if a == 1 and b == 0), None),
                    level=next((_signed(v) for a, b, v in jf if a == 2 and b == 0), None),
                    xp=next((_signed(v) for a, b, v in jf if a == 3 and b == 0), None),
                    xp_floor=next((_signed(v) for a, b, v in jf if a == 4 and b == 0), None),
                    xp_next=next((_signed(v) for a, b, v in jf if a == 5 and b == 0), None),
                )
                if job.job_id is not None:
                    jobs_by_id[job.job_id] = job
    return inventory, list(jobs_by_id.values())


def _actor_record(actor: bytes) -> dict[str, Any] | None:
    """Normalize a role-play actor without using the sign of its ID as type."""
    fields = _fields(actor)
    raw_id = next((v for n, w, v in fields if n == 1 and w == 0), None)
    if raw_id is None:
        return None
    actor_id = _signed(raw_id)
    disposition = next((v for n, w, v in fields if n == 2 and w == 2), b"")
    df = _fields(disposition)
    cell = next((v for n, w, v in df if n == 3 and w == 0 and 0 <= v <= 559), None)
    info = next((v for n, w, v in fields if n == 3 and w == 2), b"")
    kind, _ = _actor_role(actor)
    metadata: dict[str, Any] = {"raw_info": info.hex()} if info else {}
    if kind == "monster_group":
        # Keep only JSON-safe member data in the normalized event.  The final
        # map snapshot is then sufficient to rebuild groups without leaking a
        # same-ID actor from an older map.
        metadata["members"] = [{
            "monster_id": member.monster_id,
            "raw_field_4": member.raw_field_4,
            "name": member.name,
            "levels": member.levels,
            "level_override": member.level_override,
            "xp_levels": member.xp_levels,
        } for member in _monster_members(info)]
    if kind == "npc":
        # Current 3.7 role-play layout: actor-information field 1 contains
        # the role-play oneof; its field 4 contains the static actor data;
        # static field 7 is the NPC template ID.  The superficially tempting
        # scalar at info/2/1 is unrelated and mapped Anta Brok to another NPC
        # with a valid-looking French name.
        role_play = next((v for n, w, v in _fields(info) if n == 1 and w == 2), b"")
        static = next((v for n, w, v in _fields(role_play) if n == 4 and w == 2), b"")
        npc_id = next((v for n, w, v in _fields(static) if n == 7 and w == 0), None)
        if npc_id is not None:
            metadata["npc_id"] = int(npc_id)
    names = [s for s in _text(actor) if not s.startswith("type.ankama.com")]
    name: str | None = names[0] if kind == "player" and names else ("Friendly NPC" if kind == "npc" else None)
    return {"entity_id": actor_id, "cell": cell, "name": name, "kind": kind,
            "metadata": metadata}


def _actor_role(actor: bytes) -> tuple[str, bytes | None]:
    """Classify the obfuscated role-play actor by its decoded payload.

    AstraBot deserializes the generated oneof and checks
    ``monster_group_actor`` versus ``npc_actor``.  This lightweight parser
    does not have its generated obfuscation map, but the two variants still
    differ structurally: only MonsterGroupStaticInformation contains the
    repeated MonsterInGroupInformation records decoded by ``_monster_members``.
    """
    fields = _fields(actor)
    raw_id = next((v for n, w, v in fields if n == 1 and w == 0), None)
    if raw_id is None:
        return "unknown", None
    actor_id = _signed(raw_id)
    if actor_id > 0:
        return "player", None
    info = next((v for n, w, v in fields if n == 3 and w == 2), b"")
    return ("monster_group", info) if _monster_members(info) else ("npc", info)


def _map_events(stream: bytes) -> tuple[int | None, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Decode the role-play map envelope into stable, lossless-ish records.

    jnj/jns/etc. are obfuscated aliases in the current client build. The
    reducer consumes these records; it never needs to know the aliases.
    """
    map_id: int | None = None
    events: list[dict[str, Any]] = []
    interactives: list[dict[str, Any]] = []
    stated: list[dict[str, Any]] = []

    def stated_record(blob: bytes, offset: int) -> dict[str, Any] | None:
        """Current-build StatedElement mapping, confirmed against map snapshots."""
        ef = _fields(blob)
        element_id = next((v for a, b, v in ef if a == 3 and b == 0), None)
        if element_id is None:
            return None
        cell = next((v for a, b, v in ef if a == 4 and b == 0 and 0 <= v <= 559), None)
        state = next((v for a, b, v in ef if a == 2 and b == 0), None)
        return {"id": int(element_id), "cell": cell, "state": state,
                "_offset": offset, "metadata": {"raw": blob.hex()}}

    def interactive_record(blob: bytes, offset: int) -> dict[str, Any] | None:
        """Current-build InteractiveElement mapping, confirmed against jnj."""
        ef = _fields(blob)
        element_id = next((v for a, b, v in ef if a == 6 and b == 0), None)
        if element_id is None:
            return None
        skills: list[int] = []
        skill_instances: list[dict[str, int | None]] = []
        for a, b, skill_blob in ef:
            if a != 5 or b != 2:
                continue
            # Astra's InteractiveElementSkill mapping: field 1 is the
            # ephemeral skill_instance_uid used by InteractiveUseRequest;
            # field 3 is the static skill_id used to resolve the resource.
            parts = {q: int(v) for q, r, v in _fields(skill_blob) if r == 0}
            skill = parts.get(3)
            instance_uid = parts.get(1)
            if skill is not None:
                skills.append(skill)
                skill_instances.append({"skill_id": skill, "skill_instance_uid": instance_uid,
                                        "name_id": parts.get(2)})
        element_type = next((v for a, b, v in ef if a == 4 and b == 0), None)
        on_map = next((v for a, b, v in ef if a == 2 and b == 0), None)
        return {"id": int(element_id), "cell": None, "type_id": element_type, "skills": skills,
                "skill_instances": skill_instances,
                "available": bool(on_map) if on_map is not None else None,
                "_offset": offset, "metadata": {"raw": blob.hex()}}

    for offset, typ, value in _any_events(stream):
        fs = _fields(value)
        if typ == "jnj":
            map_id = next((v for n, w, v in fs if n == 10 and w == 0 and v > 1000000), map_id)
            actors = [_actor_record(v) for n, w, v in fs if n == 3 and w == 2]
            actors = [a for a in actors if a]
            for n, w, blob in fs:
                if n == 2 and w == 2:
                    # Obfuscated StatedElement: 3=element id, 4=cell,
                    # 2=state, 5=on-current-map.  This is the authoritative
                    # ready/used state AstraBot joins to an InteractiveElement.
                    record = stated_record(blob, offset)
                    if record is not None:
                        stated.append(record)
            for n, w, blob in fs:
                if n == 7 and w == 2:
                    # Obfuscated InteractiveElement: 6=element id, 4=type,
                    # 5=repeated skills (skill id is nested field 3),
                    # 2=on-current-map.
                    record = interactive_record(blob, offset)
                    if record is not None:
                        interactives.append(record)
            events.append({"kind": "map_snapshot", "type": typ, "offset": offset,
                           "map_id": map_id, "actors": actors})
        elif typ == "jns":
            actors = [_actor_record(v) for n, w, v in fs if n == 1 and w == 2]
            actors = [a for a in actors if a]
            events.append({"kind": "actors_snapshot", "type": typ, "offset": offset, "actors": actors})
        elif typ == "jnz":
            path_blob = next((v for n, w, v in fs if n == 1 and w == 2), b"")
            path = [c for c in _packed_varints(path_blob) if 0 <= c <= 559]
            actor = next((_signed(v) for n, w, v in fs if n == 4 and w == 0), None)
            events.append({"kind": "movement", "type": typ, "offset": offset, "actor_id": actor,
                           "path_cells": path, "start_cell": path[0] if path else None,
                           "end_cell": path[-1] if path else None})
        elif typ == "kjz":
            element_id = next((_signed(v) for n, w, v in fs if n == 1 and w == 0), None)
            events.append({"kind": "element_removed", "type": typ, "offset": offset, "element_id": element_id})
        elif typ in {"its", "itu", "itw", "itx"}:
            # AstraBot listens for these lifecycle messages after the initial
            # map snapshot.  Keep the snapshot current as a resource is used,
            # disabled, or becomes available again.
            candidates = [blob for _, wire, blob in fs if wire == 2]
            records = [stated_record(blob, offset) for blob in candidates] if typ in {"itu", "itw"} else [interactive_record(blob, offset) for blob in candidates]
            records = [record for record in records if record is not None]
            target = stated if typ in {"itu", "itw"} else interactives
            for record in records:
                target[:] = [old for old in target if old.get("id") != record["id"]]
                target.append(record)
            events.append({"kind": "stated_update" if typ in {"itu", "itw"} else "interactive_update",
                           "type": typ, "offset": offset, "elements": records})
        elif typ in {"itv", "itt", "ity"}:
            # Used / ended / error lifecycle.  Field semantics are stable in
            # AstraBot's protocol: element, duration/skill, actor respectively.
            ints = {n: _signed(v) for n, w, v in fs if w == 0}
            events.append({"kind": {"itv": "interactive_used", "itt": "interactive_ended", "ity": "interactive_error"}[typ],
                           "type": typ, "offset": offset, "element_id": ints.get(1),
                           "duration": ints.get(3), "actor_id": ints.get(4), "skill_id": ints.get(5)})
        elif typ == "kaz":
            # AstraBot's current mapping resolves kaz as
            # ExchangeRequestedTradeEvent: exchange_type=1, target_id=2,
            # source_id=3.  Keep this separate from voluntary NPC/bank dialog.
            ints = {n: _signed(v) for n, w, v in fs if w == 0}
            events.append({"kind": "trade_requested", "type": typ, "offset": offset,
                           "exchange_type": ints.get(1), "target_id": ints.get(2),
                           "source_id": ints.get(3)})
        elif typ == "kbr":
            # ExchangeLeaveEvent closes either a rejected player trade or a
            # voluntarily opened exchange; the interruption policy owns the
            # account-scoped distinction.
            ints = {n: _signed(v) for n, w, v in fs if w == 0}
            events.append({"kind": "exchange_left", "type": typ, "offset": offset,
                           "dialog_type": ints.get(1), "success": bool(ints.get(2, 0))})
    return map_id, events, interactives, stated


def _any_events(stream: bytes) -> list[tuple[int, str, bytes]]:
    """Return typed Any payloads in wire order, retaining their stream offset."""
    out: list[tuple[int, str, bytes]] = []
    for match in re.finditer(rb"type\.ankama\.com/([A-Za-z0-9]+)", stream):
        start = stream.rfind(b"\x0a", 0, match.start())
        if start < 0:
            continue
        try:
            type_len, cursor = _varint(stream, start + 1)
            cursor += type_len
            if stream[cursor] != 0x12:
                continue
            size, cursor = _varint(stream, cursor + 1)
            out.append((start, match.group(1).decode("ascii"), stream[cursor:cursor + size]))
        except (IndexError, ValueError):
            continue
    return out


def _fight_events(stream: bytes) -> list[dict[str, Any]]:
    """Keep a conservative, wire-ordered event trace while aliases are decoded.

    Field meanings are intentionally labelled as protocol hints rather than names;
    this gives the UI a lossless timeline without pretending an obfuscated alias is
    fully decoded.
    """
    events: list[dict[str, Any]] = []
    current_turn: int | None = None
    for offset, typ, value in _any_events(stream):
        if typ not in {"jnz", "jwj", "jwh", "jwp", "jvj", "jvi", "juh", "jup", "jxm", "jww", "jxi", "jsq"}:
            continue
        fs = _fields(value)
        event: dict[str, Any] = {"offset": offset, "type": typ}
        ints = [(n, _signed(v)) for n, w, v in fs if w == 0]
        if typ == "jvj":
            event["kind"] = "turn_marker"
            # FightTurnEvent: field 2 is remaining time; field 3 is fighter id.
            actor = next((v for n, v in ints if n == 3), None)
            if actor is not None: event["actor_id"] = actor
            for n, v in ints:
                if n == 6: event["turn"] = v
                elif n == 7: event["team"] = v
            if "turn" in event: current_turn = event["turn"]
        elif typ == "jvi":
            event["kind"] = "turn_end"
        elif typ == "jnz":
            # MapMovementEvent: packed path cells, direction, character id.
            event["kind"] = "movement"
            path_blob = next((v for n, w, v in fs if n == 1 and w == 2), b"")
            path = [c for c in _packed_varints(path_blob) if 0 <= c <= 559]
            if path:
                event["path_cells"] = path
                event["start_cell"] = path[0]
                event["end_cell"] = path[-1]
                event["cell"] = path[-1]
            for n, v in ints:
                if n == 4: event["actor_id"] = v
        elif typ == "juh":
            event["kind"] = "fight_update"
        elif typ == "jup":
            event["kind"] = "turn_end"
            if ints: event["actor_id"] = ints[0][1]
        elif typ == "jxm":
            event["kind"] = "fight_option"
        elif typ == "jww":
            event["kind"] = "placement_update"
            for n, v in ints:
                if n == 3 and 0 <= v <= 559: event["cell"] = v
                elif n == 2: event["actor_id"] = v
        elif typ == "jxi":
            event["kind"] = "fighter_update"
            for n, v in ints:
                if n == 7 and 0 <= v <= 559: event["cell"] = v
                elif n == 1: event["actor_id"] = v
        elif typ == "jsq":
            event["kind"] = "action_effect"
            for n, v in ints:
                if n == 22: event["action_id"] = v
                elif n == 36: event["source_id"] = v
            # Effect payloads are nested protobuf messages. Preserve the
            # useful, non-ambiguous coordinates/actor ids without pretending
            # every numeric field is already a decoded HP value.
            nested = [(v) for n, w, v in fs if n in (9, 13, 37) and w == 2]
            target_cells: list[int] = []
            target_ids: list[int] = []
            effect_codes: list[int] = []
            for blob in nested:
                stack = [blob]
                while stack:
                    cur = stack.pop()
                    for n, w, v in _fields(cur):
                        if w == 2:
                            stack.append(v)
                        elif w == 0:
                            sv = _signed(v)
                            if n == 4 and 0 <= sv <= 559: target_cells.append(sv)
                            elif n == 6 and abs(sv) > 559: target_ids.append(sv)
                            elif n in (3, 9, 22) and 1 <= sv <= 10000: effect_codes.append(sv)
            if target_cells: event["target_cells"] = sorted(set(target_cells))
            if target_ids: event["target_ids"] = sorted(set(target_ids))
            if effect_codes: event["effect_codes"] = sorted(set(effect_codes))
            # AstraBot's current Dofus 3 mapping confirms these oneof field
            # numbers for the combat event envelope (GameActionFightEvent).
            if 2 in {n for n, w, _ in fs}:
                event["kind"] = "spell_cast"
                body = next(v for n, w, v in fs if n == 2 and w == 2)
                bf = _fields(body)
                spell_body = next((v for n, w, v in bf if n == 2 and w == 2), b"")
                spell = next((v for n, w, v in _fields(spell_body) if n == 2 and w == 0), None)
                if spell is not None: event["spell_id"] = spell
                for n, v in [(n, _signed(v)) for n, w, v in bf if w == 0]:
                    if n == 5: event["destination_cell"] = v
                    elif n == 6: event["target_id"] = v
            elif 13 in {n for n, w, _ in fs}:
                body = next(v for n, w, v in fs if n == 13 and w == 2)
                vals = {n: _signed(v) for n, w, v in _fields(body) if w == 0}
                event["kind"] = "points_change"; event["delta"] = vals.get(2); event["target_id"] = vals.get(3)
            elif 37 in {n for n, w, _ in fs}:
                body = next(v for n, w, v in fs if n == 37 and w == 2)
                vals = {n: _signed(v) for n, w, v in _fields(body) if w == 0}
                event["kind"] = "hp_loss"; event["hp_loss"] = vals.get(6); event["target_id"] = vals.get(5)
            elif 3 in {n for n, w, _ in fs}:
                body = next(v for n, w, v in fs if n == 3 and w == 2)
                event["kind"] = "death"; event["dead_id"] = next((_signed(v) for n, w, v in _fields(body) if n == 2 and w == 0), None)
            elif 5 in {n for n, w, _ in fs}:
                body = next(v for n, w, v in fs if n == 5 and w == 2)
                vals = {n: _signed(v) for n, w, v in _fields(body) if w == 0}
                event["kind"] = "slide"; event["target_id"] = vals.get(1); event["end_cell"] = vals.get(2); event["start_cell"] = vals.get(3)
        elif typ == "jwj":
            event["kind"] = "fighter_show"
            fighter = next((v for n, w, v in fs if n == 1 and w == 2), b"")
            ff = _fields(fighter)
            actor = next((_signed(v) for n, w, v in ff if n == 1 and w == 0), None)
            disposition = next((v for n, w, v in ff if n == 2 and w == 2), b"")
            df = _fields(disposition)
            cell = next((v for n, w, v in df if n == 3 and w == 0 and 0 <= v <= 559), None)
            team = next((_signed(v) for n, w, v in df if n == 4 and w == 0), None)
            if actor is not None: event["actor_id"] = actor
            if cell is not None: event["cell"] = cell
            if team is not None: event["team"] = team
            # FightFighterInformation is nested inside ActorInformation.  The
            # wire fields are the obfuscated protobuf numbers used by Dofus 3:
            # stats=7, named_fighter=5, ai_fighter=6, spawn_information=8.
            fighter_info = next((v for n, w, v in _fields(next(
                (v for n, w, v in ff if n == 3 and w == 2), b""))
                if n == 4 and w == 2), b"")
            fif = _fields(fighter_info)
            stats_blob = next((v for n, w, v in fif if n == 7 and w == 2), b"")
            stats: dict[int, int] = {}
            for n, w, blob in _fields(stats_blob):
                if n != 4 or w != 2:
                    continue
                cf = _fields(blob)
                characteristic_id = next((_signed(v) for n, w, v in cf if n == 2 and w == 0), 0)
                # CharacteristicValue, Detailed and DetailedUsable all expose
                # their numeric values through nested varints.  Sum the usable
                # components exactly as AstraBot does; for life points the
                # default (id 0) is the max/base value.
                total = 0
                for n, w, value in cf:
                    if n == 3 and w == 2:  # value.total
                        total += next((_signed(v) for _, ww, v in _fields(value) if ww == 0), 0)
                    elif n in (1, 5) and w == 2:  # usable/detailed
                        total += sum(_signed(v) for _, ww, v in _fields(value) if ww == 0)
                stats[characteristic_id] = total
            if stats:
                max_hp = stats.get(0)
                vitality = stats.get(11, 0)
                current_delta = stats.get(97, 0)
                if max_hp is not None:
                    event["hp_max"] = max_hp + vitality
                    event["hp"] = max(0, event["hp_max"] + current_delta)
                if 1 in stats: event["pa"] = stats[1]
                if 23 in stats: event["pm"] = stats[23]
            named = next((v for n, w, v in fif if n == 5 and w == 2), b"")
            name = next((v.decode("utf-8", "replace") for n, w, v in _fields(named)
                         if n == 4 and w == 2), None)
            if name:
                event["name"] = name
            ai = next((v for n, w, v in fif if n == 6 and w == 2), b"")
            # In the current obfuscated schema the monster oneof is field 3
            # (older captures used field 1); accept both versions.
            monster_info = next((v for n, w, v in _fields(ai) if n in (1, 3) and w == 2), b"")
            mf = _fields(monster_info)
            monster_id = next((_signed(v) for n, w, v in mf if n == 1 and w == 0), None)
            if monster_id is not None:
                monster_id = next((_signed(v) for n, w, v in mf if n == 3 and w == 0), monster_id)
                meta = _MONSTER_CATALOG.get(monster_id, {})
                # AstraBot's obfuscated mapping identifies lci.gbyz (wire 3)
                # as monster_gid; wire 1 is creature_grade and wire 2 is the
                # concrete creature_level.
                event["monster_id"] = monster_id
                event["name"] = meta.get("name", f"Monster {monster_id}")
                event["grade"] = next((_signed(v) for n, w, v in mf if n == 1 and w == 0), None)
                event["level"] = next((_signed(v) for n, w, v in mf if n == 2 and w == 0), None)
        elif typ == "jwp":
            event["kind"] = "fight_result"
        else:
            event["kind"] = "fight_update"
        if typ != "jvj" and current_turn is not None:
            event["turn"] = current_turn
        events.append(event)
    return events


def _fight_snapshot(events: list[dict[str, Any]], monsters: list[NetworkMember],
                    players: list[NetworkPlayer], map_id: int | None) -> dict[str, Any]:
    """Build the planner-facing fight JSON from the ordered protocol trace."""
    active = False
    phase = "out_of_fight"
    turn: int | None = None
    fighters: dict[int, dict[str, Any]] = {}
    for event in events:
        kind = event.get("kind")
        if kind == "fighter_show":
            active = True; phase = "placement"
            if event.get("actor_id") is not None:
                actor_id = int(event["actor_id"])
                fighter = {"id": actor_id, "cell": event.get("cell"),
                           "team": event.get("team"), "alive": True}
                for key in ("name", "monster_id", "grade", "level", "hp", "hp_max", "pa", "pm"):
                    if event.get(key) is not None:
                        fighter[key] = event[key]
                fighters[actor_id] = fighter
        elif kind == "turn_marker":
            active = True; phase = "turn"; turn = event.get("turn", turn)
        elif kind == "turn_end":
            phase = "turn_end"
        elif kind == "fight_result":
            active = False; phase = "ended"
        if event.get("actor_id") is not None and kind == "movement":
            actor_id = int(event["actor_id"])
            fighters.setdefault(actor_id, {"id": actor_id, "alive": True})["cell"] = event.get("end_cell")
        if kind == "hp_loss" and event.get("target_id") is not None and event.get("hp_loss") is not None:
            actor_id = int(event["target_id"])
            fighter = fighters.setdefault(actor_id, {"id": actor_id, "alive": True})
            if fighter.get("hp") is not None:
                fighter["hp"] = max(0, int(fighter["hp"]) - int(event["hp_loss"]))
        if event.get("dead_id") is not None:
            fighters.setdefault(int(event["dead_id"]), {"id": int(event["dead_id"])})["alive"] = False
    if not active and phase == "ended":
        phase = "out_of_fight"
    # When FighterShow is absent, retain decoded fighter records as evidence,
    # but do not pretend their actor IDs are known.
    if not fighters:
        fighters = {-(i + 1): {"id": -(i + 1), "kind": "monster", "cell": m.cell,
                              "monster_id": m.monster_id, "name": m.name,
                              "grade": m.grade, "level": m.level, "alive": True}
                    for i, m in enumerate(monsters)}
    if not active:
        fighters = {}
    return {"active": active, "phase": phase, "map_id": map_id, "turn": turn,
            "fighters": list(fighters.values()),
            "events": events if active else [],
            "monster_records": [{"monster_id": m.monster_id, "name": m.name,
                                 "grade": m.grade, "level": m.level, "cell": m.cell} for m in monsters],
            "player_records": [{"entity_id": p.entity_id, "name": p.name, "cell": p.cell} for p in players]}


def _dialog(stream: bytes) -> NetworkDialog | None:
    """Reduce AstraBot's NPC-dialog lifecycle aliases (ilx/ilf/kis)."""
    active: NetworkDialog | None = None
    actor_id: int | None = None
    for _, typ, value in _any_events(stream):
        fields = _fields(value)
        if typ == "ilx":  # NpcDialogCreationEvent; current build field 5 = actor id
            actor_id = next((_signed(v) for n, w, v in fields if n == 5 and w == 0), actor_id)
        elif typ == "ilf":  # NpcDialogQuestionEvent
            message_id = next((v for n, w, v in fields if n == 3 and w == 0), None)
            replies: list[int] = []
            for n, w, blob in fields:
                if n != 1 or w != 2:
                    continue
                reply_id = next((v for a, b, v in _fields(blob) if a == 1 and b == 0), None)
                if reply_id is not None:
                    replies.append(int(reply_id))
            params = [value.decode("utf-8", "replace") for n, w, value in fields if n == 2 and w == 2]
            stage = {"message_id": int(message_id) if message_id is not None else None,
                     "params": params, "replies": replies}
            if active is not None and active.actor_id == actor_id:
                active.message_id, active.params, active.replies = stage["message_id"], params, replies
                active.stages.append(stage)
            else:
                active = NetworkDialog(actor_id, stage["message_id"], params, replies, [stage])
        elif typ == "kis":  # DialogLeaveEvent
            active = None
    return active


def _spells(stream: bytes) -> list[dict[str, Any]]:
    """Reduce AstraBot's current SpellsEvent (hkh) to the human spell bar."""
    spells: list[dict[str, Any]] = []
    for _, typ, value in _any_events(stream):
        if typ != "hkh":
            continue
        current: list[dict[str, Any]] = []
        for number, wire, blob in _fields(value):
            if number != 1 or wire != 2:  # current obfuscated human_spells
                continue
            fields = _fields(blob)
            spell_id = next((v for n, w, v in fields if n == 2 and w == 0), None)
            if spell_id is None:
                continue
            current.append({
                "spell_id": int(spell_id),
                "level": next((v for n, w, v in fields if n == 1 and w == 0), None),
                "available": next((bool(v) for n, w, v in fields if n == 4 and w == 0), True),
            })
        if current:
            spells = current
    return spells


def _join_character_actor(character: NetworkCharacter, players: list[NetworkPlayer],
                          fight_players: list[NetworkPlayer]) -> None:
    """Enrich the character record from the current role-play/fight actor list."""
    if character.entity_id is None:
        return
    own = next((p for p in players if p.entity_id == character.entity_id), None)
    if own is None:
        # During a fight the role-play player list is intentionally cleared in
        # favour of combat fighters, but the identity is the same entity.
        own = next((p for p in fight_players if p.entity_id == character.entity_id), None)
    if own is not None:
        character.cell = own.cell
        character.name = own.name or character.name


def _shortcuts(stream: bytes) -> list[dict[str, Any]]:
    """Reduce AstraBot's ShortcutBarContentEvent (ism) and refreshes (ite)."""
    bars: dict[int, dict[int, dict[str, Any]]] = {}
    for _, typ, value in _any_events(stream):
        if typ not in {"ism", "ite"}:
            continue
        fields = _fields(value)
        bar = next((int(v) for n, w, v in fields if n == 2 and w == 0), 0)
        entries = [blob for n, w, blob in fields if n == 1 and w == 2]
        if typ == "ism":
            bars[bar] = {}
        target = bars.setdefault(bar, {})
        for blob in entries:
            sf = _fields(blob)
            slot = next((int(v) for n, w, v in sf if n == 3 and w == 0), None)
            if slot is None:
                continue
            record: dict[str, Any] = {"bar": bar, "slot": slot, "kind": "unknown"}
            for number, wire, payload in sf:
                if wire != 2:
                    continue
                inner = _fields(payload)
                value_id = next((int(v) for _, w, v in inner if w == 0), None)
                if number == 2:
                    # Both shortcut variants share the obfuscated envelope.
                    # Item shortcuts expose a second scalar gid; a single
                    # nested ID is a spell shortcut in the current client.
                    gid = next((int(v) for n, w, v in inner if n == 2 and w == 0), None)
                    if gid is None:
                        record.update(kind="spell", spell_id=value_id)
                    else:
                        record.update(kind="item", uid=value_id, gid=gid)
            target[slot] = record
    return [entry for bar in sorted(bars) for _, entry in sorted(bars[bar].items())]


def parse_streams(stream: bytes, client_stream: bytes = b"", packets: int = 0) -> NetworkScan:
    """Decode already reassembled server/client streams.

    The passive pcap reader and each managed local proxy bridge use the exact
    same reducer.  Keeping this boundary byte-stream based means account
    routing never needs to fake PCAP headers or merge separate clients.
    """
    map_id, map_events, interactives, stated_elements = _map_events(stream)
    groups: list[NetworkGroup] = []
    players: list[NetworkPlayer] = []
    for value in _any_values(stream, b"jnj"):
        for number, wire, actor in _fields(value):
            if number != 3 or wire != 2:
                continue
            actor_fields = _fields(actor)
            ids = [v for n, w, v in actor_fields if n == 1 and w == 0]
            position = next((v for n, w, v in actor_fields if n == 2 and w == 2), b"")
            cells = [v for n, w, v in _fields(position) if n == 3 and w == 0]
            if not ids:
                continue
            entity_id = _signed(ids[0])
            cell = cells[0] if cells else None
            kind, role_payload = _actor_role(actor)
            if kind == "monster_group" and role_payload is not None:
                groups.append(NetworkGroup(entity_id, cell, _monster_members(role_payload)))
            elif kind == "player":
                record = _actor_record(actor)
                players.append(NetworkPlayer(entity_id, record.get("name") if record else None, cell))
    # jnj is emitted for every map visited during a capture.  Keeping every
    # decoded record makes mobs from earlier maps appear to accumulate in the
    # dashboard.  The last map snapshot is the authoritative current map;
    # retain only actors present there and apply any subsequent movement events
    # to their displayed cells.
    snapshots = [e for e in map_events if e.get("kind") == "map_snapshot"]
    if snapshots:
        latest = max(snapshots, key=lambda e: int(e.get("offset", 0)))
        latest_offset = int(latest.get("offset", 0))
        interactives = [item for item in interactives if int(item.get("_offset", -1)) >= latest_offset]
        stated_elements = [item for item in stated_elements if int(item.get("_offset", -1)) >= latest_offset]
        for item in interactives:
            item.pop("_offset", None)
        for item in stated_elements:
            item.pop("_offset", None)
        current: dict[int, dict[str, Any]] = {}
        for actor in latest.get("actors", []):
            actor_id = actor.get("entity_id")
            if actor_id is not None:
                current[int(actor_id)] = dict(actor)
        # jns/jnz after the map load update positions without replacing the
        # complete actor list.  They must not resurrect actors from an older
        # map, so only update IDs already in the latest snapshot.
        for event in map_events:
            if int(event.get("offset", 0)) < latest_offset:
                continue
            if event.get("kind") == "actors_snapshot":
                for actor in event.get("actors", []):
                    actor_id = actor.get("entity_id")
                    if actor_id is not None and int(actor_id) in current:
                        current[int(actor_id)]["cell"] = actor.get("cell")
            elif event.get("kind") == "movement":
                actor_id = event.get("actor_id")
                if actor_id is not None and int(actor_id) in current:
                    current[int(actor_id)]["cell"] = event.get("end_cell")
        # Rebuild from the latest snapshot itself.  Entity IDs are local to a
        # map context and can be reused; filtering an all-session list by ID
        # makes a previous map's monster appear where a current NPC stands.
        groups = []
        players = []
        for actor in current.values():
            actor_id = int(actor["entity_id"])
            if actor.get("kind") == "monster_group":
                raw_members = (actor.get("metadata") or {}).get("members", [])
                members = [NetworkMember(
                    int(member["monster_id"]), member.get("raw_field_4"), member.get("name"),
                    list(member.get("levels") or []), member.get("level_override"),
                    list(member.get("xp_levels") or []),
                ) for member in raw_members]
                groups.append(NetworkGroup(actor_id, actor.get("cell"), members))
            elif actor.get("kind") == "player":
                players.append(NetworkPlayer(actor_id, actor.get("name"), actor.get("cell")))
    fight_monsters = _fight_monsters(stream)
    fight_players = _fight_players(stream)
    fight_events = _fight_events(stream)
    character = _character_stats(stream)
    inventory, jobs = _inventory_and_jobs(stream)
    dialog = _dialog(stream)
    spells = _spells(stream)
    shortcuts = _shortcuts(stream)
    quests = _quests(stream)
    achievements = _achievements(stream)
    _join_character_actor(character, players, fight_players)
    protocol_events: list[NetworkProtocolEvent] = []
    for offset, alias, value in _any_events(stream):
        scalars: dict[str, list[int]] = {}
        for number, wire, raw in _fields(value):
            if wire == 0:
                scalars.setdefault(str(number), []).append(_signed(raw))
        protocol_events.append(NetworkProtocolEvent(offset, alias, len(value), scalars))
    return NetworkScan(groups=groups, players=players, packets=packets, bytes=len(stream),
                       fight_monsters=fight_monsters, fight_players=fight_players,
                       placement_moves=_placement_moves(client_stream), fight_map_id=_fight_map_id(client_stream),
                       fight_events=fight_events, map_id=map_id, map_events=map_events,
                       interactives=interactives, stated_elements=stated_elements,
                       character=character, protocol_events=protocol_events,
                       inventory=inventory, jobs=jobs, dialog=dialog, spells=spells, shortcuts=shortcuts,
                       quests=quests, achievements=achievements)


def parse_capture(path: str | Path) -> NetworkScan:
    """Decode the one passive capture stream (legacy/read-only path)."""
    stream, client_stream, packets = _pcap_streams(Path(path))
    return parse_streams(stream, client_stream, packets)


def scan_json(scan: NetworkScan) -> dict[str, Any]:
    return {
        "packets": scan.packets,
        "bytes": scan.bytes,
        "groups": [{"entity_id": g.entity_id, "cell": g.cell,
                     "members": [{"monster_id": m.monster_id, "name": m.name,
                                   "grade": m.grade, "level": m.level,
                                   "levels": m.levels, "raw_field_4": m.raw_field_4, "xp": m.xp} for m in g.members]}
                    for g in scan.groups],
        "players": [{"entity_id": p.entity_id, "name": p.name, "cell": p.cell} for p in scan.players],
        "fight_monsters": [{"monster_id": m.monster_id, "name": m.name,
                            "grade": m.grade, "level": m.level, "cell": m.cell} for m in scan.fight_monsters],
        "fight_players": [{"entity_id": p.entity_id, "name": p.name, "cell": p.cell} for p in scan.fight_players],
        "fight_events": scan.fight_events,
        "placement_moves": scan.placement_moves,
        "fight_map_id": scan.fight_map_id,
        "fight": _fight_snapshot(scan.fight_events, scan.fight_monsters, scan.fight_players, scan.fight_map_id),
        "map_id": scan.map_id,
        "map_events": scan.map_events,
        "interactives": scan.interactives,
        "stated_elements": scan.stated_elements,
        "character": {
            "level": scan.character.level,
            "name": scan.character.name,
            "experience": scan.character.experience,
            "experience_next": scan.character.experience_next,
            "hp": scan.character.hp,
            "hp_max": scan.character.hp_max,
            "pa": scan.character.pa,
            "pm": scan.character.pm,
            "entity_id": scan.character.entity_id,
            "cell": scan.character.cell,
        },
        "dialog": None if scan.dialog is None else {
            "actor_id": scan.dialog.actor_id, "message_id": scan.dialog.message_id,
            "params": scan.dialog.params, "replies": scan.dialog.replies, "stages": scan.dialog.stages,
        },
        "spells": scan.spells,
        "shortcuts": scan.shortcuts,
        "quests": scan.quests,
        "achievements": scan.achievements,
        "inventory": {
            "kamas": scan.inventory.kamas,
            "weight": scan.inventory.weight,
            "weight_max": scan.inventory.weight_max,
            "objects": scan.inventory.objects,
        },
        "jobs": [{"job_id": j.job_id, "level": j.level, "xp": j.xp,
                  "xp_floor": j.xp_floor, "xp_next": j.xp_next} for j in scan.jobs],
        # Keep the complete server message inventory visible to the UI/debugger.
        # Decoded reducers consume selected aliases; unknown aliases remain
        # inspectable by size and scalar fields instead of being discarded.
        "protocol_events": [{"offset": e.offset, "alias": e.alias, "size": e.size,
                              "scalar_fields": e.scalar_fields} for e in scan.protocol_events],
    }
