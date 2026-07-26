/**
 * Mobile device store — manages paired devices, pairing flow, and
 * online status tracking.
 *
 * v0.3.1: wraps ``mobile.pair_start``, ``mobile.list``, and
 * ``mobile.unpair`` IPC calls. The pairing token / QR payload are
 * held in state while a pairing flow is active.
 *
 * v0.7.0: Added ``onlineDevices`` set (from ``mobile.device_status``),
 * ``fetchDeviceStatus()``, and ``pushNotification()`` for mobile push.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";

export interface PairedDevice {
  id: string;
  name: string;
  paired_at: number;
  online?: boolean;
}

export interface MobileState {
  devices: PairedDevice[];
  /** Set of device IDs currently connected via WebSocket. */
  onlineDevices: Set<string>;
  pairingToken: string | null;
  qrPayload: string | null;
  expiresAt: number | null;
  loading: boolean;
  error: string | null;

  /** Start a new pairing flow — calls ``mobile.pair_start``. */
  startPairing: (suggestedName?: string) => Promise<void>;
  /** Refresh the device list from ``mobile.list``. */
  fetchDevices: () => Promise<void>;
  /** Remove a paired device via ``mobile.unpair``. */
  unpair: (deviceId: string) => Promise<void>;
  /** Clear the active pairing state. */
  clearPairing: () => void;
  /** Fetch online status via ``mobile.device_status``. */
  fetchDeviceStatus: () => Promise<void>;
  /** Push a notification to one or all devices. */
  pushNotification: (opts: {
    device_id?: string;
    notification: { type?: string; title: string; body?: string; priority?: number };
  }) => Promise<{ ok: boolean; delivered?: boolean }>;
}

export const useMobileStore = create<MobileState>((set, get) => ({
  devices: [],
  onlineDevices: new Set(),
  pairingToken: null,
  qrPayload: null,
  expiresAt: null,
  loading: false,
  error: null,

  startPairing: async (suggestedName?: string) => {
    set({ loading: true, error: null });
    try {
      const r = await typedIPC.startPairing(
        suggestedName ? { suggested_name: suggestedName } : undefined,
      );
      set({
        pairingToken: r.token,
        qrPayload: r.qr_payload,
        expiresAt: r.expires_at,
        loading: false,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message });
      toast.error("Pairing failed", message);
    }
  },

  fetchDevices: async () => {
    set({ loading: true, error: null });
    try {
      const r = await typedIPC.listDevices();
      set({ devices: r.devices, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: message });
      toast.error("Failed to load devices", message);
    }
  },

  unpair: async (deviceId: string) => {
    try {
      await typedIPC.unpairDevice(deviceId);
      set((s) => ({
        devices: s.devices.filter((d) => d.id !== deviceId),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Unpair failed", message);
    }
  },

  clearPairing: () => {
    set({ pairingToken: null, qrPayload: null, expiresAt: null });
  },

  fetchDeviceStatus: async () => {
    try {
      const r = await typedIPC.deviceStatus();
      const onlineSet = new Set(
        r.devices.filter((d) => d.online).map((d) => d.id),
      );
      // Merge online status into devices list
      set((s) => ({
        onlineDevices: onlineSet,
        devices: s.devices.map((d) => ({
          ...d,
          online: onlineSet.has(d.id),
        })),
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to fetch device status", message);
    }
  },

  pushNotification: async (opts) => {
    try {
      const r = await typedIPC.pushNotification(opts);
      return { ok: r.ok, delivered: r.delivered };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Push notification failed", message);
      return { ok: false };
    }
  },

  // expose for tests
  __getState: get,
}));
