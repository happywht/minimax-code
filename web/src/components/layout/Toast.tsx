/**
 * Toast system — extracted from ErrorBoundary so the viewport can live
 * in its own file while keeping the programmatic API stable.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";
import { IconButton } from "../../ui/IconButton";
import { strings } from "../../ui/strings";

export type ToastKind = "error" | "info" | "success";

export interface ToastItem {
  id: string;
  kind: ToastKind;
  title: string;
  detail?: string;
  createdAt: number;
}

type Listener = (toasts: ToastItem[]) => void;

class ToastBus {
  private items: ToastItem[] = [];
  private listeners = new Set<Listener>();

  subscribe(l: Listener): () => void {
    this.listeners.add(l);
    l(this.items);
    return () => this.listeners.delete(l);
  }

  push(t: Omit<ToastItem, "id" | "createdAt">): string {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const item: ToastItem = { ...t, id, createdAt: Date.now() };
    this.items = [...this.items, item];
    this.emit();
    if (t.kind !== "error") {
      // auto-dismiss info / success after 4s
      setTimeout(() => this.dismiss(id), 4000);
    }
    return id;
  }

  dismiss(id: string): void {
    this.items = this.items.filter((t) => t.id !== id);
    this.emit();
  }

  clear(): void {
    this.items = [];
    this.emit();
  }

  private emit(): void {
    for (const l of this.listeners) l(this.items);
  }
}

export const toastBus = new ToastBus();

/** Hook-free convenience helpers. */
export const toast = {
  error: (title: string, detail?: string) => toastBus.push({ kind: "error", title, detail }),
  info: (title: string, detail?: string) => toastBus.push({ kind: "info", title, detail }),
  success: (title: string, detail?: string) => toastBus.push({ kind: "success", title, detail }),
};

const KIND_CLASSES: Record<ToastKind, string> = {
  error: "border-status-error/40 bg-[var(--status-error-subtle)]",
  success: "border-status-success/40 bg-[var(--status-success-subtle)]",
  info: "border-line bg-surface-1",
};

export function ToastViewport(): JSX.Element | null {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => toastBus.subscribe(setItems), []);
  if (items.length === 0) return null;
  return (
    <div
      data-testid="toast-viewport"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2"
    >
      {items.map((t) => (
        <div
          key={t.id}
          role={t.kind === "error" ? "alert" : "status"}
          className={
            "pointer-events-auto flex animate-rise-in items-start gap-2 rounded-lg border p-3 shadow-pop " +
            `${KIND_CLASSES[t.kind]}`
          }
        >
          <span className="mt-0.5 shrink-0 text-ink-0">
            {t.kind === "error" ? (
              <AlertTriangle size={14} className="text-status-error" />
            ) : t.kind === "success" ? (
              <CheckCircle2 size={14} className="text-status-success" />
            ) : (
              <Info size={14} className="text-status-info" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-ink-0">{t.title}</div>
            {t.detail && <div className="mt-0.5 text-xs text-ink-1">{t.detail}</div>}
          </div>
          <IconButton
            aria-label={strings.layout.toast.dismiss}
            size="sm"
            onClick={() => toastBus.dismiss(t.id)}
            className="text-ink-2 hover:bg-surface-3 hover:text-ink-0"
          >
            <X size={14} />
          </IconButton>
        </div>
      ))}
    </div>
  );
}
