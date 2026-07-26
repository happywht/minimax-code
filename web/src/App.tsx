import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import {
  ChatPanel,
  CommandPalette,
  ConnectionBanner,
  CrashRecoveryPrompt,
  ErrorBoundary,
  MessageInput,
  PermissionRequestModal,
  RightPanel,
  Sidebar,
  ToastViewport,
  TopBar,
  toast,
} from "./components";
import { ipc, typedIPC } from "./ipc";
import {
  initNotificationStore,
  useChat,
  useCrashRecoveryStore,
  useModelStore,
  usePermissionStore,
  useProviderStore,
  useSessionStore,
} from "./stores";
import type { SettingsTab } from "./components/settings/SettingsPage";
import type { SidecarEvent } from "./types/ipc";

const MobilePairingModal = lazy(() =>
  import("./components/modals/MobilePairingModal").then((module) => ({ default: module.MobilePairingModal })),
);
const PreviewPanel = lazy(() =>
  import("./components/panels/PreviewPanel").then((module) => ({ default: module.PreviewPanel })),
);
const SettingsPage = lazy(() =>
  import("./components/settings/SettingsPage").then((module) => ({ default: module.SettingsPage })),
);
const ShortcutsOverlay = lazy(() =>
  import("./components/layout/ShortcutsOverlay").then((module) => ({ default: module.ShortcutsOverlay })),
);
const SkillsPanel = lazy(() =>
  import("./components/panels/SkillsPanel").then((module) => ({ default: module.SkillsPanel })),
);

type MainView = "chat" | "preview";
type OverlayView = "skills" | "settings" | "scheduled" | "agents";
type SidebarView = MainView | OverlayView;

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
  const refreshProviders = useProviderStore((s) => s.refresh);
  const refreshCrashReport = useCrashRecoveryStore((s) => s.loadPreviousReport);
  const [view, setView] = useState<MainView>("chat");
  const [overlayView, setOverlayView] = useState<OverlayView | null>(null);
  const [settingsInitialTab, setSettingsInitialTab] = useState<SettingsTab>("models");
  const [mobileModalOpen, setMobileModalOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [connState, setConnState] = useState<ConnectionState>(agentReady ? "connected" : "connecting");
  const [retryAttempt, setRetryAttempt] = useState(0);

  const retryDelayMs = Math.min(15_000, 1000 * 2 ** retryAttempt);

  const retryConnection = useCallback(async () => {
    setConnState("connecting");
    try {
      const reachable = await ipc.reprobe();
      if (reachable && (ipc.isHttp || ipc.isForcedMock)) {
        setRetryAttempt(0);
        setConnState("connected");
      } else {
        setRetryAttempt((attempt) => attempt + 1);
        setConnState("error");
      }
    } catch {
      setRetryAttempt((attempt) => attempt + 1);
      setConnState("error");
    }
  }, []);

  const handleViewChange = useCallback((next: SidebarView) => {
    if (next === "skills" || next === "settings" || next === "scheduled" || next === "agents") {
      if (next === "settings") setSettingsInitialTab("models");
      if (next === "scheduled") setSettingsInitialTab("scheduled");
      if (next === "agents") setSettingsInitialTab("agents");
      setOverlayView(next);
      return;
    }
    setOverlayView(null);
    setView(next);
  }, []);

  const openSettings = useCallback((tab: SettingsTab) => {
    setSettingsInitialTab(tab);
    setOverlayView("settings");
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await ipc.start();
        await init();
        await Promise.all([
          refreshSessions(),
          refreshModels(),
          refreshRules(),
          refreshProviders(),
          refreshCrashReport(),
        ]);
        // Initialise notification store WS listeners (v0.7.0)
        initNotificationStore();
        // Touch the typed API once so the wire is proven end-to-end
        // even when the agent isn't running yet.
        try {
          await typedIPC.ping();
          if (ipc.isHttp || ipc.isForcedMock) {
            setRetryAttempt(0);
            setConnState("connected");
          } else {
            setRetryAttempt((attempt) => attempt + 1);
            setConnState("error");
          }
        } catch {
          // The banner below reports retry state; the rest of the UI
          // can continue working against the frontend mock.
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("ui-shell init failed:", err);
        toast.error("UI init failed", err instanceof Error ? err.message : String(err));
      }
    })();
  }, [init, refreshSessions, refreshModels, refreshRules, refreshProviders, refreshCrashReport]);

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
        className="flex h-full w-full flex-col overflow-hidden bg-surface-0 text-ink-0"
      >
        <TopBar
          onOpenSettings={() => openSettings("models")}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onTogglePreview={() => {
            setOverlayView(null);
            setView((v) => v === "preview" ? "chat" : "preview");
          }}
          onOpenCommandPalette={() => {
            // The palette registers its own shortcut; this button toggles it.
            window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }));
          }}
          previewActive={view === "preview"}
        />
        <CommandPalette
          onOpenSkills={() => setOverlayView("skills")}
          onOpenSettings={(tab) => openSettings(tab)}
          onTogglePreview={() => {
            setOverlayView(null);
            setView((v) => v === "preview" ? "chat" : "preview");
          }}
        />
        <div className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden">
          {/* Desktop sidebar — always visible on md+ */}
          <div className="hidden h-full md:block">
            <Sidebar
              view={overlayView ?? view}
              onViewChange={handleViewChange}
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
                className="absolute inset-0 bg-surface-overlay"
                onClick={() => setSidebarOpen(false)}
              />
              <div className="relative z-50 h-full max-w-[85vw] animate-in slide-in-from-left-4 duration-200">
                <Sidebar
                  view={overlayView ?? view}
                  onViewChange={(v) => { handleViewChange(v); setSidebarOpen(false); }}
                  onMobileClick={() => { setMobileModalOpen(true); setSidebarOpen(false); }}
                />
              </div>
            </div>
          )}
          <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
            {view === "preview" ? (
              <Suspense fallback={<DeferredPanelFallback />}>
                <PreviewPanel onClose={() => setView("chat")} />
              </Suspense>
            ) : (
              <>
                <ChatPanel
                  onOpenProviderSettings={() => openSettings("providers")}
                  onOpenModelSettings={() => openSettings("models")}
                />
                <MessageInput />
              </>
            )}
            {overlayView && (
              <WorkspaceOverlay onClose={() => setOverlayView(null)}>
                <Suspense fallback={<DeferredPanelFallback />}>
                  {overlayView === "skills" ? (
                    <SkillsPanel onClose={() => setOverlayView(null)} />
                  ) : (
                    <SettingsPage
                      key={overlayView}
                      initialTab={settingsInitialTab}
                      onClose={() => setOverlayView(null)}
                    />
                  )}
                </Suspense>
              </WorkspaceOverlay>
            )}
          </main>
          <div className="hidden lg:block">
            <RightPanel />
          </div>
          <ToastViewport />
          <PermissionRequestModal />
          <CrashRecoveryPrompt />
          {mobileModalOpen && (
            <Suspense fallback={null}>
              <MobilePairingModal onClose={() => setMobileModalOpen(false)} />
            </Suspense>
          )}
          {shortcutsOpen && (
            <Suspense fallback={null}>
              <ShortcutsOverlay onClose={() => setShortcutsOpen(false)} />
            </Suspense>
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

function DeferredPanelFallback(): JSX.Element {
  return (
    <div className="flex h-full min-h-0 w-full items-center justify-center" aria-busy="true">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-line border-t-accent" />
    </div>
  );
}

function WorkspaceOverlay({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}): JSX.Element {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      data-testid="workspace-overlay"
      className="absolute inset-0 z-30 min-w-0 overflow-hidden bg-surface-overlay p-2 backdrop-blur-sm transition-opacity duration-200 sm:p-3 md:p-4"
    >
      <button
        type="button"
        aria-label="Close overlay"
        className="absolute inset-0 cursor-default"
        onClick={onClose}
      />
      <div className="relative h-full min-w-0 overflow-hidden rounded-lg border border-minimax-border bg-minimax-bg shadow-2xl shadow-black/20 animate-in fade-in-0 zoom-in-95 duration-200">
        {children}
      </div>
    </div>
  );
}
