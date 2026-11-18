// In-browser mock of dofus-brain (protocol §2). Emits realistic, changing Dofus-like data and
// answers steering requests the way the real backend should.
import type {
  Action, AgentStatus, ClientMessage, Fighter, FightState, GameState, LogEntry, LogLevel, LogSource,
  NpcEntity, NetworkScanStatus, OverlayStatus, PlannerStatus, ServerPayloads, ServerType, SlotReport, DofusStatus,
} from "../protocol";
import type { InProcessServer } from "../link";
import { CELL_COUNT, cellCenter, cellDistance, cellNeighbours, cellRowCol } from "../grid";
import { rectEquals } from "../slot";
import { renderPreviewJpeg } from "./preview";

type Scene = "world" | "fight" | "dialog";
const SCENE_MS = 20_000;
/** Placeholder grid calibration in window points for a 1280×800 client (§1: real values come from grid.json). */
const GRID = { ox: 64, oy: 34, cw: 80, ch: 40 };

const pick = <T,>(xs: readonly T[], rnd = Math.random): T => xs[Math.floor(rnd() * xs.length)];
const randInt = (a: number, b: number) => a + Math.floor(Math.random() * (b - a + 1));
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

const WORLD_ROUTE: { coords: [number, number]; area: string; sub: string }[] = [
  { coords: [4, -18], area: "Astrub", sub: "Cité d'Astrub" },
  { coords: [5, -18], area: "Astrub", sub: "Cité d'Astrub" },
  { coords: [5, -17], area: "Astrub", sub: "Champs d'Astrub" },
  { coords: [6, -17], area: "Astrub", sub: "Champs d'Astrub" },
  { coords: [6, -16], area: "Astrub", sub: "Forêt d'Astrub" },
  { coords: [5, -16], area: "Astrub", sub: "Forêt d'Astrub" },
];

const MONSTERS = [
  { name: "Bouftou", hp: 62 }, { name: "Tofu", hp: 34 }, { name: "Boufton Blanc", hp: 41 },
  { name: "Bouftou Royal", hp: 180 }, { name: "Larve Bleue", hp: 48 }, { name: "Pissenlit Diabolique", hp: 55 },
  { name: "Tofu Maléfique", hp: 70 }, { name: "Arakne", hp: 28 },
];

const DIALOGS = [
  {
    npc: "Ganymède",
    text: "Ah, un aventurier ! Les Bouftous ravagent les champs au sud d'Astrub. Si tu m'en ramènes trois laines, je saurai te récompenser.",
    options: ["J'accepte de vous aider.", "Que se passe-t-il dans les champs ?", "Au revoir."],
  },
  {
    npc: "Banquier d'Astrub",
    text: "Bienvenue à la banque d'Astrub. Souhaitez-vous consulter votre coffre ? Le dépôt vous coûtera 12 kamas.",
    options: ["Consulter mon coffre.", "Je voudrais acheter un parchemin.", "Rien, merci."],
  },
  {
    npc: "Maître Bûcheron",
    text: "Tu veux apprendre le métier de bûcheron ? Il te faudra une hache et de la patience. Les Frênes poussent à l'est de la cité.",
    options: ["Apprendre le métier de bûcheron.", "Où trouver une hache ?", "Plus tard."],
  },
];

const SPELLS = ["Pression", "Compulsion", "Épée Divine", "Bond", "Intimidation", "Concentration"];

function baseStatus(): AgentStatus {
  return {
    state: "running",
    mode: "assist",
    goal: "Monter niveau 45 en chassant des Bouftous autour d'Astrub",
    task: "travel to (5,-17)",
    proposed: null,
    loop_hz: 5,
    last_decision_ms: 840,
    llm: { backend: "mock", model: "qwen2.5-vl:7b (mock)", busy: false },
    control: { connected: true, permissions: { accessibility: true, screen_recording: true } },
    error: null,
    uptime_s: 0,
  };
}

function baseGame(): GameState {
  return {
    screen: "world",
    confidence: 0.96,
    map: { coords: [4, -18], area: "Astrub", sub_area: "Cité d'Astrub" },
    character: { name: "Lyrelle", level: 42, hp: 498, hp_max: 530, pa: 7, pm: 4, pods: 812, pods_max: 1650, kamas: 48_230 },
    fight: null,
    dialog: null,
    quest: { name: "Les laines de la discorde", step: "Étape 2/4", objective: "Laine de Bouftou : 1/3" },
    pois: [],
    updated_at: Date.now(),
  };
}

export function mockNpcs(): NpcEntity[] {
  return [
    { name: "Ganymède", kind: "npc", level: null, group_size: null, x: 512, y: 210, cell: 312, conf: 0.97 },
    { name: "Bouftou (34), Boufton Blanc (31)", kind: "monster", level: 65, group_size: 2, x: 760, y: 330, cell: 259, conf: 0.93,
      xp: 412, members: [{ name: "Bouftou", level: 34 }, { name: "Boufton Blanc", level: 31 }] },
    { name: "Tofu (12)", kind: "monster", level: 12, group_size: 1, x: 430, y: 420, cell: 344, conf: 0.9 },
  ];
}

function poisFor(coords: [number, number]): GameState["pois"] {
  const [x, y] = coords;
  const out: GameState["pois"] = [
    { kind: "zaap", name: "Zaap d'Astrub", coords: [5, -18], cell: x === 5 && y === -18 ? 297 : null },
    { kind: "bank", name: "Banque d'Astrub", coords: [4, -16], cell: null },
    { kind: "workshop", name: "Atelier des bûcherons", coords: [3, -17], cell: null },
  ];
  if (y >= -17) {
    out.push({ kind: "monster", name: `Groupe de Bouftous (niv. ${randInt(30, 52)})`, coords, cell: randInt(150, 420) });
    out.push({ kind: "resource", name: "Frêne", coords, cell: randInt(100, 500) });
    out.push({ kind: "resource", name: "Blé", coords, cell: randInt(100, 500) });
  } else {
    out.push({ kind: "npc", name: "Ganymède", coords, cell: 312 });
    out.push({ kind: "zaapi", name: "Zaapi — Quartier des marchands", coords, cell: 186 });
  }
  out.push({ kind: "door", name: "Sortie sud", coords, cell: 545 });
  return out;
}

export class MockBackend implements InProcessServer {
  private emitRaw: ((raw: string) => void) | null = null;
  private timers: ReturnType<typeof setInterval>[] = [];
  private t0 = Date.now();
  private status = baseStatus();
  private game = baseGame();
  private overlay: OverlayStatus = {
    dofus_window: { found: false, title: null, pid: null, frame: null },
    slot: null, aligned: false, keep_on_top: true, calibrated: false,
  };
  private lastSlot: SlotReport | null = null;
  private logs: LogEntry[] = [];
  private scene: Scene = "world";
  private sceneStart = Date.now();
  private sceneIdx = 0;
  private routeIdx = 0;
  private dialogIdx = 0;
  private preview = false;
  private fightTick = 0;
  private gameDirty = true;
  private npcs: NpcEntity[] = [];
  private planner: PlannerStatus = { goal: null, skill: null, attention: null, halted: null, outcome: null, fight: null, attack_target: null, armed: false };
  private network: NetworkScanStatus = { state: "idle", result: null, error: null };
  private dofus: DofusStatus = { state: "mock", app: "Dofus", error: null };

  start(emit: (raw: string) => void) {
    this.emitRaw = emit;
    this.t0 = Date.now();
    this.sceneStart = Date.now();
    this.game.pois = poisFor(this.game.map.coords!);
    this.seedHistory();
    // connection burst, as the real server does on connect
    setTimeout(() => {
      this.send("hello", { version: 1, server: "dofus-brain", mock: true });
      this.pushStatus();
      this.pushGame();
      this.send("log.history", { entries: this.logs.slice(-200) });
      this.send("overlay.status", this.overlay);
      this.send("npcs.list", { coords: this.game.map.coords, entities: this.npcs });
      this.send("network.scan.status", this.network);
      this.send("dofus.status", this.dofus);
      this.pushPlanner();
    }, 30);
    this.every(1000, () => this.tickStatus());
    this.every(500, () => this.tickWorld());
    this.every(700, () => this.tickLogs());
    this.every(1000, () => { if (this.preview) this.pushPreview(); });
    setTimeout(() => this.findWindow(), 1800);
  }

  stop() {
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    this.emitRaw = null;
  }

  receive(raw: string) {
    let m: ClientMessage;
    try { m = JSON.parse(raw) as ClientMessage; } catch { return; }
    const delay = randInt(25, 120);
    setTimeout(() => {
      const res = this.handle(m);
      if (m.id) this.send("ack", res.ok ? { ok: true } : { ok: false, error: res.error }, m.id);
    }, delay);
  }

  // ---------------------------------------------------------------- requests

  private handle(m: ClientMessage): { ok: boolean; error?: string } {
    const st = this.status;
    switch (m.type) {
      case "steer.goal": {
        const text = (m.payload.text ?? "").trim();
        if (!text) return { ok: false, error: "goal text is empty" };
        st.goal = text;
        st.task = "planning…";
        this.log("info", "user", `goal set: "${text}"`);
        st.llm.busy = true;
        this.pushStatus();
        setTimeout(() => {
          st.llm.busy = false;
          st.last_decision_ms = randInt(900, 2400);
          st.task = this.taskFor();
          this.log("info", "planner", `goal decomposed in ${st.last_decision_ms} ms → ${st.task}`);
          this.pushStatus();
        }, 1400);
        return { ok: true };
      }
      case "steer.pause":
        if (st.state === "stopped") return { ok: false, error: "agent is stopped (kill switch); resume first" };
        st.state = "paused";
        st.proposed = null;
        this.log("warn", "user", "paused — no more actions will be issued (perception keeps running)");
        this.pushStatus();
        return { ok: true };
      case "steer.resume": {
        const wasKilled = st.state === "stopped";
        st.state = "running";
        st.error = null;
        if (wasKilled) this.log("warn", "control", "kill switch reset (press Start to take the window again)");
        this.log("info", "user", "resumed");
        this.refreshProposal();
        this.pushStatus();
        return { ok: true };
      }
      case "steer.mode": {
        const mode = m.payload.mode;
        if (mode !== "observe" && mode !== "assist" && mode !== "auto") return { ok: false, error: `unknown mode ${String(mode)}` };
        st.mode = mode;
        this.log("info", "user", `mode → ${mode}`);
        this.refreshProposal();
        this.pushStatus();
        return { ok: true };
      }
      case "steer.reset":
        if (st.state !== "stopped") st.state = "idle";
        st.goal = null;
        st.mode = "observe";
        st.proposed = null;
        st.task = null;
        this.planner = { ...this.planner, goal: null, skill: null, attack_target: null, attention: null, outcome: null };
        this.log("info", "user", "reset: idle, observe, no goal");
        this.pushPlanner();
        this.pushStatus();
        return { ok: true };
      case "steer.kill":
        st.state = "stopped";
        st.proposed = null;
        st.task = null;
        st.llm.busy = false;
        this.log("error", "control", "KILL SWITCH tripped by user — all input stopped");
        this.overlay.keep_on_top = false;
        this.planner.armed = false;
        this.pushPlanner();
        this.log("warn", "control", "window left alone (no keep-on-top, no auto-align): kill switch (user)");
        this.send("overlay.status", this.overlay);
        this.pushStatus();
        return { ok: true };
      case "session.start":
        if (st.state === "stopped") return { ok: false, error: "kill switch is on: press Resume first" };
        this.planner.armed = true;
        this.overlay.keep_on_top = true;
        if (this.overlay.slot) { this.overlay.dofus_window.frame = { ...this.overlay.slot }; this.overlay.aligned = true; }
        this.log("info", "user", "START: Dofus aligned and kept in front until the kill switch");
        this.send("overlay.status", this.overlay);
        this.pushPlanner();
        return { ok: true };
      case "npcs.scan": {
        if (!this.planner.armed) return { ok: false, error: "control is not activated" };
        this.npcs = mockNpcs();
        const nm = this.npcs.filter((e) => e.kind === "monster").length;
        this.log("info", "perception", `located ${this.npcs.length - nm} NPC(s) and ${nm} monster group(s)`);
        this.send("npcs.list", { coords: this.game.map.coords, entities: this.npcs });
        return { ok: true };
      }
      case "network.scan.start":
        if (this.network.state === "capturing") return { ok: false, error: "network scan is already running" };
        this.network = { state: "capturing", result: null, error: null };
        this.send("network.scan.status", this.network);
        this.log("info", "user", "network capture started (mock)");
        return { ok: true };
      case "dofus.launch":
        this.dofus = { state: "mock", app: "Dofus", error: null };
        this.send("dofus.status", this.dofus);
        return { ok: true };
      case "dofus.authenticate":
        this.dofus = { state: "authenticated", app: "Dofus", error: null };
        this.send("dofus.status", this.dofus);
        return { ok: true };
      case "network.scan.stop":
        if (this.network.state !== "capturing") return { ok: false, error: "network scan is not running" };
        this.network = { state: "ready", error: null, result: {
          packets: 61, bytes: 3235,
          groups: [
            { entity_id: -20000, cell: 384, members: [{ monster_id: 3279, name: "Nitrô", grade: 3, level: 120, levels: [20, 70, 120, 170], raw_field_4: 3 }] },
            { entity_id: -20004, cell: 411, members: [{ monster_id: 3246, name: null, grade: 3, level: null, levels: [], raw_field_4: 3 }, { monster_id: 3246, name: null, grade: 3, level: null, levels: [], raw_field_4: 3 }, { monster_id: 3246, name: null, grade: 3, level: null, levels: [], raw_field_4: 3 }] },
          ],
          fight_monsters: [{ monster_id: 3279, name: "Nitrô", grade: 3, level: 120 }],
          players: [{ entity_id: 801380434211, name: "Mamoriko", cell: 434 }, { entity_id: 801402192163, name: "Kellouner", cell: 435 }],
        } };
        this.send("network.scan.status", this.network);
        this.log("info", "perception", "network scan: 2 monster group(s), 2 player(s)");
        return { ok: true };
      case "npcs.attack": {
        if (m.payload.cell != null) {
          const target = this.npcs.find((x) => x.kind === "monster" && x.cell === m.payload.cell);
          if (!target) return { ok: false, error: "unknown monster cell" };
          this.log("info", "brain", `semi-auto click cell ${m.payload.cell}: ${target.name}`);
          return { ok: true };
        }
        if (!this.planner.armed) return { ok: false, error: "control is not activated" };
        const e = m.payload.cell != null ? this.npcs.find((x) => x.kind === "monster" && x.cell === m.payload.cell)
          : m.payload.index != null ? this.npcs[m.payload.index] : this.npcs.find((x) => x.name === m.payload.name);
        if (!e) return { ok: false, error: "unknown target: locate NPCs first and pick one from the list" };
        if (e.kind !== "monster") return { ok: false, error: `${e.name} is an NPC, not a monster group` };
        st.goal = `attack ${e.name}`;
        if (m.payload.auto !== false) st.mode = "auto";
        this.planner = { ...this.planner, goal: st.goal, skill: "attack", attack_target: e.name, attention: null, outcome: null };
        this.log("info", "user", `goal: 'attack ${e.name}'`);
        this.log("info", "brain", `click cell ${e.cell ?? "?"}: attack ${e.name}`, { kind: "click", x: e.x, y: e.y + 30, reason: `attack ${e.name}`, ...(e.cell != null ? { cell: e.cell } : {}) });
        this.pushStatus();
        this.pushPlanner();
        setTimeout(() => { if (this.scene !== "fight") { this.sceneIdx = 0; this.nextScene(); } }, 1200);
        return { ok: true };
      }
      case "fight.start":
        if (!this.planner.armed) return { ok: false, error: "control is not activated" };
        st.goal = "fight sequence";
        if (m.payload.auto !== false) st.mode = "auto";
        this.planner = { ...this.planner, goal: st.goal, skill: "fight_sequence", attention: null, outcome: null };
        this.log("info", "user", "goal: 'fight sequence'");
        this.pushStatus();
        this.pushPlanner();
        return { ok: true };
      case "fight.semi_auto":
        this.log("info", "user", "semi-auto fight: challenges skipped, PRÊT pressed");
        return { ok: true };
      case "map.move":
        this.log("info", "user", `map move requested: ${m.payload.direction}`);
        return { ok: true };
      case "steer.step": {
        if (st.mode !== "assist") return { ok: false, error: `step is only valid in assist mode (mode is ${st.mode})` };
        if (st.state !== "running") return { ok: false, error: `agent is ${st.state}` };
        if (!st.proposed) return { ok: false, error: "no proposed action yet" };
        this.executeAction(st.proposed, "step");
        st.proposed = null;
        this.pushStatus();
        setTimeout(() => { this.refreshProposal(); this.pushStatus(); }, 600);
        return { ok: true };
      }
      case "slot.report": {
        const prev = this.lastSlot;
        this.lastSlot = m.payload;
        const moved = !prev || !rectEquals(prev.estimate_screen, m.payload.estimate_screen);
        if (moved) {
          if (prev) this.log("info", "server", `slot moved → ${fmt(m.payload.estimate_screen)}`);
          this.overlay.slot = { ...m.payload.estimate_screen };
          this.overlay.calibrated = false;
          this.overlay.aligned = rectEquals(this.overlay.dofus_window.frame, this.overlay.slot, 1);
          this.send("overlay.status", this.overlay);
        }
        return { ok: true };
      }
      case "overlay.align": {
        if (!this.overlay.dofus_window.found) return { ok: false, error: "Dofus window not found" };
        if (!this.overlay.slot) return { ok: false, error: "no slot reported yet" };
        this.overlay.dofus_window.frame = { ...this.overlay.slot };
        this.overlay.aligned = true;
        this.log("info", "control", `align_window → ${fmt(this.overlay.slot)} ✓`);
        this.send("overlay.status", this.overlay);
        return { ok: true };
      }
      case "overlay.calibrate": {
        if (!this.status.control.permissions.screen_recording) return { ok: false, error: "screen recording permission missing" };
        if (!this.overlay.slot) return { ok: false, error: "no slot reported yet" };
        this.log("info", "control", "calibrating: hiding Dofus, locating corner markers…");
        setTimeout(() => {
          this.overlay.calibrated = true;
          this.log("info", "control", `markers found (4/4) → slot ${fmt(this.overlay.slot!)}`);
          this.send("overlay.status", this.overlay);
        }, 900);
        return { ok: true };
      }
      case "preview.subscribe":
        this.preview = !!m.payload.enabled;
        this.log("debug", "server", `preview ${this.preview ? "on" : "off"}`);
        if (this.preview) this.pushPreview();
        return { ok: true };
      default:
        return { ok: false, error: `unknown request type ${(m as { type: string }).type}` };
    }
  }

  // ---------------------------------------------------------------- simulation

  private findWindow() {
    this.overlay.dofus_window = {
      found: true, title: "Lyrelle - Dofus 2.73.11", pid: 4182,
      frame: { x: 220, y: 140, w: 1280, h: 800 },
    };
    this.overlay.aligned = rectEquals(this.overlay.dofus_window.frame, this.overlay.slot, 1);
    this.log("info", "control", "Dofus window found (pid 4182)");
    this.send("overlay.status", this.overlay);
  }

  private tickStatus() {
    if (this.planner.goal && this.scene !== "fight" && this.planner.fight) {
      // the fight we were following is over
      this.planner = { ...this.planner, goal: null, skill: null, outcome: { status: "done", reason: "fight over" } };
      this.status.goal = null;
    }
    this.pushPlanner();
    this.status.uptime_s = Math.round((Date.now() - this.t0) / 1000);
    this.status.loop_hz = Math.round((4.6 + Math.random() * 0.8) * 10) / 10;
    this.pushStatus();
  }

  private tickWorld() {
    const now = Date.now();
    if (now - this.sceneStart > SCENE_MS) this.nextScene();
    const g = this.game;
    g.confidence = clamp(Math.round((g.confidence + (Math.random() - 0.5) * 0.04) * 100) / 100, 0.82, 0.995);
    if (this.scene === "fight") this.tickFight();
    else if (this.scene === "world") {
      // regenerate slowly and wander between maps
      const c = g.character;
      if (c.hp! < c.hp_max!) { c.hp = Math.min(c.hp_max!, c.hp! + 3); this.gameDirty = true; }
      if (Math.random() < 0.04) {
        this.routeIdx = (this.routeIdx + 1) % WORLD_ROUTE.length;
        const r = WORLD_ROUTE[this.routeIdx];
        g.map = { coords: [...r.coords], area: r.area, sub_area: r.sub };
        g.pois = poisFor(r.coords);
        this.log("info", "perception", `map changed → [${r.coords.join(",")}] ${r.sub}`);
        this.gameDirty = true;
        this.refreshProposal();
      }
      if (Math.random() < 0.05) { c.pods = clamp(c.pods! + randInt(1, 12), 0, c.pods_max!); this.gameDirty = true; }
    }
    if (this.gameDirty || Math.random() < 0.3) this.pushGame();
  }

  private nextScene() {
    const order: Scene[] = ["world", "fight", "world", "dialog"];
    this.sceneIdx = (this.sceneIdx + 1) % order.length;
    this.scene = order[this.sceneIdx];
    this.sceneStart = Date.now();
    const g = this.game;
    g.screen = this.scene;
    g.fight = null;
    g.dialog = null;
    if (this.scene === "fight") {
      g.screen = "loading";
      this.pushGame();
      g.screen = "fight";
      g.fight = this.newFight();
      g.character.pa = 7; g.character.pm = 4;
      this.status.task = "fight: placement";
      this.log("warn", "perception", `fight detected (${g.fight.fighters.filter((f) => f.team === "enemy").map((f) => f.name).join(", ")})`);
    } else if (this.scene === "dialog") {
      const d = DIALOGS[this.dialogIdx++ % DIALOGS.length];
      g.dialog = { npc: d.npc, text: d.text, options: d.options.map((text, i) => ({ index: i + 1, text })) };
      this.status.task = `talk to ${d.npc}`;
      this.log("info", "perception", `dialog with ${d.npc} (${d.options.length} options)`);
    } else {
      g.character.pa = 7; g.character.pm = 4;
      this.status.task = this.taskFor();
      this.log("info", "perception", "back to world view");
    }
    this.gameDirty = true;
    this.refreshProposal();
    this.pushStatus();
  }

  private newFight(): FightState {
    const blocked = new Set<number>();
    while (blocked.size < 34) {
      const c = randInt(30, 530);
      const { col } = cellRowCol(c);
      if (col > 0 && col < 13) blocked.add(c);
    }
    const free = () => { let c: number; do { c = randInt(60, 500); } while (blocked.has(c)); return c; };
    const mons = [...MONSTERS].sort(() => Math.random() - 0.5).slice(0, 3);
    const fighters: Fighter[] = [
      { id: "p1", name: "Lyrelle", team: "ally", is_self: true, cell: free(), hp: this.game.character.hp, hp_max: this.game.character.hp_max, pa: 7, pm: 4 },
      { id: "p2", name: "Sadi-Lou", team: "ally", is_self: false, cell: free(), hp: 386, hp_max: 410, pa: 6, pm: 3 },
      ...mons.map((m, i) => {
        const hp = Math.round(m.hp * (1.6 + Math.random()));
        return { id: `m${i + 1}`, name: m.name, team: "enemy" as const, is_self: false, cell: free(), hp, hp_max: hp, pa: 5, pm: 3 };
      }),
    ];
    this.fightTick = 0;
    return { phase: "placement", turn: 1, timer_s: 15, fighters, blocked_cells: [...blocked], reachable_cells: [] };
  }

  private tickFight() {
    const f = this.game.fight;
    if (!f) return;
    this.fightTick++;
    const me = f.fighters.find((x) => x.is_self)!;
    const occupied = new Set(f.fighters.filter((x) => x.cell != null && (x.hp ?? 1) > 0).map((x) => x.cell!));
    const step = (who: Fighter, towards: number) => {
      if (who.cell == null) return;
      const opts = cellNeighbours(who.cell).filter((n) => !occupied.has(n) && !f.blocked_cells.includes(n));
      if (!opts.length) return;
      opts.sort((a, b) => cellDistance(a, towards) - cellDistance(b, towards));
      occupied.delete(who.cell);
      who.cell = opts[0];
      occupied.add(who.cell);
    };
    const enemies = f.fighters.filter((x) => x.team === "enemy" && (x.hp ?? 0) > 0);
    const elapsed = Date.now() - this.sceneStart;

    if (f.phase === "placement") {
      f.timer_s = Math.max(0, 15 - Math.floor(elapsed / 1000) * 5);
      if (this.fightTick >= 5) {
        f.phase = "my_turn"; f.timer_s = 30;
        this.status.task = "fight: my turn";
        this.log("info", "brain", "fight started — turn 1");
      }
    } else if (f.phase === "my_turn") {
      f.timer_s = Math.max(0, (f.timer_s ?? 30) - 0.5);
      const target = enemies.sort((a, b) => cellDistance(me.cell!, a.cell!) - cellDistance(me.cell!, b.cell!))[0];
      if (this.fightTick % 2 === 0 && target) {
        if ((me.pm ?? 0) > 0 && cellDistance(me.cell!, target.cell!) > 1) {
          step(me, target.cell!);
          me.pm = (me.pm ?? 0) - 1;
          this.actionLog({ kind: "click", ...pt(me.cell!), cell: me.cell!, reason: `approach ${target.name}` });
        } else if ((me.pa ?? 0) >= 3) {
          const spell = pick(SPELLS.slice(0, 3));
          const dmg = randInt(18, 46);
          target.hp = Math.max(0, (target.hp ?? 0) - dmg);
          me.pa = (me.pa ?? 0) - 3;
          this.actionLog({ kind: "click", ...pt(target.cell!), cell: target.cell!, reason: `${spell} → ${target.name}` });
          this.log("info", "perception", `${target.name} −${dmg} PV${target.hp === 0 ? " (mort)" : ""}`);
          if (target.hp === 0) target.cell = null;
        } else {
          this.endMyTurn(f);
        }
      }
      if ((f.timer_s ?? 0) <= 0) this.endMyTurn(f);
    } else if (f.phase === "enemy_turn") {
      f.timer_s = Math.max(0, (f.timer_s ?? 30) - 0.5);
      for (const e of enemies) if (Math.random() < 0.6) step(e, me.cell!);
      const ally = f.fighters.find((x) => x.id === "p2")!;
      if (Math.random() < 0.4 && enemies[0]) step(ally, enemies[0].cell!);
      if (Math.random() < 0.3) {
        const hit = randInt(8, 30);
        me.hp = Math.max(1, (me.hp ?? 0) - hit);
        this.log("warn", "perception", `${pick(enemies)?.name ?? "?"} frappe Lyrelle −${hit} PV`);
      }
      if (this.fightTick % 8 === 0) {
        f.phase = "my_turn"; f.turn++; f.timer_s = 30;
        me.pa = 7; me.pm = 4;
        this.status.task = `fight: my turn (${f.turn})`;
        this.log("info", "brain", `turn ${f.turn}`);
      }
    }
    if (enemies.length === 0 || elapsed > SCENE_MS - 1500) {
      if (f.phase !== "ended") {
        f.phase = "ended"; f.timer_s = null;
        const xp = randInt(800, 2400), k = randInt(40, 260);
        this.game.character.kamas! += k;
        this.log("info", "brain", `fight ended — victoire, +${xp} XP, +${k} kamas`);
        this.status.task = "fight: closing results";
      }
    }
    // reachable cells for display
    f.reachable_cells = f.phase === "my_turn" && me.cell != null ? this.reachable(me.cell, me.pm ?? 0, occupied, f.blocked_cells) : [];
    this.game.character.hp = me.hp;
    this.game.character.pa = me.pa ?? null;
    this.game.character.pm = me.pm ?? null;
    this.gameDirty = true;
    if (this.status.mode === "assist") this.refreshProposal();
  }

  private endMyTurn(f: FightState) {
    f.phase = "enemy_turn"; f.timer_s = 30;
    this.actionLog({ kind: "key", key: "space", reason: "end turn" });
    this.status.task = "fight: waiting (enemy turn)";
  }

  private reachable(from: number, pm: number, occupied: Set<number>, blocked: number[]): number[] {
    const seen = new Set([from]);
    let frontier = [from];
    for (let i = 0; i < pm; i++) {
      const next: number[] = [];
      for (const c of frontier) for (const n of cellNeighbours(c)) {
        if (!seen.has(n) && !occupied.has(n) && !blocked.includes(n)) { seen.add(n); next.push(n); }
      }
      frontier = next;
    }
    seen.delete(from);
    return [...seen].filter((c) => c >= 0 && c < CELL_COUNT);
  }

  private taskFor(): string {
    const r = WORLD_ROUTE[(this.routeIdx + 1) % WORLD_ROUTE.length];
    return pick([`travel to (${r.coords.join(",")})`, "hunt Bouftou group", "harvest Frêne", `travel to (${r.coords.join(",")})`]);
  }

  private proposalFor(): Action | null {
    const g = this.game;
    if (this.scene === "dialog" && g.dialog) {
      return { kind: "click", x: 640, y: 560 + 24, reason: `choose option 1: "${g.dialog.options[0].text}"` };
    }
    if (this.scene === "fight" && g.fight) {
      const me = g.fight.fighters.find((x) => x.is_self);
      const tgt = g.fight.fighters.find((x) => x.team === "enemy" && (x.hp ?? 0) > 0 && x.cell != null);
      if (g.fight.phase !== "my_turn" || !me || !tgt) return { kind: "wait", ms: 500, reason: "not my turn" };
      return { kind: "click", ...pt(tgt.cell!), cell: tgt.cell!, reason: `cast Pression on ${tgt.name}` };
    }
    const cell = randInt(14, 545);
    return pick<Action>([
      { kind: "click", ...pt(cell), cell, reason: "walk towards map edge (east)" },
      { kind: "click", ...pt(cell), cell, reason: "harvest Frêne" },
      { kind: "key", key: "m", reason: "open world map to check route" },
      { kind: "click", ...pt(cell), cell, reason: "attack Bouftou group" },
    ]);
  }

  private refreshProposal() {
    const st = this.status;
    st.proposed = st.mode === "assist" && st.state === "running" ? this.proposalFor() : null;
  }

  private executeAction(a: Action, how: "auto" | "step") {
    this.actionLog(a, how === "step" ? "executed (step)" : "executed");
    this.status.last_decision_ms = randInt(380, 1600);
  }

  private tickLogs() {
    const st = this.status;
    const r = Math.random();
    if (r < 0.25) this.log("debug", "perception", `screen=${this.game.screen} conf=${this.game.confidence.toFixed(2)} frame ${randInt(18, 41)} ms`);
    else if (r < 0.35) this.log("debug", "control", `capture 2560×1600 @2x in ${randInt(9, 22)} ms`);
    else if (r < 0.5 && st.state === "running") {
      st.llm.busy = true;
      setTimeout(() => {
        st.llm.busy = false;
        st.last_decision_ms = randInt(420, 1900);
        this.log("info", "llm", `decision in ${st.last_decision_ms} ms (${randInt(180, 640)} tok): ${this.proposalFor()?.reason ?? "wait"}`);
        this.pushStatus();
      }, randInt(400, 1200));
    } else if (r < 0.62 && st.state === "running") {
      if (st.mode === "auto") this.executeAction(this.proposalFor()!, "auto");
      else if (st.mode === "observe") this.log("info", "planner", `observe: would ${this.proposalFor()?.reason ?? "wait"}`);
    } else if (r < 0.66) this.log("warn", "perception", pick(["low OCR confidence on PA counter (0.61)", "popup detected: \"Niveau supérieur !\" — closing", "HP bar partially occluded"]));
    else if (r < 0.675) this.log("error", "llm", "ollama timeout after 8000 ms — retrying with smaller context");
  }

  // ---------------------------------------------------------------- emit helpers

  private every(ms: number, fn: () => void) { this.timers.push(setInterval(fn, ms)); }

  private send<K extends ServerType>(type: K, payload: ServerPayloads[K], id?: string) {
    if (!this.emitRaw) return;
    this.emitRaw(JSON.stringify({ type, ts: Date.now(), ...(id ? { id } : {}), payload }));
  }

  private pushStatus() { this.send("agent.status", this.status); }
  private pushPlanner() {
    const f = this.game.fight;
    this.planner.fight = f ? { in_turn: f.phase === "my_turn", pa_left: this.game.character.pa, pm_left: this.game.character.pm, plan: this.status.task } : null;
    this.send("planner.status", this.planner);
  }
  private pushGame() { this.game.updated_at = Date.now(); this.gameDirty = false; this.send("game.state", this.game); }

  private pushPreview() {
    const p = renderPreviewJpeg(this.game);
    if (p) this.send("frame.preview", p);
  }

  private log(level: LogLevel, source: LogSource, message: string, action?: Action) {
    const e: LogEntry = { ts: Date.now(), level, source, message, ...(action ? { action } : {}) };
    this.logs.push(e);
    if (this.logs.length > 500) this.logs.splice(0, this.logs.length - 500);
    this.send("log.entry", e);
  }

  private actionLog(a: Action, verb = "executed") {
    const st = this.status;
    if (st.state !== "running" || st.mode === "observe") return;
    this.log("info", "control", `${verb}: ${a.reason ?? a.kind}`, a);
  }

  private seedHistory() {
    const now = Date.now();
    const seed: [LogLevel, LogSource, string][] = [
      ["info", "server", "dofus-brain listening on ws://127.0.0.1:8765/ws (mock)"],
      ["info", "control", "permissions: accessibility ✓ screen recording ✓"],
      ["info", "llm", "backend mock ready (qwen2.5-vl:7b)"],
      ["info", "perception", "screen=world conf=0.96 map [4,-18] Cité d'Astrub"],
      ["info", "planner", "goal: Monter niveau 45 en chassant des Bouftous autour d'Astrub"],
      ["info", "planner", "task → travel to (5,-17)"],
    ];
    seed.forEach(([l, s, m], i) => this.logs.push({ ts: now - (seed.length - i) * 900, level: l, source: s, message: m }));
  }
}

function pt(cell: number) {
  const c = cellCenter(cell, GRID);
  return { x: Math.round(c.x), y: Math.round(c.y) };
}
function fmt(r: { x: number; y: number; w: number; h: number }) { return `${r.x},${r.y} ${r.w}×${r.h}`; }
