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
import { strings } from "../../ui/strings";
import { toast } from "../layout/ErrorBoundary";
import { useAgentStore, useSessionStore, useTeamStore, useTeamRunStore } from "../../stores";
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

  const spawnRun = useTeamRunStore((s) => s.spawn);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);

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
      title: strings.settings.teams.deleteTitle(t.name),
      description: strings.settings.teams.deleteDesc,
      confirmLabel: strings.settings.teams.deleteLabel,
    });
    if (accepted) await remove(t.name);
  };

  const handleRun = (teamName: string, request: string) => {
    toast.info(strings.settings.teams.runStartedToast, strings.settings.teams.runStartedDetail);
    void spawnRun({
      team_name: teamName,
      request,
      session_id: currentSessionId ?? undefined,
    });
  };

  return (
    <section data-testid="settings-teams" className="space-y-4">
      <TabHeader
        title={strings.settings.teams.title}
        hint={
          <>
            {strings.settings.teams.hintLead} <InlineCode>@team:team-name</InlineCode>
            {strings.settings.teams.hintTail}
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
            {strings.settings.teams.new}
          </Button>
        }
      />

      {form.showForm && <TeamForm form={form} agents={agents} />}

      {loading && teams.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> {strings.settings.teams.loading}
        </div>
      ) : teams.length === 0 ? (
        <EmptyState
          title="暂无团队"
          hint='点击「新建团队」创建一个。'
        />
      ) : (
        <ul className="space-y-2">
          {teams.map((t) => (
            <TeamRow
              key={t.id}
              team={t}
              onToggleEnabled={() => void (t.enabled ? disable(t.name) : enable(t.name))}
              onDelete={() => void handleDelete(t)}
              onRun={(request) => handleRun(t.name, request)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
