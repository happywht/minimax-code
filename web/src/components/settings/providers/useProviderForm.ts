/**
 * useProviderForm — create/edit form state for the Providers tab.
 *
 * Owns every form field, preset application, the in-form model list
 * editor, and the submit mutation (create vs update). The tab only
 * wires the returned object into `ProviderForm`.
 */
import { useState } from "react";
import { useProviderStore } from "../../../stores";
import { toast } from "../../layout/ErrorBoundary";
import type { ProviderInfo, ProviderModel } from "../../../types/ipc";
import type { ProviderPreset } from "./presets";

export function useProviderForm() {
  const create = useProviderStore((s) => s.create);
  const update = useProviderStore((s) => s.update);

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  // Form fields
  const [formName, setFormName] = useState("");
  const [formProtocol, setFormProtocol] = useState<"anthropic" | "openai">("openai");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [formApiKey, setFormApiKey] = useState("");
  const [formModelId, setFormModelId] = useState("");
  const [formModelName, setFormModelName] = useState("");
  const [formModelCtx, setFormModelCtx] = useState("128000");
  const [formModels, setFormModels] = useState<ProviderModel[]>([]);
  const [revealApiKey, setRevealApiKey] = useState(false);

  const resetForm = () => {
    setFormName(""); setFormProtocol("openai"); setFormBaseUrl(""); setFormApiKey("");
    setFormModelId(""); setFormModelName(""); setFormModelCtx("128000"); setFormModels([]);
    setShowForm(false); setEditingId(null); setRevealApiKey(false);
  };

  const openForCreate = () => {
    resetForm();
    setShowForm(true);
  };

  const applyPreset = (preset: ProviderPreset) => {
    setFormName(preset.name);
    setFormProtocol(preset.protocol);
    setFormBaseUrl(preset.base_url);
  };

  const startEdit = (p: ProviderInfo) => {
    setEditingId(p.id);
    setFormName(p.name);
    setFormProtocol(p.protocol);
    setFormBaseUrl(p.base_url);
    setFormModels(p.models ?? []);
    setFormApiKey("");
    setShowForm(true);
  };

  const addModelToList = () => {
    if (!formModelId.trim()) return;
    setFormModels([...formModels, {
      id: formModelId.trim(),
      name: formModelName.trim() || formModelId.trim(),
      context_window: parseInt(formModelCtx) || 128000,
      supports_tools: true,
    }]);
    setFormModelId(""); setFormModelName(""); setFormModelCtx("128000");
  };

  const removeModelFromList = (idx: number) => {
    setFormModels(formModels.filter((_, i) => i !== idx));
  };

  const canSubmit = Boolean(formName.trim() && formBaseUrl.trim());

  const handleSubmit = async () => {
    if (!canSubmit) return;
    if (editingId) {
      const opts: Record<string, unknown> = {
        provider_id: editingId,
        name: formName.trim(),
        protocol: formProtocol,
        base_url: formBaseUrl.trim(),
        models: formModels,
      };
      if (formApiKey.trim()) opts.api_key = formApiKey.trim();
      await update(opts as Parameters<typeof update>[0]);
      toast.success("Provider updated", formName.trim());
    } else {
      const result = await create({
        name: formName.trim(),
        protocol: formProtocol,
        base_url: formBaseUrl.trim(),
        models: formModels.length > 0 ? formModels : undefined,
        api_key: formApiKey.trim() || undefined,
      });
      if (result) toast.success("Provider created", result.name);
    }
    resetForm();
  };

  return {
    showForm,
    editingId,
    formName, setFormName,
    formProtocol, setFormProtocol,
    formBaseUrl, setFormBaseUrl,
    formApiKey, setFormApiKey,
    formModelId, setFormModelId,
    formModelName, setFormModelName,
    formModelCtx, setFormModelCtx,
    formModels,
    revealApiKey, setRevealApiKey,
    canSubmit,
    resetForm,
    openForCreate,
    applyPreset,
    startEdit,
    addModelToList,
    removeModelFromList,
    handleSubmit,
  };
}

export type ProviderFormState = ReturnType<typeof useProviderForm>;
