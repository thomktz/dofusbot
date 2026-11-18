# dofus_control

macOS-only control layer (Python ≥ 3.11, PyObjC). It finds a window, aligns its content onto a screen rect (the webapp's slot), captures it, and sends humanlike mouse and keyboard input, all behind a kill switch. It knows nothing about Dofus game rules. The contract with `brain/` is `shared/protocol.md` §4.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate   # from the repo root
pip install -e control
```

macOS permissions for the terminal/app that runs it (System Settings → Privacy & Security):

- **Accessibility**: move/resize windows, post input events, and the kill-switch event tap
- **Screen Recording**: window capture

`python -m dofus_control perms` shows what is granted.

## CLI

Every command works with any app, so you can test on TextEdit before Dofus is involved.

```bash
python -m dofus_control list                                   # on-screen windows, front to back
python -m dofus_control find    --app TextEdit [--title X] [--json]
python -m dofus_control align   --app TextEdit --rect 200,200,700,450   # content rect, screen points
python -m dofus_control capture --app TextEdit --out shot.png           # PNG + scale
python -m dofus_control move    --app TextEdit --x 300 --y 100          # window points
python -m dofus_control click   --app TextEdit --x 300 --y 100 [--double|--right]
python -m dofus_control type    --app TextEdit --text "bonjour éà"
python -m dofus_control key     --app TextEdit --key a --mods cmd
python -m dofus_control scroll  --app TextEdit --x 300 --y 100 --dy 3
python -m dofus_control calibrate                              # find the slot's corner markers (§3)
python -m dofus_control killswitch-test --seconds 20
python -m dofus_control demo                                   # safe TextEdit demo
```

Global flags: `-v`, `--title-bar N` (override title bar height), `--layout auto|us|azerty`.

## Kill switch

Any of these trips it. Every running and queued action then stops, and held keys and buttons are released:

- **ctrl+alt+cmd+K** (global event tap; key-state polling as a fallback)
- slamming the mouse into the **top-left corner** of a display
- the real user moving the mouse more than 40 pt off the planned path while an action runs
- `Controller.kill()` / `steer.kill` from the webapp (Esc in the dashboard)

## Keyboard layouts

macOS keycodes are physical key positions. With the default `--layout auto`, the active layout is read through `UCKeyTranslate` (`macos/layout.py`), so `key a` hits the key that produces "a" on AZERTY, "French - numerical", QWERTZ and so on. Without this, cmd+a would come out as cmd+q on AZERTY. Digits always use the physical number row. If detection fails, it falls back to US. Text should go through `type`, which uses the unicode-string API and doesn't depend on the layout.

## Tests

```bash
cd control && python -m pytest -q                 # unit tests, no real input (macOS calls stubbed)
python control/scripts/smoke_macos.py              # live end-to-end on a new TextEdit document
python control/scripts/smoke_macos.py --interactive   # + hotkey / mouse-yank checks
```

## Layout

| module | role |
|---|---|
| `controller.py` | public `Controller` API used by brain (§4) |
| `driver.py`, `human.py` | input sequencing, humanlike paths and timing |
| `keys.py`, `macos/layout.py` | key names → keycodes, layout detection |
| `killswitch.py` | kill switch logic and poller |
| `geometry.py`, `markers.py` | rect math, slot marker detection |
| `macos/` | CGWindow, CGEvent, ScreenCaptureKit/CG capture, AX window moves, permissions |
