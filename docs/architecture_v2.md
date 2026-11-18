# Dofus agent v2 architecture

This is the migration target for the protocol-first bot. AstraBot is the reference for
the deterministic game-data layer; its code is a reference implementation, not a module
to import at runtime.

## Boundaries

```text
Dofus TCP stream + local map/data files
              |
        protocol decoder
              |
      WorldState reducer  <---- screen/OCR fallback
              |
       read-only tools
              |
       local LLM planner
              |
       validated action plan
              |
    safety gate + controller
```

The LLM never parses raw protobuf, invents cell coordinates, or presses keys directly.
It receives a compact snapshot and calls typed tools such as `scan_map`, `move_to_map`,
`choose_fight`, `plan_turn`, and `wait_for_event`. The deterministic layer validates every
cell, path, spell, and target before execution.

## Canonical world state

The reducer will maintain one snapshot containing:

- current map id and world coordinates;
- 560 map cells (`mov`, `los`, elevation, obstacles, red/blue starts);
- role-play actors (NPCs, monster groups, players), each with id, cell, type, and metadata;
- interactive elements and stated resource availability, keyed by element id and cell;
- movement paths and map transitions;
- fight state, fighters, effects, turns, and action history.

The first protocol aliases to implement are the ones AstraBot maps:

- `jnj` / `jns`: map actors and complementary map information;
- `jnz`: role-play movement paths;
- `its` / `itu`: interactive elements and their live state;
- `kjz`: actor removal;
- `jvj`, `jwj`, `jsq`, `jnz`: fight lifecycle, fighters, effects, and movement.

Z/Y overlay scans remain useful as a fallback and visual validation tool, but they are not
the primary source of coordinates once the protocol reducer is complete.

## UI target

The current panels will be replaced by a single workspace:

1. map canvas with exact cell grid, actors, resources, paths, and current selection;
2. event/timeline inspector;
3. LLM conversation and proposed plan;
4. execution/safety controls and connection health.

Every rendered item must carry its source (`protocol`, `map-data`, `screen`, or `inferred`)
and confidence so an incorrect fallback cannot look authoritative.

## Implemented first slice

The decoder now emits `map_id`, normalized `map_events`, interactive records, and
stated-element records for the role-play aliases seen in captures (`jnj`, `jns`,
`jnz`, `kjz`; `its`/`itu` are retained as update events). The server attaches this
to `world`, so the dashboard can consume one state shape from a live scan or an
archived capture. `WorldToolRegistry` and `LocalWorldOrchestrator` provide the
LLM boundary: the model observes compact `agent_context` and can propose
validated `talk_to_npc`, `choose_dialogue_reply`, `leave_dialogue`,
`use_interactive`, `cast_spell`, or `end_turn` intents. Those become narrow
`GameIntent` values; only a live managed transport may emit them. The model
cannot send input directly.
