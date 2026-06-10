import { Keyboard, X } from "lucide-react";

export interface ShortcutsOverlayProps {
  onClose: () => void;
}

const SHORTCUTS = [
  { key: "?", label: "Open shortcuts" },
  { key: "Esc", label: "Close panels" },
  { key: "Enter", label: "Send message" },
  { key: "Shift Enter", label: "New line" },
  { key: "@", label: "Pick sub-agent" },
  { key: "Tab", label: "Accept picker item" },
];

export function ShortcutsOverlay({ onClose }: ShortcutsOverlayProps): JSX.Element {
  return (
    <div
      data-testid="shortcuts-overlay"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 px-4 backdrop-blur-sm animate-in fade-in duration-200"
      role="dialog"
      aria-modal="true"
      aria-labelledby="shortcuts-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-minimax-border bg-minimax-panel shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-minimax-border px-3 py-2">
          <div className="flex items-center gap-2">
            <Keyboard size={14} className="text-minimax-muted" />
            <h2 id="shortcuts-title" className="text-sm font-semibold">
              Keyboard Shortcuts
            </h2>
          </div>
          <button
            type="button"
            data-testid="shortcuts-close"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-minimax-muted transition-colors duration-200 hover:bg-minimax-border hover:text-minimax-fg"
            aria-label="Close shortcuts"
          >
            <X size={14} />
          </button>
        </div>
        <div className="divide-y divide-minimax-border/60 px-3 py-1">
          {SHORTCUTS.map((item) => (
            <div key={item.key} className="flex items-center justify-between gap-3 py-2">
              <span className="text-xs text-minimax-muted">{item.label}</span>
              <kbd className="shrink-0 rounded border border-minimax-border bg-minimax-bg px-1.5 py-0.5 font-mono text-[11px] text-minimax-fg">
                {item.key}
              </kbd>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
