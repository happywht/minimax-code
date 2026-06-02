import { useEffect, useState } from "react";
import { Settings as SettingsIcon } from "lucide-react";
import {
  ChatPanel,
  ErrorBoundary,
  MessageInput,
  PermissionRequestModal,
  RightPanel,
  SettingsPage,
  Sidebar,
  SkillsPanel,
  ToastViewport,
  WorkspaceSwitcher,
  toast,
} from "./components";
import { ipc, isTauri, typedIPC } from "./ipc";
import { useChat, useModelStore, usePermissionStore, useSessionStore } from "./stores";

type AppView = "chat" | "skills" | "settings";

/**
 * Top-level layout. Bootstraps stores on mount, then renders the
 * three-pane shell:
 *   - <Sidebar /> (left, 240px) — brand, nav, session list, user badge
 *   - <ChatPanel /> + <MessageInput /> (center) — or <SettingsPage /> / <SkillsPanel />
 *   - <RightPanel /> (right, 280px, collapsible) — progress + agent team
 *
 * The mode indicator and the "always allow" / model picker controls
 * used to live in a separate footer; they have moved inline into the
 * floating <MessageInput /> pill, so the footer is no longer needed.
 */
export default function App() {
  const init = useChat((s) => s.init);
  const agentReady = useChat((s) => s.agentReady);
  const refreshModels = useModelStore((s) => s.refresh);
  const refreshSessions = useSessionStore((s) => s.refresh);
  const refreshRules = usePermissionStore((s) => s.refresh);
  const [view, setView] = useState<AppView>("chat");

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
        className="flex h-full w-full flex-col bg-minimax-bg text-minimax-fg"
      >
        <header
          data-testid="app-topbar"
          className="flex h-10 shrink-0 items-center justify-between border-b border-minimax-border bg-minimax-panel px-4"
        >
          <WorkspaceSwitcher />
          <button
            type="button"
            data-testid="app-topbar-settings"
            onClick={() => setView("settings")}
            className="flex items-center gap-1.5 rounded-md border border-transparent px-2 py-1 text-xs text-minimax-fg/80 hover:border-minimax-border hover:text-minimax-fg"
          >
            <SettingsIcon size={12} className="text-minimax-muted" />
            <span>Settings</span>
          </button>
        </header>
        <div className="flex min-h-0 flex-1">
          <Sidebar
            view={view}
            onViewChange={(v) => setView(v)}
          />
          <main className="relative flex flex-1 flex-col">
            {view === "settings" ? (
              <SettingsPage />
            ) : view === "skills" ? (
              <SkillsPanel />
          ) : (
            <>
              <ChatPanel />
              <MessageInput />
            </>
          )}
          </main>
          <RightPanel />
          <ToastViewport />
          <PermissionRequestModal />
          {!agentReady && view === "chat" && (
            <div
              data-testid="agent-not-ready"
              className="pointer-events-none fixed bottom-2 right-2 rounded bg-minimax-panel/80 px-2 py-1 text-xs text-minimax-muted"
            >
              {isTauri() ? "connecting to agent…" : "browser-only mode (no Tauri shell) — IPC will be mocked"}
            </div>
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
}
