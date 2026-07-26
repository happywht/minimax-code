/**
 * Mobile pairing modal — displays the pairing token / QR payload and
 * lists already-paired devices with unpair actions and online status.
 *
 * v0.3.1: opened from the Sidebar "连接手机" button. The pairing
 * token auto-expires after 10 minutes (countdown shown in UI).
 * v0.7.0: Added online/offline indicator (green/gray dot) per device
 * and fetches device status on mount.
 * v0.9.0: Migrated to the shared `Modal` primitive (backdrop, Esc,
 * focus trap, header all come from the design system).
 */
import { useEffect, useState } from "react";
import { Bell, Copy, QrCode, Smartphone, Trash2 } from "lucide-react";
import { useMobileStore } from "../../stores/mobileStore";
import { formatDateTime } from "../../lib/time";
import { Button, IconButton, Modal } from "../../ui";
import { toast } from "../layout/ErrorBoundary";

export interface MobilePairingModalProps {
  testId?: string;
  onClose: () => void;
}

export function MobilePairingModal({
  testId = "mobile-pairing-modal",
  onClose,
}: MobilePairingModalProps): JSX.Element {
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
    <Modal
      testId={testId}
      onClose={onClose}
      widthClass="max-w-sm"
      title={
        <span className="flex items-center gap-2">
          <Smartphone className="h-3.5 w-3.5 text-accent" />
          Connect Mobile
        </span>
      }
    >
      <div className="space-y-3">
        {/* Error */}
        {error && (
          <div
            data-testid="mobile-pairing-error"
            className="rounded-md border border-status-error/30 bg-[var(--status-error-subtle)] px-3 py-2 text-xs text-status-error"
          >
            {error}
          </div>
        )}

        {/* Pairing section */}
        {!pairingToken ? (
          <Button
            variant="primary"
            data-testid="mobile-pairing-start-btn"
            onClick={handleStartPairing}
            loading={loading}
            icon={<QrCode />}
            className="w-full"
          >
            {loading ? "Generating…" : "Generate Pairing Code"}
          </Button>
        ) : (
          <div data-testid="mobile-pairing-active" className="space-y-2">
            <div className="rounded-md border border-line bg-surface-2 p-3 text-center">
              <div className="mb-1 text-[11px] uppercase tracking-wider text-ink-2">
                Pairing Token
              </div>
              <div
                data-testid="mobile-pairing-token"
                className="cursor-pointer font-mono text-lg font-bold text-accent"
                onClick={handleCopyToken}
                title="Click to copy"
              >
                {pairingToken}
              </div>
              {countdown && (
                <div className="mt-1 text-[11px] text-ink-2">
                  Expires in {countdown}
                </div>
              )}
            </div>
            <div className="flex gap-1.5">
              <Button
                variant="secondary"
                size="sm"
                icon={<Copy />}
                onClick={handleCopyToken}
                className="flex-1"
              >
                Copy Token
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon={<Copy />}
                onClick={handleCopyLink}
                className="flex-1"
              >
                Copy Link
              </Button>
            </div>
          </div>
        )}

        {/* Device list */}
        {devices.length > 0 && (
          <div>
            <div className="mb-1.5 text-[11px] uppercase tracking-wider text-ink-2">
              Paired Devices ({devices.length})
            </div>
            <ul data-testid="mobile-paired-list" className="space-y-1">
              {devices.map((d) => (
                <li
                  key={d.id}
                  className="flex items-center justify-between rounded-md border border-line bg-surface-2 px-3 py-2"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    {/* Online/Offline indicator */}
                    <span
                      className={`inline-block h-2 w-2 shrink-0 rounded-full ${
                        d.online ? "bg-status-success" : "bg-line-strong"
                      }`}
                      title={d.online ? "Online" : "Offline"}
                    />
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium text-ink-0">
                        {d.name || d.id}
                      </div>
                      <div className="text-[11px] text-ink-2">
                        {d.online ? "Online" : "Offline"} · Paired {formatDateTime(d.paired_at)}
                      </div>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {d.online && (
                      <IconButton
                        size="sm"
                        onClick={() => handleTestPush(d.id, d.name || d.id)}
                        title="Send test notification"
                        aria-label={`Push test to ${d.name || d.id}`}
                      >
                        <Bell />
                      </IconButton>
                    )}
                    <IconButton
                      size="sm"
                      data-testid={`mobile-unpair-${d.id}`}
                      onClick={() => void unpair(d.id)}
                      className="hover:bg-[var(--status-error-subtle)] hover:text-status-error"
                      title="Unpair"
                      aria-label={`Unpair ${d.name || d.id}`}
                    >
                      <Trash2 />
                    </IconButton>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Empty state */}
        {devices.length === 0 && !loading && (
          <div className="py-2 text-center text-xs text-ink-2">
            No paired devices yet.
          </div>
        )}
      </div>
    </Modal>
  );
}
