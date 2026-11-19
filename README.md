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

## Windows 11 ARM VM (VMware Fusion)

Use this when running Dofus inside a Windows 11 ARM VM on an Apple-silicon
Mac.  Install **VMware Tools** first, restart the VM, and verify that Edge can
reach the internet.  The local vision model stays on the Mac; do not install
Ollama in the 8 GB VM.

### Type only these two commands before cloning

Open **PowerShell** in Windows and type these two commands, one at a time:

```powershell
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
& 'C:\Program Files\Git\cmd\git.exe' clone https://github.com/thomktz/dofusbot.git C:\dofusbot
```

Then open `C:\dofusbot` in File Explorer, right-click
`setup-windows.ps1`, and select **Run with PowerShell**.  That repository
script installs Python and Node, creates `.venv`, installs the Python and web
dependencies, and offers to start the verified mock dashboard at
http://localhost:5173.  No more commands need to be typed by hand.

The bootstrap deliberately does not start a game client.  The Windows
live-protocol launcher is configured after the Ankama Launcher and its Dofus
executable are installed in the VM.

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
