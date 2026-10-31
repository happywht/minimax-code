import { useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { IconButton } from "../../ui";
import { strings } from "../../ui/strings";

export interface InspectorDrawerProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

/**
 * InspectorDrawer — overlay variant of the RightPanel for the md–lg
 * range (768–1023px), where the inline inspector column is hidden.
 * From lg up the caller never opens it (the inline panel shows), and
 * the ``lg:hidden`` guard keeps it inert even if state lingers across
 * a resize.
 */
export function InspectorDrawer({ open, onClose, children }: InspectorDrawerProps): JSX.Element | null {
  const drawerRef = useRef<HTMLDivElement>(null);
  const [desktop, setDesktop] = useState(() => typeof window.matchMedia === "function" && window.matchMedia("(min-width: 1024px)").matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => setDesktop(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const visible = open && !desktop;
  useFocusTrap(drawerRef, visible);
  useEffect(() => {
    if (!visible) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.stopPropagation(); onClose(); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [visible, onClose]);
  if (!visible) return null;

  return (
    <div
      className="fixed inset-0 z-40 lg:hidden"
      data-testid="inspector-drawer"
    >
      <div
        className="absolute inset-0 bg-surface-overlay"
        onClick={onClose}
        data-testid="inspector-drawer-backdrop"
      />
      <div ref={drawerRef} role="dialog" aria-modal="true" aria-label={strings.rightPanel.shell.inspector}
        className="absolute right-0 top-0 z-50 flex h-full w-64 max-w-[85vw] flex-col bg-surface-1 shadow-pop">
        <div className="flex shrink-0 justify-end border-b border-line px-2 py-1">
          <IconButton aria-label="关闭检查器" title="关闭检查器" onClick={onClose}><X /></IconButton>
        </div>
        <div className="min-h-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
