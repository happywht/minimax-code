import type { ReactNode } from "react";
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
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 lg:hidden"
      role="dialog"
      aria-modal="true"
      aria-label={strings.rightPanel.shell.inspector}
      data-testid="inspector-drawer"
    >
      <div
        className="absolute inset-0 bg-surface-overlay"
        onClick={onClose}
        data-testid="inspector-drawer-backdrop"
      />
      <div className="absolute right-0 top-0 z-50 h-full max-w-[85vw] shadow-pop">
        {children}
      </div>
    </div>
  );
}
