"""On-demand bridge from a Dofus quest ID to its human-written guide.

The local game assets remain authoritative for the quest state.  This module
only resolves a source URL and, when explicitly requested, reads that one
public page for the local planner.  It never runs a background crawl and never
persists guide prose or images.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class GuideUnavailable(ValueError):
    pass


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9àâçéèêëîïôûùüÿñæœ]+", " ", text.casefold()).split())


class _PageParser(HTMLParser):
    """Extract article paragraphs, links and image references—not page chrome."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_h2 = False
        self._article_started = False
        self._article_image_depth = 0
        self._paragraph_depth = 0
        self._parts: list[str] = []
        self.sections: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "h2":
            self._in_h2 = True
        if tag == "div":
            classes = (attr.get("class") or "").split()
            if "paragraph" in classes:
                self._paragraph_depth += 1
            if self._article_started and "wsite-image" in classes:
                self._article_image_depth += 1
        if tag == "a" and self._paragraph_depth:
            self._link_href, self._link_text = attr.get("href"), []
        if tag == "img" and (self._paragraph_depth or self._article_image_depth) and attr.get("src"):
            self.images.append({"url": attr["src"] or "", "alt": attr.get("alt") or ""})
        if tag in {"br", "li", "p"} and self._paragraph_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._in_h2 = False
            self._article_started = True
        if tag == "a" and self._link_href is not None:
            label = " ".join("".join(self._link_text).split())
            if label:
                self.links.append((label, self._link_href))
            self._link_href, self._link_text = None, []
        if tag == "div":
            if self._article_image_depth:
                self._article_image_depth -= 1
            if self._paragraph_depth:
                self._paragraph_depth -= 1
                if self._paragraph_depth == 0:
                    section = " ".join("".join(self._parts).split())
                    if section:
                        self.sections.append(section)
                    self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_h2:
            self.title += data
        if self._paragraph_depth:
            self._parts.append(data)
            if self._link_href is not None:
                self._link_text.append(data)


@dataclass
class GuideResolver:
    """Resolve a quest via a small, user-curated source manifest."""

    manifest_path: Path | None = None
    fetcher: Callable[[str], bytes] | None = None
    min_interval_s: float = 5.0
    _last_fetch: float = 0.0

    def __post_init__(self) -> None:
        if self.manifest_path is None:
            self.manifest_path = Path(__file__).resolve().parents[1] / "data" / "guide-pilot" / "incarnam.json"
        if self.fetcher is None:
            self.fetcher = self._fetch

    def _manifest(self) -> dict:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError) as exc:
            raise GuideUnavailable("local guide-source manifest is unavailable") from exc

    def _fetch(self, url: str) -> bytes:
        wait = self.min_interval_s - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        request = Request(url, headers={"User-Agent": "DofusAgent on-demand guide lookup (personal local use)"})
        try:
            with urlopen(request, timeout=20) as response:
                content = response.read(1_500_001)
        except OSError as exc:
            raise GuideUnavailable(f"could not retrieve guide: {exc}") from exc
        self._last_fetch = time.monotonic()
        if len(content) > 1_500_000:
            raise GuideUnavailable("guide page exceeds the on-demand size limit")
        return content

    def resolve(self, quest_id: int, quest_name: str | None) -> dict:
        for group in self._manifest().get("success_groups", []):
            if int(quest_id) in {int(item) for item in group.get("quest_ids", [])}:
                return {
                    "found": True,
                    "quest_id": int(quest_id), "quest_name": quest_name,
                    "group": {key: group.get(key) for key in ("achievement_id", "name", "url", "preparation")},
                    "mode": "resolve_then_fetch",
                    "retention": "on-demand only; no guide prose or images are cached",
                }
        return {"found": False, "quest_id": int(quest_id), "quest_name": quest_name,
                "reason": "quest is not in the currently indexed guide pilot"}

    def resolve_success(self, achievement_id: int) -> dict:
        """Return the preflight information for one locally indexed success."""
        manifest = self._manifest()
        for group in manifest.get("success_groups", []):
            if int(group.get("achievement_id", -1)) != int(achievement_id):
                continue
            names = manifest.get("quest_names", {})
            return {
                "found": True,
                "achievement_id": int(achievement_id), "name": group.get("name"),
                "source_url": group.get("url"), "preparation": group.get("preparation", {}),
                "quests": [{"quest_id": int(quest_id), "name": names.get(str(quest_id))}
                           for quest_id in group.get("quest_ids", [])],
                "retention": "local factual pilot metadata only; use the source URL for the full guide",
            }
        return {"found": False, "achievement_id": int(achievement_id),
                "reason": "success is not in the currently indexed guide pilot"}

    def fetch_guide(self, quest_id: int, quest_name: str | None) -> dict:
        resolved = self.resolve(quest_id, quest_name)
        if not resolved["found"]:
            return resolved
        if not quest_name:
            raise GuideUnavailable("quest has no local name to match against the guide index")
        group_url = str(resolved["group"]["url"])
        index = _PageParser()
        index.feed(self.fetcher(group_url).decode("utf-8", "replace"))
        expected = _normalise(quest_name)
        matching = [(label, href) for label, href in index.links if _normalise(label).rstrip(".") == expected.rstrip(".")]
        if not matching:
            raise GuideUnavailable(f"guide index did not link a walkthrough named {quest_name!r}")
        label, href = matching[0]
        guide_url = urljoin(group_url, href)
        raw = self.fetcher(guide_url)
        page = _PageParser()
        page.feed(raw.decode("utf-8", "replace"))
        return {
            **resolved,
            "guide": {
                "url": guide_url, "title": " ".join(page.title.split()) or label,
                "sections": page.sections,
                "images": [{**image, "url": urljoin(guide_url, image["url"])} for image in page.images],
                "html_bytes": len(raw),
                "fetched_at_ms": int(time.time() * 1000),
            },
        }
