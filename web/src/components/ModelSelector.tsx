/**
 * Model selector — dropdown of available LLM models. Bound to the
 * model store; the default option is marked with a checkmark.
 */
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Cpu } from "lucide-react";
import { useModelStore } from "../stores";

export interface ModelSelectorProps {
  testId?: string;
}

export function ModelSelector({ testId = "model-selector" }: ModelSelectorProps): JSX.Element {
  const models = useModelStore((s) => s.models);
  const current = useModelStore((s) => s.current);
  const refresh = useModelStore((s) => s.refresh);
  const setCurrent = useModelStore((s) => s.setCurrent);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (models.length === 0) {
      void refresh();
    }
  }, [models.length, refresh]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const currentModel = models.find((m) => m.id === current) ?? models[0];
  const label = currentModel ? currentModel.name : "Model";

  return (
    <div ref={ref} className="relative" data-testid={testId}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid="model-selector-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-md border border-minimax-border bg-minimax-panel px-2 py-1 text-xs text-minimax-fg hover:border-minimax-accent/50"
      >
        <Cpu size={12} className="text-minimax-muted" />
        <span>{label}</span>
        <ChevronDown size={12} className="text-minimax-muted" />
      </button>
      {open && (
        <ul
          role="listbox"
          data-testid="model-selector-menu"
          className="absolute bottom-full right-0 z-10 mb-1 w-60 overflow-hidden rounded-md border border-minimax-border bg-minimax-panel shadow-xl"
        >
          {models.length === 0 && (
            <li className="px-3 py-2 text-xs text-minimax-muted">
              No models available
            </li>
          )}
          {models.map((m) => {
            const isCurrent = m.id === current;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={isCurrent}
                  onClick={async () => {
                    setOpen(false);
                    if (m.id !== current) {
                      await setCurrent(m.id);
                    }
                  }}
                  className={
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-minimax-border " +
                    (isCurrent ? "bg-minimax-accent/10" : "")
                  }
                >
                  <span className="flex-1">
                    <span className="block text-minimax-fg">{m.name}</span>
                    <span className="block text-[10px] text-minimax-muted">
                      {m.provider} · {(m.context_window / 1000).toFixed(0)}k ctx
                      {m.supports_tools ? " · tools" : ""}
                    </span>
                  </span>
                  {isCurrent && <Check size={12} className="text-minimax-accent" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
