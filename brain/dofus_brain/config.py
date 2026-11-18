"""JSON config loading. Files live in brain/config/*.json.

Keys starting with "_" are comments and are ignored by code (JSON has no comments).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import config_dir

DEFAULTS: dict[str, dict[str, Any]] = {
    "grid": {"ox": 180.0, "oy": 40.0, "cw": 68.0, "ch": 34.0, "rows": 40, "cols": 14, "calibrated": False},
    "agent": {
        "loop_hz": 4.0,
        "capture_timeout_s": 7.0,
        "status_interval_s": 2.0,
        "state_max_hz": 10.0,
        "preview_interval_s": 1.0,
        "preview_max_width": 640,
        "action_timeout_s": 6.0,
        "action_retries": 2,
        "stuck_after_s": 30.0,
        "llm_min_interval_s": 10.0,
        "classifier_vlm_threshold": 0.55,
        "vlm_hint_ttl_s": 10.0,
        "end_turn_key": "f1",
        "ready_key": "f1",
        "fight_strategy_default": "aggressive",
        "character_class": "cra",
        "keep_on_top": True,
    },
    "llm": {"backend": "mock", "model": "qwen3.5:4b", "timeout_s": 60.0, "retries": 2},
    "regions": {},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_json(name: str, directory: Path | None = None) -> dict[str, Any]:
    d = directory or config_dir()
    p = d / f"{name}.json"
    data: dict[str, Any] = {}
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    return _deep_merge(DEFAULTS.get(name, {}), data)


def save_json(name: str, data: dict[str, Any], directory: Path | None = None) -> Path:
    d = directory or config_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


@dataclass
class BrainConfig:
    grid: dict[str, Any] = field(default_factory=lambda: load_json("grid"))
    regions: dict[str, Any] = field(default_factory=lambda: load_json("regions"))
    llm: dict[str, Any] = field(default_factory=lambda: load_json("llm"))
    agent: dict[str, Any] = field(default_factory=lambda: load_json("agent"))
    directory: Path = field(default_factory=config_dir)

    @classmethod
    def load(cls, directory: Path | None = None, **overrides: dict[str, Any]) -> "BrainConfig":
        d = directory or config_dir()
        cfg = cls(
            grid=load_json("grid", d),
            regions=load_json("regions", d),
            llm=load_json("llm", d),
            agent=load_json("agent", d),
            directory=d,
        )
        for k, v in overrides.items():
            setattr(cfg, k, _deep_merge(getattr(cfg, k), v))
        return cfg
