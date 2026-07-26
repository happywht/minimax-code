import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle } from "lucide-react";
import { useFocusTrap } from "../../lib/useFocusTrap";
import { Button } from "../../ui";

export interface ConfirmationOptions {
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
}

interface ConfirmationRequest extends ConfirmationOptions {
  id: number;
  resolve: (accepted: boolean) => void;
}

type ConfirmationListener = (request: ConfirmationRequest | null) => void;

class ConfirmationBus {
  private current: ConfirmationRequest | null = null;
  private listeners = new Set<ConfirmationListener>();
  private nextId = 1;

  subscribe(listener: ConfirmationListener): () => void {
    this.listeners.add(listener);
    listener(this.current);
    return () => this.listeners.delete(listener);
  }

  request(options: ConfirmationOptions): Promise<boolean> {
    if (this.current) return Promise.resolve(false);
    return new Promise((resolve) => {
      this.current = { ...options, id: this.nextId++, resolve };
      this.emit();
    });
  }

  settle(accepted: boolean): void {
    const request = this.current;
    if (!request) return;
    this.current = null;
    this.emit();
    request.resolve(accepted);
  }

  reset(): void {
    this.settle(false);
  }

  private emit(): void {
    for (const listener of this.listeners) listener(this.current);
  }
}

export const confirmationBus = new ConfirmationBus();

export function requestConfirmation(options: ConfirmationOptions): Promise<boolean> {
  return confirmationBus.request(options);
}

export function ConfirmationDialog(): JSX.Element | null {
  const [request, setRequest] = useState<ConfirmationRequest | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useFocusTrap(dialogRef, Boolean(request));

  useEffect(() => confirmationBus.subscribe(setRequest), []);

  useEffect(() => {
    if (!request) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      confirmationBus.settle(false);
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [request]);

  if (!request) return null;

  return createPortal(
    <div
      ref={dialogRef}
      data-testid="confirmation-dialog"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className="fixed inset-0 z-[80] flex items-center justify-center bg-surface-overlay p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) confirmationBus.settle(false);
      }}
    >
      <div className="animate-modal-in w-full max-w-sm rounded-xl border border-line bg-surface-1 p-4 shadow-modal">
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-status-error/30 bg-[var(--status-error-subtle)] text-status-error">
            <AlertTriangle size={16} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-pretty text-sm font-semibold text-ink-0">
              {request.title}
            </h2>
            <p id={descriptionId} className="mt-1 text-pretty text-xs leading-relaxed text-ink-1">
              {request.description}
            </p>
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            data-testid="confirmation-cancel"
            onClick={() => confirmationBus.settle(false)}
          >
            {request.cancelLabel ?? "Cancel"}
          </Button>
          <Button
            variant="danger"
            size="sm"
            data-testid="confirmation-confirm"
            onClick={() => confirmationBus.settle(true)}
          >
            {request.confirmLabel ?? "Delete"}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
