# dofus_brain

The backend and agent loop (Python ≥ 3.11). It runs perception (screen classification, OCR, HUD/fight/dialog readers), game state, planners, the local-LLM client, and the WebSocket server the webapp talks to (`shared/protocol.md` §2). It drives the game through `dofus_control`, or through `dofus_brain.mock_control` (fixture screenshots) with `--mock` or off macOS.

## Install

```bash
source .venv/bin/activate                     # repo root
pip install -e 'brain[mac,dev]'               # mac = Apple Vision OCR + Quartz
pip install -e control                        # real control (macOS)
```

## Run the backend

```bash
python -m dofus_brain.server --mock                       # fixtures + mock LLM, no game needed
python -m dofus_brain.server                              # real: target app "Dofus", mode observe
python -m dofus_brain.server --app TextEdit --mode observe   # try the pipeline on any window
```

Flags: `--host 127.0.0.1 --port 8765`, `--mode observe|assist|auto`, `--planner noop|demo`, `--llm ollama|mlx|mock`, `--model NAME`, `--title X`, `--log-level`.

The server starts in `observe` mode, so it sends no input until you switch modes from the dashboard. Esc in the dashboard (or ctrl+alt+cmd+K anywhere) trips the kill switch.

## Local LLM

The config is in `config/llm.json`: `backend` (ollama | mlx | mock), `model`, URLs, timeout and retries, `max_image_side`. The default is Ollama with `qwen3.5:4b`. Keep one model loaded (`OLLAMA_MAX_LOADED_MODELS=1`).

```bash
brew install ollama && ollama serve &
ollama pull qwen3.5:4b
python -m dofus_brain.llm --health
python -m dofus_brain.llm "Décris cette capture" --image brain/fixtures/dialog.png
python -m dofus_brain.llm "Quel écran ?" --image shot.png --classify --json --model qwen3.5:4b
python brain/scripts/bench_llm.py --models qwen3.5:4b,qwen3-vl:4b --runs 3 --sizes 640,1280
```

## Build a real evaluation set

Synthetic fixtures are only a smoke test. To collect diverse real Dofus screenshots without
sending game input, start the normal backend in observe mode with an output directory:

```bash
python -m dofus_brain.server --capture-dir data/vlm-corpus
```

It saves a deduplicated PNG and adjacent JSON metadata only when a screen looks materially
different. Set each JSON file's `truth_screen` after reviewing the images, then use that
corpus for the model comparison. The collector is local and opt-in; it does not press keys,
click, or change the game window.

`bench_llm.py` reports cold load time, text and image prefill, generation tok/s and memory, and writes JSON with `--out`.

## OCR

Apple Vision via `ocrmac`/PyObjC, with Tesseract as an optional fallback.

```bash
python -m dofus_brain.ocr shot.png --region map_coords     # crop from config/regions.json
python brain/scripts/smoke_ocr_macos.py                     # synthetic French test card
```

## Config

| file | content |
|---|---|
| `config/agent.json` | loop timing, action timeouts/retries, VLM fallback threshold, fight defaults |
| `config/grid.json` | map-cell calibration `ox, oy, cw, ch` (placeholders until calibrated) |
| `config/regions.json` | HUD regions as fractions of the capture |
| `config/llm.json` | LLM backend and model |

## Tests

```bash
cd brain && python -m pytest -q        # offline: mock control + mock LLM + fixtures
```

`fixtures/` holds synthetic screenshots (world, fight, dialog, popup, loading) made by `scripts/make_fixtures.py`.
