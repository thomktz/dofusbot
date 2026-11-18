from pathlib import Path

from dofus_brain.guide_resolver import GuideResolver


def test_resolver_follows_group_index_to_exact_guide_without_persisting_it(tmp_path: Path) -> None:
    manifest = tmp_path / "guides.json"
    manifest.write_text("""{"success_groups":[{"achievement_id":1,"name":"Incarnam","url":"https://guides.test/group.html","quest_ids":[42]}]}""", encoding="utf-8")
    pages = {
        "https://guides.test/group.html": b'<div class="paragraph"><a href="/milk.html">Milk for Cat.</a></div>',
        "https://guides.test/milk.html": b'<h2>Milk for Cat</h2><div class="paragraph">Buy milk at the tavern.</div><div class="wsite-image"><img src="/milk.png" alt="tavern" /></div>',
    }
    resolver = GuideResolver(manifest, fetcher=pages.__getitem__, min_interval_s=0)
    resolved = resolver.fetch_guide(42, "Milk for Cat")
    assert resolved["guide"]["url"] == "https://guides.test/milk.html"
    assert resolved["guide"]["sections"] == ["Buy milk at the tavern."]
    assert resolved["guide"]["images"] == [{"url": "https://guides.test/milk.png", "alt": "tavern"}]


def test_resolver_reports_quest_outside_the_small_index(tmp_path: Path) -> None:
    manifest = tmp_path / "guides.json"
    manifest.write_text('{"success_groups":[]}', encoding="utf-8")
    assert GuideResolver(manifest, fetcher=lambda _url: b"").resolve(9, "Unknown")["found"] is False


def test_success_resolver_returns_preparation_before_quest_start(tmp_path: Path) -> None:
    manifest = tmp_path / "guides.json"
    manifest.write_text("""{"quest_names":{"42":"Milk for Cat"},"success_groups":[{"achievement_id":7,"name":"Cat Success","url":"https://guides.test/cats.html","quest_ids":[42],"preparation":{"items":[["Milk",1]]}}]}""", encoding="utf-8")
    success = GuideResolver(manifest, fetcher=lambda _url: b"").resolve_success(7)
    assert success["preparation"] == {"items": [["Milk", 1]]}
    assert success["quests"] == [{"quest_id": 42, "name": "Milk for Cat"}]
