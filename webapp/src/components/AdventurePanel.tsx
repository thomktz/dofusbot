import { useMemo, useState, type ReactNode } from "react";
import type { NetworkDialog, NetworkInventory, NetworkQuest, NetworkScanResult, NetworkShortcut, NetworkSpell } from "../protocol";
import { Panel } from "./ui";

type Tab = "inventory" | "spells" | "quests" | "dialogue";

function ItemIcon({ id, alt }: { id?: number | null; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (id == null || failed) return <span className="adventure-icon empty" aria-hidden="true" />;
  return <img className="adventure-icon" src={`/assets/items/${id}.png`} alt="" aria-label={alt} onError={() => setFailed(true)} />;
}

function SpellIcon({ id, alt }: { id?: number | null; alt: string }) {
  const [failed, setFailed] = useState(false);
  if (id == null || failed) return <span className="adventure-icon empty" aria-hidden="true" />;
  return <img className="adventure-icon" src={`/assets/spells/${id}.png`} alt="" aria-label={alt} onError={() => setFailed(true)} />;
}

function Inventory({ inventory, onUse }: { inventory: NetworkInventory | undefined; onUse: (uid: number) => void }) {
  const [filter, setFilter] = useState("");
  const all = inventory?.objects ?? [];
  const query = filter.trim().toLocaleLowerCase();
  const visible = useMemo(() => all.filter(item => !query || (item.name ?? "").toLocaleLowerCase().includes(query)), [all, query]);
  const equipped = visible.filter(item => item.container === "equipped");
  const bag = visible.filter(item => item.container === "bag");
  const other = visible.filter(item => item.container !== "equipped" && item.container !== "bag");
  return <div className="adventure-content">
    <div className="inventory-summary">
      <strong>{inventory?.kamas?.toLocaleString?.() ?? "—"} <small>kamas</small></strong>
      <span>{inventory?.weight ?? "—"} / {inventory?.weight_max ?? "—"} pods</span>
      <span>{all.length} items</span>
    </div>
    <input className="adventure-search" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Filter inventory" aria-label="Filter inventory" />
    {equipped.length > 0 && <Section title="Equipped" count={equipped.length}><div className="equipment-grid">{equipped.map(item => <Item key={item.uid ?? `${item.gid}-${item.position}`} item={item} onUse={onUse} />)}</div></Section>}
    <Section title="Bag" count={bag.length}>{bag.length ? bag.map(item => <Item key={item.uid ?? `${item.gid}-${item.position}`} item={item} onUse={onUse} />) : <Empty text="No bag items in this snapshot." />}</Section>
    {other.length > 0 && <Section title="Other positions" count={other.length}>{other.map(item => <Item key={item.uid ?? `${item.gid}-${item.position}`} item={item} onUse={onUse} />)}</Section>}
  </div>;
}

function Item({ item, onUse }: { item: NonNullable<NetworkInventory>["objects"][number]; onUse: (uid: number) => void }) {
  const name = item.name ?? `Item ${item.gid ?? "?"}`;
  const uid = item.uid;
  return <div className="adventure-row" title={`${name}${item.position_name ? ` · ${item.position_name}` : ""}`}>
    <ItemIcon id={item.icon_id} alt={name} /><span className="adventure-main"><strong>{name}</strong><small>{item.position_name ?? "Position unknown"}{item.level != null ? ` · lvl ${item.level}` : ""}</small></span><b className="adventure-quantity">×{item.quantity ?? "?"}</b>{uid != null && <button className="btn tiny" onClick={() => onUse(uid)}>Use</button>}
  </div>;
}

function Spells({ spells, shortcuts }: { spells: NetworkSpell[]; shortcuts: NetworkShortcut[] }) {
  const spellById = new Map(spells.map(spell => [spell.spell_id, spell]));
  const ordered = shortcuts.filter(item => item.kind === "spell").map(item => ({ spell: item.spell_id == null ? undefined : spellById.get(item.spell_id), slot: item.slot, fallback: item }));
  const shown = ordered.length ? ordered : spells.map((spell, slot) => ({ spell, slot, fallback: undefined }));
  return <div className="adventure-content"><div className="spell-grid">{shown.length ? shown.map(({ spell, slot, fallback }) => {
    const name = spell?.name ?? fallback?.name ?? `Spell ${spell?.spell_id ?? fallback?.spell_id ?? "?"}`;
    return <div className="spell-card" key={`${spell?.spell_id ?? fallback?.spell_id}-${slot}`} title={spell?.description ?? name}><span className="spell-slot">{slot + 1}</span><SpellIcon id={spell?.icon_id ?? fallback?.icon_id} alt={name} /><span><strong>{name}</strong><small>{spell?.ap_cost != null ? `${spell.ap_cost} PA` : "PA —"}{spell?.range != null ? ` · range ${spell.min_range ?? 0}–${spell.range}` : ""}{spell?.max_cast_turn ? ` · ×${spell.max_cast_turn}` : ""}</small></span></div>;
  }) : <Empty text="Spells have not arrived in a live character packet yet." />}</div></div>;
}

type QuestSearchResult = { quest_id: number; name: string | null; level_min: number | null; level_max: number | null; step_count: number };
type QuestRecord = { quest_id: number; found: boolean; name?: string | null; level_min?: number | null; level_max?: number | null; steps?: { step_id?: number | null; step_name?: string | null; step_description?: string | null; objectives?: { objective_id: number; description?: string | null; type_name?: string | null; coords?: { x: number; y: number } | null }[] }[] };

function QuestDatabase() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<QuestSearchResult[]>([]);
  const [record, setRecord] = useState<QuestRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const search = async () => {
    setError(null); setRecord(null);
    try {
      const response = await fetch(`/api/quests?query=${encodeURIComponent(query)}&limit=12`);
      if (!response.ok) throw new Error("Quest database unavailable");
      const data = await response.json() as { results?: QuestSearchResult[] };
      setResults(data.results ?? []);
    } catch (cause) { setResults([]); setError(cause instanceof Error ? cause.message : "Quest database unavailable"); }
  };
  const select = async (questId: number) => {
    setError(null);
    try {
      const response = await fetch(`/api/quests/${questId}`);
      if (!response.ok) throw new Error("Quest lookup failed");
      setRecord(await response.json() as QuestRecord);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Quest lookup failed"); }
  };
  return <details className="quest-database"><summary>Quest database <span>local game data</span></summary><div className="quest-search"><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === "Enter") void search(); }} placeholder="Search a quest" aria-label="Search local quest database" /><button className="btn small" onClick={() => void search()}>Search</button></div>{error && <div className="bad small">{error}</div>}{results.map(result => <button className="quest-search-result" key={result.quest_id} onClick={() => void select(result.quest_id)}><strong>{result.name ?? `Quest ${result.quest_id}`}</strong><span>lvl {result.level_min ?? "?"} · {result.step_count} step{result.step_count === 1 ? "" : "s"}</span></button>)}{record?.found && <div className="quest-record"><strong>{record.name}</strong><span>Level {record.level_min ?? "?"}{record.level_max != null && record.level_max !== record.level_min ? `–${record.level_max}` : ""}</span>{record.steps?.map((step, index) => <div key={step.step_id ?? index}><b>{step.step_name ?? `Step ${index + 1}`}</b>{step.step_description && <small>{step.step_description}</small>}{step.objectives?.filter(objective => !!objective.description && !/^\s*#\d+\s*$/.test(objective.description)).map(objective => <small className="quest-record-objective" key={objective.objective_id}>○ {objective.description}{objective.coords ? ` · (${objective.coords.x}, ${objective.coords.y})` : ""}</small>)}</div>)}</div>}</details>;
}

function Quests({ quests, onNext }: { quests: { active: NetworkQuest[]; completed: { quest_id: number }[] } | undefined; onNext: () => void }) {
  const active = quests?.active ?? [];
  return <div className="adventure-content"><Section title="Active quests" count={active.length}>{active.length ? <><button className="btn small" onClick={onNext}>Next verified quest step</button>{active.map(quest => <div className="quest-row" key={quest.quest_id}><strong>{quest.name ?? `Quest ${quest.quest_id}`}</strong>{quest.step_name && <span>{quest.step_name}</span>}{quest.step_description && <small>{quest.step_description}</small>}{quest.objectives?.map(objective => <div className={`quest-objective ${objective.reached ? "done" : ""}`} key={objective.objective_id}><i>{objective.reached ? "✓" : "○"}</i><span>{objective.description ?? objective.type_name ?? `Objective ${objective.objective_id}`}{objective.completion?.max ? ` · ${objective.completion.current}/${objective.completion.max}` : ""}{objective.coords ? ` · (${objective.coords.x}, ${objective.coords.y})` : ""}</span></div>)}</div>)}</> : <Empty text="No active quests have arrived in the current capture." />}</Section>{(quests?.completed.length ?? 0) > 0 && <div className="adventure-muted">{quests!.completed.length} completed quest IDs retained from the character state.</div>}<QuestDatabase /></div>;
}

function Dialogue({ dialog, onReply, onLeave }: { dialog: NetworkDialog | null | undefined; onReply: (replyId: number) => void; onLeave: () => void }) {
  if (!dialog) return <div className="adventure-content"><Empty text="Open an NPC dialogue and every received stage will appear here." /></div>;
  const stages = dialog.stages?.length ? dialog.stages : [{ message_id: dialog.message_id, text: dialog.text, options: dialog.options }];
  return <div className="adventure-content dialogue-history"><div className="dialogue-npc">{dialog.npc_name ?? "NPC"}<span>{stages.length} stage{stages.length === 1 ? "" : "s"}</span></div>{stages.map((stage, index) => <div className="dialogue-stage" key={`${stage.message_id ?? "?"}-${index}`}><small>Stage {index + 1}</small>{stage.text && <p>{stage.text}</p>}{stage.options?.map(option => index === stages.length - 1 ? <button className="dialogue-option action" key={option.reply_id} onClick={() => onReply(option.reply_id)}>{option.text ?? `Reply ${option.reply_id}`}</button> : <div className="dialogue-option" key={option.reply_id}>{option.text ?? `Reply ${option.reply_id}`}</div>)}</div>)}<button className="btn small" onClick={onLeave}>Leave dialogue</button></div>;
}

function Section({ title, count, children }: { title: string; count: number; children: ReactNode }) { return <section className="adventure-section"><div className="adventure-section-title"><span>{title}</span><b>{count}</b></div>{children}</section>; }
function Empty({ text }: { text: string }) { return <div className="adventure-empty">{text}</div>; }

/**
 * The character windows are deliberately separate from the world feed.  They
 * behave like a game action bar: one window is open at a time and the bar is
 * always available at the bottom of the dashboard.
 */
export function AdventurePanel({ result, onReply, onLeave, onUseItem, onQuestNext }: { result: NetworkScanResult | null; onReply: (replyId: number) => void; onLeave: () => void; onUseItem: (uid: number) => void; onQuestNext: () => void }) {
  const [tab, setTab] = useState<Tab | null>(null);
  const dialogCount = result?.dialog?.stages?.length ?? (result?.dialog ? 1 : 0);
  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "inventory", label: "Inventory", count: result?.inventory?.objects.length },
    { id: "spells", label: "Spells", count: result?.spells?.length },
    { id: "quests", label: "Quests", count: result?.quests?.active.length },
    { id: "dialogue", label: "Dialogue", count: dialogCount || undefined },
  ];
  const title = tabs.find(item => item.id === tab)?.label;
  return <>
    {tab && <Panel title={title ?? "Character"} testId="adventure-panel" right={<button className="adventure-close" aria-label={`Close ${title}`} onClick={() => setTab(null)}>×</button>}>
      {tab === "inventory" && <Inventory inventory={result?.inventory} onUse={onUseItem} />}
      {tab === "spells" && <Spells spells={result?.spells ?? []} shortcuts={result?.shortcuts ?? []} />}
      {tab === "quests" && <Quests quests={result?.quests} onNext={onQuestNext} />}
      {tab === "dialogue" && <Dialogue dialog={result?.dialog} onReply={onReply} onLeave={onLeave} />}
    </Panel>}
    <nav className="adventure-taskbar" aria-label="Character windows">
      {tabs.map(item => <button key={item.id} className={tab === item.id ? "active" : ""} aria-pressed={tab === item.id} onClick={() => setTab(current => current === item.id ? null : item.id)}>
        <span className={`taskbar-glyph ${item.id}`} aria-hidden="true">{item.id === "inventory" ? "▣" : item.id === "spells" ? "✦" : item.id === "quests" ? "☷" : "◌"}</span>
        <span>{item.label}</span>{item.count != null && <b>{item.count}</b>}
      </button>)}
    </nav>
  </>;
}
