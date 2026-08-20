/**
 * Global ErrorBoundary — catches render-time errors and surfaces an
 * inline, dismissable banner. Also exposes a programmatic toast API
 * for runtime errors (IPC failures, etc.).
 *
 * The toast viewport itself has been extracted to `./Toast.tsx` to keep
 * this file focused; all previous exports are re-exported unchanged for
 * backwards compatibility.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "../../ui/Button";
import { strings } from "../../ui/strings";
import { ToastItem, ToastKind, toast, toastBus, ToastViewport } from "./Toast";

export type { ToastItem, ToastKind };
export { toast, toastBus, ToastViewport };

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
          className="flex h-full w-full items-center justify-center bg-surface-0 p-8 text-ink-0"
        >
          <div className="max-w-md rounded-xl border border-status-error/40 bg-[var(--status-error-subtle)] p-6 shadow-modal">
            <div className="flex items-center gap-2 text-status-error">
              <AlertTriangle size={18} />
              <h2 className="text-sm font-semibold">{strings.layout.errorBoundary.title}</h2>
            </div>
            <p className="mt-3 text-sm text-ink-1">
              {this.state.error.message}
            </p>
            <Button
              variant="danger"
              size="sm"
              onClick={this.reset}
              className="mt-4"
            >
              {strings.layout.errorBoundary.reload}
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
