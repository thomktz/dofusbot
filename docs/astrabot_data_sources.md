# AstraBot live data: sources and our equivalents

This is the protocol contract we are matching. AstraBot does not infer these
values from screenshots: it reduces server messages, and joins IDs to local
game-data tables/assets.

| Data | Server messages | Our reducer | Local join |
|---|---|---|---|
| Character id/name/level | `CharacterSelectionEvent` (and character list during selection) | selection reducer (next) | breeds/classes for display |
| XP, life, energy, characteristics | `CharacterCharacteristicsEvent`; `UpdateLifePointsEvent`; regeneration events | `network_scan._character_stats` (XP, HP, PA, PM now) | XP table for level/progress |
| AP/MP and fight stats | `FightRefreshCharacterStatsEvent` | fight fighter reducer | characteristic/stat IDs |
| Kamas | `KamasUpdateEvent`, inventory content | inventory reducer (next) | — |
| Pods | `InventoryWeightEvent` | inventory reducer (next) | — |
| Items and quantities | `InventoryContentEvent`, `ObjectAddedEvent`, `ObjectDeletedEvent`, `ObjectMovementEvent`, `ObjectsQuantityEvent`, `ObjectQuantityEvent` | inventory reducer (next) | item names, categories, effects |
| Jobs | `JobExperienceUpdateEvent`, job list/description events | jobs reducer (next) | job names and levels |
| Current map and player cell | `MapCurrentEvent` / instance variant, `MapComplementaryInformationEvent` | `map_events`, `map_id`, players | map ID -> `(x,y)`, area, subarea |
| Players, NPCs, monster packs | map actor/show/remove/movement events | `players`, `groups`, `map_events` | monster/NPC names, races, icons |
| Resources and exits | `InteractiveMapUpdateEvent`, `InteractiveElementUpdatedEvent`, stated-element events | `interactives`, `stated_elements` | skill names, resource names, map neighbours |
| Fight placement and fighters | `ContextCreationEvent`, `FightPlacementPossiblePositionsEvent`, `FightFighterShowEvent` | `fight`, placement cells, fighter records | fight map asset |
| Fight turns/actions/effects | `FightTurnEvent`, `MapMovementEvent`, `GameActionFight*`, `FightRefreshCharacterStatsEvent`, `FightEndEvent` | `fight_events`, fighter state | spells/effects and monster data |
| Chat | chat-channel/message events | not yet enabled | item links/texts |
| Quests/achievements | quest/achievement events | not yet enabled | quest/objective/text tables |

Every server `Any` message is now retained in `protocol_events` with its
obfuscated alias, offset, payload size and scalar fields. Unknown messages are
therefore visible for reverse mapping instead of being silently dropped.

## Current capture findings

`session-20261117-205816.pcap` contains the full characteristics record and
decodes to XP `32600/41000`, HP `30/384`, PA `6`, PM `3`; map ID `153879810`,
the character on cell `22`, and four monster groups. It does not contain the
character-selection message, so level is correctly left unknown. AstraBot
persists that selection value across the session; we should do the same when a
capture includes it.

The map packet carries a map ID, not human coordinates. AstraBot gets `(x,y)`,
area and subarea by looking up that ID in extracted game data. Our terrain asset
reader already follows the same ID-based model; the remaining work is adding a
map metadata table.
