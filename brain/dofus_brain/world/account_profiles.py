"""Local-only account profile registry.

Profiles contain a display label and point at a separate OAuth/HAAPI token
file. Credentials never enter the dashboard payload or git; each token file is
restricted to the local user (0600).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not result:
        raise ValueError("profile label must contain a letter or number")
    return result[:48]


@dataclass(frozen=True)
class AccountProfile:
    id: str
    label: str
    token_path: Path

    def public(self) -> dict[str, object]:
        return {"id": self.id, "label": self.label, "authenticated": self.token_path.is_file()}


class AccountProfileStore:
    def __init__(self, config_dir: str | Path):
        self.config_dir = Path(config_dir)
        self.path = self.config_dir / "accounts.json"
        self.tokens = self.config_dir / "accounts"

    def list(self) -> list[AccountProfile]:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError, TypeError):
            raw = []
        entries = raw.get("profiles", []) if isinstance(raw, dict) else raw
        profiles: list[AccountProfile] = []
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("label"), str):
                continue
            profiles.append(AccountProfile(item["id"], item["label"], self.tokens / f"{item['id']}.json"))
        return profiles

    def create(self, label: str) -> AccountProfile:
        label = str(label).strip()
        profile_id = _slug(label)
        profiles = self.list()
        if any(profile.id == profile_id for profile in profiles):
            raise ValueError(f"account profile {label!r} already exists")
        profile = AccountProfile(profile_id, label, self.tokens / f"{profile_id}.json")
        self._write([*profiles, profile])
        return profile

    def get(self, profile_id: str) -> AccountProfile:
        for profile in self.list():
            if profile.id == str(profile_id):
                return profile
        raise ValueError("unknown account profile")

    def rename(self, profile_id: str, label: str) -> AccountProfile:
        profile = self.get(profile_id)
        label = str(label).strip()
        if not label:
            raise ValueError("profile label must not be empty")
        updated = AccountProfile(profile.id, label, profile.token_path)
        self._write([updated if item.id == profile.id else item for item in self.list()])
        return updated

    def _write(self, profiles: list[AccountProfile]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.tokens.mkdir(parents=True, exist_ok=True)
        data = {"profiles": [{"id": profile.id, "label": profile.label} for profile in profiles]}
        self.path.write_text(json.dumps(data, indent=2) + "\n")
        os.chmod(self.path, 0o600)
