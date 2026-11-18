"""Defaults for config/phases.json (keys, spells, aggro rules, popup lists, safety limits).

Keys are named constants only: nothing here sends them. The agent sends a key only when a
skill proposes it and the mode allows acting.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

PHASES_DEFAULTS: dict[str, Any] = {
    "keys": {
        "highlight_interactives": "y",   # Dofus: show interactive elements
        "show_entities": "z",            # Dofus: show NPC / monster names
        "ready": "f1",
        "end_turn": "f1",
        "dismiss": "escape",
        "spells": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    },
    "key_mode": "hold",                  # hold: overlay shows only while the key is held (Dofus)
    "scan_settle_ms": 250,               # wait after key down before capturing the overlay
    "safety": {
        "unknown_stop_s": 8.0,           # unknown / low-confidence screen this long -> pause and ask
        "min_confidence": 0.55,
        "max_consecutive_failures": 3,
        "allow_unverified_kinds": ["wait"],
    },
    "travel": {
        "edge_cells_per_side": 3,        # different edge cells tried before an edge is marked blocked
        "max_stuck_steps": 6,            # failed steps in a row on the same map -> give up
        "maps_file": "data/maps.json",
    },
    "fight": {
        "character_class": None,         # None = use agent.character_class
        "turn_budget_frac": 0.7,
        "max_pa": 6,                     # turn-start PA/PM (Cra level 10); tracked when unreadable
        "max_pm": 3,
        "classes": {
            "iop": {"role": "melee", "spells": [
                {"name": "Pression", "key": "1", "pa": 3, "min_range": 1, "max_range": 1,
                 "los": True, "line": False, "damage": 14, "per_turn": 2, "per_target": 2},
                {"name": "Bond", "key": "2", "pa": 5, "min_range": 1, "max_range": 4,
                 "los": False, "line": False, "damage": 0, "per_turn": 1, "kind": "teleport"},
            ]},
            "cra": {"role": "ranged", "spells": [
                {"name": "Fleche Magique", "key": "1", "pa": 4, "min_range": 1, "max_range": 8,
                 "los": True, "line": False, "damage": 18, "per_turn": 2, "per_target": 2,
                 "range_modifiable": True},
                {"name": "Fleche de Recul", "key": "2", "pa": 3, "min_range": 1, "max_range": 6,
                 "los": True, "line": True, "damage": 10, "per_turn": 1, "per_target": 1},
            ]},
        },
        "keep_distance": {"ranged": 4, "melee": 0},
    },
    "aggro": {
        "default_radius_cells": 4,
        "rules": [
            # first matching rule wins; name is a case-insensitive substring
            {"name": "", "max_group_size": 3, "max_level_ratio": 1.5, "engage": True},
            {"name": "", "engage": False},
        ],
        "aggressive_names": [],          # names known to attack on sight (filled as we learn)
        "danger_cost": 6.0,              # extra route cost for a map with a dangerous group
    },
    "harvest": {
        "min_blob_px": 40,
        "diff_threshold": 40,
        "progress_timeout_s": 8.0,
    },
    "popups": {
        "dismiss": ["niveau", "level", "succes", "succès", "achievement", "astuce", "conseil",
                    "information", "felicitations", "félicitations", "recompense", "récompense",
                    "victoire", "combat termine", "combat terminé"],
        "ask": ["echange", "échange", "groupe", "duel", "guilde", "invitation", "message prive",
                "message privé", "defi", "défi"],
    },
    "quest": {
        "llm_dialog": True,
        "min_match_score": 0.34,
        "forbidden_option_words": ["acheter", "vendre", "abandonner", "payer", "kamas", "buy", "sell", "abandon"],
        "continue_words": ["continuer", "suivant", "suite", "d'accord", "ok", "continue", "next"],
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_phases_config(directory: Path | None = None, override: dict | None = None) -> dict[str, Any]:
    """config/phases.json merged over PHASES_DEFAULTS (missing file = defaults)."""
    data: dict[str, Any] = {}
    if directory is None:
        from ..paths import config_dir

        directory = config_dir()
    p = Path(directory) / "phases.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    cfg = _merge(PHASES_DEFAULTS, data)
    return _merge(cfg, override or {})


def allowed_keys(cfg: dict) -> set[str]:
    k = cfg["keys"]
    out = {str(v).lower() for n, v in k.items() if n != "spells"}
    out |= {str(s).lower() for s in k.get("spells", [])}
    return out
