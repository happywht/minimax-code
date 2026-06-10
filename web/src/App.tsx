import { useCallback, useEffect, useState } from "react";
import {
  ChatPanel,
  ConnectionBanner,
  ErrorBoundary,
  MessageInput,
  MobilePairingModal,
  PermissionRequestModal,
  PreviewPanel,
  RightPanel,
  SettingsPage,
  ShortcutsOverlay,
  Sidebar,
  SkillsPanel,
  ToastViewport,
  TopBar,
  toast,
} from "./components";
import { ipc, typedIPC } from "./ipc";
import { useChat, useModelStore, usePermissionStore, useSessionStore, initNotificationStore } from "./stores";
import type { SidecarEvent } from "./types/ipc";

type AppView = "chat" | "skills" | "settings" | "preview";

/** Connection status derived from sidecar events. */
type ConnectionState = "connecting" | "connected" | "disconnected" | "error";

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
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [connState, setConnState] = useState<ConnectionState>(agentReady ? "connected" : "connecting");
  const [retryAttempt, setRetryAttempt] = useState(0);

  const retryDelayMs = Math.min(15_000, 1000 * 2 ** retryAttempt);

  const retryConnection = useCallback(async () => {
    setConnState("connecting");
    try {
      await ipc.start();
      await typedIPC.ping();
      setRetryAttempt(0);
      setConnState("connected");
    } catch {
      setRetryAttempt((attempt) => attempt + 1);
      setConnState("error");
    }
  }, []);

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
          setRetryAttempt(0);
          setConnState("connected");
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

  // ── 4.6: Mobile sidebar scroll lock ──
  // Prevent background scrolling when the mobile sidebar overlay is open.
  useEffect(() => {
    if (sidebarOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [sidebarOpen]);

  // ── 4.7: Live connection status via sidecar events ──
  useEffect(() => {
    const unsub = ipc.onSideCar((ev: SidecarEvent) => {
      if (ev.status === "started") {
        setRetryAttempt(0);
        setConnState("connected");
      } else if (ev.status === "stopped") setConnState("disconnected");
      else if (ev.status === "error") setConnState("error");
    });
    return unsub;
  }, []);

  useEffect(() => {
    if (connState === "connected") return;
    const timer = window.setTimeout(() => {
      void retryConnection();
    }, retryDelayMs);
    return () => window.clearTimeout(timer);
  }, [connState, retryConnection, retryDelayMs]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const active = document.activeElement as HTMLElement | null;
      const isEditableElement = (el: HTMLElement | null) => {
        const tagName = el?.tagName.toLowerCase();
        return tagName === "input" || tagName === "textarea" || !!el?.isContentEditable;
      };
      const isEditable = isEditableElement(target) || isEditableElement(active);
      if (event.key === "Escape" && shortcutsOpen) {
        event.preventDefault();
        setShortcutsOpen(false);
        return;
      }
      if (!isEditable && event.key === "?") {
        event.preventDefault();
        setShortcutsOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [shortcutsOpen]);

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
          {sidebarOpen && (
            <div
              className="fixed inset-0 z-40 md:hidden"
              role="dialog"
              aria-modal="true"
              aria-label="Navigation"
            >
              <div
                className="absolute inset-0 bg-black/50"
                onClick={() => setSidebarOpen(false)}
              />
              <div className="relative z-50 h-full max-w-[85vw] animate-in slide-in-from-left-4 duration-200">
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
              <SettingsPage onClose={() => setView("chat")} />
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
          {shortcutsOpen && (
            <ShortcutsOverlay onClose={() => setShortcutsOpen(false)} />
          )}
          {connState !== "connected" && view === "chat" && (
            <ConnectionBanner
              state={connState}
              retryDelayMs={retryDelayMs}
              onRetry={retryConnection}
            />
          )}
        </div>
      </div>
    </ErrorBoundary>
  );
}
