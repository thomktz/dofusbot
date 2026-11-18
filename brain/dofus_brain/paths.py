"""Locations of config/, data/, fixtures/ (siblings of the package inside brain/).

Override the root with the DOFUS_BRAIN_HOME environment variable.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def brain_home() -> Path:
    env = os.environ.get("DOFUS_BRAIN_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return PACKAGE_DIR.parent


def config_dir() -> Path:
    return brain_home() / "config"


def data_dir() -> Path:
    return brain_home() / "data"


def fixtures_dir() -> Path:
    return brain_home() / "fixtures"


def prompts_dir() -> Path:
    return PACKAGE_DIR / "prompts"
