# Decisions

One line each: what we chose and why. Open questions are at the bottom.

## Phase skills (Nov 2026)

- Skills live in `brain/dofus_brain/skills/`, one module per phase. They return a `PlannedStep` or an `Outcome` and never send input themselves. This keeps the planner interface pluggable and every skill testable without the game.
- All skill settings are in `brain/config/phases.json`, merged over `skills/config.py` defaults. That keeps a single place for keys, spells, rules and limits, and an absent file still works.
- Keys (Y, Z, F1, Escape, spell digits) are named constants in config only. The safety layer refuses any other key, any key modifier and all typing, so a bug can't type or press arbitrary keys in the game.
- The safety layer (`SafetyGuard`) refuses actions without a verify predicate, except `wait`. It pauses on an unknown or low-confidence screen after 8 s, and after 3 failed steps in a row. The design requires verification after every action, and unknown means stop.
- A loading screen doesn't count as "unknown" for the safety timer. Map changes show one for a second or two, and the travel skill waits it out.
- Map travel assumes every edge leads to the neighbouring coordinates until it sees otherwise. Real transitions are recorded as links, so doors and odd maps fix themselves, without needing any external map data.
- An edge is marked blocked after 3 different edge cells fail on it (middle cells first). A single failed click is often decor or a non-walkable cell, not a missing exit.
- Travel gives up after 6 failed steps in a row on the same map ("stuck"). That's enough to try two edges, and it stops endless clicking.
- A* uses Manhattan distance as the heuristic and switches to Dijkstra once any learned link jumps more than one map. That keeps routes optimal; the graph is small, so it stays cheap.
- The map graph persists to `brain/data/maps.json` (not committed), so learning survives restarts. It's data from your own play, not config.
- The fight solver is deterministic: it searches every reachable cell, and from each one a small exhaustive set of spell/target combinations within PA and per-turn/per-target limits. Fights need to be fast and predictable; an LLM is too slow and too unreliable per action.
- Scoring is damage dealt, capped by the target's remaining HP, with +25 for a kill, and lowest-HP targets are hit first. Ranged roles get a penalty for ending within `keep_distance` (4) of an enemy and a small pull toward spell range; melee roles get pulled toward enemies. It's simple, readable and easy to tune in config.
- Ranged characters may kite after casting: leftover PM is spent moving to the safest reachable cell. That's how a Cra plays.
- The turn is re-planned after every verified action, from the new frame. That handles tackle (lost PM), misses and deaths without modelling them.
- A cast is two steps: press the spell key (weak check: still our turn), then click the target (checked by PA dropping or the target's HP dropping). Selecting a spell doesn't change any state the perception reads.
- A cast that fails its check marks that (spell, cell) pair refused for the rest of the turn, so the solver tries something else. A refused cast usually means out of line of sight or range, misjudged from a misread grid.
- The turn ends with the end-turn key once 70 % of a 30 s turn has been used, or when nothing useful is left. This leaves a margin before the game ends the turn for us.
- The strategy hook (the LLM, optional) is called once per fight on our first turn, and may return `focus` (an enemy name) and `style`. That keeps the LLM off the hot path; if the hook fails, the fight goes on with defaults.
- Z-key name plates are parsed from OCR lines ("Name (hp/max)", "Name niv. N", "Groupe de N ..."). The entity's cell is taken one cell below the plate, and plates are matched to fighters within 1 cell. Plates float above sprites, and the grid reader already knows where the fighters stand.
- Spell tables for `iop` and `cra` in config are placeholders (names, costs, ranges and damage are approximate). The real spell list isn't known yet.
- Placement start cells are read per cell from the median colour in tactical mode, using separate placeholder colours (`placement_red` / `placement_blue`). They need to differ from the team circle colours so a fighter standing on a start cell isn't read as a start cell.
- Our team colour comes from the circle around the self marker, with `fight.ally_color` as the fallback. In Dofus the attacker/defender colour can be red or blue, so a fixed setting would be wrong half the time.
- Placement choice: ranged picks the cell with line of sight to an enemy within 6, then the largest distance (capped at 6); melee picks the closest cell to an enemy. These are the standard openers for each role, and they're simple and predictable.
- Placement tries a single move. If the move or the reading fails, it presses Ready anyway and lets the game place us. A wrong placement costs a little; a stuck placement timer costs nothing either, but being clever here isn't worth the risk.
- A Z-key plate with a level or a group size is a monster group; a plain name is an NPC. That's what the game shows on each, and it needs no name database.
- Engage/avoid is a first-match rule list in config: name substring, `max_group_size`, `max_level_ratio`. The default engages groups of up to 3 at no more than 1.5× our level, and avoids everything else, including groups whose level we can't read. A short rule list is easy to read and to edit.
- Only groups known to be aggressive count as threats for routing. A group becomes aggressive after it starts a fight without us clicking it (the closest group is blamed), or by listing its name in `aggressive_names`. Most monsters in Dofus don't attack on sight, so avoiding everything would make travel useless.
- Threats add a route cost of +6 to their map and make edge cells within 4 cells of them off-limits. Travel then goes around if a detour of up to ~6 maps exists, and otherwise still goes through, avoiding the nearby cells.
- Monster sightings expire after 120 s. Groups move and respawn, so old sightings shouldn't block routes forever.
- Aggro detection is world → fight with a last action that wasn't a click tagged `target: "monster"`. It's cheap and needs no extra reading.
- Interactive elements are found by diffing a plain frame with the frame where Y is shown: the max channel difference above 40, downsampled 2×, with connected blobs of at least 40 px kept. Pure numpy, no new dependency, and it needs no knowledge of what each resource looks like.
- ~~The Y key is treated as a toggle~~. Superseded on 9 Nov: Y and Z are hold keys (see below). `key_mode: "toggle"` is still supported for other setups.
- Harvest clicks the highlighted element nearest to the character, or to the screen centre while the character's position on the world map isn't read yet. It's the shortest walk, so the least chance of being interrupted.
- Harvest success means pods (or a configured count) went up, or the element's box changed by more than 15 % after 0.5 s. Some actions don't change pods (doors, levers), so a visual change is accepted too.
- An element that fails is skipped for later scans of the same map. That prevents clicking the same unusable or occupied element forever.
- Skills that need raw frames (harvest) get them through `feed_frame()`, because GameState doesn't carry images. The agent loop must call it; that wiring is pending, because `agent.py` has uncommitted changes I didn't want to mix in.
- The harvest test fixture pairs are generated in the test from the mock renderer with drawn outlines, at 1× and 2× (Retina). They're deterministic, and no binary fixtures had to be added.
- Quest objectives are parsed with French and English regexes, after stripping accents and lowercasing (talk, kill, gather, bring, goto, with an optional "n/m" progress). The objective lines follow a few fixed phrasings, regexes are testable, and accent stripping absorbs OCR's dropped accents (as with "Pâturages").
- The current objective is the first one in the tracker that isn't complete. That's the order the game shows.
- NPC, monster and resource locations come only from a local `brain/data/locations.json` (optional, empty by default) plus what Z scans learn. No external database is imported or downloaded.
- An objective whose location is unknown becomes an `ask` goal (pause and ask), never a guess. Wandering around looking costs time and aggro risk.
- Dialog choice works in this order:
  1. Options with forbidden words are removed (buy/sell/pay/kamas/abandon).
  2. An option sharing enough words with the quest (≥ 0.34 overlap and a unique best) is chosen.
  3. A "continue" option is chosen.
  4. The only allowed option is chosen.
  5. The LLM, but only if its answer is an allowed index with confidence ≥ 0.6.
  6. Otherwise ask the human.
  The deterministic steps decide most dialogs with no LLM call; the LLM covers the ambiguous rest, and a bad answer can't pick a forbidden option.
- The LLM is consulted at most once per distinct dialog text, through the async `prepare()`. That keeps it off the hot path and bounds the cost.
- ~~Talking to an NPC expects a dialog straight away~~. Superseded on 9 Nov: NPCs open a menu (see below).
- Popups are classified by keywords in their OCR text. An "ask" match (trade, group, duel, guild, invitation, private message) wins over a "dismiss" match (level up, achievement, tip, congratulations...). Anything matching neither is unknown, and it pauses and asks. An invitation can contain "félicitations", and closing a player request by mistake is worse than asking.
- A dismissible popup gets the dismiss key (Escape) once per distinct text. If it's still there, it asks. That avoids pressing Escape in a loop on something that doesn't close that way. The set of tried texts resets after a successful close, so the next level-up popup works too.
- The skills never answer chat or accept requests from other players: those always pause and notify. Answering automatically is the clearest sign of a bot, and it's your call.
- `PhasePlanner` (`--planner phases`) runs every skill behind `SafetyGuard`, with interrupt priority popup > fight (placement, then turns) > dialog > loading > world goal. It's registered in `PLANNERS` and the default planner stays `noop`. That keeps the interface pluggable, and the running backend and its behaviour don't change.
- Goals are plain text from the dashboard: `goto x,y`, `fight`, `harvest [n]`, `talk <npc>`, `quest`. This uses the existing goal box and needs no protocol change.
- Any `ask` outcome, a failed goal, or a step the safety layer refuses halts the planner, and it proposes nothing until a new goal is set. "Pause and ask" has to mean stopping, not carrying on with something else.
- The map graph is saved after every travel step. Losing learned links on a crash would mean relearning them by clicking.

## Answers from Thomas (9 Nov evening)

- Y and Z are hold keys: the overlay shows only while the key is held. Skills never press them as an action; they return a `scan` request. The agent then calls the new `Controller.capture_holding(key, settle_ms=250)`, which presses the key, waits 250 ms, captures, and releases the key in a `finally` (even on an error or a kill). Doing it as one call means a key can't be left held between agent loop iterations.
- A Z scan runs on every new map, and again after 60 s on the same map, before the goal acts. Monster positions (aggro) and NPC plates (talk) depend on it, and groups move.
- The `target` tag (monster/npc) moved from the action dict into the step's `meta`. Control rejects unknown action fields, so engage and talk clicks would have been refused. A test now runs every skill action through `dofus_control.actions.validate_action`.

- The character is a Cra. `fight.character_class` is `"cra"` in phases.json, which the phase planner reads first, so fights use the ranged role and Cra spells. `agent.json` says `cra` too, but that file isn't committed because it holds other uncommitted edits.

- **No DofusDB import.** Its licence (LPNC-IA 1.0, read at api.dofusdb.fr on 9 Nov 2026) forbids projects mostly generated by AI, using the data in an AI-driven pipeline without significant human involvement, and making the data available to AI systems or agents, and its robots.txt blocks Claude's crawlers. This project would break those terms, so nothing was imported. Only one record was fetched, to read the licence, and it wasn't saved.
- Spells are read from the game's own tooltips instead. `python -m dofus_brain.skills.spells <screenshot> --key N` OCRs one tooltip (French or English), parses PA, range, modifiable range, line only, line of sight, casts per turn and per target, cooldown and damage, and merges the result into `brain/config/spells_cra.json` by spell bar key. That's the "parse and keep a config" idea, with no external data and no licence problem.
- The fight solver reads `config/spells_<class>.json` when it exists, otherwise the placeholder table. Entries missing a PA cost are skipped. `spells_cra.json` starts with two placeholder entries marked `verified: false`.
- Cooldown means turns between casts: a spell with cooldown N cast on turn t is available again on turn t+N. That matches how "intervalle de relance" reads in the game.

- Clicking an NPC opens a context menu (Thomas). The talk skill works in four steps:
  1. Click 30 pt below the NPC's Z plate.
  2. Wait for a menu: OCR a box from 40 pt left/above to 260 × 220 pt right/below the click point. It counts as a menu only if a line starts with a known entry (Parler, Acheter, Vendre, Échanger, Talk, Buy, ...).
  3. Click the entry that starts with "Parler", "Discuter", "Talk" or "Speak".
  4. Verify that a dialog opens.
  A dialog opening directly also counts. A menu without a talk entry asks, and after 2 failed tries it asks. The menu opens next to the cursor, and matching known words keeps map text from being read as a menu.
- While the talk skill waits for or reads the menu, a `menu` or `popup` screen classification is treated as that menu and not handled as a popup. The colour classifier may well call the menu box a popup, and Escape would close it.
- `norm()`/`tokens()` moved to `dofus_brain/textnorm.py`; `skills/quest.py` still re-exports them. Perception code needs them too, and importing skills from perception made an import cycle.

- **Spell bar keys come from Thomas** (9 Nov, AZERTY, no shift): é = Flèche de recul, " = Flèche glacée, ' = Flèche de barrage, ( = Flèche vagabonde, è = Tirs éloignés. They're in `brain/config/spells_cra.json`. The stats now come from Thomas's tooltip text (see "Cra spells from Thomas's tooltip text" below).
- The spell keys are stored as the character the key types (é, ", ...), and control presses the physical number-row key without shift. `AZERTY_OVERRIDES` in `control/dofus_control/keys.py` didn't map these characters before (only `auto` layout detection could find them), so the unshifted number row `& é " ' ( § è ! ç à` → keycodes 18–29 was added, with tests. That matches what the spell bar shows and presses exactly that key. Shift+digit would type the digit instead and could hit a different shortcut.
- `keys.spells` in phases.json is the AZERTY number row, so the safety layer allows exactly those spell keys.
- The old placeholder "Fleche de Recul" (key 2) was replaced by Thomas's "Flèche de recul" (é). The placeholder "Fleche Magique" is kept but `disabled: true`, because its bar key is unknown: casting it would press a key that may hold a different spell.
- "Tirs éloignés" is a self buff (`kind: other`, damage 0), so the damage solver never chooses it. Using buffs needs its own rule later.

## Combat solver (9 Nov evening)

- Fights are handled by a per-fight `FightSession` in code plus a deterministic `TurnSolver`, not an AI-model subagent per move. Measured on this Mac, the vision model takes 5–9 s per call against a ~30 s turn and is poor at grid geometry, while the solver plans a turn in well under a second. The AI strategist hook stays optional, at most once per fight, and off in tests.
- Spells are data (`skills/spellmodel.py`):
  - targeting: range, min range, boostable, line only, line of sight, free cell;
  - area: single, cross, line, circle, cone, where line and cone follow the cast direction;
  - effects: damage per element (min–max), push/pull, AP/MP removal, range buff with turns, teleport, heal.
  Old flat entries are still read. This lets the solver reason about what spells really do instead of a single damage number.
- The solver's score:
  - expected damage after resistances, capped by HP, +25 per kill (×1.5 on the strategy's focus target);
  - +5 per AP and +4 per MP removed, +1.5 per cell an enemy is pushed away, plus push collision damage;
  - −2 × friendly or self damage;
  - −1 × (2 when defensive) × the threat of the end cell, with enemies killed this turn removed;
  - −0.05 per cell moved;
  - when nothing can be hit, a pull towards melee or spell range.
  The weights are in `combat.WEIGHTS`, simple and tunable from fight logs.
- Push collision damage uses the Dofus formula `(8 + rand(1..8) × level / 50)` per blocked cell, at its expected value. Pushes don't move targets for the later casts of the same plan; the next re-plan sees the real positions.
- Area spells also aim at cells next to enemies, to catch several in one cast. Each spell keeps its 6 best options, and plans have at most 3 casts plus one optional range buff. That keeps the search small; the range buff is only planned when it enables a cast.
- Threat map: for each enemy, the cells it can hit next turn (its MP around obstacles, then its attack range, with line of sight). Unknown monsters default to 3 MP, melee range 1, 12 damage. A Cra mostly needs to stay out of melee reach, and the defaults are cautious until real values are read.
- Resistances, AP/MP and attacks are cached per monster in `brain/data/monsters.json`, with `null` until read in game. It's seeded with the three Bouftous from the 9 Nov scan. An unknown resistance counts as 0 % and is flagged.
- The tooltip parser now also reads area and effects. Any line that looks like game data but can't be parsed goes to `review` in the spell entry and is never turned into an effect. Rule: no guessing into the solver.
- `FightTurnSkill` now plays through `FightSession`: cooldowns, buff durations, per-turn and per-target counts, and a cast log. The old `FightSolver` class stays for its tests and comparison, but the skill doesn't use it any more.

## Cra spells from Thomas's tooltip text (9 Nov, 19:54)

- All five spells in `spells_cra.json` now come from the tooltip text Thomas pasted, at spell level 1: cost, range, boostable range, crit 10 %, line/diagonal, "cible nécessaire", uses per turn and per target, cooldown, damage, crit damage and effects. Each entry keeps `source`, `level_header` and its equipment bonuses (recorded, not applied).
- Flèche Vagabonde uses Thomas's corrected paste: range 1–6, line or diagonal, needs a target, 16–18 Earth (crit 19–21). "Recule de 1 cases" is modelled as the **caster** stepping back 1 cell (`retreat`), not a push. In Dofus tooltips "Recule" is the caster, "Repousse" the target, and the first paste said "Éloigne le lanceur de la cible". The relay summary called it a target push. The first paste (1–8, 27–30, recule 3) was another spell or level.
- The headers (1 / 66 / 132, 1 / 69 / 136) are the character levels at which a spell changes slightly (Thomas). All five entries are treated as spell level 1, with no level mismatch.
- Tirs Éloignés is modelled from the text as +3 minimum and **+6 maximum** range on every offensive Cra spell, for 1 turn, with cooldown 2. The coordinator's relay said +3/+3, but the pasted text says "+6 Portée maximale". It applies to all offensive spells, whether or not their range is boostable, because the text says "tous les sorts offensifs Crâ". The solver only casts it when it enables a hit; the higher minimum range can also make close targets unreachable, and the solver accounts for that.
- Expected damage includes criticals: 0.9 × normal average + 0.1 × critical average.
- "Lancé en ligne uniquement" together with "Lancé en diagonale uniquement" is read as "line or diagonal".

### Review list (what the text says, not used by the solver yet)

- Flèche de Barrage: "repousse les cibles en zone". The area shape isn't given, so it's solved as single target until the shape is confirmed.
- Flèche Glacée: "+3 dégâts de base (3)" is a self-stacking base damage bonus and isn't modelled. The duration of the AP removal is read as 1 turn, from "(1)".
- Flèche Vagabonde: "line + diagonal" is read as either one is allowed. Damage to "enemies" may mean an area; solved as single target.
- Tirs Éloignés: "+3 Portée" to allies (non-Crâ) in an area around the caster and its Balises isn't modelled, since we fight solo.

## Fight placement: ignore challenges, just PRÊT (9 Nov, 21:20)

Thomas: "We ignore challenges btw! So just 'Pret'". Four combat shots from the real client showed it:

- The screen classifier and the placement colours are still placeholders, and on the real client they put every fight shot down as `unknown`. The label of the main fight button (bottom right) is read with OCR instead (`perception/fight_button.py`). "CHOISIR LES CHALLENGES" and "PRÊT" mean fight placement, and "FIN DE TOUR" means the fight has started. On the 1280x800 client the button is at about (920, 754). A `fight_main_button` region in regions.json overrides the built-in crop.
- Flow (Thomas, 21:39): click "Choisir les challenges", and the button turns into PRÊT; then click PRÊT. `PlacementSkill` does exactly that and never clicks the challenge panel (Bonus cards, Valider). PRÊT is clicked once more if it is still there 3 s later. If "Choisir les challenges" is still there after two clicks, the skill presses the ready key instead.
- Fixtures `brain/fixtures/real_placement_challenges.jpg`, `real_placement_ready.jpg` and `real_fight_turn.jpg` are shots 01, 02 and 03. In 02, a strip on the right (x ≥ 1110) is blacked out because another window covered it.

## Fight sequence on the real client (9 Nov, 22:10)

Built from Thomas's 1-second stream of a whole fight (21:52 to 21:53). He picked the frames and labelled them; 10 of them are fixtures in `brain/fixtures/fight_seq/` with his labels. What the bot reads at each step:

| state | signal | bot |
|---|---|---|
| loading | black game area (`perceiver._dark`) | wait |
| placement, challenge step | button "CHOISIR LES CHALLENGES" | click it |
| challenge choice | button "PRÊT" | click PRÊT |
| our turn | "FIN DE TOUR" in lime | solver: select spell (key), click the target cell, or move |
| monsters' turn | "FIN DE TOUR" in grey | wait |
| level up | "NIVEAU n" + FERMER | click FERMER |
| fight result | "VICTOIRE" / "Combat terminé" + FERMER | click FERMER |

- Grid calibrated from the blue range cells of shot 04: ox 175, oy 48.2, cw 68.8, ch 34.4 (`config/grid.json`).
- Fighters (`perception/fight_real.py`): enemies stand on red rings, our Cra on a blue ring with a white dashed outline. Our Cra is a red creature, so a cell matching both colours is ours. When a crate hides one side of a ring, the dark creature-mode pawn on the cell is enough.
- PA: Apple Vision can't read the digit on the blue star (it reads 6 as 9), and PM isn't always read either. Values are only accepted between 0 and the maximum (`fight.max_pa` 6, `fight.max_pm` 3), and the turn skill tracks what it spent. A cast is confirmed by a new "Chern-Simons lance <spell>" line in the chat.
- End of turn: the skill clicks FIN DE TOUR when it can see the button, and falls back to the key otherwise.
- A FERMER window is only recognised when the FERMER button is read, because a monster tooltip on the map also says "Niveau 7".
- Fixtures must be PNG or JPEG with `subsampling=0`. Normal JPEG chroma subsampling wipes out the thin blue ring.
- Dry run: `python -m dofus_brain.replay <frames dir>` plays captured frames through the real agent loop (OCR, perceiver, PhasePlanner, auto mode), using the file names as timestamps. `ReplayController` only records what the bot would send, so nothing reaches the game or the OS. The frames don't react to the bot, so when its choice differs from what Thomas did, the check times out ("step failed (timeout)"). FERMER is clicked at most twice per window kind before the bot asks.
- Pacing (Thomas, 22:19): sample at 1 fps at most, and after a click (move, cast, FIN DE TOUR, FERMER) wait 2.5 s before the next capture so the animation settles (`pacing.PacedController`). Key presses (spell select) get no extra wait. `make_controller` wraps the real controller in it, and the replay uses the same wrapper on its clock, so it skips the frames that fall inside a wait. Checks after a click get 5 s, so they see at least one settled frame.
- End of turn: two failed moves or casts in a row, or the game's turn timer at 4 s or less, make the bot click FIN DE TOUR. A cell where a move failed is planned around for the rest of the turn.
- Still open: enemies flicker on frames with damage numbers or a death animation (the bot re-plans every frame). Holes and obstacles aren't read as blocked cells yet, so a move into one fails and the safety guard stops after 3 failures. World-map frames are still classified as `unknown`.

## Open questions (need Thomas)

- **Key bindings:** Y and Z are answered: they're hold keys (Thomas, 9 Nov). Still open: is F1 both Ready and end turn?
- **Spells:** the real spell list (name, key, PA cost, range, line of sight, damage) is needed to replace the placeholder tables in `skills/config.py`.
- **Location data:** DofusDB can't be used (see above). Do you know a source whose licence allows this use? If not, locations keep being learned from play, or you can fill `brain/data/locations.json` by hand (format at the top of `skills/quest.py`).
- **Placement and highlight colours:** the start-cell colours, the self marker and the Y-highlight look need checking against real screenshots (a placement phase, a Y scan, a Z scan).
- **Agent wiring:** `agent.py` must pass raw frames to `PhasePlanner.feed_frame()` (harvest, placement), run the Z/Y scans, pass popup text to `set_popup_text()`, and show `PhasePlanner.attention` on the dashboard. I haven't done it, because `agent.py` has uncommitted changes from before this work.
