/**
 * Model selector — dropdown of available LLM models. Bound to the
 * model store; the default option is marked with a checkmark.
 *
 * Models are grouped by provider. Each group shows a header with the
 * provider name and a protocol badge ("anthropic" / "openai").
 *
 * Two visual variants:
 *   - "default" — full-width trigger with icon + chevron, menu opens
 *     upward (used in the legacy footer).
 *   - "inline"  — slim pill trigger with no border, menu opens upward
 *     and stays anchored to the right edge (used inside the floating
 *     composer in `MessageInput`).
 */
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { Check, ChevronDown, Cpu } from "lucide-react";
import { useModelStore } from "../stores";
import { useClickOutside } from "../lib/useClickOutside";
import type { ModelInfo } from "../types/ipc";

export type ModelSelectorVariant = "default" | "inline";

export interface ModelSelectorProps {
  testId?: string;
  variant?: ModelSelectorVariant;
}

/** Group models by provider for display. */
interface ProviderGroup {
  providerId: string;
  providerName: string;
  protocol?: string;
  models: ModelInfo[];
}

export function ModelSelector({
  testId = "model-selector",
  variant = "default",
}: ModelSelectorProps): JSX.Element {
  const models = useModelStore((s) => s.models);
  const current = useModelStore((s) => s.current);
  const refresh = useModelStore((s) => s.refresh);
  const setCurrent = useModelStore((s) => s.setCurrent);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (models.length === 0) {
      void refresh();
    }
  }, [models.length, refresh]);

  const closeMenu = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);
  useClickOutside(ref, closeMenu, { enabled: open });

  /** Group models by provider_id (or provider name as fallback). */
  const groups = useMemo<ProviderGroup[]>(() => {
    const map = new Map<string, ProviderGroup>();
    for (const m of models) {
      const key = m.provider_id ?? m.provider;
      if (!map.has(key)) {
        map.set(key, {
          providerId: key,
          providerName: m.provider,
          protocol: m.protocol,
          models: [],
        });
      }
      map.get(key)!.models.push(m);
    }
    return Array.from(map.values());
  }, [models]);
  const filteredGroups = useMemo<ProviderGroup[]>(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((group) => {
        const providerMatches = group.providerName.toLowerCase().includes(q);
        return {
          ...group,
          models: providerMatches
            ? group.models
            : group.models.filter((model) =>
                [
                  model.id,
                  model.name,
                  model.provider,
                  model.protocol ?? "",
                  String(model.context_window),
                ]
                  .join(" ")
                  .toLowerCase()
                  .includes(q),
              ),
        };
      })
      .filter((group) => group.models.length > 0);
  }, [groups, query]);

  const currentModel = models.find((m) => m.id === current) ?? models[0];
  const label = currentModel ? currentModel.name : "Model";
  const isInline = variant === "inline";

  /** Color class for protocol badge. */
  const protocolBadge = (protocol?: string) => {
    if (protocol === "anthropic")
      return "bg-orange-500/10 text-orange-300";
    if (protocol === "openai")
      return "bg-emerald-500/10 text-emerald-300";
    return "bg-minimax-border text-minimax-muted";
  };

  return (
    <div ref={ref} className="relative" data-testid={testId}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid={isInline ? "chat-input-model-select" : "model-selector-trigger"}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={
          isInline
            ? "flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            : "flex items-center gap-1.5 rounded-md border border-minimax-border bg-minimax-panel px-2 py-1 text-xs text-minimax-fg hover:border-minimax-accent/50"
        }
      >
        {!isInline && <Cpu size={12} className="text-minimax-muted" />}
        <span className="max-w-[140px] truncate">{label}</span>
        {currentModel?.protocol && !isInline && (
          <span className={`rounded px-1 py-0.5 text-[8px] font-mono ${protocolBadge(currentModel.protocol)}`}>
            {currentModel.protocol}
          </span>
        )}
        <ChevronDown size={10} className="text-minimax-muted" />
      </button>
      {open && (
        <div
          role="listbox"
          data-testid="model-selector-menu"
          className="absolute bottom-full right-0 z-30 mb-1 w-72 max-h-80 overflow-hidden rounded-md border border-minimax-border bg-minimax-panel shadow-xl"
        >
          <div className="border-b border-minimax-border p-2">
            <input
              data-testid="model-selector-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search models..."
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-xs text-minimax-fg placeholder:text-minimax-muted focus:border-minimax-accent focus:outline-none"
            />
          </div>
          {models.length === 0 && (
            <div className="px-3 py-2 text-xs text-minimax-muted">
              No models available
            </div>
          )}
          {models.length > 0 && filteredGroups.length === 0 && (
            <div
              data-testid="model-selector-empty"
              className="px-3 py-3 text-center text-xs italic text-minimax-muted"
            >
              No models match "{query}".
            </div>
          )}
          <div className="max-h-64 overflow-y-auto overflow-x-hidden">
          {filteredGroups.map((g, gi) => (
            <div key={g.providerId}>
              {/* Provider group header */}
              <div className="sticky top-0 z-10 flex items-center gap-1.5 border-b border-minimax-border/60 bg-minimax-bg/95 px-3 py-1.5">
                <span className="min-w-0 truncate text-[11px] font-medium text-minimax-fg">{g.providerName}</span>
                {g.protocol && (
                  <span className={`rounded px-1 py-0.5 text-[8px] font-mono ${protocolBadge(g.protocol)}`}>
                    {g.protocol}
                  </span>
                )}
                <span className="ml-auto text-[11px] text-minimax-muted">{g.models.length}</span>
              </div>
              {/* Model items */}
              <ul>
                {g.models.map((m) => {
                  const isCurrent = m.id === current;
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={isCurrent}
                        data-testid={`chat-input-model-option-${m.id}`}
                        onClick={async () => {
                          setOpen(false);
                          if (m.id !== current) {
                            await setCurrent(m.id);
                          }
                        }}
                        className={
                          "flex w-full items-start gap-2 px-3 py-2 text-left text-xs hover:bg-minimax-border/70 " +
                          (isCurrent ? "bg-minimax-accent/10" : "")
                        }
                      >
                        <span className="flex-1 min-w-0">
                          <span className="block truncate font-medium text-minimax-fg">{m.name}</span>
                          <span className="mt-0.5 block text-[11px] text-minimax-muted">
                            {(m.context_window / 1000).toFixed(0)}k context
                            {m.supports_tools ? " · tools" : ""}
                          </span>
                        </span>
                        {isCurrent && <Check size={12} className="shrink-0 text-minimax-accent" />}
                      </button>
                    </li>
                  );
                })}
              </ul>
              {gi < groups.length - 1 && (
                <div className="border-b border-minimax-border/40" />
              )}
            </div>
          ))}
          </div>
        </div>
      )}
    </div>
  );
}
