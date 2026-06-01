import { useEffect } from "react";
import {
  ChatPanel,
  ErrorBoundary,
  MessageInput,
  ModelSelector,
  PermissionToggle,
  ProgressPanel,
  Sidebar,
  ToastViewport,
  toast,
} from "./components";
import { ipc, isTauri, typedIPC } from "./ipc";
import { useChat, useModelStore, usePermissionStore, useSessionStore } from "./stores";

/**
 * Top-level layout. Bootstraps stores on mount, then renders the
 * three-pane shell:
 *   - <Sidebar /> (left)
 *   - <ChatPanel /> + <MessageInput /> (center)
 *   - <ProgressPanel /> (right, floating)
 * The <ModelSelector /> and <PermissionToggle /> live in the input
 * footer so they're always reachable.
 */
export default function App() {
  const init = useChat((s) => s.init);
  const agentReady = useChat((s) => s.agentReady);
  const refreshModels = useModelStore((s) => s.refresh);
  const refreshSessions = useSessionStore((s) => s.refresh);
  const refreshRules = usePermissionStore((s) => s.refresh);

  useEffect(() => {
    (async () => {
      try {
        await ipc.start();
        await init();
        await Promise.all([refreshSessions(), refreshModels(), refreshRules()]);
        // Touch the typed API once so the wire is proven end-to-end
        // even when the agent isn't running yet.
        try {
          await typedIPC.ping();
        } catch {
          // ping failure is fine in mock mode and is reported by the
          // ProgressPanel already.
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("ui-shell init failed:", err);
        toast.error("UI init failed", err instanceof Error ? err.message : String(err));
      }
    })();
  }, [init, refreshSessions, refreshModels, refreshRules]);

  return (
    <ErrorBoundary>
      <div
        data-testid="app-root"
        className="flex h-full w-full bg-minimax-bg text-minimax-fg"
      >
        <Sidebar />
        <main className="relative flex flex-1 flex-col">
          <ProgressPanel />
          <ChatPanel />
          <footer className="flex items-center justify-between gap-2 border-t border-minimax-border bg-minimax-bg/40 px-4 py-2">
            <div className="flex items-center gap-2">
              <PermissionToggle />
              <span
                data-testid="mode-indicator"
                className="rounded-md border border-minimax-border bg-minimax-panel px-2 py-0.5 text-[10px] text-minimax-muted"
              >
                {isTauri() ? "Tauri shell" : "Browser / mock"}
              </span>
            </div>
            <ModelSelector />
          </footer>
          <MessageInput />
        </main>
        <ToastViewport />
        {!agentReady && (
          <div
            data-testid="agent-not-ready"
            className="pointer-events-none fixed bottom-2 right-2 rounded bg-minimax-panel/80 px-2 py-1 text-xs text-minimax-muted"
          >
            {isTauri() ? "connecting to agent…" : "browser-only mode (no Tauri shell) — IPC will be mocked"}
          </div>
        )}
      </div>
    </ErrorBoundary>
  );
}
