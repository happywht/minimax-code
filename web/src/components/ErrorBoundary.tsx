/**
 * Global ErrorBoundary — catches render-time errors and surfaces a
 * inline, dismissable banner. Also exposes a programmatic toast API
 * for runtime errors (IPC failures, etc.).
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, X } from "lucide-react";

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

interface ErrorBoundaryState {
  error: Error | null;
}

interface ErrorBoundaryProps {
  children: ReactNode;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught:", error, info);
  }

  reset = (): void => this.setState({ error: null });

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div
          role="alert"
          data-testid="error-boundary"
          className="flex h-full w-full items-center justify-center bg-minimax-bg p-8 text-minimax-fg"
        >
          <div className="max-w-md rounded-lg border border-red-500/40 bg-red-500/10 p-6">
            <div className="flex items-center gap-2 text-status-error">
              <AlertTriangle size={18} />
              <h2 className="text-sm font-semibold">Something went wrong</h2>
            </div>
            <p className="mt-3 text-sm text-minimax-muted">
              {this.state.error.message}
            </p>
            <button
              type="button"
              onClick={this.reset}
              className="mt-4 rounded bg-red-500/20 px-3 py-1 text-xs text-red-200 hover:bg-red-500/30"
            >
              Reload component
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ─────────────────────── Toast viewport ─────────────────────── */

import { useEffect, useState } from "react";
import { CheckCircle2, Info } from "lucide-react";

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
            "pointer-events-auto flex items-start gap-2 rounded-md border p-3 text-sm shadow-lg backdrop-blur " +
            (t.kind === "error"
              ? "border-red-500/40 bg-red-500/10 text-red-100"
              : t.kind === "success"
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
                : "border-minimax-border bg-minimax-panel text-minimax-fg")
          }
        >
          <span className="mt-0.5">
            {t.kind === "error" ? (
              <AlertTriangle size={14} />
            ) : t.kind === "success" ? (
              <CheckCircle2 size={14} />
            ) : (
              <Info size={14} />
            )}
          </span>
          <div className="flex-1">
            <div className="font-medium">{t.title}</div>
            {t.detail && <div className="mt-0.5 text-xs opacity-80">{t.detail}</div>}
          </div>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => toastBus.dismiss(t.id)}
            className="text-minimax-muted hover:text-minimax-fg"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
