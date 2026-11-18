# Agent context contract

`GET /api/agent/context` is the local, versioned hand-off from the protocol
reducer to a future tool-using LLM. It is deliberately not a dashboard scrape
and does not contain raw packets, screen pixels, map-cell dumps, credentials,
or any action coordinates.

```json
{
  "version": 1,
  "source": "dofus-protocol",
  "account_id": 0,
  "scan_state": "ready",
  "capture_path": "/…/session.pcap",
  "observed_at_ms": 1763412345678,
  "transport": {"state": "ready", "session_id": "…", "uid": 42, "sent": 3, "error": null},
  "context": {"map": {}, "character": {}, "inventory": {}, "spells": [], "quests": {}, "dialog": null, "affordances": []},
  "quest_plan": {"active_quest_plans": []},
  "action_status": {"idle": true, "pending": {}}
}
```

## Guarantees

- `context` is derived from server protocol messages plus installed Dofus data
  assets. It never relies on OCR for inventory, quests, dialogue, spells,
  actors or resources.
- `inventory.items` folds identical bag stacks and retains equipped slots.
  `object_uids` are current-session opaque handles for the validated item-use
  and equip tools; they are not durable item IDs.
  `quantity: null` means the packet did not state a quantity; it is not zero.
- `spells` retain the live availability and shortcut slot, plus static range,
  PA and effect data when local data has resolved it.
- `dialog.current` is the only actionable stage; `dialog.history` preserves
  prior text/replies for a multistage NPC conversation.
- `affordances` are candidates, not authority to act. Sending one requires a
  `transport.state` of `ready`, and the server validates it against current
  protocol state before emitting an Astra-compatible game request.
- `capture_path`, `observed_at_ms`, and `scan_state` make staleness explicit. A saved PCAP is
  useful evidence but is never presented as an attached writable session.
- `quest_plan` joins only the character's active protocol-confirmed quests to
  installed assets and compact guide metadata. It exposes preparation gaps,
  prerequisite/unlock links and objective targets. Full guide prose and image
  URLs remain on-demand, so an ordinary planning turn stays small.
- `action_status` is the global acknowledgement barrier. A planner may read
  state/quest metadata while an action is pending, but the server rejects a
  second mutating intent until the pending NPC talk/dialogue, resource, navigation,
  combat, attack, or inventory action is confirmed or cancelled. Each wait is bounded;
  a timeout releases the barrier with an explicit `timed_out` state and the
  planner must refresh live state rather than assume success.

The current Astra mapping also verifies the player-trade lifecycle. An
unsolicited `ExchangeRequestedTradeEvent` is default-denied through the
managed socket with `DialogLeaveRequest`; its account remains blocked until
the matching `ExchangeLeaveEvent`. No aliases are guessed for group/fight or
challenge invitations yet, so those remain decoder work rather than unsafe
automation claims.

The richer `/api/quests` and `/api/quests/{id}` endpoints query the installed
local quest database; they are read-only and can be used by a planner to turn
an active objective into a route/dialogue plan.

`/api/quest-graph/{id}` derives explicit completed-quest prerequisites,
unlocks, and achievement/success groups from those same installed assets. It
preserves the raw Ankama criterion too, because level and event conditions
need the live character state to evaluate safely.

Guide lookup is deliberately on-demand. `GET /api/guides/success/{id}` returns
the compact local preflight metadata for a pilot success group. `GET
/api/guides/{quest_id}` resolves an individual walkthrough from the current
quest name; adding `?fetch=true` reads that source page at request time and
returns extracted sections plus original image URLs. It does not crawl in the
background or retain guide bodies/images after the response.

`GET /api/quest-plan` is the read-only bridge for the local planner. It joins
currently active protocol quests with the local graph, guide-success prep list
and observed inventory, producing explicit missing-item checklists and next
objective targets. It cannot move, buy, talk, or otherwise affect the game.

`GET /api/routes/{target_map_id}` is a read-only route query. It uses the
same explicit world-graph transitions as AstraBot: a step includes its source
and destination map/zone, transition cell, transition map, type, required
skill and criterion. If the exported graph is not installed it fails closed;
coordinates are not treated as a substitute for a navigable route.

`world_routing` is included in the agent context so a planner can distinguish
"routing unavailable" from "no route found". The local AstraBot checkout may
contain only a Git-LFS pointer until its data has been fetched.

## Multiple accounts

The transport/session design is account-scoped. The local Astra-style login
listener reads the authoritative account ID from each identification success,
then creates that account's selected game server bridge with a separate
`ManagedTransport`. Session IDs cannot be bound to two accounts. Credentials
are intentionally outside this model and never
returned by the dashboard APIs. The current dashboard continues to expose one
active client. `account_id` makes the command/state scope explicit. The first
identified account replaces the legacy account-0 command scope; later accounts
must be explicitly selected. Each managed bridge supplies its own bounded
server/client byte streams to the shared protocol reducer, so its dashboard
tab can display a separate protocol-derived snapshot. The passive `tcpdump`
stream remains account 0 and never overwrites an identified account state.

Action acknowledgement executors are account-local. The dashboard still sends
commands only to its explicitly selected account; concurrent automation stays
disabled until a separate per-account scheduler is introduced.

## Action boundary

`POST /api/agent/intent` accepts `{ "action", "args", "reason" }`. The
server recreates `WorldState` from the latest decoded protocol result, validates
the request with `WorldToolRegistry`, then translates it to one `GameIntent`.
It sends only when the managed transport is `ready`; there is no UI-click or
coordinate fallback. `observe_world` is accepted but sends nothing.

Current action names are `talk_to_npc`, `attack_monster_group`,
`choose_dialogue_reply`, `leave_dialogue`, `use_interactive`, `cast_spell`,
`end_turn`, `use_inventory_item`, `equip_inventory_item`, `navigate_to_map`,
`inspect_active_quests`, and `get_active_quest_guide`.

The mutation actions are acknowledgement-driven: resource use waits for a
confirmed approach cell and resource lifecycle event; dialogue waits for a new
question or close event; spell/end-turn waits for a newer fight event; item
use waits for a confirmed quantity/removal delta and equipment waits for the
live target slot; map navigation executes and confirms one world-graph
transition at a time.
Quest inspection and guide metadata lookup are read-only. A guide lookup is
restricted to the quest IDs active in the current protocol snapshot; stale or
invalid requests return HTTP 400 rather than being treated as server failures.

`POST /api/agent/propose` accepts `{ "goal" }` and runs the local planner
against the same compact, account-scoped protocol snapshot. It returns exactly
one already-validated proposal plus timing/queue data; it **never executes**
that proposal. Callers must explicitly submit the returned action to
`/api/agent/intent`, where the fresh-state and acknowledgement checks run
again. Multiple accounts share one serialized local-model queue, not one model
process per account.
