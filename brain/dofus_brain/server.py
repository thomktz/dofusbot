"""Backend server: FastAPI + uvicorn. `python -m dofus_brain.server [--mock] [--host] [--port 8765]`.

GET /health, WebSocket /ws implementing protocol §2 (both directions), multiple clients.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import getpass
import io
import json
import logging
import os
import random
import shlex
import subprocess
import shutil
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from fastapi import Body, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect

from . import PROTOCOL_VERSION, __version__
from .agent import Agent
from .bus import Bus
from .config import BrainConfig
from .corpus import CorpusRecorder
from .llm import LLMClient, make_llm
from .llm.coordinator import SharedLLMCoordinator
from .mock_control import make_controller
from .ocr import make_ocr
from .perception.perceiver import Perceiver
from .planner import PLANNERS, Planner
from .slot import SlotManager
from .util import now_ms
from .network_scan import parse_capture, parse_streams, scan_json
from .paths import brain_home
from .world.map_data import GameDataProvider, MapAssetProvider
from .zaap_emulator import ZaapEmulator
from .ankama_auth import authenticate as authenticate_ankama, load as load_ankama_auth
from .astra_codec import AstraCodec
from .managed_proxy import ManagedConnectionProxy
from .managed_transport import ManagedTransport
from .macos_connection_redirect import DirectProcess, RedirectedProcess, spawn_with_redirect, spawn_without_injection
from .world.orchestrator import LocalWorldOrchestrator, WorldIntent, WorldToolRegistry
from .world.state import WorldState
from .guide_resolver import GuideResolver, GuideUnavailable
from .world.quest_planner import QuestPlanner
from .world.world_route import WorldGraphUnavailable, WorldRouteProvider
from .world.roleplay_path import find_roleplay_path
from .world.interaction import plan_interactive
from .world.interaction_execution import InteractiveExecutor
from .world.dialogue_execution import DialogueExecutor
from .world.navigation_execution import NavigationExecutor
from .world.combat_execution import CombatExecutor
from .world.inventory_execution import InventoryExecutor
from .world.attack_execution import AttackExecutor
from .world.talk_execution import TalkExecutor
from .world.interruptions import Interruption, InterruptionKind, InterruptionPolicy
from .world.account_manager import AccountManager
from .world.account_profiles import AccountProfileStore
from .world.client_lifecycle import ClientLifecycleManager
from .world.account_scheduler import AccountScheduler
from .world.quest_execution import QuestExecutor
from .world.inventory_policy import InventoryPolicy
from .world.session_plan import SessionPlanStore
from .world.session_plan import SessionPlan
from .world.goal_runner import AgentGoalRun
from .world.offline_planning import farming_candidates, pack_assessment, quest_preparation
from .world.action_trace import ActionTrace
from .world.recovery_guard import RecoveryGuard

log = logging.getLogger("dofus_brain.server")


def map_exit_click_point(grid: Any, direction: str, player_cell: int | None,
                         cells: dict[str, Any] | None = None,
                         gaussian: Any = random.gauss) -> tuple[int, float, float]:
    """Choose a walkable border exit and click one grid step beyond it.

    The endpoint is outside the tactical grid: that is how the Dofus client is
    asked to change maps.  A small clipped normal offset along the border keeps
    repeated movement natural while retaining the shortest available route.
    """
    edge = {"up": "top", "down": "bottom", "left": "left", "right": "right"}[direction]
    border = grid.edge_cells(edge)
    if not border:
        raise ValueError(f"no {direction} map exit cells available")
    state = cells or {}
    open_border = [cell for cell in border if (state.get(str(cell)) or {}).get("movable") is not False]
    candidates = open_border or border
    fastest = min(candidates, key=lambda cell: grid.distance(player_cell, cell) if player_cell is not None else abs(border.index(cell) - (len(border) - 1) / 2))
    desired_index = max(0, min(len(border) - 1, border.index(fastest) + round(float(gaussian(0, 1)))))
    cell = min(candidates, key=lambda candidate: (abs(border.index(candidate) - desired_index), grid.distance(player_cell, candidate) if player_cell is not None else 0))
    x, y = grid.center(cell)
    # Centre-to-centre spacing for the first off-map cell on that side.
    dx, dy = {"up": (0.0, -grid.ch / 2), "down": (0.0, grid.ch / 2), "left": (-grid.cw, 0.0), "right": (grid.cw, 0.0)}[direction]
    return cell, x + dx, y + dy


def dialog_reply_click_point(width: float, height: float, index: int, count: int) -> tuple[float, float]:
    """Centre of a visible Dofus dialogue reply row, in content coordinates.

    The replies are a vertically centred list in the dialogue modal.  This is
    deliberately geometry-only: choosing a reply must work without granting
    Screen Recording, just like talking to an NPC or changing maps.
    """
    if index < 0 or index >= count:
        raise ValueError("dialogue reply index is out of range")
    row = max(34.0, min(52.0, height * 0.043))
    centre_y = height * 0.60
    return width * 0.50, centre_y + (index - (count - 1) / 2) * row


class Client:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=2000)
        self.preview = False
        self.task: asyncio.Task | None = None

    def put(self, text: str) -> None:
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:  # slow client: drop oldest
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            self.queue.put_nowait(text)

    async def pump(self) -> None:
        while True:
            text = await self.queue.get()
            if text is None:
                return
            await self.ws.send_text(text)


def envelope(type_: str, payload: Any, id_: str | None = None) -> str:
    m: dict[str, Any] = {"type": type_, "ts": now_ms(), "payload": payload}
    if id_ is not None:
        m["id"] = id_
    return json.dumps(m, ensure_ascii=False, separators=(",", ":"))


@dataclass
class AccountExecutors:
    """Acknowledgement barriers belonging to exactly one account session."""
    interactive: InteractiveExecutor = field(default_factory=InteractiveExecutor)
    dialogue: DialogueExecutor = field(default_factory=DialogueExecutor)
    navigation: NavigationExecutor = field(default_factory=NavigationExecutor)
    combat: CombatExecutor = field(default_factory=CombatExecutor)
    inventory: InventoryExecutor = field(default_factory=InventoryExecutor)
    attack: AttackExecutor = field(default_factory=AttackExecutor)
    talk: TalkExecutor = field(default_factory=TalkExecutor)
    trace: ActionTrace = field(default_factory=ActionTrace)


class Hub:
    def __init__(self, agent: Agent, slot: SlotManager, bus: Bus, cfg: dict, mock: bool, llm: LLMClient):
        self.agent = agent
        self.slot = slot
        self.bus = bus
        self.cfg = cfg
        self.mock = mock
        self._llm_coordinator = SharedLLMCoordinator(llm)
        self.clients: set[Client] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: int | None = None
        self._state_last = 0.0
        self._state_scheduled = False
        self._status_scheduled = False
        self._preview_last = 0.0
        self._preview_busy = False
        self.armed = False                 # "Start" pressed: window aligned + kept in front, buttons primed
        self.npcs: list = []               # last "Locate NPCs" scan (npcs.Entity)
        self.npcs_coords = None
        self._planner_last: dict | None = None
        self._scan_lock = asyncio.Lock()
        self._network_proc: subprocess.Popen[bytes] | None = None
        self._network_monitor_task: asyncio.Task | None = None
        self._grid_samples: list[tuple[int, float, float]] = []
        self._calibration_task: asyncio.Task | None = None
        # This must be a stable shared path.  A listener started through
        # macOS's privileged osascript runs with a different TMPDIR from the
        # dashboard process; using tempfile.gettempdir() made a run.sh restart
        # look in a different folder and resurrect an old map/NPC snapshot.
        # Never reuse the old shared /tmp name: captures made by a former
        # root-run backend are 0600/root and permanently block the desktop
        # user. A per-user name also keeps independent local profiles honest.
        self._network_path = os.environ.get("DOFUS_NETWORK_CAPTURE", f"/tmp/dofus-agent-{getpass.getuser()}-live.pcap")
        self._network_archive_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "captures", "network"))
        self._terrain_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "brain", "data", "terrain-preview"))
        self._network_status: dict[str, Any] = {"state": "idle", "result": None, "error": None}
        # Reassembled per-account frame streams from managed local bridges.
        # They are bounded and separate from tcpdump's passive account-0
        # capture, so no dashboard account can inherit another one's world.
        self._proxy_streams: dict[int, dict[str, bytearray]] = {}
        self._proxy_parse_busy: set[int] = set()
        self._proxy_stream_limit = 8 * 1024 * 1024
        self._dofus_status: dict[str, Any] = {"state": "idle", "app": os.environ.get("DOFUS_APP", "Dofus"), "launcher": os.environ.get("ANKAMA_LAUNCHER_APP", "Ankama Launcher"), "error": None}
        # Only Astra-style socket sessions attach this transport.  Keeping it
        # separate from the passive packet recorder prevents a stale capture
        # from ever turning into an input action.
        self.account_manager = AccountManager()
        self.managed_transport = self.account_manager.primary_transport()
        self._managed_proxy: ManagedConnectionProxy | None = None  # default compatibility alias
        self._redirected_process: RedirectedProcess | DirectProcess | None = None
        self._zaap = ZaapEmulator(port=26116)
        self._profile_proxies: dict[str, ManagedConnectionProxy] = {}
        self._profile_processes: dict[str, RedirectedProcess | DirectProcess] = {}
        self._profile_zaaps: dict[str, ZaapEmulator] = {}
        self._auth_path = brain_home() / "config" / "ankama_auth.json"
        self._profile_store = AccountProfileStore(brain_home() / "config")
        self._client_lifecycle = ClientLifecycleManager()
        self._account_scheduler = AccountScheduler()
        self._quest_executor = QuestExecutor()
        self._inventory_policy = InventoryPolicy()
        self._session_plans = SessionPlanStore(brain_home() / "config" / "sessions")
        self._map_assets = MapAssetProvider()
        self._game_data = GameDataProvider()
        self._guides = GuideResolver()
        self._world_routes = WorldRouteProvider()
        self._executions: dict[int, AccountExecutors] = {}
        self._interruptions = InterruptionPolicy()
        self._recovery_guard = RecoveryGuard()
        self._processed_interruption_offsets: set[tuple[int, int]] = set()
        self._agent_goals: dict[int, AgentGoalRun] = {}
        self._agent_goal_busy: set[int] = set()
        self._load_latest_network_capture()
        bus.subscribe(self.on_bus)
        try:   # ctrl+alt+cmd+K / mouse yank: leave the window alone too
            agent.ctl.on_kill(lambda *a: self._threadsafe(self._disarm, "kill switch"))
        except Exception:
            pass

    def _load_latest_network_capture(self) -> None:
        """Hydrate the dashboard from the newest archived capture, if one exists.

        Captures are immutable evidence, so loading one is read-only and does not
        start tcpdump or touch the game. A malformed/incomplete archive is skipped
        rather than preventing the server from starting; the user can recapture.
        """
        try:
            # ``run.sh`` restarts the backend during development.  tcpdump's
            # active file is often newer than the last explicit Stop/archive,
            # and it is still a perfectly valid read-only snapshot once the
            # former process has exited.  Prefer the newest valid local file
            # so a restart cannot silently roll the dashboard back a map (and
            # reuse negative NPC entity IDs with old names).
            paths = [Path(self._network_path)] if Path(self._network_path).is_file() else []
            paths.extend(Path(p.path) for p in os.scandir(self._network_archive_dir)
                         if p.is_file() and p.name.endswith(".pcap"))
            candidates = sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)
        except (FileNotFoundError, OSError):
            return
        for entry in candidates:
            try:
                result = scan_json(parse_capture(str(entry)))
                result["capture_path"] = str(entry)
                self._attach_latest_terrain(result, include_saved_terrain=True)
                self._attach_map_asset(result)
                self._network_status = {"state": "ready", "result": result, "error": None}
                log.info("loaded latest network capture: %s", entry)
                return
            except Exception as exc:
                log.warning("skipping unreadable archived capture %s: %s", entry.path, exc)

    def _attach_latest_terrain(self, result: dict[str, Any], include_saved_terrain: bool = True) -> None:
        """Attach the latest saved placement-preview terrain to a replay result."""
        try:
            candidates = sorted(Path(self._terrain_dir).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if include_saved_terrain else []
            if candidates:
                with candidates[0].open(encoding="utf-8") as f:
                    terrain = json.load(f)
                if isinstance(terrain, dict) and isinstance(terrain.get("walkable"), int):
                    holes = {int(c) for c in terrain.get("holes", []) if isinstance(c, int)}
                    blocked = {int(c) for c in terrain.get("blocked", []) if isinstance(c, int)}
                    terrain["walkable_cells"] = [c for c in range(560) if c not in holes and c not in blocked]
                    terrain["source_path"] = str(candidates[0])
                    result["terrain"] = terrain
            from .world.state import WorldState
            result["world"] = WorldState.from_scan(result).to_json()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            log.warning("could not attach saved terrain preview: %s", exc)

    def _attach_map_asset(self, result: dict[str, Any]) -> None:
        """Use AstraBot-compatible map/map_<id>.json when locally available."""
        fight = result.get("fight") or {}
        map_id = fight.get("map_id") if fight.get("active") else result.get("map_id")
        if map_id is None:
            return
        try:
            terrain = self._map_assets.terrain(int(map_id))
            if terrain is not None:
                result["terrain"] = terrain
            position = self._map_assets.position(int(map_id))
            if position is not None:
                subarea_id = position.get("sub_area_id")
                if subarea_id is not None:
                    position.update({key: value for key, value in self._game_data.subarea_info(int(subarea_id)).items() if value})
                result["map_position"] = position
            # AstraBot's data path: role-play NPC ID -> NPC data -> I18N name;
            # interactive element ID -> current map reference (cell/gfx).  The
            # protocol supplies availability/state, so no screen OCR is used.
            for event in result.get("map_events", []):
                for actor in event.get("actors", []):
                    if actor.get("kind") != "npc":
                        continue
                    npc_id = (actor.get("metadata") or {}).get("npc_id")
                    if npc_id is not None:
                        actor["name"] = self._game_data.npc_name(int(npc_id)) or f"NPC {npc_id}"
            # Dialog messages identify the on-map entity, not the NPC template.
            # Resolve that entity through the current role-play snapshot, then join
            # the template's dialogue reply IDs to the same local I18N data used by
            # AstraBot.  This stays read-only: no protocol packets are injected.
            dialog = result.get("dialog")
            if isinstance(dialog, dict):
                actor_id = dialog.get("actor_id")
                npc_id = None
                for event in reversed(result.get("map_events", [])):
                    for actor in event.get("actors", []):
                        if actor.get("entity_id") == actor_id:
                            npc_id = (actor.get("metadata") or {}).get("npc_id")
                            break
                    if npc_id is not None:
                        break
                if npc_id is not None:
                    dialog["npc_id"] = int(npc_id)
                    dialog["npc_name"] = self._game_data.npc_name(int(npc_id)) or f"NPC {npc_id}"
                message_id = dialog.get("message_id")
                if message_id is not None:
                    dialog["text"] = self._game_data.dialog_text(int(message_id), dialog.get("params"))
                dialog["options"] = [
                    {"reply_id": int(reply_id), "text": self._game_data.npc_reply_text(int(npc_id), int(reply_id)) if npc_id is not None else None}
                    for reply_id in dialog.get("replies", [])
                ]
                # Preserve every question observed in this open dialog.  The
                # final stage remains the actionable one; earlier stages give
                # a future agent enough context to choose deterministically.
                for stage in dialog.get("stages", []):
                    stage_message = stage.get("message_id")
                    if stage_message is not None:
                        stage["text"] = self._game_data.dialog_text(int(stage_message), stage.get("params"))
                    stage["options"] = [
                        {"reply_id": int(reply_id), "text": self._game_data.npc_reply_text(int(npc_id), int(reply_id)) if npc_id is not None else None}
                        for reply_id in stage.get("replies", [])
                    ]
            states = {item.get("id"): item for item in result.get("stated_elements", [])}
            for item in result.get("interactives", []):
                element_id = item.get("id")
                if element_id is None:
                    continue
                reference = self._map_assets.interactive_ref(int(map_id), int(element_id)) or {}
                stated = states.get(element_id) or {}
                item["cell"] = stated.get("cell") if stated.get("cell") is not None else reference.get("cell")
                if stated.get("state") is not None:
                    item["state"] = stated["state"]
                description = self._game_data.describe_interactive(
                    int(element_id), reference.get("gfx_id"), list(item.get("skills") or []),
                )
                item.update({key: value for key, value in description.items() if value is not None})
            # Inventory/equipment packets use generic object IDs.  Join those
            # IDs to ItemsData/I18N once here, rather than teaching every UI or
            # future LLM tool how to traverse Unity data tables.
            inventory = result.get("inventory") or {}
            inventory_positions = {
                0: "Amulet", 1: "Weapon", 2: "Left ring", 3: "Belt",
                4: "Right ring", 5: "Boots", 6: "Hat", 7: "Cape",
                8: "Pet", 9: "Dofus 1", 10: "Dofus 2", 11: "Dofus 3",
                12: "Dofus 4", 13: "Dofus 5", 14: "Dofus 6", 15: "Shield",
                16: "Mount", 20: "Mutation", 21: "Boost food", 22: "Bonus 1",
                23: "Bonus 2", 24: "Malus 1", 25: "Malus 2", 26: "Roleplay buffer",
                27: "Follower", 28: "Entity", 29: "Ride harness", 30: "Costume",
                31: "Consumable", 32: "Dofus 7", 33: "Dofus 8", 34: "Dofus 9",
                35: "Dofus 10", 36: "Dofus 11", 37: "Dofus 12", 38: "Plastron",
                39: "Emblem", 40: "Talisman",
            }
            for obj in inventory.get("objects", []):
                gid = obj.get("gid")
                if gid is not None:
                    obj.update({key: value for key, value in self._game_data.item_info(int(gid)).items()
                                if value is not None})
                position = obj.get("position")
                if position == 63:
                    obj["container"] = "bag"
                    obj["position_name"] = "Bag"
                elif position in inventory_positions:
                    obj["container"] = "equipped"
                    obj["position_name"] = inventory_positions[position]
                else:
                    # Preserve unknown protocol positions explicitly; a future
                    # action planner must never silently treat them as bag items.
                    obj["container"] = "other"
                    obj["position_name"] = f"Position {position}" if position is not None else "Unknown position"
            for spell in result.get("spells", []):
                spell_id = spell.get("spell_id")
                if spell_id is not None:
                    spell.update({key: value for key, value in self._game_data.spell_info(int(spell_id), spell.get("level")).items()
                                  if value is not None})
            for shortcut in result.get("shortcuts", []):
                if shortcut.get("kind") == "item" and shortcut.get("gid") is not None:
                    shortcut.update({key: value for key, value in self._game_data.item_info(int(shortcut["gid"])).items() if value is not None})
                elif shortcut.get("kind") == "spell" and shortcut.get("spell_id") is not None:
                    shortcut.update({key: value for key, value in self._game_data.spell_info(int(shortcut["spell_id"])).items() if value is not None})
            for quest in (result.get("quests") or {}).get("active", []):
                quest_id = quest.get("quest_id")
                if quest_id is not None:
                    quest.update({key: value for key, value in self._game_data.quest_info(int(quest_id), quest.get("step_id")).items() if value is not None})
                for objective in quest.get("objectives", []):
                    objective_id = objective.get("objective_id")
                    if objective_id is not None:
                        objective.update({key: value for key, value in self._game_data.quest_objective_info(int(objective_id)).items() if value is not None})
            from .world.state import WorldState
            result["world"] = WorldState.from_scan(result).to_json()
        except (OSError, ValueError, TypeError):
            log.warning("could not load local map asset for %s", map_id)

    # ------------------------------------------------------------------ outgoing
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.loop_thread = threading.get_ident()

    def broadcast(self, type_: str, payload: Any, only_preview: bool = False) -> None:
        text = envelope(type_, payload)
        for c in list(self.clients):
            if not only_preview or c.preview:
                c.put(text)

    def _threadsafe(self, fn, *args) -> None:
        if self.loop is not None and threading.get_ident() != self.loop_thread:
            self.loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)

    def _execution(self, account_id: int | None = None) -> AccountExecutors:
        account_id = self.account_manager.active_account_id if account_id is None else int(account_id)
        return self._executions.setdefault(account_id, AccountExecutors())

    # Existing action handlers address the selected dashboard account. These
    # properties keep that call surface readable while storage remains fully
    # account-local; stream observers use ``_execution(account_id)`` directly.
    @property
    def _interactive_executor(self) -> InteractiveExecutor:
        return self._execution().interactive

    @property
    def _dialogue_executor(self) -> DialogueExecutor:
        return self._execution().dialogue

    @property
    def _navigation_executor(self) -> NavigationExecutor:
        return self._execution().navigation

    @property
    def _combat_executor(self) -> CombatExecutor:
        return self._execution().combat

    @property
    def _inventory_executor(self) -> InventoryExecutor:
        return self._execution().inventory

    @property
    def _attack_executor(self) -> AttackExecutor:
        return self._execution().attack

    @property
    def _talk_executor(self) -> TalkExecutor:
        return self._execution().talk

    def planner_payload(self) -> dict | None:
        st = getattr(self.agent.planner, "status", None)
        if not callable(st):            # e.g. the noop planner: still tell the page START worked
            return {"goal": self.agent.status.goal, "armed": self.armed}
        try:
            return {**st(), "armed": self.armed}
        except Exception as e:
            return {"error": str(e), "armed": self.armed}

    def send_planner(self, force: bool = False) -> None:
        p = self.planner_payload()
        if p is not None and (force or p != self._planner_last):
            self._planner_last = p
            self.broadcast("planner.status", p)

    def npcs_payload(self) -> dict:
        from dataclasses import asdict

        return {"coords": list(self.npcs_coords) if self.npcs_coords else None,
                "entities": [{k: v for k, v in asdict(e).items() if k != "raw"} for e in self.npcs]}

    async def scan_npcs(self) -> dict:
        """'Locate NPCs': hold the entity key (Z), capture, OCR the plates (npcs.list_npcs).
        The only input is that one key hold."""
        from .npcs import list_npcs
        from .perception.regions import Regions
        from .skills.config import load_phases_config

        if self.agent.status.state == "stopped":
            raise ValueError("kill switch is on: resume first")
        ctl = self.agent.ctl
        if not hasattr(ctl, "capture_holding"):
            raise ValueError("this controller cannot hold a key while capturing")
        key = load_phases_config()["keys"]["show_entities"]
        p = self.agent.perceiver
        grid = p.grid_for(None) if p.grid_cfg.get("calibrated") else None
        async with self._scan_lock:
            ents, _ = await asyncio.to_thread(list_npcs, ctl, p.ocr, p.regions, grid, key)
        self.npcs = ents
        self.npcs_coords = self.agent.game.map.coords
        pl = self.agent.planner
        if self.npcs_coords is not None and hasattr(pl, "scanned_maps"):
            pl.scanned_maps[tuple(self.npcs_coords)] = pl.clock()
        n_m = sum(e.kind == "monster" for e in ents)
        self.bus.log("info", "perception", f"located {len(ents) - n_m} NPC(s) and {n_m} monster group(s)")
        payload = self.npcs_payload()
        self.broadcast("npcs.list", payload)
        return payload

    def network_payload(self) -> dict[str, Any]:
        selected = self.account_manager.scan()
        if selected is not None:
            return {"state": "listening", "result": selected, "error": None,
                    "source": "managed_socket", "account_id": self.account_manager.active_account_id,
                    "diagnostics": self._execution().trace.payload()}
        payload = dict(self._network_status)
        payload["diagnostics"] = self._execution().trace.payload()
        return payload

    def _selected_result(self) -> dict[str, Any]:
        """Latest protocol snapshot for the explicitly selected account."""
        selected = self.account_manager.scan()
        if selected is not None:
            return selected
        result = self._network_status.get("result")
        return dict(result) if isinstance(result, dict) else {}

    def accounts_payload(self) -> dict[str, Any]:
        result = self._network_status.get("result")
        if isinstance(result, dict) and result:
            self.account_manager.observe_primary(result)
        return {**self.account_manager.payload(), "profiles": [profile.public() for profile in self._profile_store.list()],
                "clients": self._client_lifecycle.status()}

    def create_account_profile(self, label: str) -> dict[str, Any]:
        profile = self._profile_store.create(label)
        self._client_lifecycle.get_or_create(profile.id)
        self.broadcast("accounts.status", self.accounts_payload())
        return profile.public()

    def rename_account_profile(self, profile_id: str, label: str) -> dict[str, Any]:
        profile = self._profile_store.rename(profile_id, label)
        self.broadcast("accounts.status", self.accounts_payload())
        return profile.public()

    def select_account(self, account_id: int) -> dict[str, Any]:
        """Select the account scope for dashboard/agent protocol commands.

        There is one live legacy scan while the proxy migrates to a full
        multi-connection listener.  Selection still matters today: it changes
        the writable transport used by protocol actions and refuses an unknown
        account instead of leaking commands into the default session.
        """
        self.account_manager.select(account_id)
        self.managed_transport = self.account_manager.transport(account_id)
        self._send_transport_status()
        return self.accounts_payload()

    def _bind_proxy_account(self, account_id: int, session_id: str, transport: ManagedTransport, profile_id: str = "default") -> None:
        """Receive Astra-style identity handoff from a live local proxy bridge."""
        self.account_manager.bind_identified_transport(account_id, session_id, transport, profile_id)
        # Account zero is a legacy placeholder only.  Promote the first real
        # authenticated account so the existing single-account dashboard keeps
        # showing its actual live transport, without changing an explicit tab.
        if self.account_manager.active_account_id == 0:
            self.account_manager.active_account_id = account_id
            self.managed_transport = transport
        self.bus.log("info", "protocol", f"bound live game socket {session_id} to account {account_id}")
        self._threadsafe(self._send_transport_status)

    def _receive_proxy_frame(self, account_id: int, from_server: bool, raw_frame: bytes) -> None:
        """Accept one already-framed packet from a named local game bridge.

        This runs on the asyncio loop (the proxy uses ``_threadsafe``), keeps
        at most eight MiB per direction/account, and debounces decoding so a
        burst of socket frames cannot starve the dashboard.
        """
        account_id = int(account_id)
        streams = self._proxy_streams.setdefault(account_id, {"server": bytearray(), "client": bytearray()})
        target = streams["server" if from_server else "client"]
        target.extend(raw_frame)
        if len(target) > self._proxy_stream_limit:
            target.clear()
            self.bus.log("warn", "protocol", f"account {account_id} protocol stream rotated; awaiting a fresh snapshot")
        if account_id not in self._proxy_parse_busy and self.loop is not None:
            self._proxy_parse_busy.add(account_id)
            self.loop.create_task(self._decode_proxy_stream(account_id))

    async def _decode_proxy_stream(self, account_id: int) -> None:
        # Let closely spaced frames coalesce into one valid state transition.
        await asyncio.sleep(0.08)
        try:
            streams = self._proxy_streams.get(int(account_id))
            if streams is None:
                return
            result = await asyncio.to_thread(parse_streams, bytes(streams["server"]), bytes(streams["client"]), 0)
            payload = scan_json(result)
            payload.update({"capture_path": None, "source": "managed_socket", "account_id": int(account_id)})
            self._attach_map_asset(payload)
            self._attach_latest_terrain(payload, include_saved_terrain=False)
            self._observe_protocol_payload(int(account_id), payload)
        except (OSError, ValueError, TypeError):
            # A stream can end halfway through a packet. The next frame retry
            # reuses the retained bytes; no stale scan is presented as fresh.
            pass
        finally:
            self._proxy_parse_busy.discard(int(account_id))

    def agent_context_payload(self) -> dict[str, Any]:
        """Stable protocol-derived context for an LLM or deterministic tool.

        ``quest_plan`` deliberately combines the live quest packet with local
        data/guide metadata.  It gives the model prerequisites and preparation
        facts without making it scrape a UI or invent the next quest step.
        Full human guide prose remains on-demand through the guide endpoint.
        """
        result = self._selected_result()
        world = result.get("world") if isinstance(result, dict) else None
        context = world.get("agent_context") if isinstance(world, dict) else None
        capture_path = result.get("capture_path") if isinstance(result, dict) else None
        observed_at_ms = None
        if capture_path:
            with contextlib.suppress(OSError):
                observed_at_ms = int(Path(str(capture_path)).stat().st_mtime * 1000)
        return {
            "version": 1,
            "source": "dofus-protocol",
            "account_id": self.account_manager.active_account_id,
            "scan_state": self._network_status.get("state"),
            "capture_path": capture_path,
            "observed_at_ms": observed_at_ms,
            "context": context,
            "quest_plan": self.quest_plan_payload(),
            "action_status": self.action_status_payload(),
            "transport": self.managed_transport.status(),
            "world_routing": self._world_routes.status(),
        }

    def action_status_payload(self) -> dict[str, Any]:
        """All acknowledgement barriers visible to the external planner."""
        statuses = {
            "dialogue": self._dialogue_executor.status(),
            "interactive": self._interactive_executor.status(),
            "navigation": self._navigation_executor.status(),
            "combat": self._combat_executor.status(),
            "inventory": self._inventory_executor.status(),
            "attack": self._attack_executor.status(),
            "talk": self._talk_executor.status(),
            "interruption": self._interruptions.status(self.account_manager.active_account_id),
            "recovery": self._recovery_guard.status(self.account_manager.active_account_id),
        }
        pending = {name: value for name, value in statuses.items() if value.get("state") != "idle"}
        result = self._selected_result()
        state = WorldState.from_scan(result) if result else WorldState.empty()
        fight_ready, fight_reason = self._attack_executor.fight_readiness(state)
        return {"idle": not pending, "pending": pending, "all": statuses,
                "fight_readiness": {"ready": fight_ready, "reason": fight_reason}}

    async def propose_agent_action(self, goal: str) -> dict[str, Any]:
        """Ask the one shared local model for one validated, non-executed step."""
        result = self._selected_result()
        if not isinstance(result, dict) or not result:
            raise ValueError("no decoded protocol state; start a network scan first")
        if not self.action_status_payload()["idle"]:
            raise ValueError("a protocol action is awaiting confirmation; observe before planning another mutation")
        planner = LocalWorldOrchestrator(self._llm_coordinator, self.account_manager.active_account_id)
        knowledge = {
            "quest_plan": self.quest_plan_payload(),
            "progression": self._progression_context(result),
            "quest_preparation": quest_preparation(self.quest_plan_payload()),
            "visible_resources": farming_candidates(result),
            "visible_pack_assessments": [pack_assessment(result.get("character"), group)
                                         for group in result.get("groups") or ()],
        }
        proposal, timings, narration = await planner.propose(WorldState.from_scan(result), goal, knowledge)
        return {"accepted": True, "account_id": self.account_manager.active_account_id,
                "proposal": {"action": proposal.action, "args": proposal.args, "reason": proposal.reason},
                "narration": narration, "timings": timings, "queue": self._llm_coordinator.status.to_json()}

    @staticmethod
    def _progression_context(result: dict[str, Any]) -> dict[str, Any]:
        """Expose only protocol-confirmed progression facts to the LLM.

        The current decoder has authoritative active/completed quest state.
        Achievement packets are not yet normalized on every client build, so
        their absence stays explicit rather than being guessed from assets.
        """
        quests = result.get("quests") or {}
        achievements = result.get("achievements")
        return {
            "active_quests": len(quests.get("active") or ()),
            "completed_quests": len(quests.get("completed") or ()),
            "successes": result.get("successes") or achievements or {
                "available": False,
                "reason": "no live success packet decoded for this client build yet",
            },
        }

    async def run_agent_step(self, goal: str) -> dict[str, Any]:
        """Let the local LLM take one complete, verified protocol step.

        A caller repeats this only after the next live snapshot arrives.  The
        model therefore gets the complete fresh world state on every step and
        can use every action exposed by :class:`WorldToolRegistry`, while the
        Hub remains the sole authority that validates and sends packets.
        """
        planned = await self.propose_agent_action(goal)
        proposal = planned["proposal"]
        if planned.get("narration"):
            self.bus.log("info", "llm", str(planned["narration"]))
        execution = self.execute_agent_intent(str(proposal["action"]), dict(proposal["args"]),
                                              str(proposal.get("reason") or "local LLM"))
        return {**planned, "execution": execution}

    def agent_goal_payload(self, account_id: int | None = None) -> dict[str, Any]:
        account_id = self.account_manager.active_account_id if account_id is None else int(account_id)
        run = self._agent_goals.get(account_id)
        return {"active": run is not None and run.state not in {"completed", "error"},
                "run": run.payload() if run is not None else None}

    async def start_agent_goal(self, goal: str) -> dict[str, Any]:
        """Start one persistent agent goal for the currently selected account."""
        goal = goal.strip()
        if not goal:
            raise ValueError("goal is required")
        account_id = self.account_manager.active_account_id
        self._agent_goals[account_id] = AgentGoalRun(account_id, goal)
        self.broadcast("agent.goal.status", self.agent_goal_payload(account_id))
        await self._advance_agent_goal(account_id)
        return self.agent_goal_payload(account_id)

    def pause_agent_goal(self, account_id: int | None = None) -> dict[str, Any]:
        account_id = self.account_manager.active_account_id if account_id is None else int(account_id)
        run = self._agent_goals.get(account_id)
        if run is None:
            raise ValueError("no active agent goal")
        run.set_state("paused")
        self.broadcast("agent.goal.status", self.agent_goal_payload(account_id))
        return self.agent_goal_payload(account_id)

    async def resume_agent_goal(self, account_id: int | None = None) -> dict[str, Any]:
        account_id = self.account_manager.active_account_id if account_id is None else int(account_id)
        run = self._agent_goals.get(account_id)
        if run is None:
            raise ValueError("no agent goal to resume")
        if run.state == "completed":
            raise ValueError("completed agent goals cannot be resumed; start a new goal")
        run.set_state("running")
        self.broadcast("agent.goal.status", self.agent_goal_payload(account_id))
        await self._advance_agent_goal(account_id)
        return self.agent_goal_payload(account_id)

    async def _advance_agent_goal(self, account_id: int) -> None:
        """Take one step only after the prior request has live confirmation."""
        run = self._agent_goals.get(int(account_id))
        if run is None or run.state not in {"running", "waiting_confirmation"}:
            return
        if account_id != self.account_manager.active_account_id:
            run.set_state("paused", error="select this account before resuming its agent goal")
            self.broadcast("agent.goal.status", self.agent_goal_payload(account_id))
            return
        if account_id in self._agent_goal_busy or not self.action_status_payload()["idle"]:
            return
        self._agent_goal_busy.add(account_id)
        try:
            run.set_state("running")
            step = await self.run_agent_step(run.goal)
            proposal = dict(step["proposal"])
            run.record(proposal, step.get("narration"))
            action = proposal["action"]
            if action == "finish_goal":
                run.set_state("completed")
            elif action in {"wait_for_update", "observe_world", "inspect_active_quests", "get_active_quest_guide"}:
                run.set_state("paused", error="agent needs a new live update or a more specific goal")
            elif step["execution"].get("sent"):
                run.set_state("waiting_confirmation")
            else:
                run.set_state("paused", error="agent action did not produce a protocol request")
        except Exception as exc:
            run.set_state("error", error=str(exc))
            self.bus.log("warn", "llm", f"agent goal paused: {exc}")
        finally:
            self._agent_goal_busy.discard(account_id)
            self.broadcast("agent.goal.status", self.agent_goal_payload(account_id))

    def world_route_payload(self, target_map_id: int, result: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read an explicit graph route from the protocol-confirmed current map."""
        result = self._selected_result() if result is None else result
        if not isinstance(result, dict) or result.get("map_id") is None:
            return {"found": False, "reason": "no current protocol-confirmed map"}
        position = result.get("map_position") or {}
        try:
            route = self._world_routes.route(
                int(result["map_id"]), int(target_map_id), position.get("linked_zone"),
            )
        except (WorldGraphUnavailable, ValueError) as exc:
            return {
                "found": False, "source_map_id": int(result["map_id"]),
                "target_map_id": int(target_map_id), "reason": str(exc),
                "world_routing": self._world_routes.status(),
            }
        execution: dict[str, Any] = {"ready": False, "reason": "already on destination map"}
        if route["steps"]:
            transition = route["steps"][0]
            current_cell = (result.get("character") or {}).get("cell")
            target_cell = transition.get("cell_id")
            terrain = result.get("terrain") or {}
            if current_cell is None:
                execution = {"ready": False, "reason": "current character cell is not protocol-confirmed"}
            elif target_cell is None or int(target_cell) < 0:
                execution = {"ready": False, "reason": "transition has no walk-to cell"}
            else:
                movement = find_roleplay_path(int(current_cell), int(target_cell), terrain.get("blocked", ()))
                if movement is None:
                    execution = {"ready": False, "reason": "transition cell is unreachable on current asset terrain"}
                else:
                    execution = {
                        "ready": True, "kind": "move_then_map_change",
                        "move": {"map_id": int(result["map_id"]), "cells": list(movement.cells),
                                 "key_cells": list(movement.key_cells)},
                        "map_change": {"transition_map_id": int(transition["transition_map_id"]),
                                       "expected_map_id": int(transition["to_map_id"])},
                        "confirmation": "wait for a current-map event after each request",
                    }
        return {"found": True, "world_routing": self._world_routes.status(), "execution": execution, **route}

    def interactive_plan_payload(self, element_id: int) -> dict[str, Any]:
        """Return an approach/use plan for one live interactive; never send it."""
        result = self._selected_result()
        if not isinstance(result, dict) or not result:
            return {"ready": False, "reason": "no decoded protocol state"}
        state = WorldState.from_scan(result)
        exits = ()
        if state.map_id is not None:
            with contextlib.suppress(WorldGraphUnavailable):
                exits = self._world_routes.exit_cells(int(state.map_id))
        return plan_interactive(state, element_id, exits)

    def start_world_navigation(self, target_map_id: int) -> dict[str, Any]:
        """Start exactly one graph transition; monitor continues only on proof."""
        result = self._selected_result()
        if not isinstance(result, dict) or not result:
            raise ValueError("no decoded protocol state; start a network scan first")
        route = self.world_route_payload(int(target_map_id))
        started = self._navigation_executor.start(WorldState.from_scan(result), route, self.managed_transport)
        self._send_transport_status()
        self.bus.log("info", "protocol", f"world route started toward map {target_map_id}: {started['state']}")
        return {"accepted": True, "target_map_id": int(target_map_id), **started}

    def quest_plan_payload(self) -> dict[str, Any]:
        """Read-only actionable summary of active protocol-confirmed quests."""
        result = self._selected_result()
        if not isinstance(result, dict) or not result:
            return {"source": "dofus-protocol", "active_quest_plans": [], "reason": "no decoded protocol state"}
        return QuestPlanner(self._game_data, self._guides).plan(result)

    def session_plan_payload(self) -> dict[str, Any]:
        plan = self._session_plans.load(self.account_manager.active_account_id)
        return {"configured": plan is not None, "plan": plan.status() if plan else None}

    def configure_session_plan(self, duration_minutes: int, jobs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        duration = max(1, min(8 * 60, int(duration_minutes)))
        plan = SessionPlan(self.account_manager.active_account_id, duration, list(jobs or []), started_at=int(time.time()))
        self._session_plans.save(plan)
        return self.session_plan_payload()

    def execute_next_quest_step(self) -> dict[str, Any]:
        result = self._selected_result()
        step = self._quest_executor.next_step(self.quest_plan_payload(), WorldState.from_scan(result))
        payload = {"state": step.state, "reason": step.reason, "account_id": self.account_manager.active_account_id}
        if step.intent is not None:
            payload["execution"] = self.execute_agent_intent(step.intent.action, step.intent.args, step.intent.reason)
        return payload

    def execute_agent_intent(self, action: str, args: dict[str, Any], reason: str = "") -> dict[str, Any]:
        """Validate and send one LLM-facing action against fresh protocol state.

        No controller/UI fallback exists here.  A stale PCAP can be inspected
        through ``agent_context`` but cannot be used to emit an action.
        """
        result = self._selected_result()
        if not isinstance(result, dict) or not result:
            raise ValueError("no decoded protocol state; start a network scan first")
        read_only = {"observe_world", "wait_for_update", "finish_goal", "inspect_active_quests", "get_active_quest_guide"}
        actions = self.action_status_payload()
        if action not in read_only and not actions["idle"]:
            names = ", ".join(sorted(actions["pending"]))
            raise ValueError(f"protocol action is awaiting confirmation ({names}); observe and wait before another action")
        state = WorldState.from_scan(result)
        tools = WorldToolRegistry(state)
        proposal = tools.validate(WorldIntent(str(action), dict(args), str(reason)))
        self._execution().trace.record("action_requested", {
            "action": proposal.action, "args": proposal.args, "reason": proposal.reason,
        }, state)
        if proposal.action == "navigate_to_map":
            return self.start_world_navigation(int(proposal.args["map_id"]))
        if proposal.action == "inspect_active_quests":
            return {"accepted": True, "action": proposal.action, "sent": False,
                    "quest_plan": self.quest_plan_payload()}
        if proposal.action == "get_active_quest_guide":
            quest_id = int(proposal.args["quest_id"])
            info = self._game_data.quest_info(quest_id)
            return {"accepted": True, "action": proposal.action, "sent": False,
                    "guide": self._guides.resolve(quest_id, info.get("name"))}
        if proposal.action == "talk_to_npc":
            self._talk_executor.talk(state, int(proposal.args["entity_id"]), self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for the NPC's first live dialogue stage"}
        if proposal.action == "choose_dialogue_reply":
            self._dialogue_executor.choose(state, int(proposal.args["reply_id"]), self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for a new dialogue stage or close event"}
        if proposal.action == "leave_dialogue":
            self.managed_transport.leave_dialog()
            self._dialogue_executor.cancel()
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for a dialogue close event"}
        if proposal.action == "use_interactive":
            exits = ()
            if state.map_id is not None:
                with contextlib.suppress(WorldGraphUnavailable):
                    exits = self._world_routes.exit_cells(int(state.map_id))
            execution = self._interactive_executor.start(
                state, plan_interactive(state, int(proposal.args["element_id"]), exits), self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for movement then interactive lifecycle event", "execution": execution}
        if proposal.action == "attack_monster_group":
            self._attack_executor.attack(state, int(proposal.args["entity_id"]), self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for live fight-start state"}
        if proposal.action == "use_inventory_item":
            self._inventory_executor.use(state, int(proposal.args["object_uid"]), self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for a live inventory quantity/removal update"}
        if proposal.action == "equip_inventory_item":
            self._inventory_executor.equip(state, int(proposal.args["object_uid"]), int(proposal.args["position"]),
                                           self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for the item's live equipment position update"}
        if proposal.action == "cast_spell":
            self._combat_executor.cast(state, int(proposal.args["spell_id"]),
                                       int(proposal.args["cell"]), self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for matching live spell event"}
        if proposal.action == "end_turn":
            self._combat_executor.end_turn(state, self.managed_transport)
            self._send_transport_status()
            return {"accepted": True, "action": proposal.action, "sent": True,
                    "confirmation": "waiting for live turn-end event"}
        game_intent = tools.game_intent(proposal)
        if game_intent is None:
            return {"accepted": True, "action": proposal.action, "sent": False, "observation": tools.observe()}
        try:
            self.managed_transport.send(game_intent)
        except RuntimeError as exc:
            self._send_transport_status()
            raise ValueError(str(exc)) from exc
        self._send_transport_status()
        self.bus.log("info", "protocol", f"agent intent sent: {proposal.action}" + (f" ({proposal.reason})" if proposal.reason else ""))
        return {"accepted": True, "action": proposal.action, "sent": True,
                "intent": {"kind": game_intent.kind, "payload": game_intent.payload}}

    def _send_network(self) -> None:
        self.broadcast("network.scan.status", self.network_payload())

    def _send_dofus_status(self) -> None:
        self.broadcast("dofus.status", dict(self._dofus_status))

    def _send_transport_status(self) -> None:
        self.broadcast("transport.status", self.managed_transport.status())
        self.broadcast("accounts.status", self.accounts_payload())

    def _expire_protocol_actions(self) -> None:
        """Release an acknowledgement barrier after a bounded live wait."""
        for account_id, execution in tuple(self._executions.items()):
            for executor in (execution.dialogue, execution.interactive, execution.navigation,
                             execution.combat, execution.inventory, execution.attack, execution.talk):
                transition = executor.expire()
                if transition is not None:
                    execution.trace.record("action_transition", transition)
                    snapshot = self.account_manager.scan(account_id)
                    self._recovery_guard.hold(account_id, transition,
                                              WorldState.from_scan(snapshot) if snapshot else WorldState.empty())
                    self._account_scheduler.block(account_id)
                    self.bus.log("warn", "protocol", f"account {account_id}: {transition['kind']} action timed out; fresh state required")
                    if account_id == self.account_manager.active_account_id:
                        self._send_transport_status()
                        self._send_network()

    def _process_interruptions(self, payload: dict[str, Any], account_id: int | None = None) -> None:
        """Apply only proven unsolicited-event mappings, once per packet offset."""
        account_id = self.account_manager.legacy_observation_account_id if account_id is None else int(account_id)
        for event in payload.get("map_events", ()):  # normalized by network_scan
            offset = event.get("offset")
            key = (account_id, int(offset)) if isinstance(offset, int) else None
            if key is None or key in self._processed_interruption_offsets:
                continue
            kind = event.get("kind")
            if kind not in {"trade_requested", "exchange_left"}:
                continue
            self._processed_interruption_offsets.add(key)
            if kind == "exchange_left":
                resumed = self._interruptions.closed(account_id)
                if resumed is not None:
                    self.bus.log("info", "protocol", "unsolicited trade closed; account action scope resumed")
                continue
            decision = self._interruptions.observe(Interruption(account_id, InterruptionKind.TRADE_REQUEST,
                                                                 source_id=event.get("source_id"), offset=offset))
            if decision["state"] != "blocked":
                continue
            self.bus.log("warn", "protocol", "unsolicited trade request default-denied")
            try:
                self.account_manager.transport(account_id).leave_dialog()
                if account_id == self.account_manager.active_account_id:
                    self._send_transport_status()
            except RuntimeError:
                # Passive live capture still records the interruption; without
                # a managed socket we must not fall back to mouse/UI input.
                self.bus.log("warn", "protocol", "trade refusal queued but managed transport is offline")

    def _observe_protocol_payload(self, account_id: int, payload: dict[str, Any]) -> None:
        """Apply confirmations only to the account that produced this stream."""
        account_id = int(account_id)
        self.account_manager.observe(account_id, payload)
        state = WorldState.from_scan(payload)
        recovered = self._recovery_guard.observe(account_id, state)
        if recovered is not None:
            self.bus.log("info", "protocol", f"account {account_id}: anti-stuck hold released after fresh protocol state")
        plan = self._session_plans.load(account_id)
        if plan is not None:
            decision = self._inventory_policy.decide(payload.get("inventory") or {})
            plan.checkpoint = {"map_id": payload.get("map_id"), "character_cell": (payload.get("character") or {}).get("cell"),
                               "pods": decision.percent, "pod_state": decision.state, "reason": decision.reason}
            self._session_plans.save(plan)
            if decision.state in {"bank_after_safe_action", "bank_required"}:
                self._account_scheduler.block(account_id)
                self.bus.log("warn", "scheduler", f"account {account_id}: {decision.reason}; bank transfer awaits protocol support")
        if self._recovery_guard.status(account_id)["state"] == "idle":
            self._account_scheduler.request(account_id)
        else:
            self._account_scheduler.block(account_id)
        execution = self._execution(account_id)
        try:
            transport = self.account_manager.transport(account_id)
        except ValueError:
            return
        self._process_interruptions(payload, account_id)
        navigation_transition = execution.navigation.observe(state, transport)
        if (navigation_transition is not None and navigation_transition.get("state") == "leg_completed"
                and navigation_transition.get("map_id") != navigation_transition.get("target_map_id")):
            try:
                execution.navigation.start(state, self.world_route_payload(int(navigation_transition["target_map_id"]), payload), transport)
            except (ValueError, RuntimeError) as exc:
                navigation_transition = {"state": "failed", "kind": "navigation", "reason": str(exc)}
        transitions = (
            execution.interactive.observe(state, transport),
            execution.dialogue.observe(state),
            navigation_transition,
            execution.combat.observe(state),
            execution.inventory.observe(state),
            execution.attack.observe(state),
            execution.talk.observe(state),
        )
        for transition in transitions:
            if transition is not None:
                execution.trace.record("action_transition", transition, state)
                self.bus.log("info", "protocol", f"account {account_id}: {transition.get('kind', 'action')} {transition.get('state', 'updated')}")
        if all(item.status().get("state") == "idle" for item in (
            execution.dialogue, execution.interactive, execution.navigation,
            execution.combat, execution.inventory, execution.attack, execution.talk,
        )) and self._interruptions.status(account_id).get("state") == "idle":
            self._account_scheduler.unblock(account_id)
        if account_id == self.account_manager.active_account_id:
            self._send_transport_status()
            self._send_network()
            run = self._agent_goals.get(account_id)
            if run is not None and run.state == "waiting_confirmation" and self.action_status_payload()["idle"] and self.loop is not None:
                self.loop.create_task(self._advance_agent_goal(account_id))

    def next_schedulable_account(self) -> int | None:
        """Return the next ready account without allowing pending actions to race."""
        for _ in range(len(self.account_manager.payload().get("accounts", []))):
            account_id = self._account_scheduler.next()
            if account_id is None:
                return None
            execution = self._execution(account_id)
            plan = self._session_plans.load(account_id)
            if plan is not None:
                session = plan.status()
                if session["expired"]:
                    self._account_scheduler.block(account_id)
                    self.bus.log("info", "scheduler", f"account {account_id}: session duration reached; paused")
                    continue
                if plan.checkpoint.get("pod_state") in {"bank_after_safe_action", "bank_required"}:
                    self._account_scheduler.block(account_id)
                    continue
            pending = any(item.status().get("state") != "idle" for item in (
                execution.dialogue, execution.interactive, execution.navigation,
                execution.combat, execution.inventory, execution.attack, execution.talk,
            ))
            if pending or self._interruptions.status(account_id).get("state") != "idle":
                self._account_scheduler.block(account_id)
                continue
            return account_id
        return None

    async def propose_next_scheduled_action(self, goal: str) -> dict[str, Any]:
        """Ask the shared LLM for one proposal on the next ready account.

        This is scheduling, not automation: callers receive a validated
        proposal and must still explicitly execute it through the normal
        account-scoped action boundary.
        """
        account_id = self.next_schedulable_account()
        if account_id is None:
            raise ValueError("no account is currently ready for a scheduled proposal")
        result = self.account_manager.scan(account_id)
        if not result:
            raise ValueError("scheduled account has no live protocol snapshot")
        planner = LocalWorldOrchestrator(self._llm_coordinator, account_id)
        proposal, timings, narration = await planner.propose(WorldState.from_scan(result), goal)
        return {"accepted": True, "account_id": account_id,
                "proposal": {"action": proposal.action, "args": proposal.args, "reason": proposal.reason},
                "narration": narration, "timings": timings, "queue": self._llm_coordinator.status.to_json()}

    async def launch_dofus(self, profile_id: str = "default") -> None:
        """Launch Dofus through the managed local transport.

        The managed proxy receives the full login/map-entry stream itself, so
        passive tcpdump must remain an explicit Scan action. Starting it here
        used to create an unrelated administrator-password prompt on every
        game launch, despite adding no data to this launch path.
        """
        profile_id = str(profile_id or "default")
        profile = None if profile_id == "default" else self._profile_store.get(profile_id)
        launch = self._client_lifecycle.get_or_create(profile_id)
        if profile_id in self._profile_processes:
            raise ValueError("that profile already has a managed Dofus process")
        if self.mock:
            self._dofus_status = {"state": "mock", "app": self._dofus_status["app"], "launcher": self._dofus_status["launcher"], "error": None}
            self._send_dofus_status()
            return
        app = str(os.environ.get("DOFUS_APP", self._dofus_status.get("app") or "Dofus"))
        launcher = str(os.environ.get("ANKAMA_LAUNCHER_APP", self._dofus_status.get("launcher") or "Ankama Launcher"))
        self._dofus_status = {"state": "launching", "app": app, "launcher": launcher, "error": None}
        self._send_dofus_status()
        if os.uname().sysname != "Darwin":
            self._dofus_status = {"state": "error", "app": app, "launcher": launcher, "error": "web launch is currently implemented for macOS"}
            self._send_dofus_status()
            raise ValueError(self._dofus_status["error"])
        try:
            # AstraBot's key difference is that it owns the local Zaap service
            # for the lifetime of the Dofus process. The official launcher is
            # not a registration endpoint, so do not open it and then race its
            # private service; start our compatible local service instead.
            auth_path = self._auth_path if profile is None else profile.token_path
            stored_auth = load_ankama_auth(auth_path)
            auth = os.environ.get("DOFUS_GAME_TOKEN") or (stored_auth or {}).get("game_token")
            if not auth:
                raise RuntimeError("Authenticate Ankama first; the local Zaap service has no game token")
            os.environ["DOFUS_GAME_TOKEN"] = str(auth)
            if (stored_auth or {}).get("haapi_key"):
                os.environ["DOFUS_HAAPI_KEY"] = str(stored_auth["haapi_key"])
            user_info = (stored_auth or {}).get("user_info")
            if user_info:
                # Match AstraBot's userInfo_get response.  Keep it in the
                # launcher process only; do not expose account data via WS.
                os.environ["DOFUS_USER_INFO"] = json.dumps(user_info, separators=(",", ":"))
            zaap = ZaapEmulator(port=launch.zaap_port, token=str(auth),
                                user_info=json.dumps(user_info, separators=(",", ":")) if user_info else None,
                                haapi_key=(stored_auth or {}).get("haapi_key"))
            await asyncio.to_thread(zaap.start)
            await asyncio.sleep(0.5)
            executable = os.environ.get(
                "DOFUS_EXECUTABLE",
                "/Applications/Ankama/Dofus-dofus3/Dofus.app/Contents/MacOS/Dofus",
            )
            if not os.path.isfile(executable):
                raise FileNotFoundError(f"Dofus executable not found: {executable}")
            # These are the arguments from the installed Ankama zaap.yml. The
            # launcher listens on 26116 and uses the instance/hash handshake;
            # opening the .app bundle without them causes the connection-lost
            # dialog shown by the client.
            instance_id = str(int(time.time() * 1000) % 2_000_000_000)
            game_hash = str(uuid.uuid4())
            args = [executable, "--port", str(launch.zaap_port), "--gameName", "Dofus",
                    "--gameRelease", "dofus3", "--instanceId", instance_id,
                    "--hash", game_hash, "--canLogin", "true", "--langCode", "fr",
                    "--autoConnectType", os.environ.get("DOFUS_AUTO_CONNECT", "2"),
                    "--connectionPort", "5555"]
            env = os.environ.copy()
            env.update({"ZAAP_CAN_AUTH": "true", "ZAAP_GAME": "Dofus",
                        "ZAAP_HASH": game_hash, "ZAAP_INSTANCE_ID": instance_id,
                        "ZAAP_PORT": str(launch.zaap_port), "ZAAP_RELEASE": "dofus3"})
            # Direct adaptation of AstraBot's launch path: Frida changes only
            # the initial fixed port-5555 connect to our local listener.  The
            # listener then transparently relays login and rewrites the chosen
            # shard to the one-shot managed game bridge.  No gameplay mouse
            # clicks are involved in this mode.
            proxy = ManagedConnectionProxy(
                self.managed_transport,
                AstraCodec(),
                on_transport_change=lambda: self._threadsafe(self._send_transport_status),
                transport_for_account=self.account_manager.transport_for_identified_account,
                on_account_identified=lambda account_id, session_id, transport: self._bind_proxy_account(account_id, session_id, transport, profile_id),
                on_protocol_frame=lambda account_id, from_server, raw_frame: self._threadsafe(
                    self._receive_proxy_frame, account_id, from_server, raw_frame),
            )
            # The native macOS transparent-proxy companion claims just the
            # Dofus connection-server flow and forwards it to this stable
            # loopback port.  It gives us the same bridge as Frida without
            # attaching to or modifying the hardened Dofus process.  Leave
            # the legacy Frida path available until the extension is built
            # and explicitly activated by its host app.
            transparent_port = int(os.environ.get("DOFUS_TRANSPARENT_PROXY_PORT", "0") or 0)
            connection_port = proxy.start(transparent_port)
            if transparent_port:
                proc = await asyncio.to_thread(spawn_without_injection, args, env)
                self.bus.log("info", "transport", f"launching via macOS transparent proxy on 127.0.0.1:{connection_port}")
            else:
                proc = await asyncio.to_thread(spawn_with_redirect, args, env, connection_port)
            self._profile_zaaps[profile_id], self._profile_proxies[profile_id], self._profile_processes[profile_id] = zaap, proxy, proc
            self._client_lifecycle.mark_launched(profile_id, proc.pid)
            if profile_id == "default":
                self._zaap, self._managed_proxy, self._redirected_process = zaap, proxy, proc
            # Unity can create its native window several seconds after the
            # process starts (and may restore fullscreen first).  Retry until
            # it exists instead of swallowing one failed attempt at t=2s.
            if self.slot.target() is not None:
                aligned = False
                for _ in range(24):  # up to 12 seconds; no screen capture
                    await asyncio.sleep(0.5)
                    try:
                        await self.slot.align()
                        aligned = True
                        break
                    except Exception:
                        continue
                if not aligned:
                    self.bus.log("warn", "control", "Dofus launched but did not accept the saved window slot")
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            # The official Ankama Launcher owns this loopback port while it is
            # running.  Our AstraBot-compatible Zaap implementation must own
            # the same port, so give the operator an actionable error instead
            # of a low-level macOS bind failure.
            if isinstance(exc, OSError) and getattr(exc, "errno", None) == 48:
                detail = (
                    "Port 26116 is already in use by the Ankama Launcher or another "
                    "Dofus session. Quit it completely, then use Launch + listen here."
                )
            self._client_lifecycle.mark_error(profile_id, str(detail).strip()[-500:])
            self._dofus_status = {"state": "error", "app": app, "launcher": launcher, "error": str(detail).strip()[-500:]}
            self._send_dofus_status()
            raise ValueError(f"could not launch {app}: {detail}") from exc
        self._dofus_status = {"state": "launched", "app": app, "launcher": launcher, "pid": proc.pid, "error": None}
        self.bus.log("info", "protocol", f"launched {app} through managed socket proxy; awaiting game connection")
        self._send_dofus_status()
        self._send_transport_status()

    async def authenticate_ankama(self, profile_id: str = "default") -> None:
        profile_id = str(profile_id or "default")
        profile = None if profile_id == "default" else self._profile_store.get(profile_id)
        auth_path = self._auth_path if profile is None else profile.token_path
        self._dofus_status = {**self._dofus_status, "state": "authenticating", "error": None}
        self._send_dofus_status()
        try:
            data = await authenticate_ankama(auth_path)
            os.environ["DOFUS_GAME_TOKEN"] = str(data["game_token"])
            self._dofus_status = {**self._dofus_status, "state": "authenticated", "error": None}
        except Exception as exc:
            self._dofus_status = {**self._dofus_status, "state": "error", "error": str(exc)[-500:]}
            raise ValueError(f"Ankama authentication failed: {exc}") from exc
        finally:
            self._send_dofus_status()

    async def start_network_scan(self) -> None:
        if self._network_proc is not None and self._network_proc.poll() is None:
            raise ValueError("network scan is already running")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self._network_path)
        if os.geteuid() == 0:
            self._network_proc = subprocess.Popen(
                # Keep the investigation capture broad. The decoder still
                # selects the Dofus stream (server -> client TCP/5555), but
                # retaining all en0 traffic lets us identify ancillary game
                # connections and the placement packet before narrowing later.
                ["tcpdump", "-i", "en0", "-nn", "-s", "0", "-U", "-w", self._network_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        else:
            # Keep tcpdump in the foreground so the osascript wrapper remains
            # alive. That gives the live monitor a real process to watch and
            # lets Stop terminate the privileged capture cleanly.
            # tcpdump needs elevated privilege only to open macOS's BPF
            # device.  `-Z` immediately drops it to the dashboard user before
            # opening/writing the PCAP, so the next normal run can read and
            # remove its own capture. This is the missing half of the split
            # between privileged capture and user-owned Dofus/Frida services.
            capture_user = getpass.getuser()
            shell = ("exec tcpdump -Z " + shlex.quote(capture_user)
                     + " -i en0 -nn -s 0 -U -w " + shlex.quote(self._network_path))
            self._network_proc = subprocess.Popen(
                ["osascript", "-e", f"do shell script {json.dumps(shell)} with administrator privileges"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        await asyncio.sleep(0.25)
        # Keep the last good replay visible while the activated listener watches
        # the live stream. The monitor publishes out-of-combat map state too.
        self._network_status = {"state": "listening", "result": self._network_status.get("result"), "error": None}
        self._network_monitor_task = asyncio.create_task(self._monitor_network())
        self._send_network()

    async def _monitor_network(self) -> None:
        proc = self._network_proc
        while proc is self._network_proc and proc is not None and proc.poll() is None:
            await asyncio.sleep(1.0)
            self._expire_protocol_actions()
            try:
                result = await asyncio.to_thread(parse_capture, self._network_path)
                payload = scan_json(result)
                payload["capture_path"] = self._network_path
                self._attach_map_asset(payload)
                # Live role-play state must never inherit a terrain mask from a
                # previous fight/map. Terrain is only attached to saved/fight
                # captures where it was recorded deliberately.
                self._attach_latest_terrain(payload, include_saved_terrain=False)
                self._network_status = {"state": "listening", "result": payload, "error": None}
                self._observe_protocol_payload(0, payload)
            except (OSError, ValueError, TypeError):
                # A packet can be mid-write; retain the last complete parse.
                continue
        if proc is self._network_proc and proc is not None and proc.poll() is not None:
            detail = "packet listener exited"
            try:
                stderr = proc.stderr.read() if proc.stderr else b""
                if stderr:
                    detail = stderr.decode("utf-8", "replace").strip()[-500:]
            except Exception:
                pass
            self._network_status = {"state": "error", "result": self._network_status.get("result"), "error": detail}
            self._send_network()

    async def stop_network_scan(self) -> dict[str, Any]:
        proc = self._network_proc
        if proc is None:
            raise ValueError("network scan is not running")
        proc.send_signal(signal.SIGINT)
        await asyncio.to_thread(proc.communicate)
        self._network_proc = None
        if self._network_monitor_task is not None:
            self._network_monitor_task.cancel()
            with contextlib.suppress(BaseException):
                await self._network_monitor_task
            self._network_monitor_task = None
        os.makedirs(self._network_archive_dir, exist_ok=True)
        archive_path = os.path.join(self._network_archive_dir, f"session-{time.strftime('%Y%m%d-%H%M%S')}.pcap")
        shutil.copy2(self._network_path, archive_path)
        try:
            result = scan_json(await asyncio.to_thread(parse_capture, self._network_path))
        except Exception as e:
            self._network_status = {"state": "error", "result": self._network_status.get("result"), "error": str(e)}
            self._send_network()
            raise ValueError(f"could not process capture: {e}") from e
        result["capture_path"] = archive_path
        self._attach_latest_terrain(result)
        self._attach_map_asset(result)
        self._network_status = {"state": "ready", "result": result, "error": None}
        self._send_network()
        self.bus.log("info", "perception", f"network scan: {len(result['groups'])} monster group(s), {len(result['players'])} player(s)")
        return result

    def attack(self, index: int | None, name: str | None) -> None:
        ents = self.npcs
        e = None
        if index is not None and 0 <= int(index) < len(ents):
            e = ents[int(index)]
        elif name:
            e = next((x for x in ents if x.name.casefold() == name.casefold()), None)
        if e is None:
            raise ValueError("unknown target: locate NPCs first and pick one from the list")
        if e.kind != "monster":
            raise ValueError(f"{e.name} is an NPC, not a monster group")
        pl = self.agent.planner
        if not hasattr(pl, "attack_target"):
            raise ValueError("network scan is read-only; attack requires the control app with --planner phases")
        pl.attack_target = e
        self.agent.set_goal(f"attack {e.name}")

    async def click_point(self, x: float, y: float, reason: str, cell: int | None = None) -> None:
        """Focus Dofus then make one geometry-based click without screen capture."""
        # The dashboard button necessarily steals focus. Return focus to Dofus
        # immediately before the one click; PacedController delegates focus to
        # its wrapped macOS controller.
        # Window size can legitimately change while Unity leaves fullscreen or
        # after character selection.  This click uses the just-read content
        # rect below, so discard an obsolete controller alignment rather than
        # rejecting a correctly scaled current-window click.
        forget_alignment = getattr(self.agent.ctl, "forget_alignment", None)
        if callable(forget_alignment):
            await asyncio.to_thread(forget_alignment)
        focus = getattr(self.agent.ctl, "focus", None)
        if callable(focus):
            await asyncio.to_thread(focus)
        await self.agent.executor.execute({
            "kind": "click", "x": round(x, 1), "y": round(y, 1),
            **({"cell": int(cell)} if cell is not None else {}), "reason": reason,
        })
        self.bus.log("info", "control", f"click sent at {x:.1f},{y:.1f}: {reason}")

    async def click_cell(self, cell: int, reason: str = "semi-auto map click") -> None:
        """Semi-auto: click one decoded map cell and then stop."""
        if not 0 <= int(cell) < 560:
            raise ValueError("invalid map cell")
        # This path deliberately does not capture the screen, but window
        # geometry is still available through Accessibility/CGWindowList.
        # Use the live content size so a calibration made at another window
        # size is scaled correctly.
        win = await asyncio.to_thread(self.agent.ctl.find_window)
        content = getattr(win, "content", None) if win is not None else None
        ww = float(content.w) if content is not None else self.agent._window_w()
        wh = float(content.h) if content is not None else self.agent._window_h()
        x, y = self.agent.perceiver.grid_for(ww, wh).center(int(cell))
        await self.click_point(x, y, reason, int(cell))

    async def talk_to_npc(self, entity_id: int, cell: int) -> None:
        """Open the default TALK dialogue through the managed game socket."""
        result = self._selected_result()
        state = WorldState.from_scan(result)
        actor = state.actors.get(int(entity_id))
        if actor is None or actor.kind != "npc" or actor.cell != int(cell):
            raise ValueError("NPC is not present at that current map cell; refresh the live scan")
        try:
            self._talk_executor.talk(state, int(entity_id), self.managed_transport)
        except RuntimeError as exc:
            self._send_transport_status()
            raise ValueError(str(exc)) from exc
        self._execution().trace.record("action_requested", {"action": "talk_to_npc", "entity_id": int(entity_id)}, state)
        self._send_transport_status()
        self.bus.log("info", "protocol", f"NPC talk sent: {actor.name or actor.id} (entity {actor.id}, map {state.map_id})")

    async def choose_dialog_reply(self, reply_id: int) -> None:
        """Send a network-confirmed reply through the managed game socket."""
        result = self._selected_result()
        dialog = result.get("dialog") or {}
        options = dialog.get("options") or []
        index = next((i for i, option in enumerate(options) if int(option.get("reply_id", -1)) == reply_id), None)
        if index is None:
            raise ValueError("reply is not available in the current dialogue; refresh or reopen the NPC dialogue")
        label = options[index].get("text") or f"reply {reply_id}"
        try:
            self._dialogue_executor.choose(WorldState.from_scan(result), reply_id, self.managed_transport)
        except RuntimeError as exc:
            self._send_transport_status()
            raise ValueError(str(exc)) from exc
        self._execution().trace.record("action_requested", {"action": "choose_dialogue_reply", "reply_id": reply_id}, WorldState.from_scan(result))
        self._send_transport_status()
        self.bus.log("info", "protocol", f"dialogue reply sent: {reply_id} ({label}; option {index + 1}/{len(options)})")

    async def leave_dialog(self) -> None:
        """Close the current protocol-confirmed NPC dialogue."""
        if not self._selected_result().get("dialog"):
            raise ValueError("no current NPC dialogue to close; refresh the live scan")
        try:
            self.managed_transport.leave_dialog()
            self._dialogue_executor.cancel()
        except RuntimeError as exc:
            self._send_transport_status()
            raise ValueError(str(exc)) from exc
        self._execution().trace.record("action_requested", {"action": "leave_dialogue"}, WorldState.from_scan(self._selected_result()))
        self._send_transport_status()
        self.bus.log("info", "protocol", "dialogue leave sent")

    async def use_interactive(self, element_id: int, cell: int) -> None:
        """Plan approach, wait for movement confirmation, then use live skill UID."""
        result = self._selected_result()
        interactives = result.get("interactives") or []
        item = next((entry for entry in interactives if entry.get("id") == int(element_id) and entry.get("cell") == int(cell)), None)
        if item is None:
            raise ValueError("clickable is not present at that current map cell; refresh the live scan")
        state = WorldState.from_scan(result)
        exits = ()
        if state.map_id is not None:
            with contextlib.suppress(WorldGraphUnavailable):
                exits = self._world_routes.exit_cells(int(state.map_id))
        plan = plan_interactive(state, int(element_id), exits)
        try:
            execution = self._interactive_executor.start(state, plan, self.managed_transport)
        except RuntimeError as exc:
            self._send_transport_status()
            raise ValueError(str(exc)) from exc
        self._execution().trace.record("action_requested", {"action": "use_interactive", "element_id": int(element_id), "cell": int(cell)}, state)
        self._send_transport_status()
        self.bus.log("info", "protocol", f"interactive plan started: {item.get('resource_name') or item.get('name') or element_id} ({execution['phase']})")

    async def semi_auto_fight(self) -> None:
        """Advance placement without choosing challenges, then stop at turn 1.

        The main Dofus button keeps the same fractional position as the
        configured fight-button region.  This uses window geometry and input
        only; it does not require Screen Recording or OCR.  Two clicks are
        intentional: first dismisses ``CHOISIR LES CHALLENGES``, second presses
        ``PRÊT``.  The human remains responsible for combat actions.
        """
        result = (self._network_status or {}).get("result") or {}
        fight = result.get("fight") or {}
        if not fight.get("active") or fight.get("phase") not in {"placement", "turn", "turn_end"}:
            raise ValueError("semi-auto fight is available only during character placement")
        focus = getattr(self.agent.ctl, "focus", None)
        if callable(focus):
            await asyncio.to_thread(focus)
        win = await asyncio.to_thread(self.agent.ctl.find_window)
        content = getattr(win, "content", None) if win is not None else None
        if content is None:
            raise ValueError("could not locate the Dofus content window")
        # DEFAULT_RECT from perception.fight_button: centre of the button.
        x = (0.650 + 0.130 / 2.0) * float(content.w)
        y = (0.905 + 0.075 / 2.0) * float(content.h)
        for label in ("skip challenges", "ready"):
            await self.agent.executor.execute({
                "kind": "click", "x": round(x, 1), "y": round(y, 1),
                "reason": f"semi-auto fight: {label}",
            })
            await asyncio.sleep(1.0)
        self.bus.log("info", "user", "semi-auto fight: challenges skipped, PRÊT pressed; human control remains")

    async def move_map(self, direction: str) -> None:
        """Click just beyond a viable edge to request a role-play map change."""
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("invalid map direction")
        if self._selected_result().get("fight", {}).get("active"):
            raise ValueError("map movement is disabled during a fight")
        result = self._selected_result()
        world = result.get("world") or {}
        player_cell = (result.get("character") or {}).get("cell")
        # Scale the calibrated grid to the current content rectangle before
        # calculating the off-grid point, as map movement does not use pixels
        # from a screen capture.
        win = await asyncio.to_thread(self.agent.ctl.find_window)
        content = getattr(win, "content", None) if win is not None else None
        ww = float(content.w) if content is not None else self.agent._window_w()
        wh = float(content.h) if content is not None else self.agent._window_h()
        grid = self.agent.perceiver.grid_for(ww, wh)
        cell, x, y = map_exit_click_point(grid, direction, player_cell, world.get("cells"))
        await self.click_point(x, y, f"change map {direction} via edge cell {cell}", cell)
        self.bus.log("info", "user", f"map move requested: {direction}, exit cell {cell}, outside point {x:.0f},{y:.0f}")

    async def calibrate_cell(self, cell: int) -> None:
        """Record the cursor after the user clicks a known mob cell."""
        if not 0 <= int(cell) < 560:
            raise ValueError("invalid calibration cell")
        focus = getattr(self.agent.ctl, "focus", None)
        if callable(focus):
            await asyncio.to_thread(focus)
        self.bus.log("info", "calibration", f"click the mob on cell {cell} within 4 seconds")
        await asyncio.sleep(4.0)
        ctl = getattr(self.agent.ctl, "inner", self.agent.ctl)
        if not hasattr(ctl, "b"):
            raise ValueError("mouse calibration requires the real Dofus controller")
        pos = await asyncio.to_thread(ctl.b.mouse_position)
        win = await asyncio.to_thread(ctl.find_window)
        content = getattr(win, "content", None) if win is not None else None
        if content is None:
            raise ValueError("could not read the Dofus window")
        self._grid_samples.append((int(cell), float(pos[0] - content.x), float(pos[1] - content.y)))
        self.bus.log("info", "calibration", f"recorded cell {cell} ({len(self._grid_samples)}/3)")
        if len(self._grid_samples) < 3:
            return
        xmat = np.array([[1.0, c % 14 + (c // 14 % 2) / 2] for c, _, _ in self._grid_samples])
        ymat = np.array([[1.0, c // 14 / 2] for c, _, _ in self._grid_samples])
        ox, cw = np.linalg.lstsq(xmat, np.array([x for _, x, _ in self._grid_samples]), rcond=None)[0]
        # Dofus cells are fixed 2:1 diamonds. Fitting x/y independently lets
        # one mistimed sample shear the grid and sends clicks far off-map.
        ch = abs(float(cw)) / 2.0
        oy = float(np.mean([y - (c // 14) * ch / 2.0 for c, _, y in self._grid_samples]))
        cfg = self.agent.perceiver.grid_cfg
        cfg.update({"ox": float(ox), "oy": float(oy), "cw": abs(float(cw)), "ch": ch, "calibrated": True})
        self.agent.perceiver.base_grid = self.agent.perceiver.base_grid.__class__.from_config(cfg)
        (brain_home() / "config" / "grid.json").write_text(json.dumps(cfg, indent=2) + "\n")
        self._grid_samples.clear()
        self.bus.log("info", "calibration", f"grid saved: ox={ox:.1f} oy={oy:.1f} cw={cw:.1f} ch={ch:.1f}")
        self.broadcast("calibration.status", {"state": "fitted", "message": f"Fitted and saved: ox={ox:.1f}, oy={oy:.1f}, cw={cw:.1f}, ch={ch:.1f}", "samples": 3})

    async def calibrate_player(self) -> None:
        """Use a real player movement as a cell/screen calibration sample."""
        focus = getattr(self.agent.ctl, "focus", None)
        if callable(focus):
            await asyncio.to_thread(focus)
        before_scan = await asyncio.to_thread(parse_capture, self._network_path) if os.path.exists(self._network_path) else None
        own_id = before_scan.character.entity_id if before_scan else None
        if own_id is None:
            raise ValueError("your character has not been identified in the live capture; refresh the network scan first")
        before_moves = [e.get("end_cell") for e in before_scan.map_events if e.get("kind") == "movement" and e.get("actor_id") == own_id]
        before_last = before_moves[-1] if before_moves else None
        self.bus.log("info", "calibration", "move your character to a new cell within 10 seconds")
        self.broadcast("calibration.status", {"state": "waiting", "message": "move your character now", "samples": len(self._grid_samples)})
        await asyncio.sleep(10.0)
        if not os.path.exists(self._network_path):
            raise ValueError("no live network capture available")
        scan = await asyncio.to_thread(parse_capture, self._network_path)
        movement = next((e for e in reversed(scan.map_events) if e.get("kind") == "movement" and e.get("actor_id") == own_id and e.get("end_cell") is not None and e.get("end_cell") != before_last), None)
        ctl = getattr(self.agent.ctl, "inner", self.agent.ctl)
        if movement is None or not hasattr(ctl, "b"):
            raise ValueError("no new player movement was captured; move to a different cell and try again")
        pos = await asyncio.to_thread(ctl.b.mouse_position)
        win = await asyncio.to_thread(ctl.find_window)
        content = getattr(win, "content", None) if win is not None else None
        if content is None:
            raise ValueError("could not read the Dofus window")
        cell = int(movement["end_cell"])
        rel_x, rel_y = float(pos[0] - content.x), float(pos[1] - content.y)
        self.bus.log("info", "calibration", f"sample measured: cell={cell} screen=({pos[0]:.1f},{pos[1]:.1f}) content=({content.x:.1f},{content.y:.1f},{content.w:.1f}x{content.h:.1f}) relative=({rel_x:.1f},{rel_y:.1f})")
        self._grid_samples.append((cell, rel_x, rel_y))
        self.broadcast("calibration.status", {"state": "measured", "message": f"cell {cell}: screen ({pos[0]:.1f}, {pos[1]:.1f}) → relative ({rel_x:.1f}, {rel_y:.1f})", "samples": len(self._grid_samples), "cell": cell, "screen": [pos[0], pos[1]], "relative": [rel_x, rel_y]})
        self.bus.log("info", "calibration", f"recorded moved player at cell {cell} ({len(self._grid_samples)}/3)")
        if len(self._grid_samples) >= 3:
            xmat = np.array([[1.0, c % 14 + (c // 14 % 2) / 2] for c, _, _ in self._grid_samples])
            ymat = np.array([[1.0, c // 14 / 2] for c, _, _ in self._grid_samples])
            ox, cw = np.linalg.lstsq(xmat, np.array([x for _, x, _ in self._grid_samples]), rcond=None)[0]
            ch = abs(float(cw)) / 2.0
            oy = float(np.mean([y - (c // 14) * ch / 2.0 for c, _, y in self._grid_samples]))
            residuals = [float(np.hypot(x - (ox + (c % 14 + (c // 14 % 2) / 2) * abs(float(cw))),
                                         y - (oy + (c // 14) * ch / 2.0)))
                         for c, x, y in self._grid_samples]
            if max(residuals) > 12.0:
                self._grid_samples.clear()
                raise ValueError(f"calibration rejected: samples disagree by {max(residuals):.1f}px; use three widely separated destination cells")
            expected_cw = 68.8 * float(content.w) / 1280.0
            if not (0.75 * expected_cw <= abs(float(cw)) <= 1.25 * expected_cw):
                self._grid_samples.clear()
                raise ValueError(f"calibration rejected: measured cell width {abs(float(cw)):.1f} is inconsistent with the Dofus window ({expected_cw:.1f}px expected)")
            cfg = self.agent.perceiver.grid_cfg
            cfg.update({"ox": float(ox), "oy": float(oy), "cw": abs(float(cw)), "ch": ch, "calibrated": True,
                        "window_points": [float(content.w), float(content.h)]})
            self.agent.perceiver.base_grid = self.agent.perceiver.base_grid.__class__.from_config(cfg)
            (brain_home() / "config" / "grid.json").write_text(json.dumps(cfg, indent=2) + "\n")
            self._grid_samples.clear()
            self.bus.log("info", "calibration", f"grid saved: ox={ox:.1f} oy={oy:.1f} cw={cw:.1f} ch={ch:.1f}; residual={max(residuals):.1f}px")
            self.broadcast("calibration.status", {"state": "fitted", "message": f"Fitted and saved · max error {max(residuals):.1f}px", "samples": 3})

    def start_player_calibration(self) -> None:
        if self._calibration_task is not None and not self._calibration_task.done():
            raise ValueError("a player calibration sample is already waiting")
        async def run() -> None:
            try:
                await self.calibrate_player()
            except Exception as exc:
                self.bus.log("warn", "calibration", f"player calibration failed: {exc}")
                self.broadcast("calibration.status", {"state": "error", "message": str(exc), "samples": len(self._grid_samples)})
        self._calibration_task = asyncio.create_task(run())

    def kill(self, source: str) -> None:
        self.agent.kill(source)
        self._disarm(f"kill switch ({source})")

    def _disarm(self, why: str) -> None:
        self.armed = False
        self.slot.suspend(why)
        self.send_planner(force=True)

    async def start_session(self) -> None:
        """'Start': align Dofus onto the slot and keep it in front (~1x/s) until the kill
        switch; primes the other buttons. Needs the kill switch reset (steer.resume) first."""
        if self.agent.status.state == "stopped":
            raise ValueError("kill switch is on: press Resume first")
        self.slot.keep_on_top = True
        self.slot.suspended = None
        await asyncio.to_thread(self.slot.ctl.keep_on_top, True)
        try:
            await self.slot.align()
        except ValueError as e:      # no slot report yet: keep-on-top still runs
            self.bus.log("warn", "control", f"start: {e}")
        self.armed = True
        self.bus.log("info", "user", "START: Dofus aligned and kept in front until the kill switch")
        self.slot._maybe_emit()
        self.send_planner(force=True)

    def _need_armed(self) -> None:
        if not self.armed:
            raise ValueError("control is not activated")

    def status_payload(self) -> dict:
        return self.agent.status_json(window_found=self.slot.window.get("found"))

    def on_bus(self, kind: str, payload: Any) -> None:
        if self.loop is None:
            return
        if threading.get_ident() != self.loop_thread:
            self.loop.call_soon_threadsafe(self._dispatch, kind, payload)
        else:
            self._dispatch(kind, payload)

    def _dispatch(self, kind: str, payload: Any) -> None:
        if kind == "log":
            self.broadcast("log.entry", payload)
        elif kind == "status":
            self.send_planner()
            if not self._status_scheduled:  # coalesce several changes in one loop iteration
                self._status_scheduled = True
                self.loop.call_soon(self._send_status)  # type: ignore[union-attr]
        elif kind == "state":
            self._schedule_state()
            self.send_planner()
        elif kind == "overlay":
            self.broadcast("overlay.status", payload)
        elif kind == "frame":
            self._maybe_preview(payload)

    def _send_status(self) -> None:
        self._status_scheduled = False
        self.broadcast("agent.status", self.status_payload())

    def _schedule_state(self) -> None:
        min_dt = 1.0 / float(self.cfg.get("state_max_hz", 10.0))
        if self._state_scheduled:
            return
        wait = self._state_last + min_dt - time.monotonic()
        if wait <= 0:
            self._send_state()
        else:
            self._state_scheduled = True
            self.loop.call_later(wait, self._send_state)  # type: ignore[union-attr]

    def _send_state(self) -> None:
        self._state_scheduled = False
        self._state_last = time.monotonic()
        self.broadcast("game.state", self.agent.game.to_json())

    def _maybe_preview(self, frame: Any) -> None:
        if not any(c.preview for c in self.clients) or self._preview_busy:
            return
        if time.monotonic() - self._preview_last < float(self.cfg.get("preview_interval_s", 1.0)):
            return
        self._preview_last = time.monotonic()
        self._preview_busy = True
        self.loop.create_task(self._send_preview(frame))  # type: ignore[union-attr]

    async def _send_preview(self, frame: Any) -> None:
        try:
            payload = await asyncio.to_thread(encode_preview, frame.image, int(self.cfg.get("preview_max_width", 640)))
            self.broadcast("frame.preview", payload, only_preview=True)
        finally:
            self._preview_busy = False

    # ------------------------------------------------------------------ incoming
    async def handle(self, client: Client, msg: dict) -> None:
        t = msg.get("type")
        p = msg.get("payload") or {}
        mid = msg.get("id")
        try:
            await self._route(client, t, p)
        except Exception as e:
            if mid is not None:
                client.put(envelope("ack", {"ok": False, "error": f"{type(e).__name__}: {e}"}, mid))
            self.bus.log("warn", "server", f"{t} failed: {e}")
            return
        if mid is not None:
            client.put(envelope("ack", {"ok": True}, mid))

    async def _route(self, client: Client, t: str | None, p: dict) -> None:
        a = self.agent
        if t == "steer.goal":
            text = p.get("text")
            if text is not None and not isinstance(text, str):
                raise ValueError("text must be a string")
            a.set_goal(text)
        elif t == "steer.pause":
            a.pause()
        elif t == "steer.resume":
            a.resume()                       # window management stays off until Start again
        elif t == "steer.mode":
            a.set_mode(p.get("mode"))
        elif t == "steer.kill":
            self.kill("user")
        elif t == "steer.reset":
            a.reset()
            self.send_planner(force=True)
        elif t == "session.start":
            await self.start_session()
        elif t == "npcs.scan":
            self._need_armed()
            await self.scan_npcs()
        elif t == "network.scan.start":
            await self.start_network_scan()
        elif t == "dofus.launch":
            await self.launch_dofus(str(p.get("profile_id", "default")))
        elif t == "dofus.authenticate":
            await self.authenticate_ankama(str(p.get("profile_id", "default")))
        elif t == "accounts.select":
            self.select_account(int(p.get("account_id")))
        elif t == "accounts.profile.create":
            self.create_account_profile(str(p.get("label", "")))
        elif t == "accounts.profile.rename":
            self.rename_account_profile(str(p.get("profile_id", "")), str(p.get("label", "")))
        elif t == "network.scan.stop":
            await self.stop_network_scan()
        elif t == "network.scan.refresh":
            path = self._network_path if os.path.exists(self._network_path) else None
            if path:
                result = scan_json(await asyncio.to_thread(parse_capture, path))
                result["capture_path"] = path
                self._attach_map_asset(result)
                self._network_status = {"state": "listening" if self._network_proc else "ready", "result": result, "error": None}
                self._send_network()
            else:
                self._load_latest_network_capture()
                self._send_network()
        elif t == "npcs.attack":
            if p.get("entity_id") is not None:
                # Protocol action: group identity comes from the current
                # network snapshot, never from a screen coordinate.
                self.execute_agent_intent("attack_monster_group", {"entity_id": int(p["entity_id"])}, "dashboard")
            elif p.get("cell") is not None:
                # Network-derived attack targets use the semi-auto one-click
                # path; they do not depend on the phases planner.
                await self.click_cell(int(p["cell"]))
            else:
                self._need_armed()
                self.attack(p.get("index"), p.get("name"))
            if p.get("auto", True):
                a.set_mode("auto")
        elif t == "npcs.talk":
            await self.talk_to_npc(int(p.get("entity_id")), int(p.get("cell")))
        elif t == "dialog.reply":
            await self.choose_dialog_reply(int(p.get("reply_id")))
        elif t == "dialog.leave":
            await self.leave_dialog()
        elif t == "agent.intent":
            self.execute_agent_intent(str(p.get("action", "")), dict(p.get("args") or {}), str(p.get("reason") or ""))
        elif t == "agent.propose_next":
            await self.propose_next_scheduled_action(str(p.get("goal", "")))
        elif t == "agent.step":
            await self.run_agent_step(str(p.get("goal", "")))
        elif t == "agent.goal.start":
            await self.start_agent_goal(str(p.get("goal", "")))
        elif t == "agent.goal.pause":
            self.pause_agent_goal()
        elif t == "agent.goal.resume":
            await self.resume_agent_goal()
        elif t == "world.navigate":
            self.start_world_navigation(int(p.get("target_map_id")))
        elif t == "quest.next":
            self.execute_next_quest_step()
        elif t == "session.plan":
            self.configure_session_plan(int(p.get("duration_minutes", 180)), list(p.get("jobs") or []))
        elif t == "interactives.use":
            await self.use_interactive(int(p.get("id")), int(p.get("cell")))
        elif t == "inventory.use":
            self.execute_agent_intent("use_inventory_item", {"object_uid": int(p.get("object_uid"))}, "dashboard")
        elif t == "fight.start":
            # Thomas starts this during character placement
            self._need_armed()
            a.set_goal("fight sequence")
            if p.get("auto", True):
                a.set_mode("auto")
        elif t == "fight.semi_auto":
            await self.semi_auto_fight()
        elif t == "map.move":
            await self.move_map(str(p.get("direction", "")))
        elif t == "steer.step":
            await a.step()
        elif t == "slot.report":
            await self.slot.on_report(p)
        elif t == "overlay.align":
            await self.slot.align()
        elif t == "overlay.calibrate":
            await self.slot.calibrate()
        elif t == "overlay.adopt_current":
            await self.slot.adopt_current()
        elif t == "grid.calibrate.cell":
            await self.calibrate_cell(int(p.get("cell")))
        elif t == "grid.calibrate.player":
            self.start_player_calibration()
        elif t == "preview.subscribe":
            client.preview = bool(p.get("enabled"))
            if client.preview:
                self._preview_last = 0.0
        else:
            raise ValueError(f"unknown message type {t!r}")

    def initial(self, client: Client) -> None:
        client.put(envelope("hello", {"version": PROTOCOL_VERSION, "server": "dofus-brain", "mock": self.mock}))
        client.put(envelope("agent.status", self.status_payload()))
        client.put(envelope("game.state", self.agent.game.to_json()))
        client.put(envelope("log.history", {"entries": list(self.bus.history)}))
        client.put(envelope("overlay.status", self.slot.status()))
        client.put(envelope("npcs.list", self.npcs_payload()))
        client.put(envelope("network.scan.status", self.network_payload()))
        client.put(envelope("dofus.status", dict(self._dofus_status)))
        client.put(envelope("transport.status", self.managed_transport.status()))
        client.put(envelope("accounts.status", self.accounts_payload()))
        pp = self.planner_payload()
        if pp is not None:
            client.put(envelope("planner.status", pp))


def encode_preview(img: Any, max_w: int) -> dict:
    from PIL import Image

    im = Image.fromarray(img)
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)), Image.BILINEAR)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=70)
    return {"jpeg_b64": base64.b64encode(buf.getvalue()).decode("ascii"), "width": im.width, "height": im.height}


def create_app(mock: bool = False, controller: Any = None, llm: LLMClient | None = None,
               planner: Planner | None = None, config: BrainConfig | None = None,
               agent_overrides: dict | None = None, app_name: str = "Dofus",
               title_contains: str | None = None, corpus_dir: str | None = None,
               network_only: bool = False, screen_capture: bool = False) -> FastAPI:
    cfg = config or BrainConfig.load()
    agent_cfg = {**cfg.agent, **(agent_overrides or {})}
    bus = Bus(history=200)
    if controller is None:
        controller, mock_ctl = make_controller(mock, app_name=app_name, title_contains=title_contains)
        mock = mock or mock_ctl
    is_mock_ctl = type(controller).__name__ == "MockController"
    grid_cfg, regions_cfg = cfg.grid, cfg.regions
    if is_mock_ctl:  # synthetic frames were drawn with the fixture geometry
        grid_cfg = getattr(controller, "grid_cfg", grid_cfg)
        regions_cfg = getattr(controller, "regions_cfg", regions_cfg)
    ocr = make_ocr(regions_cfg.get("ocr", {}), mock=is_mock_ctl)
    perceiver = Perceiver(grid_cfg, regions_cfg, ocr, vlm_threshold=float(agent_cfg.get("classifier_vlm_threshold", 0.55)))
    if llm is None:
        llm = make_llm(cfg.llm, backend="mock" if mock and cfg.llm.get("backend") != "mock" and not cfg.llm.get("use_real_in_mock") else None)
    recorder = CorpusRecorder(corpus_dir) if corpus_dir else None
    agent = Agent(controller, perceiver, llm, bus, agent_cfg, planner=planner, mock=mock,
                  corpus_recorder=recorder)
    slot = SlotManager(controller, bus, cfg.directory, keep_on_top=bool(agent_cfg.get("keep_on_top", True)))
    hub = Hub(agent, slot, bus, agent_cfg, mock, llm)
    from .world.map_data import GameIconProvider
    icons = GameIconProvider()
    tasks: list[asyncio.Task] = []

    async def status_ticker() -> None:
        while True:
            await asyncio.sleep(float(agent_cfg.get("status_interval_s", 2.0)))
            hub.broadcast("agent.status", hub.status_payload())
            hub.send_planner(force=True)

    async def slot_ticker() -> None:
        while True:
            with contextlib.suppress(Exception):
                await slot.periodic()
            await asyncio.sleep(2.0)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind(asyncio.get_running_loop())
        bus.log("info", "server", f"dofus-brain {__version__} starting (mock={mock}, llm={llm.backend}:{llm.model}, "
                f"ocr={ocr.name}, planner={agent.planner.name})")
        tasks.extend([asyncio.create_task(status_ticker()), asyncio.create_task(slot_ticker())])
        # Screen perception is opt-in. Packet parsing and one-shot control
        # clicks do not require macOS Screen Recording permission.
        if screen_capture and not network_only:
            tasks.append(asyncio.create_task(agent.run()))
        try:
            yield
        finally:
            agent.stop()
            if hub._network_proc is not None:
                hub._network_proc.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(hub._network_proc.communicate)
            if hub._network_monitor_task is not None:
                hub._network_monitor_task.cancel()
                with contextlib.suppress(BaseException):
                    await hub._network_monitor_task
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t
            with contextlib.suppress(Exception):
                await llm.aclose()
            with contextlib.suppress(Exception):
                controller.close()

    app = FastAPI(title="dofus-brain", version=__version__, lifespan=lifespan)
    app.state.agent, app.state.hub, app.state.controller, app.state.bus, app.state.slot = agent, hub, controller, bus, slot

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "server": "dofus-brain", "version": PROTOCOL_VERSION, "mock": mock,
                "agent": agent.status.state, "mode": agent.status.mode, "clients": len(hub.clients)}

    @app.get("/api/quests")
    async def quests(query: str = "", limit: int = 20) -> dict:
        """Read-only local quest database search for future agent tools."""
        return {"query": query, "results": hub._game_data.search_quests(query, limit)}

    @app.get("/api/quest-graph/{quest_id}")
    async def quest_graph(quest_id: int) -> dict:
        """Local progression graph: prerequisites, unlocks and success groups."""
        return hub._game_data.quest_graph(quest_id)

    @app.get("/api/quest-plan")
    async def quest_plan() -> dict:
        return hub.quest_plan_payload()

    @app.get("/api/routes/{target_map_id}")
    async def route(target_map_id: int) -> dict:
        """Read-only route using explicit world-graph transitions."""
        return hub.world_route_payload(target_map_id)

    @app.get("/api/interactives/{element_id}/plan")
    async def interactive_plan(element_id: int) -> dict:
        """Read-only role-play approach/use plan for a live map element."""
        return hub.interactive_plan_payload(element_id)

    @app.get("/api/guides/success/{achievement_id}")
    async def guide_success(achievement_id: int) -> dict:
        """Read the compact preflight metadata for one guide-backed success."""
        return hub._guides.resolve_success(achievement_id)

    @app.get("/api/guides/{quest_id}")
    async def guide(quest_id: int, fetch: bool = False) -> dict:
        """Resolve, or explicitly fetch, one human-written quest walkthrough.

        ``fetch=false`` is local-only. ``fetch=true`` reads at most two paced
        pages (success index then the individual guide), retaining nothing
        after the response is sent.
        """
        quest_info = hub._game_data.quest_info(quest_id)
        name = quest_info.get("name")
        try:
            if fetch:
                return await asyncio.to_thread(hub._guides.fetch_guide, quest_id, name)
            return hub._guides.resolve(quest_id, name)
        except GuideUnavailable as exc:
            return {"found": False, "quest_id": quest_id, "quest_name": name, "reason": str(exc)}

    @app.get("/api/quests/{quest_id}")
    async def quest(quest_id: int) -> dict:
        """Read-only full quest/step record from installed game data."""
        return hub._game_data.quest_record(quest_id)

    @app.get("/api/agent/context")
    async def agent_context() -> dict:
        """One compact, versioned local contract for the future game agent."""
        return hub.agent_context_payload()

    @app.get("/api/accounts")
    async def accounts() -> dict:
        """Central manager status for the dashboard account tabs."""
        return hub.accounts_payload()

    @app.post("/api/agent/intent")
    async def agent_intent(payload: dict[str, Any] = Body(...)) -> dict:
        """Execute exactly one validated domain action through managed socket mode."""
        try:
            return hub.execute_agent_intent(str(payload.get("action", "")), dict(payload.get("args") or {}),
                                            str(payload.get("reason") or ""))
        except (ValueError, RuntimeError) as exc:
            # Agent inputs are normal validation failures, not server faults.
            # A deterministic 400 lets the caller repair its plan instead of
            # treating a stale entity/quest as an infrastructure outage.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/agent/propose")
    async def agent_propose(payload: dict[str, Any] = Body(...)) -> dict:
        """Return one local-model proposal; this endpoint never executes it."""
        goal = payload.get("goal", "")
        if not isinstance(goal, str) or not goal.strip():
            raise HTTPException(status_code=400, detail="goal must be a non-empty string")
        try:
            return await hub.propose_agent_action(goal.strip())
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/agent/step")
    async def agent_step(payload: dict[str, Any] = Body(...)) -> dict:
        """Plan and execute one validated local-LLM action from live state."""
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            raise HTTPException(422, "goal is required")
        try:
            return await hub.run_agent_step(goal)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/agent/goal")
    async def agent_goal() -> dict:
        return hub.agent_goal_payload()

    @app.post("/api/agent/goal")
    async def start_agent_goal(payload: dict[str, Any] = Body(...)) -> dict:
        try:
            return await hub.start_agent_goal(str(payload.get("goal") or ""))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/agent/goal/pause")
    async def pause_agent_goal() -> dict:
        try:
            return hub.pause_agent_goal()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/agent/goal/resume")
    async def resume_agent_goal() -> dict:
        try:
            return await hub.resume_agent_goal()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/assets/items/{item_id}.png")
    async def item_icon(item_id: int) -> Response:
        """A real installed-game item sprite; 404 means that bundle has none."""
        image = icons.item_png(item_id)
        if image is None:
            return Response(status_code=404)
        return Response(image, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/assets/spells/{icon_id}.png")
    async def spell_icon(icon_id: int) -> Response:
        image = icons.spell_png(icon_id)
        if image is None:
            return Response(status_code=404)
        return Response(image, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        client = Client(ws)
        hub.clients.add(client)
        client.task = asyncio.create_task(client.pump())
        hub.initial(client)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        raise ValueError("message must be a JSON object")
                except ValueError as e:
                    client.put(envelope("log.entry", {"ts": now_ms(), "level": "warn", "source": "server",
                                                      "message": f"bad message: {e}"}))
                    continue
                await hub.handle(client, msg)
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(client)
            client.queue.put_nowait(None)
            if client.task:
                client.task.cancel()

    return app


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m dofus_brain.server")
    ap.add_argument("--mock", action="store_true", help="MockController (fixtures) + mock LLM")
    ap.add_argument("--network-only", action="store_true", help="Disable real window/control access; expose only packet scanning")
    ap.add_argument("--screen", action="store_true", help="Enable the screen-perception loop (requires macOS Screen Recording permission)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--planner", default="noop", choices=sorted(PLANNERS))
    ap.add_argument("--llm", choices=["ollama", "mlx", "mock"], help="override config/llm.json backend")
    ap.add_argument("--model")
    ap.add_argument("--mode", choices=["observe", "assist", "auto"], default="observe")
    ap.add_argument("--app", default="Dofus", help="target app name (e.g. TextEdit for testing without Dofus)")
    ap.add_argument("--title", help="only windows whose title contains this")
    ap.add_argument("--capture-dir", help="save deduplicated local frames + label metadata for VLM evaluation; no input")
    ap.add_argument("--log-level", default="info")
    a = ap.parse_args(argv)
    logging.basicConfig(level=a.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = BrainConfig.load()
    llm = make_llm(cfg.llm, backend=a.llm, model=a.model) if (a.llm or a.model) else None
    # Network-only deliberately uses the inert controller. Packet capture remains real;
    # no Dofus window is located, moved, clicked, typed into, or otherwise controlled.
    app = create_app(mock=(a.mock or a.network_only), llm=llm, planner=PLANNERS[a.planner](), config=cfg,
                     app_name=a.app, title_contains=a.title, corpus_dir=a.capture_dir,
                     network_only=a.network_only, screen_capture=a.screen)
    app.state.agent.status.mode = a.mode
    import uvicorn

    uvicorn.run(app, host=a.host, port=a.port, log_level=a.log_level.lower())


if __name__ == "__main__":
    main()
