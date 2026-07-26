/**
 * Agents tab — sub-agent CRUD via `agent.*` IPC.
 */
import { useEffect, useState } from "react";
import { Bot, Plus, Trash2 } from "lucide-react";
import { Badge, Button, EmptyState, IconButton, Input, Panel, Spinner, Textarea } from "../../ui";
import { useAgentStore } from "../../stores";
import type { AgentInfo } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { Field, InlineCode, TabHeader } from "./fields";

export { AgentsTab };

function AgentsTab(): JSX.Element {
  const agents = useAgentStore((s) => s.agents);
  const loading = useAgentStore((s) => s.loading);
  const refresh = useAgentStore((s) => s.refresh);
  const create = useAgentStore((s) => s.create);
  const remove = useAgentStore((s) => s.remove);

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formPrompt, setFormPrompt] = useState("");

  useEffect(() => { if (agents.length === 0) void refresh(); }, [agents.length, refresh]);

  const handleCreate = async () => {
    if (!formName.trim() || !formPrompt.trim()) return;
    const a = await create({ name: formName.trim(), system_prompt: formPrompt.trim() });
    if (a) { setFormName(""); setFormPrompt(""); setShowForm(false); }
  };

  const handleDelete = async (agent: AgentInfo) => {
    const accepted = await requestConfirmation({
      title: `Delete agent ${agent.name}?`,
      description: "This agent will no longer be available in chat or team assignments. Existing conversation history is not deleted.",
      confirmLabel: "Delete Agent",
    });
    if (accepted) await remove(agent.name);
  };

  return (
    <section data-testid="settings-agents" className="space-y-4">
      <TabHeader
        title="Sub-agents"
        hint={
          <>
            Manage agents that can be invoked via <InlineCode>@agent</InlineCode> in chat.
          </>
        }
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-agent-create"
            onClick={() => setShowForm((v) => !v)}
            icon={<Plus />}
          >
            New Agent
          </Button>
        }
      />

      {showForm && (
        <Panel
          data-testid="settings-agent-form"
          title="New Agent"
          actions={
            <Button size="sm" variant="ghost" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
          }
        >
          <div className="space-y-2">
            <Field label="Agent Name" htmlFor="agent-form-name">
              <Input
                id="agent-form-name"
                name="agent-name"
                autoComplete="off"
                spellCheck={false}
                data-testid="settings-agent-form-name"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder="e.g. code-reviewer…"
              />
            </Field>
            <Field label="System Prompt" htmlFor="agent-form-prompt">
              <Textarea
                id="agent-form-prompt"
                name="agent-system-prompt"
                autoComplete="off"
                data-testid="settings-agent-form-prompt"
                value={formPrompt}
                onChange={(e) => setFormPrompt(e.target.value)}
                placeholder="System prompt…"
                rows={3}
                className="resize-none text-xs"
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                data-testid="settings-agent-form-submit"
                onClick={() => void handleCreate()}
                disabled={!formName.trim() || !formPrompt.trim()}
              >
                Create
              </Button>
            </div>
          </div>
        </Panel>
      )}

      {loading && agents.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> Loading agents…
        </div>
      ) : agents.length === 0 ? (
        <EmptyState
          title="No sub-agents configured."
          hint='Click "New Agent" to create one.'
        />
      ) : (
        <ul className="space-y-2">
          {agents.map((a) => (
            <li
              key={a.id}
              data-testid={`settings-agent-row-${a.name}`}
              className="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface-2 p-3 transition-colors duration-150 hover:border-line-strong"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Bot size={12} className="shrink-0 text-accent" />
                  <span className="truncate text-xs font-medium text-ink-0">{a.name}</span>
                  <Badge tone={a.enabled ? "success" : "neutral"} dot>
                    {a.enabled ? "enabled" : "disabled"}
                  </Badge>
                </div>
                {a.description && (
                  <p className="mt-0.5 truncate text-[11px] text-ink-2">{a.description}</p>
                )}
              </div>
              <IconButton
                data-testid={`settings-agent-delete-${a.name}`}
                onClick={() => void handleDelete(a)}
                aria-label={`Delete agent ${a.name}`}
              >
                <Trash2 />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
