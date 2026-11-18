# Agent phases: how the agent should play each part of the game

Design notes, 9 Nov 2026. Nothing here is implemented yet beyond what already exists in `brain/` (screen classifier, HUD OCR for area and coordinates, fight grid reader, planner interface, executor).

## 1. Overall shape

The agent works on three levels:

1. **Interrupt layer (every frame, deterministic).** It decides which phase the game is in from `screen` plus a few cues, and it always wins over the current goal. Priority, highest first: `popup` > `fight` > `dialog` > `loading` > `world`. An unexpected switch into `fight` means we were aggroed. It suspends the current task, and that task resumes after the fight.
2. **Skill layer (deterministic code, one per phase).** Small routines: go to a map, walk to a cell, scan the map, talk to an NPC, pick a dialog option, place for a fight, play a turn, close results. Each skill has preconditions, the perception it needs, its actions, a **postcondition check**, a timeout and a retry budget. When it fails it returns a reason. It never loops blindly.
3. **Goal layer (slow, LLM allowed).** It picks the next quest step, turns quest text into a skill (`talk_to(npc, map)`, `kill(n, mob)`, `gather(item, n)`, `go_to(map)`), and chooses dialog options when matching keywords isn't enough. It runs a few times per minute at most and never in the fight loop.

Every action is verified against the next frame. A click that didn't change anything counts as a failed step, not a success. After 2 failures the skill gives up and the goal layer replans. In `auto` mode, after 3 failed replans the agent stops and notifies you.

Modes stay as they are: `observe` reads only, `assist` proposes and you press Step, `auto` acts. Each new phase goes through observe → assist → auto before it can run on its own.

## 2. Phases

### 2.1 Loading and map transitions
- **Detect:** a mostly black or uniform frame, or the loading classifier; the coordinates OCR becomes unreadable and then changes.
- **Do:** nothing. Wait until `world` is stable for about 0.5 s.
- **Verify:** the new coordinates equal the old ones plus the expected step (for example a right edge gives `x+1`). If they don't, record the real link in the map graph: some maps don't follow the grid, and indoor maps or zaaps jump.

### 2.2 World navigation
- **Between maps:** click a walkable cell on the map edge in the right direction (the mouse cursor changes to an arrow over map exits). Learn a **map graph** from observed transitions (`data/maps.json`: coordinates → exits, blocked edges, last visited). Find routes with BFS over that graph. Then use known zaaps once we have kamas and the zaap is saved.
- **Within a map:** click the target cell, then verify the character moved (the self marker or the character sprite moved; the coordinates stay the same).
- **Danger check before each map step:** if the scan (2.3) shows aggressive mobs near the route, go around them or accept the fight, depending on the goal and their level.
- **Stuck:** the same frame 3 times after clicks means a blocked cell. Try a neighbouring exit cell, then mark the edge blocked.

### 2.3 Scanning a map (the highlight keys)
- **Y (highlight interactive elements):** hold the key, capture, release. Diff that frame with the normal one: outlined objects are resources, doors and interactive elements. That gives POIs with screen positions, cheaply and without a vision model.
- **Z (show NPCs and mobs):** same method. You get name plates over NPCs and mob groups. OCR the plates: mob groups show a level, NPCs show a name. Results go into `state.pois` with `kind`, `name` and screen position, converted to a cell through `game/grid.py`.
- **Hover tooltips (mouse move only):** a mob group shows its makeup and total level. Use this only when the goal needs it (picking a fight).
- **Cost:** one scan per map arrival, about 1 s. Cache it per map with a short time limit, because mobs move.
- **Open question:** check that Y and Z match your key bindings. Dofus defaults follow the keyboard layout (AZERTY), and the controller has `keyboard_layout`.

### 2.4 Interacting with elements
- **NPC:** click it, and a context menu opens (Talk, Buy, ...). OCR the menu, click "Parler"/"Talk", then wait for `dialog`.
- **Resource:** click it, and a harvest bar appears. Wait until it's gone, then verify with the inventory or pods change, or the resource sprite changing.
- **Door, stairs, zaap:** click, then expect a map transition (2.1) or the zaap UI.
- **Verify:** the screen or state changed in the expected way within about 3 s. If not, try once more on a slightly different point (sprites are larger than their click box), then fail.

### 2.5 Dialogs
- **Read:** `dialog_npc`, `dialog_text` and `dialog_options` regions with OCR, which `DialogReader` already does.
- **Choose:**
  1. An option that matches the current quest objective by keywords.
  2. "Continue" or "Suivant".
  3. The LLM, given the quest text, the NPC text and the options.
  - Never choose an option that spends kamas or items, or abandons a quest, unless the goal says to.
- **Exit:** with Escape, or the close option. Verify `world` comes back.

### 2.6 Questing
- **Source of truth:** the on-screen quest tracker (the `!` button at the top right of the capture) and the quest log. Parse the quest name, the step and the objective lines into `state.quest`.
- **Turning objectives into skills:** each objective pattern maps to one skill:
  - "Parler à X" → `talk_to(X)`
  - "Vaincre N Y" → `kill(Y, N)`
  - "Récolter/Ramener N Z" → `gather(Z, N)`
  - "Se rendre en [x,y]" → `go_to(x,y)`
- **Where things are:** quest NPCs and mobs need a location. Options, best first:
  1. A static data file (quests, NPCs with map coordinates, mob areas), from DofusDB or a similar public game database, imported once into `data/`.
  2. What the agent learns itself from Z scans.
  3. Direction arrows and map flags the game shows for quests.
- **Order:** finish the Incarnam tutorial chain first. It's linear, has known NPCs and gives easy fights, so it's a good test of every phase.
- **Progress check:** re-read the tracker after each completed skill. If the objective text didn't change, the skill didn't actually succeed.

### 2.7 Being aggroed by mobs
- **Detect:** `world` → `fight` (placement phase) without having clicked a mob. Log it as an aggro and note the map and the mob group.
- **Do:** suspend the current task, run the fight (2.8 to 2.10), then resume the task. Navigation restarts from wherever we ended up, because the map may differ after a fight.
- **Prevent:** the Z scan before moving. Aggressive groups get a radius around them that route planning avoids, and re-visits to that map count as risky. Above a level or HP threshold, skip prevention and just fight.
- **Low HP before aggro risk:** regenerate first (sit, or wait out of combat) before crossing risky maps.

### 2.8 Fight placement
- **Detect:** fight screen, the timeline has no turn running, the "Prêt"/Ready button is visible, and red and blue starting cells are shown.
- **Read:** our team colour (`fight.ally_color`; check the self marker because it can be red or blue), our start cells, and the enemy positions.
- **Choose a cell, by class:**
  - **Cra (ranged, which your character is):** maximise distance to the nearest enemy, while keeping line of sight to at least one enemy at range 6 or more. Break ties with obstacles between us and the melee enemies.
  - **Melee (Iop):** minimise distance to the weakest enemy.
- **Act:** click the chosen start cell, verify our marker moved there, then press Ready (`ready_key`, F1).
- **Fallback:** if the timer is low or reading is uncertain, just press Ready. The game places us itself.

### 2.9 Fight turns
- **Detect my turn:** the timeline highlights us, or the end-turn button is active (`end_turn_active` colour). Also read the `pa`/`pm` HUD values. Turn on tactical mode and creature mode in the client settings once, by hand, so the grid reader works.
- **State:** fighters with cells and HP from the timeline and the grid, our PA/PM, blocked cells, and line of sight computed on the grid.
- **Decision (deterministic, no LLM):** read the spell table from config: key, PA cost, min/max range, LoS required, range modifiable, damage, uses per turn or per target.
  1. If any spell hits an enemy from the current cell: cast the best damage per PA, finishing low-HP enemies first.
  2. Otherwise, move within PM to the reachable cell that allows the best cast. For a Cra, prefer cells that also keep distance and break LoS to melee enemies, and then cast.
  3. With PA left and nothing to hit, spend PM to kite away from the enemies.
  4. End the turn.
- **Cast:** press the spell key, click the target cell, then verify PA went down and the enemy HP changed (or a miss popup appeared). If PA didn't change, the cast was refused (LoS or range): mark that pair and try the next option.
- **Move:** click a reachable cell, then verify our cell and PM changed. Being tackled means less PM than expected, so re-plan with the real PM.
- **Timer:** budget about 70 % of the turn timer for actions. If it runs low, end the turn.
- **Enemy turns:** keep perceiving to track positions and HP, but don't act.

### 2.10 Fight end
- **Detect:** the results window (victory or defeat, XP, loot).
- **Do:** close it (Escape or its button), then handle any level-up popup (2.11). Update kill counts for `kill(Y, N)` objectives.
- **Defeat or death:** if we become a ghost (or HP is 0 after a defeat), stop `auto` and notify you. The phoenix and respawn route can come later.

### 2.11 Popups and other interrupts
- **Harmless** (level up, achievement such as the one seen today, server notice, tutorial tip): close it with Escape or its close button, then verify it's gone.
- **Level up:** spending characteristic or spell points goes to a separate skill, done by hand or off by default.
- **Other players** (trade, group, duel, guild invitations, private messages): decline or ignore the invitation, **pause `auto`, and notify you.** Never answer chat automatically.
- **Unknown popup:** press Escape once. If it's still there, stop and notify.

### 2.12 Character upkeep
- **HP:** below a threshold out of combat, sit or wait until HP recovers before the next fight or risky map.
- **Pods:** when nearly full, go to the bank (later; a known bank location in Astrub).
- **Inventory and equipment:** done by hand for now.

## 3. Perception needed (in build order)

| Need | Status | Next step |
|---|---|---|
| Screen type | colour classifier + VLM fallback | label `data/vlm-corpus` with `truth_screen` and measure accuracy per screen |
| Coordinates and area | **works** (Vision OCR, 20–40 ms) | add a check that rejects a jump of more than 1 map without a zaap or door |
| Level | visible next to the coordinates ("Niveau 5") | read the same OCR line |
| HP, PA, PM | placeholder regions | locate them on real captures; the HUD in today's capture shows HP 108, PA 6, PM 3 |
| Quest tracker | none | find its region; parse with OCR |
| POIs (Y and Z scans) | none | frame diff and plate OCR |
| Fight placement cells, our team, timeline, turn | `FightReader` with placeholder palette | needs real captures of placement and of a turn |
| Results, level-up and invitation popups | classified only as `popup` | OCR the popup title to tell which kind it is |

## 4. Rollout

1. **Observe only (now):** turn on `--capture-dir` to collect frames from every phase while you play by hand. Label them, then make perception correct on those frames offline. This needs no input to the game.
2. **Assist:** for each phase, the agent proposes an action with the reason and the expected result, and you press Step. That shows decision quality before anything runs on its own.
3. **Auto, narrow:** first a single loop in Incarnam: scan, pick a weak mob group, fight, close results, repeat, with HP-based rest. Then the tutorial quest chain. Then navigation across maps.
4. **Safety stays on:** the kill switch, pausing when any other player interacts, a cap on session length, and the agent always stops on anything unknown.

## 5. Open questions

- Are Y and Z your actual bindings for "highlight interactive elements" and "show NPCs/mobs" (AZERTY)? Is Ready/end turn F1?
- The character is a **Cra**, but `brain/config/agent.json` says `"character_class": "iop"`. It should be changed to `cra` before anything uses it.
- Which spells and levels does the character have now? That gives the first spell table.
- Is it OK to import a public game database (quests, NPC and mob locations) into `brain/data/`?

Note that Ankama's terms forbid automating the game, and accounts can be banned. Keep that in mind when moving phases to `auto`.

## 6. Implementation status (9 Nov 2026)

Implemented in `brain/dofus_brain/` and tested on fixtures only (nothing has been run against the game):

| Phase | Code | Tests |
|---|---|---|
| Safety layer | `skills/safety.py` | `tests/test_safety.py` |
| Map travel | `skills/travel.py` | `tests/test_travel.py` |
| Fight placement | `perception/placement.py`, `skills/placement.py` | `tests/test_placement.py` |
| Fight turns | `perception/entities.py`, `skills/fight.py` | `tests/test_fight_solver.py` |
| Aggro | `skills/aggro.py` | `tests/test_aggro.py` |
| Harvest / interact | `perception/highlight.py`, `skills/harvest.py` | `tests/test_harvest.py` |
| Quests and dialogs | `skills/quest.py` | `tests/test_quest.py` |
| Popups | `skills/popup.py` | `tests/test_popup.py` |
| Everything together | `planner/phases.py` (`--planner phases`) | `tests/test_phase_planner.py` |

The choices made and the open questions are in [DECISIONS.md](DECISIONS.md).
