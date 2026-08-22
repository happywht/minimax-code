/**
 * AgentRow — one agent card in the Agents tab list.
 *
 * v1.1.0: adds an inline edit form (Pencil button expands a system-prompt
 * + model editor) and an enable/disable toggle so agents can be managed
 * without recreating them.
 */
import { useState } from "react";
import { Bot, Pencil, Trash2, X } from "lucide-react";
import { Badge, Button, IconButton, Input, Textarea } from "../../../ui";
import { strings } from "../../../ui/strings";
import { Field } from "../fields";
import type { AgentInfo } from "../../../types/ipc";

export interface AgentRowProps {
  agent: AgentInfo;
  onToggleEnabled: () => void;
  onSave: (fields: { system_prompt?: string; model?: string }) => void;
  onDelete: () => void;
}

export function AgentRow({ agent, onToggleEnabled, onSave, onDelete }: AgentRowProps): JSX.Element {
  const [showEdit, setShowEdit] = useState(false);
  const [prompt, setPrompt] = useState(agent.system_prompt ?? "");
  const [model, setModel] = useState(agent.model ?? "");

  const dirty = prompt !== (agent.system_prompt ?? "") || model !== (agent.model ?? "");

  const submit = () => {
    onSave({
      system_prompt: prompt.trim() || undefined,
      model: model.trim() || undefined,
    });
    setShowEdit(false);
  };

  return (
    <li
      data-testid={`settings-agent-row-${agent.name}`}
      className="rounded-lg border border-line bg-surface-2 p-3 transition-colors duration-150 hover:border-line-strong"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Bot size={12} className="shrink-0 text-accent" />
            <span className="truncate text-xs font-medium text-ink-0">{agent.name}</span>
            <Badge tone={agent.enabled ? "success" : "neutral"} dot>
              {agent.enabled ? strings.settings.agents.enabled : strings.settings.agents.disabled}
            </Badge>
            {agent.model && <Badge tone="neutral">{agent.model}</Badge>}
          </div>
          {agent.description && (
            <p className="mt-0.5 truncate text-[11px] text-ink-2">{agent.description}</p>
          )}
        </div>
        <div className="ml-2 flex shrink-0 items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            data-testid={`settings-agent-toggle-${agent.name}`}
            onClick={onToggleEnabled}
          >
            <span className={agent.enabled ? "text-ink-2" : "text-status-success"}>
              {agent.enabled
                ? strings.settings.agents.disable
                : strings.settings.agents.enable}
            </span>
          </Button>
          <IconButton
            data-testid={`settings-agent-edit-${agent.name}`}
            aria-label={strings.settings.agents.editAria(agent.name)}
            title={strings.settings.agents.editAria(agent.name)}
            active={showEdit}
            onClick={() => setShowEdit((v) => !v)}
          >
            {showEdit ? <X /> : <Pencil />}
          </IconButton>
          <IconButton
            data-testid={`settings-agent-delete-${agent.name}`}
            onClick={onDelete}
            aria-label={strings.settings.agents.deleteAria(agent.name)}
          >
            <Trash2 />
          </IconButton>
        </div>
      </div>

      {showEdit && (
        <div
          data-testid={`settings-agent-editform-${agent.name}`}
          className="mt-2 space-y-2 border-t border-line pt-2"
        >
          <Field label={strings.settings.agents.fieldPrompt} htmlFor={`agent-edit-prompt-${agent.name}`}>
            <Textarea
              id={`agent-edit-prompt-${agent.name}`}
              data-testid={`settings-agent-edit-prompt-${agent.name}`}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder={strings.settings.agents.placeholderPrompt}
              rows={3}
              className="resize-none text-xs"
            />
          </Field>
          <Field label={strings.settings.agents.fieldModel} htmlFor={`agent-edit-model-${agent.name}`}>
            <Input
              id={`agent-edit-model-${agent.name}`}
              data-testid={`settings-agent-edit-model-${agent.name}`}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={strings.settings.agents.placeholderModel}
              spellCheck={false}
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setPrompt(agent.system_prompt ?? "");
                setModel(agent.model ?? "");
                setShowEdit(false);
              }}
            >
              {strings.settings.agents.cancel}
            </Button>
            <Button
              size="sm"
              variant="subtle"
              data-testid={`settings-agent-edit-submit-${agent.name}`}
              disabled={!dirty}
              onClick={submit}
            >
              {strings.settings.agents.save}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
