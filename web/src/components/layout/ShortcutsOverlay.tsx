import { Keyboard, X } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { strings } from "../../ui/strings";

export interface ShortcutsOverlayProps {
  onClose: () => void;
}

export function ShortcutsOverlay({ onClose }: ShortcutsOverlayProps): JSX.Element {
  return (
    <div
      data-testid="shortcuts-overlay"
      className="fixed inset-0 z-50 flex animate-rise-in items-center justify-center bg-surface-overlay px-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="shortcuts-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-xl border border-line bg-surface-1 shadow-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex h-11 items-center justify-between gap-2 border-b border-line px-4">
          <div className="flex items-center gap-2">
            <Keyboard size={14} className="text-ink-2" />
            <h2 id="shortcuts-title" className="text-sm font-semibold text-ink-0">
              {strings.layout.shortcuts.title}
            </h2>
          </div>
          <IconButton
            aria-label={strings.layout.shortcuts.close}
            data-testid="shortcuts-close"
            onClick={onClose}
          >
            <X size={14} />
          </IconButton>
        </div>
        <div className="divide-y divide-line/60 px-4 py-1">
          {strings.layout.shortcuts.items.map((item) => (
            <div key={item.key} className="flex items-center justify-between gap-3 py-2">
              <span className="text-xs text-ink-1">{item.label}</span>
              <kbd className="shrink-0 rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-ink-0">
                {item.key}
              </kbd>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
