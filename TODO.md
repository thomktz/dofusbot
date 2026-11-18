# TODO

## Current blockers

- Replace the current friendly-NPC scan/classifier: friendly NPC nameplates are
  sometimes grouped into the mob section. Keep NPCs, players, resources, and
  monster groups as separate entity types, with map/cell association validated
  against real captures.
- Make web-launched Dofus use the validated saved slot size and position instead
  of opening fullscreen.
- Populate the player header from network data: character name, level, XP/next
  level XP, HP, AP, MP, map coordinates, and current map/region name are still
  missing or incomplete in the live dashboard.

## Follow-up

- Extend the account-scoped interruption policy to decoded protocol events. Player-trade
  requests now default-deny through the managed socket; add the remaining unsolicited
  party/group invitations, exchange windows,
  challenge/fight invites, and unexpected dialogs; pause only that account,
  send the typed refuse/leave request, wait for the matching server close
  event, then resume. Allow exceptions only for an explicit managed-account
  coordination job.
- Finish the AstraBot-style central account manager: the local connection listener now
  extracts Ankama's authoritative account ID and creates a distinct managed game bridge
  and transport per connection. Each bridge now has a separate bounded protocol snapshot.
  Action acknowledgement executors are account-local too. Still needed:
  independently launch/manage multiple Dofus clients and add an explicit
  per-account automation scheduler before concurrent automation is enabled.
- Add a launch integration test with a local Thrift Zaap server fixture.
- Keep the AstraBot-compatible launcher route covered by a manual login test.

## Managed socket acceptance (after `sudo ./run.sh`)

1. Launch Dofus only through the dashboard and confirm `SOCKET · ready` after
   character entry (not merely `LIVE`, which only means the web socket is up).
2. Open an NPC: verify the protocol dialogue shows each stage, then reply and
   leave from the dashboard. Confirm the game advances/closes and the action
   UID increments.
3. Use one ready resource and verify the server acknowledgement updates the
   interactive state/inventory.
4. Engage a monster group, then confirm one manually selected spell cast and
   end-turn action reach the game only through the managed socket.
5. Exercise one inventory item use/equip action against a current object UID;
   verify a fresh inventory packet reflects the change.
