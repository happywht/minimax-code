/**
 * Providers tab — manage LLM providers via `provider.*` IPC.
 * Includes ProviderCard sub-component and PROVIDER_PRESETS.
 */
import { useEffect, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  Globe,
  Pencil,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { useProviderStore } from "../../stores";
import { toast } from "../ErrorBoundary";
import type { ProviderInfo, ProviderModel } from "../../types/ipc";

export { ProvidersTab };

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
              <span className="text-[11px] text-minimax-muted">Quick presets:</span>
              <div className="flex flex-wrap gap-1.5">
                {PROVIDER_PRESETS.map((p) => (
                  <button key={p.label} type="button"
                    onClick={() => applyPreset(p)}
                    className="rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-[11px] text-minimax-fg hover:border-minimax-accent/40"
                  >{p.label}</button>
                ))}
              </div>
            </div>
          )}

          {/* Main fields */}
          <div className="grid grid-cols-12 gap-2">
            <div className="col-span-4">
              <label className="text-[11px] text-minimax-muted">Name</label>
              <input value={formName} onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. DeepSeek"
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg" />
            </div>
            <div className="col-span-3">
              <label className="text-[11px] text-minimax-muted">Protocol</label>
              <select value={formProtocol} onChange={(e) => setFormProtocol(e.target.value as "anthropic" | "openai")}
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg">
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
            <div className="col-span-5">
              <label className="text-[11px] text-minimax-muted">Base URL</label>
              <input value={formBaseUrl} onChange={(e) => setFormBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
                className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs font-mono text-minimax-fg" />
            </div>
          </div>

          {/* API key */}
          <div>
            <label className="text-[11px] text-minimax-muted">API Key {editingId ? "(leave empty to keep current)" : ""}</label>
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
            <label className="text-[11px] text-minimax-muted">Models</label>
            {formModels.length > 0 && (
              <ul className="mt-1 space-y-1">
                {formModels.map((m, i) => (
                  <li key={m.id} className="flex items-center gap-2 rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs">
                    <span className="flex-1 text-minimax-fg">{m.name || m.id}</span>
                    <span className="text-[11px] text-minimax-muted">{(m.context_window / 1000).toFixed(0)}k</span>
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
            <span className={`rounded px-1 py-0.5 text-[11px] font-mono ${protocolColor}`}>
              {provider.protocol}
            </span>
            {!provider.enabled && (
              <span className="rounded bg-minimax-border px-1 py-0.5 text-[11px] text-minimax-muted">disabled</span>
            )}
            {provider.api_key_configured ? (
              <span className="rounded bg-emerald-500/10 px-1 py-0.5 text-[11px] text-emerald-300">key ✓</span>
            ) : (
              <span className="rounded bg-red-500/10 px-1 py-0.5 text-[11px] text-red-300">no key</span>
            )}
          </div>
          <span className="block truncate text-[11px] font-mono text-minimax-muted">{provider.base_url}</span>
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
              <h4 className="text-[11px] font-medium text-minimax-muted mb-1">Models ({provider.models.length})</h4>
              <div className="flex flex-wrap gap-1">
                {provider.models.map((m) => (
                  <span key={m.id}
                    className="rounded bg-minimax-bg border border-minimax-border px-1.5 py-0.5 text-[11px] text-minimax-fg">
                    {m.name || m.id}
                    <span className="ml-1 text-minimax-muted">{(m.context_window / 1000).toFixed(0)}k</span>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* API key management */}
          <div>
            <h4 className="text-[11px] font-medium text-minimax-muted mb-1">API Key</h4>
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
