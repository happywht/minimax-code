/**
 * Secrets store — drives the Settings page's "API Key" tab.
 *
 * Backed by the ``secrets.*`` IPC namespace, which writes to the
 * OS keyring (Windows Credential Manager / macOS Keychain /
 * Linux Secret Service) with an env-var fallback.
 *
 * The store only tracks the *status* (configured / source), not
 * the key value itself — the key never travels back through the
 * IPC layer after a successful write.
 */

import { create } from "zustand";
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import type { SecretStatus } from "../types/ipc";
import { strings } from "../ui/strings";

export type SecretSource = SecretStatus["source"];

export interface SecretState {
  /** Latest known status — null until first refresh or write. */
  status: SecretStatus | null;
  /** True while a ``secrets.*`` request is in flight. */
  loading: boolean;

  /** Pull the current status from the backend. */
  refresh: () => Promise<void>;
  /** Persist a new key to the keyring. Returns the new status. */
  setKey: (value: string) => Promise<SecretStatus | null>;
  /** Remove the key from the keyring. Returns the new status. */
  clear: () => Promise<SecretStatus | null>;
}

export const useSecretStore = create<SecretState>((set, get) => ({
  status: null,
  loading: false,

  refresh: async () => {
    set({ loading: true });
    try {
      const s = await typedIPC.getSecretStatus();
      set({ status: s, loading: false });
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.apiKeyStatusFailed, message);
    }
  },

  setKey: async (value: string) => {
    set({ loading: true });
    try {
      const s = await typedIPC.setSecret(value);
      set({ status: s, loading: false });
      toast.success(strings.toasts.apiKeySaved, strings.toasts.apiKeySavedDetail);
      return s;
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.apiKeySaveFailed, message);
      // Refresh the status so the UI shows the post-failure truth
      // (the keyring may have partially written before the error).
      void get().refresh();
      return null;
    }
  },

  clear: async () => {
    set({ loading: true });
    try {
      const s = await typedIPC.clearSecret();
      set({ status: s, loading: false });
      toast.success(strings.toasts.apiKeyCleared, strings.toasts.apiKeyClearedDetail);
      return s;
    } catch (err) {
      set({ loading: false });
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.toasts.apiKeyClearFailed, message);
      void get().refresh();
      return null;
    }
  },
}));
