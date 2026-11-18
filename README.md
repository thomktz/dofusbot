# dofus-agent

A local Dofus assistant for macOS. It's a dashboard with a fixed slot for the real Dofus window, a Python brain that watches the screen (OCR + a local vision LLM), and a control layer that sends humanlike input behind a kill switch. Everything runs on the Mac. Nothing leaves localhost.

```
webapp/  (Vite + React)  ⇄  WebSocket ws://127.0.0.1:8765/ws  ⇄  brain/ (dofus_brain)  →  control/ (dofus_control, macOS)
```

The contract between the three parts is [`shared/protocol.md`](shared/protocol.md).

| part | what | docs |
|---|---|---|
| `webapp/` | dashboard, steering, slot for the Dofus window, in-browser mock backend | [webapp/README.md](webapp/README.md) |
| `brain/` | backend server, agent loop, perception, game state, planners, LLM client | [brain/README.md](brain/README.md) |
| `control/` | window find/align/capture, humanlike mouse and keyboard, kill switch | [control/README.md](control/README.md) |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e 'brain[mac,dev]' -e control
(cd webapp && npm install)
# optional, for the vision LLM (see brain/README.md):
ollama serve &
ollama pull qwen3.5:4b
```

Give the terminal **Accessibility** and **Screen Recording** permissions (`python -m dofus_control perms`).

## Run

```bash
./run.sh                    # backend (real control, target "Dofus", observe mode) + webapp
./run.sh --mock             # no game: fixture screenshots + mock LLM
./run.sh --app TextEdit     # try the real pipeline on any window
BRAIN_PORT=8770 ./run.sh    # if 8765 is taken; the page URL then carries ?ws=...
```

Open http://localhost:5173. Ctrl+C stops both processes.

## Safety

- The backend starts in **observe** mode and sends no input until you pick assist or auto in the dashboard.
- Kill switch: **ctrl+alt+cmd+K**, the mouse slammed into the top-left screen corner, moving the mouse yourself while an action runs, or **Esc** in the dashboard.

## Tests

```bash
(cd control && python -m pytest -q)
(cd brain && python -m pytest -q)
(cd webapp && npm test)
python control/scripts/smoke_macos.py      # live, on a new TextEdit document
```
