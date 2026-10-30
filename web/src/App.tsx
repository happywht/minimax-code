import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ChatPanel,
  CommandPalette,
  ConfirmationDialog,
  ConnectionBanner,
  CrashRecoveryPrompt,
  ErrorBoundary,
  InspectorDrawer,
  MessageInput,
  PermissionRequestModal,
  RightPanel,
  Sidebar,
  StorageBanner,
  ToastViewport,
  TopBar,
  toast,
} from "./components";
import { fetchHealth, ipc, typedIPC } from "./ipc";
import { strings } from "./ui/strings";
import {
  initNotificationStore,
  useChat,
  useCrashRecoveryStore,
  useModelStore,
  useNotificationStore,
  usePermissionStore,
  useProviderStore,
  useSessionStore,
} from "./stores";
import type { TimelineTarget } from "./components/right-panel/RunTimelinePanel";
import type { HistoricalReportTransfer } from "./components/panels/DeliveryWorkspace";
import type { SettingsTab } from "./components/settings/SettingsPage";
import type { SidecarEvent } from "./types/ipc";
import type { CommandPaletteHandle } from "./components/layout/CommandPalette";
import { tunnelPage } from "./ipc/tunnelPage";

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
const ProjectRunDashboard = lazy(() =>
  import("./components/panels/ProjectRunDashboard").then((module) => ({ default: module.ProjectRunDashboard })),
);
const AutomationWorkspace = lazy(() =>
  import("./components/panels/AutomationWorkspace").then((module) => ({ default: module.AutomationWorkspace })),
);
const DeliveryWorkspace = lazy(() =>
  import("./components/panels/DeliveryWorkspace").then((module) => ({ default: module.DeliveryWorkspace })),
);

type MainView = "chat" | "preview";
type OverlayView = "skills" | "settings" | "scheduled" | "workflows" | "webhooks" | "agents" | "artifacts" | "attachments" | "office" | "research" | "run-artifacts" | "task-dashboard";
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
  const automationEdited = useRef(false);
  // Conservative page-lifetime warning: drafts in other scopes may still exist.
  const scheduledDraftEdited = useRef(false);
  const settingsEdited = useRef(false);
  const deliveryEdited = useRef(false);
  const officeEdited = useRef(false);
  const [overlayView, setOverlayView] = useState<OverlayView | null>(null);
  const [deliveryRunTarget, setDeliveryRunTarget] = useState<{ host: string; service: string; project: string; session: string; run: string } | null>(null);
  const [historicalTransfer, setHistoricalTransfer] = useState<HistoricalReportTransfer | null>(null);
  useEffect(() => { if (overlayView !== "attachments") setHistoricalTransfer(null); }, [overlayView]);
  const [scheduleTarget, setScheduleTarget] = useState<{ jobId: string; occurrenceId?: string; host: string } | null>(null);
  const [settingsInitialTab, setSettingsInitialTab] = useState<SettingsTab>("models");
  const [mobileModalOpen, setMobileModalOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [timelineTarget, setTimelineTarget] = useState<TimelineTarget | null>(null);
  useEffect(() => useSessionStore.subscribe((scope) => {
    setTimelineTarget((target) => target && (target.host !== ipc.agentBaseUrl || target.project !== scope.currentProjectId || target.session !== scope.currentSessionId) ? null : target);
  }), []);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const mobileSidebarRef = useRef<HTMLDivElement>(null);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [connState, setConnState] = useState<ConnectionState>(agentReady ? "connected" : "connecting");
  const [retryAttempt, setRetryAttempt] = useState(0);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [storageDegraded, setStorageDegraded] = useState(false);
  const paletteRef = useRef<CommandPaletteHandle>(null);
  const notificationInitialized = useRef(false);
  const storesInitialized = useRef(false);
  const initializeNotifications = useCallback(() => {
    if (notificationInitialized.current) {
      void useNotificationStore.getState().refresh();
    } else {
      initNotificationStore();
      notificationInitialized.current = true;
    }
  }, []);

  const retryDelayMs = Math.min(15_000, 1000 * 2 ** retryAttempt);

  /**
   * Probe the agent's `/health` body for a degraded storage layer.
   * A `null` result (agent unreachable / unparseable body) is treated
   * as "unknown", not degraded — the connection banner owns the
   * unreachable case. Forced-mock mode has no real storage to degrade.
   */
  const probeStorageHealth = useCallback(async () => {
    if (ipc.isForcedMock) {
      setStorageDegraded(false);
      return;
    }
    const health = await fetchHealth();
    setStorageDegraded(health !== null && !health.db);
  }, []);

  const retryConnection = useCallback(async () => {
    setConnState("connecting");
    try {
      const wasHttp = ipc.isHttp;
      const reachable = await ipc.reprobe();
      if (reachable && (ipc.isHttp || ipc.isForcedMock)) {
        if ((!wasHttp || !storesInitialized.current) && ipc.isHttp) {
          await init();
          await Promise.all([
            refreshSessions(), refreshModels(), refreshRules(),
            refreshProviders(), refreshCrashReport(),
          ]);
          initializeNotifications();
          storesInitialized.current = true;
        }
        setRetryAttempt(0);
        setConnState("connected");
        void probeStorageHealth();
      } else {
        setRetryAttempt((attempt) => attempt + 1);
        setConnState("error");
      }
    } catch {
      setRetryAttempt((attempt) => attempt + 1);
      setConnState("error");
    }
  }, [init, refreshSessions, refreshModels, refreshRules, refreshProviders, refreshCrashReport, probeStorageHealth, initializeNotifications]);

  const leaveAutomation = useCallback(() => {
    if (!automationEdited.current) return true;
    if (!window.confirm("工作流或 Webhook 可能有未保存的编辑，离开会丢失这些草稿。定时任务草稿会保留在当前页面内存中。确定离开吗？")) return false;
    automationEdited.current = false;
    return true;
  }, []);
  const leaveSettings = useCallback(() => {
    if (!settingsEdited.current) return true;
    if (!window.confirm("设置中可能有未保存的编辑。离开会丢失草稿，确定离开吗？")) return false;
    settingsEdited.current = false;
    return true;
  }, []);
  const leaveDelivery = useCallback(() => true, []);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!deliveryEdited.current && !officeEdited.current && !automationEdited.current && !scheduledDraftEdited.current && !settingsEdited.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
  const isDelivery = (value: string | null) => value === "artifacts" || value === "attachments" || value === "office" || value === "research" || value === "run-artifacts";
  const handleViewChange = useCallback((next: SidebarView) => {
    if (isDelivery(overlayView) && !isDelivery(next) && !leaveDelivery()) return;
    if ((overlayView === "settings" || overlayView === "agents") && next !== overlayView && !leaveSettings()) return;
    const automation = (value: string | null) => value === "scheduled" || value === "workflows" || value === "webhooks";
    if (automation(overlayView) && !automation(next) && !leaveAutomation()) return;
    if (next === "skills" || next === "settings" || next === "scheduled" || next === "workflows" || next === "webhooks" || next === "agents" || next === "artifacts" || next === "attachments" || next === "office" || next === "research" || next === "run-artifacts" || next === "task-dashboard") {
      if (next === "run-artifacts") setDeliveryRunTarget(null);
      if (next === "settings") setSettingsInitialTab("models");
      if (next === "scheduled" || next === "workflows" || next === "webhooks") setSettingsInitialTab(next);
      if (next === "agents") setSettingsInitialTab("agents");
      setOverlayView(next);
      return;
    }
    setOverlayView(null);
    setView(next);
  }, [overlayView, leaveAutomation, leaveSettings, leaveDelivery]);

  const openSettings = useCallback((tab: SettingsTab) => {
    if (isDelivery(overlayView) && !leaveDelivery()) return;
    if ((overlayView === "scheduled" || overlayView === "workflows" || overlayView === "webhooks") && !leaveAutomation()) return;
    if ((overlayView === "settings" || overlayView === "agents") && !leaveSettings()) return;
    setSettingsInitialTab(tab);
    setOverlayView("settings");
  }, [overlayView, leaveAutomation, leaveSettings, leaveDelivery]);

  useEffect(() => {
    const openChat = () => { if (isDelivery(overlayView) && !leaveDelivery()) return; if ((overlayView === "settings" || overlayView === "agents") && !leaveSettings()) return; if ((overlayView === "scheduled" || overlayView === "workflows" || overlayView === "webhooks") && !leaveAutomation()) return; setOverlayView(null); setView("chat"); };
    window.addEventListener("minimax:open-chat", openChat);
    return () => window.removeEventListener("minimax:open-chat", openChat);
  }, [overlayView, leaveAutomation, leaveSettings, leaveDelivery]);

  useEffect(() => {
    const openSessionTimeline = (event: Event) => {
      const target = (event as CustomEvent<TimelineTarget>).detail;
      const scope = useSessionStore.getState();
      if (!target || target.host !== ipc.agentBaseUrl || target.project !== scope.currentProjectId ||
          target.session !== scope.currentSessionId || !target.service || !target.run || !target.job || !target.occurrence || !target.workspace) return;
      // The caller has already obtained permission to close its workspace.
      setTimelineTarget(target); setView("chat"); setInspectorOpen(true);
      window.dispatchEvent(new Event("minimax:open-timeline"));
    };
    window.addEventListener("minimax:open-session-timeline", openSessionTimeline);
    return () => window.removeEventListener("minimax:open-session-timeline", openSessionTimeline);
  }, []);

  useEffect(() => {
    const openRunDeliveries = (event: Event) => {
      const target = (event as CustomEvent<{ host: string; service: string; project: string; session: string; run: string }>).detail;
      const scope = useSessionStore.getState();
      if (!target || target.host !== ipc.agentBaseUrl || target.project !== scope.currentProjectId ||
          target.session !== scope.currentSessionId || !target.run || !target.service ||
          scope.sessions.find((item) => item.id === target.session)?.project_id !== target.project) return;
      if (isDelivery(overlayView) && !leaveDelivery()) return;
      if ((overlayView === "settings" || overlayView === "agents") && !leaveSettings()) return;
      if ((overlayView === "scheduled" || overlayView === "workflows" || overlayView === "webhooks") && !leaveAutomation()) return;
      setDeliveryRunTarget(target); setOverlayView("run-artifacts");
    };
    window.addEventListener("minimax:open-run-deliveries", openRunDeliveries);
    return () => window.removeEventListener("minimax:open-run-deliveries", openRunDeliveries);
  }, [overlayView, leaveAutomation, leaveSettings, leaveDelivery]);

  useEffect(() => {
    const openScheduled = (event: Event) => { if (isDelivery(overlayView) && !leaveDelivery()) return; if ((overlayView === "settings" || overlayView === "agents") && !leaveSettings()) return; const target = (event as CustomEvent<{ jobId?: string; occurrenceId?: string }>).detail; setScheduleTarget(target?.jobId ? { jobId: target.jobId, occurrenceId: target.occurrenceId, host: ipc.agentBaseUrl } : null); setOverlayView("scheduled"); };
    window.addEventListener("minimax:open-scheduled", openScheduled);
    return () => window.removeEventListener("minimax:open-scheduled", openScheduled);
  }, [overlayView, leaveSettings, leaveDelivery]);

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
        initializeNotifications();
        storesInitialized.current = true;
        // Touch the typed API once so the wire is proven end-to-end
        // even when the agent isn't running yet.
        try {
          await typedIPC.ping();
          if (ipc.isHttp || ipc.isForcedMock) {
            setRetryAttempt(0);
            setConnState("connected");
            void probeStorageHealth();
          } else {
            setRetryAttempt((attempt) => attempt + 1);
            setConnState("error");
          }
        } catch {
          setRetryAttempt((attempt) => attempt + 1);
          setConnState("error");
        }
      } catch (err) {
        setRetryAttempt((attempt) => attempt + 1);
        setConnState("error");
        // eslint-disable-next-line no-console
        console.warn("ui-shell init failed:", err);
        toast.error("UI init failed", err instanceof Error ? err.message : String(err));
      }
    })();
  }, [init, refreshSessions, refreshModels, refreshRules, refreshProviders, refreshCrashReport, probeStorageHealth, initializeNotifications]);

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

  useEffect(() => {
    if (!sidebarOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    mobileSidebarRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
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
    if (connState === "connected") setBannerDismissed(false);
  }, [connState]);

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
          onToggleInspector={() => setInspectorOpen(true)}
          onTogglePreview={() => {
            setOverlayView(null);
            setView((v) => v === "preview" ? "chat" : "preview");
          }}
          onToggleCommandPalette={() => paletteRef.current?.toggle()}
          previewActive={view === "preview"}
        />
        {tunnelPage() && <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2 text-xs text-ink-2" role="status">
          <span>远端隧道标签页 · 当前服务：{ipc.agentBaseUrl}。会话和项目独立；连接状态不表示任务仍在运行。</span>
          {tunnelPage()?.returnOrigin && <a className="text-accent underline" href={tunnelPage()?.returnOrigin ?? undefined} target="_blank" rel="noopener noreferrer">打开来源页面</a>}
        </div>}
        <StorageBanner degraded={storageDegraded} />
        <CommandPalette
          ref={paletteRef}
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
              ref={mobileSidebarRef}
              aria-modal="true"
              aria-label={strings.a11y.navigation}
              onKeyDown={(event) => {
                if (event.key === "Escape" && event.target instanceof HTMLElement && event.target.closest('[role="dialog"]') === event.currentTarget) {
                  event.preventDefault(); event.stopPropagation(); setSidebarOpen(false);
                }
              }}
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
                  connectionState={connState}
                  onOpenTimeline={() => { setTimelineTarget(null); setInspectorOpen(true); window.dispatchEvent(new Event("minimax:open-timeline")); }}
                  onOpenProviderSettings={() => openSettings("providers")}
                  onOpenModelSettings={() => openSettings("models")}
                />
                <MessageInput />
              </>
            )}
            {overlayView && (
              <WorkspaceOverlay onClose={() => { if (isDelivery(overlayView) && !leaveDelivery()) return; if ((overlayView === "settings" || overlayView === "agents") && !leaveSettings()) return; if (overlayView !== "scheduled" && overlayView !== "workflows" && overlayView !== "webhooks" || leaveAutomation()) setOverlayView(null); }}>
                <Suspense fallback={<DeferredPanelFallback />}>
                  {overlayView === "task-dashboard" ? (
                    <ProjectRunDashboard onClose={() => setOverlayView(null)} onOpenSession={() => {
                      setOverlayView(null); setView("chat"); setInspectorOpen(true); window.dispatchEvent(new Event("minimax:open-timeline"));
                    }} onPrepareReport={(transfer) => { setHistoricalTransfer(transfer); setOverlayView("attachments"); }} />
                  ) : overlayView === "office" || overlayView === "research" || overlayView === "attachments" || overlayView === "artifacts" || overlayView === "run-artifacts" ? (
                    <DeliveryWorkspace tab={overlayView} runTarget={deliveryRunTarget} onClearRunTarget={() => setDeliveryRunTarget(null)} historicalTransfer={historicalTransfer} onClearHistoricalTransfer={() => setHistoricalTransfer(null)} onSelect={(tab) => setOverlayView(tab)} onEdit={(kind) => { if (kind === "table") officeEdited.current = true; else deliveryEdited.current = true; }} onClose={() => { if (leaveDelivery()) setOverlayView(null); }} />
                  ) : overlayView === "scheduled" || overlayView === "workflows" || overlayView === "webhooks" ? (
                    <AutomationWorkspace scheduleTarget={scheduleTarget} tab={overlayView} onSelect={(tab) => setOverlayView(tab)} onEdit={() => { automationEdited.current = true; }} onScheduledEdit={() => { scheduledDraftEdited.current = true; }} onClose={() => { if (leaveAutomation()) setOverlayView(null); }} onOpenSession={() => { if (!leaveAutomation()) return false; setOverlayView(null); setView("chat"); return true; }} />
                  ) : overlayView === "skills" ? (
                    <SkillsPanel onClose={() => setOverlayView(null)} onOpenRun={() => { setOverlayView(null); setView("chat"); setInspectorOpen(true); window.dispatchEvent(new Event("minimax:open-timeline")); }} />
                  ) : (
                    <SettingsPage
                      key={overlayView}
                      initialTab={settingsInitialTab}
                      onEdit={() => { settingsEdited.current = true; }}
                      onClose={() => { if (!leaveSettings()) return false; setOverlayView(null); return true; }}
                    />
                  )}
                </Suspense>
              </WorkspaceOverlay>
            )}
          </main>
          <div className="hidden lg:block">
            <RightPanel timelineTarget={timelineTarget} onClearTimelineTarget={() => setTimelineTarget(null)} />
          </div>
          <InspectorDrawer open={inspectorOpen} onClose={() => setInspectorOpen(false)}>
            <RightPanel timelineTarget={timelineTarget} onClearTimelineTarget={() => setTimelineTarget(null)} />
          </InspectorDrawer>
          <ToastViewport />
          <PermissionRequestModal connectionState={connState} />
          <ConfirmationDialog />
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
          {connState !== "connected" && view === "chat" && !bannerDismissed && (
            <ConnectionBanner
              state={connState}
              retryDelayMs={retryDelayMs}
              onRetry={retryConnection}
              onDismiss={() => setBannerDismissed(true)}
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
        aria-label={strings.a11y.closeOverlay}
        className="absolute inset-0 cursor-default"
        onClick={onClose}
      />
      <div className="relative h-full min-w-0 overflow-hidden rounded-xl border border-line bg-surface-0 shadow-modal animate-in fade-in-0 zoom-in-95 duration-200">
        {children}
      </div>
    </div>
  );
}
