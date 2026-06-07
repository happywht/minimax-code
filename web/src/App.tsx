import { useEffect, useState } from "react";
import {
  ChatPanel,
  ErrorBoundary,
  MessageInput,
  MobilePairingModal,
  PermissionRequestModal,
  PreviewPanel,
  RightPanel,
  SettingsPage,
  Sidebar,
  SkillsPanel,
  ToastViewport,
  TopBar,
  toast,
} from "./components";
import { ipc, isTauri, typedIPC } from "./ipc";
import { useChat, useModelStore, usePermissionStore, useSessionStore, initNotificationStore } from "./stores";

type AppView = "chat" | "skills" | "settings" | "preview";

/**
 * Top-level layout. Bootstraps stores on mount, then renders the
 * three-pane shell:
 *   - <TopBar /> — workspace switcher + git status + settings shortcut
 *   - <Sidebar /> (left, 240px) — brand, nav, session list, user badge
 *   - <ChatPanel /> + <MessageInput /> (center) — or <SettingsPage /> / <SkillsPanel />
 *   - <RightPanel /> (right, 280px, collapsible) — progress + agent team
 *
 * Responsive: on <768px the sidebar is hidden by default and shown as
 * an overlay via the hamburger button in TopBar. On <1024px the right
 * panel is hidden.
 */
export default function App() {
  const init = useChat((s) => s.init);
  const agentReady = useChat((s) => s.agentReady);
  const refreshModels = useModelStore((s) => s.refresh);
  const refreshSessions = useSessionStore((s) => s.refresh);
  const refreshRules = usePermissionStore((s) => s.refresh);
  const [view, setView] = useState<AppView>("chat");
  const [mobileModalOpen, setMobileModalOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        await ipc.start();
        await init();
        await Promise.all([refreshSessions(), refreshModels(), refreshRules()]);
        // Initialise notification store WS listeners (v0.7.0)
        initNotificationStore();
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
        <TopBar
          onOpenSettings={() => setView("settings")}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onTogglePreview={() => setView((v) => v === "preview" ? "chat" : "preview")}
          previewActive={view === "preview"}
        />
        <div className="relative flex min-h-0 flex-1">
          {/* Desktop sidebar — always visible on md+ */}
          <div className="hidden md:block">
            <Sidebar
              view={view}
              onViewChange={(v) => { setView(v); }}
              onMobileClick={() => setMobileModalOpen(true)}
            />
          </div>
          {/* Mobile sidebar — overlay with backdrop */}
          {sidebarOpen && (
            <div className="fixed inset-0 z-40 md:hidden">
              <div
                className="absolute inset-0 bg-black/50"
                onClick={() => setSidebarOpen(false)}
              />
              <div className="relative z-50 h-full">
                <Sidebar
                  view={view}
                  onViewChange={(v) => { setView(v); setSidebarOpen(false); }}
                  onMobileClick={() => { setMobileModalOpen(true); setSidebarOpen(false); }}
                />
              </div>
            </div>
          )}
          <main className="relative flex flex-1 flex-col">
            {view === "settings" ? (
              <SettingsPage />
            ) : view === "skills" ? (
              <SkillsPanel />
            ) : view === "preview" ? (
              <PreviewPanel onClose={() => setView("chat")} />
          ) : (
            <>
              <ChatPanel />
              <MessageInput />
            </>
          )}
          </main>
          <div className="hidden lg:block">
            <RightPanel />
          </div>
          <ToastViewport />
          <PermissionRequestModal />
          {mobileModalOpen && (
            <MobilePairingModal onClose={() => setMobileModalOpen(false)} />
          )}
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
