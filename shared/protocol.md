# dofus-agent shared protocol (v1)

This file is the contract between the three modules. If you change it, change all three.

```
 ┌────────────┐  WebSocket JSON (this doc §2)   ┌──────────────────────────┐
 │  webapp/   │ <──────────────────────────────> │  brain/  (backend + loop) │
 │ dashboard  │   ws://127.0.0.1:8765/ws         │  perception, state,       │
 │ + Dofus    │                                  │  planner, LLM, server     │
 │   slot     │                                  └────────────┬─────────────┘
 └────────────┘                                    Python API (§4)│ import dofus_control
                                                   ┌──────────────▼────────────┐
        real Dofus window, moved/resized on top    │  control/  (macOS only)   │
        of the slot by control/  ───────────────── │  window, capture, input,  │
                                                   │  kill switch              │
                                                   └───────────────────────────┘
```

- **webapp/** – Vite + React + TypeScript. Static page; talks only WebSocket. Has an in-browser mock backend so it runs alone.
- **brain/** – Python ≥ 3.11 package `dofus_brain`. Runs the backend (`python -m dofus_brain.server`), the agent loop, perception, game state, planners, LLM client. Imports `dofus_control`, or `dofus_brain.mock_control` when not on macOS / `--mock`.
- **control/** – Python ≥ 3.11 package `dofus_control` (PyObjC). Library + CLI (`python -m dofus_control ...`). Knows nothing about Dofus game rules.

## 1. Coordinates and units

| Name | Unit | Origin | Used by |
|---|---|---|---|
| **screen points** | macOS points (not pixels) | top-left of the main display, y down (CGEvent / CGWindow convention) | control, slot rect |
| **window points** | points | top-left of the Dofus window's **content** (below the title bar) | every action sent to control |
| **capture pixels** | pixels | top-left of the captured window content image | perception |
| **CSS px** | CSS pixels | page viewport | webapp only |

- `scale = capture_pixel_width / window_point_width` (2.0 on Retina). Control returns it with every capture. Perception works in capture pixels and divides by `scale` before emitting an action.
- Map cells: Dofus map = **560 cells**, ids `0..559`, `14` per row, `40` rows; odd rows are shifted right by half a cell (isometric stagger). `row = id // 14`, `col = id % 14`. Cell centre in window points: `x = ox + col*cw + (row % 2)*cw/2`, `y = oy + row*ch/2`, where `ox, oy, cw, ch` come from the grid calibration (`brain/config/grid.json`). Defaults are placeholders until calibrated on the real client.

## 2. WebSocket protocol

URL: `ws://127.0.0.1:8765/ws` (override in the webapp with `?ws=...`; `?mock=1` forces the in-browser mock).

Every message is one JSON object:

```json
{ "type": "agent.status", "ts": 1731090000123, "id": "optional-correlation-id", "payload": { } }
```

`ts` = ms since epoch. `id` is set on client requests that want an `ack`.

### 2.1 Server → client

| type | payload | when |
|---|---|---|
| `hello` | `{ "version": 1, "server": "dofus-brain", "mock": bool }` | on connect |
| `agent.status` | `AgentStatus` | on connect, on change, at least every 2 s |
| `game.state` | `GameState` | on connect, on change (max 10/s) |
| `log.entry` | `LogEntry` | each event |
| `log.history` | `{ "entries": LogEntry[] }` | on connect (last 200) |
| `overlay.status` | `OverlayStatus` | on connect, on change |
| `frame.preview` | `{ "jpeg_b64": string, "width": int, "height": int }` | optional, ≤ 1/s, only when client sent `preview.subscribe` |
| `npcs.list` | `NpcsList` | on connect, after each `npcs.scan` |
| `network.scan.status` | `NetworkScanStatus` | on connect, when capture starts/stops/processes |
| `planner.status` | `PlannerStatus` | on connect, on change, at least every 2 s (phases planner only) |
| `ack` | `{ "ok": bool, "error"?: string }` (envelope `id` = request id) | reply to any request carrying `id` |

### 2.2 Client → server

| type | payload | effect |
|---|---|---|
| `steer.goal` | `{ "text": string }` | set the high-level goal (free text, given to the LLM goal planner) |
| `steer.pause` | `{}` | stop issuing actions; perception keeps running |
| `steer.resume` | `{}` | resume |
| `steer.mode` | `{ "mode": Mode }` | change mode |
| `steer.kill` | `{}` | **kill switch**: stop all input immediately, state → `stopped`; also turns keep-on-top and auto-align off so the Dofus window is left alone (same for the ctrl+alt+cmd+K hotkey); needs `steer.resume` to restart (which restores them) |
| `steer.step` | `{}` | in `assist` mode, execute the one proposed action |
| `slot.report` | `SlotReport` | webapp tells where the Dofus slot is (on load, resize, move, and every 2 s) |
| `overlay.align` | `{}` | ask control to move/resize Dofus onto the slot now |
| `overlay.calibrate` | `{}` | ask control to locate the slot's corner markers on screen (precise, §3) |
| `preview.subscribe` | `{ "enabled": bool }` | turn `frame.preview` on/off |
| `npcs.scan` | `{}` | "Locate NPCs": hold the entity key (Z), capture, OCR the name plates → `npcs.list`. The only input is that one key hold; refused while killed |
| `network.scan.start` | `{}` | start a bounded, read-only TCP/5555 capture |
| `network.scan.stop` | `{}` | stop and process the capture into player actors and monster groups; payloads are never replayed |
| `npcs.attack` | `{ "index"?: int, "name"?: string, "auto"?: bool }` | attack a monster group from the last `npcs.list` (NPCs are refused): goal `attack <name>`, click it, then the fight sequence runs to the end. `auto` (default true) switches the mode to `auto` |
| `fight.start` | `{ "auto"?: bool }` | goal `fight sequence`, started during character placement: Choisir les challenges → PRÊT → turns → FERMER windows, done when back on the map |

### 2.3 Types

```ts
type Mode = "observe" | "assist" | "auto";
// observe: perceive + plan, never act. assist: propose next action, wait for steer.step. auto: act.

type AgentState = "idle" | "running" | "paused" | "stopped" | "error";

interface AgentStatus {
  state: AgentState;
  mode: Mode;
  goal: string | null;            // current high-level goal text
  task: string | null;            // what the deterministic executor is doing now, e.g. "travel to (4,-18)"
  proposed: Action | null;        // next action (assist mode shows it with a Step button)
  loop_hz: number;                // perception loop rate
  last_decision_ms: number | null;// last LLM call latency
  llm: { backend: "ollama" | "mlx" | "mock"; model: string; busy: boolean };
  control: { connected: boolean; permissions: { accessibility: boolean; screen_recording: boolean } };
  error: string | null;
  uptime_s: number;
}

type Screen = "unknown" | "loading" | "world" | "fight" | "dialog" | "popup" | "menu";

interface GameState {
  screen: Screen;
  confidence: number;             // 0..1 of the screen classification
  map: { coords: [number, number] | null; area: string | null; sub_area: string | null };
  character: {
    name: string | null; level: number | null;
    hp: number | null; hp_max: number | null;
    pa: number | null; pm: number | null;       // AP / MP
    pods: number | null; pods_max: number | null;
    kamas: number | null;
  };
  fight: FightState | null;       // non-null only when screen == "fight"
  dialog: { npc: string | null; text: string; options: { index: number; text: string }[] } | null;
  quest: { name: string; step: string | null; objective: string | null } | null;
  pois: { kind: "npc" | "zaap" | "zaapi" | "bank" | "workshop" | "resource" | "monster" | "door" | "other";
          name: string; coords: [number, number] | null; cell: number | null }[];
  updated_at: number;             // ms epoch
}

interface FightState {
  phase: "placement" | "my_turn" | "enemy_turn" | "ended";
  turn: number;
  timer_s: number | null;
  fighters: { id: string; name: string; team: "ally" | "enemy"; is_self: boolean;
              cell: number | null; hp: number | null; hp_max: number | null;
              pa?: number | null; pm?: number | null }[];
  blocked_cells: number[];        // obstacles (tactical mode)
  reachable_cells: number[];      // highlighted movement cells, if known
}

interface LogEntry {
  ts: number;
  level: "debug" | "info" | "warn" | "error";
  source: "brain" | "planner" | "llm" | "perception" | "control" | "server" | "user";
  message: string;
  action?: Action;                // set when the entry is an executed / proposed action
}

interface OverlayStatus {
  dofus_window: { found: boolean; title: string | null; pid: number | null;
                  frame: Rect | null };       // screen points, content area
  slot: Rect | null;              // target, screen points (from SlotReport)
  aligned: boolean;               // |frame - slot| <= 1 pt on every edge
  keep_on_top: boolean;
  calibrated: boolean;            // slot came from marker calibration (§3) rather than estimate
}

interface Rect { x: number; y: number; w: number; h: number }

interface NpcEntity {             // one name plate from npcs.scan (window points)
  name: string; kind: "npc" | "monster"; level: number | null; group_size: number | null;
  x: number; y: number; cell: number | null; conf: number;
  xp?: number | null; members?: { name: string; level: number }[] | null;
}
interface NpcsList { coords: [number, number] | null; entities: NpcEntity[] }

interface NetworkScanStatus {
  state: "idle" | "capturing" | "ready" | "error";
  error: string | null;
  result: { packets: number; bytes: number;
    groups: { entity_id: number; cell: number | null;
      members: { monster_id: number; raw_field_4: number | null }[] }[];
    players: { entity_id: number; name: string | null; cell: number | null }[] } | null;
}

interface PlannerStatus {         // what the phases planner is doing
  goal: string | null; skill: string | null;
  attention: string | null;       // why it stopped and needs the human
  halted: string | null;
  outcome: { status: string; reason: string } | null;
  fight: { in_turn: boolean; pa_left: number | null; pm_left: number | null; plan: string | null } | null;
  attack_target: string | null;
}

interface SlotReport {
  rect_css: Rect;                 // slot rect in viewport CSS px (getBoundingClientRect)
  window: { screen_x: number; screen_y: number; outer_w: number; outer_h: number;
            inner_w: number; inner_h: number };   // window.screenX/Y, outer/innerWidth/Height
  dpr: number;                    // window.devicePixelRatio
  zoom_hint: number;              // = 1 unless the page knows it is zoomed
  estimate_screen: Rect;          // the page's own estimate in screen points (formula in §3)
}

type Action =
  | { kind: "click"; x: number; y: number; button?: "left" | "right"; double?: boolean }
  | { kind: "move"; x: number; y: number }
  | { kind: "drag"; x: number; y: number; to_x: number; to_y: number }
  | { kind: "key"; key: string; modifiers?: ("cmd" | "ctrl" | "alt" | "shift")[] }
  | { kind: "type"; text: string }
  | { kind: "scroll"; x: number; y: number; dy: number }
  | { kind: "wait"; ms: number };
// x/y are WINDOW POINTS (§1). Every action may also carry optional "reason"?: string and "cell"?: number (display only).
// Fighter with hp === 0 is dead. steer.pause while stopped → ack ok:false. slot.report is sent without id (no ack).
```

Key names for `key`: single characters (`"a"`, `"1"`), or `"enter" "escape" "space" "tab" "backspace" "up" "down" "left" "right" "f1".."f12"`.

## 3. The Dofus slot

The webapp renders a fixed-size empty rectangle, the **slot**, where the real Dofus window will sit. Control moves and resizes Dofus so its **content area** (excluding title bar) matches the slot, then keeps it on top.

- Default slot size: **1280 × 800 CSS px** at browser zoom 100% (= 1280 × 800 screen points; Dofus is 16:10-friendly). Configurable in the webapp (`?slot=1280x800`). Its top-left is pinned to a fixed spot in the layout; sidebars use the rest.
- The slot has a 4 px solid neutral grey (`#3a4350`) border **outside** the rect and four **corner markers inside** the rect corners: 12 × 12 CSS px squares, colours `#FF00FF` (TL), `#00FFFF` (TR), `#FFFF00` (BL), `#00FF00` (BR). They are only visible when Dofus isn't covering them, which is exactly when calibration runs.
- **Estimate** (no permissions needed), assuming a browser with only a top toolbar (Chrome/Arc/Safari, no bottom bar):
  `screen_x = window.screen_x + (outer_w - inner_w)/2 + rect_css.x`,
  `screen_y = window.screen_y + (outer_h - inner_h) + rect_css.y`, `w = rect_css.w`, `h = rect_css.h`. With browser zoom, multiply only the CSS-px terms (`inner_w`, `inner_h`, `rect_css.*`) by `zoom_hint`; `screen_x/y` and `outer_*` are already screen points.
  Recommended: put the browser in full screen or app mode (`chrome --app=http://localhost:5173`) so the estimate is exact.
- **Calibration** (precise): control temporarily hides/moves Dofus away, captures the display, finds the four marker colours, and derives the slot rect in screen points (pixel positions ÷ display scale). Result is stored in `brain/config/slot.json` and used instead of the estimate until the next `slot.report` shows the browser window moved.
- `brain` decides the target: calibrated rect if valid, else `estimate_screen`. It calls `control.align_window(target)` on `overlay.align`, when the slot changes, and every 2 s if `keep_on_top` and not aligned.

## 4. control ↔ brain Python API

`brain` uses only this surface (`dofus_brain.mock_control.MockController` implements the same, backed by screenshots on disk):

```python
from dofus_control import Controller, Rect, WindowInfo, Frame, PermissionStatus, KillSwitchTripped

ctl = Controller(app_name="Dofus", title_contains=None)   # matching is case-insensitive; any app works for tests
ctl.permissions() -> PermissionStatus      # .accessibility, .screen_recording (bools); never prompts
ctl.request_permissions() -> None          # triggers the macOS prompts
ctl.find_window() -> WindowInfo | None     # .window_id, .pid, .app, .title, .frame (Rect, screen points, whole window), .content (Rect, content area)
ctl.align_window(target: Rect, content=True) -> WindowInfo   # move+resize so content (or frame) == target, verify, retry; raises if impossible
ctl.keep_on_top(enabled: bool) -> None     # best effort: raise/activate Dofus whenever another app covers the slot
ctl.capture() -> Frame                     # .image (numpy HxWx3 uint8, RGB, capture pixels of the content area), .scale, .ts, .window (WindowInfo)
ctl.capture_display(display=0) -> Frame    # whole display (used by slot calibration)
ctl.calibrate_slot(markers=DEFAULT_MARKERS) -> Rect   # §3 calibration, screen points
ctl.execute(action: dict) -> None          # one §2.3 Action, window points; humanlike timing applied; raises KillSwitchTripped
ctl.kill() / ctl.reset_kill() / ctl.killed -> bool
ctl.on_kill(callback) -> None              # called (from any thread) when the kill switch trips
ctl.close() -> None
```

Kill switch triggers (any one): `ctl.kill()`; global hotkey **ctrl+alt+cmd+K** (via a listen-only CGEventTap); the **real user moving the mouse** more than 40 pt away from where control last put it while an action runs; the mouse slammed into the **top-left screen corner**. Once tripped, `execute` raises until `reset_kill()`.

Humanlike timing (defaults, configurable): mouse moves along a curved (Bezier) path with ease-in-out over 120–450 ms depending on distance, ±2 pt random jitter on the target unless `exact=True`, 40–110 ms mouse-down hold, 60–180 ms between key down/up and between typed characters, and a 150–600 ms random pause after every action.

## 5. Ports and processes

| What | Command | Port |
|---|---|---|
| webapp dev | `cd webapp && npm run dev` | 5173 |
| backend | `python -m dofus_brain.server [--mock]` | 8765 (WS `/ws`, HTTP `GET /health`) |
| LLM | Ollama `ollama serve` (default) or `mlx_vlm.server` | 11434 / 8080 |
