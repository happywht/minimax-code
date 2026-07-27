/**
 * TeamForm — new-team creation form. Pure presentation; state lives
 * in `useTeamForm`.
 */
import { Check } from "lucide-react";
import { Button, Input, Panel } from "../../../ui";
import type { AgentInfo, OrchestrationMode } from "../../../types/ipc";
import { Field, Select } from "../fields";
import { TEAM_COLORS } from "./constants";
import type { TeamFormState } from "./useTeamForm";

export interface TeamFormProps {
  form: TeamFormState;
  agents: AgentInfo[];
}

export function TeamForm({ form, agents }: TeamFormProps): JSX.Element {
  return (
    <Panel
      data-testid="settings-team-form"
      title="New Team"
      actions={
        <Button size="sm" variant="ghost" onClick={form.resetForm}>
          Cancel
        </Button>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <Field label="Team Name" htmlFor="team-form-name" className="sm:col-span-4">
            <Input
              id="team-form-name"
              name="team-name"
              autoComplete="off"
              spellCheck={false}
              value={form.formName}
              onChange={(e) => form.setFormName(e.target.value)}
              placeholder="e.g. fullstack-review…"
            />
          </Field>
          <Field label="Orchestration" htmlFor="team-form-mode" className="sm:col-span-4">
            <Select
              id="team-form-mode"
              name="team-orchestration-mode"
              value={form.formMode}
              onChange={(e) => form.setFormMode(e.target.value as OrchestrationMode)}
            >
              <option value="parallel">Parallel</option>
              <option value="sequential">Sequential</option>
              <option value="round-robin">Round-robin</option>
            </Select>
          </Field>
          <fieldset className="min-w-0 sm:col-span-4">
            <legend className="mb-0.5 text-[11px] text-ink-2">Color</legend>
            <div className="mt-0.5 flex flex-wrap gap-1">
              {TEAM_COLORS.slice(0, 5).map((c) => (
                <button
                  key={c}
                  type="button"
                  aria-label={`Use team color ${c}`}
                  aria-pressed={form.formColor === c}
                  onClick={() => form.setFormColor(c)}
                  className={
                    "h-5 w-5 rounded-full border-2 transition-colors duration-150 " +
                    (form.formColor === c ? "border-ink-0" : "border-transparent")
                  }
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </fieldset>
        </div>

        <Field label="Description" htmlFor="team-form-description">
          <Input
            id="team-form-description"
            name="team-description"
            autoComplete="off"
            value={form.formDescription}
            onChange={(e) => form.setFormDescription(e.target.value)}
            placeholder="e.g. Reviews frontend and backend changes…"
          />
        </Field>

        <fieldset>
          <legend className="text-[11px] text-ink-2">
            Agents ({form.formAgents.length} selected)
          </legend>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {agents.length === 0 && (
              <span className="text-[11px] italic text-ink-2">
                暂无可用 Agent — 请先创建。
              </span>
            )}
            {agents.map((a) => {
              const selected = form.formAgents.includes(a.name);
              return (
                <Button
                  key={a.id}
                  size="sm"
                  variant={selected ? "subtle" : "secondary"}
                  aria-pressed={selected}
                  onClick={() => form.toggleAgent(a.name)}
                  icon={selected ? <Check /> : undefined}
                >
                  {a.name}
                </Button>
              );
            })}
          </div>
        </fieldset>

        <div className="flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={form.resetForm}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            data-testid="settings-team-form-submit"
            onClick={() => void form.handleCreate()}
            disabled={!form.canSubmit}
          >
            Create Team
          </Button>
        </div>
      </div>
    </Panel>
  );
}
