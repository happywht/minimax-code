/**
 * useModelRegistry — data + draft logic for the Models tab.
 *
 * Owns the per-provider "add model" draft state and the add/remove
 * mutations that round-trip through `provider.update` (the model
 * registry lives on the provider record). The Models tab itself only
 * renders what this hook returns.
 */
import { useEffect, useState } from "react";
import { useModelStore, useProviderStore } from "../../../stores";
import type { ProviderInfo, ProviderModel } from "../../../types/ipc";
import { requestConfirmation } from "../../modals/ConfirmationDialog";

export interface ModelDraft {
  id: string;
  name: string;
  ctx: string;
}

const EMPTY_DRAFT: ModelDraft = { id: "", name: "", ctx: "128000" };

export function useModelRegistry() {
  const models = useModelStore((s) => s.models);
  const current = useModelStore((s) => s.current);
  const refresh = useModelStore((s) => s.refresh);
  const setCurrent = useModelStore((s) => s.setCurrent);
  const loading = useModelStore((s) => s.loading);
  const providers = useProviderStore((s) => s.providers);
  const providersLoading = useProviderStore((s) => s.loading);
  const refreshProviders = useProviderStore((s) => s.refresh);
  const updateProvider = useProviderStore((s) => s.update);

  const [drafts, setDrafts] = useState<Record<string, ModelDraft>>({});

  useEffect(() => {
    if (models.length === 0) void refresh();
  }, [models.length, refresh]);

  useEffect(() => {
    if (providers.length === 0) void refreshProviders();
  }, [providers.length, refreshProviders]);

  const draftFor = (providerId: string): ModelDraft => drafts[providerId] ?? EMPTY_DRAFT;

  const setDraft = (providerId: string, patch: Partial<ModelDraft>) => {
    setDrafts((currentDrafts) => ({
      ...currentDrafts,
      [providerId]: { ...(currentDrafts[providerId] ?? EMPTY_DRAFT), ...patch },
    }));
  };

  const addModel = async (provider: ProviderInfo) => {
    const draft = draftFor(provider.id);
    const id = draft.id.trim();
    if (!id) return;
    const model: ProviderModel = {
      id,
      name: draft.name.trim() || id,
      context_window: Number.parseInt(draft.ctx, 10) || 128000,
      supports_tools: true,
    };
    const nextModels = [...(provider.models ?? []).filter((m) => m.id !== id), model];
    const result = await updateProvider({ provider_id: provider.id, models: nextModels });
    if (result) {
      setDraft(provider.id, EMPTY_DRAFT);
      await refresh();
    }
  };

  const removeModel = async (provider: ProviderInfo, modelId: string) => {
    const accepted = await requestConfirmation({
      title: `Remove model ${modelId}?`,
      description: `This removes the model from ${provider.name}. Conversations that selected it will need another active model.`,
      confirmLabel: "Remove Model",
    });
    if (!accepted) return;
    const result = await updateProvider({
      provider_id: provider.id,
      models: (provider.models ?? []).filter((m) => m.id !== modelId),
    });
    if (result) await refresh();
  };

  return {
    models,
    current,
    loading,
    providers,
    providersLoading,
    refresh,
    setCurrent,
    draftFor,
    setDraft,
    addModel,
    removeModel,
  };
}

export type ModelRegistry = ReturnType<typeof useModelRegistry>;
