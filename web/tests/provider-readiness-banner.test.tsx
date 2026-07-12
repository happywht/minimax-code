import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ProviderReadinessBanner } from "../src/components/ProviderReadinessBanner";
import { useModelStore, useProviderStore } from "../src/stores";
import type { ProviderInfo } from "../src/types/ipc";

const provider: ProviderInfo = {
  id: "builtin-minimax",
  name: "MiniMax",
  protocol: "anthropic",
  base_url: "https://api.minimaxi.com/anthropic",
  api_key_configured: false,
  models: [],
  enabled: true,
  created_at: "2026-07-12T00:00:00Z",
  updated_at: "2026-07-12T00:00:00Z",
};

function renderBanner() {
  const onOpenProviders = vi.fn();
  const onOpenModels = vi.fn();
  render(
    <ProviderReadinessBanner
      onOpenProviders={onOpenProviders}
      onOpenModels={onOpenModels}
    />,
  );
  return { onOpenProviders, onOpenModels };
}

describe("ProviderReadinessBanner", () => {
  beforeEach(() => {
    useModelStore.setState({
      models: [{
        id: "MiniMax-M3",
        name: "MiniMax-M3",
        provider: "MiniMax",
        provider_id: "builtin-minimax",
        protocol: "anthropic",
        context_window: 200000,
        supports_tools: true,
      }],
      current: "MiniMax-M3",
      loading: false,
    });
    useProviderStore.setState({
      providers: [provider],
      loading: false,
      initialized: true,
    });
  });

  it("stays hidden until provider state is initialized", () => {
    useProviderStore.setState({ initialized: false });
    renderBanner();
    expect(screen.queryByTestId("provider-readiness-banner")).toBeNull();
  });

  it("shows demo mode and opens provider settings when the key is missing", () => {
    const { onOpenProviders } = renderBanner();
    expect(screen.getByTestId("provider-readiness-banner")).toHaveTextContent("Demo Mode");
    expect(screen.getByTestId("provider-readiness-banner")).toHaveTextContent("mock responses");
    fireEvent.click(screen.getByTestId("provider-readiness-action"));
    expect(onOpenProviders).toHaveBeenCalledTimes(1);
  });

  it("hides when the selected provider is enabled and configured", () => {
    useProviderStore.setState({
      providers: [{ ...provider, api_key_configured: true }],
    });
    renderBanner();
    expect(screen.queryByTestId("provider-readiness-banner")).toBeNull();
  });

  it("routes to model settings when no active model exists", () => {
    useModelStore.setState({ current: null });
    const { onOpenModels } = renderBanner();
    expect(screen.getByTestId("provider-readiness-banner")).toHaveTextContent("No Active Model");
    fireEvent.click(screen.getByTestId("provider-readiness-action"));
    expect(onOpenModels).toHaveBeenCalledTimes(1);
  });
});
