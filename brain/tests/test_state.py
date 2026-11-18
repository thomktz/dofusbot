from dofus_brain.game.state import (Character, Dialog, DialogOption, Fighter, FightState, GameState, MapInfo, Poi,
                                    Quest)

GAME_STATE_KEYS = {"screen", "confidence", "map", "character", "fight", "dialog", "quest", "pois", "updated_at"}
CHAR_KEYS = {"name", "level", "hp", "hp_max", "pa", "pm", "pods", "pods_max", "kamas"}
FIGHT_KEYS = {"phase", "turn", "timer_s", "fighters", "blocked_cells", "reachable_cells"}
FIGHTER_KEYS = {"id", "name", "team", "is_self", "cell", "hp", "hp_max", "pa", "pm"}


def test_field_names_match_protocol():
    gs = GameState(screen="fight", confidence=0.9, map=MapInfo((4, -18), "Astrub", "Cité"),
                   character=Character(hp=10, hp_max=20, pa=6, pm=3),
                   fight=FightState(phase="my_turn", turn=1, fighters=[Fighter("a", "A", "ally", True, 12)]),
                   dialog=Dialog("Npc", "txt", [DialogOption(1, "oui")]), quest=Quest("Q", None, None),
                   pois=[Poi("npc", "Npc", (4, -18), 12)])
    j = gs.to_json()
    assert set(j) == GAME_STATE_KEYS
    assert set(j["map"]) == {"coords", "area", "sub_area"} and j["map"]["coords"] == [4, -18]
    assert set(j["character"]) == CHAR_KEYS
    assert set(j["fight"]) == FIGHT_KEYS
    assert set(j["fight"]["fighters"][0]) == FIGHTER_KEYS
    assert set(j["dialog"]) == {"npc", "text", "options"} and j["dialog"]["options"] == [{"index": 1, "text": "oui"}]
    assert set(j["quest"]) == {"name", "step", "objective"}
    assert set(j["pois"][0]) == {"kind", "name", "coords", "cell"}
    assert GameState.from_json(j).to_json() == j


def test_defaults_are_nullable():
    j = GameState().to_json()
    assert j["fight"] is None and j["dialog"] is None and j["map"]["coords"] is None and j["pois"] == []
