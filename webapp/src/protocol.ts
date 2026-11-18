// Mirrors shared/protocol.md §2 (v1). Keep in sync with the doc; do not invent fields.

export const PROTOCOL_VERSION = 1;

// ---- §2.3 Types -----------------------------------------------------------

export type Mode = "observe" | "assist" | "auto";
// observe: perceive + plan, never act. assist: propose next action, wait for steer.step. auto: act.

export type AgentState = "idle" | "running" | "paused" | "stopped" | "error";

export interface AgentStatus {
  state: AgentState;
  mode: Mode;
  goal: string | null;
  task: string | null;
  proposed: Action | null;
  loop_hz: number;
  last_decision_ms: number | null;
  llm: { backend: "ollama" | "mlx" | "mock"; model: string; busy: boolean };
  control: { connected: boolean; permissions: { accessibility: boolean; screen_recording: boolean } };
  error: string | null;
  uptime_s: number;
}

export type Screen = "unknown" | "loading" | "world" | "fight" | "dialog" | "popup" | "menu";

export type PoiKind = "npc" | "zaap" | "zaapi" | "bank" | "workshop" | "resource" | "monster" | "door" | "other";

export interface GameState {
  screen: Screen;
  confidence: number;
  map: { coords: [number, number] | null; area: string | null; sub_area: string | null };
  character: {
    name: string | null; level: number | null;
    hp: number | null; hp_max: number | null;
    pa: number | null; pm: number | null;
    pods: number | null; pods_max: number | null;
    kamas: number | null;
  };
  fight: FightState | null;
  dialog: { npc: string | null; text: string; options: { index: number; text: string }[] } | null;
  quest: { name: string; step: string | null; objective: string | null } | null;
  pois: { kind: PoiKind; name: string; coords: [number, number] | null; cell: number | null }[];
  updated_at: number;
}

export interface Fighter {
  id: string; name: string; team: "ally" | "enemy"; is_self: boolean;
  cell: number | null; hp: number | null; hp_max: number | null;
  pa?: number | null; pm?: number | null;
}

export interface FightState {
  phase: "placement" | "my_turn" | "enemy_turn" | "ended";
  turn: number;
  timer_s: number | null;
  fighters: Fighter[];
  blocked_cells: number[];
  reachable_cells: number[];
}

export type LogLevel = "debug" | "info" | "warn" | "error";
export type LogSource = "brain" | "planner" | "llm" | "perception" | "control" | "server" | "user" | "protocol";

export interface LogEntry {
  ts: number;
  level: LogLevel;
  source: LogSource;
  message: string;
  action?: Action;
}

export interface Rect { x: number; y: number; w: number; h: number }

export interface OverlayStatus {
  dofus_window: { found: boolean; title: string | null; pid: number | null; frame: Rect | null };
  slot: Rect | null;
  aligned: boolean;
  keep_on_top: boolean;
  calibrated: boolean;
}

export interface SlotReport {
  rect_css: Rect;
  window: { screen_x: number; screen_y: number; outer_w: number; outer_h: number; inner_w: number; inner_h: number };
  dpr: number;
  zoom_hint: number;
  estimate_screen: Rect;
}

/** One plate from the "Locate NPCs" scan (entity key held, npcs.list_npcs). x/y = window points. */
export interface NpcEntity {
  name: string;
  kind: "npc" | "monster";
  level: number | null;
  group_size: number | null;
  x: number; y: number;
  cell: number | null;
  conf: number;
  xp?: number | null;
  members?: { name: string; level: number }[] | null;
}

export interface NpcsList { coords: [number, number] | null; entities: NpcEntity[] }

export interface NetworkScanMember { monster_id: number; name: string | null; grade: number | null; level: number | null; levels: number[]; raw_field_4: number | null; xp?: number | null }
export interface NetworkScanGroup { entity_id: number; cell: number | null; members: NetworkScanMember[] }
export interface NetworkScanPlayer { entity_id: number; name: string | null; cell: number | null }
export interface NetworkFightMonster { monster_id: number; name: string | null; grade: number | null; level: number | null; cell?: number | null }
export interface NetworkFightEvent { offset: number; type: string; kind: string; actor_id?: number; cell?: number; turn?: number; team?: number; name?: string; monster_id?: number; grade?: number; level?: number; hp?: number; hp_max?: number; pa?: number; pm?: number; action_id?: number; source_id?: number; target_cells?: number[]; target_ids?: number[]; effect_codes?: number[]; spell_id?: number; destination_cell?: number; target_id?: number; delta?: number; hp_loss?: number; dead_id?: number; start_cell?: number; end_cell?: number; path_cells?: number[] }
export interface NetworkTerrain { walkable: number; walkable_cells?: number[]; blocked: number[]; holes: number[]; red: number[]; blue: number[]; source?: string; source_path?: string }
export interface NetworkCharacter { name?: string | null; level: number | null; experience: number | null; experience_next: number | null; hp: number | null; hp_max: number | null; pa: number | null; pm: number | null; entity_id?: number | null; cell?: number | null }
export interface NetworkInventory { kamas: number | null; weight: number | null; weight_max: number | null; objects: { uid: number | null; quantity: number | null; gid: number | null; position: number | null; container?: "bag" | "equipped" | "other"; position_name?: string | null; name?: string | null; type_id?: number | null; level?: number | null; icon_id?: number | null }[] }
export interface NetworkJob { job_id: number | null; level: number | null; xp: number | null; xp_floor: number | null; xp_next: number | null }
export interface NetworkProtocolEvent { offset: number; alias: string; size: number; scalar_fields: Record<string, number[]> }
export interface NetworkFightSnapshot { active: boolean; phase: string; map_id?: number | null; turn?: number | null; fighters: { id: number; cell?: number | null; team?: number | null; alive?: boolean; kind?: string; monster_id?: number; name?: string | null; grade?: number | null; level?: number | null; hp?: number | null; hp_max?: number | null; pa?: number | null; pm?: number | null }[]; events: NetworkFightEvent[]; monster_records: NetworkFightMonster[]; player_records: NetworkScanPlayer[] }
export interface NetworkDialog { actor_id?: number | null; npc_id?: number | null; npc_name?: string | null; message_id?: number | null; text?: string | null; params?: string[]; replies?: number[]; options?: { reply_id: number; text?: string | null }[]; stages?: { message_id?: number | null; text?: string | null; params?: string[]; replies?: number[]; options?: { reply_id: number; text?: string | null }[] }[] }
export interface NetworkSpellEffect { action_id?: number | null; element?: number | null; min?: number | null; max?: number | null; value?: number | null; duration?: number | null; target_mask?: string | null; zone?: { shape?: number | null; param1?: number | null; param2?: number | null } }
export interface NetworkSpell { spell_id: number; level?: number | null; available?: boolean; name?: string | null; description?: string | null; icon_id?: number | null; ap_cost?: number | null; min_range?: number | null; range?: number | null; critical_hit_probability?: number | null; max_cast_turn?: number | null; max_cast_target?: number | null; max_global_cast_turn?: number | null; min_cast_interval?: number | null; initial_cooldown?: number | null; global_cooldown?: number | null; min_player_level?: number | null; effects?: NetworkSpellEffect[] }
export interface NetworkShortcut { bar: number; slot: number; kind: "item" | "spell" | "unknown"; gid?: number | null; spell_id?: number | null; name?: string | null; icon_id?: number | null }
export interface NetworkQuestObjective { objective_id: number; reached?: boolean; params?: string[]; completion?: { current: number; max: number } | null; type_id?: number | null; type_name?: string | null; description?: string | null; dialog_id?: number | null; map_id?: number | null; coords?: { x: number; y: number } | null; parameters?: Record<string, number>; parameter_labels?: Record<string, string> }
export interface NetworkQuest { quest_id: number; name?: string | null; level_min?: number | null; step_id?: number | null; step_name?: string | null; step_description?: string | null; objective_ids?: number[]; objectives?: NetworkQuestObjective[] }
export interface NetworkScanResult { packets: number; bytes: number; groups: NetworkScanGroup[]; players: NetworkScanPlayer[]; fight_monsters: NetworkFightMonster[]; fight_players?: NetworkScanPlayer[]; fight_events?: NetworkFightEvent[]; fight?: NetworkFightSnapshot; placement_moves?: number[]; fight_map_id?: number | null; map_id?: number | null; map_position?: { x: number; y: number; sub_area_id?: number | null; subarea_name?: string | null; area_name?: string | null; world_map?: number | null }; map_events?: { kind: string; type: string; offset: number }[]; interactives?: { id: number; cell: number | null; gfx_id?: number | null; available?: boolean | null; state?: number | null; name?: string | null; kind?: string; resource_name?: string | null; resource_id?: number | null; icon_id?: number | null; job_name?: string | null; level_min?: number | null; skills?: number[]; skill_instances?: { skill_id: number; skill_instance_uid: number | null; name_id?: number | null }[] }[]; stated_elements?: { id: number | null; state: number | null }[]; character?: NetworkCharacter; inventory?: NetworkInventory; jobs?: NetworkJob[]; dialog?: NetworkDialog | null; spells?: NetworkSpell[]; shortcuts?: NetworkShortcut[]; quests?: { active: NetworkQuest[]; completed: { quest_id: number }[] }; protocol_events?: NetworkProtocolEvent[]; capture_path?: string; terrain?: NetworkTerrain; world?: Record<string, unknown> }
export interface NetworkScanStatus { state: "idle" | "listening" | "capturing" | "ready" | "error"; result: NetworkScanResult | null; error: string | null }
export interface DofusStatus { state: "idle" | "authenticating" | "authenticated" | "launching" | "launched" | "mock" | "error"; app: string; launcher?: string; pid?: number; error: string | null }

/** What the phase planner is doing (dashboard fight flow). */
export interface PlannerStatus {
  goal: string | null;
  skill: string | null;
  attention: string | null;      // why it stopped and needs you
  halted: string | null;
  outcome: { status: string; reason: string } | null;
  fight: { in_turn: boolean; pa_left: number | null; pm_left: number | null; plan: string | null } | null;
  attack_target: string | null;
  preview?: string | null;       // terrain preview during placement: "preview parsed, ready" or why not
  armed?: boolean;               // "Start" pressed: window aligned + kept in front, buttons primed
  error?: string;
}

export interface AgentGoalStatus {
  active: boolean;
  run: {
    account_id: number; goal: string; state: "running" | "waiting_confirmation" | "paused" | "completed" | "error";
    steps: number; last_narration: string | null; last_action: { action?: string; reason?: string } | null;
    error: string | null; updated_at: number;
  } | null;
}

/** "Optional on every action: reason, cell (for display)" — §2.3 comment under Action. */
export interface ActionMeta { reason?: string; cell?: number }

export type Action = ActionMeta & (
  | { kind: "click"; x: number; y: number; button?: "left" | "right"; double?: boolean }
  | { kind: "move"; x: number; y: number }
  | { kind: "drag"; x: number; y: number; to_x: number; to_y: number }
  | { kind: "key"; key: string; modifiers?: ("cmd" | "ctrl" | "alt" | "shift")[] }
  | { kind: "type"; text: string }
  | { kind: "scroll"; x: number; y: number; dy: number }
  | { kind: "wait"; ms: number }
);

// ---- §2.1 Server -> client ------------------------------------------------

export interface ServerPayloads {
  "hello": { version: number; server: string; mock: boolean };
  "agent.status": AgentStatus;
  "game.state": GameState;
  "log.entry": LogEntry;
  "log.history": { entries: LogEntry[] };
  "overlay.status": OverlayStatus;
  "frame.preview": { jpeg_b64: string; width: number; height: number };
  "npcs.list": NpcsList;
  "network.scan.status": NetworkScanStatus;
  "dofus.status": DofusStatus;
  "transport.status": { state: "offline" | "connecting" | "ready" | "error"; session_id: string | null; error: string | null; uid: number; sent: number };
  "accounts.status": { active_account_id: number; mode: "independent"; model_runtime: "shared"; accounts: { account_id: number; profile_id?: string | null; session_id: string | null; map_id: number | null; character: { id: number | null; name: string | null; level: number | null } }[]; profiles?: { id: string; label: string; authenticated: boolean }[]; clients?: { profile_id: string; zaap_port: number; state: string; pid: number | null; error: string | null }[] };
  "planner.status": PlannerStatus;
  "agent.goal.status": AgentGoalStatus;
  "ack": { ok: boolean; error?: string };
  "calibration.status": { state: string; message: string; samples: number; cell?: number; screen?: [number, number]; relative?: [number, number] };
}

// ---- §2.2 Client -> server ------------------------------------------------

export interface ClientPayloads {
  "steer.goal": { text: string };
  "steer.pause": Record<string, never>;
  "steer.resume": Record<string, never>;
  "steer.mode": { mode: Mode };
  "steer.kill": Record<string, never>;
  "steer.reset": Record<string, never>;
  "steer.step": Record<string, never>;
  "slot.report": SlotReport;
  "overlay.align": Record<string, never>;
  "overlay.calibrate": Record<string, never>;
  "overlay.adopt_current": Record<string, never>;
  "grid.calibrate.cell": { cell: number };
  "grid.calibrate.player": Record<string, never>;
  "preview.subscribe": { enabled: boolean };
  "session.start": Record<string, never>;
  "npcs.scan": Record<string, never>;
  "network.scan.start": Record<string, never>;
  "network.scan.stop": Record<string, never>;
  "network.scan.refresh": Record<string, never>;
  "dofus.launch": { profile_id?: string };
  "dofus.authenticate": { profile_id?: string };
  "accounts.select": { account_id: number };
  "accounts.profile.create": { label: string };
  "accounts.profile.rename": { profile_id: string; label: string };
  "npcs.attack": { entity_id?: number; index?: number; name?: string; cell?: number; auto?: boolean };
  "npcs.talk": { entity_id: number; cell: number };
  "dialog.reply": { reply_id: number };
  "dialog.leave": Record<string, never>;
  "agent.intent": { action: string; args?: Record<string, unknown>; reason?: string };
  "agent.propose_next": { goal: string };
  "agent.step": { goal: string };
  "agent.goal.start": { goal: string };
  "agent.goal.pause": Record<string, never>;
  "agent.goal.resume": Record<string, never>;
  "world.navigate": { target_map_id: number };
  "quest.next": Record<string, never>;
  "session.plan": { duration_minutes: number; jobs?: Record<string, unknown>[] };
  "interactives.use": { id: number; cell: number };
  "inventory.use": { object_uid: number };
  "fight.start": { auto?: boolean };
  "fight.semi_auto": Record<string, never>;
  "map.move": { direction: "up" | "down" | "left" | "right" };
}

export type ServerType = keyof ServerPayloads;
export type ClientType = keyof ClientPayloads;

export interface Envelope<T extends string, P> { type: T; ts: number; id?: string; payload: P }

export type ServerMessage = { [K in ServerType]: Envelope<K, ServerPayloads[K]> }[ServerType];
export type ClientMessage = { [K in ClientType]: Envelope<K, ClientPayloads[K]> }[ClientType];

export const SERVER_TYPES: readonly ServerType[] = [
  "hello", "agent.status", "game.state", "log.entry", "log.history", "overlay.status", "frame.preview",
  "npcs.list", "network.scan.status", "dofus.status", "transport.status", "planner.status", "calibration.status", "ack",
];

/** Minimal structural validation of an incoming frame. Returns null if it is not a known server message. */
export function parseServerMessage(raw: string): ServerMessage | null {
  let obj: unknown;
  try { obj = JSON.parse(raw); } catch { return null; }
  if (!obj || typeof obj !== "object") return null;
  const m = obj as { type?: unknown; payload?: unknown };
  if (typeof m.type !== "string" || !(SERVER_TYPES as readonly string[]).includes(m.type)) return null;
  if (!m.payload || typeof m.payload !== "object") return null;
  return obj as ServerMessage;
}

export function describeAction(a: Action): string {
  let s: string;
  switch (a.kind) {
    case "click": s = `${a.double ? "double-" : ""}${a.button === "right" ? "right-" : ""}click (${r(a.x)}, ${r(a.y)})`; break;
    case "move": s = `move (${r(a.x)}, ${r(a.y)})`; break;
    case "drag": s = `drag (${r(a.x)}, ${r(a.y)}) → (${r(a.to_x)}, ${r(a.to_y)})`; break;
    case "key": s = `key ${[...(a.modifiers ?? []), a.key].join("+")}`; break;
    case "type": s = `type "${a.text}"`; break;
    case "scroll": s = `scroll ${a.dy > 0 ? "+" : ""}${a.dy} at (${r(a.x)}, ${r(a.y)})`; break;
    case "wait": s = `wait ${a.ms} ms`; break;
  }
  if (a.cell != null) s += ` · cell ${a.cell}`;
  return s;
}
const r = (n: number) => Math.round(n);
