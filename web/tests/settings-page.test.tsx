/**
 * Settings page tests — verifies the three tabs render, model
 * switching round-trips through the typed IPC, permission rules
 * can be added/deleted, and scheduled jobs can be toggled.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SettingsPage } from "../src/components/SettingsPage";
import {
  useModelStore,
  usePermissionStore,
  useProviderStore,
  useScheduleStore,
  useSecretStore,
} from "../src/stores";
import type { ProviderInfo, SecretStatus } from "../src/types/ipc";

// Per-test mutable backing store for the secrets mock — the
// IPC factory closure returns fresh `getSecretStatus` / `setSecret`
// / `clearSecret` that read/write this single object.
const mockSecretState: { current: SecretStatus } = {
  current: { configured: false, source: "none" },
};

const baseProvider = (): ProviderInfo => ({
  id: "builtin-minimax",
  name: "MiniMax",
  protocol: "anthropic",
  base_url: "https://api.minimax.chat/v1",
  api_key_configured: false,
  models: [
    { id: "m1", name: "Alpha", context_window: 8000, supports_tools: true },
    { id: "m2", name: "Beta", context_window: 32000, supports_tools: true },
  ],
  enabled: true,
  created_at: "2026-06-10T00:00:00Z",
  updated_at: "2026-06-10T00:00:00Z",
});

const mockProviderState: { providers: ProviderInfo[] } = {
  providers: [baseProvider()],
};

// Mock the typed IPC so we can drive each namespace independently.
vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listModels: vi.fn(async () => ({
        models: mockProviderState.providers.flatMap((p) =>
          p.models.map((m) => ({
            ...m,
            provider: p.name,
            provider_id: p.id,
            protocol: p.protocol,
          })),
        ),
        current: "m1",
      })),
      setCurrentModel: vi.fn(async (modelId: string) => ({ current: modelId })),
      listRules: vi.fn(async () => ({ rules: [] })),
      setRule: vi.fn(
        async (rule: { id?: string; tool: string; pattern: string; decision: "allow" | "deny" | "ask" }) => ({
          rule: {
            id: rule.id ?? `rule_${Math.random().toString(36).slice(2, 6)}`,
            tool: rule.tool,
            pattern: rule.pattern,
            decision: rule.decision,
            created_at: Date.now(),
          },
        }),
      ),
      deleteRule: vi.fn(async () => ({ ok: true })),
      listJobs: vi.fn(async () => ({ jobs: [] })),
      createJob: vi.fn(
        async (opts: { name: string; cron: string; prompt: string }) => ({
          job: {
            id: `job_${Math.random().toString(36).slice(2, 6)}`,
            name: opts.name,
            cron: opts.cron,
            prompt: opts.prompt,
            enabled: true,
            last_run_at: null,
            next_run_at: null,
          },
        }),
      ),
      deleteJob: vi.fn(async () => ({ ok: true })),
      enableJob: vi.fn(async (jobId: string) => ({
        job: {
          id: jobId,
          name: "stub",
          cron: "* * * * *",
          prompt: "stub",
          enabled: true,
          last_run_at: null,
          next_run_at: null,
        },
      })),
      disableJob: vi.fn(async (jobId: string) => ({
        job: {
          id: jobId,
          name: "stub",
          cron: "* * * * *",
          prompt: "stub",
          enabled: false,
          last_run_at: null,
          next_run_at: null,
        },
      })),
      getSecretStatus: vi.fn(async () => mockSecretState.current),
      setSecret: vi.fn(async (value: string) => {
        const trimmed = value.trim();
        if (!trimmed) {
          throw new Error("invalid params: 'value' must be a non-empty string");
        }
        mockSecretState.current = { configured: true, source: "keyring" };
        return mockSecretState.current;
      }),
      clearSecret: vi.fn(async () => {
        mockSecretState.current = { configured: false, source: "none" };
        return mockSecretState.current;
      }),
      listProviders: vi.fn(async () => ({ providers: mockProviderState.providers })),
      updateProvider: vi.fn(async (opts: {
        provider_id: string;
        name?: string;
        protocol?: "anthropic" | "openai";
        base_url?: string;
        models?: ProviderInfo["models"];
        enabled?: boolean;
      }) => {
        mockProviderState.providers = mockProviderState.providers.map((p) =>
          p.id === opts.provider_id
            ? {
                ...p,
                ...(opts.name ? { name: opts.name } : {}),
                ...(opts.protocol ? { protocol: opts.protocol } : {}),
                ...(opts.base_url ? { base_url: opts.base_url } : {}),
                ...(opts.models ? { models: opts.models } : {}),
                ...(typeof opts.enabled === "boolean" ? { enabled: opts.enabled } : {}),
                updated_at: "2026-06-10T00:01:00Z",
              }
            : p,
        );
        return { provider: mockProviderState.providers.find((p) => p.id === opts.provider_id)! };
      }),
      createProvider: vi.fn(async () => ({ provider: baseProvider() })),
      deleteProvider: vi.fn(async () => ({ ok: true, deleted: "provider-x" })),
      setProviderApiKey: vi.fn(async (providerId: string, apiKey: string) => {
        if (!apiKey.trim()) throw new Error("invalid key");
        mockProviderState.providers = mockProviderState.providers.map((p) =>
          p.id === providerId ? { ...p, api_key_configured: true } : p,
        );
        return { ok: true, provider_id: providerId, api_key_configured: true };
      }),
      clearProviderApiKey: vi.fn(async (providerId: string) => {
        mockProviderState.providers = mockProviderState.providers.map((p) =>
          p.id === providerId ? { ...p, api_key_configured: false } : p,
        );
        return { ok: true, provider_id: providerId, api_key_configured: false };
      }),
    },
  };
});

beforeEach(() => {
  useModelStore.setState({ models: [], current: null, loading: false });
  usePermissionStore.setState({ rules: [], alwaysAllow: false, loading: false });
  useProviderStore.setState({ providers: [], loading: false });
  useScheduleStore.setState({ jobs: [], loading: false });
  useSecretStore.setState({ status: null, loading: false });
  // Default: no key configured.
  mockSecretState.current = { configured: false, source: "none" };
  mockProviderState.providers = [baseProvider()];
});

describe("SettingsPage", () => {
  it("closes via the close button", async () => {
    const onClose = vi.fn();
    render(<SettingsPage onClose={onClose} />);
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBeGreaterThan(0);
    });
    fireEvent.click(screen.getByTestId("settings-close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("renders with the models tab open by default", async () => {
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-models-list").children.length).toBe(2);
    });
    expect(screen.getByTestId("settings-nav")).toBeInTheDocument();
    expect(screen.getByText("Core")).toBeInTheDocument();
    expect(screen.getByTestId("settings-models")).toBeInTheDocument();
    expect(screen.getByTestId("settings-title")).toHaveTextContent("Settings");
  });

  it("highlights the current model and disables its select button", async () => {
    render(<SettingsPage />);
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBe(2);
    });
    expect(screen.getByTestId("settings-model-current-m1")).toBeInTheDocument();
    const selectCurrent = screen.getByTestId("settings-model-select-m1");
    expect(selectCurrent).toBeDisabled();
  });

  it("switches model when the user clicks 'Use' on a non-current row", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-model-select-m2")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("settings-model-select-m2"));
    await waitFor(() => {
      expect(typedIPC.setCurrentModel).toHaveBeenCalledWith("m2");
    });
    await waitFor(() => {
      expect(useModelStore.getState().current).toBe("m2");
    });
  });

  it("adds a provider model from the Models tab and persists it", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("settings-model-provider-builtin-minimax")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("settings-model-add-id-builtin-minimax"), {
      target: { value: "m3" },
    });
    fireEvent.change(screen.getByTestId("settings-model-add-name-builtin-minimax"), {
      target: { value: "Gamma" },
    });
    fireEvent.change(screen.getByTestId("settings-model-add-ctx-builtin-minimax"), {
      target: { value: "64000" },
    });
    fireEvent.click(screen.getByTestId("settings-model-add-submit-builtin-minimax"));
    await waitFor(() => {
      expect(typedIPC.updateProvider).toHaveBeenCalledWith(expect.objectContaining({
        provider_id: "builtin-minimax",
        models: expect.arrayContaining([
          expect.objectContaining({ id: "m3", name: "Gamma", context_window: 64000 }),
        ]),
      }));
    });
    await waitFor(() => {
      expect(screen.getByTestId("settings-model-m3")).toBeInTheDocument();
    });
  });

  it("switches to the permissions tab and shows an empty state", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    await waitFor(() => {
      expect(useModelStore.getState().models.length).toBeGreaterThan(0);
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("settings-tab-permissions"));
    });
    await waitFor(() => {
      expect(typedIPC.listRules).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId("settings-permissions-list").textContent).toMatch(
        /No permission rules yet/,
      );
    });
  });

  it("adds a permission rule via the form", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-permissions"));
    fireEvent.change(screen.getByTestId("settings-permission-pattern"), {
      target: { value: "^rm" },
    });
    fireEvent.click(screen.getByTestId("settings-permission-add"));
    await waitFor(() => {
      expect(typedIPC.setRule).toHaveBeenCalledWith(
        expect.objectContaining({ pattern: "^rm", decision: "allow" }),
      );
    });
    await waitFor(() => {
      expect(usePermissionStore.getState().rules.length).toBe(1);
    });
  });

  it("switches to the scheduled tab and creates a new job", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-scheduled"));
    fireEvent.change(screen.getByTestId("settings-job-name"), {
      target: { value: "nightly" },
    });
    fireEvent.change(screen.getByTestId("settings-job-cron"), {
      target: { value: "0 2 * * *" },
    });
    fireEvent.click(screen.getByTestId("settings-job-add"));
    await waitFor(() => {
      expect(typedIPC.createJob).toHaveBeenCalledWith({
        name: "nightly",
        cron: "0 2 * * *",
        prompt: "",
      });
    });
    await waitFor(() => {
      expect(useScheduleStore.getState().jobs.length).toBe(1);
    });
  });

  it("toggles a job's enabled state", async () => {
    useScheduleStore.setState({
      jobs: [
        {
          id: "job_1",
          name: "echo",
          cron: "* * * * *",
          prompt: "hi",
          enabled: true,
          last_run_at: null,
          next_run_at: null,
        },
      ],
    });
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-scheduled"));
    const checkbox = screen.getByTestId("settings-job-toggle-job_1").querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    await waitFor(() => {
      expect(typedIPC.disableJob).toHaveBeenCalledWith("job_1");
    });
  });

  // ─────────────────────── API Key tab ───────────────────────

  it("switches to the API Key tab and shows the 'not configured' state", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-api-key"));
    expect(screen.getByTestId("settings-api-key")).toBeInTheDocument();
    await waitFor(() => {
      expect(typedIPC.getSecretStatus).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId("settings-api-key-status-text").textContent).toMatch(
        /Not configured/,
      );
    });
    // The 'Clear keyring' button only appears when the source is
    // 'keyring' (since clearing a non-existent entry would be a
    // no-op but we want to keep the UI honest).
    expect(screen.queryByTestId("settings-api-key-clear")).toBeNull();
  });

  it("shows 'Using environment variable' when the backend reports source=env", async () => {
    mockSecretState.current = { configured: true, source: "env" };
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-api-key"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-api-key-status-text").textContent).toMatch(
        /environment variable/i,
      );
    });
  });

  it("saves a key into the keyring and flips the status pill", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-api-key"));
    fireEvent.change(screen.getByTestId("settings-api-key-input"), {
      target: { value: "sk-test-1" },
    });
    // The save button is disabled until draft is non-empty + the
    // async re-render lands — wait for the button to enable.
    const save = await waitFor(() => {
      const btn = screen.getByTestId("settings-api-key-save") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
      return btn;
    });
    fireEvent.click(save);
    await waitFor(() => {
      expect(typedIPC.setSecret).toHaveBeenCalledWith("sk-test-1");
    });
    await waitFor(() => {
      expect(screen.getByTestId("settings-api-key-status-text").textContent).toMatch(
        /OS keyring/,
      );
    });
    // The input was cleared after a successful save.
    await waitFor(() => {
      expect(
        (screen.getByTestId("settings-api-key-input") as HTMLInputElement).value,
      ).toBe("");
    });
    // And the 'Clear keyring' affordance now appears.
    expect(screen.getByTestId("settings-api-key-clear")).toBeInTheDocument();
  });

  it("disables the save button while the input is empty", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-api-key"));
    const save = screen.getByTestId("settings-api-key-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("settings-api-key-input"), {
      target: { value: "sk-anything" },
    });
    await waitFor(() => {
      expect(save.disabled).toBe(false);
    });
  });

  it("toggles the password reveal button to plain-text input", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-api-key"));
    const input = screen.getByTestId("settings-api-key-input") as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.click(screen.getByTestId("settings-api-key-reveal"));
    await waitFor(() => {
      expect(
        (screen.getByTestId("settings-api-key-input") as HTMLInputElement).type,
      ).toBe("text");
    });
    fireEvent.click(screen.getByTestId("settings-api-key-reveal"));
    await waitFor(() => {
      expect(
        (screen.getByTestId("settings-api-key-input") as HTMLInputElement).type,
      ).toBe("password");
    });
  });

  it("clears the keyring when the user clicks 'Clear keyring'", async () => {
    // Seed a keyring entry so the 'Clear keyring' button shows up.
    mockSecretState.current = { configured: true, source: "keyring" };
    useSecretStore.setState({
      status: { configured: true, source: "keyring" },
      loading: false,
    });
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-api-key"));
    expect(screen.getByTestId("settings-api-key-clear")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("settings-api-key-clear"));
    await waitFor(() => {
      expect(typedIPC.clearSecret).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByTestId("settings-api-key-status-text").textContent).toMatch(
        /Not configured/,
      );
    });
  });

  it("saves a provider API key and flips the provider badge", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SettingsPage />);
    fireEvent.click(screen.getByTestId("settings-tab-providers"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-provider-builtin-minimax")).toHaveTextContent("no key");
    });
    fireEvent.click(screen.getByTestId("settings-provider-builtin-minimax-expand"));
    fireEvent.change(screen.getByTestId("settings-provider-builtin-minimax-key-input"), {
      target: { value: "sk-provider-test" },
    });
    fireEvent.click(screen.getByTestId("settings-provider-builtin-minimax-key-save"));
    await waitFor(() => {
      expect(typedIPC.setProviderApiKey).toHaveBeenCalledWith("builtin-minimax", "sk-provider-test");
    });
    await waitFor(() => {
      expect(screen.getByTestId("settings-provider-builtin-minimax")).toHaveTextContent("key ✓");
    });
  });
});
