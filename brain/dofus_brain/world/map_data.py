"""AstraBot-compatible local Dofus map archive reader.

The archive is expected to contain ``map/map_<id>.json`` with 560 ordered
``cellsData`` entries. No terrain is guessed when the archive is absent.
"""
from __future__ import annotations

import json
import io
import os
import re
import struct
from pathlib import Path
from zipfile import ZipFile

try:
    import UnityPy
except ImportError:  # pragma: no cover - optional on non-game machines
    UnityPy = None


class MapAssetProvider:
    def __init__(self, archive: str | Path | None = None):
        configured = archive or os.environ.get("DOFUS_MAPS_ARCHIVE")
        self.path = Path(configured) if configured else Path(__file__).resolve().parents[2] / "data" / "maps.zip"
        self._zip: ZipFile | None = None
        self.bundle_dir = Path(os.environ.get(
            "DOFUS_MAP_BUNDLES",
            "/Applications/Ankama/Dofus-dofus3/DofusContent/Content/Map/Data",
        ))
        self._bundle_for_map: dict[int, Path] = {}
        self._unity_cache: dict[int, dict] = {}
        self._positions: dict[int, dict] | None = None

    def position(self, map_id: int) -> dict | None:
        """Resolve a network map ID through the installed map-position asset."""
        if self._positions is None:
            self._positions = {}
            source = self.bundle_dir.parents[1] / "Data" / "data_assets_mapsinformationdataroot.asset.bundle"
            if UnityPy is None or not source.is_file():
                return None
            try:
                UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0f1"
                env = UnityPy.load(str(source))
                for obj in env.objects:
                    if obj.type.name != "MonoBehaviour" or obj.peek_name() != "MapsInformationDataRoot":
                        continue
                    raw = obj.parse_as_dict(); refs = {x.get("rid"): x.get("data", {}) for x in raw.get("references", {}).get("RefIds", [])}
                    entries = raw.get("objectsById", {})
                    for ident, ref in zip(entries.get("m_keys", []), entries.get("m_values", []), strict=False):
                        data = refs.get(ref.get("rid"), {}) if isinstance(ref, dict) else {}
                        if "posX" in data and "posY" in data:
                            self._positions[int(ident)] = {"x": int(data["posX"]), "y": int(data["posY"]), "sub_area_id": data.get("subAreaId"), "world_map": data.get("worldMap")}
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        return self._positions.get(int(map_id))

    def _open(self) -> ZipFile | None:
        if self._zip is not None:
            return self._zip
        if not self.path.is_file():
            return None
        self._zip = ZipFile(self.path)
        return self._zip

    def load(self, map_id: int) -> dict | None:
        archive = self._open()
        if archive is None:
            return self._load_unity_map(map_id)
        name = f"map/map_{int(map_id)}.json"
        try:
            raw = json.loads(archive.read(name).decode("utf-8"))
        except (KeyError, OSError, ValueError, UnicodeDecodeError):
            return None
        cells = raw.get("mapData", raw).get("cellsData", []) if isinstance(raw, dict) else []
        if not isinstance(cells, list) or len(cells) != 560:
            return None
        return raw

    def _load_unity_map(self, map_id: int) -> dict | None:
        if UnityPy is None or not self.bundle_dir.is_dir():
            return None
        if map_id in self._unity_cache:
            return self._unity_cache[map_id]
        bundle = self._bundle_for_map.get(map_id)
        if bundle is None:
            needle = str(int(map_id)).encode("ascii")
            for candidate in self.bundle_dir.glob("mapdata_assets_world_*.bundle"):
                try:
                    if needle in candidate.read_bytes():
                        bundle = candidate
                        self._bundle_for_map[map_id] = candidate
                        break
                except OSError:
                    continue
        if bundle is None:
            return None
        try:
            UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0f1"
            env = UnityPy.load(str(bundle))
            try:
                obj = env.container[f"map_{int(map_id)}.asset"]
            except KeyError:
                return None
            data = obj.read()
            cells = []
            for cell in getattr(data, "cellsData", []):
                cells.append({
                    "cellNumber": int(getattr(cell, "cellNumber", len(cells))),
                    "mov": int(getattr(cell, "mov", 0)), "los": int(getattr(cell, "los", 0)),
                    "altitude": int(getattr(cell, "altitude", getattr(cell, "floor", 0))),
                    "red": int(getattr(cell, "red", 0)), "blue": int(getattr(cell, "blue", 0)),
                })
            if len(cells) != 560:
                return None
            raw = {"mapData": {"cellsData": cells}, "_source_bundle": str(bundle)}
            # AstraBot joins an InteractiveElement's protocol id to this map
            # reference table for the authoritative cell/gfx.  It is stored
            # alongside the map rather than guessed from screen pixels.
            typed = data.object_reader.parse_as_dict()
            refs = {entry.get("rid"): entry.get("data", {})
                    for entry in typed.get("references", {}).get("RefIds", [])}
            raw["interactives"] = {
                int(ref.get("<interactiveId>k__BackingField")): {
                    "cell": ref.get("cellId"), "gfx_id": ref.get("gfxId"),
                }
                for ref in refs.values()
                if isinstance(ref, dict) and ref.get("<interactiveId>k__BackingField") is not None
            }
            self._unity_cache[map_id] = raw
            return raw
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def terrain(self, map_id: int) -> dict | None:
        raw = self.load(map_id)
        if raw is None:
            return None
        cells = raw.get("mapData", raw).get("cellsData", [])
        blocked: list[int] = []
        red: list[int] = []
        blue: list[int] = []
        los_blocked: list[int] = []
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                continue
            # The asset's cellNumber is the Dofus protocol id.  Do not use
            # the array position: Unity/JSON map assets are allowed to store
            # cells in a different order (and AstraBot keys them by this
            # explicit number).
            cell_id = int(cell.get("cellNumber", index))
            if not 0 <= cell_id < 560:
                continue
            if int(cell.get("mov", 0)) == 0:
                blocked.append(cell_id)
            if int(cell.get("los", 0)) == 0:
                los_blocked.append(cell_id)
            if int(cell.get("red", 0)):
                red.append(cell_id)
            if int(cell.get("blue", 0)):
                blue.append(cell_id)
        return {"map_id": int(map_id), "walkable": 560 - len(blocked),
                "walkable_cells": [i for i in range(560) if i not in set(blocked)],
                "blocked": blocked, "holes": [], "los_blocked": los_blocked,
                "red": red, "blue": blue, "source": "map-asset",
                "source_path": str(raw.get("_source_bundle", self.path))}

    def interactive_ref(self, map_id: int, element_id: int) -> dict | None:
        raw = self.load(map_id)
        if raw is None:
            return None
        item = raw.get("interactives", {}).get(int(element_id))
        return dict(item) if isinstance(item, dict) else None


class GameDataProvider:
    """Small local-data joiner, matching AstraBot's DataReader + I18N flow."""

    def __init__(self, data_dir: str | Path | None = None, i18n_path: str | Path | None = None):
        content = Path("/Applications/Ankama/Dofus-dofus3/DofusContent/Content")
        self.data_dir = Path(data_dir) if data_dir else content / "Data"
        self.i18n_path = Path(i18n_path) if i18n_path else content / "I18n" / "fr.bin"
        self._tables: dict[str, dict[int, dict]] = {}
        self._i18n: dict[int, str] | None = None

    def _table(self, stem: str) -> dict[int, dict]:
        if stem in self._tables:
            return self._tables[stem]
        out: dict[int, dict] = {}
        path = self.data_dir / f"data_assets_{stem}dataroot.asset.bundle"
        if UnityPy is None or not path.is_file():
            self._tables[stem] = out
            return out
        try:
            UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0f1"
            obj = next(o for o in UnityPy.load(str(path)).objects if o.type.name == "MonoBehaviour")
            raw = obj.parse_as_dict()
            refs = {entry.get("rid"): entry.get("data", {}) for entry in raw.get("references", {}).get("RefIds", [])}
            entries = raw.get("objectsById", {})
            for ident, ref in zip(entries.get("m_keys", []), entries.get("m_values", []), strict=False):
                data = refs.get(ref.get("rid"), {}) if isinstance(ref, dict) else {}
                if isinstance(data, dict):
                    out[int(ident)] = data
        except (OSError, ValueError, TypeError, AttributeError, StopIteration):
            pass
        self._tables[stem] = out
        return out

    def _texts(self) -> dict[int, str]:
        if self._i18n is not None:
            return self._i18n
        out: dict[int, str] = {}
        try:
            raw = self.i18n_path.read_bytes()
            offset = 0
            language_size = raw[offset]; offset += 1 + language_size
            count = struct.unpack_from("<I", raw, offset)[0]; offset += 4
            entries: list[tuple[int, int]] = []
            for _ in range(count):
                ident, cursor = struct.unpack_from("<II", raw, offset); offset += 8
                entries.append((ident, cursor))
            ui_count = struct.unpack_from("<I", raw, offset)[0]; offset += 4
            for _ in range(ui_count):
                ident = struct.unpack_from("<Q", raw, offset)[0]; offset += 8
                cursor = struct.unpack_from("<I", raw, offset)[0]; offset += 4
                entries.append((ident, cursor))
            for ident, cursor in entries:
                size = shift = value = 0
                while cursor + size < len(raw):
                    byte = raw[cursor + size]; value |= (byte & 0x7F) << shift; size += 1; shift += 7
                    if byte < 0x80:
                        start = cursor + size
                        out[int(ident)] = raw[start:start + value].decode("utf-8", "replace")
                        break
        except (OSError, IndexError, struct.error, UnicodeDecodeError):
            pass
        self._i18n = out
        return out

    def npc_name(self, npc_id: int) -> str | None:
        npc = self._table("npcs").get(int(npc_id), {})
        name_id = npc.get("nameId")
        return self._texts().get(int(name_id)) if name_id is not None else None

    def subarea_info(self, subarea_id: int) -> dict:
        """Resolve the map's human region names from Dofus local data."""
        subarea = self._table("subareas").get(int(subarea_id), {})
        area = self._table("areas").get(int(subarea.get("areaId", -1)), {})
        return {
            "subarea_name": self._texts().get(int(subarea["nameId"])) if subarea.get("nameId") is not None else None,
            "area_name": self._texts().get(int(area["nameId"])) if area.get("nameId") is not None else None,
        }

    def spell_info(self, spell_id: int, level: int | None = None) -> dict:
        spell = self._table("spells").get(int(spell_id), {})
        name_id = spell.get("nameId")
        description_id = spell.get("descriptionId")
        levels = self._table("spelllevels")
        selected = next((levels.get(int(level_id), {}) for level_id in spell.get("spellLevels", [])
                         if level is None or levels.get(int(level_id), {}).get("grade") == level), {})
        effects = []
        for effect in selected.get("effects", []):
            if not isinstance(effect, dict):
                continue
            zone = effect.get("zoneDescr") or {}
            effects.append({
                "action_id": effect.get("actionId"), "element": effect.get("effectElement"),
                "min": effect.get("diceNum"), "max": effect.get("diceSide"),
                "value": effect.get("value"), "duration": effect.get("duration"),
                "target_mask": effect.get("targetMask"),
                "zone": {"shape": zone.get("shape"), "param1": zone.get("param1"), "param2": zone.get("param2")},
            })
        return {"spell_id": int(spell_id), "name": self._texts().get(int(name_id)) if name_id is not None else None,
                "description": self._texts().get(int(description_id)) if description_id is not None else None,
                "icon_id": spell.get("iconId"), "ap_cost": selected.get("apCost"),
                "min_range": selected.get("minRange"), "range": selected.get("range"),
                # AstraBot's spell flag semantics: 1=line-only, 2=diagonal,
                # 4=requires line of sight, 64=range modifiable by character PO.
                "cast_in_line": bool(int(selected.get("m_flags", 0)) & 1),
                "cast_in_diagonal": bool(int(selected.get("m_flags", 0)) & 2),
                "requires_los": bool(int(selected.get("m_flags", 0)) & 4),
                "range_modifiable": bool(int(selected.get("m_flags", 0)) & 64),
                "critical_hit_probability": selected.get("criticalHitProbability"),
                "max_cast_turn": selected.get("maxCastPerTurn"),
                "max_cast_target": selected.get("maxCastPerTarget"),
                "max_global_cast_turn": selected.get("maxGlobalCastPerTurn"),
                "min_cast_interval": selected.get("minCastInterval"),
                "initial_cooldown": selected.get("initialCooldown"),
                "global_cooldown": selected.get("globalCooldown"),
                "min_player_level": selected.get("minPlayerLevel"), "effects": effects}

    def quest_info(self, quest_id: int, step_id: int | None = None) -> dict:
        """Resolve quest/step IDs using the same local data tables as AstraBot."""
        quest = self._table("quests").get(int(quest_id), {})
        selected_step = int(step_id) if step_id is not None else next(iter(quest.get("stepIds", [])), None)
        step = self._table("queststeps").get(selected_step, {}) if selected_step is not None else {}
        text = self._texts()
        return {"quest_id": int(quest_id), "name": text.get(int(quest["nameId"])) if quest.get("nameId") is not None else None,
                "level_min": quest.get("levelMin"), "step_id": selected_step,
                "step_name": text.get(int(step["nameId"])) if step.get("nameId") is not None else None,
                "step_description": text.get(int(step["descriptionId"])) if step.get("descriptionId") is not None else None,
                "objective_ids": list(step.get("objectiveIds", []))}

    def search_quests(self, query: str = "", limit: int = 20) -> list[dict]:
        """Search the installed Dofus quest database without network access."""
        needle = query.strip().casefold()
        limit = max(1, min(int(limit), 100))
        texts = self._texts()
        matches: list[dict] = []
        for quest_id, quest in self._table("quests").items():
            name = texts.get(int(quest["nameId"])) if quest.get("nameId") is not None else None
            if needle and needle not in str(quest_id) and needle not in (name or "").casefold():
                continue
            matches.append({"quest_id": int(quest_id), "name": name,
                            "level_min": quest.get("levelMin"), "level_max": quest.get("levelMax"),
                            "step_count": len(quest.get("stepIds", []))})
            if len(matches) >= limit:
                break
        return matches

    def quest_record(self, quest_id: int) -> dict:
        """Return a full local quest record suitable for a future planner/tool."""
        quest = self._table("quests").get(int(quest_id), {})
        if not quest:
            return {"quest_id": int(quest_id), "found": False}
        base = self.quest_info(int(quest_id))
        steps = []
        for step_id in quest.get("stepIds", []):
            step = self.quest_info(int(quest_id), int(step_id))
            step["objectives"] = [self.quest_objective_info(int(objective_id))
                                  for objective_id in step.get("objective_ids", [])]
            steps.append(step)
        base.update({"found": True, "level_max": quest.get("levelMax"), "repeat_type": quest.get("repeatType"),
                     "is_dungeon": bool(quest.get("isDungeonQuest")), "is_party": bool(quest.get("isPartyQuest")),
                     "followable": bool(quest.get("followable")), "start_positions": list(quest.get("startPosition", [])),
                     "steps": steps})
        return base

    @staticmethod
    def _criterion_quest_ids(criterion: str | None) -> list[int]:
        """Extract explicit completed-quest requirements from an Ankama criterion.

        The raw criterion is retained as well: it can include ORs, level checks
        and event flags that cannot safely be reduced without live character
        state. ``Qf=<id>`` is nevertheless a stable enough atom to build the
        prerequisite and unlock graph used by quest guides.
        """
        return sorted({int(value) for value in re.findall(r"Qf=([0-9]+)", criterion or "")})

    def _quest_summary(self, quest_id: int) -> dict:
        quest = self._table("quests").get(int(quest_id), {})
        name_id = quest.get("nameId")
        return {
            "quest_id": int(quest_id),
            "name": self._texts().get(int(name_id)) if name_id is not None else None,
            "level_min": quest.get("levelMin"),
            "level_max": quest.get("levelMax"),
            "start_criterion": quest.get("startCriterion") or "",
            "start_positions": list(quest.get("startPosition") or []),
        }

    def _achievement_summary(self, achievement_id: int) -> dict:
        achievement = self._table("achievements").get(int(achievement_id), {})
        category = self._table("achievementcategories").get(int(achievement.get("categoryId", -1)), {})
        texts = self._texts()
        name_id, category_name_id = achievement.get("nameId"), category.get("nameId")
        return {
            "achievement_id": int(achievement_id),
            "name": texts.get(int(name_id)) if name_id is not None else None,
            "category_id": achievement.get("categoryId"),
            "category": texts.get(int(category_name_id)) if category_name_id is not None else None,
            "points": achievement.get("points"),
            "level": achievement.get("level"),
        }

    def quest_graph(self, quest_id: int) -> dict:
        """Return local prerequisite, unlock and achievement relationships."""
        target = self._table("quests").get(int(quest_id))
        if not target:
            return {"quest_id": int(quest_id), "found": False}

        criterion = str(target.get("startCriterion") or "")
        prerequisites = self._criterion_quest_ids(criterion)
        unlocks: list[dict] = []
        for candidate_id, candidate in self._table("quests").items():
            if int(quest_id) in self._criterion_quest_ids(str(candidate.get("startCriterion") or "")):
                unlocks.append(self._quest_summary(candidate_id))
        unlocks.sort(key=lambda item: (item["level_min"] is None, item["level_min"] or 0, item["quest_id"]))

        success_groups: list[dict] = []
        objectives = self._table("achievementobjectives")
        for achievement_id, achievement in self._table("achievements").items():
            grouped_quests: list[int] = []
            for objective_id in achievement.get("objectiveIds") or []:
                objective = objectives.get(int(objective_id), {})
                grouped_quests.extend(self._criterion_quest_ids(str(objective.get("criterion") or "")))
            grouped_quests = sorted(set(grouped_quests))
            if int(quest_id) not in grouped_quests:
                continue
            group = self._achievement_summary(achievement_id)
            group["quests"] = [self._quest_summary(item) for item in grouped_quests]
            success_groups.append(group)
        success_groups.sort(key=lambda item: (str(item.get("category") or ""), str(item.get("name") or ""), item["achievement_id"]))

        return {
            "quest": self._quest_summary(int(quest_id)),
            "found": True,
            "prerequisites": [self._quest_summary(item) for item in prerequisites],
            "unlocks": unlocks,
            "success_groups": success_groups,
        }

    def quest_objective_info(self, objective_id: int) -> dict:
        """Resolve a live QuestObjective without turning it into screen text.

        The objective's static target/map parameters stay machine-readable for
        a planner, while the objective type is resolved through the same local
        I18N table used everywhere else.
        """
        objective = self._table("questobjectives").get(int(objective_id), {})
        objective_type = self._table("questobjectivetypes").get(int(objective.get("typeId", -1)), {})
        parameters = objective.get("parameters") or {}
        coords = objective.get("coords") or {}
        x, y = coords.get("x"), coords.get("y")
        type_name = self._texts().get(int(objective_type["nameId"])) if objective_type.get("nameId") is not None else None
        raw_parameters = {key: value for key, value in parameters.items()
                          if key.startswith("parameter") and value not in (None, 0)}
        # Quest objective templates use #1, #2 … for their stored parameters.
        # Resolve only unambiguous local entities; retain the numeric fallback
        # where the type's parameter semantics are not known yet.
        parameter_labels: dict[str, str] = {}
        prefers_npc = bool(type_name and any(word in type_name.casefold() for word in ("voir", "parler", "dialogue")))
        for key, value in raw_parameters.items():
            value = int(value)
            npc = self.npc_name(value)
            item = self.item_info(value).get("name")
            monster = self._table("monsters").get(value, {})
            monster_name = self._texts().get(int(monster["nameId"])) if monster.get("nameId") is not None else None
            # Only prefer NPC IDs for an explicitly conversational template.
            # Small quantities (for example the `1` in “Craft 1 item”) often
            # collide with perfectly valid NPC IDs, so generic objectives must
            # not use NPC names as a fallback.
            label = (npc if prefers_npc else item) or (item or monster_name if not prefers_npc else None)
            if label:
                parameter_labels[key] = str(label)
        description = type_name
        if description:
            for index in range(5):
                key = f"parameter{index}"
                # Plain small values are quantities; larger unresolved values
                # are internal game references. Keep those references in the
                # machine-readable ``parameters`` field, not in human text.
                raw_value = raw_parameters.get(key)
                value = parameter_labels.get(key) or (str(raw_value) if isinstance(raw_value, int) and raw_value <= 99 else None)
                if value is not None:
                    description = description.replace(f"#{index + 1}", value)
        return {
            "objective_id": int(objective_id),
            "step_id": objective.get("stepId"),
            "type_id": objective.get("typeId"),
            "type_name": type_name,
            "description": description,
            "dialog_id": objective.get("dialogId"),
            "map_id": objective.get("mapId"),
            "coords": {"x": x, "y": y} if x not in (None, -2147483648) and y not in (None, -2147483648) else None,
            "parameters": raw_parameters,
            "parameter_labels": parameter_labels,
        }

    def dialog_text(self, message_id: int, params: list[str] | None = None) -> str | None:
        text = self._texts().get(int(message_id))
        if text is None:
            return None
        for index, param in enumerate(params or [], start=1):
            text = text.replace(f"#{index}", param).replace(f"{{{index - 1}}}", param)
        return text

    def npc_reply_text(self, npc_id: int, reply_id: int) -> str | None:
        npc = self._table("npcs").get(int(npc_id), {})
        for entry in npc.get("dialogReplies", []):
            values = entry.get("values", []) if isinstance(entry, dict) else []
            if len(values) >= 2 and int(values[0]) == int(reply_id):
                return self._texts().get(int(values[1]))
        return None

    def item_info(self, item_id: int) -> dict:
        """AstraBot-compatible item/I18N join for inventory and equipment."""
        item = self._table("items").get(int(item_id), {})
        name_id = item.get("nameId")
        return {
            "gid": int(item_id),
            "name": self._texts().get(int(name_id)) if name_id is not None else None,
            "type_id": item.get("typeId"),
            "level": item.get("level"),
            "icon_id": item.get("iconId"),
        }

    def describe_interactive(self, element_id: int, gfx_id: int | None = None,
                             skills: list[int] | None = None) -> dict:
        """Join a live interactive's skill IDs to local resource/job data."""
        interactive = self._table("interactives").get(int(element_id), {})
        name_id = interactive.get("nameId")
        name = self._texts().get(int(name_id)) if name_id is not None else None
        out: dict = {"name": name, "gfx_id": gfx_id, "kind": "interactive"}
        for skill_id in skills or []:
            skill = self._table("skills").get(int(skill_id), {})
            resource_id = skill.get("gatheredRessourceItem")
            if resource_id in (None, -1, 0):
                continue
            resource = self._table("items").get(int(resource_id), {})
            job_id = skill.get("parentJobId")
            job = self._table("jobs").get(int(job_id), {}) if job_id is not None else {}
            job_name_id = job.get("nameId")
            out.update({
                "kind": "resource",
                "resource_name": (self._texts().get(int(resource["nameId"]))
                                  if resource.get("nameId") is not None else None) or f"resource {resource_id}",
                "resource_id": int(resource_id), "job_id": job_id,
                "icon_id": resource.get("iconId"),
                "job_name": self._texts().get(int(job_name_id)) if job_name_id is not None else None,
                "level_min": skill.get("levelMin"), "skill_id": int(skill_id),
            })
            break
        return out


class GameIconProvider:
    """Serve Dofus's own item sprites directly from the installed bundles.

    The dashboard never copies the game's icon archive into this repository;
    this provider exposes only a requested PNG and keeps the Unity bundle open
    in memory after the first request.
    """
    def __init__(self, content: str | Path | None = None):
        base = Path(content) if content else Path("/Applications/Ankama/Dofus-dofus3/DofusContent/Content")
        self.path = base / "Picto" / "Items" / "item_assets_1x.bundle"
        self.spell_path = base / "Picto" / "Spells" / "spell_assets_1x.bundle"
        self._env = None
        self._spell_env = None

    def item_png(self, item_id: int) -> bytes | None:
        return self._png("item", item_id)

    def spell_png(self, icon_id: int) -> bytes | None:
        return self._png("spell", icon_id)

    def _png(self, kind: str, icon_id: int) -> bytes | None:
        path = self.path if kind == "item" else self.spell_path
        attr = "_env" if kind == "item" else "_spell_env"
        if UnityPy is None or not path.is_file():
            return None
        try:
            if getattr(self, attr) is None:
                UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0f1"
                setattr(self, attr, UnityPy.load(str(path)))
            env = getattr(self, attr)
            name = f"Assets/BuiltAssets/items/1x/{int(icon_id)}.png" if kind == "item" else f"Assets/BuiltAssets/spells/1x/sort_{int(icon_id)}.png"
            obj = env.container.container_dict.get(name)
            if obj is None:
                return None
            image = obj.read().image
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
        except (OSError, ValueError, TypeError, AttributeError):
            return None
