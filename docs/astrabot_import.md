# AstraBot-derived protocol compatibility

This project is a local Mac implementation.  The decoder incorporates the
useful Dofus 3 protocol mapping and reducer behavior from the local AstraBot
checkout (`/private/tmp/AstraBot`), especially:

- `MapCurrentEvent` / `MapComplementaryInformationEvent` map lifecycle
- `InventoryContentEvent`, `InventoryWeightEvent`, `KamasUpdateEvent`, and
  object add/delete reducers
- `JobExperiencesUpdateEvent` job snapshot reducer
- character-selection and fight-map lifecycle ordering

The current-build aliases are isolated in `brain/dofus_brain/network_scan.py`
so a future game update can replace the mapping without changing the UI. Raw
unknown events remain available through the Protocol tab.

Source reference: <https://github.com/Valentin-alix/AstraBot>.

## Launcher integration

The launch path follows AstraBot's important architecture: it starts a local
Zaap/Thrift service on `127.0.0.1:26116`, keeps the session alive, and answers
the Dofus `connect`, settings, user-info, and game-token calls. It then starts
the official Dofus executable with the matching instance/hash and connection
port. The emulator is intentionally narrow. The webapp's **Authenticate
Ankama** button performs local OAuth 2.0 + PKCE in a headed Chrome window,
exchanges the callback for a HAAPI game token, and stores it in
`brain/config/ankama_auth.json` with mode `0600`. `DOFUS_GAME_TOKEN` remains an
optional diagnostic override.

The backend declares `thrift>=0.20` as a runtime dependency.
