/**
 * Teams tab — agent team management (v0.8.0).
 *
 * Slim container: create-form state lives in `teams/useTeamForm`,
 * presentation in `teams/TeamForm` / `teams/TeamRow`, color presets in
 * `teams/constants`.
 */
import { useEffect } from "react";
import { Plus } from "lucide-react";
import { Button, EmptyState, Spinner } from "../../ui";
import { useAgentStore, useTeamStore } from "../../stores";
import type { AgentTeam } from "../../types/ipc";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { InlineCode, TabHeader } from "./fields";
import { useTeamForm } from "./teams/useTeamForm";
import { TeamForm } from "./teams/TeamForm";
import { TeamRow } from "./teams/TeamRow";

export { TeamsTab };

function TeamsTab(): JSX.Element {
  const teams = useTeamStore((s) => s.teams);
  const loading = useTeamStore((s) => s.loading);
  const refresh = useTeamStore((s) => s.refresh);
  const remove = useTeamStore((s) => s.remove);
  const enable = useTeamStore((s) => s.enable);
  const disable = useTeamStore((s) => s.disable);

  // Available agents for team assignment
  const agents = useAgentStore((s) => s.agents);
  const refreshAgents = useAgentStore((s) => s.refresh);

  const form = useTeamForm();

  useEffect(() => {
    if (teams.length === 0) void refresh();
    if (agents.length === 0) void refreshAgents();
  }, [teams.length, refresh, agents.length, refreshAgents]);

  const handleDelete = async (t: AgentTeam) => {
    const accepted = await requestConfirmation({
      title: `Delete team ${t.name}?`,
      description: "The team definition and its agent assignments will be permanently removed. Individual agents are kept.",
      confirmLabel: "Delete Team",
    });
    if (accepted) await remove(t.name);
  };

  return (
    <section data-testid="settings-teams" className="space-y-4">
      <TabHeader
        title="Agent Teams"
        hint={
          <>
            Create named groups of agents that work together. Trigger with{" "}
            <InlineCode>@team:team-name</InlineCode> in chat.
          </>
        }
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-team-create"
            onClick={form.toggleForm}
            icon={<Plus />}
          >
            New Team
          </Button>
        }
      />

      {form.showForm && <TeamForm form={form} agents={agents} />}

      {loading && teams.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> Loading teams…
        </div>
      ) : teams.length === 0 ? (
        <EmptyState
          title="No teams configured."
          hint='Click "New Team" to create one.'
        />
      ) : (
        <ul className="space-y-2">
          {teams.map((t) => (
            <TeamRow
              key={t.id}
              team={t}
              onToggleEnabled={() => void (t.enabled ? disable(t.name) : enable(t.name))}
              onDelete={() => void handleDelete(t)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
