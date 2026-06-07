/**
 * Settings page — tabbed overlay for the user-facing configuration
 * of the agent:
 *
 *   - Models:        list / select via `model.*` IPC
 *   - Providers:     manage LLM providers via `provider.*` IPC
 *   - Permissions:   list / upsert / remove via `permission.*` IPC
 *   - Scheduled:     list / enable / disable / delete via `schedule.*` IPC
 *   - API Key:       legacy MiniMax key management via `secrets.*` IPC
 *   - Agents:        sub-agent CRUD via `agent.*` IPC
 *
 * The component is mounted by `App.tsx` when the sidebar nav is
 * "settings" and is otherwise hidden.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  Eye,
  EyeOff,
  Globe,
  KeyRound,
  Pencil,
  Plus,
  Play,
  Save,
  ScrollText,
  ShieldAlert,
  Trash2,
  Webhook,
} from "lucide-react";
import {
  useAgentStore,
  useAuditStore,
  useModelStore,
  usePermissionStore,
  useProviderStore,
  useWebhookStore,
  useScheduleStore,
  useSecretStore,
  useTaskStore,
} from "../stores";
import { toast } from "./ErrorBoundary";
import type { AuditEntry, PermissionRule, ProviderInfo, ProviderModel, ScheduledJob, WebhookConfig } from "../types/ipc";

type Tab = "models" | "providers" | "permissions" | "scheduled" | "api-key" | "agents" | "audit" | "webhooks";

export interface SettingsPageProps {
  testId?: string;
}

export function SettingsPage({ testId = "settings-page" }: SettingsPageProps): JSX.Element {
  const [tab, setTab] = useState<Tab>("models");
  return (
    <div
      data-testid={testId}
      className="flex h-full w-full flex-col overflow-hidden bg-minimax-bg text-minimax-fg"
    >
      <header className="flex flex-col gap-3 border-b border-minimax-border px-6 py-4">
        <div>
          <h1 data-testid="settings-title" className="text-base font-semibold">
            Settings
          </h1>
          <p className="text-[11px] text-minimax-muted">
            Configure models, providers, permissions, scheduled jobs, and API keys.
          </p>
        </div>
        <nav className="flex flex-wrap gap-1 rounded-md border border-minimax-border bg-minimax-panel p-1">
          <TabButton id="models" current={tab} onClick={setTab} icon={<Cpu size={12} />} label="Models" testId="settings-tab-models" />
          <TabButton id="providers" current={tab} onClick={setTab} icon={<Globe size={12} />} label="Providers" testId="settings-tab-providers" />
          <TabButton id="permissions" current={tab} onClick={setTab} icon={<ShieldAlert size={12} />} label="Permissions" testId="settings-tab-permissions" />
          <TabButton id="scheduled" current={tab} onClick={setTab} icon={<CalendarClock size={12} />} label="Scheduled" testId="settings-tab-scheduled" />
          <TabButton id="api-key" current={tab} onClick={setTab} icon={<KeyRound size={12} />} label="API Key" testId="settings-tab-api-key" />
          <TabButton id="agents" current={tab} onClick={setTab} icon={<Bot size={12} />} label="Agents" testId="settings-tab-agents" />
          <TabButton id="audit" current={tab} onClick={setTab} icon={<ScrollText size={12} />} label="Audit" testId="settings-tab-audit" />
          <TabButton id="webhooks" current={tab} onClick={setTab} icon={<Webhook size={12} />} label="Webhooks" testId="settings-tab-webhooks" />
        </nav>
      </header>
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {tab === "models" && <ModelsTab />}
        {tab === "providers" && <ProvidersTab />}
        {tab === "permissions" && <PermissionsTab />}
        {tab === "scheduled" && <ScheduledTab />}
        {tab === "api-key" && <ApiKeyTab />}
        {tab === "agents" && <AgentsTab />}
        {tab === "audit" && <AuditTab />}
        {tab === "webhooks" && <WebhooksTab />}
      </div>
    </div>
  );
}

/* ─────────────────────── Tab chrome ─────────────────────── */

function TabButton({
  id, current, onClick, icon, label, testId,
}: {
  id: Tab; current: Tab; onClick: (t: Tab) => void;
  icon: JSX.Element; label: string; testId: string;
}): JSX.Element {
  const active = id === current;
  return (
    <button
      type="button" role="tab" aria-selected={active}
      data-testid={testId}
      onClick={() => onClick(id)}
      className={
        "flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors " +
        (active ? "bg-minimax-accent/20 text-minimax-accent" : "text-minimax-muted hover:text-minimax-fg")
      }
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

/* ─────────────────────── Models tab ─────────────────────── */

function ModelsTab(): JSX.Element {
  const models = useModelStore((s) => s.models);
  const current = useModelStore((s) => s.current);
  const refresh = useModelStore((s) => s.refresh);
  const setCurrent = useModelStore((s) => s.setCurrent);
  const loading = useModelStore((s) => s.loading);

  useEffect(() => {
    if (models.length === 0) void refresh();
  }, [models.length, refresh]);

  return (
    <section data-testid="settings-models" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Available models</h2>
        <button
          type="button" data-testid="settings-models-refresh"
          onClick={() => void refresh()}
          className="rounded border border-minimax-border bg-minimax-panel px-2 py-0.5 text-[11px] text-minimax-muted hover:text-minimax-fg"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      <ul className="space-y-1.5" data-testid="settings-models-list">
        {models.length === 0 && !loading && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">
            No models available — add a provider first.
          </li>
        )}
        {models.map((m) => {
          const isCurrent = m.id === current;
          return (
            <li key={m.id} data-testid={`settings-model-${m.id}`}
              className={
                "flex items-center justify-between rounded-md border px-3 py-2 text-sm " +
                (isCurrent ? "border-minimax-accent/40 bg-minimax-accent/5" : "border-minimax-border bg-minimax-panel/40")
              }
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-minimax-fg">{m.name}</span>
                  {isCurrent && (
                    <span data-testid={`settings-model-current-${m.id}`}
                      className="rounded bg-minimax-accent/20 px-1.5 py-0.5 text-[10px] text-minimax-accent">current</span>
                  )}
                  {m.protocol && (
                    <span className="rounded bg-minimax-border px-1 py-0.5 text-[9px] font-mono text-minimax-muted">{m.protocol}</span>
                  )}
                </div>
                <div className="mt-0.5 text-[11px] text-minimax-muted">
                  {m.provider} · {(m.context_window / 1000).toFixed(0)}k ctx{m.supports_tools ? " · tools" : ""}
                </div>
              </div>
              <button type="button" data-testid={`settings-model-select-${m.id}`}
                onClick={() => { if (!isCurrent) void setCurrent(m.id); }}
                disabled={isCurrent}
                className={
                  "ml-3 inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs " +
                  (isCurrent ? "cursor-not-allowed border-minimax-border text-minimax-muted" : "border-minimax-accent/40 text-minimax-accent hover:bg-minimax-accent/10")
                }
              >
                {isCurrent ? <Check size={12} /> : null}
                {isCurrent ? "Selected" : "Use"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ─────────────────────── Providers tab ─────────────────────── */

/** Preset templates for common LLM providers. */
const PROVIDER_PRESETS: Array<{
  label: string;
  name: string;
  protocol: "anthropic" | "openai";
  base_url: string;
}> = [
  { label: "OpenAI", name: "OpenAI", protocol: "openai", base_url: "https://api.openai.com/v1" },
  { label: "智谱 GLM", name: "智谱 GLM", protocol: "openai", base_url: "https://open.bigmodel.cn/api/paas/v4" },
  { label: "DeepSeek", name: "DeepSeek", protocol: "openai", base_url: "https://api.deepseek.com/v1" },
  { label: "Moonshot", name: "Moonshot", protocol: "openai", base_url: "https://api.moonshot.cn/v1" },
  { label: "本地 Ollama", name: "Ollama", protocol: "openai", base_url: "http://localhost:11434/v1" },
];

function ProvidersTab(): JSX.Element {
  const providers = useProviderStore((s) => s.providers);
  const loading = useProviderStore((s) => s.loading);
  const refresh = useProviderStore((s) => s.refresh);
  const create = useProviderStore((s) => s.create);
  const update = useProviderStore((s) => s.update);
  const remove = useProviderStore((s) => s.remove);
  const setApiKey = useProviderStore((s) => s.setApiKey);
  const clearApiKey = useProviderStore((s) => s.clearApiKey);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Form fields
  const [formName, setFormName] = useState("");
  const [formProtocol, setFormProtocol] = useState<"anthropic" | "openai">("openai");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formApiKey, setFormApiKey] = useState("");
  const [formModelId, setFormModelId] = useState("");
  const [formModelName, setFormModelName] = useState("");
  const [formModelCtx, setFormModelCtx] = useState("128000");
  const [formModels, setFormModels] = useState<ProviderModel[]>([]);
  const [revealApiKey, setRevealApiKey] = useState(false);

  useEffect(() => {
    if (providers.length === 0) void refresh();
  }, [providers.length, refresh]);

  const resetForm = () => {
    setFormName(""); setFormProtocol("openai"); setFormBaseUrl(""); setFormApiKey("");
    setFormModelId(""); setFormModelName(""); setFormModelCtx("128000"); setFormModels([]);
    setShowForm(false); setEditingId(null); setRevealApiKey(false);
  };

  const applyPreset = (preset: typeof PROVIDER_PRESETS[number]) => {
    setFormName(preset.name);
    setFormProtocol(preset.protocol);
    setFormBaseUrl(preset.base_url);
  };

  const startEdit = (p: ProviderInfo) => {
    setEditingId(p.id);
    setFormName(p.name);
    setFormProtocol(p.protocol);
    setFormBaseUrl(p.base_url);
    setFormModels(p.models ?? []);
    setFormApiKey("");
    setShowForm(true);
  };

  const addModelToList = () => {
    if (!formModelId.trim()) return;
    setFormModels([...formModels, {
      id: formModelId.trim(),
      name: formModelName.trim() || formModelId.trim(),
      context_window: parseInt(formModelCtx) || 128000,
      supports_tools: true,
    }]);
    setFormModelId(""); setFormModelName(""); setFormModelCtx("128000");
  };

  const removeModelFromList = (idx: number) => {
    setFormModels(formModels.filter((_, i) => i !== idx));
  };

  const handleSubmit = async () => {
    if (!formName.trim() || !formBaseUrl.trim()) return;
    if (editingId) {
      const opts: Record<string, unknown> = {
        provider_id: editingId,
        name: formName.trim(),
        protocol: formProtocol,
        base_url: formBaseUrl.trim(),
        models: formModels,
      };
      if (formApiKey.trim()) opts.api_key = formApiKey.trim();
      await update(opts as Parameters<typeof update>[0]);
      toast.success("Provider updated", formName.trim());
    } else {
      const result = await create({
        name: formName.trim(),
        protocol: formProtocol,
        base_url: formBaseUrl.trim(),
        models: formModels.length > 0 ? formModels : undefined,
        api_key: formApiKey.trim() || undefined,
      });
      if (result) toast.success("Provider created", result.name);
    }
    resetForm();
  };

  const handleDelete = async (p: ProviderInfo) => {
    if (p.id === "builtin-minimax") {
      toast.error("Cannot delete", "The built-in MiniMax provider cannot be removed.");
      return;
    }
    await remove(p.id);
    toast.success("Provider deleted", p.name);
  };

  const handleSetKey = async (providerId: string, key: string) => {
    const ok = await setApiKey(providerId, key);
    if (ok) toast.success("API key saved");
  };

  const handleClearKey = async (providerId: string) => {
    const ok = await clearApiKey(providerId);
    if (ok) toast.success("API key cleared");
  };

  return (
    <section data-testid="settings-providers" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium">LLM Providers</h2>
          <p className="mt-0.5 text-[11px] text-minimax-muted">
            Manage LLM providers and API keys. Models from enabled providers appear in the model selector.
          </p>
        </div>
        <button type="button" data-testid="settings-provider-add"
          onClick={() => { resetForm(); setShowForm(true); }}
          className="inline-flex items-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20"
        >
          <Plus size={12} /> Add Provider
        </button>
      </div>

      {/* Provider form */}
      {showForm && (
        <div data-testid="settings-provider-form"
          className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium">{editingId ? "Edit Provider" : "New Provider"}</h3>
            <button type="button" onClick={resetForm}
              className="text-[11px] text-minimax-muted hover:text-minimax-fg">Cancel</button>
          </div>

          {/* Preset buttons */}
          {!editingId && (
            <div className="space-y-1.5">
              <span className="text-[10px] text-minimax-muted">Quick presets:</span>
              <div className="flex flex-wrap gap-1.5">
                {PROVIDER_PRESETS.map((p) => (
                  <button key={p.label} type="button"
                    onClick={() => applyPreset(p)}
                    className="rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-[10px] text-minimax-fg hover:border-minimax-accent/40"
                  >{p.label}</button>
                ))}
              </div>
            </div>
          )}

          {/* Main fields */}
          <div className="grid grid-cols-12 gap-2">
            <div className="col-span-4">
              <label className="text-[10px] text-minimax-muted">Name</label>
              <input value={formName} onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. DeepSeek"
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg" />
            </div>
            <div className="col-span-3">
              <label className="text-[10px] text-minimax-muted">Protocol</label>
              <select value={formProtocol} onChange={(e) => setFormProtocol(e.target.value as "anthropic" | "openai")}
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg">
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
            <div className="col-span-5">
              <label className="text-[10px] text-minimax-muted">Base URL</label>
              <input value={formBaseUrl} onChange={(e) => setFormBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs font-mono text-minimax-fg" />
            </div>
          </div>

          {/* API key */}
          <div>
            <label className="text-[10px] text-minimax-muted">API Key {editingId ? "(leave empty to keep current)" : ""}</label>
            <div className="mt-0.5 flex gap-2">
              <div className="relative flex-1">
                <input
                  type={revealApiKey ? "text" : "password"}
                  value={formApiKey} onChange={(e) => setFormApiKey(e.target.value)}
                  placeholder={editingId ? "••••••••" : "sk-..."}
                  autoComplete="off" spellCheck={false}
                  className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 pr-8 font-mono text-xs text-minimax-fg"
                />
                <button type="button" onClick={() => setRevealApiKey((v) => !v)}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-minimax-muted hover:text-minimax-fg">
                  {revealApiKey ? <EyeOff size={12} /> : <Eye size={12} />}
                </button>
              </div>
            </div>
          </div>

          {/* Models list */}
          <div>
            <label className="text-[10px] text-minimax-muted">Models</label>
            {formModels.length > 0 && (
              <ul className="mt-1 space-y-1">
                {formModels.map((m, i) => (
                  <li key={m.id} className="flex items-center gap-2 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs">
                    <span className="flex-1 text-minimax-fg">{m.name || m.id}</span>
                    <span className="text-[10px] text-minimax-muted">{(m.context_window / 1000).toFixed(0)}k</span>
                    <button type="button" onClick={() => removeModelFromList(i)}
                      className="text-minimax-muted hover:text-red-300"><Trash2 size={10} /></button>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-1.5 grid grid-cols-12 gap-1.5">
              <input value={formModelId} onChange={(e) => setFormModelId(e.target.value)}
                placeholder="model id" className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-[11px] text-minimax-fg" />
              <input value={formModelName} onChange={(e) => setFormModelName(e.target.value)}
                placeholder="display name" className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-[11px] text-minimax-fg" />
              <input value={formModelCtx} onChange={(e) => setFormModelCtx(e.target.value)}
                placeholder="ctx window" className="col-span-2 rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-[11px] text-minimax-fg" />
              <button type="button" onClick={addModelToList} disabled={!formModelId.trim()}
                className="col-span-4 inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-0.5 text-[11px] text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-50">
                <Plus size={10} /> Add Model
              </button>
            </div>
          </div>

          {/* Submit */}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={resetForm}
              className="rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-minimax-fg">Cancel</button>
            <button type="button" data-testid="settings-provider-form-submit"
              onClick={() => void handleSubmit()}
              disabled={!formName.trim() || !formBaseUrl.trim()}
              className="rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-50 disabled:cursor-not-allowed">
              <Save size={12} className="mr-1 inline" />
              {editingId ? "Update" : "Create"}
            </button>
          </div>
        </div>
      )}

      {/* Provider list */}
      {loading && providers.length === 0 ? (
        <div className="py-4 text-center text-xs text-minimax-muted">Loading providers…</div>
      ) : providers.length === 0 ? (
        <div className="py-4 text-center text-xs italic text-minimax-muted">
          No providers configured. Click "Add Provider" to get started.
        </div>
      ) : (
        <ul className="space-y-2" data-testid="settings-providers-list">
          {providers.map((p) => (
            <ProviderCard key={p.id} provider={p}
              onEdit={() => startEdit(p)}
              onDelete={() => void handleDelete(p)}
              onSetKey={(key) => void handleSetKey(p.id, key)}
              onClearKey={() => void handleClearKey(p.id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function ProviderCard({ provider, onEdit, onDelete, onSetKey, onClearKey }: {
  provider: ProviderInfo;
  onEdit: () => void;
  onDelete: () => void;
  onSetKey: (key: string) => void;
  onClearKey: () => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [keyDraft, setKeyDraft] = useState("");
  const [reveal, setReveal] = useState(false);
  const isBuiltin = provider.id === "builtin-minimax";

  const protocolColor = provider.protocol === "anthropic"
    ? "bg-orange-500/10 text-orange-300"
    : "bg-emerald-500/10 text-emerald-300";

  return (
    <li data-testid={`settings-provider-${provider.id}`}
      className="rounded-md border border-minimax-border bg-minimax-panel/40">
      {/* Header row */}
      <div className="flex items-center gap-2 px-3 py-2.5">
        <Globe size={14} className="shrink-0 text-minimax-accent" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-xs font-medium text-minimax-fg">{provider.name}</span>
            <span className={`rounded px-1 py-0.5 text-[9px] font-mono ${protocolColor}`}>
              {provider.protocol}
            </span>
            {!provider.enabled && (
              <span className="rounded bg-minimax-border px-1 py-0.5 text-[9px] text-minimax-muted">disabled</span>
            )}
            {provider.api_key_configured ? (
              <span className="rounded bg-emerald-500/10 px-1 py-0.5 text-[9px] text-emerald-300">key ✓</span>
            ) : (
              <span className="rounded bg-red-500/10 px-1 py-0.5 text-[9px] text-red-300">no key</span>
            )}
          </div>
          <span className="block truncate text-[10px] font-mono text-minimax-muted">{provider.base_url}</span>
        </div>
        <button type="button" onClick={() => setExpanded((v) => !v)}
          className="shrink-0 rounded p-1 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg">
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        <button type="button" onClick={onEdit} aria-label="Edit provider"
          className="shrink-0 rounded border border-minimax-border p-1 text-minimax-muted hover:text-minimax-accent">
          <Pencil size={12} />
        </button>
        {!isBuiltin && (
          <button type="button" onClick={onDelete} aria-label="Delete provider"
            className="shrink-0 rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300">
            <Trash2 size={12} />
          </button>
        )}
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-minimax-border/60 px-3 py-2.5 space-y-3">
          {/* Models */}
          {provider.models.length > 0 && (
            <div>
              <h4 className="text-[10px] font-medium text-minimax-muted mb-1">Models ({provider.models.length})</h4>
              <div className="flex flex-wrap gap-1">
                {provider.models.map((m) => (
                  <span key={m.id}
                    className="rounded bg-minimax-bg border border-minimax-border px-1.5 py-0.5 text-[10px] text-minimax-fg">
                    {m.name || m.id}
                    <span className="ml-1 text-minimax-muted">{(m.context_window / 1000).toFixed(0)}k</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* API key management */}
          <div>
            <h4 className="text-[10px] font-medium text-minimap-muted mb-1">API Key</h4>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={reveal ? "text" : "password"}
                  value={keyDraft} onChange={(e) => setKeyDraft(e.target.value)}
                  placeholder={provider.api_key_configured ? "Replace key…" : "Enter API key…"}
                  autoComplete="off" spellCheck={false}
                  className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 pr-8 font-mono text-[11px] text-minimax-fg"
                />
                <button type="button" onClick={() => setReveal((v) => !v)}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-minimax-muted hover:text-minimax-fg">
                  {reveal ? <EyeOff size={10} /> : <Eye size={10} />}
                </button>
              </div>
              <button type="button"
                disabled={!keyDraft.trim()}
                onClick={() => { void onSetKey(keyDraft.trim()); setKeyDraft(""); }}
                className="inline-flex items-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-[11px] text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-50">
                <Save size={10} /> Save
              </button>
              {provider.api_key_configured && (
                <button type="button" onClick={() => void onClearKey()}
                  className="inline-flex items-center gap-1 rounded border border-minimax-border px-2 py-1 text-[11px] text-minimax-muted hover:text-red-300">
                  <Trash2 size={10} /> Clear
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

/* ─────────────────────── Permissions tab ─────────────────────── */

function PermissionsTab(): JSX.Element {
  const rules = usePermissionStore((s) => s.rules);
  const refresh = usePermissionStore((s) => s.refresh);
  const upsertRule = usePermissionStore((s) => s.upsertRule);
  const removeRule = usePermissionStore((s) => s.removeRule);
  const [draftTool, setDraftTool] = useState("*");
  const [draftPattern, setDraftPattern] = useState("");
  const [draftDecision, setDraftDecision] = useState<"allow" | "deny" | "ask">("allow");

  useEffect(() => {
    if (rules.length === 0) void refresh();
  }, [rules.length, refresh]);

  return (
    <section data-testid="settings-permissions" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">Permission rules</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Patterns are matched against tool call arguments. A rule with
          decision <code>allow</code> skips the confirmation modal;{" "}
          <code>deny</code> blocks the call; <code>ask</code> always prompts.
        </p>
      </div>

      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <div className="grid grid-cols-12 gap-2 text-xs">
          <input data-testid="settings-permission-tool" value={draftTool}
            onChange={(e) => setDraftTool(e.target.value)} placeholder="tool (e.g. bash)"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg" />
          <input data-testid="settings-permission-pattern" value={draftPattern}
            onChange={(e) => setDraftPattern(e.target.value)} placeholder="pattern (regex)"
            className="col-span-5 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg" />
          <select data-testid="settings-permission-decision" value={draftDecision}
            onChange={(e) => setDraftDecision(e.target.value as "allow" | "deny" | "ask")}
            className="col-span-2 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg">
            <option value="allow">allow</option><option value="deny">deny</option><option value="ask">ask</option>
          </select>
          <button type="button" data-testid="settings-permission-add"
            disabled={!draftPattern.trim() || !draftTool.trim()}
            onClick={async () => {
              await upsertRule({ tool: draftTool.trim(), pattern: draftPattern.trim(), decision: draftDecision });
              toast.success("Rule saved", `${draftTool} ${draftPattern} → ${draftDecision}`);
              setDraftPattern("");
            }}
            className="col-span-2 inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50">
            <Plus size={12} /> Add
          </button>
        </div>
      </div>

      <ul className="space-y-1.5" data-testid="settings-permissions-list">
        {rules.length === 0 && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">
            No permission rules yet
          </li>
        )}
        {rules.map((r) => (
          <PermissionRuleRow key={r.id} rule={r}
            onDelete={() => void removeRule(r.id)}
            onUpdate={(decision) => void upsertRule({ id: r.id, tool: r.tool, pattern: r.pattern, decision })}
          />
        ))}
      </ul>
    </section>
  );
}

function PermissionRuleRow({ rule, onDelete, onUpdate }: {
  rule: PermissionRule; onDelete: () => void;
  onUpdate: (decision: "allow" | "deny" | "ask") => void;
}): JSX.Element {
  return (
    <li data-testid={`settings-permission-row-${rule.id}`}
      className="flex items-center gap-2 rounded-md border border-minimax-border bg-minimax-panel/40 px-3 py-2 text-sm">
      <span className="rounded bg-minimax-bg px-1.5 py-0.5 text-[10px] text-minimax-muted">{rule.tool}</span>
      <code className="flex-1 truncate font-mono text-xs text-minimax-fg">{rule.pattern}</code>
      <select data-testid={`settings-permission-decision-${rule.id}`} value={rule.decision}
        onChange={(e) => onUpdate(e.target.value as "allow" | "deny" | "ask")}
        className="rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-xs text-minimax-fg">
        <option value="allow">allow</option><option value="deny">deny</option><option value="ask">ask</option>
      </select>
      <button type="button" data-testid={`settings-permission-delete-${rule.id}`}
        onClick={onDelete} aria-label="Delete rule"
        className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300">
        <Trash2 size={12} />
      </button>
    </li>
  );
}

/* ─────────────────────── Scheduled tab ─────────────────────── */

function ScheduledTab(): JSX.Element {
  const jobs = useScheduleStore((s) => s.jobs);
  const refresh = useScheduleStore((s) => s.refresh);
  const create = useScheduleStore((s) => s.create);
  const remove = useScheduleStore((s) => s.remove);
  const setEnabled = useScheduleStore((s) => s.setEnabled);
  const runNow = useScheduleStore((s) => s.runNow);
  const loading = useScheduleStore((s) => s.loading);

  const [draftName, setDraftName] = useState("");
  const [draftCron, setDraftCron] = useState("");
  const [draftPrompt, setDraftPrompt] = useState("");

  useEffect(() => { if (jobs.length === 0) void refresh(); }, [jobs.length, refresh]);

  return (
    <section data-testid="settings-scheduled" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">Scheduled jobs</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Cron jobs the agent runs on a schedule. Use 5-field cron
          expressions (e.g. <code>*/5 * * * *</code> = every 5 minutes).
        </p>
      </div>
      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <div className="grid grid-cols-12 gap-2 text-xs">
          <input data-testid="settings-job-name" value={draftName}
            onChange={(e) => setDraftName(e.target.value)} placeholder="job name"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg" />
          <input data-testid="settings-job-cron" value={draftCron}
            onChange={(e) => setDraftCron(e.target.value)} placeholder="cron expr"
            className="col-span-3 rounded border border-minimax-border bg-minimax-bg px-2 py-1 font-mono text-minimax-fg" />
          <input data-testid="settings-job-prompt" value={draftPrompt}
            onChange={(e) => setDraftPrompt(e.target.value)} placeholder="prompt payload"
            className="col-span-4 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg" />
          <button type="button" data-testid="settings-job-add"
            disabled={!draftName.trim() || !draftCron.trim()}
            onClick={async () => {
              const job = await create({ name: draftName.trim(), cron: draftCron.trim(), prompt: draftPrompt.trim() });
              if (job) { toast.success("Job created", job.name); setDraftName(""); setDraftCron(""); setDraftPrompt(""); }
            }}
            className="col-span-2 inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50">
            <Save size={12} /> Create
          </button>
        </div>
      </div>
      <ul className="space-y-1.5" data-testid="settings-jobs-list">
        {jobs.length === 0 && !loading && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">No scheduled jobs</li>
        )}
        {jobs.map((j) => (
          <ScheduledJobRow key={j.id} job={j}
            onToggle={(enabled) => void setEnabled(j.id, enabled)}
            onDelete={() => void remove(j.id)}
            onRunNow={() => void runNow(j.id)}
          />
        ))}
      </ul>
    </section>
  );
}

function ScheduledJobRow({ job, onToggle, onDelete, onRunNow }: {
  job: ScheduledJob; onToggle: (enabled: boolean) => void;
  onDelete: () => void; onRunNow: () => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const tasks = useTaskStore((s) => s.tasks);

  const relatedTasks = useMemo(() => {
    const allTasks = Object.values(tasks);
    if (allTasks.length === 0) return [];
    const jobName = job.name.toLowerCase();
    return allTasks.filter((t) =>
      (t.message && t.message.toLowerCase().includes(jobName)) ||
      t.task_id.toLowerCase().includes(job.id.toLowerCase().slice(0, 6))
    );
  }, [tasks, job.name, job.id]);

  return (
    <li data-testid={`settings-job-row-${job.id}`} className="rounded-md border border-minimax-border bg-minimax-panel/40">
      <div className="flex items-center gap-2 px-3 py-2 text-sm">
        <button type="button" data-testid={`settings-job-expand-${job.id}`}
          onClick={() => setExpanded((v) => !v)}
          className="shrink-0 rounded p-0.5 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
          aria-label={expanded ? "Collapse" : "Expand"}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        <span className="flex-1 min-w-0">
          <span className="block truncate font-medium text-minimax-fg">{job.name}</span>
          <span className="block truncate font-mono text-[11px] text-minimax-muted">{job.cron} · {job.prompt || "(no prompt)"}</span>
        </span>
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-[11px] text-minimax-muted" data-testid={`settings-job-toggle-${job.id}`}>
          <input type="checkbox" checked={job.enabled} onChange={(e) => onToggle(e.target.checked)} className="h-3 w-3 accent-minimax-accent" />
          {job.enabled ? "enabled" : "disabled"}
        </label>
        <button type="button" data-testid={`settings-job-run-now-${job.id}`} onClick={onRunNow} aria-label="Run job now"
          className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-emerald-300"><Play size={12} /></button>
        <button type="button" data-testid={`settings-job-delete-${job.id}`} onClick={onDelete} aria-label="Delete job"
          className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300"><Trash2 size={12} /></button>
      </div>
      {expanded && (
        <div data-testid={`settings-job-tasks-${job.id}`} className="border-t border-minimax-border/60 px-3 py-2">
          {relatedTasks.length === 0 ? (
            <div className="text-[11px] italic text-minimax-muted">No task runs recorded yet.</div>
          ) : (
            <ul className="space-y-1">
              {relatedTasks.map((t) => (
                <li key={t.task_id} className="flex items-center justify-between text-[11px]">
                  <div className="min-w-0 flex items-center gap-1.5">
                    <TaskStatusDot status={t.status} />
                    <span className="truncate text-minimax-fg">{t.message || t.task_id}</span>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 text-minimax-muted">
                    <span>{Math.round(t.progress * 100)}%</span>
                    <span>{t.status === "running" ? "running" : new Date(t.updated_at).toLocaleTimeString()}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

function TaskStatusDot({ status }: { status: string }): JSX.Element {
  const colors: Record<string, string> = {
    running: "bg-minimax-accent animate-pulse", done: "bg-emerald-400",
    error: "bg-red-400", cancelled: "bg-minimax-muted",
  };
  return <span aria-hidden className={`inline-block h-1.5 w-1.5 rounded-full ${colors[status] ?? "bg-minimax-muted"}`} />;
}

/* ──────────────────────── API Key tab ──────────────────────── */

function ApiKeyTab(): JSX.Element {
  const status = useSecretStore((s) => s.status);
  const loading = useSecretStore((s) => s.loading);
  const refresh = useSecretStore((s) => s.refresh);
  const setKey = useSecretStore((s) => s.setKey);
  const clear = useSecretStore((s) => s.clear);

  const [draft, setDraft] = useState("");
  const [reveal, setReveal] = useState(false);

  useEffect(() => { if (status === null) void refresh(); }, [status, refresh]);

  const sourceLabel: Record<"keyring" | "env" | "none", string> = {
    keyring: "OS keyring", env: "environment variable", none: "not configured",
  };

  const statusPill = status
    ? { keyring: { text: "Stored in OS keyring", tone: "bg-minimax-accent/20 text-minimax-accent" },
        env: { text: "Using environment variable", tone: "bg-minimax-border text-minimax-muted" },
        none: { text: "Not configured — agent in mock mode", tone: "bg-red-500/15 text-red-300" },
      }[status.source]
    : { text: "Loading…", tone: "bg-minimax-border text-minimax-muted" };

  const hasKey = status?.configured ?? false;

  return (
    <section data-testid="settings-api-key" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">MiniMax API key</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Legacy key for the built-in MiniMax provider. For multi-provider setups, use the Providers tab.
          Stored in the OS keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service).
          Falls back to the
          <code className="mx-1 rounded bg-minimax-panel px-1.5 py-0.5 font-mono text-[10px]">MINIMAX_API_KEY</code>
          env var if no keyring entry exists.
        </p>
      </div>

      <div data-testid="settings-api-key-status"
        className={"inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs " + statusPill.tone}>
        <KeyRound size={12} />
        <span data-testid="settings-api-key-status-text">{statusPill.text}</span>
      </div>

      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <label htmlFor="api-key-input" className="text-[11px] text-minimax-muted">
          {hasKey ? "Replace the keyring entry" : "Paste a key to store in the OS keyring"}
        </label>
        <div className="mt-1.5 flex gap-2">
          <div className="relative flex-1">
            <input id="api-key-input" data-testid="settings-api-key-input"
              type={reveal ? "text" : "password"} value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim() && !loading) {
                  void (async () => { const ok = await setKey(draft); if (ok) setDraft(""); })();
                }
              }}
              placeholder="sk-..." autoComplete="off" spellCheck={false}
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 pr-9 font-mono text-xs text-minimax-fg" />
            <button type="button" data-testid="settings-api-key-reveal"
              onClick={() => setReveal((v) => !v)}
              aria-label={reveal ? "Hide API key" : "Show API key"}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-minimax-muted hover:text-minimax-fg">
              {reveal ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
          </div>
          <button type="button" data-testid="settings-api-key-save"
            disabled={!draft.trim() || loading}
            onClick={async () => { const ok = await setKey(draft); if (ok) { setDraft(""); setReveal(false); } }}
            className="inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-3 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50">
            <Save size={12} /> {loading ? "Saving…" : "Save"}
          </button>
        </div>
        <p className="mt-1.5 text-[10px] text-minimax-muted">
          The key is written to <code>{sourceLabel.keyring}</code> on save.
          It is never echoed back through the wire after the write.
        </p>
      </div>

      {status?.source === "keyring" && (
        <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-xs font-medium">Keyring entry</h3>
              <p className="mt-0.5 text-[11px] text-minimax-muted">
                Removes the entry from the OS keyring. Does not affect the <code>MINIMAX_API_KEY</code> env var.
              </p>
            </div>
            <button type="button" data-testid="settings-api-key-clear"
              onClick={() => void clear()} disabled={loading}
              className="inline-flex items-center gap-1 rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50">
              <Trash2 size={12} /> Clear keyring
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/* ─────────────────────── Agents tab ─────────────────────── */

function AgentsTab(): JSX.Element {
  const agents = useAgentStore((s) => s.agents);
  const loading = useAgentStore((s) => s.loading);
  const refresh = useAgentStore((s) => s.refresh);
  const create = useAgentStore((s) => s.create);
  const remove = useAgentStore((s) => s.remove);

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formPrompt, setFormPrompt] = useState("");

  useEffect(() => { if (agents.length === 0) void refresh(); }, [agents.length, refresh]);

  const handleCreate = async () => {
    if (!formName.trim() || !formPrompt.trim()) return;
    const a = await create({ name: formName.trim(), system_prompt: formPrompt.trim() });
    if (a) { setFormName(""); setFormPrompt(""); setShowForm(false); }
  };

  return (
    <section data-testid="settings-agents" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium">Sub-agents</h2>
          <p className="mt-0.5 text-[11px] text-minimax-muted">
            Manage agents that can be invoked via <code className="rounded bg-minimax-panel px-1 font-mono text-[10px]">@agent</code> in chat.
          </p>
        </div>
        <button type="button" data-testid="settings-agent-create"
          onClick={() => setShowForm((v) => !v)}
          className="inline-flex items-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20">
          <Plus size={12} /> New Agent
        </button>
      </div>

      {showForm && (
        <div data-testid="settings-agent-form" className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3 space-y-2">
          <input data-testid="settings-agent-form-name" value={formName}
            onChange={(e) => setFormName(e.target.value)} placeholder="Agent name (e.g. code-reviewer)"
            className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg" />
          <textarea data-testid="settings-agent-form-prompt" value={formPrompt}
            onChange={(e) => setFormPrompt(e.target.value)} placeholder="System prompt…" rows={3}
            className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg resize-none" />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setShowForm(false)}
              className="rounded border border-minimax-border px-2 py-1 text-xs text-minimax-muted hover:text-minimax-fg">Cancel</button>
            <button type="button" data-testid="settings-agent-form-submit"
              onClick={() => void handleCreate()} disabled={!formName.trim() || !formPrompt.trim()}
              className="rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:opacity-50 disabled:cursor-not-allowed">Create</button>
          </div>
        </div>
      )}

      {loading && agents.length === 0 ? (
        <div className="py-4 text-center text-xs text-minimax-muted">Loading agents…</div>
      ) : agents.length === 0 ? (
        <div className="py-4 text-center text-xs italic text-minimax-muted">No sub-agents configured. Click "New Agent" to create one.</div>
      ) : (
        <ul className="space-y-2">
          {agents.map((a) => (
            <li key={a.id} data-testid={`settings-agent-row-${a.name}`}
              className="flex items-center justify-between rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <Bot size={12} className="text-minimax-accent" />
                  <span className="truncate text-xs font-medium text-minimax-fg">{a.name}</span>
                  {a.enabled ? (
                    <span className="rounded bg-emerald-500/10 px-1 py-0.5 text-[9px] text-emerald-300">enabled</span>
                  ) : (
                    <span className="rounded bg-minimax-border px-1 py-0.5 text-[9px] text-minimax-muted">disabled</span>
                  )}
                </div>
                {a.description && <p className="mt-0.5 truncate text-[10px] text-minimax-muted">{a.description}</p>}
              </div>
              <button type="button" data-testid={`settings-agent-delete-${a.name}`}
                onClick={() => void remove(a.name)} aria-label={`Delete agent ${a.name}`}
                className="ml-2 rounded border border-minimax-border p-1 text-minimax-muted hover:text-red-300">
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ─────────────────────── Audit Tab ─────────────────────── */

function AuditTab(): JSX.Element {
  const { entries, total, stats, loading, page, pageSize, filterTool, refresh, loadStats, setPage, setFilterTool } = useAuditStore();

  useEffect(() => {
    refresh();
    loadStats();
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <section data-testid="settings-audit-section" className="space-y-4">
      <h2 className="text-sm font-semibold">Audit Log</h2>
      <p className="text-[11px] text-minimax-muted">
        Every tool dispatch is recorded for full traceability. Use this to review what the agent did and when.
      </p>

      {/* Stats dashboard */}
      {stats && stats.total > 0 && (
        <div className="grid grid-cols-3 gap-3 text-center">
          <div className="rounded border border-minimax-border bg-minimax-panel px-3 py-2">
            <div className="text-lg font-bold">{stats.total}</div>
            <div className="text-[10px] text-minimax-muted">Total Calls</div>
          </div>
          <div className="rounded border border-minimax-border bg-minimax-panel px-3 py-2">
            <div className="text-lg font-bold">{Object.keys(stats.by_tool).length}</div>
            <div className="text-[10px] text-minimax-muted">Tools Used</div>
          </div>
          <div className="rounded border border-minimax-border bg-minimax-panel px-3 py-2">
            <div className="text-lg font-bold text-green-400">
              {stats.by_status.success ?? 0}
            </div>
            <div className="text-[10px] text-minimax-muted">Successes</div>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-minimax-muted">Filter by tool:</span>
        <select
          data-testid="audit-filter-tool"
          className="rounded border border-minimax-border bg-minimax-panel px-2 py-1 text-xs"
          value={filterTool ?? ""}
          onChange={(e) => setFilterTool(e.target.value || null)}
        >
          <option value="">All</option>
          {stats && Object.keys(stats.by_tool).map((t) => (
            <option key={t} value={t}>{t} ({stats.by_tool[t]})</option>
          ))}
        </select>
        <button
          type="button"
          className="ml-auto rounded border border-minimax-border px-2 py-1 text-xs hover:bg-minimax-accent/20"
          onClick={() => { refresh(); loadStats(); }}
        >
          Refresh
        </button>
      </div>

      {/* Table */}
      {loading ? (
        <div className="py-8 text-center text-xs text-minimax-muted">Loading…</div>
      ) : entries.length === 0 ? (
        <div className="py-8 text-center text-xs text-minimax-muted">
          No audit entries yet. Tool calls will appear here once the agent executes tools.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b border-minimax-border text-minimax-muted">
                <th className="px-2 py-1">Time</th>
                <th className="px-2 py-1">Tool</th>
                <th className="px-2 py-1">Status</th>
                <th className="px-2 py-1">Duration</th>
                <th className="px-2 py-1">Permission</th>
                <th className="px-2 py-1">Error</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e: AuditEntry) => (
                <tr key={e.id} className="border-b border-minimax-border/40 hover:bg-minimax-panel">
                  <td className="px-2 py-1 whitespace-nowrap">{e.created_at ? new Date(e.created_at).toLocaleString() : "—"}</td>
                  <td className="px-2 py-1 font-mono">{e.tool_name}</td>
                  <td className="px-2 py-1">
                    <StatusBadge status={e.result_status} />
                  </td>
                  <td className="px-2 py-1">{e.duration_ms != null ? `${e.duration_ms}ms` : "—"}</td>
                  <td className="px-2 py-1">{e.permission ?? "—"}</td>
                  <td className="px-2 py-1 max-w-[200px] truncate text-red-400" title={e.error ?? ""}>{e.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 text-xs text-minimax-muted">
          <button
            type="button"
            className="rounded border border-minimax-border px-2 py-1 disabled:opacity-40"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            ← Prev
          </button>
          <span>Page {page + 1} of {totalPages}</span>
          <button
            type="button"
            className="rounded border border-minimax-border px-2 py-1 disabled:opacity-40"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage(page + 1)}
          >
            Next →
          </button>
        </div>
      )}
    </section>
  );
}

function StatusBadge({ status }: { status: string }): JSX.Element {
  const colors: Record<string, string> = {
    success: "text-green-400",
    fail: "text-red-400",
    timeout: "text-yellow-400",
    denied: "text-orange-400",
  };
  return <span className={colors[status] ?? "text-minimax-muted"}>{status}</span>;
}

/* ─────────────────────── Webhooks Tab ─────────────────────── */

function WebhooksTab(): JSX.Element {
  const { entries, total, loading, error, refresh, create, remove, regenerateSecret, update } = useWebhookStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSource, setNewSource] = useState<"github" | "gitee" | "custom">("github");
  const [newAction, setNewAction] = useState<"code-review" | "send-message">("send-message");
  const [revealedSecrets, setRevealedSecrets] = useState<Set<string>>(new Set());

  useEffect(() => { refresh(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await create({ name: newName.trim(), source: newSource, action_type: newAction });
    setNewName("");
    setShowCreate(false);
  };

  const toggleSecret = (id: string) => {
    setRevealedSecrets((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <section data-testid="settings-webhooks-section" className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">Webhooks</h2>
          <p className="text-[11px] text-minimax-muted">
            Configure inbound webhook endpoints for GitHub / Gitee push events and custom integrations.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded border border-minimax-border px-2 py-1 text-xs hover:bg-minimax-accent/20"
            onClick={() => refresh()}
          >
            Refresh
          </button>
          <button
            type="button"
            data-testid="webhook-create-btn"
            className="flex items-center gap-1 rounded bg-minimax-accent px-2 py-1 text-xs text-white hover:bg-minimax-accent/80"
            onClick={() => setShowCreate(!showCreate)}
          >
            <Plus size={12} /> New
          </button>
        </div>
      </div>

      {error && <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>}

      {/* Create form */}
      {showCreate && (
        <div className="space-y-2 rounded border border-minimax-border bg-minimax-panel p-3">
          <div className="flex items-center gap-2">
            <input
              data-testid="webhook-name-input"
              className="flex-1 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
              placeholder="Webhook name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            />
            <select
              className="rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
              value={newSource}
              onChange={(e) => setNewSource(e.target.value as "github" | "gitee" | "custom")}
            >
              <option value="github">GitHub</option>
              <option value="gitee">Gitee</option>
              <option value="custom">Custom</option>
            </select>
            <select
              className="rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs"
              value={newAction}
              onChange={(e) => setNewAction(e.target.value as "code-review" | "send-message")}
            >
              <option value="send-message">Send Message</option>
              <option value="code-review">Code Review</option>
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" className="text-xs text-minimax-muted" onClick={() => setShowCreate(false)}>Cancel</button>
            <button
              type="button"
              data-testid="webhook-create-submit"
              className="rounded bg-minimax-accent px-3 py-1 text-xs text-white hover:bg-minimax-accent/80"
              onClick={handleCreate}
            >
              Create
            </button>
          </div>
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="py-8 text-center text-xs text-minimax-muted">Loading…</div>
      ) : entries.length === 0 ? (
        <div className="py-8 text-center text-xs text-minimax-muted">
          No webhooks configured. Click "New" to create one.
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((wh: WebhookConfig) => (
            <div key={wh.id} className="rounded border border-minimax-border bg-minimax-panel p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold">{wh.name}</span>
                  <span className="rounded bg-minimax-accent/20 px-1.5 py-0.5 text-[10px] text-minimax-accent">{wh.source}</span>
                  <span className="rounded bg-minimax-bg px-1.5 py-0.5 text-[10px] text-minimax-muted">{wh.action_type}</span>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className={`rounded px-1.5 py-0.5 text-[10px] ${wh.enabled ? "text-green-400" : "text-minimax-muted"}`}
                    onClick={() => update(wh.id, { enabled: !wh.enabled })}
                  >
                    {wh.enabled ? "Enabled" : "Disabled"}
                  </button>
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-[10px] text-minimax-muted hover:text-minimax-accent"
                    onClick={() => regenerateSecret(wh.id)}
                  >
                    Re-secret
                  </button>
                  <button
                    type="button"
                    className="rounded px-1.5 py-0.5 text-[10px] text-red-400 hover:text-red-300"
                    onClick={() => remove(wh.id)}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
              <div className="text-[10px] text-minimax-muted font-mono">
                POST {wh.url_path}
              </div>
              {wh.secret && (
                <div className="flex items-center gap-1 text-[10px] text-minimax-muted">
                  <span>Secret:</span>
                  <span className="font-mono">{revealedSecrets.has(wh.id) ? wh.secret : "••••••••"}</span>
                  <button
                    type="button"
                    className="text-minimax-muted hover:text-minimax-fg"
                    onClick={() => toggleSecret(wh.id)}
                  >
                    {revealedSecrets.has(wh.id) ? <EyeOff size={10} /> : <Eye size={10} />}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="text-[10px] text-minimax-muted">
        {total} webhook(s) configured
      </div>
    </section>
  );
}
