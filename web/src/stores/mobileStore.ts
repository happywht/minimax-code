/**
 * Mobile device store — manages paired devices and pairing flow.
 *
 * v0.3.1: wraps ``mobile.pair_start``, ``mobile.list``, and
 * ``mobile.unpair`` IPC calls. The pairing token / QR payload are
 * held in state while a pairing flow is active.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/ErrorBoundary";

export interface PairedDevice {
  id: string;
  name: string;
  paired_at: number;
}

export interface MobileState {
  devices: PairedDevice[];
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
}

export const useMobileStore = create<MobileState>((set, get) => ({
  devices: [],
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

  // expose for tests
  __getState: get,
}));
