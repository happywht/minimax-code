/**
 * ProviderForm — create/edit provider form (name, protocol, base URL,
 * API key, model list). Pure presentation: all state lives in
 * `useProviderForm`.
 */
import { Eye, EyeOff, Plus, Trash2 } from "lucide-react";
import { Button, IconButton, Input, Panel } from "../../../ui";
import { Field, Select } from "../fields";
import { PROVIDER_PRESETS } from "./presets";
import type { ProviderFormState } from "./useProviderForm";

export interface ProviderFormProps {
  form: ProviderFormState;
}

export function ProviderForm({ form }: ProviderFormProps): JSX.Element {
  const editing = form.editingId !== null;
  return (
    <Panel
      data-testid="settings-provider-form"
      title={editing ? "Edit Provider" : "New Provider"}
      actions={
        <Button size="sm" variant="ghost" onClick={form.resetForm}>
          Cancel
        </Button>
      }
    >
      <div className="space-y-3">
        {/* Preset buttons */}
        {!editing && (
          <div className="space-y-1.5">
            <span className="text-[11px] text-ink-2">Quick presets:</span>
            <div className="flex flex-wrap gap-1.5">
              {PROVIDER_PRESETS.map((p) => (
                <Button
                  key={p.label}
                  size="sm"
                  variant="secondary"
                  onClick={() => form.applyPreset(p)}
                >
                  {p.label}
                </Button>
              ))}
            </div>
          </div>
        )}

        {/* Main fields */}
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <Field label="Name" htmlFor="provider-form-name" className="sm:col-span-4">
            <Input
              id="provider-form-name"
              name="provider-name"
              autoComplete="off"
              value={form.formName}
              onChange={(e) => form.setFormName(e.target.value)}
              placeholder="e.g. DeepSeek…"
            />
          </Field>
          <Field label="Protocol" htmlFor="provider-form-protocol" className="sm:col-span-3">
            <Select
              id="provider-form-protocol"
              name="provider-protocol"
              value={form.formProtocol}
              onChange={(e) => form.setFormProtocol(e.target.value as "anthropic" | "openai")}
            >
              <option value="openai">OpenAI</option>
              <option value="anthropic">Anthropic</option>
            </Select>
          </Field>
          <Field label="Base URL" htmlFor="provider-form-base-url" className="sm:col-span-5">
            <Input
              id="provider-form-base-url"
              name="provider-base-url"
              type="url"
              autoComplete="off"
              spellCheck={false}
              value={form.formBaseUrl}
              onChange={(e) => form.setFormBaseUrl(e.target.value)}
              placeholder="https://api.example.com/v1…"
              className="font-mono"
            />
          </Field>
        </div>

        {/* API key */}
        <Field
          label={`API Key ${editing ? "(leave empty to keep current)" : ""}`}
          htmlFor="provider-form-api-key"
        >
          <div className="relative">
            <Input
              id="provider-form-api-key"
              name="provider-api-key"
              type={form.revealApiKey ? "text" : "password"}
              value={form.formApiKey}
              onChange={(e) => form.setFormApiKey(e.target.value)}
              placeholder={editing ? "Leave empty to keep current…" : "sk-…"}
              autoComplete="new-password"
              spellCheck={false}
              className="pr-8 font-mono"
            />
            <IconButton
              size="sm"
              onClick={() => form.setRevealApiKey((v) => !v)}
              aria-label={form.revealApiKey ? "Hide API key" : "Show API key"}
              className="absolute right-1 top-1/2 -translate-y-1/2"
            >
              {form.revealApiKey ? <EyeOff /> : <Eye />}
            </IconButton>
          </div>
        </Field>

        {/* Models list */}
        <div>
          <span className="mb-0.5 block text-[11px] text-ink-2">Models</span>
          {form.formModels.length > 0 && (
            <ul className="mt-1 space-y-1">
              {form.formModels.map((m, i) => (
                <li
                  key={m.id}
                  className="flex items-center gap-2 rounded-md border border-line bg-surface-2 px-2 py-1 text-xs"
                >
                  <span className="flex-1 truncate text-ink-0">{m.name || m.id}</span>
                  <span className="text-[11px] text-ink-2">
                    {(m.context_window / 1000).toFixed(0)}k
                  </span>
                  <IconButton
                    size="sm"
                    onClick={() => form.removeModelFromList(i)}
                    aria-label={`Remove ${m.name || m.id}`}
                  >
                    <Trash2 />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-12">
            <Input
              name="provider-model-id"
              aria-label="Model ID"
              autoComplete="off"
              spellCheck={false}
              value={form.formModelId}
              onChange={(e) => form.setFormModelId(e.target.value)}
              placeholder="model id…"
              className="sm:col-span-3"
            />
            <Input
              name="provider-model-name"
              aria-label="Model display name"
              autoComplete="off"
              value={form.formModelName}
              onChange={(e) => form.setFormModelName(e.target.value)}
              placeholder="display name…"
              className="sm:col-span-3"
            />
            <Input
              name="provider-model-context"
              aria-label="Context window"
              type="number"
              inputMode="numeric"
              min="1"
              value={form.formModelCtx}
              onChange={(e) => form.setFormModelCtx(e.target.value)}
              placeholder="context…"
              className="sm:col-span-2"
            />
            <Button
              size="sm"
              variant="subtle"
              onClick={form.addModelToList}
              disabled={!form.formModelId.trim()}
              icon={<Plus />}
              className="sm:col-span-4"
            >
              Add Model
            </Button>
          </div>
        </div>

        {/* Submit */}
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={form.resetForm}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            data-testid="settings-provider-form-submit"
            onClick={() => void form.handleSubmit()}
            disabled={!form.canSubmit}
          >
            {editing ? "Update" : "Create"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
