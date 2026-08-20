/**
 * WebhookCreateForm — name/source/action form for creating an inbound
 * webhook. Pure presentation; state lives in `useWebhookForm`.
 */
import { Button, Input, Panel } from "../../../ui";
import { strings } from "../../../ui/strings";
import { Field, Select } from "../fields";
import type { WebhookFormState } from "./useWebhookForm";

export interface WebhookCreateFormProps {
  form: WebhookFormState;
}

export function WebhookCreateForm({ form }: WebhookCreateFormProps): JSX.Element {
  return (
    <Panel
      title={strings.settings.webhooks.formTitle}
      actions={
        <Button size="sm" variant="ghost" onClick={form.closeForm}>
          {strings.settings.webhooks.cancel}
        </Button>
      }
    >
      <div className="space-y-2">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <Field
            label={strings.settings.webhooks.fieldName}
            htmlFor="webhook-name"
            className="sm:col-span-5"
          >
            <Input
              id="webhook-name"
              name="webhook-name"
              autoComplete="off"
              data-testid="webhook-name-input"
              className={form.createError ? "border-status-error" : ""}
              placeholder={strings.settings.webhooks.placeholderName}
              value={form.newName}
              onChange={(e) => form.setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void form.handleCreate(); }}
            />
          </Field>
          <Field
            label={strings.settings.webhooks.fieldSource}
            htmlFor="webhook-source"
            className="sm:col-span-3"
          >
            <Select
              id="webhook-source"
              name="webhook-source"
              value={form.newSource}
              onChange={(e) => form.setNewSource(e.target.value as "github" | "gitee" | "custom")}
            >
              <option value="github">GitHub</option>
              <option value="gitee">Gitee</option>
              <option value="custom">{strings.settings.webhooks.sourceCustom}</option>
            </Select>
          </Field>
          <Field
            label={strings.settings.webhooks.fieldAction}
            htmlFor="webhook-action"
            className="sm:col-span-4"
          >
            <Select
              id="webhook-action"
              name="webhook-action"
              value={form.newAction}
              onChange={(e) => form.setNewAction(e.target.value as "code-review" | "send-message")}
            >
              <option value="send-message">{strings.settings.webhooks.actionSendMessage}</option>
              <option value="code-review">{strings.settings.webhooks.actionCodeReview}</option>
            </Select>
          </Field>
        </div>
        {form.createError && (
          <p data-testid="webhook-create-error" aria-live="polite" className="text-[11px] text-status-error">
            {form.createError}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={form.closeForm}>
            {strings.settings.webhooks.cancel}
          </Button>
          <Button
            size="sm"
            variant="primary"
            data-testid="webhook-create-submit"
            onClick={() => void form.handleCreate()}
          >
            {strings.settings.webhooks.create}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
