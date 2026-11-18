import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

BRAIN = Path(__file__).resolve().parents[1]
FIX = BRAIN / "fixtures"


@pytest.fixture(scope="session")
def meta() -> dict:
    p = FIX / "fixtures.json"
    if not p.exists():
        import subprocess
        import sys

        subprocess.run([sys.executable, str(BRAIN / "scripts" / "make_fixtures.py")], check=True)
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def load_fixture(meta):
    def _load(name: str) -> np.ndarray:
        return np.asarray(Image.open(FIX / name).convert("RGB"))
    return _load


@pytest.fixture
def config_dir(tmp_path) -> Path:
    d = tmp_path / "config"
    # ``ankama_auth.json`` is a local, potentially root-owned credential
    # cache created by the launch flow. Tests must never copy or depend on it.
    shutil.copytree(BRAIN / "config", d, ignore=shutil.ignore_patterns("slot.json", "glyphs", "ankama_auth.json"))
    return d
