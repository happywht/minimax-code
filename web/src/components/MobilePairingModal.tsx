/**
 * Mobile pairing modal — displays the pairing token / QR payload and
 * lists already-paired devices with unpair actions and online status.
 *
 * v0.3.1: opened from the Sidebar "连接手机" button. The pairing
 * token auto-expires after 10 minutes (countdown shown in UI).
 * v0.7.0: Added online/offline indicator (green/gray dot) per device
 * and fetches device status on mount.
 */
import { useEffect, useRef, useState } from "react";
import { Bell, Copy, QrCode, Smartphone, Trash2, X } from "lucide-react";
import { useMobileStore } from "../stores/mobileStore";
import { useFocusTrap } from "../lib/useFocusTrap";
import { formatDateTime } from "../lib/time";
import { toast } from "./ErrorBoundary";

export interface MobilePairingModalProps {
  testId?: string;
  onClose: () => void;
}

export function MobilePairingModal({
  testId = "mobile-pairing-modal",
  onClose,
}: MobilePairingModalProps): JSX.Element {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(dialogRef, true); // always active when rendered
  const devices = useMobileStore((s) => s.devices);
  const pairingToken = useMobileStore((s) => s.pairingToken);
  const qrPayload = useMobileStore((s) => s.qrPayload);
  const expiresAt = useMobileStore((s) => s.expiresAt);
  const loading = useMobileStore((s) => s.loading);
  const error = useMobileStore((s) => s.error);
  const startPairing = useMobileStore((s) => s.startPairing);
  const fetchDevices = useMobileStore((s) => s.fetchDevices);
  const fetchDeviceStatus = useMobileStore((s) => s.fetchDeviceStatus);
  const unpair = useMobileStore((s) => s.unpair);
  const clearPairing = useMobileStore((s) => s.clearPairing);
  const pushNotification = useMobileStore((s) => s.pushNotification);

  const [countdown, setCountdown] = useState<string>("");

  // Fetch devices + online status on mount
  useEffect(() => {
    void fetchDevices();
    void fetchDeviceStatus();
  }, [fetchDevices, fetchDeviceStatus]);

  // Countdown timer for pairing token expiry
  useEffect(() => {
    if (!expiresAt) {
      setCountdown("");
      return;
    }
    const tick = () => {
      const remaining = Math.max(0, expiresAt - Date.now());
      if (remaining <= 0) {
        setCountdown("Expired");
        clearPairing();
        return;
      }
      const mins = Math.floor(remaining / 60_000);
      const secs = Math.floor((remaining % 60_000) / 1000);
      setCountdown(`${mins}:${secs.toString().padStart(2, "0")}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiresAt, clearPairing]);

  const handleStartPairing = () => {
    void startPairing();
  };

  const handleCopyToken = () => {
    if (pairingToken) {
      navigator.clipboard.writeText(pairingToken).then(
        () => toast.info("Token copied"),
        () => toast.error("Copy failed"),
      );
    }
  };

  const handleCopyLink = () => {
    if (qrPayload) {
      navigator.clipboard.writeText(qrPayload).then(
        () => toast.info("Link copied"),
        () => toast.error("Copy failed"),
      );
    }
  };

  const handleTestPush = (deviceId: string, name: string) => {
    void pushNotification({
      device_id: deviceId,
      notification: { type: "info", title: "Test Push", body: `Hello from MiniMax Code, ${name}!` },
    }).then((r) => {
      if (r.ok) toast.info("Push sent");
    });
  };

  return (
    <div
      ref={dialogRef}
      data-testid={testId}
      role="dialog"
      aria-modal="true"
      aria-label="Mobile pairing"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="animate-modal-in relative w-full max-w-sm rounded-xl border border-minimax-border bg-minimax-panel shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-minimax-border px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-medium text-minimax-fg">
            <Smartphone size={14} className="text-minimax-accent" />
            Connect Mobile
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            aria-label="Close"
          >
            <X size={14} />
          </button>
        </div>

        <div className="px-4 py-3 space-y-3">
          {/* Error */}
          {error && (
            <div data-testid="mobile-pairing-error" className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-status-error">
              {error}
            </div>
          )}

          {/* Pairing section */}
          {!pairingToken ? (
            <button
              type="button"
              data-testid="mobile-pairing-start-btn"
              onClick={handleStartPairing}
              disabled={loading}
              className="flex w-full items-center justify-center gap-2 rounded-md bg-minimax-accent px-3 py-2.5 text-sm font-medium text-white disabled:opacity-40"
            >
              <QrCode size={14} />
              {loading ? "Generating…" : "Generate Pairing Code"}
            </button>
          ) : (
            <div data-testid="mobile-pairing-active" className="space-y-2">
              <div className="rounded-md border border-minimax-border bg-minimax-bg p-3 text-center">
                <div className="mb-1 text-[11px] uppercase tracking-wider text-minimax-muted">
                  Pairing Token
                </div>
                <div
                  data-testid="mobile-pairing-token"
                  className="cursor-pointer font-mono text-lg font-bold text-minimax-accent"
                  onClick={handleCopyToken}
                  title="Click to copy"
                >
                  {pairingToken}
                </div>
                {countdown && (
                  <div className="mt-1 text-[11px] text-minimax-muted">
                    Expires in {countdown}
                  </div>
                )}
              </div>
              <div className="flex gap-1.5">
                <button
                  type="button"
                  onClick={handleCopyToken}
                  className="flex flex-1 items-center justify-center gap-1 rounded-md border border-minimax-border px-2 py-1.5 text-xs text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
                >
                  <Copy size={10} /> Copy Token
                </button>
                <button
                  type="button"
                  onClick={handleCopyLink}
                  className="flex flex-1 items-center justify-center gap-1 rounded-md border border-minimax-border px-2 py-1.5 text-xs text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
                >
                  <Copy size={10} /> Copy Link
                </button>
              </div>
            </div>
          )}

          {/* Device list */}
          {devices.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] uppercase tracking-wider text-minimax-muted">
                Paired Devices ({devices.length})
              </div>
              <ul data-testid="mobile-paired-list" className="space-y-1">
                {devices.map((d) => (
                  <li
                    key={d.id}
                    className="flex items-center justify-between rounded-md border border-minimax-border bg-minimax-bg px-3 py-2"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {/* Online/Offline indicator */}
                      <span
                        className={`inline-block h-2 w-2 rounded-full shrink-0 ${
                          d.online ? "bg-green-400" : "bg-minimax-border"
                        }`}
                        title={d.online ? "Online" : "Offline"}
                      />
                      <div className="min-w-0">
                        <div className="truncate text-xs font-medium text-minimax-fg">
                          {d.name || d.id}
                        </div>
                        <div className="text-[11px] text-minimax-muted">
                          {d.online ? "Online" : "Offline"} · Paired {formatDateTime(d.paired_at)}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      {d.online && (
                        <button
                          type="button"
                          onClick={() => handleTestPush(d.id, d.name || d.id)}
                          className="rounded p-1 text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
                          title="Send test notification"
                          aria-label={`Push test to ${d.name || d.id}`}
                        >
                          <Bell size={11} />
                        </button>
                      )}
                      <button
                        type="button"
                        data-testid={`mobile-unpair-${d.id}`}
                        onClick={() => void unpair(d.id)}
                        className="rounded p-1 text-minimax-muted hover:bg-red-500/10 hover:text-status-error"
                        title="Unpair"
                        aria-label={`Unpair ${d.name || d.id}`}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Empty state */}
          {devices.length === 0 && !loading && (
            <div className="py-2 text-center text-xs text-minimax-muted">
              No paired devices yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
